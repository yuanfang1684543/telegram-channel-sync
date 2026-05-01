from __future__ import annotations
"""
Telegram 频道同步机器人 v4
全内联按键操作 · 定时广告 · 文字替换 · 多频道同步
"""
import os, json, uuid, logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ContextTypes, MessageHandler,
    CommandHandler, CallbackQueryHandler, filters,
)
from telegram.error import TelegramError

# ━━━ 日志 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ━━━ 环境变量 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN = os.getenv("BOT_TOKEN") or (_ for _ in ()).throw(ValueError("BOT_TOKEN 未设置"))
ENV_ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()
}
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))

# ━━━ 输入状态机（每用户独立）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class S:  # States
    IDLE        = "idle"
    CH_SRC      = "ch_src"       # 等待源频道 ID
    CH_TGT      = "ch_tgt"       # 等待目标频道 ID
    RULE_OLD    = "rule_old"     # 等待原文
    RULE_NEW    = "rule_new"     # 等待替换文
    ADMIN_ID    = "admin_id"     # 等待管理员 ID
    AD_TEXT     = "ad_text"      # 等待广告正文
    AD_INTERVAL = "ad_interval"  # 等待间隔分钟
    AD_CHANNELS = "ad_channels"  # 等待目标频道
    AD_BUTTONS  = "ad_buttons"   # 等待按钮定义

# ━━━ 配置管理 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _default_config() -> dict:
    return {"channel_mappings": {}, "replace_rules": {}, "admins": [], "ads": []}

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
            # 补齐缺失字段
            for k, v in _default_config().items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            logger.error(f"配置加载失败: {e}")
    return _default_config()

def save_config() -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        logger.error(f"配置保存失败: {e}")

cfg = load_config()

# 同步环境变量中的初始频道 & 管理员
for _aid in ENV_ADMIN_IDS:
    if _aid not in cfg["admins"]:
        cfg["admins"].append(_aid)
_src, _tgt = os.getenv("SOURCE_CHANNEL_ID"), os.getenv("TARGET_CHANNEL_ID")
if _src and _tgt and _src not in cfg["channel_mappings"]:
    cfg["channel_mappings"][_src] = _tgt
save_config()

# ━━━ 权限 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def is_admin(uid: int) -> bool:
    return uid in cfg["admins"] or uid in ENV_ADMIN_IDS

def get_mappings() -> dict[int, int]:
    out = {}
    for k, v in cfg["channel_mappings"].items():
        try: out[int(k)] = int(v)
        except ValueError: pass
    return out

# ━━━ 文字替换 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def apply_replace(text: str | None) -> str | None:
    if not text:
        return text
    for old, new in cfg["replace_rules"].items():
        text = text.replace(old, new)
    return text

# ━━━ 广告按钮解析 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def parse_buttons(raw: str) -> list[list[dict]]:
    """
    每行一排按钮，同排用 | 分隔，格式: 文字::URL
    返回: [[{"text":..,"url":..},...],...]
    """
    rows = []
    for line in raw.strip().splitlines():
        row = []
        for cell in line.split("|"):
            cell = cell.strip()
            if "::" in cell:
                t, u = cell.split("::", 1)
                if t.strip() and u.strip():
                    row.append({"text": t.strip(), "url": u.strip()})
        if row:
            rows.append(row)
    return rows

def build_reply_markup(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in buttons]
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  内联面板构建器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)

BACK_MAIN = [btn("◀ 主菜单", "menu:main")]

# ── 主菜单 ────────────────────────────────────────────────────────────────────
MAIN_MENU_KB = kb(
    [btn("📡 频道映射", "menu:ch"), btn("🔤 替换规则", "menu:rule")],
    [btn("📢 定时广告", "menu:ad"), btn("👮 管理员", "menu:admin")],
    [btn("📊 运行状态", "cb:status"), btn("❌ 关闭", "cb:close")],
)

