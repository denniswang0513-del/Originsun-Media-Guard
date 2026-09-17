# -*- coding: utf-8 -*-
"""core/overtime_logic 純規則：起訖算時數、工作日／假日、重疊、46／54 守衛、補休換算、加班費估算、掛哪個月。"""
from datetime import date

import pytest

from core.overtime_logic import (MONTH_MAX_HOURS, MONTH_WARN_HOURS, day_kind_for, evaluate, expires_on_for, ot_hours, overlaps,
                                 pay_month_for, summarize_month, vocab)

SAT = date(2026, 10, 3)      # 週六
FRI = date(2026, 10, 2)


def test_ot_hours_rounds_to_half_and_bounds():
    assert ot_hours("18:00", "20:00") == 2
    assert ot_hours("18:00", "20:20") == 2.5
    assert ot_hours("18:00", "20:10") == 2
    for a, b in (("20:00", "18:00"), ("18:00", "18:10"), ("06:00", "19:00"), ("x", "19:00")):
        with pytest.raises(ValueError):
            ot_hours(a, b)


def test_day_kind_uses_holiday_table():
    assert day_kind_for(FRI) == "工作日"
    assert day_kind_for(SAT) == "假日"
    assert day_kind_for(FRI, {FRI: "國定假日"}) == "假日"
    assert day_kind_for(SAT, {SAT: "補班日"}) == "工作日"


def test_overlaps():
    assert overlaps("18:00", "20:00", "19:00", "21:00")
    assert not overlaps("18:00", "20:00", "20:00", "22:00")


def test_evaluate_workday_credit_and_pay():
    ev = evaluate(FRI, "18:00", "20:00", "加班費", hourly=40000 / 240)
    assert ev["hours"] == 2 and ev["day_kind"] == "工作日" and ev["credit_hours"] == 2 and ev["pay_amount"] == 333
    assert not ev["errors"] and not ev["warnings"]
    ev2 = evaluate(SAT, "10:00", "18:00", "補休")
    assert ev2["credit_hours"] == 16 and ev2["pay_amount"] is None and ev2["day_kind"] == "假日"


def test_evaluate_pay_without_profile_warns():
    ev = evaluate(SAT, "10:00", "18:00", "加班費", hourly=None)
    assert ev["pay_amount"] is None and any(w["code"] == "no_profile" for w in ev["warnings"])


def test_evaluate_month_guard_and_overlap():
    ev = evaluate(FRI, "18:00", "20:00", "補休", month_hours=45)
    assert not ev["errors"] and any(w["code"] == "month_warn" for w in ev["warnings"]) and ev["month_total"] == 47
    ev = evaluate(FRI, "18:00", "20:00", "補休", month_hours=53)
    assert any(e["code"] == "month_max" for e in ev["errors"])
    ev = evaluate(FRI, "18:00", "20:00", "補休", same_day=[("19:00", "21:00")])
    assert any(e["code"] == "overlap" for e in ev["errors"])
    ev = evaluate(FRI, "18:00", "20:00", "現金")
    assert any(e["code"] == "bad_payout" for e in ev["errors"])
    assert MONTH_WARN_HOURS == 46 and MONTH_MAX_HOURS == 54


def test_expires_and_pay_month():
    assert expires_on_for(SAT) == date(2026, 12, 31)
    assert pay_month_for(SAT, []) == "2026-10"
    assert pay_month_for(SAT, ["2026-10"]) == "2026-11"
    assert pay_month_for(SAT, ["2026-10", "2026-11"]) == "2026-12"


def test_summarize_month():
    items = [{"status": "已核准", "date": "2026-10-03", "hours": 8, "payout": "補休", "credit_hours": 16},
             {"status": "待審", "date": "2026-10-05", "hours": 2, "payout": "加班費", "pay_amount": 333},
             {"status": "已退回", "date": "2026-10-06", "hours": 4, "payout": "補休", "credit_hours": 4},
             {"status": "已核准", "date": "2026-09-30", "hours": 3, "payout": "補休", "credit_hours": 3}]
    assert summarize_month(items, "2026-10") == {"hours": 10, "credit_hours": 16, "pay_amount": 333, "count": 2}


def test_vocab_keys():
    v = vocab()
    for k in ("payouts", "statuses", "day_kinds", "month_warn_hours", "month_max_hours", "credit_multiplier", "pay_multiplier", "today"):
        assert k in v
