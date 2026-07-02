"""db/models_website/redirect.py
---
Legacy 舊站 → 新站「頁面級」301 轉址。

用途：舊 WordPress 站的頁面 / 分類 / 標籤 URL（如 /commercial-film、/contact-us、
/portfolio-category/commercial-film）遷移到新站對應頁。這類轉址「不綁特定作品或文章」，
所以無法用 works.public_old_slugs / posts.old_urls 表達 —— 由這張表補齊。

from_path 存正規化後的相對路徑（無結尾斜線，如 /commercial-film）。實際 nginx 生成時
（publish_update.sync_redirects_to_nas）會同時輸出帶/不帶結尾斜線兩種 exact-match 規則，
因為舊 Yoast URL 都帶結尾 /。

合併：public.py 的 GET /redirects 把 works + posts + 這張表 merge（作品/文章優先）。
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from db.models import Base


class WebsiteRedirect(Base):
    """Legacy 頁面級 301 轉址（from_path → to_path）。"""
    __tablename__ = "website_redirects"

    id = Column(Integer, primary_key=True)
    from_path = Column(String(500), unique=True, nullable=False)  # 正規化舊路徑 e.g. /commercial-film
    to_path = Column(String(500), nullable=False)                 # 新路徑 e.g. /works/category/commercial
    note = Column(String(300))                                    # admin 備註（來源說明）
    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)        # false = 暫停此轉址（不進 nginx）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
