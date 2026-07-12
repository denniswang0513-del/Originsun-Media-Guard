"""
api_finance.py — 財務管理階段二/三：科目對映 + 銀行帳戶 + 對帳 + 調整表
+ 設定精靈 + 三表 statements

權責制三表的地基（風格比照 routers/api_cashflow.py）：
- 科目表（finance_accounts）：種子唯讀清單（後台目前只讀，代碼藏引擎）
- category 對映（finance_category_map）：收支/請款/發票的中文 category → 科目
  + 會計處理方式（treatment），批次 upsert + 未對映掃描
- 銀行帳戶（bank_accounts）：期初餘額 + 掛帳收支流水 = 即時餘額
- 對帳（bank_reconciliations）：每帳戶每月一筆，system_balance 後端算
- 調整表（finance_adjustments）：期初/更正/業主往來 — 🔴 不得指向銀行類科目
  （code 11xx），影響現金的修正一律走收支明細；受月結守衛
- 設定精靈（setup-wizard）：一次建帳戶 + 掛歷史收支 + 設基準月 + 期初權益
- 三表（階段三）：GET /statements?period=...（損益/資產負債/現金流量 + 白話
  解讀 + meta；已鎖月優先讀快照 v2）、GET /statements/drilldown?kind=&period=
  （報表列 → 底層明細）。聚合在 services/finance_statements.py、規則在
  core/finance_logic.py 純函式（黃金測試 tests/unit/test_finance_statements.py）。
- 銀行貸款（階段四）：/loans CRUD（建檔即生攤還表；PUT 只重生未繳期別）
  + pay/unpay（自動建/刪收支明細，月結守衛）+ /loans/upcoming 到期清單。
  攤還純函式 amortization_schedule 在 core/finance_logic.py
  （黃金測試 tests/unit/test_finance_loans.py）。

守門：全部端點 check_admin_or_module(request, 'crm_invoices')（沿用帳務模組 key）。
金額一律 Integer 新台幣。純計算規則在 core/finance_logic.py（有單元測試）。
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from config import load_settings, save_settings
from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.finance_logic import (amortization_schedule, bank_running_balance,
                                local_day, period_months, reconciliation_diff,
                                today_start)
from core.schemas import (BankAccountPayload, BulkAssignAccountPayload,
                          FinanceAdjustmentPayload, FinanceCategoryMapPut,
                          FinanceSetupWizardPayload, LoanPayload,
                          LoanPayPayload, ReconciliationPayload)
from routers.crm._shared import (_assert_month_open, _parse_day, _username,
                                 _validate_month)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

MAP_SOURCES = {"cash", "payment", "invoice"}
# ⚠ 改 TREATMENTS / ACCT_KINDS 值域要同步 frontend/tabs/finance/fin-utils.js 的 *_OPTIONS
TREATMENTS = {"direct_expense", "direct_income", "ap_settlement", "ar_settlement",
              "transfer", "tax_vat", "tax_income", "advance", "passthrough", "loan"}
ADJ_TYPES = {"opening", "correction", "owner_in", "owner_out",
             "accountant", "writeoff", "other"}
ACCT_KINDS = {"bank", "cash"}
LOAN_PAY_CATEGORY = "貸款繳款"  # 對映 (cash, 貸款繳款) → 2400/loan（seed_finance）


def _guard(request: Request):
    check_admin_or_module(request, "crm_invoices")


async def _flow_sums_by_account(session, until=None, account_id=None) -> dict:
    """各帳戶掛帳收支的 SUM 聚合 {bank_account_id: {deposit, expense, bank_fee, claim}}。

    流水公式線性（見 finance_logic.bank_running_balance docstring）→ 聚合值包成
    一筆 entry 餵 bank_running_balance 即得餘額，免逐筆搬。until 給對帳用
    （只算 entry_date < until 的流水；未填日期的收支無法定位月份，不計入）。
    account_id 非 None 時只聚合單一帳戶（對帳只需要一個帳戶，免全表掃）。"""
    from sqlalchemy import select, func as safunc
    from db.models import CrmCashEntry
    q = (select(CrmCashEntry.bank_account_id,
                safunc.coalesce(safunc.sum(CrmCashEntry.deposit), 0),
                safunc.coalesce(safunc.sum(CrmCashEntry.expense), 0),
                safunc.coalesce(safunc.sum(CrmCashEntry.bank_fee), 0),
                safunc.coalesce(safunc.sum(CrmCashEntry.claim), 0))
         .where(CrmCashEntry.bank_account_id.isnot(None),
                CrmCashEntry.bank_account_id != ""))
    if account_id is not None:
        q = q.where(CrmCashEntry.bank_account_id == account_id)
    if until is not None:
        q = q.where(CrmCashEntry.entry_date < until)
    q = q.group_by(CrmCashEntry.bank_account_id)
    return {row[0]: {"deposit": int(row[1] or 0), "expense": int(row[2] or 0),
                     "bank_fee": int(row[3] or 0), "claim": int(row[4] or 0)}
            for row in (await session.execute(q)).all()}


async def _unassigned_count(session) -> int:
    from sqlalchemy import select, or_, func as safunc
    from db.models import CrmCashEntry
    return (await session.execute(
        select(safunc.count(CrmCashEntry.id)).where(
            or_(CrmCashEntry.bank_account_id.is_(None),
                CrmCashEntry.bank_account_id == "")))).scalar() or 0


# ── 科目表 ──────────────────────────────────────────────────

def _acct_dict(a) -> dict:
    return {
        "id": a.id, "code": a.code, "name": a.name, "name_plain": a.name_plain,
        "parent_id": a.parent_id, "acct_type": a.acct_type,
        "cf_activity": a.cf_activity, "pnl_group": a.pnl_group,
        "is_system": bool(a.is_system), "sort_order": a.sort_order or 0,
        "active": bool(a.active),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/accounts")
async def list_accounts(request: Request):
    _guard(request)
    from sqlalchemy import select
    from db.models import FinanceAccount
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceAccount).order_by(FinanceAccount.code))).scalars().all()
    return {"items": [_acct_dict(a) for a in rows]}


# ── category → 科目 對映 ────────────────────────────────────

def _map_dict(m) -> dict:
    return {"id": m.id, "source": m.source, "category_text": m.category_text,
            "account_id": m.account_id, "treatment": m.treatment,
            "active": bool(m.active)}


@router.get("/category-map")
async def list_category_map(request: Request):
    _guard(request)
    from sqlalchemy import select
    from db.models import FinanceCategoryMap
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceCategoryMap).order_by(
                FinanceCategoryMap.source, FinanceCategoryMap.category_text))).scalars().all()
    return {"items": [_map_dict(m) for m in rows]}


@router.put("/category-map")
async def upsert_category_map(payload: FinanceCategoryMapPut, request: Request):
    """批次 upsert：以 (source, category_text) 為 key，有則改 account/treatment、無則建。"""
    _guard(request)
    if not payload.items:
        raise HTTPException(status_code=422, detail="items 不可為空")
    for it in payload.items:
        if it.source not in MAP_SOURCES:
            raise HTTPException(status_code=422, detail=f"source 需為 {sorted(MAP_SOURCES)}: {it.source}")
        if it.treatment not in TREATMENTS:
            raise HTTPException(status_code=422, detail=f"treatment 無效: {it.treatment}")
        # 'loan' 只對 source='cash' 有引擎語意（貸款撥款/繳款走收支）；掛到
        # payment/invoice 會靜默錯帳（請款流進損益 + 與 BS 貸款餘額重複列負債）。
        if it.treatment == "loan" and it.source != "cash":
            raise HTTPException(
                status_code=422,
                detail="treatment='loan' 僅適用於收支明細（source='cash'）")
        if not (it.category_text or "").strip():
            raise HTTPException(status_code=422, detail="category_text 不可為空")
    from sqlalchemy import select
    from db.models import FinanceAccount, FinanceCategoryMap
    factory = _factory_or_503()
    async with factory() as session:
        valid_ids = set((await session.execute(select(FinanceAccount.id))).scalars().all())
        bad = [it.account_id for it in payload.items if it.account_id not in valid_ids]
        if bad:
            raise HTTPException(status_code=422, detail=f"科目不存在: {', '.join(sorted(set(bad)))}")
        existing = {(m.source, m.category_text): m for m in (await session.execute(
            select(FinanceCategoryMap))).scalars().all()}
        count = 0
        for it in payload.items:
            key = (it.source, it.category_text.strip())
            row = existing.get(key)
            if row:
                row.account_id = it.account_id
                row.treatment = it.treatment
                row.active = True
            else:
                row = FinanceCategoryMap(
                    id=uuid.uuid4().hex, source=it.source,
                    category_text=it.category_text.strip(),
                    account_id=it.account_id, treatment=it.treatment, active=True)
                session.add(row)
                existing[key] = row
            count += 1
        await session.commit()
    return {"ok": True, "count": count}


@router.get("/category-map/unmapped")
async def list_unmapped_categories(request: Request):
    """掃收支明細 + 請款單的 distinct category 中沒有對映的值（含使用筆數），
    給後台「有幾個類別還沒歸科目」的待辦清單。"""
    _guard(request)
    from sqlalchemy import select, func as safunc
    from db.models import CrmCashEntry, CrmPaymentRequest, FinanceCategoryMap
    factory = _factory_or_503()
    async with factory() as session:
        mapped = {(s, t) for s, t in (await session.execute(
            select(FinanceCategoryMap.source, FinanceCategoryMap.category_text))).all()}
        items = []
        for source, col, id_col in (
                ("cash", CrmCashEntry.category, CrmCashEntry.id),
                ("payment", CrmPaymentRequest.category, CrmPaymentRequest.id)):
            rows = (await session.execute(
                select(col, safunc.count(id_col))
                .where(col.isnot(None), col != "").group_by(col))).all()
            items.extend({"source": source, "category_text": c, "usage_count": int(n)}
                         for c, n in rows if (source, c) not in mapped)
    items.sort(key=lambda x: -x["usage_count"])
    return {"items": items}


# ── 銀行帳戶 ────────────────────────────────────────────────

def _bank_dict(b) -> dict:
    return {
        "id": b.id, "name": b.name, "bank_name": b.bank_name or "",
        "account_no": b.account_no or "", "acct_kind": b.acct_kind or "bank",
        "opening_balance": b.opening_balance or 0,
        "opening_date": b.opening_date.strftime("%Y-%m-%d") if b.opening_date else None,
        "is_default": bool(b.is_default), "active": bool(b.active),
        "sort_order": b.sort_order or 0, "note": b.note or "",
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


async def _unset_other_defaults(session, keep_id: str):
    """is_default 單選：設某帳戶為預設時，把其他帳戶的預設拿掉。"""
    from sqlalchemy import update as sa_update
    from db.models import BankAccount
    await session.execute(
        sa_update(BankAccount).where(BankAccount.id != keep_id)
        .values(is_default=False))


@router.get("/bank-accounts")
async def list_bank_accounts(request: Request, with_balances: int = 1):
    """with_balances=0 跳過流水聚合與未掛帳統計（current_balance 回 null）—
    給只要帳戶清單的呼叫端（如收支明細的帳戶下拉）省兩次全表聚合。"""
    _guard(request)
    from sqlalchemy import select
    from db.models import BankAccount
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(BankAccount).order_by(BankAccount.sort_order, BankAccount.created_at))).scalars().all()
        sums = await _flow_sums_by_account(session) if with_balances else {}
        unassigned = await _unassigned_count(session) if with_balances else 0
    items = []
    for b in rows:
        d = _bank_dict(b)
        d["current_balance"] = (bank_running_balance(b.opening_balance or 0,
                                                     [sums.get(b.id, {})])
                                if with_balances else None)
        items.append(d)
    return {"items": items, "unassigned_count": int(unassigned)}


@router.post("/bank-accounts")
async def create_bank_account(payload: BankAccountPayload, request: Request):
    _guard(request)
    if not (payload.name or "").strip():
        raise HTTPException(status_code=422, detail="name 必填")
    if payload.acct_kind and payload.acct_kind not in ACCT_KINDS:
        raise HTTPException(status_code=422, detail=f"acct_kind 需為 {sorted(ACCT_KINDS)}")
    from db.models import BankAccount
    factory = _factory_or_503()
    async with factory() as session:
        b = BankAccount(
            id=uuid.uuid4().hex, name=payload.name.strip(),
            bank_name=payload.bank_name, account_no=payload.account_no,
            acct_kind=payload.acct_kind or "bank",
            opening_balance=payload.opening_balance or 0,
            opening_date=_parse_day(payload.opening_date),
            is_default=bool(payload.is_default),
            active=payload.active if payload.active is not None else True,
            sort_order=payload.sort_order or 0, note=payload.note,
        )
        session.add(b)
        if b.is_default:
            await session.flush()
            await _unset_other_defaults(session, b.id)
        await session.commit()
        return _bank_dict(b)


@router.put("/bank-accounts/{account_id}")
async def update_bank_account(account_id: str, payload: BankAccountPayload, request: Request):
    _guard(request)
    if payload.acct_kind and payload.acct_kind not in ACCT_KINDS:
        raise HTTPException(status_code=422, detail=f"acct_kind 需為 {sorted(ACCT_KINDS)}")
    from db.models import BankAccount
    factory = _factory_or_503()
    async with factory() as session:
        b = await session.get(BankAccount, account_id)
        if not b:
            raise HTTPException(status_code=404, detail="帳戶不存在")
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and not (data["name"] or "").strip():
            raise HTTPException(status_code=422, detail="name 不可為空")
        if "opening_date" in data:
            data["opening_date"] = _parse_day(data["opening_date"])
        for k, v in data.items():
            setattr(b, k, v)
        if data.get("is_default"):
            await _unset_other_defaults(session, b.id)
        await session.commit()
        return _bank_dict(b)


@router.delete("/bank-accounts/{account_id}")
async def delete_bank_account(account_id: str, request: Request):
    """有掛帳收支的帳戶不可刪（歷史流水會變孤兒）→ 409 建議停用 active=false。"""
    _guard(request)
    from sqlalchemy import select, func as safunc
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        b = await session.get(BankAccount, account_id)
        if not b:
            raise HTTPException(status_code=404, detail="帳戶不存在")
        used = (await session.execute(
            select(safunc.count(CrmCashEntry.id))
            .where(CrmCashEntry.bank_account_id == account_id))).scalar() or 0
        if used:
            raise HTTPException(
                status_code=409,
                detail=f"此帳戶已有 {used} 筆收支掛帳，不可刪除 — 建議改為停用（active=false）")
        await session.delete(b)
        await session.commit()
    return {"ok": True}


# ── 對帳 ────────────────────────────────────────────────────

def _recon_dict(r) -> dict:
    return {
        "id": r.id, "bank_account_id": r.bank_account_id, "month": r.month,
        "statement_balance": r.statement_balance, "system_balance": r.system_balance,
        "diff": r.diff, "status": r.status, "note": r.note or "",
        "reconciled_by": r.reconciled_by or "",
        "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None,
    }


@router.get("/reconciliations")
async def list_reconciliations(request: Request, bank_account_id: str = ""):
    _guard(request)
    from sqlalchemy import select
    from db.models import BankReconciliation
    factory = _factory_or_503()
    async with factory() as session:
        q = select(BankReconciliation).order_by(
            BankReconciliation.month.desc(), BankReconciliation.bank_account_id)
        if bank_account_id:
            q = q.where(BankReconciliation.bank_account_id == bank_account_id)
        rows = (await session.execute(q)).scalars().all()
    return {"items": [_recon_dict(r) for r in rows]}


@router.post("/reconciliations")
async def create_reconciliation(payload: ReconciliationPayload, request: Request):
    """對帳：system_balance = 帳戶期初 + entry_date 在該月底（含）前的掛帳流水。
    同帳戶同月重送 = 覆蓋（upsert，重新對一次）。"""
    _guard(request)
    month = _validate_month(payload.month)
    from sqlalchemy import select
    from db.models import BankAccount, BankReconciliation
    factory = _factory_or_503()
    async with factory() as session:
        acct = await session.get(BankAccount, payload.bank_account_id)
        if not acct:
            raise HTTPException(status_code=404, detail="帳戶不存在")
        # 月底（含）前 = entry_date < 次月 1 日
        next_month = (datetime.strptime(month + "-01", "%Y-%m-%d")
                      + timedelta(days=32)).replace(day=1)
        sums = await _flow_sums_by_account(session, until=next_month,
                                           account_id=acct.id)
        system_balance = bank_running_balance(acct.opening_balance or 0,
                                              [sums.get(acct.id, {})])
        rd = reconciliation_diff(system_balance, payload.statement_balance)
        row = (await session.execute(
            select(BankReconciliation).where(
                BankReconciliation.bank_account_id == payload.bank_account_id,
                BankReconciliation.month == month))).scalar_one_or_none()
        if not row:
            row = BankReconciliation(
                id=uuid.uuid4().hex, bank_account_id=payload.bank_account_id, month=month,
                statement_balance=0, system_balance=0, diff=0, status="diff")
            session.add(row)
        row.statement_balance = payload.statement_balance
        row.system_balance = system_balance
        row.diff = rd["diff"]
        row.status = rd["status"]
        row.note = payload.note
        row.reconciled_by = _username(request)
        row.reconciled_at = datetime.now()
        await session.commit()
        return {"id": row.id, "system_balance": system_balance,
                "diff": rd["diff"], "status": rd["status"]}


# ── 調整表 ──────────────────────────────────────────────────

def _adj_dict(a, code: str = "", name: str = "") -> dict:
    adj_date = local_day(a.adj_date)
    return {
        "id": a.id,
        "adj_date": adj_date.strftime("%Y-%m-%d") if adj_date else None,
        "account_id": a.account_id, "account_code": code, "account_name": name,
        "amount": a.amount, "adj_type": a.adj_type, "description": a.description,
        "created_by": a.created_by or "",
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def _get_non_bank_account(session, account_id: str):
    """調整表鐵則：不得指向銀行類科目（code 11xx）— 影響現金的修正走收支明細。"""
    from db.models import FinanceAccount
    acct = await session.get(FinanceAccount, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="科目不存在")
    if (acct.code or "").startswith("11"):
        raise HTTPException(status_code=400, detail="影響現金的修正請走收支明細")
    return acct


@router.get("/adjustments")
async def list_adjustments(request: Request):
    _guard(request)
    from sqlalchemy import select
    from db.models import FinanceAccount, FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceAdjustment, FinanceAccount.code, FinanceAccount.name)
            .outerjoin(FinanceAccount, FinanceAccount.id == FinanceAdjustment.account_id)
            .order_by(FinanceAdjustment.adj_date.desc()))).all()
    return {"items": [_adj_dict(a, c or "", n or "") for a, c, n in rows]}


@router.post("/adjustments")
async def create_adjustment(payload: FinanceAdjustmentPayload, request: Request):
    _guard(request)
    adj_date = _parse_day(payload.adj_date)
    if not adj_date:
        raise HTTPException(status_code=422, detail="adj_date 必填（YYYY-MM-DD）")
    if not payload.account_id:
        raise HTTPException(status_code=422, detail="account_id 必填")
    if payload.amount is None:
        raise HTTPException(status_code=422, detail="amount 必填（有號整數）")
    if payload.adj_type not in ADJ_TYPES:
        raise HTTPException(status_code=422, detail=f"adj_type 需為 {sorted(ADJ_TYPES)}")
    if not (payload.description or "").strip():
        raise HTTPException(status_code=422, detail="description 必填")
    from db.models import FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        acct = await _get_non_bank_account(session, payload.account_id)
        await _assert_month_open(session, adj_date)
        a = FinanceAdjustment(
            id=uuid.uuid4().hex, adj_date=adj_date, account_id=payload.account_id,
            amount=payload.amount, adj_type=payload.adj_type,
            description=payload.description.strip(), created_by=_username(request))
        session.add(a)
        await session.commit()
        return _adj_dict(a, acct.code, acct.name)


@router.put("/adjustments/{adj_id}")
async def update_adjustment(adj_id: str, payload: FinanceAdjustmentPayload, request: Request):
    _guard(request)
    from db.models import FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        a = await session.get(FinanceAdjustment, adj_id)
        if not a:
            raise HTTPException(status_code=404, detail="調整列不存在")
        data = payload.model_dump(exclude_unset=True)
        new_date = _parse_day(data["adj_date"]) if data.get("adj_date") else None
        # 舊/新月份都要開著（搬進或搬出鎖定月都算改帳）
        await _assert_month_open(session, a.adj_date, new_date)
        if "account_id" in data and data["account_id"]:
            await _get_non_bank_account(session, data["account_id"])
            a.account_id = data["account_id"]
        if new_date:
            a.adj_date = new_date
        if "amount" in data and data["amount"] is not None:
            a.amount = data["amount"]
        if "adj_type" in data and data["adj_type"]:
            if data["adj_type"] not in ADJ_TYPES:
                raise HTTPException(status_code=422, detail=f"adj_type 需為 {sorted(ADJ_TYPES)}")
            a.adj_type = data["adj_type"]
        if "description" in data and (data["description"] or "").strip():
            a.description = data["description"].strip()
        await session.commit()
        return _adj_dict(a)


@router.delete("/adjustments/{adj_id}")
async def delete_adjustment(adj_id: str, request: Request):
    _guard(request)
    from db.models import FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        a = await session.get(FinanceAdjustment, adj_id)
        if not a:
            raise HTTPException(status_code=404, detail="調整列不存在")
        await _assert_month_open(session, a.adj_date)
        await session.delete(a)
        await session.commit()
    return {"ok": True}


# ── 收支整批掛帳戶 ──────────────────────────────────────────

@router.post("/cash-entries/bulk-assign-account")
async def bulk_assign_account(payload: BulkAssignAccountPayload, request: Request):
    """把（未掛帳戶的）收支明細整批掛到指定帳戶 — 導入期一鍵補歷史。"""
    _guard(request)
    from sqlalchemy import or_, update as sa_update
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        if not await session.get(BankAccount, payload.bank_account_id):
            raise HTTPException(status_code=404, detail="帳戶不存在")
        stmt = sa_update(CrmCashEntry).values(bank_account_id=payload.bank_account_id)
        if payload.only_unassigned:
            stmt = stmt.where(or_(CrmCashEntry.bank_account_id.is_(None),
                                  CrmCashEntry.bank_account_id == ""))
        result = await session.execute(stmt)
        await session.commit()
    return {"updated": int(result.rowcount or 0)}


# ── 銀行貸款（階段四）───────────────────────────────────────
# 會計處理三分離（規格）：
# - 利息費用 = 權責按攤還表 due_date 進損益「業外支出」（不管繳沒繳）
# - 繳款現金流 = pay 自動建收支明細（category=貸款繳款 → 2400/loan →
#   CF financing），treatment='loan' 不進損益（避免與權責利息重複）
# - BS 貸款餘額 = 起始本金 − Σ已繳期別 principal_due（單純看繳款事實）

def _loan_dict(l, *, paid_periods=None, total_periods=None) -> dict:
    """貸款序列化。status（active/paid_off）為推導值 — 無 DB 欄位：
    給了期數就算，全繳完 → paid_off，否則 active。"""
    sd, fpd = local_day(l.start_date), local_day(l.first_payment_date)
    status = "active"
    if total_periods and paid_periods is not None and paid_periods >= total_periods:
        status = "paid_off"
    return {
        "id": l.id, "name": l.name, "lender": l.lender or "",
        "principal": l.principal or 0, "annual_rate": l.annual_rate or 0.0,
        "term_months": l.term_months or 0, "method": l.method or "annuity",
        "grace_months": l.grace_months or 0,
        "start_date": sd.strftime("%Y-%m-%d") if sd else None,
        "first_payment_date": fpd.strftime("%Y-%m-%d") if fpd else None,
        "bank_account_id": l.bank_account_id, "status": status,
        "opening_balance": l.opening_balance, "note": l.note or "",
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "updated_at": l.updated_at.isoformat() if l.updated_at else None,
    }


def _loan_pay_dict(p, today=None) -> dict:
    """攤還期別序列化。overdue 即時推導（未繳且過期）— 不落庫，前端一律吃此欄。"""
    due, paid_at = local_day(p.due_date), local_day(p.paid_at)
    status = p.status or "scheduled"
    d = {
        "id": p.id, "loan_id": p.loan_id, "period_no": p.period_no,
        "due_date": due.strftime("%Y-%m-%d") if due else None,
        "principal_due": p.principal_due or 0,
        "interest_due": p.interest_due or 0,
        "total": (p.principal_due or 0) + (p.interest_due or 0),
        "paid_at": paid_at.strftime("%Y-%m-%d") if paid_at else None,
        "cash_entry_id": p.cash_entry_id, "status": status,
    }
    if today is not None:
        d["overdue"] = bool(due and status != "paid" and due < today)
    return d


def _loan_base(l) -> int:
    """攤還/餘額基準本金：opening_balance（導入舊貸=當下剩餘本金）優先。"""
    return int(l.opening_balance or l.principal or 0)


async def _get_loan_and_period(session, loan_id: str, period_no: int):
    """pay/unpay 共用：取貸款 + 指定期別，任一不存在 → 404。"""
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    loan = await session.get(FinanceLoan, loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="貸款不存在")
    row = (await session.execute(
        select(FinanceLoanPayment).where(
            FinanceLoanPayment.loan_id == loan_id,
            FinanceLoanPayment.period_no == period_no))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="期別不存在")
    return loan, row


async def _query_upcoming_payments(session, horizon):
    """未繳且 due_date ≤ horizon 的期別 + 貸款名 → [(payment_row, loan_name)]。
    /loans/upcoming 端點與排程提醒（core.scheduler._loan_due_check）共用 —
    不掛 guard。逾期（due_date < today）本就 ≤ horizon 故一併涵蓋。"""
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    return (await session.execute(
        select(FinanceLoanPayment, FinanceLoan.name)
        .join(FinanceLoan, FinanceLoan.id == FinanceLoanPayment.loan_id)
        .where(FinanceLoanPayment.status != "paid",
               FinanceLoanPayment.due_date <= horizon)
        .order_by(FinanceLoanPayment.due_date,
                  FinanceLoanPayment.period_no))).all()


def _build_schedule_or_422(principal, annual_rate, term_months, method,
                           start_date, grace_months, first_payment_date) -> list:
    try:
        return amortization_schedule(principal, annual_rate, term_months,
                                     method, start_date, grace_months,
                                     first_payment_date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/loans")
async def list_loans(request: Request):
    """貸款清單 + 即時彙總：outstanding（餘額）/next_due（下一期）/
    paid_periods/total_periods。"""
    _guard(request)
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    async with factory() as session:
        loans = (await session.execute(
            select(FinanceLoan).order_by(FinanceLoan.created_at))).scalars().all()
        pays = (await session.execute(
            select(FinanceLoanPayment).order_by(
                FinanceLoanPayment.loan_id,
                FinanceLoanPayment.period_no))).scalars().all()
    by_loan: dict = {}
    for p in pays:
        by_loan.setdefault(p.loan_id, []).append(p)  # 查詢已按 period_no 排序
    today = today_start()
    items = []
    for l in loans:
        rows = by_loan.get(l.id, [])
        paid = [r for r in rows if (r.status or "") == "paid"]
        unpaid = [r for r in rows if (r.status or "") != "paid"]
        next_due = None
        if unpaid:
            nd = unpaid[0]  # 已排序 → 第一個未繳即最早到期
            nd_due = local_day(nd.due_date)
            next_due = dict(period_no=nd.period_no,
                            due_date=nd_due.strftime("%Y-%m-%d") if nd_due else None,
                            total=(nd.principal_due or 0) + (nd.interest_due or 0),
                            overdue=bool(nd_due and nd_due < today))
        d = _loan_dict(l, paid_periods=len(paid), total_periods=len(rows))
        d.update(outstanding=_loan_base(l) - sum(r.principal_due or 0 for r in paid),
                 next_due=next_due, paid_periods=len(paid), total_periods=len(rows))
        items.append(d)
    return {"items": items}


@router.post("/loans")
async def create_loan(payload: LoanPayload, request: Request):
    """建檔即生攤還表（amortization_schedule 純函式）。

    opening_balance 模式（導入舊貸）：principal 記原始本金供參考，攤還表以
    opening_balance（當下剩餘本金）+ term_months（剩餘期數）生成剩餘期。"""
    _guard(request)
    # 純函式沒守的（值域/日期正負）在此擋；principal/term/method/日期有效性
    # 交給 _build_schedule_or_422（amortization_schedule 的 ValueError → 422）。
    if not (payload.name or "").strip():
        raise HTTPException(status_code=422, detail="name 必填")
    if payload.annual_rate is not None and payload.annual_rate < 0:
        raise HTTPException(status_code=422, detail="annual_rate 不可為負")
    if payload.opening_balance is not None and payload.opening_balance <= 0:
        raise HTTPException(status_code=422, detail="opening_balance 需為正整數（或不填）")
    method = payload.method or "annuity"
    start = _parse_day(payload.start_date)
    first = _parse_day(payload.first_payment_date)
    sched = _build_schedule_or_422(
        payload.opening_balance or payload.principal, payload.annual_rate or 0.0,
        payload.term_months, method, start, payload.grace_months or 0, first)

    from db.models import BankAccount, FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    async with factory() as session:
        if payload.bank_account_id and \
                not await session.get(BankAccount, payload.bank_account_id):
            raise HTTPException(status_code=404, detail="扣款帳戶不存在")
        loan = FinanceLoan(
            id=uuid.uuid4().hex, name=payload.name.strip(),
            lender=payload.lender, principal=payload.principal,
            annual_rate=payload.annual_rate or 0.0,
            term_months=payload.term_months, method=method,
            grace_months=payload.grace_months or 0,
            start_date=start, first_payment_date=first,
            bank_account_id=payload.bank_account_id or None,
            opening_balance=payload.opening_balance,
            note=payload.note)
        session.add(loan)
        for row in sched:
            session.add(FinanceLoanPayment(
                id=uuid.uuid4().hex, loan_id=loan.id,
                period_no=row["period_no"],
                due_date=datetime.strptime(row["due_date"], "%Y-%m-%d"),
                principal_due=row["principal_due"],
                interest_due=row["interest_due"], status="scheduled"))
        await session.commit()
        d = _loan_dict(loan, paid_periods=0, total_periods=len(sched))
        d["total_periods"] = len(sched)
        return d


@router.put("/loans/{loan_id}")
async def update_loan(loan_id: str, payload: LoanPayload, request: Request):
    """更新貸款。名稱/銀行/帳戶/備註直接改；結構欄位（利率/期數/方法/寬限/
    日期/本金）任一有變 → **只重生未繳期別**，已繳列不可變：

    剩餘本金 = 基準本金（opening_balance 或 principal）− Σ已繳 principal_due；
    剩餘期數 = 新 term_months − 已繳期數（新期數 ≤ 已繳期數 → 422）；
    首期到期日 = 原第一個未繳期別的 due_date（保持原繳款節奏，全繳過則
    接在最後已繳期的下月同日）；寬限期扣掉已繳期數。"""
    _guard(request)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(status_code=422, detail="name 不可為空")
    # method 值域由 _build_schedule_or_422 統一驗（結構欄變更必觸發重生）。
    from sqlalchemy import select
    from db.models import BankAccount, FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    structural = {"principal", "annual_rate", "term_months", "method",
                  "grace_months", "start_date", "first_payment_date",
                  "opening_balance"}
    async with factory() as session:
        loan = await session.get(FinanceLoan, loan_id)
        if not loan:
            raise HTTPException(status_code=404, detail="貸款不存在")
        if "bank_account_id" in data and data["bank_account_id"] and \
                not await session.get(BankAccount, data["bank_account_id"]):
            raise HTTPException(status_code=404, detail="扣款帳戶不存在")
        for k in ("name", "lender", "note"):
            if k in data:
                setattr(loan, k, (data[k] or "").strip() if k == "name" else data[k])
        if "bank_account_id" in data:
            loan.bank_account_id = data["bank_account_id"] or None
        if structural & set(data):
            for k in ("principal", "annual_rate", "term_months", "grace_months",
                      "method", "opening_balance"):
                if k in data:
                    setattr(loan, k, data[k])
            if "start_date" in data:
                loan.start_date = _parse_day(data["start_date"])
            if "first_payment_date" in data:
                loan.first_payment_date = _parse_day(data["first_payment_date"])
            if not loan.principal or loan.principal <= 0:
                raise HTTPException(status_code=422, detail="principal 需為正整數")
            rows = (await session.execute(
                select(FinanceLoanPayment)
                .where(FinanceLoanPayment.loan_id == loan_id)
                .order_by(FinanceLoanPayment.period_no))).scalars().all()
            paid = [r for r in rows if (r.status or "") == "paid"]
            unpaid = [r for r in rows if (r.status or "") != "paid"]
            k_paid = len(paid)
            new_term = int(loan.term_months or 0)
            if new_term <= k_paid:
                raise HTTPException(
                    status_code=422,
                    detail=f"term_months（{new_term}）不可少於或等於已繳期數（{k_paid}）")
            if any(r.period_no > new_term for r in paid):
                raise HTTPException(
                    status_code=422, detail="期數不可縮到已繳期別之下")
            remaining = _loan_base(loan) - sum(r.principal_due or 0 for r in paid)
            if remaining <= 0:
                raise HTTPException(status_code=422, detail="剩餘本金 ≤ 0，無未繳期別可重生")
            # 首期錨定：原第一個未繳期別 due_date；全繳過則接最後已繳期下月同日
            # （due_date 為 NOT NULL，min/max 恆有值）
            if unpaid:
                anchor_kw = dict(
                    start_date=None,
                    first_payment_date=local_day(min(r.due_date for r in unpaid)))
            elif paid:
                anchor_kw = dict(
                    start_date=local_day(max(r.due_date for r in paid)),
                    first_payment_date=None)
            else:
                anchor_kw = dict(start_date=local_day(loan.start_date),
                                 first_payment_date=local_day(loan.first_payment_date))
            sched = _build_schedule_or_422(
                remaining, loan.annual_rate or 0.0, new_term - k_paid,
                loan.method or "annuity",
                grace_months=max(0, int(loan.grace_months or 0) - k_paid),
                **anchor_kw)
            for r in unpaid:
                await session.delete(r)
            paid_nos = {r.period_no for r in paid}
            free_nos = [n for n in range(1, new_term + 1) if n not in paid_nos]
            for row, pno in zip(sched, free_nos):
                session.add(FinanceLoanPayment(
                    id=uuid.uuid4().hex, loan_id=loan.id, period_no=pno,
                    due_date=datetime.strptime(row["due_date"], "%Y-%m-%d"),
                    principal_due=row["principal_due"],
                    interest_due=row["interest_due"], status="scheduled"))
        loan.updated_at = datetime.now()
        await session.commit()
        return _loan_dict(loan)


@router.delete("/loans/{loan_id}")
async def delete_loan(loan_id: str, request: Request):
    """有已繳期別的貸款不可刪（收支明細會變孤兒）→ 409 先取消繳款；
    否則連攤還表一併刪。"""
    _guard(request)
    from sqlalchemy import delete as sa_delete, select, func as safunc
    from db.models import FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    async with factory() as session:
        loan = await session.get(FinanceLoan, loan_id)
        if not loan:
            raise HTTPException(status_code=404, detail="貸款不存在")
        paid = (await session.execute(
            select(safunc.count(FinanceLoanPayment.id)).where(
                FinanceLoanPayment.loan_id == loan_id,
                FinanceLoanPayment.status == "paid"))).scalar() or 0
        if paid:
            raise HTTPException(
                status_code=409,
                detail=f"此貸款已有 {paid} 期繳款紀錄，請先取消繳款（unpay）再刪除")
        await session.execute(sa_delete(FinanceLoanPayment).where(
            FinanceLoanPayment.loan_id == loan_id))
        await session.delete(loan)
        await session.commit()
    return {"ok": True}


@router.get("/loans/{loan_id}/schedule")
async def loan_schedule(loan_id: str, request: Request):
    """攤還表全期別（含 overdue 即時標記）。"""
    _guard(request)
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    async with factory() as session:
        loan = await session.get(FinanceLoan, loan_id)
        if not loan:
            raise HTTPException(status_code=404, detail="貸款不存在")
        rows = (await session.execute(
            select(FinanceLoanPayment)
            .where(FinanceLoanPayment.loan_id == loan_id)
            .order_by(FinanceLoanPayment.period_no))).scalars().all()
    today = today_start()
    paid_n = sum(1 for r in rows if (r.status or "") == "paid")
    return {"loan": _loan_dict(loan, paid_periods=paid_n, total_periods=len(rows)),
            "items": [_loan_pay_dict(r, today) for r in rows]}


@router.post("/loans/{loan_id}/payments/{period_no}/pay")
async def pay_loan_period(loan_id: str, period_no: int,
                          payload: LoanPayPayload, request: Request):
    """繳款：驗期別未繳 → 自動建收支明細（entry_date=paid_date、category=貸款繳款、
    expense=本+息、掛扣款帳戶、**硬連結 loan_payment_id**）→ 期別標 paid + cash_entry_id。

    月結守衛：paid_date 落鎖定月 409。收支靠 loan_payment_id 硬連結 → classify
    回 'loan' → 不進損益（利息費用權責已按攤還表 due_date 認列，避免重複）+ 走
    科目 2400 cf_activity=financing。硬連結壓過文字 category，日後改 category/
    對映都不會誤入損益。"""
    _guard(request)
    paid_date = _parse_day(payload.paid_date) or today_start()
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        loan, row = await _get_loan_and_period(session, loan_id, period_no)
        if (row.status or "") == "paid":
            raise HTTPException(status_code=409, detail=f"第 {period_no} 期已繳款")
        await _assert_month_open(session, paid_date)
        acct_id = payload.bank_account_id or loan.bank_account_id
        if acct_id and not await session.get(BankAccount, acct_id):
            raise HTTPException(status_code=404, detail="扣款帳戶不存在")
        total = int(row.principal_due or 0) + int(row.interest_due or 0)
        entry = CrmCashEntry(
            id=uuid.uuid4().hex, entry_date=paid_date, expense=total,
            summary=f"{loan.name} 第{row.period_no}期",
            category=LOAN_PAY_CATEGORY, bank_account_id=acct_id or None,
            loan_payment_id=row.id)
        session.add(entry)
        row.status = "paid"
        row.paid_at = paid_date
        row.cash_entry_id = entry.id
        await session.commit()
        return {"ok": True, "cash_entry_id": entry.id}


@router.post("/loans/{loan_id}/payments/{period_no}/unpay")
async def unpay_loan_period(loan_id: str, period_no: int, request: Request):
    """取消繳款：刪關聯收支明細（月結守衛看其 entry_date 月）→ 期別回 scheduled。
    （loan status 為推導值 — 未繳期別出現即 active，無需寫回。）"""
    _guard(request)
    from db.models import CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        _loan, row = await _get_loan_and_period(session, loan_id, period_no)
        if (row.status or "") != "paid":
            raise HTTPException(status_code=409, detail=f"第 {period_no} 期尚未繳款")
        if row.cash_entry_id:
            entry = await session.get(CrmCashEntry, row.cash_entry_id)
            if entry:
                await _assert_month_open(session, entry.entry_date)
                await session.delete(entry)
        row.status = "scheduled"
        row.paid_at = None
        row.cash_entry_id = None
        await session.commit()
    return {"ok": True}


@router.get("/loans/upcoming")
async def loans_upcoming(request: Request, days: int = 14):
    """未繳且 due_date ≤ today+days 的期別（含逾期，overdue 標記）—
    儀表板/提醒共用（查詢見 _query_upcoming_payments）。"""
    _guard(request)
    days = max(1, min(days, 365))
    factory = _factory_or_503()
    today = today_start()
    horizon = today + timedelta(days=days)
    async with factory() as session:
        rows = await _query_upcoming_payments(session, horizon)
    items = []
    for p, name in rows:
        d = _loan_pay_dict(p, today)
        d["loan_name"] = name
        items.append(d)
    return {"items": items, "days": days}


# ── 三表 statements（階段三）────────────────────────────────

def _parse_period(period: str) -> list:
    """period 查詢參數 → 月清單；格式錯 → 422（訊息帶支援格式）。"""
    try:
        return period_months(period)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/statements")
async def get_statements(request: Request, period: str = ""):
    """期間三表（損益/資產負債/現金流量）+ 白話解讀 + meta。

    period：'2026-06' | '2026-Q2' | '2026' | '2025-07..2026-06'。
    已鎖月（未重開）且快照為 v2 → PnL/CF 讀快照逐月加總、BS 取期末月快照；
    其餘月份 live 算。meta 含 locked_months/live_months/baseline_month/warnings。
    """
    _guard(request)
    months = _parse_period(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        return await fs.statements_for_period(session, months)


@router.get("/statements/drilldown")
async def statements_drilldown(request: Request, kind: str = "", period: str = ""):
    """三表列 → 底層明細（上限 500 列 + 合計 + truncated 旗標）。

    kind ∈ revenue / cost.<料|工|費> / opex.<銷售|管理|研發> / non_operating /
    receivable / payable / cash.<operating|investing|financing>。
    """
    _guard(request)
    months = _parse_period(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        try:
            return await fs.drilldown(session, kind, months)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


# ── 儀表板 + 稅務包（階段五）─────────────────────────────────

def _period_or_current(period: str) -> tuple:
    """period 缺省 → 本月；解析同 /statements（格式錯 422）。回 (months, 原字串)。"""
    eff = (period or "").strip() or datetime.now().strftime("%Y-%m")
    return _parse_period(eff), eff


@router.get("/dashboard")
async def get_dashboard(request: Request, period: str = ""):
    """財務儀表板：12 月損益趨勢 + 現金水位/跑道 + AR 帳齡 + 客戶集中度 +
    專案毛利 Top/Bottom。period 缺省=本月，格式同 /statements。"""
    _guard(request)
    months, eff = _period_or_current(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        return await fs.dashboard_summary(session, months, period=eff)


@router.get("/tax-package")
async def get_tax_package(request: Request, period: str = ""):
    """稅務包：銷項/進項發票明細 + 分類支出 + 勞報彙總 + 營業稅位置。
    period 缺省=本月，格式同 /statements。"""
    _guard(request)
    months, eff = _period_or_current(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        return await fs.tax_package(session, months, period=eff)


# ── 設定精靈 ────────────────────────────────────────────────

@router.post("/setup-wizard")
async def setup_wizard(payload: FinanceSetupWizardPayload, request: Request):
    """財務導入一鍵設定（單一 transaction）：
    建帳戶（default_account_index 那個設為預設；opening_date=基準月 1 日）
    → assign_history 時把既有收支掛到預設帳戶（只掛未掛帳的，重跑不覆蓋手動掛帳）
    → settings 寫 finance.baseline_month
    → equity_amount 非空時建 3100 期初權益 opening 調整列。"""
    _guard(request)
    month = _validate_month(payload.baseline_month)
    if not payload.bank_accounts:
        raise HTTPException(status_code=422, detail="bank_accounts 至少要一個帳戶")
    if not (0 <= payload.default_account_index < len(payload.bank_accounts)):
        raise HTTPException(status_code=422, detail="default_account_index 超出範圍")
    for spec in payload.bank_accounts:
        if not (spec.name or "").strip():
            raise HTTPException(status_code=422, detail="帳戶 name 必填")
        if spec.acct_kind not in ACCT_KINDS:
            raise HTTPException(status_code=422, detail=f"acct_kind 需為 {sorted(ACCT_KINDS)}")

    from sqlalchemy import or_, select, update as sa_update
    from db.models import (BankAccount, CrmCashEntry, FinanceAccount,
                           FinanceAdjustment)
    factory = _factory_or_503()
    baseline_day1 = datetime.strptime(month + "-01", "%Y-%m-%d")
    username = _username(request)

    async with factory() as session:
        await _assert_month_open(session, baseline_day1)
        # 1) 建帳戶
        default_id = None
        for i, spec in enumerate(payload.bank_accounts):
            b = BankAccount(
                id=uuid.uuid4().hex, name=spec.name.strip(),
                bank_name=spec.bank_name, account_no=spec.account_no,
                acct_kind=spec.acct_kind, opening_balance=spec.opening_balance or 0,
                opening_date=baseline_day1,
                is_default=(i == payload.default_account_index),
                active=True, sort_order=i)
            session.add(b)
            if b.is_default:
                default_id = b.id
        await session.flush()
        if default_id:
            await _unset_other_defaults(session, default_id)

        # 2) 歷史收支掛預設帳戶
        assigned = 0
        if payload.assign_history and default_id:
            result = await session.execute(
                sa_update(CrmCashEntry).values(bank_account_id=default_id)
                .where(or_(CrmCashEntry.bank_account_id.is_(None),
                           CrmCashEntry.bank_account_id == "")))
            assigned = int(result.rowcount or 0)

        # 3) 期初權益（3100）opening 調整列
        if payload.equity_amount is not None:
            equity_acct = (await session.execute(
                select(FinanceAccount).where(FinanceAccount.code == "3100"))).scalar_one_or_none()
            if not equity_acct:
                raise HTTPException(status_code=500, detail="找不到 3100 期初權益科目（種子未跑？）")
            session.add(FinanceAdjustment(
                id=uuid.uuid4().hex, adj_date=baseline_day1,
                account_id=equity_acct.id, amount=payload.equity_amount,
                adj_type="opening", description="期初權益（設定精靈）",
                created_by=username))
        await session.commit()

    # 4) settings 寫 baseline_month（DB transaction 成功後才落檔）
    settings = load_settings()
    fin = settings.get("finance") or {}
    fin["baseline_month"] = month
    settings["finance"] = fin
    save_settings(settings)

    return {"ok": True, "created_accounts": len(payload.bank_accounts), "assigned": assigned}
