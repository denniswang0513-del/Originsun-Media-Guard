import os
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from core.schemas import BackupRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs")
async def create_backup_job(req: BackupRequest):
    try:
        job_id = enqueue_job(req, req.project_name, "backup")
        return {"status": "queued", "job_id": job_id}
    except ValueError as e:
        return JSONResponse(status_code=409, content={"status": "duplicate", "detail": str(e)})
