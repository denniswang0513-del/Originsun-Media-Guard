"""Repository for reports table."""

from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Report


async def list_all(session: AsyncSession) -> List[dict]:
    rows = (await session.execute(
        select(Report).order_by(Report.created_at.desc())
    )).scalars().all()
    return [_to_dict(r) for r in rows]


async def add(session: AsyncSession, entry: dict) -> None:
    stmt = pg_insert(Report).values(
        id=entry.get("id", ""),
        name=entry.get("name", ""),
        local_path=entry.get("local_path", ""),
        pdf_path=entry.get("pdf_path", ""),
        public_url=entry.get("public_url", ""),
        drive_url=entry.get("drive_url", ""),
        machine_id=entry.get("machine_id", ""),
        file_count=entry.get("file_count", 0),
        total_size_str=entry.get("total_size_str", ""),
    ).on_conflict_do_nothing(index_elements=["id"])
    await session.execute(stmt)


async def update_drive_url(session: AsyncSession, report_id: str, drive_url: str) -> None:
    row = (await session.execute(select(Report).where(Report.id == report_id))).scalar()
    if row:
        row.drive_url = drive_url
        await session.flush()


async def remove(session: AsyncSession, report_id: str) -> bool:
    result = await session.execute(delete(Report).where(Report.id == report_id))
    return result.rowcount > 0


def _to_dict(r: Report) -> dict:
    return {
        "id": r.id, "name": r.name,
        "local_path": r.local_path or "",
        "pdf_path": r.pdf_path or "",
        "public_url": r.public_url or "",
        "drive_url": r.drive_url or "",
        "machine_id": r.machine_id or "",
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        "file_count": r.file_count or 0,
        "total_size_str": r.total_size_str or "",
    }
