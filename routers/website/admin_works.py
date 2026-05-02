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
- DELETE /works/{id}/if-skeleton   (Phase M-W) 編輯 overlay 關閉時清除沒填內容的 skeleton
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

# Skeleton 建立時預設狀態 — 作品集上架的本來就是已完成的成品，預設「已結案」
# 比「進行中」對齊使用情境；後續 CRM 管理員仍可手動調整。
_DEFAULT_WORK_STATUS = "已結案"

# 跳過小表單流程下沒填名稱用的 sentinel；/works/{id}/if-skeleton 用這個值判定是否為
# 「使用者開了 overlay 但什麼都沒填就關掉」的孤兒紀錄。
_SKELETON_NAME = "（未命名作品）"


@router.post("/works/create", response_model=WorkCreateResponse)
async def create_work(
    req: WorkCreateRequest,
    session: AsyncSession = Depends(admin_session),
):
    """建立作品 skeleton，同步產生 showcase edit token 讓 UI 直接打開編輯器。"""
    from db.models_website import WebsiteProjectCategory
    project_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    name = (req.name or "").strip() or _SKELETON_NAME
    # crm_projects.client_id 是 NOT NULL（soft FK，沒實際 FK constraint）— 使用者
    # 跳過小表單沒選客戶時，塞空字串而不是 None；使用者進編輯頁可填，if-skeleton
    # 清理時也會被連帶刪掉，不會留 client_id="" 的孤兒紀錄。
    client_id = (req.client_id or "").strip()
    session.add(CrmProject(
        id=project_id, name=name,
        client_id=client_id,
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
        id=project_id, name=name,
        edit_url=f"/showcase-edit.html?token={token}",
    )


@router.delete("/works/{project_id}/if-skeleton")
async def delete_work_if_skeleton(
    project_id: str,
    session: AsyncSession = Depends(admin_session),
):
    """Overlay 關閉時前端呼叫：如果這筆是「沒填內容就被關掉」的 skeleton，刪掉避免留垃圾。

    判定條件（全部成立才刪）：
      - crm_projects.name 仍是 sentinel（使用者沒按儲存改名）
      - public_title / public_youtube_id / public_description 全部空
      - public_credits_text 也空、public_credits 也空
      - 對應 crm_project_showcase row 也沒填內容（cover/desc/video/gallery/credits）

    任一欄位有值 → 視為使用者已開始填內容，回 {deleted: false} 不動。

    刪除時連帶清除 crm_project_showcase + website_project_categories 兩張關聯表
    （沒設 cascade，要手動）。
    """
    from db.models import CrmProjectShowcase
    from db.models_website import WebsiteProjectCategory
    from sqlalchemy import delete as sa_delete

    p = await session.get(CrmProject, project_id)
    if not p:
        return {"deleted": False, "reason": "not_found"}

    if (p.name or "").strip() != _SKELETON_NAME:
        return {"deleted": False, "reason": "name_changed"}
    if (
        (p.public_title or "").strip()
        or (p.public_youtube_id or "").strip()
        or (p.public_description or "").strip()
        or (p.public_credits_text or "").strip()
        or (p.public_credits and len(p.public_credits) > 0)
    ):
        return {"deleted": False, "reason": "has_public_content"}

    sc = await session.get(CrmProjectShowcase, project_id)
    if sc and (
        (sc.cover_url or "").strip()
        or (sc.description or "").strip()
        or (sc.video_url or "").strip()
        or (sc.credits_text or "").strip()
        or (sc.gallery and len(sc.gallery) > 0)
        or (sc.process_items and len(sc.process_items) > 0)
        or (sc.credits and len(sc.credits) > 0)
    ):
        return {"deleted": False, "reason": "has_showcase_content"}

    if sc:
        await session.delete(sc)
    await session.execute(
        sa_delete(WebsiteProjectCategory).where(
            WebsiteProjectCategory.project_id == project_id
        )
    )
    await session.delete(p)
    await session.commit()
    return {"deleted": True}


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
