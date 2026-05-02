from __future__ import annotations
"""
Telegram 频道同步机器人 v8
- 多频道映射 + 自动识别频道
- 首次引导向导
- 全内联按键设置面板
- 媒体组（相册）转发
- 消息编辑同步
- 洪水控制自动重试
"""
import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, time as dtime
from pathlib import Path

from telegram import (
    Chat, ChatMemberAdministrator, ChatMemberOwner,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo,
    Update,
)
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 未设置")

ENV_ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
CONFIG_PATH  = Path(os.getenv("CONFIG_PATH",  "config.json"))
MSG_MAP_PATH = Path(os.getenv("MSG_MAP_PATH", "msg_map.json"))


# ── 状态机 ────────────────────────────────────────────────────────────────────
class S:
    IDLE = "idle"
    WIZ_ADMIN = "wiz_admin"; WIZ_SRC = "wiz_src"; WIZ_TGT = "wiz_tgt"
    CH_SRC = "ch_src"; CH_TGT = "ch_tgt"
    RULE_OLD = "rule_old"; RULE_NEW = "rule_new"
    ADMIN_ID = "admin_id"
    AD_TEXT = "ad_text"; AD_INTERVAL = "ad_interval"
    AD_CHANNELS = "ad_channels"; AD_BUTTONS = "ad_buttons"


# ── 配置管理 ──────────────────────────────────────────────────────────────────
def _default_config() -> dict:
    return {
        "setup_complete": False,
        "channel_mappings": {},
        "known_channels": {},
        "replace_rules": {},
        "admins": [],
        "ads": [],
    }


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
            for k, v in _default_config().items():
                if isinstance(v, dict):
                    data.setdefault(k, {})
                    for kk, vv in v.items():
                        data[k].setdefault(kk, vv)
                else:
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

for _aid in ENV_ADMIN_IDS:
    if _aid not in cfg["admins"]:
        cfg["admins"].append(_aid)

_s0, _t0 = os.getenv("SOURCE_CHANNEL_ID"), os.getenv("TARGET_CHANNEL_ID")
if _s0 and _t0 and _s0 not in cfg["channel_mappings"]:
    cfg["channel_mappings"][_s0] = _t0
    cfg["setup_complete"] = True

save_config()


# ── 消息 ID 映射（用于编辑同步） ───────────────────────────────────────────────
def _load_msg_map() -> dict:
    if MSG_MAP_PATH.exists():
        try:
            return json.loads(MSG_MAP_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _save_msg_map() -> None:
    try:
        MSG_MAP_PATH.write_text(json.dumps(_msg_map, ensure_ascii=False), "utf-8")
    except Exception as e:
        logger.error(f"消息映射保存失败: {e}")


_msg_map: dict = _load_msg_map()


def _store_msg(src_chat: int, src_msg: int, tgt_chat: int, tgt_msg: int) -> None:
    _msg_map[f"{src_chat}:{src_msg}"] = f"{tgt_chat}:{tgt_msg}"
    if len(_msg_map) > 20000:
        keep = dict(list(_msg_map.items())[10000:])
        _msg_map.clear()
        _msg_map.update(keep)
    _save_msg_map()


def _lookup_msg(src_chat: int, src_msg: int) -> tuple[int, int] | None:
    val = _msg_map.get(f"{src_chat}:{src_msg}")
    if not val:
        return None
    try:
        tc, tm = val.split(":")
        return int(tc), int(tm)
    except ValueError:
        return None


# ── 每日统计 ──────────────────────────────────────────────────────────────────
_daily: dict = {"forwarded": 0, "errors": 0, "date": ""}


def _today() -> str:
    return str(date.today())


def _reset_daily() -> None:
    if _daily["date"] != _today():
        _daily.update({"forwarded": 0, "errors": 0, "date": _today()})


def _inc_fwd() -> None:
    _reset_daily()
    _daily["forwarded"] += 1


def _inc_err() -> None:
    _reset_daily()
    _daily["errors"] += 1


# ━━━ 工具函数 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def is_admin(uid: int) -> bool:
    return uid in cfg["admins"] or uid in ENV_ADMIN_IDS


def get_mappings() -> dict[int, int]:
    out = {}
    for k, v in cfg["channel_mappings"].items():
        try:
            out[int(k)] = int(v)
        except (ValueError, TypeError):
            pass
    return out


def apply_replace(text: str | None) -> str | None:
    if not text:
        return text
    for old, new in cfg["replace_rules"].items():
        text = text.replace(old, new)
    return text


def ch_label(cid: int) -> str:
    return cfg["known_channels"].get(str(cid), {}).get("title") or str(cid)


def known_ch_list(role: str | None = None) -> list[dict]:
    return [
        {"id": int(k), "title": v.get("title", k), "role": v.get("role")}
        for k, v in cfg["known_channels"].items()
        if role is None or v.get("role") == role
    ]


def _is_id(s: str) -> bool:
    return bool(s) and s.lstrip("-").isdigit()


def parse_buttons(raw: str) -> list[list[dict]]:
    rows = []
    for line in raw.strip().splitlines():
        row = []
        for cell in line.split("|"):
            cell = cell.strip()
            if "::" in cell:
                text, url = cell.split("::", 1)
                text, url = text.strip(), url.strip()
                if text and url:
                    row.append({"text": text, "url": url})
        if row:
            rows.append(row)
    return rows


def build_markup(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in buttons]
    )


def kb(*rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))


