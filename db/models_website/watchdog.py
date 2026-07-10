"""db/models_website/watchdog.py
---
站點守衛（Site Watchdog）—— 對外官網的健康監控狀態 + 事件流水。

兩張表：
- WebsiteIndexStatus:     每個公開 URL 的 Google 索引現況（GSC URL Inspection 快照，
                          url 當主鍵，每次掃描 upsert 覆蓋）
- WebsiteWatchdogEvent:   守衛觸發的告警 / 恢復事件（探測到轉址/狀態碼/內容異常、
                          索引消失/過期、GSC 錯誤、恢復…）流水帳，供後台事件牆呈現

資料來源：
- index_status 由 gsc_service.inspect_url 逐 URL 撈回正規化 dict 後 upsert。
- watchdog_events 由 runner 比對「探測結果 vs 上次狀態」時 append。
"""
from sqlalchemy import Column, String, Text, DateTime, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from db.models import Base


class WebsiteIndexStatus(Base):
    """單一公開 URL 的 Google 索引狀態快照（GSC URL Inspection，每次掃描 upsert）。"""
    __tablename__ = "website_index_status"

    url = Column(String(512), primary_key=True)
    verdict = Column(String(32), nullable=True)            # PASS / PARTIAL / FAIL / NEUTRAL
    coverage_state = Column(String(160), nullable=True)    # e.g. "Submitted and indexed"
    indexing_state = Column(String(64), nullable=True)
    robots_txt_state = Column(String(32), nullable=True)
    page_fetch_state = Column(String(64), nullable=True)
    google_canonical = Column(String(512), nullable=True)  # Google 選定的 canonical
    user_canonical = Column(String(512), nullable=True)    # 頁面自宣告的 canonical
    last_crawl_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)                    # 該 URL inspect 失敗訊息
    checked_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())


class WebsiteWatchdogEvent(Base):
    """守衛事件流水（告警 / 恢復）。"""
    __tablename__ = "website_watchdog_events"

    id = Column(String(32), primary_key=True)              # uuid4().hex[:32]
    level = Column(String(16), nullable=False)             # critical | warn | info
    # probe_redirect / probe_status / probe_content / index_missing /
    # index_stale / gsc_error / recovered
    kind = Column(String(48), nullable=False)
    title = Column(String(255), nullable=False)
    detail = Column(JSONB, nullable=True)                  # 診斷細節（探測到什麼 / 差異）
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 事件牆最新在上：ORDER BY created_at DESC
        Index("idx_watchdog_events_created", created_at.desc()),
    )
