"""routers/website/admin_works.py
---
管理端：作品對外展示編輯、排序、置頂。

Endpoints (prefix `/api/website/admin`):
- GET  /works                  列出所有作品（含未公開）
- PUT  /works/{project_id}     更新對外展示欄位 + 分類關聯
- POST /works/reorder          批次重排 public_sort_order
- POST /works/{id}/featured    切換精選 ({"featured": true/false})
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session
from core.schemas_website import ProjectAdminUpdate, ProjectReorder
from services.website import project_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-works"])


@router.get("/works")
async def list_admin_works(
    include_non_public: bool = True,
    session: AsyncSession = Depends(admin_session),
):
    items = await project_service.list_admin_projects(
        session, include_non_public=include_non_public
    )
    return {"items": items}


@router.put("/works/{project_id}")
async def update_work(
    project_id: str,
    req: ProjectAdminUpdate,
    session: AsyncSession = Depends(admin_session),
):
    updates = req.model_dump(exclude_none=True, exclude={"category_ids"})
    ok = await project_service.update_project_public(
        session, project_id, updates, category_ids=req.category_ids
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True}


@router.post("/works/reorder")
async def reorder_works(
    req: ProjectReorder,
    session: AsyncSession = Depends(admin_session),
):
    await project_service.reorder_projects(session, req.order)
    return {"ok": True, "count": len(req.order)}


@router.post("/works/{project_id}/featured")
async def set_featured(
    project_id: str,
    payload: dict,
    session: AsyncSession = Depends(admin_session),
):
    featured = bool(payload.get("featured", False))
    ok = await project_service.toggle_featured(session, project_id, featured)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, "featured": featured}
