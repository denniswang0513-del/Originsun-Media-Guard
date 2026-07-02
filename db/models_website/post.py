"""db/models_website/post.py
---
部落格文章 / 分類 / M2M 中介表。

3 張表：
- WebsitePost:                文章主檔（含 metadata + body JSONB + per-post SEO + old_urls）
- WebsitePostCategory:       文章分類主檔（admin CRUD，與作品分類 website_categories 分開）
- WebsitePostCategoryLink:   M2M 中介表

設計決策：
- DB 是 source of truth，Notion 只負責「給新東西」+「強制重置某篇」。
- status 三態：draft / published / archived（取代簡單的 visible boolean）。
- published_at 可未來時間 = 排程；公開 endpoint filter status='published' AND <= now。
- old_urls JSONB 儲舊 URL 陣列，用於 SEO 301 軟+硬轉址。
- date_modified 在 service 層每次寫入時自動更新（給 NewsArticle.dateModified 用）。
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from db.models import Base


class WebsitePost(Base):
    """部落格文章主檔。"""
    __tablename__ = "website_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(50), unique=True, nullable=False)         # URL: /news/11
    title = Column(Text, nullable=False)
    excerpt = Column(Text)
    cover_url = Column(Text)
    body = Column(JSONB, nullable=False, default=list)             # PostBlock[]
    published_at = Column(DateTime(timezone=True))                  # 可未來=排程
    date_modified = Column(DateTime(timezone=True))                 # service 層維護
    read_time_min = Column(Integer)

    # 三態：draft（草稿）/ published（已發布）/ archived（下架但保留 URL）
    status = Column(String(16), nullable=False, default="draft")
    sort_order = Column(Integer, nullable=False, default=0)

    # Notion 同步追蹤（不存在 = admin 直接新建）
    notion_page_id = Column(String(64))                             # UUID 32 字元 + dashes
    imported_from_notion_at = Column(DateTime(timezone=True))

    # ── per-post SEO 覆寫（全部選填，空則 fallback 自動推算） ──
    seo_title = Column(String(200))
    seo_description = Column(String(300))
    og_image_url = Column(Text)
    canonical_url = Column(Text)
    noindex = Column(Boolean, nullable=False, default=False)
    author_name = Column(String(100))
    author_url = Column(Text)
    # NULL = 跟隨站台 seo.ai_allow；TRUE/FALSE = 個別頁覆寫
    ai_allow_override = Column(Boolean)

    # SEO 301 來源舊路徑（軟 301 from Astro + 硬 301 from nginx）
    old_urls = Column(JSONB, nullable=False, default=list)          # string[]

    # AI SEO runner 生成的常見問題（FAQPage JSON-LD + 文章底部可見區段）
    faqs = Column(JSONB, nullable=False, default=list)             # [{"q","a"}]

    # ── Phase M 英文版：_en 翻譯欄（transcreation；空則前端 fallback 中文）──
    title_en = Column(Text)
    excerpt_en = Column(Text)
    body_en = Column(JSONB, nullable=False, default=list)          # PostBlock[]（英文）
    seo_title_en = Column(String(200))
    seo_description_en = Column(String(300))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # 公開查詢：WHERE status='published' AND published_at<=NOW() ORDER BY published_at DESC
        Index("idx_post_status_pub", "status", "published_at"),
        # Notion 重匯 match
        Index("idx_post_notion_page", "notion_page_id"),
    )


class WebsitePostCategory(Base):
    """部落格分類（與 website_categories 不同 — 作品分類 vs 文章分類分開）。"""
    __tablename__ = "website_post_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(50), unique=True, nullable=False)
    label_zh = Column(String(100), nullable=False)
    label_en = Column(String(100))
    color = Column(String(20))                                       # Notion 'blue' 或 hex
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_postcat_visible_sort", "visible", "sort_order"),
    )


class WebsitePostCategoryLink(Base):
    """post ↔ post_category 多對多中介表。"""
    __tablename__ = "website_post_category_links"

    post_id = Column(Integer, ForeignKey("website_posts.id", ondelete="CASCADE"),
                     primary_key=True)
    category_id = Column(Integer, ForeignKey("website_post_categories.id", ondelete="CASCADE"),
                         primary_key=True)

    __table_args__ = (
        Index("idx_pcl_category", "category_id"),
    )
