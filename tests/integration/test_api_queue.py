"""
Layer 2 — 專案總覽 API 測試（佇列 + 機器狀態）。
"""
import asyncio
import time
import json
import pytest
import core.state as state

MINIMAL_BACKUP = {
    "task_type": "backup",
    "project_name": "QueueTest",
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
def _clean_jobs():
    """Clean up job registry between tests."""
    for jid in list(state._jobs.keys()):
        state._jobs.pop(jid, None)
    with state._active_lock:
        for k in state._active_counts:
            state._active_counts[k] = 0
    yield
    for jid in list(state._jobs.keys()):
        state._jobs.pop(jid, None)
    with state._active_lock:
        for k in state._active_counts:
            state._active_counts[k] = 0


# ── 1. 空佇列 ───────────────────────────────────────────────
async def test_queue_empty(async_client):
    """GET /api/v1/queue with no jobs → empty list."""
    r = await async_client.get("/api/v1/queue")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 0


# ── 2. 佇列回傳 jobs ────────────────────────────────────────
async def test_queue_returns_queued_jobs(async_client, mock_engine):
    """Submit 2 backup jobs → GET queue → at least 1 job present (running or queued)."""
    # Make mock engine slow so jobs stay visible
    def slow_backup(*args, **kwargs):
        time.sleep(2.0)

    mock_engine.run_backup_job.side_effect = slow_backup

    r1 = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "Q_A"},
    )
    assert r1.status_code == 200

    r2 = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "Q_B"},
    )
    assert r2.status_code == 200

    await asyncio.sleep(0.3)

    r = await async_client.get("/api/v1/queue")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1, f"Expected at least 1 job in queue, got {data}"

    # Wait for jobs to finish
    await asyncio.sleep(3.0)


# ── 3. 排序佇列 ─────────────────────────────────────────────
async def test_reorder_queue(async_client, mock_engine):
    """Submit 3 jobs → reorder → verify new order."""
    def slow_backup(*args, **kwargs):
        time.sleep(3.0)

    mock_engine.run_backup_job.side_effect = slow_backup
    state.CONCURRENCY_LIMITS["backup"] = 1

    ids = []
    for name in ["R_A", "R_B", "R_C"]:
        r = await async_client.post(
            "/api/v1/jobs",
            json={**MINIMAL_BACKUP, "project_name": name},
        )
        assert r.status_code == 200
        ids.append(r.json()["job_id"])

    await asyncio.sleep(0.3)

    # Reorder: put third first
    r = await async_client.post(
        "/api/v1/queue/reorder",
        json={"ordered_job_ids": [ids[2], ids[0], ids[1]]},
    )
    assert r.status_code == 200

    # Verify queue
    q = await async_client.get("/api/v1/queue")
    queue_data = q.json()
    queued_ids = [j["job_id"] for j in queue_data if j["status"] in ("queued", "waiting")]

    # The reorder should have changed priorities
    if len(queued_ids) >= 2:
        assert queued_ids[0] == ids[2], f"Expected {ids[2]} first, got {queued_ids}"

    await asyncio.sleep(4.0)


# ── 4. 排序無效 ID ──────────────────────────────────────────
async def test_reorder_invalid_ids(async_client):
    """POST reorder with non-existent IDs → no crash."""
    r = await async_client.post(
        "/api/v1/queue/reorder",
        json={"ordered_job_ids": ["nonexistent_1", "nonexistent_2"]},
    )
    assert r.status_code != 500, f"Server error: {r.text}"


# ── 5. 標記緊急 ─────────────────────────────────────────────
async def test_set_urgent(async_client, mock_engine):
    """Mark third job urgent → it should move to front."""
    def slow_backup(*args, **kwargs):
        time.sleep(3.0)

    mock_engine.run_backup_job.side_effect = slow_backup
    state.CONCURRENCY_LIMITS["backup"] = 1

    ids = []
    for name in ["U_A", "U_B", "U_C"]:
        r = await async_client.post(
            "/api/v1/jobs",
            json={**MINIMAL_BACKUP, "project_name": name},
        )
        assert r.status_code == 200
        ids.append(r.json()["job_id"])

    await asyncio.sleep(0.3)

    # Mark third job as urgent
    r = await async_client.post(f"/api/v1/queue/{ids[2]}/urgent")
    assert r.status_code == 200

    q = await async_client.get("/api/v1/queue")
    queue_data = q.json()
    queued = [j for j in queue_data if j["status"] in ("queued", "waiting")]

    if queued:
        # The urgent job should be first among queued
        assert queued[0]["job_id"] == ids[2], f"Expected {ids[2]} first, got {queued}"
        assert queued[0]["urgent"] is True

    await asyncio.sleep(4.0)


# ── 6. 取消緊急 ─────────────────────────────────────────────
async def test_unset_urgent(async_client, mock_engine):
    """Mark urgent then unmark → urgent becomes False."""
    def slow_backup(*args, **kwargs):
        time.sleep(2.0)

    mock_engine.run_backup_job.side_effect = slow_backup
    state.CONCURRENCY_LIMITS["backup"] = 1

    r = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "UU_A"},
    )
    job_id = r.json()["job_id"]

    r2 = await async_client.post(
        "/api/v1/jobs",
        json={**MINIMAL_BACKUP, "project_name": "UU_B"},
    )
    job_id_b = r2.json()["job_id"]

    await asyncio.sleep(0.3)

    # Mark then unmark
    await async_client.post(f"/api/v1/queue/{job_id_b}/urgent")
    r_unmark = await async_client.delete(f"/api/v1/queue/{job_id_b}/urgent")
    assert r_unmark.status_code == 200

    q = await async_client.get("/api/v1/queue")
    for j in q.json():
        if j["job_id"] == job_id_b:
            assert j["urgent"] is False

    await asyncio.sleep(3.0)


# ── 7. Health 結構 ───────────────────────────────────────────
async def test_health_structure(async_client):
    """GET /api/v1/health → verify all expected keys."""
    r = await async_client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    expected_keys = [
        "status", "hostname", "cpu_percent", "memory_percent",
        "worker_busy", "active_job_count", "current_tasks", "version"
    ]
    for key in expected_keys:
        assert key in data, f"Missing key '{key}' in health response. Got: {list(data.keys())}"


# ── 8. Agents settings roundtrip ─────────────────────────────
async def test_agents_settings_roundtrip(async_client):
    """Load settings → add agent → save → load → agent persists."""
    # Load current settings
    r = await async_client.get("/api/settings/load")
    assert r.status_code == 200
    settings = r.json()

    # Add an agent
    agents = settings.get("agents", [])
    new_agent = {"id": "test_agent_1", "name": "TestPC", "url": "http://192.168.1.200:8000"}
    agents.append(new_agent)
    settings["agents"] = agents

    # Save
    r2 = await async_client.post("/api/settings/save", json=settings)
    assert r2.status_code == 200

    # Reload and verify
    r3 = await async_client.get("/api/settings/load")
    reloaded = r3.json()
    saved_agents = reloaded.get("agents", [])

    assert any(a.get("id") == "test_agent_1" for a in saved_agents), (
        f"Agent not found in reloaded settings. agents={saved_agents}"
    )

    # Verify other settings are preserved
    assert "notifications" in reloaded
    assert "concurrency" in reloaded

    # Clean up: remove test agent
    settings["agents"] = [a for a in saved_agents if a.get("id") != "test_agent_1"]
    await async_client.post("/api/settings/save", json=settings)
