"""routers/crm/cost_groups.py — 成本子表（cost groups）：清單／新增／改／刪／複製／摘要。

2026-09-13 從 costs.py 原樣切出（純搬移）。共用 helper 從 costs.py 拿；cost_lines.py 再從這裡拿
`_replace_group_cost_lines`。掃原始碼的測試用 `_srcscan.costs_src()`。
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request

from core.schemas import (CostGroupCreate, CostGroupUpdate, CostGroupDuplicate)

from ._shared import (router, _check_project_write_auth, money_dep, _require_db,
                      _get_factory, _parse_shoot_date)

try:
    from ._shared import (select, delete,
                          CrmProject, CrmProjectExpense, CrmProjectCostLine,
                          CrmProjectCostGroup)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

from .costs import ADMIN_PHASE, _fmt_date, _project_misc_pct


# ── Cost Groups (成本子表) Endpoints ─────────────────────────

async def _replace_group_cost_lines(session, project_id: str, group_id: str, new_lines: list) -> int:
    """清空目標子表 cost_lines 並插入 new_lines；回傳新增筆數。
    new_lines 為 dict list，dict 內容是 CrmProjectCostLine 的 kwargs
    （不含 id/project_id/cost_group_id）。呼叫端自行 commit。"""
    await session.execute(
        delete(CrmProjectCostLine).where(
            CrmProjectCostLine.project_id == project_id,
            CrmProjectCostLine.cost_group_id == group_id,
        )
    )
    for kw in new_lines:
        session.add(CrmProjectCostLine(
            id=uuid.uuid4().hex, project_id=project_id, cost_group_id=group_id, **kw
        ))
    return len(new_lines)


async def _compute_group_summary(session, group_id: str) -> dict:
    """彙總單一子表：cost_estimated / cost_actual / expense_estimated / expense_actual。"""
    from sqlalchemy import func as _fn
    cl_row = (await session.execute(
        select(_fn.coalesce(_fn.sum(CrmProjectCostLine.estimated_amount), 0),
               _fn.coalesce(_fn.sum(CrmProjectCostLine.actual_amount), 0),
               _fn.count(CrmProjectCostLine.id))
        .where(CrmProjectCostLine.cost_group_id == group_id)
        .where(CrmProjectCostLine.phase != ADMIN_PHASE)     # 同清單／financial-summary：行政雜支工項不算人員成本
    )).first()
    ex_row = (await session.execute(
        select(_fn.coalesce(_fn.sum(CrmProjectExpense.estimated), 0),
               _fn.coalesce(_fn.sum(CrmProjectExpense.actual), 0),
               _fn.count(CrmProjectExpense.id))
        .where(CrmProjectExpense.cost_group_id == group_id)
    )).first()
    cost_est = int(cl_row[0] or 0) if cl_row else 0
    cost_act = int(cl_row[1] or 0) if cl_row else 0
    cl_count = int(cl_row[2] or 0) if cl_row else 0
    exp_est = int(ex_row[0] or 0) if ex_row else 0
    exp_act = int(ex_row[1] or 0) if ex_row else 0
    exp_count = int(ex_row[2] or 0) if ex_row else 0
    return {
        "cost_estimated": cost_est, "cost_actual": cost_act,
        "expense_estimated": exp_est, "expense_actual": exp_act,
        "total_estimated": cost_est + exp_est,
        "total_actual": cost_act + exp_act,
        "cost_lines_count": cl_count,
        "expenses_count": exp_count,
    }




def _cost_group_to_dict(g, summary: Optional[dict] = None, misc_pct: int = 5) -> dict:
    # 子表預算含委外與雜支（owner 2026-09-05）；雜支預算沒設＝預設 預算 × 專案雜支比（core.crm_logic.group_misc_default）
    from core.crm_logic import group_misc_default
    total_budget = g.budget_amount or 0
    misc_default = group_misc_default(total_budget, misc_pct)
    d = {
        "id": g.id, "project_id": g.project_id, "name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "notes": g.notes or "",
        "sort_order": g.sort_order,
        "budget_amount": g.budget_amount,
        "misc_budget_amount": g.misc_budget_amount,
        "misc_budget_default": misc_default,                                          # 沒設時畫「預設 $X（5%）」
        "misc_budget_effective": g.misc_budget_amount if g.misc_budget_amount is not None else misc_default,
        "profit_target_pct": g.profit_target_pct,
        "receipt_path": g.receipt_path or "",
        "total_budget": total_budget if g.budget_amount else None,      # 只設雜支、或預算 0 都不算「有預算」（同 misc_budget_total_of；別畫 $0／剩餘負數）
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }
    if summary is not None:
        d["summary"] = summary
        # usage_pct: budget 未設時回 None；設了才算
        if total_budget > 0:
            d["usage_pct"] = round(summary["total_actual"] / total_budget * 100)
        else:
            d["usage_pct"] = None
    return d


@router.get("/projects/{project_id}/cost-groups", dependencies=[Depends(money_dep)])
async def list_project_cost_groups(project_id: str):
    """列出指定專案的所有子表 + 每組 summary。純讀取：依賴
    migration + create_project 保證主表存在，避免 GET 寫入。"""
    _require_db()
    factory = await _get_factory()
    from sqlalchemy import func as _fn
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()
        if not rows:
            return {"cost_groups": []}

        # 單次 GROUP BY 聚合，避免 O(groups) 次子查詢
        cl_rows = (await session.execute(
            select(CrmProjectCostLine.cost_group_id,
                   _fn.coalesce(_fn.sum(CrmProjectCostLine.estimated_amount), 0),
                   _fn.coalesce(_fn.sum(CrmProjectCostLine.actual_amount), 0),
                   _fn.count(CrmProjectCostLine.id))
            # 同 financial-summary：行政雜支階段不算人員成本（見 ADMIN_PHASE）
            .where(CrmProjectCostLine.project_id == project_id,
                   CrmProjectCostLine.phase != ADMIN_PHASE)
            .group_by(CrmProjectCostLine.cost_group_id)
        )).all()
        cl_map = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in cl_rows}

        ex_rows = (await session.execute(
            select(CrmProjectExpense.cost_group_id,
                   _fn.coalesce(_fn.sum(CrmProjectExpense.estimated), 0),
                   _fn.coalesce(_fn.sum(CrmProjectExpense.actual), 0),
                   _fn.count(CrmProjectExpense.id))
            .where(CrmProjectExpense.project_id == project_id)
            .group_by(CrmProjectExpense.cost_group_id)
        )).all()
        ex_map = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in ex_rows}
        # 🔴 在 session 還開著的時候查、而且**只查一次** —— 原本寫在下面的迴圈裡，
        # 那時 `async with` 已經結束（session 被 close），而且每張子表查一次。
        _pct = await _project_misc_pct(session, project_id)

    result = []
    for g in rows:
        ce, ca, cc = cl_map.get(g.id, (0, 0, 0))
        ee, ea, ec = ex_map.get(g.id, (0, 0, 0))
        summary = {
            "cost_estimated": ce, "cost_actual": ca,
            "expense_estimated": ee, "expense_actual": ea,
            "total_estimated": ce + ee, "total_actual": ca + ea,
            "cost_lines_count": cc, "expenses_count": ec,
        }
        result.append(_cost_group_to_dict(g, summary, _pct))
    return {"cost_groups": result}


@router.post("/projects/{project_id}/cost-groups")
async def create_cost_group(project_id: str, req: CostGroupCreate, request: Request):
    """新增子表。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        shoot_date = _parse_shoot_date(req.shoot_date) if req.shoot_date else None
        g = CrmProjectCostGroup(
            id=uuid.uuid4().hex, project_id=project_id,
            name=req.name.strip(), shoot_date=shoot_date, notes=req.notes or None,
            sort_order=req.sort_order,
            budget_amount=req.budget_amount, misc_budget_amount=req.misc_budget_amount,
            profit_target_pct=req.profit_target_pct,
            receipt_path=(req.receipt_path or None),
        )
        session.add(g)
        await session.commit()
        await session.refresh(g)
        summary = await _compute_group_summary(session, g.id)
        _pct = await _project_misc_pct(session, g.project_id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(g, summary, _pct)}


