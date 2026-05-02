"""db/models_website/seo.py
---
SEO 內容資料表：站級 FAQ / Testimonial / QuickFact + 作品級 ProjectSeo。

站級三表對應前端「🔍 SEO / AI SEO 管理」子視圖的 CRUD 卡片，輸出為 schema.org
JSON-LD（FAQPage / Review / dl 條列事實）。

作品級 WebsiteProjectSeo 是 1:1 擴充 crm_projects，存 AI/SEO 生成 pipeline 內容
（seo_title / seo_description / keywords / narrative_long / key_facts / faqs）+
review tracking。Astro [slug].astro / [slug].md / llms-full.txt 都會吃這些欄位。
"""
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, Date, func
from sqlalchemy.dialects.postgresql import JSONB
from db.models import Base


class WebsiteFAQ(Base):
    """常見問題（FAQPage schema 來源）。"""
    __tablename__ = "website_faqs"

    id = Column(Integer, primary_key=True)
    question_zh = Column(String(300), nullable=False)
    question_en = Column(String(300))
    answer_zh = Column(Text, nullable=False)
    answer_en = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WebsiteTestimonial(Base):
    """客戶證言（Review + AggregateRating schema 來源）。"""
    __tablename__ = "website_testimonials"

    id = Column(Integer, primary_key=True)
    author_zh = Column(String(100), nullable=False)
    author_en = Column(String(100))
    role_zh = Column(String(100))
    role_en = Column(String(100))
    company = Column(String(200))
    rating = Column(Integer, nullable=False, default=5)   # 1-5
    content_zh = Column(Text)
    content_en = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    date_published = Column(Date)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WebsiteQuickFact(Base):
    """Quick Facts（AI 搜尋條列事實 — 成立年/地點/團隊規模/獎項...）。"""
    __tablename__ = "website_quick_facts"

    id = Column(Integer, primary_key=True)
    label_zh = Column(String(100), nullable=False)
    label_en = Column(String(100))
    value = Column(String(300), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WebsiteProjectSeo(Base):
    """作品級 SEO / AI SEO 內容（1:1 對應 crm_projects.id）。

    跟 crm_projects.public_* 區隔：那些是 PM 編輯展示用的；這裡是 SEO/AI 生成
    pipeline 寫入的（seo_title 跟 H1 區分、narrative_long 給 LLM 看不顯示、
    faqs 渲染成 FAQPage schema...）。

    Pipeline 流程：
      1. needs_ai_review=True → 出現在 audit endpoint
      2. Claude/admin 用 draft endpoint 拿 work context
      3. PATCH endpoint 寫入內容、自動設 last_ai_review_at + by
      4. approve endpoint 標 needs_ai_review=False、觸發 rebuild
    """
    __tablename__ = "website_project_seo"

    project_id = Column(String(32), primary_key=True)  # FK soft → crm_projects.id

    # SEO meta（跟 H1 / public_description 區分；空 → fallback 到 public_description）
    seo_title = Column(String(120))                    # browser tab + SERP title（建議 50-60 字以內）
    seo_description = Column(String(500))              # SERP description（建議 60-160 字）
    keywords = Column(JSONB, nullable=False, default=list)  # ["導演 訪談", ...]
    # Canonical URL — 跨站發布時指向原作（少用；空 → Astro 自動算 site/works/{slug}）
    canonical_url = Column(String(500))

    # AI-targeted 長文（不顯示在 HTML 主體、注入 .md / llms-full.txt 給 LLM crawler）
    narrative_long = Column(Text)                      # 200-600 字劇情/技術 narrative
    key_facts = Column(JSONB, nullable=False, default=list)  # [{"label":"拍攝地","value":"東京"}]
    faqs = Column(JSONB, nullable=False, default=list)       # [{"q":"...","a":"..."}]

    # Pipeline tracking
    needs_ai_review = Column(Boolean, nullable=False, default=True)
    last_ai_review_at = Column(DateTime(timezone=True))
    last_ai_review_by = Column(String(64))             # username 或 "claude"
    ai_review_notes = Column(Text)                     # Claude 留給下次的備忘

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
