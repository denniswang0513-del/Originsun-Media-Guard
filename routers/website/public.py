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
    category_service, credit_service, initiative_service, inquiry_service,
    nav_service, notify_service, post_service, project_service, seo_service,
    service_service, settings_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website", tags=["website-public"])


@router.get("/works")
async def list_works(
    request: Request,
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(12, ge=1, le=500),  # 上限 500：SSG build 一次撈全部；正常前端 ≤ 50
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


@router.get("/hero")
async def list_hero(
    request: Request,
    limit: int = Query(10, ge=1, le=12),  # 對齊前端 LIMITS.HOME_HERO
    session: AsyncSession = Depends(public_session),
):
    # 首頁最上方 Hero 輪播：admin 在「首頁設定」挑選 + 排序（home.hero_work_ids）；
    # 未設定則 fallback 精選作品。build 期由 index.astro fetchHero() 呼叫。
    rate_limit(request, max_per_minute=60)
    items = await project_service.list_hero_projects(session, limit=limit)
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
        from sqlalchemy import func
        from db.models import CrmStaff
        # 官網覆寫優先（website_*），空則 fallback 到 CRM 正本。回傳 key 與舊版一致
        # （name/role/photo_url/bio）→ about.astro / ITeamMember 不需改。
        # 排序：website_sort_order（NULL 視為 0）, id。
        stmt = (
            select(CrmStaff)
            .where(CrmStaff.show_on_website.is_(True))
            .order_by(func.coalesce(CrmStaff.website_sort_order, 0), CrmStaff.id)
        )
        staff = list((await session.execute(stmt)).scalars())
        return {"items": [
            {
                "id": s.id,
                "name": s.name,
                "role": s.website_title or s.role,
                "bio": s.website_bio or s.bio,
                "photo_url": s.website_photo_url or s.photo_url,
            }
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
    # 導覽選單（visible=true ORDER BY sort_order）— Header.astro 透過 meta.nav 讀，
    # 省一次 build 期 round-trip。空 list → Header 用硬寫 7 筆 fallback。
    meta["nav"] = await nav_service.list_nav(session, visible_only=True)
    return meta


@router.get("/faqs")
async def list_public_faqs(request: Request, session: AsyncSession = Depends(public_session)):
    rate_limit(request, max_per_minute=60)
    return {"items": await seo_service.list_faqs(session, visible_only=True)}


@router.get("/testimonials")
async def list_public_testimonials(request: Request, session: AsyncSession = Depends(public_session)):
    rate_limit(request, max_per_minute=60)
    return {"items": await seo_service.list_testimonials(session, visible_only=True)}


@router.get("/quick_facts")
async def list_public_quick_facts(request: Request, session: AsyncSession = Depends(public_session)):
    rate_limit(request, max_per_minute=60)
    return {"items": await seo_service.list_quick_facts(session, visible_only=True)}


@router.get("/awards")
async def list_public_awards(request: Request, session: AsyncSession = Depends(public_session)):
    """站級獎項紀錄（visible=true）— /portfolio 頁面頂部「Honors & Awards」用。"""
    rate_limit(request, max_per_minute=60)
    return {"items": await seo_service.list_awards(session, visible_only=True)}


@router.get("/initiatives")
async def list_public_initiatives(request: Request, session: AsyncSession = Depends(public_session)):
    """公益合作 / 創作計畫 案例（visible=true，解析作品連動）。

    ?line=impact|lab 過濾；不給則回兩條線都有（前端再分組）。
    """
    rate_limit(request, max_per_minute=60)
    line = (request.query_params.get("line") or "").strip()
    if line in ("impact", "lab"):
        return {"items": await initiative_service.list_public(session, line)}
    impact = await initiative_service.list_public(session, "impact")
    lab = await initiative_service.list_public(session, "lab")
    return {"impact": impact, "lab": lab, "items": impact + lab}


@router.get("/nav")
async def list_public_nav(request: Request, session: AsyncSession = Depends(public_session)):
    """頂部導覽選單（visible=true ORDER BY sort_order）— Header.astro fetch。

    空 list → Header.astro fallback 到硬寫的 7 筆 navItems（對外網站零變化）。
    """
    rate_limit(request, max_per_minute=60)
    return {"items": await nav_service.list_nav(session, visible_only=True)}


@router.get("/credit_roles")
async def list_public_credit_roles(request: Request, session: AsyncSession = Depends(public_session)):
    """對外可見的職位庫（visible=true）— Astro / showcase-edit.html 都會用。"""
    rate_limit(request, max_per_minute=60)
    return {"items": await credit_service.list_roles(session, visible_only=True)}


# ── Posts（DB-as-truth，取代舊 posts.json） ──

@router.get("/posts")
async def list_public_posts(
    request: Request,
    session: AsyncSession = Depends(public_session),
):
    """對外 Astro build 撈所有 published 文章（含未來時間排除）。"""
    rate_limit(request, max_per_minute=60)
    return {"items": await post_service.list_public_posts(session)}


@router.get("/posts/{slug}")
async def get_public_post(
    slug: str, request: Request,
    session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    p = await post_service.get_public_post_by_slug(session, slug)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    return p


@router.get("/post_categories")
async def list_public_post_categories(
    request: Request, session: AsyncSession = Depends(public_session),
):
    rate_limit(request, max_per_minute=60)
    return {"items": await post_service.list_categories(session, visible_only=True)}


@router.get("/redirects")
async def list_public_redirects(
    request: Request, session: AsyncSession = Depends(public_session),
):
    """聚合所有 visible post + public works 的 old URL → 新 URL 對應表。

    Astro build 期 fetch 一次塞進 astro.config.mjs `redirects`；
    publish_update.sync_redirects_to_nas() 也撈這個生成 nginx config。

    /news/* 跟 /works/* 命名空間不重疊，merge 後不會撞 key。
    """
    rate_limit(request, max_per_minute=60)
    from services.website import redirect_service
    legacy = await redirect_service.list_redirects(session)   # 頁面級 legacy 轉址
    posts = await post_service.list_redirects(session)
    works = await project_service.list_redirects(session)
    # 衝突偵測：/news/* vs /works/* 不該重疊，重疊代表資料異常 — log 不阻擋
    overlap = posts.keys() & works.keys()
    if overlap:
        logger.warning("[redirects] posts × works key overlap: %s — works override", sorted(overlap))
    # 優先序：作品 > 文章 > legacy（真實內容 slug 勝過手動 legacy 對照）
    items = {**legacy, **posts, **works}
    return {"items": items, "count": len(items)}


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