@router.put("/cost-groups/{group_id}")
async def update_cost_group(group_id: str, req: CostGroupUpdate, request: Request):
    """更新子表（名稱/拍攝日/備註/排序/預算/毛利率），PATCH 風格只動有帶的欄位。

    exclude_unset（不是 exclude_none）：顯式帶 null ＝ 清空 —— 雜支預算清回
    「未設」（≠ 0，未設才會退回 % 自動推算）靠這個；exclude_none 會把 null
    靜默丟掉，編輯視窗按了清空其實沒清（同 costs.py 收據路徑的前例）。
    """
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        data = req.model_dump(exclude_unset=True)
        if data.get("name"):
            g.name = data["name"].strip() or g.name
        if "shoot_date" in data:
            g.shoot_date = _parse_shoot_date(data["shoot_date"]) if data["shoot_date"] else None
        if "notes" in data:
            g.notes = data["notes"] or None
        if "receipt_path" in data:
            g.receipt_path = data["receipt_path"] or None
        for fld in ("sort_order", "budget_amount", "misc_budget_amount", "profit_target_pct"):
            if fld in data:
                setattr(g, fld, data[fld])
        from sqlalchemy import func as _fn
        g.updated_at = _fn.now()
        await session.commit()
        await session.refresh(g)
        summary = await _compute_group_summary(session, g.id)
        _pct = await _project_misc_pct(session, g.project_id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(g, summary, _pct)}


