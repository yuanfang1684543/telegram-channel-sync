#!/usr/bin/env python3
"""
Telegram 频道同步消息机器人 - 高级版本
支持多频道映射和详细的消息转发日志
"""

import os
import json
import logging
from datetime import datetime
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

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 环境变量未设置")

# 频道映射配置 - 支持多个频道对
# 格式: {源频道_id: 目标频道_id}
CHANNEL_MAPPINGS = {}

# 从环境变量读取频道映射
# 可以通过设置 CHANNEL_MAPPINGS_JSON='{"源id":"目标id","源id":"目标id"}' 来配置
mappings_json = os.getenv('CHANNEL_MAPPINGS_JSON')
if mappings_json:
    try:
        mappings_dict = json.loads(mappings_json)
        CHANNEL_MAPPINGS = {int(k): int(v) for k, v in mappings_dict.items()}
        logger.info(f"已加载频道映射: {CHANNEL_MAPPINGS}")
    except json.JSONDecodeError as e:
        logger.error(f"频道映射 JSON 格式错误: {e}")

# 如果没有通过 JSON 配置，尝试从单独的环境变量读取
if not CHANNEL_MAPPINGS:
    source_id = os.getenv('SOURCE_CHANNEL_ID')
    target_id = os.getenv('TARGET_CHANNEL_ID')
    if source_id and target_id:
        CHANNEL_MAPPINGS[int(source_id)] = int(target_id)
        logger.info(f"已加载单个频道映射: {CHANNEL_MAPPINGS}")

if not CHANNEL_MAPPINGS:
    raise ValueError("未找到频道映射配置，请设置环境变量")


class MessageLogger:
    """消息转发日志管理"""
    
    def __init__(self, log_file='sync_log.json'):
        self.log_file = log_file
        self.load_log()
    
    def load_log(self):
        """加载日志文件"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    self.log_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.log_data = {'messages': []}
        else:
            self.log_data = {'messages': []}
    
    def add_log(self, source_id: int, target_id: int, message_id: int, 
                message_type: str, status: str, error: str = None):
        """添加日志条目"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'source_channel': source_id,
            'target_channel': target_id,
            'message_id': message_id,
            'message_type': message_type,
            'status': status,
            'error': error
        }
        self.log_data['messages'].append(log_entry)
        self.save_log()
    
    def save_log(self):
        """保存日志到文件"""
        try:
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(self.log_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"保存日志失败: {e}")
    
    def get_stats(self):
        """获取统计信息"""
        total = len(self.log_data['messages'])
        success = sum(1 for m in self.log_data['messages'] if m['status'] == 'success')
        failed = sum(1 for m in self.log_data['messages'] if m['status'] == 'failed')
        return {'total': total, 'success': success, 'failed': failed}


# 创建日志管理实例
message_logger = MessageLogger()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令"""
    mappings_info = "\n".join([
        f"📍 {src} → {tgt}" 
        for src, tgt in CHANNEL_MAPPINGS.items()
    ])
    await update.message.reply_text(
        "👋 Telegram 频道同步机器人已启动！\n\n"
        "机器人功能：\n"
        "✓ 监听源频道的消息\n"
        "✓ 自动转发到目标频道\n"
        "✓ 支持多种媒体类型\n"
        "✓ 详细的转发日志\n\n"
        "📍 频道映射关系：\n" + mappings_info
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /help 命令"""
    help_text = (
        "📖 帮助信息\n\n"
        "可用命令：\n"
        "/start - 启动机器人\n"
        "/help - 显示帮助信息\n"
        "/status - 显示当前状态\n"
        "/stats - 显示转发统计\n\n"
        "支持的内容类型：\n"
        "• 文本消息\n"
        "• 图片\n"
        "• 视频\n"
        "• 音频\n"
        "• 文件\n"
        "• 动画（GIF）\n"
        "• 贴纸"
    )
    await update.message.reply_text(help_text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /status 命令"""
    mappings_info = "\n".join([
        f"📍 {src} → {tgt}" 
        for src, tgt in CHANNEL_MAPPINGS.items()
    ])
    status_text = (
        "✅ 机器人运行状态：正常\n\n"
        "📍 频道映射关系：\n" + mappings_info + 
        f"\n\n🔄 监听状态：已激活"
    )
    await update.message.reply_text(status_text)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /stats 命令"""
    stats = message_logger.get_stats()
    stats_text = (
        "📊 转发统计信息\n\n"
        f"📈 总消息数: {stats['total']}\n"
        f"✅ 成功转发: {stats['success']}\n"
        f"❌ 转发失败: {stats['failed']}\n"
        f"📊 成功率: {(stats['success']/stats['total']*100):.1f}%" if stats['total'] > 0 else "📊 成功率: N/A"
    )
    await update.message.reply_text(stats_text)


def get_message_type(message) -> str:
    """获取消息类型"""
    if message.text:
        return "text"
    elif message.photo:
        return "photo"
    elif message.video:
        return "video"
    elif message.audio:
        return "audio"
    elif message.document:
        return "document"
    elif message.animation:
        return "animation"
    elif message.sticker:
        return "sticker"
    else:
        return "unknown"


async def forward_text(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发文本消息"""
    await context.bot.send_message(
        chat_id=target_id,
        text=message.text,
        parse_mode='HTML' if message.entities else None
    )


async def forward_photo(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发图片"""
    await context.bot.send_photo(
        chat_id=target_id,
        photo=message.photo[-1].file_id,
        caption=message.caption or None,
        parse_mode='HTML' if message.caption_entities else None
    )


async def forward_video(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发视频"""
    await context.bot.send_video(
        chat_id=target_id,
        video=message.video.file_id,
        caption=message.caption or None,
        parse_mode='HTML' if message.caption_entities else None
    )


async def forward_audio(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发音频"""
    await context.bot.send_audio(
        chat_id=target_id,
        audio=message.audio.file_id,
        caption=message.caption or None,
        parse_mode='HTML' if message.caption_entities else None
    )


async def forward_document(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发文件"""
    await context.bot.send_document(
        chat_id=target_id,
        document=message.document.file_id,
        caption=message.caption or None,
        parse_mode='HTML' if message.caption_entities else None
    )


async def forward_animation(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发动画"""
    await context.bot.send_animation(
        chat_id=target_id,
        animation=message.animation.file_id,
        caption=message.caption or None,
        parse_mode='HTML' if message.caption_entities else None
    )


async def forward_sticker(context: ContextTypes.DEFAULT_TYPE, message, target_id: int):
    """转发贴纸"""
    await context.bot.send_sticker(
        chat_id=target_id,
        sticker=message.sticker.file_id
    )


# 转发函数映射
FORWARD_FUNCTIONS = {
    'text': forward_text,
    'photo': forward_photo,
    'video': forward_video,
    'audio': forward_audio,
    'document': forward_document,
    'animation': forward_animation,
    'sticker': forward_sticker,
}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理来自源频道的所有消息"""
    try:
        message = update.channel_post
        source_id = message.chat_id
        message_id = message.message_id
        
        # 检查是否是来自配置的源频道
        if source_id not in CHANNEL_MAPPINGS:
            return
        
        target_id = CHANNEL_MAPPINGS[source_id]
        message_type = get_message_type(message)
        
        logger.info(f"收到消息 - 源: {source_id}, ID: {message_id}, 类型: {message_type}")
        
        # 获取对应的转发函数
        forward_func = FORWARD_FUNCTIONS.get(message_type)
        
        if forward_func:
            await forward_func(context, message, target_id)
            logger.info(f"消息已转发 - 目标: {target_id}, ID: {message_id}")
            message_logger.add_log(source_id, target_id, message_id, message_type, 'success')
        else:
            logger.warning(f"不支持的消息类型: {message_type}")
            message_logger.add_log(source_id, target_id, message_id, message_type, 'skipped')
        
    except TelegramError as e:
        logger.error(f"Telegram 错误: {e}")
        message_logger.add_log(source_id, target_id, message_id, message_type, 'failed', str(e))
    except Exception as e:
        logger.error(f"处理消息时发生错误: {e}")
        message_logger.add_log(source_id, target_id, message_id, 'unknown', 'failed', str(e))


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
    application.add_handler(CommandHandler("stats", stats_command))

    # 添加消息处理器 - 监听所有频道消息
    application.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_message))

    # 添加错误处理
    application.add_error_handler(error_handler)

    logger.info("机器人启动中...")
    logger.info(f"频道映射关系: {CHANNEL_MAPPINGS}")
    logger.info(f"共监听 {len(CHANNEL_MAPPINGS)} 个源频道")

    # 启动轮询
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
