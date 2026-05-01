from __future__ import annotations
#!/usr/bin/env python3
"""
Telegram 频道同步机器人 v3
- 动态频道管理
- 文字替换规则
- 定时广告推送（支持内联按键）
- 全内联按键设置面板
"""

import os, json, uuid, logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ContextTypes, MessageHandler, CommandHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)
from telegram.error import TelegramError

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 环境变量 ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 未设置")

ENV_ADMIN_IDS = (
    {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
    if os.getenv("ADMIN_IDS") else set()
)
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))

# ── 对话状态 ──────────────────────────────────────────────────────────────────
AD_TEXT, AD_INTERVAL, AD_CHANNELS, AD_BUTTONS = range(4)

# ── 配置管理 ──────────────────────────────────────────────────────────────────
def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    return {"channel_mappings": {}, "replace_rules": {}, "admins": [], "ads": []}

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")

config = load_config()
_src = os.getenv("SOURCE_CHANNEL_ID")
_tgt = os.getenv("TARGET_CHANNEL_ID")
if _src and _tgt and _src not in config["channel_mappings"]:
    config["channel_mappings"][_src] = _tgt
for _aid in ENV_ADMIN_IDS:
    if _aid not in config["admins"]:
        config["admins"].append(_aid)
if "ads" not in config:
    config["ads"] = []
save_config(config)

# ── 权限 ──────────────────────────────────────────────────────────────────────
def is_admin(uid):
    return uid in config.get("admins", []) or uid in ENV_ADMIN_IDS

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if not uid or not is_admin(uid):
            msg = update.message or (update.callback_query and update.callback_query.message)
            if msg:
                await msg.reply_text("⛔ 无权限")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

# ── 工具 ──────────────────────────────────────────────────────────────────────
def apply_replace(text):
    if not text:
        return text
    for old, new in config.get("replace_rules", {}).items():
        text = text.replace(old, new)
    return text

def get_mappings():
    result = {}
    for k, v in config.get("channel_mappings", {}).items():
        try:
            result[int(k)] = int(v)
        except ValueError:
            pass
    return result

def parse_buttons(raw):
    """
    解析按钮文本，每行一行，同行多个用 | 分隔
    格式: 文字::URL | 文字2::URL2
    返回: [[{"text":..,"url":..}, ...], ...]
    """
    rows = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for btn_raw in line.split("|"):
            btn_raw = btn_raw.strip()
            if "::" in btn_raw:
                parts = btn_raw.split("::", 1)
                row.append({"text": parts[0].strip(), "url": parts[1].strip()})
        if row:
            rows.append(row)
    return rows

def build_inline_keyboard(buttons_data):
    """从按钮数据构建 InlineKeyboardMarkup"""
    if not buttons_data:
        return None
    keyboard = []
    for row in buttons_data:
        keyboard.append([InlineKeyboardButton(b["text"], url=b["url"]) for b in row])
    return InlineKeyboardMarkup(keyboard)

# ══════════════════════════════════════════════════════════════════════════════
# 内联按键设置面板
# ══════════════════════════════════════════════════════════════════════════════

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 频道映射", callback_data="menu:channels"),
         InlineKeyboardButton("🔤 替换规则", callback_data="menu:rules")],
        [InlineKeyboardButton("📢 定时广告", callback_data="menu:ads"),
         InlineKeyboardButton("👮 管理员", callback_data="menu:admins")],
        [InlineKeyboardButton("❌ 关闭", callback_data="menu:close")],
    ])

def back_kb(target="menu:main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ 返回", callback_data=target)]])

