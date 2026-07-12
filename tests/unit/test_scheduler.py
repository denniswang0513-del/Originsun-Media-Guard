"""
Unit tests for the cron scheduler module.
"""
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ── Helpers ──────────────────────────────────────────────────


@pytest.fixture
def tmp_schedule_file(tmp_path, monkeypatch):
    """Point scheduler's _SCHEDULE_FILE to a temp location."""
    path = str(tmp_path / "scheduled_jobs.json")
    monkeypatch.setattr("core.scheduler._SCHEDULE_FILE", path)
    return path


# ── Tests ────────────────────────────────────────────────────


class TestCronValidation:
    def test_valid_cron(self):
        from core.scheduler import is_valid_cron
        assert is_valid_cron("0 2 * * *") is True
        assert is_valid_cron("30 18 * * 1-5") is True
        assert is_valid_cron("*/15 * * * *") is True

    def test_invalid_cron(self):
        from core.scheduler import is_valid_cron
        assert is_valid_cron("not a cron") is False
        assert is_valid_cron("") is False
        assert is_valid_cron("60 25 * * *") is False  # invalid minute/hour


class TestDailyMasterGate:
    """/simplify C：_run_daily_master_task 共用底座的 gate 副作用（_loan_due_check /
    _finance_calendar_check 共用，這幾個分支手抄易漂 → 鎖住行為）。"""

    def _run(self, monkeypatch, key, *, master=True, db=True, factory=True,
             hour_ok=True, body=None):
        import asyncio
        import core.state
        import core.topology
        import config
        import db.session as dbsession
        import core.scheduler as sched
        sched._daily_fired.clear()
        monkeypatch.setattr(core.topology, "is_master_machine", lambda: master)
        monkeypatch.setattr(core.state, "db_online", db)
        monkeypatch.setattr(dbsession, "get_session_factory",
                            lambda: (lambda: None) if factory else None)
        # hour_ok：remind_hour=0 → now.hour<0 永遠 False（過 hour gate）；否則 99 擋掉
        monkeypatch.setattr(config, "load_settings",
                            lambda: {"finance": {"loan_remind_hour": 0 if hour_ok else 99}})
        calls = []

        async def _default_body(f, n):
            calls.append((f, n))
        asyncio.run(sched._run_daily_master_task(
            key, "loan_remind_hour", body or _default_body))
        return calls, sched._daily_fired

    def test_master_runs_body_and_fires(self, monkeypatch):
        calls, fired = self._run(monkeypatch, "t_ok")
        assert len(calls) == 1 and fired.get("t_ok") is not None

    def test_non_master_marks_fired_skips_body(self, monkeypatch):
        calls, fired = self._run(monkeypatch, "t_nm", master=False)
        assert calls == [] and fired.get("t_nm") is not None  # 標記當日、不跑 body

    def test_db_offline_no_fire_retry(self, monkeypatch):
        calls, fired = self._run(monkeypatch, "t_db", db=False)
        assert calls == [] and "t_db" not in fired            # 不標記 → 下 tick 重試

    def test_before_hour_no_fire_retry(self, monkeypatch):
        calls, fired = self._run(monkeypatch, "t_hr", hour_ok=False)
        assert calls == [] and "t_hr" not in fired

    def test_body_exception_no_fire(self, monkeypatch):
        async def _boom(f, n):
            raise RuntimeError("boom")
        with pytest.raises(RuntimeError):
            self._run(monkeypatch, "t_ex", body=_boom)
        import core.scheduler as sched
        assert "t_ex" not in sched._daily_fired                # body 拋錯 → 不標記


class TestComputeNextRun:
    def test_next_run_daily(self):
        from core.scheduler import compute_next_run
        base = datetime(2026, 3, 19, 10, 0, 0)
        result = compute_next_run("0 2 * * *", base)
        expected = datetime(2026, 3, 20, 2, 0, 0)
        assert result == expected.isoformat(timespec="seconds")

    def test_next_run_already_past_today(self):
        from core.scheduler import compute_next_run
        base = datetime(2026, 3, 19, 3, 0, 0)  # 3am, past 2am
        result = compute_next_run("0 2 * * *", base)
        expected = datetime(2026, 3, 20, 2, 0, 0)
        assert result == expected.isoformat(timespec="seconds")


class TestPersistence:
    def test_load_empty(self, tmp_schedule_file):
        from core.scheduler import load_schedules
        assert load_schedules() == []

    def test_save_and_load(self, tmp_schedule_file):
        from core.scheduler import load_schedules, save_schedules
        data = [{"schedule_id": "test-1", "name": "Test", "cron": "0 2 * * *"}]
        save_schedules(data)
        loaded = load_schedules()
        assert len(loaded) == 1
        assert loaded[0]["schedule_id"] == "test-1"

    def test_save_atomic(self, tmp_schedule_file):
        """Verify no .tmp file remains after save."""
        from core.scheduler import save_schedules
        save_schedules([{"id": "x"}])
        assert not os.path.exists(tmp_schedule_file + ".tmp")
        assert os.path.exists(tmp_schedule_file)

    def test_load_corrupt_file(self, tmp_schedule_file):
        """Corrupt JSON should return empty list, not crash."""
        from core.scheduler import load_schedules
        with open(tmp_schedule_file, "w") as f:
            f.write("not json{{{")
        assert load_schedules() == []


