"""
notifier.py
───────────
Sends task completion notifications via:
  - Google Chat (Incoming Webhook)
  - LINE Notify

Webhook URL / Token and Message Templates are read from settings.json or environment variables.

Usage:
  from notifier import notify_all
  notify_all(project_name="20260302", drive_url="https://drive.google.com/...", file_count=100, total_size=102400)
"""

import os
import json
from typing import Optional

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def _load_settings() -> dict:
    if os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _fmt_size_py(size: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {u}"
        size /= 1024.0
    return f"{size:.1f} TB"

def _build_message(template: str, project_name: str, drive_url: Optional[str], file_count: int, total_size: float) -> str:
    msg = template
    msg = msg.replace("{project_name}", project_name)
    msg = msg.replace("{file_count}", str(file_count))
    msg = msg.replace("{total_size}", _fmt_size_py(total_size))
    msg = msg.replace("{report_url}", drive_url if drive_url else "無報表連結")
    return msg


def send_google_chat(project_name: str, drive_url: Optional[str] = None, file_count: int = 0, total_size: float = 0.0) -> bool:
    """
    Send a Google Chat card message via Incoming Webhook.
    Returns True on success, False on failure.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        print("notifier: 'requests' not installed, skipping Google Chat notification.")
        return False

    settings = _load_settings()
    notif = settings.get("notifications", {})
    tpls = settings.get("message_templates", {})
    
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK") or notif.get("google_chat_webhook", "")

    if not webhook_url:
        print("notifier: Google Chat webhook URL not configured.")
        return False

    # Default template fallback
    if drive_url:
        default_tpl = "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}"
        tpl = tpls.get("report_success") or default_tpl
    else:
        default_tpl = "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}"
        tpl = tpls.get("backup_success") or default_tpl

    text = _build_message(tpl, project_name, drive_url, file_count, total_size)
    payload = {"text": text}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"notifier: Google Chat send failed: {e}")
        return False


def send_line_notify(project_name: str, drive_url: Optional[str] = None, file_count: int = 0, total_size: float = 0.0) -> bool:
    """
    Send a LINE Notify message.
    Returns True on success, False on failure.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        print("notifier: 'requests' not installed, skipping LINE Notify.")
        return False

    settings = _load_settings()
    notif = settings.get("notifications", {})
    tpls = settings.get("message_templates", {})
    
    token = os.environ.get("LINE_NOTIFY_TOKEN") or notif.get("line_notify_token", "")

    if not token:
        print("notifier: LINE Notify token not configured.")
        return False

    # Default template fallback
    if drive_url:
        default_tpl = "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}"
        tpl = tpls.get("report_success") or default_tpl
    else:
        default_tpl = "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}"
        tpl = tpls.get("backup_success") or default_tpl

    # LINE messaging format: always start with a newline to clearly separate the sender name
    text = "\n" + _build_message(tpl, project_name, drive_url, file_count, total_size)

    try:
        resp = requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {token}"},
            data={"message": text},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"notifier: LINE Notify send failed: {e}")
        return False


def notify_all(project_name: str, drive_url: Optional[str] = None, file_count: int = 0, total_size: float = 0.0) -> None:
    """
    Fire-and-forget wrapper: send to all configured notification channels.
    Silently skips any channel that fails.
    """
    send_google_chat(project_name, drive_url, file_count, total_size)
    send_line_notify(project_name, drive_url, file_count, total_size)


def notify_tab(template_key: str, **variables) -> None:
    """
    Generic notification for any tab.
    Looks up the message template by template_key from settings.json,
    substitutes all provided keyword variables, and sends to enabled channels.

    The tab_key (e.g. 'backup', 'transcode') is derived from template_key by
    stripping the '_success' suffix. Channel toggles are read from
    notification_channels in settings.json.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        return

    settings = _load_settings()
    notif = settings.get("notifications", {})
    tpls = settings.get("message_templates", {})
    channels = settings.get("notification_channels", {})

    # Derive tab key: "backup_success" → "backup"
    tab_key = template_key.replace("_success", "")
    tab_channels = channels.get(tab_key, {"gchat": True, "line": False})
    send_gchat = tab_channels.get("gchat", True)
    send_line  = tab_channels.get("line", False)

    # Nothing enabled → skip
    if not send_gchat and not send_line:
        return

    # Default fallback templates per tab
    _defaults = {
        "backup_success":    "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}",
        "report_success":    "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}",
        "transcode_success": "🎬 【轉檔完成】{project_name} 已轉檔完成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
        "concat_success":    "🔗 【串接完成】{output_file} 已輸出！\n📁 儲存位置：{dest_dir}",
        "verify_success":    "🔍 【比對完成】{project_name}\n✅ 通過：{pass_count} | ❌ 失敗：{fail_count} | 共 {total_count} 個",
        "transcribe_success":"🎙️ 【逐字稿完成】{project_name} 已生成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
    }

    raw_tpl = tpls.get(template_key) or _defaults.get(template_key, "")
    if not raw_tpl:
        return

    # Substitute all provided variables; unknown placeholders remain as-is
    msg = raw_tpl
    for k, v in variables.items():
        if isinstance(v, float):
            if k in ("total_size", "size", "total_bytes"):
                v_str = _fmt_size_py(v)
            else:
                v_str = f"{v:.2f}"
        else:
            v_str = str(v)
        msg = msg.replace(f"{{{k}}}", v_str)

    gchat_url = os.environ.get("GOOGLE_CHAT_WEBHOOK") or notif.get("google_chat_webhook", "")
    line_token = os.environ.get("LINE_NOTIFY_TOKEN") or notif.get("line_notify_token", "")

    if send_gchat and gchat_url:
        try:
            requests.post(gchat_url, json={"text": msg}, timeout=10)
        except Exception as e:
            print(f"notifier: Google Chat [{template_key}] failed: {e}")

    if send_line and line_token:
        try:
            requests.post(
                "https://notify-api.line.me/api/notify",
                headers={"Authorization": f"Bearer {line_token}"},
                data={"message": "\n" + msg},
                timeout=10
            )
        except Exception as e:
            print(f"notifier: LINE Notify [{template_key}] failed: {e}")