# ── 频道面板 ──────────────────────────────────────────────────────────────────
def channels_kb():
    m = get_mappings()
    rows = []
    for src, tgt in m.items():
        rows.append([InlineKeyboardButton(
            f"🗑 {src} → {tgt}", callback_data=f"ch:del:{src}"
        )])
    rows.append([
        InlineKeyboardButton("➕ 添加", callback_data="ch:add_hint"),
        InlineKeyboardButton("◀ 返回", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(rows)

def channels_text():
    m = get_mappings()
    if not m:
        return "📡 *频道映射* — 暂无\n\n点击 ➕ 添加，点击条目删除。"
    lines = ["📡 *频道映射*\n"]
    for i, (s, t) in enumerate(m.items(), 1):
        lines.append(f"{i}. `{s}` → `{t}`")
    lines.append("\n点击条目可删除。")
    return "\n".join(lines)

# ── 替换规则面板 ──────────────────────────────────────────────────────────────
def rules_kb():
    rules = config.get("replace_rules", {})
    rows = []
    for i, old in enumerate(rules):
        new = rules[old]
        label = f"🗑 「{old[:10]}」→「{(new or '[删]')[:10]}」"
        rows.append([InlineKeyboardButton(label, callback_data=f"rule:del:{i}")])
    rows.append([
        InlineKeyboardButton("➕ 添加", callback_data="rule:add_hint"),
        InlineKeyboardButton("◀ 返回", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(rows)

def rules_text():
    rules = config.get("replace_rules", {})
    if not rules:
        return "🔤 *替换规则* — 暂无\n\n点击 ➕ 添加，点击条目删除。"
    lines = ["🔤 *替换规则*\n"]
    for i, (old, new) in enumerate(rules.items(), 1):
        lines.append(f"{i}. 「{old}」→「{new or '[删除]'}」")
    lines.append("\n点击条目可删除。")
    return "\n".join(lines)

# ── 广告面板 ──────────────────────────────────────────────────────────────────
def ads_kb():
    ads = config.get("ads", [])
    rows = []
    for ad in ads:
        status = "✅" if ad.get("enabled", True) else "⏸"
        label = f"{status} {ad['text'][:18]}… ({ad['interval_minutes']}min)"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"ad:detail:{ad['id']}"),
        ])
    rows.append([
        InlineKeyboardButton("➕ 添加广告", callback_data="ad:add"),
        InlineKeyboardButton("◀ 返回", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(rows)

def ads_text():
    ads = config.get("ads", [])
    if not ads:
        return "📢 *定时广告* — 暂无\n\n点击 ➕ 添加广告。"
    lines = ["📢 *定时广告*\n"]
    for i, ad in enumerate(ads, 1):
        status = "✅ 运行中" if ad.get("enabled", True) else "⏸ 已暂停"
        lines.append(f"{i}. {ad['text'][:20]}…")
        lines.append(f"   间隔 {ad['interval_minutes']} 分钟 | {status}")
    return "\n".join(lines)

def ad_detail_kb(ad_id):
    ad = next((a for a in config.get("ads", []) if a["id"] == ad_id), None)
    if not ad:
        return back_kb("menu:ads")
    status_label = "⏸ 暂停" if ad.get("enabled", True) else "▶ 启动"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(status_label, callback_data=f"ad:toggle:{ad_id}"),
         InlineKeyboardButton("🗑 删除", callback_data=f"ad:del:{ad_id}")],
        [InlineKeyboardButton("◀ 返回", callback_data="menu:ads")],
    ])

def ad_detail_text(ad_id):
    ad = next((a for a in config.get("ads", []) if a["id"] == ad_id), None)
    if not ad:
        return "❌ 广告不存在"
    ch_list = ", ".join(str(c) for c in ad.get("channels", []))
    btn_count = sum(len(row) for row in ad.get("buttons", []))
    status = "✅ 运行中" if ad.get("enabled", True) else "⏸ 已暂停"
    return (
        f"📢 *广告详情*\n\n"
        f"状态: {status}\n"
        f"间隔: {ad['interval_minutes']} 分钟\n"
        f"目标频道: {ch_list or '（继承映射）'}\n"
        f"按钮数: {btn_count} 个\n\n"
        f"内容:\n{ad['text']}"
    )

# ── 管理员面板 ────────────────────────────────────────────────────────────────
def admins_kb():
    admins = config.get("admins", [])
    rows = []
    for uid in admins:
        rows.append([InlineKeyboardButton(f"🗑 {uid}", callback_data=f"admin:del:{uid}")])
    rows.append([
        InlineKeyboardButton("➕ 添加", callback_data="admin:add_hint"),
        InlineKeyboardButton("◀ 返回", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(rows)

def admins_text():
    admins = config.get("admins", [])
    if not admins:
        return "👮 *管理员* — 暂无\n\n点击 ➕ 添加。"
    lines = ["👮 *管理员列表*\n"]
    for uid in admins:
        lines.append(f"• `{uid}`")
    lines.append("\n点击条目可删除。")
    return "\n".join(lines)

# ── 回调路由 ──────────────────────────────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # 权限检查
    if not is_admin(uid):
        await q.answer("⛔ 无权限", show_alert=True)
        return

    # ── 主菜单导航 ────────────────────────────────────────────────────────────
    if data == "menu:main":
        await q.edit_message_text("⚙️ *设置面板*", parse_mode="Markdown", reply_markup=main_menu_kb())

    elif data == "menu:channels":
        await q.edit_message_text(channels_text(), parse_mode="Markdown", reply_markup=channels_kb())

    elif data == "menu:rules":
        await q.edit_message_text(rules_text(), parse_mode="Markdown", reply_markup=rules_kb())

    elif data == "menu:ads":
        await q.edit_message_text(ads_text(), parse_mode="Markdown", reply_markup=ads_kb())

    elif data == "menu:admins":
        await q.edit_message_text(admins_text(), parse_mode="Markdown", reply_markup=admins_kb())

    elif data == "menu:close":
        await q.edit_message_text("✅ 设置已关闭")

    # ── 频道操作 ──────────────────────────────────────────────────────────────
    elif data == "ch:add_hint":
        await q.edit_message_text(
            "📡 *添加频道映射*\n\n发送命令:\n`/addchannel 源ID 目标ID`\n\n"
            "例: `/addchannel -1001111111111 -1002222222222`",
            parse_mode="Markdown", reply_markup=back_kb("menu:channels")
        )

    elif data.startswith("ch:del:"):
        src = data.split(":", 2)[2]
        if src in config["channel_mappings"]:
            tgt = config["channel_mappings"].pop(src)
            save_config(config)
            logger.info(f"删除频道映射: {src} -> {tgt}")
        await q.edit_message_text(channels_text(), parse_mode="Markdown", reply_markup=channels_kb())

    # ── 替换规则操作 ──────────────────────────────────────────────────────────
    elif data == "rule:add_hint":
        await q.edit_message_text(
            "🔤 *添加替换规则*\n\n发送命令:\n`/addrule 原文 >> 替换文`\n\n"
            "例:\n"
            "`/addrule 旧名字 >> 新名字`\n"
            "`/addrule 广告词 >>` （右侧留空=删除该词）",
            parse_mode="Markdown", reply_markup=back_kb("menu:rules")
        )

    elif data.startswith("rule:del:"):
        idx = int(data.split(":", 2)[2])
        rules = config.get("replace_rules", {})
        keys = list(rules.keys())
        if 0 <= idx < len(keys):
            removed = keys[idx]
            del config["replace_rules"][removed]
            save_config(config)
        await q.edit_message_text(rules_text(), parse_mode="Markdown", reply_markup=rules_kb())

    # ── 广告操作 ──────────────────────────────────────────────────────────────
    elif data == "ad:add":
        context.user_data["ad_draft"] = {}
        await q.edit_message_text(
            "📢 *新建广告 (1/4)*\n\n请发送广告正文内容：",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="ad:cancel")]])
        )
        context.user_data["ad_step"] = AD_TEXT

    elif data == "ad:cancel":
        context.user_data.pop("ad_draft", None)
        context.user_data.pop("ad_step", None)
        await q.edit_message_text(ads_text(), parse_mode="Markdown", reply_markup=ads_kb())

    elif data.startswith("ad:detail:"):
        ad_id = data.split(":", 2)[2]
        await q.edit_message_text(
            ad_detail_text(ad_id), parse_mode="Markdown",
            reply_markup=ad_detail_kb(ad_id)
        )

    elif data.startswith("ad:toggle:"):
        ad_id = data.split(":", 2)[2]
        for ad in config["ads"]:
            if ad["id"] == ad_id:
                ad["enabled"] = not ad.get("enabled", True)
                save_config(config)
                # 重新注册任务
                reschedule_ad(context.application, ad)
                break
        await q.edit_message_text(
            ad_detail_text(ad_id), parse_mode="Markdown",
            reply_markup=ad_detail_kb(ad_id)
        )

    elif data.startswith("ad:del:"):
        ad_id = data.split(":", 2)[2]
        # 移除定时任务
        remove_ad_job(context.application, ad_id)
        config["ads"] = [a for a in config["ads"] if a["id"] != ad_id]
        save_config(config)
        await q.edit_message_text(ads_text(), parse_mode="Markdown", reply_markup=ads_kb())

    elif data.startswith("ad:buttons_skip:"):
        ad_id = data.split(":", 2)[2]
        # 无按钮，完成创建
        await finish_ad_creation(q, context, ad_id, buttons=[])

    elif data.startswith("ad:buttons_confirm:"):
        # 已在 user_data 里有按钮数据，完成
        ad_id = data.split(":", 2)[2]
        buttons = context.user_data.get("ad_buttons_pending", [])
        await finish_ad_creation(q, context, ad_id, buttons=buttons)

    # ── 管理员操作 ────────────────────────────────────────────────────────────
    elif data == "admin:add_hint":
        await q.edit_message_text(
            "👮 *添加管理员*\n\n发送命令:\n`/addadmin 用户ID`\n\n"
            "可发消息给 @userinfobot 获取用户 ID。",
            parse_mode="Markdown", reply_markup=back_kb("menu:admins")
        )

    elif data.startswith("admin:del:"):
        uid_to_del = int(data.split(":", 2)[2])
        if uid_to_del in config["admins"]:
            config["admins"].remove(uid_to_del)
            save_config(config)
        await q.edit_message_text(admins_text(), parse_mode="Markdown", reply_markup=admins_kb())


# ── 广告创建流程（文字消息驱动）────────────────────────────────────────────────
async def handle_ad_creation_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """捕获管理员在广告创建流程中发送的消息"""
    step = context.user_data.get("ad_step")
    if step is None:
        return  # 不在广告创建流程中

    uid = update.effective_user.id
    if not is_admin(uid):
        return

    text = update.message.text.strip()
    draft = context.user_data.setdefault("ad_draft", {})

    if step == AD_TEXT:
        draft["text"] = text
        context.user_data["ad_step"] = AD_INTERVAL
        await update.message.reply_text(
            "📢 *新建广告 (2/4)*\n\n请发送推送间隔（分钟）：\n例: `60` 代表每小时推送一次",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="ad:cancel")]])
        )

    elif step == AD_INTERVAL:
        try:
            minutes = int(text)
            if minutes < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ 请输入正整数（分钟数）")
            return
        draft["interval_minutes"] = minutes
        context.user_data["ad_step"] = AD_CHANNELS
        await update.message.reply_text(
            "📢 *新建广告 (3/4)*\n\n请发送目标频道 ID（多个用空格分隔）：\n"
            "例: `-1001111111111 -1002222222222`\n\n或发送 `all` 推送到所有已配置的目标频道",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="ad:cancel")]])
        )

    elif step == AD_CHANNELS:
        if text.lower() == "all":
            channels = list(get_mappings().values())
        else:
            try:
                channels = [int(x) for x in text.split()]
            except ValueError:
                await update.message.reply_text("❌ 格式错误，请输入数字 ID 或 `all`", parse_mode="Markdown")
                return
        draft["channels"] = channels
        context.user_data["ad_step"] = AD_BUTTONS

        # 暂存草稿 ID，用于跳过按钮时完成创建
        draft["id"] = str(uuid.uuid4())[:8]

        await update.message.reply_text(
            "📢 *新建广告 (4/4)*\n\n请发送广告按钮（可选）：\n\n"
            "格式：每行一排按钮，同排多个用 `|` 分隔\n"
            "按钮格式：`按钮文字::URL`\n\n"
            "示例：\n"
            "`点击官网::https://example.com`\n"
            "`加入频道::https://t.me/ch | 联系客服::https://t.me/admin`\n\n"
            "不需要按钮请点下方跳过。",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ 跳过（无按钮）", callback_data=f"ad:buttons_skip:{draft['id']}")],
                [InlineKeyboardButton("❌ 取消", callback_data="ad:cancel")],
            ])
        )

    elif step == AD_BUTTONS:
        buttons = parse_buttons(text)
        if not buttons:
            await update.message.reply_text("❌ 格式错误，请按示例输入，或点跳过。")
            return
        context.user_data["ad_buttons_pending"] = buttons
        ad_id = draft.get("id", str(uuid.uuid4())[:8])

        # 预览按钮
        preview_kb = build_inline_keyboard(buttons)
        confirm_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认创建", callback_data=f"ad:buttons_confirm:{ad_id}")],
            [InlineKeyboardButton("❌ 取消", callback_data="ad:cancel")],
        ])
        await update.message.reply_text(
            f"📢 *按钮预览*\n\n{draft.get('text', '')}\n\n↓ 点击下方按钮确认",
            parse_mode="Markdown",
            reply_markup=preview_kb
        )
        await update.message.reply_text("确认创建广告？", reply_markup=confirm_kb)


