"""routers/website/admin_rebuild.py
---
管理端：Rebuild 觸發、Notion 同步、儀表板統計。

Endpoints (prefix `/api/website/admin`):
- GET  /stats             儀表板指標（本月詢問/轉換、公開作品數、最新詢問）
- POST /rebuild           觸發 npm run build（背景）
- GET  /rebuild/status    查詢 rebuild 狀態
- GET  /notion/status     Notion 連線狀態
- POST /notion/sync       觸發 Notion 同步（目前等同 rebuild）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_guard, admin_session
from services.website import (
    category_service, inquiry_service, project_service,
    rebuild_service, settings_service,
)

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-rebuild"])


@router.get("/stats")
async def get_stats(session: AsyncSession = Depends(admin_session)):
    month_stats = await inquiry_service.count_month_stats(session)
    latest_inquiries, _ = await inquiry_service.list_inquiries(session, page=1, limit=5)
    top_cats = await category_service.list_admin_categories(session)
    public_projects = await project_service.list_admin_projects(
        session, include_non_public=False
    )
    featured_count = sum(1 for p in public_projects if p.get("public_featured"))
    top_cats.sort(key=lambda c: c.get("project_count", 0), reverse=True)
    return {
        **month_stats,
        "total_public_works": len(public_projects),
        "featured_count": featured_count,
        "latest_inquiries": latest_inquiries,
        "top_categories": top_cats[:5],
    }


@router.post("/rebuild")
async def trigger_rebuild(_: None = Depends(admin_guard)):
    return await rebuild_service.trigger_rebuild()


@router.get("/rebuild/status")
async def rebuild_status(_: None = Depends(admin_guard)):
    return rebuild_service.get_rebuild_status()


@router.get("/notion/status")
async def notion_status(session: AsyncSession = Depends(admin_session)):
    settings = await settings_service.get_all_settings(session)
    return await rebuild_service.get_notion_status(settings)


@router.post("/notion/sync")
async def notion_sync(_: None = Depends(admin_guard)):
    return await rebuild_service.trigger_notion_sync()
