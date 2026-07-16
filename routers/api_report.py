"""
api_report.py — 報表 API（DB 優先，JSON fallback）
Endpoints:
  POST   /api/v1/report_jobs           — 啟動報表任務
  GET    /api/v1/reports/history        — 列出報表歷史
  DELETE /api/v1/reports/{report_id}   — 刪除報表記錄
"""
import os
import json as _json

from fastapi import APIRouter, Request  # type: ignore

from core.schemas import ReportJobRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore
from config import load_settings
import core.state as state

router = APIRouter()


# ─── JSON Fallback Helpers ────────────────────────────────

def _get_report_index_path() -> str:
    web_dir = load_settings().get("nas_paths", {}).get("web_report_dir", "")
    if not web_dir:
        return ""
    return os.path.join(web_dir, "reports_index.json")


def _load_reports_json() -> dict:
    index_path = _get_report_index_path()
    if not index_path or not os.path.exists(index_path):
        return {"reports": []}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"reports": []}


def _save_reports_json(data: dict) -> None:
    index_path = _get_report_index_path()
    if not index_path:
        return
    with open(index_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Endpoints ────────────────────────────────────────────

@router.post("/api/v1/report_jobs")
async def start_report_job(req: ReportJobRequest):
    project_name = req.report_name or "report"
    # enqueue_job 回傳 (job_id, warning) tuple — 必須解包（與 api_backup/api_verify 等
    # 一致）。舊碼 `job_id = await enqueue_job(...)` 把整個 tuple 當 job_id 回傳，前端
    # 收到 ["id", null] → _myReportJobIds 比對永遠 miss → 報表跑完卻不會自動開啟、
    # 也吃不到重複任務警告（backup 內建報表走 asyncio.create_task 不經此路，故正常）。
    job_id, warning = await enqueue_job(req, project_name, "report")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp


@router.get("/api/v1/reports/history")
async def get_reports_history(output_dir: str = ""):
    # DB first
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import reports_repo
                async with factory() as session:
                    reports = await reports_repo.list_all(session)
                    return {"reports": reports, "source": "db"}
        except Exception:
            pass

    # JSON fallback
    data = _load_reports_json()
    if "error" not in data:
        data["source"] = "json"
    return data


@router.delete("/api/v1/reports/{report_id}")
async def delete_report_entry(request: Request, report_id: str, output_dir: str = ""):
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass
    # DB first
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import reports_repo
                async with factory() as session:
                    removed = await reports_repo.remove(session, report_id)
                    await session.commit()
                    if not removed:
                        return {"status": "error", "message": f"找不到報表記錄: {report_id}"}
                    return {"status": "ok"}
        except Exception:
            pass

    # JSON fallback
    index_path = _get_report_index_path()
    if not index_path or not os.path.exists(index_path):
        return {"status": "error", "message": "nas_paths.web_report_dir not configured or index not found"}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        data["reports"] = [r for r in data.get("reports", []) if r.get("id") != report_id]
        with open(index_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
