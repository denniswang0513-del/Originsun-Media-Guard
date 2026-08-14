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

from core.auth import create_token

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


@pytest.fixture
def admin_token(tmp_settings):
    """Create a valid admin JWT token for authenticated test requests."""
    return create_token({"sub": "admin", "role": "admin", "access_level": 3})


@pytest.fixture
def admin_headers(admin_token):
    """HTTP headers with admin Bearer token."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def app_client():
    """同步 TestClient（守衛/路由測試用）—— 全套件共用一個，app 只啟動一次。"""
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


@pytest.fixture
def user_token():
    """造一張非管理員 token：`user_token(modules=[...])`。

    「lv1 + 指定模組」是模組級權限測試的固定樣板，之前每個檔各抄一份。
    """
    def _make(**claims):
        return create_token({"sub": "u", "username": "u",
                             "access_level": 1, "modules": [], **claims})
    return _make


@pytest.fixture
def as_user(user_token):
    """`as_user(modules=[...])` → 帶該 token 的 HTTP headers。

    刻意**不叫** `user_headers`：`tests/integration/test_paste_upload.py` 已經
    有一個同名的區域 fixture 回傳 dict（不是 callable），同名會讓兩種形狀在
    不同檔案裡互相遮蔽。名字帶動詞也讀得出它要被呼叫。
    """
    return lambda **claims: {"Authorization": f"Bearer {user_token(**claims)}"}


# ── 5. real_server ───────────────────────────────────────────
@pytest.fixture(scope="session")
def real_server():
    """Start a real uvicorn server on port 18000 for E2E / smoke tests."""
    project_root = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["MOCK_FFMPEG"] = "1"
    env["MOCK_NAS"] = "1"
    env["ORIGINSUN_DISABLE_SELFHEAL"] = "1"  # 沒這行 _self_heal_scheduled_task 在 Session 0 會 4s 後 taskkill server

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
    # 🔴 **127.0.0.1 不是 localhost**：uvicorn 綁 0.0.0.0（只有 IPv4），而
    # localhost 在 Windows 先解到 ::1 —— 每一條新連線都先耗 ~2 秒等 IPv6 SYN
    # 逾時才退回 IPv4。整個測試套跑幾十條連線，那是幾十秒純等待。
    base_url = "http://127.0.0.1:18000"
    deadline = time.time() + 15
    ready = False
    # 共用一個 Client：模組層 `httpx.get` 每次重建 Client 要 ~158ms，比這裡
    # 的輪詢間隔還久（等於實際週期是 0.66s 不是 0.5s）。連線被拒是立即返回的，
    # 所以間隔可以縮到 0.1s，開機就緒後少空等最多半秒。
    with httpx.Client(timeout=2.0) as probe:
        while time.time() < deadline:
            # 🔴 先看**我們這支**還活著沒。港口被上一輪殘留的 server 佔住時，
            # 我們的 uvicorn 會 bind 失敗立刻死掉，而下面那個 health 探測會被
            # 那支舊的接走 —— 測試就安安靜靜地跑在**舊程式碼**上（2026-08-14
            # 實際咬到：改了端點卻一直看到舊回應）。
            if proc.poll() is not None:
                pytest.fail(
                    f"測試 server 啟動就死了（rc={proc.returncode}）—— "
                    "port 18000 多半被上一輪的殘留行程佔住了。")
            try:
                if probe.get(f"{base_url}/api/v1/health").status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.1)

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
