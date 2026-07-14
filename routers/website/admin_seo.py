"""routers/website/admin_seo.py
---
管理端：SEO 內容 CRUD（FAQ / Testimonial / QuickFact）。

對應前端「🔍 SEO / AI SEO 管理」子視圖的 Card 2/3/4。
任一寫入觸發 rebuild_service.mark_dirty()，60s debounce 後重 build 對外網站。
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime

from ._common import admin_session, current_username, register_crud
from routers.website.admin_posts import _ALLOWED_IMG_EXT, _UPLOAD_BASE, _save_image_as_webp
from core.schemas_website import (
    WebsiteFAQCreate, WebsiteFAQUpdate,
    WebsiteTestimonialCreate, WebsiteTestimonialUpdate,
    WebsiteQuickFactCreate, WebsiteQuickFactUpdate,
    WebsiteAwardCreate, WebsiteAwardUpdate, WebsiteAwardBulkImport,
    NavItemCreate, NavItemUpdate,
    ProjectSeoUpdate,
    WebsiteSeriesCreate, WebsiteSeriesUpdate, WebsiteSeriesMembersPayload,
)
from services.website import nav_service, rebuild_service, seo_service, series_service

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

register_crud(
    router,
    prefix="awards", name="Award",
    list_fn=seo_service.list_awards,
    create_fn=seo_service.create_award,
    update_fn=seo_service.update_award,
    delete_fn=seo_service.delete_award,
    create_schema=WebsiteAwardCreate,
    update_schema=WebsiteAwardUpdate,
    on_change=rebuild_service.mark_dirty,
)


@router.post("/awards/bulk_import")
async def awards_bulk_import(
    req: WebsiteAwardBulkImport,
    session: AsyncSession = Depends(admin_session),
):
    """貼整段「歷年作品（獎項）」純文字 → film-centric 解析 → 建 WebsiteAward rows。

    body: {text, dry_run=true, now_year?}
      - dry_run=true（預設）：回 {dry_run, works, total_works, total_lines, warnings}
        不寫 DB（給前端「預覽」用）。
      - dry_run=false：建 row 後回 {dry_run, created, total_works, warnings} + mark_dirty。

    解析規則見 seo_service.parse_awards_bulk。是 film-centric 專用端點、不走 register_crud。
    """
    now_year = req.now_year or datetime.now().year
    result = await seo_service.bulk_import_awards(
        session, req.text, now_year=now_year, dry_run=req.dry_run,
    )
    if not req.dry_run and result.get("created", 0) > 0:
        await rebuild_service.mark_dirty()
    return result

# 頂部導覽選單（label / href / 排序 / 顯示隱藏）— admin「🧭 導覽選單」子視圖。
# 任一寫入 mark_dirty → rebuild → Header.astro 重 fetch /api/website/nav。
register_crud(
    router,
    prefix="nav", name="Nav item",
    list_fn=nav_service.list_nav,
    create_fn=nav_service.create_nav,
    update_fn=nav_service.update_nav,
    delete_fn=nav_service.delete_nav,
    create_schema=NavItemCreate,
    update_schema=NavItemUpdate,
    on_change=rebuild_service.mark_dirty,
)

# 作品系列（跨專案策展集合）— admin 作品子視圖「🎬 作品系列」卡。
# ⚠ slug 是 URL 永久承諾（/works/series/{slug}），發布後勿改。
register_crud(
    router,
    prefix="series", name="Series",
    list_fn=series_service.list_series,
    create_fn=series_service.create_series,
    update_fn=series_service.update_series,
    delete_fn=series_service.delete_series,
    create_schema=WebsiteSeriesCreate,
    update_schema=WebsiteSeriesUpdate,
    on_change=rebuild_service.mark_dirty,
)


@router.put("/series/{series_id}/members")
async def set_series_members(
    series_id: int,
    payload: WebsiteSeriesMembersPayload,
    session: AsyncSession = Depends(admin_session),
):
    """系列成員全量替換（排序/移除同一支端點）：work_ids 依序 = series_order。"""
    result = await series_service.set_series_members(session, series_id, payload.work_ids)
    await rebuild_service.mark_dirty()
    return result


@router.post("/series/{series_id}/generate-description")
async def generate_series_description(
    series_id: int,
    session: AsyncSession = Depends(admin_session),
):
    """AI 生成系列介紹（資料來源 = 成員作品標題/介紹；claude --print，最長 3 分鐘）。
    只回建議不落庫 — 前端填進 textarea，owner 按「儲存介紹/封面」才寫入。"""
    return await series_service.generate_description(session, series_id)


@router.post("/series/upload-cover")
async def upload_series_cover(
    file: UploadFile = File(...),
    _session: AsyncSession = Depends(admin_session),
):
    """上傳系列封面 → /uploads/series/{uuid}.webp（自動轉 WebP + 長邊縮到 1600px）。"""
    ext = (os.path.splitext(file.filename or "image.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="圖片大於 20MB")
    upload_dir = os.path.join(_UPLOAD_BASE, "series")
    os.makedirs(upload_dir, exist_ok=True)
    saved = _save_image_as_webp(content, upload_dir, uuid.uuid4().hex[:12], max_side=1600)
    rel = os.path.relpath(saved, _UPLOAD_BASE).replace("\\", "/")
    return {"url": f"/uploads/{rel}"}


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
    """立即執行整個 audit list（受 batch_size 限制）。
    dry_run=true → 同步回預覽；否則 → 非同步背景跑（立即回 {started}），前端輪詢
    /seo/runner/settings 的 running/progress/last_run_at（避免長同步請求被 cloudflared 切斷）。"""
    dry = request.query_params.get("dry_run", "").lower() in {"1", "true"}
    settings = await seo_runner.get_runner_settings(session)
    if dry:
        return await seo_runner.run_pipeline(
            session, batch_size=settings["batch_size"], dry_run=True,
        )
    return await seo_runner.trigger_batch(session, batch_size=settings["batch_size"])


@router.post("/seo/projects/{project_id}/run")
async def seo_runner_run_one(
    project_id: str, session: AsyncSession = Depends(admin_session),
):
    """單筆執行 — showcase-edit「立即跑此作品」按鈕用。"""
    result = await seo_runner.run_pipeline(session, target_project_id=project_id)
    if result.get("processed", 0) > 0:
        await rebuild_service.mark_dirty()
    return result


# ── Internal forward endpoints（NAS website-api → master）──────────
# claude.exe 只在 master Windows 上能跑（吃用戶 Max 訂閱）。NAS container
# 是 python:3.11-slim 沒 claude，也沒登入 Anthropic — 收到 admin 請求後
# relay 到這裡。X-Internal-Key 認證 + internal DB session 已上移到 _common（多 router 共用）。
from ._common import _check_internal_key, _admin_session_for_internal  # noqa: F401


@router.post("/internal/seo/run")
async def seo_runner_run_internal(request: Request):
    """NAS website-api 把整批 run 轉給 master。master 背景跑、立即回（避免 NAS relay
    與 cloudflared 長時間阻塞）；mark_dirty 由背景 runner 在跑完後處理。"""
    _check_internal_key(request)
    try:
        batch_size = int(request.query_params.get("batch_size", "10"))
    except ValueError:
        batch_size = 10
    if not seo_runner.start_batch_bg(batch_size):
        return {"status": "busy"}
    return {"status": "started"}


@router.post("/internal/seo/projects/{project_id}/run")
async def seo_runner_run_one_internal(project_id: str, request: Request):
    """NAS website-api 把單筆 run 轉給 master。"""
    _check_internal_key(request)
    async with _admin_session_for_internal() as session:
        result = await seo_runner.run_pipeline(session, target_project_id=project_id)
        if result.get("processed", 0) > 0:
            await rebuild_service.mark_dirty()
        return result


# ══════════════════════════════════════════════════════════
# 文章版 AI SEO（對齊作品集，走 post_seo_runner）
# ══════════════════════════════════════════════════════════

from services.website import post_seo_runner


@router.get("/seo/posts/audit")
async def post_seo_audit(session: AsyncSession = Depends(admin_session)):
    """文章 SEO 一覽 — 每篇 completeness(0-4) + needs_ai flag。"""
    return {"items": await post_seo_runner.list_post_seo_audit(session)}


@router.get("/seo/posts/runner/settings")
async def post_seo_runner_get_settings(session: AsyncSession = Depends(admin_session)):
    return await post_seo_runner.get_runner_settings(session)


@router.put("/seo/posts/runner/settings")
async def post_seo_runner_update_settings(
    payload: dict, request: Request,
    session: AsyncSession = Depends(admin_session),
):
    try:
        return await post_seo_runner.update_runner_settings(
            session, payload, by=current_username(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/seo/posts/runner/run")
async def post_seo_runner_run_now(
    request: Request, session: AsyncSession = Depends(admin_session),
):
    """整批：dry_run=1 同步回預覽；否則非同步背景跑（立即回 {started}）。"""
    dry = request.query_params.get("dry_run", "").lower() in {"1", "true"}
    settings = await post_seo_runner.get_runner_settings(session)
    if dry:
        return await post_seo_runner.run_pipeline(
            session, batch_size=settings["batch_size"], dry_run=True,
        )
    return await post_seo_runner.trigger_batch(session, batch_size=settings["batch_size"])


@router.post("/seo/posts/{post_id}/generate")
async def post_seo_generate(
    post_id: int, session: AsyncSession = Depends(admin_session),
):
    """單篇生成 — 編輯器「🤖 AI 生成 SEO」按鈕。回傳給編輯器填，不自動存 DB。"""
    return await post_seo_runner.generate_for_post(session, post_id)


# ── Internal forward（NAS → master，跑 claude）──

@router.post("/internal/seo/posts/run")
async def post_seo_run_internal(request: Request):
    _check_internal_key(request)
    try:
        batch_size = int(request.query_params.get("batch_size", "10"))
    except ValueError:
        batch_size = 10
    if not post_seo_runner.start_batch_bg(batch_size):
        return {"status": "busy"}
    return {"status": "started"}


@router.post("/internal/seo/posts/{post_id}/run")
async def post_seo_run_one_internal(post_id: int, request: Request):
    _check_internal_key(request)
    async with _admin_session_for_internal() as session:
        result = await post_seo_runner.run_pipeline(session, target_post_id=post_id)
        if result.get("processed", 0) > 0:
            await rebuild_service.mark_dirty()
        return result


@router.post("/internal/seo/posts/{post_id}/generate")
async def post_seo_generate_internal(post_id: int, request: Request):
    _check_internal_key(request)
    async with _admin_session_for_internal() as session:
        return await post_seo_runner._generate_local(session, post_id)
