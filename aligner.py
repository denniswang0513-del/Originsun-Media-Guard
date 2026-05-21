"""
Forced alignment for video subtitles.

Given an audio file + a known-good transcript, produce timed cues that
preserve the user's exact text, segmentation, and line breaks.

Public API:
    run_align_job(...)          # full pipeline (loads model, runs ffmpeg, writes SRT)

Pure helpers (testable without audio):
    parse_transcript(content, fmt)
    extract_char_metas(segments)
    build_anchored_timeline(char_metas, audio_duration, anchor_threshold)
    map_segments_to_cues(segments, char_metas, anchored)
    polish_subtitle_timeline(cues, fps, ...)
    cues_to_srt_bytes(cues, encoding_bom)
"""

from __future__ import annotations

import os
import re
import time
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict, Any, Callable

from core.whisper_helpers import (  # type: ignore
    detect_device as _detect_device,
    cleanup_gpu as _cleanup_gpu,
    format_srt_timestamp as _format_timestamp,
)


# Source markers for CharMeta.source — string constants prevent typo bugs
# while keeping JSON output human-readable.
SOURCE_ANCHOR = "anchor"
SOURCE_INTERPOLATED = "interpolated"
SOURCE_EDGE_FILL = "edge_fill"


# Module-level model cache — keyed by (model_size, device, compute_type).
# stable_whisper model loading is 5-30s; reusing across jobs is a big win
# since the queue often runs the same model back-to-back on different videos.
_MODEL_CACHE: Dict[Tuple[str, str, str], Any] = {}


def _get_or_load_model(model_size: str, device: str, compute_type: str, models_dir: str):
    key = (model_size, device, compute_type)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    import stable_whisper  # type: ignore
    model = stable_whisper.load_faster_whisper(
        model_size, device=device, compute_type=compute_type,
        download_root=models_dir,
    )
    _MODEL_CACHE[key] = model
    return model


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CharMeta:
    """One non-whitespace character from the transcript, with its position."""
    char: str
    seg_idx: int        # which segment (0-based) this char belongs to
    text_idx: int       # 0-based index in full_text (the concat string fed to align)
    start: float = 0.0
    end: float = 0.0
    prob: float = 0.0
    source: str = ""    # one of SOURCE_ANCHOR / SOURCE_INTERPOLATED / SOURCE_EDGE_FILL


@dataclass
class Cue:
    """One subtitle cue, ready for SRT output."""
    index: int
    text: str           # may contain '\n' for multi-line cue
    start: float
    end: float
    anchor_chars: int = 0
    interpolated_chars: int = 0
    edge_fill_chars: int = 0
    reading_speed: float = 0.0  # chars per second


# ─────────────────────────────────────────────────────────────────────────────
# Transcript parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_transcript(content: str, fmt: str = "auto") -> List[str]:
    """Split transcript into segments. Each segment becomes one SRT cue.

    Rules:
        - .srt: each entry's text (without timestamps) is one segment.
        - .txt: every non-blank line is one segment (blank lines ignored).
        - 'auto': sniff for SRT signature, else .txt rules.

    Internal newlines within a segment are preserved — but only .srt entries
    can carry them; .txt segments are always single-line.
    Empty/whitespace-only segments are dropped.
    """
    if not content or not content.strip():
        return []

    fmt = (fmt or "auto").lower()
    if fmt == "auto":
        head = content[:500]
        # SRT signature: "-->" arrow plus a leading numeric index line
        if "-->" in head and re.search(r"^\s*\d+\s*$", head, re.MULTILINE):
            fmt = "srt"
        else:
            fmt = "txt"

    if fmt == "srt":
        return _parse_srt_segments(content)
    return _parse_txt_segments(content)


