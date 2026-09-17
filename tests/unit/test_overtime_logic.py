# -*- coding: utf-8 -*-
"""core/overtime_logic 純規則（勞基法版，owner 2026-09-18「所有設定吻合勞基法」）：四種日子、每日／每月／三個月上限、
重疊、補休換算、加班費估算、掛哪個月。"""
from datetime import date

import pytest

from core.overtime_logic import (daily_max_for, day_kind_for, evaluate, expires_on_for, month_cap_for, ot_hours, overlaps,
                                 pay_month_for, summarize_month, vocab)
from core.payroll_logic import normalize_rates

SAT = date(2026, 10, 3)
SUN = date(2026, 10, 4)
FRI = date(2026, 10, 2)
HOURLY = 40000 / 240


def test_ot_hours_rounds_to_half_and_bounds():
    assert ot_hours("18:00", "20:00") == 2
    assert ot_hours("18:00", "20:20") == 2.5
    assert ot_hours("18:00", "20:10") == 2
    for a, b in (("20:00", "18:00"), ("18:00", "18:10"), ("06:00", "19:00"), ("x", "19:00")):
        with pytest.raises(ValueError):
            ot_hours(a, b)
    with pytest.raises(ValueError):
        ot_hours("18:00", "23:00", max_hours=4)


def test_day_kind_four_kinds_and_holiday_table():
    assert day_kind_for(FRI) == "工作日"
    assert day_kind_for(SAT) == "休息日"
    assert day_kind_for(SUN) == "例假日"
    assert day_kind_for(FRI, {FRI: "國定假日"}) == "國定假日"
    assert day_kind_for(SAT, {SAT: "補班日"}) == "工作日"
    assert day_kind_for(FRI, {FRI: "颱風假"}) == "休息日", "颱風假有上班：公司比照休息日費率"


def test_daily_and_month_caps_follow_the_act():
    assert daily_max_for("工作日") == 4 and daily_max_for("休息日") == 12 and daily_max_for("國定假日") == 12
    assert month_cap_for() == 46
    assert month_cap_for(normalize_rates({"overtime": {"extended": True}})) == 54


def test_overlaps():
    assert overlaps("18:00", "20:00", "19:00", "21:00")
    assert not overlaps("18:00", "20:00", "20:00", "22:00")


def test_evaluate_workday_pay_and_credit():
    ev = evaluate(FRI, "18:00", "20:00", "加班費", hourly=HOURLY)
    assert ev["hours"] == 2 and ev["day_kind"] == "工作日" and ev["credit_hours"] == 2
    assert ev["pay_amount"] == 447, "前 2 小時 ×1.34"
    ev4 = evaluate(FRI, "18:00", "22:00", "加班費", hourly=HOURLY)
    assert ev4["pay_amount"] == 1003, "2×1.34 ＋ 2×1.67"
    ev5 = evaluate(FRI, "18:00", "23:00", "補休")
    assert any(e["code"] == "bad_range" for e in ev5["errors"]), "工作日加班最多 4 小時（一天 12 小時）"


def test_evaluate_restday_and_holiday():
    ev = evaluate(SAT, "10:00", "18:00", "加班費", hourly=HOURLY)
    assert ev["day_kind"] == "休息日" and ev["pay_amount"] == 2667, "休息日：公司 ×2（法定 2,117 較低）"
    assert ev["credit_hours"] == 16, "補休：公司給假日 1:2（高於法定 1:1）"
    ev2 = evaluate(FRI, "09:00", "18:00", "加班費", holidays={FRI: "國定假日"}, hourly=HOURLY)
    assert ev2["day_kind"] == "國定假日" and ev2["pay_amount"] == 3000, "9 h × 2 ＝ 3,000（法定 1,556 較低）"
    ev2b = evaluate(FRI, "09:00", "12:00", "加班費", holidays={FRI: "國定假日"}, hourly=HOURLY)
    assert ev2b["pay_amount"] == 1333, "只做 3 h：法定一日工資 1,333 高於 ×2 的 1,000，取法定"
    ev3 = evaluate(SUN, "10:00", "12:00", "補休")
    assert ev3["day_kind"] == "例假日" and any(w["code"] == "regular_dayoff" for w in ev3["warnings"])


