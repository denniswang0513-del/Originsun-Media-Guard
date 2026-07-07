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
    # 第一次 publish 時 auto-assign max+1，永久綁定（unpublish 不釋放號碼,
    # 避免 republish 時編號改變破壞 permalink / Google 索引）。
    ("public_number", "INTEGER"),
    # SEO 301 來源舊 slug 陣列（軟+硬 301 雙保險，跟 website_posts.old_urls 對齊）
    ("public_old_slugs", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    # OG image — _sync_showcase_to_public 從 sc.cover_url 鏡像
    ("public_cover_url", "TEXT"),
    # 首頁輪播精選圖（admin 直接設定，不被 showcase 鏡像覆蓋）
    ("public_featured_image", "TEXT"),
    # per-work SEO 索引控制（true = 強制 noindex；null/false = 跟站級 indexable）
    ("public_noindex", "BOOLEAN DEFAULT FALSE"),
    # credits 雙模式：'block' (JSONB blocks) / 'text' (純文字貼上)
    ("public_credits_mode", "VARCHAR(16) NOT NULL DEFAULT 'text'"),
    ("public_credits_text", "TEXT"),
    # Phase M 英文版 _en（transcreation；public_client_en 手動指定，AI runner 不翻客戶名）
    ("public_title_en", "VARCHAR(300)"),
    ("public_description_en", "TEXT"),
    ("public_client_en", "VARCHAR(150)"),
]

