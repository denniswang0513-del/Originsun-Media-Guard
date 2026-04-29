"""db/migrations_website.py
---
Phase M 官網模組 DB migration。

在 main.py / main_website.py startup 呼叫：
    await run_website_migrations(session_factory)

功能：
- ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS {11 對外欄位}
- ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS show_on_website
- CREATE TABLE IF NOT EXISTS {5 新表} + indexes

遵循既有慣例（見 main.py 第 ~430 行 crm_staff migration 區塊）。
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


# ── ALTER TABLE: crm_projects 擴充 對外欄位 ──
_CRM_PROJECTS_COLUMNS: list[tuple[str, str]] = [
    ("public", "BOOLEAN DEFAULT FALSE"),
    ("public_slug", "VARCHAR(100)"),
    ("public_title", "VARCHAR(200)"),
    ("public_client", "VARCHAR(100)"),
    ("public_youtube_id", "VARCHAR(20)"),
    ("public_description", "TEXT"),
    ("public_credits", "JSONB"),
    ("public_year", "INTEGER"),
    ("public_featured", "BOOLEAN DEFAULT FALSE"),
    ("public_sort_order", "INTEGER DEFAULT 0"),
    ("public_published_at", "TIMESTAMPTZ"),
    # 對外作品編號（1, 2, 3, ...）— slug 沒設時走這個。
    # 第一次 publish 時 auto-assign max+1，永久綁定（unpublish 不釋放號碼，
    # 避免 republish 時編號改變破壞 permalink / Google 索引）。
    ("public_number", "INTEGER"),
]

# ── ALTER TABLE: crm_staff 擴充 ──
_CRM_STAFF_COLUMNS: list[tuple[str, str]] = [
    ("show_on_website", "BOOLEAN DEFAULT FALSE"),
]

# ── ALTER TABLE: website_categories 擴充 ──
# kind: 'category' = 製作類型（形象/廣告/MV…），'tag' = 使用場景（展覽/講座…）
_WEBCAT_COLUMNS: list[tuple[str, str]] = [
    ("kind", "VARCHAR(16) NOT NULL DEFAULT 'category'"),
]

# ── CREATE TABLE IF NOT EXISTS: 5 新表 ──
_CREATE_TABLES: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS website_categories (
        id SERIAL PRIMARY KEY,
        slug VARCHAR(50) UNIQUE NOT NULL,
        name_zh VARCHAR(100) NOT NULL,
        name_en VARCHAR(100),
        description TEXT,
        cover_image TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        kind VARCHAR(16) NOT NULL DEFAULT 'category',
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_project_categories (
        project_id VARCHAR(32) NOT NULL,
        category_id INTEGER NOT NULL,
        PRIMARY KEY (project_id, category_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_settings (
        key VARCHAR(100) PRIMARY KEY,
        value JSONB,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        updated_by VARCHAR(100)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_services (
        id SERIAL PRIMARY KEY,
        title VARCHAR(100) NOT NULL,
        slug VARCHAR(100) UNIQUE NOT NULL,
        icon VARCHAR(50),
        short_desc VARCHAR(300),
        full_desc TEXT,
        cover_image TEXT,
        related_category_id INTEGER,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # Singleton row for rebuild state — master + NAS website-api 看同一個 source。
    # 之前 master 用 .rebuild_meta.json file-based，NAS 也獨立寫一份 → 雙寫，
    # admin Tab 跨機後 pending_count 跟 last_success_at 會不一致。
    """
    CREATE TABLE IF NOT EXISTS website_rebuild_state (
        id INTEGER PRIMARY KEY DEFAULT 1,
        last_success_at DOUBLE PRECISION,
        pending_count INTEGER NOT NULL DEFAULT 0,
        CONSTRAINT website_rebuild_state_singleton CHECK (id = 1)
    )
    """,
    "INSERT INTO website_rebuild_state (id, pending_count) VALUES (1, 0) ON CONFLICT (id) DO NOTHING",
    """
    CREATE TABLE IF NOT EXISTS website_contact_inquiries (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100),
        email VARCHAR(200),
        phone VARCHAR(50),
        company VARCHAR(200),
        service_type VARCHAR(50),
        budget_range VARCHAR(50),
        message TEXT,
        source VARCHAR(50),
        status VARCHAR(20) NOT NULL DEFAULT 'new',
        converted_client_id VARCHAR(32),
        ip_address VARCHAR(50),
        user_agent TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        handled_at TIMESTAMPTZ,
        handled_by VARCHAR(100),
        notes TEXT
    )
    """,
    # SEO 內容三表：FAQ / Testimonials / QuickFacts
    """
    CREATE TABLE IF NOT EXISTS website_faqs (
        id SERIAL PRIMARY KEY,
        question_zh VARCHAR(300) NOT NULL,
        question_en VARCHAR(300),
        answer_zh TEXT NOT NULL,
        answer_en TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_testimonials (
        id SERIAL PRIMARY KEY,
        author_zh VARCHAR(100) NOT NULL,
        author_en VARCHAR(100),
        role_zh VARCHAR(100),
        role_en VARCHAR(100),
        company VARCHAR(200),
        rating INTEGER NOT NULL DEFAULT 5,
        content_zh TEXT,
        content_en TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        date_published DATE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_quick_facts (
        id SERIAL PRIMARY KEY,
        label_zh VARCHAR(100) NOT NULL,
        label_en VARCHAR(100),
        value VARCHAR(300) NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
]

_CREATE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_webcat_visible ON website_categories (visible)",
    "CREATE INDEX IF NOT EXISTS idx_webcat_sort ON website_categories (sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_webcat_kind ON website_categories (kind)",
    "CREATE INDEX IF NOT EXISTS idx_wpc_category ON website_project_categories (category_id)",
    "CREATE INDEX IF NOT EXISTS idx_websvc_visible ON website_services (visible)",
    "CREATE INDEX IF NOT EXISTS idx_websvc_sort ON website_services (sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_inq_status ON website_contact_inquiries (status)",
    "CREATE INDEX IF NOT EXISTS idx_inq_created ON website_contact_inquiries (created_at)",
    # SEO 三表：visible+sort 經常一起查（公開端點 visible=true ORDER BY sort）
    "CREATE INDEX IF NOT EXISTS idx_webfaq_visible_sort ON website_faqs (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_webtst_visible_sort ON website_testimonials (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_webqf_visible_sort ON website_quick_facts (visible, sort_order)",
    # 對外作品查詢加速
    "CREATE INDEX IF NOT EXISTS idx_crmproj_public ON crm_projects (public)",
    "CREATE INDEX IF NOT EXISTS idx_crmproj_featured ON crm_projects (public_featured)",
    "CREATE INDEX IF NOT EXISTS idx_crmproj_slug ON crm_projects (public_slug)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_crmproj_pubnum ON crm_projects (public_number) WHERE public_number IS NOT NULL",
]


# 既有公開作品 backfill：第一次跑時依 published_at 給 1,2,3...
# 之後新 publish 走 auto-assign（max+1）邏輯。
_BACKFILL_PUBLIC_NUMBER = """
WITH numbered AS (
    SELECT id, ROW_NUMBER() OVER (
        ORDER BY public_published_at NULLS LAST, created_at NULLS LAST, id
    ) AS rn
    FROM crm_projects
    WHERE public IS TRUE AND public_number IS NULL
)
UPDATE crm_projects
   SET public_number = numbered.rn
  FROM numbered
 WHERE crm_projects.id = numbered.id
"""


async def run_website_migrations(session_factory: Callable) -> None:
    """Execute Phase M DB migrations.

    All 27 DDL statements batched in a single transaction (1 commit vs 27).
    Every statement uses IF NOT EXISTS so re-runs are no-op on server side.
    On any failure, whole batch rolls back; next startup will retry idempotently.
    """
    try:
        from sqlalchemy import text
    except ImportError:
        logger.warning("[website-migration] SQLAlchemy unavailable, skip")
        return

    stmts: list[str] = [
        *[f"ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _CRM_PROJECTS_COLUMNS],
        *[f"ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _CRM_STAFF_COLUMNS],
        # CREATE TABLE 必須先跑，ALTER 才有對象（fresh DB 沒這張表）
        *_CREATE_TABLES,
        *[f"ALTER TABLE website_categories ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _WEBCAT_COLUMNS],
        *_CREATE_INDEXES,
        # 既有 public 作品 backfill 編號（idempotent — 已有 public_number 的會跳過）
        _BACKFILL_PUBLIC_NUMBER,
    ]

    async with session_factory() as session:
        try:
            for sql in stmts:
                await session.execute(text(sql))
            await session.commit()
            logger.info("[website-migration] %d statements applied in 1 commit", len(stmts))
        except Exception as e:
            await session.rollback()
            logger.error("[website-migration] batch failed, rolled back: %s", e)
