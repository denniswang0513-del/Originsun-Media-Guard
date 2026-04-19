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

import json
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


async def seed_website_if_empty(session_factory: Callable) -> None:
    """若 website_settings 為空則寫入初始資料。

    再次呼叫時會自動 skip（讓使用者後台調整的值不被覆寫）。
    """
    try:
        from sqlalchemy import text
    except ImportError:
        logger.warning("[website-seed] SQLAlchemy unavailable, skip seeding")
        return

    async with session_factory() as session:
        # 檢查是否已經 seed 過
        try:
            result = await session.execute(
                text("SELECT COUNT(*) FROM website_settings")
            )
            count = result.scalar() or 0
            if count > 0:
                logger.info("[website-seed] Already seeded (%d settings), skip", count)
                return
        except Exception as e:
            logger.warning("[website-seed] Check failed: %s", e)
            return

        # Seed settings
        for key, value in _SEED_SETTINGS.items():
            try:
                await session.execute(
                    text(
                        "INSERT INTO website_settings (key, value, updated_by) "
                        "VALUES (:k, :v::jsonb, :u) ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": key, "v": json.dumps(value), "u": "seed"},
                )
            except Exception as e:
                logger.warning("[website-seed] setting %s failed: %s", key, e)

        # Seed categories
        for cat in _SEED_CATEGORIES:
            try:
                await session.execute(
                    text(
                        "INSERT INTO website_categories "
                        "(slug, name_zh, name_en, description, sort_order, visible) "
                        "VALUES (:slug, :nz, :ne, :desc, :so, :vis) "
                        "ON CONFLICT (slug) DO NOTHING"
                    ),
                    {
                        "slug": cat["slug"], "nz": cat["name_zh"], "ne": cat["name_en"],
                        "desc": cat["description"], "so": cat["sort_order"], "vis": cat["visible"],
                    },
                )
            except Exception as e:
                logger.warning("[website-seed] category %s failed: %s", cat["slug"], e)

        # Seed services (先抓剛剛建的 categories 對應的 id)
        cat_id_map: dict[str, int] = {}
        try:
            res = await session.execute(text("SELECT id, slug FROM website_categories"))
            for row in res.fetchall():
                cat_id_map[row[1]] = row[0]
        except Exception as e:
            logger.warning("[website-seed] fetch category ids failed: %s", e)

        for svc in _SEED_SERVICES:
            related_cat_id = cat_id_map.get(svc.get("related_category_slug", ""))
            try:
                await session.execute(
                    text(
                        "INSERT INTO website_services "
                        "(slug, title, icon, short_desc, full_desc, related_category_id, "
                        "sort_order, visible) "
                        "VALUES (:slug, :t, :i, :sd, :fd, :rc, :so, :vis) "
                        "ON CONFLICT (slug) DO NOTHING"
                    ),
                    {
                        "slug": svc["slug"], "t": svc["title"], "i": svc["icon"],
                        "sd": svc["short_desc"], "fd": svc["full_desc"],
                        "rc": related_cat_id, "so": svc["sort_order"], "vis": svc["visible"],
                    },
                )
            except Exception as e:
                logger.warning("[website-seed] service %s failed: %s", svc["slug"], e)

        await session.commit()
        logger.info(
            "[website-seed] Seeded %d settings + %d categories + %d services",
            len(_SEED_SETTINGS), len(_SEED_CATEGORIES), len(_SEED_SERVICES),
        )