async def finish_ad_creation(q, context, ad_id, buttons):
    """完成广告创建，注册定时任务"""
    draft = context.user_data.get("ad_draft", {})
    ad = {
        "id": ad_id,
        "text": draft.get("text", ""),
        "interval_minutes": draft.get("interval_minutes", 60),
        "channels": draft.get("channels", []),
        "buttons": buttons,
        "enabled": True,
    }
    config["ads"].append(ad)
    save_config(config)
    context.user_data.pop("ad_draft", None)
    context.user_data.pop("ad_step", None)
    context.user_data.pop("ad_buttons_pending", None)

    # 注册定时任务
    reschedule_ad(context.application, ad)

    await q.edit_message_text(
        f"✅ *广告已创建*\n\n"
        f"间隔: {ad['interval_minutes']} 分钟\n"
        f"目标: {', '.join(str(c) for c in ad['channels']) or '（映射频道）'}\n"
        f"按钮: {sum(len(r) for r in buttons)} 个",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀ 广告列表", callback_data="menu:ads")
        ]])
    )
    logger.info(f"创建广告: {ad_id} 每{ad['interval_minutes']}分钟")

# ══════════════════════════════════════════════════════════════════════════════
# 定时广告推送
# ══════════════════════════════════════════════════════════════════════════════

