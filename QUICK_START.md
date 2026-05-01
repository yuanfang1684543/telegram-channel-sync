# 🚀 Telegram 频道同步机器人 - 快速参考

## 📌 关键信息

### 所需的 3 个核心信息
```
✓ BOT_TOKEN          - Telegram Bot Token (@BotFather)
✓ SOURCE_CHANNEL_ID  - 源频道 ID (格式: -100XXXXXXXXX)
✓ TARGET_CHANNEL_ID  - 目标频道 ID (格式: -100XXXXXXXXX)
```

### 获取 Bot Token
```
1. 打开 Telegram，搜索 @BotFather
2. 发送 /newbot
3. 按照提示创建机器人
4. 复制获得的 Token
```

### 获取频道 ID
```
方法 A (推荐)：
  1. 在频道发送任何消息
  2. 转发给 @userinfobot
  3. 查看返回的 "from_chat_id": -100XXXXXXXXX
  
方法 B：
  1. 在终端运行 curl
  2. 查看频道数据
```

---

## 🛠️ 快速命令

### 本地运行

#### Linux / macOS
```bash
# 创建虚拟环境
python3 -m venv venv && source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export BOT_TOKEN="your_token"
export SOURCE_CHANNEL_ID="-1001111111111"
export TARGET_CHANNEL_ID="-1009999999999"

# 运行机器人
python main.py
```

#### Windows
```bash
# 创建虚拟环境
python -m venv venv && venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
set BOT_TOKEN=your_token
set SOURCE_CHANNEL_ID=-1001111111111
set TARGET_CHANNEL_ID=-1009999999999

# 运行机器人
python main.py
```

### Docker 运行

```bash
# 构建镜像
docker build -t telegram-bot .

# 运行容器
docker run -e BOT_TOKEN="your_token" \
           -e SOURCE_CHANNEL_ID="-1001111111111" \
           -e TARGET_CHANNEL_ID="-1009999999999" \
           telegram-bot

# 或使用 docker-compose
docker-compose up -d
```

### Railway 部署

#### 使用脚本（推荐）
```bash
# Linux / macOS
bash deploy.sh

# Windows
deploy.bat
```

#### 使用 Railway CLI
```bash
# 登录
railway login

# 初始化
railway init

# 设置变量
railway variables set BOT_TOKEN "your_token"
railway variables set SOURCE_CHANNEL_ID "-1001111111111"
railway variables set TARGET_CHANNEL_ID "-1009999999999"

# 部署
railway up

# 查看日志
railway logs -f
```

---

## 📋 文件说明

| 文件 | 用途 |
|------|------|
| `main.py` | 基础版机器人 |
| `main_advanced.py` | 高级版（多频道、统计） |
| `requirements.txt` | Python 依赖 |
| `Procfile` | Railway 启动配置 |
| `Dockerfile` | Docker 镜像配置 |
| `docker-compose.yml` | Docker Compose 配置 |
| `deploy.sh` | Linux/Mac 部署脚本 |
| `deploy.bat` | Windows 部署脚本 |
| `.env.example` | 环境变量示例 |
| `README.md` | 完整说明 |
| `GUIDE_CN.md` | 详细中文指南 |

---

## 🔍 验证配置

### 检查清单
- [ ] Bot Token 已从 @BotFather 获取
- [ ] 源频道 ID 格式正确 (-100XXXXXXXXX)
- [ ] 目标频道 ID 格式正确 (-100XXXXXXXXX)
- [ ] 机器人已添加到源频道（作为管理员）
- [ ] 机器人已添加到目标频道（作为管理员）
- [ ] 机器人拥有"发布消息"权限

### 测试转发
```
1. 在源频道发送一条简单文本消息
2. 等待 5-10 秒
3. 检查目标频道是否出现该消息
4. 查看日志确认转发成功
```

---

## 🐛 常见问题速查

| 问题 | 解决方案 |
|------|---------|
| Bot 无法接收消息 | ✓ 检查机器人是否在频道中<br>✓ 检查 SOURCE_CHANNEL_ID 是否正确 |
| 转发失败 | ✓ 检查机器人权限<br>✓ 查看错误日志 |
| Token 无效 | ✓ 重新从 @BotFather 获取<br>✓ 检查复制是否完整 |
| 频道 ID 错误 | ✓ 确保以 -100 开头<br>✓ 使用 @userinfobot 方法 |

---

## 📞 帮助资源

- 📖 [完整中文指南](./GUIDE_CN.md)
- 📖 [README](./README.md)
- 🔗 [Railway 官方文档](https://docs.railway.app)
- 🤖 [python-telegram-bot 文档](https://python-telegram-bot.readthedocs.io)

---

## 💡 提示

✨ 机器人支持所有 Telegram 媒体格式
✨ 保留原始消息格式和样式
✨ 支持扩展到多频道映射
✨ Railway 每月 $5 免费额度足够运行
✨ 24/7 无需手动启动

---

**快速开始时间：约 10 分钟** ⏱️
