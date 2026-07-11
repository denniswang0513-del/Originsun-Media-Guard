"""
api_cashflow.py — B3 現金流：付款節點 CRUD + 90 天現金流預測（BIZ_PLAN B3）

- 付款節點：專案的訂金/期中/尾款排程（未到期→待請款→已請款→已收款），
  一鍵套模板從合約金額按比例生成。N1 上線後 trigger_phase 自動推進（預留欄）。
- 預測：流入=節點（按 due_date 週分桶）；流出=請款單未付（按 planned_month）
  + 固定月成本（settings finance.monthly_fixed_costs）。逾期節點單獨列（該催了）。
- 通知：目前由 UI 紅字呈現；Google Chat webhook 設定後掛每日提醒（TODO）。
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from config import load_settings
from core.auth import check_admin_or_module
from core.schemas import MilestonePayload, MonthClosePayload
from routers.crm._shared import _parse_day, _username, _validate_month

router = APIRouter(prefix="/api/v1/cashflow", tags=["cashflow"])

MILESTONE_STATUSES = ["未到期", "待請款", "已請款", "已收款"]
_DEFAULT_TEMPLATE = [("訂金", 30), ("期中款", 40), ("尾款", 30)]


def _guard(request: Request):
    check_admin_or_module(request, "crm_invoices", "crm_projects")


from core.db_guard import db_factory_or_503 as _factory_or_503


def _ms_dict(m, pname: str = ""):
    return {
        "id": m.id, "project_id": m.project_id, "project_name": pname,
        "label": m.label, "amount": m.amount,
        "due_date": m.due_date.strftime("%Y-%m-%d") if m.due_date else None,
        "status": m.status, "invoice_id": m.invoice_id,
        "sort_order": m.sort_order, "note": m.note,
    }


@router.get("/milestones")
async def list_milestones(request: Request, project_id: str = ""):
    _guard(request)
    from sqlalchemy import select
    from db.models import PaymentMilestone, CrmProject
    factory = _factory_or_503()
    async with factory() as session:
        q = select(PaymentMilestone).order_by(
            PaymentMilestone.project_id, PaymentMilestone.sort_order)
        if project_id:
            q = q.where(PaymentMilestone.project_id == project_id)
        rows = (await session.execute(q)).scalars().all()
        pids = {r.project_id for r in rows}
        names = {}
        if pids:
            for pid, name in (await session.execute(
                    select(CrmProject.id, CrmProject.name).where(CrmProject.id.in_(pids)))).all():
                names[pid] = name
    return {"milestones": [_ms_dict(m, names.get(m.project_id, "")) for m in rows]}


@router.post("/milestones")
async def create_milestone(payload: MilestonePayload, request: Request):
    _guard(request)
    if not payload.project_id:
        raise HTTPException(status_code=422, detail="project_id 必填")
    if payload.status and payload.status not in MILESTONE_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 需為 {MILESTONE_STATUSES}")
    from db.models import PaymentMilestone
    factory = _factory_or_503()
    async with factory() as session:
        m = PaymentMilestone(
            id=uuid.uuid4().hex, project_id=payload.project_id,
            label=payload.label or "付款節點", amount=payload.amount,
            due_date=_parse_day(payload.due_date),
            status=payload.status or "未到期",
            invoice_id=payload.invoice_id,
            sort_order=payload.sort_order or 0, note=payload.note,
        )
        session.add(m)
        await session.commit()
        return _ms_dict(m)


@router.put("/milestones/{mid}")
async def update_milestone(mid: str, payload: MilestonePayload, request: Request):
    _guard(request)
    if payload.status and payload.status not in MILESTONE_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 需為 {MILESTONE_STATUSES}")
    from db.models import PaymentMilestone
    factory = _factory_or_503()
    async with factory() as session:
        m = await session.get(PaymentMilestone, mid)
        if not m:
            raise HTTPException(status_code=404, detail="節點不存在")
        if payload.label:
            m.label = payload.label
        if payload.amount is not None:
            m.amount = payload.amount
        if payload.due_date is not None:
            m.due_date = _parse_day(payload.due_date) if payload.due_date else None
        if payload.status:
            m.status = payload.status
        if payload.invoice_id is not None:
            m.invoice_id = payload.invoice_id or None
        if payload.sort_order is not None:
            m.sort_order = payload.sort_order
        if payload.note is not None:
            m.note = payload.note
        m.updated_at = datetime.now()
        await session.commit()
        return _ms_dict(m)


@router.delete("/milestones/{mid}")
async def delete_milestone(mid: str, request: Request):
    _guard(request)
    from db.models import PaymentMilestone
    factory = _factory_or_503()
    async with factory() as session:
        m = await session.get(PaymentMilestone, mid)
        if not m:
            raise HTTPException(status_code=404, detail="節點不存在")
        await session.delete(m)
        await session.commit()
    return {"ok": True}


@router.post("/milestones/template/{project_id}")
async def apply_template(project_id: str, request: Request):
    """從合約金額按 30/40/30 生成三節點（已有節點的專案拒絕，避免蓋掉手工排程）。"""
    _guard(request)
    from sqlalchemy import select, func as safunc
    from db.models import PaymentMilestone, CrmProject
    factory = _factory_or_503()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="專案不存在")
        existing = (await session.execute(
            select(safunc.count(PaymentMilestone.id))
            .where(PaymentMilestone.project_id == project_id))).scalar() or 0
        if existing:
            raise HTTPException(status_code=409, detail="此專案已有節點，請手動編輯")
        total = proj.contract_amount or 0
        if total <= 0:
            raise HTTPException(status_code=422, detail="專案未填合約金額，無法套模板")
        created = []
        acc = 0
        for i, (label, pct) in enumerate(_DEFAULT_TEMPLATE):
            amt = (total - acc) if i == len(_DEFAULT_TEMPLATE) - 1 else round(total * pct / 100)
            acc += amt
            m = PaymentMilestone(id=uuid.uuid4().hex, project_id=project_id,
                                 label=label, amount=amt, sort_order=i)
            session.add(m)
            created.append(m)
        await session.commit()
        return {"milestones": [_ms_dict(m, proj.name) for m in created]}


@router.get("/forecast")
async def forecast(request: Request, days: int = 90):
    """90 天現金流：週分桶（流入=節點、流出=未付請款+固定成本），逾期/未排期另列。"""
    _guard(request)
    days = max(14, min(days, 365))
    from sqlalchemy import select
    from db.models import PaymentMilestone, CrmPaymentRequest, CrmProject
    factory = _factory_or_503()

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=days)
    fixed_monthly = int((load_settings().get("finance") or {}).get("monthly_fixed_costs") or 0)

    async with factory() as session:
        ms = (await session.execute(
            select(PaymentMilestone).where(PaymentMilestone.status != "已收款"))).scalars().all()
        prs = (await session.execute(
            select(CrmPaymentRequest).where(CrmPaymentRequest.payment_status != "已付款"))).scalars().all()
        pids = {m.project_id for m in ms}
        names = {}
        if pids:
            for pid, name in (await session.execute(
                    select(CrmProject.id, CrmProject.name).where(CrmProject.id.in_(pids)))).all():
                names[pid] = name

    # 週分桶
    n_weeks = (days + 6) // 7
    weeks = [{"start": (today + timedelta(days=i * 7)).strftime("%m/%d"),
              "inflow": 0, "outflow": 0} for i in range(n_weeks)]

    overdue, unscheduled = [], 0
    for m in ms:
        item = _ms_dict(m, names.get(m.project_id, ""))
        if not m.due_date:
            unscheduled += (m.amount or 0)
            continue
        d = m.due_date.replace(tzinfo=None) if m.due_date.tzinfo else m.due_date
        if d < today:
            overdue.append(item)
        elif d < horizon:
            weeks[min(int((d - today).days // 7), n_weeks - 1)]["inflow"] += (m.amount or 0)

    for p in prs:
        # 流出時點：planned_month 月中；沒填則落最近一週（保守：當作快要付）
        when = today
        if p.planned_month:
            try:
                when = datetime.strptime(p.planned_month + "-15", "%Y-%m-%d")
            except ValueError:
                when = today
        if when < today:
            when = today
        if when < horizon:
            weeks[min(int((when - today).days // 7), n_weeks - 1)]["outflow"] += (p.amount or 0)

    # 固定月成本：攤在每月第一個落在 horizon 內的週
    cursor = today.replace(day=1)
    while cursor < horizon:
        if cursor >= today and fixed_monthly:
            weeks[min(int((cursor - today).days // 7), n_weeks - 1)]["outflow"] += fixed_monthly
        cursor = (cursor + timedelta(days=32)).replace(day=1)

    cum = 0
    for w in weeks:
        w["net"] = w["inflow"] - w["outflow"]
        cum += w["net"]
        w["cum"] = cum

    return {
        "weeks": weeks,
        "overdue": sorted(overdue, key=lambda x: x["due_date"] or ""),
        "unscheduled_inflow": unscheduled,
        "fixed_monthly": fixed_monthly,
        "horizon_days": days,
    }


# ── F1 月結鎖帳（HR_FIN_PLAN F1）────────────────────────────────
# 鎖定月的收支不可改（守衛唯一實作在 routers/crm/_shared.py _assert_month_open）；
# snapshot 留當月彙總讓報表數字可重現。重開留稽核痕跡，重鎖會刷新 snapshot。
#
# 快照 v2（財務階段三）：{v:2, income/expense/entry_count/expense_by_category
# （v1 四欄留頂層 — crm-cashflow.js 月結列表直接讀 snapshot.income，別動）,
# cash:{同 v1 四欄，規格巢狀}, pnl, bs, cf, checks:{bs_diff, cf_diff}}。
# 三表部分由 services/finance_statements.month_close_extras 算（與 GET
# /api/v1/finance/statements 同一套聚合）；/statements 讀快照的判準是
# snapshot.get("v")==2 且含 "pnl" 鍵，v1 舊快照視同未鎖 → live 算（相容）。

async def _month_snapshot(session, month: str) -> dict:
    from sqlalchemy import select, func as safunc
    from db.models import CrmCashEntry
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    end = (start + timedelta(days=32)).replace(day=1)
    cond = (CrmCashEntry.entry_date >= start) & (CrmCashEntry.entry_date < end)
    total = (await session.execute(
        select(safunc.coalesce(safunc.sum(CrmCashEntry.deposit), 0),
               safunc.coalesce(safunc.sum(CrmCashEntry.expense), 0),
               safunc.count(CrmCashEntry.id)).where(cond))).one()
    by_cat = (await session.execute(
        select(CrmCashEntry.category,
               safunc.coalesce(safunc.sum(CrmCashEntry.expense), 0))
        .where(cond).group_by(CrmCashEntry.category))).all()
    return {
        "income": int(total[0] or 0), "expense": int(total[1] or 0),
        "entry_count": int(total[2] or 0),
        "expense_by_category": {(c or "未分類"): int(v or 0) for c, v in by_cat},
    }


@router.get("/month-close")
async def list_month_close(request: Request):
    _guard(request)
    from sqlalchemy import select
    from db.models import FinanceMonthClose
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceMonthClose).order_by(FinanceMonthClose.month.desc()))).scalars().all()
    return {"months": [{
        "month": r.month, "closed_by": r.closed_by,
        "closed_at": r.closed_at.strftime("%Y-%m-%d %H:%M") if r.closed_at else "",
        "reopened_by": r.reopened_by,
        "reopened_at": r.reopened_at.strftime("%Y-%m-%d %H:%M") if r.reopened_at else None,
        "locked": r.reopened_at is None,
        "snapshot": r.snapshot or {},
    } for r in rows]}


@router.post("/month-close")
async def close_month(payload: MonthClosePayload, request: Request):
    _guard(request)
    month = _validate_month(payload.month)
    from sqlalchemy import select
    from db.models import FinanceMonthClose
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        cash = await _month_snapshot(session, month)
        extras, warnings = await fs.month_close_extras(session, month)
        # v1 四欄留頂層（前端月結列表讀 snapshot.income）+ 規格巢狀 cash
        snap = {"v": 2, **cash, "cash": cash, **extras}
        row = (await session.execute(
            select(FinanceMonthClose).where(FinanceMonthClose.month == month))).scalar_one_or_none()
        if row and row.reopened_at is None:
            raise HTTPException(status_code=409, detail=f"{month} 已鎖帳")
        if row:  # 重開後再鎖：刷新 snapshot、清稽核欄
            row.closed_by = _username(request)
            row.closed_at = datetime.now()
            row.snapshot = snap
            row.reopened_by = None
            row.reopened_at = None
        else:
            session.add(FinanceMonthClose(
                id=uuid.uuid4().hex, month=month,
                closed_by=_username(request), snapshot=snap))
        await session.commit()
    # warnings 不擋鎖帳（未歸類/未掛帳戶/該月對帳缺漏）— 誠實回報給前端顯示
    return {"ok": True, "month": month, "snapshot": snap, "warnings": warnings}


@router.post("/month-close/reopen")
async def reopen_month(payload: MonthClosePayload, request: Request):
    _guard(request)
    month = _validate_month(payload.month)
    from sqlalchemy import select
    from db.models import FinanceMonthClose
    factory = _factory_or_503()
    async with factory() as session:
        row = (await session.execute(
            select(FinanceMonthClose).where(FinanceMonthClose.month == month))).scalar_one_or_none()
        if not row or row.reopened_at is not None:
            raise HTTPException(status_code=404, detail=f"{month} 未在鎖定狀態")
        row.reopened_by = _username(request)
        row.reopened_at = datetime.now()
        await session.commit()
    return {"ok": True, "month": month}
