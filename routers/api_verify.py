from fastapi import APIRouter, BackgroundTasks  # type: ignore
from core.schemas import VerifyRequest
import core.state as state  # type: ignore
from core.worker import _background_worker  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs/verify")
async def create_verify_job(req: VerifyRequest, bg_tasks: BackgroundTasks):
    state.task_queue.put(req)
    if not state.worker_busy:
        state.worker_busy = True
        bg_tasks.add_task(_background_worker)
    return {"status": "queued", "position": state.task_queue.qsize()}
