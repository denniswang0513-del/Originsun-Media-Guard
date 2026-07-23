"""core/journal_logic.py — 每週工作日誌純函式：週正規化 + 可編輯窗 + 條目清洗。

照 core/hr_logic.py 慣例：純函式、無 I/O，單元測試在 tests/unit/test_journal.py。
routers/api_journal.py 只留 I/O。
"""
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

# 單區條目數上限 / 單條字數上限（超過 → 400）
MAX_ENTRIES_PER_SECTION = 50
MAX_ENTRY_LEN = 2000


def week_start_of(d) -> date:
    """任何日期 → 該週週一（date）。接受 date 或 datetime；週以週一起算
    （週日輸入 → 回前一個週一）。"""
    if isinstance(d, datetime):
        d = d.date()
    return d - timedelta(days=d.weekday())


def editable_window_ok(week_start: date, today: Optional[date] = None) -> bool:
    """可編輯窗（owner 規則 2026-07-22）：week_start <= 本週週一 + 7 天
    ＝ 過往任何週、當週、下一週恆可編；更遠未來拒絕（403「只能編輯到下一週」）。"""
    today = today or date.today()
    return week_start <= week_start_of(today) + timedelta(days=7)


def clean_entries(raw) -> Tuple[List[str], Optional[str]]:
    """單一區塊條目清洗：strip + 去空（保持原順序）。

    回 (cleaned, err)：err=None 合法；超限回中文訊息（直接給 400 detail）。
    上限在清洗後檢查 — 純空白條目不佔額度。
    """
    cleaned = [s.strip() for s in (raw or []) if isinstance(s, str) and s.strip()]
    if len(cleaned) > MAX_ENTRIES_PER_SECTION:
        return cleaned, f"單區最多 {MAX_ENTRIES_PER_SECTION} 條"
    for s in cleaned:
        if len(s) > MAX_ENTRY_LEN:
            return cleaned, f"單條上限 {MAX_ENTRY_LEN} 字"
    return cleaned, None
