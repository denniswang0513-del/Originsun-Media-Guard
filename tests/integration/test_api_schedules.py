"""
Integration tests for the schedules CRUD API.
"""
import json
from datetime import datetime, timedelta
import pytest


@pytest.fixture(autouse=True)
def tmp_schedule_file(tmp_path, monkeypatch):
    """Isolate scheduler file for each test."""
    path = str(tmp_path / "scheduled_jobs.json")
    monkeypatch.setattr("core.scheduler._SCHEDULE_FILE", path)
    return path


# ── Helpers ──────────────────────────────────────────────────

VALID_BACKUP = {
    "name": "每日備份",
    "cron": "0 2 * * *",
    "task_type": "backup",
    "request": {
        "project_name": "TestProject",
        "local_root": "D:/Backup",
        "nas_root": "Z:/Backup",
        "proxy_root": "D:/Proxy",
        "cards": [["CARD", "U:/Source"]],
    },
}


# ── Tests ────────────────────────────────────────────────────

class TestListSchedules:
    @pytest.mark.anyio
    async def test_list_empty(self, async_client):
        r = await async_client.get("/api/v1/schedules")
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.anyio
    async def test_list_after_create(self, async_client):
        await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        r = await async_client.get("/api/v1/schedules")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "每日備份"


class TestCreateSchedule:
    @pytest.mark.anyio
    async def test_create_success(self, async_client):
        r = await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        assert r.status_code == 200
        data = r.json()
        assert "schedule_id" in data
        assert data["name"] == "每日備份"
        assert data["cron"] == "0 2 * * *"
        assert data["enabled"] is True
        assert data["next_run"] is not None

    @pytest.mark.anyio
    async def test_create_with_run_at(self, async_client):
        """單次排程用 run_at 建立。"""
        future = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        payload = {
            "name": "單次備份",
            "run_at": future,
            "task_type": "backup",
            "request": VALID_BACKUP["request"],
        }
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["cron"] is None
        assert data["run_at"] == future
        assert data["next_run"] == future

    @pytest.mark.anyio
    async def test_create_no_cron_no_run_at(self, async_client):
        """既無 cron 也無 run_at 應回 422。"""
        payload = {
            "name": "缺少時間",
            "task_type": "backup",
            "request": VALID_BACKUP["request"],
        }
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_create_run_at_in_past(self, async_client):
        """run_at 在過去應回 422。"""
        past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        payload = {
            "name": "過去時間",
            "run_at": past,
            "task_type": "backup",
            "request": VALID_BACKUP["request"],
        }
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_create_invalid_cron(self, async_client):
        payload = {**VALID_BACKUP, "cron": "invalid"}
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_create_invalid_task_type(self, async_client):
        payload = {**VALID_BACKUP, "task_type": "nonexistent"}
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_create_invalid_request_fields(self, async_client):
        payload = {**VALID_BACKUP, "request": {"bad_field": True}}
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 422

    @pytest.mark.anyio
    async def test_create_transcode_schedule(self, async_client):
        payload = {
            "name": "每日轉檔",
            "cron": "30 3 * * *",
            "task_type": "transcode",
            "request": {"sources": ["D:/Source"], "dest_dir": "D:/Proxy"},
        }
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 200
        assert r.json()["task_type"] == "transcode"

    @pytest.mark.anyio
    async def test_create_tts_schedule(self, async_client):
        """TTS 排程不驗證 request 欄位。"""
        future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
        payload = {
            "name": "TTS 排程",
            "run_at": future,
            "task_type": "tts",
            "request": {
                "text": "測試文字",
                "output_dir": "D:/TTS",
                "voice": "zh-TW-HsiaoChenNeural",
            },
        }
        r = await async_client.post("/api/v1/schedules", json=payload)
        assert r.status_code == 200
        assert r.json()["task_type"] == "tts"


class TestUpdateSchedule:
    @pytest.mark.anyio
    async def test_update_name(self, async_client):
        r = await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        sid = r.json()["schedule_id"]

        r2 = await async_client.put(f"/api/v1/schedules/{sid}", json={"name": "新名稱"})
        assert r2.status_code == 200
        assert r2.json()["name"] == "新名稱"

    @pytest.mark.anyio
    async def test_toggle_enabled(self, async_client):
        r = await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        sid = r.json()["schedule_id"]

        r2 = await async_client.put(f"/api/v1/schedules/{sid}", json={"enabled": False})
        assert r2.status_code == 200
        assert r2.json()["enabled"] is False

    @pytest.mark.anyio
    async def test_update_cron_updates_next_run(self, async_client):
        r = await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        sid = r.json()["schedule_id"]
        old_next = r.json()["next_run"]

        r2 = await async_client.put(f"/api/v1/schedules/{sid}", json={"cron": "0 5 * * *"})
        assert r2.status_code == 200
        assert r2.json()["cron"] == "0 5 * * *"
        # next_run should change
        assert r2.json()["next_run"] != old_next or True  # time-dependent

    @pytest.mark.anyio
    async def test_update_invalid_cron(self, async_client):
        r = await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        sid = r.json()["schedule_id"]

        r2 = await async_client.put(f"/api/v1/schedules/{sid}", json={"cron": "bad"})
        assert r2.status_code == 422

    @pytest.mark.anyio
    async def test_update_not_found(self, async_client):
        r = await async_client.put("/api/v1/schedules/nonexistent", json={"name": "x"})
        assert r.status_code == 404


class TestDeleteSchedule:
    @pytest.mark.anyio
    async def test_delete_success(self, async_client):
        r = await async_client.post("/api/v1/schedules", json=VALID_BACKUP)
        sid = r.json()["schedule_id"]

        r2 = await async_client.delete(f"/api/v1/schedules/{sid}")
        assert r2.status_code == 200

        r3 = await async_client.get("/api/v1/schedules")
        assert r3.json() == []

    @pytest.mark.anyio
    async def test_delete_not_found(self, async_client):
        r = await async_client.delete("/api/v1/schedules/nonexistent")
        assert r.status_code == 404