def get_job_name(ad_id):
    return f"ad_{ad_id}"

def remove_ad_job(app, ad_id):
    jobs = app.job_queue.get_jobs_by_name(get_job_name(ad_id))
    for job in jobs:
        job.schedule_removal()

def reschedule_ad(app, ad):
    remove_ad_job(app, ad["id"])
    if not ad.get("enabled", True):
        return
    interval_secs = ad["interval_minutes"] * 60
    app.job_queue.run_repeating(
        send_ad_job,
        interval=interval_secs,
        first=interval_secs,
        name=get_job_name(ad["id"]),
        data=ad["id"],
    )
    logger.info(f"注册广告任务: {ad['id']} 每{ad['interval_minutes']}分钟")

async def send_ad_job(context: ContextTypes.DEFAULT_TYPE):
    ad_id = context.job.data
    ad = next((a for a in config.get("ads", []) if a["id"] == ad_id), None)
    if not ad or not ad.get("enabled", True):
        return

    reply_markup = build_inline_keyboard(ad.get("buttons", []))
    channels = ad.get("channels") or list(get_mappings().values())

    for ch_id in channels:
        try:
            await context.bot.send_message(
                chat_id=ch_id,
                text=ad["text"],
                reply_markup=reply_markup,
            )
            logger.info(f"广告已推送: {ad_id} → {ch_id}")
        except TelegramError as e:
            logger.error(f"广告推送失败 {ad_id} → {ch_id}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 普通命令
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Telegram 频道同步机器人 v3*\n\n"
        "发送 /settings 打开设置面板\n"
        "发送 /status 查看运行状态",
        parse_mode="Markdown"
    )

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ 无权限")
        return
    await update.message.reply_text(
        "⚙️ *设置面板*", parse_mode="Markdown", reply_markup=main_menu_kb()
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = get_mappings()
    r = config.get("replace_rules", {})
    ads = config.get("ads", [])
    active_ads = sum(1 for a in ads if a.get("enabled", True))
    await update.message.reply_text(
        f"✅ 机器人运行中\n\n"
        f"📡 频道映射: {len(m)} 条\n"
        f"🔤 替换规则: {len(r)} 条\n"
        f"📢 定时广告: {active_ads}/{len(ads)} 条运行中\n"
        f"👮 管理员数: {len(config.get('admins', []))} 人"
    )

@admin_only
async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("❌ 用法: /addchannel 源ID 目标ID")
        return
    src, tgt = args[0], args[1]
    try:
        int(src); int(tgt)
    except ValueError:
        await update.message.reply_text("❌ ID 必须是数字")
        return
    config["channel_mappings"][src] = tgt
    save_config(config)
    await update.message.reply_text(f"✅ 已添加: {src} → {tgt}")

@admin_only
async def cmd_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ 用法: /removechannel 源ID")
        return
    src = args[0]
    if src in config["channel_mappings"]:
        tgt = config["channel_mappings"].pop(src)
        save_config(config)
        await update.message.reply_text(f"🗑 已删除: {src} → {tgt}")
    else:
        await update.message.reply_text(f"⚠️ 未找到 {src}")

@admin_only
async def cmd_addrule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.split(None, 1)
    if len(raw) < 2 or ">>" not in raw[1]:
        await update.message.reply_text(
            "❌ 用法: /addrule 原文 >> 替换文\n"
            "例: /addrule 旧名字 >> 新名字"
        )
        return
    parts = raw[1].split(">>", 1)
    old_text, new_text = parts[0].strip(), parts[1].strip()
    if not old_text:
        await update.message.reply_text("❌ 原文不能为空")
        return
    config["replace_rules"][old_text] = new_text
    save_config(config)
    await update.message.reply_text(f"✅ 已添加: 「{old_text}」→「{new_text or '[删除]'}」")

@admin_only
async def cmd_removerule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.split(None, 1)
    if len(raw) < 2:
        await update.message.reply_text("❌ 用法: /removerule 原文")
        return
    old_text = raw[1].strip()
    if old_text in config["replace_rules"]:
        config["replace_rules"].pop(old_text)
        save_config(config)
        await update.message.reply_text(f"🗑 已删除规则: 「{old_text}」")
    else:
        await update.message.reply_text(f"⚠️ 未找到规则「{old_text}」")

@admin_only
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ 用法: /addadmin 用户ID")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID 必须是整数")
        return
    if uid not in config["admins"]:
        config["admins"].append(uid)
        save_config(config)
        await update.message.reply_text(f"✅ 已添加管理员: {uid}")
    else:
        await update.message.reply_text(f"ℹ️ {uid} 已是管理员")

@admin_only
async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ 用法: /removeadmin 用户ID")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID 必须是整数")
        return
    if uid in config["admins"]:
        config["admins"].remove(uid)
        save_config(config)
        await update.message.reply_text(f"🗑 已移除: {uid}")
    else:
        await update.message.reply_text(f"⚠️ {uid} 不是管理员")

# ── 消息转发 ──────────────────────────────────────────────────────────────────
async def forward_message(context, message, target_id):
    has_rules = bool(config.get("replace_rules"))
    if message.text:
        await context.bot.send_message(
            chat_id=target_id, text=apply_replace(message.text),
            entities=message.entities if not has_rules else None,
        )
    elif message.photo:
        await context.bot.send_photo(
            chat_id=target_id, photo=message.photo[-1].file_id,
            caption=apply_replace(message.caption),
            caption_entities=message.caption_entities if not has_rules else None,
        )
    elif message.video:
        await context.bot.send_video(
            chat_id=target_id, video=message.video.file_id,
            caption=apply_replace(message.caption),
            caption_entities=message.caption_entities if not has_rules else None,
        )
    elif message.audio:
        await context.bot.send_audio(
            chat_id=target_id, audio=message.audio.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.document:
        await context.bot.send_document(
            chat_id=target_id, document=message.document.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.animation:
        await context.bot.send_animation(
            chat_id=target_id, animation=message.animation.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.sticker:
        await context.bot.send_sticker(chat_id=target_id, sticker=message.sticker.file_id)
    elif message.voice:
        await context.bot.send_voice(
            chat_id=target_id, voice=message.voice.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.video_note:
        await context.bot.send_video_note(chat_id=target_id, video_note=message.video_note.file_id)
    elif message.poll:
        poll = message.poll
        await context.bot.send_poll(
            chat_id=target_id,
            question=apply_replace(poll.question),
            options=[apply_replace(o.text) for o in poll.options],
            is_anonymous=poll.is_anonymous, type=poll.type,
            allows_multiple_answers=poll.allows_multiple_answers,
        )
    else:
        logger.warning(f"不支持的消息类型 msg_id={message.message_id}")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        return
    mappings = get_mappings()
    if message.chat_id not in mappings:
        return
    target_id = mappings[message.chat_id]
    logger.info(f"[转发] {message.chat_id} → {target_id} msg_id={message.message_id}")
    try:
        await forward_message(context, message, target_id)
    except TelegramError as e:
        logger.error(f"转发失败: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"[错误] {context.error}")

# ── 启动 ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # 命令
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("addchannel", cmd_addchannel))
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))
    app.add_handler(CommandHandler("addrule", cmd_addrule))
    app.add_handler(CommandHandler("removerule", cmd_removerule))
    app.add_handler(CommandHandler("addadmin", cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))

    # 内联按键回调
    app.add_handler(CallbackQueryHandler(on_callback))

    # 广告创建流程（管理员私聊消息）
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_ad_creation_step
    ))

    # 频道消息转发
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    app.add_error_handler(error_handler)

    # 启动时恢复所有定时广告
    async def post_init(application):
        for ad in config.get("ads", []):
            if ad.get("enabled", True):
                reschedule_ad(application, ad)
        logger.info(
            f"机器人启动 | 频道映射:{len(get_mappings())} "
            f"| 替换规则:{len(config.get('replace_rules',{}))} "
            f"| 广告:{len(config.get('ads',[]))}"
        )

    app.post_init = post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
