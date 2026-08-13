# -*- coding: utf-8 -*-
"""產出檔的原子落地：失敗絕不能動到上一份好檔。

2026-08-13 事故修的是轉檔（半截檔頂著正式檔名）。串帶其實更嚴重 —— 舊做法是
**先 os.remove 掉上一份好的 reel**、再讓 ffmpeg 寫最終檔名，中途失敗（NAS 斷線、
機器掛掉、使用者中止）就連舊的都沒了，而 reel 是要交給客戶的東西。

這裡釘住兩層：AtomicOutput 本身的約定，以及 run_concat_job 真的走了這條路。
"""
import json
import os
import subprocess

import pytest

from core_engine import AtomicOutput, MediaGuardEngine, is_incomplete_output, part_path_for


def _touch(path, data=b"OLD-GOOD-REEL"):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


class TestAtomicOutputContract:
    def test_commit_replaces_final(self, tmp_path):
        final = str(tmp_path / "Reel.mov")
        _touch(final)
        with AtomicOutput(final) as out:
            _touch(out.path, b"NEW")
            out.commit()
        assert open(final, "rb").read() == b"NEW"
        assert not os.path.exists(out.path)

    def test_no_commit_leaves_final_untouched(self, tmp_path):
        """🔴 這條就是串帶的核心保證：失敗了，舊的還在。"""
        final = str(tmp_path / "Reel.mov")
        _touch(final)
        with AtomicOutput(final) as out:
            _touch(out.path, b"HALF")
        assert open(final, "rb").read() == b"OLD-GOOD-REEL"
        assert not os.path.exists(out.path)

    def test_exception_discards_partial(self, tmp_path):
        final = str(tmp_path / "Reel.mov")
        _touch(final)
        with pytest.raises(RuntimeError):
            with AtomicOutput(final) as out:
                _touch(out.path, b"HALF")
                raise RuntimeError("NAS 斷線")
        assert open(final, "rb").read() == b"OLD-GOOD-REEL"
        assert not os.path.exists(out.path)

    def test_enter_clears_previous_leftover(self, tmp_path):
        """上一輪留下的暫存檔不能被當成這一輪的內容接著寫。"""
        final = str(tmp_path / "Reel.mov")
        stale = _touch(part_path_for(final), b"STALE")
        with AtomicOutput(final) as out:
            assert not os.path.exists(stale)

    def test_failed_commit_keeps_the_good_temp(self, tmp_path, monkeypatch):
        """成品是好的、只差改名（目的檔被播放器鎖住）→ 暫存檔不准刪。"""
        final = str(tmp_path / "Reel.mov")

        def _boom(src, dst):
            raise OSError("被占用")

        monkeypatch.setattr(os, "replace", _boom)
        with AtomicOutput(final) as out:
            _touch(out.path, b"NEW-COMPLETE")
            with pytest.raises(OSError):
                out.commit()
        assert open(out.path, "rb").read() == b"NEW-COMPLETE"

    def test_reset_drops_partial_for_retry(self, tmp_path):
        """NVENC 失敗改軟編重試：暫存檔要先歸零，不能接著往下寫。"""
        final = str(tmp_path / "Reel.mov")
        with AtomicOutput(final) as out:
            _touch(out.path, b"NVENC-HALF")
            out.reset()
            assert not os.path.exists(out.path)
            _touch(out.path, b"X264")
            out.commit()
        assert open(final, "rb").read() == b"X264"

    def test_temp_is_recognised_as_incomplete(self, tmp_path):
        """暫存檔留在共享夾時，驗收／補轉端必須認得它不算數。"""
        final = str(tmp_path / "Reel.mp4")
        with AtomicOutput(final) as out:
            _touch(out.path, b"HALF")
            assert is_incomplete_output(out.path) is True


# ── run_concat_job 真的走 AtomicOutput 嗎 ─────────────────────────────

class _FakeProc:
    """假的 ffmpeg：可設定 returncode，成功時真的把輸出檔寫出來。"""

    def __init__(self, cmd, returncode=0, write=True):
        self.returncode = returncode
        self.stdout = iter([])
        if returncode == 0 and write:
            _touch(cmd[-1], b"NEW-REEL")

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        pass


def _fake_probe(cmd, *a, **kw):
    """ffprobe：一律回報「有影片流、有音訊」。"""
    joined = " ".join(str(c) for c in cmd)

    class _R:
        returncode = 0
        stdout = ("audio\n" if "csv=p=0" in joined
                  else json.dumps({"streams": [{"codec_name": "h264",
                                                "codec_type": "video"}]}))
        stderr = ""

    return _R()


def _run_concat(monkeypatch, tmp_path, ffmpeg_rc):
    captured = {"outputs": []}

    def fake_popen(cmd, *a, **kw):
        captured["outputs"].append(cmd[-1])
        return _FakeProc(list(cmd), returncode=ffmpeg_rc)

    monkeypatch.setattr(MediaGuardEngine, "_get_video_duration", staticmethod(lambda p: 10.0))
    monkeypatch.setattr(subprocess, "run", _fake_probe)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for name in ("A001.mov", "A002.mov"):
        _touch(src_dir / name, b"\x00" * 32)

    dest = tmp_path / "dest"
    dest.mkdir()
    reel = _touch(dest / "MyReel.mov")   # 上一份好的成品

    engine = MediaGuardEngine(logger_cb=lambda m: None, error_cb=lambda m: None)
    engine.run_concat_job(
        sources=[str(src_dir)], dest_dir=str(dest),
        custom_name="MyReel", codec="ProRes",
        burn_timecode=False, burn_filename=False,
    )
    return reel, captured


def test_concat_failure_keeps_previous_reel(monkeypatch, tmp_path):
    """🔴 ffmpeg 失敗 → 上一份 reel 必須原封不動，且不留半截檔。"""
    reel, captured = _run_concat(monkeypatch, tmp_path, ffmpeg_rc=1)

    assert open(reel, "rb").read() == b"OLD-GOOD-REEL", "舊 reel 被動到了"
    assert not os.path.exists(part_path_for(reel)), "失敗的半截檔沒清掉"
    # ffmpeg 寫的是暫存檔，不是最終檔名
    assert captured["outputs"] and all(is_incomplete_output(o) for o in captured["outputs"])


def test_concat_success_replaces_reel(monkeypatch, tmp_path):
    reel, _ = _run_concat(monkeypatch, tmp_path, ffmpeg_rc=0)

    assert open(reel, "rb").read() == b"NEW-REEL"
    assert not os.path.exists(part_path_for(reel))
