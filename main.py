from __future__ import annotations
#!/usr/bin/env python3
"""
Telegram 频道同步消息机器人 v2
- 动态添加/删除频道映射
- 自定义文字替换规则
- 管理员白名单
- 持久化配置 config.json
"""

import os
import json
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)
from telegram.error import TelegramError

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── 环境变量 ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 环境变量未设置")

ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")
ENV_ADMIN_IDS = (
    {int(x.strip()) for x in ADMIN_IDS_ENV.split(",") if x.strip()}
    if ADMIN_IDS_ENV else set()
)

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))


# ── 配置管理 ──────────────────────────────────────────────────────────────────
def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    return {"channel_mappings": {}, "replace_rules": {}, "admins": []}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")


config = load_config()

# 从环境变量同步初始频道
_src = os.getenv("SOURCE_CHANNEL_ID")
_tgt = os.getenv("TARGET_CHANNEL_ID")
if _src and _tgt and _src not in config["channel_mappings"]:
    config["channel_mappings"][_src] = _tgt

for _aid in ENV_ADMIN_IDS:
    if _aid not in config["admins"]:
        config["admins"].append(_aid)

save_config(config)


# ── 权限检查 ──────────────────────────────────────────────────────────────────
def is_admin(user_id):
    return user_id in config.get("admins", []) or user_id in ENV_ADMIN_IDS


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if not uid or not is_admin(uid):
            await update.message.reply_text("⛔ 你没有权限使用此命令。")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


# ── 工具函数 ──────────────────────────────────────────────────────────────────
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


# ── 命令处理器 ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lines = [
        "👋 Telegram 频道同步机器人 v2\n",
        "📌 通用命令",
        "/status         — 运行状态",
        "/listchannels   — 查看频道映射",
        "/listrules      — 查看替换规则",
    ]
    if is_admin(uid):
        lines += [
            "\n🔧 频道管理（管理员）",
            "/addchannel 源ID 目标ID  — 添加映射",
            "/removechannel 源ID      — 删除映射",
            "\n🔤 替换规则（管理员）",
            "/addrule 原文 >> 替换文  — 添加规则",
            "/removerule 原文         — 删除规则",
            "\n👮 权限管理（管理员）",
            "/addadmin 用户ID         — 添加管理员",
            "/removeadmin 用户ID      — 删除管理员",
        ]
    await update.message.reply_text("\n".join(lines))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = get_mappings()
    r = config.get("replace_rules", {})
    a = config.get("admins", [])
    await update.message.reply_text(
        f"✅ 机器人运行中\n\n"
        f"📡 频道映射: {len(m)} 条\n"
        f"🔤 替换规则: {len(r)} 条\n"
        f"👮 管理员数: {len(a)} 人"
    )


# ── 频道管理 ──────────────────────────────────────────────────────────────────

async def cmd_listchannels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = get_mappings()
    if not m:
        await update.message.reply_text("📭 暂无频道映射。\n用 /addchannel 源ID 目标ID 添加。")
        return
    lines = ["📡 频道映射列表:\n"]
    for i, (s, t) in enumerate(m.items(), 1):
        lines.append(f"{i}. {s} → {t}")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text(
            "❌ 用法: /addchannel 源频道ID 目标频道ID\n"
            "例: /addchannel -1001111111111 -1002222222222"
        )
        return
    src, tgt = args[0], args[1]
    try:
        int(src); int(tgt)
    except ValueError:
        await update.message.reply_text("❌ 频道 ID 必须是数字（如 -1001234567890）。")
        return
    config["channel_mappings"][src] = tgt
    save_config(config)
    await update.message.reply_text(f"✅ 已添加: {src} → {tgt}")
    logger.info(f"添加频道映射: {src} -> {tgt}")


@admin_only
async def cmd_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ 用法: /removechannel 源频道ID")
        return
    src = args[0]
    if src in config["channel_mappings"]:
        tgt = config["channel_mappings"].pop(src)
        save_config(config)
        await update.message.reply_text(f"🗑 已删除: {src} → {tgt}")
    else:
        await update.message.reply_text(f"⚠️ 未找到源频道 {src} 的映射。")


# ── 替换规则管理 ──────────────────────────────────────────────────────────────

async def cmd_listrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = config.get("replace_rules", {})
    if not rules:
        await update.message.reply_text(
            "📭 暂无替换规则。\n用 /addrule 原文 >> 替换文 添加。"
        )
        return
    lines = ["🔤 文字替换规则:\n"]
    for i, (old, new) in enumerate(rules.items(), 1):
        new_display = new if new else "[删除]"
        lines.append(f"{i}. 「{old}」→「{new_display}」")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_addrule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.split(None, 1)
    if len(raw) < 2 or ">>" not in raw[1]:
        await update.message.reply_text(
            "❌ 用法: /addrule 原文 >> 替换文\n\n"
            "示例:\n"
            "  /addrule 旧名字 >> 新名字\n"
            "  /addrule 广告词 >>          （右侧留空 = 删除该词）\n"
            "  /addrule http://old.com >> http://new.com"
        )
        return
    parts = raw[1].split(">>", 1)
    old_text = parts[0].strip()
    new_text = parts[1].strip()
    if not old_text:
        await update.message.reply_text("❌ 原文不能为空。")
        return
    config["replace_rules"][old_text] = new_text
    save_config(config)
    new_display = new_text if new_text else "[删除该词]"
    await update.message.reply_text(f"✅ 已添加规则:\n「{old_text}」→「{new_display}」")
    logger.info(f"添加替换规则: '{old_text}' -> '{new_text}'")


