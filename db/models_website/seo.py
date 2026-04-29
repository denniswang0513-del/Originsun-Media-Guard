"""db/models_website/seo.py
---
SEO 內容資料表：FAQ / Testimonial / QuickFact。

對應前端「🔍 SEO / AI SEO 管理」子視圖的 3 個 CRUD 卡片。
所有資料都會被輸出為 schema.org JSON-LD（FAQPage / Review / dl 條列事實），
admin 編輯後 60s debounce 觸發 Astro rebuild → 對外網站立即更新。
"""
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, Date, func
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
