"""
Layer 1 — core_engine.py 純邏輯測試。

MediaGuardEngine.__init__ 接受 logger_cb 和 error_cb（皆 optional）。
get_xxh64 是 @staticmethod，回傳 16 字元 hex 字串。
_pause_event: is_set()==True 代表暫停中，clear() 代表恢復。
_stop_event: is_set()==True 代表已停止。
"""
import os
import pytest
from core_engine import MediaGuardEngine


@pytest.fixture
def engine():
    """建立一個乾淨的 MediaGuardEngine 實例。"""
    return MediaGuardEngine(logger_cb=lambda m: None, error_cb=lambda m: None)


# ── 1. xxh64 一致性 ─────────────────────────────────────────
def test_xxh64_consistency(tmp_path):
    """同一個檔案跑兩次結果相同。"""
    f = tmp_path / "test.bin"
    f.write_bytes(os.urandom(1024 * 1024))  # 1MB random
    h1 = MediaGuardEngine.get_xxh64(str(f))
    h2 = MediaGuardEngine.get_xxh64(str(f))
    assert h1 == h2


# ── 2. 不同檔案 hash 不同 ───────────────────────────────────
def test_xxh64_different_files(tmp_path):
    f1 = tmp_path / "a.bin"
    f2 = tmp_path / "b.bin"
    f1.write_bytes(b"\x00" * 1024)
    f2.write_bytes(b"\xff" * 1024)
    assert MediaGuardEngine.get_xxh64(str(f1)) != MediaGuardEngine.get_xxh64(str(f2))


# ── 3. 回傳格式：16 字元 hex ────────────────────────────────
def test_xxh64_result_format(tmp_path):
    f = tmp_path / "fmt.bin"
    f.write_bytes(b"hello originsun")
    result = MediaGuardEngine.get_xxh64(str(f))
    assert len(result) == 16, f"Expected 16 chars, got {len(result)}: {result}"
    assert all(c in "0123456789abcdef" for c in result), f"Non-hex chars in: {result}"


# ── 4. request_stop 設定 _stop_event ────────────────────────
def test_stop_event(engine):
    assert not engine._stop_event.is_set()
    engine.request_stop()
    assert engine._stop_event.is_set()


# ── 5. pause / resume 切換 ──────────────────────────────────
def test_pause_resume(engine):
    """pause 後 _pause_event.is_set()==True，resume 後 is_set()==False。"""
    assert not engine._pause_event.is_set(), "Initial state should be not paused"
    engine.request_pause()
    assert engine._pause_event.is_set(), "After pause, event should be set"
    engine.request_resume()
    assert not engine._pause_event.is_set(), "After resume, event should be cleared"


# ── 6. stop 重置（run_*_job 開頭會 clear _stop_event）──────
def test_stop_reset(engine):
    """run_backup_job 等方法開頭會 clear _stop_event，
    但因為需要 ffmpeg/真實路徑，此處只確認 _stop_event 可手動 clear。"""
    engine.request_stop()
    assert engine._stop_event.is_set()
    engine._stop_event.clear()
    assert not engine._stop_event.is_set()
