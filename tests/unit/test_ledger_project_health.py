# -*- coding: utf-8 -*-
"""/health 2026-09-13（第二次）特徵測試：今天動過的純函式，把現在的行為釘住（不判斷對錯）。整檔可刪。"""
from datetime import date

from core.hr_logic import burn_rate
from core.ledger_project import link_note, norm_detail, set_parent_share
from core.reminder_logic import missing_log_days


def test_link_note_says_no_agency_fee_for_a_cash_mine_mirror():
    d, c = set_parent_share(norm_detail({"source": "源日"}), 0, "P", 120000, {}, source="自接", face=True, claim=False)
    out = link_note("company", ("X", "查理回家紀錄", c), d)
    assert "案源 自接" in out["text"] and "不抽代辦費" in out["text"] and out["invoice_fee"] == 0
    # 混合（一案自接、一案代開）→ 案源寫「混合」、代辦費句子照代開份額出
    d2, c2 = set_parent_share(d, c, "Q", 50000, {}, source="代開發票")
    out2 = link_note("company", ("X", "查理回家紀錄", c2), d2)
    assert "案源 混合" in out2["text"] and "代辦費" in out2["text"]


def test_burn_rate_edge_values():
    assert burn_rate(None, 32, None) == {"base": "budget", "base_hours": 32, "remaining": 32, "pct": 0.0}
    assert burn_rate(10, 0, 0) == {"base": "", "base_hours": None, "remaining": None, "pct": None}   # 0 預算＝沒設
    assert burn_rate(32, 32, 10)["pct"] == 100.0 and burn_rate(32, 32, 10)["remaining"] == 0


def test_missing_log_days_treats_legacy_rows_without_status_as_filled():
    today = date(2026, 9, 11)   # 週五
    filled = {date(2026, 9, 9): {""}, date(2026, 9, 10): {"pending", ""}}
    out = missing_log_days(today, date(2026, 9, 8), filled)
    assert out == {"missing": [date(2026, 9, 8)], "pending": []}     # 9/10 有一列沒狀態（舊 Sheet 列）＝填了
