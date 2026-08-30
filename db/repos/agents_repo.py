"""agents 表的薄 CRUD。

共同契約（四個 repos/ 檔一樣）：吃 session、回 dict、**不 commit** ——
交易由呼叫端收尾，這裡只是把 SQL 集中，一支函式一句話講得完的不另寫 docstring。
"""

from typing import List, Optional

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Agent


async def list_all(session: AsyncSession) -> List[dict]:
    rows = (await session.execute(select(Agent).order_by(Agent.name))).scalars().all()
    return [{"id": r.id, "name": r.name, "url": r.url} for r in rows]


async def add(session: AsyncSession, id: str, name: str, url: str) -> None:
    """冪等：同 id 已存在就靜默不動（on_conflict_do_nothing）—— 兩台同時註冊不炸。"""
    stmt = pg_insert(Agent).values(id=id, name=name, url=url).on_conflict_do_nothing()
    await session.execute(stmt)


async def get(session: AsyncSession, agent_id: str) -> Optional[dict]:
    row = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar()
    if not row:
        return None
    return {"id": row.id, "name": row.name, "url": row.url}


async def url_exists(session: AsyncSession, url: str) -> bool:
    """同一台機器有沒有註冊過 —— 比對前去尾斜線＋小寫，
    不然 `http://x:8000` 和 `http://x:8000/` 會被當成兩台。"""
    normalized = url.rstrip("/").lower()
    count = (await session.execute(
        select(func.count()).select_from(Agent)
        .where(func.lower(func.rtrim(Agent.url, "/")) == normalized)
    )).scalar()
    return (count or 0) > 0


async def update(session: AsyncSession, agent_id: str, name: str, url: str) -> bool:
    from sqlalchemy import update as sa_update
    result = await session.execute(
        sa_update(Agent).where(Agent.id == agent_id).values(name=name, url=url)
    )
    return result.rowcount > 0


async def remove(session: AsyncSession, agent_id: str) -> bool:
    result = await session.execute(delete(Agent).where(Agent.id == agent_id))
    return result.rowcount > 0
