# -*- coding: utf-8 -*-
"""/health 2026-09-15 特徵測試：services/leave_service 的純函式（send／preview 共用的 hours_from_body、
序列化 request_dict／credit_dict、組單 build_request；覆蓋率 16%）。只釘現在的行為，不判斷對錯。整檔可刪。"""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from services import leave_service as ls


def _body(**kw):
    base = dict(leave_type="特休", start_date="2026-09-14", end_date="2026-09-18", reason=None)
    base.update(kw)
    return NS(**base)


def test_hours_from_body_prefers_explicit_hours_then_days_then_calendar():
    # hours 明給就用它（0.5 步進檢查）
    assert ls.hours_from_body(_body(hours=4), {}) == (4.0, "all")
    # 舊分頁只給 days → days×8
    assert ls.hours_from_body(_body(days=2), {}) == (16.0, "all")
    # 都沒給 → 起迄工作日 ×8（9/14 週一～9/18 週五＝5 天）
    assert ls.hours_from_body(_body(), {}) == (40.0, "all")
    # 半天限單日、4 小時
    assert ls.hours_from_body(_body(start_date="2026-09-14", end_date="2026-09-14", part="am"), {}) == (4.0, "am")


def test_hours_from_body_rejects_bad_part_zero_hours_and_weekends():
    with pytest.raises(ValueError):
        ls.hours_from_body(_body(part="night"), {})
    with pytest.raises(ValueError):                       # 0.3 不是 0.5 的倍數
        ls.hours_from_body(_body(hours=0.3), {})
    with pytest.raises(ValueError):                       # 週六～週日沒工作日
        ls.hours_from_body(_body(start_date="2026-09-19", end_date="2026-09-20"), {})
    with pytest.raises(ValueError):                       # 半天跨日
        ls.hours_from_body(_body(part="pm"), {})


def _req(**kw):
    base = dict(id="L1", staff_id="s1", staff_name="小明", leave_type="特休", start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 21), days=1, reason=None, status="待審", approved_by=None, approved_at=None,
                created_by="小明", created_at=datetime(2026, 9, 15, 9, 0), hours=8.0, part="all",
                start_time=None, end_time=None, reject_note=None, cancel_note=None, proof_path=None,
                google_event_id=None, synced_at=None, sync_error=None)
    base.update(kw)
    return NS(**base)


def test_request_dict_cancel_mode_only_for_approved_and_days_mirrors_hours():
    today = date(2026, 9, 15)
    d = ls.request_dict(_req(), today=today)
    assert d["cancel_mode"] is None and d["days"] == 1.0 and d["hours"] == 8.0
    d = ls.request_dict(_req(status="已核准", hours=4.0), today=today)
    assert d["cancel_mode"] == "free" and d["days"] == 0.5          # 開始前 ≥2 天
    d = ls.request_dict(_req(status="已核准", start_date=date(2026, 9, 16)), today=today)
    assert d["cancel_mode"] == "apply"                              # <2 天只能申請消假
    d = ls.request_dict(_req(status="已核准"), holidays={today: "颱風假"}, today=today)
    assert d["cancel_mode"] == "locked"


def test_credit_dict_remaining_is_hours_minus_used():
    c = NS(id="C1", staff_id="s1", staff_name=None, kind="補休", hours=12.0, granted_on=date(2026, 9, 1),
           expires_on=None, source=None, reason=None, shoot_id=None, status="已核准", approved_by=None,
           approved_at=None, note=None, created_by=None, created_at=None)
    d = ls.credit_dict(c, used=4.5)
    assert d["remaining"] == 7.5 and d["used"] == 4.5 and d["source"] == "manual"
    assert d["granted_on"] == "2026-09-01" and d["expires_on"] is None and d["staff_name"] == ""
    assert ls.credit_dict(NS(**{**c.__dict__, "hours": None}))["remaining"] == 0.0


def test_build_request_only_keeps_times_for_range_and_mirrors_days():
    b = _body(leave_type=" 病假 ", start_date="2026-09-16", end_date="2026-09-16", start_time="10:00 ", end_time="12:00",
              reason="  看醫生 ")
    r = ls.build_request("s1", "小明", b, 2.0, "range", "小明")
    assert (r.leave_type, r.hours, r.days, r.part, r.start_time, r.end_time, r.reason, r.status) == \
        ("病假", 2.0, 0.25, "range", "10:00", "12:00", "看醫生", "待審")
    assert r.start_date.date() == date(2026, 9, 16) and len(r.id) == 12
    r2 = ls.build_request("s1", "小明", b, 4.0, "am", "小明")
    assert r2.start_time is None and r2.end_time is None and r2.days == 0.5
