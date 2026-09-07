# -*- coding: utf-8 -*-
"""routers/crm/work_stages.py — 工作階段（work_stage_nodes）的 CRUD ＋ 種子。

docs/JOURNAL_WORKLOG_PLAN.md §12／§14：**每個分類自己的階段清單**（固定兩層：分類＝WORK_TYPES
九類、底下是階段），不是路徑樹。寫法照 routers/crm/taxonomy.py（同一個 `_shared.router`，
URL `/api/v1/crm/work-stages/nodes`），但沒有收支那邊的鏡射遷移 —— timesheets.stage_name 的
鏡射由 services.timesheet_self.set_stage 在寫列時做，改名不回頭改舊列（舊列留當時的名字）。

守衛：管理員照舊；其他人＝有綁人員檔案且**在職**（owner 2026-09-07「工作階段的設定開放給在職員工調整」；
鑰匙同 timesheets/mine：timesheets 模組或任一 me_* 鑰匙）。
停用不刪：有列引用的節點 DELETE 會變成停用（回 {"status": "deactivated"}）。
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from core.hr_logic import STAFF_ACTIVE, STAGE_SEED, WORK_TYPES, stage_categories
from core.schemas import WorkStageNodePayload, WorkStageNodeUpdate

from ._shared import router, _require_db, _get_factory, _now

try:
    from ._shared import select, func
    from db.models import Timesheet, WorkStageNode
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同 taxonomy.py
    pass


async def seed_if_empty(session) -> int:
    """種子只在表**空**時寫（core.hr_logic.STAGE_SEED）；之後全由 owner 在編輯器改。回寫入的節點數。"""
    n = (await session.execute(select(func.count()).select_from(WorkStageNode))).scalar() or 0
    if n:
        return 0
    made = 0
    for i, cat in enumerate(WORK_TYPES):
        cid = uuid.uuid4().hex
        session.add(WorkStageNode(id=cid, parent_id="", name=cat, depth=1, sort=i, active=1))
        made += 1
        for j, st in enumerate(STAGE_SEED.get(cat, ())):
            session.add(WorkStageNode(id=uuid.uuid4().hex, parent_id=cid, name=st, depth=2, sort=j, active=1))
            made += 1
    await session.commit()
    return made


async def _all_nodes(session) -> list:
    return (await session.execute(select(WorkStageNode))).scalars().all()


async def _stage_guard(request: Request) -> None:
    """管理員與工作追蹤模組照舊（原本就是這把鑰匙，不能因為開放而收回）；其他人＝有綁人員檔案且在職
    （owner 2026-09-07「工作階段的設定開放給在職員工調整」）：任一 me_* 鑰匙、沒綁 409、不在職 403。
    status 空白視同在職（人員序列化 routers/crm/staff.py 也是這樣預設）。"""
    from core.auth import ME_MODULE_KEYS, check_admin_or_module
    from core.identity import require_bound_staff
    try:
        check_admin_or_module(request, "timesheets")
        return
    except HTTPException as e:
        if e.status_code != 403:
            raise
    ident = await require_bound_staff(request, *ME_MODULE_KEYS)
    if (getattr(ident["staff"], "status", "") or STAFF_ACTIVE).strip() != STAFF_ACTIVE:
        raise HTTPException(status_code=403, detail="工作階段只開放在職員工調整")


@router.get("/work-stages/nodes")
async def list_work_stage_nodes(request: Request):
    """九個分類各一格（**含停用**）—— 編輯器用；下拉請走 GET /timesheets/options（只回 active）。"""
    await _stage_guard(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        cats = stage_categories(await _all_nodes(session), include_inactive=True)
        # 每個階段被幾列工時引用：編輯器只對 used=0 的露出「刪除」（owner 2026-09-07「還沒有人使用可以移除」）；
        # 真正的閘在 DELETE（有人用→停用），這裡只是讓按鈕不要出現在按了也不會刪的地方
        used = {sid: int(n or 0) for sid, n in (await session.execute(
            select(Timesheet.stage_id, func.count()).where(Timesheet.stage_id.isnot(None))
            .group_by(Timesheet.stage_id))).all()}
        for c in cats:
            for st in c.get("stages") or []:
                st["used"] = used.get(st.get("id"), 0)
        return {"categories": cats}


@router.post("/work-stages/nodes")
async def create_work_stage_node(req: WorkStageNodePayload, request: Request):
    """新增一個階段（只能掛在分類底下）。同名已存在就回它（停用過的順手復活）。"""
    await _stage_guard(request)
    name = (req.name or "").strip()
    parent_id = (req.parent_id or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="階段名稱必填")
    if not parent_id:
        raise HTTPException(status_code=422, detail="階段必須掛在分類底下（parent_id 必填）")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        parent = await session.get(WorkStageNode, parent_id)
        if parent is None or int(parent.depth or 1) != 1:
            raise HTTPException(status_code=404, detail="找不到這個分類")
        exist = (await session.execute(
            select(WorkStageNode).where(WorkStageNode.parent_id == parent_id,
                                        WorkStageNode.name == name))).scalar_one_or_none()
        if exist is not None:
            if not exist.active:
                exist.active = 1
                exist.updated_at = _now()
                await session.commit()
            return {"status": "ok", "node": {"id": exist.id, "name": exist.name, "sort": exist.sort,
                                             "active": bool(exist.active)}, "existed": True}
        last = (await session.execute(
            select(func.max(WorkStageNode.sort)).where(WorkStageNode.parent_id == parent_id))).scalar()
        node = WorkStageNode(id=uuid.uuid4().hex, parent_id=parent_id, name=name, depth=2,
                             sort=int(last if last is not None else -1) + 1, active=1)
        session.add(node)
        await session.commit()
        return {"status": "ok", "node": {"id": node.id, "name": node.name, "sort": node.sort,
                                         "active": True}, "existed": False}


@router.put("/work-stages/nodes/{node_id}")
async def update_work_stage_node(node_id: str, req: WorkStageNodeUpdate, request: Request):
    """改名／排序／停用（部分更新）。改名**不回頭改舊列的 stage_name**：舊列留當時的字。"""
    await _stage_guard(request)
    data = req.model_dump(exclude_unset=True)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        node = await session.get(WorkStageNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="找不到這個工作階段")
        if "name" in data and int(node.depth or 0) <= 1:
            # 分類名是工時列的分類鍵（timesheets.work_type 與 stage.category 以名字對）：改了就整個分類對不上 → 422
            raise HTTPException(status_code=422, detail="分類名稱不能在這裡改（工時列以分類名對階段）")
        if "name" in data:
            new_name = (data.get("name") or "").strip()
            if not new_name:
                raise HTTPException(status_code=422, detail="名稱必填")
            if new_name != node.name:
                dup = (await session.execute(
                    select(WorkStageNode.id).where(WorkStageNode.parent_id == node.parent_id,
                                                   WorkStageNode.name == new_name,
                                                   WorkStageNode.id != node_id))).scalar()
                if dup:
                    raise HTTPException(status_code=409, detail=f"同一個分類底下已經有「{new_name}」了")
                node.name = new_name
        if data.get("sort") is not None:
            node.sort = int(data["sort"])
        if data.get("active") is not None:
            node.active = 1 if data["active"] else 0
        node.updated_at = _now()
        await session.commit()
        return {"status": "ok", "node": {"id": node.id, "name": node.name, "sort": node.sort,
                                         "active": bool(node.active), "depth": node.depth}}


@router.delete("/work-stages/nodes/{node_id}")
async def delete_work_stage_node(node_id: str, request: Request):
    """有列引用 → 改成停用（回 deactivated；舊列照樣顯示）；沒人用才真的刪。分類底下還有階段不能刪。"""
    await _stage_guard(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        node = await session.get(WorkStageNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="找不到這個工作階段")
        if int(node.depth or 0) <= 1:
            raise HTTPException(status_code=409, detail="分類不能刪（工作分類是固定的一組，只能停用底下的階段）")
        kids = (await session.execute(
            select(func.count()).select_from(WorkStageNode).where(WorkStageNode.parent_id == node_id))).scalar() or 0
        if kids:
            raise HTTPException(status_code=409, detail=f"底下還有 {kids} 個階段，請先處理它們")
        used = (await session.execute(
            select(func.count()).select_from(Timesheet).where(Timesheet.stage_id == node_id))).scalar() or 0
        if used:
            node.active = 0
            node.updated_at = _now()
            await session.commit()
            return {"status": "deactivated", "used": int(used)}
        await session.delete(node)
        await session.commit()
    return {"status": "ok"}
