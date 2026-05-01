"""db/models_website/credit_role.py
---
全站演職員職位主檔（中英對照）。

對外網站作品的演職員表升級為 block 結構後，每個 block 引用此表的 role_id；
admin Tab「🎬 演職員管理」可 CRUD 職位、調整中英對照、控制顯示順序與可見性。

被引用對象：
- crm_project_showcases.credits（block 結構：[{role_id, entries:[...]}]）
- 對外 crm_projects.public_credits（snapshot — block 結構，role 名稱 inline 存入避免 join）
"""
from sqlalchemy import Column, String, Integer, Boolean, DateTime, func, Index
from db.models import Base


class WebsiteCreditRole(Base):
    __tablename__ = "website_credit_roles"

    id = Column(Integer, primary_key=True)
    name_zh = Column(String(80), nullable=False, unique=True)   # "演員"
    name_en = Column(String(80), nullable=False)                # "Cast"
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_credit_role_visible_sort", "visible", "sort_order"),
    )
