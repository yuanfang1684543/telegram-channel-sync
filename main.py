from __future__ import annotations
"""
Telegram 频道同步机器人 v5 + Google 集成
- 自动识别频道
- 首次引导向导
- 全内联按键操作
- Google Drive 备份
- Gmail 日报 & 告警
"""
import os, json, uuid, logging
from datetime import datetime
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

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 未设置")

ENV_ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))

# ── 状态机 ────────────────────────────────────────────────────────────────────
class S:
    IDLE="idle"; WIZ_ADMIN="wiz_admin"; WIZ_SRC="wiz_src"; WIZ_TGT="wiz_tgt"
    CH_SRC="ch_src"; CH_TGT="ch_tgt"; RULE_OLD="rule_old"; RULE_NEW="rule_new"
    ADMIN_ID="admin_id"; AD_TEXT="ad_text"; AD_INTERVAL="ad_interval"
    AD_CHANNELS="ad_channels"; AD_BUTTONS="ad_buttons"
    G_FOLDER="g_folder"; G_EMAIL="g_email"; G_HOUR="g_hour"

# ── 配置 ──────────────────────────────────────────────────────────────────────
def _default() -> dict:
    return {
        "setup_complete": False, "channel_mappings": {}, "known_channels": {},
        "replace_rules": {}, "admins": [], "ads": [],
        "google": {"drive_backup": False, "daily_report": False, "error_alert": False,
                   "drive_folder_id": "", "notify_email": "", "report_hour": 8},
    }

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
            for k, v in _default().items():
                if isinstance(v, dict):
                    data.setdefault(k, {})
                    for kk, vv in v.items():
                        data[k].setdefault(kk, vv)
                else:
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
for _aid in ENV_ADMIN_IDS:
    if _aid not in cfg["admins"]: cfg["admins"].append(_aid)
_s0, _t0 = os.getenv("SOURCE_CHANNEL_ID"), os.getenv("TARGET_CHANNEL_ID")
if _s0 and _t0 and _s0 not in cfg["channel_mappings"]:
    cfg["channel_mappings"][_s0] = _t0; cfg["setup_complete"] = True
save_config()

# ── 每日统计 ──────────────────────────────────────────────────────────────────
_daily: dict = {"forwarded": 0, "errors": 0, "date": ""}

def _today() -> str:
    from datetime import date; return str(date.today())

def _reset_daily():
    if _daily["date"] != _today():
        _daily.update({"forwarded": 0, "errors": 0, "date": _today()})

def _inc_fwd(): _reset_daily(); _daily["forwarded"] += 1
def _inc_err(): _reset_daily(); _daily["errors"] += 1

# ━━━ 工具 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
    for o, n in cfg["replace_rules"].items(): text = text.replace(o, n)
    return text

def ch_label(cid: int) -> str:
    return cfg["known_channels"].get(str(cid), {}).get("title") or str(cid)

def known_ch_list(role: str | None = None) -> list[dict]:
    return [{"id": int(k), "title": v.get("title", k), "role": v.get("role")}
            for k, v in cfg["known_channels"].items()
            if role is None or v.get("role") == role]

def _is_id(s: str) -> bool: return s.lstrip("-").isdigit()

def parse_buttons(raw: str) -> list[list[dict]]:
    rows = []
    for line in raw.strip().splitlines():
        row = [{"text": t.strip(), "url": u.strip()}
               for cell in line.split("|")
               if "::" in (cell := cell.strip())
               for t, u in [cell.split("::", 1)]
               if t.strip() and u.strip()]
        if row: rows.append(row)
    return rows

def build_markup(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons: return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in buttons]
    )

def kb(*rows: list) -> InlineKeyboardMarkup: return InlineKeyboardMarkup(list(rows))
def btn(t: str, d: str) -> InlineKeyboardButton: return InlineKeyboardButton(t, callback_data=d)
def cancel_kb(back: str = "cb:cancel") -> InlineKeyboardMarkup: return kb([btn("✖ 取消", back)])

