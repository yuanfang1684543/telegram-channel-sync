@echo off
REM Telegram 频道同步机器人 - Railway 快速部署脚本 (Windows 版)
REM 使用方式: deploy.bat

setlocal enabledelayedexpansion

echo.
echo 🚀 Telegram 频道同步机器人 - Railway 部署助手
echo ==================================================
echo.

REM 检查是否安装了 Railway CLI
where railway >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Railway CLI 未安装
    echo.
    echo 请先安装 Railway CLI:
    echo 访问: https://docs.railway.app/guides/cli
    echo.
    pause
    exit /b 1
)

echo ✅ Railway CLI 已安装
echo.

REM 登录 Railway
echo 📝 登录到 Railway...
call railway login

if %ERRORLEVEL% NEQ 0 (
    echo ❌ 登录失败
    pause
    exit /b 1
)

echo.
echo 🔑 配置环境变量
echo ====================
echo.

REM 获取用户输入
set /p BOT_TOKEN="请输入 Bot Token: "
set /p SOURCE_CHANNEL_ID="请输入源频道 ID (如: -1001234567890): "
set /p TARGET_CHANNEL_ID="请输入目标频道 ID (如: -1001234567890): "

echo.
echo 📋 确认配置:
echo   * Bot Token: %BOT_TOKEN:~0,10%***
echo   * 源频道 ID: %SOURCE_CHANNEL_ID%
echo   * 目标频道 ID: %TARGET_CHANNEL_ID%
echo.

set /p CONFIRM="配置正确吗? (y/n): "

if /i not "%CONFIRM%"=="y" (
    echo ❌ 已取消
    pause
    exit /b 1
)

echo.
echo ⏳ 初始化 Railway 项目...

REM 检查 railway.json 是否存在
if not exist "railway.json" (
    call railway init
    if %ERRORLEVEL% NEQ 0 (
        echo ❌ 初始化失败
        pause
        exit /b 1
    )
)

echo.
echo 🔐 设置环境变量...

REM 设置环境变量
call railway variables set BOT_TOKEN "%BOT_TOKEN%"
if %ERRORLEVEL% NEQ 0 (
    echo ❌ 设置 BOT_TOKEN 失败
    pause
    exit /b 1
)

call railway variables set SOURCE_CHANNEL_ID "%SOURCE_CHANNEL_ID%"
if %ERRORLEVEL% NEQ 0 (
    echo ❌ 设置 SOURCE_CHANNEL_ID 失败
    pause
    exit /b 1
)

call railway variables set TARGET_CHANNEL_ID "%TARGET_CHANNEL_ID%"
if %ERRORLEVEL% NEQ 0 (
    echo ❌ 设置 TARGET_CHANNEL_ID 失败
    pause
    exit /b 1
)

echo.
echo ✅ 环境变量已设置
echo.

echo 📦 开始部署...
echo.

REM 部署
call railway up

if %ERRORLEVEL% NEQ 0 (
    echo ❌ 部署失败
    pause
    exit /b 1
)

echo.
echo ✅ 部署完成！
echo.
echo 📊 后续步骤:
echo   1. 将机器人添加到源频道（作为管理员）
echo   2. 在源频道发送消息测试
echo   3. 在 Railway Dashboard 查看实时日志
echo.
echo 🔗 Railway Dashboard: https://railway.app/dashboard
echo.

pause
