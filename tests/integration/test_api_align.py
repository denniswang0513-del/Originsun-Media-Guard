"""
Integration tests for /api/v1/jobs/align endpoint.

Coverage:
    - 400 when tasks list is empty
    - 400 when a task has missing source/transcript
    - 200 + queued status when payload is valid
    - Job appears in the queue with the expected request type
"""
import pytest


async def test_align_rejects_empty_tasks(async_client):
    payload = {"tasks": [], "dest_dir": "/tmp/x"}
    r = await async_client.post("/api/v1/jobs/align", json=payload)
    assert r.status_code == 400
    assert "tasks" in (r.json().get("detail") or "")


async def test_align_rejects_missing_source(async_client):
    payload = {
        "tasks": [{"source": "", "transcript": "abc"}],
        "dest_dir": "/tmp/x",
    }
    r = await async_client.post("/api/v1/jobs/align", json=payload)
    assert r.status_code == 400


async def test_align_rejects_empty_transcript(async_client):
    payload = {
        "tasks": [{"source": "C:/v.mp4", "transcript": "   "}],
        "dest_dir": "/tmp/x",
    }
    r = await async_client.post("/api/v1/jobs/align", json=payload)
    assert r.status_code == 400


async def test_align_accepts_valid_payload_and_queues(async_client):
    payload = {
        "tasks": [
            {"source": "C:/test.mov", "transcript": "段一\n\n段二", "transcript_format": "txt"},
        ],
        "dest_dir": "C:/output",
        "model_size": "turbo",
        "language": "zh",
    }
    r = await async_client.post("/api/v1/jobs/align", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["mode"] == "align"
    assert "job_id" in body and len(body["job_id"]) >= 4


async def test_align_picks_up_default_anchor_threshold(async_client):
    """Verify that omitting optional fields uses the schema default 0.4."""
    from core.schemas import AlignRequest, AlignTask
    req = AlignRequest(
        tasks=[AlignTask(source="C:/v.mov", transcript="x")],
        dest_dir="C:/out",
    )
    assert req.anchor_threshold == 0.4
    assert req.subtitle_polish is True
    assert req.min_duration == 1.0
    assert req.min_gap_frames == 2
    assert req.encoding_bom is True
    assert req.fps_override is None
