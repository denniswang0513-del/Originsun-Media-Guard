"""services/website/service_service.py
---
服務項目 CRUD（首頁「服務」區塊用）。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteCategory, WebsiteService


def _to_public_dict(s: WebsiteService, related_slug: Optional[str] = None) -> dict:
    return {
        "slug": s.slug,
        "title": s.title,
        "icon": s.icon,
        "short_desc": s.short_desc,
        "full_desc": s.full_desc,
        "cover_image": s.cover_image,
        "related_category_slug": related_slug,
        "title_en": s.title_en,
        "short_desc_en": s.short_desc_en,
        "full_desc_en": s.full_desc_en,
    }


def _to_admin_dict(s: WebsiteService) -> dict:
    return {
        "id": s.id,
        "slug": s.slug,
        "title": s.title,
        "icon": s.icon,
        "short_desc": s.short_desc,
        "full_desc": s.full_desc,
        "cover_image": s.cover_image,
        "related_category_id": s.related_category_id,
        "sort_order": s.sort_order,
        "visible": s.visible,
        "title_en": s.title_en,
        "short_desc_en": s.short_desc_en,
        "full_desc_en": s.full_desc_en,
    }


async def _category_id_to_slug(session: AsyncSession) -> dict[int, str]:
    stmt = select(WebsiteCategory.id, WebsiteCategory.slug)
    return {row[0]: row[1] for row in await session.execute(stmt)}


async def list_public_services(session: AsyncSession) -> list[dict]:
    stmt = select(WebsiteService).where(
        WebsiteService.visible.is_(True)
    ).order_by(WebsiteService.sort_order, WebsiteService.id)
    services = list((await session.execute(stmt)).scalars())
    cat_map = await _category_id_to_slug(session)
    return [_to_public_dict(s, cat_map.get(s.related_category_id)) for s in services]


async def list_admin_services(session: AsyncSession) -> list[dict]:
    stmt = select(WebsiteService).order_by(WebsiteService.sort_order, WebsiteService.id)
    return [_to_admin_dict(s) for s in (await session.execute(stmt)).scalars()]


async def create_service(session: AsyncSession, data: dict) -> dict:
    svc = WebsiteService(
        slug=data["slug"],
        title=data["title"],
        icon=data.get("icon"),
        short_desc=data.get("short_desc"),
        full_desc=data.get("full_desc"),
        cover_image=data.get("cover_image"),
        related_category_id=data.get("related_category_id"),
        sort_order=data.get("sort_order", 0),
        visible=data.get("visible", True),
    )
    session.add(svc)
    await session.commit()
    await session.refresh(svc)
    return _to_admin_dict(svc)


async def update_service(
    session: AsyncSession, service_id: int, updates: dict
) -> Optional[dict]:
    svc = await session.get(WebsiteService, service_id)
    if not svc:
        return None
    for k, v in updates.items():
        if hasattr(svc, k) and v is not None:
            setattr(svc, k, v)
    await session.commit()
    await session.refresh(svc)
    return _to_admin_dict(svc)


async def delete_service(session: AsyncSession, service_id: int) -> bool:
    result = await session.execute(
        delete(WebsiteService).where(WebsiteService.id == service_id)
    )
    await session.commit()
    return result.rowcount > 0
