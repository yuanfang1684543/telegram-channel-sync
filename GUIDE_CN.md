# 📚 Telegram 频道同步机器人 - 完整使用指南

## 📑 目录

1. [功能概述](#功能概述)
2. [环境配置](#环境配置)
3. [本地运行](#本地运行)
4. [Railway 部署](#railway-部署)
5. [常见问题](#常见问题)
6. [高级配置](#高级配置)
7. [故障排除](#故障排除)

---

## 功能概述

### 核心功能

✅ **自动消息转发** - 实时监听源频道消息并自动转发到目标频道
✅ **多媒体支持** - 支持文本、图片、视频、音频、文件等多种格式
✅ **格式保留** - 保留原始消息的格式、样式和说明文字
✅ **多频道映射** - 支持一对一或多对多的频道转发（高级版本）
✅ **日志记录** - 详细记录所有转发操作（高级版本）
✅ **24/7 运行** - 在 Railway 上持续运行，无需手动干预

### 支持的消息类型

| 类型 | 说明 | 支持 |
|------|------|------|
| 文本 | 纯文本消息 | ✅ |
| 文本+格式 | 粗体、斜体、代码等 | ✅ |
| 图片 | 单张或多张图片 | ✅ |
| 图片+说明 | 带文字说明的图片 | ✅ |
| 视频 | MP4 等视频格式 | ✅ |
| 视频+说明 | 带说明的视频 | ✅ |
| 音频 | MP3 等音频文件 | ✅ |
| 文件 | PDF、Word 等任意文件 | ✅ |
| 动画 | GIF 和其他动画 | ✅ |
| 贴纸 | Telegram 贴纸 | ✅ |

---

## 环境配置

### 前置要求

- Python 3.8+（本地运行）或 Railway 账户（云部署）
- Telegram 机器人 Token
- 源和目标频道的 ID

### 获取必要信息

#### 1️⃣ 创建 Telegram Bot

**步骤：**
1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot` 命令
3. 按照提示输入机器人名称和用户名
4. 复制并保存 Bot Token

**示例 Token：**
```
123456789:ABCDEFGHIjklmnopqrstuvwxyz1234567890
```

#### 2️⃣ 获取频道 ID

**方法 A：使用 Bot Forward（推荐）**

```
1. 在 Telegram 创建或打开一个频道
2. 将你的机器人添加到频道（作为管理员）
3. 在频道中发送任何消息
4. 将该消息转发给 @userinfobot
5. 查看返回的信息，找到 "from_chat_id": -100XXXXXXXXX
6. 该 -100XXXXXXXXX 就是你的频道 ID
```

**方法 B：从频道链接**

如果是公开频道：
- 频道链接：`https://t.me/mychannel123`
- 频道 ID：`@mychannel123` 或数字 ID

如果是私密频道：
- 必须使用方法 A 获取

**示例频道 ID：**
```
-1001234567890  （私密频道）
123456789       （公开频道数字 ID）
```

#### 3️⃣ 添加机器人到频道

**步骤：**
1. 打开频道设置 → 管理员
2. 搜索并添加你的机器人
3. 赋予以下权限：
   - 发布消息
   - 编辑消息
   - 删除消息
   - （可选）上传媒体

---

## 本地运行

### 快速开始

#### Linux / macOS

```bash
# 1. 克隆或下载项目
git clone <repo-url>
cd telegram-channel-sync

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 设置环境变量
export BOT_TOKEN="你的_bot_token"
export SOURCE_CHANNEL_ID="-1001234567890"
export TARGET_CHANNEL_ID="-1009876543210"

# 5. 运行机器人
python main.py
```

#### Windows

```cmd
# 1. 克隆或下载项目
git clone <repo-url>
cd telegram-channel-sync

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 设置环境变量
set BOT_TOKEN=你的_bot_token
set SOURCE_CHANNEL_ID=-1001234567890
set TARGET_CHANNEL_ID=-1009876543210

# 5. 运行机器人
python main.py
```

### 测试机器人

```bash
# 1. 在源频道发送测试消息
# 2. 检查目标频道是否收到消息
# 3. 查看终端日志输出

# 预期输出：
# INFO - 收到来自源频道的消息: 12345
# INFO - 消息已转发到目标频道
```

### 常见错误

**错误：TelegramError: Bot can't initiate conversation**
```
✓ 确保频道存在且机器人已被添加
✓ 检查频道 ID 是否正确
```

**错误：Unauthorized**
```
✓ 检查 BOT_TOKEN 是否正确
✓ 确保 Token 未过期或泄露
```

---

## Railway 部署

### 方式一：使用部署脚本（推荐）

#### Linux / macOS

```bash
# 1. 确保已安装 Railway CLI
# 访问: https://docs.railway.app/guides/cli

# 2. 运行部署脚本
bash deploy.sh

# 3. 按照提示输入相关信息
# • Bot Token
# • 源频道 ID
# • 目标频道 ID

# 4. 自动部署完成！
```

#### Windows

```cmd
# 1. 确保已安装 Railway CLI
# 访问: https://docs.railway.app/guides/cli

# 2. 运行部署脚本
deploy.bat

# 3. 按照提示输入相关信息
# 4. 自动部署完成！
```

### 方式二：Web 界面部署

**步骤：**

1. **访问 Railway Dashboard**
   - 打开 [https://railway.app/dashboard](https://railway.app/dashboard)
   - 登录或注册账户

2. **创建新项目**
   - 点击 "New Project"
   - 选择 "Deploy from GitHub"

3. **连接 GitHub**
   - 授权 Railway 访问你的 GitHub
   - 选择包含机器人代码的仓库

4. **配置环境变量**
   - 在项目中点击 "Variables"
   - 添加以下变量：
     ```
     BOT_TOKEN = 你的_bot_token
     SOURCE_CHANNEL_ID = -1001234567890
     TARGET_CHANNEL_ID = -1009876543210
     ```

5. **开始部署**
   - Railway 自动检测 Procfile
   - 点击 "Deploy" 开始部署
   - 等待部署完成（通常 2-5 分钟）

### 方式三：使用 Railway CLI

```bash
# 1. 安装 Railway CLI
npm install -g @railway/cli

# 2. 登录
railway login

# 3. 初始化项目
railway init

# 4. 设置环境变量
railway variables set BOT_TOKEN "你的_bot_token"
railway variables set SOURCE_CHANNEL_ID "-1001234567890"
railway variables set TARGET_CHANNEL_ID "-1009876543210"

# 5. 部署
railway up

# 6. 查看日志
railway logs
```

### 验证部署

1. **检查 Railway Dashboard**
   - 查看 "Status" 是否为 "Running"
   - 查看 "Logs" 中是否有错误

2. **测试机器人**
   - 在源频道发送消息
   - 检查是否出现在目标频道

3. **监控日志**
   ```bash
   # 使用 Railway CLI 查看实时日志
   railway logs -f
   ```

---

## 常见问题

### Q1：机器人收不到消息怎么办？

**检查清单：**
- ☐ 机器人已添加到源频道
- ☐ 机器人在频道中有"发布消息"权限
- ☐ SOURCE_CHANNEL_ID 正确（以 -100 开头）
- ☐ 频道确实发送了消息

**解决方案：**
```bash
# 检查 Bot Token 是否有效
curl https://api.telegram.org/bot{BOT_TOKEN}/getMe

# 如果返回 200，说明 Token 有效
```

### Q2：如何修改转发目标？

**方案 A：修改环境变量**
```bash
# 在 Railway Dashboard 中
# Variables → 编辑 TARGET_CHANNEL_ID
```

**方案 B：修改代码**
```python
# 编辑 main.py 中的
SOURCE_CHANNEL_ID = int(os.getenv('SOURCE_CHANNEL_ID'))
TARGET_CHANNEL_ID = int(os.getenv('TARGET_CHANNEL_ID'))
```

### Q3：能否同时转发到多个频道？

**答：可以！** 使用 `main_advanced.py`

```bash
# 1. 重命名文件
mv main_advanced.py main.py

# 2. 设置频道映射（JSON 格式）
railway variables set CHANNEL_MAPPINGS_JSON '{"源id1":"目标id1","源id2":"目标id2"}'

# 示例：
# {"-1001111111111":"-1002222222222","-1003333333333":"-1004444444444"}

# 3. 重新部署
railway up
```

### Q4：Railway 是免费的吗？

**答：是的！**

- 每月免费 $5 额度
- 此机器人消耗极少（通常 < $0.50/月）
- 完全在免费额度内

### Q5：如何查看转发日志？

**实时日志：**
```bash
# 使用 Railway CLI
railway logs -f

# 或在 Dashboard 中查看 "Logs" 标签
```

**统计信息（使用高级版本）：**
```
/stats  - 显示转发统计
```

---

## 高级配置

### 使用高级版本

高级版本（`main_advanced.py`）提供以下额外功能：

✨ 多频道映射
✨ 转发统计
✨ 详细日志记录
✨ 更好的错误处理

**启用步骤：**

```bash
# 1. 重命名文件
mv main_advanced.py main.py

# 2. 重新部署
railway up
```

**新增命令：**
```
/stats  - 显示转发统计信息
```

### 配置多频道映射

**方式一：使用 JSON 配置**

```bash
# 设置环境变量
railway variables set CHANNEL_MAPPINGS_JSON '{"-100_source1":"-100_target1","-100_source2":"-100_target2"}'

# 示例：
# {"-1001111111111":"-1002222222222","-1003333333333":"-1004444444444"}
```

**方式二：修改代码**

编辑 `main.py` 或 `main_advanced.py`：

```python
CHANNEL_MAPPINGS = {
    -100_source_id_1: -100_target_id_1,
    -100_source_id_2: -100_target_id_2,
    -100_source_id_3: -100_target_id_3,
}
```

### 自定义消息处理

修改 `handle_message` 函数实现自定义逻辑：

```python
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    
    # 只转发包含特定关键词的消息
    if message.text and "关键词" in message.text:
        # 转发消息
        
    # 或添加前缀/后缀
    if message.text:
        new_text = f"[已转发] {message.text}"
        await context.bot.send_message(chat_id=TARGET_CHANNEL_ID, text=new_text)
```

---

## 故障排除

### 日志分析

**日志中的常见错误信息：**

```
错误: Unauthorized
→ Bot Token 无效或过期

错误: Chat not found
→ 频道 ID 错误或频道已删除

错误: Not enough rights
→ 机器人权限不足，需要给予"发布消息"权限

错误: Flood control is active
→ 消息发送过于频繁，稍等片刻重试
```

### 调试步骤

1. **验证 Bot Token**
   ```bash
   curl "https://api.telegram.org/bot{BOT_TOKEN}/getMe"
   # 应返回 JSON，包含机器人信息
   ```

2. **验证频道 ID**
   ```bash
   # 确保格式正确：
   # -1001234567890（正确）
   # 1234567890（错误）
   # @channel_name（错误）
   ```

3. **检查机器人权限**
   - 打开频道设置 → 管理员
   - 找到你的机器人
   - 确保以下权限已启用：
     - ☑️ 发布消息
     - ☑️ 编辑消息
     - ☑️ 删除消息

4. **查看实时日志**
   ```bash
   railway logs -f
   # 或在 Dashboard → Logs 中查看
   ```

5. **测试消息转发**
   ```
   1. 在源频道发送简单文本消息
   2. 等待 5-10 秒
   3. 检查目标频道
   4. 查看日志中是否有相关记录
   ```

### 重启机器人

**在 Railway Dashboard 中：**
1. 进入项目
2. 点击 Deployments
3. 点击最新的 Deployment
4. 点击 "Restart"

**使用 CLI：**
```bash
railway down
railway up
```

### 获取更多帮助

- 📖 [Railway 官方文档](https://docs.railway.app)
- 🤖 [python-telegram-bot 文档](https://python-telegram-bot.readthedocs.io)
- 💬 [Telegram Bot API](https://core.telegram.org/bots/api)

---

## 总结

| 步骤 | 说明 |
|------|------|
| 1 | 从 @BotFather 获取 Bot Token |
| 2 | 获取源和目标频道 ID |
| 3 | 将机器人添加到两个频道 |
| 4 | 在 Railway 中设置环境变量 |
| 5 | 部署机器人 |
| 6 | 测试转发功能 |
| 7 | 监控日志并调整配置 |

---

祝你使用愉快！🚀
