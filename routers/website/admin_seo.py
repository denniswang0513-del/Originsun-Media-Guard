"""routers/website/admin_seo.py
---
管理端：SEO 內容 CRUD（FAQ / Testimonial / QuickFact）。

對應前端「🔍 SEO / AI SEO 管理」子視圖的 Card 2/3/4。
任一寫入觸發 rebuild_service.mark_dirty()，60s debounce 後重 build 對外網站。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, current_username, register_crud
from core.schemas_website import (
    WebsiteFAQCreate, WebsiteFAQUpdate,
    WebsiteTestimonialCreate, WebsiteTestimonialUpdate,
    WebsiteQuickFactCreate, WebsiteQuickFactUpdate,
    ProjectSeoUpdate,
)
from services.website import rebuild_service, seo_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-seo"])


register_crud(
    router,
    prefix="faqs", name="FAQ",
    list_fn=seo_service.list_faqs,
    create_fn=seo_service.create_faq,
    update_fn=seo_service.update_faq,
    delete_fn=seo_service.delete_faq,
    create_schema=WebsiteFAQCreate,
    update_schema=WebsiteFAQUpdate,
    on_change=rebuild_service.mark_dirty,
)

register_crud(
    router,
    prefix="testimonials", name="Testimonial",
    list_fn=seo_service.list_testimonials,
    create_fn=seo_service.create_testimonial,
    update_fn=seo_service.update_testimonial,
    delete_fn=seo_service.delete_testimonial,
    create_schema=WebsiteTestimonialCreate,
    update_schema=WebsiteTestimonialUpdate,
    on_change=rebuild_service.mark_dirty,
)

register_crud(
    router,
    prefix="quick_facts", name="Quick fact",
    list_fn=seo_service.list_quick_facts,
    create_fn=seo_service.create_quick_fact,
    update_fn=seo_service.update_quick_fact,
    delete_fn=seo_service.delete_quick_fact,
    create_schema=WebsiteQuickFactCreate,
    update_schema=WebsiteQuickFactUpdate,
    on_change=rebuild_service.mark_dirty,
)


# ══════════════════════════════════════════════════════════
# 作品級 SEO / AI SEO Pipeline（4 endpoint）
# ══════════════════════════════════════════════════════════
# 給 admin / 未來 Claude session 跑「audit → draft → PATCH → approve」流程。
# 不走 register_crud — 1:1 by project_id、需要 query crm_projects 拼 audit 資料。


@router.get("/seo/projects/audit")
async def seo_projects_audit(session: AsyncSession = Depends(admin_session)):
    """所有 public 作品 + completeness 評分 + needs_ai_review。

    Claude pipeline 入口 — 從這裡看哪些缺、優先順序。
    """
    return {"items": await seo_service.list_seo_audit(session)}


@router.get("/seo/projects/{project_id}/draft")
async def seo_project_draft(
    project_id: str, session: AsyncSession = Depends(admin_session),
):
    """拿單一作品 SEO 生成 context（含現有 SEO + 公開欄位 + credits）。

    Claude / admin 拿這個產 draft，再 PATCH 回去。
    """
    ctx = await seo_service.get_project_seo_draft_context(session, project_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="作品不存在或未公開")
    return ctx


@router.patch("/seo/projects/{project_id}")
async def seo_project_update(
    project_id: str,
    req: ProjectSeoUpdate,
    request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """寫入 SEO 內容（upsert）。

    自動 set last_ai_review_at + by；不動 needs_ai_review（讓 admin 看完才 approve）。
    觸發 mark_dirty → 60s debounce → rebuild → 對外網站立即更新。
    """
    by = current_username(request)
    payload = req.model_dump(exclude_unset=True)  # Pydantic 已遞迴展開 ProjectSeoKeyFact/FAQ → dict
    result = await seo_service.update_project_seo(session, project_id, payload, by=by)
    await rebuild_service.mark_dirty()
    return result


@router.post("/seo/projects/{project_id}/approve")
async def seo_project_approve(
    project_id: str, session: AsyncSession = Depends(admin_session),
):
    """admin 看過 draft 後核准 — needs_ai_review=False（從 audit 列表移除）。"""
    ok = await seo_service.approve_project_seo(session, project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="此作品尚無 SEO 內容（先 PATCH 才能 approve）")
    await rebuild_service.mark_dirty()
    return {"ok": True}


# ══════════════════════════════════════════════════════════
# AI SEO Runner（排程 + 立即執行）
# ══════════════════════════════════════════════════════════
# Runner 透過 `claude --print` headless 模式呼叫 Claude（吃 Max 訂閱額度），
# audit → draft → PATCH 全自動。排程 cron 由 core/scheduler.py 觸發。

from services.website import seo_runner


@router.get("/seo/runner/settings")
async def seo_runner_get_settings(session: AsyncSession = Depends(admin_session)):
    """讀排程設定 + 上次執行結果。"""
    return await seo_runner.get_runner_settings(session)


@router.put("/seo/runner/settings")
async def seo_runner_update_settings(
    payload: dict, request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """更新排程設定（enabled / cron / batch_size）。"""
    try:
        return await seo_runner.update_runner_settings(
            session, payload, by=current_username(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/seo/runner/run")
async def seo_runner_run_now(
    request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """立即執行 — 跑整個 audit list，受 batch_size 限制。可帶 ?dry_run=true 預覽。"""
    dry = request.query_params.get("dry_run", "").lower() in {"1", "true"}
    settings = await seo_runner.get_runner_settings(session)
    result = await seo_runner.run_pipeline(
        session, batch_size=settings["batch_size"], dry_run=dry,
    )
    if not dry and result.get("processed", 0) > 0:
        await rebuild_service.mark_dirty()
    return result


@router.post("/seo/projects/{project_id}/run")
async def seo_runner_run_one(
    project_id: str, session: AsyncSession = Depends(admin_session),
):
    """單筆執行 — showcase-edit「立即跑此作品」按鈕用。"""
    result = await seo_runner.run_pipeline(session, target_project_id=project_id)
    if result.get("processed", 0) > 0:
        await rebuild_service.mark_dirty()
    return result
