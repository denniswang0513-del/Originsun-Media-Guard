"""Shared helpers for Whisper-based modules (transcriber.py, aligner.py).

These were duplicated identically in both files; promoted here to avoid
cross-leaf imports between transcriber and aligner.
"""
from __future__ import annotations
from typing import Tuple


def detect_device() -> Tuple[str, str]:
    """Pick CUDA when available, else CPU. Returns (device, compute_type)."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


def cleanup_gpu() -> None:
    """Best-effort GPU cache release. No-op when torch / CUDA unavailable."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timecode HH:MM:SS,mmm. Negative clamps to zero;
    sub-millisecond rounding that would overflow to 1000 ms carries to the
    next second so timestamps stay well-formed."""
    seconds = max(seconds, 0.0)
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    if ms >= 1000:
        ms -= 1000
        s += 1
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
