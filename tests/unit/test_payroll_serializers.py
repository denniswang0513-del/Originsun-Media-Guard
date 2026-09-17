# -*- coding: utf-8 -*-
"""polish safety net：釘住薪資／加班 router 裡「不碰 DB 的序列化」現況（_profile_dict／_line_dict／_run_dict／ot_dict）——
給 SimpleNamespace 當 ORM 物件，斷言現在回的形狀。改了回傳形狀（前端與手機都在讀）這裡會先紅。"""
from datetime import date, datetime, timezone
from types import SimpleNamespace

from routers.api_overtime import ot_dict
from routers.api_payroll import _LINE_FIELDS, _SUM_FIELDS, _line_dict, _profile_dict, _run_dict

_NOW = datetime(2026, 9, 18, 1, 2, 3, tzinfo=timezone.utc)


def _line(**kw):
    base = {k: 0 for k in _LINE_FIELDS}
    base.update({"pay_type": "月薪", "payroll_entity": "公司", "note": None, "work_hours": 0.0, "pension_self_rate": 0.0,
                 "id": "L1", "run_id": "R1", "staff_id": "S1", "staff_name": "測試員工", "profile_id": "P1", "manual_fields": None,
                 "bank_name": None, "bank_account": None, "payment_request_id": None})
    base.update(kw)
    return SimpleNamespace(**base)


def test_profile_dict_defaults_and_types():
    p = SimpleNamespace(id="P1", staff_id="S1", staff_name=None, effective_from="2026-01", pay_type=None, base_amount=None,
                        meal_allowance=3000, labor_grade=43900, health_grade=None, pension_self_rate=None, dependents=None,
                        payroll_entity=None, pay_day=None, note=None)
    d = _profile_dict(p)
    assert d == {"id": "P1", "staff_id": "S1", "staff_name": "", "effective_from": "2026-01", "pay_type": "月薪", "base_amount": 0,
                 "meal_allowance": 3000, "labor_grade": 43900, "health_grade": 0, "pension_self_rate": 0.0, "dependents": 0,
                 "payroll_entity": "公司", "pay_day": 5, "note": ""}


def test_line_dict_shape_and_numeric_coercion():
    d = _line_dict(_line(base_pay=40000, net_pay=41221, work_hours=None, manual_fields=["overtime_pay"], note="x", bank_account="123"))
    assert d["base_pay"] == 40000 and d["net_pay"] == 41221 and d["work_hours"] == 0.0 and d["pension_self_rate"] == 0.0
    assert d["manual_fields"] == ["overtime_pay"] and d["note"] == "x" and d["bank_account"] == "123" and d["payment_request_id"] == ""
    assert set(_LINE_FIELDS) <= set(d) and {"id", "run_id", "staff_id", "staff_name", "profile_id", "bank_name"} <= set(d)
    assert isinstance(d["pay_type"], str) and isinstance(d["overtime_pay"], int)


def test_run_dict_totals_sum_every_money_column():
    run = SimpleNamespace(id="R1", month="2026-09", status=None, table_year=2026, note=None, confirmed_by=None, confirmed_at=None, created_at=_NOW)
    d = _run_dict(run, [_line(net_pay=100, employer_total=150, gross_pay=120), _line(id="L2", staff_id="S2", net_pay=200, employer_total=260, gross_pay=230)])
    assert d["status"] == "草稿" and d["count"] == 2 and d["confirmed_at"] is None and d["created_at"] == _NOW.isoformat()
    assert d["totals"]["net_pay"] == 300 and d["totals"]["employer_total"] == 410 and d["totals"]["gross_pay"] == 350
    assert set(d["totals"]) == set(_SUM_FIELDS)
    assert "lines" not in _run_dict(run), "沒給 lines 就不帶明細（清單頁）"


def test_ot_dict_shape():
    o = SimpleNamespace(id="O1", staff_id="S1", staff_name="測試員工", date=date(2026, 10, 3), start_time="10:00", end_time="18:00",
                        hours=8, day_kind="休息日", payout="加班費", project_id=None, project_name=None, shoot_id=None, reason=None,
                        status=None, approved_by=None, approved_at=_NOW, reject_note=None, credit_hours=None, credit_id=None,
                        pay_month="2026-10", pay_amount=2667, payroll_line_id=None, created_at=None)
    d = ot_dict(o)
    assert d["date"] == "2026-10-03" and d["hours"] == 8.0 and d["status"] == "待審" and d["approved_at"] == _NOW.isoformat()
    assert d["pay_amount"] == 2667 and d["credit_hours"] == 0.0 and d["project_name"] == "" and d["created_at"] is None
    o.pay_amount = None
    assert ot_dict(o)["pay_amount"] is None, "沒算定的加班費是 None，不是 0（卡片據此畫「核准時算」）"
