"""/health 2026-09-19 特徵測試：core/drone_watcher.check_and_fire（排程 tick 的觸發閘）。

只釘住現在的行為：到時間才跑、一天一次、重啟後靠 history 補防、失敗不鎖今天（下一分鐘重試）。
"""
from datetime import datetime

import pytest

from core import drone_watcher as dw


@pytest.fixture
def wired(monkeypatch):
    calls = {"scan": [], "hist": []}
    monkeypatch.setattr(dw, "_last_fired_date", None)
    monkeypatch.setattr(dw, "_load_config_cached", lambda: {"enabled": True, "run_time": "02:00"})
    monkeypatch.setattr(dw, "load_history", lambda: [])
    monkeypatch.setattr(dw, "run_watcher_scan", lambda cfg, trigger: calls["scan"].append(trigger))
    monkeypatch.setattr(dw, "append_history", lambda entry: calls["hist"].append(entry))

    class _Clock(datetime):
        _now = datetime(2026, 9, 19, 2, 0, 30)

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(dw, "datetime", _Clock)
    calls["clock"] = _Clock
    return calls


def test_disabled_or_wrong_minute_never_scans(wired, monkeypatch):
    monkeypatch.setattr(dw, "_load_config_cached", lambda: {"enabled": False, "run_time": "02:00"})
    assert dw.check_and_fire() is False
    monkeypatch.setattr(dw, "_load_config_cached", lambda: {"enabled": True, "run_time": "02:00"})
    wired["clock"]._now = datetime(2026, 9, 19, 2, 1, 0)
    assert dw.check_and_fire() is False
    assert wired["scan"] == []


def test_fires_once_per_day_on_the_configured_minute(wired):
    assert dw.check_and_fire() is True
    assert wired["scan"] == ["scheduled"]
    assert dw._last_fired_date == "2026-09-19"
    assert dw.check_and_fire() is False                 # 同一天第二個 tick 不再跑
    assert wired["scan"] == ["scheduled"]


def test_history_from_before_a_restart_counts_as_already_fired(wired, monkeypatch):
    monkeypatch.setattr(dw, "load_history", lambda: [
        {"trigger": "manual", "ts": "2026-09-19T01:00:00"},
        {"trigger": "scheduled", "ts": "2026-09-19T02:00:05"},
    ])
    assert dw.check_and_fire() is False
    assert wired["scan"] == []
    assert dw._last_fired_date == "2026-09-19"          # 補上 in-process guard


def test_only_the_five_newest_history_rows_are_consulted(wired, monkeypatch):
    rows = [{"trigger": "manual", "ts": "2026-09-19T00:00:00"}] * 5 + [
        {"trigger": "scheduled", "ts": "2026-09-19T02:00:05"}]
    monkeypatch.setattr(dw, "load_history", lambda: rows)
    assert dw.check_and_fire() is True                  # 第 6 筆看不到 → 照跑
    assert wired["scan"] == ["scheduled"]


def test_a_failing_scan_is_logged_and_leaves_today_open_for_retry(wired, monkeypatch):
    def boom(cfg, trigger):
        raise RuntimeError("nas down")
    monkeypatch.setattr(dw, "run_watcher_scan", boom)
    assert dw.check_and_fire() is True
    assert dw._last_fired_date is None
    assert wired["hist"] == [{"ts": "2026-09-19T02:00:30", "trigger": "scheduled",
                              "status": "error", "error": "nas down"}]
