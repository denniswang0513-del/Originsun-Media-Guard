import os
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from core.schemas import ConcatRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs/concat")
async def create_concat_job(req: ConcatRequest):
    project_name = req.project_name or os.path.basename(req.dest_dir) or "unnamed"
    job_id, warning = enqueue_job(req, project_name, "concat")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp
