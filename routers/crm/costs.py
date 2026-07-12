"""routers/crm/costs.py — 專案成本：雜支 / 收據儲存 / 財務摘要 /
成本估算 cost lines / 成本估算範本 / 成本子表 cost groups。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
upload_expense_receipt 內以 __file__ 推 uploads 路徑的運算多包一層
os.path.dirname（檔案移深一層，維持原專案根目錄基準）。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional

from fastapi import HTTPException, Request, UploadFile, File, Query

from core.schemas import (ProjectExpensePayload, ProjectExpensePatchPayload,
                          CostLinePayload, CostLineUpdatePayload,
                          CostGroupCreate, CostGroupUpdate, CostGroupDuplicate)

from ._shared import (router, _check_auth, _require_db, _get_factory, _now,
                      _parse_shoot_date, _seed_default_expenses)

try:
    from ._shared import (select, delete,
                          CrmProject, CrmProjectExpense, CrmProjectStaff,
                          CrmProjectCostLine, CrmCostLineTemplate,
                          CrmProjectCostGroup, CrmQuotation, CrmQuotationItem,
                          CrmStaff, CrmPaymentRequest)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── Project Expense (雜支) Endpoints ────────────────────────

@router.get("/projects/{project_id}/expenses")
async def list_project_expenses(project_id: str, group_id: Optional[str] = Query(None)):
    """列出專案雜支。
    - 無 group_id：回整個專案的雜支 + 多一層 grouped_by_group
    - 有 group_id：只回該子表的雜支"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        if group_id:
            q = q.where(CrmProjectExpense.cost_group_id == group_id)
        rows = (await session.execute(q)).scalars().all()

        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()

    def _e_to_dict(e):
        return {
            "id": e.id, "category": e.category,
            "cost_group_id": e.cost_group_id or "",
            "estimated": e.estimated, "actual": e.actual,
            "sub_item": e.sub_item or "", "payee": e.payee or "",
            "advance_id": e.advance_id or "",
            "receipt_url": e.receipt_url or "", "notes": e.notes or "",
            "created_at": _fmt_date(e.created_at),
        }

    expenses = [_e_to_dict(e) for e in rows]
    grouped_by_group = [{
        "group_id": g.id, "group_name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "expenses": [_e_to_dict(e) for e in rows if e.cost_group_id == g.id],
    } for g in groups]
    return {"expenses": expenses, "grouped_by_group": grouped_by_group}


async def _create_expense(session, project_id: str, req, advance_id=None, payee_override=None):
    """建立專案雜支的共用 helper。"""
    cost_group_id = await _resolve_target_group(session, project_id, req.cost_group_id)
    e = CrmProjectExpense(
        id=uuid.uuid4().hex, project_id=project_id,
        cost_group_id=cost_group_id,
        category=req.category, estimated=req.estimated,
        actual=req.actual, sub_item=req.sub_item or None,
        payee=payee_override or req.payee or None,
        advance_id=advance_id or req.advance_id or None,
        notes=req.notes, created_at=_now(),
    )
    session.add(e)
    await session.commit()
    return e


@router.post("/advance/{advance_id}/expenses")
async def add_advance_expense(advance_id: str, req: ProjectExpensePayload):
    """公開端點：透過預支款 ID 登記支出（不需登入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        adv = await session.get(CrmPaymentRequest, advance_id)
        if not adv or not adv.is_advance:
            raise HTTPException(status_code=404, detail="找不到此預支款")
        if not adv.project_id:
            raise HTTPException(status_code=400, detail="此預支款未綁定專案")
        e = await _create_expense(session, adv.project_id, req, advance_id=advance_id, payee_override=adv.payee_name)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.post("/public/projects/{project_id}/expenses")
async def add_public_project_expense(project_id: str, req: ProjectExpensePayload):
    """公開端點：透過專案 ID 登記雜支（不需登入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.get("/public/cost-groups/{group_id}/info")
