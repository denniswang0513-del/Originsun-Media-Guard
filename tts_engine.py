"""
TTS Engine — Originsun Media Guard Pro
Supports:
  1. Edge-TTS  (standard, free, Taiwan accent)
  2. XTTS v2   (Coqui, voice cloning, local)
"""
import os
import asyncio

# ─── PyTorch 2.6+ compatibility patch ────────────────────────────────────────
# Coqui TTS checkpoints contain custom classes that fail with weights_only=True
import torch
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

# ─── torchaudio: force soundfile backend (torchcodec broken on Windows) ───────
try:
    import torchaudio
    from torchaudio._backend import utils as _ta_utils
    from torchaudio._backend.soundfile import SoundfileBackend as _SfBackend
    # Clear the cached backend list and replace it so soundfile is the ONLY option
    _ta_utils.get_available_backends.cache_clear()
    _ta_utils.get_available_backends = lambda: {"soundfile": _SfBackend}
except Exception:
    pass

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



# ─── XTTS v2 (Voice Cloning) ─────────────────────────────────────────────────
XTTS_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "xtts_v2")

def xtts_is_ready() -> bool:
    """Check if XTTS v2 model files are downloaded."""
    # Coqui TTS downloads into "tts/tts_models--multilingual--multi-dataset--xtts_v2" if we set XDG_DATA_HOME
    config_path = os.path.join(XTTS_MODEL_PATH, "tts", "tts_models--multilingual--multi-dataset--xtts_v2", "config.json")
    return os.path.exists(config_path)

def download_xtts_model():
    """Trigger XTTS v2 model download (blocking)."""
    try:
        os.environ["XTTS_MODEL_PATH"] = XTTS_MODEL_PATH  # for subprocess/async instances
        os.makedirs(XTTS_MODEL_PATH, exist_ok=True)
        # Point Coqui TTS to our local directory and bypass TOS prompt
        os.environ["XDG_DATA_HOME"] = XTTS_MODEL_PATH
        os.environ["TTS_HOME"] = XTTS_MODEL_PATH
        os.environ["COQUI_TOS_AGREED"] = "1"
        from TTS.api import TTS  # type: ignore
        # Instantiating the TTS class will automatically download the model if it doesn't exist
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=True)
        print("[TTS] XTTS v2 model downloaded successfully.")
    except ImportError:
        raise RuntimeError("Coqui TTS 未安裝，請執行: pip install TTS")

async def run_voice_clone(text: str, reference_audio: str, output_path: str, language: str = "zh") -> str:
    """Clone voice from reference audio and synthesize text."""
    if not xtts_is_ready():
        raise RuntimeError("XTTS v2 模型尚未下載，請先下載模型")
    try:
        from TTS.api import TTS  # type: ignore
    except ImportError:
        raise RuntimeError("Coqui TTS 未安裝，請執行: pip install TTS")

    # Run in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _clone_sync, text, reference_audio, output_path, language)
    return output_path

def _clone_sync(text, reference_audio, output_path, language):
    import shutil
    import tempfile

    # Ensure TTS loads from the correct local path and bypass TOS
    os.environ["XDG_DATA_HOME"] = XTTS_MODEL_PATH
    os.environ["TTS_HOME"] = XTTS_MODEL_PATH
    os.environ["COQUI_TOS_AGREED"] = "1"

    # Workaround: soundfile/libsndfile cannot open non-ASCII paths on Windows.
    # Copy reference audio to a temp file with an ASCII-safe name.
    tmp_ref = None
    try:
        ext = os.path.splitext(reference_audio)[1] or ".wav"
        tmp_fd, tmp_ref = tempfile.mkstemp(suffix=ext, prefix="tts_ref_")
        os.close(tmp_fd)
        shutil.copy2(reference_audio, tmp_ref)

        from TTS.api import TTS  # type: ignore
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        tts.tts_to_file(
            text=text,
            speaker_wav=tmp_ref,
            language=language,
            file_path=output_path
        )
    finally:
        if tmp_ref and os.path.exists(tmp_ref):
            os.remove(tmp_ref)
