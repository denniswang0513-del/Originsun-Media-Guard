"""db/models_website/series.py
---
WebsiteSeries: 作品系列（跨專案策展集合）— 作品牆把成員摺疊成一張系列卡、
系列有自己的對外頁 /works/series/{slug}（封面 + 介紹 + 成員作品格）。

Table: website_series
成員關係在 crm_project_showcase.series_id（soft FK → 此表 id）+ series_order；
一支作品最多屬一個系列。與「同專案其他作品」（1:N read-time 推導）是兩層概念：
系列 = 跨專案、有名字有頁面的策展；同專案互連照舊服務沒歸系列的多支專案。

⚠ slug 是 URL 永久承諾（發布後勿改 — 會斷外部連結與收錄）。
公開端 GET /api/website/series 走 visible=true；admin 卡在官網管理作品子視圖。
"""
from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, func, Index
from sqlalchemy.dialects.postgresql import JSONB

from db.models import Base


class WebsiteSeries(Base):
    __tablename__ = "website_series"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(80), unique=True, nullable=False)      # URL：/works/series/{slug}
    title_zh = Column(String(200), nullable=False)
    title_en = Column(String(300))
    description_zh = Column(Text)                               # 系列頁介紹（也是 SEO description 來源）
    description_en = Column(Text)
    cover_image = Column(Text)                                  # 空 → fallback 第一支成員封面
    old_slugs = Column(JSONB)                                   # slug 改名自動記舊值 → 301 轉址來源
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)     # 隱藏 → 作品牆不摺疊、系列頁不生成
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_webseries_visible_sort", "visible", "sort_order"),
    )
