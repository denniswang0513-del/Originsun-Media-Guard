# -*- coding: utf-8 -*-
"""/health 2026-09-16 特徵測試：services/leave_application 的純函式（申請單序列化 application_dict、
每日時數試算 day_slots；模組覆蓋率 11.7%、09-15 新進）。只釘現在的行為，不判斷對錯。整檔可刪。"""
from datetime import date, datetime
from types import SimpleNamespace as NS

from services import leave_application as la


def _app(**kw):
    base = dict(id="A1", staff_id="s1", staff_name="小明", dates='["2026-09-21", "2026-09-22"]', part="all",
                start_time=None, end_time=None, items='[{"kind": "特休", "credit_id": "c1", "take": 8}, {"kind": "病假", "take": 8}]',
                hours=16.0, reason="出國", status="待審", proof_path=None, created_by="小明",
                created_at=datetime(2026, 9, 15, 9, 0), approved_by=None, approved_at=None, reject_note=None, cancel_note=None)
    base.update(kw)
    return NS(**base)


def test_application_dict_derives_range_days_kinds_and_proof_from_stored_json():
    d = la.application_dict(_app(), today=date(2026, 9, 15))
    assert d["dates"] == ["2026-09-21", "2026-09-22"]
    assert (d["start_date"], d["end_date"]) == ("2026-09-21", "2026-09-22")
    assert d["hours"] == 16.0 and d["days"] == 2.0                      # 8 小時＝1 天
    assert d["kinds"] == sorted({"特休", "病假"}) == ["特休", "病假"]      # 去重＋碼位排序
    assert d["proof_required"] is True                                   # 病假在 RECORD_META 要證明
    assert d["cancel_mode"] is None                                      # 沒核准就沒有消假模式
    assert d["created_at"] == "2026-09-15T09:00:00" and d["approved_at"] is None
    assert "children" not in d                                           # children=None 就不帶鍵
    assert d["part"] == "all" and d["start_time"] == "" and d["reason"] == "出國"


def test_application_dict_proof_not_required_when_no_kind_needs_it():
    d = la.application_dict(_app(items='[{"kind": "事假", "take": 8}]'))
    assert d["proof_required"] is False and d["kinds"] == ["事假"]


def test_application_dict_cancel_mode_only_when_approved():
    today = date(2026, 9, 15)
    assert la.application_dict(_app(status="已核准"), today=today)["cancel_mode"] == "free"          # 9/21 距今 ≥2 天
    assert la.application_dict(_app(status="已核准", dates='["2026-09-16"]'), today=today)["cancel_mode"] == "apply"
    assert la.application_dict(_app(status="已核准"), today=today, holidays={today: "颱風假"})["cancel_mode"] == "locked"
    assert la.application_dict(_app(status="已核准", dates="[]"), today=today)["cancel_mode"] is None   # 沒日期不算


def test_application_dict_tolerates_bad_or_missing_json_and_empty_children():
    d = la.application_dict(_app(dates="not json", items=None, hours=None), children=[])
    assert d["dates"] == [] and d["start_date"] == "" and d["end_date"] == ""
    assert d["items"] == [] and d["kinds"] == [] and d["proof_required"] is False
    assert d["hours"] == 0.0 and d["days"] == 0.0
    assert d["children"] == []                                           # 給了空清單就帶鍵
    # dates 存成 list（不是字串）也照收；型別不對（dict）退回預設
    assert la.application_dict(_app(dates=["2026-09-21"]))["dates"] == ["2026-09-21"]
    assert la.application_dict(_app(dates='{"a": 1}'))["dates"] == []


def test_day_slots_applies_one_time_spec_to_every_day_and_keeps_times_only_for_range():
    rows = la.day_slots(["2026-09-21", "2026-09-22"], "all", None, None, None)
    assert [r["hours"] for r in rows] == [8.0, 8.0] and all(r["errors"] == [] for r in rows)
    assert rows[0]["start_time"] is None and rows[0]["part"] == "all"
    rows = la.day_slots(["2026-09-21"], "am", "09:00", "12:00", None)
    assert rows[0]["hours"] == 4.0 and rows[0]["start_time"] is None    # 半天不帶時段
    rows = la.day_slots(["2026-09-21"], "range", "09:00", "11:20", None)
    assert rows[0]["hours"] == 2.5 and (rows[0]["start_time"], rows[0]["end_time"]) == ("09:00", "11:20")
    assert la.day_slots(["2026-09-21"], "", None, None, None)[0]["part"] == "all"   # 空 part 視同整天


def test_day_slots_reports_bad_range_per_day_and_zero_hours_on_non_workday():
    rows = la.day_slots(["2026-09-21"], "range", "11:00", "09:00", None)   # 迄早於起
    assert rows[0]["hours"] == 0.0
    assert [e["code"] for e in rows[0]["errors"]] == ["bad_range"] and rows[0]["errors"][0]["msg"]
    rows = la.day_slots(["2026-09-19"], "am", None, None, None)             # 週六：0 小時、不算錯
    assert rows[0]["hours"] == 0.0 and rows[0]["errors"] == []
    rows = la.day_slots(["2026-09-19"], "all", None, None, None)
    assert rows[0]["hours"] == 0.0 and rows[0]["errors"] == []
