"""db/models_website/service.py
---
WebsiteService: 服務項目（首頁「服務」區塊展示用）。

Table: website_services
Soft FK: related_category_id → website_categories.id
      (點此服務的 CTA → 跳到該分類的作品列表)
"""
from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, func, Index
from db.models import Base


class WebsiteService(Base):
    __tablename__ = "website_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    icon = Column(String(50))                   # Lucide icon name
    short_desc = Column(String(300))
    full_desc = Column(Text)
    # Phase M 英文版（transcreation；空則前端 fallback 中文）
    title_en = Column(String(200))
    short_desc_en = Column(String(500))
    full_desc_en = Column(Text)
    cover_image = Column(Text)
    related_category_id = Column(Integer)
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_websvc_visible", "visible"),
        Index("idx_websvc_sort", "sort_order"),
    )
