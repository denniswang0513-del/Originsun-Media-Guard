"""Queue management API — reorder, urgent marking."""
from fastapi import APIRouter, HTTPException  # type: ignore

from core.socket_mgr import sio  # type: ignore
import core.state as state  # type: ignore
from core.schemas import ReorderRequest  # type: ignore

router = APIRouter()


def _build_queue_list() -> list:
    """Build the queue payload: running (locked) + queued/waiting (sorted)."""
    items = []
    for j in state.get_all_jobs().values():
        if j.status == state.JobStatus.RUNNING:
            items.append({
                "job_id": j.job_id,
                "project_name": j.project_name,
                "task_type": j.task_type,
                "priority": j.priority,
                "urgent": j.urgent,
                "status": j.status.value,
            })
    queued = state.get_queued_jobs()
    for j in queued:
        items.append({
            "job_id": j.job_id,
            "project_name": j.project_name,
            "task_type": j.task_type,
            "priority": j.priority,
            "urgent": j.urgent,
            "status": j.status.value,
        })
    return items


@router.get("/api/v1/queue")
async def get_queue():
    return _build_queue_list()


@router.post("/api/v1/queue/reorder")
async def reorder_queue(body: ReorderRequest):
    state.reorder_jobs(body.ordered_job_ids)
    queue = _build_queue_list()
    await sio.emit('queue_updated', {'queue': queue})
    return queue


@router.post("/api/v1/queue/{job_id}/urgent")
async def mark_urgent(job_id: str):
    job = state.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    state.set_job_urgent(job_id)
    queue = _build_queue_list()
    await sio.emit('queue_updated', {'queue': queue})
    return queue


@router.delete("/api/v1/queue/{job_id}/urgent")
async def unmark_urgent(job_id: str):
    job = state.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    state.unset_job_urgent(job_id)
    queue = _build_queue_list()
    await sio.emit('queue_updated', {'queue': queue})
    return queue
