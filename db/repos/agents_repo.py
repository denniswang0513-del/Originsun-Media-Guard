"""Repository for agents table."""

from typing import List, Optional, Dict

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Agent


async def list_all(session: AsyncSession) -> List[dict]:
    rows = (await session.execute(select(Agent).order_by(Agent.name))).scalars().all()
    return [{"id": r.id, "name": r.name, "url": r.url} for r in rows]


async def add(session: AsyncSession, id: str, name: str, url: str) -> None:
    stmt = pg_insert(Agent).values(id=id, name=name, url=url).on_conflict_do_nothing()
    await session.execute(stmt)


async def get(session: AsyncSession, agent_id: str) -> Optional[dict]:
    row = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar()
    if not row:
        return None
    return {"id": row.id, "name": row.name, "url": row.url}


async def url_exists(session: AsyncSession, url: str) -> bool:
    normalized = url.rstrip("/").lower()
    count = (await session.execute(
        select(func.count()).select_from(Agent)
        .where(func.lower(func.rtrim(Agent.url, "/")) == normalized)
    )).scalar()
    return (count or 0) > 0


async def remove(session: AsyncSession, agent_id: str) -> bool:
    result = await session.execute(delete(Agent).where(Agent.id == agent_id))
    return result.rowcount > 0
