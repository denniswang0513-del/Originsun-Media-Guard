# -*- coding: utf-8 -*-
"""core/payroll_logic 純規則（docs/PAYROLL_OVERTIME_PLAN.md §3）：級距、勞健保勞退、應發實發、加班費倍率、主檔生效段。"""
import pytest

from core.payroll_logic import (DEFAULT_RATES, LEVELS_2026, LINE_EDITABLE, base_pay_for, build_line, check_month, credit_hours_for,
                                default_grades, grade_for, hourly_wage, insurance_for, legal_overtime_pay, line_totals, min_wage_warnings,
                                next_month, normalize_rates, overtime_pay, profile_as_of, vocab)


def test_grade_picks_first_level_at_or_above_and_caps():
    lv = LEVELS_2026
    assert grade_for(40000, 45800, lv) == 40100
    assert grade_for(40100, 45800, lv) == 40100
    assert grade_for(20000, 45800, lv) == 29500, "低於最低級距用最低級距"
    assert grade_for(60000, 45800, lv) == 45800, "勞保上限 45,800"
    assert grade_for(60000, 313000, lv) == 60800, "健保照級距往上"
    assert grade_for(400000, 313000, lv) == 313000


def test_insurance_2026_monthly_40100():
    """月薪 40,000 → 級距 40,100：勞保自負 40100×12.5%×20% ＝ 1,003；健保自負 40100×5.17%×30% ＝ 622；
    雇主勞保 40100×12.5%×70% ＝ 3,509 ＋ 職災 52（0.13%）；雇主健保 40100×5.17%×60%×1.56 ＝ 1,940；勞退 6% ＝ 2,406。"""
    ins = insurance_for(40100, 40100, 0, 0, DEFAULT_RATES)
    assert ins["labor_self"] == 1003
    assert ins["health_self"] == 622
    assert ins["labor_employer"] == 3509 + 52
    assert ins["health_employer"] == 1940
    assert ins["pension_employer"] == 2406
    assert ins["pension_self"] == 0


def test_insurance_dependents_and_self_pension():
    ins = insurance_for(40100, 40100, 2, 6, DEFAULT_RATES)
    assert ins["health_self"] == 622 * 3, "眷屬一人加一份本人自負額"
    assert ins["pension_self"] == 2406, "自提 6% 跟雇主提繳同一個基數"
    assert insurance_for(40100, 40100, 0, 9, DEFAULT_RATES)["pension_self"] == 2406, "自提最多 6%"


def test_insurance_matches_2024_parttime_sheet_labor_share():
    """兼職薪資清冊（2024）：級距 22,000、勞保費率 12% → 勞保自負 528。費率換成當年就對得上。"""
    rates = normalize_rates({"labor_rate": 0.12, "levels": [21010, 22000]}, 2024)
    assert insurance_for(22000, 22000, 0, 0, rates)["labor_self"] == 528


def test_pension_grade_capped_at_150000():
    ins = insurance_for(45800, 200000, 0, 0, DEFAULT_RATES)
    assert ins["pension_grade"] == 150000 and ins["pension_employer"] == 9000


def test_hourly_wage_and_base_pay():
    assert hourly_wage("月薪", 40000) == pytest.approx(40000 / 240)
    assert hourly_wage("時薪", 220) == 220
    assert hourly_wage("日薪", 1800) == 225
    assert base_pay_for("月薪", 40000, 999) == 40000, "月薪制不看時數"
    assert base_pay_for("時薪", 220, 88) == 19360
    assert base_pay_for("日薪", 1800, 16) == 3600


def test_overtime_pay_workday_by_law_holiday_x2_never_below_law():
    """工作日照 §24（前 2 h ×1.34、再 2 h ×1.67）；假日公司 ×2，但取「×2」與法定較高者。月薪 40,000 → 每小時 166.67。"""
    h = hourly_wage("月薪", 40000)
    assert overtime_pay(h, 2, "工作日") == 447
    assert overtime_pay(h, 4, "工作日") == 1003
    assert legal_overtime_pay(h, 8, "休息日") == 2117 and overtime_pay(h, 8, "休息日") == 2667, "休息日 8 h：公司 ×2 高於法定"
    assert legal_overtime_pay(h, 12, "休息日") == 3897 and overtime_pay(h, 12, "休息日") == 4000, "12 h：×2 ＝ 4,000 仍高於法定 3,897"
    assert legal_overtime_pay(h, 3, "國定假日") == 1333 and overtime_pay(h, 3, "國定假日") == 1333, "只做 3 h：法定一日工資較高，取法定"
    assert overtime_pay(h, 9, "國定假日") == 3000, "9 h：×2 ＝ 3,000 高於法定 1,556"
    assert overtime_pay(h, 0, "工作日") == 0
    assert credit_hours_for(2, "工作日") == 2 and credit_hours_for(8, "休息日") == 16 and credit_hours_for(8, "國定假日") == 16


