"""Shared helpers for Whisper-based modules (transcriber.py, aligner.py,
services/meeting_transcriber.py).

These were duplicated identically in both files; promoted here to avoid
cross-leaf imports between transcriber and aligner. load_model / extract_wav
進駐（2026-08-12）：模型載入樣板已在 transcriber ×2、tts_engine 各長一份、
ffmpeg 抽 wav 在 transcriber/aligner 也各一份 —— 新消費者一律走這裡
（既有呼叫端是機隊 OTA 關鍵路徑，遷移另案）。
"""
from __future__ import annotations

import os
import subprocess

from typing import Tuple

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def load_model(model_size: str):
    """WhisperModel + 裝置偵測 + models/ 快取目錄，一次到位。**每次呼叫都
    重新載模型**（turbo 約 1.5GB、CPU 載入 15–60 秒）—— 不做全域快取是刻意
    的：快取會讓 agent 行程常駐多 2–3GB RAM，偶發性任務不值得。"""
    from faster_whisper import WhisperModel  # type: ignore
    device, compute_type = detect_device()
    models_dir = os.path.join(_BASE, "models")
    os.makedirs(models_dir, exist_ok=True)
    return WhisperModel(model_size, device=device, compute_type=compute_type,
                        download_root=models_dir)


def extract_wav(src: str, dst: str) -> str:
    """ffmpeg 抽 16k 單聲道 wav（whisper 的標準輸入）。回錯誤訊息（"" = 成功）。"""
    ffmpeg = os.path.join(_BASE, "ffmpeg.exe")
    if not os.path.exists(ffmpeg):
        ffmpeg = "ffmpeg"
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", src, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", dst],
            capture_output=True, text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return f"ffmpeg 起不來：{e}"
    if r.returncode != 0 or not os.path.exists(dst):
        tail = (r.stderr or "").strip().splitlines()[-1:] or ["未知錯誤"]
        return f"音訊抽取失敗：{tail[0]}"
    return ""


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