@admin_only
async def cmd_removerule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.split(None, 1)
    if len(raw) < 2:
        await update.message.reply_text("❌ 用法: /removerule 原文")
        return
    old_text = raw[1].strip()
    if old_text in config["replace_rules"]:
        new_text = config["replace_rules"].pop(old_text)
        save_config(config)
        new_display = new_text if new_text else "[删除该词]"
        await update.message.reply_text(f"🗑 已删除规则:\n「{old_text}」→「{new_display}」")
    else:
        await update.message.reply_text(f"⚠️ 未找到规则「{old_text}」。")


# ── 管理员管理 ────────────────────────────────────────────────────────────────

@admin_only
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ 用法: /addadmin 用户ID")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ 用户 ID 必须是整数。")
        return
    if uid not in config["admins"]:
        config["admins"].append(uid)
        save_config(config)
        await update.message.reply_text(f"✅ 已添加管理员: {uid}")
    else:
        await update.message.reply_text(f"ℹ️ {uid} 已经是管理员。")


@admin_only
async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ 用法: /removeadmin 用户ID")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ 用户 ID 必须是整数。")
        return
    if uid in config["admins"]:
        config["admins"].remove(uid)
        save_config(config)
        await update.message.reply_text(f"🗑 已移除管理员: {uid}")
    else:
        await update.message.reply_text(f"⚠️ {uid} 不是管理员。")


# ── 消息转发核心 ──────────────────────────────────────────────────────────────

async def forward_message(context: ContextTypes.DEFAULT_TYPE, message, target_id):
    has_rules = bool(config.get("replace_rules"))

    if message.text:
        await context.bot.send_message(
            chat_id=target_id,
            text=apply_replace(message.text),
            entities=message.entities if not has_rules else None,
        )
    elif message.photo:
        await context.bot.send_photo(
            chat_id=target_id,
            photo=message.photo[-1].file_id,
            caption=apply_replace(message.caption),
            caption_entities=message.caption_entities if not has_rules else None,
        )
    elif message.video:
        await context.bot.send_video(
            chat_id=target_id,
            video=message.video.file_id,
            caption=apply_replace(message.caption),
            caption_entities=message.caption_entities if not has_rules else None,
        )
    elif message.audio:
        await context.bot.send_audio(
            chat_id=target_id,
            audio=message.audio.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.document:
        await context.bot.send_document(
            chat_id=target_id,
            document=message.document.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.animation:
        await context.bot.send_animation(
            chat_id=target_id,
            animation=message.animation.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.sticker:
        await context.bot.send_sticker(
            chat_id=target_id,
            sticker=message.sticker.file_id,
        )
    elif message.voice:
        await context.bot.send_voice(
            chat_id=target_id,
            voice=message.voice.file_id,
            caption=apply_replace(message.caption),
        )
    elif message.video_note:
        await context.bot.send_video_note(
            chat_id=target_id,
            video_note=message.video_note.file_id,
        )
    elif message.poll:
        poll = message.poll
        await context.bot.send_poll(
            chat_id=target_id,
            question=apply_replace(poll.question),
            options=[apply_replace(o.text) for o in poll.options],
            is_anonymous=poll.is_anonymous,
            type=poll.type,
            allows_multiple_answers=poll.allows_multiple_answers,
        )
    else:
        logger.warning(f"不支持的消息类型，跳过 msg_id={message.message_id}")


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        return
    mappings = get_mappings()
    source_id = message.chat_id
    if source_id not in mappings:
        return
    target_id = mappings[source_id]
    logger.info(f"[转发] {source_id} → {target_id} | msg_id={message.message_id}")
    try:
        await forward_message(context, message, target_id)
    except TelegramError as e:
        logger.error(f"[错误] 转发失败: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"[全局错误] {context.error}")


# ── 启动 ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("listchannels", cmd_listchannels))
    app.add_handler(CommandHandler("listrules", cmd_listrules))
    app.add_handler(CommandHandler("addchannel", cmd_addchannel))
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))
    app.add_handler(CommandHandler("addrule", cmd_addrule))
    app.add_handler(CommandHandler("removerule", cmd_removerule))
    app.add_handler(CommandHandler("addadmin", cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_error_handler(error_handler)

    logger.info(
        f"机器人启动 | 频道映射: {len(get_mappings())} 条 "
        f"| 替换规则: {len(config.get('replace_rules', {}))} 条"
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
