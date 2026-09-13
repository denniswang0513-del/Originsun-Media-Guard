"""routers/crm/cost_lines.py — 成本估算 cost lines：階段分組、預設範本、從報價單匯入、範本 CRUD。

2026-09-13 從 costs.py 原樣切出（純搬移）。共用 helper 從 costs.py／cost_groups.py 拿。
掃原始碼的測試用 `_srcscan.costs_src()`。
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, Query

from core.schemas import (CostLinePayload, CostLineUpdatePayload)

from ._shared import (router, _check_project_write_auth, money_dep, _require_db,
                      _get_factory)

try:
    from ._shared import (select, delete,
                          CrmProject, CrmProjectCostLine, CrmCostLineTemplate,
                          CrmProjectCostGroup, CrmQuotation, CrmQuotationItem,
                          CrmStaff)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

from .cost_groups import _replace_group_cost_lines
from .costs import _fmt_date, _resolve_target_group


# ── Phase helpers ────────────────────────────────────────────
PHASE_ORDER = ("前期製作", "現場拍攝", "後期製作")


def _phase_group(lines: list) -> list:
    """[{phase, lines}, ...]，PHASE_ORDER 優先排序，其他 phase 追加末尾。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for ln in lines:
        buckets[ln["phase"]].append(ln)
    seen = set()
    result = []
    for p in PHASE_ORDER:
        if buckets.get(p):
            result.append({"phase": p, "lines": buckets[p]})
            seen.add(p)
    for p, ls in buckets.items():
        if p not in seen and ls:
            result.append({"phase": p, "lines": ls})
    return result


# ── Cost Line Default Templates ─────────────────────────────
_COST_LINE_DEFAULTS = [
    ("前期製作", "製片/專案管理", 0), ("前期製作", "導演", 1),
    ("前期製作", "腳本", 2), ("前期製作", "視覺設計", 3),
    ("前期製作", "分鏡圖", 4), ("前期製作", "其他", 5),
    ("現場拍攝", "動態攝影", 0), ("現場拍攝", "平面攝影", 1),
    ("現場拍攝", "攝影助理", 2), ("現場拍攝", "燈光師", 3),
    ("現場拍攝", "收音師", 4), ("現場拍攝", "美術", 5),
    ("現場拍攝", "服裝", 6), ("現場拍攝", "梳化", 7),
    ("現場拍攝", "翻譯", 8), ("現場拍攝", "其他", 9),
    ("後期製作", "剪輯", 0), ("後期製作", "調光", 1),
    ("後期製作", "混音", 2), ("後期製作", "視覺包裝", 3),
    ("後期製作", "動態設計", 4), ("後期製作", "錄音", 5),
    ("後期製作", "配音", 6), ("後期製作", "翻譯", 7),
    ("後期製作", "其他", 8),
]



# ── Quotation → Cost Line mapping helpers ──────────────────────
_QUOTE_GROUP_TO_PHASE = {
    # 2026-07-06 修復：「前期製作」原含 U+FFFD 損壞字元（歷史編碼事故），
    # 導致「前期」關鍵字對到損壞字串、與 _map_group_to_phase fallback 值不一致。
    # 舊資料若已存入損壞 phase 值，需另跑資料修正。
    "前期": "前期製作", "拍攝": "現場拍攝", "現場": "現場拍攝",
    "後製": "後期製作", "後期": "後期製作",
}
_QUOTE_UNIT_MAP = {"天": "日"}


def _map_group_to_phase(group_name: str) -> str:
    g = (group_name or "").strip()
    for keyword, phase in _QUOTE_GROUP_TO_PHASE.items():
        if keyword in g:
            return phase
    return "前期製作"


def _cost_line_to_dict(line, staff_map: dict) -> dict:
    est_staff = staff_map.get(line.estimated_staff_id or "", {})
    act_staff = staff_map.get(line.actual_staff_id or "", {})
    return {
        "id": line.id, "project_id": line.project_id,
        "phase": line.phase, "item_name": line.item_name,
        "sort_order": line.sort_order,
        "estimated_unit_price": line.estimated_unit_price,
        "estimated_quantity": line.estimated_quantity,
        "estimated_unit_type": line.estimated_unit_type or "",
        "estimated_amount": line.estimated_amount,
        "estimated_staff_id": line.estimated_staff_id or "",
        "estimated_staff_name": est_staff.get("name", ""),
        "estimated_notes": line.estimated_notes or "",
        "actual_unit_price": line.actual_unit_price,
        "actual_quantity": line.actual_quantity,
        "actual_unit_type": line.actual_unit_type or "",
        "actual_amount": line.actual_amount,
        "actual_staff_id": line.actual_staff_id or "",
        "actual_staff_name": act_staff.get("name", ""),
        "actual_notes": line.actual_notes or "",
    }


# ── Project Cost Lines (成本估算) Endpoints ──────────────────

@router.get("/projects/{project_id}/cost-lines", dependencies=[Depends(money_dep)])
async def list_project_cost_lines(project_id: str, group_id: Optional[str] = Query(None)):
    """回傳成本估算明細，按 phase 分組。
    - 無 group_id：回傳整個專案 + 多一層 grouped_by_group
    - 有 group_id：只回傳該子表
    舊 `grouped` 欄位保留給向後相容（= 當前 group_id 或全專案彙總）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = select(CrmProjectCostLine).where(CrmProjectCostLine.project_id == project_id)
        if group_id:
            q = q.where(CrmProjectCostLine.cost_group_id == group_id)
        rows = (await session.execute(
            q.order_by(CrmProjectCostLine.phase, CrmProjectCostLine.sort_order)
        )).scalars().all()

        staff_ids = set()
        for r in rows:
            if r.estimated_staff_id: staff_ids.add(r.estimated_staff_id)
            if r.actual_staff_id:    staff_ids.add(r.actual_staff_id)
        staff_map = {}
        if staff_ids:
            staff_rows = (await session.execute(
                select(CrmStaff).where(CrmStaff.id.in_(list(staff_ids)))
            )).scalars().all()
            staff_map = {s.id: {"name": s.name, "role": s.role} for s in staff_rows}

        # 指定單組 → 不需要列出所有子表（grouped_by_group 留空即可）
        groups = []
        if not group_id:
            groups = (await session.execute(
                select(CrmProjectCostGroup)
                .where(CrmProjectCostGroup.project_id == project_id)
                .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
            )).scalars().all()

    lines = [_cost_line_to_dict(r, staff_map) for r in rows]
    grouped_by_group = [{
        "group_id": g.id, "group_name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "sort_order": g.sort_order,
        "phases": _phase_group([_cost_line_to_dict(r, staff_map) for r in rows if r.cost_group_id == g.id]),
    } for g in groups]

    return {
        "cost_lines": lines,
        "grouped": _phase_group(lines),  # backward-compat (frontend crm-projects-cost.js 仍在讀)
        "grouped_by_group": grouped_by_group,
    }


@router.post("/projects/{project_id}/cost-lines/init")
async def init_project_cost_lines(project_id: str, request: Request):
    """用預設清單初始化成本項目（跳過已存在的）。
    預設目標為主表（第一張子表）；可傳 body `{"cost_group_id": "..."}` 指定。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        target_gid = await _resolve_target_group(session, project_id, body.get("cost_group_id"))
        existing = (await session.execute(
            select(CrmProjectCostLine.phase, CrmProjectCostLine.item_name)
            .where(CrmProjectCostLine.project_id == project_id,
                   CrmProjectCostLine.cost_group_id == target_gid)
        )).all()
        existing_set = {(r[0], r[1]) for r in existing}

        added = 0
        for phase, item_name, sort_order in _COST_LINE_DEFAULTS:
            if (phase, item_name) in existing_set:
                continue
            session.add(CrmProjectCostLine(
                id=uuid.uuid4().hex, project_id=project_id, cost_group_id=target_gid,
                phase=phase, item_name=item_name, sort_order=sort_order,
            ))
            added += 1
        await session.commit()
    return {"status": "ok", "added": added, "cost_group_id": target_gid}