def _parse_txt_segments(text: str) -> List[str]:
    """Every non-blank line becomes one cue. Blank lines are treated purely
    as separators (ignored) — NOT as paragraph boundaries.

    This guarantees "one line = one subtitle" regardless of whether the
    transcript also contains blank lines between paragraphs. The old
    either/or rule flipped the whole file into blank-line mode the moment a
    single blank line appeared, silently merging consecutive lines into one
    multi-line cue.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _parse_srt_segments(text: str) -> List[str]:
    """Parse SRT, return list of cue texts (timestamps stripped, internal \\n kept)."""
    try:
        import pysrt  # type: ignore
        subs = pysrt.from_string(text)
        out = []
        for s in subs:
            t = (s.text_without_tags or s.text or "").strip()
            if t:
                out.append(t)
        if out:
            return out
    except Exception:
        pass
    # Fallback: regex-based parser (tolerates malformed SRT)
    return _parse_srt_fallback(text)


def _parse_srt_fallback(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", text.strip())
    out = []
    for blk in blocks:
        lines = blk.split("\n")
        # Drop the leading index line and the timestamp line
        body_lines = []
        for ln in lines:
            if re.match(r"^\s*\d+\s*$", ln):
                continue
            if "-->" in ln:
                continue
            body_lines.append(ln.rstrip())
        body = "\n".join(body_lines).strip()
        if body:
            out.append(body)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Concat segments + char metadata
# ─────────────────────────────────────────────────────────────────────────────

def extract_char_metas(segments: List[str]) -> Tuple[str, List[CharMeta]]:
    """Concatenate segments with '\\n' separators and produce CharMeta for every
    non-whitespace character (whitespace contributes nothing to alignment).

    Returns (full_text, char_metas).
    full_text is the exact string to pass to stable-ts align().
    char_metas[k].text_idx is k's index in full_text.
    """
    pieces = []
    for i, seg in enumerate(segments):
        if i > 0:
            pieces.append("\n")
        pieces.append(seg)
    full_text = "".join(pieces)

    metas: List[CharMeta] = []
    text_pos = 0
    for seg_idx, seg in enumerate(segments):
        if seg_idx > 0:
            text_pos += 1  # the '\n' separator
        for ch in seg:
            if not ch.isspace():
                metas.append(CharMeta(char=ch, seg_idx=seg_idx, text_idx=text_pos))
            text_pos += 1
    return full_text, metas


# ─────────────────────────────────────────────────────────────────────────────
# Map raw align output → CharMeta times
# ─────────────────────────────────────────────────────────────────────────────

def assign_raw_word_times(
    full_text: str,
    char_metas: List[CharMeta],
    raw_words: List[Any],
) -> int:
    """Walk through raw_words from stable-ts and assign (start, end, prob) to
    char_metas in-place. Returns count of chars that received times.

    raw_words: list of objects/dicts with 'word', 'start', 'end', 'probability'.

    For Chinese the model emits one char per word; for English a word like
    'hello' covers 5 chars in full_text. We consume chars from full_text in
    order, matching them against the chars of each raw_word.word (after
    stripping leading whitespace).
    """
    by_text_idx: Dict[int, CharMeta] = {m.text_idx: m for m in char_metas}

    text_pos = 0
    matched = 0
    for w in raw_words:
        wtext = _word_attr(w, "word", "")
        if wtext is None:
            continue
        wclean = wtext.lstrip()
        if not wclean:
            continue
        wstart = float(_word_attr(w, "start", 0.0) or 0.0)
        wend = float(_word_attr(w, "end", wstart) or wstart)
        wprob = float(_word_attr(w, "probability", _word_attr(w, "prob", 1.0)) or 0.0)

        for ch in wclean:
            while text_pos < len(full_text) and full_text[text_pos].isspace():
                text_pos += 1
            if text_pos >= len(full_text):
                return matched
            meta = by_text_idx.get(text_pos)
            if meta is not None:
                # Assign even if char doesn't exactly match — stable-ts may
                # normalize a few edge chars; positional alignment is what matters.
                meta.start = wstart
                meta.end = wend
                meta.prob = wprob
                matched += 1
            text_pos += 1
    return matched


def _word_attr(w: Any, name: str, default: Any = None) -> Any:
    if w is None:
        return default
    if isinstance(w, dict):
        return w.get(name, default)
    return getattr(w, name, default)


# ─────────────────────────────────────────────────────────────────────────────
# Anchor + interpolation
# ─────────────────────────────────────────────────────────────────────────────

def build_anchored_timeline(
    char_metas: List[CharMeta],
    audio_duration: float,
    anchor_threshold: float = 0.4,
) -> None:
    """In-place: for each CharMeta, ensure start/end/source is set such that:
        - source='anchor': raw align time, prob >= threshold
        - source='interpolated': linearly between two surrounding anchors
        - source='edge_fill': before first anchor or after last anchor (linear from t=0 / to audio_duration)
        - All times are monotonically non-decreasing across the array.

    Guarantees first char starts at a known time (>=0) even if no head anchor.
    Guarantees last char ends at audio_duration if no tail anchor (subject to
    detect_trailing_silence handled by caller).
    """
    n = len(char_metas)
    if n == 0:
        return

    # Identify anchors
    anchor_idxs = [i for i, m in enumerate(char_metas) if m.prob >= anchor_threshold]

    if not anchor_idxs:
        # No anchor: full linear spread from 0 to audio_duration
        per = audio_duration / max(n, 1)
        for i, m in enumerate(char_metas):
            m.start = i * per
            m.end = (i + 1) * per
            m.source = SOURCE_EDGE_FILL
        return

    # Mark anchors first
    for ai in anchor_idxs:
        char_metas[ai].source = SOURCE_ANCHOR

    # Edge fill BEFORE first anchor — fill [0, first_anchor.start] completely
    first = anchor_idxs[0]
    if first > 0:
        first_start = char_metas[first].start
        slots = first
        per = first_start / max(slots, 1)
        for k in range(first):
            m = char_metas[k]
            m.start = k * per
            m.end = (k + 1) * per
            m.source = SOURCE_EDGE_FILL

    # Edge fill AFTER last anchor — fill [last_anchor.end, audio_duration] completely
    last = anchor_idxs[-1]
    if last < n - 1:
        last_end = char_metas[last].end
        remain = max(audio_duration - last_end, 0.0)
        slots = (n - 1) - last
        per = remain / max(slots, 1)
        for k in range(last + 1, n):
            offset = k - last
            m = char_metas[k]
            m.start = last_end + (offset - 1) * per
            m.end = last_end + offset * per
            m.source = SOURCE_EDGE_FILL

    # Interp BETWEEN consecutive anchors — fill [left_end, right_start] completely
    for ai_pos in range(len(anchor_idxs) - 1):
        left = anchor_idxs[ai_pos]
        right = anchor_idxs[ai_pos + 1]
        if right - left <= 1:
            continue
        left_end = char_metas[left].end
        right_start = char_metas[right].start
        # Defensive: if right anchor's time is before left anchor's end, demote it
        if right_start < left_end:
            right_start = left_end
            char_metas[right].start = right_start
            if char_metas[right].end < right_start:
                char_metas[right].end = right_start
        gap = right_start - left_end
        slots = right - left - 1
        per = gap / max(slots, 1)
        for offset in range(1, slots + 1):
            idx = left + offset
            m = char_metas[idx]
            m.start = left_end + (offset - 1) * per
            m.end = left_end + offset * per
            m.source = SOURCE_INTERPOLATED

    # Final monotonicity sweep — guarantee non-decreasing times
    prev_end = 0.0
    for m in char_metas:
        if m.start < prev_end:
            m.start = prev_end
        if m.end < m.start:
            m.end = m.start
        prev_end = m.end


# ─────────────────────────────────────────────────────────────────────────────
# Map char metas → Cues (one per segment)
# ─────────────────────────────────────────────────────────────────────────────

def map_segments_to_cues(segments: List[str], char_metas: List[CharMeta]) -> List[Cue]:
    """Build one Cue per segment, using the first/last char's times.
    Segments with no non-whitespace chars are skipped (and warned upstream)."""
    by_seg: Dict[int, List[CharMeta]] = {}
    for m in char_metas:
        by_seg.setdefault(m.seg_idx, []).append(m)

    cues: List[Cue] = []
    out_idx = 0
    for seg_idx, seg_text in enumerate(segments):
        chars = by_seg.get(seg_idx, [])
        if not chars:
            continue
        out_idx += 1
        start = chars[0].start
        end = chars[-1].end
        # Defensive: ensure end >= start
        if end < start:
            end = start
        # Quality counts
        anc = sum(1 for c in chars if c.source == SOURCE_ANCHOR)
        intp = sum(1 for c in chars if c.source == SOURCE_INTERPOLATED)
        edg = sum(1 for c in chars if c.source == SOURCE_EDGE_FILL)
        dur = max(end - start, 0.001)
        rspeed = len(chars) / dur
        cues.append(Cue(
            index=out_idx,
            text=seg_text,
            start=start,
            end=end,
            anchor_chars=anc,
            interpolated_chars=intp,
            edge_fill_chars=edg,
            reading_speed=rspeed,
        ))
    return cues


# ─────────────────────────────────────────────────────────────────────────────
# Subtitle timing polish
# ─────────────────────────────────────────────────────────────────────────────

def polish_subtitle_timeline(
    cues: List[Cue],
    fps: float = 25.0,
    min_duration: float = 1.0,
    max_duration: float = 7.0,
    min_gap_frames: int = 2,
    audio_duration: Optional[float] = None,
    hold_until_next: bool = True,
) -> Dict[str, Any]:
    """In-place: snap to frame, enforce timing rules, collect warnings.

    NEVER modifies cue text. Only adjusts start/end times.

    When hold_until_next=True (Netflix-style), each cue.end is extended to the
    next cue.start (last cue → audio_duration). min_duration / min_gap rules
    are skipped because the hold step already satisfies them with zero gap.

    Returns a stats dict for QA reporting.
    """
    stats = {
        "frame_snapped": 0,
        "min_duration_extended": 0,
        "gap_inserted": 0,
        "hold_extended": 0,
        "max_duration_warnings": [],
        "reading_speed_warnings": [],
    }
    if not cues:
        return stats

    fps = max(float(fps), 1.0)
    frame = 1.0 / fps
    min_gap = min_gap_frames * frame

    def _snap(t: float) -> float:
        return round(t * fps) / fps

    for c in cues:
        s0, e0 = c.start, c.end
        c.start = _snap(c.start)
        c.end = _snap(c.end)
        if c.start != s0 or c.end != e0:
            stats["frame_snapped"] += 1
        if c.end < c.start:
            c.end = c.start

    if hold_until_next:
        # Each cue persists until the next one starts (zero gap).
        # Last cue extends to audio_duration if known, else stays put.
        for i in range(len(cues) - 1):
            cur, nxt = cues[i], cues[i + 1]
            target_end = _snap(nxt.start)
            if target_end < cur.start:
                target_end = cur.start  # don't invert
            if target_end != cur.end:
                cur.end = target_end
                stats["hold_extended"] += 1
        if audio_duration is not None and cues:
            last = cues[-1]
            target_end = _snap(audio_duration)
            if target_end > last.end:
                last.end = target_end
                stats["hold_extended"] += 1
    else:
        # 2. Min duration — extend, but never overlap next cue
        for i, c in enumerate(cues):
            if c.end - c.start >= min_duration:
                continue
            target_end = _snap(c.start + min_duration)
            if i + 1 < len(cues):
                cap = _snap(cues[i + 1].start - min_gap)
                target_end = min(target_end, cap)
            elif audio_duration is not None:
                target_end = min(target_end, _snap(audio_duration))
            if target_end > c.end:
                c.end = target_end
                stats["min_duration_extended"] += 1

        # 3. Min gap between consecutive cues
        for i in range(len(cues) - 1):
            cur, nxt = cues[i], cues[i + 1]
            if nxt.start - cur.end < min_gap:
                new_end = _snap(nxt.start - min_gap)
                if new_end < cur.start:
                    new_end = cur.start  # don't invert
                if new_end < cur.end:
                    cur.end = new_end
                    stats["gap_inserted"] += 1

    # 4. Warnings — informational only, no time changes
    for c in cues:
        dur = c.end - c.start
        if dur > max_duration:
            stats["max_duration_warnings"].append({
                "index": c.index,
                "duration": round(dur, 2),
                "preview": c.text[:30],
            })
        if dur > 0:
            visible_chars = sum(1 for ch in c.text if not ch.isspace())
            rate = visible_chars / dur
            c.reading_speed = rate
            if rate > 18.0:
                stats["reading_speed_warnings"].append({
                    "index": c.index,
                    "rate": round(rate, 1),
                    "preview": c.text[:30],
                })
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# SRT serialization
# ─────────────────────────────────────────────────────────────────────────────

def cues_to_srt_bytes(cues: List[Cue], encoding_bom: bool = True) -> bytes:
    """Serialize cues to SRT format (CRLF line endings, optional UTF-8 BOM).

    Cues are renumbered 1..N to keep index sequential after any drops.
    """
    lines = []
    for i, c in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_format_timestamp(c.start)} --> {_format_timestamp(c.end)}")
        # Preserve internal newlines in cue text — they become multi-line subtitles
        for tline in c.text.split("\n"):
            lines.append(tline)
        lines.append("")  # trailing blank line between entries
    body = "\r\n".join(lines).encode("utf-8")
    if encoding_bom:
        body = b"\xef\xbb\xbf" + body
    return body


# ─────────────────────────────────────────────────────────────────────────────
# Quality report
# ─────────────────────────────────────────────────────────────────────────────

def build_quality_report(
    cues: List[Cue],
    char_metas: List[CharMeta],
    fps: float,
    fps_source: str,
    audio_duration: float,
    polish_stats: Dict[str, Any],
) -> Dict[str, Any]:
    total_chars = len(char_metas)
    anc = sum(1 for m in char_metas if m.source == SOURCE_ANCHOR)
    intp = sum(1 for m in char_metas if m.source == SOURCE_INTERPOLATED)
    edg = sum(1 for m in char_metas if m.source == SOURCE_EDGE_FILL)

    low_conf_cues = []
    for c in cues:
        seg_total = c.anchor_chars + c.interpolated_chars + c.edge_fill_chars
        if seg_total == 0:
            continue
        ratio = c.anchor_chars / seg_total
        if ratio < 0.4:
            low_conf_cues.append({
                "index": c.index,
                "start": round(c.start, 2),
                "end": round(c.end, 2),
                "anchor_ratio": round(ratio, 2),
                "preview": c.text[:30],
            })

    return {
        "total_chars": total_chars,
        "audio_duration": round(audio_duration, 2),
        "fps": fps,
        "fps_source": fps_source,
        "subtitle_count": len(cues),
        "alignment": {
            "anchor_chars": anc,
            "interpolated_chars": intp,
            "edge_fill_chars": edg,
            "anchor_ratio": round(anc / max(total_chars, 1), 3),
            "interpolated_ratio": round(intp / max(total_chars, 1), 3),
            "edge_fill_ratio": round(edg / max(total_chars, 1), 3),
        },
        "subtitle_polish": polish_stats,
        "low_confidence_cues": low_conf_cues,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Audio + video helpers (used by full pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def probe_video_meta(path: str, ffprobe_bin: str = "ffprobe") -> Dict[str, Any]:
    """Read fps + duration in a single ffprobe call.
    Returns {'fps': float, 'fps_source': 'auto-detected'|'fallback-default',
             'duration': float}.
    """
    out = {"fps": 25.0, "fps_source": "fallback-default", "duration": 0.0}
    try:
        cmd = [
            ffprobe_bin, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate:format=duration",
            "-of", "json", path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=15)
        data = json.loads(r.stdout or "{}")
        streams = data.get("streams") or []
        if streams:
            rfr = streams[0].get("r_frame_rate", "")
            if "/" in rfr:
                num, den = rfr.split("/", 1)
                num, den = float(num), float(den)
                if den > 0:
                    out["fps"] = num / den
                    out["fps_source"] = "auto-detected"
        out["duration"] = float((data.get("format") or {}).get("duration", 0.0) or 0.0)
    except Exception:
        pass
    return out


def probe_audio_duration(path: str, ffprobe_bin: str = "ffprobe") -> float:
    """Single-purpose duration probe — used as a fallback when the source
    didn't carry container duration metadata but the extracted WAV does."""
    try:
        cmd = [
            ffprobe_bin, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=15)
        data = json.loads(r.stdout or "{}")
        return float((data.get("format") or {}).get("duration", 0.0) or 0.0)
    except Exception:
        return 0.0


