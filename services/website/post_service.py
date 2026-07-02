"""services/website/post_service.py
---
部落格文章 + 文章分類 CRUD + Notion 匯入 merge 邏輯。

設計：
- DB 是 source of truth，admin 編輯永遠勝。
- Notion 匯入只插入新文章；force=True 才覆寫（admin 主動選）。
- date_modified 在每次 update 自動更新（給 NewsArticle.dateModified）。
- old_urls / category_slugs / 衝突偵測都在這層處理，router 只負責 HTTP I/O。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import asc, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import (
    WebsitePost, WebsitePostCategory, WebsitePostCategoryLink,
)


# ── 共用輔助 ──

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_old_url(raw: str) -> Optional[str]:
    """把使用者貼進來的舊 URL 正規化成「相對路徑」用於 redirect map。

    可接受：絕對 URL、相對路徑、含/不含結尾 /。回傳 None 表示無效。
    """
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        try:
            parsed = urlparse(s)
            s = parsed.path or "/"
        except Exception:
            return None
    if not s.startswith("/"):
        s = "/" + s
    # strip 結尾 /（除了根 /）
    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    # 拒絕含控制字元 / 引號 / 空白
    if re.search(r"[\s\"'\x00-\x1f]", s):
        return None
    return s


def _normalize_old_urls(raw_list: list[str] | None) -> list[str]:
    if not raw_list:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_list:
        n = _normalize_old_url(raw)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _calc_read_time(body: list[dict] | None) -> Optional[int]:
    """根據 PostBlock[] 估閱讀時間（中文 300 字/分、英文 200 字/分）。"""
    if not body:
        return None
    text_chars = 0
    for block in body:
        t = block.get("type")
        if t == "paragraph" or t == "heading" or t == "quote":
            text_chars += len(block.get("text") or "")
        elif t == "list":
            for it in block.get("items") or []:
                text_chars += len(it)
    if text_chars == 0:
        return None
    # 粗估：把字數整體當中文計（英文混雜時略高估）
    return max(1, round(text_chars / 300))


# ── 序列化（Post → dict） ──

def _post_to_dict(p: WebsitePost, category_slugs: list[str]) -> dict[str, Any]:
    return {
        "id": p.id,
        "slug": p.slug,
        "title": p.title,
        "excerpt": p.excerpt,
        "cover_url": p.cover_url,
        "body": list(p.body or []),
        "category_slugs": category_slugs,
        "status": p.status,
        "published_at": p.published_at,
        "date_modified": p.date_modified,
        "read_time_min": p.read_time_min,
        "sort_order": p.sort_order,
        "notion_page_id": p.notion_page_id,
        "imported_from_notion_at": p.imported_from_notion_at,
        "seo_title": p.seo_title,
        "seo_description": p.seo_description,
        "og_image_url": p.og_image_url,
        "canonical_url": p.canonical_url,
        "noindex": p.noindex,
        "author_name": p.author_name,
        "author_url": p.author_url,
        "ai_allow_override": p.ai_allow_override,
        "old_urls": list(p.old_urls or []),
        "redirect_count": len(p.old_urls or []),
        "faqs": list(p.faqs or []),
    }


def _post_to_list_item(p: WebsitePost, category_slugs: list[str]) -> dict[str, Any]:
    """精簡型 — 給列表頁，不含 body / SEO 細節 reduce payload。"""
    return {
        "id": p.id,
        "slug": p.slug,
        "title": p.title,
        "excerpt": p.excerpt,
        "cover_url": p.cover_url,
        "category_slugs": category_slugs,
        "status": p.status,
        "published_at": p.published_at,
        "date_modified": p.date_modified,
        "sort_order": p.sort_order,
        "notion_page_id": p.notion_page_id,
        "redirect_count": len(p.old_urls or []),
    }


def _post_to_public_dict(p: WebsitePost, category_slugs: list[str]) -> dict[str, Any]:
    """對外 Astro 端的形狀 — 拿掉 admin-only 欄位。"""
    return {
        "slug": p.slug,
        "title": p.title,
        "excerpt": p.excerpt,
        "cover_url": p.cover_url,
        "category_slugs": category_slugs,
        "body": list(p.body or []),
        "published_at": p.published_at,
        "date_modified": p.date_modified,
        "read_time_min": p.read_time_min,
        "seo_title": p.seo_title,
        "seo_description": p.seo_description,
        "og_image_url": p.og_image_url,
        "canonical_url": p.canonical_url,
        "noindex": p.noindex,
        "author_name": p.author_name,
        "author_url": p.author_url,
        "ai_allow_override": p.ai_allow_override,
        "faqs": list(p.faqs or []),
    }


# ── slug 自動分配（純數字延續 Notion 慣例） ──

async def _max_slug_int(session: AsyncSession) -> int:
    """掃 DB 拿到最大的數字 slug。非數字 slug 忽略。

    Bulk import 時 caller 應該預取一次 + 在記憶體 increment（避免每篇重掃）。
    """
    rows = await session.execute(select(WebsitePost.slug))
    max_n = 0
    for (s,) in rows:
        try:
            n = int(s)
            if n > max_n:
                max_n = n
        except (TypeError, ValueError):
            continue
    return max_n


async def _next_slug(session: AsyncSession) -> str:
    return str(await _max_slug_int(session) + 1)


# ── Category 解析（slug → id，不存在自動新建） ──

async def _resolve_categories(
    session: AsyncSession, slugs: list[str], auto_create: bool = False,
) -> tuple[list[int], list[str]]:
    """把 category slug 解析成 id list。auto_create=True 時 missing 的會新建。

    回傳 (id_list, new_slugs_created)。
    """
    if not slugs:
        return [], []
    stmt = select(WebsitePostCategory).where(WebsitePostCategory.slug.in_(slugs))
    existing = {c.slug: c for c in (await session.execute(stmt)).scalars()}
    ids: list[int] = []
    new_slugs: list[str] = []
    for s in slugs:
        if s in existing:
            ids.append(existing[s].id)
        elif auto_create:
            cat = WebsitePostCategory(slug=s, label_zh=s, sort_order=999)
            session.add(cat)
            await session.flush()
            ids.append(cat.id)
            new_slugs.append(s)
            existing[s] = cat
    return ids, new_slugs


async def _link_categories(session: AsyncSession, post_id: int, category_ids: list[int]) -> None:
    """重設 post 的所有 category links（先刪舊、再插新）。"""
    await session.execute(
        delete(WebsitePostCategoryLink).where(WebsitePostCategoryLink.post_id == post_id)
    )
    for cid in category_ids:
        session.add(WebsitePostCategoryLink(post_id=post_id, category_id=cid))


async def _load_category_slugs_for_posts(
    session: AsyncSession, post_ids: list[int],
) -> dict[int, list[str]]:
    """批次拉一群 post 的 category_slugs（read-only lookup，不是 link）。"""
    if not post_ids:
        return {}
    stmt = (
        select(WebsitePostCategoryLink.post_id, WebsitePostCategory.slug)
        .join(WebsitePostCategory,
              WebsitePostCategory.id == WebsitePostCategoryLink.category_id)
        .where(WebsitePostCategoryLink.post_id.in_(post_ids))
        .order_by(WebsitePostCategory.sort_order)
    )
    out: dict[int, list[str]] = {pid: [] for pid in post_ids}
    for pid, slug in await session.execute(stmt):
        out[pid].append(slug)
    return out


# ── PostCategory CRUD ──

def _category_to_dict(c: WebsitePostCategory, post_count: int = 0) -> dict[str, Any]:
    return {
        "id": c.id,
        "slug": c.slug,
        "label_zh": c.label_zh,
        "label_en": c.label_en,
        "color": c.color,
        "sort_order": c.sort_order,
        "visible": c.visible,
        "post_count": post_count,
    }


async def list_categories(session: AsyncSession, visible_only: bool = False) -> list[dict]:
    stmt = select(WebsitePostCategory).order_by(asc(WebsitePostCategory.sort_order),
                                                  asc(WebsitePostCategory.id))
    if visible_only:
        stmt = stmt.where(WebsitePostCategory.visible.is_(True))
    cats = list((await session.execute(stmt)).scalars())

    # 一次性統計每個 category 的 post 數
    count_stmt = (
        select(WebsitePostCategoryLink.category_id, func.count())
        .group_by(WebsitePostCategoryLink.category_id)
    )
    counts = {cid: cnt for cid, cnt in await session.execute(count_stmt)}

    return [_category_to_dict(c, counts.get(c.id, 0)) for c in cats]


async def create_category(session: AsyncSession, data: dict) -> dict:
    cat = WebsitePostCategory(**data)
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return _category_to_dict(cat, 0)


async def update_category(session: AsyncSession, cat_id: int, data: dict) -> Optional[dict]:
    cat = await session.get(WebsitePostCategory, cat_id)
    if not cat:
        return None
    for k, v in data.items():
        setattr(cat, k, v)
    await session.commit()
    await session.refresh(cat)
    return _category_to_dict(cat, 0)


async def delete_category(session: AsyncSession, cat_id: int) -> bool:
    """刪分類 — junction CASCADE 自動清掉文章關聯（文章本身保留）。"""
    result = await session.execute(
        delete(WebsitePostCategory).where(WebsitePostCategory.id == cat_id)
    )
    await session.commit()
    return (result.rowcount or 0) > 0


# ── Post CRUD ──

async def list_posts(
    session: AsyncSession,
    status: Optional[str] = None,
    category_slug: Optional[str] = None,
    visible_only: bool = False,        # True = 只回 status='published' AND published_at<=now
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    base = select(WebsitePost)
    if status:
        base = base.where(WebsitePost.status == status)
    if visible_only:
        base = base.where(
            WebsitePost.status == "published",
            (WebsitePost.published_at.is_(None)) | (WebsitePost.published_at <= _now()),
        )
    if category_slug:
        base = base.join(WebsitePostCategoryLink,
                         WebsitePostCategoryLink.post_id == WebsitePost.id) \
                   .join(WebsitePostCategory,
                         WebsitePostCategory.id == WebsitePostCategoryLink.category_id) \
                   .where(WebsitePostCategory.slug == category_slug)

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    stmt = base.order_by(
        desc(WebsitePost.published_at.is_(None)),    # NULL 排後
        desc(WebsitePost.published_at),
        desc(WebsitePost.id),
    ).offset(offset).limit(limit)
    posts = list((await session.execute(stmt)).scalars())
    cat_map = await _load_category_slugs_for_posts(session, [p.id for p in posts])

    return [_post_to_list_item(p, cat_map.get(p.id, [])) for p in posts], total


async def list_public_posts(session: AsyncSession, limit: int = 500) -> list[dict]:
    """對外 Astro 用：只 published + 未來時間排除。"""
    stmt = (
        select(WebsitePost)
        .where(
            WebsitePost.status == "published",
            (WebsitePost.published_at.is_(None)) | (WebsitePost.published_at <= _now()),
        )
        .order_by(desc(WebsitePost.published_at), desc(WebsitePost.id))
        .limit(limit)
    )
    posts = list((await session.execute(stmt)).scalars())
    cat_map = await _load_category_slugs_for_posts(session, [p.id for p in posts])
    return [_post_to_public_dict(p, cat_map.get(p.id, [])) for p in posts]


async def get_post(session: AsyncSession, post_id: int) -> Optional[dict]:
    p = await session.get(WebsitePost, post_id)
    if not p:
        return None
    cats = (await _load_category_slugs_for_posts(session, [p.id])).get(p.id, [])
    return _post_to_dict(p, cats)


async def get_public_post_by_slug(session: AsyncSession, slug: str) -> Optional[dict]:
    """對外 Astro fetchPostBySlug。隱藏 draft / archived / 未來排程未到。"""
    stmt = select(WebsitePost).where(
        WebsitePost.slug == slug,
        WebsitePost.status == "published",
        (WebsitePost.published_at.is_(None)) | (WebsitePost.published_at <= _now()),
    )
    p = (await session.execute(stmt)).scalar_one_or_none()
    if not p:
        return None
    cats = (await _load_category_slugs_for_posts(session, [p.id])).get(p.id, [])
    return _post_to_public_dict(p, cats)


async def create_post(session: AsyncSession, data: dict, auto_create_categories: bool = False) -> dict:
    cat_slugs = data.pop("category_slugs", None) or []
    if not data.get("slug"):
        data["slug"] = await _next_slug(session)
    data["old_urls"] = _normalize_old_urls(data.get("old_urls"))
    data["read_time_min"] = _calc_read_time(data.get("body"))
    data["date_modified"] = _now()

    p = WebsitePost(**data)
    session.add(p)
    await session.flush()

    cat_ids, _ = await _resolve_categories(session, cat_slugs, auto_create=auto_create_categories)
    await _link_categories(session, p.id, cat_ids)

    await session.commit()
    await session.refresh(p)
    return _post_to_dict(p, cat_slugs)


async def update_post(
    session: AsyncSession, post_id: int, data: dict, auto_create_categories: bool = False,
) -> Optional[dict]:
    p = await session.get(WebsitePost, post_id)
    if not p:
        return None

    cat_slugs = data.pop("category_slugs", None)   # None = 不動，[] = 清空

    if "old_urls" in data:
        data["old_urls"] = _normalize_old_urls(data["old_urls"])
    if "body" in data:
        data["read_time_min"] = _calc_read_time(data["body"])

    for k, v in data.items():
        setattr(p, k, v)
    p.date_modified = _now()

    if cat_slugs is not None:
        cat_ids, _ = await _resolve_categories(session, cat_slugs, auto_create=auto_create_categories)
        await _link_categories(session, p.id, cat_ids)

    await session.commit()
    await session.refresh(p)
    final_cats = (await _load_category_slugs_for_posts(session, [p.id])).get(p.id, [])
    return _post_to_dict(p, final_cats)


async def delete_post(session: AsyncSession, post_id: int) -> bool:
    result = await session.execute(delete(WebsitePost).where(WebsitePost.id == post_id))
    await session.commit()
    return (result.rowcount or 0) > 0


# ── Redirects（聚合所有 post.old_urls） ──

async def list_redirects(session: AsyncSession) -> dict[str, str]:
    """聚合所有 visible post 的 old_urls → 新 URL。

    衝突（同 old_url 對應多個 post）：依 published_at 排序，最新的勝。
    """
    stmt = (
        select(WebsitePost.slug, WebsitePost.old_urls, WebsitePost.published_at,
               WebsitePost.status)
        .where(WebsitePost.status.in_(("published", "archived")))
        .order_by(asc(WebsitePost.published_at.is_(None)),
                  asc(WebsitePost.published_at))
    )
    out: dict[str, str] = {}
    for slug, old_urls, _published, _status in await session.execute(stmt):
        new_url = f"/news/{slug}"
        for u in old_urls or []:
            # 後寫入勝 → 最新（published_at 最大者）會最後 overwrite
            out[u] = new_url
    return out


# ── Notion 匯入：upsert 邏輯 ──

async def _upsert_notion_categories(
    session: AsyncSession, notion_categories: list[dict],
) -> list[str]:
    """同步 categories — 已存在依 label/color 更新；不存在新建。回傳新建 slug 清單。"""
    new_slugs: list[str] = []
    incoming_slugs = [c.get("slug") for c in notion_categories if c.get("slug")]
    if not incoming_slugs:
        return []
    existing = {c.slug: c for c in (await session.execute(
        select(WebsitePostCategory).where(WebsitePostCategory.slug.in_(incoming_slugs))
    )).scalars()}
    for ncat in notion_categories:
        slug = ncat.get("slug")
        if not slug:
            continue
        if slug in existing:
            cat = existing[slug]
            cat.label_zh = ncat.get("label_zh") or cat.label_zh
            cat.label_en = ncat.get("label_en") or cat.label_en
            cat.color = ncat.get("color") or cat.color
        else:
            session.add(WebsitePostCategory(
                slug=slug,
                label_zh=ncat.get("label_zh") or slug,
                label_en=ncat.get("label_en"),
                color=ncat.get("color"),
                sort_order=999,
            ))
            new_slugs.append(slug)
    await session.flush()
    return new_slugs


def _parse_notion_published_at(iso: str | None) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _overwrite_post_from_notion(existing: WebsitePost, np: dict) -> None:
    """force=True 時整篇覆寫 metadata + body — 保留 admin 的 status / old_urls / per-post SEO。"""
    existing.title = np.get("title") or existing.title
    existing.excerpt = np.get("excerpt")
    existing.cover_url = np.get("cover_url")
    existing.body = np.get("body") or []
    existing.published_at = _parse_notion_published_at(np.get("published_at"))
    existing.imported_from_notion_at = _now()
    existing.read_time_min = _calc_read_time(existing.body)
    existing.date_modified = _now()


def _new_post_from_notion(np: dict, slug: str) -> WebsitePost:
    return WebsitePost(
        slug=slug,
        title=np.get("title") or "Untitled",
        excerpt=np.get("excerpt"),
        cover_url=np.get("cover_url"),
        body=np.get("body") or [],
        published_at=_parse_notion_published_at(np.get("published_at")),
        status="draft",                          # 預設 draft，admin 過目才上線
        notion_page_id=np.get("notion_page_id"),
        imported_from_notion_at=_now(),
        date_modified=_now(),
        read_time_min=_calc_read_time(np.get("body")),
    )


async def upsert_from_notion(
    session: AsyncSession,
    notion_posts: list[dict],
    notion_categories: list[dict],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """把 notion_service 抽出的 raw posts/categories 寫進 DB。

    每個 notion post 必含：
        notion_page_id (str), title, slug, category (str slug), cover_url,
        excerpt, body (PostBlock[]), published_at (ISO str)

    notion_categories 必含：slug, label_zh, label_en, color

    匯入規則：
      - notion_page_id 不存在 → 插入（status=draft，admin 必須過目）
      - 已存在 + force=False → 跳過（DB 為真）
      - 已存在 + force=True → 整篇覆寫 metadata + body（保留 admin status/old_urls/SEO）

    Batch 策略（避免 N+1）：
      - 一次 SELECT 拿全部 notion_page_id 已 match 的 posts
      - 一次 SELECT + auto-create 拿全部 cat_slug → cat_id 的 map
      - _next_slug 用記憶體 counter 不重掃 DB
    """
    started = _now()
    inserted = skipped = overwritten = failed = 0
    warnings: list[str] = []

    # 1) Categories upsert
    new_cat_slugs: list[str] = await _upsert_notion_categories(session, notion_categories)

    # 2) 預取所有要 match 的 posts（一次 query 而非每篇一次）
    page_ids = [np.get("notion_page_id") for np in notion_posts if np.get("notion_page_id")]
    existing_by_pageid: dict[str, WebsitePost] = {}
    if page_ids:
        rows = (await session.execute(
            select(WebsitePost).where(WebsitePost.notion_page_id.in_(page_ids))
        )).scalars()
        existing_by_pageid = {p.notion_page_id: p for p in rows}

    # 3) 預取所有 distinct cat_slugs → id（含 auto-create 新分類）
    all_cat_slugs = list({np.get("category") for np in notion_posts if np.get("category")})
    cat_ids, created_by_post = await _resolve_categories(session, all_cat_slugs, auto_create=True)
    new_cat_slugs.extend(created_by_post)
    cat_id_by_slug: dict[str, int] = dict(zip(all_cat_slugs, cat_ids))

    # 4) Bulk slug counter — 不每篇重掃 DB
    next_slug_n = await _max_slug_int(session) + 1

    # 5) 逐篇 upsert
    for np in notion_posts:
        try:
            page_id = np.get("notion_page_id")
            if not page_id:
                warnings.append(f"跳過：Notion post 無 page_id ({np.get('title')})")
                continue

            existing = existing_by_pageid.get(page_id)
            if existing and not force:
                skipped += 1
                continue

            cat_slug = np.get("category")
            cat_ids_for_post = [cat_id_by_slug[cat_slug]] if cat_slug and cat_slug in cat_id_by_slug else []

            if existing:
                _overwrite_post_from_notion(existing, np)
                await _link_categories(session, existing.id, cat_ids_for_post)
                overwritten += 1
            else:
                slug = np.get("slug") or str(next_slug_n)
                if not np.get("slug"):
                    next_slug_n += 1
                p = _new_post_from_notion(np, slug)
                session.add(p)
                await session.flush()
                await _link_categories(session, p.id, cat_ids_for_post)
                inserted += 1
        except Exception as e:
            failed += 1
            warnings.append(f"匯入失敗：{np.get('title')} — {e}")

    await session.commit()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "overwritten": overwritten,
        "failed": failed,
        "new_categories": sorted(set(new_cat_slugs)),
        "warnings": warnings,
        "duration_ms": int((_now() - started).total_seconds() * 1000),
    }
