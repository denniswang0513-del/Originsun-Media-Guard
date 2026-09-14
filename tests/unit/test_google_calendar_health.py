# -*- coding: utf-8 -*-
"""/health 2026-09-15 特徵測試：services/google_calendar 的「哪本日曆」純規則（7c1aea04 每種事件各一本，覆蓋率 50%）。
只釘現在的行為，不判斷對錯。整檔可刪。"""
from services import google_calendar as gc


def _settings(monkeypatch, gcal: dict):
    import config
    monkeypatch.setattr(config, "load_settings", lambda: {"google_calendar": gcal})


def test_calendars_keeps_only_known_kinds_with_a_value(monkeypatch):
    _settings(monkeypatch, {"calendars": {"leave": " leave@group ", "shoot": "", "milestone": None, "bogus": "x"}})
    assert gc.calendars() == {"leave": "leave@group"}
    _settings(monkeypatch, {"calendars": None})
    assert gc.calendars() == {}


def test_calendar_for_prefers_dedicated_then_shared_then_settings_id(monkeypatch):
    _settings(monkeypatch, {"calendar_id": " main@cal ", "calendars": {"leave": "leave@cal"}})
    assert gc.calendar_for("leave", "shared@cal") == "leave@cal"
    assert gc.calendar_for("shoot", "shared@cal") == "shared@cal"
    assert gc.calendar_for("shoot") == "main@cal"
    _settings(monkeypatch, {})
    assert gc.calendar_for("shoot") == ""


def test_calendar_id_strips_and_defaults_to_empty(monkeypatch):
    _settings(monkeypatch, {"calendar_id": "  abc  "})
    assert gc.calendar_id() == "abc"
    _settings(monkeypatch, {"calendar_id": None})
    assert gc.calendar_id() == ""


def test_load_sa_from_settings_returns_none_when_blank(monkeypatch):
    _settings(monkeypatch, {"service_account_json": "   "})
    assert gc.load_sa_from_settings() is None
    _settings(monkeypatch, {})
    assert gc.load_sa_from_settings() is None


def test_calendar_kinds_are_the_four_synced_kinds():
    assert gc.CALENDAR_KINDS == ("shoot", "schedule", "milestone", "leave")