def main_menu_text() -> str:
    m = get_mappings()
    ads = cfg["ads"]
    active = sum(1 for a in ads if a.get("enabled", True))
    return (
        "⚙️ *设置面板*\n\n"
        f"📡 频道映射: {len(m)} 条\n"
        f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
        f"📢 定时广告: {active}/{len(ads)} 运行中\n"
        f"👮 管理员: {len(cfg['admins'])} 人"
    )

# ── 频道面板 ──────────────────────────────────────────────────────────────────
def ch_panel() -> tuple[str, InlineKeyboardMarkup]:
    m = get_mappings()
    rows: list[list[InlineKeyboardButton]] = []
    for src, tgt in m.items():
        rows.append([btn(f"🗑 {src} → {tgt}", f"ch:del:{src}")])
    rows.append([btn("➕ 添加映射", "ch:add"), btn("◀ 返回", "menu:main")])
    text = "📡 *频道映射*\n\n" + (
        "\n".join(f"`{s}` → `{t}`" for s, t in m.items()) if m else "暂无映射"
    ) + "\n\n点击条目删除 · 点击 ➕ 添加"
    return text, InlineKeyboardMarkup(rows)

# ── 替换规则面板 ──────────────────────────────────────────────────────────────
def rule_panel() -> tuple[str, InlineKeyboardMarkup]:
    rules = cfg["replace_rules"]
    rows: list[list[InlineKeyboardButton]] = []
    for i, (old, new) in enumerate(rules.items()):
        label = f"🗑 「{old[:12]}」→「{(new or '删除')[:12]}」"
        rows.append([btn(label, f"rule:del:{i}")])
    rows.append([btn("➕ 添加规则", "rule:add"), btn("◀ 返回", "menu:main")])
    text = "🔤 *替换规则*\n\n" + (
        "\n".join(f"`{o}` → `{n or '[删除]'}`" for o, n in rules.items()) if rules else "暂无规则"
    ) + "\n\n点击条目删除 · 点击 ➕ 添加"
    return text, InlineKeyboardMarkup(rows)

# ── 广告面板 ──────────────────────────────────────────────────────────────────
def ad_panel() -> tuple[str, InlineKeyboardMarkup]:
    ads = cfg["ads"]
    rows: list[list[InlineKeyboardButton]] = []
    for ad in ads:
        ico = "✅" if ad.get("enabled", True) else "⏸"
        label = f"{ico} {ad['text'][:16]}… / {ad['interval_minutes']}min"
        rows.append([btn(label, f"ad:detail:{ad['id']}")])
    rows.append([btn("➕ 新建广告", "ad:add"), btn("◀ 返回", "menu:main")])
    lines = ["📢 *定时广告*\n"]
    if not ads:
        lines.append("暂无广告")
    for i, ad in enumerate(ads, 1):
        s = "✅" if ad.get("enabled", True) else "⏸"
        ch = ", ".join(str(c) for c in ad.get("channels", []))
        btn_n = sum(len(r) for r in ad.get("buttons", []))
        lines.append(f"{i}. {s} {ad['text'][:18]} / 每{ad['interval_minutes']}min")
        if ch: lines.append(f"   频道: {ch}")
        if btn_n: lines.append(f"   按钮: {btn_n}个")
    lines.append("\n点击条目管理 · 点击 ➕ 新建")
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def ad_detail(ad_id: str) -> tuple[str, InlineKeyboardMarkup]:
    ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
    if not ad:
        return "❌ 广告不存在", kb(BACK_MAIN)
    s = "✅ 运行中" if ad.get("enabled", True) else "⏸ 已暂停"
    ch = ", ".join(str(c) for c in ad.get("channels", [])) or "（继承映射频道）"
    btn_rows = ad.get("buttons", [])
    btn_n = sum(len(r) for r in btn_rows)
    btn_preview = ""
    for row in btn_rows:
        btn_preview += "  " + " | ".join(f"[{b['text']}]" for b in row) + "\n"
    text = (
        f"📢 *广告详情*\n\n"
        f"状态: {s}\n"
        f"间隔: 每 {ad['interval_minutes']} 分钟\n"
        f"目标: {ch}\n"
        f"按钮: {btn_n} 个\n"
        + (f"```\n{btn_preview}```\n" if btn_preview else "")
        + f"\n正文:\n{ad['text']}"
    )
    tog = "⏸ 暂停" if ad.get("enabled", True) else "▶ 启动"
    mk = kb(
        [btn(tog, f"ad:toggle:{ad_id}"), btn("🗑 删除", f"ad:del:{ad_id}")],
        [btn("◀ 广告列表", "menu:ad")],
    )
    return text, mk

