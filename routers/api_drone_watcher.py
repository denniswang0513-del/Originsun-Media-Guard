"""空拍排程監控 API — config CRUD + 立即掃描 + 取消全部空拍任務。"""

import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore

from core.schemas import DroneWatcherConfig  # type: ignore
from core import drone_watcher  # type: ignore
import core.state as state  # type: ignore

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


@router.post("/api/v1/drone_watcher/cancel_all")
async def cancel_all_drone_jobs():
    """Cancel every drone_meta job — queued ones are removed, the running one
    gets engine.request_stop(). Used by the watcher panel's 取消全部 button.
    """
    cancelled_queued = 0
    stopped_running = 0
    for j in list(state.get_all_jobs().values()):
        if j.task_type != "drone_meta":
            continue
        if j.status in (state.JobStatus.QUEUED, state.JobStatus.WAITING):
            state.remove_job(j.job_id)
            cancelled_queued += 1
        elif j.status in (state.JobStatus.RUNNING, state.JobStatus.PAUSED):
            if j.engine:
                j.engine.request_stop()
            if j.asyncio_task and not j.asyncio_task.done():
                j.asyncio_task.cancel()
            j.status = state.JobStatus.CANCELLED
            stopped_running += 1
    return {"cancelled_queued": cancelled_queued, "stopped_running": stopped_running}


@router.post("/api/v1/drone_watcher/run_now")
async def run_watcher_now(cfg: DroneWatcherConfig):
    """立即執行一次掃描（測試用，不檢查時間、不寫入儲存設定）。

    使用請求 body 內的 config 值，避免「UI 有填但還沒按儲存」的使用者困惑。
    """
    data = cfg.model_dump()
    if not data.get("source_root") or not data.get("dest_root"):
        return JSONResponse(
            {"error": "來源或目的地根目錄未設定"}, status_code=400
        )
    try:
        result = await asyncio.to_thread(
            drone_watcher.run_watcher_scan, data, "manual"
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return result
