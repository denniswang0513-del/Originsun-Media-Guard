"""db/models_website/translation_state.py
---
英文翻譯（transcreation）工作流狀態 —— 每個實體（作品/文章/服務）一列。

實際英文內容存在各實體自己的 `_en` 欄；這張表只追蹤「翻譯工作流」：
- source_hash：中文來源欄位的雜湊，用來偵測「中文改過 → 英文過時」需重譯。
- status：pending（待翻）/ translated（AI 已翻待審）/ approved（人工核准）/ needs_review（過時或重譯待審）。
- last_translated_by：'ai' 或 admin 使用者名。

比照 AI SEO（website_project_seo.needs_ai_review）的 review 流程。
"""
from sqlalchemy import Column, String, Text, DateTime, func
from db.models import Base


class WebsiteTranslationState(Base):
    __tablename__ = "website_translation_state"

    entity_type = Column(String(16), primary_key=True)   # 'work' | 'post' | 'service'
    entity_id = Column(String(64), primary_key=True)      # crm id / post id / service id（文字）
    source_hash = Column(String(64))                      # 中文來源欄位雜湊 → 過時偵測
    status = Column(String(20), nullable=False, default="pending")
    last_translated_at = Column(DateTime(timezone=True))
    last_translated_by = Column(String(64))               # 'ai' | username
    reviewed_at = Column(DateTime(timezone=True))
    reviewed_by = Column(String(64))
    notes = Column(Text)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
