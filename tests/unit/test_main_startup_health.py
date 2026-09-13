# -*- coding: utf-8 -*-
"""main._on_startup 的骨架 —— 特徵測試（/health 2026-09-13，拆 619 行前先釘）。

釘的是「DB 離線」那條路：loop 註冊、DB 連線失敗告警、**不跑** migration、
背景工的集合與順序、wedge watchdog 執行緒、工時排程啟動。migration 各段
（DB 在線才跑）另由 db/startup_migrations 的順序測試守；這裡不碰真 DB、
不寫 settings.json、不動 users。
"""
import asyncio
import threading

import pytest

import main
from core import state


@pytest.fixture
def startup_env(monkeypatch):
    """把 _on_startup 會碰到的外界全換成記錄器；回傳事件清單。"""
    events = []

    def _rec_coro(name, ret=None):
        async def _f(*a, **k):
            events.append(name)
            return ret
        _f.__name__ = name
        return _f

    def _rec_fn(name):
        def _f(*a, **k):
            events.append(name)
        return _f

    # DB：有套件、連不上
    import db.session as db_session
    monkeypatch.setattr(db_session, "init_db", _rec_coro("init_db", ret=False))
    monkeypatch.setattr(db_session, "get_session_factory", _rec_fn("get_session_factory"))
    # settings.json：讀到空、寫入只記錄
    import config
    monkeypatch.setattr(config, "load_settings", lambda: {})
    monkeypatch.setattr(config, "save_settings", _rec_fn("save_settings"))
    # 帳號回填：沒有人
    import routers.api_auth as api_auth
    monkeypatch.setattr(api_auth, "_get_all_users", _rec_coro("_get_all_users", ret=[]))
    # 背景工：全部換成「記名字就結束」的協程
    import core.maintenance as maint
    import core.agent_watch as agent_watch
    import core.scheduler as scheduler
    import services.media_log_catchup as catchup
    monkeypatch.setattr(maint, "notify_db_transition", _rec_coro("notify_db_transition"))
    monkeypatch.setattr(maint, "run_local_maintenance", _rec_coro("run_local_maintenance"))
    monkeypatch.setattr(agent_watch, "run_agent_watch", _rec_coro("run_agent_watch"))
    monkeypatch.setattr(catchup, "run_media_log_catchup", _rec_coro("run_media_log_catchup"))
    monkeypatch.setattr(scheduler, "run_scheduler", _rec_coro("run_scheduler"))
    monkeypatch.setattr(main, "_periodic_version_check", _rec_coro("_periodic_version_check"))
    monkeypatch.setattr(main, "_periodic_db_health", _rec_coro("_periodic_db_health"))
    monkeypatch.setattr(main, "_loop_heartbeat", _rec_coro("_loop_heartbeat"))
    # watchdog 執行緒：記名字、不真的起
    monkeypatch.setattr(main, "_wedge_watchdog", _rec_fn("_wedge_watchdog_target"))
    monkeypatch.setattr(threading.Thread, "start", lambda self: events.append(f"thread:{self.name}"))
    # 工時排程
    from services import timesheet_digest, timesheet_puller
    monkeypatch.setattr(timesheet_puller, "start_scheduler_task", _rec_fn("timesheet_puller.start"))
    monkeypatch.setattr(timesheet_digest, "start_scheduler_task", _rec_fn("timesheet_digest.start"))
    # state 還原
    prev_loop, prev_online = state.get_main_loop(), state.db_online
    yield events
    state.set_main_loop(prev_loop)
    state.db_online = prev_online


async def test_db_offline_startup_pins_loop_alert_no_migration_and_background_jobs(startup_env):
    events = startup_env
    await main._on_startup()
    for _ in range(3):           # 讓 create_task／ensure_future 排進去的協程跑完
        await asyncio.sleep(0)

    assert state.get_main_loop() is asyncio.get_running_loop()
    assert state.db_online is False
    assert "init_db" in events
    assert "get_session_factory" not in events, "DB 離線不該碰任何 migration"
    assert "notify_db_transition" in events, "有 DB 套件但連不上要告警"
    # 兩段「拆鑰匙」回填：settings 沒旗標就各跑一次（0 個帳號也寫旗標）；DB 密碼輪替沒舊密碼就不寫
    assert events.count("_get_all_users") == 2
    assert events.count("save_settings") == 2

    sync_tail = [e for e in events if e.startswith("thread:") or e.endswith(".start")]
    assert sync_tail == ["thread:wedge-watchdog", "timesheet_puller.start", "timesheet_digest.start"]

    bg = [e for e in events if e in {
        "_periodic_version_check", "_periodic_db_health", "run_local_maintenance",
        "run_agent_watch", "run_media_log_catchup", "_loop_heartbeat", "run_scheduler"}]
    assert bg == ["_periodic_version_check", "_periodic_db_health", "run_local_maintenance",
                  "run_agent_watch", "run_media_log_catchup", "_loop_heartbeat", "run_scheduler"]
