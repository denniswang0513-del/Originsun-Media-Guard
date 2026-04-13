"""Drone Metadata Writer — scan files + submit job endpoints."""
import os
import io
import json
import base64
import hashlib
import subprocess
import time
import asyncio
import tempfile
from collections import OrderedDict
from threading import Lock
from typing import List, Optional
from fastapi import APIRouter, Query, Request  # type: ignore
from fastapi.responses import JSONResponse, Response, StreamingResponse  # type: ignore
from starlette.background import BackgroundTasks  # type: ignore
from core.schemas import DroneMetaScanRequest, DroneMetaRequest, TimelineExportRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

HNO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG_BIN = os.path.join(_PROJECT_ROOT, "ffmpeg.exe")

VIDEO_EXTS = {'.mp4', '.mov', '.MP4', '.MOV', '.mkv', '.avi', '.mts', '.m2ts'}

def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('utf-8')}"


# ── Filmstrip caches ──

_FILMSTRIP_CACHE_DIR = os.path.join(_PROJECT_ROOT, "cache", "filmstrips")
_FILMSTRIP_CACHE_VERSION = "v2"  # bump on format changes to invalidate old entries
_FILMSTRIP_CACHE_MAX_AGE_DAYS = 30
_FILMSTRIP_MEM_CACHE: "OrderedDict[str, list]" = OrderedDict()
_FILMSTRIP_MEM_LOCK = Lock()
_FILMSTRIP_MEM_MAX = 500

_WEBP_SUPPORTED: Optional[bool] = None


def _split_webp_stream(data: bytes) -> List[bytes]:
    """WebP concatenated stream: each frame is 'RIFF' + uint32 size + 'WEBP' + payload."""
    results = []
    i = 0
    while i < len(data) - 12:
        if data[i:i + 4] != b"RIFF":
            nxt = data.find(b"RIFF", i + 1)
            if nxt < 0:
                break
            i = nxt
            continue
        sz = int.from_bytes(data[i + 4:i + 8], "little")
        total = sz + 8
        if i + total > len(data):
            break
        results.append(data[i:i + total])
        i += total
    return results


def _split_jpeg_stream(data: bytes) -> List[bytes]:
    """MJPEG concatenated stream: SOI (0xFFD8) ... EOI (0xFFD9) per frame."""
    results = []
    i = 0
    while True:
        start = data.find(b"\xff\xd8", i)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start)
        if end < 0:
            break
        results.append(data[start:end + 2])
        i = end + 2
    return results


# Codec dispatch: (vcodec, extra_args, mime, split_fn)
_WEBP_CFG = ("libwebp", ["-quality", "70"], "image/webp", _split_webp_stream)
_JPEG_CFG = ("mjpeg", ["-q:v", "5"], "image/jpeg", _split_jpeg_stream)


