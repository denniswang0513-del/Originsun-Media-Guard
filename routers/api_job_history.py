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
    limit: int = 100,
    offset: int = 0,
):
    history = _load_history()
    if date:
        history = [e for e in history if _match_date(e, date)]
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
