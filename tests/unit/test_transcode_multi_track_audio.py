"""Regression: multi-track audio MXF (e.g. multi-mic shoot — each mic on its
own mono stream) lost all audio except stream 0 when transcoded to proxy.

Bug context: ``-map 0:a:0?`` picked only the first audio stream from the
source. Sources with N>1 audio streams (typical pro audio MXF + multi-mic
field recorders) silently lost streams 1..N-1, then concat blew up because
some clips ended up mono and others stereo.

Lock the cmd shape: proxy must always end up with a single stereo audio
stream regardless of source layout (0 / 1 / N audio streams), so downstream
concat sees a uniform shape.
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


def test_multi_stream_audio_uses_amerge(monkeypatch, tmp_path):
    """Source with 4 audio streams (4-mic boom rig) must merge all into
    stereo proxy, not silently drop streams 1-3."""
    cmd = _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams=4)
    cmd_str = " ".join(cmd)
    assert "amerge=inputs=4" in cmd_str, (
        f"Expected amerge=inputs=4 in filter_complex, got cmd: {cmd_str}"
    )
    assert "-map 0:a:0?" not in cmd_str, (
        "Old buggy mapping -map 0:a:0? still present — it drops streams 1+"
    )


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


def test_proxy_audio_always_aac_for_concat_compat(monkeypatch, tmp_path):
    """Proxy audio must be re-encoded to AAC (not copied) so multi-stream
    sources can be merged. -c:a copy fails when amerge is in the chain."""
    cmd = _run_transcode_with_audio_streams(monkeypatch, tmp_path, n_audio_streams=2)
    cmd_str = " ".join(cmd)
    assert "-c:a copy" not in cmd_str, (
        "Multi-stream proxy must re-encode audio (amerge needs decoded streams), "
        f"got: {cmd_str}"
    )
    assert "-c:a aac" in cmd_str, "Expected aac re-encode for downstream compat"