# ━━━ Google 服务 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _drive_ok() -> bool: return bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
def _gmail_ok() -> bool: return bool(os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD"))

def _drive_svc():
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        sa = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/drive"])
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error(f"Drive 初始化失败: {e}"); return None

def drive_upload(content: str, name: str, folder_id: str | None = None) -> str | None:
    svc = _drive_svc()
    if not svc: return None
    try:
        from googleapiclient.http import MediaInMemoryUpload
        meta = {"name": name}
        if folder_id: meta["parents"] = [folder_id]
        media = MediaInMemoryUpload(content.encode(), mimetype="text/plain")
        f = svc.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
        logger.info(f"Drive 上传: {name}"); return f.get("id")
    except Exception as e:
        logger.error(f"Drive 上传失败: {e}"); return None

def drive_list(folder_id: str | None = None) -> list[dict]:
    svc = _drive_svc()
    if not svc: return []
    try:
        q = f"'{folder_id}' in parents and trashed=false" if folder_id else "trashed=false"
        r = svc.files().list(q=q, fields="files(id,name,modifiedTime,webViewLink)",
                             orderBy="modifiedTime desc").execute()
        return r.get("files", [])
    except Exception as e:
        logger.error(f"Drive 列表失败: {e}"); return []

def drive_delete(file_id: str) -> bool:
    svc = _drive_svc()
    if not svc: return False
    try: svc.files().delete(fileId=file_id).execute(); return True
    except Exception as e: logger.error(f"Drive 删除失败: {e}"); return False

def gmail_send(to: str, subject: str, body: str, html: str | None = None) -> bool:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    user, pw = os.getenv("GMAIL_USER", ""), os.getenv("GMAIL_APP_PASSWORD", "")
    if not user or not pw: return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = user; msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html: msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(user, pw); s.sendmail(user, [to], msg.as_string())
        logger.info(f"Gmail 已发送: {subject}"); return True
    except Exception as e:
        logger.error(f"Gmail 失败: {e}"); return False

# ━━━ 面板构建 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main_menu_text() -> str:
    m = get_mappings(); gcfg = cfg["google"]
    drive_s = "✅" if _drive_ok() else "❌"; gmail_s = "✅" if _gmail_ok() else "❌"
    return (f"⚙️ *设置面板*\n\n"
            f"📡 频道映射: {len(m)} 条  🤖 已知: {len(cfg['known_channels'])}\n"
            f"🔤 替换规则: {len(cfg['replace_rules'])} 条\n"
            f"📢 广告: {sum(1 for a in cfg['ads'] if a.get('enabled',True))}/{len(cfg['ads'])}\n"
            f"☁️ Drive:{drive_s}  📧 Gmail:{gmail_s}")

MAIN_MENU_KB = kb(
    [btn("📡 频道映射", "menu:ch"),   btn("🤖 已知频道", "menu:known")],
    [btn("🔤 替换规则", "menu:rule"),  btn("📢 定时广告",  "menu:ad")],
    [btn("👮 管理员",   "menu:admin"), btn("🔗 Google集成","menu:google")],
    [btn("📊 运行状态", "cb:status"),  btn("❌ 关闭",      "cb:close")],
)

def google_panel() -> tuple[str, InlineKeyboardMarkup]:
    gcfg = cfg["google"]
    bk = "✅" if gcfg.get("drive_backup") else "⭕"
    rp = "✅" if gcfg.get("daily_report") else "⭕"
    al = "✅" if gcfg.get("error_alert")  else "⭕"
    folder = gcfg.get("drive_folder_id","") or "未设置"
    email  = gcfg.get("notify_email","")    or "未设置"
    hour   = gcfg.get("report_hour", 8)
    drive_s = "✅ 已连接" if _drive_ok() else "❌ 未配置"
    gmail_s = "✅ 已连接" if _gmail_ok() else "❌ 未配置"
    txt = (
        "🔗 *Google 集成*\n\n"
        f"☁️ Google Drive: {drive_s}\n"
        f"📧 Gmail SMTP: {gmail_s}\n\n"
        f"*Drive 功能*\n"
        f"• 自动备份: {bk}  文件夹: `{folder[:24]}`\n\n"
        f"*Gmail 功能*\n"
        f"• 每日报告: {rp}  邮箱: `{email}`  时间: {hour:02d}:00\n"
        f"• 错误告警: {al}\n\n"
        "_在 Railway Variables 中配置：_\n"
        "`GOOGLE_SERVICE_ACCOUNT_JSON` `GMAIL_USER` `GMAIL_APP_PASSWORD`"
    )
    mk = kb(
        [btn(f"☁️ 自动备份 {bk}", "g:toggle_backup"),
         btn("📁 设置文件夹", "g:set_folder")],
        [btn(f"📧 每日报告 {rp}", "g:toggle_report"),
         btn("📮 设置邮箱", "g:set_email")],
        [btn(f"🚨 错误告警 {al}", "g:toggle_alert"),
         btn(f"🕐 报告时间 {hour:02d}:00", "g:set_hour")],
        [btn("💾 立即备份", "g:backup_now"),
         btn("📤 立即发报告", "g:report_now")],
        [btn("📋 Drive文件列表", "g:list_files")],
        [btn("◀ 返回", "menu:main")],
    )
    return txt, mk

def ch_panel() -> tuple[str, InlineKeyboardMarkup]:
    m = get_mappings()
    rows = [[btn(f"🗑 {ch_label(s)} → {ch_label(t)}", f"ch:del:{s}")] for s, t in m.items()]
    rows.append([btn("➕ 添加映射", "ch:add"), btn("◀ 返回", "menu:main")])
    txt = "📡 *频道映射*\n\n" + ("\n".join(f"`{ch_label(s)}` → `{ch_label(t)}`" for s,t in m.items()) or "暂无") + "\n\n_点条目删除_"
    return txt, InlineKeyboardMarkup(rows)

def known_panel() -> tuple[str, InlineKeyboardMarkup]:
    known = cfg["known_channels"]
    rows = [[btn({"src":"📤 ","tgt":"📥 "}.get(v.get("role"),"📡 ") + v.get("title",k), f"known:detail:{k}")]
            for k, v in known.items()]
    rows.append([btn("◀ 返回", "menu:main")])
    txt = "🤖 *已知频道*\n\n" + ("\n".join(
        f"• {v.get('title',k)} — " + {"src":"📤源","tgt":"📥目标"}.get(v.get("role"),"未分配")
        for k, v in known.items()) or "暂无\n将机器人添加为频道管理员后自动识别")
    return txt, InlineKeyboardMarkup(rows)

def known_detail(cid: str) -> tuple[str, InlineKeyboardMarkup]:
    info = cfg["known_channels"].get(cid, {}); title = info.get("title", cid)
    role_txt = {"src":"📤 源频道", "tgt":"📥 目标频道"}.get(info.get("role"), "未分配")
    txt = f"📡 *{title}*\nID: `{cid}`\n角色: {role_txt}"
    mk = kb([btn("📤 设为源频道", f"known:set_src:{cid}"), btn("📥 设为目标频道", f"known:set_tgt:{cid}")],
            [btn("🗑 移除", f"known:del:{cid}"), btn("◀ 返回", "menu:known")])
    return txt, mk

def rule_panel() -> tuple[str, InlineKeyboardMarkup]:
    rules = cfg["replace_rules"]
    rows = [[btn(f"🗑 「{o[:12]}」→「{(n or '删')[:12]}」", f"rule:del:{i}")] for i,(o,n) in enumerate(rules.items())]
    rows.append([btn("➕ 添加规则", "rule:add"), btn("◀ 返回", "menu:main")])
    txt = "🔤 *替换规则*\n\n" + ("\n".join(f"`{o}` → `{n or '[删]'}`" for o,n in rules.items()) or "暂无") + "\n\n_点条目删除_"
    return txt, InlineKeyboardMarkup(rows)

def ad_panel() -> tuple[str, InlineKeyboardMarkup]:
    ads = cfg["ads"]
    rows = [[btn(("✅" if a.get("enabled",True) else "⏸") + f" {a['text'][:16]}… /{a['interval_minutes']}min", f"ad:detail:{a['id']}")] for a in ads]
    rows.append([btn("➕ 新建广告", "ad:add"), btn("◀ 返回", "menu:main")])
    lines = ["📢 *定时广告*\n"] + ([f"{i}. {'✅' if a.get('enabled',True) else '⏸'} {a['text'][:20]} / 每{a['interval_minutes']}min" for i,a in enumerate(ads,1)] or ["暂无"]) + ["\n_点条目管理_"]
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def ad_detail(ad_id: str) -> tuple[str, InlineKeyboardMarkup]:
    ad = next((a for a in cfg["ads"] if a["id"]==ad_id), None)
    if not ad: return "❌ 不存在", kb([btn("◀",  "menu:ad")])
    s = "✅运行" if ad.get("enabled",True) else "⏸暂停"
    ch = ", ".join(ch_label(c) for c in ad.get("channels",[])) or "继承映射"
    btn_rows = ad.get("buttons",[])
    bp = "\n".join("  "+" | ".join(f"[{b['text']}]" for b in r) for r in btn_rows)
    txt = f"📢 *广告* | {s}\n间隔:{ad['interval_minutes']}min 目标:{ch}\n" + (f"```\n{bp}\n```\n" if bp else "") + f"\n{ad['text']}"
    tog = "⏸暂停" if ad.get("enabled",True) else "▶启动"
    return txt, kb([btn(tog, f"ad:toggle:{ad_id}"), btn("🗑删除", f"ad:del:{ad_id}")], [btn("◀返回", "menu:ad")])

def admin_panel() -> tuple[str, InlineKeyboardMarkup]:
    admins = cfg["admins"]
    rows = [[btn(f"🗑 {a}", f"admin:del:{a}")] for a in admins]
    rows.append([btn("➕ 添加管理员", "admin:add"), btn("◀ 返回", "menu:main")])
    txt = "👮 *管理员*\n\n" + ("\n".join(f"• `{a}`" for a in admins) or "暂无") + "\n\n_点条目删除_"
    return txt, InlineKeyboardMarkup(rows)

# ━━━ 向导 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WIZ_STEPS = {"admin":"第1步 设置管理员","source":"第2步 设置源频道","target":"第3步 设置目标频道","confirm":"第4步 确认完成"}

def wiz_progress(cur: str) -> str:
    keys = list(WIZ_STEPS.keys()); past = True
    out = []
    for k in keys:
        if k == cur: past = False; out.append(f"🔵 {WIZ_STEPS[k]}")
        elif past:   out.append(f"✅ {WIZ_STEPS[k]}")
        else:        out.append(f"⚪ {WIZ_STEPS[k]}")
    return "\n".join(out)

async def wiz_admin(tgt, ud, edit=False):
    ud["state"] = S.WIZ_ADMIN
    txt = (f"🧙 *设置向导*\n\n{wiz_progress('admin')}\n\n━━━━━━━━━\n"
           "发送你的 Telegram *数字 ID* 设为管理员\n获取：发消息给 @userinfobot\n\n"
           + (f"当前管理员: " + " ".join(f"`{a}`" for a in cfg["admins"]) if cfg["admins"] else "⚠️ 暂无管理员"))
    mk = kb([btn("➡ 跳过（已有管理员）", "wiz:skip_admin")] if cfg["admins"] else [])
    if edit: await tgt.edit_message_text(txt, parse_mode="Markdown", reply_markup=mk)
    else:    await tgt.reply_text(txt, parse_mode="Markdown", reply_markup=mk)

async def wiz_source(tgt, ud, edit=False):
    ud["state"] = S.WIZ_SRC
    known = known_ch_list()
    rows = [[btn(f"📡 {c['title']}", f"wiz:src:{c['id']}")] for c in known]
    rows.append([btn("⬅ 返回", "wiz:back_admin")])
    lines = [f"🧙 *设置向导*\n\n{wiz_progress('source')}\n\n━━━━━━━━━\n",
             "选择*源频道*（监听的频道）或发送频道ID：\n" if known else "⚠️ 暂未检测到频道，先将机器人加为管理员\n或直接发送频道ID："]
    txt = "\n".join(lines)
    mk = InlineKeyboardMarkup(rows)
    if edit: await tgt.edit_message_text(txt, parse_mode="Markdown", reply_markup=mk)
    else:    await tgt.reply_text(txt, parse_mode="Markdown", reply_markup=mk)

async def wiz_target(tgt, ud, src_id, edit=False):
    ud["state"] = S.WIZ_TGT
    ud.setdefault("draft", {})["wiz_src"] = src_id
    known = [c for c in known_ch_list() if str(c["id"]) != src_id]
    rows = [[btn(f"📡 {c['title']}", f"wiz:tgt:{c['id']}")] for c in known]
    rows.append([btn("⬅ 返回", "wiz:back_source")])
    src_t = ch_label(int(src_id)) if src_id in cfg["known_channels"] else src_id
    txt = (f"🧙 *设置向导*\n\n{wiz_progress('target')}\n\n━━━━━━━━━\n"
           f"源: `{src_t}`\n\n选择*目标频道*或发送频道ID：")
    mk = InlineKeyboardMarkup(rows)
    if edit: await tgt.edit_message_text(txt, parse_mode="Markdown", reply_markup=mk)
    else:    await tgt.reply_text(txt, parse_mode="Markdown", reply_markup=mk)

async def wiz_confirm(tgt, ud, src_id, tgt_id, edit=False):
    ud.setdefault("draft", {}).update({"wiz_src": src_id, "wiz_tgt": tgt_id})
    src_t = ch_label(int(src_id)); tgt_t = ch_label(int(tgt_id))
    txt = (f"🧙 *设置向导*\n\n{wiz_progress('confirm')}\n\n━━━━━━━━━\n\n"
           f"📤 源: `{src_t}`\n📥 目标: `{tgt_t}`\n👮 管理员: {len(cfg['admins'])} 人\n\n确认完成设置？")
    mk = kb([btn("✅ 确认完成", f"wiz:confirm:{src_id}:{tgt_id}")], [btn("⬅ 重选", "wiz:back_source")])
    if edit: await tgt.edit_message_text(txt, parse_mode="Markdown", reply_markup=mk)
    else:    await tgt.reply_text(txt, parse_mode="Markdown", reply_markup=mk)

# ━━━ 频道自动识别 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chg = update.my_chat_member
    chat = chg.chat
    if chat.type not in (Chat.CHANNEL, Chat.SUPERGROUP): return
    cid = str(chat.id); title = chat.title or cid
    is_adm_now = isinstance(chg.new_chat_member, (ChatMemberAdministrator, ChatMemberOwner))
    was_adm    = isinstance(chg.old_chat_member, (ChatMemberAdministrator, ChatMemberOwner))
    if is_adm_now and not was_adm:
        logger.info(f"加入频道: {title} ({cid})")
        cfg["known_channels"].setdefault(cid, {}).update({"title": title})
        if "role" not in cfg["known_channels"][cid]: cfg["known_channels"][cid]["role"] = None
        save_config()
        note = (f"🔔 *检测到新频道*\n\n📡 *{title}*\n`{cid}`\n\n请设置其角色完成同步配置")
        for aid in cfg["admins"]:
            try:
                await ctx.bot.send_message(aid, note, parse_mode="Markdown", reply_markup=kb(
                    [btn("📤 设为源频道", f"known:set_src:{cid}"), btn("📥 设为目标频道", f"known:set_tgt:{cid}")],
                    [btn("⚙️ 打开设置", "menu:known")]))
            except TelegramError as e: logger.warning(f"通知失败 {aid}: {e}")
    elif not is_adm_now and was_adm:
        logger.info(f"离开频道: {title} ({cid})")
        if cid in cfg["known_channels"]: cfg["known_channels"][cid]["role"] = None; save_config()
        for aid in cfg["admins"]:
            try: await ctx.bot.send_message(aid, f"⚠️ 机器人已失去 *{title}* 管理员权限", parse_mode="Markdown")
            except TelegramError: pass

# ━━━ 回调路由 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data; ud = ctx.user_data
    if not is_admin(uid): await q.answer("⛔ 无权限", show_alert=True); return

    # ── Google 回调 ───────────────────────────────────────────────────────────
    gcfg = cfg["google"]
    if data == "menu:google":
        t, mk = google_panel(); await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)
    elif data == "g:toggle_backup":
        gcfg["drive_backup"] = not gcfg.get("drive_backup"); save_config()
        schedule_google_jobs(ctx.application)
        t, mk = google_panel(); await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)
    elif data == "g:toggle_report":
        gcfg["daily_report"] = not gcfg.get("daily_report"); save_config()
        schedule_google_jobs(ctx.application)
        t, mk = google_panel(); await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)
    elif data == "g:toggle_alert":
        gcfg["error_alert"] = not gcfg.get("error_alert"); save_config()
        t, mk = google_panel(); await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)
    elif data == "g:set_folder":
        ud["state"] = S.G_FOLDER
        await q.edit_message_text("📁 *设置 Drive 文件夹*\n\n发送文件夹 ID\n（Drive 地址栏最后一段字符串）",
                                  parse_mode="Markdown", reply_markup=cancel_kb("menu:google"))
    elif data == "g:set_email":
        ud["state"] = S.G_EMAIL
        await q.edit_message_text("📮 *设置通知邮箱*\n\n发送接收报告的 Gmail 地址：",
                                  parse_mode="Markdown", reply_markup=cancel_kb("menu:google"))
    elif data == "g:set_hour":
        ud["state"] = S.G_HOUR
        await q.edit_message_text("🕐 *报告发送时间*\n\n发送小时数（0-23）：",
                                  parse_mode="Markdown", reply_markup=cancel_kb("menu:google"))
    elif data == "g:backup_now":
        await q.edit_message_text("⏳ 正在备份…")
        await _do_backup(ctx)
        t, mk = google_panel(); await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)
    elif data == "g:report_now":
        await q.edit_message_text("⏳ 正在发送报告…")
        await _do_report(ctx)
        t, mk = google_panel(); await q.edit_message_text(t, parse_mode="Markdown", reply_markup=mk)
    elif data == "g:list_files":
        files = drive_list(gcfg.get("drive_folder_id") or None)
        if not files:
            await q.edit_message_text("📋 Drive 暂无文件（或未配置服务账号）",
                                      reply_markup=kb([btn("◀ 返回", "menu:google")])); return
        rows = [[InlineKeyboardButton(f"📄 {f['name'][:22]}", url=f.get("webViewLink","https://drive.google.com"))] for f in files[:8]]
        rows.append([btn("🗑 清理旧备份(保留5个)", "g:clean"), btn("◀ 返回", "menu:google")])
        lines = ["📋 *Drive 文件*\n"] + [f"• {f['name']} _{f.get('modifiedTime','')[:10]}_" for f in files[:8]]
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    elif data == "g:clean":
        files = drive_list(gcfg.get("drive_folder_id") or None)
        for f in files[5:]: drive_delete(f["id"])
        await q.edit_message_text(f"🗑 已删除 {max(0,len(files)-5)} 个旧文件",
                                  reply_markup=kb([btn("◀ 返回", "menu:google")]))

    # ── 向导回调 ──────────────────────────────────────────────────────────────
    elif data == "wiz:skip_admin":  await wiz_source(q, ud, edit=True)
    elif data == "wiz:back_admin":  await wiz_admin(q, ud, edit=True)
    elif data == "wiz:back_source": await wiz_source(q, ud, edit=True)
    elif data.startswith("wiz:src:"):  await wiz_target(q, ud, data[8:], edit=True)
    elif data.startswith("wiz:tgt:"):
        src = ud.get("draft", {}).get("wiz_src", "")
        if src: await wiz_confirm(q, ud, src, data[8:], edit=True)
    elif data.startswith("wiz:confirm:"):
        _, _, src, tgt = data.split(":", 3)
        cfg["channel_mappings"][src] = tgt
        for cid, role in [(src, "src"), (tgt, "tgt")]:
            cfg["known_channels"].setdefault(cid, {})["role"] = role
        cfg["setup_complete"] = True; save_config(); ud["state"] = S.IDLE
        await q.edit_message_text(
            f"🎉 *设置完成！*\n\n📤 源: `{ch_label(int(src))}`\n📥 目标: `{ch_label(int(tgt))}`\n\n机器人开始同步消息。",
            parse_mode="Markdown", reply_markup=kb([btn("⚙️ 打开设置", "menu:main")]))

    # ── 主菜单 ────────────────────────────────────────────────────────────────
    elif data == "menu:main":
        ud["state"] = S.IDLE
        await q.edit_message_text(main_menu_text(), parse_mode="Markdown", reply_markup=MAIN_MENU_KB)
    elif data == "menu:ch":    ud["state"]=S.IDLE; t,mk=ch_panel();    await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data == "menu:known": ud["state"]=S.IDLE; t,mk=known_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data == "menu:rule":  ud["state"]=S.IDLE; t,mk=rule_panel();  await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data == "menu:ad":    ud["state"]=S.IDLE; t,mk=ad_panel();    await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data == "menu:admin": ud["state"]=S.IDLE; t,mk=admin_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data == "cb:status":
        m=get_mappings(); active=sum(1 for a in cfg["ads"] if a.get("enabled",True))
        await q.edit_message_text(f"✅ *运行中*\n\n📡 映射:{len(m)} 🤖 已知:{len(cfg['known_channels'])}\n🔤 规则:{len(cfg['replace_rules'])} 📢 广告:{active}/{len(cfg['ads'])}\n📨 今日转发:{_daily['forwarded']} ❌ 失败:{_daily['errors']}",
                                  parse_mode="Markdown", reply_markup=kb([btn("◀ 返回","menu:main")]))
    elif data == "cb:close":   ud["state"]=S.IDLE; await q.edit_message_text("✅ 面板已关闭。/settings 重新打开")
    elif data == "cb:cancel":  ud["state"]=S.IDLE; await q.edit_message_text(main_menu_text(),parse_mode="Markdown",reply_markup=MAIN_MENU_KB)

    # ── 频道操作 ──────────────────────────────────────────────────────────────
    elif data == "ch:add":
        ud["state"] = S.CH_SRC
        known = known_ch_list()
        rows = [[btn(f"📡 {c['title']}", f"ch:pick_src:{c['id']}")] for c in known] + [[btn("✖ 取消", "menu:ch")]]
        await q.edit_message_text("📡 *添加映射 1/2*\n\n选择源频道或发送 ID：", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    elif data.startswith("ch:pick_src:"):
        src = data[12:]; ud["state"]=S.CH_TGT; ud.setdefault("draft",{})["src"]=src
        others = [c for c in known_ch_list() if str(c["id"])!=src]
        rows = [[btn(f"📡 {c['title']}", f"ch:pick_tgt:{c['id']}")] for c in others] + [[btn("✖ 取消","menu:ch")]]
        await q.edit_message_text(f"📡 *添加映射 2/2*\n\n源: `{ch_label(int(src))}`\n\n选择目标或发送ID：", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    elif data.startswith("ch:pick_tgt:"):
        tgt=data[12:]; src=ud.get("draft",{}).get("src","")
        if src:
            cfg["channel_mappings"][src]=tgt
            for c,r in [(src,"src"),(tgt,"tgt")]: cfg["known_channels"].setdefault(c,{})["role"]=r
            save_config(); ud["state"]=S.IDLE; logger.info(f"添加映射: {src}→{tgt}")
        t,mk=ch_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data.startswith("ch:del:"):
        cfg["channel_mappings"].pop(data[7:],None); save_config()
        t,mk=ch_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)

    # ── 已知频道 ──────────────────────────────────────────────────────────────
    elif data.startswith("known:detail:"): t,mk=known_detail(data[13:]); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data.startswith("known:set_src:"):
        cid=data[14:]; cfg["known_channels"].setdefault(cid,{})["role"]="src"; save_config()
        others=[c for c in known_ch_list() if str(c["id"])!=cid]
        rows=[[btn(f"📥 {c['title']}",f"ch:pick_tgt_from:{cid}:{c['id']}")] for c in others]+[[btn("⏭ 稍后配置","menu:known")]]
        await q.edit_message_text(f"📤 已设为源频道\n\n立即选择目标频道完成映射？",reply_markup=InlineKeyboardMarkup(rows))
    elif data.startswith("ch:pick_tgt_from:"):
        _,_,src,tgt=data.split(":",3)
        cfg["channel_mappings"][src]=tgt
        for c,r in [(src,"src"),(tgt,"tgt")]: cfg["known_channels"].setdefault(c,{})["role"]=r
        save_config()
        await q.edit_message_text(f"✅ `{ch_label(int(src))}` → `{ch_label(int(tgt))}`",parse_mode="Markdown",
                                  reply_markup=kb([btn("📡 频道映射","menu:ch"),btn("⚙️ 主菜单","menu:main")]))
    elif data.startswith("known:set_tgt:"):
        cid=data[14:]; cfg["known_channels"].setdefault(cid,{})["role"]="tgt"; save_config()
        t,mk=known_detail(cid); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data.startswith("known:del:"):
        cfg["known_channels"].pop(data[10:],None); save_config()
        t,mk=known_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)

    # ── 替换规则 ──────────────────────────────────────────────────────────────
    elif data == "rule:add":
        ud["state"]=S.RULE_OLD
        await q.edit_message_text("🔤 *添加规则 1/2*\n\n发送*原文*：",parse_mode="Markdown",reply_markup=cancel_kb())
    elif data.startswith("rule:del:"):
        idx=int(data[9:]); keys=list(cfg["replace_rules"].keys())
        if 0<=idx<len(keys): del cfg["replace_rules"][keys[idx]]; save_config()
        t,mk=rule_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)

    # ── 广告 ──────────────────────────────────────────────────────────────────
    elif data == "ad:add":
        ud["state"]=S.AD_TEXT; ud["draft"]={"ad_id":str(uuid.uuid4())[:8]}
        await q.edit_message_text("📢 *新建广告 1/4*\n\n发送*广告正文*：",parse_mode="Markdown",reply_markup=cancel_kb())
    elif data.startswith("ad:detail:"): t,mk=ad_detail(data[10:]); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data.startswith("ad:toggle:"):
        ad_id=data[10:]; ad=next((a for a in cfg["ads"] if a["id"]==ad_id),None)
        if ad: ad["enabled"]=not ad.get("enabled",True); save_config(); reschedule_ad(ctx.application,ad)
        t,mk=ad_detail(ad_id); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data.startswith("ad:del:"):
        ad_id=data[7:]; remove_ad_job(ctx.application,ad_id)
        cfg["ads"]=[a for a in cfg["ads"] if a["id"]!=ad_id]; save_config()
        t,mk=ad_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)
    elif data.startswith("ad:skip_buttons:"): await _save_ad(ctx.application,q,ud,data[16:],[])
    elif data.startswith("ad:confirm_buttons:"): await _save_ad(ctx.application,q,ud,data[19:],ud.get("draft",{}).get("buttons_pending",[]))
    elif data.startswith("ad:pick_ch:"):
        val=data[11:]; channels=list(get_mappings().values()) if val=="all" else [int(val)]
        ad_id=ud.get("draft",{}).get("ad_id",str(uuid.uuid4())[:8])
        ud.setdefault("draft",{}).update({"ad_channels":channels,"ad_id":ad_id}); ud["state"]=S.AD_BUTTONS
        await q.edit_message_text("📢 *第4/4步*\n\n发送内联按钮定义（可选）：\n`文字::URL`\n`A::URL | B::URL`（同行多个）",
                                  parse_mode="Markdown",reply_markup=kb([btn("⏭ 跳过",f"ad:skip_buttons:{ad_id}")],[btn("✖ 取消","cb:cancel")]))

    # ── 管理员 ────────────────────────────────────────────────────────────────
    elif data == "admin:add":
        ud["state"]=S.ADMIN_ID
        await q.edit_message_text("👮 *添加管理员*\n\n发送用户数字ID：",parse_mode="Markdown",reply_markup=cancel_kb())
    elif data.startswith("admin:del:"):
        uid_del=int(data[10:])
        if uid_del in cfg["admins"]: cfg["admins"].remove(uid_del); save_config()
        t,mk=admin_panel(); await q.edit_message_text(t,parse_mode="Markdown",reply_markup=mk)

