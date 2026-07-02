"""db/models_website/initiative.py
---
公益合作 / 創作計畫 —— 公司在日常業務外經營的兩條線。

每條線（line）一組案例（entry）。案例可「連動作品集的作品」（project_id 指向
crm_projects.id，渲染時自動帶封面/標題、點擊跳作品頁），也可「獨立案例」
（project_id 空，自填 title/cover_url/link_url，給不在作品集裡的公益活動/創作）。

對應：
- 首頁 ABOUT US 段兩個按鈕 → /impact（公益合作）、/lab（創作計畫）
- 後台「🤝 公益 & 創作」子視圖 CRUD
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Index, func
from db.models import Base


class WebsiteInitiative(Base):
    """公益合作 / 創作計畫 案例。"""
    __tablename__ = "website_initiatives"

    id = Column(Integer, primary_key=True)
    line = Column(String(16), nullable=False)        # 'impact'（公益合作）| 'lab'（創作計畫）

    # 作品連動：指向 crm_projects.id（soft FK，無實際約束）。空 = 獨立案例。
    project_id = Column(String(32))

    # 獨立案例自填；連動作品時可空（渲染 fallback 用作品的對應欄位）。
    title = Column(String(300))
    summary = Column(Text)
    cover_url = Column(Text)
    link_url = Column(String(500))                    # 外連；連動作品時空 → /works/{slug}
    year = Column(Integer)

    sort_order = Column(Integer, nullable=False, default=0)
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_initiative_line_sort", "line", "visible", "sort_order"),
    )
