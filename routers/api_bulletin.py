"""
api_bulletin.py — 公布欄待辦提醒 CRUD（團隊共用一份，存 mediaguard）。

權限：check_admin_or_module(request, 'bulletin') — 管理員或帳號有 bulletin 模組即可讀寫。
排序：置頂 → 手動 sort_order → 建立時間（DB 端 order_by）；priority 前端當徽章/篩選用。
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import select, func  # type: ignore

from core.auth import check_admin_or_module
from core.schemas import BulletinCreate, BulletinUpdate, BulletinReorder
from db.models import BulletinItem
import core.state as state

router = APIRouter(prefix="/api/v1", tags=["bulletin"])

# 顯示順序：置頂 → 手動 sort_order → 建立時間。
_ORDER_BY = (BulletinItem.pinned.desc(), BulletinItem.sort_order, BulletinItem.created_at)


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
    async with factory() as session:
        rows = (await session.execute(select(BulletinItem).order_by(*_ORDER_BY))).scalars()
        return {"items": [_to_dict(r) for r in rows]}


@router.post("/bulletin")
async def create_bulletin(body: BulletinCreate, request: Request):
    payload = check_admin_or_module(request, "bulletin")
    if not (body.title or "").strip():
        raise HTTPException(status_code=422, detail="標題必填")
    factory = _require_db()
    async with factory() as session:
        maxo = (await session.execute(select(func.max(BulletinItem.sort_order)))).scalar() or 0
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
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        data = body.model_dump(exclude_unset=True)
        # 字串欄位 strip、空→None（title 空則不動）
        for k in ("note", "category"):
            if k in data:
                setattr(obj, k, (data[k] or "").strip() or None)
        if data.get("title"):
            obj.title = data["title"].strip()
        # 直取欄位
        for k in ("priority", "pinned", "status"):
            if data.get(k) is not None:
                setattr(obj, k, data[k])
        if "status" in data:
            obj.done_at = datetime.now() if obj.status == "done" else None
        await session.commit()
        await session.refresh(obj)
        return _to_dict(obj)


@router.delete("/bulletin/{item_id}")
async def delete_bulletin(item_id: str, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
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
    order = {iid: i for i, iid in enumerate(body.ordered_ids)}
    async with factory() as session:
        # 一次撈齊再就地改 sort_order（免 N 次 session.get 往返）。
        rows = (await session.execute(
            select(BulletinItem).where(BulletinItem.id.in_(list(order))))).scalars()
        for r in rows:
            r.sort_order = order[r.id]
        await session.commit()
    return {"ok": True}


# ── 種子：表空時塞入開場待辦（seed 邏輯與 feature 同住；mirrors db.seed_website）──
async def seed_if_empty(session) -> int:
    """bulletin_items 空時塞入開場待辦清單。回新增筆數（0 = 已有資料、跳過）。"""
    cnt = (await session.execute(select(func.count(BulletinItem.id)))).scalar() or 0
    if cnt:
        return 0
    seed = [
        ("存好備份加密金鑰到密碼管理器（settings 解密用；master 掛了才解得開）", "備份", "high", True),
        ("提供 46 條舊作品網址對照清單 → 補 301 轉址（保 SEO 權重）", "官網", "med", False),
        ("聯絡表單實測一次（www 域 Turnstile 驗證碼 + 收得到信）", "官網", "med", False),
        ("Bing Webmaster Tools 提交 sitemap", "官網", "low", False),
        ("清 CORS 白名單殘留的 test.originsun-studio.com", "系統", "low", False),
        ("新站穩定後停掉舊 WordPress 主機（43.254.17.7）", "系統", "low", False),
        ("翻譯 backlog 165 篇（每日自動清，想加速可手動整批）", "官網", "low", False),
    ]
    for i, (title, cat, pri, pin) in enumerate(seed):
        session.add(BulletinItem(
            id=uuid.uuid4().hex[:12], title=title, category=cat,
            priority=pri, pinned=pin, status="todo", sort_order=i, created_by="system"))
    await session.commit()
    return len(seed)