def _ffmpeg_has_webp() -> bool:
    """Probe ffmpeg once for libwebp encoder availability."""
    global _WEBP_SUPPORTED
    if _WEBP_SUPPORTED is not None:
        return _WEBP_SUPPORTED
    try:
        res = subprocess.run(
            [_FFMPEG_BIN, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, creationflags=HNO_WINDOW, text=True,
        )
        _WEBP_SUPPORTED = "libwebp" in (res.stdout or "")
    except Exception:
        _WEBP_SUPPORTED = False
    return _WEBP_SUPPORTED


def _filmstrip_cache_key(filepath: str, frames: int, width: int) -> Optional[str]:
    try:
        mtime = int(os.path.getmtime(filepath))
        size = os.path.getsize(filepath)
    except OSError:
        return None
    raw = f"{filepath}|{mtime}|{size}|{frames}|{width}|{_FILMSTRIP_CACHE_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _filmstrip_disk_path(key: str) -> str:
    return os.path.join(_FILMSTRIP_CACHE_DIR, f"{key}.json")


def _filmstrip_mem_get(key: str):
    with _FILMSTRIP_MEM_LOCK:
        val = _FILMSTRIP_MEM_CACHE.get(key)
        if val is not None:
            _FILMSTRIP_MEM_CACHE.move_to_end(key)
        return val


def _filmstrip_mem_put(key: str, val: list) -> None:
    with _FILMSTRIP_MEM_LOCK:
        _FILMSTRIP_MEM_CACHE[key] = val
        _FILMSTRIP_MEM_CACHE.move_to_end(key)
        while len(_FILMSTRIP_MEM_CACHE) > _FILMSTRIP_MEM_MAX:
            _FILMSTRIP_MEM_CACHE.popitem(last=False)


def _filmstrip_load_disk(key: str):
    try:
        with open(_filmstrip_disk_path(key), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[filmstrip cache] load {key} failed: {e}")
        return None


def _filmstrip_save_disk(key: str, data: list) -> None:
    try:
        os.makedirs(_FILMSTRIP_CACHE_DIR, exist_ok=True)
        with open(_filmstrip_disk_path(key), "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
    except Exception as e:
        print(f"[filmstrip cache] save {key} failed: {e}")


def _filmstrip_prune_disk(max_age_days: int = _FILMSTRIP_CACHE_MAX_AGE_DAYS) -> None:
    """Best-effort eviction: delete cache files not accessed in max_age_days."""
    try:
        if not os.path.isdir(_FILMSTRIP_CACHE_DIR):
            return
        cutoff = time.time() - max_age_days * 86400
        for name in os.listdir(_FILMSTRIP_CACHE_DIR):
            fp = os.path.join(_FILMSTRIP_CACHE_DIR, name)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
            except OSError:
                pass
    except Exception:
        pass


# Best-effort prune on module import (non-blocking)
from threading import Thread as _T
_T(target=_filmstrip_prune_disk, daemon=True).start()


def _probe_file(filepath: str) -> dict:
    """Use ffprobe to get video metadata for a single file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name:format=duration,size:format_tags=creation_time",
        "-of", "json", filepath
    ]
    try:
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=15, creationflags=HNO_WINDOW,
        )
        data = json.loads(res.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})
        format_tags = fmt.get("tags", {})
        codec = stream.get("codec_name", "")
        width = stream.get("width", 0)
        height = stream.get("height", 0)
        try:
            duration = float(fmt.get("duration", 0) or 0)
        except Exception:
            duration = 0.0
        try:
            size = int(fmt.get("size", 0) or 0)
        except Exception:
            size = 0
        return {
            "codec": codec, "width": width, "height": height,
            "duration": duration, "size": size,
            "creation_time": format_tags.get("creation_time", ""),
        }
    except Exception:
        return {"codec": "", "width": 0, "height": 0, "duration": 0.0, "size": 0, "creation_time": ""}


def _extract_thumbnail(filepath: str, seek: float = 1.0, width: int = 240) -> str:
    """Extract a single frame as base64 JPEG."""
    try:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-ss", str(seek),
            "-i", filepath,
            "-vf", f"scale={width}:-1",
            "-vframes", "1",
            "-f", "image2pipe", "-vcodec", "mjpeg",
            "pipe:1",
        ]
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, creationflags=HNO_WINDOW,
        )
        if res.returncode == 0 and res.stdout:
            b64 = base64.b64encode(res.stdout).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"
    except Exception:
        pass
    return ""


def _extract_filmstrip(filepath: str, duration: float, frames: int = 10, width: int = 480) -> List[str]:
    """Extract equally-spaced frames as base64 data URLs.

    Strategy: parallel seek-then-extract (one subprocess per frame). `-ss` BEFORE `-i`
    triggers ffmpeg's fast input seek (jumps to nearest keyframe without decoding
    intervening frames). This beats a single-subprocess `fps` filter for long videos
    (e.g. 4K 30-min clips) by orders of magnitude because `fps` must decode the full
    stream end-to-end.

    Caches: memory (LRU) → disk → ffmpeg.
    Codec: WebP when available (~43% smaller), else MJPEG.
    """
    if duration <= 0 or frames <= 0:
        return []

    key = _filmstrip_cache_key(filepath, frames, width)
    if key:
        cached = _filmstrip_mem_get(key)
        if cached is not None:
            return cached
        cached = _filmstrip_load_disk(key)
        if cached is not None:
            _filmstrip_mem_put(key, cached)
            return cached

    vcodec, extra_args, mime, _split_fn = _WEBP_CFG if _ffmpeg_has_webp() else _JPEG_CFG

    def _extract_one(idx: int) -> Optional[bytes]:
        t = duration * (idx + 0.5) / frames  # center of each segment
        # `-ss TIME` BEFORE `-i` triggers fast input seek (jumps to nearest keyframe
        # without decoding intervening frames) — orders of magnitude faster than
        # decoding from start, especially for long 4K clips.
        cmd = [
            _FFMPEG_BIN, "-y", "-nostdin",
            "-ss", f"{t:.3f}", "-i", filepath,
            "-vf", f"scale={width}:-2",
            "-frames:v", "1",
            *extra_args,
            "-f", "image2pipe", "-vcodec", vcodec,
            "pipe:1",
        ]
        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=15, creationflags=HNO_WINDOW,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except Exception:
            pass
        return None

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(frames, 8)) as pool:
        frame_bytes = list(pool.map(_extract_one, range(frames)))

    results = [_data_url(mime, b) for b in frame_bytes if b]

    if key and results:
        _filmstrip_mem_put(key, results)
        _filmstrip_save_disk(key, results)
    return results


def _probe_only(filepath: str) -> dict:
    """Probe metadata + DJI detection only (NO thumbnail, NO filmstrip — ~80ms)."""
    info = _probe_file(filepath)
    result = {
        "path": filepath.replace("\\", "/"),
        "filename": os.path.basename(filepath),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "codec": info["codec"],
        "size": info["size"] or _safe_file_size(filepath),
        "thumbnail": "",
        "filmstrip": [],
        "creation_time": info.get("creation_time", ""),
        "is_dji": False,
        "dji_gps": None,
        "dji_camera": None,
    }
    try:
        from utils.dji_meta_parser import parse_dji_meta
        dji_data = parse_dji_meta(filepath)
        if dji_data.get("is_dji"):
            result["is_dji"] = True
            frames = dji_data.get("frames", [])
            if frames:
                first = frames[0]
                result["dji_gps"] = {"lat": first.get("lat", 0), "lon": first.get("lon", 0), "alt": first.get("alt", 0)}
                result["dji_camera"] = {"iso": first.get("iso", 0), "shutter": first.get("shutter", 0), "fnum": first.get("fnum", 0)}
    except Exception:
        pass
    return result


def _scan_single_file_quick(filepath: str) -> dict:
    """Probe + thumbnail + DJI detection (NO filmstrip)."""
    result = _probe_only(filepath)
    result["thumbnail"] = _extract_thumbnail(
        filepath, seek=min(1.0, result["duration"] / 2) if result["duration"] > 0 else 0
    )
    return result


def _safe_file_size(filepath: str) -> int:
    try:
        return os.path.getsize(filepath)
    except Exception:
        return 0


def _collect_video_files(paths: List[str]) -> List[str]:
    """Given a list of files/folders, return all video files sorted."""
    files = []
    for p in paths:
        p = p.strip()
        if not p:
            continue
        if os.path.isfile(p):
            if os.path.splitext(p)[1] in VIDEO_EXTS:
                files.append(p)
        elif os.path.isdir(p):
            for entry in sorted(os.scandir(p), key=lambda e: e.name.lower()):
                if entry.is_file() and os.path.splitext(entry.name)[1] in VIDEO_EXTS:
                    files.append(entry.path)
    return files


@router.post("/api/v1/drone_meta/scan")
async def scan_drone_meta_files(req: DroneMetaScanRequest):
    """Scan paths for video files, return metadata + thumbnails + filmstrip."""
    video_files = await asyncio.to_thread(_collect_video_files, req.paths)
    if not video_files:
        return {"status": "ok", "files": [], "message": "找不到影片檔案"}

    import concurrent.futures

    def _scan_all():
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(_scan_single_file_quick, video_files))

    results = await asyncio.to_thread(_scan_all)
    return {"status": "ok", "files": results}


@router.post("/api/v1/drone_meta/rescan_file")
async def rescan_single_file(path: str = Query(...)):
    """Re-probe a single file (metadata + thumbnail + DJI). Used by card refresh button."""
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        return JSONResponse({"error": "file not found"}, status_code=404)
    data = await asyncio.to_thread(_scan_single_file_quick, real)
    return data


@router.post("/api/v1/drone_meta/scan_stream")
async def scan_drone_meta_stream(req: DroneMetaScanRequest):
    """SSE stream: two-phase scan — metadata first (fast), thumbnails second (parallel)."""
    video_files = await asyncio.to_thread(_collect_video_files, req.paths)

    async def _generate():
        import concurrent.futures
        total = len(video_files)
        yield f"data: {json.dumps({'event': 'start', 'total': total})}\n\n"
        if total == 0:
            yield f"data: {json.dumps({'event': 'done'})}\n\n"
            return

        # ── Phase 1: filename + size only (pure filesystem, instant) ──
        file_sizes = []
        for i, fp in enumerate(video_files):
            sz = _safe_file_size(fp)
            file_sizes.append(sz)
            data = {
                "path": fp.replace("\\", "/"),
                "filename": os.path.basename(fp),
                "size": sz,
            }
            yield f"data: {json.dumps({'event': 'file', 'index': i, 'data': data}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'event': 'files_done'})}\n\n"

        # ── Phase 2: thumbnails (parallel 4 workers, ~60ms each) ──
        # Thumbnails come before metadata so the user sees images quickly.
        # We don't know durations yet, so seek to 1s (safe default).
        queue = asyncio.Queue()

        def _thumb_worker(idx, fp):
            return idx, _extract_thumbnail(fp, seek=1.0)

        async def _run_thumb_pool():
            def _do():
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    futs = {pool.submit(_thumb_worker, i, fp): i
                            for i, fp in enumerate(video_files)}
                    for fut in concurrent.futures.as_completed(futs):
                        idx, thumb = fut.result()
                        asyncio.run_coroutine_threadsafe(queue.put((idx, thumb)), _loop)
                asyncio.run_coroutine_threadsafe(queue.put(None), _loop)
            await asyncio.to_thread(_do)

        _loop = asyncio.get_event_loop()
        asyncio.ensure_future(_run_thumb_pool())

        while True:
            item = await queue.get()
            if item is None:
                break
            idx, thumb = item
            if thumb:
                yield f"data: {json.dumps({'event': 'thumb', 'index': idx, 'thumbnail': thumb})}\n\n"

        yield f"data: {json.dumps({'event': 'thumbs_done'})}\n\n"

        # ── Phase 3: ffprobe + DJI metadata (each ~80ms) ──
        for i, fp in enumerate(video_files):
            result = await asyncio.to_thread(_probe_only, fp)
            yield f"data: {json.dumps({'event': 'detail', 'index': i, 'data': result}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'event': 'done'})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.get("/api/v1/drone_meta/filmstrip")
async def get_filmstrip(
    path: str = Query(...),
    frames: int = Query(10),
):
    """Lazy-load filmstrip for a single file (called when user opens editor)."""
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        return JSONResponse({"error": "File not found"}, status_code=404)
    info = await asyncio.to_thread(_probe_file, real)
    filmstrip = await asyncio.to_thread(_extract_filmstrip, real, info["duration"], frames)
    return {"filmstrip": filmstrip}


@router.post("/api/v1/jobs/drone_meta")
async def create_drone_meta_job(req: DroneMetaRequest):
    """Submit a drone metadata write job (optionally with concat)."""
    project_name = req.project_name or "unnamed"
    job_id, warning = await enqueue_job(req, project_name, "drone_meta")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp


@router.get("/api/v1/drone_meta/frame")
async def get_frame(
    request: Request,
    path: str = Query(...),
    time: float = Query(0.0),
    brightness: float = Query(0.0),
    contrast: float = Query(1.0),
    saturation: float = Query(1.0),
    color_temp: float = Query(0.0),
    width: int = Query(640),
):
    """Extract a single frame with optional color grading, returned as JPEG."""
    from core.auth import check_admin
    try:
        check_admin(request)
    except Exception:
        return JSONResponse({"error": "未授權"}, status_code=401)
    # Validate path is a real file (no traversal tricks)
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        return JSONResponse({"error": "File not found"}, status_code=404)
    path = real

    # Build video filter chain
    vf_parts = [f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"]
    if color_temp != 0.0:
        t = color_temp
        vf_parts.append(f"colorbalance=rs={t}:gs=0:bs={-t}")
    vf_parts.append(f"scale={width}:-2")
    vf_str = ",".join(vf_parts)

    cmd = [
        _FFMPEG_BIN, "-y", "-nostdin",
        "-ss", str(time),
        "-i", path,
        "-vf", vf_str,
        "-frames:v", "1",
        "-f", "image2pipe", "-vcodec", "mjpeg",
        "pipe:1",
    ]

    def _run():
        return subprocess.run(
            cmd, capture_output=True, timeout=15,
            creationflags=HNO_WINDOW,
        )

    result = await asyncio.to_thread(_run)
    if result.returncode != 0 or not result.stdout:
        err = (result.stderr or b"")[:300].decode("utf-8", errors="ignore")
        return JSONResponse({"error": f"FFmpeg failed: {err}"}, status_code=500)

    return Response(content=result.stdout, media_type="image/jpeg")


@router.post("/api/v1/jobs/drone_timeline_export")
async def submit_timeline_export(req: TimelineExportRequest):
    """Submit a timeline export job to the task queue."""
    project_name = req.output_name or "timeline_export"
    job_id, warning = await enqueue_job(req, project_name, "timeline_export")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp
