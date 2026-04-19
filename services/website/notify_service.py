"""services/website/notify_service.py
---
聯絡表單通知：走 notifier.notify_tab("inquiry_received", **vars) 觸發 Google
Chat / LINE（依 settings.json notification_channels 開關）+ log 出 email stub。

Email 傳送留 log stub；正式接 SMTP 在 M-F 部署時補。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _template_vars(inq: dict) -> dict:
    """組出 inquiry_received 範本需要的變數（與 notifier.py _defaults 對齊）。"""
    return {
        "id": inq.get("id", "?"),
        "name": inq.get("name") or "-",
        "email": inq.get("email") or "-",
        "phone": inq.get("phone") or "-",
        "company": inq.get("company") or "-",
        "service_type": inq.get("service_type") or "-",
        "budget_range": inq.get("budget_range") or "-",
        "message": inq.get("message") or "",
    }


def _format_autoreply(inq: dict, company_name: str, reply_email: str) -> str:
    return (
        f"您好 {inq.get('name', '')}，\n\n"
        f"感謝您透過 {company_name} 官網與我們聯繫。\n"
        f"我們已收到您的訊息，將在 1-2 個工作天內由專人回覆。\n\n"
        f"您填寫的內容摘要：\n"
        f"服務類型：{inq.get('service_type', '-')}\n"
        f"訊息：\n{inq.get('message', '')}\n\n"
        f"如有緊急需求，可直接回信至 {reply_email}。\n\n"
        f"-- {company_name}"
    )


async def notify_new_inquiry(inq: dict, settings: dict) -> dict:
    """發送聯絡表單通知。

    Google Chat / LINE 走 notifier.notify_tab，讀 settings.notification_channels.inquiry
    決定實際 channel 開關。Email（內部 + 自動回覆）目前只記 log，M-F 接 SMTP。
    """
    result = {"chat_sent": False, "email_internal": False, "autoreply": False}

    try:
        from notifier import notify_tab
        notify_tab("inquiry_received", **_template_vars(inq))
        result["chat_sent"] = True
    except Exception as e:
        logger.warning("[notify] notify_tab failed: %s", e)

    reply_email = settings.get("company.email", "")
    company_name = settings.get("company.name_zh", "Originsun")
    internal_recipient = settings.get("notify.email_to", reply_email)

    if internal_recipient:
        logger.info(
            "[notify] email-internal stub → %s (inquiry #%s)",
            internal_recipient, inq.get("id"),
        )
        result["email_internal"] = True
    if inq.get("email"):
        logger.info(
            "[notify] autoreply stub → %s\n%s",
            inq["email"], _format_autoreply(inq, company_name, reply_email),
        )
        result["autoreply"] = True

    return result


async def verify_turnstile(token: str, secret: str, ip: Optional[str] = None) -> bool:
    """Cloudflare Turnstile server-side 驗證。

    secret 空字串視為 dev 模式（自動通過）。正式環境必須設定 secret。
    """
    if not secret:
        logger.debug("[turnstile] dev mode (empty secret), auto-pass")
        return True
    if not token:
        return False

    try:
        import httpx
    except ImportError:
        logger.warning("[turnstile] httpx unavailable, auto-pass in fallback")
        return True

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            data = {"secret": secret, "response": token}
            if ip:
                data["remoteip"] = ip
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=data,
            )
            return bool(resp.json().get("success", False))
    except Exception as e:
        logger.warning("[turnstile] verification error: %s", e)
        return False
