"""db/seed_website.py
---
Phase M 官網模組初始資料（seed data）。

在 main.py / main_website.py startup 呼叫：
    await seed_website_if_empty(session_factory)

僅當 website_settings 是空時才寫入（避免覆蓋使用者後台調整）。

Seed 內容：
- 14 筆 website_settings（從舊站 originsun-studio.com 擷取的品牌資訊）
- 4 筆 website_categories（對應舊站的 4 大類）
- 4 筆 website_services（對應 4 大類）
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


# ── 14 筆全站設定（品牌資訊，擷取自舊站） ──
_SEED_SETTINGS: dict[str, object] = {
    "company.name_zh": "源日影像",
    "company.name_en": "OriginsunStudio",
    "company.tagline": "Best Story, Best Production",
    "company.subtitle": "影像製作 | 行銷規劃",
    "company.address": "10491 台北市中山區林森北路 614 號 6 樓",
    "company.phone": "02-25950595",
    "company.email": "originsun.media@gmail.com",
    "social.youtube": "https://www.youtube.com/@OriginsunStudio",
    "social.instagram": "https://instagram.com/originsun_studio",
    "social.facebook": "",
    "seo.default_title": "源日影像 OriginsunStudio｜影像製作・行銷規劃",
    "seo.default_description": "Best Story, Best Production. 源日影像是一間位於台北的專業影像製作團隊，提供商業廣告、紀實短片、活動紀實、動畫製作等服務。",
    "seo.og_image": "",               # 使用者後台上傳後填入
    "analytics.ga4_id": "",           # 未啟用
}

# ── 4 筆作品分類（對應舊站 4 大類） ──
_SEED_CATEGORIES: list[dict[str, object]] = [
    {
        "slug": "commercial",
        "name_zh": "商業廣告・形象影片",
        "name_en": "Commercial & Brand Films",
        "description": "商業廣告、品牌形象、產品介紹影片",
        "sort_order": 1,
        "visible": True,
    },
    {
        "slug": "documentary",
        "name_zh": "紀實短片・紀錄片",
        "name_en": "Documentary & Short Films",
        "description": "紀實影像、人物專訪、社會議題紀錄",
        "sort_order": 2,
        "visible": True,
    },
    {
        "slug": "event",
        "name_zh": "活動紀實・精彩回放",
        "name_en": "Event Recaps",
        "description": "企業活動、展覽紀錄、精彩片段剪輯",
        "sort_order": 3,
        "visible": True,
    },
    {
        "slug": "animation",
        "name_zh": "動畫製作・動態設計",
        "name_en": "Animation & Motion Design",
        "description": "動畫影片、動態圖像、視覺特效",
        "sort_order": 4,
        "visible": True,
    },
]

# ── 7 筆頂部導覽選單（對齊 Header.astro navItems + i18n.ts nav_* labels） ──
# 與對外網站當前導覽完全一致，seed 後 Header 改走 DB 但顯示不變。
_SEED_NAV_ITEMS: list[dict[str, object]] = [
    {"label_zh": "首頁",     "label_en": "Home",      "href": "/",          "sort_order": 1},
    {"label_zh": "作品集",   "label_en": "Works",     "href": "/works",     "sort_order": 2},
    {"label_zh": "歷年作品", "label_en": "Portfolio", "href": "/portfolio", "sort_order": 3},
    {"label_zh": "服務項目", "label_en": "Services",  "href": "/services",  "sort_order": 4},
    {"label_zh": "關於我們", "label_en": "About",     "href": "/about",     "sort_order": 5},
    {"label_zh": "部落格",   "label_en": "Insight",   "href": "/news",      "sort_order": 6},
    {"label_zh": "聯絡",     "label_en": "Contact",   "href": "/contact",   "sort_order": 7},
]

# ── 4 筆服務項目（對應 4 大類） ──
_SEED_SERVICES: list[dict[str, object]] = [
    {
        "slug": "commercial",
        "title": "商業廣告・形象影片",
        "icon": "video",
        "short_desc": "用影像為品牌說故事，打造深入人心的形象片。",
        "full_desc": "從品牌策略發想、腳本撰寫、拍攝執行到後期剪輯調色，提供完整的商業廣告與形象影片製作服務。",
        "related_category_slug": "commercial",
        "sort_order": 1,
        "visible": True,
    },
    {
        "slug": "documentary",
        "title": "紀實短片・紀錄片",
        "icon": "film",
        "short_desc": "真實的力量，讓故事被看見。",
        "full_desc": "深度訪談、田野調查、紀實拍攝，為人物、品牌、社會議題留下真實而有溫度的影像紀錄。",
        "related_category_slug": "documentary",
        "sort_order": 2,
        "visible": True,
    },
    {
        "slug": "event",
        "title": "活動紀實・精彩回放",
        "icon": "camera",
        "short_desc": "捕捉活動現場的每一個精彩瞬間。",
        "full_desc": "企業活動、展覽、演唱會、發表會全程紀實拍攝與精華剪輯，快速產出可發布的高品質活動影片。",
        "related_category_slug": "event",
        "sort_order": 3,
        "visible": True,
    },
    {
        "slug": "animation",
        "title": "動畫製作・動態設計",
        "icon": "sparkles",
        "short_desc": "用動態圖像傳達複雜的資訊與想法。",
        "full_desc": "2D/3D 動畫、Motion Graphics、解說影片製作，為品牌與產品提供清晰而生動的動態呈現。",
        "related_category_slug": "animation",
        "sort_order": 4,
        "visible": True,
    },
]


async def seed_nav_if_empty(session_factory: Callable) -> None:
    """若 website_nav_items 為空則寫入 7 筆預設導覽（對齊 Header.astro navItems）。

    獨立於 settings gate：website_nav_items 是 Phase 3 新表，既有部署 settings 早已
    seed 過（seed_website_if_empty 會 early-return），所以 nav 必須用自己的 emptiness
    閘門才能在升級後補 seed。幂等：表非空就 skip（使用者刪改過的不被覆蓋）。
    """
    try:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import insert
        from db.models_website import WebsiteNavItem
    except ImportError:
        logger.warning("[website-seed] SQLAlchemy/models unavailable, skip nav")
        return

    async with session_factory() as session:
        try:
            count = (await session.execute(
                text("SELECT COUNT(*) FROM website_nav_items")
            )).scalar() or 0
        except Exception as e:
            logger.warning("[website-seed] nav pre-check failed: %s", e)
            return

        if count > 0:
            return

        try:
            await session.execute(
                insert(WebsiteNavItem).values([
                    {**n, "visible": True} for n in _SEED_NAV_ITEMS
                ]).on_conflict_do_nothing()
            )
            await session.commit()
            logger.info("[website-seed] seeded %d nav items", len(_SEED_NAV_ITEMS))
        except Exception as e:
            await session.rollback()
            logger.error("[website-seed] nav seed failed, rolled back: %s", e)


async def seed_website_if_empty(session_factory: Callable) -> None:
    """若 website_settings 為空則寫入初始資料。

    幂等：檢查 settings 非空就 skip，讓使用者後台調整的值永遠不被覆寫。
    整批 ORM insert().on_conflict_do_nothing() 單一 commit。

    導覽選單（website_nav_items）走獨立 emptiness 閘門（見 seed_nav_if_empty），
    在 settings gate 之外先跑，確保既有部署升級後也會補上 7 筆預設導覽。
    """
    try:
        from sqlalchemy import text, select
        from sqlalchemy.dialects.postgresql import insert
        from db.models_website import (
            WebsiteSetting, WebsiteCategory, WebsiteService,
        )
    except ImportError:
        logger.warning("[website-seed] SQLAlchemy/models unavailable, skip")
        return

    # 導覽選單獨立 seed（不受 settings gate 影響 — 新表升級補種）
    await seed_nav_if_empty(session_factory)

    async with session_factory() as session:
        try:
            count = (await session.execute(
                text("SELECT COUNT(*) FROM website_settings")
            )).scalar() or 0
        except Exception as e:
            logger.warning("[website-seed] pre-check failed: %s", e)
            return

        if count > 0:
            logger.info("[website-seed] already seeded (%d settings), skip", count)
            return

        try:
            settings_rows = [
                {"key": k, "value": v, "updated_by": "seed"}
                for k, v in _SEED_SETTINGS.items()
            ]
            await session.execute(
                insert(WebsiteSetting).values(settings_rows).on_conflict_do_nothing(
                    index_elements=["key"]
                )
            )

            # RETURNING id, slug lets us map category slug→id without a second SELECT
            category_rows = [
                {
                    "slug": c["slug"], "name_zh": c["name_zh"], "name_en": c["name_en"],
                    "description": c["description"], "sort_order": c["sort_order"],
                    "visible": c["visible"],
                }
                for c in _SEED_CATEGORIES
            ]
            cat_result = await session.execute(
                insert(WebsiteCategory).values(category_rows).on_conflict_do_nothing(
                    index_elements=["slug"]
                ).returning(WebsiteCategory.id, WebsiteCategory.slug)
            )
            cat_id_map: dict[str, int] = {row.slug: row.id for row in cat_result}

            service_rows = [
                {
                    "slug": s["slug"], "title": s["title"], "icon": s["icon"],
                    "short_desc": s["short_desc"], "full_desc": s["full_desc"],
                    "related_category_id": cat_id_map.get(
                        s.get("related_category_slug", "")  # type: ignore[arg-type]
                    ),
                    "sort_order": s["sort_order"], "visible": s["visible"],
                }
                for s in _SEED_SERVICES
            ]
            await session.execute(
                insert(WebsiteService).values(service_rows).on_conflict_do_nothing(
                    index_elements=["slug"]
                )
            )

            await session.commit()
            logger.info(
                "[website-seed] seeded %d settings + %d categories + %d services",
                len(_SEED_SETTINGS), len(_SEED_CATEGORIES), len(_SEED_SERVICES),
            )
        except Exception as e:
            await session.rollback()
            logger.error("[website-seed] batch failed, rolled back: %s", e)