# ── 管理员面板 ────────────────────────────────────────────────────────────────
def admin_panel() -> tuple[str, InlineKeyboardMarkup]:
    admins = cfg["admins"]
    rows: list[list[InlineKeyboardButton]] = [
        [btn(f"🗑 {uid}", f"admin:del:{uid}")] for uid in admins
    ]
    rows.append([btn("➕ 添加管理员", "admin:add"), btn("◀ 返回", "menu:main")])
    text = "👮 *管理员列表*\n\n" + (
        "\n".join(f"• `{uid}`" for uid in admins) if admins else "暂无管理员"
    ) + "\n\n点击条目删除 · 点击 ➕ 添加"
    return text, InlineKeyboardMarkup(rows)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  输入状态辅助
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cancel_kb(data="cb:cancel") -> InlineKeyboardMarkup:
    return kb([btn("✖ 取消", data)])

def set_state(ud: dict, state: str, **draft_fields):
    ud["state"] = state
    ud.setdefault("draft", {}).update(draft_fields)

def clear_state(ud: dict):
    ud["state"] = S.IDLE
    ud.pop("draft", None)

async def prompt(msg_or_q, text: str, reply_markup=None, parse_mode="Markdown"):
    """统一发送提示消息（支持 Message 和 CallbackQuery）"""
    if hasattr(msg_or_q, "edit_message_text"):   # CallbackQuery
        await msg_or_q.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:                                         # Message
        await msg_or_q.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  回调处理主路由
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    ud = ctx.user_data

    if not is_admin(uid):
        await q.answer("⛔ 无权限", show_alert=True)
        return

    # ── 菜单导航 ──────────────────────────────────────────────────────────────
    if data == "menu:main":
        clear_state(ud)
        await q.edit_message_text(main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB)

    elif data == "menu:ch":
        clear_state(ud)
        text, mk = ch_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:rule":
        clear_state(ud)
        text, mk = rule_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:ad":
        clear_state(ud)
        text, mk = ad_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:admin":
        clear_state(ud)
        text, mk = admin_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    # ── 通用操作 ──────────────────────────────────────────────────────────────
    elif data == "cb:status":
        m = get_mappings()
        active = sum(1 for a in cfg["ads"] if a.get("enabled", True))
        await q.edit_message_text(
            f"✅ *机器人运行中*\n\n"
            f"📡 频道映射: {len(m)} 条\n"
            f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
            f"📢 定时广告: {active}/{len(cfg['ads'])} 运行中\n"
            f"👮 管理员: {len(cfg['admins'])} 人",
            parse_mode="Markdown",
            reply_markup=kb(BACK_MAIN),
        )

    elif data == "cb:close":
        clear_state(ud)
        await q.edit_message_text("✅ 面板已关闭。发送 /settings 重新打开。")

    elif data == "cb:cancel":
        clear_state(ud)
        await q.edit_message_text(main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB)

    # ── 频道映射操作 ──────────────────────────────────────────────────────────
    elif data == "ch:add":
        set_state(ud, S.CH_SRC)
        await q.edit_message_text(
            "📡 *添加频道映射 — 第 1 步*\n\n请发送*源频道 ID*（要监听的频道）\n\n"
            "格式: `-1001234567890`\n获取方式: 转发频道消息给 @userinfobot",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("ch:del:"):
        src = data.split(":", 2)[2]
        tgt = cfg["channel_mappings"].pop(src, None)
        save_config()
        if tgt:
            remove_ad_jobs_for_channel(ctx.application, int(src))
            logger.info(f"删除频道映射: {src} → {tgt}")
        text, mk = ch_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    # ── 替换规则操作 ──────────────────────────────────────────────────────────
    elif data == "rule:add":
        set_state(ud, S.RULE_OLD)
        await q.edit_message_text(
            "🔤 *添加替换规则 — 第 1 步*\n\n请发送*要被替换的原文*：",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("rule:del:"):
        idx = int(data.split(":", 2)[2])
        keys = list(cfg["replace_rules"].keys())
        if 0 <= idx < len(keys):
            removed = keys[idx]
            del cfg["replace_rules"][removed]
            save_config()
            logger.info(f"删除替换规则: {removed}")
        text, mk = rule_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    # ── 广告操作 ──────────────────────────────────────────────────────────────
    elif data == "ad:add":
        set_state(ud, S.AD_TEXT, ad_id=str(uuid.uuid4())[:8])
        await q.edit_message_text(
            "📢 *新建广告 — 第 1/4 步*\n\n请发送*广告正文*：",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("ad:detail:"):
        ad_id = data[10:]
        text, mk = ad_detail(ad_id)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ad:toggle:"):
        ad_id = data[10:]
        ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
        if ad:
            ad["enabled"] = not ad.get("enabled", True)
            save_config()
            reschedule_ad(ctx.application, ad)
            logger.info(f"广告 {ad_id} {'启动' if ad['enabled'] else '暂停'}")
        text, mk = ad_detail(ad_id)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ad:del:"):
        ad_id = data[7:]
        remove_ad_job(ctx.application, ad_id)
        cfg["ads"] = [a for a in cfg["ads"] if a["id"] != ad_id]
        save_config()
        logger.info(f"删除广告: {ad_id}")
        text, mk = ad_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ad:skip_buttons:"):
        ad_id = data[16:]
        await _save_ad(ctx.application, q, ud, ad_id, buttons=[])

    elif data.startswith("ad:confirm_buttons:"):
        ad_id = data[19:]
        buttons = ud.get("draft", {}).get("buttons_pending", [])
        await _save_ad(ctx.application, q, ud, ad_id, buttons=buttons)

    # ── 管理员操作 ────────────────────────────────────────────────────────────
    elif data == "admin:add":
        set_state(ud, S.ADMIN_ID)
        await q.edit_message_text(
            "👮 *添加管理员*\n\n请发送用户的 Telegram *数字 ID*：\n"
            "（可让对方发消息给 @userinfobot 获取）",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("admin:del:"):
        uid_del = int(data[10:])
        if uid_del in cfg["admins"]:
            cfg["admins"].remove(uid_del)
            save_config()
            logger.info(f"删除管理员: {uid_del}")
        text, mk = admin_panel()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  私聊消息状态路由
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def handle_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    ud = ctx.user_data
    state = ud.get("state", S.IDLE)
    if state == S.IDLE:
        return

    text = update.message.text.strip()
    msg = update.message

    # ── 频道映射流程 ──────────────────────────────────────────────────────────
    if state == S.CH_SRC:
        if not _is_channel_id(text):
            await msg.reply_text("❌ 格式错误，请输入数字 ID（如 `-1001234567890`）", parse_mode="Markdown")
            return
        set_state(ud, S.CH_TGT, src=text)
        await msg.reply_text(
            f"📡 *添加频道映射 — 第 2 步*\n\n源: `{text}`\n\n现在请发送*目标频道 ID*（转发目的地）：",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif state == S.CH_TGT:
        if not _is_channel_id(text):
            await msg.reply_text("❌ 格式错误，请输入数字 ID（如 `-1001234567890`）", parse_mode="Markdown")
            return
        src = ud["draft"]["src"]
        cfg["channel_mappings"][src] = text
        save_config()
        clear_state(ud)
        logger.info(f"添加频道映射: {src} → {text}")
        await msg.reply_text(
            f"✅ *已添加频道映射*\n\n`{src}` → `{text}`",
            parse_mode="Markdown",
            reply_markup=kb([btn("📡 频道管理", "menu:ch"), btn("⚙️ 主菜单", "menu:main")]),
        )

    # ── 替换规则流程 ──────────────────────────────────────────────────────────
    elif state == S.RULE_OLD:
        set_state(ud, S.RULE_NEW, rule_old=text)
        await msg.reply_text(
            f"🔤 *添加替换规则 — 第 2 步*\n\n原文: `{text}`\n\n"
            "现在请发送*替换后的文字*：\n（发送空格或 `-` 表示删除该词）",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif state == S.RULE_NEW:
        old = ud["draft"]["rule_old"]
        new = "" if text in ("-", " ") else text
        cfg["replace_rules"][old] = new
        save_config()
        clear_state(ud)
        logger.info(f"添加替换规则: '{old}' → '{new}'")
        display = new if new else "[删除该词]"
        await msg.reply_text(
            f"✅ *已添加替换规则*\n\n`{old}` → `{display}`",
            parse_mode="Markdown",
            reply_markup=kb([btn("🔤 规则管理", "menu:rule"), btn("⚙️ 主菜单", "menu:main")]),
        )

    # ── 管理员添加流程 ────────────────────────────────────────────────────────
    elif state == S.ADMIN_ID:
        try:
            new_admin = int(text)
        except ValueError:
            await msg.reply_text("❌ 请输入纯数字用户 ID")
            return
        if new_admin not in cfg["admins"]:
            cfg["admins"].append(new_admin)
            save_config()
            logger.info(f"添加管理员: {new_admin}")
            result = f"✅ 已添加管理员: `{new_admin}`"
        else:
            result = f"ℹ️ `{new_admin}` 已是管理员"
        clear_state(ud)
        await msg.reply_text(
            result, parse_mode="Markdown",
            reply_markup=kb([btn("👮 管理员", "menu:admin"), btn("⚙️ 主菜单", "menu:main")]),
        )

    # ── 广告创建流程 ──────────────────────────────────────────────────────────
    elif state == S.AD_TEXT:
        set_state(ud, S.AD_INTERVAL, ad_text=text)
        await msg.reply_text(
            "📢 *新建广告 — 第 2/4 步*\n\n"
            "请发送*推送间隔*（分钟）：\n\n"
            "示例: `60` = 每小时推送一次\n`1440` = 每天推送一次",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif state == S.AD_INTERVAL:
        try:
            mins = int(text)
            assert mins >= 1
        except (ValueError, AssertionError):
            await msg.reply_text("❌ 请输入正整数（分钟），最小为 1")
            return
        set_state(ud, S.AD_CHANNELS, ad_interval=mins)
        m = get_mappings()
        ch_hint = "\n".join(f"`{t}`" for t in m.values()) if m else "（暂无映射频道）"
        await msg.reply_text(
            f"📢 *新建广告 — 第 3/4 步*\n\n"
            "请发送*目标频道 ID*（可多个，空格分隔）\n"
            "或发送 `all` 推送到所有映射目标频道\n\n"
            f"当前映射目标频道:\n{ch_hint}",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif state == S.AD_CHANNELS:
        if text.lower() == "all":
            channels = list(get_mappings().values())
        else:
            try:
                channels = [int(x) for x in text.split() if x]
            except ValueError:
                await msg.reply_text("❌ 格式错误，请输入数字 ID 或 `all`", parse_mode="Markdown")
                return
        if not channels:
            await msg.reply_text("⚠️ 没有找到目标频道，请检查配置或手动输入 ID")
            return
        ad_id = ud["draft"].get("ad_id", str(uuid.uuid4())[:8])
        set_state(ud, S.AD_BUTTONS, ad_channels=channels, ad_id=ad_id)
        await msg.reply_text(
            "📢 *新建广告 — 第 4/4 步（可选）*\n\n"
            "请发送广告*内联按钮*定义，或点击跳过\n\n"
            "格式（每行一排，`|` 分隔同排多个）:\n"
            "```\n"
            "按钮文字::https://example.com\n"
            "加入频道::https://t.me/ch | 联系客服::https://t.me/admin\n"
            "```",
            parse_mode="Markdown",
            reply_markup=kb(
                [btn("⏭ 跳过（无按钮）", f"ad:skip_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )

    elif state == S.AD_BUTTONS:
        buttons = parse_buttons(text)
        if not buttons:
            await msg.reply_text(
                "❌ 格式错误，请按示例输入，或点击跳过\n\n"
                "格式: `按钮文字::URL`\n同排多个用 `|` 分隔",
                parse_mode="Markdown",
            )
            return
        ad_id = ud["draft"].get("ad_id", str(uuid.uuid4())[:8])
        ud["draft"]["buttons_pending"] = buttons
        # 预览广告效果
        ad_text = ud["draft"].get("ad_text", "")
        preview_mk = build_reply_markup(buttons)
        await msg.reply_text(
            f"👇 *广告预览*\n\n{ad_text}",
            parse_mode="Markdown",
            reply_markup=preview_mk,
        )
        await msg.reply_text(
            "效果如上，确认创建？",
            reply_markup=kb(
                [btn("✅ 确认创建", f"ad:confirm_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )


async def _save_ad(app, q, ud: dict, ad_id: str, buttons: list):
    """完成广告创建并注册定时任务"""
    draft = ud.get("draft", {})
    ad = {
        "id": ad_id,
        "text": draft.get("ad_text", ""),
        "interval_minutes": draft.get("ad_interval", 60),
        "channels": draft.get("ad_channels", []),
        "buttons": buttons,
        "enabled": True,
    }
    cfg["ads"].append(ad)
    save_config()
    clear_state(ud)
    reschedule_ad(app, ad)
    logger.info(f"创建广告: {ad_id} 每{ad['interval_minutes']}分钟")

    ch_str = ", ".join(str(c) for c in ad["channels"])
    btn_n = sum(len(r) for r in buttons)
    await q.edit_message_text(
        f"✅ *广告已创建*\n\n"
        f"间隔: 每 {ad['interval_minutes']} 分钟\n"
        f"目标: {ch_str}\n"
        f"按钮: {btn_n} 个\n\n"
        f"正文: {ad['text'][:50]}{'…' if len(ad['text'])>50 else ''}",
        parse_mode="Markdown",
        reply_markup=kb([btn("📢 广告管理", "menu:ad"), btn("⚙️ 主菜单", "menu:main")]),
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  定时广告任务管理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _job_name(ad_id: str) -> str:
    return f"ad_{ad_id}"

def remove_ad_job(app: Application, ad_id: str):
    for job in app.job_queue.get_jobs_by_name(_job_name(ad_id)):
        job.schedule_removal()

def remove_ad_jobs_for_channel(app: Application, ch_id: int):
    """删除频道时同步清理含该频道的广告任务（不删广告，只对应调整）"""
    pass  # 广告任务保留，下次发送时若频道无效会报错日志

def reschedule_ad(app: Application, ad: dict):
    remove_ad_job(app, ad["id"])
    if not ad.get("enabled", True):
        return
    secs = ad["interval_minutes"] * 60
    app.job_queue.run_repeating(
        _send_ad,
        interval=secs,
        first=secs,
        name=_job_name(ad["id"]),
        data=ad["id"],
    )
    logger.info(f"注册广告任务: {ad['id']} 间隔{ad['interval_minutes']}分钟")

async def _send_ad(ctx: ContextTypes.DEFAULT_TYPE):
    ad_id = ctx.job.data
    ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
    if not ad or not ad.get("enabled", True):
        return

    markup = build_reply_markup(ad.get("buttons", []))
    channels = ad.get("channels") or list(get_mappings().values())

    for ch in channels:
        try:
            await ctx.bot.send_message(chat_id=ch, text=ad["text"], reply_markup=markup)
            logger.info(f"广告推送: {ad_id} → {ch}")
        except TelegramError as e:
            logger.error(f"广告推送失败: {ad_id} → {ch}: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  消息转发
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _forward(ctx: ContextTypes.DEFAULT_TYPE, msg, target: int):
    has_rules = bool(cfg["replace_rules"])
    cap = apply_replace(msg.caption)
    cap_ent = msg.caption_entities if not has_rules else None

    if msg.text:
        await ctx.bot.send_message(
            chat_id=target, text=apply_replace(msg.text),
            entities=msg.entities if not has_rules else None,
        )
    elif msg.photo:
        await ctx.bot.send_photo(target, msg.photo[-1].file_id, caption=cap, caption_entities=cap_ent)
    elif msg.video:
        await ctx.bot.send_video(target, msg.video.file_id, caption=cap, caption_entities=cap_ent)
    elif msg.audio:
        await ctx.bot.send_audio(target, msg.audio.file_id, caption=cap)
    elif msg.document:
        await ctx.bot.send_document(target, msg.document.file_id, caption=cap)
    elif msg.animation:
        await ctx.bot.send_animation(target, msg.animation.file_id, caption=cap)
    elif msg.sticker:
        await ctx.bot.send_sticker(target, msg.sticker.file_id)
    elif msg.voice:
        await ctx.bot.send_voice(target, msg.voice.file_id, caption=cap)
    elif msg.video_note:
        await ctx.bot.send_video_note(target, msg.video_note.file_id)
    elif msg.poll:
        p = msg.poll
        await ctx.bot.send_poll(
            target, question=apply_replace(p.question),
            options=[apply_replace(o.text) for o in p.options],
            is_anonymous=p.is_anonymous, type=p.type,
            allows_multiple_answers=p.allows_multiple_answers,
        )
    else:
        logger.warning(f"不支持的消息类型 msg_id={msg.message_id}")

async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    mappings = get_mappings()
    if msg.chat_id not in mappings:
        return
    target = mappings[msg.chat_id]
    logger.info(f"[转发] {msg.chat_id}→{target} id={msg.message_id}")
    try:
        await _forward(ctx, msg, target)
    except TelegramError as e:
        logger.error(f"转发失败: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  命令处理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Telegram 频道同步机器人 v4*\n\n"
        "发送 /settings 打开可视化设置面板\n"
        "发送 /status 查看运行状态\n\n"
        "💡 所有配置均可在面板内通过按键完成，无需记忆命令。",
        parse_mode="Markdown",
    )

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ 无权限")
        return
    ctx.user_data["state"] = S.IDLE
    await update.message.reply_text(
        main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = get_mappings()
    active = sum(1 for a in cfg["ads"] if a.get("enabled", True))
    await update.message.reply_text(
        f"✅ *机器人运行中*\n\n"
        f"📡 频道映射: {len(m)} 条\n"
        f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
        f"📢 定时广告: {active}/{len(cfg['ads'])} 运行中\n"
        f"👮 管理员: {len(cfg['admins'])} 人",
        parse_mode="Markdown",
    )

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"[错误] {ctx.error}", exc_info=ctx.error)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  工具函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_channel_id(s: str) -> bool:
    return s.lstrip("-").isdigit()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  启动
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _post_init(app: Application):
    """启动时恢复所有定时广告"""
    restored = 0
    for ad in cfg.get("ads", []):
        if ad.get("enabled", True):
            reschedule_ad(app, ad)
            restored += 1
    logger.info(
        f"🚀 机器人已启动 | 频道:{len(get_mappings())} "
        f"| 规则:{len(cfg['replace_rules'])} "
        f"| 广告:{restored}/{len(cfg['ads'])} 已恢复"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # 命令（仅作为快捷入口）
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status",   cmd_status))

    # 内联按键
    app.add_handler(CallbackQueryHandler(on_callback))

    # 私聊文字输入（状态路由）
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_text,
    ))

    # 频道消息转发
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    app.add_error_handler(error_handler)
    app.post_init = _post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
