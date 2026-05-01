"""db/models_website/credit_template.py
---
演職員模板（職位組合預設）。

admin 在編輯作品時可一鍵套用模板（例：「商業廣告標準」自動展開
製作/導演/攝影指導/燈光/剪輯/後製/演員 7 個 block，再各自填行）。

role_ids 是已排序的 role.id list — 套用時順序即 block 顯示順序。
"""
from sqlalchemy import Column, String, Integer, Text, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from db.models import Base


class WebsiteCreditTemplate(Base):
    __tablename__ = "website_credit_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    description = Column(Text)
    role_ids = Column(JSONB, nullable=False, default=list)  # [3, 1, 5, 7, 4] 已排序
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
