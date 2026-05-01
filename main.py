from __future__ import annotations
"""
Telegram 频道同步机器人 v5
- 自动识别机器人所在频道
- 首次运行强制引导设置向导
- 频道角色选择（源/目标）
- 全内联按键操作
- 定时广告 + 文字替换
"""
import os, json, uuid, logging
from pathlib import Path
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatMemberAdministrator, ChatMemberOwner, Chat,
)
from telegram.ext import (
    Application, ContextTypes, MessageHandler,
    CommandHandler, CallbackQueryHandler,
    ChatMemberHandler, filters,
)
from telegram.error import TelegramError

# ━━━ 日志 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ━━━ 环境变量 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 未设置")

ENV_ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))

# ━━━ 输入状态 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class S:
    IDLE        = "idle"
    # 向导
    WIZ_ADMIN   = "wiz_admin"    # 向导：输入管理员ID
    WIZ_SRC     = "wiz_src"     # 向导：输入源频道ID
    WIZ_TGT     = "wiz_tgt"     # 向导：输入目标频道ID
    # 频道
    CH_SRC      = "ch_src"
    CH_TGT      = "ch_tgt"
    # 规则
    RULE_OLD    = "rule_old"
    RULE_NEW    = "rule_new"
    # 管理员
    ADMIN_ID    = "admin_id"
    # 广告
    AD_TEXT     = "ad_text"
    AD_INTERVAL = "ad_interval"
    AD_CHANNELS = "ad_channels"
    AD_BUTTONS  = "ad_buttons"

# ━━━ 配置 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _default() -> dict:
    return {
        "setup_complete": False,
        "channel_mappings": {},   # str(src_id) → str(tgt_id)
        "known_channels": {},     # str(ch_id) → {"title":..,"role":null/src/tgt}
        "replace_rules": {},
        "admins": [],
        "ads": [],
    }

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
            for k, v in _default().items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            logger.error(f"配置加载失败: {e}")
    return _default()

def save_config() -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        logger.error(f"配置保存失败: {e}")

cfg = load_config()

# 从环境变量同步
for _aid in ENV_ADMIN_IDS:
    if _aid not in cfg["admins"]:
        cfg["admins"].append(_aid)
_src0, _tgt0 = os.getenv("SOURCE_CHANNEL_ID"), os.getenv("TARGET_CHANNEL_ID")
if _src0 and _tgt0 and _src0 not in cfg["channel_mappings"]:
    cfg["channel_mappings"][_src0] = _tgt0
    if cfg["channel_mappings"]:
        cfg["setup_complete"] = True
save_config()

# ━━━ 工具函数 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def is_admin(uid: int) -> bool:
    return uid in cfg["admins"] or uid in ENV_ADMIN_IDS

def get_mappings() -> dict[int, int]:
    out = {}
    for k, v in cfg["channel_mappings"].items():
        try: out[int(k)] = int(v)
        except ValueError: pass
    return out

def apply_replace(text: str | None) -> str | None:
    if not text: return text
    for old, new in cfg["replace_rules"].items():
        text = text.replace(old, new)
    return text

def known_ch_list(role: str | None = None) -> list[dict]:
    """返回已知频道列表，可按角色过滤"""
    result = []
    for cid, info in cfg["known_channels"].items():
        if role is None or info.get("role") == role:
            result.append({"id": int(cid), "title": info.get("title", cid), "role": info.get("role")})
    return result

def ch_label(cid: int) -> str:
    info = cfg["known_channels"].get(str(cid), {})
    return info.get("title") or str(cid)

def _is_id(s: str) -> bool:
    return s.lstrip("-").isdigit()

def parse_buttons(raw: str) -> list[list[dict]]:
    rows = []
    for line in raw.strip().splitlines():
        row = []
        for cell in line.split("|"):
            cell = cell.strip()
            if "::" in cell:
                t, u = cell.split("::", 1)
                if t.strip() and u.strip():
                    row.append({"text": t.strip(), "url": u.strip()})
        if row: rows.append(row)
    return rows

def build_markup(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons: return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in buttons]
    )

# ━━━ 按键构建器 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def kb(*rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)

def cancel_kb(back: str = "cb:cancel") -> InlineKeyboardMarkup:
    return kb([btn("✖ 取消", back)])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  首次引导设置向导
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WIZARD_STEPS = {
    "admin":   "第 1 步：设置管理员",
    "source":  "第 2 步：设置源频道（监听）",
    "target":  "第 3 步：设置目标频道（转发）",
    "confirm": "第 4 步：确认完成",
}

def wizard_progress(current: str) -> str:
    steps = list(WIZARD_STEPS.keys())
    icons = []
    found = False
    for s in steps:
        if s == current:
            icons.append(f"🔵 {WIZARD_STEPS[s]}")
            found = True
        elif found:
            icons.append(f"⚪ {WIZARD_STEPS[s]}")
        else:
            icons.append(f"✅ {WIZARD_STEPS[s]}")
    return "\n".join(icons)

async def show_wizard_admin(target, ud: dict, edit=False):
    """向导步骤1：管理员设置"""
    ud["state"] = S.WIZ_ADMIN
    existing = cfg["admins"]
    text = (
        f"🧙 *首次设置向导*\n\n{wizard_progress('admin')}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "请发送你的 Telegram *数字 ID* 以设为管理员\n\n"
        "获取方式：发消息给 @userinfobot\n\n"
        + (f"当前管理员: " + ", ".join(f"`{a}`" for a in existing) if existing else "⚠️ 暂无管理员")
    )
    mk = kb(
        [btn("➡ 已有管理员，跳过", "wiz:skip_admin")] if existing else [],
    )
    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=mk)

