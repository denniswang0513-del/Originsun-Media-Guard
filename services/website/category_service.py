"""services/website/category_service.py
---
作品分類 CRUD + 排序 + 計數。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteCategory, WebsiteProjectCategory


def _to_admin_dict(c: WebsiteCategory, count: int = 0) -> dict:
    return {
        "id": c.id,
        "slug": c.slug,
        "name_zh": c.name_zh,
        "name_en": c.name_en,
        "description": c.description,
        "cover_image": c.cover_image,
        "sort_order": c.sort_order,
        "visible": c.visible,
        "kind": c.kind or "category",
        "project_count": count,
    }


def _to_public_dict(c: WebsiteCategory, count: int = 0) -> dict:
    return {
        "slug": c.slug,
        "name_zh": c.name_zh,
        "name_en": c.name_en,
        "count": count,
        "kind": c.kind or "category",
    }


async def _count_per_category(session: AsyncSession) -> dict[int, int]:
    """Map category_id → number of public projects in that category."""
    from db.models import CrmProject
    stmt = (
        select(WebsiteProjectCategory.category_id, func.count(CrmProject.id))
        .join(CrmProject, CrmProject.id == WebsiteProjectCategory.project_id)
        .where(CrmProject.public.is_(True))
        .group_by(WebsiteProjectCategory.category_id)
    )
    return {row[0]: row[1] for row in await session.execute(stmt)}


async def list_public_categories(session: AsyncSession) -> list[dict]:
    """對外：只取 visible=true，含公開作品計數。"""
    stmt = select(WebsiteCategory).where(
        WebsiteCategory.visible.is_(True)
    ).order_by(WebsiteCategory.sort_order, WebsiteCategory.id)
    cats = list((await session.execute(stmt)).scalars())
    counts = await _count_per_category(session)
    return [_to_public_dict(c, counts.get(c.id, 0)) for c in cats]


async def list_admin_categories(session: AsyncSession) -> list[dict]:
    """管理：所有分類（含隱藏），含全部作品計數。"""
    stmt = select(WebsiteCategory).order_by(WebsiteCategory.sort_order, WebsiteCategory.id)
    cats = list((await session.execute(stmt)).scalars())
    counts = await _count_per_category(session)
    return [_to_admin_dict(c, counts.get(c.id, 0)) for c in cats]


async def create_category(session: AsyncSession, data: dict) -> dict:
    cat = WebsiteCategory(
        slug=data["slug"],
        name_zh=data["name_zh"],
        name_en=data.get("name_en"),
        description=data.get("description"),
        cover_image=data.get("cover_image"),
        sort_order=data.get("sort_order", 0),
        visible=data.get("visible", True),
        kind=data.get("kind", "category"),
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return _to_admin_dict(cat, 0)


async def update_category(
    session: AsyncSession, category_id: int, updates: dict
) -> Optional[dict]:
    cat = await session.get(WebsiteCategory, category_id)
    if not cat:
        return None
    for k, v in updates.items():
        if hasattr(cat, k) and v is not None:
            setattr(cat, k, v)
    await session.commit()
    await session.refresh(cat)
    return _to_admin_dict(cat, 0)


async def delete_category(session: AsyncSession, category_id: int) -> bool:
    """刪除分類（會連帶清除 website_project_categories 關聯）。"""
    await session.execute(
        delete(WebsiteProjectCategory).where(WebsiteProjectCategory.category_id == category_id)
    )
    result = await session.execute(
        delete(WebsiteCategory).where(WebsiteCategory.id == category_id)
    )
    await session.commit()
    return result.rowcount > 0


async def reorder_categories(session: AsyncSession, ordered_ids: list[int]) -> None:
    """批次更新 sort_order。"""
    total = len(ordered_ids)
    for idx, cid in enumerate(ordered_ids):
        await session.execute(
            update(WebsiteCategory).where(WebsiteCategory.id == cid).values(
                sort_order=total - idx
            )
        )
    await session.commit()
