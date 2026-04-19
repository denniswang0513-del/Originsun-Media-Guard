"""db/models_website/project_category.py
---
WebsiteProjectCategory: 作品↔分類多對多關聯表。

Table: website_project_categories
Soft FK:
- project_id → crm_projects.id (VARCHAR(32) 在既有 schema)
- category_id → website_categories.id
"""
from sqlalchemy import Column, String, Integer, PrimaryKeyConstraint, Index
from db.models import Base


class WebsiteProjectCategory(Base):
    __tablename__ = "website_project_categories"

    project_id = Column(String(32), nullable=False)
    category_id = Column(Integer, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("project_id", "category_id"),
        Index("idx_wpc_category", "category_id"),
    )
