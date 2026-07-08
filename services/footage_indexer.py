"""
footage_indexer.py — B5 內部素材庫掃描器（docs/BIZ_PLAN.md B5 段）

walk 資料夾找影片 → ffprobe 抽 metadata → 找同名 .txt/.srt 讀逐字稿 →
回 records list（純同步，router 用 asyncio.to_thread 包後在 async 層批次 upsert）。

設計原則：ffprobe / 讀檔失敗都容錯（該檔仍入索引、欄位留空），單次上限防暴走。
DB 寫入不在這層做（thread 裡碰 async session 會出事）。
"""

import json
import os
import re
import subprocess

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mkv", ".avi", ".mts", ".m2ts"}
_MAX_FILES = 2000
_TRANSCRIPT_MAX = 200_000   # 單檔逐字稿存 200KB 上限，防超長 SRT 撐爆列

# 專案根目錄的 ffprobe.exe（隨附二進制）
_FFPROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ffprobe.exe")

# SRT 序號行（純數字）與時間軸行（00:00:01,000 --> 00:00:03,000）
_SRT_INDEX_RE = re.compile(r"^\d+\s*$")
_SRT_TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->")


def extract_srt_text(raw: str) -> str:
    """SRT → 純文字：去掉序號行與時間軸行，只留字幕內容，合併成連續段落。
    純函式（可測）；.txt 逐字稿直接原樣回。"""
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if _SRT_INDEX_RE.match(s) or _SRT_TIME_RE.search(s):
            continue
        lines.append(s)
    return " ".join(lines).strip()


def _read_transcript(video_path: str) -> str:
    """找同 stem 的 .txt 或 .srt（同資料夾），讀成純文字。找不到回空字串。"""
    stem = os.path.splitext(video_path)[0]
    for ext in (".txt", ".srt"):
        cand = stem + ext
        if os.path.isfile(cand):
            try:
                # utf-8-sig：Whisper/記事本存的逐字稿常帶 BOM，一併吃掉
                with open(cand, "r", encoding="utf-8-sig", errors="replace") as f:
                    raw = f.read(_TRANSCRIPT_MAX + 1)
                text = extract_srt_text(raw) if ext == ".srt" else raw.strip()
                return text[:_TRANSCRIPT_MAX]
            except Exception:
                return ""
    return ""


def _probe(video_path: str) -> dict:
    """ffprobe 抽 duration/resolution/fps。失敗回空 dict（該檔仍入索引）。"""
    if not os.path.isfile(_FFPROBE):
        return {}
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video_path],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0 or not out.stdout:
            return {}
        data = json.loads(out.stdout)
    except Exception:
        return {}

    meta = {}
    fmt = data.get("format") or {}
    try:
        if fmt.get("duration"):
            meta["duration_sec"] = round(float(fmt["duration"]), 1)
    except (ValueError, TypeError):
        pass
    for st in (data.get("streams") or []):
        if st.get("codec_type") == "video":
            w, h = st.get("width"), st.get("height")
            if w and h:
                meta["resolution"] = f"{w}x{h}"
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or ""
            if "/" in rate:
                try:
                    num, den = rate.split("/")
                    if float(den) > 0:
                        meta["fps"] = str(round(float(num) / float(den), 2)).rstrip("0").rstrip(".")
                except (ValueError, ZeroDivisionError):
                    pass
            break
    return meta


def collect_folder(root_path: str, project_name: str = "") -> dict:
    """walk 影片 → 逐檔 probe + 讀逐字稿 → 回 {records, scanned, errors, truncated}。
    純同步（在 thread 跑）。records 交給 router 批次 upsert。"""
    records = []
    scanned = errors = 0
    truncated = False
    folder_name = project_name or os.path.basename(os.path.normpath(root_path))

    for dirpath, _dirs, files in os.walk(root_path):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            if scanned >= _MAX_FILES:
                truncated = True
                break
            scanned += 1
            fpath = os.path.join(dirpath, fname)
            try:
                stat = os.stat(fpath)
                rec = {
                    "file_path": os.path.abspath(fpath),
                    "file_name": fname,
                    "ext": ext,
                    "size_bytes": stat.st_size,
                    "shot_mtime": stat.st_mtime,
                    "project_name": folder_name,
                    "transcript": _read_transcript(fpath),
                }
                rec.update(_probe(fpath))
                records.append(rec)
            except Exception:
                errors += 1
        if truncated:
            break

    return {"records": records, "scanned": scanned, "errors": errors, "truncated": truncated}
