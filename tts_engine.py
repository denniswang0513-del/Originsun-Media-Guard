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

# ─── Lazy-loaded torch/torchaudio/soundfile ──────────────────────────────────
# These imports are DEFERRED to first use (not module load time) because
# importing torch takes 15-30+ seconds and would block uvicorn startup.
# The torchcodec/torchaudio patches are applied once on first import.
# See CLAUDE.md §5.1-5.2 for background.
import os
import asyncio

_torch = None      # lazy: set by _ensure_tts_imports()
_ta = None         # lazy: torchaudio
_sf = None         # lazy: soundfile
_TTS_PACKAGES_AVAILABLE = None  # None = not yet checked
_TTS_MISSING_REASON = ""
_tts_imports_done = False


def _ensure_tts_imports():
    """Lazy-import torch, torchaudio, soundfile and apply Windows patches.

    Called once on first use of any F5-TTS function.
    This keeps uvicorn startup fast (~2s instead of ~20s).
    """
    global _torch, _ta, _sf, _TTS_PACKAGES_AVAILABLE, _TTS_MISSING_REASON, _tts_imports_done
    if _tts_imports_done:
        return _TTS_PACKAGES_AVAILABLE
    _tts_imports_done = True

    try:
        # Patch os.add_dll_directory BEFORE importing torch/torchaudio
        # torchcodec crashes on Windows: os.add_dll_directory('.') → [WinError 87]
        _orig_add_dll_directory = os.add_dll_directory

        def _safe_add_dll_directory(path):
            try:
                return _orig_add_dll_directory(path)
            except OSError:
                import contextlib
                @contextlib.contextmanager
                def _noop_cm():
                    yield None
                return _noop_cm()

        os.add_dll_directory = _safe_add_dll_directory

        import torchaudio
        import soundfile
        import torch

        _ta = torchaudio
        _sf = soundfile
        _torch = torch

        # Patch torchaudio.load to use soundfile (avoids torchcodec backend crash)
        def _patched_ta_load(uri, frame_offset=0, num_frames=-1, normalize=True,
                             channels_first=True, format=None, buffer_size=4096, backend=None):
            data, sr = soundfile.read(str(uri), dtype="float32",
                                      start=frame_offset,
                                      stop=frame_offset + num_frames if num_frames > 0 else None)
            t = torch.from_numpy(data)
            if t.ndim == 1:
                t = t.unsqueeze(0)
            elif channels_first:
                t = t.T
            return t, sr

        torchaudio.load = _patched_ta_load

        _TTS_PACKAGES_AVAILABLE = True
        return True

    except ImportError as e:
        _TTS_PACKAGES_AVAILABLE = False
        _TTS_MISSING_REASON = f"缺少 TTS 套件: {e}. 請安裝 torch, torchaudio, soundfile 後重啟。"
        print(f"[WARNING] {_TTS_MISSING_REASON}")
        return False


def tts_packages_available() -> bool:
    """Check if core TTS packages (torch, torchaudio, soundfile) are installed."""
    if _TTS_PACKAGES_AVAILABLE is None:
        _ensure_tts_imports()
    return _TTS_PACKAGES_AVAILABLE


def tts_missing_reason() -> str:
    """Return human-readable reason why TTS packages are unavailable."""
    if _TTS_PACKAGES_AVAILABLE is None:
        _ensure_tts_imports()
    return _TTS_MISSING_REASON


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
        # Remove PYTHONHASHSEED if invalid (uvicorn may set non-numeric values)
        env.pop("PYTHONHASHSEED", None)
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
F5_MODEL_REPO = "SWivid/F5-TTS"
F5_MODEL_FILENAME = "F5TTS_v1_Base/model_1250000.safetensors"

_f5_instance = None


