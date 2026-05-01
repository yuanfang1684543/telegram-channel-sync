# Telegram 频道同步消息机器人

一个功能完整的 Telegram 机器人，可以自动监听源频道的消息并同步转发到目标频道。

## ✨ 功能特性

- ✅ 自动监听源频道消息
- ✅ 支持多种媒体类型（文本、图片、视频、音频、文件）
- ✅ 保留原始格式和样式
- ✅ 自动错误处理和日志记录
- ✅ 轻量级无数据库需求
- ✅ 支持多频道同步（可扩展）

## 📋 前置要求

1. **Telegram Bot Token** - 从 [@BotFather](https://t.me/botfather) 获取
2. **源频道 ID** 和 **目标频道 ID**
3. **Railway 账户** - 访问 [railway.app](https://railway.app)

## 🚀 快速开始

### 1️⃣ 获取必要的 ID

#### 获取 Bot Token
- 打开 Telegram，搜索 `@BotFather`
- 发送 `/newbot` 命令
- 按照提示创建机器人
- 复制获得的 Token

#### 获取频道 ID
**方法一：使用 Bot Forward 消息**
1. 将机器人添加到源频道（作为管理员）
2. 在源频道发送任何消息
3. 将消息转发给 [@userinfobot](https://t.me/userinfobot)
4. 获得频道 ID（格式：-100XXXXXXXXX）

**方法二：使用链接提取**
- 频道链接：`https://t.me/your_channel`
- 对于公开频道，替换 `your_channel` 为数字 ID
- 对于私密频道，使用上述方法一

### 2️⃣ 本地测试（可选）

```bash
# 克隆或下载项目文件
cd telegram-channel-sync

# 安装依赖
pip install -r requirements.txt

# 设置环境变量（Linux/Mac）
export BOT_TOKEN="你的_bot_token"
export SOURCE_CHANNEL_ID="-100你的_源频道_id"
export TARGET_CHANNEL_ID="-100你的_目标频道_id"

# Windows 用户使用
set BOT_TOKEN=你的_bot_token
set SOURCE_CHANNEL_ID=-100你的_源频道_id
set TARGET_CHANNEL_ID=-100你的_目标频道_id

# 启动机器人
python main.py
```

### 3️⃣ 部署到 Railway

#### 方式一：使用 Railway CLI（推荐）

```bash
# 1. 安装 Railway CLI
# 访问 https://railway.app/ 获取安装指令

# 2. 登录 Railway
railway login

# 3. 初始化项目
railway init

# 4. 添加环境变量
railway variables set BOT_TOKEN "你的_bot_token"
railway variables set SOURCE_CHANNEL_ID "-100你的_源频道_id"
railway variables set TARGET_CHANNEL_ID "-100你的_目标频道_id"

# 5. 部署
railway up
```

#### 方式二：通过 Railway Web 界面

1. **创建新项目**
   - 访问 [railway.app/dashboard](https://railway.app/dashboard)
   - 点击 "New Project"
   - 选择 "GitHub Repo" 或 "Deploy from GitHub"

2. **连接 GitHub**
   - 授权 Railway 访问你的 GitHub 账户
   - 选择包含此代码的仓库

3. **配置环境变量**
   - 在 Railway Dashboard 中：
   - 进入你的项目 → Variables
   - 添加以下变量：
     - `BOT_TOKEN` = 你的bot token
     - `SOURCE_CHANNEL_ID` = -100你的源频道id
     - `TARGET_CHANNEL_ID` = -100你的目标频道id

4. **部署**
   - Railway 会自动检测 `Procfile`
   - 点击 "Deploy" 开始部署

## 📱 配置说明

### 环境变量

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `BOT_TOKEN` | Telegram Bot Token | `123456:ABC-DEF...` |
| `SOURCE_CHANNEL_ID` | 源频道 ID（要监听的频道） | `-1001234567890` |
| `TARGET_CHANNEL_ID` | 目标频道 ID（转发目标） | `-1001234567890` |

> ⚠️ **重要**：频道 ID 必须以 `-100` 开头！

## 🔧 机器人命令

机器人支持以下命令：

```
/start  - 启动机器人并显示配置信息
/help   - 显示帮助信息
/status - 查看当前运行状态
```

## 📝 支持的消息类型

- ✓ 文本消息
- ✓ 图片（带或不带说明文字）
- ✓ 视频（带或不带说明文字）
- ✓ 音频
- ✓ 文件/文档
- ✓ 动画（GIF）
- ✓ 贴纸
- ✓ 格式化文本（粗体、斜体、代码等）

## 🔍 故障排除

### 常见问题

**问：机器人无法接收消息？**
- ✓ 确认机器人已被添加到源频道
- ✓ 机器人需要有"发布消息"权限
- ✓ 检查 `SOURCE_CHANNEL_ID` 是否正确

**问：转发失败？**
- ✓ 确认机器人是目标频道的管理员
- ✓ 检查 `TARGET_CHANNEL_ID` 是否正确
- ✓ 查看 Railway 日志了解错误详情

**问：如何查看日志？**
- 在 Railway Dashboard 中选择项目
- 点击 "Deployments"
- 查看实时日志输出

### 调试建议

1. **检查 Token**
   ```bash
   # 测试 token 是否有效
   curl https://api.telegram.org/bot{BOT_TOKEN}/getMe
   ```

2. **验证频道 ID**
   - 确保 ID 包含 `-100` 前缀
   - ID 应为负整数

3. **查看 Railway 日志**
   - 登录 Railway Dashboard
   - 实时查看部署日志
   - 搜索错误关键词

## 🛡️ 安全建议

1. **不要硬编码敏感信息**
   - 始终使用环境变量存储 Token 和 ID
   - 不要提交 `.env` 文件到 Git

2. **限制 Bot 权限**
   - 仅在需要的频道中添加机器人
   - 给予最小必需权限

3. **监控日志**
   - 定期检查 Railway 日志
   - 及时处理错误信息

## 📊 扩展功能

### 添加多个频道映射

编辑 `main.py` 中的 `handle_message` 函数：

```python
CHANNEL_MAPPINGS = {
    -100111111111: -100222222222,  # 源频道 → 目标频道
    -100333333333: -100444444444,  # 另一个映射
}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post.chat_id in CHANNEL_MAPPINGS:
        target_id = CHANNEL_MAPPINGS[update.channel_post.chat_id]
        # 转发消息到对应的目标频道
```

### 添加消息过滤

```python
# 只转发包含特定关键词的消息
if update.channel_post.text and "关键词" in update.channel_post.text:
    # 转发消息
```

## 💰 成本

- **Railway 免费额度**：$5/月
- 此机器人使用资源极少，完全在免费额度内
- 主要成本来自：轮询请求（可忽略）

## 📄 项目结构

```
.
├── main.py           # 主程序
├── requirements.txt  # Python 依赖
├── Procfile         # Railway 启动配置
├── runtime.txt      # Python 版本
└── README.md        # 本文件
```

## 📞 获取帮助

如遇到问题：

1. 查看本文档的故障排除部分
2. 检查 Railway Dashboard 中的实时日志
3. 验证所有环境变量配置正确
4. 确认机器人权限设置正确

## 📜 许可证

MIT License - 自由使用和修改

## 🌟 提示

- 定期检查 Railway 的免费额度使用情况
- 可以同时监听多个源频道（修改代码）
- 机器人 24/7 运行，无需手动操作
- 支持自定义消息处理逻辑

---

**祝你使用愉快！** 🚀