def extract_audio_to_wav(
    src: str,
    dst_wav: str,
    ffmpeg_bin: str = "ffmpeg",
    check_cancel_cb: Optional[Callable[[], bool]] = None,
) -> None:
    """Extract 16k mono PCM WAV. Polls check_cancel_cb every 0.5s so the
    user's Stop button can kill long extracts on multi-GB sources."""
    cmd = [
        ffmpeg_bin, "-y", "-i", src,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        dst_wav,
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True:
        if p.poll() is not None:
            break
        if check_cancel_cb and check_cancel_cb():
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            raise Exception("使用者強制中止任務")
        time.sleep(0.5)
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd)


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_align_job(
    tasks: List[dict],
    dest_dir: str,
    model_size: str = "turbo",
    language: str = "zh",
    anchor_threshold: float = 0.4,
    subtitle_polish: bool = True,
    fps_override: Optional[float] = None,
    min_duration: float = 1.0,
    max_duration: float = 7.0,
    min_gap_frames: int = 2,
    hold_until_next: bool = True,
    encoding_bom: bool = True,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    check_cancel_cb: Optional[Callable[[], bool]] = None,
) -> dict:
    """Run forced alignment for one or more (video, transcript) pairs.

    tasks: list of dicts, each must have keys:
        - source: str (video file path)
        - transcript: str (raw text content; may be .txt or .srt body)
        - transcript_format: 'auto' | 'txt' | 'srt'

    Returns: {success, file_count, output_files, qualities, error?}
    """
    def _prog(p: float, m: str):
        if progress_callback:
            progress_callback(p, m)

    def _canceled() -> bool:
        return bool(check_cancel_cb and check_cancel_cb())

    try:
        import stable_whisper  # type: ignore
    except ImportError:
        return {"success": False, "error": "stable-ts 套件未安裝（pip install stable-ts）"}

    os.makedirs(dest_dir, exist_ok=True)
    project_root = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_bin = os.path.join(project_root, "ffmpeg.exe")
    ffprobe_bin = os.path.join(project_root, "ffprobe.exe")
    if not os.path.isfile(ffmpeg_bin):
        ffmpeg_bin = "ffmpeg"
    if not os.path.isfile(ffprobe_bin):
        ffprobe_bin = "ffprobe"

    # Detect device
    device, compute_type = _detect_device()
    models_dir = os.path.join(project_root, "models")
    os.makedirs(models_dir, exist_ok=True)

    _prog(3.0, f"啟動對齊引擎 (Model: {model_size})... 初次載入可能較久")
    try:
        model = _get_or_load_model(model_size, device, compute_type, models_dir)
    except Exception as e:
        return {"success": False, "error": f"模型載入失敗: {e}"}

    output_files = []
    qualities = []
    total = len(tasks)

    try:
        for ti, task in enumerate(tasks):
            if _canceled():
                raise Exception("使用者強制中止任務")

            src = task.get("source", "")
            transcript = task.get("transcript", "")
            tfmt = task.get("transcript_format", "auto")

            base = os.path.splitext(os.path.basename(src))[0]
            file_pct_start = (ti / total) * 100
            file_pct_end = ((ti + 1) / total) * 100

            _prog(file_pct_start + 1, f"[{ti+1}/{total}] 解析文字稿: {base}")
            segments = parse_transcript(transcript, tfmt)
            if not segments:
                _prog(file_pct_end, f"[{ti+1}/{total}] ⚠️ 文字稿為空，已跳過")
                continue

            full_text, char_metas = extract_char_metas(segments)
            if not char_metas:
                _prog(file_pct_end, f"[{ti+1}/{total}] ⚠️ 文字稿無有效字元，已跳過")
                continue

            # Probe video — single ffprobe call returns fps + duration
            meta = probe_video_meta(src, ffprobe_bin)
            fps, fps_source, audio_dur = meta["fps"], meta["fps_source"], meta["duration"]
            if fps_override and fps_override > 0:
                fps, fps_source = float(fps_override), "user-override"

            _prog(file_pct_start + 5, f"[{ti+1}/{total}] 提取音訊: {base}")
            tmp_wav = os.path.join(tempfile.gettempdir(), f"_align_{int(time.time()*1000)}_{ti}.wav")
            try:
                extract_audio_to_wav(src, tmp_wav, ffmpeg_bin, check_cancel_cb=check_cancel_cb)
            except Exception as e:
                _prog(file_pct_end, f"[{ti+1}/{total}] ⚠️ 無法提取音訊: {e}")
                continue

            try:
                if audio_dur <= 0.0:
                    audio_dur = probe_audio_duration(tmp_wav, ffprobe_bin) or 0.0

                _prog(file_pct_start + 15, f"[{ti+1}/{total}] 強制對齊中: {base}")
                # stable-ts align: known text + audio → word-level timing
                result = model.align(
                    tmp_wav, full_text,
                    language=language,
                )

                # Collect raw words
                raw_words = []
                try:
                    raw_words = list(result.all_words())
                except AttributeError:
                    # Fallback: walk segments → words
                    for seg in getattr(result, "segments", []):
                        for w in getattr(seg, "words", []) or []:
                            raw_words.append(w)

                if not raw_words:
                    _prog(file_pct_end, f"[{ti+1}/{total}] ⚠️ 對齊未產出任何字時間，已跳過")
                    continue

                _prog(file_pct_start + 70, f"[{ti+1}/{total}] 計算錨點與內插...")
                assign_raw_word_times(full_text, char_metas, raw_words)
                if audio_dur <= 0.0:
                    # last resort: use the max end time across raw words
                    audio_dur = max(
                        (float(_word_attr(w, "end", 0.0) or 0.0) for w in raw_words),
                        default=0.0,
                    )
                build_anchored_timeline(char_metas, audio_dur, anchor_threshold)
                cues = map_segments_to_cues(segments, char_metas)

                polish_stats = {}
                if subtitle_polish:
                    _prog(file_pct_start + 85, f"[{ti+1}/{total}] 字幕時間優化...")
                    polish_stats = polish_subtitle_timeline(
                        cues, fps=fps,
                        min_duration=min_duration,
                        max_duration=max_duration,
                        min_gap_frames=min_gap_frames,
                        audio_duration=audio_dur,
                        hold_until_next=hold_until_next,
                    )

                # Write SRT
                srt_path = os.path.join(dest_dir, f"{base}.srt")
                srt_bytes = cues_to_srt_bytes(cues, encoding_bom=encoding_bom)
                with open(srt_path, "wb") as f:
                    f.write(srt_bytes)
                output_files.append(srt_path)

                # Write quality report
                quality = build_quality_report(
                    cues, char_metas, fps, fps_source, audio_dur, polish_stats,
                )
                quality["video"] = src
                quality["srt"] = srt_path
                qualities.append(quality)
                qpath = os.path.join(dest_dir, f"{base}_quality.json")
                with open(qpath, "w", encoding="utf-8") as f:
                    json.dump(quality, f, ensure_ascii=False, indent=2)
                output_files.append(qpath)

                # Per-file summary log
                anc_pct = quality["alignment"]["anchor_ratio"] * 100
                intp_pct = quality["alignment"]["interpolated_ratio"] * 100
                edg_pct = quality["alignment"]["edge_fill_ratio"] * 100
                _prog(file_pct_end, (
                    f"[{ti+1}/{total}] ✅ {base}.srt — "
                    f"{len(cues)} 段 / 錨點 {anc_pct:.0f}% "
                    f"內插 {intp_pct:.0f}% 邊緣補 {edg_pct:.0f}%"
                ))
            finally:
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass

        _prog(100.0, f"全部 {total} 支影片對齊完成")
        return {
            "success": True,
            "dest_dir": dest_dir,
            "file_count": total,
            "output_files": output_files,
            "qualities": qualities,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        # Keep model in _MODEL_CACHE — only clean up transient GPU state.
        _cleanup_gpu()