def test_evaluate_pay_without_profile_warns():
    ev = evaluate(SAT, "10:00", "18:00", "加班費", hourly=None)
    assert ev["pay_amount"] is None and any(w["code"] == "no_profile" for w in ev["warnings"])


def test_evaluate_month_guard_and_overlap():
    ev = evaluate(FRI, "18:00", "20:00", "補休", month_hours=38)
    assert not ev["errors"] and any(w["code"] == "month_warn" for w in ev["warnings"]) and ev["month_total"] == 40
    ev = evaluate(FRI, "18:00", "20:00", "補休", month_hours=45)
    assert any(e["code"] == "month_max" for e in ev["errors"]), "46 小時是法定上限，沒勾延長就擋"
    ext = normalize_rates({"overtime": {"extended": True}})
    ev = evaluate(FRI, "18:00", "20:00", "補休", month_hours=45, rates=ext)
    assert not ev["errors"] and ev["month_cap"] == 54
    ev = evaluate(FRI, "18:00", "20:00", "補休", month_hours=45, quarter_hours=137, rates=ext)
    assert any(e["code"] == "quarter_max" for e in ev["errors"]), "三個月 138 小時"
    ev = evaluate(FRI, "18:00", "20:00", "補休", same_day=[("19:00", "21:00")])
    assert any(e["code"] == "overlap" for e in ev["errors"])
    ev = evaluate(FRI, "18:00", "20:00", "現金")
    assert any(e["code"] == "bad_payout" for e in ev["errors"])


def test_expires_and_pay_month():
    assert expires_on_for(SAT) == date(2026, 12, 31)
    assert pay_month_for(SAT, []) == "2026-10"
    assert pay_month_for(SAT, ["2026-10"]) == "2026-11"
    assert pay_month_for(SAT, ["2026-10", "2026-11"]) == "2026-12"


def test_summarize_month():
    items = [{"status": "已核准", "date": "2026-10-03", "hours": 8, "payout": "補休", "credit_hours": 16},
             {"status": "待審", "date": "2026-10-05", "hours": 2, "payout": "加班費", "pay_amount": 447},
             {"status": "已退回", "date": "2026-10-06", "hours": 4, "payout": "補休", "credit_hours": 4},
             {"status": "已核准", "date": "2026-09-30", "hours": 3, "payout": "補休", "credit_hours": 3}]
    assert summarize_month(items, "2026-10") == {"hours": 10, "credit_hours": 16, "pay_amount": 447, "count": 2}


def test_vocab_keys():
    v = vocab()
    for k in ("payouts", "statuses", "day_kinds", "month_cap", "month_warn_hours", "month_max_hours", "daily_max", "credit_multiplier", "overtime", "rule_text", "today"):
        assert k in v
    assert v["day_kinds"] == ["工作日", "休息日", "國定假日", "例假日"]


def test_daily_cap_counts_every_request_on_that_day():
    """BUG-3：每日上限只擋單張，同一天分兩張不重疊的單就繞過去了（工作日 4 h、休息日 12 h 是勞基法一天 12 小時那條）。"""
    ev = evaluate(FRI, "22:00", "23:30", "補休", same_day=[("18:00", "22:00")])
    assert any(e["code"] == "day_max" for e in ev["errors"]), "工作日已報 4 h，再 1.5 h 就超過"
    ev2 = evaluate(SAT, "20:00", "23:00", "補休", same_day=[("08:00", "20:00")])
    assert any(e["code"] == "day_max" for e in ev2["errors"]), "休息日已報 12 h"
    ok = evaluate(FRI, "20:00", "22:00", "補休", same_day=[("18:00", "20:00")])
    assert not ok["errors"], "同一天合計剛好 4 h 可以"


def test_ot_hours_rounds_half_up_not_bankers():
    """BUG-4：Python 的 round 是四捨六入五成雙 —— 45 分與 75 分都變 1.0 小時。改成四捨五入到 0.5。"""
    assert ot_hours("18:00", "18:45") == 1.0
    assert ot_hours("18:00", "19:15") == 1.5
    assert ot_hours("18:00", "20:45") == 3.0
    assert ot_hours("18:00", "18:15") == 0.5
