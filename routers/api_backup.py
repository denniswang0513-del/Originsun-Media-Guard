from fastapi import APIRouter  # type: ignore
from core.schemas import BackupRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()


@router.post("/api/v1/jobs")
async def create_backup_job(req: BackupRequest):
    # 磁碟代號→UNC 翻譯在 enqueue_job 中央咽喉統一做（core/drive_map），
    # 排程/書籤重放同樣涵蓋；翻譯明細由 worker 以 [UNC] log 推到終端。
    job_id, warning = await enqueue_job(req, req.project_name, "backup")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp
