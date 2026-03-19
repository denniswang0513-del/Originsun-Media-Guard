import os
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from core.schemas import VerifyRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs/verify")
async def create_verify_job(req: VerifyRequest):
    project_name = req.project_name or (
        os.path.basename(req.pairs[0][0]) if req.pairs else "unnamed"
    )
    job_id, warning = enqueue_job(req, project_name, "verify")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp
