"""
api_job_history.py — 任務歷史 API（DB 優先，JSON fallback）
"""
import os
import json
import threading
from fastapi import APIRouter, Body  # type: ignore
from typing import Optional
import core.state as state

router = APIRouter()

# ─── JSON Fallback ────────────────────────────────────────
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


def _match_date(entry: dict, date_str: str) -> bool:
    ts = entry.get("finished_at") or entry.get("created_at") or ""
    return ts[:10] == date_str


try:
    from db.json_fallback import get_machine_id as _get_machine_id  # noqa: E402
except ImportError:
    import socket as _socket
    def _get_machine_id() -> str:
        return _socket.gethostname()


# ─── Public API (called from worker.py) ──────────────────

def append_history(entry: dict):
    """Thread-safe append. Writes to DB if available, else JSON."""
    # Try DB (async from sync context)
    if state.db_online:
        try:
            import asyncio
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import job_history_repo

                async def _db_append():
                    async with factory() as session:
                        await job_history_repo.append(session, entry, _get_machine_id())
                        await session.commit()

                loop = state.get_main_loop()
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_db_append(), loop)
                    return
        except Exception:
            pass

    # Fallback to JSON
    with _file_lock:
        history = _load_history()
        job_id = entry.get("job_id")
        if job_id:
            for existing in history:
                if existing.get("job_id") == job_id:
                    return
        history.insert(0, entry)
        if len(history) > _MAX_ENTRIES:
            history = history[:_MAX_ENTRIES]
        _save_history(history)


# ─── Endpoints ────────────────────────────────────────────

@router.get("/api/v1/job_history")
async def get_job_history(
    date: Optional[str] = None,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import job_history_repo
                async with factory() as session:
                    return await job_history_repo.query(
                        session, date=date, task_type=task_type,
                        status=status, q=q, limit=limit, offset=offset,
                    )
        except Exception:
            pass

    # JSON fallback
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
    return {"jobs": subset, "total": len(history)}


@router.post("/api/v1/job_history")
async def post_job_history(entry: dict = Body(...)):
    append_history(entry)
    return {"status": "ok"}


_LOG_MAX_BYTES = 512 * 1024


@router.get("/api/v1/job_history/{job_id}/log")
async def get_job_log(job_id: str):
    log_file = None

    # Try DB first
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import job_history_repo
                async with factory() as session:
                    log_file = await job_history_repo.get_log_path(session, job_id)
        except Exception:
            pass

    # Fallback: search JSON
    if not log_file:
        history = _load_history()
        entry = next((e for e in history if e.get("job_id") == job_id), None)
        if not entry:
            return {"log": "", "error": "找不到此任務"}
        log_file = entry.get("log_file", "")

    if not log_file:
        return {"log": "", "error": "此任務沒有 Log 檔案"}

    resolved = os.path.realpath(log_file)
    if not resolved.endswith(".txt") or "_Log_" not in os.path.basename(resolved):
        return {"log": "", "error": "Log 路徑格式不符"}
    try:
        size = os.path.getsize(resolved)
        with open(resolved, "rb") as f:
            if size > _LOG_MAX_BYTES:
                f.seek(max(0, size - _LOG_MAX_BYTES))
                f.readline()
                raw = f.read()
                content = f"…（僅顯示最後 {_LOG_MAX_BYTES // 1024} KB）\n" + raw.decode("utf-8-sig", errors="replace")
            else:
                content = f.read().decode("utf-8-sig", errors="replace")
        return {"log": content}
    except Exception as exc:
        return {"log": "", "error": str(exc)}


@router.delete("/api/v1/job_history")
async def clear_job_history(date: Optional[str] = None):
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import job_history_repo
                async with factory() as session:
                    await job_history_repo.clear(session, date=date)
                    await session.commit()
                    return {"status": "cleared"}
        except Exception:
            pass

    # JSON fallback
    with _file_lock:
        if date:
            history = _load_history()
            history = [e for e in history if not _match_date(e, date)]
            _save_history(history)
        else:
            if os.path.exists(_HISTORY_FILE):
                os.remove(_HISTORY_FILE)
    return {"status": "cleared"}
