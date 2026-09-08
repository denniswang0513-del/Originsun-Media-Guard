# -*- coding: utf-8 -*-
"""安全網（/polish 階段零，2026-09-08）：把假勤／里程碑這批新增的**純函式現有行為**釘住。

不判斷對錯，只記錄「現在給這個輸入會回這個」——之後重構若動到它們，這裡先紅。
只補這一批（b7240f8e..HEAD）動到、而且原本沒有任何測試碰過的公開純函式。
"""
from datetime import date, datetime, timezone

import pytest

from core.leave_logic import (as_date, credit_remaining, fmt_days, holiday_kind, hours_to_days, is_workday,
                              parse_hhmm, usable_credits, workdays_between)
from core.milestone_logic import as_date as ms_as_date


@pytest.mark.parametrize("raw, want", [
    (None, None), ("", None), ("2026-09-08", date(2026, 9, 8)),
    ("2026-09-08T13:00:00", date(2026, 9, 8)),     # 只取前十個字
    (date(2026, 9, 8), date(2026, 9, 8)),
    ("不是日期", None), ("2026-13-99", None),
])
def test_as_date_is_the_same_in_both_modules(raw, want):
    """假勤與里程碑各有一份 as_date（刻意不共用：兩邊的 import 邊界）；行為要一樣。"""
    assert as_date(raw) == want
    assert ms_as_date(raw) == want


def test_as_date_reads_utc_timestamps_back_as_taipei_days():
    """timestamptz 讀回是 aware UTC：9/8 00:00+08 存進去、讀回 9/7 16:00Z，仍要算 9/8（差一天的老坑）。"""
    assert as_date(datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)) == date(2026, 9, 8)


@pytest.mark.parametrize("hours, days, text", [
    (8, 1.0, "1"), (4, 0.5, "0.5"), (0, 0.0, "0"), (None, 0.0, "0"),
    (12, 1.5, "1.5"), (2, 0.25, "0.25"), (1, 0.12, "0.12"),      # 8 除不盡：round(0.125, 2) 進到偶數＝0.12（銀行家捨入，記錄現狀）
])
def test_hours_to_days_and_fmt_days(hours, days, text):
    assert hours_to_days(hours) == days
    assert fmt_days(hours) == text


@pytest.mark.parametrize("raw, want", [
    ("09:00", 540), ("00:00", 0), ("24:00", 1440), (" 9:05 ", 545),
    ("24:01", None), ("25:00", None), ("09:60", None), ("9", None), ("", None), (None, None),
])
def test_parse_hhmm(raw, want):
    assert parse_hhmm(raw) == want


def test_holiday_kind_accepts_the_three_shapes_of_the_holiday_map():
    d = date(2026, 9, 8)
    assert holiday_kind(d, None) is None
    assert holiday_kind(d, {d: "國定假日"}) == "國定假日"
    assert holiday_kind(d, {"2026-09-08": "颱風假"}) == "颱風假"
    assert holiday_kind(d, {d: {"kind": "補班日"}}) == "補班日"
    assert holiday_kind(d, {d: ""}) is None


def test_is_workday_and_workdays_between():
    sat, sun, mon = date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 14)
    assert is_workday(mon) and not is_workday(sat) and not is_workday(sun)
    assert is_workday(sat, {sat: "補班日"}), "補班日的週六算工作日"
    assert not is_workday(mon, {mon: "國定假日"}) and not is_workday(mon, {mon: "颱風假"})
    week = workdays_between(date(2026, 9, 7), date(2026, 9, 13))
    assert week == [date(2026, 9, d) for d in range(7, 12)], "含首尾、去掉週末"
    assert workdays_between(mon, date(2026, 9, 7)) == [], "迄早於起＝空清單，不是無窮迴圈"


def test_credit_remaining_handles_objects_dicts_and_nulls():
    class C:
        hours, used = 8.0, 2.0
    assert credit_remaining(C()) == 6.0
    assert credit_remaining({"hours": 8, "used": None}) == 8.0
    assert credit_remaining({}) == 0.0


def test_usable_credits_filters_and_orders_first_expiring_first():
    on = date(2026, 9, 8)
    rows = [
        {"id": "no_exp", "status": "可用", "kind": "補休", "hours": 8, "used": 0, "expires_on": None, "granted_on": "2026-01-01"},
        {"id": "late", "status": "可用", "kind": "補休", "hours": 8, "used": 0, "expires_on": "2026-12-31", "granted_on": "2026-02-01"},
        {"id": "soon", "status": "可用", "kind": "補休", "hours": 8, "used": 4, "expires_on": "2026-10-01", "granted_on": "2026-03-01"},
        {"id": "expired", "status": "可用", "kind": "補休", "hours": 8, "used": 0, "expires_on": "2026-09-07", "granted_on": "2026-01-01"},
        {"id": "used_up", "status": "可用", "kind": "補休", "hours": 8, "used": 8, "expires_on": None, "granted_on": "2026-01-01"},
        {"id": "void", "status": "作廢", "kind": "補休", "hours": 8, "used": 0, "expires_on": None, "granted_on": "2026-01-01"},
        {"id": "other_kind", "status": "可用", "kind": "特休", "hours": 8, "used": 0, "expires_on": None, "granted_on": "2026-01-01"},
    ]
    assert [c["id"] for c in usable_credits(rows, "補休", on)] == ["soon", "late", "no_exp"]
    assert {c["id"] for c in usable_credits(rows, None, on)} >= {"other_kind"}, "不指定 kind 就不篩假別"
    assert [c["id"] for c in usable_credits(rows, "補休", date(2026, 9, 7))] == ["expired", "soon", "late", "no_exp"], "到期當天還能用"
