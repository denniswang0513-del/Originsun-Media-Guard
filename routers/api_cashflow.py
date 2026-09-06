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
from core.finance_logic import local_day
from core.schemas import MilestonePayload, MonthClosePayload
from routers.crm._shared import (_parse_day, _username, _validate_month,
                                 project_names_map)

router = APIRouter(prefix="/api/v1/cashflow", tags=["cashflow"])

MILESTONE_STATUSES = ["未到期", "待請款", "已請款", "已收款"]
_DEFAULT_TEMPLATE = [("訂金", 30), ("期中款", 40), ("尾款", 30)]


def _guard(request: Request, entity: str = "", level: str = "view") -> str:
    """財務域守衛 v2：回傳解析後的帳本 entity（docs/LEDGER_ENTITY_PLAN.md §2.4）。

    這支每一格都是錢：付款節點的金額、90 天預測的流入流出 —— 沒有「拿掉數字
    還剩下什麼」可言，所以擋入口而不是抹欄位（owner 2026-08-15；
    docs/MONEY_VISIBILITY.md）。level="view"＝報表層（合夥人 finance_partner
    可及；本檔只有 month-close GET）；level="full"＝寫入與 CRM 域（parent ⟺
    crm_invoices AND money_view、mine ⟺ finance_mine；Lv3 全開）。舊的
    check_admin_or_module + check_money 語意已內含在 require_entity 的 scope
    判定裡 —— 不要再疊舊守衛。"""
    from core.ledger import require_entity
    return require_entity(request, entity, level=level)


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
    _guard(request, "parent", level="full")  # 付款節點綁專案＝母公司 CRM 域（我的帳無專案；合夥人不可及，§2.4）
    from sqlalchemy import select
    from db.models import PaymentMilestone
    factory = _factory_or_503()
    async with factory() as session:
        q = select(PaymentMilestone).order_by(
            PaymentMilestone.project_id, PaymentMilestone.sort_order)
        if project_id:
            q = q.where(PaymentMilestone.project_id == project_id)
        rows = (await session.execute(q)).scalars().all()
        names = await project_names_map(session, rows)
    return {"milestones": [_ms_dict(m, names.get(m.project_id, "")) for m in rows]}


@router.post("/milestones")
async def create_milestone(payload: MilestonePayload, request: Request):
    _guard(request, "parent", level="full")  # 付款節點綁專案＝母公司 CRM 域（合夥人不可及，§2.4）
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
    _guard(request, "parent", level="full")  # 付款節點綁專案＝母公司 CRM 域（合夥人不可及，§2.4）
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
    _guard(request, "parent", level="full")  # 付款節點綁專案＝母公司 CRM 域（合夥人不可及，§2.4）
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
    _guard(request, "parent", level="full")  # 付款節點綁專案＝母公司 CRM 域（合夥人不可及，§2.4）
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
        # 🔴 佔位金額（從私帳帶過來、還沒人確認）不給套模板 —— 那是「我拿到的
        # 那段」不是公司合約額，照它排出來的 30/40/30 節點全部偏低，而節點一旦
        # 生成就變成現金流預測的來源。擋下來比默默算錯好（LEDGER_UNIFY_PLAN §2.2）。
        if getattr(proj, "contract_amount_source", None) == "mine":
            raise HTTPException(
                status_code=422,
                detail="這一案的合約金額是從私帳帶過來的佔位值（待確認），"
                       "請先在專案頁填上公司實際的合約金額再套模板")
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
    """90 天現金流：週分桶（流入=節點、流出=未付請款+貸款攤還+固定成本），
    逾期/未排期另列。"""
    # 預測綁 milestones/專案/固定成本＝母公司 CRM 域（合夥人不可及）；
    # v1 不做我的帳預測（plan §6）
    _guard(request, "parent", level="full")
    days = max(14, min(days, 365))
    from sqlalchemy import select
    from db.models import (PaymentMilestone, CrmPaymentRequest, FinanceLoanPayment)
    factory = _factory_or_503()

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=days)
    fixed_monthly = int((load_settings().get("finance") or {}).get("monthly_fixed_costs") or 0)

    async with factory() as session:
        ms = (await session.execute(
            select(PaymentMilestone).where(PaymentMilestone.status != "已收款"))).scalars().all()
        prs = (await session.execute(
            select(CrmPaymentRequest).where(
                CrmPaymentRequest.payment_status != "已付款",
                CrmPaymentRequest.entity == "parent"))).scalars().all()  # 母公司帳本
        from db.models import FinanceLoan
        loan_pays = (await session.execute(
            select(FinanceLoanPayment)
            .join(FinanceLoan, FinanceLoan.id == FinanceLoanPayment.loan_id)
            .where(FinanceLoanPayment.status != "paid",
                   FinanceLoanPayment.due_date < horizon,
                   FinanceLoan.entity == "parent"))).scalars().all()  # 母公司帳本
        names = await project_names_map(session, ms)

    # 週分桶
    n_weeks = (days + 6) // 7
    weeks = [{"start": (today + timedelta(days=i * 7)).strftime("%m/%d"),
              "inflow": 0, "outflow": 0} for i in range(n_weeks)]

    def bucket(when, side, amount):
        """把金額加到 when 所屬的週桶（超出視窗夾在最後一週）。"""
        weeks[min(int((when - today).days // 7), n_weeks - 1)][side] += amount

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
            bucket(d, "inflow", m.amount or 0)

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
            bucket(when, "outflow", p.amount or 0)

    # 貸款攤還（財務階段四）：未繳期別按 due_date 分桶；逾期未繳保守當作馬上要付
    for lp in loan_pays:
        d = local_day(lp.due_date)
        if not d:
            continue
        when = d if d >= today else today
        if when < horizon:
            bucket(when, "outflow", (lp.principal_due or 0) + (lp.interest_due or 0))

    # 固定月成本：攤在每月第一個落在 horizon 內的週
    cursor = today.replace(day=1)
    while cursor < horizon:
        if cursor >= today and fixed_monthly:
            bucket(cursor, "outflow", fixed_monthly)
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

async def _month_snapshot(session, month: str, entity: str = "parent") -> dict:
    from sqlalchemy import select, func as safunc
    from db.models import CrmCashEntry
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    end = (start + timedelta(days=32)).replace(day=1)
    cond = ((CrmCashEntry.entry_date >= start) & (CrmCashEntry.entry_date < end)
            & (CrmCashEntry.entity == entity))  # 兩本帳：快照只算本帳本的收支
    total = (await session.execute(
        select(safunc.coalesce(safunc.sum(CrmCashEntry.deposit), 0),
               safunc.coalesce(safunc.sum(CrmCashEntry.expense), 0),
               safunc.count(CrmCashEntry.id)).where(cond))).one()
    by_cat = (await session.execute(
        select(CrmCashEntry.category,
               safunc.coalesce(safunc.sum(CrmCashEntry.expense), 0))
        .where(cond).group_by(CrmCashEntry.category))).all()
    cats = {(c or "未分類"): int(v or 0) for c, v in by_cat}
    # 拆項父列的 category 是空的（正本在拆項）—— 不展開的話，拆過的支出整筆
    # 落進快照的「未分類」，而快照是鎖月後**永久**的紀錄。把拆項的分類補回來、
    # 同額從未分類移出（Σ 不變，只是歸對格子）。收入側快照本來就不分類。
    from db.models import CrmCashSplit
    split_cats = (await session.execute(
        select(CrmCashSplit.category,
               safunc.coalesce(safunc.sum(CrmCashSplit.amount), 0))
        .join(CrmCashEntry, CrmCashEntry.id == CrmCashSplit.entry_id)
        .where(cond, CrmCashEntry.expense > 0)
        .group_by(CrmCashSplit.category))).all()
    for c, v in split_cats:
        amt = int(v or 0)
        if not c:
            continue                              # 拆項也沒分類：父列那份本來就在未分類裡，不要再加一次
        cats[c] = cats.get(c, 0) + amt
        cats["未分類"] = cats.get("未分類", 0) - amt   # 從「未分類」把父列那份移出
    if not cats.get("未分類"):
        cats.pop("未分類", None)
    return {
        "income": int(total[0] or 0), "expense": int(total[1] or 0),
        "entry_count": int(total[2] or 0),
        "expense_by_category": cats,
    }


@router.get("/month-close")
async def list_month_close(request: Request, entity: str = ""):
    ent = _guard(request, entity)  # view：合夥人看得到母公司鎖帳狀態（§2.4）
    from sqlalchemy import select
    from db.models import FinanceMonthClose
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceMonthClose).where(FinanceMonthClose.entity == ent)
            .order_by(FinanceMonthClose.month.desc()))).scalars().all()
    return {"months": [{
        "month": r.month, "entity": r.entity or "parent", "closed_by": r.closed_by,
        "closed_at": r.closed_at.strftime("%Y-%m-%d %H:%M") if r.closed_at else "",
        "reopened_by": r.reopened_by,
        "reopened_at": r.reopened_at.strftime("%Y-%m-%d %H:%M") if r.reopened_at else None,
        "locked": r.reopened_at is None,
        "snapshot": r.snapshot or {},
    } for r in rows]}


@router.post("/month-close")
async def close_month(payload: MonthClosePayload, request: Request,
                      entity: str = ""):
    ent = _guard(request, entity, level="full")
    month = _validate_month(payload.month)
    from sqlalchemy import select
    from db.models import FinanceMonthClose
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        cash = await _month_snapshot(session, month, entity=ent)
        extras, warnings = await fs.month_close_extras(session, month, entity=ent)
        # v1 四欄留頂層（前端月結列表讀 snapshot.income）+ 規格巢狀 cash
        snap = {"v": 2, **cash, "cash": cash, **extras}
        row = (await session.execute(
            select(FinanceMonthClose).where(
                FinanceMonthClose.month == month,
                FinanceMonthClose.entity == ent))).scalar_one_or_none()
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
                id=uuid.uuid4().hex, month=month, entity=ent,
                closed_by=_username(request), snapshot=snap))
        await session.commit()
    # warnings 不擋鎖帳（未歸類/未掛帳戶/該月對帳缺漏）— 誠實回報給前端顯示
    return {"ok": True, "month": month, "entity": ent,
            "snapshot": snap, "warnings": warnings}


@router.post("/month-close/reopen")
async def reopen_month(payload: MonthClosePayload, request: Request,
                       entity: str = ""):
    ent = _guard(request, entity, level="full")
    month = _validate_month(payload.month)
    from sqlalchemy import select
    from db.models import FinanceMonthClose
    factory = _factory_or_503()
    async with factory() as session:
        row = (await session.execute(
            select(FinanceMonthClose).where(
                FinanceMonthClose.month == month,
                FinanceMonthClose.entity == ent))).scalar_one_or_none()
        if not row or row.reopened_at is not None:
            raise HTTPException(status_code=404, detail=f"{month} 未在鎖定狀態")
        row.reopened_by = _username(request)
        row.reopened_at = datetime.now()
        await session.commit()
    return {"ok": True, "month": month, "entity": ent}
