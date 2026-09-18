# -*- coding: utf-8 -*-
"""研究助理的每週排程（docs/KNOWLEDGE_BASE_PLAN.md §9.3）。

owner 2026-09-18：「定期依照我書的內容主題幫我做研究助理」。這支釘四件事：

  1. 兩道開關都預設關 —— 這一條會叫 claude 上網，沒人開就不准自己跑。
  2. 同一週只跑一次，一輪有上限；待補的書不算。
  3. 排程的 tick 不會被擋 —— 六本書各跑七分鐘就是四十分鐘，貸款提醒那些會一起停擺。
  4. 只在 master 跑（機隊九台共用同一份設定）。
"""
import asyncio
from datetime import datetime

import pytest

from services import knowledge_watch as kw
from tests.unit._srcscan import func_body, repo_src


# ── 1. 預設是關的 ───────────────────────────────────────────
def test_both_switches_default_to_off():
    from config import _DEFAULT_SETTINGS
    watch = (_DEFAULT_SETTINGS.get("knowledge") or {}).get("watch") or {}
    assert watch.get("enabled") is False, "會叫 claude 上網的功能不准預設開（同 intel／social）"
    assert watch == {"enabled": False, "weekday": 6, "hour": 21}


def test_a_book_is_not_watched_unless_it_says_so():
    """`meta` 沒有 watch 這個鍵＝關；書架與書頁回的是布林不是 None。"""
    src = repo_src("services/knowledge_service.py")
    assert '"watch": bool(meta.get("watch")),' in func_body(src, "def _summary(")


def test_nothing_runs_when_the_global_switch_is_off(monkeypatch):
    called = []
    monkeypatch.setattr(kw, "settings_watch", lambda: {"enabled": False, "weekday": 6, "hour": 21})
    monkeypatch.setattr(kw, "run_once", lambda now: called.append(now))
    asyncio.run(kw.weekly_check())
    assert called == []


# ── 2. 一週一次、有上限、待補的不算 ─────────────────────────
def test_the_week_key_is_the_iso_week():
    assert kw.iso_week(datetime(2026, 9, 18)) == "2026-W38"
    # 跨年那週照 ISO 走：2025-12-29 是週一，算 2026 年的第一週
    assert kw.iso_week(datetime(2025, 12, 29)) == "2026-W01"
    assert kw.iso_week(datetime(2027, 1, 3)) == "2026-W53"


def _books(monkeypatch, rows, last=None):
    monkeypatch.setattr(kw.ks, "list_books", lambda *a, **k: rows)
    monkeypatch.setattr(kw.ks, "watch_last", lambda bid: (last or {}).get(bid, ""))


def test_only_watched_books_with_a_file_are_due(monkeypatch):
    _books(monkeypatch, [
        {"id": "a" * 16, "watch": True, "status": "compiled"},
        {"id": "b" * 16, "watch": False, "status": "compiled"},
        {"id": "c" * 16, "watch": True, "status": "pending"},     # 還沒有檔，沒東西可讀
        {"id": "d" * 16, "watch": True, "status": "uploaded"},
    ])
    assert kw.due_books(datetime(2026, 9, 18)) == ["a" * 16, "d" * 16]


def test_a_book_already_done_this_week_is_skipped(monkeypatch):
    now = datetime(2026, 9, 18)
    rows = [{"id": "a" * 16, "watch": True, "status": "compiled"},
            {"id": "b" * 16, "watch": True, "status": "compiled"}]
    _books(monkeypatch, rows, last={"a" * 16: kw.iso_week(now)})
    assert kw.due_books(now) == ["b" * 16]
    _books(monkeypatch, rows, last={"a" * 16: "2026-W37"})
    assert kw.due_books(now) == ["a" * 16, "b" * 16], "上一週跑過的，這一週還是要再跑"


def test_one_run_is_capped(monkeypatch):
    _books(monkeypatch, [{"id": str(i) * 16, "watch": True, "status": "compiled"} for i in range(9)])
    assert len(kw.due_books(datetime(2026, 9, 18))) == kw.MAX_BOOKS_PER_RUN


def test_one_bad_book_does_not_stop_the_batch(monkeypatch):
    ran, marked = [], []
    _books(monkeypatch, [{"id": "a" * 16, "watch": True, "status": "compiled"},
                         {"id": "b" * 16, "watch": True, "status": "compiled"}])
    monkeypatch.setattr(kw.ks, "start_extend", lambda bid, url="": None)
    monkeypatch.setattr(kw.ks, "set_watch_last", lambda bid, week: marked.append(bid))

    async def _run(bid, *a, **k):
        ran.append(bid)
        if bid == "a" * 16:
            raise RuntimeError("這本炸了")

    monkeypatch.setattr(kw.ks, "run_extend", _run)
    assert asyncio.run(kw.run_once(datetime(2026, 9, 18))) == 2
    assert ran == ["a" * 16, "b" * 16]
    assert marked == ["a" * 16, "b" * 16], "失敗的也要記這一週，不要同一晚重打同一本"


def test_a_busy_book_is_left_alone(monkeypatch):
    ran = []
    _books(monkeypatch, [{"id": "a" * 16, "watch": True, "status": "compiled"}])

    def _start(bid, url=""):
        raise kw.ks.BookBusy(bid)

    monkeypatch.setattr(kw.ks, "start_extend", _start)
    monkeypatch.setattr(kw.ks, "run_extend", lambda *a, **k: ran.append(1))
    assert asyncio.run(kw.run_once(datetime(2026, 9, 18))) == 0
    assert ran == []