async def get_public_cost_group_info(group_id: str):
    """公開端點：取得子表 + 所屬專案資訊（不需登入，供 /group-expense.html 使用）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        proj = await session.get(CrmProject, g.project_id)
    return {
        "group": {
            "id": g.id, "name": g.name,
            "shoot_date": _fmt_date(g.shoot_date),
            "notes": g.notes or "",
            "budget_amount": g.budget_amount,
            "misc_budget_amount": g.misc_budget_amount,
        },
        "project": {"id": proj.id, "name": proj.name} if proj else None,
    }


@router.get("/public/cost-groups/{group_id}/expenses")
async def list_public_cost_group_expenses(group_id: str):
    """公開端點：列出該子表最近 20 筆已登記雜支（前端只渲染 10 筆，多撈一些保留彈性）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.cost_group_id == group_id)
            .order_by(CrmProjectExpense.created_at.desc())
            .limit(20)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


@router.post("/public/cost-groups/{group_id}/expenses")
async def add_public_cost_group_expense(group_id: str, req: ProjectExpensePayload):
    """公開端點：登記雜支到指定子表（強制 cost_group_id = URL 參數，防呼叫端注入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        req.cost_group_id = group_id  # 覆寫 payload 以強制歸屬
        e = await _create_expense(session, g.project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}, "project_id": g.project_id}


@router.post("/public/cost-groups/{group_id}/receipts/{expense_id}")
async def upload_public_cost_group_receipt(group_id: str, expense_id: str, file: UploadFile = File(...)):
    """公開端點：上傳收據到指定子表的 expense。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        e = await session.get(CrmProjectExpense, expense_id)
        if not e or e.cost_group_id != group_id:
            raise HTTPException(status_code=404, detail="此雜支不屬於此子表")
    return await _save_receipt(g.project_id, expense_id, file)


@router.get("/public/projects/{project_id}/info")
async def get_public_project_info(project_id: str):
    """公開端點：取得專案名稱 + 子表列表（不需登入，供公開雜支頁選子表）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()
    return {
        "id": proj.id, "name": proj.name,
        "cost_groups": [{
            "id": g.id, "name": g.name,
            "shoot_date": _fmt_date(g.shoot_date),
        } for g in groups],
    }


@router.get("/public/projects/{project_id}/expenses")
async def list_public_project_expenses(project_id: str):
    """公開端點：列出專案雜支（不需登入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