class TestBuildRequest:
    def test_build_backup_request(self):
        from core.scheduler import build_request
        data = {
            "project_name": "TestProject",
            "local_root": "D:/Backup",
            "nas_root": "Z:/Backup",
            "proxy_root": "D:/Proxy",
            "cards": [["CARD", "U:/Source"]],
        }
        req = build_request("backup", data)
        assert req.project_name == "TestProject"
        assert req.task_type == "backup"

    def test_build_transcode_request(self):
        from core.scheduler import build_request
        data = {"sources": ["D:/Source"], "dest_dir": "D:/Proxy"}
        req = build_request("transcode", data)
        assert req.dest_dir == "D:/Proxy"

    def test_build_invalid_type(self):
        from core.scheduler import build_request
        with pytest.raises(ValueError, match="不支援"):
            build_request("unknown_type", {})

    def test_build_invalid_data(self):
        from core.scheduler import build_request
        with pytest.raises(Exception):
            build_request("backup", {})  # missing required fields


class TestCheckAndDispatch:
    def test_dispatch_due_schedule(self, tmp_schedule_file):
        from core.scheduler import save_schedules, _check_and_dispatch

        past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        schedules = [{
            "schedule_id": "test-due",
            "name": "Due Task",
            "cron": "0 2 * * *",
            "task_type": "backup",
            "enabled": True,
            "next_run": past,
            "request": {
                "project_name": "TestProject",
                "local_root": "D:/Backup",
                "nas_root": "Z:/Backup",
                "proxy_root": "D:/Proxy",
                "cards": [["CARD", "U:/Source"]],
            },
        }]
        save_schedules(schedules)

        with patch("core.worker.enqueue_job") as mock_enqueue:
            count = _check_and_dispatch()
            assert count == 1
            mock_enqueue.assert_called_once()
            args = mock_enqueue.call_args
            assert args[0][1] == "TestProject"  # project_name
            assert args[0][2] == "backup"  # task_type

    def test_skip_disabled_schedule(self, tmp_schedule_file):
        from core.scheduler import save_schedules, _check_and_dispatch

        past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        schedules = [{
            "schedule_id": "test-disabled",
            "name": "Disabled Task",
            "cron": "0 2 * * *",
            "task_type": "backup",
            "enabled": False,
            "next_run": past,
            "request": {
                "project_name": "TestProject",
                "local_root": "D:/Backup",
                "nas_root": "Z:/Backup",
                "proxy_root": "D:/Proxy",
                "cards": [["CARD", "U:/Source"]],
            },
        }]
        save_schedules(schedules)

        with patch("core.worker.enqueue_job") as mock_enqueue:
            count = _check_and_dispatch()
            assert count == 0
            mock_enqueue.assert_not_called()

    def test_skip_future_schedule(self, tmp_schedule_file):
        from core.scheduler import save_schedules, _check_and_dispatch

        future = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        schedules = [{
            "schedule_id": "test-future",
            "name": "Future Task",
            "cron": "0 2 * * *",
            "task_type": "backup",
            "enabled": True,
            "next_run": future,
            "request": {
                "project_name": "TestProject",
                "local_root": "D:/Backup",
                "nas_root": "Z:/Backup",
                "proxy_root": "D:/Proxy",
                "cards": [["CARD", "U:/Source"]],
            },
        }]
        save_schedules(schedules)

        with patch("core.worker.enqueue_job") as mock_enqueue:
            count = _check_and_dispatch()
            assert count == 0
            mock_enqueue.assert_not_called()

    def test_updates_next_run_after_dispatch(self, tmp_schedule_file):
        from core.scheduler import save_schedules, _check_and_dispatch, load_schedules

        past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        schedules = [{
            "schedule_id": "test-update",
            "name": "Update Test",
            "cron": "0 2 * * *",
            "task_type": "backup",
            "enabled": True,
            "next_run": past,
            "request": {
                "project_name": "P",
                "local_root": "D:/B",
                "nas_root": "Z:/B",
                "proxy_root": "D:/P",
                "cards": [["C", "U:/S"]],
            },
        }]
        save_schedules(schedules)

        with patch("core.worker.enqueue_job"):
            _check_and_dispatch()

        updated = load_schedules()
        assert updated[0]["last_run"] is not None
        # next_run should be in the future now
        next_dt = datetime.fromisoformat(updated[0]["next_run"])
        assert next_dt > datetime.now()

    def test_one_shot_disables_after_dispatch(self, tmp_schedule_file):
        """單次排程（無 cron）執行後應自動停用。"""
        from core.scheduler import save_schedules, _check_and_dispatch, load_schedules

        past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        schedules = [{
            "schedule_id": "test-oneshot",
            "name": "One Shot Task",
            "cron": None,
            "run_at": past,
            "task_type": "backup",
            "enabled": True,
            "next_run": past,
            "request": {
                "project_name": "P",
                "local_root": "D:/B",
                "nas_root": "Z:/B",
                "proxy_root": "D:/P",
                "cards": [["C", "U:/S"]],
            },
        }]
        save_schedules(schedules)

        with patch("core.worker.enqueue_job"):
            count = _check_and_dispatch()
            assert count == 1

        updated = load_schedules()
        assert updated[0]["enabled"] is False
        assert updated[0]["next_run"] is None
        assert updated[0]["last_run"] is not None
