"""Regression: multi-track audio (multi-mic shoot — each mic on its own stream)
must be PRESERVED in the proxy, not collapsed.

History:
  1. Original bug: ``-map 0:a:0?`` mapped only stream 0 → dropped streams 1+.
  2. Interim: amerge'd all streams into ONE stereo track → still lost the
     separate tracks (a 4-track source became 1 merged track — colleague report).
  3. Now (correct): ``-map 0:a`` maps EVERY audio stream, each re-encoded to AAC,
     so a 4-track source yields a 4-track proxy. Editors need every mic; the
     reel/concat is unaffected (it reads the original sources, not the proxies).

Cmd shape per source audio layout:
  - 0 streams → no audio args (silent proxy)
  - 1 stream  → normalize to stereo (fixes mono / left-only sources)
  - N>1       → -map 0:a (preserve ALL tracks), each re-encoded to AAC
"""
import subprocess
import pytest

from core_engine import MediaGuardEngine


class _FakeProc:
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


def _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams):
    """Helper: invoke run_transcode_job with a mocked ffprobe that reports
    n_audio_streams audio streams, capture the ffmpeg cmd."""
    captured = {"cmd": None}

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(MediaGuardEngine, "_get_video_duration", staticmethod(lambda p: 10.0))
    monkeypatch.setattr(
        MediaGuardEngine,
        "_probe_audio_stream_count",
        staticmethod(lambda p: n_audio_streams),
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    src = tmp_path / "multi_mic.mxf"
    src.write_bytes(b"\x00" * 16)
    dest = tmp_path / "out"
    dest.mkdir()

    engine = MediaGuardEngine(logger_cb=lambda m: None, error_cb=lambda m: None)
    engine.run_transcode_job([str(src)], str(dest))
    assert captured["cmd"], "ffmpeg subprocess.Popen never called"
    return captured["cmd"]


def test_multi_stream_audio_preserved(monkeypatch, tmp_path):
    """Source with 4 audio streams (4-mic boom rig / field recorder) must KEEP
    all 4 tracks in the proxy (editors need every mic) — map every stream via
    `-map 0:a` and re-encode each to AAC. Must NOT amerge/merge into one track
    and must NOT drop streams 1+ via the old `-map 0:a:0?`."""
    cmd = _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams=4)
    cmd_str = " ".join(cmd)
    assert "0:a" in cmd, (
        f"Expected -map 0:a (preserve ALL audio streams), got cmd: {cmd_str}"
    )
    assert "amerge" not in cmd_str, (
        f"Proxy must NOT merge multi-track audio into one track, got: {cmd_str}"
    )
    assert "0:a:0?" not in cmd and "0:a:0" not in cmd, (
        "Old buggy single-stream mapping (drops streams 1+) must be gone"
    )
    assert "aac" in cmd, "every track must be re-encoded to AAC"


def test_single_stream_audio_normalized_to_stereo(monkeypatch, tmp_path):
    """Source with 1 audio stream (most common) — must still be normalized
    to stereo so downstream concat doesn't choke on mono+stereo mix."""
    cmd = _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams=1)
    cmd_str = " ".join(cmd)
    assert "channel_layouts=stereo" in cmd_str, (
        f"Single-stream proxy must enforce stereo channel layout, got: {cmd_str}"
    )


def test_zero_audio_source_no_audio_codec(monkeypatch, tmp_path):
    """Source with no audio (silent action cam etc.) — proxy must not have
    audio args at all (ffmpeg would otherwise fail with 'no such stream')."""
    cmd = _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams=0)
    cmd_str = " ".join(cmd)
    # No audio map should be present
    audio_maps = [cmd[i + 1] for i, a in enumerate(cmd)
                  if a == "-map" and i + 1 < len(cmd) and cmd[i + 1].startswith("0:a")]
    audio_filter_maps = [cmd[i + 1] for i, a in enumerate(cmd)
                         if a == "-map" and i + 1 < len(cmd) and cmd[i + 1].startswith("[a")]
    assert not audio_maps and not audio_filter_maps, (
        f"Silent source must produce no audio maps, got audio_maps={audio_maps} "
        f"audio_filter_maps={audio_filter_maps}"
    )
    # Must not contain audio codec args either (no -c:a aac)
    assert "-c:a" not in cmd, "Silent source must not specify audio codec"


def test_proxy_audio_always_aac(monkeypatch, tmp_path):
    """Proxy audio must be re-encoded to AAC (uniform codec across proxies),
    never copied."""
    cmd = _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams=2)
    cmd_str = " ".join(cmd)
    assert "-c:a copy" not in cmd_str, (
        f"Proxy must re-encode audio, not copy, got: {cmd_str}"
    )
    assert "-c:a aac" in cmd_str, "Expected aac re-encode"
    # 2-track source must keep BOTH tracks (preserve, don't merge)
    assert "0:a" in cmd and "amerge" not in cmd_str, (
        f"2-track source must preserve both tracks (-map 0:a, no amerge), got: {cmd_str}"
    )


@pytest.mark.parametrize("filename", [
    "clip.mp4",                 # OBS / 螢幕錄影 / 雙麥 hot-shoe — 可能多軌
    "field_recording.mxf",      # 專業外景 — 多麥多軌典型
    "drone.mov",                # action cam 系列
    "broadcast.mts",            # AVCHD 多語系廣播
    "raw.r3d",                  # RED RAW
])
def test_audio_handling_independent_of_container(filename, monkeypatch, tmp_path):
    """Bug surfaced on .mp4 too — the fix must not be ext-gated. ffprobe
    reads stream count from the actual file, not the ext, so the cmd shape
    must be identical across all supported containers when stream count is
    the same."""
    captured = {"cmd": None}

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(MediaGuardEngine, "_get_video_duration", staticmethod(lambda p: 10.0))
    monkeypatch.setattr(MediaGuardEngine, "_probe_audio_stream_count", staticmethod(lambda p: 3))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    src = tmp_path / filename
    src.write_bytes(b"\x00" * 16)
    dest = tmp_path / "out"
    dest.mkdir()

    engine = MediaGuardEngine(logger_cb=lambda m: None, error_cb=lambda m: None)
    engine.run_transcode_job([str(src)], str(dest))

    cmd_str = " ".join(captured["cmd"])
    assert "0:a" in captured["cmd"] and "amerge" not in cmd_str, (
        f"{filename} with 3 audio streams must preserve ALL tracks (-map 0:a, "
        f"no amerge) — fix must not ext-gate. Got: {cmd_str}"
    )