def btn(t: str, d: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(t, callback_data=d)


def cancel_kb(back: str = "cb:cancel") -> InlineKeyboardMarkup:
    return kb([btn("✖ 取消", back)])


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ━━━ 洪水控制重试 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _send_with_retry(func, retries: int = 3, **kwargs):
    for attempt in range(retries):
        try:
            return await func(**kwargs)
        except RetryAfter as e:
            if attempt < retries - 1:
                wait = e.retry_after + 1
                logger.warning(f"洪水控制，等待 {wait}s 后重试")
                await asyncio.sleep(wait)
            else:
                raise
    return None


# ━━━ 媒体组缓冲 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_media_groups: dict[str, list] = defaultdict(list)


async def _flush_media_group(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    group_id: str = ctx.job.data
    messages = _media_groups.pop(group_id, [])
    if not messages:
        return

    src_chat_id = messages[0].chat_id
    mappings = get_mappings()
    if src_chat_id not in mappings:
        return

    target_id = mappings[src_chat_id]
    cap = apply_replace(messages[0].caption)

    media_list = []
    group_msgs = []
    for i, msg in enumerate(messages):
        item_cap = cap if i == 0 else None
        if msg.photo:
            media_list.append(InputMediaPhoto(msg.photo[-1].file_id, caption=item_cap))
            group_msgs.append(msg)
        elif msg.video:
            media_list.append(InputMediaVideo(msg.video.file_id, caption=item_cap))
            group_msgs.append(msg)

    if len(media_list) >= 2:
        try:
            sent_list = await _send_with_retry(
                ctx.bot.send_media_group,
                chat_id=target_id,
                media=media_list,
            )
            if sent_list:
                for src_msg, tgt_msg in zip(group_msgs, sent_list):
                    _store_msg(src_chat_id, src_msg.message_id, target_id, tgt_msg.message_id)
                _inc_fwd()
                logger.info(f"[相册] {src_chat_id}→{target_id} {len(sent_list)} 张")
            for msg in messages:
                if msg not in group_msgs:
                    await _forward_one(ctx, msg, src_chat_id, target_id)
            return
        except TelegramError as e:
            _inc_err()
            logger.error(f"相册转发失败，降级单条: {e}")

    for msg in messages:
        await _forward_one(ctx, msg, src_chat_id, target_id)


async def _forward_one(ctx, msg, src_chat_id: int, target_id: int) -> None:
    try:
        tgt_msg_id = await _forward_single(ctx, msg, target_id)
        if tgt_msg_id:
            _store_msg(src_chat_id, msg.message_id, target_id, tgt_msg_id)
        _inc_fwd()
    except TelegramError as e:
        _inc_err()
        logger.error(f"转发失败: {e}")


# ━━━ 面板构建 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main_menu_text() -> str:
    m = get_mappings()
    active_ads = sum(1 for a in cfg["ads"] if a.get("enabled", True))
    return (
        "⚙️ <b>设置面板</b>\n\n"
        f"📡 频道映射: {len(m)} 条  🤖 已知: {len(cfg['known_channels'])}\n"
        f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
        f"📢 广告: {active_ads}/{len(cfg['ads'])}"
    )


MAIN_MENU_KB = kb(
    [btn("📡 频道映射", "menu:ch"),   btn("🤖 已知频道", "menu:known")],
    [btn("🔤 替换规则", "menu:rule"),  btn("📢 定时广告",  "menu:ad")],
    [btn("👮 管理员",   "menu:admin"), btn("📊 运行状态",  "cb:status")],
    [btn("❌ 关闭",     "cb:close")],
)


def ch_panel() -> tuple[str, InlineKeyboardMarkup]:
    m = get_mappings()
    rows = [
        [btn(f"🗑 {esc(ch_label(s))} → {esc(ch_label(t))}", f"ch:del:{s}")]
        for s, t in m.items()
    ]
    rows.append([btn("➕ 添加映射", "ch:add"), btn("◀ 返回", "menu:main")])
    lines = ["📡 <b>频道映射</b>\n"]
    lines += [
        f"<code>{esc(ch_label(s))}</code> → <code>{esc(ch_label(t))}</code>"
        for s, t in m.items()
    ] if m else ["暂无"]
    lines.append("\n<i>点条目删除</i>")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def known_panel() -> tuple[str, InlineKeyboardMarkup]:
    known = cfg["known_channels"]
    role_icons = {"src": "📤 ", "tgt": "📥 "}
    rows = [
        [btn(role_icons.get(v.get("role"), "📡 ") + v.get("title", k), f"known:detail:{k}")]
        for k, v in known.items()
    ]
    rows.append([btn("◀ 返回", "menu:main")])
    role_names = {"src": "📤源", "tgt": "📥目标"}
    lines = ["🤖 <b>已知频道</b>\n"]
    if known:
        lines += [
            f"• {esc(v.get('title', k))} — {role_names.get(v.get('role'), '未分配')}"
            for k, v in known.items()
        ]
    else:
        lines.append("暂无\n将机器人添加为频道管理员后自动识别")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def known_detail(cid: str) -> tuple[str, InlineKeyboardMarkup]:
    info = cfg["known_channels"].get(cid, {})
    title = esc(info.get("title", cid))
    role_txt = {"src": "📤 源频道", "tgt": "📥 目标频道"}.get(info.get("role"), "未分配")
    txt = f"📡 <b>{title}</b>\nID: <code>{cid}</code>\n角色: {role_txt}"
    mk = kb(
        [btn("📤 设为源频道", f"known:set_src:{cid}"),
         btn("📥 设为目标频道", f"known:set_tgt:{cid}")],
        [btn("🗑 移除", f"known:del:{cid}"), btn("◀ 返回", "menu:known")],
    )
    return txt, mk


def rule_panel() -> tuple[str, InlineKeyboardMarkup]:
    rules = cfg["replace_rules"]
    rows = [
        [btn(f"🗑 「{esc(o[:12])}」→「{esc((n or '删')[:12])}」", f"rule:del:{i}")]
        for i, (o, n) in enumerate(rules.items())
    ]
    rows.append([btn("➕ 添加规则", "rule:add"), btn("◀ 返回", "menu:main")])
    lines = ["🔤 <b>替换规则</b>\n"]
    lines += [
        f"<code>{esc(o)}</code> → <code>{esc(n) if n else '[删]'}</code>"
        for o, n in rules.items()
    ] if rules else ["暂无"]
    lines.append("\n<i>点条目删除</i>")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def ad_panel() -> tuple[str, InlineKeyboardMarkup]:
    ads = cfg["ads"]
    rows = [
        [btn(
            ("✅" if a.get("enabled", True) else "⏸") +
            f" {esc(a['text'][:16])}… /{a['interval_minutes']}min",
            f"ad:detail:{a['id']}",
        )]
        for a in ads
    ]
    rows.append([btn("➕ 新建广告", "ad:add"), btn("◀ 返回", "menu:main")])
    lines = ["📢 <b>定时广告</b>\n"]
    lines += [
        f"{i}. {'✅' if a.get('enabled', True) else '⏸'} "
        f"{esc(a['text'][:20])} / 每{a['interval_minutes']}min"
        for i, a in enumerate(ads, 1)
    ] if ads else ["暂无"]
    lines.append("\n<i>点条目管理</i>")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def ad_detail(ad_id: str) -> tuple[str, InlineKeyboardMarkup]:
    ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
    if not ad:
        return "❌ 广告不存在", kb([btn("◀ 返回", "menu:ad")])
    s = "✅运行" if ad.get("enabled", True) else "⏸暂停"
    ch = ", ".join(esc(ch_label(c)) for c in ad.get("channels", [])) or "继承映射"
    btn_rows = ad.get("buttons", [])
    bp = "\n".join("  " + " | ".join(f"[{esc(b['text'])}]" for b in r) for r in btn_rows)
    txt = (
        f"📢 <b>广告</b> | {s}\n"
        f"间隔:{ad['interval_minutes']}min  目标:{ch}\n"
        + (f"<pre>{bp}</pre>\n" if bp else "")
        + f"\n{esc(ad['text'])}"
    )
    tog = "⏸暂停" if ad.get("enabled", True) else "▶启动"
    return txt, kb(
        [btn(tog, f"ad:toggle:{ad_id}"), btn("🗑删除", f"ad:del:{ad_id}")],
        [btn("◀返回", "menu:ad")],
    )


def admin_panel() -> tuple[str, InlineKeyboardMarkup]:
    admins = cfg["admins"]
    rows = [[btn(f"🗑 {a}", f"admin:del:{a}")] for a in admins]
    rows.append([btn("➕ 添加管理员", "admin:add"), btn("◀ 返回", "menu:main")])
    lines = ["👮 <b>管理员</b>\n"]
    lines += [f"• <code>{a}</code>" for a in admins] if admins else ["暂无"]
    lines.append("\n<i>点条目删除</i>")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ━━━ 向导 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WIZ_STEPS = {
    "admin":   "第1步 设置管理员",
    "source":  "第2步 设置源频道",
    "target":  "第3步 设置目标频道",
    "confirm": "第4步 确认完成",
}


def wiz_progress(cur: str) -> str:
    keys = list(WIZ_STEPS.keys())
    past = True
    out = []
    for k in keys:
        if k == cur:
            past = False
            out.append(f"🔵 {WIZ_STEPS[k]}")
        elif past:
            out.append(f"✅ {WIZ_STEPS[k]}")
        else:
            out.append(f"⚪ {WIZ_STEPS[k]}")
    return "\n".join(out)


async def wiz_admin(tgt, ud: dict, edit: bool = False) -> None:
    ud["state"] = S.WIZ_ADMIN
    admins_txt = " ".join(f"<code>{a}</code>" for a in cfg["admins"])
    txt = (
        f"🧙 <b>设置向导</b>\n\n{wiz_progress('admin')}\n\n━━━━━━━━━\n"
        "发送你的 Telegram <b>数字 ID</b> 设为管理员\n"
        "获取方法：发消息给 @userinfobot\n\n"
        + (f"当前管理员: {admins_txt}" if admins_txt else "⚠️ 暂无管理员")
    )
    mk = kb([btn("➡ 跳过（已有管理员）", "wiz:skip_admin")]) if cfg["admins"] else kb()
    if edit:
        await tgt.edit_message_text(txt, parse_mode="HTML", reply_markup=mk)
    else:
        await tgt.reply_text(txt, parse_mode="HTML", reply_markup=mk)


async def wiz_source(tgt, ud: dict, edit: bool = False) -> None:
    ud["state"] = S.WIZ_SRC
    known = known_ch_list()
    rows = [[btn(f"📡 {esc(c['title'])}", f"wiz:src:{c['id']}")] for c in known]
    rows.append([btn("⬅ 返回", "wiz:back_admin")])
    prompt = (
        "选择<b>源频道</b>（监听的频道）或发送频道 ID：\n"
        if known else
        "⚠️ 暂未检测到频道，请先将机器人设为频道管理员\n或直接发送频道 ID："
    )
    txt = f"🧙 <b>设置向导</b>\n\n{wiz_progress('source')}\n\n━━━━━━━━━\n{prompt}"
    if edit:
        await tgt.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    else:
        await tgt.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def wiz_target(tgt, ud: dict, src_id: str, edit: bool = False) -> None:
    ud["state"] = S.WIZ_TGT
    ud.setdefault("draft", {})["wiz_src"] = src_id
    known = [c for c in known_ch_list() if str(c["id"]) != src_id]
    rows = [[btn(f"📡 {esc(c['title'])}", f"wiz:tgt:{c['id']}")] for c in known]
    rows.append([btn("⬅ 返回", "wiz:back_source")])
    src_t = esc(ch_label(int(src_id)))
    txt = (
        f"🧙 <b>设置向导</b>\n\n{wiz_progress('target')}\n\n━━━━━━━━━\n"
        f"源: <code>{src_t}</code>\n\n选择<b>目标频道</b>或发送频道 ID："
    )
    if edit:
        await tgt.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    else:
        await tgt.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def wiz_confirm(tgt, ud: dict, src_id: str, tgt_id: str, edit: bool = False) -> None:
    ud.setdefault("draft", {}).update({"wiz_src": src_id, "wiz_tgt": tgt_id})
    src_t = esc(ch_label(int(src_id)))
    tgt_t = esc(ch_label(int(tgt_id)))
    txt = (
        f"🧙 <b>设置向导</b>\n\n{wiz_progress('confirm')}\n\n━━━━━━━━━\n\n"
        f"📤 源: <code>{src_t}</code>\n📥 目标: <code>{tgt_t}</code>\n"
        f"👮 管理员: {len(cfg['admins'])} 人\n\n确认完成设置？"
    )
    mk = kb(
        [btn("✅ 确认完成", f"wiz:confirm:{src_id}:{tgt_id}")],
        [btn("⬅ 重选", "wiz:back_source")],
    )
    if edit:
        await tgt.edit_message_text(txt, parse_mode="HTML", reply_markup=mk)
    else:
        await tgt.reply_text(txt, parse_mode="HTML", reply_markup=mk)


# ━━━ 频道自动识别 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chg = update.my_chat_member
    chat = chg.chat
    if chat.type not in (Chat.CHANNEL, Chat.SUPERGROUP):
        return

    cid = str(chat.id)
    title = chat.title or cid
    is_adm_now = isinstance(chg.new_chat_member, (ChatMemberAdministrator, ChatMemberOwner))
    was_adm    = isinstance(chg.old_chat_member, (ChatMemberAdministrator, ChatMemberOwner))

    if is_adm_now and not was_adm:
        logger.info(f"加入频道: {title} ({cid})")
        cfg["known_channels"].setdefault(cid, {}).update({"title": title})
        if "role" not in cfg["known_channels"][cid]:
            cfg["known_channels"][cid]["role"] = None
        save_config()
        note = (
            f"🔔 <b>检测到新频道</b>\n\n"
            f"📡 <b>{esc(title)}</b>\n<code>{cid}</code>\n\n"
            "请设置其角色完成同步配置"
        )
        for aid in cfg["admins"]:
            try:
                await ctx.bot.send_message(
                    aid, note, parse_mode="HTML",
                    reply_markup=kb(
                        [btn("📤 设为源频道", f"known:set_src:{cid}"),
                         btn("📥 设为目标频道", f"known:set_tgt:{cid}")],
                        [btn("⚙️ 打开设置", "menu:known")],
                    ),
                )
            except TelegramError as e:
                logger.warning(f"通知管理员失败 {aid}: {e}")

    elif not is_adm_now and was_adm:
        logger.info(f"失去频道管理权: {title} ({cid})")
        if cid in cfg["known_channels"]:
            cfg["known_channels"][cid]["role"] = None
            save_config()
        for aid in cfg["admins"]:
            try:
                await ctx.bot.send_message(
                    aid,
                    f"⚠️ 机器人已失去 <b>{esc(title)}</b> 管理员权限",
                    parse_mode="HTML",
                )
            except TelegramError:
                pass


# ━━━ 回调路由 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    ud = ctx.user_data

    if not is_admin(uid):
        await q.answer("⛔ 无权限", show_alert=True)
        return

    # ── 主菜单 ────────────────────────────────────────────────────────────────
    if data == "menu:main":
        ud["state"] = S.IDLE
        await q.edit_message_text(main_menu_text(), parse_mode="HTML", reply_markup=MAIN_MENU_KB)
    elif data == "menu:ch":
        ud["state"] = S.IDLE
        t, mk = ch_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data == "menu:known":
        ud["state"] = S.IDLE
        t, mk = known_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data == "menu:rule":
        ud["state"] = S.IDLE
        t, mk = rule_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data == "menu:ad":
        ud["state"] = S.IDLE
        t, mk = ad_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data == "menu:admin":
        ud["state"] = S.IDLE
        t, mk = admin_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)

    elif data == "cb:status":
        m = get_mappings()
        active = sum(1 for a in cfg["ads"] if a.get("enabled", True))
        _reset_daily()
        await q.edit_message_text(
            f"✅ <b>运行中</b>\n\n"
            f"📡 映射:{len(m)}  🤖 已知:{len(cfg['known_channels'])}\n"
            f"🔤 规则:{len(cfg['replace_rules'])}  📢 广告:{active}/{len(cfg['ads'])}\n"
            f"📨 今日转发:{_daily['forwarded']}  ❌ 失败:{_daily['errors']}",
            parse_mode="HTML",
            reply_markup=kb([btn("◀ 返回", "menu:main")]),
        )
    elif data == "cb:close":
        ud["state"] = S.IDLE
        await q.edit_message_text("✅ 面板已关闭。/settings 重新打开")
    elif data == "cb:cancel":
        ud["state"] = S.IDLE
        await q.edit_message_text(main_menu_text(), parse_mode="HTML", reply_markup=MAIN_MENU_KB)

    # ── 向导 ──────────────────────────────────────────────────────────────────
    elif data == "wiz:skip_admin":
        await wiz_source(q, ud, edit=True)
    elif data == "wiz:back_admin":
        await wiz_admin(q, ud, edit=True)
    elif data == "wiz:back_source":
        await wiz_source(q, ud, edit=True)
    elif data.startswith("wiz:src:"):
        await wiz_target(q, ud, data[8:], edit=True)
    elif data.startswith("wiz:tgt:"):
        src = ud.get("draft", {}).get("wiz_src", "")
        if src:
            await wiz_confirm(q, ud, src, data[8:], edit=True)
    elif data.startswith("wiz:confirm:"):
        _, _, src, tgt = data.split(":", 3)
        cfg["channel_mappings"][src] = tgt
        for cid, role in [(src, "src"), (tgt, "tgt")]:
            cfg["known_channels"].setdefault(cid, {})["role"] = role
        cfg["setup_complete"] = True
        save_config()
        ud["state"] = S.IDLE
        await q.edit_message_text(
            f"🎉 <b>设置完成！</b>\n\n"
            f"📤 源: <code>{esc(ch_label(int(src)))}</code>\n"
            f"📥 目标: <code>{esc(ch_label(int(tgt)))}</code>\n\n"
            "机器人开始同步消息。",
            parse_mode="HTML",
            reply_markup=kb([btn("⚙️ 打开设置", "menu:main")]),
        )

    # ── 频道映射操作 ──────────────────────────────────────────────────────────
    elif data == "ch:add":
        ud["state"] = S.CH_SRC
        known = known_ch_list()
        rows = [[btn(f"📡 {esc(c['title'])}", f"ch:pick_src:{c['id']}")] for c in known]
        rows.append([btn("✖ 取消", "menu:ch")])
        await q.edit_message_text(
            "📡 <b>添加映射 1/2</b>\n\n选择源频道或发送 ID：",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )
    elif data.startswith("ch:pick_src:"):
        src = data[12:]
        ud["state"] = S.CH_TGT
        ud.setdefault("draft", {})["src"] = src
        others = [c for c in known_ch_list() if str(c["id"]) != src]
        rows = [[btn(f"📡 {esc(c['title'])}", f"ch:pick_tgt:{c['id']}")] for c in others]
        rows.append([btn("✖ 取消", "menu:ch")])
        await q.edit_message_text(
            f"📡 <b>添加映射 2/2</b>\n\n源: <code>{esc(ch_label(int(src)))}</code>\n\n选择目标或发送 ID：",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )
    elif data.startswith("ch:pick_tgt:"):
        tgt = data[12:]
        src = ud.get("draft", {}).get("src", "")
        if src:
            cfg["channel_mappings"][src] = tgt
            for c, r in [(src, "src"), (tgt, "tgt")]:
                cfg["known_channels"].setdefault(c, {})["role"] = r
            save_config()
            ud["state"] = S.IDLE
            logger.info(f"添加映射: {src}→{tgt}")
        t, mk = ch_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data.startswith("ch:del:"):
        cfg["channel_mappings"].pop(data[7:], None)
        save_config()
        t, mk = ch_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)

    # ── 已知频道操作 ──────────────────────────────────────────────────────────
    elif data.startswith("known:detail:"):
        t, mk = known_detail(data[13:])
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data.startswith("known:set_src:"):
        cid = data[14:]
        cfg["known_channels"].setdefault(cid, {})["role"] = "src"
        save_config()
        others = [c for c in known_ch_list() if str(c["id"]) != cid]
        rows = [[btn(f"📥 {esc(c['title'])}", f"ch:pick_tgt_from:{cid}:{c['id']}")] for c in others]
        rows.append([btn("⏭ 稍后配置", "menu:known")])
        await q.edit_message_text(
            "📤 已设为源频道\n\n立即选择目标频道完成映射？",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif data.startswith("ch:pick_tgt_from:"):
        _, _, src, tgt = data.split(":", 3)
        cfg["channel_mappings"][src] = tgt
        for c, r in [(src, "src"), (tgt, "tgt")]:
            cfg["known_channels"].setdefault(c, {})["role"] = r
        save_config()
        await q.edit_message_text(
            f"✅ <code>{esc(ch_label(int(src)))}</code> → <code>{esc(ch_label(int(tgt)))}</code>",
            parse_mode="HTML",
            reply_markup=kb([btn("📡 频道映射", "menu:ch"), btn("⚙️ 主菜单", "menu:main")]),
        )
    elif data.startswith("known:set_tgt:"):
        cid = data[14:]
        cfg["known_channels"].setdefault(cid, {})["role"] = "tgt"
        save_config()
        t, mk = known_detail(cid)
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data.startswith("known:del:"):
        cfg["known_channels"].pop(data[10:], None)
        save_config()
        t, mk = known_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)

    # ── 替换规则操作 ──────────────────────────────────────────────────────────
    elif data == "rule:add":
        ud["state"] = S.RULE_OLD
        await q.edit_message_text(
            "🔤 <b>添加规则 1/2</b>\n\n发送<b>原文</b>：",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )
    elif data.startswith("rule:del:"):
        idx = int(data[9:])
        keys = list(cfg["replace_rules"].keys())
        if 0 <= idx < len(keys):
            del cfg["replace_rules"][keys[idx]]
            save_config()
        t, mk = rule_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)

    # ── 广告操作 ──────────────────────────────────────────────────────────────
    elif data == "ad:add":
        ud["state"] = S.AD_TEXT
        ud["draft"] = {"ad_id": str(uuid.uuid4())[:8]}
        await q.edit_message_text(
            "📢 <b>新建广告 1/4</b>\n\n发送<b>广告正文</b>：",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )
    elif data.startswith("ad:detail:"):
        t, mk = ad_detail(data[10:])
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data.startswith("ad:toggle:"):
        ad_id = data[10:]
        ad = next((a for a in cfg["ads"] if a["id"] == ad_id), None)
        if ad:
            ad["enabled"] = not ad.get("enabled", True)
            save_config()
            reschedule_ad(ctx.application, ad)
        t, mk = ad_detail(ad_id)
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data.startswith("ad:del:"):
        ad_id = data[7:]
        remove_ad_job(ctx.application, ad_id)
        cfg["ads"] = [a for a in cfg["ads"] if a["id"] != ad_id]
        save_config()
        t, mk = ad_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)
    elif data.startswith("ad:skip_buttons:"):
        await _save_ad(ctx.application, q, ud, data[16:], [])
    elif data.startswith("ad:confirm_buttons:"):
        await _save_ad(ctx.application, q, ud, data[19:], ud.get("draft", {}).get("buttons_pending", []))
    elif data.startswith("ad:pick_ch:"):
        val = data[11:]
        channels = list(get_mappings().values()) if val == "all" else [int(val)]
        ad_id = ud.get("draft", {}).get("ad_id", str(uuid.uuid4())[:8])
        ud.setdefault("draft", {}).update({"ad_channels": channels, "ad_id": ad_id})
        ud["state"] = S.AD_BUTTONS
        await q.edit_message_text(
            "📢 <b>第4/4步</b>\n\n发送内联按钮定义（可选）：\n"
            "<code>文字::URL</code>\n<code>A::URL | B::URL</code>（同行多个）",
            parse_mode="HTML",
            reply_markup=kb(
                [btn("⏭ 跳过", f"ad:skip_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )

    # ── 管理员操作 ────────────────────────────────────────────────────────────
    elif data == "admin:add":
        ud["state"] = S.ADMIN_ID
        await q.edit_message_text(
            "👮 <b>添加管理员</b>\n\n发送用户数字 ID：",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )
    elif data.startswith("admin:del:"):
        uid_del = int(data[10:])
        if uid_del in cfg["admins"]:
            cfg["admins"].remove(uid_del)
            save_config()
        t, mk = admin_panel()
        await q.edit_message_text(t, parse_mode="HTML", reply_markup=mk)


# ━━━ 私聊文字路由 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def handle_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    ud = ctx.user_data
    state = ud.get("state", S.IDLE)
    text = update.message.text.strip()
    msg = update.message

    def back_kb(*pairs: tuple[str, str]) -> InlineKeyboardMarkup:
        return kb(*[[btn(label, cb)] for label, cb in pairs])

    # ── 向导输入 ──────────────────────────────────────────────────────────────
    if state == S.WIZ_ADMIN:
        try:
            nid = int(text)
        except ValueError:
            await msg.reply_text("❌ 请输入纯数字 ID")
            return
        if nid not in cfg["admins"]:
            cfg["admins"].append(nid)
            save_config()
        await wiz_source(msg, ud)

    elif state == S.WIZ_SRC:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误（如 -1001234567890）")
            return
        if text not in cfg["known_channels"]:
            try:
                chat = await ctx.bot.get_chat(int(text))
                cfg["known_channels"][text] = {"title": chat.title or text, "role": "src"}
            except TelegramError:
                cfg["known_channels"][text] = {"title": text, "role": "src"}
            save_config()
        await wiz_target(msg, ud, text)

    elif state == S.WIZ_TGT:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误（如 -1001234567890）")
            return
        if text not in cfg["known_channels"]:
            try:
                chat = await ctx.bot.get_chat(int(text))
                cfg["known_channels"][text] = {"title": chat.title or text, "role": "tgt"}
            except TelegramError:
                cfg["known_channels"][text] = {"title": text, "role": "tgt"}
            save_config()
        src = ud.get("draft", {}).get("wiz_src", "")
        if src:
            await wiz_confirm(msg, ud, src, text)

    # ── 频道输入 ──────────────────────────────────────────────────────────────
    elif state == S.CH_SRC:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误")
            return
        ud["state"] = S.CH_TGT
        ud.setdefault("draft", {})["src"] = text
        others = [c for c in known_ch_list() if str(c["id"]) != text]
        rows = [[btn(f"📡 {esc(c['title'])}", f"ch:pick_tgt:{c['id']}")] for c in others]
        rows.append([btn("✖ 取消", "menu:ch")])
        await msg.reply_text(
            f"📡 <b>2/2</b>\n源: <code>{esc(text)}</code>\n\n选择目标或发送 ID：",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )

    elif state == S.CH_TGT:
        if not _is_id(text):
            await msg.reply_text("❌ 格式错误")
            return
        src = ud.get("draft", {}).get("src", "")
        if src:
            cfg["channel_mappings"][src] = text
            for c, r in [(src, "src"), (text, "tgt")]:
                cfg["known_channels"].setdefault(c, {})["role"] = r
            save_config()
            ud["state"] = S.IDLE
            await msg.reply_text(
                f"✅ <code>{esc(ch_label(int(src)))}</code> → <code>{esc(text)}</code>",
                parse_mode="HTML",
                reply_markup=back_kb(("📡 频道映射", "menu:ch"), ("⚙️ 主菜单", "menu:main")),
            )

    # ── 替换规则输入 ──────────────────────────────────────────────────────────
    elif state == S.RULE_OLD:
        ud["state"] = S.RULE_NEW
        ud.setdefault("draft", {})["rule_old"] = text
        await msg.reply_text(
            f"🔤 <b>2/2</b>\n原文: <code>{esc(text)}</code>\n\n"
            "发送替换文（<code>-</code> 表示删除该词）：",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif state == S.RULE_NEW:
        old = ud.get("draft", {}).get("rule_old", "")
        new = "" if text == "-" else text
        cfg["replace_rules"][old] = new
        save_config()
        ud["state"] = S.IDLE
        await msg.reply_text(
            f"✅ <code>{esc(old)}</code> → <code>{esc(new) if new else '[删]'}</code>",
            parse_mode="HTML",
            reply_markup=back_kb(("🔤 规则", "menu:rule"), ("⚙️ 主菜单", "menu:main")),
        )

    # ── 管理员输入 ────────────────────────────────────────────────────────────
    elif state == S.ADMIN_ID:
        try:
            nid = int(text)
        except ValueError:
            await msg.reply_text("❌ 请输入纯数字 ID")
            return
        if nid not in cfg["admins"]:
            cfg["admins"].append(nid)
            save_config()
            result = f"✅ 已添加: <code>{nid}</code>"
        else:
            result = "ℹ️ 已是管理员"
        ud["state"] = S.IDLE
        await msg.reply_text(
            result, parse_mode="HTML",
            reply_markup=back_kb(("👮 管理员", "menu:admin"), ("⚙️ 主菜单", "menu:main")),
        )

    # ── 广告输入 ──────────────────────────────────────────────────────────────
    elif state == S.AD_TEXT:
        ud.setdefault("draft", {})["ad_text"] = text
        ud["state"] = S.AD_INTERVAL
        await msg.reply_text(
            "📢 <b>2/4</b>\n\n发送<b>推送间隔</b>（分钟）：\n"
            "<code>60</code>=每小时  <code>1440</code>=每天",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif state == S.AD_INTERVAL:
        try:
            mins = int(text)
            assert mins >= 1
        except (ValueError, AssertionError):
            await msg.reply_text("❌ 请输入正整数（最小 1）")
            return
        ud["draft"]["ad_interval"] = mins
        ud["state"] = S.AD_CHANNELS
        tgts = known_ch_list("tgt") or known_ch_list()
        rows = [[btn(f"📥 {esc(c['title'])}", f"ad:pick_ch:{c['id']}")] for c in tgts[:6]]
        rows.append([btn("📡 所有目标频道", "ad:pick_ch:all")])
        rows.append([btn("✖ 取消", "cb:cancel")])
        await msg.reply_text(
            "📢 <b>3/4</b>\n\n选择<b>目标频道</b>或发送 ID（空格分隔）：",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )

    elif state == S.AD_CHANNELS:
        if text.lower() == "all":
            channels = list(get_mappings().values())
        else:
            try:
                channels = [int(x) for x in text.split() if x]
            except ValueError:
                await msg.reply_text("❌ 格式错误，请输入频道 ID（空格分隔）")
                return
        if not channels:
            await msg.reply_text("⚠️ 没有目标频道")
            return
        ad_id = ud.get("draft", {}).get("ad_id", str(uuid.uuid4())[:8])
        ud["draft"].update({"ad_channels": channels, "ad_id": ad_id})
        ud["state"] = S.AD_BUTTONS
        await msg.reply_text(
            "📢 <b>4/4（可选）</b>\n\n发送内联按钮：\n"
            "<code>文字::URL</code>\n<code>A::URL | B::URL</code>（同行多个）",
            parse_mode="HTML",
            reply_markup=kb(
                [btn("⏭ 跳过", f"ad:skip_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )

    elif state == S.AD_BUTTONS:
        buttons = parse_buttons(text)
        if not buttons:
            await msg.reply_text("❌ 格式错误，或点跳过")
            return
        ad_id = ud.get("draft", {}).get("ad_id", str(uuid.uuid4())[:8])
        ud["draft"]["buttons_pending"] = buttons
        await msg.reply_text(
            f"👇 <b>预览</b>\n\n{esc(ud['draft'].get('ad_text', ''))}",
            parse_mode="HTML",
            reply_markup=build_markup(buttons),
        )
        await msg.reply_text(
            "确认创建？",
            reply_markup=kb(
                [btn("✅ 确认", f"ad:confirm_buttons:{ad_id}")],
                [btn("✖ 取消", "cb:cancel")],
            ),
        )


# ━━━ 广告任务 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _save_ad(app: Application, q, ud: dict, ad_id: str, buttons: list) -> None:
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
    ch_str = ", ".join(esc(ch_label(c)) for c in ad["channels"])
    button_count = sum(len(r) for r in buttons)
    await q.edit_message_text(
        f"✅ <b>广告已创建</b>\n\n"
        f"⏱{ad['interval_minutes']}min  📡{ch_str}\n"
        f"🔘{button_count} 个按钮\n\n{esc(ad['text'][:60])}",
        parse_mode="HTML",
        reply_markup=kb([btn("📢 广告管理", "menu:ad"), btn("⚙️ 主菜单", "menu:main")]),
    )


def remove_ad_job(app: Application, ad_id: str) -> None:
    for job in app.job_queue.get_jobs_by_name(f"ad_{ad_id}"):
        job.schedule_removal()


def reschedule_ad(app: Application, ad: dict) -> None:
    remove_ad_job(app, ad["id"])
    if not ad.get("enabled", True):
        return
    app.job_queue.run_repeating(
        _send_ad,
        interval=ad["interval_minutes"] * 60,
        first=ad["interval_minutes"] * 60,
        name=f"ad_{ad['id']}",
        data=ad["id"],
    )


async def _send_ad(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ad = next((a for a in cfg["ads"] if a["id"] == ctx.job.data), None)
    if not ad or not ad.get("enabled", True):
        return
    markup = build_markup(ad.get("buttons", []))
    channels = ad.get("channels") or list(get_mappings().values())
    for ch in channels:
        try:
            await _send_with_retry(
                ctx.bot.send_message,
                chat_id=ch,
                text=ad["text"],
                reply_markup=markup,
            )
            _inc_fwd()
            logger.info(f"广告推送:{ad['id']}→{ch}")
        except TelegramError as e:
            logger.error(f"广告推送失败 {ad['id']}→{ch}: {e}")
            _inc_err()


# ━━━ 消息转发 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _forward_single(ctx: ContextTypes.DEFAULT_TYPE, msg, target: int) -> int | None:
    """转发单条消息，返回目标消息 ID；不支持的类型返回 None。"""
    has_rules = bool(cfg["replace_rules"])
    cap  = apply_replace(msg.caption)
    ent  = msg.entities if not has_rules else None
    cent = msg.caption_entities if not has_rules else None

    sent = None
    if msg.text:
        sent = await _send_with_retry(
            ctx.bot.send_message,
            chat_id=target,
            text=apply_replace(msg.text),
            entities=ent,
        )
    elif msg.photo:
        sent = await _send_with_retry(
            ctx.bot.send_photo,
            chat_id=target,
            photo=msg.photo[-1].file_id,
            caption=cap,
            caption_entities=cent,
        )
    elif msg.video:
        sent = await _send_with_retry(
            ctx.bot.send_video,
            chat_id=target,
            video=msg.video.file_id,
            caption=cap,
            caption_entities=cent,
        )
    elif msg.audio:
        sent = await _send_with_retry(
            ctx.bot.send_audio,
            chat_id=target,
            audio=msg.audio.file_id,
            caption=cap,
        )
    elif msg.document:
        sent = await _send_with_retry(
            ctx.bot.send_document,
            chat_id=target,
            document=msg.document.file_id,
            caption=cap,
        )
    elif msg.animation:
        sent = await _send_with_retry(
            ctx.bot.send_animation,
            chat_id=target,
            animation=msg.animation.file_id,
            caption=cap,
        )
    elif msg.sticker:
        sent = await _send_with_retry(
            ctx.bot.send_sticker,
            chat_id=target,
            sticker=msg.sticker.file_id,
        )
    elif msg.voice:
        sent = await _send_with_retry(
            ctx.bot.send_voice,
            chat_id=target,
            voice=msg.voice.file_id,
            caption=cap,
        )
    elif msg.video_note:
        sent = await _send_with_retry(
            ctx.bot.send_video_note,
            chat_id=target,
            video_note=msg.video_note.file_id,
        )
    elif msg.poll:
        p = msg.poll
        sent = await _send_with_retry(
            ctx.bot.send_poll,
            chat_id=target,
            question=apply_replace(p.question),
            options=[apply_replace(o.text) for o in p.options],
            is_anonymous=p.is_anonymous,
            type=p.type,
            allows_multiple_answers=p.allows_multiple_answers,
        )
    elif msg.location:
        sent = await _send_with_retry(
            ctx.bot.send_location,
            chat_id=target,
            latitude=msg.location.latitude,
            longitude=msg.location.longitude,
        )
    elif msg.venue:
        sent = await _send_with_retry(
            ctx.bot.send_venue,
            chat_id=target,
            latitude=msg.venue.location.latitude,
            longitude=msg.venue.location.longitude,
            title=msg.venue.title,
            address=msg.venue.address,
        )
    elif msg.contact:
        sent = await _send_with_retry(
            ctx.bot.send_contact,
            chat_id=target,
            phone_number=msg.contact.phone_number,
            first_name=msg.contact.first_name,
            last_name=msg.contact.last_name,
        )
    else:
        logger.warning(f"不支持的消息类型 id={msg.message_id}")
        return None

    return sent.message_id if sent else None


async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg:
        return
    m = get_mappings()
    if msg.chat_id not in m:
        return

    target = m[msg.chat_id]

    # 媒体组：缓冲 0.5s 后批量转发
    if msg.media_group_id:
        group_id = msg.media_group_id
        _media_groups[group_id].append(msg)
        job_name = f"mg_{group_id}"
        for job in ctx.application.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()
        ctx.application.job_queue.run_once(
            _flush_media_group, when=0.5, name=job_name, data=group_id,
        )
        return

    try:
        tgt_msg_id = await _forward_single(ctx, msg, target)
        if tgt_msg_id:
            _store_msg(msg.chat_id, msg.message_id, target, tgt_msg_id)
        _inc_fwd()
        logger.info(f"[转发] {msg.chat_id}→{target} id={msg.message_id}")
    except TelegramError as e:
        _inc_err()
        logger.error(f"转发失败 {msg.chat_id}→{target}: {e}")


async def handle_edited_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """同步频道消息编辑到目标频道。"""
    msg = update.edited_channel_post
    if not msg:
        return
    m = get_mappings()
    if msg.chat_id not in m:
        return

    mapping = _lookup_msg(msg.chat_id, msg.message_id)
    if not mapping:
        return
    tgt_chat, tgt_msg = mapping

    try:
        if msg.text:
            await _send_with_retry(
                ctx.bot.edit_message_text,
                chat_id=tgt_chat,
                message_id=tgt_msg,
                text=apply_replace(msg.text),
            )
        elif msg.caption is not None:
            await _send_with_retry(
                ctx.bot.edit_message_caption,
                chat_id=tgt_chat,
                message_id=tgt_msg,
                caption=apply_replace(msg.caption),
            )
        logger.info(f"[编辑同步] {msg.chat_id}:{msg.message_id}→{tgt_chat}:{tgt_msg}")
    except TelegramError as e:
        logger.warning(f"编辑同步失败: {e}")


# ━━━ 命令 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        if not cfg["admins"] and not ENV_ADMIN_IDS:
            cfg["admins"].append(uid)
            save_config()
        else:
            await update.message.reply_text("👋 此机器人需要管理员权限。")
            return
    if not cfg["setup_complete"]:
        await wiz_admin(update.message, ctx.user_data)
    else:
        await update.message.reply_text(
            "👋 <b>Telegram 频道同步机器人 v8</b>\n\n"
            "/settings — 打开设置面板\n"
            "/status — 查看运行状态\n"
            "/help — 帮助信息",
            parse_mode="HTML",
        )


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ 无权限")
        return
    if not cfg["setup_complete"]:
        await wiz_admin(update.message, ctx.user_data)
        return
    ctx.user_data["state"] = S.IDLE
    await update.message.reply_text(
        main_menu_text(), parse_mode="HTML", reply_markup=MAIN_MENU_KB,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ 无权限")
        return
    m = get_mappings()
    _reset_daily()
    await update.message.reply_text(
        f"✅ <b>运行中</b>\n\n"
        f"📡 映射:{len(m)}  🤖 已知:{len(cfg['known_channels'])}\n"
        f"🔤 规则:{len(cfg['replace_rules'])}  "
        f"📢 广告:{sum(1 for a in cfg['ads'] if a.get('enabled', True))}/{len(cfg['ads'])}\n"
        f"📨 今日转发:{_daily['forwarded']}  ❌ 失败:{_daily['errors']}",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 <b>帮助</b>\n\n"
        "<b>命令</b>\n"
        "/start — 启动 / 首次配置\n"
        "/settings — 打开设置面板\n"
        "/status — 查看运行状态\n"
        "/help — 帮助信息\n\n"
        "<b>支持的消息类型</b>\n"
        "• 文本 / 图片 / 视频 / 音频\n"
        "• 文件 / 动画(GIF) / 贴纸 / 语音\n"
        "• 视频消息 / 投票 / 位置 / 场所 / 联系人\n"
        "• 媒体组（相册）\n\n"
        "<b>功能特性</b>\n"
        "• 多频道映射，自动识别频道\n"
        "• 文本替换规则\n"
        "• 定时广告推送（含内联按钮）\n"
        "• 消息编辑同步\n"
        "• 媒体组完整转发\n"
        "• 洪水控制自动重试",
        parse_mode="HTML",
    )


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"[全局错误] {ctx.error}", exc_info=ctx.error)


# ━━━ 启动 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _post_init(app: Application) -> None:
    for ad in cfg.get("ads", []):
        if ad.get("enabled", True):
            reschedule_ad(app, ad)
    logger.info(
        f"🚀 启动完成 | 映射:{len(get_mappings())}  "
        f"已知:{len(cfg.get('known_channels', {}))}  "
        f"广告:{len(cfg.get('ads', []))}"
    )


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_private_text,
    ))
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POST,
        handle_channel_post,
    ))
    app.add_handler(MessageHandler(
        filters.UpdateType.EDITED_CHANNEL_POST,
        handle_edited_channel_post,
    ))
    app.add_error_handler(error_handler)
    app.post_init = _post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
