"""
api_finance.py — 財務管理階段二：科目對映 + 銀行帳戶 + 對帳 + 調整表 + 設定精靈

權責制三表的地基（風格比照 routers/api_cashflow.py）：
- 科目表（finance_accounts）：種子唯讀清單（後台目前只讀，代碼藏引擎）
- category 對映（finance_category_map）：收支/請款/發票的中文 category → 科目
  + 會計處理方式（treatment），批次 upsert + 未對映掃描
- 銀行帳戶（bank_accounts）：期初餘額 + 掛帳收支流水 = 即時餘額
- 對帳（bank_reconciliations）：每帳戶每月一筆，system_balance 後端算
- 調整表（finance_adjustments）：期初/更正/業主往來 — 🔴 不得指向銀行類科目
  （code 11xx），影響現金的修正一律走收支明細；受月結守衛
- 設定精靈（setup-wizard）：一次建帳戶 + 掛歷史收支 + 設基準月 + 期初權益

守門：全部端點 check_admin_or_module(request, 'crm_invoices')（沿用帳務模組 key）。
金額一律 Integer 新台幣。純計算規則在 core/finance_logic.py（有單元測試）。
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from config import load_settings, save_settings
from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.finance_logic import bank_running_balance, reconciliation_diff
from core.schemas import (BankAccountPayload, BulkAssignAccountPayload,
                          FinanceAdjustmentPayload, FinanceCategoryMapPut,
                          FinanceSetupWizardPayload, ReconciliationPayload)
from routers.crm._shared import (_assert_month_open, _parse_day, _username,
                                 _validate_month)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

MAP_SOURCES = {"cash", "payment", "invoice"}
# ⚠ 改 TREATMENTS / ACCT_KINDS 值域要同步 frontend/tabs/finance/fin-utils.js 的 *_OPTIONS
TREATMENTS = {"direct_expense", "direct_income", "ap_settlement", "ar_settlement",
              "transfer", "tax_vat", "tax_income", "advance", "passthrough"}
ADJ_TYPES = {"opening", "correction", "owner_in", "owner_out",
             "accountant", "writeoff", "other"}
ACCT_KINDS = {"bank", "cash"}


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
    return {
        "id": a.id,
        "adj_date": a.adj_date.strftime("%Y-%m-%d") if a.adj_date else None,
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
