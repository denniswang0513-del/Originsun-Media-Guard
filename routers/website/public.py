"""routers/website/public.py
---
對外公開 API（無認證、有 rate limit、聯絡表單有 Turnstile）。

Endpoints (prefix `/api/website`):
- GET  /works                 分頁列作品（?category=slug&page=1&limit=12）
- GET  /works/{slug}          單一作品詳情
- GET  /featured              首頁精選 6 筆
- GET  /categories            分類清單（含公開作品計數）
- GET  /services              服務項目
- GET  /team                  對外展示的團隊成員（crm_staff.show_on_website）
- GET  /meta                  全站 metadata（SEO、社群連結）
- POST /contact               聯絡表單（Turnstile 驗證 + 3/min rate limit）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import client_ip, public_session, rate_limit
from core.schemas_website import ContactInquiryCreate
from services.website import (
    category_service, inquiry_service, notify_service,
    project_service, service_service, settings_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website", tags=["website-public"])


@router.get("/works")
async def list_works(
    request: Request,
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(12, ge=1, le=50),
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    items, total = await project_service.list_public_projects(
        session, category_slug=category, page=page, limit=limit
    )
    return {"items": items, "total": total, "page": page, "limit": limit}


@router.get("/works/{slug}")
async def get_work(
    slug: str, request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    project = await project_service.get_public_project_by_slug(session, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Not found")
    return project


@router.get("/featured")
async def list_featured(
    request: Request,
    limit: int = Query(6, ge=1, le=12),
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    items = await project_service.list_featured_projects(session, limit=limit)
    return {"items": items}


@router.get("/categories")
async def list_categories(
    request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    return {"items": await category_service.list_public_categories(session)}


@router.get("/services")
async def list_services(
    request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    return {"items": await service_service.list_public_services(session)}


@router.get("/team")
async def list_team(
    request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    try:
        from db.models import CrmStaff
        stmt = select(CrmStaff).where(CrmStaff.show_on_website.is_(True)).order_by(CrmStaff.id)
        staff = list((await session.execute(stmt)).scalars())
        return {"items": [
            {"id": s.id, "name": s.name, "role": s.role, "bio": s.bio, "photo_url": s.photo_url}
            for s in staff
        ]}
    except Exception as e:
        logger.warning("[team] query failed: %s", e)
        return {"items": []}


@router.get("/meta")
async def get_meta(
    request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    meta = await settings_service.get_meta(session)
    meta["categories"] = await category_service.list_public_categories(session)
    return meta


@router.post("/contact", status_code=201)
async def submit_contact(
    req: ContactInquiryCreate,
    request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=3)

    settings = await settings_service.get_all_settings(session)
    ok = await notify_service.verify_turnstile(
        req.turnstile_token, settings.get("turnstile.secret", ""), ip=client_ip(request),
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Turnstile 驗證失敗")

    inquiry = await inquiry_service.create_inquiry(
        session, req.model_dump(exclude={"turnstile_token"}),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500],
    )

    try:
        await notify_service.notify_new_inquiry(inquiry, settings)
    except Exception as e:
        logger.warning("[contact] notify failed (inquiry saved): %s", e)

    return {"ok": True, "id": inquiry["id"]}
