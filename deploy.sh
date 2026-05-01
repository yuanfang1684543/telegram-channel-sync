#!/bin/bash

# Telegram 频道同步机器人 - Railway 快速部署脚本
# 使用方式: bash deploy.sh

set -e

echo "🚀 Telegram 频道同步机器人 - Railway 部署助手"
echo "=================================================="
echo ""

# 检查是否安装了 Railway CLI
if ! command -v railway &> /dev/null; then
    echo "❌ Railway CLI 未安装"
    echo ""
    echo "请先安装 Railway CLI:"
    echo "访问: https://docs.railway.app/guides/cli"
    echo ""
    exit 1
fi

echo "✅ Railway CLI 已安装"
echo ""

# 登录 Railway
echo "📝 登录到 Railway..."
railway login

echo ""
echo "🔑 配置环境变量"
echo "=================="
echo ""

# 获取用户输入
read -p "请输入 Bot Token: " BOT_TOKEN
read -p "请输入源频道 ID (如: -1001234567890): " SOURCE_CHANNEL_ID
read -p "请输入目标频道 ID (如: -1001234567890): " TARGET_CHANNEL_ID

echo ""
echo "📋 确认配置:"
echo "  • Bot Token: ${BOT_TOKEN:0:10}***"
echo "  • 源频道 ID: $SOURCE_CHANNEL_ID"
echo "  • 目标频道 ID: $TARGET_CHANNEL_ID"
echo ""

read -p "配置正确吗? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ 已取消"
    exit 1
fi

echo ""
echo "⏳ 初始化 Railway 项目..."

# 初始化项目（如果还没有）
if [ ! -f "railway.json" ]; then
    railway init
fi

echo ""
echo "🔐 设置环境变量..."

# 设置环境变量
railway variables set BOT_TOKEN "$BOT_TOKEN"
railway variables set SOURCE_CHANNEL_ID "$SOURCE_CHANNEL_ID"
railway variables set TARGET_CHANNEL_ID "$TARGET_CHANNEL_ID"

echo ""
echo "✅ 环境变量已设置"
echo ""

echo "📦 开始部署..."
echo ""

# 部署
railway up

echo ""
echo "✅ 部署完成！"
echo ""
echo "📊 后续步骤:"
echo "  1. 将机器人添加到源频道（作为管理员）"
echo "  2. 在源频道发送消息测试"
echo "  3. 在 Railway Dashboard 查看实时日志"
echo ""
echo "🔗 Railway Dashboard: https://railway.app/dashboard"
echo ""