# ━━━ 私聊输入路由 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def handle_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    ud = ctx.user_data; state = ud.get("state", S.IDLE); text = update.message.text.strip(); msg = update.message

    def back(*bs): return kb(*[[btn(l,d)] for l,d in bs])

    # ── 向导 ─────────────────────────────────────────────────────────────────
    if state == S.WIZ_ADMIN:
        try: nid=int(text)
        except ValueError: await msg.reply_text("❌ 请输入纯数字ID"); return
        if nid not in cfg["admins"]: cfg["admins"].append(nid); save_config()
        await wiz_source(msg, ud)
    elif state == S.WIZ_SRC:
        if not _is_id(text): await msg.reply_text("❌ 格式错误（如 -1001234567890）"); return
        if text not in cfg["known_channels"]:
            try:
                chat = await ctx.bot.get_chat(int(text))
                cfg["known_channels"][text]={"title":chat.title or text,"role":"src"}
            except: cfg["known_channels"][text]={"title":text,"role":"src"}
            save_config()
        await wiz_target(msg, ud, text)
    elif state == S.WIZ_TGT:
        if not _is_id(text): await msg.reply_text("❌ 格式错误（如 -1001234567890）"); return
        if text not in cfg["known_channels"]:
            try:
                chat = await ctx.bot.get_chat(int(text))
                cfg["known_channels"][text]={"title":chat.title or text,"role":"tgt"}
            except: cfg["known_channels"][text]={"title":text,"role":"tgt"}
            save_config()
        src = ud.get("draft",{}).get("wiz_src","")
        if src: await wiz_confirm(msg, ud, src, text)

    # ── 频道 ──────────────────────────────────────────────────────────────────
    elif state == S.CH_SRC:
        if not _is_id(text): await msg.reply_text("❌ 格式错误"); return
        ud["state"]=S.CH_TGT; ud.setdefault("draft",{})["src"]=text
        others=[c for c in known_ch_list() if str(c["id"])!=text]
        rows=[[btn(f"📡 {c['title']}",f"ch:pick_tgt:{c['id']}")] for c in others]+[[btn("✖ 取消","menu:ch")]]
        await msg.reply_text(f"📡 *2/2*\n源: `{text}`\n\n选择目标或发送ID：",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    elif state == S.CH_TGT:
        if not _is_id(text): await msg.reply_text("❌ 格式错误"); return
        src=ud.get("draft",{}).get("src","")
        if src:
            cfg["channel_mappings"][src]=text
            for c,r in [(src,"src"),(text,"tgt")]: cfg["known_channels"].setdefault(c,{})["role"]=r
            save_config(); ud["state"]=S.IDLE
            await msg.reply_text(f"✅ `{ch_label(int(src))}` → `{text}`",parse_mode="Markdown",
                                 reply_markup=back(("📡 频道映射","menu:ch"),("⚙️ 主菜单","menu:main")))

    # ── 替换规则 ──────────────────────────────────────────────────────────────
    elif state == S.RULE_OLD:
        ud["state"]=S.RULE_NEW; ud.setdefault("draft",{})["rule_old"]=text
        await msg.reply_text(f"🔤 *2/2*\n原文: `{text}`\n\n发送替换文（`-` 表示删除该词）：",
                             parse_mode="Markdown",reply_markup=cancel_kb())
    elif state == S.RULE_NEW:
        old=ud.get("draft",{}).get("rule_old",""); new="" if text=="-" else text
        cfg["replace_rules"][old]=new; save_config(); ud["state"]=S.IDLE
        await msg.reply_text(f"✅ `{old}` → `{new or '[删]'}`",parse_mode="Markdown",
                             reply_markup=back(("🔤 规则","menu:rule"),("⚙️ 主菜单","menu:main")))

    # ── 管理员 ────────────────────────────────────────────────────────────────
    elif state == S.ADMIN_ID:
        try: nid=int(text)
        except ValueError: await msg.reply_text("❌ 请输入纯数字ID"); return
        result="ℹ️ 已是管理员"
        if nid not in cfg["admins"]: cfg["admins"].append(nid); save_config(); result=f"✅ 已添加: `{nid}`"
        ud["state"]=S.IDLE
        await msg.reply_text(result,parse_mode="Markdown",reply_markup=back(("👮 管理员","menu:admin"),("⚙️ 主菜单","menu:main")))

    # ── 广告 ──────────────────────────────────────────────────────────────────
    elif state == S.AD_TEXT:
        ud.setdefault("draft",{})["ad_text"]=text; ud["state"]=S.AD_INTERVAL
        await msg.reply_text("📢 *2/4*\n\n发送*推送间隔*（分钟）：\n`60`=每小时 `1440`=每天",
                             parse_mode="Markdown",reply_markup=cancel_kb())
    elif state == S.AD_INTERVAL:
        try: mins=int(text); assert mins>=1
        except: await msg.reply_text("❌ 请输入正整数"); return
        ud["draft"]["ad_interval"]=mins; ud["state"]=S.AD_CHANNELS
        tgts=[c for c in known_ch_list("tgt")]+[c for c in known_ch_list(None)]
        rows=[[btn(f"📥 {c['title']}",f"ad:pick_ch:{c['id']}")] for c in tgts[:6]]
        rows.append([btn("📡 所有目标频道","ad:pick_ch:all")]); rows.append([btn("✖ 取消","cb:cancel")])
        await msg.reply_text("📢 *3/4*\n\n选择*目标频道*或发送ID（空格分隔）：",
                             parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    elif state == S.AD_CHANNELS:
        if text.lower()=="all": channels=list(get_mappings().values())
        else:
            try: channels=[int(x) for x in text.split() if x]
            except: await msg.reply_text("❌ 格式错误"); return
        if not channels: await msg.reply_text("⚠️ 没有目标频道"); return
        ad_id=ud.get("draft",{}).get("ad_id",str(uuid.uuid4())[:8])
        ud["draft"].update({"ad_channels":channels,"ad_id":ad_id}); ud["state"]=S.AD_BUTTONS
        await msg.reply_text("📢 *4/4（可选）*\n\n发送内联按钮：\n`文字::URL`\n`A::URL | B::URL`（同行多个）",
                             parse_mode="Markdown",reply_markup=kb([btn("⏭ 跳过",f"ad:skip_buttons:{ad_id}")],[btn("✖ 取消","cb:cancel")]))
    elif state == S.AD_BUTTONS:
        buttons=parse_buttons(text)
        if not buttons: await msg.reply_text("❌ 格式错误，或点跳过"); return
        ad_id=ud.get("draft",{}).get("ad_id",str(uuid.uuid4())[:8])
        ud["draft"]["buttons_pending"]=buttons
        await msg.reply_text(f"👇 *预览*\n\n{ud['draft'].get('ad_text','')}",parse_mode="Markdown",reply_markup=build_markup(buttons))
        await msg.reply_text("确认创建？",reply_markup=kb([btn("✅ 确认",f"ad:confirm_buttons:{ad_id}")],[btn("✖ 取消","cb:cancel")]))

    # ── Google 输入 ───────────────────────────────────────────────────────────
    elif state == S.G_FOLDER:
        cfg["google"]["drive_folder_id"]=text; save_config(); ud["state"]=S.IDLE
        await msg.reply_text(f"✅ Drive文件夹已设置",reply_markup=back(("🔗 Google集成","menu:google")))
    elif state == S.G_EMAIL:
        if "@" not in text: await msg.reply_text("❌ 请输入有效邮箱"); return
        cfg["google"]["notify_email"]=text; save_config(); ud["state"]=S.IDLE
        await msg.reply_text(f"✅ 通知邮箱: `{text}`",parse_mode="Markdown",reply_markup=back(("🔗 Google集成","menu:google")))
    elif state == S.G_HOUR:
        try: h=int(text); assert 0<=h<=23
        except: await msg.reply_text("❌ 请输入 0-23"); return
        cfg["google"]["report_hour"]=h; save_config(); ud["state"]=S.IDLE
        schedule_google_jobs(ctx.application)
        await msg.reply_text(f"✅ 报告时间: {h:02d}:00",reply_markup=back(("🔗 Google集成","menu:google")))

# ━━━ 广告任务 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _save_ad(app, q, ud, ad_id, buttons):
    draft=ud.get("draft",{})
    ad={"id":ad_id,"text":draft.get("ad_text",""),"interval_minutes":draft.get("ad_interval",60),
        "channels":draft.get("ad_channels",[]),"buttons":buttons,"enabled":True}
    cfg["ads"].append(ad); save_config(); ud["state"]=S.IDLE; ud.pop("draft",None)
    reschedule_ad(app, ad)
    ch_str=", ".join(ch_label(c) for c in ad["channels"])
    await q.edit_message_text(
        f"✅ *广告已创建*\n\n⏱{ad['interval_minutes']}min 📡{ch_str}\n🔘{sum(len(r) for r in buttons)}个按钮\n\n{ad['text'][:60]}",
        parse_mode="Markdown",reply_markup=kb([btn("📢 广告管理","menu:ad"),btn("⚙️ 主菜单","menu:main")]))

def remove_ad_job(app, ad_id):
    for job in app.job_queue.get_jobs_by_name(f"ad_{ad_id}"): job.schedule_removal()

def reschedule_ad(app, ad):
    remove_ad_job(app, ad["id"])
    if not ad.get("enabled",True): return
    app.job_queue.run_repeating(_send_ad,interval=ad["interval_minutes"]*60,
                                first=ad["interval_minutes"]*60,name=f"ad_{ad['id']}",data=ad["id"])

async def _send_ad(ctx: ContextTypes.DEFAULT_TYPE):
    ad=next((a for a in cfg["ads"] if a["id"]==ctx.job.data),None)
    if not ad or not ad.get("enabled",True): return
    markup=build_markup(ad.get("buttons",[])); channels=ad.get("channels") or list(get_mappings().values())
    for ch in channels:
        try: await ctx.bot.send_message(ch,ad["text"],reply_markup=markup); _inc_fwd(); logger.info(f"广告推送:{ad['id']}→{ch}")
        except TelegramError as e: logger.error(f"广告推送失败:{e}"); _inc_err()

# ━━━ Google 定时任务 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _do_backup(ctx: ContextTypes.DEFAULT_TYPE):
    if not _drive_ok(): return
    gcfg=cfg["google"]; folder=gcfg.get("drive_folder_id") or None
    now=datetime.now().strftime("%Y%m%d_%H%M")
    fid=drive_upload(json.dumps(cfg,ensure_ascii=False,indent=2),f"tg_config_{now}.json",folder)
    if fid: logger.info(f"Drive 备份: {fid}")

async def _do_report(ctx: ContextTypes.DEFAULT_TYPE):
    gcfg=cfg["google"]; to=gcfg.get("notify_email","")
    if not to or not _gmail_ok(): return
    _reset_daily()
    m=get_mappings()
    body=(f"TG频道同步机器人 运行报告\n{'='*35}\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
          f"📡 频道映射: {len(m)}\n📨 今日转发: {_daily['forwarded']}\n"
          f"📢 广告: {sum(1 for a in cfg['ads'] if a.get('enabled',True))}/{len(cfg['ads'])}\n"
          f"❌ 失败: {_daily['errors']}")
    gmail_send(to, f"[TG机器人] 运行报告 {_today()}", body)

async def job_daily(ctx: ContextTypes.DEFAULT_TYPE):
    await _do_report(ctx)
    if cfg["google"].get("drive_backup"): await _do_backup(ctx)

async def job_backup(ctx: ContextTypes.DEFAULT_TYPE):
    if cfg["google"].get("drive_backup"): await _do_backup(ctx)

def schedule_google_jobs(app: Application):
    from datetime import time as dtime
    for name in ("daily_report","drive_backup"):
        for job in app.job_queue.get_jobs_by_name(name): job.schedule_removal()
    gcfg=cfg["google"]; hour=gcfg.get("report_hour",8)
    if gcfg.get("daily_report") or gcfg.get("drive_backup"):
        app.job_queue.run_daily(job_daily,time=dtime(hour=hour,minute=0),name="daily_report")
    if gcfg.get("drive_backup"):
        app.job_queue.run_repeating(job_backup,interval=6*3600,first=300,name="drive_backup")

# ━━━ 消息转发 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _forward(ctx, msg, target):
    hr=bool(cfg["replace_rules"]); cap=apply_replace(msg.caption)
    ent=msg.entities if not hr else None; cent=msg.caption_entities if not hr else None
    if   msg.text:       await ctx.bot.send_message(target,apply_replace(msg.text),entities=ent)
    elif msg.photo:      await ctx.bot.send_photo(target,msg.photo[-1].file_id,caption=cap,caption_entities=cent)
    elif msg.video:      await ctx.bot.send_video(target,msg.video.file_id,caption=cap,caption_entities=cent)
    elif msg.audio:      await ctx.bot.send_audio(target,msg.audio.file_id,caption=cap)
    elif msg.document:   await ctx.bot.send_document(target,msg.document.file_id,caption=cap)
    elif msg.animation:  await ctx.bot.send_animation(target,msg.animation.file_id,caption=cap)
    elif msg.sticker:    await ctx.bot.send_sticker(target,msg.sticker.file_id)
    elif msg.voice:      await ctx.bot.send_voice(target,msg.voice.file_id,caption=cap)
    elif msg.video_note: await ctx.bot.send_video_note(target,msg.video_note.file_id)
    elif msg.poll:
        p=msg.poll
        await ctx.bot.send_poll(target,question=apply_replace(p.question),
                                options=[apply_replace(o.text) for o in p.options],
                                is_anonymous=p.is_anonymous,type=p.type,
                                allows_multiple_answers=p.allows_multiple_answers)
    else: logger.warning(f"不支持类型 id={msg.message_id}")

async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg=update.channel_post
    if not msg: return
    m=get_mappings()
    if msg.chat_id not in m: return
    try:
        await _forward(ctx,msg,m[msg.chat_id]); _inc_fwd()
        logger.info(f"[转发] {msg.chat_id}→{m[msg.chat_id]} id={msg.message_id}")
        # 错误告警重置
    except TelegramError as e:
        _inc_err(); logger.error(f"转发失败: {e}")
        gcfg=cfg["google"]
        if gcfg.get("error_alert") and gcfg.get("notify_email") and _gmail_ok():
            gmail_send(gcfg["notify_email"],"[TG机器人] 转发失败",f"频道:{msg.chat_id}\n错误:{e}")

# ━━━ 命令 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid):
        if not cfg["admins"] and not ENV_ADMIN_IDS:
            cfg["admins"].append(uid); save_config()
        else:
            await update.message.reply_text("👋 此机器人需要管理员权限。"); return
    if not cfg["setup_complete"]: await wiz_admin(update.message, ctx.user_data)
    else: await update.message.reply_text("👋 *Telegram 频道同步机器人 v5*\n\n/settings 打开设置面板",parse_mode="Markdown")

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("⛔ 无权限"); return
    if not cfg["setup_complete"]: await wiz_admin(update.message,ctx.user_data); return
    ctx.user_data["state"]=S.IDLE
    await update.message.reply_text(main_menu_text(),parse_mode="Markdown",reply_markup=MAIN_MENU_KB)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m=get_mappings(); _reset_daily()
    await update.message.reply_text(
        f"✅ *运行中*\n\n📡 映射:{len(m)} 🤖 已知:{len(cfg['known_channels'])}\n"
        f"🔤 规则:{len(cfg['replace_rules'])} 📢 广告:{sum(1 for a in cfg['ads'] if a.get('enabled',True))}/{len(cfg['ads'])}\n"
        f"☁️ Drive:{'✅' if _drive_ok() else '❌'} 📧 Gmail:{'✅' if _gmail_ok() else '❌'}\n"
        f"📨 今日转发:{_daily['forwarded']} ❌ 失败:{_daily['errors']}",
        parse_mode="Markdown")

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"[错误] {ctx.error}",exc_info=ctx.error)

# ━━━ 启动 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _post_init(app: Application):
    for ad in cfg.get("ads",[]):
        if ad.get("enabled",True): reschedule_ad(app,ad)
    schedule_google_jobs(app)
    logger.info(f"🚀 启动完成 | 映射:{len(get_mappings())} 已知:{len(cfg.get('known_channels',{}))} 广告:{len(cfg.get('ads',[]))} Drive:{'✅' if _drive_ok() else '❌'} Gmail:{'✅' if _gmail_ok() else '❌'}")

def main():
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("settings",cmd_settings))
    app.add_handler(CommandHandler("status",cmd_status))
    app.add_handler(ChatMemberHandler(on_my_chat_member,ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,handle_private_text))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST,handle_channel_post))
    app.add_error_handler(error_handler)
    app.post_init=_post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