def _find_local_f5_ckpt() -> str:
    """Find local F5-TTS checkpoint file in models/f5_tts/."""
    import glob
    patterns = [
        os.path.join(F5_MODEL_DIR, "*.safetensors"),
        os.path.join(F5_MODEL_DIR, "**", "*.safetensors"),
    ]
    for pat in patterns:
        matches = glob.glob(pat, recursive=True)
        if matches:
            return matches[0]
    return ""


def _check_hf_cache_has_model() -> bool:
    """Check if F5-TTS model exists in HuggingFace cache (~/.cache/huggingface/)."""
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore
        result = try_to_load_from_cache(F5_MODEL_REPO, F5_MODEL_FILENAME)
        return result is not None and isinstance(result, str)
    except Exception:
        return False


def f5_model_is_ready() -> bool:
    """Check if F5-TTS model checkpoint exists in local models/ dir."""
    return bool(_find_local_f5_ckpt())


def f5_model_status() -> dict:
    """Return detailed F5-TTS model status.

    Returns dict with:
        local: bool — model exists in ./models/f5_tts/
        cached: bool — model exists in HuggingFace cache
    """
    return {
        "local": bool(_find_local_f5_ckpt()),
        "cached": _check_hf_cache_has_model(),
    }


def download_f5_model(progress_cb=None) -> str:
    """Download F5-TTS model to ./models/f5_tts/ using huggingface_hub.

    Args:
        progress_cb: Optional callback(dict) for progress updates.
            dict keys: phase (str), pct (int 0-100), msg (str)
    Returns:
        Path to downloaded checkpoint file.
    """
    os.makedirs(F5_MODEL_DIR, exist_ok=True)

    def _report(pct, msg):
        if progress_cb:
            try:
                progress_cb({"phase": "downloading", "pct": pct, "msg": msg})
            except Exception:
                pass

    _report(5, "正在從 HuggingFace 下載 F5-TTS 模型...")

    from huggingface_hub import hf_hub_download  # type: ignore
    local_path = hf_hub_download(
        repo_id=F5_MODEL_REPO,
        filename=F5_MODEL_FILENAME,
        local_dir=F5_MODEL_DIR,
    )

    _report(100, "F5-TTS 模型下載完成！")
    return local_path


def _get_f5_tts():
    """Lazy-load F5-TTS model (cached after first call)."""
    _ensure_tts_imports()
    global _f5_instance
    if _f5_instance is None:
        from f5_tts.api import F5TTS  # type: ignore
        os.makedirs(F5_MODEL_DIR, exist_ok=True)
        local_ckpt = _find_local_f5_ckpt()
        _f5_instance = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=local_ckpt or "",
            vocab_file="",
        )
    return _f5_instance


def f5_tts_is_available() -> bool:
    """Check if F5-TTS package is installed."""
    try:
        import f5_tts  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


