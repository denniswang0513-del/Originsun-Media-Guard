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


class CategoryAdminResponse(BaseModel):
    id: int
    slug: str
    name_zh: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None
    sort_order: int = 0
    visible: bool = True
    project_count: int = 0


class CategoryCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    name_zh: str = Field(..., min_length=1, max_length=100)
    name_en: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    cover_image: Optional[str] = None
    sort_order: int = 0
    visible: bool = True


class CategoryUpdate(BaseModel):
    slug: Optional[str] = None
    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None
    sort_order: Optional[int] = None
    visible: Optional[bool] = None


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
# Project (對外作品 — 從 crm_projects 的 public_* 欄位投影)
# ══════════════════════════════════════════════════════════

class ProjectPublicResponse(BaseModel):
    slug: str                       # public_slug
    title: str                      # public_title
    client: Optional[str] = None
    youtube_id: Optional[str] = None
    description: Optional[str] = None
    year: Optional[int] = None
    categories: list[str] = Field(default_factory=list)  # category slugs
    thumbnail_url: Optional[str] = None                   # 自 YouTube API 組合
    featured: bool = False


class ProjectPublicDetail(ProjectPublicResponse):
    credits: dict[str, Any] = Field(default_factory=dict)
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
    public_credits: Optional[dict[str, Any]] = None
    public_year: Optional[int] = None
    public_featured: Optional[bool] = None
    public_sort_order: Optional[int] = None
    category_ids: Optional[list[int]] = None


class ProjectReorder(BaseModel):
    order: list[str]  # ordered list of crm_project.id


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
    categories: list[CategoryPublicResponse] = Field(default_factory=list)
    # About 頁面文案（非工程師從官網管理 Tab 編輯 website_settings.about.*）
    about_intro_zh: str = ""
    about_intro_en: str = ""
    about_founded_year: str = ""
    about_team_intro_zh: str = ""
    # Home Hero 影片 YouTube ID（admin 從官網管理 Tab 設定 home.hero_youtube_id）
    home_hero_youtube_id: str = ""


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
