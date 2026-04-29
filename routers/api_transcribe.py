import os
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from core.schemas import TranscribeRequest, AlignRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs/transcribe")
async def create_transcribe_job(req: TranscribeRequest):
    project_name = req.project_name or os.path.basename(req.dest_dir) or "unnamed"
    job_id, warning = await enqueue_job(req, project_name, "transcribe")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp


@router.post("/api/v1/jobs/align")
async def create_align_job(req: AlignRequest):
    """Force-align known transcripts to video audio → timed SRT files.

    See aligner.py for the underlying anchor + interpolation algorithm.
    Reuses the transcribe queue (single-slot AI inference).
    """
    if not req.tasks:
        return JSONResponse({"detail": "tasks 不能為空"}, status_code=400)
    for i, t in enumerate(req.tasks):
        if not t.source or not t.transcript.strip():
            return JSONResponse(
                {"detail": f"第 {i+1} 個任務缺少影片或文字稿"},
                status_code=400,
            )

    project_name = req.project_name or os.path.basename(req.dest_dir) or "align"
    # Use task_type='transcribe' for queue scheduling — shares the same
    # AI inference slot to prevent two GPU jobs running concurrently.
    job_id, warning = await enqueue_job(req, project_name, "transcribe")
    resp = {"status": "queued", "job_id": job_id, "mode": "align"}
    if warning:
        resp["warning"] = warning
    return resp
