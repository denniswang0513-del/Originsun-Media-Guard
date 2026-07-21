from fastapi import APIRouter  # type: ignore
from core.schemas import BackupRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()


@router.post("/api/v1/jobs")
async def create_backup_job(req: BackupRequest):
    # 磁碟代號 → UNC 翻譯（core/drive_map）：任務可能派在沒掛 T:/S:/V: 的機器
    # 或非互動 session 執行 — 進件就翻成 UNC，徹底不依賴磁碟對映。
    # 明細回給前端顯示；不在對應表的字母（本機實體碟）原樣通過。
    from core.drive_map import effective_map, translate_path
    mapping = effective_map()
    changes: list = []

    def tr(v: str) -> str:
        nv = translate_path(v, mapping)
        if nv != v:
            changes.append(f"{v} → {nv}")
        return nv

    req.local_root = tr(req.local_root)
    req.nas_root = tr(req.nas_root)
    req.proxy_root = tr(req.proxy_root)
    req.report_output = tr(req.report_output)
    req.cards = [(name, tr(path)) for name, path in req.cards]

    job_id, warning = await enqueue_job(req, req.project_name, "backup")
    resp = {"status": "queued", "job_id": job_id}
    if changes:
        resp["path_translations"] = changes
    if warning:
        resp["warning"] = warning
    return resp
