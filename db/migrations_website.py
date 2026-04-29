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


# ── ALTER TABLE: crm_projects 擴充 11 對外欄位 ──
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
    # 對外作品查詢加速
    "CREATE INDEX IF NOT EXISTS idx_crmproj_public ON crm_projects (public)",
    "CREATE INDEX IF NOT EXISTS idx_crmproj_featured ON crm_projects (public_featured)",
    "CREATE INDEX IF NOT EXISTS idx_crmproj_slug ON crm_projects (public_slug)",
]


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
