from fastapi import APIRouter  # type: ignore
from core.schemas import BackupRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs")
async def create_backup_job(req: BackupRequest):
    job_id, warning = await enqueue_job(req, req.project_name, "backup")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp
