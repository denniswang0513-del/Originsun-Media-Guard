# -*- coding: utf-8 -*-
"""core/leave_logic 純函式（docs/LEAVE_PLAN.md §7.3）：行為測試，不掃原始碼。

每一支對應規章的一條：特休級距、假日表怎麼扣、半天／時段的小時、FIFO 先到期先扣、
最晚一週前、消假前兩天／颱風假鎖、假日加班 ×2、Google 日曆事件的長相、行政院 CSV。
"""
from datetime import date, datetime, timezone

import pytest

from core import leave_logic as L

HOL = {date(2026, 1, 1): "國定假日",          # 週四 開國紀念日
       date(2026, 2, 7): "補班日",            # 週六 補行上班
       date(2026, 9, 28): "國定假日",         # 週一 教師節
       date(2026, 9, 9): "颱風假"}            # 週三（假想）


# ── 字彙 ────────────────────────────────────────────────────────────────────

def test_vocab_is_a_superset_of_the_legacy_constants_and_exposes_the_rule_numbers():
    from core.hr_logic import LEAVE_STATUSES, LEAVE_TYPES
    v = L.vocab()
    assert set(LEAVE_TYPES) < set(v["leave_types"]) and "補休" in v["leave_types"]
    assert set(LEAVE_STATUSES) < set(v["request_statuses"]) and {"已撤回", "消假待審"} <= set(v["request_statuses"])
    assert set(L.LEDGER_TYPES) | set(L.RECORD_TYPES) == set(L.ALL_LEAVE_TYPES)
    assert v["parts"] == ["all", "am", "pm", "range"]
    assert (v["hours_per_day"], v["notice_days"], v["cancel_free_days"], v["sick_cap_days"], v["holiday_ot_multiplier"]) == (8, 7, 2, 30, 2)


# ── 特休級距（勞基法 §38／規章表）──────────────────────────────────────────

@pytest.mark.parametrize("hire, on, days", [
    (date(2026, 4, 1), date(2026, 9, 7), 0),      # 未滿 6 個月
    (date(2026, 3, 7), date(2026, 9, 7), 3),      # 剛滿 6 個月（邊界）
    (date(2026, 3, 8), date(2026, 9, 7), 0),      # 差一天
    (date(2025, 9, 7), date(2026, 9, 7), 7),      # 滿 1 年
    (date(2025, 9, 8), date(2026, 9, 7), 3),      # 差一天滿 1 年
    (date(2024, 9, 7), date(2026, 9, 7), 10),     # 2 年
    (date(2023, 9, 7), date(2026, 9, 7), 14),     # 3 年
    (date(2022, 9, 7), date(2026, 9, 7), 14),     # 4 年
    (date(2021, 9, 7), date(2026, 9, 7), 15),     # 5 年
    (date(2017, 9, 7), date(2026, 9, 7), 15),     # 9 年
    (date(2016, 9, 7), date(2026, 9, 7), 16),     # 10 年：15 +1
    (date(2012, 9, 7), date(2026, 9, 7), 20),     # 14 年
    (date(1990, 9, 7), date(2026, 9, 7), 30),     # 36 年：封頂 30
])
def test_annual_days_for_follows_the_seniority_table(hire, on, days):
    assert L.annual_days_for(hire, on) == days


def test_annual_days_for_tolerates_missing_or_timestamp_hire_dates():
    assert L.annual_days_for(None, date(2026, 9, 7)) == 0
    assert L.annual_days_for(date(2027, 1, 1), date(2026, 9, 7)) == 0       # 還沒到職
    assert L.annual_days_for("2020-09-07", "2026-09-07") == 15               # ISO 字串
    assert L.annual_days_for(datetime(2020, 9, 6, 16, 0, tzinfo=timezone.utc), date(2026, 9, 7)) == 15   # UTC 讀回＝台北 9/7


# ── 工作日 ─────────────────────────────────────────────────────────────────

def test_is_workday_weekend_holiday_makeup_and_typhoon():
    assert L.is_workday(date(2026, 9, 7), HOL)            # 週一
    assert not L.is_workday(date(2026, 9, 5), HOL)        # 週六
    assert not L.is_workday(date(2026, 9, 6), HOL)        # 週日
    assert not L.is_workday(date(2026, 1, 1), HOL)        # 國定假日（週四）
    assert L.is_workday(date(2026, 2, 7), HOL)            # 補班日（週六）算上班
    assert not L.is_workday(date(2026, 9, 9), HOL)        # 颱風假
    assert L.is_workday(date(2026, 9, 9), {})             # 沒有假日表＝平日


def test_holidays_map_accepts_iso_keys_and_dict_values():
    assert not L.is_workday(date(2026, 1, 1), {"2026-01-01": "國定假日"})
    assert L.is_workday(date(2026, 2, 7), {date(2026, 2, 7): {"kind": "補班日", "name": "補行上班"}})


# ── 時數 ───────────────────────────────────────────────────────────────────

def test_working_hours_all_day_counts_only_workdays():
    # 9/25（五）～9/29（二）：五、一（教師節）、二 → 週末＋國定假日扣掉剩 2 天
    assert L.working_hours(date(2026, 9, 25), date(2026, 9, 29), "all", holidays=HOL) == 16
    assert L.working_hours("2026-09-07", "2026-09-07") == 8
    assert L.working_hours(date(2026, 9, 5), date(2026, 9, 6), "all", holidays=HOL) == 0      # 整個週末
    assert L.working_hours(date(2026, 2, 7), date(2026, 2, 7), "all", holidays=HOL) == 8      # 補班日要請假


def test_working_hours_half_day_is_four_and_single_day_only():
    assert L.working_hours(date(2026, 9, 7), date(2026, 9, 7), "am") == 4
    assert L.working_hours(date(2026, 9, 7), date(2026, 9, 7), "pm") == 4
    with pytest.raises(ValueError):
        L.working_hours(date(2026, 9, 7), date(2026, 9, 8), "am")
    assert L.working_hours(date(2026, 9, 5), date(2026, 9, 5), "am", holidays=HOL) == 0      # 週六半天＝0


def test_working_hours_range_rounds_to_half_hour_and_caps_at_eight():
    d = date(2026, 9, 7)
    assert L.working_hours(d, d, "range", "14:00", "17:00") == 3
    assert L.working_hours(d, d, "range", "13:20", "17:00") == 3.5      # 3h40 → 3.5
    assert L.working_hours(d, d, "range", "13:00", "17:50") == 5        # 4h50 → 5
    assert L.working_hours(d, d, "range", "08:00", "20:00") == 8        # 12h 封頂
    assert L.working_hours(d, d, "range", "09:00", "09:10") == 0.5      # 最小 0.5
    for st, et in (("17:00", "14:00"), ("", "17:00"), ("9am", "17:00")):
        with pytest.raises(ValueError):
            L.working_hours(d, d, "range", st, et)
    with pytest.raises(ValueError):
        L.working_hours(d, date(2026, 9, 8), "range", "09:00", "10:00")


def test_working_hours_rejects_bad_ranges_and_parts():
    with pytest.raises(ValueError):
        L.working_hours(date(2026, 9, 8), date(2026, 9, 7))
    with pytest.raises(ValueError):
        L.working_hours(None, date(2026, 9, 7))
    with pytest.raises(ValueError):
        L.working_hours(date(2026, 9, 7), date(2026, 9, 7), "night")


# ── 時數帳 ─────────────────────────────────────────────────────────────────

def _credits():
    return [
        {"id": "never", "kind": "特休", "hours": 8, "used": 0, "status": "可用", "expires_on": None, "granted_on": date(2026, 1, 1)},
        {"id": "dec", "kind": "補休", "hours": 16, "used": 4, "status": "可用", "expires_on": date(2026, 12, 31), "granted_on": date(2026, 3, 1)},
        {"id": "sep", "kind": "特休", "hours": 8, "used": 0, "status": "可用", "expires_on": date(2026, 9, 30), "granted_on": date(2025, 10, 1)},
        {"id": "gone", "kind": "特休", "hours": 40, "used": 0, "status": "可用", "expires_on": date(2026, 8, 31), "granted_on": date(2025, 9, 1)},   # 已到期
        {"id": "pend", "kind": "補休", "hours": 40, "used": 0, "status": "待審", "expires_on": None, "granted_on": date(2026, 9, 1)},               # 還沒核
        {"id": "empty", "kind": "特休", "hours": 8, "used": 8, "status": "可用", "expires_on": date(2026, 9, 15), "granted_on": date(2026, 1, 1)},   # 扣光
    ]


