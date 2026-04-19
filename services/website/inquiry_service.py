"""services/website/inquiry_service.py
---
聯絡表單收件、狀態管理、轉 CRM client。

Turnstile 驗證在 routers 層處理（需 request context），此處不依賴 HTTP。
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteContactInquiry

logger = logging.getLogger(__name__)


def _to_dict(inq: WebsiteContactInquiry) -> dict:
    return {
        "id": inq.id,
        "name": inq.name,
        "email": inq.email,
        "phone": inq.phone,
        "company": inq.company,
        "service_type": inq.service_type,
        "budget_range": inq.budget_range,
        "message": inq.message,
        "source": inq.source,
        "status": inq.status or "new",
        "converted_client_id": inq.converted_client_id,
        "ip_address": inq.ip_address,
        "created_at": inq.created_at.isoformat() if inq.created_at else None,
        "handled_at": inq.handled_at.isoformat() if inq.handled_at else None,
        "handled_by": inq.handled_by,
        "notes": inq.notes,
    }


async def create_inquiry(session: AsyncSession, data: dict, ip: str, user_agent: str) -> dict:
    """寫入新詢問並 commit。調用方負責後續通知。"""
    inq = WebsiteContactInquiry(
        name=data.get("name"),
        email=data.get("email"),
        phone=data.get("phone"),
        company=data.get("company"),
        service_type=data.get("service_type"),
        budget_range=data.get("budget_range"),
        message=data.get("message"),
        source=data.get("source", "/contact"),
        status="new",
        ip_address=ip,
        user_agent=user_agent,
    )
    session.add(inq)
    await session.commit()
    await session.refresh(inq)
    return _to_dict(inq)


async def list_inquiries(
    session: AsyncSession,
    status: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[dict], int]:
    base = select(WebsiteContactInquiry)
    if status:
        base = base.where(WebsiteContactInquiry.status == status)

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    stmt = base.order_by(desc(WebsiteContactInquiry.created_at)).offset(
        (page - 1) * limit
    ).limit(limit)

    return [_to_dict(i) for i in (await session.execute(stmt)).scalars()], total


async def get_inquiry(session: AsyncSession, inquiry_id: int) -> Optional[dict]:
    inq = await session.get(WebsiteContactInquiry, inquiry_id)
    return _to_dict(inq) if inq else None


async def update_inquiry(
    session: AsyncSession,
    inquiry_id: int,
    updates: dict,
    handled_by: Optional[str] = None,
) -> Optional[dict]:
    from datetime import datetime, timezone
    inq = await session.get(WebsiteContactInquiry, inquiry_id)
    if not inq:
        return None
    for k in ("status", "notes"):
        if k in updates and updates[k] is not None:
            setattr(inq, k, updates[k])
    if updates.get("status") in ("in_progress", "converted", "spam"):
        inq.handled_at = datetime.now(timezone.utc)
        inq.handled_by = handled_by
    await session.commit()
    await session.refresh(inq)
    return _to_dict(inq)


async def delete_inquiry(session: AsyncSession, inquiry_id: int) -> bool:
    result = await session.execute(
        delete(WebsiteContactInquiry).where(WebsiteContactInquiry.id == inquiry_id)
    )
    await session.commit()
    return result.rowcount > 0


async def convert_to_client(
    session: AsyncSession,
    inquiry_id: int,
    convert_data: dict,
    handled_by: Optional[str] = None,
) -> Optional[dict]:
    """把詢問轉成 CRM clients 表的一筆潛在客戶。

    建立新的 Client 紀錄，並更新 inquiry.converted_client_id + status='converted'。
    """
    from datetime import datetime, timezone
    try:
        from db.models import Client
    except ImportError:
        logger.error("[inquiry] Client model unavailable, cannot convert")
        return None

    inq = await session.get(WebsiteContactInquiry, inquiry_id)
    if not inq:
        return None

    client_id = uuid.uuid4().hex[:16]
    client = Client(
        id=client_id,
        short_name=(inq.company or inq.name or "網站潛在客戶")[:64],
        full_name=inq.company or "",
        tax_id=convert_data.get("tax_id") or "",
        contact_person=convert_data.get("contact_person") or inq.name or "",
        contact_method=f"Email: {inq.email or ''} / Phone: {inq.phone or ''}",
        status="潛在客戶",
        notes=(
            f"從網站聯絡表單轉入 (inquiry #{inq.id})\n"
            f"來源頁：{inq.source or '/contact'}\n"
            f"服務類型：{inq.service_type or '-'}\n"
            f"預算：{inq.budget_range or '-'}\n"
            f"訊息：{inq.message or ''}\n\n"
            f"{convert_data.get('notes', '')}"
        ),
    )
    session.add(client)

    inq.converted_client_id = client_id
    inq.status = "converted"
    inq.handled_at = datetime.now(timezone.utc)
    inq.handled_by = handled_by

    await session.commit()
    return {"client_id": client_id, "inquiry": _to_dict(inq)}


async def count_month_stats(session: AsyncSession) -> dict:
    """儀表板：本月詢問數、已轉換數。"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total = (await session.execute(
        select(func.count(WebsiteContactInquiry.id)).where(
            WebsiteContactInquiry.created_at >= month_start
        )
    )).scalar() or 0

    converted = (await session.execute(
        select(func.count(WebsiteContactInquiry.id)).where(
            WebsiteContactInquiry.created_at >= month_start,
            WebsiteContactInquiry.status == "converted",
        )
    )).scalar() or 0

    return {"month_inquiries": total, "month_converted": converted}