# ── 3. 排程的 tick 不會被擋 ─────────────────────────────────
def test_the_tick_only_fires_a_background_task():
    """六本書各跑七分鐘就是四十分鐘；在 tick 裡 await 下去，貸款提醒那些會一起停擺。"""
    body = func_body(repo_src("services/knowledge_watch.py"), "async def weekly_check(")
    assert "fire(run_once(now)" in body
    assert "await run_once(" not in body


def test_the_scheduler_wraps_it_so_one_exception_does_not_kill_the_loop():
    src = repo_src("core/scheduler.py")
    i = src.index("from services.knowledge_watch import weekly_check")
    block = src[i - 200:i + 260]
    assert "try:" in block and "except Exception:" in block and "_log.exception" in block


# ── 4. 這個鐘點不在 finance 底下 ────────────────────────────
def test_the_hour_comes_from_the_knowledge_settings():
    assert kw.watch_hour({"knowledge": {"watch": {"hour": 7}}}) == 7
    assert kw.watch_hour({"finance": {"hour": 3}}) == 21, "不准掉回 finance 的鐘點"
    assert kw.watch_hour({"knowledge": {"watch": {"hour": 99}}}) == 21, "超出範圍用預設"
    assert kw.watch_hour({}) == 21


def test_the_shared_base_still_reads_finance_for_everyone_else():
    """加 `hour_getter` 不能改到既有呼叫端的行為。"""
    body = func_body(repo_src("core/scheduler.py"), "async def _run_daily_master_task(")
    assert 'hour_getter=None' in body or "hour_getter" in body.split("\n")[0]
    assert '(s.get("finance") or {}).get(hour_key) or 9' in body, "沒給 hour_getter 還是讀 finance"


def test_it_only_runs_on_the_master():
    """機隊九台共用同一份設定；每台都跑等於同一本書被找九次。"""
    body = func_body(repo_src("services/knowledge_watch.py"), "async def weekly_check(")
    assert "_run_daily_master_task" in body, "master gate 靠共用底座，不要自己再判一次"
    base = func_body(repo_src("core/scheduler.py"), "async def _run_daily_master_task(")
    assert "is_master_machine()" in base


def test_no_claude_cli_means_skip_not_crash():
    body = func_body(repo_src("services/knowledge_watch.py"), "async def weekly_check(")
    assert "claude_available()" in body


@pytest.mark.parametrize("bad", [None, "x", -1, 7, 99])
def test_a_broken_setting_falls_back_instead_of_crashing(bad):
    assert kw._int_in(bad, 6, 0, 6) == 6

# ── 5. 全域開關要有地方按（士源不寫程式）────────────────────
def test_the_global_switch_has_a_button():
    """「去改 settings.json」對不寫程式的人等於這個功能永遠開不起來。"""
    js = repo_src("frontend/js/knowledge/extend.js")
    assert "export async function toggleWatchAll(" in js
    assert 'data-kact="watch-all"' in js
    assert 'data-kact="watch"' in js, "每本書那一道也要在同一頁"
    line = next(ln for ln in repo_src("frontend/js/knowledge/index.js").splitlines()
                if "act === 'watch-all'" in ln)
    assert "toggleWatchAll(el.checked)" in line


def test_only_an_admin_can_flip_the_global_switch():
    src = repo_src("routers/api_knowledge.py")
    assert "check_admin(request)" in func_body(src, "async def watch_put(")
    assert "payload_grants(payload)" in func_body(src, "async def watch_get("), (
        "can_edit 要用既有的判定，不要自己再寫一套")


def test_the_watch_routes_come_before_the_book_id_route():
    """FastAPI 照註冊順序比對 —— 排在 `/{book_id}` 後面的話，「watch」會被當成書的 id。"""
    src = repo_src("routers/api_knowledge.py")
    assert src.index('@router.get("/watch")') < src.index('@router.get("/{book_id}")')
    from routers.api_knowledge import router
    paths = [r.path for r in router.routes]
    assert paths.index("/api/v1/knowledge/watch") < paths.index("/api/v1/knowledge/{book_id}")


def test_saving_the_switch_does_not_wipe_the_shelf_path():
    """🔴 `knowledge.root` 被 /api/settings/load 抹掉（api_system._SECRET_SUBKEYS）——
    前端拿不到 root 又整塊送回去，書架的路徑就沒了。所以這一格不走那條路。"""
    body = func_body(repo_src("services/knowledge_watch.py"), "def save_watch(")
    assert 'save_settings({"knowledge": {"watch": nxt}})' in body, "只寫 watch 這一格"
    assert body.count("save_settings(") == 1, "只寫一次，而且就是上面那一格"


def test_a_broken_switch_value_is_clamped_not_saved(monkeypatch):
    saved = {}
    monkeypatch.setattr(kw, "settings_watch", lambda: {"enabled": False, "weekday": 6, "hour": 21})
    monkeypatch.setitem(__import__("sys").modules, "config",
                        type("M", (), {"save_settings": staticmethod(lambda d: saved.update(d)),
                                       "load_settings": staticmethod(dict)})())
    assert kw.save_watch(enabled=True, weekday=99, hour="x") == {"enabled": True, "weekday": 6, "hour": 21}
    assert saved == {"knowledge": {"watch": {"enabled": True, "weekday": 6, "hour": 21}}}
