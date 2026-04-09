"""Drone Metadata Writer — scan files + submit job endpoints."""
import os
import io
import json
import base64
import subprocess
import asyncio
import tempfile
from typing import List
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from core.schemas import DroneMetaScanRequest, DroneMetaRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

HNO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

VIDEO_EXTS = {'.mp4', '.mov', '.MP4', '.MOV', '.mkv', '.avi', '.mts', '.m2ts'}


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


def _extract_filmstrip(filepath: str, duration: float, frames: int = 10, width: int = 160) -> List[str]:
    """Extract equally-spaced frames as individual base64 JPEGs."""
    if duration <= 0:
        return []
    interval = duration / (frames + 1)
    results = []

    def _extract_one(idx):
        t = interval * (idx + 1)
        try:
            cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-ss", str(t),
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

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(frames, 6)) as pool:
        results = list(pool.map(_extract_one, range(frames)))

    return results


def _scan_single_file(filepath: str) -> dict:
    """Probe + thumbnail + filmstrip + DJI detection for one video file."""
    info = _probe_file(filepath)
    thumb = _extract_thumbnail(filepath, seek=min(1.0, info["duration"] / 2) if info["duration"] > 0 else 0)
    filmstrip = _extract_filmstrip(filepath, info["duration"])

    result = {
        "path": filepath.replace("\\", "/"),
        "filename": os.path.basename(filepath),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "codec": info["codec"],
        "size": info["size"] or _safe_file_size(filepath),
        "thumbnail": thumb,
        "filmstrip": filmstrip,
        "creation_time": info.get("creation_time", ""),
        "is_dji": False,
        "dji_gps": None,
        "dji_camera": None,
    }

    # Detect DJI metadata
    try:
        from utils.dji_meta_parser import parse_dji_meta
        dji_data = parse_dji_meta(filepath)
        if dji_data.get("is_dji"):
            result["is_dji"] = True
            frames = dji_data.get("frames", [])
            if frames:
                first = frames[0]
                result["dji_gps"] = {
                    "lat": first.get("lat", 0),
                    "lon": first.get("lon", 0),
                    "alt": first.get("alt", 0),
                }
                result["dji_camera"] = {
                    "iso": first.get("iso", 0),
                    "shutter": first.get("shutter", 0),
                    "fnum": first.get("fnum", 0),
                }
    except Exception:
        pass

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
            return list(pool.map(_scan_single_file, video_files))

    results = await asyncio.to_thread(_scan_all)
    return {"status": "ok", "files": results}


@router.post("/api/v1/jobs/drone_meta")
async def create_drone_meta_job(req: DroneMetaRequest):
    """Submit a drone metadata write job (optionally with concat)."""
    project_name = req.project_name or "unnamed"
    job_id, warning = await enqueue_job(req, project_name, "drone_meta")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp
