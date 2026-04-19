"""services/website/settings_service.py
---
website_settings key-value store 的 get/put。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteSetting


async def get_all_settings(session: AsyncSession) -> dict[str, Any]:
    """撈全部 key-value，回傳 dict。"""
    stmt = select(WebsiteSetting.key, WebsiteSetting.value)
    return {row[0]: row[1] for row in await session.execute(stmt)}


async def get_meta(session: AsyncSession) -> dict[str, Any]:
    """公開端 /meta：只回傳對外需要的欄位（company.*、seo.*、social.*）。"""
    all_settings = await get_all_settings(session)
    out: dict[str, Any] = {
        "company_name_zh": all_settings.get("company.name_zh", ""),
        "company_name_en": all_settings.get("company.name_en", ""),
        "tagline": all_settings.get("company.tagline", ""),
        "subtitle": all_settings.get("company.subtitle", ""),
        "address": all_settings.get("company.address", ""),
        "phone": all_settings.get("company.phone", ""),
        "email": all_settings.get("company.email", ""),
        "seo_default_title": all_settings.get("seo.default_title", ""),
        "seo_default_description": all_settings.get("seo.default_description", ""),
        "social": {
            "youtube": all_settings.get("social.youtube", ""),
            "instagram": all_settings.get("social.instagram", ""),
            "facebook": all_settings.get("social.facebook", ""),
        },
    }
    return out


async def update_settings(
    session: AsyncSession,
    values: dict[str, Any],
    updated_by: Optional[str] = None,
) -> int:
    """批次 upsert settings。回傳更新的 key 數量。"""
    if not values:
        return 0
    rows = [
        {"key": k, "value": v, "updated_by": updated_by, "updated_at": datetime.now(timezone.utc)}
        for k, v in values.items()
    ]
    stmt = insert(WebsiteSetting).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={
            "value": stmt.excluded.value,
            "updated_by": stmt.excluded.updated_by,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)
