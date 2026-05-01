#!/usr/bin/env python3
"""
Telegram 频道同步消息机器人
支持监听源频道消息并同步到目标频道
"""

import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
    CommandHandler
)
from telegram.error import TelegramError

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 环境变量配置
BOT_TOKEN = os.getenv('BOT_TOKEN')
SOURCE_CHANNEL_ID = os.getenv('SOURCE_CHANNEL_ID')
TARGET_CHANNEL_ID = os.getenv('TARGET_CHANNEL_ID')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 环境变量未设置")
if not SOURCE_CHANNEL_ID:
    raise ValueError("SOURCE_CHANNEL_ID 环境变量未设置")
if not TARGET_CHANNEL_ID:
    raise ValueError("TARGET_CHANNEL_ID 环境变量未设置")

# 转换为整数
SOURCE_CHANNEL_ID = int(SOURCE_CHANNEL_ID)
TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令"""
    await update.message.reply_text(
        "👋 Telegram 频道同步机器人已启动！\n\n"
        "机器人功能：\n"
        "✓ 监听源频道的消息\n"
        "✓ 自动转发到目标频道\n"
        "✓ 支持文本、图片、视频、文件等媒体\n\n"
        "配置信息：\n"
        f"📍 源频道: {SOURCE_CHANNEL_ID}\n"
        f"📍 目标频道: {TARGET_CHANNEL_ID}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /help 命令"""
    help_text = (
        "📖 帮助信息\n\n"
        "可用命令：\n"
        "/start - 启动机器人\n"
        "/help - 显示帮助信息\n"
        "/status - 显示当前状态\n\n"
        "说明：\n"
        "机器人会自动监听源频道的所有消息，"
        "并将其转发到目标频道。\n"
        "支持的内容类型：\n"
        "• 文本消息\n"
        "• 图片\n"
        "• 视频\n"
        "• 音频\n"
        "• 文件\n"
        "• 贴纸等"
    )
    await update.message.reply_text(help_text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /status 命令"""
    status_text = (
        "✅ 机器人运行状态：正常\n\n"
        "配置信息：\n"
        f"📍 源频道 ID: {SOURCE_CHANNEL_ID}\n"
        f"📍 目标频道 ID: {TARGET_CHANNEL_ID}\n"
        "🔄 监听状态：已激活"
    )
    await update.message.reply_text(status_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理来自源频道的所有消息"""
    try:
        # 检查消息是否来自源频道
        if update.channel_post.chat_id != SOURCE_CHANNEL_ID:
            return

        message = update.channel_post
        logger.info(f"收到来自源频道的消息: {message.message_id}")

        # 处理纯文本消息
        if message.text and not message.caption:
            await context.bot.send_message(
                chat_id=TARGET_CHANNEL_ID,
                text=message.text,
                parse_mode='HTML' if message.entities else None
            )

        # 处理带文本的图片
        elif message.photo:
            await context.bot.send_photo(
                chat_id=TARGET_CHANNEL_ID,
                photo=message.photo[-1].file_id,
                caption=message.caption or None,
                parse_mode='HTML' if message.caption_entities else None
            )

        # 处理带文本的视频
        elif message.video:
            await context.bot.send_video(
                chat_id=TARGET_CHANNEL_ID,
                video=message.video.file_id,
                caption=message.caption or None,
                parse_mode='HTML' if message.caption_entities else None
            )

        # 处理音频
        elif message.audio:
            await context.bot.send_audio(
                chat_id=TARGET_CHANNEL_ID,
                audio=message.audio.file_id,
                caption=message.caption or None,
                parse_mode='HTML' if message.caption_entities else None
            )

        # 处理文件
        elif message.document:
            await context.bot.send_document(
                chat_id=TARGET_CHANNEL_ID,
                document=message.document.file_id,
                caption=message.caption or None,
                parse_mode='HTML' if message.caption_entities else None
            )

        # 处理动画
        elif message.animation:
            await context.bot.send_animation(
                chat_id=TARGET_CHANNEL_ID,
                animation=message.animation.file_id,
                caption=message.caption or None,
                parse_mode='HTML' if message.caption_entities else None
            )

        # 处理贴纸
        elif message.sticker:
            await context.bot.send_sticker(
                chat_id=TARGET_CHANNEL_ID,
                sticker=message.sticker.file_id
            )

        logger.info(f"消息已转发到目标频道")

    except TelegramError as e:
        logger.error(f"Telegram 错误: {e}")
    except Exception as e:
        logger.error(f"处理消息时发生错误: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理错误"""
    logger.error(f"发生异常: {context.error}")


def main() -> None:
    """启动机器人"""
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()

    # 添加命令处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))

    # 添加消息处理器 - 监听所有频道消息
    application.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_message))

    # 添加错误处理
    application.add_error_handler(error_handler)

    logger.info("机器人启动中...")
    logger.info(f"源频道: {SOURCE_CHANNEL_ID}")
    logger.info(f"目标频道: {TARGET_CHANNEL_ID}")

    # 启动轮询
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