async def show_wizard_source(target, ud: dict, edit=False):
    """向导步骤2：源频道设置"""
    ud["state"] = S.WIZ_SRC
    known_src = known_ch_list()
    src_mapped = [str(k) for k in get_mappings().keys()]
    lines = ["🧙 *首次设置向导*\n\n" + wizard_progress("source") + "\n\n━━━━━━━━━━━━━━━\n"]
    rows = []

    if known_src:
        lines.append("机器人已加入以下频道，选择作为*源频道*（监听消息来源）：\n")
        for ch in known_src:
            cid = str(ch["id"])
            role_icon = {"src": "📤", "tgt": "📥"}.get(ch["role"], "📡")
            already = " ✅" if cid in src_mapped else ""
            rows.append([btn(f"{role_icon} {ch['title']}{already}", f"wiz:src:{cid}")])
    else:
        lines.append("⚠️ 机器人尚未加入任何频道\n请先将机器人添加为频道管理员\n")

    lines.append("\n或直接发送频道 ID（格式：`-1001234567890`）")
    mk = InlineKeyboardMarkup(rows + [[btn("⬅ 返回", "wiz:back_admin")]])
    text = "\n".join(lines)

    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=mk)

async def show_wizard_target(target, ud: dict, src_id: str, edit=False):
    """向导步骤3：目标频道设置"""
    ud["state"] = S.WIZ_TGT
    ud.setdefault("draft", {})["wiz_src"] = src_id
    known = known_ch_list()
    tgt_mapped = [str(v) for v in get_mappings().values()]
    src_title = ch_label(int(src_id))
    lines = [
        "🧙 *首次设置向导*\n\n" + wizard_progress("target") + "\n\n━━━━━━━━━━━━━━━\n",
        f"源频道: `{src_title}`\n\n"
        "请选择或输入*目标频道*（消息转发至此）：\n"
    ]
    rows = []
    for ch in known:
        if str(ch["id"]) == src_id: continue
        role_icon = {"src": "📤", "tgt": "📥"}.get(ch["role"], "📡")
        already = " ✅" if str(ch["id"]) in tgt_mapped else ""
        rows.append([btn(f"{role_icon} {ch['title']}{already}", f"wiz:tgt:{ch['id']}")])
    lines.append("\n或直接发送频道 ID（格式：`-1001234567890`）")
    mk = InlineKeyboardMarkup(rows + [[btn("⬅ 返回", "wiz:back_source")]])

    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=mk)
    else:
        await target.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=mk)

async def show_wizard_confirm(target, ud: dict, src_id: str, tgt_id: str, edit=False):
    """向导步骤4：确认"""
    src_title = ch_label(int(src_id))
    tgt_title = ch_label(int(tgt_id))
    text = (
        f"🧙 *首次设置向导*\n\n{wizard_progress('confirm')}\n\n━━━━━━━━━━━━━━━\n\n"
        "请确认以下配置：\n\n"
        f"📤 源频道: `{src_title}` (`{src_id}`)\n"
        f"📥 目标频道: `{tgt_title}` (`{tgt_id}`)\n"
        f"👮 管理员: {len(cfg['admins'])} 人\n\n"
        "确认后机器人将开始监听并转发消息。"
    )
    ud.setdefault("draft", {}).update({"wiz_src": src_id, "wiz_tgt": tgt_id})
    mk = kb(
        [btn("✅ 确认完成设置", f"wiz:confirm:{src_id}:{tgt_id}")],
        [btn("⬅ 重新选择频道", "wiz:back_source")],
    )
    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=mk)
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=mk)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  主菜单面板
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main_menu_text() -> str:
    m = get_mappings()
    active_ads = sum(1 for a in cfg["ads"] if a.get("enabled", True))
    known = cfg.get("known_channels", {})
    return (
        "⚙️ *设置面板*\n\n"
        f"📡 频道映射: {len(m)} 条\n"
        f"🤖 已知频道: {len(known)} 个\n"
        f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
        f"📢 定时广告: {active_ads}/{len(cfg['ads'])} 运行中\n"
        f"👮 管理员: {len(cfg['admins'])} 人"
    )

MAIN_MENU_KB = kb(
    [btn("📡 频道映射", "menu:ch"),  btn("🤖 已知频道", "menu:known")],
    [btn("🔤 替换规则", "menu:rule"), btn("📢 定时广告",  "menu:ad")],
    [btn("👮 管理员",   "menu:admin"), btn("📊 运行状态", "cb:status")],
    [btn("❌ 关闭",     "cb:close")],
)

# ━━━ 频道映射面板 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ch_panel() -> tuple[str, InlineKeyboardMarkup]:
    m = get_mappings()
    rows = []
    for src, tgt in m.items():
        sl, tl = ch_label(src), ch_label(tgt)
        rows.append([btn(f"🗑 {sl} → {tl}", f"ch:del:{src}")])
    rows.append([btn("➕ 添加映射", "ch:add"), btn("◀ 返回", "menu:main")])
    txt = "📡 *频道映射*\n\n" + (
        "\n".join(f"• `{ch_label(s)}` → `{ch_label(t)}`" for s, t in m.items()) or "暂无映射"
    ) + "\n\n_点击条目删除 · 点击 ➕ 添加_"
    return txt, InlineKeyboardMarkup(rows)

