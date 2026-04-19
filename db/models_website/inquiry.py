"""db/models_website/inquiry.py
---
WebsiteContactInquiry: 聯絡表單收件箱。

Table: website_contact_inquiries
Status: new / in_progress / converted / spam
Soft FK: converted_client_id → crm_clients.id（轉成正式客戶後的關聯）
"""
from sqlalchemy import Column, String, Text, Integer, DateTime, func, Index
from db.models import Base


class WebsiteContactInquiry(Base):
    __tablename__ = "website_contact_inquiries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100))
    email = Column(String(200))
    phone = Column(String(50))
    company = Column(String(200))
    service_type = Column(String(50))
    budget_range = Column(String(50))
    message = Column(Text)
    source = Column(String(50))                 # /contact / /works/[slug] etc.
    status = Column(String(20), nullable=False, default="new")
    converted_client_id = Column(String(32))
    ip_address = Column(String(50))
    user_agent = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    handled_at = Column(DateTime(timezone=True))
    handled_by = Column(String(100))
    notes = Column(Text)

    __table_args__ = (
        Index("idx_inq_status", "status"),
        Index("idx_inq_created", "created_at"),
    )