@router.delete("/cost-groups/{group_id}")
async def delete_cost_group(group_id: str, request: Request):
    """刪除子表（cascade cost_lines + expenses）。若只剩 1 張子表則禁止。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        from sqlalchemy import func as _fn
        count = (await session.execute(
            select(_fn.count(CrmProjectCostGroup.id))
            .where(CrmProjectCostGroup.project_id == g.project_id)
        )).scalar_one()
        if count <= 1:
            raise HTTPException(status_code=400, detail="至少需保留一張子表")
        # claim 列不准從專案側消滅（同 _guard_claimed 的政策，這裡是整批路徑）
        claimed = (await session.execute(
            select(_fn.count(CrmProjectExpense.id))
            .where(CrmProjectExpense.cost_group_id == group_id,
                   CrmProjectExpense.claim_id.isnot(None)))).scalar_one()
        if claimed:
            raise HTTPException(
                status_code=409,
                detail=f"這張子表有 {claimed} 筆已進零用金請款單的雜支，"
                       "刪表會讓請款單對不上。請先到零用金頁處理那些請款單。")
        # cascade cost_lines + expenses（receipt 實體檔目前保留，不做磁碟清理）
        await session.execute(delete(CrmProjectCostLine).where(CrmProjectCostLine.cost_group_id == group_id))
        await session.execute(delete(CrmProjectExpense).where(CrmProjectExpense.cost_group_id == group_id))
        await session.delete(g)
        await session.commit()
    return {"status": "ok"}


@router.post("/cost-groups/{group_id}/duplicate")
async def duplicate_cost_group(group_id: str, req: CostGroupDuplicate, request: Request):
    """複製整張子表（含 cost_lines；結算值清空），雜支不複製。"""
    _check_project_write_auth(request)   # 預算表：跟專案本體同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        src = await session.get(CrmProjectCostGroup, group_id)
        if not src:
            raise HTTPException(status_code=404, detail="找不到此子表")
        shoot_date = _parse_shoot_date(req.shoot_date) if req.shoot_date else None
        # 新 group 的 sort_order 放最後
        from sqlalchemy import func as _fn
        max_sort = (await session.execute(
            select(_fn.coalesce(_fn.max(CrmProjectCostGroup.sort_order), 0))
            .where(CrmProjectCostGroup.project_id == src.project_id)
        )).scalar_one()
        new_g = CrmProjectCostGroup(
            id=uuid.uuid4().hex, project_id=src.project_id,
            name=req.name.strip(), shoot_date=shoot_date,
            notes=src.notes, sort_order=(max_sort or 0) + 1,
            budget_amount=src.budget_amount, misc_budget_amount=src.misc_budget_amount,
            profit_target_pct=src.profit_target_pct,
            receipt_path=src.receipt_path,
        )
        session.add(new_g)
        # 複製 cost_lines（結算欄位清空）
        src_lines = (await session.execute(
            select(CrmProjectCostLine)
            .where(CrmProjectCostLine.cost_group_id == group_id)
            .order_by(CrmProjectCostLine.phase, CrmProjectCostLine.sort_order)
        )).scalars().all()
        for l in src_lines:
            session.add(CrmProjectCostLine(
                id=uuid.uuid4().hex, project_id=src.project_id, cost_group_id=new_g.id,
                phase=l.phase, item_name=l.item_name, sort_order=l.sort_order,
                estimated_unit_price=l.estimated_unit_price,
                estimated_quantity=l.estimated_quantity,
                estimated_unit_type=l.estimated_unit_type,
                estimated_amount=l.estimated_amount,
                estimated_staff_id=l.estimated_staff_id,
                estimated_notes=l.estimated_notes,
                # 結算欄位不複製
            ))
        await session.commit()
        await session.refresh(new_g)
        summary = await _compute_group_summary(session, new_g.id)
        _pct = await _project_misc_pct(session, new_g.project_id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(new_g, summary, _pct), "lines_copied": len(src_lines)}


@router.get("/cost-groups/{group_id}/summary", dependencies=[Depends(money_dep)])
async def get_cost_group_summary(group_id: str, request: Request):
    """單一子表的儀表板資料。

    🔴 要自己補列級的帳本守衛：`money_dep` 的私帳那道只認路徑參數
    `project_id`，這支的路徑參數是 `group_id`，所以整道跳過 —— 有 money_view
    但沒有 finance_mine 的人，拿到 group_id 就讀得到 mine 專案的成本合計。
    發票／請款／收支那幾支都各自補了 require_entity，只有這支漏掉
    （/simplify 2026-08-25；dev 上 mine 專案還沒有成本子表，所以尚未可利用）。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        from core.ledger import require_entity
        proj = await session.get(CrmProject, g.project_id) if g.project_id else None
        if proj is not None:
            require_entity(request, proj.entity or "parent", level="full")
        summary = await _compute_group_summary(session, group_id)
        # 雜支比在 session 還開著時查（同清單那支：原本寫在 return 裡，
        # 那時 `async with` 已經結束）
        _pct = await _project_misc_pct(session, g.project_id)
    return {"cost_group": _cost_group_to_dict(g, summary, _pct)}
