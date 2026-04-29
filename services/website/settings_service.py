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
    """公開端 /meta：對外需要的欄位（company / seo / social / about / home.hero）。

    這些欄位皆對應 website_settings 表中的 key，admin 在「🌐 官網管理」Tab 可編輯。
    前端（Astro build）一次撈全部，避免多次 round-trip。
    """
    s = await get_all_settings(session)
    return {
        "company_name_zh": s.get("company.name_zh", ""),
        "company_name_en": s.get("company.name_en", ""),
        "tagline": s.get("company.tagline", ""),
        "subtitle": s.get("company.subtitle", ""),
        "address": s.get("company.address", ""),
        "phone": s.get("company.phone", ""),
        "email": s.get("company.email", ""),
        "seo_default_title": s.get("seo.default_title", ""),
        "seo_default_description": s.get("seo.default_description", ""),
        "seo_og_image": s.get("seo.og_image", ""),
        "social": {
            "youtube": s.get("social.youtube", ""),
            "instagram": s.get("social.instagram", ""),
            "facebook": s.get("social.facebook", ""),
        },
        "about_intro_zh": s.get("about.intro_zh", ""),
        "about_intro_en": s.get("about.intro_en", ""),
        "about_founded_year": s.get("about.founded_year", ""),
        "about_team_intro_zh": s.get("about.team_intro_zh", ""),
        "home_hero_youtube_id": s.get("home.hero_youtube_id", ""),
        # SEO 索引控制（影響 BaseLayout noindex meta + robots.txt 動態端點）。
        # `is True` 比 `bool()` 嚴：避免使用者誤把 "false" 字串塞進去後變 truthy。
        "indexable": s.get("seo.indexable") is True,
        "ai_allow": s.get("seo.ai_allow") is True,
        # admin 在「llms.txt 編輯器」自填的 body；空則 /llms.txt 走自動生成
        "llms_txt_body": s.get("seo.llms_txt_body", ""),
    }


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