@router.post("/projects/{project_id}/expenses")
async def add_project_expense(project_id: str, req: ProjectExpensePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.patch("/project-expenses/{expense_id}")
async def patch_project_expense(expense_id: str, req: ProjectExpensePatchPayload, request: Request):
    """部分更新雜支欄位（供前端 inline edit 使用）。僅動 payload 有給的欄位。"""
    _check_auth(request)
    _require_db()
    data = req.model_dump(exclude_unset=True)  # 只要有帶就進，None 也算
    if not data:
        return {"status": "ok"}
    factory = await _get_factory()
    _NULLABLE_TEXT = {"sub_item", "payee", "notes", "advance_id", "cost_group_id"}
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        for key, val in data.items():
            if key in _NULLABLE_TEXT and val == "":
                val = None
            setattr(e, key, val)
        await session.commit()
    return {"status": "ok"}


@router.patch("/project-expenses/link-advance")
async def link_expenses_to_advance(request: Request):
    """批次綁定/解除雜支與預支款。body: {expense_ids: [...], advance_id: "..." 或 ""}"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    expense_ids = body.get("expense_ids", [])
    advance_id = body.get("advance_id")
    if not expense_ids:
        raise HTTPException(status_code=400, detail="缺少 expense_ids")
    factory = await _get_factory()
    async with factory() as session:
        for eid in expense_ids:
            e = await session.get(CrmProjectExpense, eid)
            if e:
                e.advance_id = advance_id if advance_id else None
        await session.commit()
    return {"status": "ok", "linked": len(expense_ids)}


@router.put("/project-expenses/{expense_id}")
async def update_project_expense(expense_id: str, req: ProjectExpensePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        e.category = req.category
        e.estimated = req.estimated
        e.actual = req.actual
        e.sub_item = req.sub_item or None
        e.payee = req.payee or None
        e.advance_id = req.advance_id or None
        e.notes = req.notes
        if req.cost_group_id:
            e.cost_group_id = req.cost_group_id
        await session.commit()
    return {"status": "ok"}


@router.delete("/project-expenses/{expense_id}")
async def delete_project_expense(expense_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        await session.delete(e)
        await session.commit()
    return {"status": "ok"}


@router.post("/project-expenses/{expense_id}/receipt")
async def upload_expense_receipt(expense_id: str, request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    ext = os.path.splitext(file.filename or "img.jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"):
        raise HTTPException(status_code=400, detail="不支援的檔案格式")

    upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads", "receipts")
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{expense_id}{ext}"
    filepath = os.path.join(upload_dir, filename)

    content = await file.read()
    import pathlib
    await asyncio.to_thread(pathlib.Path(filepath).write_bytes, content)

    receipt_url = f"/uploads/receipts/{filename}"
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        e.receipt_url = receipt_url
        await session.commit()

    return {"status": "ok", "receipt_url": receipt_url}


# ── Per-Project Receipt Storage ────────────────────────────

@router.post("/projects/{project_id}/receipts/{expense_id}")
async def upload_project_receipt(project_id: str, expense_id: str, request: Request, file: UploadFile = File(...)):
    """上傳收據到專案收據資料夾。"""
    _check_auth(request)
    return await _save_receipt(project_id, expense_id, file)


@router.post("/public/projects/{project_id}/receipts/{expense_id}")
async def upload_project_receipt_public(project_id: str, expense_id: str, file: UploadFile = File(...)):
    """公開端點：上傳收據。"""
    return await _save_receipt(project_id, expense_id, file)


async def _save_receipt(project_id: str, expense_id: str, file: UploadFile):
    import re as _re
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        exp = await session.get(CrmProjectExpense, expense_id)
        if not exp:
            raise HTTPException(status_code=404, detail="找不到此支出")

        # 路徑優先序：cost_group.receipt_path → uploads/receipts/{project_name}/{group_name}/
        # 沒 cost_group 關聯時 fallback 到專案層級 uploads 子目錄。
        cg = await session.get(CrmProjectCostGroup, exp.cost_group_id) if exp.cost_group_id else None
        if cg and cg.receipt_path:
            base = cg.receipt_path
        else:
            sub = (cg.name if cg and cg.name else "main")
            base = os.path.join(os.getcwd(), "uploads", "receipts",
                                proj.name or project_id, sub)
        os.makedirs(base, exist_ok=True)

        # Build filename: date_category_subitem_payee_id.ext
        date_str = exp.created_at.strftime("%Y%m%d") if exp.created_at else "nodate"
        cat = _re.sub(r'[\\/:*?"<>|]', '', exp.category or "misc")
        sub = _re.sub(r'[\\/:*?"<>|]', '', exp.sub_item or "")
        payee = _re.sub(r'[\\/:*?"<>|]', '', exp.payee or "")
        parts = [date_str, cat]
        if sub:
            parts.append(sub)
        if payee:
            parts.append(payee)
        parts.append(expense_id[:8])
        ext = os.path.splitext(file.filename or ".jpg")[1]
        filename = "_".join(parts) + ext

        filepath = os.path.join(base, filename)
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        # Save receipt_url to expense
        exp.receipt_url = filepath
        await session.commit()

    return {"status": "ok", "path": filepath, "filename": filename}


@router.get("/projects/{project_id}/receipts")
async def list_project_receipts(project_id: str, request: Request):
    """列出專案下所有子表的收據檔（依 cost_group 聚合）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()

    result_groups = []
    for g in groups:
        base = g.receipt_path if g.receipt_path else os.path.join(
            os.getcwd(), "uploads", "receipts", proj.name or project_id, g.name or "main")
        files = []
        if os.path.isdir(base):
            for fn in sorted(os.listdir(base)):
                fp = os.path.join(base, fn)
                if os.path.isfile(fp):
                    files.append({"filename": fn, "path": fp, "size": os.path.getsize(fp)})
        result_groups.append({
            "cost_group_id": g.id, "cost_group_name": g.name,
            "path": base, "receipts": files,
        })
    return {"groups": result_groups}


@router.get("/cost-groups/{group_id}/receipts")
async def list_cost_group_receipts(group_id: str, request: Request):
    """列出單一子表收據資料夾內的所有檔案。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        proj = await session.get(CrmProject, g.project_id)

    base = g.receipt_path if g.receipt_path else os.path.join(
        os.getcwd(), "uploads", "receipts",
        (proj.name if proj else g.project_id), g.name or "main")
    if not os.path.isdir(base):
        return {"receipts": [], "path": base}
    files = []
    for fn in sorted(os.listdir(base)):
        fp = os.path.join(base, fn)
        if os.path.isfile(fp):
            files.append({"filename": fn, "path": fp, "size": os.path.getsize(fp)})
    return {"receipts": files, "path": base}


@router.get("/receipt-file")
async def serve_receipt(path: str = Query(""), request: Request = None):
    """提供收據檔案下載/檢視（限定 uploads/ 或子表 receipt_path）。"""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="檔案不存在")
    abs_path = os.path.abspath(path)
    uploads_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads"))
    if not abs_path.startswith(uploads_dir):
        # 檢查是否在某個子表的 receipt_path 內
        _require_db()
        factory = await _get_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(CrmProjectCostGroup.receipt_path)
                .where(CrmProjectCostGroup.receipt_path.isnot(None))
            )).scalars().all()
        if not any(rp and abs_path.startswith(os.path.abspath(rp)) for rp in rows):
            raise HTTPException(status_code=403, detail="無權存取此路徑")
    from starlette.responses import FileResponse
    return FileResponse(path)


@router.get("/projects/{project_id}/financial-summary")
async def project_financial_summary(project_id: str):
    """專案財務摘要：含稅/未稅/毛利/雜支/外包 預估vs實際。
    多子表後另附：allocated_budget_sum / groups_count / groups_missing_budget_count。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        from sqlalchemy import func as sa_func

        exp_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectExpense.estimated), 0),
                   sa_func.coalesce(sa_func.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.project_id == project_id)
        )).first()
        expense_estimated = exp_row[0] if exp_row else 0
        expense_actual = exp_row[1] if exp_row else 0

        staff_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectStaff.cost), 0),
                   sa_func.coalesce(sa_func.sum(sa_func.coalesce(CrmProjectStaff.actual_cost, CrmProjectStaff.cost)), 0))
            .where(CrmProjectStaff.project_id == project_id)
        )).first()
        staff_estimated = staff_row[0] if staff_row else 0
        staff_actual = staff_row[1] if staff_row else 0

        costline_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectCostLine.estimated_amount), 0),
                   sa_func.coalesce(sa_func.sum(CrmProjectCostLine.actual_amount), 0))
            .where(CrmProjectCostLine.project_id == project_id)
        )).first()
        costline_estimated = costline_row[0] if costline_row else 0
        costline_actual = costline_row[1] if costline_row else 0

        # 跨子表彙總（預算分配）— 單次聚合，不 hydrate ORM 物件
        from sqlalchemy import case as _case
        g_row = (await session.execute(
            select(
                sa_func.coalesce(sa_func.sum(
                    sa_func.coalesce(CrmProjectCostGroup.budget_amount, 0)
                    + sa_func.coalesce(CrmProjectCostGroup.misc_budget_amount, 0)
                ), 0),
                sa_func.count(CrmProjectCostGroup.id),
                sa_func.coalesce(sa_func.sum(_case(
                    (CrmProjectCostGroup.budget_amount.is_(None)
                     & CrmProjectCostGroup.misc_budget_amount.is_(None), 1),
                    else_=0,
                )), 0),
            )
            .where(CrmProjectCostGroup.project_id == project_id)
        )).first()
        allocated_budget_sum = int(g_row[0] or 0) if g_row else 0
        groups_count = int(g_row[1] or 0) if g_row else 0
        groups_missing_budget_count = int(g_row[2] or 0) if g_row else 0

    from core.crm_logic import project_margin
    contract = project.contract_amount or 0
    tax_rate = project.tax_rate or 5
    # 毛利公式單一來源（與財務儀表板 Top/Bottom 共用，見 core.crm_logic.project_margin）
    m = project_margin(contract, tax_rate, expense_actual, staff_actual)
    ex_tax = m["ex_tax"]
    profit_target = int(ex_tax * (project.profit_target_pct or 20) / 100)
    misc_budget = int(ex_tax * (project.misc_budget_pct or 5) / 100)
    outsource_budget = ex_tax - profit_target - misc_budget

    total_cost = m["cost"]
    actual_profit = m["margin"]
    profit_rate = round(actual_profit / ex_tax * 100) if ex_tax > 0 else 0

    return {
        "contract_amount": contract, "ex_tax": ex_tax,
        "profit_target": profit_target, "profit_target_pct": project.profit_target_pct or 20,
        "misc_budget": misc_budget, "misc_budget_pct": project.misc_budget_pct or 5,
        "outsource_budget": outsource_budget,
        "expense_estimated": expense_estimated, "expense_actual": expense_actual,
        "staff_estimated": staff_estimated, "staff_actual": staff_actual,
        "total_cost": total_cost, "actual_profit": actual_profit, "profit_rate": profit_rate,
        "payment_status": project.payment_status or "未到帳",
        "amount_receivable": project.amount_receivable,
        "amount_received": project.amount_received,
        "transfer_fee": project.transfer_fee,
        "costline_estimated": costline_estimated,
        "costline_actual": costline_actual,
        # 多子表擴充
        "allocated_budget_sum": allocated_budget_sum,
        "groups_count": groups_count,
        "groups_missing_budget_count": groups_missing_budget_count,
    }