# ━━━ 已知频道面板 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def known_panel() -> tuple[str, InlineKeyboardMarkup]:
    known = cfg.get("known_channels", {})
    rows = []
    for cid, info in known.items():
        role = info.get("role")
        role_icon = {"src": "📤 源", "tgt": "📥 目标"}.get(role, "📡 未分配")
        rows.append([
            btn(f"{role_icon} | {info.get('title', cid)}", f"known:detail:{cid}")
        ])
    rows.append([btn("🔄 刷新", "menu:known"), btn("◀ 返回", "menu:main")])
    txt = (
        "🤖 *已知频道*\n\n"
        "_机器人加入的所有频道，点击设置角色_\n\n"
        + ("\n".join(
            f"• {info.get('title', cid)} — "
            + {"src": "📤 源频道", "tgt": "📥 目标频道"}.get(info.get("role"), "未分配")
            for cid, info in known.items()
        ) or "暂无已知频道\n\n将机器人添加为频道管理员后自动识别")
    )
    return txt, InlineKeyboardMarkup(rows)

def known_detail(cid: str) -> tuple[str, InlineKeyboardMarkup]:
    info = cfg["known_channels"].get(cid, {})
    title = info.get("title", cid)
    role = info.get("role")
    role_text = {"src": "📤 源频道（监听）", "tgt": "📥 目标频道（转发）"}.get(role, "未分配")
    txt = f"📡 *{title}*\n\nID: `{cid}`\n当前角色: {role_text}"
    mk = kb(
        [btn("📤 设为源频道", f"known:set_src:{cid}"),
         btn("📥 设为目标频道", f"known:set_tgt:{cid}")],
        [btn("🗑 移除记录", f"known:del:{cid}"), btn("◀ 返回", "menu:known")],
    )
    return txt, mk

# ━━━ 替换规则面板 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def rule_panel() -> tuple[str, InlineKeyboardMarkup]:
    rules = cfg["replace_rules"]
    rows = [[btn(f"🗑 「{o[:12]}」→「{(n or '删')[:12]}」", f"rule:del:{i}")]
            for i, (o, n) in enumerate(rules.items())]
    rows.append([btn("➕ 添加规则", "rule:add"), btn("◀ 返回", "menu:main")])
    txt = "🔤 *替换规则*\n\n" + (
        "\n".join(f"• `{o}` → `{n or '[删除]'}`" for o, n in rules.items()) or "暂无规则"
    ) + "\n\n_点击条目删除 · 点击 ➕ 添加_"
    return txt, InlineKeyboardMarkup(rows)

# ━━━ 广告面板 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ad_panel() -> tuple[str, InlineKeyboardMarkup]:
    ads = cfg["ads"]
    rows = []
    for ad in ads:
        ico = "✅" if ad.get("enabled", True) else "⏸"
        rows.append([btn(f"{ico} {ad['text'][:16]}… / {ad['interval_minutes']}min", f"ad:detail:{ad['id']}")])
    rows.append([btn("➕ 新建广告", "ad:add"), btn("◀ 返回", "menu:main")])
    lines = ["📢 *定时广告*\n"]
    if not ads:
        lines.append("暂无广告")
    for i, ad in enumerate(ads, 1):
        s = "✅" if ad.get("enabled", True) else "⏸"
        btn_n = sum(len(r) for r in ad.get("buttons", []))
        ch_titles = [ch_label(c) for c in ad.get("channels", [])]
        lines.append(f"{i}. {s} {ad['text'][:20]}")
        lines.append(f"   ⏱ 每{ad['interval_minutes']}分钟 | 🔘 {btn_n}个按钮")
        if ch_titles: lines.append(f"   📡 {', '.join(ch_titles[:2])}")
    lines.append("\n_点击条目管理 · 点击 ➕ 新建_")
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def ad_detail(ad_id: str) -> tuple[str, InlineKeyboardMarkup]:
    ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
    if not ad:
        return "❌ 广告不存在", kb([btn("◀ 返回", "menu:ad")])
    s = "✅ 运行中" if ad.get("enabled", True) else "⏸ 已暂停"
    ch = ", ".join(ch_label(c) for c in ad.get("channels", [])) or "（继承映射目标）"
    btn_rows = ad.get("buttons", [])
    btn_preview = "\n".join(
        "  " + " | ".join(f"[{b['text']}]" for b in row) for row in btn_rows
    )
    txt = (
        f"📢 *广告详情*\n\n状态: {s}\n"
        f"间隔: 每 {ad['interval_minutes']} 分钟\n"
        f"目标: {ch}\n"
        f"按钮: {sum(len(r) for r in btn_rows)} 个"
        + (f"\n```\n{btn_preview}\n```" if btn_preview else "")
        + f"\n\n正文:\n{ad['text']}"
    )
    tog = "⏸ 暂停" if ad.get("enabled", True) else "▶ 启动"
    mk = kb(
        [btn(tog, f"ad:toggle:{ad_id}"), btn("🗑 删除", f"ad:del:{ad_id}")],
        [btn("◀ 广告列表", "menu:ad")],
    )
    return txt, mk

