"""Repository for bookmarks table."""

from typing import List, Optional
from datetime import datetime

from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Bookmark


async def list_all(session: AsyncSession, machine_id: Optional[str] = None) -> List[dict]:
    stmt = select(Bookmark).order_by(Bookmark.created_at.desc())
    if machine_id:
        stmt = stmt.where(Bookmark.machine_id == machine_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


async def create(session: AsyncSession, id: str, machine_id: str,
                 name: str, task_type: str, request: dict) -> dict:
    bm = Bookmark(id=id, machine_id=machine_id, name=name,
                  task_type=task_type, request=request)
    session.add(bm)
    await session.flush()
    return _to_dict(bm)


async def update_bookmark(session: AsyncSession, bookmark_id: str, **kwargs) -> Optional[dict]:
    row = (await session.execute(select(Bookmark).where(Bookmark.id == bookmark_id))).scalar()
    if not row:
        return None
    for k, v in kwargs.items():
        if v is not None and hasattr(row, k):
            setattr(row, k, v)
    row.updated_at = datetime.now()
    await session.flush()
    return _to_dict(row)


async def delete_bookmark(session: AsyncSession, bookmark_id: str) -> bool:
    result = await session.execute(delete(Bookmark).where(Bookmark.id == bookmark_id))
    return result.rowcount > 0


def _to_dict(r: Bookmark) -> dict:
    return {
        "id": r.id, "machine_id": r.machine_id, "name": r.name,
        "task_type": r.task_type, "request": r.request,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "updated_at": r.updated_at.isoformat() if r.updated_at else "",
    }