def test_normalize_overtime_only_accepts_extended_and_credit():
    r = normalize_rates({"overtime": {"workday_tiers": [[8, 0.5]], "extended": "yes", "credit_multiplier": {"工作日": 0.5, "休息日": 3}}})
    assert r["overtime"]["workday_tiers"] == [[2, 1.34], [2, 1.67]], "法定倍率不給改"
    assert r["overtime"]["extended"] is True
    assert r["overtime"]["credit_multiplier"]["工作日"] == 1, "補休不得低於 1:1"
    assert r["overtime"]["credit_multiplier"]["休息日"] == 3
    assert normalize_rates(None)["overtime"]["extended"] is False


def test_min_wage_warnings():
    assert min_wage_warnings("月薪", 28000) and not min_wage_warnings("月薪", 29500)
    assert min_wage_warnings("時薪", 190) and not min_wage_warnings("時薪", 196) and not min_wage_warnings("日薪", 1000)


def test_line_totals_and_build_line_monthly():
    prof = {"pay_type": "月薪", "base_amount": 40000, "meal_allowance": 3000, "labor_grade": 43900, "health_grade": 43900,
            "dependents": 0, "pension_self_rate": 0, "payroll_entity": "公司"}
    line = build_line(prof, DEFAULT_RATES, extras={"overtime_pay": 2667, "leave_deduction": 1333})
    assert line["base_pay"] == 40000 and line["meal_allowance"] == 3000
    assert line["gross_pay"] == 40000 + 3000 + 2667 - 1333
    ins = insurance_for(43900, 43900, 0, 0, DEFAULT_RATES)
    assert line["net_pay"] == line["gross_pay"] - ins["labor_self"] - ins["health_self"]
    assert line["employer_total"] == line["gross_pay"] + ins["labor_employer"] + ins["health_employer"] + ins["pension_employer"]
    assert line_totals({"base_pay": 100}) == {"gross_pay": 100, "net_pay": 100, "employer_total": 100}


def test_build_line_hourly_uses_hand_filled_hours():
    prof = {"pay_type": "時薪", "base_amount": 220, "meal_allowance": 0, "labor_grade": 29500, "health_grade": 29500}
    line = build_line(prof, DEFAULT_RATES, work_hours=88)
    assert line["base_pay"] == 19360 and line["work_hours"] == 88
    line2 = build_line(prof, DEFAULT_RATES, work_hours=88, extras={"work_hours": 40})
    assert line2["base_pay"] == 8800, "手改時數優先"


def test_default_grades():
    g = default_grades("月薪", 40000, 3000, DEFAULT_RATES)
    assert g == {"labor_grade": 43900, "health_grade": 43900}, "投保薪資看底薪＋伙食費"
    assert default_grades("時薪", 220, 0, DEFAULT_RATES) == {"labor_grade": 29500, "health_grade": 29500}


def test_normalize_rates_keeps_defaults_and_sorts_levels():
    r = normalize_rates({"health_rate": "0.0517", "levels": [30000, "29500", 30000], "junk": 1})
    assert r["labor_rate"] == 0.125 and r["levels"] == [29500, 30000] and "junk" not in r
    assert normalize_rates(None)["year"] == 2026 and normalize_rates(None, 2027)["year"] == 2027


def test_profile_as_of_and_months():
    ps = [{"effective_from": "2026-01", "base_amount": 36000}, {"effective_from": "2026-07", "base_amount": 40000}]
    assert profile_as_of(ps, "2026-06")["base_amount"] == 36000
    assert profile_as_of(ps, "2026-07")["base_amount"] == 40000
    assert profile_as_of(ps, "2025-12") is None
    assert check_month("2026-09") == "2026-09"
    for bad in ("2026/09", "2026-13", "202609", ""):
        with pytest.raises(ValueError):
            check_month(bad)
    assert next_month("2026-09") == "2026-10" and next_month("2026-12") == "2027-01"


def test_vocab_contract_keys():
    v = vocab()
    for k in ("pay_types", "payroll_entities", "run_statuses", "day_kinds", "line_editable", "overtime", "rule_text", "min_wage_monthly"):
        assert k in v
    assert v["line_editable"] == list(LINE_EDITABLE)


def test_health_dependents_capped_at_three():
    """BUG-5：健保法 §18「眷屬超過三口者，以三口計」。眷屬 5 口不該扣 6 份。"""
    one = insurance_for(45800, 45800, 0, 0, DEFAULT_RATES)["health_self"]
    assert insurance_for(45800, 45800, 3, 0, DEFAULT_RATES)["health_self"] == one * 4
    assert insurance_for(45800, 45800, 5, 0, DEFAULT_RATES)["health_self"] == one * 4, "超過三口以三口計"
    assert insurance_for(45800, 45800, 9, 0, DEFAULT_RATES)["health_self"] == one * 4


def test_normalize_rates_survives_a_bad_level_token():
    """BUG-6：級距貼到非數字（或前端 Number('') → null）不該 500。"""
    r = normalize_rates({"levels": [29500, "abc", None, "", 30300, -5]})
    assert r["levels"] == [29500, 30300]
    assert normalize_rates({"levels": ["abc"]})["levels"] == DEFAULT_RATES["levels"], "全壞就退回預設"
