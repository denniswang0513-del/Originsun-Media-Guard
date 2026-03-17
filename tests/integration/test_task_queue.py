"""
Layer 2 — 任務佇列與 Socket 事件整合測試。

worker.py 使用 _emit_sync_for_job (core.logger) 在工作執行緒中 emit Socket 事件。
enqueue_job() 是公共 API。
"""
import asyncio
import time
import pytest
from core.schemas import BackupRequest
import core.state as state


MINIMAL_BACKUP = {
    "task_type": "backup",
    "project_name": "TestProject",
    "local_root": "/tmp/local",
    "nas_root": "/tmp/nas",
    "proxy_root": "/tmp/proxy",
    "cards": [["/tmp/card1", "Card_A"]],
    "do_hash": False,
    "do_transcode": False,
    "do_concat": False,
    "do_report": False,
}


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state between tests to avoid leaks."""
    # Reset before test
    for jid in list(state._jobs.keys()):
        state._jobs.pop(jid, None)
    with state._active_lock:
        for k in state._active_counts:
            state._active_counts[k] = 0
    # Reset main loop to current event loop
    try:
        state.set_main_loop(asyncio.get_event_loop())
    except RuntimeError:
        pass
    yield
    # Also reset after test
    for jid in list(state._jobs.keys()):
        state._jobs.pop(jid, None)
    with state._active_lock:
        for k in state._active_counts:
            state._active_counts[k] = 0


# ── 1. Job 被接受並處理 ─────────────────────────────────────
async def test_job_enqueued(async_client, mock_engine):
    """POST job → 200 → mock engine 被呼叫。"""
    r = await async_client.post("/api/v1/jobs", json=MINIMAL_BACKUP)
    assert r.status_code == 200, f"POST failed: {r.text}"
    data = r.json()
    assert "job_id" in data

    # Wait for task to complete (mock returns immediately)
    await asyncio.sleep(0.5)

    # Verify engine was called
    assert mock_engine.run_backup_job.called, "run_backup_job was never called"


# ── 2. task_done event 被發出 ────────────────────────────────
async def test_task_done_event(async_client, mock_engine, mocker):
    """mock run_backup_job + 攔截 sio.emit → POST job → 確認收到 task_status done。"""
    emitted = []

    from core.socket_mgr import sio

    async def capture_emit(event, data=None, **kwargs):
        emitted.append((event, data))

    mocker.patch.object(sio, 'emit', side_effect=capture_emit)

    r = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "DoneTest"},
    )
    assert r.status_code == 200

    await asyncio.sleep(1.5)

    done_events = [
        (e, d) for e, d in emitted
        if e == "task_status" and isinstance(d, dict) and d.get("status") == "done"
    ]
    assert len(done_events) > 0, (
        f"No 'done' event found. All task_status events: "
        f"{[(e, d) for e, d in emitted if e == 'task_status']}"
    )


# ── 3. 串行執行驗證 ─────────────────────────────────────────
async def test_serial_execution(async_client, mock_engine):
    """Backup concurrency=1 → 連送兩個 job → 確認兩個都被處理。"""
    state.CONCURRENCY_LIMITS["backup"] = 1

    r1 = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "Serial_A"},
    )
    assert r1.status_code == 200

    await asyncio.sleep(0.5)

    r2 = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "Serial_B"},
    )
    assert r2.status_code == 200

    # Wait for both to complete
    await asyncio.sleep(3.0)

    assert mock_engine.run_backup_job.call_count >= 2, (
        f"Expected 2+ calls, got {mock_engine.run_backup_job.call_count}"
    )
