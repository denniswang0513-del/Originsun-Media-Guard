"""
TTS Engine — Originsun Media Guard Pro
Supports:
  1. Edge-TTS  (standard, free, Taiwan accent)
  2. F5-TTS   (voice cloning, local) — planned
  3. GPT-SoVITS (voice cloning, local) — planned
"""
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
        _f5_instance = F5TTS(model_type="F5-TTS", ckpt_file="", vocab_file="")
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

        tts = _get_f5_tts()
        tts.infer(
            ref_file=tmp_ref,
            ref_text="",       # empty = auto ASR
            gen_text=text,
            file_wave=output_path,
        )
    finally:
        if tmp_ref and os.path.exists(tmp_ref):
            os.remove(tmp_ref)