# ── ALTER TABLE: website_posts 擴充（既有部署補欄）──
# faqs: AI SEO runner 生成的 3 題 Q&A（JSONB [{"q","a"}]），渲染成 FAQPage JSON-LD
# + 文章底部「常見問題」可見區段（Google FAQ rich result 要求內容對使用者可見）
_WEBPOSTS_COLUMNS: list[tuple[str, str]] = [
    ("faqs", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    # Phase M 英文版 _en（transcreation）
    ("title_en", "TEXT"),
    ("excerpt_en", "TEXT"),
    ("body_en", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("seo_title_en", "VARCHAR(200)"),
    ("seo_description_en", "VARCHAR(300)"),
]

# ── ALTER TABLE: website_services 擴充（英文版 _en）──
_WEBSVC_COLUMNS: list[tuple[str, str]] = [
    ("title_en", "VARCHAR(200)"),
    ("short_desc_en", "VARCHAR(500)"),
    ("full_desc_en", "TEXT"),
]

# ── ALTER TABLE: crm_staff 擴充 ──
_CRM_STAFF_COLUMNS: list[tuple[str, str]] = [
    ("show_on_website", "BOOLEAN DEFAULT FALSE"),
    # Phase M: 官網團隊頁顯示覆寫（不動 CRM 正本 name/role/photo_url/bio；空 → fallback 正本）
    ("website_title", "VARCHAR(128)"),       # 官網顯示職稱（空 → fallback role）
    ("website_photo_url", "VARCHAR(512)"),   # 官網頭像（空 → fallback photo_url）
    ("website_bio", "TEXT"),                 # 官網簡介（空 → fallback bio）
    ("website_sort_order", "INTEGER DEFAULT 0"),  # 官網團隊頁排序
]

# ── ALTER TABLE: crm_project_showcase 擴充（既有部署補欄）──
_CRM_PROJECT_SHOWCASE_COLUMNS: list[tuple[str, str]] = [
    # credits 雙模式：'block' (JSONB blocks) / 'text' (純文字貼上)
    ("credits_mode", "VARCHAR(16) NOT NULL DEFAULT 'text'"),
    ("credits_text", "TEXT"),
]

# ── ALTER TABLE: website_categories 擴充 ──
# kind: 'category' = 製作類型（形象/廣告/MV…），'tag' = 使用場景（展覽/講座…）
_WEBCAT_COLUMNS: list[tuple[str, str]] = [
    ("kind", "VARCHAR(16) NOT NULL DEFAULT 'category'"),
]

# ── ALTER TABLE: website_project_seo 擴充（既有部署補欄）──
_WEBPROJSEO_COLUMNS: list[tuple[str, str]] = [
    ("canonical_url", "VARCHAR(500)"),
]

# ── ALTER TABLE: website_awards 擴充（film-centric rework 補欄）──
# work_type = 作品類型純文字（劇情短片/紀錄短片…），work_year = 作品所屬年度（YYYY | 群組）。
_WEBAWARD_COLUMNS: list[tuple[str, str]] = [
    ("work_type", "VARCHAR(64)"),
    ("work_year", "INTEGER"),
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
    # 作品級 SEO / AI SEO 內容（1:1 with crm_projects；給 AI/SEO 生成 pipeline 寫入）
    """
    CREATE TABLE IF NOT EXISTS website_project_seo (
        project_id VARCHAR(32) PRIMARY KEY,
        seo_title VARCHAR(120),
        seo_description VARCHAR(500),
        keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
        canonical_url VARCHAR(500),
        narrative_long TEXT,
        key_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
        faqs JSONB NOT NULL DEFAULT '[]'::jsonb,
        needs_ai_review BOOLEAN NOT NULL DEFAULT TRUE,
        last_ai_review_at TIMESTAMPTZ,
        last_ai_review_by VARCHAR(64),
        ai_review_notes TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # 部落格三表（DB-as-truth，Notion 只是匯入器）
    """
    CREATE TABLE IF NOT EXISTS website_posts (
        id SERIAL PRIMARY KEY,
        slug VARCHAR(50) UNIQUE NOT NULL,
        title TEXT NOT NULL,
        excerpt TEXT,
        cover_url TEXT,
        body JSONB NOT NULL DEFAULT '[]'::jsonb,
        published_at TIMESTAMPTZ,
        date_modified TIMESTAMPTZ,
        read_time_min INTEGER,
        status VARCHAR(16) NOT NULL DEFAULT 'draft',
        sort_order INTEGER NOT NULL DEFAULT 0,
        notion_page_id VARCHAR(64),
        imported_from_notion_at TIMESTAMPTZ,
        seo_title VARCHAR(200),
        seo_description VARCHAR(300),
        og_image_url TEXT,
        canonical_url TEXT,
        noindex BOOLEAN NOT NULL DEFAULT FALSE,
        author_name VARCHAR(100),
        author_url TEXT,
        ai_allow_override BOOLEAN,
        old_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
        faqs JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_post_categories (
        id SERIAL PRIMARY KEY,
        slug VARCHAR(50) UNIQUE NOT NULL,
        label_zh VARCHAR(100) NOT NULL,
        label_en VARCHAR(100),
        color VARCHAR(20),
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_post_category_links (
        post_id INTEGER NOT NULL REFERENCES website_posts(id) ON DELETE CASCADE,
        category_id INTEGER NOT NULL REFERENCES website_post_categories(id) ON DELETE CASCADE,
        PRIMARY KEY (post_id, category_id)
    )
    """,
    # 演職員職位主檔 + 模板（block 結構演職員表的後盾）
    """
    CREATE TABLE IF NOT EXISTS website_credit_roles (
        id SERIAL PRIMARY KEY,
        name_zh VARCHAR(80) UNIQUE NOT NULL,
        name_en VARCHAR(80) NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_credit_templates (
        id SERIAL PRIMARY KEY,
        name VARCHAR(120) NOT NULL UNIQUE,
        description TEXT,
        role_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # 頂部導覽選單項目（可改名 / 排序 / 顯示隱藏）— admin「🧭 導覽選單」管理
    """
    CREATE TABLE IF NOT EXISTS website_nav_items (
        id SERIAL PRIMARY KEY,
        label_zh VARCHAR(100) NOT NULL,
        label_en VARCHAR(100),
        href VARCHAR(200) NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # 站級獎項紀錄（公司榮譽，不綁特定作品 — work_title 是純文字）
    # film-centric：work_type + work_year 把同一作品的多行獎項分組。
    """
    CREATE TABLE IF NOT EXISTS website_awards (
        id SERIAL PRIMARY KEY,
        name_zh VARCHAR(200) NOT NULL,
        name_en VARCHAR(200),
        year INTEGER NOT NULL,
        category VARCHAR(200),
        org VARCHAR(200),
        level VARCHAR(20) NOT NULL DEFAULT '獲獎',
        work_type VARCHAR(64),
        work_title VARCHAR(300),
        work_year INTEGER,
        recipient VARCHAR(200),
        cert_url VARCHAR(500),
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # 公益合作 / 創作計畫 案例（日常業務外的兩條線）。project_id 連動 crm_projects。
    """
    CREATE TABLE IF NOT EXISTS website_initiatives (
        id SERIAL PRIMARY KEY,
        line VARCHAR(16) NOT NULL,
        project_id VARCHAR(32),
        title VARCHAR(300),
        summary TEXT,
        cover_url TEXT,
        link_url VARCHAR(500),
        year INTEGER,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # Legacy 舊站→新站 頁面級 301 轉址（不綁作品/文章；from_path 是正規化舊路徑）。
    """
    CREATE TABLE IF NOT EXISTS website_redirects (
        id SERIAL PRIMARY KEY,
        from_path VARCHAR(500) UNIQUE NOT NULL,
        to_path VARCHAR(500) NOT NULL,
        note VARCHAR(300),
        sort_order INTEGER NOT NULL DEFAULT 0,
        visible BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # 英文翻譯（transcreation）工作流狀態（每實體一列；英文內容在各實體 _en 欄）。
    """
    CREATE TABLE IF NOT EXISTS website_translation_state (
        entity_type VARCHAR(16) NOT NULL,
        entity_id VARCHAR(64) NOT NULL,
        source_hash VARCHAR(64),
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        last_translated_at TIMESTAMPTZ,
        last_translated_by VARCHAR(64),
        reviewed_at TIMESTAMPTZ,
        reviewed_by VARCHAR(64),
        notes TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (entity_type, entity_id)
    )
    """,
    # 社群文稿佇列（Phase N-soc 階段一）：social_runner 產出的多平台草稿 + 審核狀態。
    """
    CREATE TABLE IF NOT EXISTS website_social_posts (
        id VARCHAR(32) PRIMARY KEY,
        source_type VARCHAR(16) NOT NULL,
        source_id VARCHAR(64) NOT NULL,
        platform VARCHAR(16) NOT NULL,
        content TEXT,
        media_url VARCHAR(512),
        status VARCHAR(16) NOT NULL DEFAULT 'draft',
        scheduled_at TIMESTAMPTZ,
        published_url VARCHAR(512),
        error_detail TEXT,
        run_id VARCHAR(32),
        reviewed_by VARCHAR(64),
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
    # SEO 四表（FAQ/Testimonial/QuickFact/Award）：visible+sort 經常一起查
    # 公開端點都是 visible=true ORDER BY sort_order
    "CREATE INDEX IF NOT EXISTS idx_webfaq_visible_sort ON website_faqs (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_webtst_visible_sort ON website_testimonials (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_webqf_visible_sort ON website_quick_facts (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_award_visible_sort ON website_awards (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_initiative_line_sort ON website_initiatives (line, visible, sort_order)",
    # 作品級 SEO：audit 端點頻繁查 needs_ai_review=true → 索引加速
    "CREATE INDEX IF NOT EXISTS idx_webprojseo_needs_review ON website_project_seo (needs_ai_review)",
    # Posts 公開查詢：WHERE status='published' AND published_at<=NOW() ORDER BY published_at DESC
    "CREATE INDEX IF NOT EXISTS idx_post_status_pub ON website_posts (status, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_post_notion_page ON website_posts (notion_page_id)",
    "CREATE INDEX IF NOT EXISTS idx_postcat_visible_sort ON website_post_categories (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_pcl_category ON website_post_category_links (category_id)",
    # 對外作品查詢加速
    "CREATE INDEX IF NOT EXISTS idx_crmproj_public ON crm_projects (public)",
    "CREATE INDEX IF NOT EXISTS idx_crmproj_featured ON crm_projects (public_featured)",
    "CREATE INDEX IF NOT EXISTS idx_crmproj_slug ON crm_projects (public_slug)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_crmproj_pubnum ON crm_projects (public_number) WHERE public_number IS NOT NULL",
    # 演職員職位查詢：visible+sort
    "CREATE INDEX IF NOT EXISTS idx_credit_role_visible_sort ON website_credit_roles (visible, sort_order)",
    # 頂部導覽：公開端 visible=true ORDER BY sort_order
    "CREATE INDEX IF NOT EXISTS idx_webnav_visible_sort ON website_nav_items (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_webredir_visible_sort ON website_redirects (visible, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_transl_status ON website_translation_state (status)",
    # 社群文稿佇列：後台佇列篩 status、選題查「此來源是否已推過」、頻率上限數當日/當週
    "CREATE INDEX IF NOT EXISTS idx_socialpost_status ON website_social_posts (status)",
    "CREATE INDEX IF NOT EXISTS idx_socialpost_source ON website_social_posts (source_type, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_socialpost_created ON website_social_posts (created_at)",
]


# ── 舊 flat array credits 一次性 migration（idempotent — 條件守門避免重轉） ──
# 條件：sc.credits 是非空 array 且首元素無 'entries' 鍵。已是 block 結構不影響。
# DO 區塊 + to_regclass 守門：fresh DB 還沒 crm_project_showcases / crm_projects 表時
# 整個 batch 會 rollback 把 CREATE TABLE 也廢掉，所以必須在表存在才跑。
def _build_flat_credits_migration_sql(table: str, col: str) -> str:
    return f"""
    DO $migrate$
    BEGIN
        IF to_regclass('public.{table}') IS NOT NULL THEN
            UPDATE {table}
            SET {col} = jsonb_build_array(jsonb_build_object(
                'role_id', null, 'name_zh', '', 'name_en', '',
                'entries', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'duty', COALESCE(elem->>'role', ''),
                        'name', COALESCE(elem->>'name', ''),
                        'resume_url', COALESCE(elem->>'resume_url', '')))
                    FROM jsonb_array_elements({col}) elem
                    WHERE elem->>'name' IS NOT NULL AND elem->>'name' <> ''
                ), '[]'::jsonb)
            ))
            WHERE jsonb_typeof({col}) = 'array'
              AND jsonb_array_length({col}) > 0
              AND NOT ({col}->0 ? 'entries');
        END IF;
    END
    $migrate$;
    """


_MIGRATE_FLAT_CREDITS_SHOWCASE = _build_flat_credits_migration_sql(
    "crm_project_showcase", "credits"
)
_MIGRATE_FLAT_CREDITS_PROJECT = _build_flat_credits_migration_sql(
    "crm_projects", "public_credits"
)


# ── 演職員職位庫 + 模板 seed（idempotent — 重跑不會炸） ──
# Roles 用 ON CONFLICT (name_zh) DO NOTHING；
# Templates 用 NOT EXISTS 子查詢避免重複，role_ids 透過子查詢動態解析 id（避免 hardcode）。
_SEED_CREDIT_ROLES = """
INSERT INTO website_credit_roles (name_zh, name_en, sort_order, visible) VALUES
    ('製作', 'Production', 10, true),
    ('導演', 'Director', 20, true),
    ('編劇', 'Writer', 30, true),
    ('攝影指導', 'DP', 40, true),
    ('燈光', 'Gaffer', 50, true),
    ('收音', 'Sound', 60, true),
    ('剪輯', 'Editor', 70, true),
    ('後製', 'Post-Production', 80, true),
    ('演員', 'Cast', 90, true),
    ('配樂', 'Music', 100, true),
    ('美術', 'Art', 110, true),
    ('造型', 'Stylist', 120, true)
ON CONFLICT (name_zh) DO NOTHING
"""

# 模板 seed：4 欄定義（name/desc/sort/roles）→ generator 組成 NOT EXISTS-guarded INSERT。
# 子查詢用 array_position 把 role_ids JSONB array 按 _SEED_TEMPLATES 中羅列的順序排好。
# Seed 是內部固定字串、無使用者輸入 → 直接 f-string interpolate 不需 parameterize。
_SEED_TEMPLATES: list[tuple[str, str, int, list[str]]] = [
    ("商業廣告標準", "適用於 30 / 60 秒 TVC、品牌形象片", 10,
     ["製作", "導演", "攝影指導", "燈光", "剪輯", "後製", "演員"]),
    ("MV 完整版", "音樂錄影帶常用 9 職位組合", 20,
     ["製作", "導演", "攝影指導", "燈光", "剪輯", "後製", "演員", "造型", "美術"]),
    ("企業形象片精簡版", "小型製作或內部宣傳片用 4 職位", 30,
     ["製作", "導演", "攝影指導", "剪輯"]),
]


def _build_template_seed_sql(name: str, desc: str, sort: int, roles: list[str]) -> str:
    arr = ",".join(f"'{r}'" for r in roles)
    # ON CONFLICT (name) DO NOTHING + UNIQUE constraint 雙保險。
    # 之前用 WHERE NOT EXISTS 過濾 SELECT，但 SELECT 帶 jsonb_agg 是 implicit aggregate
    # 不論 input rows 多少都會產出 1 row（input 0 rows → COALESCE 給 '[]'），WHERE 對
    # aggregate 結果無效 → 每次跑 migration 都會多 INSERT 一筆 role_ids=[] 的空模板，
    # production 累積 148 筆 duplicate（修復於 v1.10.121）。
    return f"""
    INSERT INTO website_credit_templates (name, description, role_ids, sort_order)
    SELECT '{name}', '{desc}',
           COALESCE(jsonb_agg(id ORDER BY rn), '[]'::jsonb), {sort}
    FROM (
        SELECT id, array_position(ARRAY[{arr}]::varchar[], name_zh::varchar) AS rn
        FROM website_credit_roles
        WHERE name_zh IN ({arr})
    ) t
    ON CONFLICT (name) DO NOTHING
    """


_SEED_CREDIT_TEMPLATES: list[str] = [
    _build_template_seed_sql(name, desc, sort, roles)
    for name, desc, sort, roles in _SEED_TEMPLATES
]


# ── v1.10.121 修復：清掉舊版 WHERE NOT EXISTS bug 累積的 duplicate templates ──
# 同名留 role_ids 最多 + created_at 最早 + id 最小那筆，刪其餘。idempotent。
_CLEANUP_TEMPLATE_DUPLICATES = """
DO $cleanup_tmpl$
BEGIN
    IF to_regclass('public.website_credit_templates') IS NOT NULL THEN
        DELETE FROM website_credit_templates
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY name
                           ORDER BY jsonb_array_length(COALESCE(role_ids, '[]'::jsonb)) DESC,
                                    created_at ASC, id ASC
                       ) AS rn
                FROM website_credit_templates
            ) ranked
            WHERE rn > 1
        );
    END IF;
END
$cleanup_tmpl$;
"""

# 補上 UNIQUE(name) constraint — 既有 table 沒這個約束，加上後 ON CONFLICT 才生效。
# 查 pg_constraint 守門避免 ALTER raise duplicate_table（constraint 帶隱含 index 名稱）。
_ADD_TEMPLATE_NAME_UNIQUE = """
DO $add_uniq$
BEGIN
    IF to_regclass('public.website_credit_templates') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'website_credit_templates_name_key'
             AND conrelid = 'public.website_credit_templates'::regclass
       ) THEN
        ALTER TABLE website_credit_templates
            ADD CONSTRAINT website_credit_templates_name_key UNIQUE (name);
    END IF;
END
$add_uniq$;
"""


# Seed 預設使用場景標籤（kind='tag'）— 給空 DB 一些 sample，admin 可自行增刪。
#
# 跑過一次就在 website_settings 寫 'defaults.tags_seeded' 旗標；之後啟動會 skip。
# 為什麼不靠 ON CONFLICT slug DO NOTHING：admin DELETE 後，row 不存在 → 沒 conflict
# → INSERT 成功 → tag 自動復活。Bug：使用者刪掉的 tag 重啟後又長回來。
#
# 用 WHERE NOT EXISTS 子查詢 + key 寫旗標：兩條 SQL 必須在同一個 batch commit 內，
# 否則第一次跑的中間若 crash，下次會雙寫。本檔案的 run_website_migrations 用單一
# commit 包整批，符合此 invariant。
_SEED_DEFAULT_TAGS = """
INSERT INTO website_categories (slug, name_zh, name_en, description, sort_order, visible, kind)
SELECT * FROM (VALUES
    ('tag-vr',         'VR/360 沉浸式',   'VR/360 Immersive',   '用於 VR 頭顯 / 360 全景場景的影像',     1, TRUE, 'tag'),
    ('tag-onsite',     '現場活動',        'On-site Event',      '線下活動現場拍攝、即時投影或快剪',      2, TRUE, 'tag'),
    ('tag-online',     '線上獨家',        'Online Exclusive',   '專為網路平台投放、社群傳播設計',        3, TRUE, 'tag'),
    ('tag-overseas',   '海外拍攝',        'Overseas Shoot',     '海外或境外取景的製作案',                 4, TRUE, 'tag'),
    ('tag-public-good','公益／非營利',     'Public Good',        '公益、NGO、政府宣導等非商業性質',        5, TRUE, 'tag')
) AS v(slug, name_zh, name_en, description, sort_order, visible, kind)
WHERE NOT EXISTS (
    SELECT 1 FROM website_settings WHERE key = 'defaults.tags_seeded'
)
ON CONFLICT (slug) DO NOTHING
"""

# 同 batch commit 內標記旗標 — 之後啟動 _SEED_DEFAULT_TAGS 的 WHERE NOT EXISTS 會擋下
_MARK_DEFAULT_TAGS_SEEDED = """
INSERT INTO website_settings (key, value)
VALUES ('defaults.tags_seeded', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING
"""


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
        *[f"ALTER TABLE crm_project_showcase ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _CRM_PROJECT_SHOWCASE_COLUMNS],
        # CREATE TABLE 必須先跑，ALTER 才有對象（fresh DB 沒這張表）
        *_CREATE_TABLES,
        *[f"ALTER TABLE website_posts ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _WEBPOSTS_COLUMNS],
        *[f"ALTER TABLE website_categories ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _WEBCAT_COLUMNS],
        *[f"ALTER TABLE website_project_seo ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _WEBPROJSEO_COLUMNS],
        *[f"ALTER TABLE website_awards ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _WEBAWARD_COLUMNS],
        *[f"ALTER TABLE website_services ADD COLUMN IF NOT EXISTS {c} {t}"
          for c, t in _WEBSVC_COLUMNS],
        *_CREATE_INDEXES,
        # v1.10.121 修復 — 既有部署 templates 表無 UNIQUE constraint，先清重複再補
        _CLEANUP_TEMPLATE_DUPLICATES,
        _ADD_TEMPLATE_NAME_UNIQUE,
        # Seed 5 個預設 tag — 一次性，靠 'defaults.tags_seeded' 旗標 idempotent
        # （admin 刪掉之後不會自動重新 seed 復活）
        _SEED_DEFAULT_TAGS,
        _MARK_DEFAULT_TAGS_SEEDED,
        # 既有 public 作品 backfill 編號（idempotent — 已有 public_number 的會跳過）
        _BACKFILL_PUBLIC_NUMBER,
        # 演職員職位庫 seed（roles 必先於 templates，因為 templates 子查詢要查 role.id）
        _SEED_CREDIT_ROLES,
        *_SEED_CREDIT_TEMPLATES,
        # 舊 flat array credits 升級為 block 結構（idempotent，已轉過的不再變動）
        _MIGRATE_FLAT_CREDITS_SHOWCASE,
        _MIGRATE_FLAT_CREDITS_PROJECT,
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
