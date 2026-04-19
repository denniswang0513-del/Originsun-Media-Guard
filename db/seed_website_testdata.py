"""db/seed_website_testdata.py
---
Phase M 測試資料 seed：幫 dev / staging 環境塞範例作品、員工、詢問，
讓官網 + 管理 Tab 有內容可瀏覽（base seed 只有 brand info + 分類 + 服務）。

執行：
    python -m db.seed_website_testdata

幂等：每項都以 id/slug 做 ON CONFLICT DO NOTHING，重複執行不會重複插入。

包含：
- 1 筆測試客戶（crm_clients，讓 projects 有 FK 可接）
- 7 筆測試專案（crm_projects + public_* 欄位 + 分類關聯）
  * 3 筆 featured、4 筆一般
  * 涵蓋所有 4 大分類
- 3 筆測試員工（crm_staff + show_on_website=True）
- 6 筆測試聯絡詢問（不同 status + 不同日期）

所有 ID 都以 `test_` 開頭方便事後清理（WHERE id LIKE 'test_%'）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

logger = logging.getLogger(__name__)


_TEST_CLIENT_ID = "test_client_001"
_TEST_CLIENT = {
    "id": _TEST_CLIENT_ID,
    "short_name": "源日測試客戶",
    "full_name": "源日影像內部測試客戶（展示用）",
    "status": "舊客戶",
    "notes": "測試資料 seed 建立，實際上線前可刪除或覆寫",
}


_TEST_PROJECTS: list[dict] = [
    {
        "id": "test_proj_001",
        "name": "[TEST] XYZ 品牌形象 TVC 2025",
        "project_type": "商業廣告",
        "public": True,
        "public_slug": "xyz-brand-tvc-2025",
        "public_title": "XYZ 品牌形象廣告",
        "public_client": "XYZ 品牌",
        "public_youtube_id": "lQYKHJ7sryM",
        "public_description": (
            "為 XYZ 品牌打造的年度形象 TVC。以都市日常切片重新詮釋品牌核心價值，"
            "從早晨第一杯咖啡到深夜加班的片刻，傳遞「日常也能不平凡」的品牌精神。"
        ),
        "public_credits": {
            "導演": "王小明",
            "製片": "李小華",
            "攝影": "陳大偉",
            "剪輯": "林美玲",
        },
        "public_year": 2025,
        "public_featured": True,
        "public_sort_order": 100,
        "categories": ["commercial"],
    },
    {
        "id": "test_proj_002",
        "name": "[TEST] 陶藝職人紀實短片",
        "project_type": "紀實短片",
        "public": True,
        "public_slug": "potter-documentary-2024",
        "public_title": "一雙手的溫度 — 陶藝家紀實",
        "public_client": "文化部影視局",
        "public_youtube_id": "ah9SjbwzniI",
        "public_description": (
            "記錄台灣新竹陶藝職人三十年的工藝傳承。透過長時間跟拍與深度訪談，"
            "呈現老師傅對泥土與火的執著，以及面對工藝傳承斷層的思索。"
        ),
        "public_credits": {
            "導演": "張偉成",
            "製片": "林美玲",
            "攝影": "黃志豪",
        },
        "public_year": 2024,
        "public_featured": True,
        "public_sort_order": 95,
        "categories": ["documentary"],
    },
    {
        "id": "test_proj_003",
        "name": "[TEST] ABC 集團 60 週年形象片",
        "project_type": "商業廣告",
        "public": True,
        "public_slug": "abc-group-60th-anniversary",
        "public_title": "ABC 集團 60 週年紀念影片",
        "public_client": "ABC 集團",
        "public_youtube_id": "test_vid003",
        "public_description": (
            "ABC 集團六十週年紀念影片，回顧從創業初期至今的關鍵時刻。"
            "結合歷史照片與現場訪談，呈現集團文化傳承與未來展望。"
        ),
        "public_credits": {"導演": "陳大偉", "製片": "王小明"},
        "public_year": 2024,
        "public_featured": False,
        "public_sort_order": 80,
        "categories": ["commercial", "documentary"],
    },
    {
        "id": "test_proj_004",
        "name": "[TEST] 2024 科技創新峰會活動紀實",
        "project_type": "活動紀實",
        "public": True,
        "public_slug": "tech-summit-2024-recap",
        "public_title": "2024 科技創新峰會精彩回顧",
        "public_client": "經濟部工業局",
        "public_youtube_id": "test_vid004",
        "public_description": (
            "2024 科技創新峰會全程活動紀實。兩天議程濃縮成 3 分鐘精華，"
            "涵蓋主題演講、展區亮點與專家座談，活動結束 48 小時內交付。"
        ),
        "public_credits": {"製片": "李小華", "攝影": "周大同"},
        "public_year": 2024,
        "public_featured": False,
        "public_sort_order": 70,
        "categories": ["event"],
    },
    {
        "id": "test_proj_005",
        "name": "[TEST] 國際電影節開幕回顧",
        "project_type": "活動紀實",
        "public": True,
        "public_slug": "film-festival-opening-2024",
        "public_title": "2024 金馬影展開幕精華",
        "public_client": "台北金馬影展執委會",
        "public_youtube_id": "test_vid005",
        "public_description": (
            "金馬影展開幕夜活動紀實，從紅毯到頒獎典禮，捕捉每一個閃光燈下的瞬間。"
        ),
        "public_credits": {"製片": "林美玲", "攝影": "陳大偉"},
        "public_year": 2024,
        "public_featured": False,
        "public_sort_order": 60,
        "categories": ["event"],
    },
    {
        "id": "test_proj_006",
        "name": "[TEST] DEF 科技企業介紹動畫",
        "project_type": "動畫製作",
        "public": True,
        "public_slug": "def-tech-brand-animation",
        "public_title": "DEF 科技企業介紹動畫",
        "public_client": "DEF 科技",
        "public_youtube_id": "test_vid006",
        "public_description": (
            "2D 動畫企業介紹影片。以輕快節奏與活潑插畫風格呈現產品線與服務流程，"
            "適用於官網首頁與展場投影。"
        ),
        "public_credits": {"動畫指導": "黃志豪", "配樂": "王小明"},
        "public_year": 2023,
        "public_featured": False,
        "public_sort_order": 50,
        "categories": ["animation"],
    },
    {
        "id": "test_proj_007",
        "name": "[TEST] 機能食品產品影片（混合動畫）",
        "project_type": "商業廣告",
        "public": True,
        "public_slug": "functional-food-product-video",
        "public_title": "機能食品產品影片",
        "public_client": "GHI 健康食品",
        "public_youtube_id": "test_vid007",
        "public_description": (
            "結合實拍與動畫的產品介紹影片，動畫段落說明成分機制，實拍段落呈現使用情境。"
        ),
        "public_credits": {"導演": "張偉成", "動畫指導": "黃志豪"},
        "public_year": 2024,
        "public_featured": True,
        "public_sort_order": 90,
        "categories": ["commercial", "animation"],
    },
]


_TEST_STAFF: list[dict] = [
    {
        "id": "test_staff_001",
        "name": "王小明",
        "role": "導演",
        "bio": "10 年影視製作經驗，擅長品牌敘事與廣告影片。",
        "photo_url": "https://i.pravatar.cc/300?u=test_staff_001",
        "show_on_website": True,
    },
    {
        "id": "test_staff_002",
        "name": "李小華",
        "role": "製片",
        "bio": "統籌過 50+ 專案，熟悉預算管控與團隊協調。",
        "photo_url": "https://i.pravatar.cc/300?u=test_staff_002",
        "show_on_website": True,
    },
    {
        "id": "test_staff_003",
        "name": "陳大偉",
        "role": "攝影師",
        "bio": "商業廣告資深攝影師，熟悉 Alexa、Venice、FX6 系列。",
        "photo_url": "https://i.pravatar.cc/300?u=test_staff_003",
        "show_on_website": True,
    },
]


def _inquiry(days_ago: int, **kw) -> dict:
    """建構 inquiry dict；非 new/spam 狀態自動補 handled_at/handled_by 保持資料合理。"""
    now = datetime.now(timezone.utc)
    created = now - timedelta(days=days_ago, hours=days_ago * 2)
    row = {"created_at": created, **kw}
    status = row.get("status", "new")
    if status in ("in_progress", "converted") and "handled_at" not in row:
        row["handled_at"] = created + timedelta(hours=4)
        row.setdefault("handled_by", "test_admin")
    return row


_TEST_INQUIRIES: list[dict] = [
    _inquiry(
        0, name="陳先生", email="chen@example.com", phone="0912-345-678",
        company="創新科技股份有限公司", service_type="commercial", budget_range="100-300 萬",
        message="想洽詢年度形象片製作，預計 11 月拍攝，請協助報價。", source="/contact",
        status="new", ip_address="203.0.113.10",
    ),
    _inquiry(
        1, name="林小姐", email="lin.meifang@example.org",
        company="未來設計工作室", service_type="animation", budget_range="30-80 萬",
        message="需要 90 秒產品介紹動畫，預計 2 個月內完成，希望近期能 meeting 討論。",
        source="/services", status="new", ip_address="203.0.113.20",
    ),
    _inquiry(
        3, name="王總", email="w.boss@abc-corp.com", phone="02-2345-6789",
        company="ABC 集團", service_type="documentary", budget_range="300 萬以上",
        message="集團 70 週年希望拍攝深度紀錄片，已內部確認預算，請提供製作團隊介紹。",
        source="/contact", status="in_progress",
        notes="已回信報價範本、安排下週二下午 14:00 碰面",
    ),
    _inquiry(
        7, name="黃先生", email="huang.test@sample.net",
        service_type="event", budget_range="10-30 萬",
        message="下個月科技論壇需要活動紀實，1 天拍攝 + 3 分鐘精華。", source="/contact",
        status="converted", converted_client_id="test_client_001",
        notes="已轉為正式客戶，專案編號 test_proj_004",
    ),
    _inquiry(
        14, name="spam bot", email="bot@spamsite.xyz",
        company="SEO services", message="We can help your website rank #1 on Google, contact us now!",
        source="/contact", status="spam",
    ),
    _inquiry(
        21, name="張小姐", email="zhang@design-co.tw",
        company="視覺設計公司", service_type="animation", budget_range="80-150 萬",
        message="客戶需要 2D 動畫，想找外包合作夥伴。", source="/services",
        status="converted", converted_client_id="test_client_001",
    ),
]


async def seed_test_data(session_factory: Callable) -> None:
    """Insert test data. Idempotent via ON CONFLICT DO NOTHING."""
    try:
        from sqlalchemy import select, text
        from sqlalchemy.dialects.postgresql import insert
        from db.models import Client, CrmProject, CrmStaff
        from db.models_website import WebsiteCategory, WebsiteContactInquiry, WebsiteProjectCategory
    except ImportError as e:
        logger.error("[test-seed] import failed: %s", e)
        return

    async with session_factory() as session:
        # 1. Client
        await session.execute(
            insert(Client).values(_TEST_CLIENT).on_conflict_do_nothing(index_elements=["id"])
        )

        # 2. Categories slug→id 對照表
        cat_map = {
            row.slug: row.id
            for row in (await session.execute(
                select(WebsiteCategory.id, WebsiteCategory.slug)
            ))
        }

        # 3. Projects + category links (不 mutate module-level _TEST_PROJECTS)
        for proj in _TEST_PROJECTS:
            categories = proj.get("categories", [])
            proj_values = {k: v for k, v in proj.items() if k != "categories"}
            await session.execute(
                insert(CrmProject).values({
                    **proj_values,
                    "client_id": _TEST_CLIENT_ID,
                    "status": "已結案",
                    "public_published_at": datetime.now(timezone.utc),
                }).on_conflict_do_nothing(index_elements=["id"])
            )
            for slug in categories:
                cid = cat_map.get(slug)
                if cid is None:
                    logger.warning("[test-seed] category '%s' not found, skip", slug)
                    continue
                await session.execute(
                    insert(WebsiteProjectCategory).values(
                        project_id=proj["id"], category_id=cid
                    ).on_conflict_do_nothing()
                )

        # 4. Staff
        for staff in _TEST_STAFF:
            await session.execute(
                insert(CrmStaff).values({
                    **staff,
                    "daily_rate": 0,
                    "hourly_rate": 0,
                    "status": "在職",
                }).on_conflict_do_nothing(index_elements=["id"])
            )

        # 5. Inquiries
        for inq in _TEST_INQUIRIES:
            await session.execute(
                insert(WebsiteContactInquiry).values(inq).on_conflict_do_nothing()
            )

        await session.commit()
        logger.info(
            "[test-seed] inserted: 1 client + %d projects + %d staff + %d inquiries",
            len(_TEST_PROJECTS), len(_TEST_STAFF), len(_TEST_INQUIRIES),
        )


async def _main() -> None:
    from db.session import get_session_factory, init_db
    await init_db()
    factory = get_session_factory()
    if not factory:
        print("DB not online, aborting")
        return
    await seed_test_data(factory)
    print("Test data seed completed")


if __name__ == "__main__":
    asyncio.run(_main())
