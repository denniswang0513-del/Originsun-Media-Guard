"""
Layer 2 — 系統 API 整合測試。
使用 async_client fixture（httpx.AsyncClient + ASGITransport）。
"""
import re
import pytest


# ── 1. health 回 200 ────────────────────────────────────────
async def test_health_returns_200(async_client):
    r = await async_client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert data["status"] == "ok"


# ── 2. version 格式 x.y.z ──────────────────────────────────
async def test_version_format(async_client):
    r = await async_client.get("/api/v1/version")
    assert r.status_code == 200
    data = r.json()
    version = data.get("version", "")
    assert re.match(r"^\d+\.\d+\.\d+", version), f"Unexpected version format: {version}"


# ── 3. status 結構 ─────────────────────────────────────────
async def test_status_structure(async_client):
    r = await async_client.get("/api/v1/status")
    assert r.status_code == 200
    data = r.json()
    print("status keys:", list(data.keys()))
    # At minimum, should have some status info
    assert isinstance(data, dict)


# ── 4. settings roundtrip ──────────────────────────────────
async def test_settings_roundtrip(async_client):
    # Load
    r = await async_client.get("/api/settings/load")
    assert r.status_code == 200
    settings = r.json()

    # Add marker
    settings["__test_marker__"] = "hello_test"
    r2 = await async_client.post(
        "/api/settings/save",
        json=settings,
    )
    assert r2.status_code == 200

    # Reload and verify
    r3 = await async_client.get("/api/settings/load")
    reloaded = r3.json()
    assert reloaded.get("__test_marker__") == "hello_test"


# ── 5. list_dir 回傳影片檔 ─────────────────────────────────
async def test_list_dir_videos(async_client, tmp_path):
    # Create 3 mp4 + 1 txt
    for name in ["a.mp4", "b.mp4", "c.mp4"]:
        (tmp_path / name).write_bytes(b"\x00" * 16)
    (tmp_path / "readme.txt").write_text("hello")

    r = await async_client.post(
        "/api/v1/list_dir",
        json={"path": str(tmp_path)},
    )
    assert r.status_code == 200
    data = r.json()
    print("list_dir response:", data)
    # data could be a list or a dict with files key
    files = data if isinstance(data, list) else data.get("files", data.get("items", []))
    assert len(files) == 3


# ── 6. list_dir 壞路徑不 crash ─────────────────────────────
async def test_list_dir_bad_path(async_client):
    r = await async_client.post(
        "/api/v1/list_dir",
        json={"path": "Z:/nonexistent/path/12345"},
    )
    assert r.status_code != 500, f"Server error on bad path: {r.text}"


# ── 7. stop 空閒時不 crash ─────────────────────────────────
async def test_stop_when_idle(async_client):
    r = await async_client.post("/api/v1/control/stop")
    assert r.status_code in range(200, 300), f"Stop failed: {r.status_code} {r.text}"