# ── Phase helpers ────────────────────────────────────────────
PHASE_ORDER = ("前期製作", "現場拍攝", "後期製作")


def _fmt_date(dt) -> Optional[str]:
    """Datetime → 'YYYY-MM-DD'，None 時回 None。"""
    return dt.isoformat()[:10] if dt else None


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

@router.get("/projects/{project_id}/cost-lines")
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
    _check_auth(request)
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
        # Also back-fill the 10 default 行政雜支 categories — legacy projects
        # created before this feature won't have them.
        added_exp = await _seed_default_expenses(session, project_id, target_gid)
        await session.commit()
    return {"status": "ok", "added": added, "added_expenses": added_exp,
            "cost_group_id": target_gid}


@router.post("/projects/{project_id}/cost-lines")
async def add_project_cost_line(project_id: str, req: CostLinePayload, request: Request):
    """新增單一自訂成本項目。必須指定 cost_group_id，否則自動歸入主表。"""
    _check_auth(request)
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
    _check_auth(request)
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
    _check_auth(request)
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
    _check_auth(request)
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

@router.get("/cost-line-templates")
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
    _check_auth(request)
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
    _check_auth(request)
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
    _check_auth(request)
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
    _check_auth(request)
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
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        tpl = await session.get(CrmCostLineTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="找不到此範本")
        await session.delete(tpl)
        await session.commit()
    return {"status": "ok"}


