"""Repository for job_history table."""

from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import JobHistory


async def append(session: AsyncSession, entry: dict, machine_id: str) -> None:
    """Insert a job history entry (skip if job_id already exists)."""
    stmt = pg_insert(JobHistory).values(
        job_id=entry.get("job_id", ""),
        task_type=entry.get("task_type", ""),
        project_name=entry.get("project_name", ""),
        status=entry.get("status", ""),
        machine_id=machine_id,
        created_at=_parse_dt(entry.get("created_at")),
        started_at=_parse_dt(entry.get("started_at")),
        finished_at=_parse_dt(entry.get("finished_at")),
        error_detail=entry.get("error_detail"),
        log_file=entry.get("log_file", ""),
    ).on_conflict_do_nothing(index_elements=["job_id"])
    await session.execute(stmt)


async def query(
    session: AsyncSession,
    date: Optional[str] = None,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    machine_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """Query job history with filters. Returns {"jobs": [...], "total": int}."""
    stmt = select(JobHistory).order_by(JobHistory.finished_at.desc().nullslast())
    count_stmt = select(func.count()).select_from(JobHistory)

    # Apply filters
    conditions = []
    if date:
        conditions.append(func.to_char(JobHistory.finished_at, 'YYYY-MM-DD') == date)
    if task_type:
        conditions.append(JobHistory.task_type == task_type)
    if status:
        conditions.append(JobHistory.status == status)
    if q:
        conditions.append(JobHistory.project_name.ilike(f"%{q}%"))
    if machine_id:
        conditions.append(JobHistory.machine_id == machine_id)

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = (await session.execute(count_stmt)).scalar() or 0
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()

    return {
        "jobs": [_row_to_dict(r) for r in rows],
        "total": total,
    }


async def get_log_path(session: AsyncSession, job_id: str) -> Optional[str]:
    """Get log file path for a job."""
    row = (await session.execute(
        select(JobHistory.log_file).where(JobHistory.job_id == job_id)
    )).scalar()
    return row


async def clear(session: AsyncSession, date: Optional[str] = None, machine_id: Optional[str] = None) -> int:
    """Delete job history entries. Returns count deleted."""
    stmt = delete(JobHistory)
    if date:
        stmt = stmt.where(func.to_char(JobHistory.finished_at, 'YYYY-MM-DD') == date)
    if machine_id:
        stmt = stmt.where(JobHistory.machine_id == machine_id)
    result = await session.execute(stmt)
    return result.rowcount


# ── Helpers ──

def _parse_dt(val) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _row_to_dict(row: JobHistory) -> dict:
    return {
        "job_id": row.job_id,
        "task_type": row.task_type,
        "project_name": row.project_name,
        "status": row.status,
        "machine_id": row.machine_id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "finished_at": row.finished_at.isoformat() if row.finished_at else "",
        "error_detail": row.error_detail,
        "log_file": row.log_file or "",
    }