def test_allocate_is_fifo_by_expiry_then_grant_and_skips_expired_pending_and_empty():
    on = date(2026, 9, 7)
    # 先到期（sep 9/30）→ dec（12/31）→ never（永不），expired／待審／扣光的全部不碰
    assert L.allocate(_credits(), 14, on=on) == [("sep", 8), ("dec", 6)]
    assert L.allocate(_credits(), 28, on=on) == [("sep", 8), ("dec", 12), ("never", 8)]
    assert L.allocate(_credits(), 0, on=on) == []


def test_allocate_raises_with_the_shortfall():
    with pytest.raises(L.InsufficientHours) as ei:
        L.allocate(_credits(), 30, on=date(2026, 9, 7))       # 可用 8+12+8=28
    assert ei.value.short == 2
    with pytest.raises(L.InsufficientHours) as ei:
        L.allocate([], 4)
    assert ei.value.short == 4


def test_allocate_can_take_the_used_hours_from_object_attributes():
    class C:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    cs = [C(id="a", kind="特休", hours=8, used=6, status="可用", expires_on=None, granted_on=date(2026, 1, 1))]
    assert L.allocate(cs, 2) == [("a", 2)]


def test_balance_reserved_expiring_and_kind_filter():
    on = date(2026, 9, 7)
    b = L.balance(_credits(), pending_hours=4, kind="特休", on=on)
    assert b["available"] == 16                     # never 8 + sep 8（gone 到期、empty 扣光）
    assert b["reserved"] == 4
    assert b["expiring"] == [{"hours": 8, "expires_on": "2026-09-30", "reason": ""}]
    assert b["expiring_soon"] == 8
    assert L.balance(_credits(), kind="補休", on=on)["available"] == 12
    # used 不在 credit 上時從 allocations 補算（兩種形狀）
    cs = [{"id": "x", "kind": "特休", "hours": 16, "status": "可用", "expires_on": None, "granted_on": date(2026, 1, 1)}]
    assert L.balance(cs, allocations=[("x", 6)], kind="特休", on=on)["available"] == 10
    assert L.balance(cs, allocations=[{"credit_id": "x", "hours": 6}], kind="特休", on=on)["available"] == 10


def test_balance_never_stores_a_snapshot_it_is_recomputed_from_inputs():
    cs = [{"id": "x", "kind": "特休", "hours": 8, "used": 0, "status": "可用", "expires_on": None, "granted_on": date(2026, 1, 1)}]
    assert L.balance(cs, kind="特休")["available"] == 8
    cs[0]["used"] = 8
    assert L.balance(cs, kind="特休")["available"] == 0


# ── 規章 ───────────────────────────────────────────────────────────────────

def test_notice_warning_only_under_seven_days():
    today = date(2026, 9, 7)
    assert L.notice_warning(date(2026, 9, 14), today) is None          # 剛好 7 天
    assert "6 天" in L.notice_warning(date(2026, 9, 13), today)
    assert L.notice_warning(date(2026, 9, 6), today)                   # 已經過去也要提醒
    assert L.notice_warning(None, today) is None


def test_cancel_mode_free_apply_locked():
    assert L.cancel_mode(date(2026, 9, 9), date(2026, 9, 7), HOL) == "free"      # 2 天（邊界）
    assert L.cancel_mode(date(2026, 9, 8), date(2026, 9, 7), HOL) == "apply"     # 1 天
    assert L.cancel_mode(date(2026, 9, 7), date(2026, 9, 7), HOL) == "apply"     # 當天
    assert L.cancel_mode(date(2026, 9, 30), date(2026, 9, 9), HOL) == "locked"   # 今天颱風假 → 誰都不能消
    assert L.cancel_mode(date(2026, 9, 30), date(2026, 9, 9), {}) == "free"      # 沒標颱風假就不鎖


def test_overtime_credit_hours_doubles_on_weekend_holiday_and_typhoon():
    assert L.overtime_credit_hours(4, date(2026, 9, 7), HOL) == 4        # 週一
    assert L.overtime_credit_hours(4, date(2026, 9, 5), HOL) == 8        # 週六
    assert L.overtime_credit_hours(4, date(2026, 9, 28), HOL) == 8       # 教師節
    assert L.overtime_credit_hours(4, date(2026, 9, 9), HOL) == 8        # 颱風假上班
    assert L.overtime_credit_hours(4, date(2026, 2, 7), HOL) == 4        # 補班日是上班日