@router.post("/projects/{project_id}/cost-lines")
async def add_project_cost_line(project_id: str, req: CostLinePayload, request: Request):
    """新增單一自訂成本項目。必須指定 cost_group_id，否則自動歸入主表。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        target_gid = await _resolve_target_group(session, project_id, req.cost_group_id)
        est_amt = req.estimated_amount
        if req.estimated_unit_price and req.estimated_quantity:
            est_amt = req.estimated_unit_price * req.estimated_quantity
        act_amt = req.actual_amount
        if req.actual_unit_price and req.actual_quantity:
            act_amt = req.actual_unit_price * req.actual_quantity
        line = CrmProjectCostLine(
            id=uuid.uuid4().hex, project_id=project_id, cost_group_id=target_gid,
            phase=req.phase, item_name=req.item_name, sort_order=req.sort_order,
            estimated_unit_price=req.estimated_unit_price,
            estimated_quantity=req.estimated_quantity,
            estimated_unit_type=req.estimated_unit_type or None,
            estimated_amount=est_amt,
            estimated_staff_id=req.estimated_staff_id or None,
            estimated_notes=req.estimated_notes,
            actual_unit_price=req.actual_unit_price,
            actual_quantity=req.actual_quantity,
            actual_unit_type=req.actual_unit_type or None,
            actual_amount=act_amt,
            actual_staff_id=req.actual_staff_id or None,
            actual_notes=req.actual_notes,
        )
        session.add(line)
        await session.commit()
    return {"status": "ok", "id": line.id, "cost_group_id": target_gid}


@router.put("/project-cost-lines/{line_id}")
async def update_project_cost_line(line_id: str, req: CostLineUpdatePayload, request: Request):
    """部分更新成本項目。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        line = await session.get(CrmProjectCostLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="找不到此成本項目")
        # exclude_unset (not exclude_none): keeps explicit nulls so frontend
        # can clear a numeric field by sending {"estimated_quantity": null}.
        # exclude_none would silently drop the null and the field would never
        # update — user sees "saved" status but the DB never changed.
        update_data = req.model_dump(exclude_unset=True)
        for fld in ("estimated_staff_id", "actual_staff_id"):
            if fld in update_data and update_data[fld] == "":
                update_data[fld] = None
        for key, value in update_data.items():
            setattr(line, key, value)
        # Auto-calculate amount = unit_price × quantity
        if "estimated_unit_price" in update_data or "estimated_quantity" in update_data:
            up = line.estimated_unit_price or 0
            qty = line.estimated_quantity or 0
            line.estimated_amount = up * qty if (up and qty) else None
        if "actual_unit_price" in update_data or "actual_quantity" in update_data:
            up = line.actual_unit_price or 0
            qty = line.actual_quantity or 0
            line.actual_amount = up * qty if (up and qty) else None
        from sqlalchemy import func as _fn
        line.updated_at = _fn.now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/projects/{project_id}/cost-lines/phase")
async def delete_project_cost_phase(project_id: str, request: Request):
    """刪除指定 phase 的所有成本項目。
    支援 body 或 query 的 `group_id` / `cost_group_id` — 有指定時只刪該子表的該 phase。
    未指定：刪該專案全部子表的此 phase（向後相容）。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    phase = body.get("phase") or request.query_params.get("phase", "")
    gid = body.get("group_id") or body.get("cost_group_id") or request.query_params.get("group_id")
    if not phase:
        raise HTTPException(status_code=400, detail="需指定 phase")
    factory = await _get_factory()
    async with factory() as session:
        q = delete(CrmProjectCostLine).where(
            CrmProjectCostLine.project_id == project_id,
            CrmProjectCostLine.phase == phase,
        )
        if gid:
            q = q.where(CrmProjectCostLine.cost_group_id == gid)
        await session.execute(q)
        await session.commit()
    return {"status": "ok"}


@router.delete("/project-cost-lines/{line_id}")
async def delete_project_cost_line(line_id: str, request: Request):
    """刪除成本項目。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        line = await session.get(CrmProjectCostLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="找不到此成本項目")
        await session.delete(line)
        await session.commit()
    return {"status": "ok"}


# ── Cost Line Templates (成本估算範本) Endpoints ─────────────

@router.get("/cost-line-templates", dependencies=[Depends(money_dep)])
async def list_cost_line_templates():
    """列出所有成本估算範本。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCostLineTemplate).order_by(CrmCostLineTemplate.created_at.desc())
        )).scalars().all()
    return {"templates": [{
        "id": t.id, "name": t.name, "items": t.items or [],
        "item_count": len(t.items or []),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in rows]}


@router.post("/cost-line-templates")
async def create_cost_line_template(request: Request):
    """從指定專案建立成本估算範本。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    body = await request.json()
    name = body.get("name", "").strip()
    project_id = body.get("project_id", "")
    if not name:
        raise HTTPException(status_code=400, detail="範本名稱不可為空")
    factory = await _get_factory()
    async with factory() as session:
        # Support creating from defaults or from a project
        if not project_id or project_id == "__defaults__":
            items = [{"phase": p, "item_name": n, "sort_order": s} for p, n, s in _COST_LINE_DEFAULTS]
            tpl = CrmCostLineTemplate(id=uuid.uuid4().hex, name=name, items=items)
            session.add(tpl)
            await session.commit()
            return {"status": "ok", "id": tpl.id, "item_count": len(items)}
        rows = (await session.execute(
            select(CrmProjectCostLine)
            .where(CrmProjectCostLine.project_id == project_id)
            .order_by(CrmProjectCostLine.phase, CrmProjectCostLine.sort_order)
        )).scalars().all()
        items = [{"phase": r.phase, "item_name": r.item_name, "sort_order": r.sort_order} for r in rows]
        tpl = CrmCostLineTemplate(id=uuid.uuid4().hex, name=name, items=items)
        session.add(tpl)
        await session.commit()
    return {"status": "ok", "id": tpl.id, "item_count": len(items)}


