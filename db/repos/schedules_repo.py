"""Repository for scheduled_jobs table."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ScheduledJob


async def list_all(session: AsyncSession, machine_id: Optional[str] = None) -> List[dict]:
    stmt = select(ScheduledJob).order_by(ScheduledJob.created_at.desc())
    if machine_id:
        stmt = stmt.where(ScheduledJob.machine_id == machine_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


async def create(session: AsyncSession, **kwargs) -> dict:
    sj = ScheduledJob(**kwargs)
    session.add(sj)
    await session.flush()
    return _to_dict(sj)


async def update_schedule(session: AsyncSession, schedule_id: str, **kwargs) -> Optional[dict]:
    row = (await session.execute(
        select(ScheduledJob).where(ScheduledJob.schedule_id == schedule_id)
    )).scalar()
    if not row:
        return None
    for k, v in kwargs.items():
        if v is not None and hasattr(row, k):
            setattr(row, k, v)
    await session.flush()
    return _to_dict(row)


async def delete_schedule(session: AsyncSession, schedule_id: str) -> bool:
    result = await session.execute(
        delete(ScheduledJob).where(ScheduledJob.schedule_id == schedule_id)
    )
    return result.rowcount > 0


async def get_due(session: AsyncSession, machine_id: str, now: datetime) -> List[dict]:
    """Get enabled schedules whose next_run <= now for this machine."""
    stmt = select(ScheduledJob).where(
        ScheduledJob.machine_id == machine_id,
        ScheduledJob.enabled == True,  # noqa: E712
        ScheduledJob.next_run <= now,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


async def mark_fired(session: AsyncSession, schedule_id: str,
                     last_run: datetime, next_run: Optional[datetime],
                     enabled: bool = True) -> None:
    """Update schedule after firing."""
    await session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.schedule_id == schedule_id)
        .values(last_run=last_run, next_run=next_run, enabled=enabled)
    )


def _to_dict(r: ScheduledJob) -> dict:
    return {
        "schedule_id": r.schedule_id, "machine_id": r.machine_id,
        "name": r.name, "cron": r.cron, "run_at": r.run_at.isoformat() if r.run_at else None,
        "task_type": r.task_type, "request": r.request,
        "enabled": r.enabled,
        "next_run": r.next_run.isoformat() if r.next_run else None,
        "last_run": r.last_run.isoformat() if r.last_run else None,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }
