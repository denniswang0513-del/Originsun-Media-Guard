"""
Layer 2 — 衝突解決流程測試。

worker.py 的 _on_conflict 是 _execute_job 內的 nested function，
直接使用 JobState 的 conflict_event / current_conflict_action / global_conflict_action。
這裡重建相同邏輯來測試。
"""
import threading
import time
import pytest
import core.state as state


def _simulate_on_conflict(job: state.JobState, data: dict, timeout: float = 60) -> str:
    """
    Reproduce the _on_conflict logic from worker.py _execute_job (lines 149-156).
    Skips the emit call since we're testing the conflict resolution logic, not Socket.IO.
    """
    if job.global_conflict_action:
        return job.global_conflict_action
    job.conflict_event.clear()
    # In production, _emit_sync_for_job would be called here
    if not job.conflict_event.wait(timeout=timeout):
        return 'skip'
    return job.current_conflict_action


CONFLICT_DATA = {"file": "test.mp4", "src": "/a/test.mp4", "dst": "/b/test.mp4"}


# ── 1. global_conflict_action = "skip" → 直接回傳 ──────────
def test_global_skip():
    """When global_conflict_action is set to 'skip', return immediately without emitting."""
    job = state.JobState(job_id="c1", task_type="backup", project_name="Test")
    job.global_conflict_action = "skip"

    result = _simulate_on_conflict(job, CONFLICT_DATA)
    assert result == "skip"


# ── 2. global_conflict_action = "overwrite" → 直接回傳 ─────
def test_global_overwrite():
    """When global_conflict_action is set to 'overwrite', return immediately."""
    job = state.JobState(job_id="c2", task_type="backup", project_name="Test")
    job.global_conflict_action = "overwrite"

    result = _simulate_on_conflict(job, CONFLICT_DATA)
    assert result == "overwrite"


# ── 3. 手動解決 — 另一個 thread set event ──────────────────
def test_manual_resolution():
    """Simulates user clicking 'copy' — another thread sets conflict_event after 50ms."""
    job = state.JobState(job_id="c3", task_type="backup", project_name="Test")
    job.global_conflict_action = None
    job.current_conflict_action = "copy"

    def resolve_after_delay():
        time.sleep(0.05)
        job.current_conflict_action = "copy"
        job.conflict_event.set()

    t = threading.Thread(target=resolve_after_delay)
    t.start()

    result = _simulate_on_conflict(job, CONFLICT_DATA)
    t.join()
    assert result == "copy"


# ── 4. timeout → 預設 "skip" ───────────────────────────────
def test_timeout_defaults_skip():
    """When no one resolves the conflict within timeout, default to 'skip'."""
    job = state.JobState(job_id="c4", task_type="backup", project_name="Test")
    job.global_conflict_action = None

    start = time.time()
    result = _simulate_on_conflict(job, CONFLICT_DATA, timeout=0.3)
    elapsed = time.time() - start

    assert result == "skip"
    assert elapsed < 1.0, f"Timeout took too long: {elapsed:.1f}s"
