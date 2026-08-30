# -*- coding: utf-8 -*-
"""背景工作的狀態自癒（`core/bg_status.py`）。

這個檔三週改了 8 次，測試 0 次。它守的是一個使用者看得到的壞狀態：伺服器一
重啟（OTA 發布、master 重開），跑到一半的背景工作那個 asyncio task 就沒了，
DB 卻還停在 `pending` —— UI 上是一個永遠轉不完的圈。修法是在**讀取端推導**：
超過 claude 的 timeout 還停在 pending 就當它死了。

判錯的兩個方向都很痛，而且都是靜默的：
  · 判太早 → 還在跑的長工被改判 failed，使用者以為壞了、再按一次，兩份一起跑
  · 判太晚 → 那個圈繼續轉，沒有人知道要重按

所以這裡把「什麼時候算死了」的每一條邊界都釘住。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core.bg_status import STALE_AFTER, STALE_MSG, _BEAT_SEC, keepalive, settle


def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


# ── settle：什麼時候把 pending 改判成死了 ────────────────────────

def test_a_fresh_pending_job_is_left_alone():
    assert settle("pending", _ago(seconds=5)) == ("pending", "")


def test_a_pending_job_past_the_window_is_declared_dead_with_a_usable_message():
    """訊息要告訴人**下一步怎麼辦** —— 這是使用者唯一的出路。"""
    status, err = settle("pending", _ago(seconds=STALE_AFTER.total_seconds() + 10))
    assert status == "failed"
    assert err == STALE_MSG
    assert "再按一次" in err, "訊息沒告訴人怎麼脫困，那個圈對他來說還是死的"


def test_the_boundary_falls_on_the_late_side():
    """剛好卡在門檻上算還活著。判太早的代價是把還在跑的工作殺掉。"""
    assert settle("pending", _ago(seconds=STALE_AFTER.total_seconds() - 1))[0] == "pending"
    assert settle("pending", _ago(seconds=STALE_AFTER.total_seconds() + 1))[0] == "failed"


@pytest.mark.parametrize("status", ["ok", "failed", "", "queued"])
def test_only_pending_is_ever_reinterpreted(status):
    """🔴 已經有結論的列不准被動到 —— 一個很久以前成功的工作，`updated_at`
    當然早就超過門檻了。只看時間不看狀態的話，所有歷史成功紀錄會被改寫成失敗。"""
    old = _ago(days=30)
    assert settle(status, old, "原本的錯誤") == (status, "原本的錯誤")


def test_a_row_without_a_timestamp_is_left_alone():
    """沒有時間戳就無從判斷。猜「大概死了」會殺掉剛建好還沒開跑的列。"""
    assert settle("pending", None) == ("pending", "")


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time():
    """🔴 timestamptz 從 DB 回讀有時是 naive 的。當成本地時間算的話，台北
    +8 會讓每一列都「八小時前開始」→ 所有 pending 一律被判死。"""
    naive = datetime.utcnow() - timedelta(seconds=5)      # noqa: DTZ003 — 就是要測 naive
    assert settle("pending", naive)[0] == "pending"
    naive_old = datetime.utcnow() - timedelta(seconds=STALE_AFTER.total_seconds() + 10)
    assert settle("pending", naive_old)[0] == "failed"


def test_the_existing_error_is_replaced_not_appended_when_a_job_goes_stale():
    _, err = settle("pending", _ago(days=1), "舊訊息")
    assert err == STALE_MSG


# ── 心跳：長工不能被誤判 ────────────────────────────────────────

def test_the_heartbeat_is_strictly_faster_than_the_stale_window():
    """🔴 心跳間隔必須**明顯**小於門檻，不然一拍沒寫成就會被判死。

    這兩個數字綁在一起（`_BEAT_SEC` 由 `STALE_AFTER` 推導），釘住的是那個
    推導關係還在 —— 有人把 `_BEAT_SEC` 改成寫死的秒數，這裡會紅。
    """
    assert _BEAT_SEC == STALE_AFTER.total_seconds() / 2
    assert _BEAT_SEC * 2 <= STALE_AFTER.total_seconds(), "掉一拍就會被判死"


@pytest.mark.asyncio
async def test_keepalive_stops_beating_when_the_work_finishes():
    """🔴 心跳沒收乾淨的話，工作結束後它還在蓋 `updated_at` —— 那一列
    永遠不會 stale，就算它真的死了。"""
    beats = []

    async def fake_save(model_cls, row_id, **kw):
        beats.append(row_id)

    import core.bg_status as bg
    orig, bg.save_job_row = bg.save_job_row, fake_save
    orig_beat, bg._BEAT_SEC = bg._BEAT_SEC, 0.01
    try:
        async with keepalive(object, "row-1"):
            await asyncio.sleep(0.05)
        n = len(beats)
        assert n >= 2, "心跳根本沒跳"
        await asyncio.sleep(0.05)
        assert len(beats) == n, "工作結束後心跳還在跳"
    finally:
        bg.save_job_row, bg._BEAT_SEC = orig, orig_beat


@pytest.mark.asyncio
async def test_a_failed_beat_does_not_kill_the_job():
    """一拍寫失敗（DB 抖一下）只跳過那一拍 —— 心跳死掉的代價是還活著的
    長工被判 failed，比漏記一次進度嚴重得多。"""
    calls = []

    async def flaky(model_cls, row_id, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("DB 抖了一下")

    import core.bg_status as bg
    orig, bg.save_job_row = bg.save_job_row, flaky
    orig_beat, bg._BEAT_SEC = bg._BEAT_SEC, 0.01
    try:
        # 🔴 等到「失敗那一拍之後又跳了一拍」為止，不要靠固定 sleep 湊拍數 ——
        # Windows 的計時器精度約 15ms，寫死秒數的版本會間歇性紅。
        async with keepalive(object, "row-2"):
            for _ in range(200):
                if len(calls) >= 2:
                    break
                await asyncio.sleep(0.01)
        assert len(calls) >= 2, "第一拍失敗之後心跳就停了"
    finally:
        bg.save_job_row, bg._BEAT_SEC = orig, orig_beat


@pytest.mark.asyncio
async def test_keepalive_awaits_the_cancelled_task_before_returning():
    """🔴 取消若落在 `save_job_row` 中間，那條 pool 連線要等 session 的
    `__aexit__` 跑完才還得回去（pool_size=5）。不 await 就會漏連線。"""
    inside = {"n": 0}

    async def slow(model_cls, row_id, **kw):
        inside["n"] += 1
        try:
            await asyncio.sleep(1)
        finally:
            inside["n"] -= 1

    import core.bg_status as bg
    orig, bg.save_job_row = bg.save_job_row, slow
    orig_beat, bg._BEAT_SEC = bg._BEAT_SEC, 0.01
    try:
        async with keepalive(object, "row-3"):
            await asyncio.sleep(0.03)
        assert inside["n"] == 0, "keepalive 回來時心跳還卡在寫入中間（連線沒還）"
    finally:
        bg.save_job_row, bg._BEAT_SEC = orig, orig_beat


# ── 寫入端的約定（掃原始碼；跑起來要真的 claude）────────────────

def test_the_job_runner_stamps_the_start_time_after_taking_the_gate():
    """🔴 檔頭的前提：`updated_at` 必須是**工作真正開始**的時間，不是排進
    佇列的時間 —— `_call_claude` 有併發閘，排隊時間不算在 timeout 裡。
    少了 `on_start` 那一下，塞車時健康的工作會被 settle 誤判成死掉。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("core/bg_status.py"), "async def run_claude_job("))
    assert "on_start=lambda: save_job_row(model_cls, row_id)" in body, \
        "沒有在拿到併發閘之後補蓋開工時間戳"


def test_an_empty_answer_from_claude_is_a_failure_not_a_success():
    """空字串存成 `ok` 的話，畫面上是一份空白的企劃書，而且不會有人知道
    它其實沒生出來。"""
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("core/bg_status.py"), "async def run_claude_job("))
    i, j = body.index("if not content:"), body.index('status="ok"')
    assert i < j, "空回應的分支要在寫 ok 之前"
    assert 'status="failed"' in body[i:j]


def test_the_stale_window_leaves_room_for_the_write_back():
    """門檻要比 claude 自己的 timeout 長 —— 剛好等於的話，正常結束的工作
    在寫回 DB 的那幾秒裡會被讀取端判死。"""
    assert STALE_AFTER.total_seconds() >= 180 + 30
