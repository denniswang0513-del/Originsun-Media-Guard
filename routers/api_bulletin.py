"""
api_bulletin.py — 公布欄待辦提醒 CRUD（團隊共用一份，存 mediaguard）。

權限：check_admin_or_module(request, 'bulletin') — 管理員或帳號有 bulletin 模組即可讀寫。
排序：置頂 → 手動 sort_order → 建立時間；priority 由前端當徽章/篩選用。
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from core.auth import check_admin_or_module
from core.schemas import BulletinCreate, BulletinUpdate, BulletinReorder
import core.state as state

router = APIRouter(prefix="/api/v1", tags=["bulletin"])


def _to_dict(o) -> dict:
    return {
        "id": o.id, "title": o.title, "note": o.note or "",
        "status": o.status, "priority": o.priority, "category": o.category or "",
        "pinned": bool(o.pinned), "sort_order": o.sort_order,
        "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "done_at": o.done_at.isoformat() if o.done_at else None,
    }


def _require_db():
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫離線，公布欄暫不可用")
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(status_code=503, detail="資料庫未初始化")
    return factory


@router.get("/bulletin")
async def list_bulletin(request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    from sqlalchemy import select
    from db.models import BulletinItem
    async with factory() as session:
        rows = list((await session.execute(select(BulletinItem))).scalars())
    items = [_to_dict(r) for r in rows]
    items.sort(key=lambda x: (0 if x["pinned"] else 1, x["sort_order"], x["created_at"] or ""))
    return {"items": items}


@router.post("/bulletin")
async def create_bulletin(body: BulletinCreate, request: Request):
    payload = check_admin_or_module(request, "bulletin")
    if not (body.title or "").strip():
        raise HTTPException(status_code=422, detail="標題必填")
    factory = _require_db()
    from sqlalchemy import select, func as safunc
    from db.models import BulletinItem
    async with factory() as session:
        maxo = (await session.execute(select(safunc.max(BulletinItem.sort_order)))).scalar() or 0
        obj = BulletinItem(
            id=uuid.uuid4().hex[:12],
            title=body.title.strip(),
            note=(body.note or "").strip() or None,
            status=body.status or "todo",
            priority=body.priority or "med",
            category=(body.category or "").strip() or None,
            pinned=bool(body.pinned),
            sort_order=int(maxo) + 1,
            created_by=(payload or {}).get("sub"),
        )
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return _to_dict(obj)


@router.put("/bulletin/{item_id}")
async def update_bulletin(item_id: str, body: BulletinUpdate, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    from db.models import BulletinItem
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        data = body.model_dump(exclude_unset=True)
        if "title" in data and data["title"] is not None:
            obj.title = data["title"].strip()
        if "note" in data:
            obj.note = (data["note"] or "").strip() or None
        if "category" in data:
            obj.category = (data["category"] or "").strip() or None
        if "priority" in data and data["priority"] is not None:
            obj.priority = data["priority"]
        if "pinned" in data and data["pinned"] is not None:
            obj.pinned = bool(data["pinned"])
        if "status" in data and data["status"] is not None:
            obj.status = data["status"]
            obj.done_at = datetime.now() if data["status"] == "done" else None
        await session.commit()
        await session.refresh(obj)
        return _to_dict(obj)


@router.delete("/bulletin/{item_id}")
async def delete_bulletin(item_id: str, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    from db.models import BulletinItem
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        await session.delete(obj)
        await session.commit()
    return {"deleted": item_id}


@router.post("/bulletin/reorder")
async def reorder_bulletin(body: BulletinReorder, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    from db.models import BulletinItem
    async with factory() as session:
        for i, iid in enumerate(body.ordered_ids):
            obj = await session.get(BulletinItem, iid)
            if obj is not None:
                obj.sort_order = i
        await session.commit()
    return {"ok": True}
