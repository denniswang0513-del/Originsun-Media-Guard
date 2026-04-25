"""Regression: DJI Mavic 3+ embeds an mjpeg attached_pic stream alongside
the main h264 video. ``-map 0:v`` selected both, prores_ks couldn't tag the
second stream for the mov muxer, so every DJI clip failed transcode.

Lock the cmd shape so a future refactor can't silently drop the ``:0``,
and lock that ffmpeg stderr stays piped (was DEVNULL → undiagnosable).
"""
import subprocess
import pytest

from core_engine import MediaGuardEngine


class _FakeProc:
    """Stand-in for subprocess.Popen so we never actually invoke ffmpeg."""

    def __init__(self):
        self.stdout = iter([])
        self.stderr = iter([])
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


@pytest.fixture
def transcode_call(monkeypatch, tmp_path):
    """Run run_transcode_job once and return a dict with ``cmd`` and ``kwargs``
    captured from the ffmpeg subprocess.Popen invocation.
    """
    captured: dict = {"cmd": None, "kwargs": None}

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(MediaGuardEngine, "_get_video_duration", staticmethod(lambda p: 10.0))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # The stderr-reader thread runs against FakeProc.stderr (empty iter)
    # and exits cleanly — no patching of threading needed.

    src = tmp_path / "DJI_0856.MOV"
    src.write_bytes(b"\x00" * 16)
    dest = tmp_path / "out"
    dest.mkdir()

    engine = MediaGuardEngine(logger_cb=lambda m: None, error_cb=lambda m: None)
    engine.run_transcode_job([str(src)], str(dest))

    assert captured["cmd"], "subprocess.Popen was never called — run_transcode_job didn't reach ffmpeg"
    return captured


def _video_maps(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd)
            if a == "-map" and i + 1 < len(cmd) and cmd[i + 1].startswith("0:v")]


def _audio_maps(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd)
            if a == "-map" and i + 1 < len(cmd) and cmd[i + 1].startswith("0:a")]


def test_transcode_uses_first_video_stream_only(transcode_call):
    """``-map 0:v:0`` must be passed (not ``0:v``) to skip attached_pic."""
    maps = _video_maps(transcode_call["cmd"])
    assert maps == ["0:v:0"], (
        f"Expected video map to be ['0:v:0'], got {maps}. "
        "Bare 0:v sweeps in DJI's attached_pic and breaks prores_ks."
    )


def test_transcode_uses_first_audio_stream_only(transcode_call):
    """``-map 0:a:0?`` defends against multi-track audio sources."""
    maps = _audio_maps(transcode_call["cmd"])
    assert maps == ["0:a:0?"], f"Expected audio map to be ['0:a:0?'], got {maps}."


def test_transcode_captures_stderr(transcode_call):
    """ffmpeg stderr must be PIPE (not DEVNULL) so failures surface the real
    error. Bug context: ``[!] 轉檔失敗`` was the ONLY signal users got — the
    actual ``Could not find tag for codec prores`` line was thrown away.
    """
    assert transcode_call["kwargs"].get("stderr") == subprocess.PIPE
