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
from core.crm_logic import showcase_edit_url
from core.schemas_website import (
    ClientLookupItem, ClientLookupResponse, EditUrlResponse,
    ProjectAdminUpdate, ProjectReorder,
    WorkChildCreateRequest, WorkChildCreateResponse,
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

# Skeleton 建立時預設狀態 — 作品集上架的本來就是已完成的成品，預設「歸檔」
# 比「製作」對齊使用情境；後續 CRM 管理員仍可手動調整。
_DEFAULT_WORK_STATUS = "歸檔"

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
    # 1:N：作品身分欄位正本在 showcase — year 同步落到 mint 出來的主作品列
    _sc.year = req.year
    await session.commit()
    return WorkCreateResponse(
        id=project_id, name=name,
        edit_url=showcase_edit_url(token),
    )


@router.post("/projects/{project_id}/works", response_model=WorkChildCreateResponse)
async def create_work_under_project(
    project_id: str,
    req: WorkChildCreateRequest,
    session: AsyncSession = Depends(admin_session),
):
    """收件匣版「同專案加作品」（1:N）— 與 CRM 端 POST /projects/{id}/works 同 service。"""
    project = await session.get(CrmProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    from services.website.project_service import create_child_work
    sc, token = await create_child_work(session, project, req.title)
    await session.commit()
    return WorkChildCreateResponse(
        id=sc.id, title=sc.title or "",
        edit_url=showcase_edit_url(token),
    )


def _work_has_content(sc) -> bool:
    """作品列是否已被填過內容（skeleton 清理的守門判定）。

    「有無內容」的定義收斂到 completeness 四項（影片/圖/說明/credits，含
    extra_videos/featured_image — 只填了附加影片的作品不可被當空殼刪）+
    completeness 沒看的 process_items。"""
    from services.website.project_service import work_completeness_dict
    return any(work_completeness_dict(sc).values()) or bool(
        sc.process_items and len(sc.process_items) > 0
    )


@router.delete("/works/{project_id}/if-skeleton")
async def delete_work_if_skeleton(
    project_id: str,
    session: AsyncSession = Depends(admin_session),
):
    """Overlay 關閉時前端呼叫：如果這筆是「沒填內容就被關掉」的 skeleton，刪掉避免留垃圾。

    id 自 1:N 起語意 = work id，兩種 skeleton：
    - **project-skeleton**（id 是 project id，works.js「新增作品」建的）：判定同舊版
      （name 仍 sentinel + public_* 全空 + showcase 沒內容）→ 連刪 project+sc+categories。
    - **work-skeleton**（id 是子作品 id）：標題仍是「{專案名}（N）」預設值且沒內容
      → 只刪 sc + categories + SEO + 翻譯狀態，**絕不動專案**（sc.id != sc.project_id 硬閘）。

    任一欄位有值 → 視為使用者已開始填內容，回 {deleted: false} 不動。
    （沒設 DB cascade，關聯表要手動清。）
    """
    from db.models import CrmProjectShowcase
    from db.models_website import WebsiteProjectCategory
    from sqlalchemy import delete as sa_delete

    p = await session.get(CrmProject, project_id)
    if not p:
        # 1:N：不是 project id → 試子作品 skeleton 清理
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc or sc.id == sc.project_id:   # 硬閘：主作品絕不走這條
            return {"deleted": False, "reason": "not_found"}
        import re
        title = (sc.title or "").strip()
        # 預設標題恆為「{專案名}（N）」；專案名可能在子作品建立後被改（主作品標題
        # 會 mirror 回 project.name），所以只認通用「…（N）」模式。
        # TODO(Phase 4)：改建立時 skeleton 標記（欄位或 title=NULL），淘汰模式判定
        if title and re.fullmatch(r".+（\d+）", title) is None:
            return {"deleted": False, "reason": "title_changed"}
        if _work_has_content(sc):
            return {"deleted": False, "reason": "has_showcase_content"}
        from services.website.project_service import delete_work_cascade
        await delete_work_cascade(session, sc)
        await session.commit()
        return {"deleted": True, "kind": "work"}

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
    if sc and _work_has_content(sc):
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
    """取得既有作品的 showcase-edit.html 編輯 URL。若已有 token 則重用（冪等）。

    id 自 1:N 起語意 = work id：先查 showcase（子作品 id 不在 crm_projects），
    查無再當舊 project id 檢查（_mint 會 get-or-create 主作品列）。"""
    from db.models import CrmProjectShowcase
    exists = (
        await session.get(CrmProjectShowcase, project_id)
        or await session.get(CrmProject, project_id)
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Work not found")
    token, _sc = await _mint_showcase_edit_token(session, project_id, reuse_existing=True)
    if session.dirty or session.new:
        await session.commit()
    return EditUrlResponse(edit_url=showcase_edit_url(token))


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