# ── Cost Groups (成本子表) Endpoints ─────────────────────────

async def _get_first_group_id(session, project_id: str) -> Optional[str]:
    """專案的首張子表 id（依 sort_order + created_at）。純查詢，無副作用。"""
    return (await session.execute(
        select(CrmProjectCostGroup.id)
        .where(CrmProjectCostGroup.project_id == project_id)
        .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        .limit(1)
    )).scalar_one_or_none()


async def _resolve_target_group(session, project_id: str, supplied: Optional[str]) -> str:
    """回傳目標子表 id：供給者優先，否則取首張（主表）。
    若專案完全沒有子表（migration 漏網 / create_project 失敗），
    自我修復建立主表（flush，不 commit，交由外層 commit）。"""
    if supplied:
        return supplied
    gid = await _get_first_group_id(session, project_id)
    if gid:
        return gid
    gid = uuid.uuid4().hex
    session.add(CrmProjectCostGroup(id=gid, project_id=project_id, name="主表", sort_order=0))
    await session.flush()
    return gid


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


def _cost_group_to_dict(g, summary: Optional[dict] = None) -> dict:
    total_budget = (g.budget_amount or 0) + (g.misc_budget_amount or 0)
    d = {
        "id": g.id, "project_id": g.project_id, "name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "notes": g.notes or "",
        "sort_order": g.sort_order,
        "budget_amount": g.budget_amount,
        "misc_budget_amount": g.misc_budget_amount,
        "profit_target_pct": g.profit_target_pct,
        "receipt_path": g.receipt_path or "",
        "total_budget": total_budget if (g.budget_amount is not None or g.misc_budget_amount is not None) else None,
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


@router.get("/projects/{project_id}/cost-groups")
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
            .where(CrmProjectCostLine.project_id == project_id)
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
        result.append(_cost_group_to_dict(g, summary))
    return {"cost_groups": result}


@router.post("/projects/{project_id}/cost-groups")
async def create_cost_group(project_id: str, req: CostGroupCreate, request: Request):
    """新增子表。"""
    _check_auth(request)
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
        await _seed_default_expenses(session, project_id, g.id)
        await session.commit()
        await session.refresh(g)
        summary = await _compute_group_summary(session, g.id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(g, summary)}


@router.put("/cost-groups/{group_id}")
async def update_cost_group(group_id: str, req: CostGroupUpdate, request: Request):
    """更新子表（名稱/拍攝日/備註/排序/預算/毛利率），PATCH 風格只動非 None 欄位。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        data = req.model_dump(exclude_none=True)
        if "name" in data:
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
    return {"status": "ok", "cost_group": _cost_group_to_dict(g, summary)}


@router.delete("/cost-groups/{group_id}")
async def delete_cost_group(group_id: str, request: Request):
    """刪除子表（cascade cost_lines + expenses）。若只剩 1 張子表則禁止。"""
    _check_auth(request)
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
        # cascade cost_lines + expenses（receipt 實體檔目前保留，不做磁碟清理）
        await session.execute(delete(CrmProjectCostLine).where(CrmProjectCostLine.cost_group_id == group_id))
        await session.execute(delete(CrmProjectExpense).where(CrmProjectExpense.cost_group_id == group_id))
        await session.delete(g)
        await session.commit()
    return {"status": "ok"}


@router.post("/cost-groups/{group_id}/duplicate")
async def duplicate_cost_group(group_id: str, req: CostGroupDuplicate, request: Request):
    """複製整張子表（含 cost_lines；結算值清空），雜支不複製。"""
    _check_auth(request)
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
        await _seed_default_expenses(session, src.project_id, new_g.id)
        await session.commit()
        await session.refresh(new_g)
        summary = await _compute_group_summary(session, new_g.id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(new_g, summary), "lines_copied": len(src_lines)}


@router.get("/cost-groups/{group_id}/summary")
async def get_cost_group_summary(group_id: str):
    """單一子表的儀表板資料。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        summary = await _compute_group_summary(session, group_id)
    return {"cost_group": _cost_group_to_dict(g, summary)}

