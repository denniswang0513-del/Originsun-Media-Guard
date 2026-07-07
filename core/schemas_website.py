"""core/schemas_website.py
---
Phase M 官網模組 Pydantic schemas。

分 3 組：
1. Public（對外網站 fetch 用）：簡化欄位、不含敏感資訊
2. Admin（管理 Tab 用）：完整欄位、含統計
3. Request（寫入用）：Create / Update / Reorder

命名：
- XxxPublicResponse / XxxAdminResponse / XxxCreate / XxxUpdate
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

InquiryStatus = Literal["new", "in_progress", "converted", "spam"]


# ══════════════════════════════════════════════════════════
# Category（作品分類）
# ══════════════════════════════════════════════════════════

class CategoryPublicResponse(BaseModel):
    slug: str
    name_zh: str
    name_en: Optional[str] = None
    count: int = 0
    kind: str = "category"  # 'category' | 'tag'


class CategoryAdminResponse(BaseModel):
    id: int
    slug: str
    name_zh: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None
    sort_order: int = 0
    visible: bool = True
    kind: str = "category"
    project_count: int = 0


class CategoryCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    name_zh: str = Field(..., min_length=1, max_length=100)
    name_en: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    cover_image: Optional[str] = None
    sort_order: int = 0
    visible: bool = True
    kind: str = Field("category", pattern=r"^(category|tag)$")


class CategoryUpdate(BaseModel):
    slug: Optional[str] = None
    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None
    kind: Optional[str] = Field(None, pattern=r"^(category|tag)$")


class CategoryReorder(BaseModel):
    order: list[int]  # ordered list of category IDs


# ══════════════════════════════════════════════════════════
# Service（服務項目）
# ══════════════════════════════════════════════════════════

class ServicePublicResponse(BaseModel):
    slug: str
    title: str
    icon: Optional[str] = None
    short_desc: Optional[str] = None
    cover_image: Optional[str] = None
    related_category_slug: Optional[str] = None


class ServiceAdminResponse(BaseModel):
    id: int
    slug: str
    title: str
    icon: Optional[str] = None
    short_desc: Optional[str] = None
    full_desc: Optional[str] = None
    cover_image: Optional[str] = None
    related_category_id: Optional[int] = None
    sort_order: int = 0
    visible: bool = True


class ServiceCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    title: str = Field(..., min_length=1, max_length=100)
    icon: Optional[str] = None
    short_desc: Optional[str] = Field(None, max_length=300)
    full_desc: Optional[str] = None
    cover_image: Optional[str] = None
    related_category_id: Optional[int] = None
    sort_order: int = 0
    visible: bool = True


class ServiceUpdate(BaseModel):
    slug: Optional[str] = None
    title: Optional[str] = None
    icon: Optional[str] = None
    short_desc: Optional[str] = None
    full_desc: Optional[str] = None
    cover_image: Optional[str] = None
    related_category_id: Optional[int] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


# ══════════════════════════════════════════════════════════
# Nav Item（頂部導覽選單）
# ══════════════════════════════════════════════════════════

class NavItemPublicResponse(BaseModel):
    label_zh: str
    label_en: Optional[str] = None
    href: str
    sort_order: int = 0


class NavItemResponse(BaseModel):
    id: int
    label_zh: str
    label_en: Optional[str] = None
    href: str
    sort_order: int = 0
    visible: bool = True


class NavItemCreate(BaseModel):
    label_zh: str = Field(..., min_length=1, max_length=100)
    label_en: Optional[str] = Field(None, max_length=100)
    href: str = Field(..., min_length=1, max_length=200)
    sort_order: int = 0
    visible: bool = True


class NavItemUpdate(BaseModel):
    label_zh: Optional[str] = Field(None, min_length=1, max_length=100)
    label_en: Optional[str] = None
    href: Optional[str] = Field(None, min_length=1, max_length=200)
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


# ══════════════════════════════════════════════════════════
# Project (對外作品 — 從 crm_projects 的 public_* 欄位投影)
# ══════════════════════════════════════════════════════════

class ProjectPublicResponse(BaseModel):
    slug: str                       # public_slug
    title: str                      # public_title
    client: Optional[str] = None
    youtube_id: Optional[str] = None
    description: Optional[str] = None
    year: Optional[int] = None
    categories: list[str] = Field(default_factory=list)  # 製作類型 slug — kind=category
    tags: list[str] = Field(default_factory=list)         # 使用場景 slug — kind=tag
    # credits 雙模式：'block' (沿用 credits 結構化 blocks) / 'text' (純文字貼上)
    credits_mode: Literal["block", "text"] = "text"
    credits_text: Optional[str] = None
    thumbnail_url: Optional[str] = None                   # 自 YouTube API 組合
    cover_url: Optional[str] = None                       # OG image — sc.cover_url 鏡像
    featured: bool = False
    noindex: bool = False                                 # per-work 強制 noindex
    # SEO 301 來源舊 URL 陣列（給 Astro JSON-LD sameAs / markdown 鏡像列「曾用 URL」用）
    old_urls: list[str] = Field(default_factory=list)
    # 列表卡片用的 credits 摘要（「主演 邱雲福 · 導演 王小明」最多 ~30 字）
    credits_summary: str = ""


class ProjectPublicDetail(ProjectPublicResponse):
    # Block 結構（見下方 CreditBlock）；舊資料若仍是 dict / flat array
    # 由 Astro [slug].astro 端轉型，後端不做相容處理。
    credits: list[CreditBlock] = Field(default_factory=list)
    published_at: Optional[datetime] = None
    related: list[ProjectPublicResponse] = Field(default_factory=list)


class ProjectAdminUpdate(BaseModel):
    """更新作品的對外展示欄位（走 crm_projects 路由）。"""
    public: Optional[bool] = None
    public_slug: Optional[str] = None
    public_title: Optional[str] = None
    public_client: Optional[str] = None
    public_youtube_id: Optional[str] = None
    public_description: Optional[str] = None
    # public_credits / public_cover_url 不允許從 admin Tab 直接改 — credits / cover_url
    # 唯一 source of truth 是 crm_project_showcases（PM 從 showcase-edit 編輯），透過
    # _sync_showcase_to_public 反向 mirror。admin Tab 編輯路徑就只 toggle
    # public/featured/sort/category/noindex/old_slugs。
    public_old_slugs: Optional[list[str]] = None
    public_noindex: Optional[bool] = None
    public_year: Optional[int] = None
    public_featured: Optional[bool] = None
    public_sort_order: Optional[int] = None
    category_ids: Optional[list[int]] = None


class ProjectReorder(BaseModel):
    order: list[str]  # ordered list of crm_project.id


# Phase M-W：網站管理員在「作品集管理」新增作品
class WorkCreateRequest(BaseModel):
    # name 可選 — 跳過小表單流程下，前端不傳 name，後端塞 sentinel 「（未命名作品）」，
    # 由使用者進編輯頁後直接填。Overlay 關閉時若仍是 sentinel 且各 public_* 都空，
    # /works/{id}/if-skeleton 會把這筆 skeleton 刪掉避免留垃圾。
    name: Optional[str] = Field(None, max_length=200)
    client_id: Optional[str] = None
    year: Optional[int] = None
    # 一次帶上分類 + 標籤 ID（共用同一張 website_categories 表，後端不分 kind）。
    # 空 list / None 都允許，使用者也可以晚點再進編輯頁勾選。
    category_ids: Optional[list[int]] = None


class WorkCreateResponse(BaseModel):
    id: str
    name: str
    edit_url: str


class EditUrlResponse(BaseModel):
    edit_url: str


class ClientLookupItem(BaseModel):
    id: str
    name: str


class ClientLookupResponse(BaseModel):
    items: list[ClientLookupItem] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════
# Contact Inquiry（聯絡表單收件箱）
# ══════════════════════════════════════════════════════════

class ContactInquiryCreate(BaseModel):
    """公開端：使用者送出聯絡表單（Turnstile 驗證由 middleware 處理）。

    Email 基本格式驗證用 regex；深層 DNS 驗證留給 endpoint。
    """
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: Optional[str] = Field(None, max_length=50)
    company: Optional[str] = Field(None, max_length=200)
    service_type: Optional[str] = Field(None, max_length=50)
    budget_range: Optional[str] = Field(None, max_length=50)
    message: str = Field(..., min_length=1, max_length=5000)
    source: Optional[str] = Field("/contact", max_length=50)
    turnstile_token: str   # Cloudflare Turnstile response token


class ContactInquiryResponse(BaseModel):
    id: int
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    service_type: Optional[str] = None
    budget_range: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = None
    status: InquiryStatus = "new"
    converted_client_id: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: Optional[datetime] = None
    handled_at: Optional[datetime] = None
    handled_by: Optional[str] = None
    notes: Optional[str] = None


class ContactInquiryUpdate(BaseModel):
    status: Optional[InquiryStatus] = None
    notes: Optional[str] = None


class ContactInquiryConvert(BaseModel):
    """轉為正式 CRM client。"""
    tax_id: Optional[str] = None
    contact_person: Optional[str] = None
    notes: Optional[str] = None


# ══════════════════════════════════════════════════════════
# Website Settings（key-value 全站設定）
# ══════════════════════════════════════════════════════════

class SettingUpdate(BaseModel):
    """批次更新設定。"""
    values: dict[str, Any]


class SettingsResponse(BaseModel):
    settings: dict[str, Any]            # 所有 key-value


# ══════════════════════════════════════════════════════════
# Dashboard & Meta
# ══════════════════════════════════════════════════════════

class DashboardStats(BaseModel):
    month_inquiries: int = 0
    month_converted: int = 0
    total_public_works: int = 0
    featured_count: int = 0
    latest_inquiries: list[ContactInquiryResponse] = Field(default_factory=list)
    top_categories: list[CategoryAdminResponse] = Field(default_factory=list)


class WebsiteMeta(BaseModel):
    """全站 SEO / 基本 metadata（前端 build 時撈一次）。"""
    company_name_zh: str = ""
    company_name_en: str = ""
    tagline: str = ""
    subtitle: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    social: dict[str, str] = Field(default_factory=dict)
    seo_default_title: str = ""
    seo_default_description: str = ""
    seo_og_image: str = ""
    categories: list[CategoryPublicResponse] = Field(default_factory=list)
    # About 頁面文案（非工程師從官網管理 Tab 編輯 website_settings.about.*）
    about_intro_zh: str = ""
    about_intro_en: str = ""
    about_founded_year: str = ""
    about_team_intro_zh: str = ""
    # Home Hero 影片 YouTube ID（admin 從官網管理 Tab 設定 home.hero_youtube_id）
    home_hero_youtube_id: str = ""
    # SEO 索引控制（admin 從「網站設定」打開 seo.indexable 後對外網站才允許 Google 索引）
    indexable: bool = False
    # robots.txt 是否允許 AI 爬蟲（GPTBot / ClaudeBot / PerplexityBot / Google-Extended）
    ai_allow: bool = False
    # admin 自填的 llms.txt 內容；空則 /llms.txt 走自動生成
    llms_txt_body: str = ""
    # 頁面行銷文案覆寫（copy.<page>.<block>_<lang>）。巢狀 dict：copy[page][block_lang]。
    # 由 settings_service.get_meta 掃 copy.* key 組成；空則 Astro 各頁用硬寫 fallback。
    # 屬性名用 copy_overrides 避免 shadow pydantic BaseModel.copy()；wire/JSON key 仍是 "copy"（alias）。
    copy_overrides: dict[str, dict[str, str]] = Field(default_factory=dict, alias="copy")
    # 頂部導覽選單（visible=true ORDER BY sort_order）。空則 Header.astro 用硬寫 7 筆 fallback。
    nav: list[NavItemPublicResponse] = Field(default_factory=list)
    # 表單選項清單（forms.contact.service_types / budget_ranges）。每項 {value,label_zh,label_en}；
    # value 穩定不可變（後端 / CRM 存這個），只 label 可編。空則 ContactForm.astro 用硬寫 fallback。
    forms: dict[str, dict[str, list[dict[str, str]]]] = Field(default_factory=dict)


# ══════════════════════════════════════════════════════════
# SEO 內容（FAQ / Testimonial / QuickFact）
# ══════════════════════════════════════════════════════════

class WebsiteFAQResponse(BaseModel):
    id: int
    question_zh: str
    question_en: Optional[str] = None
    answer_zh: str
    answer_en: Optional[str] = None
    sort_order: int = 0
    visible: bool = True


class WebsiteFAQCreate(BaseModel):
    question_zh: str = Field(..., min_length=1, max_length=300)
    question_en: Optional[str] = Field(None, max_length=300)
    answer_zh: str = Field(..., min_length=1)
    answer_en: Optional[str] = None
    sort_order: int = 0
    visible: bool = True


class WebsiteFAQUpdate(BaseModel):
    question_zh: Optional[str] = None
    question_en: Optional[str] = None
    answer_zh: Optional[str] = None
    answer_en: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


class WebsiteTestimonialResponse(BaseModel):
    id: int
    author_zh: str
    author_en: Optional[str] = None
    role_zh: Optional[str] = None
    role_en: Optional[str] = None
    company: Optional[str] = None
    rating: int = 5
    content_zh: Optional[str] = None
    content_en: Optional[str] = None
    sort_order: int = 0
    visible: bool = True
    date_published: Optional[str] = None  # ISO date string


class WebsiteTestimonialCreate(BaseModel):
    author_zh: str = Field(..., min_length=1, max_length=100)
    author_en: Optional[str] = Field(None, max_length=100)
    role_zh: Optional[str] = Field(None, max_length=100)
    role_en: Optional[str] = Field(None, max_length=100)
    company: Optional[str] = Field(None, max_length=200)
    rating: int = Field(5, ge=1, le=5)
    content_zh: Optional[str] = None
    content_en: Optional[str] = None
    sort_order: int = 0
    visible: bool = True
    date_published: Optional[str] = None


class WebsiteTestimonialUpdate(BaseModel):
    author_zh: Optional[str] = None
    author_en: Optional[str] = None
    role_zh: Optional[str] = None
    role_en: Optional[str] = None
    company: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    content_zh: Optional[str] = None
    content_en: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None
    date_published: Optional[str] = None


class WebsiteQuickFactResponse(BaseModel):
    id: int
    label_zh: str
    label_en: Optional[str] = None
    value: str
    sort_order: int = 0
    visible: bool = True


class WebsiteQuickFactCreate(BaseModel):
    label_zh: str = Field(..., min_length=1, max_length=100)
    label_en: Optional[str] = Field(None, max_length=100)
    value: str = Field(..., min_length=1, max_length=300)
    sort_order: int = 0
    visible: bool = True


class WebsiteQuickFactUpdate(BaseModel):
    label_zh: Optional[str] = None
    label_en: Optional[str] = None
    value: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


# ══════════════════════════════════════════════════════════
# 站級獎項紀錄
# ══════════════════════════════════════════════════════════

AwardLevel = Literal["獲獎", "入圍"]


class WebsiteAwardResponse(BaseModel):
    id: int
    name_zh: str
    name_en: Optional[str] = None
    year: int
    category: Optional[str] = None
    org: Optional[str] = None
    level: AwardLevel = "獲獎"
    work_type: Optional[str] = None
    work_title: Optional[str] = None
    work_year: Optional[int] = None
    recipient: Optional[str] = None
    cert_url: Optional[str] = None
    sort_order: int = 0
    visible: bool = True


class WebsiteAwardCreate(BaseModel):
    name_zh: str = Field(..., min_length=1, max_length=200)
    name_en: Optional[str] = Field(None, max_length=200)
    year: int = Field(..., ge=1900, le=2100)
    category: Optional[str] = Field(None, max_length=200)
    org: Optional[str] = Field(None, max_length=200)
    level: AwardLevel = "獲獎"
    work_type: Optional[str] = Field(None, max_length=64)
    work_title: Optional[str] = Field(None, max_length=300)
    work_year: Optional[int] = Field(None, ge=1900, le=2100)
    recipient: Optional[str] = Field(None, max_length=200)
    cert_url: Optional[str] = Field(None, max_length=500)
    sort_order: int = 0
    visible: bool = True


class WebsiteAwardUpdate(BaseModel):
    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    year: Optional[int] = Field(None, ge=1900, le=2100)
    category: Optional[str] = None
    org: Optional[str] = None
    level: Optional[AwardLevel] = None
    work_type: Optional[str] = None
    work_title: Optional[str] = None
    work_year: Optional[int] = Field(None, ge=1900, le=2100)
    recipient: Optional[str] = None
    cert_url: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


# ── 公益合作 / 創作計畫 案例 ──
InitiativeLine = Literal["impact", "lab"]


class WebsiteInitiativeCreate(BaseModel):
    line: InitiativeLine
    project_id: Optional[str] = Field(None, max_length=32)   # 連動 crm_projects.id；空=獨立案例
    title: Optional[str] = Field(None, max_length=300)
    summary: Optional[str] = None
    cover_url: Optional[str] = None
    link_url: Optional[str] = Field(None, max_length=500)
    year: Optional[int] = Field(None, ge=1900, le=2100)
    sort_order: int = 0
    visible: bool = True


class WebsiteInitiativeUpdate(BaseModel):
    line: Optional[InitiativeLine] = None
    project_id: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    cover_url: Optional[str] = None
    link_url: Optional[str] = None
    year: Optional[int] = Field(None, ge=1900, le=2100)
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


class WebsiteRedirectCreate(BaseModel):
    from_path: str = Field(..., min_length=1, max_length=500)   # 舊站路徑 e.g. /commercial-film
    to_path: str = Field(..., min_length=1, max_length=500)     # 新站路徑 e.g. /works/category/commercial
    note: Optional[str] = Field(None, max_length=300)
    sort_order: int = 0
    visible: bool = True


class WebsiteRedirectUpdate(BaseModel):
    from_path: Optional[str] = Field(None, max_length=500)
    to_path: Optional[str] = Field(None, max_length=500)
    note: Optional[str] = Field(None, max_length=300)
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


class WebsiteAwardBulkImport(BaseModel):
    """貼上整段「歷年作品（獎項）」純文字 → 解析成 film-centric 結構。

    text 規則見 routers/website/admin_seo.py 的 _parse_awards_bulk。
    dry_run=true（預設）只回 parsed 結構不寫 DB；false 才真的建 row。
    now_year 供 runtime 無法呼叫 Date 時帶入（fallback 用）；空則用伺服器當年。
    """
    text: str = Field(..., min_length=1)
    dry_run: bool = True
    now_year: Optional[int] = Field(None, ge=1900, le=2100)


# ══════════════════════════════════════════════════════════
# 作品級 SEO / AI SEO Pipeline
# ══════════════════════════════════════════════════════════

class ProjectSeoKeyFact(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    value: str = Field(..., min_length=1, max_length=300)


class ProjectSeoFAQ(BaseModel):
    q: str = Field(..., min_length=1, max_length=300)
    a: str = Field(..., min_length=1, max_length=2000)


class ProjectSeoResponse(BaseModel):
    project_id: str
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    keywords: list[str] = []
    canonical_url: Optional[str] = None
    narrative_long: Optional[str] = None
    key_facts: list[ProjectSeoKeyFact] = []
    faqs: list[ProjectSeoFAQ] = []
    needs_ai_review: bool = True
    last_ai_review_at: Optional[str] = None
    last_ai_review_by: Optional[str] = None
    ai_review_notes: Optional[str] = None


class ProjectSeoUpdate(BaseModel):
    """PATCH body — 全欄位 optional。Claude / admin 一次填多少都行。
    keywords / key_facts / faqs 如果送了空 list = 明確清空（不是「不動」）。
    需要「不動」就不要帶該 key（exclude_unset 已處理）。
    """
    seo_title: Optional[str] = Field(None, max_length=120)
    seo_description: Optional[str] = Field(None, max_length=500)
    keywords: Optional[list[str]] = None
    canonical_url: Optional[str] = Field(None, max_length=500)
    narrative_long: Optional[str] = None
    key_facts: Optional[list[ProjectSeoKeyFact]] = None
    faqs: Optional[list[ProjectSeoFAQ]] = None
    ai_review_notes: Optional[str] = None


class ProjectSeoAuditItem(BaseModel):
    project_id: str
    title: str
    slug: Optional[str] = None
    client: Optional[str] = None
    year: Optional[int] = None
    completeness: int = 0          # 0-6
    needs_ai_review: bool = True
    last_ai_review_at: Optional[str] = None
    last_ai_review_by: Optional[str] = None


class ProjectSeoDraftContext(BaseModel):
    """draft endpoint 回給 Claude / admin 的完整上下文。"""
    project_id: str
    title: str
    client: Optional[str] = None
    year: Optional[int] = None
    youtube_id: Optional[str] = None
    description: Optional[str] = None
    credits: list = []
    credits_text: Optional[str] = None
    credits_mode: Literal["block", "text"] = "text"
    current_seo: ProjectSeoResponse


# ══════════════════════════════════════════════════════════
# Credit Roles & Templates（演職員職位庫 + 模板）
# ══════════════════════════════════════════════════════════

class CreditRoleResponse(BaseModel):
    id: int
    name_zh: str
    name_en: str
    sort_order: int = 0
    visible: bool = True
    usage_count: int = 0  # 多少件作品的 credits 用到此 role_id（admin list 才填）


class CreditRoleCreate(BaseModel):
    name_zh: str = Field(..., min_length=1, max_length=80)
    name_en: str = Field(..., min_length=1, max_length=80)
    sort_order: int = 0
    visible: bool = True


class CreditRoleUpdate(BaseModel):
    name_zh: Optional[str] = Field(None, min_length=1, max_length=80)
    name_en: Optional[str] = Field(None, min_length=1, max_length=80)
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


class CreditTemplateRoleSummary(BaseModel):
    """模板 list 回傳時 hydrate 進去的 role 摘要。"""
    id: int
    name_zh: str
    name_en: str


class CreditTemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    role_ids: list[int] = Field(default_factory=list)
    roles: list[CreditTemplateRoleSummary] = Field(default_factory=list)  # hydrated
    sort_order: int = 0


class CreditTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    role_ids: list[int] = Field(default_factory=list)
    sort_order: int = 0


class CreditTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    role_ids: Optional[list[int]] = None
    sort_order: Optional[int] = None


# ── Block 結構 schemas（showcase.credits / public_credits 共用，與 TS ICreditBlock 對齊） ──

class CreditEntry(BaseModel):
    duty: Optional[str] = ""
    name: str
    resume_url: Optional[str] = ""


class CreditBlock(BaseModel):
    """演職員 block — 一個職位下的多筆人員 entries。"""
    role_id: Optional[int] = None  # null = 自由分類（未從職位庫挑）
    name_zh: str = ""
    name_en: Optional[str] = ""
    entries: list[CreditEntry] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════
# Blog Posts（DB-as-truth，Notion 只是匯入器）
# ══════════════════════════════════════════════════════════

PostStatus = Literal["draft", "published", "archived"]


# ── 文章分類（M2M relation 用） ──

class PostCategoryResponse(BaseModel):
    id: int
    slug: str
    label_zh: str
    label_en: Optional[str] = None
    color: Optional[str] = None
    sort_order: int = 0
    visible: bool = True
    post_count: int = 0   # 列表查詢時 join 統計（admin 看，公開端點忽略）


class PostCategoryCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    label_zh: str = Field(..., min_length=1, max_length=100)
    label_en: Optional[str] = Field(None, max_length=100)
    color: Optional[str] = Field(None, max_length=20)
    sort_order: int = 0
    visible: bool = True


class PostCategoryUpdate(BaseModel):
    slug: Optional[str] = None
    label_zh: Optional[str] = None
    label_en: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


# ── 文章主體 ──

class PostListItem(BaseModel):
    """admin 列表頁用的精簡型（不含 body 避免 payload 過大）。"""
    id: int
    slug: str
    title: str
    excerpt: Optional[str] = None
    cover_url: Optional[str] = None
    category_slugs: list[str] = Field(default_factory=list)
    status: PostStatus = "draft"
    published_at: Optional[datetime] = None
    date_modified: Optional[datetime] = None
    sort_order: int = 0
    notion_page_id: Optional[str] = None
    redirect_count: int = 0    # len(old_urls)


class PostResponse(PostListItem):
    """完整型（含 body + per-post SEO）。"""
    body: list[Any] = Field(default_factory=list)
    read_time_min: Optional[int] = None
    imported_from_notion_at: Optional[datetime] = None
    # SEO
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    og_image_url: Optional[str] = None
    canonical_url: Optional[str] = None
    noindex: bool = False
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    ai_allow_override: Optional[bool] = None
    old_urls: list[str] = Field(default_factory=list)


class PostPublicResponse(BaseModel):
    """對外 Astro 端拿的形狀（去掉 admin-only 欄位）。"""
    slug: str
    title: str
    excerpt: Optional[str] = None
    cover_url: Optional[str] = None
    category_slugs: list[str] = Field(default_factory=list)
    body: list[Any] = Field(default_factory=list)
    published_at: Optional[datetime] = None
    date_modified: Optional[datetime] = None
    read_time_min: Optional[int] = None
    # 對外渲染需要的 SEO 欄位
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    og_image_url: Optional[str] = None
    canonical_url: Optional[str] = None
    noindex: bool = False
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    ai_allow_override: Optional[bool] = None


class PostCreate(BaseModel):
    slug: Optional[str] = None             # 不給 → service 自動 max+1
    title: str = Field(..., min_length=1)
    excerpt: Optional[str] = None
    cover_url: Optional[str] = None
    body: list[Any] = Field(default_factory=list)
    category_slugs: list[str] = Field(default_factory=list)
    status: PostStatus = "draft"
    published_at: Optional[datetime] = None
    sort_order: int = 0
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    og_image_url: Optional[str] = None
    canonical_url: Optional[str] = None
    noindex: bool = False
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    ai_allow_override: Optional[bool] = None
    old_urls: list[str] = Field(default_factory=list)
    faqs: list[Any] = Field(default_factory=list)        # [{"q","a"}]


class PostUpdate(BaseModel):
    """所有欄位 Optional — 用 model_dump(exclude_unset=True) partial update。"""
    slug: Optional[str] = None
    title: Optional[str] = None
    excerpt: Optional[str] = None
    cover_url: Optional[str] = None
    body: Optional[list[Any]] = None
    category_slugs: Optional[list[str]] = None
    status: Optional[PostStatus] = None
    published_at: Optional[datetime] = None
    sort_order: Optional[int] = None
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    og_image_url: Optional[str] = None
    canonical_url: Optional[str] = None
    noindex: Optional[bool] = None
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    ai_allow_override: Optional[bool] = None
    old_urls: Optional[list[str]] = None
    faqs: Optional[list[Any]] = None        # [{"q","a"}]


# ── Notion 匯入請求 ──

class NotionImportRequest(BaseModel):
    """admin Tab「實際同步」按鈕送來的請求。"""
    force: bool = False           # True = 已存在 notion_page_id 的文章整篇覆寫；
                                  # False（預設）= 跳過已存在的（DB 為真）


class PostImportResult(BaseModel):
    inserted: int = 0
    skipped: int = 0
    overwritten: int = 0
    failed: int = 0
    new_categories: list[str] = Field(default_factory=list)  # 自動新建的分類 slug
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0


# ── 單篇「公開 Notion 連結」匯入（免 token，走 loadPageChunk 公開端點）──

class NotionUrlImportRequest(BaseModel):
    """admin 貼一個公開分享的 Notion 頁面 URL（或裸 page id）。"""
    url: str = Field(..., min_length=8)


class NotionUrlImportResult(BaseModel):
    ok: bool = False
    post_id: Optional[int] = None
    slug: Optional[str] = None
    title: str = ""
    image_count: int = 0
    block_count: int = 0
    category_slugs: list[str] = Field(default_factory=list)
    published_at: Optional[datetime] = None
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None


# ── Redirects（軟+硬 301 來源） ──

class RedirectMap(BaseModel):
    """GET /api/website/redirects 回傳形狀。"""
    items: dict[str, str] = Field(default_factory=dict)  # {"/old": "/new", ...}
    count: int = 0


class RedirectSyncResult(BaseModel):
    """POST /api/website/admin/redirects/sync 回傳。"""
    synced: int
    last_sync: Optional[datetime] = None
    method: str = "nginx-hard-301"
    ok: bool = True
    error: Optional[str] = None


# ══════════════════════════════════════════════════════════
# Notion Sync（Phase M-E-8 部落格 Notion-as-CMS）
# ══════════════════════════════════════════════════════════

SyncType = Literal["preview", "sync"]


class NotionCategorySummary(BaseModel):
    id: str
    name: str
    label_zh: str
    label_en: str
    color: str = "default"
    count: int = 0


class NotionPostSummary(BaseModel):
    slug: str
    title: str
    category: Optional[str] = None
    category_label_zh: Optional[str] = None
    cover_url: Optional[str] = None
    excerpt: str = ""
    published_at: str = ""


class NotionSyncSkipped(BaseModel):
    title: str
    reason: str


class NotionSyncResult(BaseModel):
    ok: bool
    sync_type: SyncType
    posts_count: int = 0
    categories_count: int = 0
    posts: list[NotionPostSummary] = Field(default_factory=list)
    categories: list[NotionCategorySummary] = Field(default_factory=list)
    skipped: list[NotionSyncSkipped] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None
    posts_json_path: Optional[str] = None
    categories_json_path: Optional[str] = None
    rebuild_queued: bool = False


# ══════════════════════════════════════════════════════════
# Team (官網團隊頁顯示覆寫 — crm_staff 的 website_* 欄位)
# ══════════════════════════════════════════════════════════
# 正本 name/role/photo_url/bio 在 CRM「人力資源」，官網管理端**永不寫入**正本，
# 只批次更新 show_on_website + website_*。see routers/website/admin_team.py


class TeamOverrideItem(BaseModel):
    """單筆團隊成員的官網顯示覆寫（不含正本欄位）。

    id 為 crm_staff.id（String）。website_* 欄位皆 exclude_unset 友善：
    沒帶的欄位 admin_team router 不會 setattr，已有值保持不變。
    """
    id: str
    show_on_website: Optional[bool] = None
    website_title: Optional[str] = Field(None, max_length=128)
    website_photo_url: Optional[str] = Field(None, max_length=512)
    website_bio: Optional[str] = None
    website_sort_order: Optional[int] = None


class TeamOverrideBatch(BaseModel):
    """PUT /api/website/admin/team body — 批次更新。"""
    items: list[TeamOverrideItem] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════
# 社群編輯自動化（Phase N-soc 階段一 — website_social_posts 佇列）
# ══════════════════════════════════════════════════════════
# social_runner 產草稿 → 後台「📣 社群工作台」審核。
# 設定（social.*）走 dict payload（同 seo runner settings），不另建 schema。


class SocialPostResponse(BaseModel):
    """單筆社群文稿（GET /admin/social/posts 列表項）。"""
    id: str
    source_type: str                       # work / post / initiative / evergreen
    source_id: str
    platform: str                          # facebook / instagram / threads
    content: Optional[str] = None
    media_url: Optional[str] = None
    status: str                            # draft / approved / published / rejected
    scheduled_at: Optional[datetime] = None
    published_url: Optional[str] = None
    error_detail: Optional[str] = None
    run_id: Optional[str] = None
    reviewed_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SocialPostUpdate(BaseModel):
    """編輯改稿：只開放 content / media_url / scheduled_at 三欄。

    scheduled_at 為階段二發佈器預留（階段一前端未接、runner 不寫 — 非漏接）。
    """
    content: Optional[str] = None
    media_url: Optional[str] = Field(None, max_length=512)
    scheduled_at: Optional[datetime] = None


class SocialPostPublishedBody(BaseModel):
    """POST /admin/social/posts/{id}/published body — 回寫實際貼文連結。"""
    published_url: str = Field(..., min_length=1, max_length=512)
