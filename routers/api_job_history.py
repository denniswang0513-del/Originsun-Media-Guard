import os
import json
import threading
from fastapi import APIRouter, Body  # type: ignore
from typing import Optional

router = APIRouter()

_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "job_history.json"
)
_MAX_ENTRIES = 200
_file_lock = threading.Lock()


def _load_history() -> list:
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history: list):
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_history(entry: dict):
    """Called from worker.py after each job completes. Thread-safe."""
    with _file_lock:
        history = _load_history()
        # Deduplicate by job_id
        job_id = entry.get("job_id")
        if job_id:
            for existing in history:
                if existing.get("job_id") == job_id:
                    return  # already recorded
        history.insert(0, entry)
        if len(history) > _MAX_ENTRIES:
            history = history[:_MAX_ENTRIES]
        _save_history(history)


def _match_date(entry: dict, date_str: str) -> bool:
    """Check if entry's finished_at (or created_at) starts with YYYY-MM-DD."""
    ts = entry.get("finished_at") or entry.get("created_at") or ""
    return ts[:10] == date_str


@router.get("/api/v1/job_history")
async def get_job_history(
    date: Optional[str] = None,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    history = _load_history()
    if date:
        history = [e for e in history if _match_date(e, date)]
    if task_type:
        history = [e for e in history if e.get("task_type") == task_type]
    if status:
        history = [e for e in history if e.get("status") == status]
    if q:
        q_lower = q.lower()
        history = [e for e in history if q_lower in (e.get("project_name") or "").lower()]
    subset = history[offset:offset + limit]
    return {
        "jobs": subset,
        "total": len(history),
    }


@router.post("/api/v1/job_history")
async def post_job_history(entry: dict = Body(...)):
    """Add a history entry from the frontend. Deduplicates by job_id."""
    append_history(entry)
    return {"status": "ok"}


_LOG_MAX_BYTES = 512 * 1024  # 512 KB cap to avoid sending huge logs


@router.get("/api/v1/job_history/{job_id}/log")
async def get_job_log(job_id: str):
    """Return the log file content for a completed job."""
    history = _load_history()
    entry = next((e for e in history if e.get("job_id") == job_id), None)
    if not entry:
        return {"log": "", "error": "找不到此任務"}
    log_file = entry.get("log_file", "")
    if not log_file:
        return {"log": "", "error": "此任務沒有 Log 檔案"}
    # Validate: only allow paths written by our own worker (log_file comes from job_history.json)
    # Basic sanity: must end with _Log_ pattern and .txt extension
    resolved = os.path.realpath(log_file)
    if not resolved.endswith(".txt") or "_Log_" not in os.path.basename(resolved):
        return {"log": "", "error": "Log 路徑格式不符"}
    try:
        size = os.path.getsize(resolved)
        with open(resolved, "rb") as f:
            if size > _LOG_MAX_BYTES:
                f.seek(max(0, size - _LOG_MAX_BYTES))
                f.readline()  # skip partial line at boundary
                raw = f.read()
                content = f"…（僅顯示最後 {_LOG_MAX_BYTES // 1024} KB）\n" + raw.decode("utf-8-sig", errors="replace")
            else:
                content = f.read().decode("utf-8-sig", errors="replace")
        return {"log": content}
    except Exception as exc:
        return {"log": "", "error": str(exc)}


@router.delete("/api/v1/job_history")
async def clear_job_history(date: Optional[str] = None):
    with _file_lock:
        if date:
            history = _load_history()
            history = [e for e in history if not _match_date(e, date)]
            _save_history(history)
        else:
            if os.path.exists(_HISTORY_FILE):
                os.remove(_HISTORY_FILE)
    return {"status": "cleared"}
