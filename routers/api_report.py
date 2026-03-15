from fastapi import APIRouter
from core.schemas import ReportJobRequest
import core.state as state
import os
import asyncio
from core.report_job import _run_report_job

router = APIRouter()

@router.post("/api/v1/report_jobs")
async def start_report_job(req: ReportJobRequest):
    state._report_pause_event.set()
    state._current_report_task = asyncio.create_task(_run_report_job(req))
    return {"status": "queued"}

@router.get("/api/v1/reports/history")
async def get_reports_history(output_dir: str = ""):
    index_path = r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport\reports_index.json"
    if not os.path.exists(index_path):
        return {"reports": []}
    try:
        import json as _j
        with open(index_path, "r", encoding="utf-8") as f:
            return _j.load(f)
    except Exception as e:
        return {"reports": [], "error": str(e)}

@router.delete("/api/v1/reports/{report_id}")
async def delete_report_entry(report_id: str, output_dir: str = ""):
    import json as _j
    index_path = r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport\reports_index.json"
    if not os.path.exists(index_path):
        return {"status": "error", "message": "NAS index not found"}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = _j.load(f)
        data["reports"] = [r for r in data.get("reports", []) if r.get("id") != report_id]
        with open(index_path, "w", encoding="utf-8") as f:
            _j.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
