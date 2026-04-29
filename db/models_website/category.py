"""db/models_website/category.py
---
WebsiteCategory: 作品分類主檔（可 CRUD、多對多關聯作品）。

Table: website_categories
Referenced by: website_project_categories (多對多)、website_services.related_category_id
"""
from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, func, Index
from db.models import Base


class WebsiteCategory(Base):
    __tablename__ = "website_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(50), unique=True, nullable=False)  # URL: /works?category=commercial
    name_zh = Column(String(100), nullable=False)
    name_en = Column(String(100))
    description = Column(Text)
    cover_image = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    # 'category' = 製作類型（形象/廣告/MV…）；'tag' = 使用場景（展覽/講座…）。
    # 共用同一張表 + slug namespace，渲染時前端依 kind 分兩排 chip。
    kind = Column(String(16), nullable=False, default="category")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_webcat_visible", "visible"),
        Index("idx_webcat_sort", "sort_order"),
        Index("idx_webcat_kind", "kind"),
    )
