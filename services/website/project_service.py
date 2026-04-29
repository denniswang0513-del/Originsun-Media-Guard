"""services/website/project_service.py
---
作品查詢、排序、公開切換邏輯。

操作 crm_projects 的 public_* 欄位 + website_project_categories 關聯表。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrmProject
from db.models_website import WebsiteCategory, WebsiteProjectCategory

logger = logging.getLogger(__name__)


def _youtube_thumbnail(video_id: Optional[str]) -> Optional[str]:
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg" if video_id else None


async def _categories_for_projects(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, list[str]]:
    """Batch fetch：一次 JOIN 取所有專案的分類 slug，避免 N+1。"""
    if not project_ids:
        return {}
    stmt = (
        select(WebsiteProjectCategory.project_id, WebsiteCategory.slug)
        .join(WebsiteCategory, WebsiteCategory.id == WebsiteProjectCategory.category_id)
        .where(WebsiteProjectCategory.project_id.in_(project_ids))
    )
    out: dict[str, list[str]] = defaultdict(list)
    for pid, slug in await session.execute(stmt):
        out[pid].append(slug)
    return out


def _slug_or_fallback(p: CrmProject) -> str:
    """slug 沒設時用 project id 前 8 字元 fallback。
    避免 '/works/' 空連結讓使用者點不進去；admin 自己填 slug 後立即生效。"""
    return (p.public_slug or "").strip() or (p.id or "")[:8]


def _to_public_dict(p: CrmProject, categories: Optional[list[str]] = None) -> dict:
    return {
        "slug": _slug_or_fallback(p),
        "title": p.public_title or p.name,
        "client": p.public_client,
        "youtube_id": p.public_youtube_id,
        "description": p.public_description,
        "year": p.public_year,
        "categories": categories or [],
        "thumbnail_url": _youtube_thumbnail(p.public_youtube_id),
        "featured": bool(p.public_featured),
    }


def _to_admin_dict(p: CrmProject, categories: Optional[list[str]] = None) -> dict:
    d = _to_public_dict(p, categories)
    d.update({
        "id": p.id,
        "name": p.name,
        "public": bool(p.public),
        "public_sort_order": p.public_sort_order or 0,
        "credits": p.public_credits or {},
        "published_at": p.public_published_at.isoformat() if p.public_published_at else None,
    })
    return d


async def list_public_projects(
    session: AsyncSession,
    category_slug: Optional[str] = None,
    page: int = 1,
    limit: int = 12,
) -> tuple[list[dict], int]:
    """列出對外公開的作品（分頁 + 分類篩選）。"""
    base = select(CrmProject).where(CrmProject.public.is_(True))

    if category_slug:
        cat_subq = select(WebsiteCategory.id).where(WebsiteCategory.slug == category_slug).scalar_subquery()
        base = base.join(
            WebsiteProjectCategory, WebsiteProjectCategory.project_id == CrmProject.id
        ).where(WebsiteProjectCategory.category_id == cat_subq)

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    stmt = base.order_by(
        CrmProject.public_sort_order.desc().nullslast(),
        CrmProject.public_published_at.desc().nullslast(),
    ).offset((page - 1) * limit).limit(limit)

    projects = list((await session.execute(stmt)).scalars())
    cat_map = await _categories_for_projects(session, [p.id for p in projects])
    return [_to_public_dict(p, cat_map.get(p.id, [])) for p in projects], total


async def get_public_project_by_slug(session: AsyncSession, slug: str) -> Optional[dict]:
    """單一公開作品詳情。slug 對得上 public_slug 直接拿；對不上就 fallback 到
    用 id 前 8 字元當 slug 找（搭配 _slug_or_fallback）。"""
    stmt = select(CrmProject).where(
        and_(CrmProject.public.is_(True), CrmProject.public_slug == slug)
    )
    project = (await session.execute(stmt)).scalar_one_or_none()
    if not project and len(slug) == 8:
        # fallback：把 slug 當 project id 前綴找
        from sqlalchemy import func as _fn
        stmt = select(CrmProject).where(
            and_(CrmProject.public.is_(True), _fn.substring(CrmProject.id, 1, 8) == slug)
        )
        project = (await session.execute(stmt)).scalar_one_or_none()
    if not project:
        return None

    cat_map = await _categories_for_projects(session, [project.id])
    data = _to_public_dict(project, cat_map.get(project.id, []))
    data["credits"] = project.public_credits or {}
    data["published_at"] = project.public_published_at.isoformat() if project.public_published_at else None
    return data


async def list_featured_projects(session: AsyncSession, limit: int = 6) -> list[dict]:
    """首頁精選：featured 優先，不足時補最新 public。"""
    stmt = select(CrmProject).where(
        and_(CrmProject.public.is_(True), CrmProject.public_featured.is_(True))
    ).order_by(
        CrmProject.public_sort_order.desc().nullslast(),
        CrmProject.public_published_at.desc().nullslast(),
    ).limit(limit)
    featured = list((await session.execute(stmt)).scalars())

    if len(featured) < limit:
        remaining = limit - len(featured)
        existing_ids = [p.id for p in featured]
        fb_stmt = select(CrmProject).where(CrmProject.public.is_(True))
        if existing_ids:
            fb_stmt = fb_stmt.where(CrmProject.id.notin_(existing_ids))
        fb_stmt = fb_stmt.order_by(
            CrmProject.public_published_at.desc().nullslast()
        ).limit(remaining)
        featured.extend((await session.execute(fb_stmt)).scalars())

    cat_map = await _categories_for_projects(session, [p.id for p in featured])
    return [_to_public_dict(p, cat_map.get(p.id, [])) for p in featured]


async def list_admin_projects(
    session: AsyncSession, include_non_public: bool = True
) -> list[dict]:
    """管理 Tab 列出所有作品（預設含未公開）。"""
    stmt = select(CrmProject)
    if not include_non_public:
        stmt = stmt.where(CrmProject.public.is_(True))
    stmt = stmt.order_by(CrmProject.updated_at.desc().nullslast())

    projects = list((await session.execute(stmt)).scalars())
    cat_map = await _categories_for_projects(session, [p.id for p in projects])
    return [_to_admin_dict(p, cat_map.get(p.id, [])) for p in projects]


_UPDATABLE_FIELDS = {
    "public", "public_slug", "public_title", "public_client", "public_youtube_id",
    "public_description", "public_credits", "public_year", "public_featured",
    "public_sort_order", "public_published_at",
}


async def update_project_public(
    session: AsyncSession,
    project_id: str,
    updates: dict,
    category_ids: Optional[list[int]] = None,
) -> bool:
    """更新 crm_projects.public_* 欄位 + 重置分類關聯。"""
    project = await session.get(CrmProject, project_id)
    if not project:
        return False

    for k, v in updates.items():
        if k in _UPDATABLE_FIELDS:
            setattr(project, k, v)

    if category_ids is not None:
        await session.execute(
            delete(WebsiteProjectCategory).where(WebsiteProjectCategory.project_id == project_id)
        )
        session.add_all([
            WebsiteProjectCategory(project_id=project_id, category_id=cid)
            for cid in category_ids
        ])

    await session.commit()
    return True


async def reorder_projects(session: AsyncSession, ordered_ids: list[str]) -> None:
    """批次更新 public_sort_order（靠前的優先）。"""
    total = len(ordered_ids)
    for idx, pid in enumerate(ordered_ids):
        await session.execute(
            update(CrmProject).where(CrmProject.id == pid).values(
                public_sort_order=total - idx
            )
        )
    await session.commit()


async def toggle_featured(session: AsyncSession, project_id: str, featured: bool) -> bool:
    """切換單一作品的精選狀態。"""
    result = await session.execute(
        update(CrmProject).where(CrmProject.id == project_id).values(public_featured=featured)
    )
    await session.commit()
    return result.rowcount > 0
