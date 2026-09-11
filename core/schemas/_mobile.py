"""core/schemas/_mobile.py — CRM 手機版 BFF。

拆自 core/schemas.py（2026-09-11，2,000 行剛好卡在單次讀取上限）。
界畫在原本的分節註解上、零 class 搬家；對外仍是 `from core.schemas import X`。
"""
from pydantic import BaseModel  # type: ignore


# ── CRM 手機版（routers/api_crm_mobile.py；docs/CRM_MOBILE_PLAN.md §3）──────

class MobileNotePayload(BaseModel):
    """手機版「加一則備註」：只有內容；時間與誰由後端補（手機時鐘不可信）。
    長度 1..500 在端點驗（回中文 422），這裡不用 Field 上限——超長要講清楚是備註不是雜支。"""
    text: str = ""


class MobileQuoteStatusPayload(BaseModel):
    """手機版報價改狀態：只改 `status`（既有 PUT 要整包 items，手機不該重送）。
    `activate`＝簽回時順便把專案推進「製作」（走專案狀態同一支 helper）。"""
    status: str = ""
    activate: bool = False