async def run_f5_tts_clone(text: str, reference_audio: str, output_path: str,
                           progress_cb=None, speed: float = 1.0,
                           pitch: int = 0, ref_text: str = None) -> str:
    """Clone voice from reference audio using F5-TTS and synthesize text.

    Args:
        progress_cb: Optional callback(dict) for progress updates.
            dict keys: phase (str), pct (int 0-100), msg (str)
        speed: Speech speed multiplier (0.5~1.5, default 1.0)
        pitch: Pitch shift in semitones (-10~+10, default 0)
        ref_text: Optional manual transcription of reference audio.
            If provided, skips automatic Whisper transcription (most accurate).
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _f5_clone_sync, text, reference_audio, output_path,
        progress_cb, speed, pitch, ref_text
    )
    return output_path


def _trim_silence(audio_path: str) -> str:
    """Trim leading/trailing silence from audio, return path to trimmed temp file.

    This ensures Whisper only transcribes actual speech content, and the
    transcription matches the audio F5-TTS will actually use (since F5-TTS
    also trims silence internally via preprocess_ref_audio_text).
    """
    import tempfile
    try:
        from pydub import AudioSegment  # type: ignore
        from pydub.silence import detect_leading_silence  # type: ignore
        audio = AudioSegment.from_file(audio_path)
        start_trim = detect_leading_silence(audio, silence_threshold=-42)
        end_trim = detect_leading_silence(audio.reverse(), silence_threshold=-42)
        end_trim = max(end_trim, 0)
        trimmed = audio[start_trim:len(audio) - end_trim] if end_trim > 0 else audio[start_trim:]
        if len(trimmed) < 500:  # Less than 0.5s — probably trimmed too much
            print("[TTS] Silence trimming removed too much, using original audio")
            return audio_path
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="f5_trimmed_")
        os.close(tmp_fd)
        trimmed.export(tmp_path, format="wav")
        print(f"[TTS] Trimmed silence: {len(audio)}ms → {len(trimmed)}ms")
        return tmp_path
    except Exception as e:
        print(f"[TTS] Silence trimming failed, using original: {e}")
        return audio_path


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    try:
        import soundfile as sf
        data, sr = sf.read(audio_path)
        return len(data) / sr
    except Exception:
        return 0.0


def _transcribe_ref_audio(audio_path: str) -> str:
    """Transcribe reference audio using faster-whisper (avoids torchcodec crash).

    F5-TTS's built-in ASR uses transformers pipeline which imports torchcodec,
    and torchcodec crashes on Windows (missing FFmpeg shared DLLs).
    We use faster-whisper (already installed for Whisper tab) instead.

    IMPORTANT: ref_text must be proportional to the actual speech content.
    A too-short ref_text (e.g. "...") causes F5-TTS tensor size mismatch errors
    because it miscalculates the output duration ratio.
    """
    _ensure_tts_imports()
    audio_dur = _get_audio_duration(audio_path)
    effective_dur = min(audio_dur, 12.0) if audio_dur > 0 else 10.0

    try:
        from faster_whisper import WhisperModel  # type: ignore
        _dev = "cuda" if _torch.cuda.is_available() else "cpu"
        _ctype = "float16" if _dev == "cuda" else "int8"
        # Use "large-v3" for maximum transcription accuracy.
        # ref_text quality directly affects F5-TTS voice alignment & pronunciation.
        # On GPU with 3-15s audio clips this only takes ~1-2 seconds.
        model = WhisperModel("large-v3", device=_dev, compute_type=_ctype)
        segments, _ = model.transcribe(
            audio_path, language="zh",
            condition_on_previous_text=False,       # Prevent hallucination propagation
            no_speech_threshold=0.6,                 # Skip silent segments
            hallucination_silence_threshold=1.0,     # Detect hallucinated text in silence >1s
        )
        text = " ".join(seg.text.strip() for seg in segments)
        if text.strip():
            # Sanity check: Chinese speech is typically 2~5 chars/sec
            max_reasonable = int(effective_dur * 5)
            min_reasonable = max(int(effective_dur * 2), 4)
            if len(text) > max_reasonable:
                print(f"[TTS] ref_text too long ({len(text)} chars for {effective_dur:.1f}s), "
                      f"truncating to {max_reasonable} (suspected hallucination)")
                text = text[:max_reasonable]
            elif len(text) < min_reasonable:
                print(f"[TTS] ref_text too short ({len(text)} chars for {effective_dur:.1f}s), "
                      f"padding to {min_reasonable}")
                text = "。" * min_reasonable
            print(f"[TTS] ref_text transcribed ({len(text)} chars, {effective_dur:.1f}s): {text}")
            return text
    except Exception as e:
        print(f"[TTS] faster-whisper transcription failed: {e}")

    # Fallback: estimate ~4 chars/sec of audio to generate proportional placeholder.
    char_count = max(int(effective_dur * 4), 10)
    return "。" * char_count


def _num_to_zh(n: int) -> str:
    """Convert a non-negative integer to Mandarin Chinese reading (台灣口語)."""
    if n == 0:
        return '零'
    units = ['', '十', '百', '千', '萬', '十', '百', '千', '億']
    digits = '零一二三四五六七八九'
    result = ''
    s = str(n)
    length = len(s)
    for i, ch in enumerate(s):
        d = int(ch)
        pos = length - 1 - i          # position weight
        # suppress leading / consecutive zeros
        if d == 0:
            if result and result[-1] != '零':
                result += '零'
        else:
            result += digits[d]
            if pos < len(units):
                result += units[pos]
    # trim trailing 零, collapse internal 零零→零
    import re as _re
    result = result.rstrip('零')
    result = _re.sub(r'零+', '零', result)
    # 一十 → 十 at the start
    if result.startswith('一十'):
        result = result[1:]
    return result or '零'


def _convert_numbers_in_text(text: str) -> str:
    """Replace Arabic numerals in text with their Mandarin reading.

    Rules (in order):
    - Percentage:  50% / 50％       → 百分之五十
    - Year:        2024年 / 1999年  → 二零二四年  (digit-by-digit for 4-digit years)
    - Ordinal:     第3名 / 第10集   → 第三名 / 第十集
    - General:     123             → 一百二十三
    - Digit run:   12345678        → digit-by-digit (≥8 digits, likely ID/phone)
    """
    import re

    # 1. Percentage
    def _pct(m):
        n = m.group(1)
        try:
            val = float(n)
            if val == int(val):
                return '百分之' + _num_to_zh(int(val))
            # decimal percentage: just read digits
            return '百分之' + ''.join('零一二三四五六七八九'[int(c)] if c.isdigit() else '點' for c in n)
        except Exception:
            return m.group(0)
    text = re.sub(r'(\d+(?:\.\d+)?)[%％]', _pct, text)

    # 2. Year (exactly 4 digits followed by 年)
    def _year(m):
        return ''.join('零一二三四五六七八九'[int(c)] for c in m.group(1)) + '年'
    text = re.sub(r'(\d{4})年', _year, text)

    # 3. Ordinal — 第N (1–4 digits)
    def _ordinal(m):
        return '第' + _num_to_zh(int(m.group(1)))
    text = re.sub(r'第(\d{1,4})', _ordinal, text)

    # 4. Long digit runs (≥8 digits): phone / ID — read digit by digit
    def _digits(m):
        return ''.join('零一二三四五六七八九'[int(c)] for c in m.group(0))
    text = re.sub(r'\d{8,}', _digits, text)

    # 5. General integers (remaining)
    def _general(m):
        try:
            return _num_to_zh(int(m.group(0)))
        except Exception:
            return m.group(0)
    text = re.sub(r'\d+', _general, text)

    return text


def _prepare_gen_text(text: str) -> str:
    """Preprocess generation text to improve F5-TTS output completeness.

    F5-TTS is trained on Chinese characters; Arabic numerals are
    out-of-distribution and often skipped or mispronounced.  This function:
    1. Converts Arabic numerals to Mandarin Chinese readings.
    2. Inserts a space after CJK punctuation so chunk_text() has stable
       split points (prevents short chunks that trigger local_speed=0.3).
    3. Ensures the text ends with sentence-closing punctuation so the last
       chunk is not silently dropped.
    """
    import re

    if not text or not text.strip():
        return text

    # 1. Normalise line breaks → single space
    text = re.sub(r'\s+', ' ', text).strip()

    # 2. Convert Arabic numbers to Chinese
    text = _convert_numbers_in_text(text)

    # 3. Add space after CJK punctuation for stable chunk splitting
    text = re.sub(r'([。！？；：，、])', r'\1 ', text)
    text = re.sub(r' +', ' ', text).strip()

    # 4. Ensure text ends with sentence-closing punctuation
    if text and text[-1] not in '。！？.!?':
        text = text + '。'

    return text


def _pad_text_for_inference(text: str, ref_text_len: int, ref_audio_dur: float, speed: float):
    """Pad short trailing chunks to prevent F5-TTS from truncating the last batch.

    F5-TTS splits long text into chunks based on max_chars. When the last chunk
    has fewer than 10 UTF-8 bytes, its inferred duration is too short, causing
    audible truncation. This function appends filler characters ('…') so the
    last chunk meets the minimum length threshold.

    Returns:
        (padded_text, did_pad): The (possibly padded) text and whether padding was applied.
    """
    if ref_audio_dur <= 0 or ref_text_len <= 0:
        return text, False

    text_bytes = len(text.encode("utf-8"))

    # Mirror F5-TTS max_chars formula (utils_infer.py line ~402)
    max_chars = int(ref_text_len / ref_audio_dur * (22 - ref_audio_dur) * speed)
    if max_chars <= 0:
        max_chars = 100

    # Only one chunk → no splitting issue
    if text_bytes <= max_chars:
        return text, False

    last_chunk_bytes = text_bytes % max_chars
    if last_chunk_bytes == 0:
        last_chunk_bytes = max_chars

    # If the last chunk is dangerously short, pad it
    if last_chunk_bytes < 15:
        padding_needed = 15 - last_chunk_bytes
        # Each '…' is 3 UTF-8 bytes
        num_ellipsis = (padding_needed // 3) + 1
        padded = text + "…" * num_ellipsis
        print(f"[TTS] Padded text: last chunk {last_chunk_bytes}B → +{num_ellipsis} filler chars")
        return padded, True

    return text, False


def _f5_clone_sync(text: str, reference_audio: str, output_path: str,
                   progress_cb=None, speed: float = 1.0, pitch: int = 0,
                   ref_text: str = None):
    _ensure_tts_imports()
    import shutil
    import tempfile
    import sys
    import io

    def _report(phase: str, pct: int = 0, msg: str = ""):
        if progress_cb:
            try:
                progress_cb({"phase": phase, "pct": pct, "msg": msg})
            except Exception:
                pass

    # Force UTF-8 stdout/stderr to prevent cp950 encoding crashes on Windows
    # F5-TTS internally prints Chinese text (ref_text, gen_text) during inference
    # IMPORTANT: We must detach() the wrapper before restoring, otherwise
    # TextIOWrapper.__del__ will close the underlying buffer, causing
    # "I/O operation on closed file" on the next call.
    old_stdout, old_stderr = sys.stdout, sys.stderr
    wrapped_out, wrapped_err = None, None
    try:
        wrapped_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        wrapped_err = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stdout = wrapped_out
        sys.stderr = wrapped_err
    except Exception:
        wrapped_out, wrapped_err = None, None  # wrapping failed, proceed with originals

    _report("preparing", 5, "正在準備參考音訊...")

    # Workaround: copy reference to ASCII-safe temp path (same as old XTTS fix)
    tmp_ref = None
    trimmed_path = None
    try:
        ext = os.path.splitext(reference_audio)[1] or ".wav"
        tmp_fd, tmp_ref = tempfile.mkstemp(suffix=ext, prefix="f5_ref_")
        os.close(tmp_fd)
        shutil.copy2(reference_audio, tmp_ref)

        # Trim leading/trailing silence before transcription
        _report("preparing", 10, "正在裁切靜音...")
        trimmed_path = _trim_silence(tmp_ref)
        # Use trimmed audio as the reference for both transcription and synthesis
        if trimmed_path != tmp_ref:
            os.remove(tmp_ref)
            tmp_ref = trimmed_path
            trimmed_path = None  # Don't double-delete

        # Transcribe reference audio (or use user-provided ref_text)
        if ref_text and ref_text.strip():
            print(f"[TTS] Using user-provided ref_text ({len(ref_text)} chars): {ref_text}")
            _report("transcribing", 20, "使用手動輸入的參考文字")
        else:
            _report("transcribing", 15, "正在轉錄參考音訊...")
            ref_text = _transcribe_ref_audio(tmp_ref)

        # Load model (first time is slow: ~30-60s)
        if _f5_instance is None:
            _report("loading_model", 30, "正在載入 F5-TTS 模型（首次約需 30 秒）...")

        tts = _get_f5_tts()

        device_name = "GPU" if _torch.cuda.is_available() else "CPU"
        _report("inferring", 50, f"正在用 {device_name} 生成語音，請稍候...")

        # Pre-process generation text: ensure sentence boundaries & avoid
        # short chunks that trigger F5-TTS's local_speed=0.3 override
        gen_text = _prepare_gen_text(text)
        prepared_bytes = len(gen_text.encode("utf-8"))  # track before padding
        print(f"[TTS] gen_text after prepare: {repr(gen_text[:80])}{'...' if len(gen_text) > 80 else ''}")

        # Pad text if last chunk would be too short (prevents end truncation)
        ref_audio_dur = _get_audio_duration(tmp_ref)
        ref_text_bytes = len(ref_text.encode("utf-8"))
        gen_text, did_pad = _pad_text_for_inference(gen_text, ref_text_bytes, ref_audio_dur, speed)

        tts.infer(
            ref_file=tmp_ref,
            ref_text=ref_text,
            gen_text=gen_text,
            file_wave=output_path,
            speed=speed,
        )

        # If we padded filler text, trim the trailing audio it generated
        if did_pad:
            try:
                wav_data, wav_sr = _sf.read(output_path, dtype="float32")
                total_samples = len(wav_data)
                # Estimate how much audio the filler produced:
                # ratio = filler_bytes / total_gen_bytes * total_duration
                gen_bytes = len(gen_text.encode("utf-8"))
                # filler_bytes = only the ellipsis added by _pad_text_for_inference,
                # NOT the spaces added by _prepare_gen_text (those are legitimate speech)
                filler_bytes = gen_bytes - prepared_bytes
                filler_ratio = filler_bytes / gen_bytes if gen_bytes > 0 else 0
                trim_samples = int(total_samples * filler_ratio)
                if 0 < trim_samples < total_samples:
                    _sf.write(output_path, wav_data[:total_samples - trim_samples], wav_sr)
                    print(f"[TTS] Trimmed {trim_samples} samples ({trim_samples/wav_sr:.2f}s) of filler audio")
            except Exception as e:
                print(f"[TTS] Warning: filler trim failed, using full audio: {e}")

        # Post-processing: pitch shift (F5-TTS has no native pitch control)
        if pitch != 0:
            _report("pitch_shift", 90, f"正在調整音高 ({'+' if pitch > 0 else ''}{pitch} 半音)...")
            import torchaudio.functional as F
            waveform, sr = _sf.read(output_path, dtype="float32")
            t = _torch.from_numpy(waveform)
            if t.ndim == 1:
                t = t.unsqueeze(0)
            else:
                t = t.T
            t_shifted = F.pitch_shift(t, sr, pitch)
            # Save back
            if t_shifted.ndim == 2 and t_shifted.shape[0] == 1:
                out_np = t_shifted.squeeze(0).numpy()
            else:
                out_np = t_shifted.T.numpy()
            _sf.write(output_path, out_np, sr)

        _report("done", 100, "生成完成！")
    finally:
        if tmp_ref and os.path.exists(tmp_ref):
            os.remove(tmp_ref)
        if trimmed_path and trimmed_path != tmp_ref and os.path.exists(trimmed_path):
            os.remove(trimmed_path)
        # Restore original stdout/stderr — detach wrappers first to prevent
        # them from closing the underlying buffer when garbage collected
        try:
            if wrapped_out is not None:
                wrapped_out.detach()
            if wrapped_err is not None:
                wrapped_err.detach()
        except Exception:
            pass
        sys.stdout, sys.stderr = old_stdout, old_stderr
