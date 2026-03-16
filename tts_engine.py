"""
TTS Engine — Originsun Media Guard Pro
Supports:
  1. Edge-TTS  (standard, free, Taiwan accent)
  2. F5-TTS   (voice cloning, local)
  3. GPT-SoVITS (voice cloning, local) — planned

NOTE: torchaudio backend patch below is REQUIRED before any torch import.
      torchcodec backend crashes on Windows with os.add_dll_directory('.')
      See CLAUDE.md §5.2 for details.
"""

# ─── torchcodec + torchaudio patch (Windows) ─────────────────────────────────
# torchcodec crashes on Windows: os.add_dll_directory('.') → [WinError 87]
# torchaudio 2.10+ uses torchcodec by default for load().
# Fix both by: (1) patching os.add_dll_directory to skip invalid paths,
#              (2) patching torchaudio.load to use soundfile instead.
# See CLAUDE.md §5.2 for background.
import os as _os
_orig_add_dll_directory = _os.add_dll_directory

def _safe_add_dll_directory(path):
    """Skip invalid DLL directories (e.g. '.') that crash on Windows."""
    try:
        return _orig_add_dll_directory(path)
    except OSError:
        import contextlib
        @contextlib.contextmanager
        def _noop_cm():
            yield None
        return _noop_cm()

_os.add_dll_directory = _safe_add_dll_directory

import torchaudio as _ta
import soundfile as _sf
import torch as _torch

_orig_ta_load = _ta.load

def _patched_ta_load(uri, frame_offset=0, num_frames=-1, normalize=True,
                     channels_first=True, format=None, buffer_size=4096, backend=None):
    data, sr = _sf.read(str(uri), dtype="float32",
                        start=frame_offset,
                        stop=frame_offset + num_frames if num_frames > 0 else None)
    t = _torch.from_numpy(data)
    if t.ndim == 1:
        t = t.unsqueeze(0)
    elif channels_first:
        t = t.T
    return t, sr

_ta.load = _patched_ta_load

import os
import asyncio


# ─── Edge TTS ────────────────────────────────────────────────────────────────
async def run_edge_tts(text: str, voice: str, rate: int, pitch: int, output_path: str) -> str:
    """Generate speech using Microsoft Edge TTS via CLI."""
    import tempfile
    import subprocess
    import sys

    # Write text to a temporary UTF-8 file (handles special characters gracefully)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
        tf.write(text)
        text_file_path = tf.name

    rate_str = f"{'+' if rate > 0 else ''}{rate}%"
    pitch_str = f"{'+' if pitch > 0 else ''}{pitch}Hz"

    cmd = [
        sys.executable, "-m", "edge_tts",
        "--file", text_file_path,
        "--voice", voice,
        "--write-media", output_path
    ]
    if rate != 0:
        cmd.extend(["--rate", rate_str])
    if pitch != 0:
        cmd.extend(["--pitch", pitch_str])

    loop = asyncio.get_event_loop()
    def _run_subprocess():
        import os
        env = os.environ.copy()
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)

        # Cleanup temp file
        if os.path.exists(text_file_path):
            os.remove(text_file_path)

        if result.returncode != 0:
            raise RuntimeError(f"edge-tts failed: {result.stderr}")

    await loop.run_in_executor(None, _run_subprocess)  # type: ignore
    return output_path


# ─── F5-TTS (Voice Cloning) ──────────────────────────────────────────────────
F5_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "f5_tts")

_f5_instance = None

def _get_f5_tts():
    """Lazy-load F5-TTS model (cached after first call)."""
    global _f5_instance
    if _f5_instance is None:
        from f5_tts.api import F5TTS  # type: ignore
        os.makedirs(F5_MODEL_DIR, exist_ok=True)
        _f5_instance = F5TTS(model="F5TTS_v1_Base", ckpt_file="", vocab_file="")
    return _f5_instance


def f5_tts_is_available() -> bool:
    """Check if F5-TTS package is installed."""
    try:
        import f5_tts  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


async def run_f5_tts_clone(text: str, reference_audio: str, output_path: str) -> str:
    """Clone voice from reference audio using F5-TTS and synthesize text."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _f5_clone_sync, text, reference_audio, output_path)
    return output_path


def _transcribe_ref_audio(audio_path: str) -> str:
    """Transcribe reference audio using faster-whisper (avoids torchcodec crash).

    F5-TTS's built-in ASR uses transformers pipeline which imports torchcodec,
    and torchcodec crashes on Windows (missing FFmpeg shared DLLs).
    We use faster-whisper (already installed for Whisper tab) instead.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, language="zh")
        text = " ".join(seg.text.strip() for seg in segments)
        return text if text.strip() else "..."
    except Exception:
        # Fallback: return placeholder (F5-TTS still works, just slightly lower quality)
        return "..."


def _f5_clone_sync(text: str, reference_audio: str, output_path: str):
    import shutil
    import tempfile

    # Workaround: copy reference to ASCII-safe temp path (same as old XTTS fix)
    tmp_ref = None
    try:
        ext = os.path.splitext(reference_audio)[1] or ".wav"
        tmp_fd, tmp_ref = tempfile.mkstemp(suffix=ext, prefix="f5_ref_")
        os.close(tmp_fd)
        shutil.copy2(reference_audio, tmp_ref)

        # Transcribe reference audio ourselves (avoids torchcodec crash)
        ref_text = _transcribe_ref_audio(tmp_ref)

        tts = _get_f5_tts()
        tts.infer(
            ref_file=tmp_ref,
            ref_text=ref_text,
            gen_text=text,
            file_wave=output_path,
        )
    finally:
        if tmp_ref and os.path.exists(tmp_ref):
            os.remove(tmp_ref)
