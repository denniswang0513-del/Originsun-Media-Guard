"""空拍排程監控 API — config CRUD + 立即掃描。"""

import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore

from core.schemas import DroneWatcherConfig  # type: ignore
from core import drone_watcher  # type: ignore

router = APIRouter()


def _compute_next_run(cfg: dict) -> str:
    """回傳下次執行時間的 ISO 字串。未啟用則回空字串。"""
    if not cfg.get("enabled"):
        return ""
    run_time = cfg.get("run_time", "02:00")
    try:
        hh, mm = run_time.split(":")
        hh, mm = int(hh), int(mm)
    except ValueError:
        return ""
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.isoformat(timespec="seconds")


@router.get("/api/v1/drone_watcher/config")
async def get_watcher_config():
    cfg = drone_watcher.load_config()
    if not cfg:
        # 回預設
        cfg = DroneWatcherConfig().model_dump()
    return {
        "config": cfg,
        "next_run": _compute_next_run(cfg),
        "history": drone_watcher.load_history()[:10],
    }


@router.post("/api/v1/drone_watcher/config")
async def save_watcher_config(cfg: DroneWatcherConfig):
    data = cfg.model_dump()
    drone_watcher.save_config(data)
    return {
        "status": "saved",
        "config": data,
        "next_run": _compute_next_run(data),
        "history": drone_watcher.load_history()[:10],
    }


@router.post("/api/v1/drone_watcher/run_now")
async def run_watcher_now():
    """立即執行一次掃描（測試用，不檢查時間）。"""
    try:
        result = await asyncio.to_thread(
            drone_watcher.run_watcher_scan, None, "manual"
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return result
