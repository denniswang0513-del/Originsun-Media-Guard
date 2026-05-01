"""services/website/_crud_base.py
---
共用 CRUD 泛型：被 seo_service / credit_service / 其他單純 CRUD service 用。

設計原則：
- to_dict(obj) callback 各 service 自定（每個實體 dict shape 不同）
- pre_write hook（optional）— 給需要型別轉換（如 ISO date string → date 物件）的 service 用
- list/create/update/delete 接 model class 跟 callback；不依賴 setattr 之外的 ORM 細節

不抽 hydrate / cascade — 那些是 service-specific 邏輯。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, Type

from sqlalchemy import asc, delete, select
from sqlalchemy.ext.asyncio import AsyncSession


ToDictFn = Callable[[Any], dict]
PreWriteHook = Callable[[dict], dict]


async def list_items(
    session: AsyncSession,
    model: Type,
    to_dict: ToDictFn,
    *,
    visible_only: bool = False,
) -> list[dict]:
    """通用 list — order by sort_order, id；可選 visible filter。"""
    stmt = select(model).order_by(asc(model.sort_order), asc(model.id))
    if visible_only:
        stmt = stmt.where(model.visible.is_(True))
    return [to_dict(o) for o in (await session.execute(stmt)).scalars()]


async def create_item(
    session: AsyncSession,
    model: Type,
    data: dict,
    to_dict: ToDictFn,
    *,
    pre_write: Optional[PreWriteHook] = None,
) -> dict:
    if pre_write:
        data = pre_write(data)
    obj = model(**data)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return to_dict(obj)


async def update_item(
    session: AsyncSession,
    model: Type,
    item_id: int,
    data: dict,
    to_dict: ToDictFn,
    *,
    pre_write: Optional[PreWriteHook] = None,
) -> Optional[dict]:
    """data 來自 model_dump(exclude_unset=True) — 每個 key 都是 client 主動送的
    （null = 明確清空、值 = 更新）。NOT NULL columns 由 DB constraint 把關。"""
    obj = await session.get(model, item_id)
    if not obj:
        return None
    if pre_write:
        data = pre_write(data)
    for k, v in data.items():
        setattr(obj, k, v)
    await session.commit()
    await session.refresh(obj)
    return to_dict(obj)


async def delete_item(
    session: AsyncSession, model: Type, item_id: int,
) -> bool:
    result = await session.execute(delete(model).where(model.id == item_id))
    await session.commit()
    return (result.rowcount or 0) > 0