def test_overlaps_is_inclusive_and_in_crew_falls_back_to_name():
    assert L.overlaps("2026-09-07", "2026-09-09", "2026-09-09", "2026-09-10")
    assert not L.overlaps("2026-09-07", "2026-09-08", "2026-09-09", None)
    crew = [{"staff_id": "s1", "name": "甲"}, {"staff_id": "", "name": "乙"}]
    assert L.in_crew(crew, "s1", "別名") and L.in_crew(crew, "s9", "乙") and not L.in_crew(crew, "s9", "甲")


# ── Google 日曆事件（同 core.shoot_logic.event_body 形狀）────────────────────

def test_leave_event_body_all_day_uses_exclusive_end_date():
    body = L.leave_event_body({"id": "r1", "leave_type": "特休", "hours": 16, "part": "all",
                               "start_date": "2026-09-10", "end_date": "2026-09-11", "reason": "回家"}, "蔡念栩")
    assert body["summary"] == "休假｜蔡念栩（特休 2天）"
    assert body["start"] == {"date": "2026-09-10"} and body["end"] == {"date": "2026-09-12"}
    assert body["extendedProperties"] == {"private": {"originsun_leave_id": "r1"}}
    assert "事由：回家" in body["description"] and "16 小時" in body["description"]


def test_leave_event_body_timed_parts_use_taipei_datetimes():
    pm = L.leave_event_body({"id": "r2", "leave_type": "事假", "hours": 4, "part": "pm",
                             "start_date": "2026-09-10", "end_date": "2026-09-10"}, "蔡念栩")
    assert pm["summary"] == "休假｜蔡念栩（事假 0.5天）"
    assert pm["start"] == {"dateTime": "2026-09-10T14:00:00", "timeZone": "Asia/Taipei"}
    assert pm["end"] == {"dateTime": "2026-09-10T18:00:00", "timeZone": "Asia/Taipei"}
    rng = L.leave_event_body({"id": "r3", "leave_type": "病假", "hours": 3, "part": "range", "start_time": "14:00",
                              "end_time": "17:00", "start_date": "2026-09-10", "end_date": "2026-09-10"}, "蔡念栩", link="http://x/my.html")
    assert rng["start"]["dateTime"] == "2026-09-10T14:00:00" and rng["end"]["dateTime"] == "2026-09-10T17:00:00"
    assert "系統：http://x/my.html" in rng["description"] and "14:00–17:00" in rng["description"]


def test_leave_event_body_mirrors_the_shoot_event_shape():
    from core.shoot_logic import event_body
    shoot = event_body({"id": "s", "date": "2026-09-10", "project_name": "案"})
    leave = L.leave_event_body({"id": "r", "leave_type": "特休", "hours": 8, "part": "all", "start_date": "2026-09-10", "end_date": "2026-09-10"}, "甲")
    assert set(leave) - {"location"} == set(shoot) - {"location"}
    assert set(leave["start"]) == set(shoot["start"]) == {"date"}


# ── 行政院行事曆 CSV ────────────────────────────────────────────────────────

def test_parse_gov_calendar_csv_new_format_with_bom():
    csv = ("﻿西元日期,星期,是否放假,備註\n"
           "20260101,四,2,開國紀念日\n"
           "20260103,六,2,\n"            # 週六放假：略過
           "20260105,一,0,\n"            # 平日上班：略過
           "20260207,六,0,補行上班\n"     # 週六上班 → 補班日
           "20260928,一,2,教師節\n")
    rows = L.parse_gov_calendar_csv(csv)
    assert [(r["date"].isoformat(), r["kind"], r["name"]) for r in rows] == [
        ("2026-01-01", "國定假日", "開國紀念日"), ("2026-02-07", "補班日", "補行上班"), ("2026-09-28", "國定假日", "教師節")]


def test_parse_gov_calendar_csv_old_format_and_bad_input():
    old = "date,name,isHoliday,holidayCategory,description\n2017/1/2,,是,放假之紀念日及節日,開國紀念日補假\n2017/2/18,,否,補行上班,\n"
    rows = L.parse_gov_calendar_csv(old)
    assert [(r["date"].isoformat(), r["kind"]) for r in rows] == [("2017-01-02", "國定假日"), ("2017-02-18", "補班日")]
    assert L.parse_gov_calendar_csv("") == []
    with pytest.raises(ValueError):
        L.parse_gov_calendar_csv("a,b\n1,2\n")