@router.post("/projects/{project_id}/cost-lines/apply-template")
async def apply_cost_line_template(project_id: str, request: Request):
    """套用範本到指定子表（覆蓋該子表既有成本項目）。
    body: `{template_id, cost_group_id?}` — cost_group_id 未給則套到主表。
    不動任何雜支（範本只定義成本結構）。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    body = await request.json()
    template_id = body.get("template_id", "")
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        target_gid = await _resolve_target_group(session, project_id, body.get("cost_group_id"))

        if template_id == "__default__":
            items = [{"phase": p, "item_name": n, "sort_order": s} for p, n, s in _COST_LINE_DEFAULTS]
        else:
            tpl = await session.get(CrmCostLineTemplate, template_id)
            if not tpl:
                raise HTTPException(status_code=404, detail="找不到此範本")
            items = tpl.items or []

        new_lines = [
            {"phase": it["phase"], "item_name": it["item_name"], "sort_order": it.get("sort_order", 0)}
            for it in items if it.get("phase") and it.get("item_name")
        ]
        added = await _replace_group_cost_lines(session, project_id, target_gid, new_lines)
        await session.commit()
    return {"status": "ok", "added": added, "cost_group_id": target_gid}


@router.post("/projects/{project_id}/cost-lines/import-from-quotation")
async def import_cost_lines_from_quotation(project_id: str, request: Request):
    """從報價單匯入成本項目到指定子表（覆蓋該子表既有項目，填入預估欄位）。
    body: `{quotation_id, cost_group_id?}` — cost_group_id 未給則匯入到主表。
    不動任何雜支。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    body = await request.json()
    quotation_id = body.get("quotation_id", "")
    if not quotation_id:
        raise HTTPException(status_code=400, detail="缺少 quotation_id")
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        quotation = await session.get(CrmQuotation, quotation_id)
        if not quotation or quotation.project_id != project_id:
            raise HTTPException(status_code=400, detail="報價不屬於此專案")

        target_gid = await _resolve_target_group(session, project_id, body.get("cost_group_id"))

        items = (await session.execute(
            select(CrmQuotationItem)
            .where(CrmQuotationItem.quotation_id == quotation_id)
            .order_by(CrmQuotationItem.sort_order)
        )).scalars().all()

        new_lines = []
        for it in items:
            desc = (it.description or "").strip()
            if not desc:
                continue
            unit = (it.unit or "式").strip()
            new_lines.append({
                "phase": _map_group_to_phase(it.group_name),
                "item_name": desc,
                "sort_order": it.sort_order or 0,
                "estimated_unit_price": it.unit_price,
                "estimated_quantity": it.quantity,
                "estimated_unit_type": _QUOTE_UNIT_MAP.get(unit, unit),
                "estimated_amount": it.amount,
                "estimated_notes": it.note or None,
            })
        added = await _replace_group_cost_lines(session, project_id, target_gid, new_lines)
        await session.commit()
    return {"status": "ok", "added": added, "cost_group_id": target_gid}


@router.put("/cost-line-templates/{template_id}")
async def update_cost_line_template(template_id: str, request: Request):
    """修改範本名稱。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名稱不可為空")
    factory = await _get_factory()
    async with factory() as session:
        tpl = await session.get(CrmCostLineTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="找不到此範本")
        tpl.name = name
        await session.commit()
    return {"status": "ok"}


@router.delete("/cost-line-templates/{template_id}")
async def delete_cost_line_template(template_id: str, request: Request):
    """刪除成本估算範本。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        tpl = await session.get(CrmCostLineTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="找不到此範本")
        await session.delete(tpl)
        await session.commit()
    return {"status": "ok"}