# ━━━ 管理员面板 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def admin_panel() -> tuple[str, InlineKeyboardMarkup]:
    admins = cfg["admins"]
    rows = [[btn(f"🗑 {uid}", f"admin:del:{uid}")] for uid in admins]
    rows.append([btn("➕ 添加管理员", "admin:add"), btn("◀ 返回", "menu:main")])
    txt = "👮 *管理员*\n\n" + (
        "\n".join(f"• `{a}`" for a in admins) or "暂无管理员"
    ) + "\n\n_点击条目删除 · 点击 ➕ 添加_"
    return txt, InlineKeyboardMarkup(rows)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  频道成员变化监听（自动识别频道）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """机器人被添加或移除频道时触发"""
    change = update.my_chat_member
    chat = change.chat
    new_status = change.new_chat_member

    # 只处理频道（channel）
    if chat.type not in (Chat.CHANNEL, Chat.SUPERGROUP):
        return

    cid = str(chat.id)
    title = chat.title or cid

    is_admin_now = isinstance(new_status, (ChatMemberAdministrator, ChatMemberOwner))
    was_admin = isinstance(change.old_chat_member, (ChatMemberAdministrator, ChatMemberOwner))

    if is_admin_now and not was_admin:
        # 机器人刚被设为管理员（新加入或权限提升）
        logger.info(f"机器人已加入频道: {title} ({cid})")
        if cid not in cfg["known_channels"]:
            cfg["known_channels"][cid] = {"title": title, "role": None}
        else:
            cfg["known_channels"][cid]["title"] = title
        save_config()

        # 通知所有管理员
        notification = (
            f"🔔 *检测到新频道*\n\n"
            f"机器人已成为频道管理员：\n"
            f"📡 *{title}*\n`{cid}`\n\n"
            f"请前往 /settings → 🤖 已知频道 设置其角色，\n"
            f"或在 📡 频道映射 中添加同步配置。"
        )
        for admin_id in cfg["admins"]:
            try:
                await ctx.bot.send_message(
                    admin_id, notification, parse_mode="Markdown",
                    reply_markup=kb(
                        [btn("📤 设为源频道", f"known:set_src:{cid}"),
                         btn("📥 设为目标频道", f"known:set_tgt:{cid}")],
                        [btn("⚙️ 打开设置", "menu:known")],
                    ),
                )
            except TelegramError as e:
                logger.warning(f"通知管理员 {admin_id} 失败: {e}")

    elif not is_admin_now and was_admin:
        # 机器人被移除或降权
        logger.info(f"机器人已离开频道: {title} ({cid})")
        if cid in cfg["known_channels"]:
            cfg["known_channels"][cid]["role"] = None
        save_config()
        for admin_id in cfg["admins"]:
            try:
                await ctx.bot.send_message(
                    admin_id,
                    f"⚠️ *频道权限变更*\n\n机器人已失去 *{title}* 的管理员权限。\n"
                    f"该频道的同步功能可能已停止。",
                    parse_mode="Markdown",
                )
            except TelegramError:
                pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  回调主路由
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

    # ── 向导回调 ──────────────────────────────────────────────────────────────
    if data == "wiz:skip_admin":
        await show_wizard_source(q, ud, edit=True)

    elif data == "wiz:back_admin":
        await show_wizard_admin(q, ud, edit=True)

    elif data == "wiz:back_source":
        await show_wizard_source(q, ud, edit=True)

    elif data.startswith("wiz:src:"):
        src_id = data[8:]
        await show_wizard_target(q, ud, src_id, edit=True)

    elif data.startswith("wiz:tgt:"):
        tgt_id = data[8:]
        src_id = ud.get("draft", {}).get("wiz_src", "")
        if src_id:
            await show_wizard_confirm(q, ud, src_id, tgt_id, edit=True)

    elif data.startswith("wiz:confirm:"):
        _, _, src_id, tgt_id = data.split(":", 3)
        cfg["channel_mappings"][src_id] = tgt_id
        if src_id in cfg["known_channels"]:
            cfg["known_channels"][src_id]["role"] = "src"
        if tgt_id in cfg["known_channels"]:
            cfg["known_channels"][tgt_id]["role"] = "tgt"
        cfg["setup_complete"] = True
        save_config()
        ud["state"] = S.IDLE
        src_t, tgt_t = ch_label(int(src_id)), ch_label(int(tgt_id))
        await q.edit_message_text(
            f"🎉 *设置完成！*\n\n"
            f"📤 源频道: `{src_t}`\n"
            f"📥 目标频道: `{tgt_t}`\n\n"
            f"机器人已开始同步消息。\n"
            f"使用 /settings 随时调整配置。",
            parse_mode="Markdown",
            reply_markup=kb([btn("⚙️ 打开设置面板", "menu:main")]),
        )

    # ── 菜单导航 ──────────────────────────────────────────────────────────────
    elif data == "menu:main":
        ud["state"] = S.IDLE
        await q.edit_message_text(main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB)

    elif data == "menu:ch":
        ud["state"] = S.IDLE
        t, mk = ch_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:known":
        ud["state"] = S.IDLE
        t, mk = known_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:rule":
        ud["state"] = S.IDLE
        t, mk = rule_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:ad":
        ud["state"] = S.IDLE
        t, mk = ad_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data == "menu:admin":
        ud["state"] = S.IDLE
        t, mk = admin_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data == "cb:status":
        m = get_mappings()
        active = sum(1 for a in cfg["ads"] if a.get("enabled", True))
        await q.edit_message_text(
            f"✅ *机器人运行中*\n\n"
            f"📡 频道映射: {len(m)} 条\n"
            f"🤖 已知频道: {len(cfg['known_channels'])} 个\n"
            f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
            f"📢 定时广告: {active}/{len(cfg['ads'])} 运行中\n"
            f"👮 管理员: {len(cfg['admins'])} 人",
            parse_mode="Markdown", reply_markup=kb([btn("◀ 返回", "menu:main")]),
        )

    elif data == "cb:close":
        ud["state"] = S.IDLE
        await q.edit_message_text("✅ 面板已关闭。发送 /settings 重新打开。")

    elif data == "cb:cancel":
        ud["state"] = S.IDLE
        await q.edit_message_text(main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB)

    # ── 频道操作 ──────────────────────────────────────────────────────────────
    elif data == "ch:add":
        ud["state"] = S.CH_SRC
        known_unmapped_src = [
            c for c in known_ch_list() if c["role"] in ("src", None)
        ]
        rows = [[btn(f"📤 {c['title']}", f"ch:pick_src:{c['id']}")] for c in known_unmapped_src]
        rows.append([btn("✖ 取消", "menu:ch")])
        await q.edit_message_text(
            "📡 *添加频道映射 — 第 1/2 步*\n\n"
            "选择*源频道*（监听消息来源）\n"
            "或直接发送频道 ID：",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("ch:pick_src:"):
        src = data[12:]
        ud["state"] = S.CH_TGT
        ud.setdefault("draft", {})["src"] = src
        known_tgt = [c for c in known_ch_list() if c["role"] in ("tgt", None) and str(c["id"]) != src]
        rows = [[btn(f"📥 {c['title']}", f"ch:pick_tgt:{c['id']}")] for c in known_tgt]
        rows.append([btn("✖ 取消", "menu:ch")])
        src_title = ch_label(int(src))
        await q.edit_message_text(
            f"📡 *添加频道映射 — 第 2/2 步*\n\n"
            f"源: `{src_title}`\n\n"
            "选择*目标频道*（消息转发至此）\n"
            "或直接发送频道 ID：",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("ch:pick_tgt:"):
        tgt = data[12:]
        src = ud.get("draft", {}).get("src", "")
        if src:
            cfg["channel_mappings"][src] = tgt
            if src in cfg["known_channels"]: cfg["known_channels"][src]["role"] = "src"
            if tgt in cfg["known_channels"]: cfg["known_channels"][tgt]["role"] = "tgt"
            save_config()
            ud["state"] = S.IDLE
            logger.info(f"添加频道映射: {src} → {tgt}")
        t, mk = ch_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ch:del:"):
        src = data[7:]
        cfg["channel_mappings"].pop(src, None)
        save_config()
        t, mk = ch_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    # ── 已知频道操作 ──────────────────────────────────────────────────────────
    elif data.startswith("known:detail:"):
        cid = data[13:]
        t, mk = known_detail(cid)
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("known:set_src:"):
        cid = data[14:]
        cfg["known_channels"].setdefault(cid, {})["role"] = "src"
        save_config()
        # 提示选择目标
        known_tgt = [c for c in known_ch_list() if str(c["id"]) != cid]
        rows = [[btn(f"📥 {c['title']}", f"ch:pick_tgt_from:{cid}:{c['id']}")] for c in known_tgt]
        rows.append([btn("⏭ 稍后在频道映射中配置", "menu:known")])
        src_title = ch_label(int(cid))
        await q.edit_message_text(
            f"📤 已将 *{src_title}* 设为源频道\n\n"
            "是否立即选择目标频道完成映射？",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("ch:pick_tgt_from:"):
        _, _, src, tgt = data.split(":", 3)
        cfg["channel_mappings"][src] = tgt
        if src in cfg["known_channels"]: cfg["known_channels"][src]["role"] = "src"
        if tgt in cfg["known_channels"]: cfg["known_channels"][tgt]["role"] = "tgt"
        save_config()
        src_t, tgt_t = ch_label(int(src)), ch_label(int(tgt))
        await q.edit_message_text(
            f"✅ *映射已创建*\n\n`{src_t}` → `{tgt_t}`",
            parse_mode="Markdown",
            reply_markup=kb([btn("📡 频道映射", "menu:ch"), btn("⚙️ 主菜单", "menu:main")]),
        )

    elif data.startswith("known:set_tgt:"):
        cid = data[14:]
        cfg["known_channels"].setdefault(cid, {})["role"] = "tgt"
        save_config()
        t, mk = known_detail(cid)
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("known:del:"):
        cid = data[10:]
        cfg["known_channels"].pop(cid, None)
        save_config()
        t, mk = known_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    # ── 替换规则操作 ──────────────────────────────────────────────────────────
    elif data == "rule:add":
        ud["state"] = S.RULE_OLD
        await q.edit_message_text(
            "🔤 *添加替换规则 — 第 1/2 步*\n\n请发送*要被替换的原文*：",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("rule:del:"):
        idx = int(data[9:])
        keys = list(cfg["replace_rules"].keys())
        if 0 <= idx < len(keys):
            del cfg["replace_rules"][keys[idx]]
            save_config()
        t, mk = rule_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    # ── 广告操作 ──────────────────────────────────────────────────────────────
    elif data == "ad:add":
        ud["state"] = S.AD_TEXT
        ud["draft"] = {"ad_id": str(uuid.uuid4())[:8]}
        await q.edit_message_text(
            "📢 *新建广告 — 第 1/4 步*\n\n请发送*广告正文*内容：",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("ad:detail:"):
        t, mk = ad_detail(data[10:])
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ad:toggle:"):
        ad_id = data[10:]
        ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
        if ad:
            ad["enabled"] = not ad.get("enabled", True)
            save_config()
            reschedule_ad(ctx.application, ad)
        t, mk = ad_detail(ad_id)
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ad:del:"):
        ad_id = data[7:]
        remove_ad_job(ctx.application, ad_id)
        cfg["ads"] = [a for a in cfg["ads"] if a["id"] != ad_id]
        save_config()
        t, mk = ad_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

    elif data.startswith("ad:skip_buttons:"):
        await _save_ad(ctx.application, q, ud, data[16:], [])

    elif data.startswith("ad:confirm_buttons:"):
        buttons = ud.get("draft", {}).get("buttons_pending", [])
        await _save_ad(ctx.application, q, ud, data[19:], buttons)

    # ── 管理员操作 ────────────────────────────────────────────────────────────
    elif data == "admin:add":
        ud["state"] = S.ADMIN_ID
        await q.edit_message_text(
            "👮 *添加管理员*\n\n请发送用户的 Telegram *数字 ID*：\n"
            "（对方发消息给 @userinfobot 可获取）",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif data.startswith("admin:del:"):
        uid_del = int(data[10:])
        if uid_del in cfg["admins"]:
            cfg["admins"].remove(uid_del)
            save_config()
        t, mk = admin_panel()
        await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  私聊文字输入路由
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def handle_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return

    ud  = ctx.user_data
    state = ud.get("state", S.IDLE)
    if state == S.IDLE: return

    text = update.message.text.strip()
    msg  = update.message

    def back_mk(label: str, data: str) -> InlineKeyboardMarkup:
        return kb([btn(label, data)])

    # ── 向导流程 ──────────────────────────────────────────────────────────────
    if state == S.WIZ_ADMIN:
        try: new_admin = int(text)
        except ValueError:
            await msg.reply_text("❌ 请输入纯数字 ID"); return
        if new_admin not in cfg["admins"]:
            cfg["admins"].append(new_admin)
            save_config()
        await show_wizard_source(msg, ud)

    elif state == S.WIZ_SRC:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误（如 `-1001234567890`）", parse_mode="Markdown"); return
        if text not in cfg["known_channels"]:
            try:
                chat = await ctx.bot.get_chat(int(text))
                cfg["known_channels"][text] = {"title": chat.title or text, "role": "src"}
                save_config()
            except TelegramError:
                cfg["known_channels"][text] = {"title": text, "role": "src"}
                save_config()
        await show_wizard_target(msg, ud, text)

    elif state == S.WIZ_TGT:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误（如 `-1001234567890`）", parse_mode="Markdown"); return
        if text not in cfg["known_channels"]:
            try:
                chat = await ctx.bot.get_chat(int(text))
                cfg["known_channels"][text] = {"title": chat.title or text, "role": "tgt"}
                save_config()
            except TelegramError:
                cfg["known_channels"][text] = {"title": text, "role": "tgt"}
                save_config()
        src_id = ud.get("draft", {}).get("wiz_src", "")
        if src_id:
            await show_wizard_confirm(msg, ud, src_id, text)

    # ── 频道映射流程 ──────────────────────────────────────────────────────────
    elif state == S.CH_SRC:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误（如 `-1001234567890`）", parse_mode="Markdown"); return
        ud["state"] = S.CH_TGT
        ud.setdefault("draft", {})["src"] = text
        known_tgt = [c for c in known_ch_list() if str(c["id"]) != text]
        rows = [[btn(f"📥 {c['title']}", f"ch:pick_tgt:{c['id']}")] for c in known_tgt]
        rows.append([btn("✖ 取消", "menu:ch")])
        src_title = ch_label(int(text)) if text in cfg["known_channels"] else text
        await msg.reply_text(
            f"📡 *第 2/2 步*\n\n源: `{src_title}`\n\n选择或发送*目标频道 ID*：",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows),
        )

    elif state == S.CH_TGT:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误（如 `-1001234567890`）", parse_mode="Markdown"); return
        src = ud.get("draft", {}).get("src", "")
        if src:
            cfg["channel_mappings"][src] = text
            if src in cfg["known_channels"]: cfg["known_channels"][src]["role"] = "src"
            if text in cfg["known_channels"]: cfg["known_channels"][text]["role"] = "tgt"
            save_config()
            ud["state"] = S.IDLE
            src_t, tgt_t = ch_label(int(src)), ch_label(int(text)) if text in cfg["known_channels"] else text
            await msg.reply_text(
                f"✅ 已添加映射: `{src_t}` → `{tgt_t}`",
                parse_mode="Markdown",
                reply_markup=kb([btn("📡 频道映射", "menu:ch"), btn("⚙️ 主菜单", "menu:main")]),
            )

    # ── 替换规则流程 ──────────────────────────────────────────────────────────
    elif state == S.RULE_OLD:
        ud["state"] = S.RULE_NEW
        ud.setdefault("draft", {})["rule_old"] = text
        await msg.reply_text(
            f"🔤 *第 2/2 步*\n\n原文: `{text}`\n\n请发送*替换后文字*：\n"
            "（发送 `-` 表示删除该词）",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif state == S.RULE_NEW:
        old = ud.get("draft", {}).get("rule_old", "")
        new = "" if text == "-" else text
        cfg["replace_rules"][old] = new
        save_config()
        ud["state"] = S.IDLE
        display = new or "[删除该词]"
        await msg.reply_text(
            f"✅ 规则已添加: `{old}` → `{display}`",
            parse_mode="Markdown",
            reply_markup=kb([btn("🔤 规则管理", "menu:rule"), btn("⚙️ 主菜单", "menu:main")]),
        )

    # ── 管理员流程 ────────────────────────────────────────────────────────────
    elif state == S.ADMIN_ID:
        try: new_admin = int(text)
        except ValueError:
            await msg.reply_text("❌ 请输入纯数字用户 ID"); return
        result = f"ℹ️ `{new_admin}` 已是管理员"
        if new_admin not in cfg["admins"]:
            cfg["admins"].append(new_admin)
            save_config()
            result = f"✅ 已添加管理员: `{new_admin}`"
        ud["state"] = S.IDLE
        await msg.reply_text(
            result, parse_mode="Markdown",
            reply_markup=kb([btn("👮 管理员", "menu:admin"), btn("⚙️ 主菜单", "menu:main")]),
        )

    # ── 广告创建流程 ──────────────────────────────────────────────────────────
    elif state == S.AD_TEXT:
        ud.setdefault("draft", {})["ad_text"] = text
        ud["state"] = S.AD_INTERVAL
        await msg.reply_text(
            "📢 *第 2/4 步*\n\n请发送*推送间隔*（分钟）：\n"
            "• `60` = 每小时\n• `1440` = 每天\n• `10080` = 每周",
            parse_mode="Markdown", reply_markup=cancel_kb(),
        )

    elif state == S.AD_INTERVAL:
        try:
            mins = int(text)
            assert mins >= 1
        except (ValueError, AssertionError):
            await msg.reply_text("❌ 请输入正整数（分钟）"); return
        ud["draft"]["ad_interval"] = mins
        ud["state"] = S.AD_CHANNELS
        tgt_channels = [c for c in known_ch_list("tgt")] + [c for c in known_ch_list(None)]
        rows = [[btn(f"📥 {c['title']}", f"ad:pick_ch:{c['id']}")] for c in tgt_channels[:6]]
        rows.append([btn("📡 所有目标频道 (all)", "ad:pick_ch:all")])
        rows.append([btn("✖ 取消", "cb:cancel")])
        await msg.reply_text(
            "📢 *第 3/4 步*\n\n选择*目标频道*或发送频道 ID（空格分隔多个）：",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows),
        )

    elif state == S.AD_CHANNELS:
        if text.lower() == "all":
            channels = list(get_mappings().values())
        else:
            try: channels = [int(x) for x in text.split() if x]
            except ValueError:
                await msg.reply_text("❌ 格式错误，请输入数字 ID 或 all"); return
        if not channels:
            await msg.reply_text("⚠️ 没有目标频道，请先配置频道映射"); return
        ad_id = ud.get("draft", {}).get("ad_id", str(uuid.uuid4())[:8])
        ud["draft"]["ad_channels"] = channels
        ud["draft"]["ad_id"] = ad_id
        ud["state"] = S.AD_BUTTONS
        await msg.reply_text(
            "📢 *第 4/4 步（可选）*\n\n发送广告*内联按钮*定义：\n\n"
            "格式（每行一排，`|` 分隔同排多个）：\n"
            "```\n文字::URL\n按钮A::URL | 按钮B::URL\n```",
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
                "❌ 格式错误。格式: `文字::URL`，同排用 `|` 分隔\n"
                "或点击跳过不设按钮", parse_mode="Markdown"); return
        ad_id = ud.get("draft", {}).get("ad_id", str(uuid.uuid4())[:8])
        ud["draft"]["buttons_pending"] = buttons
        ad_text = ud.get("draft", {}).get("ad_text", "")
        await msg.reply_text(
            f"👇 *广告预览*\n\n{ad_text}", parse_mode="Markdown",
            reply_markup=build_markup(buttons),
        )
        await msg.reply_text(
            "效果如上，确认创建？",
            reply_markup=kb(
                [btn("✅ 确认创建", f"ad:confirm_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )


async def _handle_ad_ch_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """处理广告频道选择按钮（需单独注册）"""
    q = update.callback_query
    await q.answer()
    data = q.data
    ud = ctx.user_data
    if not is_admin(q.from_user.id): return

    if data.startswith("ad:pick_ch:"):
        val = data[11:]
        if val == "all":
            channels = list(get_mappings().values())
        else:
            try: channels = [int(val)]
            except ValueError: return
        ad_id = ud.get("draft", {}).get("ad_id", str(uuid.uuid4())[:8])
        ud.setdefault("draft", {})["ad_channels"] = channels
        ud["draft"]["ad_id"] = ad_id
        ud["state"] = S.AD_BUTTONS
        await q.edit_message_text(
            "📢 *第 4/4 步（可选）*\n\n发送广告*内联按钮*定义：\n\n"
            "格式（每行一排，`|` 分隔同排多个）：\n"
            "```\n文字::URL\n按钮A::URL | 按钮B::URL\n```",
            parse_mode="Markdown",
            reply_markup=kb(
                [btn("⏭ 跳过（无按钮）", f"ad:skip_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  广告保存 & 任务管理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _save_ad(app, q, ud: dict, ad_id: str, buttons: list):
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
    ud["state"] = S.IDLE
    ud.pop("draft", None)
    reschedule_ad(app, ad)
    ch_str = ", ".join(ch_label(c) for c in ad["channels"])
    await q.edit_message_text(
        f"✅ *广告已创建*\n\n"
        f"⏱ 间隔: 每 {ad['interval_minutes']} 分钟\n"
        f"📡 目标: {ch_str}\n"
        f"🔘 按钮: {sum(len(r) for r in buttons)} 个\n\n"
        f"📝 正文: {ad['text'][:60]}{'…' if len(ad['text'])>60 else ''}",
        parse_mode="Markdown",
        reply_markup=kb([btn("📢 广告管理", "menu:ad"), btn("⚙️ 主菜单", "menu:main")]),
    )

def remove_ad_job(app: Application, ad_id: str):
    for job in app.job_queue.get_jobs_by_name(f"ad_{ad_id}"):
        job.schedule_removal()

def reschedule_ad(app: Application, ad: dict):
    remove_ad_job(app, ad["id"])
    if not ad.get("enabled", True): return
    app.job_queue.run_repeating(
        _send_ad, interval=ad["interval_minutes"] * 60,
        first=ad["interval_minutes"] * 60,
        name=f"ad_{ad['id']}", data=ad["id"],
    )

async def _send_ad(ctx: ContextTypes.DEFAULT_TYPE):
    ad_id = ctx.job.data
    ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
    if not ad or not ad.get("enabled", True): return
    markup = build_markup(ad.get("buttons", []))
    channels = ad.get("channels") or list(get_mappings().values())
    for ch in channels:
        try:
            await ctx.bot.send_message(chat_id=ch, text=ad["text"], reply_markup=markup)
            logger.info(f"广告推送: {ad_id}→{ch}")
        except TelegramError as e:
            logger.error(f"广告推送失败 {ad_id}→{ch}: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  消息转发
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _forward(ctx: ContextTypes.DEFAULT_TYPE, msg, target: int):
    has_rules = bool(cfg["replace_rules"])
    cap = apply_replace(msg.caption)
    ent = msg.entities if not has_rules else None
    cent = msg.caption_entities if not has_rules else None
    if msg.text:
        await ctx.bot.send_message(target, apply_replace(msg.text), entities=ent)
    elif msg.photo:
        await ctx.bot.send_photo(target, msg.photo[-1].file_id, caption=cap, caption_entities=cent)
    elif msg.video:
        await ctx.bot.send_video(target, msg.video.file_id, caption=cap, caption_entities=cent)
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
        logger.warning(f"不支持类型 msg_id={msg.message_id}")

async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    m = get_mappings()
    if msg.chat_id not in m: return
    try:
        await _forward(ctx, msg, m[msg.chat_id])
        logger.info(f"[转发] {msg.chat_id}→{m[msg.chat_id]} id={msg.message_id}")
    except TelegramError as e:
        logger.error(f"转发失败: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  命令
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        # 第一个发 /start 且 admins 为空时自动引导设置管理员
        if not cfg["admins"] and not ENV_ADMIN_IDS:
            cfg["admins"].append(uid)
            save_config()
            logger.info(f"首位管理员自动设置: {uid}")
        else:
            await update.message.reply_text(
                "👋 你好！此机器人需要管理员权限才能使用设置功能。"
            )
            return

    if not cfg["setup_complete"]:
        await show_wizard_admin(update.message, ctx.user_data)
    else:
        await update.message.reply_text(
            "👋 *Telegram 频道同步机器人 v5*\n\n"
            "发送 /settings 打开设置面板",
            parse_mode="Markdown",
        )

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ 无权限"); return
    if not cfg["setup_complete"]:
        await show_wizard_admin(update.message, ctx.user_data)
        return
    ctx.user_data["state"] = S.IDLE
    await update.message.reply_text(
        main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = get_mappings()
    active = sum(1 for a in cfg["ads"] if a.get("enabled", True))
    known = cfg.get("known_channels", {})
    await update.message.reply_text(
        f"✅ *机器人运行中*\n\n"
        f"📡 频道映射: {len(m)} 条\n"
        f"🤖 已知频道: {len(known)} 个\n"
        f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
        f"📢 定时广告: {active}/{len(cfg['ads'])} 运行中\n"
        f"👮 管理员: {len(cfg['admins'])} 人",
        parse_mode="Markdown",
    )

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"[错误] {ctx.error}", exc_info=ctx.error)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  启动
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _post_init(app: Application):
    restored = sum(1 for ad in cfg.get("ads", []) if ad.get("enabled", True) and reschedule_ad(app, ad) is None)
    logger.info(
        f"🚀 机器人启动 | 频道:{len(get_mappings())} "
        f"| 已知:{len(cfg.get('known_channels',{}))} "
        f"| 广告:{restored}/{len(cfg.get('ads',[]))} "
        f"| 设置完成:{cfg['setup_complete']}"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status",   cmd_status))

    # 频道成员变化（自动识别）
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # 广告频道选择（优先于通用回调）
    app.add_handler(CallbackQueryHandler(_handle_ad_ch_pick, pattern=r"^ad:pick_ch:"))

    # 通用回调
    app.add_handler(CallbackQueryHandler(on_callback))

    # 私聊文字输入
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_text,
    ))

    # 频道转发
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    app.add_error_handler(error_handler)
    app.post_init = _post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
