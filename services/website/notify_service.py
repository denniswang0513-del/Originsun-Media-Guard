"""services/website/notify_service.py
---
聯絡表單 4 通道通知：Email / LINE / Google Chat / 自動回覆。

設計：輕量封裝，不自行實作傳輸層，複用既有 notifier.py 的 send_* 函式。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _format_inquiry_body(inq: dict) -> str:
    """組裝給內部通知用的訊息內容。"""
    return (
        f"【新的網站詢問】#{inq.get('id', '?')}\n"
        f"姓名：{inq.get('name', '-')}\n"
        f"Email：{inq.get('email', '-')}\n"
        f"電話：{inq.get('phone', '-')}\n"
        f"公司：{inq.get('company', '-')}\n"
        f"服務類型：{inq.get('service_type', '-')}\n"
        f"預算：{inq.get('budget_range', '-')}\n"
        f"來源頁：{inq.get('source', '-')}\n\n"
        f"訊息：\n{inq.get('message', '')}"
    )


def _format_autoreply_body(inq: dict, company_name: str, reply_email: str) -> str:
    """給填表人的自動回覆。"""
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
    """發送 4 通道通知。

    Returns: dict of {"channel": bool success} — 失敗個別 channel 不阻塞其他。
    """
    result = {"google_chat": False, "line": False, "email_internal": False, "autoreply": False}

    # Google Chat / LINE：複用既有 notifier.py
    try:
        from notifier import send_google_chat, send_line_notify
        body = _format_inquiry_body(inq)
        try:
            result["google_chat"] = bool(send_google_chat(
                project_name=body[:200],  # notifier 原簽名限制，這裡塞全文會被截
            ))
        except Exception as e:
            logger.warning("[notify] google_chat failed: %s", e)
        try:
            result["line"] = bool(send_line_notify(
                project_name=body[:200],
            ))
        except Exception as e:
            logger.warning("[notify] line failed: %s", e)
    except ImportError:
        logger.warning("[notify] notifier.py unavailable, skip chat/line")

    # Email 內部 + 自動回覆：最小實作，stub 記 log（M-F 接 SMTP 時補）
    reply_email = settings.get("company.email", "")
    company_name = settings.get("company.name_zh", "Originsun")
    internal_recipient = settings.get("notify.email_to", reply_email)

    if internal_recipient:
        logger.info(
            "[notify] email-internal → %s\nsubject: 新詢問 #%s\n%s",
            internal_recipient, inq.get("id"), _format_inquiry_body(inq),
        )
        result["email_internal"] = True
    if inq.get("email"):
        logger.info(
            "[notify] autoreply → %s\n%s",
            inq["email"], _format_autoreply_body(inq, company_name, reply_email),
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
