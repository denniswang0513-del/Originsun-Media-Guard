"""
Shared fixtures for the Originsun Media Guard Pro test suite.
"""
import asyncio
import os
import sys
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import httpx

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 1. tmp_settings ─────────────────────────────────────────
@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Create a temporary settings.json and patch config._SETTINGS_FILE."""
    settings = {
        "notifications": {
            "line_notify_token": "",
            "google_chat_webhook": "",
            "custom_webhook_url": "",
        },
        "message_templates": {
            "backup_success": "test",
            "report_success": "test",
            "transcode_success": "test",
            "concat_success": "test",
            "verify_success": "test",
        },
        "notification_channels": {
            "backup": {"gchat": False, "line": False},
            "report": {"gchat": False, "line": False},
            "transcode": {"gchat": False, "line": False},
            "concat": {"gchat": False, "line": False},
            "verify": {"gchat": False, "line": False},
        },
        "agents": [],
        "compute_hosts": [],
        "concurrency": {
            "backup": 1,
            "transcode": 2,
            "concat": 2,
            "verify": 2,
            "transcribe": 1,
            "report": 1,
        },
    }
    settings_path = str(tmp_path / "settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)

    monkeypatch.setattr("config._SETTINGS_FILE", settings_path)
    return settings_path


# ── 2. tmp_dict ──────────────────────────────────────────────
@pytest.fixture
def tmp_dict(tmp_path):
    """Create a temporary taiwan_dict.json with sample data."""
    d = {
        "vocab_mapping": {
            "視頻": "影片",
            "軟件": "軟體",
            "手機APP": "手機 App",
        },
        "pronunciation_hacks": {
            "垃圾": "勒色",
            "說服": "ㄕㄨㄟˋ服",
        },
    }
    dict_path = tmp_path / "taiwan_dict.json"
    with open(dict_path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=4)
    return dict_path


# ── 3. mock_engine ───────────────────────────────────────────
@pytest.fixture
def mock_engine(mocker):
    """Replace the engine factory with a MagicMock-based engine."""
    mock = MagicMock()
    mock.run_backup_job.return_value = None
    mock.run_transcode_job.return_value = None
    mock.run_concat_job.return_value = None
    mock.run_verify_job.return_value = None
    mock.get_xxh64.return_value = "abcd1234abcd1234"
    mock.request_stop.return_value = None
    mock.request_pause.return_value = None
    mock.request_resume.return_value = None
    mocker.patch("core.engine_inst.create_engine", return_value=mock)
    mocker.patch("core.worker.create_engine", return_value=mock)
    return mock


# ── 4. async_client ──────────────────────────────────────────
@pytest_asyncio.fixture
async def async_client(mock_engine, tmp_settings):
    """httpx.AsyncClient wrapping main.io_app for integration tests."""
    import core.state as _state
    # Ensure state._main_loop points to the current test's event loop
    _state.set_main_loop(asyncio.get_running_loop())
    _state.init_concurrency()
    # Clear stale jobs from any prior test
    for jid in list(_state._jobs.keys()):
        _state._jobs.pop(jid, None)
    with _state._active_lock:
        for k in _state._active_counts:
            _state._active_counts[k] = 0
    from main import io_app
    transport = httpx.ASGITransport(app=io_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── 5. real_server ───────────────────────────────────────────
@pytest.fixture(scope="session")
def real_server():
    """Start a real uvicorn server on port 18000 for E2E / smoke tests."""
    project_root = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["MOCK_FFMPEG"] = "1"
    env["MOCK_NAS"] = "1"

    # Use DEVNULL to prevent pipe buffer deadlock (64KB buffer on Windows
    # fills up when server writes logs, blocking the event loop)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "main:io_app",
            "--host", "0.0.0.0",
            "--port", "18000",
        ],
        cwd=project_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to be ready (max 15 seconds)
    base_url = "http://localhost:18000"
    deadline = time.time() + 15
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not ready:
        proc.terminate()
        proc.wait(timeout=5)
        pytest.fail("real_server failed to start within 15 seconds")

    yield {"base_url": base_url, "_proc": proc}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
