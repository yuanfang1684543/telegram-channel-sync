"""
Google Drive & Gmail 服务模块
- Drive: 备份消息日志、导出/导入配置
- Gmail: 发送通知邮件、每日报告
"""
from __future__ import annotations
import os, json, logging, base64, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Google Drive ──────────────────────────────────────────────────────────────

def _drive_service():
    """构建 Drive API 服务（使用服务账号 JSON）"""
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not sa_json:
            return None
        sa_info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error(f"Drive 服务初始化失败: {e}")
        return None

def drive_upload_text(content: str, filename: str, folder_id: str | None = None) -> str | None:
    """上传文本内容到 Drive，返回文件 ID"""
    svc = _drive_service()
    if not svc:
        return None
    try:
        from googleapiclient.http import MediaInMemoryUpload
        meta = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
        f = svc.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
        logger.info(f"Drive 上传成功: {filename} ({f.get('id')})")
        return f.get("id")
    except Exception as e:
        logger.error(f"Drive 上传失败: {e}")
        return None

def drive_update_file(file_id: str, content: str) -> bool:
    """更新 Drive 上已有文件内容"""
    svc = _drive_service()
    if not svc:
        return False
    try:
        from googleapiclient.http import MediaInMemoryUpload
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
        svc.files().update(fileId=file_id, media_body=media).execute()
        logger.info(f"Drive 文件已更新: {file_id}")
        return True
    except Exception as e:
        logger.error(f"Drive 更新失败: {e}")
        return False

def drive_get_file_content(file_id: str) -> str | None:
    """从 Drive 读取文件内容"""
    svc = _drive_service()
    if not svc:
        return None
    try:
        content = svc.files().get_media(fileId=file_id).execute()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    except Exception as e:
        logger.error(f"Drive 读取失败: {e}")
        return None

def drive_list_files(folder_id: str | None = None) -> list[dict]:
    """列出 Drive 文件（可指定文件夹）"""
    svc = _drive_service()
    if not svc:
        return []
    try:
        q = f"'{folder_id}' in parents" if folder_id else "trashed=false"
        res = svc.files().list(q=q, fields="files(id,name,modifiedTime,webViewLink)").execute()
        return res.get("files", [])
    except Exception as e:
        logger.error(f"Drive 列表失败: {e}")
        return []

def drive_delete_file(file_id: str) -> bool:
    svc = _drive_service()
    if not svc:
        return False
    try:
        svc.files().delete(fileId=file_id).execute()
        return True
    except Exception as e:
        logger.error(f"Drive 删除失败: {e}")
        return False

def drive_is_configured() -> bool:
    return bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

# ── Gmail ─────────────────────────────────────────────────────────────────────

def gmail_is_configured() -> bool:
    return bool(os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD"))

def gmail_send(to: str | list[str], subject: str, body: str, html: str | None = None) -> bool:
    """
    通过 Gmail SMTP 发送邮件
    需设置环境变量: GMAIL_USER, GMAIL_APP_PASSWORD
    """
    user = os.getenv("GMAIL_USER", "")
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not user or not password:
        logger.warning("Gmail 未配置，跳过发送")
        return False

    recipients = [to] if isinstance(to, str) else to
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = ", ".join(recipients)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())

        logger.info(f"Gmail 已发送: {subject} → {recipients}")
        return True
    except Exception as e:
        logger.error(f"Gmail 发送失败: {e}")
        return False

def gmail_send_report(to: str, stats: dict) -> bool:
    """发送运行日报"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject = f"[TG同步机器人] 运行报告 {now}"
    body = (
        f"Telegram 频道同步机器人运行报告\n"
        f"{'='*40}\n"
        f"时间: {now}\n\n"
        f"📡 频道映射: {stats.get('mappings', 0)} 条\n"
        f"📨 今日转发: {stats.get('forwarded', 0)} 条\n"
        f"📢 定时广告: {stats.get('ads_active', 0)}/{stats.get('ads_total', 0)} 运行中\n"
        f"🔤 替换规则: {stats.get('rules', 0)} 条\n"
        f"❌ 转发失败: {stats.get('errors', 0)} 次\n"
    )
    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px">
    <h2 style="color:#2196F3">📱 TG同步机器人 运行报告</h2>
    <p style="color:#666">{now}</p>
    <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:8px;border:1px solid #ddd">📡 频道映射</td><td style="padding:8px;border:1px solid #ddd"><b>{stats.get('mappings',0)} 条</b></td></tr>
    <tr style="background:#f5f5f5"><td style="padding:8px;border:1px solid #ddd">📨 今日转发</td><td style="padding:8px;border:1px solid #ddd"><b>{stats.get('forwarded',0)} 条</b></td></tr>
    <tr><td style="padding:8px;border:1px solid #ddd">📢 运行广告</td><td style="padding:8px;border:1px solid #ddd"><b>{stats.get('ads_active',0)}/{stats.get('ads_total',0)}</b></td></tr>
    <tr style="background:#f5f5f5"><td style="padding:8px;border:1px solid #ddd">❌ 失败次数</td><td style="padding:8px;border:1px solid #ddd"><b style="color:{'red' if stats.get('errors',0) else 'green'}">{stats.get('errors',0)}</b></td></tr>
    </table>
    </body></html>
    """
    return gmail_send(to, subject, body, html)

def gmail_send_alert(to: str, title: str, detail: str) -> bool:
    """发送告警邮件"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return gmail_send(
        to,
        f"[TG机器人告警] {title}",
        f"告警时间: {now}\n\n{title}\n\n详情:\n{detail}",
        f"<h3 style='color:red'>⚠️ {title}</h3><p>时间: {now}</p><pre>{detail}</pre>",
    )
