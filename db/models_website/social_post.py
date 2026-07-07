"""db/models_website/social_post.py
---
社群編輯自動化（Phase N-soc 階段一）—— AI 產出的社群文稿佇列。

social_runner 每日掃新內容（作品/文章/公益案例；無新內容走常青輪播）→
每選題 × 每啟用平台用 claude 產一篇文稿 → 寫入本表（status=draft）。
編輯在後台「📣 社群工作台」審核：改稿 / 核准 / 退回；階段二發佈器
（n8n 中繼）發文成功後回寫 published_url → status=published。

- source_type：'work' | 'post' | 'initiative' | 'evergreen'（常青重推的舊作品）
- source_id：來源實體 id（crm_projects.id / website_posts.id / website_initiatives.id，統一存文字）
- status：draft（待審）/ approved（已核准，等發佈）/ published / rejected
- scheduled_at：預定發佈時間（階段二發佈器用；階段一先存）
- error_detail：發佈失敗診斷（階段二回寫；階段一保留欄位）
- run_id：產出它的那一輪 runner run（uuid4().hex，除錯/追溯用）
"""
from sqlalchemy import Column, String, Text, DateTime, func
from db.models import Base


class WebsiteSocialPost(Base):
    """AI 產出的社群文稿（多平台，一稿一列）。"""
    __tablename__ = "website_social_posts"

    id = Column(String(32), primary_key=True)             # uuid4().hex
    source_type = Column(String(16), nullable=False)      # 'work' | 'post' | 'initiative' | 'evergreen'
    source_id = Column(String(64), nullable=False)        # 來源實體 id（文字）
    platform = Column(String(16), nullable=False)         # 'facebook' | 'instagram' | 'threads'
    content = Column(Text)                                 # 文稿本文（AI 產出，編輯可改）
    media_url = Column(String(512))                        # 建議配圖（來源的 OG 圖 / 封面）
    status = Column(String(16), nullable=False, default="draft")  # draft/approved/published/rejected
    scheduled_at = Column(DateTime(timezone=True))         # 預定發佈時間（階段二）
    published_url = Column(String(512))                    # 實際貼文連結（發佈後回寫）
    error_detail = Column(Text)                            # 發佈失敗診斷（階段二）
    run_id = Column(String(32))                            # 產出批次（social_runner run）
    reviewed_by = Column(String(64))                       # 核准/退回的 admin 使用者名
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
