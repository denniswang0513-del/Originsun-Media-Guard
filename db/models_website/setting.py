"""db/models_website/setting.py
---
WebsiteSetting: 網站全站設定 key-value store。

Table: website_settings
用途：存放品牌資訊、SEO 預設、GA4 ID、Turnstile key 等不常變動的全站設定，
避免未來每加一項就 ALTER TABLE 現有設定表。
"""
from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from db.models import Base


class WebsiteSetting(Base):
    __tablename__ = "website_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSONB)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by = Column(String(100))
