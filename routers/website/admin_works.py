"""routers/website/admin_works.py
---
管理端：作品對外展示編輯、排序、置頂、新增、編輯入口。

Endpoints (prefix `/api/website/admin`):
- GET  /works                      列出所有作品（含未公開）
- PUT  /works/{project_id}         更新對外展示欄位 + 分類關聯
- POST /works/reorder              批次重排 public_sort_order
- POST /works/{id}/featured        切換精選 ({"featured": true/false})
- POST /works/create               (Phase M-W) 建立作品 skeleton → 回傳 edit URL
- POST /works/{id}/edit-url        (Phase M-W) 產生既有作品的 showcase-edit token URL
- GET  /clients/lookup             (Phase M-W) 客戶 name-only 簡化列表（新增作品 modal）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session
from core.schemas_website import (
    ClientLookupItem, ClientLookupResponse, EditUrlResponse,
    ProjectAdminUpdate, ProjectReorder,
    WorkCreateRequest, WorkCreateResponse,
)
from db.models import Client, CrmProject
from routers.api_crm import _mint_showcase_edit_token
from services.website import project_service, rebuild_service

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
    await rebuild_service.mark_dirty()
    return {"ok": True}


@router.post("/works/reorder")
async def reorder_works(
    req: ProjectReorder,
    session: AsyncSession = Depends(admin_session),
):
    await project_service.reorder_projects(session, req.order)
    await rebuild_service.mark_dirty()
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
    await rebuild_service.mark_dirty()
    return {"ok": True, "featured": featured}


# ══════════════════════════════════════════════════════════
# Phase M-W：網站管理員獨立作品新增/編輯流程
#
# 網站管理員 role=website_admin 看不到 CRM Tab。這組 endpoint 讓他們能：
# 1. 建立作品（skeleton row 進 crm_projects，CRM 管理員後補財務/人力）
# 2. 取得 showcase-edit.html 的編輯 URL（iframe 嵌入官網管理 Tab）
# 3. 查客戶 name list（新增作品時選客戶）
# ══════════════════════════════════════════════════════════

# Skeleton 建立時預設狀態 — 日後 CRM 管理員可手動調整
_DEFAULT_WORK_STATUS = "進行中"


@router.post("/works/create", response_model=WorkCreateResponse)
async def create_work(
    req: WorkCreateRequest,
    session: AsyncSession = Depends(admin_session),
):
    """建立作品 skeleton，同步產生 showcase edit token 讓 UI 直接打開編輯器。"""
    from db.models_website import WebsiteProjectCategory
    project_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    session.add(CrmProject(
        id=project_id, name=req.name,
        client_id=req.client_id or None,
        status=_DEFAULT_WORK_STATUS,
        public=False, public_year=req.year, public_sort_order=0,
        created_at=now, updated_at=now,
    ))
    # 同 commit 寫入 category 關聯（含分類 + 標籤；表共用，後端不分 kind）
    for cid in (req.category_ids or []):
        session.add(WebsiteProjectCategory(project_id=project_id, category_id=cid))
    token, _sc = await _mint_showcase_edit_token(session, project_id, reuse_existing=False)
    await session.commit()
    return WorkCreateResponse(
        id=project_id, name=req.name,
        edit_url=f"/showcase-edit.html?token={token}",
    )


@router.post("/works/{project_id}/edit-url", response_model=EditUrlResponse)
async def get_work_edit_url(
    project_id: str,
    session: AsyncSession = Depends(admin_session),
):
    """取得既有作品的 showcase-edit.html 編輯 URL。若已有 token 則重用（冪等）。"""
    if not await session.get(CrmProject, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    token, _sc = await _mint_showcase_edit_token(session, project_id, reuse_existing=True)
    if session.dirty or session.new:
        await session.commit()
    return EditUrlResponse(edit_url=f"/showcase-edit.html?token={token}")


@router.get("/clients/lookup", response_model=ClientLookupResponse)
async def lookup_clients(session: AsyncSession = Depends(admin_session)):
    """回傳客戶 name-only 列表（新增作品 modal 選客戶用；不含聯絡方式等敏感資料）。"""
    rows = (await session.execute(
        select(Client.id, Client.short_name, Client.full_name)
        .order_by(Client.short_name, Client.full_name)
        .limit(500)
    )).all()
    items = [
        ClientLookupItem(id=r[0], name=(r[1] or r[2] or "").strip() or r[0])
        for r in rows
    ]
    return ClientLookupResponse(items=items)
