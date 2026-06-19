"""db/models_website/nav_item.py
---
WebsiteNavItem: 對外官網頂部導覽選單項目（label + href + 排序 + 顯示控制）。

Table: website_nav_items
公開端 GET /api/website/nav 走 visible=true ORDER BY sort_order；
admin「🧭 導覽選單」子視圖可改名 / 排序 / 顯示隱藏 / 新增刪除。

對外 Header.astro fetch 此 endpoint；空（表 / endpoint 都拿不到）→ fallback
到 Header.astro 內硬寫的 7 筆 navItems，確保「未編輯前對外網站零變化」。
"""
from sqlalchemy import Column, String, Boolean, Integer, DateTime, func, Index
from db.models import Base


class WebsiteNavItem(Base):
    __tablename__ = "website_nav_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    label_zh = Column(String(100), nullable=False)
    label_en = Column(String(100))
    href = Column(String(200), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_webnav_visible_sort", "visible", "sort_order"),
    )
