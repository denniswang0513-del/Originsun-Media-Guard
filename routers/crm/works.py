"""routers/crm/works.py — 1:N 作品子端點（Phase 2）。

一個 CRM 專案下可有多個官網作品（crm_project_showcase 列，id=work id）。
主作品（sc.id == sc.project_id）仍可走 showcase.py 的 project-scoped 舊端點
（語意 = 操作主作品）；本模組提供逐作品操作：

- GET   /projects/{project_id}/works          作品清單 + 聚合 summary（看板/收件匣用）
- POST  /projects/{project_id}/works          在既有專案下新增子作品（回 edit_url）
- POST  /works/{work_id}/publish              逐作品發布/下架切換
- PATCH /works/{work_id}/stage                逐作品官網階段（待製作/製作中/不上官網）
- POST  /works/{work_id}/generate-edit-token  逐作品 edit token（重發語義）
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from core.schemas_website import WorkChildCreateRequest, WorkChildCreateResponse

from ._shared import (router, _check_website_auth, _require_db, _get_factory, _now)
from .projects import _WEBSITE_PROD_STAGES, _work_items_for_project

try:
    from ._shared import CrmProject, CrmProjectShowcase
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同其他 crm 模組
    pass


async def _mark_dirty_safe(tag: str) -> None:
    """觸發對外網站 rebuild（debounce 60s）— 失敗不擋操作（比照 showcase.py 各端點）。"""
    try:
        from services.website import rebuild_service
        await rebuild_service.mark_dirty()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("[%s] mark_dirty 失敗: %s", tag, e)


@router.get("/projects/{project_id}/works")
async def list_project_works(project_id: str, request: Request):
    """專案的作品清單（主作品先）+ 聚合 summary（total/live/verified/skipped/all_live）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        items = await _work_items_for_project(session, project)
    from core.crm_logic import project_works_summary
    return {"items": items, "summary": project_works_summary(items)}


@router.post("/projects/{project_id}/works", response_model=WorkChildCreateResponse)
async def create_project_work(project_id: str, req: WorkChildCreateRequest, request: Request):
    """在既有專案下新增子作品 → 回編輯器 URL（結案看板「+ 新增影片作品」）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        from services.website.project_service import create_child_work
        sc, token = await create_child_work(session, project, req.title)
        await session.commit()
    return WorkChildCreateResponse(
        id=sc.id, title=sc.title or "",
        edit_url=f"/showcase-edit.html?token={token}",
    )


@router.post("/works/{work_id}/publish")
async def toggle_work_publish(work_id: str, request: Request):
    """逐作品發布/下架切換（= showcase publish 的 work 版；首發配號）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, work_id)
        if not sc:
            raise HTTPException(status_code=404, detail="找不到此作品")
        sc.published = not sc.published
        if sc.published:
            sc.published_at = sc.published_at or _now()
        sc.updated_at = _now()
        if sc.published and sc.number is None:
            from services.website.project_service import _next_public_number
            sc.number = await _next_public_number(session)
        # 主作品鏡射 public_*（子作品查無對應 project → 天然 no-op）
        from .showcase import _sync_showcase_to_public
        await _sync_showcase_to_public(session, sc)
        await session.commit()
        published = bool(sc.published)
        slug = (sc.slug or "").strip() or (
            str(sc.number) if sc.number is not None else None
        )
    await _mark_dirty_safe("work publish")
    return {"ok": True, "published": published, "slug": slug}


@router.patch("/works/{work_id}/stage")
async def update_work_stage(work_id: str, request: Request):
    """逐作品官網製作階段（待製作/製作中/不上官網；含逐作品「不上官網」排除）。"""
    _check_website_auth(request)
    _require_db()
    body = await request.json()
    stage = (body.get("stage") or "").strip()
    if stage not in _WEBSITE_PROD_STAGES:
        raise HTTPException(status_code=400, detail="無效的製作階段")
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, work_id)
        if not sc:
            raise HTTPException(status_code=404, detail="找不到此作品")
        sc.prod_stage = stage
        sc.updated_at = _now()
        # 主作品 dual-write 回 project 欄（Phase 4 停寫）
        if sc.id == sc.project_id:
            project = await session.get(CrmProject, sc.project_id)
            if project:
                project.website_prod_stage = stage
                project.updated_at = _now()
        await session.commit()
    return {"ok": True, "stage": stage}


@router.get("/works/{work_id}/edit-token")
async def get_work_edit_token(work_id: str, request: Request):
    """取得作品的 edit token（已有就重用、沒有才 mint）— delivery tab 分頁切換用，
    不作廢既有分享連結（重發請走 POST generate-edit-token）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        if not await session.get(CrmProjectShowcase, work_id):
            raise HTTPException(status_code=404, detail="找不到此作品")
        from .showcase import _mint_showcase_edit_token
        token, _sc = await _mint_showcase_edit_token(session, work_id, reuse_existing=True)
        await session.commit()
    return {"status": "ok", "token": token, "url": f"/showcase-edit.html?token={token}"}


@router.post("/works/{work_id}/generate-edit-token")
async def generate_work_edit_token(work_id: str, request: Request):
    """逐作品產生 showcase-edit token（永遠產新 = 分享連結重發語義）。

    只對既有作品發（404 守門）— 不做 get-or-create，避免打錯 id 長出垃圾列。
    """
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        if not await session.get(CrmProjectShowcase, work_id):
            raise HTTPException(status_code=404, detail="找不到此作品")
        from .showcase import _mint_showcase_edit_token
        token, _sc = await _mint_showcase_edit_token(session, work_id, reuse_existing=False)
        await session.commit()
    return {"status": "ok", "token": token, "url": f"/showcase-edit.html?token={token}"}
