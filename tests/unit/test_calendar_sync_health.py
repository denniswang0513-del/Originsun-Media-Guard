# -*- coding: utf-8 -*-
"""/health 2026-09-15 特徵測試：services/calendar_sync 的純函式（行事曆一期 fd3cf0df 起，覆蓋率 22%）。
只把現在的行為釘住，不判斷對錯。整檔可刪。"""
from datetime import date, datetime
from types import SimpleNamespace as NS

from services import calendar_sync as cs


def test_attendees_of_accepts_json_string_or_list_and_dedups_by_staff_id():
    a = NS(attendees='[{"staff_id":"s1","name":"小明","role":"攝影"},{"staff_id":"s1","name":"小明"},{"name":"外部阿花","contact":"0912"}]')
    out = cs.attendees_of(a)
    assert [x["staff_id"] for x in out] == ["s1", ""]
    assert out[0] == {"staff_id": "s1", "name": "小明", "role": "攝影", "external": False, "contact": ""}
    assert out[1]["external"] is True and out[1]["contact"] == "0912"
    # list 直接吃；壞 JSON／None → 空
    assert cs.attendees_of(NS(attendees=[{"name": "A"}]))[0]["name"] == "A"
    assert cs.attendees_of(NS(attendees="{not json")) == []
    assert cs.attendees_of(NS(attendees=None)) == []


def test_timesheet_ids_of_stringifies_and_drops_falsy():
    assert cs.timesheet_ids_of(NS(timesheet_ids='["a", 7, "", null]')) == ["a", "7"]
    assert cs.timesheet_ids_of(NS(timesheet_ids=["x"])) == ["x"]
    assert cs.timesheet_ids_of(NS(timesheet_ids='{"a":1}')) == []      # 不是 list → 空
    assert cs.timesheet_ids_of(NS(timesheet_ids="oops")) == []
    assert cs.timesheet_ids_of(NS(timesheet_ids=None)) == []


def _sched(**kw):
    base = dict(id="sch1", kind=None, project_id=None, title=None, date=date(2026, 9, 15), end_date=None,
                start_time=None, end_time=None, attendees=None, location_text=None, notes=None, status=None,
                timesheet_ids=None, plan_row_id=None, google_event_id=None, synced_at=None, sync_error=None,
                created_by=None)
    base.update(kw)
    return NS(**base)


def test_schedule_dict_fills_every_key_with_defaults():
    d = cs.schedule_dict(_sched())
    assert d == {
        "id": "sch1", "kind": "work", "project_id": "", "project_name": "", "client": "",
        "title": "", "date": "2026-09-15", "end_date": "", "start_time": "", "end_time": "",
        "attendees": [], "location_text": "", "notes": "", "status": "planned", "timesheet_ids": [],
        "plan_row_id": "", "google_event_id": "", "synced_at": None, "sync_error": "", "created_by": "",
    }


def test_schedule_dict_passes_through_values_and_iso_timestamps():
    d = cs.schedule_dict(_sched(kind="meeting", project_id="P1", title="開會", end_date=date(2026, 9, 16),
                                start_time="09:00", end_time="10:30", status="done", plan_row_id="ts9",
                                google_event_id="g1", synced_at=datetime(2026, 9, 15, 8, 0), created_by="admin",
                                timesheet_ids='["t1"]'),
                         project_name="案名", client="客戶")
    assert d["kind"] == "meeting" and d["project_name"] == "案名" and d["client"] == "客戶"
    assert d["end_date"] == "2026-09-16" and d["synced_at"] == "2026-09-15T08:00:00"
    assert d["timesheet_ids"] == ["t1"] and d["status"] == "done"


def test_colors_from_settings_merges_over_defaults(monkeypatch):
    import config
    monkeypatch.setattr(config, "load_settings", lambda: {"google_calendar": {"colors": {"leave": "11", "bogus": "1"}}})
    out = cs.colors_from_settings()
    assert out["leave"] == "11" and out["shoot"] == "6" and "bogus" not in out
    monkeypatch.setattr(config, "load_settings", lambda: {})
    assert cs.colors_from_settings()["leave"] == "8"
