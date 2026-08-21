"""
api_finance.py — 財務管理階段二/三：科目對映 + 銀行帳戶 + 對帳 + 調整表
+ 設定精靈 + 三表 statements

權責制三表的地基（風格比照 routers/api_cashflow.py）：
- 科目表（finance_accounts）：種子唯讀清單（後台目前只讀，代碼藏引擎）
- category 對映（finance_category_map）：收支/請款/發票的中文 category → 科目
  + 會計處理方式（treatment），批次 upsert + 未對映掃描
- 銀行帳戶（bank_accounts）：期初餘額 + 掛帳收支流水 = 即時餘額
- 對帳（bank_reconciliations）：每帳戶每月一筆，system_balance 後端算
- 對帳工作台（bank_statement_lines）：對帳單明細匯入/手動 key → 逐筆與收支
  勾銷（自動配對 + 手動配對 + 補記入帳選類別 + 時間差註記）。明細是工作底稿，
  唯一寫真帳的是補記入帳（建 CrmCashEntry，受月結守衛）
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

守門：兩本帳 entity 化 v2（2026-08-19；docs/LEDGER_ENTITY_PLAN.md §2.4）——
`_guard(request, entity, level)` 走 core.ledger.require_entity：entity 值
'parent'＝母公司（預設；既有資料全歸此）、'mine'＝我的帳。scope 分兩層：
level="view"（報表：儀表板/三表/drilldown/稅務包＋科目/對映讀取）合夥人
（finance_partner）可及；level="full"（其餘全部：銀行帳戶/對帳/對帳單明細/
調整/貸款/bulk-assign/category-map 寫入/setup-wizard）⟺ `crm_invoices`＋
`money_view` 雙鑰匙（沿用 2026-08-15；docs/MONEY_VISIBILITY.md）或
`finance_mine`；Lv3 全開。銀行帳戶/調整/貸款/三表/儀表板/稅務包以解析後
entity 過濾；科目表與對映兩本共用（讀任一 scope、寫限母公司 full scope）。
金額一律 Integer 新台幣。純計算規則在 core/finance_logic.py（有單元測試）。
"""

import json
import re
import uuid
from datetime import datetime, timedelta

from fastapi import (APIRouter, File, Form, HTTPException,  # type: ignore
                     Request, UploadFile)

from config import load_settings, save_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.finance_logic import (amortization_schedule,
                                auto_match_statement_lines,
                                bank_running_balance, cash_entry_flow,
                                local_day, period_months, reconciliation_diff,
                                statement_line_status, today_start,
                                workbench_summary)
from core.schemas import (BankAccountPayload, BankImportRulePayload,
                          BulkAssignAccountPayload,
                          FinanceAdjustmentPayload, FinanceCategoryMapPut,
                          FinanceSetupWizardPayload, LoanPayload,
                          LoanPayPayload, ReconciliationPayload,
                          StatementAutoMatchPayload,
                          StatementLineCreateEntryPayload,
                          StatementLineMatchPayload,
                          StatementDraftPayload,
                          StatementImportApply,
                          StatementLinesBulkPayload,
                          StatementLineUpdatePayload)
from routers.crm._shared import (_assert_month_open, _assert_rows_open,
                                 _fmt_minute, _parse_day, _username,
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


def _guard(request: Request, entity: str = "", level: str = "view") -> str:
    """財務域守衛 v2：回傳解析後的帳本 entity（docs/LEDGER_ENTITY_PLAN.md §2.4）。

    level="view"＝報表層（合夥人 finance_partner 可及）；level="full"＝記帳/
    銀行/原始帳列/月結寫入（parent ⟺ crm_invoices AND money_view、
    mine ⟺ finance_mine；Lv3 全開）。舊的 check_admin_or_module('crm_invoices')
    + check_money 語意已內含在 require_entity 的 scope 判定裡 —— 不要在這裡
    疊舊守衛。"""
    from core.ledger import require_entity
    return require_entity(request, entity, level=level)


async def _flow_sums_by_account(session, until=None, account_id=None,
                                entity=None) -> dict:
    """各帳戶掛帳收支的 SUM 聚合 {bank_account_id: {deposit, expense, bank_fee, claim}}。

    流水公式線性（見 finance_logic.bank_running_balance docstring）→ 聚合值包成
    一筆 entry 餵 bank_running_balance 即得餘額，免逐筆搬。until 給對帳用
    （只算 entry_date < until 的流水；未填日期的收支無法定位月份，不計入）。
    account_id 非 None 時只聚合單一帳戶（對帳只需要一個帳戶，免全表掃）。
    entity 非 None 時只聚合該帳本（帳戶清單只要自己那本 —— 不加這個過濾的話，
    /my-ledger.html 每次開銀行帳戶都會把整份母公司收支歷史聚合完再全部丟掉）。"""
    from sqlalchemy import select, func as safunc
    from db.models import CrmCashEntry
    q = (select(CrmCashEntry.bank_account_id,
                safunc.coalesce(safunc.sum(CrmCashEntry.deposit), 0),
                safunc.coalesce(safunc.sum(CrmCashEntry.expense), 0),
                safunc.coalesce(safunc.sum(CrmCashEntry.bank_fee), 0),
                safunc.coalesce(safunc.sum(CrmCashEntry.claim), 0))
         .where(CrmCashEntry.bank_account_id.isnot(None),
                CrmCashEntry.bank_account_id != ""))
    if entity is not None:
        q = q.where(CrmCashEntry.entity == entity)
    if account_id is not None:
        q = q.where(CrmCashEntry.bank_account_id == account_id)
    if until is not None:
        q = q.where(CrmCashEntry.entry_date < until)
    q = q.group_by(CrmCashEntry.bank_account_id)
    return {row[0]: {"deposit": int(row[1] or 0), "expense": int(row[2] or 0),
                     "bank_fee": int(row[3] or 0), "claim": int(row[4] or 0)}
            for row in (await session.execute(q)).all()}


async def _unassigned_count(session, entity: str = "parent") -> int:
    """未掛帳戶的收支筆數 — 兩本帳分開數（兩本帳互不見彼此的未掛帳待辦）。"""
    from sqlalchemy import select, or_, func as safunc
    from db.models import CrmCashEntry
    return (await session.execute(
        select(safunc.count(CrmCashEntry.id)).where(
            or_(CrmCashEntry.bank_account_id.is_(None),
                CrmCashEntry.bank_account_id == ""),
            CrmCashEntry.entity == entity))).scalar() or 0


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
async def list_accounts(request: Request, entity: str = ""):
    _guard(request, entity)  # 科目表兩本帳共用：任一 scope 可讀、不過濾
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
async def list_category_map(request: Request, entity: str = ""):
    _guard(request, entity)  # 對映兩本帳共用：任一 scope 可讀、不過濾
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
    # 共用科目表，寫入限母公司 full scope（合夥人不可改兩本帳共用的對映）
    _guard(request, "parent", level="full")
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
async def list_unmapped_categories(request: Request, entity: str = ""):
    """掃收支明細 + 請款單的 distinct category 中沒有對映的值（含使用筆數），
    給後台「有幾個類別還沒歸科目」的待辦清單。"""
    _guard(request, entity)  # 對映兩本帳共用：任一 scope 可讀、不過濾
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
        "entity": b.entity or "parent",
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


async def _unset_other_defaults(session, keep_id: str, entity: str = "parent"):
    """is_default 單選（每本帳各一個預設）：設某帳戶為預設時，把**同帳本**
    其他帳戶的預設拿掉 — 兩本帳的預設互不干擾。"""
    from sqlalchemy import update as sa_update
    from db.models import BankAccount
    await session.execute(
        sa_update(BankAccount).where(BankAccount.id != keep_id,
                                     BankAccount.entity == entity)
        .values(is_default=False))


@router.get("/bank-accounts")
async def list_bank_accounts(request: Request, with_balances: int = 1,
                             entity: str = ""):
    """with_balances=0 跳過流水聚合與未掛帳統計（current_balance 回 null）—
    給只要帳戶清單的呼叫端（如收支明細的帳戶下拉）省兩次全表聚合。"""
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select
    from db.models import BankAccount
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(BankAccount).where(BankAccount.entity == ent)
            .order_by(BankAccount.sort_order, BankAccount.created_at))).scalars().all()
        sums = await _flow_sums_by_account(session, entity=ent) if with_balances else {}
        unassigned = await _unassigned_count(session, ent) if with_balances else 0
    items = []
    for b in rows:
        d = _bank_dict(b)
        d["current_balance"] = (bank_running_balance(b.opening_balance or 0,
                                                     [sums.get(b.id, {})])
                                if with_balances else None)
        items.append(d)
    return {"items": items, "unassigned_count": int(unassigned)}


@router.post("/bank-accounts")
async def create_bank_account(payload: BankAccountPayload, request: Request,
                              entity: str = ""):
    # payload.entity（None＝落 entity query param 的解析結果）驗 scope
    # —— 不能建自己看不到的帳本。_guard 對空字串會自己套預設，一次就夠。
    target_ent = _guard(request, payload.entity or entity, level="full")
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
            entity=target_ent,
        )
        session.add(b)
        if b.is_default:
            await session.flush()
            await _unset_other_defaults(session, b.id, b.entity)
        await session.commit()
        return _bank_dict(b)


@router.put("/bank-accounts/{account_id}")
async def update_bank_account(account_id: str, payload: BankAccountPayload,
                              request: Request):
    _guard(request, level="full")
    if payload.acct_kind and payload.acct_kind not in ACCT_KINDS:
        raise HTTPException(status_code=422, detail=f"acct_kind 需為 {sorted(ACCT_KINDS)}")
    from db.models import BankAccount
    factory = _factory_or_503()
    async with factory() as session:
        b = await session.get(BankAccount, account_id)
        if not b:
            raise HTTPException(status_code=404, detail="帳戶不存在")
        _guard(request, b.entity or "parent", level="full")  # 這顆帳戶所屬帳本要在 scope 內
        data = payload.model_dump(exclude_unset=True)
        # entity 不允許改：payload.entity None＝維持既有值；非 None 且不同 → 422
        new_ent = data.pop("entity", None)
        if new_ent is not None and new_ent != (b.entity or "parent"):
            raise HTTPException(status_code=422, detail="帳戶不可跨帳本搬移")
        if "name" in data and not (data["name"] or "").strip():
            raise HTTPException(status_code=422, detail="name 不可為空")
        if "opening_date" in data:
            data["opening_date"] = _parse_day(data["opening_date"])
        for k, v in data.items():
            setattr(b, k, v)
        if data.get("is_default"):
            await _unset_other_defaults(session, b.id, b.entity or "parent")
        # 🔴 明著給 updated_at（跟 update_loan 同一慣例）：欄位有 onupdate=func.now()
        # ＝值由 DB 算，UPDATE 後 SQLAlchemy 會把該屬性標成過期；下面 _bank_dict 讀它
        # 就會在 async context 裡觸發 lazy IO → MissingGreenlet 500。症狀很賊：
        # **有真的改到東西才 500**（沒改到就沒 UPDATE、屬性不過期 → 200），
        # 而且 500 之前已經 commit 成功，UI 顯示存檔失敗但其實存進去了。
        b.updated_at = datetime.now()
        await session.commit()
        return _bank_dict(b)


@router.delete("/bank-accounts/{account_id}")
async def delete_bank_account(account_id: str, request: Request):
    """有掛帳收支的帳戶不可刪（歷史流水會變孤兒）→ 409 建議停用 active=false。"""
    _guard(request, level="full")
    from sqlalchemy import select, func as safunc
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        b = await session.get(BankAccount, account_id)
        if not b:
            raise HTTPException(status_code=404, detail="帳戶不存在")
        _guard(request, b.entity or "parent", level="full")  # 這顆帳戶所屬帳本要在 scope 內
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

def _entry_flow(e) -> int:
    """CrmCashEntry ORM 列 → 有號淨流（cash_entry_flow 的 dict 形狀轉接）。"""
    return cash_entry_flow({"deposit": e.deposit, "expense": e.expense,
                            "bank_fee": e.bank_fee, "claim": e.claim})


def _month_window(month: str) -> tuple:
    """'YYYY-MM' → (月初, 次月初) naive datetime（與收支寫入端一致的本地日語意）。"""
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    return start, (start + timedelta(days=32)).replace(day=1)


async def _system_balance(session, acct, month: str) -> int:
    """帳戶月底系統餘額 = 期初 + 月底（含）前掛帳流水。
    餘額核對（create_reconciliation）與工作台共用 — 對帳的核心數字只算一種。"""
    _start, end = _month_window(month)
    sums = await _flow_sums_by_account(session, until=end, account_id=acct.id)
    return bank_running_balance(acct.opening_balance or 0, [sums.get(acct.id, {})])


def _recon_dict(r) -> dict:
    return {
        "id": r.id, "bank_account_id": r.bank_account_id, "month": r.month,
        "statement_balance": r.statement_balance, "system_balance": r.system_balance,
        "diff": r.diff, "status": r.status, "note": r.note or "",
        "reconciled_by": r.reconciled_by or "",
        "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None,
    }


@router.get("/reconciliations")
async def list_reconciliations(request: Request, bank_account_id: str = "",
                               entity: str = ""):
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select
    from db.models import BankAccount, BankReconciliation
    factory = _factory_or_503()
    async with factory() as session:
        # 對帳紀錄無 entity 欄 — 經帳戶推導：join 只留本帳本帳戶的對帳列
        q = (select(BankReconciliation)
             .join(BankAccount, BankAccount.id == BankReconciliation.bank_account_id)
             .where(BankAccount.entity == ent)
             .order_by(BankReconciliation.month.desc(),
                       BankReconciliation.bank_account_id))
        if bank_account_id:
            q = q.where(BankReconciliation.bank_account_id == bank_account_id)
        rows = (await session.execute(q)).scalars().all()
    return {"items": [_recon_dict(r) for r in rows]}


@router.post("/reconciliations")
async def create_reconciliation(payload: ReconciliationPayload, request: Request):
    """對帳：system_balance = 帳戶期初 + entry_date 在該月底（含）前的掛帳流水。
    同帳戶同月重送 = 覆蓋（upsert，重新對一次）。"""
    _guard(request, level="full")
    month = _validate_month(payload.month)
    from sqlalchemy import select
    from db.models import BankReconciliation
    factory = _factory_or_503()
    async with factory() as session:
        acct, _ent = await _acct_and_entity(session, request, payload.bank_account_id)  # 對帳的帳本 scope 經帳戶推導
        system_balance = await _system_balance(session, acct, month)
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


# ── 對帳工作台（對帳單明細逐筆勾銷）──────────────────────────

def _stmt_dict(l) -> dict:
    line_date = local_day(l.line_date)
    d = {
        "id": l.id, "bank_account_id": l.bank_account_id, "month": l.month,
        "line_date": line_date.strftime("%Y-%m-%d") if line_date else None,
        "description": l.description or "", "amount": l.amount,
        "matched_entry_id": l.matched_entry_id, "note": l.note or "",
    }
    d["status"] = statement_line_status(d)
    return d


def _wb_entry_dict(e, matched_ids: set) -> dict:
    entry_date = local_day(e.entry_date)
    return {
        "id": e.id,
        "entry_date": entry_date.strftime("%Y-%m-%d") if entry_date else None,
        "summary": e.summary or "", "category": e.category or "",
        "payee": e.payee or "", "amount": _entry_flow(e),
        "matched": e.id in matched_ids,
    }


async def _wb_load(session, bank_account_id: str, month: str) -> tuple:
    """工作台資料：(該帳戶該月對帳單明細列, 該帳戶該月收支明細, 已配對 entry id 集合)。

    matched 集合查「這批收支被哪列認領」不限列的月份 — 跨月認領也要現形；
    以 in_ 篩住本月收支的 id，避免掃全表（配對唯一性另有 partial unique index 後盾）。"""
    from sqlalchemy import select
    from db.models import BankStatementLine, CrmCashEntry
    lines = (await session.execute(
        select(BankStatementLine).where(
            BankStatementLine.bank_account_id == bank_account_id,
            BankStatementLine.month == month)
        .order_by(BankStatementLine.line_date, BankStatementLine.created_at))).scalars().all()
    start, end = _month_window(month)
    entries = (await session.execute(
        select(CrmCashEntry).where(
            CrmCashEntry.bank_account_id == bank_account_id,
            CrmCashEntry.entry_date >= start, CrmCashEntry.entry_date < end)
        .order_by(CrmCashEntry.entry_date))).scalars().all()
    matched_ids = set()
    if entries:
        matched_ids = set((await session.execute(
            select(BankStatementLine.matched_entry_id).where(
                BankStatementLine.matched_entry_id.in_([e.id for e in entries])))).scalars().all())
    return lines, entries, matched_ids


@router.get("/reconciliations/workbench")
async def reconciliation_workbench(request: Request, bank_account_id: str, month: str):
    """對帳工作台一次拿全：對帳單明細 + 該月收支（含配對旗標）+ 摘要 + 系統餘額
    + 既有對帳紀錄。前端只打這支就能畫整個工作台。"""
    _guard(request, level="full")
    month = _validate_month(month)
    factory = _factory_or_503()
    async with factory() as session:
        acct, _ent = await _acct_and_entity(session, request, bank_account_id)  # 工作台的帳本 scope 經帳戶推導
        lines, entries, matched_ids = await _wb_load(session, bank_account_id, month)
        system_balance = await _system_balance(session, acct, month)
    line_dicts = [_stmt_dict(l) for l in lines]
    entry_dicts = [_wb_entry_dict(e, matched_ids) for e in entries]
    return {
        "lines": line_dicts,
        "entries": entry_dicts,
        "summary": workbench_summary(line_dicts, entry_dicts),
        "system_balance": system_balance,
    }


@router.post("/statement-lines")
async def add_statement_lines(payload: StatementLinesBulkPayload, request: Request):
    """對帳單明細批次新增（貼上匯入 / 手動 key 都走這支）。

    工作底稿不掛月結守衛 — 唯一寫真帳的補記入帳才有。

    🔴 月份逐列由 line_date 算，不是整批一個月：一份跨月的對帳單不該逼人切成
    三次傳（owner 2026-08-20）。沒有日期的列才退回用 payload.month。

    🔴 預設是**補進來**而不是覆蓋：同帳戶同日同額同摘要的列會被跳過。這支的
    使用情境是「每個月固定丟一次對帳單」，區間重疊很正常 —— 每次都新增一份
    重複的話，工作台會愈長愈髒，而且已經勾銷好的那些會多出一個未配對的分身。

    replace=true 才清既有列，而且只清**這批真的涵蓋到的月份**（不是日期區間 ——
    按區間清的話，檔案裡剛好沒有交易的那個月會被連坐清空，那個月已經勾銷好的
    紀錄就沒了）。回應會回報清掉幾筆已配對的，讓呼叫端能講給人聽。
    """
    _guard(request, level="full")
    if not payload.lines:
        raise HTTPException(status_code=422, detail="lines 不可為空")
    fallback = _validate_month(payload.month) if payload.month else None
    for i, ln in enumerate(payload.lines):
        if not ln.amount:
            raise HTTPException(status_code=422, detail=f"第 {i + 1} 列金額不可為 0")
        if not ln.line_date and not fallback:
            raise HTTPException(
                status_code=422,
                detail=f"第 {i + 1} 列沒有日期，也沒給預設月份 —— 無法決定它屬於哪個月")
    from sqlalchemy import delete as sadelete, select as sa_sel
    from db.models import BankStatementLine
    factory = _factory_or_503()
    async with factory() as session:
        acct, _ent = await _acct_and_entity(session, request, payload.bank_account_id)  # 明細掛在帳戶下 — 帳本 scope 經帳戶推導

        # 逐列定月份（日期優先，沒有才用 payload.month）
        dated = []
        for ln in payload.lines:
            d = _parse_day(ln.line_date) if ln.line_date else None
            m = local_day(d).strftime("%Y-%m") if d else fallback
            dated.append((ln, d, m))
        months = sorted({m for _l, _d, m in dated})

        dropped_matched = 0
        if payload.replace:
            gone = (await session.execute(sa_sel(BankStatementLine.matched_entry_id).where(
                BankStatementLine.bank_account_id == payload.bank_account_id,
                BankStatementLine.month.in_(months)))).scalars().all()
            dropped_matched = sum(1 for g in gone if g)
            await session.execute(sadelete(BankStatementLine).where(
                BankStatementLine.bank_account_id == payload.bank_account_id,
                BankStatementLine.month.in_(months)))
            existing = set()
        else:
            # 去重鍵：同帳戶同日同額同摘要。用 set 而不是多重集 —— 對帳單上
            # 真的有兩筆一模一樣時，工作台留一筆與留兩筆的差別只是畫面，
            # 而重傳造成的分身會直接污染勾銷狀態，寧可少不可多。
            existing = {
                (local_day(d).strftime("%Y-%m-%d") if d else "", int(a or 0), (desc or ""))
                for d, a, desc in (await session.execute(sa_sel(
                    BankStatementLine.line_date, BankStatementLine.amount,
                    BankStatementLine.description).where(
                        BankStatementLine.bank_account_id == payload.bank_account_id,
                        BankStatementLine.month.in_(months)))).all()}

        user = _username(request)
        added, skipped = 0, 0
        for ln, d, m in dated:
            desc = (ln.description or "")[:255]
            key = (local_day(d).strftime("%Y-%m-%d") if d else "", int(ln.amount or 0), desc)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            session.add(BankStatementLine(
                id=uuid.uuid4().hex, bank_account_id=payload.bank_account_id,
                month=m, line_date=d, description=desc, amount=ln.amount,
                created_by=user))
            added += 1
        await session.commit()
    return {"ok": True, "added": added, "skipped": skipped,
            "months": months, "dropped_matched": dropped_matched}


async def _acct_and_entity(session, request, account_id: str,
                           detail: str = "帳戶不存在"):
    """取銀行帳戶 + 由它推出帳本 scope。這兩件事一定成對做。

    銀行相關的端點沒有 entity 參數 —— 帳本是**帳戶決定的**（帳戶屬於哪一本，
    這筆操作就在哪一本）。這四行本來在這個檔案裡抄了 8 次；抄漏第二行的那次
    就是「查無帳戶時拿 None.entity 炸掉」，抄漏第三行的那次更糟：另一本帳的
    帳戶也能操作。
    """
    from db.models import BankAccount
    acct = await session.get(BankAccount, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail=detail)
    return acct, _guard(request, acct.entity or "parent", level="full")


async def _stmt_line_or_404(session, line_id: str, request: Request):
    """載明細列 + 依所屬帳戶的 entity 驗帳本 scope（兩本帳，§2.4）。"""
    from db.models import BankAccount, BankStatementLine
    row = await session.get(BankStatementLine, line_id)
    if not row:
        raise HTTPException(status_code=404, detail="對帳單明細不存在")
    acct = await session.get(BankAccount, row.bank_account_id)
    _guard(request, (acct.entity if acct else "") or "parent", level="full")
    return row


@router.put("/statement-lines/{line_id}")
async def update_statement_line(line_id: str, payload: StatementLineUpdatePayload,
                                request: Request):
    """只開放補交易日與註記 — 金額/摘要要改就刪列重加（工作底稿，改配對過的
    金額會讓差額數學失真，乾脆不開這扇門）。"""
    _guard(request, level="full")
    factory = _factory_or_503()
    async with factory() as session:
        row = await _stmt_line_or_404(session, line_id, request)
        if payload.line_date is not None:
            row.line_date = _parse_day(payload.line_date)
        if payload.note is not None:
            row.note = payload.note
        await session.commit()
        return _stmt_dict(row)


@router.delete("/statement-lines/{line_id}")
async def delete_statement_line(line_id: str, request: Request):
    """刪明細列（工作底稿）— 配對連結一併消失，收支明細本身不動。"""
    _guard(request, level="full")
    factory = _factory_or_503()
    async with factory() as session:
        row = await _stmt_line_or_404(session, line_id, request)
        await session.delete(row)
        await session.commit()
    return {"ok": True}


@router.post("/statement-lines/auto-match")
async def auto_match_statement(payload: StatementAutoMatchPayload, request: Request):
    """自動配對：金額相等 + 日期最近（純函式 auto_match_statement_lines）→ 寫回連結。"""
    _guard(request, level="full")
    month = _validate_month(payload.month)
    factory = _factory_or_503()
    async with factory() as session:
        acct, _ent = await _acct_and_entity(session, request, payload.bank_account_id)  # 帳本 scope 經帳戶推導
        lines, entries, matched_ids = await _wb_load(session, payload.bank_account_id, month)
        pairs = auto_match_statement_lines(
            [{"id": l.id, "amount": l.amount, "line_date": local_day(l.line_date),
              "matched_entry_id": l.matched_entry_id} for l in lines],
            [{"id": e.id, "entry_date": local_day(e.entry_date), "deposit": e.deposit,
              "expense": e.expense, "bank_fee": e.bank_fee, "claim": e.claim}
             for e in entries if e.id not in matched_ids])
        by_id = {l.id: l for l in lines}
        for line_id, entry_id in pairs:
            by_id[line_id].matched_entry_id = entry_id
        await session.commit()
    return {"ok": True, "matched": len(pairs)}


@router.post("/statement-lines/{line_id}/match")
async def match_statement_line(line_id: str, payload: StatementLineMatchPayload,
                               request: Request):
    """手動配對：金額必須相等（差額數學才成立）、一筆收支只能被一列認領。"""
    _guard(request, level="full")
    from sqlalchemy import select
    from db.models import BankStatementLine, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        row = await _stmt_line_or_404(session, line_id, request)
        if row.matched_entry_id:
            raise HTTPException(status_code=409, detail="此列已配對，請先取消配對")
        entry = await session.get(CrmCashEntry, payload.entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="收支明細不存在")
        if (entry.bank_account_id or "") != row.bank_account_id:
            raise HTTPException(status_code=422, detail="該筆收支不屬於這個帳戶")
        flow = _entry_flow(entry)
        if flow != row.amount:
            raise HTTPException(
                status_code=422,
                detail=f"金額不符：對帳單 {row.amount:+,} vs 收支淨流 {flow:+,} — "
                       "金額不同不能硬配，漏記請用「補記入帳」")
        taken = (await session.execute(
            select(BankStatementLine.id).where(
                BankStatementLine.matched_entry_id == payload.entry_id))).scalar_one_or_none()
        if taken:
            raise HTTPException(status_code=409, detail="該筆收支已被其他對帳單明細認領")
        row.matched_entry_id = payload.entry_id
        await session.commit()
        return _stmt_dict(row)


@router.post("/statement-lines/{line_id}/unmatch")
async def unmatch_statement_line(line_id: str, request: Request):
    _guard(request, level="full")
    factory = _factory_or_503()
    async with factory() as session:
        row = await _stmt_line_or_404(session, line_id, request)
        row.matched_entry_id = None
        await session.commit()
        return _stmt_dict(row)


@router.post("/statement-lines/{line_id}/create-entry")
async def create_entry_from_statement_line(
        line_id: str, payload: StatementLineCreateEntryPayload, request: Request):
    """補記入帳：銀行有、系統漏記 → 從明細列建收支明細並自動配對。
    寫真帳 → 月結守衛看交易日；category 必填（→ 科目走既有對映，沒對映會
    出現在未歸類佇列）。正額=存入、負額=支出。"""
    _guard(request, level="full")
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        row = await _stmt_line_or_404(session, line_id, request)  # 內含帳戶 entity scope 驗證
        acct = await session.get(BankAccount, row.bank_account_id)
        row_ent = (acct.entity if acct else "") or "parent"
        if row.matched_entry_id:
            raise HTTPException(status_code=409, detail="此列已配對，不需補記")
        if not row.line_date:
            raise HTTPException(status_code=422, detail="請先填這列的交易日再補記")
        if not (payload.category or "").strip():
            raise HTTPException(status_code=422, detail="請選擇類別（報表要靠它歸科目）")
        await _assert_month_open(session, row.line_date, entity=row_ent)
        amt = row.amount
        entry = CrmCashEntry(
            id=uuid.uuid4().hex, entry_date=row.line_date,
            deposit=amt if amt > 0 else None,
            expense=-amt if amt < 0 else None,
            summary=(payload.summary or row.description or "銀行對帳補記")[:255],
            category=payload.category.strip(), payee=payload.payee or None,
            note="銀行對帳補記", bank_account_id=row.bank_account_id,
            entity=row_ent)  # 補記入帳繼承帳戶的帳本
        session.add(entry)
        row.matched_entry_id = entry.id
        await session.commit()
        return {"ok": True, "cash_entry_id": entry.id, "line": _stmt_dict(row)}


# ── 調整表 ──────────────────────────────────────────────────

def _adj_dict(a, code: str = "", name: str = "") -> dict:
    adj_date = local_day(a.adj_date)
    return {
        "id": a.id,
        "adj_date": adj_date.strftime("%Y-%m-%d") if adj_date else None,
        "account_id": a.account_id, "account_code": code, "account_name": name,
        "amount": a.amount, "adj_type": a.adj_type, "description": a.description,
        "entity": a.entity or "parent",
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
async def list_adjustments(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select
    from db.models import FinanceAccount, FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceAdjustment, FinanceAccount.code, FinanceAccount.name)
            .outerjoin(FinanceAccount, FinanceAccount.id == FinanceAdjustment.account_id)
            .where(FinanceAdjustment.entity == ent)
            .order_by(FinanceAdjustment.adj_date.desc()))).all()
    return {"items": [_adj_dict(a, c or "", n or "") for a, c, n in rows]}


@router.post("/adjustments")
async def create_adjustment(payload: FinanceAdjustmentPayload, request: Request,
                            entity: str = ""):
    # payload.entity（None＝落 entity query param 的解析結果）驗 scope
    # —— 不能建自己看不到的帳本。_guard 對空字串會自己套預設，一次就夠。
    target_ent = _guard(request, payload.entity or entity, level="full")
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
        await _assert_month_open(session, adj_date, entity=target_ent)
        a = FinanceAdjustment(
            id=uuid.uuid4().hex, adj_date=adj_date, account_id=payload.account_id,
            amount=payload.amount, adj_type=payload.adj_type,
            description=payload.description.strip(), created_by=_username(request),
            entity=target_ent)
        session.add(a)
        await session.commit()
        return _adj_dict(a, acct.code, acct.name)


@router.put("/adjustments/{adj_id}")
async def update_adjustment(adj_id: str, payload: FinanceAdjustmentPayload,
                            request: Request):
    _guard(request, level="full")
    from db.models import FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        a = await session.get(FinanceAdjustment, adj_id)
        if not a:
            raise HTTPException(status_code=404, detail="調整列不存在")
        _guard(request, a.entity or "parent", level="full")  # 這列所屬帳本要在 scope 內
        data = payload.model_dump(exclude_unset=True)
        # entity 不允許改：payload.entity None＝維持既有值；非 None 且不同 → 422
        new_ent = data.pop("entity", None)
        if new_ent is not None and new_ent != (a.entity or "parent"):
            raise HTTPException(status_code=422, detail="調整列不可跨帳本搬移")
        new_date = _parse_day(data["adj_date"]) if data.get("adj_date") else None
        # 舊/新月份都要開著（搬進或搬出鎖定月都算改帳）— 用這列的帳本查鎖
        await _assert_month_open(session, a.adj_date, new_date,
                                 entity=a.entity or "parent")
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
    _guard(request, level="full")
    from db.models import FinanceAdjustment
    factory = _factory_or_503()
    async with factory() as session:
        a = await session.get(FinanceAdjustment, adj_id)
        if not a:
            raise HTTPException(status_code=404, detail="調整列不存在")
        _guard(request, a.entity or "parent", level="full")  # 這列所屬帳本要在 scope 內
        await _assert_month_open(session, a.adj_date, entity=a.entity or "parent")
        await session.delete(a)
        await session.commit()
    return {"ok": True}


# ── 收支整批掛帳戶 ──────────────────────────────────────────

@router.post("/cash-entries/bulk-assign-account")
async def bulk_assign_account(payload: BulkAssignAccountPayload, request: Request):
    """把（未掛帳戶的）收支明細整批掛到指定帳戶 — 導入期一鍵補歷史。

    兩本帳守衛：候選列的 entity 必須全部 == 目標帳戶 entity（跨帳本掛帳 → 409；
    候選列與帳戶同帳本 ⇒ 也必在請求者 scope 內，因為帳戶 entity 已驗過）。"""
    _guard(request, level="full")
    from sqlalchemy import or_, select, func as safunc, update as sa_update
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        acct = await session.get(BankAccount, payload.bank_account_id)
        if not acct:
            raise HTTPException(status_code=404, detail="帳戶不存在")
        acct_ent = acct.entity or "parent"
        _guard(request, acct_ent, level="full")  # 目標帳戶所屬帳本要在 scope 內
        cond = []
        if payload.only_unassigned:
            cond.append(or_(CrmCashEntry.bank_account_id.is_(None),
                            CrmCashEntry.bank_account_id == ""))
        cross = (await session.execute(
            select(safunc.count(CrmCashEntry.id))
            .where(*cond, CrmCashEntry.entity != acct_ent))).scalar() or 0
        if cross:
            raise HTTPException(
                status_code=409,
                detail=f"有 {cross} 筆收支與目標帳戶分屬不同帳本，不可跨帳本掛帳")
        stmt = (sa_update(CrmCashEntry)
                .values(bank_account_id=payload.bank_account_id)
                .where(*cond, CrmCashEntry.entity == acct_ent))
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
        "account_no": l.account_no or "",
        "opening_balance": l.opening_balance, "note": l.note or "",
        "entity": l.entity or "parent",
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


async def _query_upcoming_payments(session, horizon, entity=None):
    """未繳且 due_date ≤ horizon 的期別 + 貸款名 → [(payment_row, loan_name)]。
    /loans/upcoming 端點與排程提醒（core.scheduler._loan_due_check）共用 —
    不掛 guard。逾期（due_date < today）本就 ≤ horizon 故一併涵蓋。
    entity 非 None 時只回該帳本的貸款期別（排程提醒不帶 = 兩本都提醒）。"""
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    q = (select(FinanceLoanPayment, FinanceLoan.name)
         .join(FinanceLoan, FinanceLoan.id == FinanceLoanPayment.loan_id)
         .where(FinanceLoanPayment.status != "paid",
                FinanceLoanPayment.due_date <= horizon))
    if entity is not None:
        q = q.where(FinanceLoan.entity == entity)
    return (await session.execute(
        q.order_by(FinanceLoanPayment.due_date,
                   FinanceLoanPayment.period_no))).all()


def _build_schedule_or_422(principal, annual_rate, term_months, method,
                           start_date, grace_months, first_payment_date) -> list:
    try:
        return amortization_schedule(principal, annual_rate, term_months,
                                     method, start_date, grace_months,
                                     first_payment_date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


async def _load_loans_with_payments(session, entity: str):
    """(貸款清單, {loan_id: [期別…按 period_no 排序]}) —— 清單頁與對帳單配對共用。

    兩支本來各寫一份同樣的兩個查詢加分組；分開寫的話「下一期是哪一期」很容易
    長出第二種定義（實際已經發生過：一邊算 overdue、一邊沒算）。
    """
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    loans = (await session.execute(
        select(FinanceLoan).where(FinanceLoan.entity == entity)
        .order_by(FinanceLoan.created_at))).scalars().all()
    pays = (await session.execute(
        select(FinanceLoanPayment)
        .join(FinanceLoan, FinanceLoan.id == FinanceLoanPayment.loan_id)
        .where(FinanceLoan.entity == entity)
        .order_by(FinanceLoanPayment.loan_id,
                  FinanceLoanPayment.period_no))).scalars().all()
    by_loan: dict = {}
    for p in pays:
        by_loan.setdefault(p.loan_id, []).append(p)   # 查詢已按 period_no 排序
    return loans, by_loan


@router.get("/loans")
async def list_loans(request: Request, entity: str = ""):
    """貸款清單 + 即時彙總：outstanding（餘額）/next_due（下一期）/
    paid_periods/total_periods。"""
    ent = _guard(request, entity, level="full")
    factory = _factory_or_503()
    async with factory() as session:
        loans, by_loan = await _load_loans_with_payments(session, ent)
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
async def create_loan(payload: LoanPayload, request: Request, entity: str = ""):
    """建檔即生攤還表（amortization_schedule 純函式）。

    opening_balance 模式（導入舊貸）：principal 記原始本金供參考，攤還表以
    opening_balance（當下剩餘本金）+ term_months（剩餘期數）生成剩餘期。"""
    # payload.entity（None＝落 entity query param 的解析結果）驗 scope
    # —— 不能建自己看不到的帳本。_guard 對空字串會自己套預設，一次就夠。
    target_ent = _guard(request, payload.entity or entity, level="full")
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
        if payload.bank_account_id:
            acct = await session.get(BankAccount, payload.bank_account_id)
            if not acct:
                raise HTTPException(status_code=404, detail="扣款帳戶不存在")
            if (acct.entity or "parent") != target_ent:
                raise HTTPException(
                    status_code=409, detail="貸款與扣款帳戶分屬不同帳本")
        loan = FinanceLoan(
            id=uuid.uuid4().hex, name=payload.name.strip(),
            lender=payload.lender, principal=payload.principal,
            annual_rate=payload.annual_rate or 0.0,
            term_months=payload.term_months, method=method,
            grace_months=payload.grace_months or 0,
            start_date=start, first_payment_date=first,
            bank_account_id=payload.bank_account_id or None,
            account_no=(payload.account_no or "").strip() or None,
            opening_balance=payload.opening_balance,
            note=payload.note, entity=target_ent)
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
    _guard(request, level="full")
    data = payload.model_dump(exclude_unset=True)
    data.pop("entity", None)  # entity 由下方鎖死不可改 — 別讓它流進 setattr
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
        _guard(request, loan.entity or "parent", level="full")  # 這筆貸款所屬帳本要在 scope 內
        # entity 不允許改：payload.entity None＝維持既有值；非 None 且不同 → 422
        if payload.entity is not None and payload.entity != (loan.entity or "parent"):
            raise HTTPException(status_code=422, detail="貸款不可跨帳本搬移")
        if "bank_account_id" in data and data["bank_account_id"]:
            new_acct = await session.get(BankAccount, data["bank_account_id"])
            if not new_acct:
                raise HTTPException(status_code=404, detail="扣款帳戶不存在")
            if (new_acct.entity or "parent") != (loan.entity or "parent"):
                raise HTTPException(
                    status_code=409, detail="貸款與扣款帳戶分屬不同帳本")
        for k in ("name", "lender", "note", "account_no"):
            if k in data:
                setattr(loan, k, (data[k] or "").strip() if k in ("name", "account_no") else data[k])
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
    _guard(request, level="full")
    from sqlalchemy import delete as sa_delete, select, func as safunc
    from db.models import FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    async with factory() as session:
        loan = await session.get(FinanceLoan, loan_id)
        if not loan:
            raise HTTPException(status_code=404, detail="貸款不存在")
        _guard(request, loan.entity or "parent", level="full")  # 這筆貸款所屬帳本要在 scope 內
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
    _guard(request, level="full")
    from sqlalchemy import select
    from db.models import FinanceLoan, FinanceLoanPayment
    factory = _factory_or_503()
    async with factory() as session:
        loan = await session.get(FinanceLoan, loan_id)
        if not loan:
            raise HTTPException(status_code=404, detail="貸款不存在")
        _guard(request, loan.entity or "parent", level="full")  # 這筆貸款所屬帳本要在 scope 內
        rows = (await session.execute(
            select(FinanceLoanPayment)
            .where(FinanceLoanPayment.loan_id == loan_id)
            .order_by(FinanceLoanPayment.period_no))).scalars().all()
    today = today_start()
    paid_n = sum(1 for r in rows if (r.status or "") == "paid")
    return {"loan": _loan_dict(loan, paid_periods=paid_n, total_periods=len(rows)),
            "items": [_loan_pay_dict(r, today) for r in rows]}


def _record_loan_payment(session, loan, row, paid_date, acct_id, note: str = None,
                         actual_amount: int = None):
    """標某一期已繳 + 建對應的收支明細（**手動按繳款與對帳單匯入共用這一條路**）。

    🔴 `loan_payment_id` 這個硬連結是關鍵：classify 靠它回 'loan' —— 不進損益
    （利息費用已按攤還表 due_date 權責認列，重複計會雙算）、現金流走籌資活動。
    兩條路各寫一份的話，其中一邊日後多帶一個欄位，同一筆繳款就會依「從哪裡記的」
    落到不同報表列。

    🔴 `actual_amount`＝銀行**實際扣款**。有給就以它為準記現金，沒給才退回攤還表
    （本+息）。銀行按實際天數算息、進位方式也不同 —— 實測合庫 315614 攤還表算
    4,456、銀行扣 4,470；315611 是 25,286 對 25,290。記攤還表那個數字的話系統的
    銀行餘額每期歪十幾元且累積（owner 剛把三個帳戶對到分毫不差），而對帳工作台
    要求金額完全相等才勾得掉，那些列會永遠配不上、每個月都要人工處理。

    本金／利息的拆分仍照攤還表（貸款餘額的推進以它為準）；差額只落在現金那一側。
    """
    from db.models import CrmCashEntry
    total = int(row.principal_due or 0) + int(row.interest_due or 0)
    paid = int(actual_amount) if actual_amount else total
    diff = paid - total
    memo = note or ""
    if diff:
        memo = (memo + " " if memo else "") + (
            f"（銀行實扣 {paid:,}，攤還表 {total:,}，差 {diff:+,}）")
    entry = CrmCashEntry(
        id=uuid.uuid4().hex, entry_date=paid_date, expense=paid,
        summary=f"{loan.name} 第{row.period_no}期", note=memo or None,
        category=LOAN_PAY_CATEGORY, bank_account_id=acct_id or None,
        loan_payment_id=row.id, entity=loan.entity or "parent")  # 繳款收支繼承貸款帳本
    session.add(entry)
    row.status = "paid"
    row.paid_at = paid_date
    row.paid_amount = paid
    row.cash_entry_id = entry.id
    return entry


@router.post("/loans/{loan_id}/payments/{period_no}/pay")
async def pay_loan_period(loan_id: str, period_no: int,
                          payload: LoanPayPayload, request: Request):
    """繳款：驗期別未繳 → 自動建收支明細（entry_date=paid_date、category=貸款繳款、
    expense=本+息、掛扣款帳戶、**硬連結 loan_payment_id**）→ 期別標 paid + cash_entry_id。

    月結守衛：paid_date 落鎖定月 409。收支靠 loan_payment_id 硬連結 → classify
    回 'loan' → 不進損益（利息費用權責已按攤還表 due_date 認列，避免重複）+ 走
    科目 2400 cf_activity=financing。硬連結壓過文字 category，日後改 category/
    對映都不會誤入損益。"""
    _guard(request, level="full")
    paid_date = _parse_day(payload.paid_date) or today_start()
    from db.models import BankAccount
    factory = _factory_or_503()
    async with factory() as session:
        loan, row = await _get_loan_and_period(session, loan_id, period_no)
        loan_ent = loan.entity or "parent"
        _guard(request, loan_ent, level="full")  # 這筆貸款所屬帳本要在 scope 內
        if (row.status or "") == "paid":
            raise HTTPException(status_code=409, detail=f"第 {period_no} 期已繳款")
        await _assert_month_open(session, paid_date, entity=loan_ent)
        acct_id = payload.bank_account_id or loan.bank_account_id
        if acct_id:
            acct = await session.get(BankAccount, acct_id)
            if not acct:
                raise HTTPException(status_code=404, detail="扣款帳戶不存在")
            if (acct.entity or "parent") != loan_ent:
                raise HTTPException(
                    status_code=409, detail="貸款與扣款帳戶分屬不同帳本")
        entry = _record_loan_payment(session, loan, row, paid_date, acct_id,
                                     actual_amount=payload.amount)
        await session.commit()
        return {"ok": True, "cash_entry_id": entry.id}


@router.post("/loans/{loan_id}/payments/{period_no}/unpay")
async def unpay_loan_period(loan_id: str, period_no: int, request: Request):
    """取消繳款：刪關聯收支明細（月結守衛看其 entry_date 月）→ 期別回 scheduled。
    （loan status 為推導值 — 未繳期別出現即 active，無需寫回。）"""
    _guard(request, level="full")
    from db.models import CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        loan, row = await _get_loan_and_period(session, loan_id, period_no)
        loan_ent = loan.entity or "parent"
        _guard(request, loan_ent, level="full")  # 這筆貸款所屬帳本要在 scope 內
        if (row.status or "") != "paid":
            raise HTTPException(status_code=409, detail=f"第 {period_no} 期尚未繳款")
        if row.cash_entry_id:
            entry = await session.get(CrmCashEntry, row.cash_entry_id)
            if entry:
                await _assert_month_open(session, entry.entry_date, entity=loan_ent)
                await session.delete(entry)
        row.status = "scheduled"
        row.paid_at = None
        row.cash_entry_id = None
        await session.commit()
    return {"ok": True}


@router.get("/loans/upcoming")
async def loans_upcoming(request: Request, days: int = 14, entity: str = ""):
    """未繳且 due_date ≤ today+days 的期別（含逾期，overdue 標記）—
    儀表板/提醒共用（查詢見 _query_upcoming_payments）。"""
    ent = _guard(request, entity, level="full")
    days = max(1, min(days, 365))
    factory = _factory_or_503()
    today = today_start()
    horizon = today + timedelta(days=days)
    async with factory() as session:
        rows = await _query_upcoming_payments(session, horizon, entity=ent)
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
async def get_statements(request: Request, period: str = "", entity: str = ""):
    """期間三表（損益/資產負債/現金流量）+ 白話解讀 + meta。

    period：'2026-06' | '2026-Q2' | '2026' | '2025-07..2026-06'。
    已鎖月（未重開）且快照為 v2 → PnL/CF 讀快照逐月加總、BS 取期末月快照；
    其餘月份 live 算。meta 含 locked_months/live_months/baseline_month/warnings。
    """
    ent = _guard(request, entity)
    months = _parse_period(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        return await fs.statements_for_period(session, months, entity=ent)


@router.get("/statements/drilldown")
async def statements_drilldown(request: Request, kind: str = "", period: str = "",
                               entity: str = ""):
    """三表列 → 底層明細（上限 500 列 + 合計 + truncated 旗標）。

    kind ∈ revenue / cost.<料|工|費> / opex.<銷售|管理|研發> / non_operating /
    receivable / payable / cash.<operating|investing|financing>。
    """
    ent = _guard(request, entity)
    months = _parse_period(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        try:
            return await fs.drilldown(session, kind, months, entity=ent)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


# ── 儀表板 + 稅務包（階段五）─────────────────────────────────

def _period_or_current(period: str) -> tuple:
    """period 缺省 → 本月；解析同 /statements（格式錯 422）。回 (months, 原字串)。"""
    eff = (period or "").strip() or datetime.now().strftime("%Y-%m")
    return _parse_period(eff), eff


@router.get("/dashboard")
async def get_dashboard(request: Request, period: str = "", entity: str = ""):
    """財務儀表板：12 月損益趨勢 + 現金水位/跑道 + AR 帳齡 + 客戶集中度 +
    專案毛利 Top/Bottom。period 缺省=本月，格式同 /statements。"""
    ent = _guard(request, entity)
    months, eff = _period_or_current(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        return await fs.dashboard_summary(session, months, period=eff, entity=ent)


@router.get("/tax-package")
async def get_tax_package(request: Request, period: str = "", entity: str = ""):
    """稅務包：銷項/進項發票明細 + 分類支出 + 勞報彙總 + 營業稅位置。
    period 缺省=本月，格式同 /statements。"""
    ent = _guard(request, entity)
    months, eff = _period_or_current(period)
    from services import finance_statements as fs
    factory = _factory_or_503()
    async with factory() as session:
        return await fs.tax_package(session, months, period=eff, entity=ent)


# ── 設定精靈 ────────────────────────────────────────────────

@router.post("/setup-wizard")
async def setup_wizard(payload: FinanceSetupWizardPayload, request: Request):
    """財務導入一鍵設定（單一 transaction）：
    建帳戶（default_account_index 那個設為預設；opening_date=基準月 1 日）
    → assign_history 時把既有收支掛到預設帳戶（只掛未掛帳的，重跑不覆蓋手動掛帳）
    → settings 寫 finance.baseline_month
    → equity_amount 非空時建 3100 期初權益 opening 調整列。"""
    # 精靈只服務母公司帳本（baseline_month settings 是全域單值；我的帳精靈
    # v1 不做，plan §2.4）→ 強制 parent + full scope
    _guard(request, "parent", level="full")
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
        await _assert_month_open(session, baseline_day1, entity="parent")
        # 1) 建帳戶（精靈只服務母公司帳本）
        default_id = None
        for i, spec in enumerate(payload.bank_accounts):
            b = BankAccount(
                id=uuid.uuid4().hex, name=spec.name.strip(),
                bank_name=spec.bank_name, account_no=spec.account_no,
                acct_kind=spec.acct_kind, opening_balance=spec.opening_balance or 0,
                opening_date=baseline_day1,
                is_default=(i == payload.default_account_index),
                active=True, sort_order=i, entity="parent")
            session.add(b)
            if b.is_default:
                default_id = b.id
        await session.flush()
        if default_id:
            await _unset_other_defaults(session, default_id, "parent")

        # 2) 歷史收支掛預設帳戶（只掛母公司帳本的未掛帳列 — 別把我的帳列掃進來）
        assigned = 0
        if payload.assign_history and default_id:
            result = await session.execute(
                sa_update(CrmCashEntry).values(bank_account_id=default_id)
                .where(or_(CrmCashEntry.bank_account_id.is_(None),
                           CrmCashEntry.bank_account_id == ""),
                       CrmCashEntry.entity == "parent"))
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
                created_by=username, entity="parent"))
        await session.commit()

    # 4) settings 寫 baseline_month（DB transaction 成功後才落檔）
    settings = load_settings()
    fin = settings.get("finance") or {}
    fin["baseline_month"] = month
    settings["finance"] = fin
    save_settings(settings)

    return {"ok": True, "created_accounts": len(payload.bank_accounts), "assigned": assigned}
# ── 對帳單匯入（上傳銀行明細 → 自動換算成收支＋貸款繳款）────────────

_STMT_MAX_BYTES = 8 * 1024 * 1024      # 對帳單就是幾頁文字，超過必是傳錯檔


def _statement_text(filename: str, blob: bytes) -> str:
    """上傳檔 → 純文字。PDF 走 core.doc_text（含康熙部首正規化），其餘當文字。"""
    import os
    import tempfile

    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        from core.doc_text import extract_text
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
            txt, err = extract_text(tmp)     # 同步 CPU-bound，呼叫端已包 to_thread
            if err:
                raise HTTPException(status_code=422, detail=f"讀不了這個 PDF：{err}")
            return txt
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", errors="replace")


async def _loans_for_matching(session, entity: str) -> list:
    """配對用的貸款清單：帶 account_no 與全部期別（含已繳，供重匯偵測）。"""
    loans, by_loan = await _load_loans_with_payments(session, entity)
    out = []
    for l in loans:
        rows_all = by_loan.get(l.id, [])
        # **全部**期別（含已繳）都給配對器：配對是照「到期日最接近」挑的，
        # 已繳期別要在裡面，重匯同一份對帳單才認得出「這期已經記過了」——
        # 少了它，重匯會被配到下一期並蓋上錯的繳款日。
        periods = [{"period_no": r.period_no,
                    "total": (r.principal_due or 0) + (r.interest_due or 0),
                    "due_date": (local_day(r.due_date).strftime("%Y-%m-%d")
                                 if r.due_date else None),
                    "paid": (r.status or "") == "paid"} for r in rows_all]
        # 這裡刻意**不**算「下一期是哪一期」—— _load_loans_with_payments 的
        # docstring 已經說了「分開寫的話很容易長出第二種定義」，而配對器
        # （match_loan_payments）只讀 id/name/account_no/periods，回應也不帶
        # loans。算了沒人看的第二種定義，就是在等它跟 list_loans 那份漂開。
        out.append({
            "id": l.id, "name": l.name, "account_no": l.account_no or "",
            "periods": periods,
        })
    return out


async def _existing_entry_keys(session, acct_id, dates):
    """帳上已有的 (日期, 帶號金額) → 筆數。對帳單匯入的重複偵測用。

    preview 拿它標「已匯過」、apply 拿它**真的擋下來** —— 只活在 preview 顯示層
    的防護等於沒有：表頭全選會把 duplicate 列一起勾起來、送出逾時重按也一樣，
    兩條路都直接寫進 CrmCashEntry，同日同額的收支就變兩份，餘額與三張報表全部
    雙倍（貸款列有 status=="paid" 保護，一般列一個都沒有）。

    回 Counter 而不是 set：對帳單同一天真的可能有兩筆一樣的三十元手續費 ——
    帳上已有一筆就只跳過一筆，第二筆照匯。
    """
    from collections import Counter

    from sqlalchemy import and_, select

    from db.models import CrmCashEntry
    out = Counter()
    if not dates:
        return out
    # 🔴 前後各放寬一天：_parse_day 回 naive datetime，跟 timestamptz 欄位比較時
    # 邊界會偏掉，實測「對帳單最後一天」的列永遠判不出重複 —— 而那正是使用者
    # 重複匯入時最常撞到的一天。真正的比對交給 (日期, 金額) 鍵。
    lo = _parse_day(dates[0]) - timedelta(days=1)
    hi = _parse_day(dates[-1]) + timedelta(days=1)
    rows = (await session.execute(
        select(CrmCashEntry.entry_date, CrmCashEntry.expense, CrmCashEntry.deposit)
        .where(and_(CrmCashEntry.bank_account_id == acct_id,
                    CrmCashEntry.entry_date >= lo,
                    CrmCashEntry.entry_date <= hi)))).all()
    for dt, exp, dep in rows:
        if dt:
            out[(local_day(dt).strftime("%Y-%m-%d"), (dep or 0) - (exp or 0))] += 1
    return out


# ── 對帳單分類規則（bank_import_rules）──────────────────────
#
# 這一層在 finance_category_map **之前**：
#     銀行摘要文字 →〔本組端點〕→ 類別 →〔category_map〕→ 會計科目
# 右半邊本來就是資料驅動的，左半邊原本寫死在 core/bank_statement.KEYWORD_RULES。


def _rule_priority_key(bank_account_id: str = ""):
    """規則的命中優先序。**清單顯示與實際比對共用這一支** —— 各寫一份的話，
    畫面上說「由上而下比對，先命中的先贏」就會是假的。

    帳戶專屬的排在通用的前面：合庫寫「攤還本息」、一銀寫「中小７月」，同一件事
    兩種寫法 —— 綁這個帳戶的規則要贏過「所有帳戶」的。同層再照 sort_order。
    沒指定帳戶時（清單頁沒選帳戶），所有綁帳戶的一律排在通用的前面。
    """
    def key(r):
        if bank_account_id:
            tier = 0 if r.bank_account_id == bank_account_id else (
                2 if r.bank_account_id else 1)
        else:
            tier = 0 if r.bank_account_id else 1
        return (tier, r.sort_order, r.keyword or "")
    return key


async def _load_import_rules(session, bank_account_id: str = ""):
    """回 [(關鍵字, category, 方向)]，已照優先序排好給 _classify 用。"""
    from sqlalchemy import select

    from db.models import BankImportRule
    rows = (await session.execute(
        select(BankImportRule).where(BankImportRule.active.is_(True)))).scalars().all()
    # 別家帳戶專屬的規則不參與這次比對（tier 2）
    rows = [r for r in rows
            if not r.bank_account_id or r.bank_account_id == bank_account_id]
    rows.sort(key=_rule_priority_key(bank_account_id))
    return [(r.keyword, r.category, int(r.direction or 0)) for r in rows]


def _rule_dict(r) -> dict:
    return {"id": r.id, "keyword": r.keyword, "category": r.category,
            "bank_account_id": r.bank_account_id or "",
            "sort_order": r.sort_order, "active": bool(r.active),
            "note": r.note or ""}


@router.get("/import-rules")
async def list_import_rules(request: Request, bank_account_id: str = ""):
    """規則清單。顯示順序＝命中優先序（共用 _rule_priority_key）。

    帶 bank_account_id 就是「這個帳戶實際會怎麼比」；不帶則是綁帳戶的一律在前
    （多帳戶時那不等於任何一個帳戶的真實順序，所以畫面要嘛選帳戶、要嘛別宣稱）。
    """
    _guard(request, level="full")
    from sqlalchemy import select

    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(select(BankImportRule))).scalars().all()
    rows.sort(key=_rule_priority_key(bank_account_id))
    return {"items": [_rule_dict(r) for r in rows]}


async def _apply_rule_payload(r, payload, *, require_all: bool, session):
    """驗規則欄位並寫進 r。新增與修改共用 —— 各寫一份的話，防呆會只加在一邊。

    require_all：新增時關鍵字與類別都必填；修改時允許只送要改的那個。
    """
    kw = (payload.keyword or "").strip()
    cat = (payload.category or "").strip()
    if require_all and (not kw or not cat):
        raise HTTPException(status_code=422, detail="關鍵字與類別都要填")
    if kw:
        _assert_usable_keyword(kw)
    await _assert_known_category(cat, session)
    r.keyword = kw or r.keyword
    r.category = cat or r.category
    r.bank_account_id = payload.bank_account_id or None
    r.sort_order = payload.sort_order
    r.active = payload.active
    r.note = (payload.note or "")[:255]
    return r


@router.post("/import-rules")
async def create_import_rule(payload: BankImportRulePayload, request: Request):
    _guard(request, level="full")
    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        r = await _apply_rule_payload(
            BankImportRule(id=uuid.uuid4().hex, direction=0), payload,
            require_all=True, session=session)
        session.add(r)
        await session.commit()
        return _rule_dict(r)


@router.put("/import-rules/{rule_id}")
async def update_import_rule(rule_id: str, payload: BankImportRulePayload,
                             request: Request):
    _guard(request, level="full")
    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        r = await session.get(BankImportRule, rule_id)
        if not r:
            raise HTTPException(status_code=404, detail="規則不存在")
        await _apply_rule_payload(r, payload, require_all=False, session=session)
        r.updated_at = datetime.now()
        await session.commit()
        return _rule_dict(r)


@router.delete("/import-rules/{rule_id}")
async def delete_import_rule(rule_id: str, request: Request):
    _guard(request, level="full")
    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        r = await session.get(BankImportRule, rule_id)
        if not r:
            raise HTTPException(status_code=404, detail="規則不存在")
        await session.delete(r)
        await session.commit()
    return {"ok": True}


# 日期樣（含 09-08 這種沒有年份的）與純數字串 —— 這種關鍵字會把「所有那天/
# 那個號碼的交易」一網打盡，而且完全看不出是錯的。
# 尾端允許殘留的分隔符 —— `2026/08/` 正是原本那個 bug 的字面值（slice(0,8) 切出來的），
# 不收尾綴的話它會漏網，而它恰好是最危險的那一個。
_KW_DATEISH = re.compile(r"^[\d０-９]{1,4}([/\-.][\d０-９]{0,2}){1,2}$")
_KW_NUMISH = re.compile(r"^[\d０-９]+$")


def _assert_usable_keyword(kw: str):
    """關鍵字的形狀防呆。

    🔴 這條擋的是**會生效但意思完全不對**的規則：
    - `2026/08/` → 那個月的交易全歸這一類（原本預覽的「＋規則」預設值就長這樣，
      因為摘要裡留著銀行的入帳日欄，owner 2026-08-20 看畫面時發現）
    - `150725950595` → 一個帳號，只會中那一筆，看起來卻像設好了規則
    - 單一個字 → 「中」會把「中小」「中信」「台中」全部掃進來

    錯的規則不會報錯，只會靜靜把帳分到錯的地方，所以寧可在建立時就擋。
    """
    kw = (kw or "").strip()
    if len(kw) < 2:
        raise HTTPException(status_code=422, detail="關鍵字至少兩個字 —— 單字會誤中一大片")
    if _KW_DATEISH.match(kw):
        raise HTTPException(
            status_code=422,
            detail=f"「{kw}」看起來是日期 —— 那會把那天/那個月的交易全歸成同一類。"
                   f"請改用摘要裡描述性質的字，例如「電信費」。")
    if _KW_NUMISH.match(kw):
        raise HTTPException(
            status_code=422,
            detail=f"「{kw}」是純數字（多半是帳號或流水號）—— 只會中那一筆，"
                   f"卻看起來像設好了規則。請改用描述性質的字。")


async def _assert_known_category(cat: str, session):
    """規則只能填 finance_category_map 認得的類別。

    🔴 不然規則會產出一個報表看不懂的值，那些列全部靜靜落到「未歸類」——
    而且是**匯入當下就分好類**的假象，比沒分類更難發現。

    session 必填 —— 呼叫端一定已經開著一個。在已開的 session 裡再開一個等於
    同時佔兩條連線，那是會被照抄的形狀（而且那條分支根本沒人走）。
    """
    if not cat:
        return
    from routers.crm._shared import cash_category_texts
    known = set(await cash_category_texts(session))
    if cat not in known:
        raise HTTPException(
            status_code=422,
            detail=f"「{cat}」不在收支科目對映裡 —— 規則只能填報表認得的類別。"
                   f"要新增科目請到帳務設定。")


@router.post("/import-rules/apply-unclassified")
async def apply_rules_to_unclassified(request: Request, entity: str = ""):
    """把現行規則套用到**還沒分類**的既有收支列。

    🔴 只碰 category 是空的列。已經有類別的（不管是規則分的還是人手改的）一律
    不動 —— 規則是會改的，讓它回頭重寫已經進帳的分類，等於每次調規則都在
    重寫歷史帳（owner 2026-08-20 拍板：不自動追溯）。
    """
    ent = _guard(request, entity, level="full")
    from sqlalchemy import or_, select

    from core.bank_statement import _classify
    from db.models import CrmCashEntry
    factory = _factory_or_503()
    changed, by_cat = 0, {}
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCashEntry).where(
                CrmCashEntry.entity == ent,
                or_(CrmCashEntry.category.is_(None), CrmCashEntry.category == "")))
        ).scalars().all()
        # 規則依帳戶不同 → 逐帳戶載一次（同帳戶的列共用）
        cache = {}
        for e in rows:
            acct = e.bank_account_id or ""
            if acct not in cache:
                cache[acct] = await _load_import_rules(session, acct)
            cat, _d = _classify(f"{e.summary or ''} {e.note or ''}", cache[acct])
            if cat:
                e.category = cat
                e.updated_at = datetime.now()
                changed += 1
                by_cat[cat] = by_cat.get(cat, 0) + 1
        if changed:
            await session.commit()
    return {"ok": True, "scanned": len(rows), "changed": changed, "by_category": by_cat}


@router.post("/bank-statement/preview")
async def preview_bank_statement(
        request: Request,
        bank_account_id: str = Form(""),
        text: str = Form(""),
        file: UploadFile | None = File(None)):
    """上傳/貼上銀行對帳單 → 解析＋分類＋配貸款期別。**只讀不寫**。

    輸入（multipart）：file=對帳單檔（PDF/CSV/TXT）或 text=貼上的文字，
    外加 bank_account_id。回每一列的建議動作與所有警告 —— 前端逐列給人確認，
    確認後才打 /bank-statement/apply。

    重複匯入偵測：同帳戶同日同金額已經有收支明細 → 標 duplicate=True 預設不勾。
    """

    # 🔴 先驗身份再碰檔案。原本是讀完 8MB、跑完 PDF 文字抽取（CPU-bound）才在
    # 拿到帳戶之後守衛 —— 未登入的人也能讓這台機器去解析他上傳的 PDF。
    # 下面拿到 acct 之後那次 entity 守衛照留（這次守的是「有沒有權限」，
    # 那次守的是「這個帳本在不在你的 scope 裡」），兩次不是重複。
    _guard(request, level="full")
    acct_id = (bank_account_id or "").strip()
    if not acct_id:
        raise HTTPException(status_code=422, detail="請先選擇這份對帳單是哪個銀行帳戶")
    text = (text or "").strip()
    if file is not None:
        blob = await file.read()
        if len(blob) > _STMT_MAX_BYTES:
            raise HTTPException(status_code=422, detail="檔案過大（上限 8MB）")
        import asyncio
        text = await asyncio.to_thread(_statement_text, file.filename or "", blob)
    if not text.strip():
        raise HTTPException(status_code=422, detail="沒有收到對帳單內容（檔案或貼上的文字）")

    factory = _factory_or_503()
    async with factory() as session:
        acct, ent = await _acct_and_entity(session, request, acct_id)

        return await _build_statement_preview(session, acct, ent, text)


async def _build_statement_preview(session, acct, ent, text):
    """解析對帳單 → 逐列建議 + 選項清單。**只讀不寫**。

    上傳預覽與「開啟草稿」共用這一支：草稿存的是**原始文字**，開啟時重新解析，
    才拿得到最新的重複判定（草稿放兩天，中間可能有幾列已經從別的路進帳了）
    與最新的專案／發票清單。人工掛好的專案與發票由呼叫端貼回去。
    """
    from core.bank_statement import (LOAN_CATEGORY, match_loan_payments,
                                     parse_statement)
    acct_id = acct.id
    # 分類規則走 DB（使用者可編），不是寫死那 14 條
    res = parse_statement(text, rules=await _load_import_rules(session, acct_id))
    if not res.ok:
        return {"ok": False, "errors": res.errors,
                "warnings": res.warnings, "rows": []}

    loans = await _loans_for_matching(session, ent)
    matches = match_loan_payments(res.rows, loans)
    # line_no → 該列被配到的貸款期別
    by_line = {}
    for m in matches:
        for ln in m["lines"]:
            by_line[ln] = m

    # 重複偵測：同帳戶、同日、同金額已存在 → 這列多半已經匯過
    existing = await _existing_entry_keys(
        session, acct_id, sorted({r.date for r in res.rows}))

    # 科目對映：category 沒對映的要標出來（不然匯進去報表變未歸類）
    from routers.crm._shared import cash_category_texts
    mapped = set(await cash_category_texts(session))

    # 預覽要能當場掛專案／發票（owner 2026-08-20）→ 選項一起回，
    # 前端不用再多打兩支 API（那兩支還在不同的 prefix 下）。
    from sqlalchemy import or_, select

    from core.project_link import CASH_CATEGORIES
    from db.models import Client, CrmInvoice, CrmProject
    # 挑選視窗要看得出「是哪個案子」—— 只有名稱不夠（同名/近名的案子很多）
    cmap = {c.id: c.short_name for c in
            (await session.execute(select(Client))).scalars().all()}
    projects = [{
        "id": p.id, "name": p.name,
        "client": cmap.get(p.client_id, ""),
        "status": p.status or "",
        "start": str(p.start_date)[:10] if p.start_date else "",
    } for p in (await session.execute(
        select(CrmProject).order_by(CrmProject.created_at.desc()))).scalars().all()]
    # 發票只列**還沒收齊**的收款發票 —— 已經收完的列出來只會讓人選錯
    # 方向與作廢在 SQL 就篩掉：撈回來再用 Python 篩的話，下一步的
    # _invoice_collections 會拿到全部 400 個 id，而不是真正需要的那 150 個。
    from routers.crm.finance import _invoice_collections, collection_fields
    invs = (await session.execute(
        select(CrmInvoice).where(
            CrmInvoice.entity == ent,
            or_(CrmInvoice.payment_type == "收款",
                CrmInvoice.payment_type.is_(None)),
            or_(CrmInvoice.issue_status != "作廢",
                CrmInvoice.issue_status.is_(None))))).scalars().all()
    coll = await _invoice_collections(session, [i.id for i in invs])
    open_invs = []
    for i in invs:
        # 🔴「收齊了沒」走 collection_fields（含 NT$50 匯費容差），不要自己算
        # `outstanding > 0` —— 被匯費短收 30 元的那 42 張歷史發票，在收支明細的
        # 下拉裡是「收齊、不列出」，在這個挑選視窗卻會冒出來說「尚欠 $30」，
        # 引人再掛一次款。欄名也用發票的正式欄名，兩個挑選器才有共用的可能。
        c = collection_fields(i.amount_total, coll.get(i.id))
        if c["settled"]:
            continue
        open_invs.append({
            "id": i.id, "invoice_number": i.invoice_number or "",
            "title": i.title or "", "company_name": i.company_name or "",
            "date": str(i.invoice_date)[:10] if i.invoice_date else "",
            "amount_total": int(i.amount_total or 0), **c})
    # 排序交給前端 —— 它要依「跟該列入帳金額的接近程度」排，而那是逐列不同的
    open_invs.sort(key=lambda x: (x["date"] or "", x["invoice_number"]), reverse=True)

    out_rows = []
    for r in res.rows:
        m = by_line.get(r.line_no)
        # 兩條重複線：①同帳戶同日同金額已有收支 ②配到的貸款期別已經記過繳款
        # （②必要 —— 繳款收支存的是攤還表金額，跟銀行實扣差幾十元，①抓不到）
        dup = (existing[(r.date, r.amount)] > 0
               or (m or {}).get("confidence") == "already_paid")
        if dup and existing[(r.date, r.amount)] > 0:
            existing[(r.date, r.amount)] -= 1   # multiset：一筆抵一筆
        out_rows.append({
            "date": r.date, "amount": r.amount, "description": r.note[:120],
            "category": r.category, "inferred": r.inferred,
            # 🔴 方向是推出來的列**不預設勾選**：我們才剛跟使用者說「這列的方向
            # 是猜的」，不能又讓表頭那顆全選一按就把猜測寫進帳。要它就自己勾。
            "duplicate": dup, "selected": not dup and not r.inferred,
            # 🔴 兩種都會落到報表的「未歸類」：有類別但沒對映、以及**完全沒類別**
            # （摘要沒中任何 KEYWORD_RULES）。舊寫法只認前者，後者靜默通過 ——
            # 那是更該提醒的一種，連該歸哪裡都不知道。
            "unmapped_category": (not r.category) or (r.category not in mapped),
            "loan_id": (m or {}).get("loan_id"),
            "loan_name": (m or {}).get("loan_name", ""),
            "period_no": (m or {}).get("period_no"),
            "match_confidence": (m or {}).get("confidence", ""),
            "is_loan": r.category == LOAN_CATEGORY and r.amount < 0,
        })
    return {
        "ok": True, "rows": out_rows, "warnings": res.warnings, "errors": [],
        # 原始文字原樣回給前端 —— 存草稿時要送回來（上傳的是 PDF 時，
        # 文字是後端抽出來的，前端手上沒有）
        "source_text": text,
        # 帳戶跟著回 —— 上傳與開啟草稿兩條路才不用各自記一份（一條從表單欄位
        # 拿、一條從草稿頭拿，同一個值兩個來源）
        "bank_account_id": acct.id,
        "projects": projects,
        "invoices": open_invs,
        # 哪些類別收得下專案 —— 前端拿它決定專案下拉何時可用（規則同收支明細）
        "project_categories": list(CASH_CATEGORIES),
        # 有科目對映的類別。前端改分類時要用同一條規則判「會不會落到未歸類」，
        # 不然改成一個沒對映的類別之後那個提醒就消失了。
        # （也省掉前端跨 prefix 去打 /crm/cash-entries/options 那一支）
        "mapped_categories": sorted(mapped),
        "summary": {"count": len(out_rows), "total_in": res.total_in,
                    "total_out": res.total_out,
                    "duplicates": sum(1 for r in out_rows if r["duplicate"]),
                    "loan_rows": sum(1 for r in out_rows if r["is_loan"])},
    }


def _draft_key(date, amount, description):
    """人工決定貼回去用的鍵。摘要取前 120 字 —— 存的時候也是這個長度。"""
    return (str(date or ""), int(amount or 0), (description or "")[:120])


@router.get("/bank-statement/drafts")
async def list_statement_drafts(request: Request, entity: str = "parent"):
    """未匯入的草稿清單（對帳系統卡片上顯示）。"""
    from sqlalchemy import select

    from db.models import BankImportDraft
    ent = _guard(request, entity, level="full")   # 草稿＝記帳層的東西，跟匯入同級
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(BankImportDraft)
            .where(BankImportDraft.entity == ent)
            .order_by(BankImportDraft.updated_at.desc()))).scalars().all()
        # 草稿的名字（_auto_draft_name）本來就以帳戶名開頭，不必再多撈一次帳戶 ——
        # 那個查詢還是**不分帳本**全撈的，只為了填一個前端沒在讀的欄位
        return {"drafts": [{
            "id": d.id, "name": d.name,
            "bank_account_id": d.bank_account_id,
            "updated_at": _fmt_minute(d.updated_at),
        } for d in rows]}


@router.post("/bank-statement/drafts")
async def save_statement_draft(payload: StatementDraftPayload, request: Request):
    """存草稿（id 有值＝更新）。**不寫任何帳** —— 只是把做到一半的決定收起來。"""
    from db.models import BankImportDraft
    factory = _factory_or_503()
    async with factory() as session:
        acct, ent = await _acct_and_entity(session, request, payload.bank_account_id)
        if not (payload.source_text or "").strip():
            raise HTTPException(status_code=422, detail="草稿沒有對帳單內容")

        decisions = [{
            "date": r.date, "amount": r.amount,
            "description": (r.description or "")[:120],
            "category": r.category or "",
            "loan_id": r.loan_id, "period_no": r.period_no,
            "project_id": r.project_id,
            "invoices": [{"invoice_id": x.invoice_id, "amount": x.amount}
                         for x in (r.invoices or [])],
            "selected": bool(r.selected),
        } for r in payload.rows]

        d = None
        if payload.id:
            d = await session.get(BankImportDraft, payload.id)
            if d and (d.entity or "parent") != ent:
                raise HTTPException(status_code=409, detail="草稿與帳戶分屬不同帳本")
        if d is None:
            d = BankImportDraft(id=uuid.uuid4().hex, entity=ent)
            session.add(d)
        d.bank_account_id = payload.bank_account_id
        d.name = _auto_draft_name(acct, payload.rows)
        d.source_text = payload.source_text
        d.decisions = json.dumps(decisions, ensure_ascii=False)
        d.row_count = len(decisions)
        d.created_by = _username(request)
        d.updated_at = datetime.now()
        await session.commit()
        return {"ok": True, "id": d.id, "name": d.name}


def _auto_draft_name(acct, rows):
    """沒給名字就自己取：帳戶 + 日期區間 + 列數。夠用來認出是哪一份。"""
    days = sorted(r.date for r in rows if r.date)
    span = f"{days[0]}～{days[-1]}" if days else "（空）"
    return f"{acct.name} {span}（{len(rows)} 筆）"[:120]


@router.get("/bank-statement/drafts/{draft_id}")
async def open_statement_draft(draft_id: str, request: Request):
    """開啟草稿 → **重新解析**原始文字，再把人工決定貼回去。

    🔴 不是把當初的解析結果原封不動端回來。草稿可能放了兩天，這中間：
      · 有幾列已經從別的路進帳了（重複判定要重算，否則會匯第二次）
      · 有些發票已經收齊了（不該再出現在候選清單裡）
      · 貸款期別可能已經被別的匯入認領了
    所以重新跑一次 preview，只把「人做的決定」蓋回去。
    """
    from db.models import BankAccount, BankImportDraft
    factory = _factory_or_503()
    async with factory() as session:
        d = await session.get(BankImportDraft, draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="草稿不存在（可能已經匯入或被刪掉）")
        acct = await session.get(BankAccount, d.bank_account_id)
        if not acct:
            raise HTTPException(status_code=404, detail="這份草稿的帳戶已經不在了")
        ent = _guard(request, acct.entity or "parent", level="full")

        out = await _build_statement_preview(session, acct, ent, d.source_text)
        head = {"draft_id": d.id, "draft_name": d.name,
                "bank_account_id": d.bank_account_id}
        if not out.get("ok"):
            return {**out, **head}

        # 人工決定貼回去。同一份對帳單裡可能有兩列一模一樣（同日同額同摘要），
        # 所以一個鍵存一串、依序 pop —— 用 dict 直接覆蓋會讓兩列共用同一個決定。
        saved = {}
        for x in json.loads(d.decisions or "[]"):
            saved.setdefault(
                _draft_key(x.get("date"), x.get("amount"), x.get("description")),
                []).append(x)
        restored = 0
        for r in out["rows"]:
            bucket = saved.get(_draft_key(r["date"], r["amount"], r["description"]))
            if not bucket:
                continue
            x = bucket.pop(0)
            restored += 1
            r["category"] = x.get("category") or r["category"]
            r["project_id"] = x.get("project_id")
            r["invoices"] = x.get("invoices") or []
            # 🔴 重複的列一律不勾，即使草稿裡勾了 —— 存草稿之後才被匯進去的列，
            # 使用者當初勾的時候還不重複。這裡以「現在的帳」為準。
            r["selected"] = bool(x.get("selected")) and not r["duplicate"]
        # draft_missing = 存檔時有決定、重新解析後對不回去的列數。會發生在
        # 對帳單被重新下載過（摘要或金額改了）—— 那些決定就這樣消失了，
        # 前端要說出來，不然人以為自己上次沒做完。
        return {**out, **head, "draft_restored": restored,
                "draft_missing": sum(len(v) for v in saved.values())}


@router.delete("/bank-statement/drafts/{draft_id}")
async def delete_statement_draft(draft_id: str, request: Request):
    from db.models import BankImportDraft
    factory = _factory_or_503()
    async with factory() as session:
        d = await session.get(BankImportDraft, draft_id)
        if not d:
            return {"ok": True}
        _guard(request, d.entity or "parent", level="full")
        await session.delete(d)
        await session.commit()
        return {"ok": True}


@router.post("/bank-statement/apply")
async def apply_bank_statement(payload: StatementImportApply, request: Request):
    """把確認過的列寫進帳：一般列 → 收支明細；貸款繳款列 → 走繳款流程
    （標期別已繳＋自動建帶 loan_payment_id 硬連結的收支，與手動按繳款同一條路）。

    月結守衛：任一列落鎖定月 → 整批 409，不做半套。
    """
    _guard(request, level="full")
    if not payload.rows:
        raise HTTPException(status_code=422, detail="沒有要匯入的列")
    from db.models import CrmCashEntry
    # 專案／發票的寫入規則只有一套正本，在 crm/finance —— 這裡借用，不另寫
    from routers.crm.finance import (_enforce_cash_project_link,
                                     replace_invoice_allocs_bulk,
                                     resolve_invoice_allocs)

    factory = _factory_or_503()
    async with factory() as session:
        # 整份對帳單要用到的發票一次撈完。逐列各撈一次的話，一份 60 列的月對帳單
        # 就是 25 個往返，一年份好幾百個 —— 而且全部關在同一個交易裡（那段時間
        # crm_invoices 上是有寫鎖的）。
        from sqlalchemy import select as _select

        from db.models import CrmInvoice
        inv_ids = {x.invoice_id for r in payload.rows for x in (r.invoices or [])}
        inv_by_id = {i.id: i for i in (await session.execute(
            _select(CrmInvoice).where(CrmInvoice.id.in_(inv_ids))
        )).scalars().all()} if inv_ids else {}

        acct, ent = await _acct_and_entity(session, request, payload.bank_account_id)

        # 先驗全部月份（整批原子性：有一列落鎖定月就全部不做）。
        # 🔴 用 _assert_rows_open 而不是逐列 _assert_month_open —— 後者每呼叫一次就
        # 重撈一次鎖定月集合（一年份對帳單 = 1,000+ 次多餘往返），而且只會在第一個
        # 違規列就中斷；批次版一次撈完、409 把所有違規列一起列出來。
        dated = []
        for r in payload.rows:
            d = _parse_day(r.date)
            if not d:
                raise HTTPException(status_code=422, detail=f"日期無法解析：{r.date}")
            dated.append((f"{r.date} {(r.description or '')[:20]}", d))
        await _assert_rows_open(session, dated, entity=ent)

        # 🔴 重複防護不能只活在 preview 的顯示層：表頭那顆全選會把「已匯過」的
        # 列一起勾起來，送出逾時重按一次也一樣 —— 兩條路都直接寫進 CrmCashEntry，
        # 同日同額的收支就變兩份，帳戶餘額與三張報表全部雙倍。貸款列本來就有
        # status=="paid" 擋著，一般列在這之前一個防護都沒有。
        seen = await _existing_entry_keys(
            session, payload.bank_account_id, sorted(r.date for r in payload.rows))
        made_entries = made_payments = 0
        skipped_dup = []
        linked = []          # 有掛發票的收款列 → commit 前要進分配表並重算發票狀態
        # 對帳工作台的「銀行說發生了什麼」那一欄。
        #
        # 🔴 這支端點原本只寫帳（右欄），左欄留白 —— 用它匯完一整年，打開工作台
        # 會看到「帳上 30 筆、銀行 0 筆」，看起來像銀行整年沒有任何交易。工作台
        # 自己那條匯入路徑只填左欄不寫帳，兩邊各做一半，誰都沒說完整的話。
        # 這裡兩欄一起填，並且直接把兩邊配起來（matched_entry_id）：帳本來就是
        # 從這份對帳單記的，本來就是同一件事，不需要人再去勾一次。
        stmt_lines = []
        for r, (_label, d) in zip(payload.rows, dated):
            if r.loan_id and r.period_no:
                loan, row = await _get_loan_and_period(session, r.loan_id, r.period_no)
                if (loan.entity or "parent") != ent:
                    raise HTTPException(status_code=409, detail="貸款與帳戶分屬不同帳本")
                if (row.status or "") == "paid":
                    continue          # 已繳過就跳過（重跑不重複記）
                # r.amount 是帶號的（支出為負）→ 取絕對值當實扣。
                # 這才是銀行真的扣的錢，攤還表只決定本息拆分。
                ent_line = _record_loan_payment(
                    session, loan, row, d, payload.bank_account_id,
                    note="銀行對帳單匯入", actual_amount=abs(int(r.amount or 0)))
                made_payments += 1
                stmt_lines.append((r, ent_line))
            else:
                amt = int(r.amount or 0)
                if not amt:
                    continue
                key = (r.date, amt)
                if seen[key] > 0:
                    seen[key] -= 1          # multiset：帳上有幾筆就跳過幾筆
                    skipped_dup.append(f"{r.date} {amt:+,}")
                    continue
                ce = CrmCashEntry(
                    id=uuid.uuid4().hex, entry_date=d,
                    expense=(-amt if amt < 0 else None),
                    deposit=(amt if amt > 0 else None),
                    summary=(r.description or "銀行對帳單")[:255],
                    note="銀行對帳單匯入",
                    category=(r.category or "").strip() or None,
                    bank_account_id=payload.bank_account_id, entity=ent)
                # 預覽時當場掛的專案／發票（owner 2026-08-20）。
                # 🔴 走 crm/finance 的既有 helper，不在這裡自己寫一套：
                #  - 專案要過 _enforce_cash_project_link（行政/薪資掛專案會讓
                #    專案毛利多算一筆不屬於它的錢）
                #  - 發票要進**分配表**而不是只寫 invoice_id —— 列表那欄讀 invoice_id、
                #    但「已收金額」與關聯面板讀 crm_cash_invoice_links，只寫一邊
                #    會變成兩處各講各的（這一輪已經修過同樣的洞）
                if r.project_id:
                    ce.project_id = r.project_id
                    _enforce_cash_project_link(ce)
                # 發票：一列可以掛多張（合併匯款 —— 客戶一次匯三張的錢）。
                # 驗證與寫入都走 crm/finance 的共用兩支 —— 分配表有三條寫入路徑
                # （這裡、關聯面板、編輯視窗），各寫一份的話規則遲早分岔。
                allocs = [(x.invoice_id, int(x.amount or 0)) for x in (r.invoices or [])]
                session.add(ce)
                made_entries += 1
                stmt_lines.append((r, ce))
                if allocs and amt > 0:
                    linked.append((ce, await resolve_invoice_allocs(
                        session, allocs, ent, by_id=inv_by_id)))

        # 工作台可能已經自己匯過同一個月（它那條路只填左欄不寫帳）。那些列就是
        # 這幾筆交易，不該再建一份 —— 找同日同額且還沒配對的，直接拿來配。
        from collections import defaultdict

        from sqlalchemy import and_, select

        from db.models import BankStatementLine
        spare = defaultdict(list)
        if stmt_lines:
            months = {r.date[:7] for r, _ce in stmt_lines}
            for ln in (await session.execute(
                    select(BankStatementLine).where(and_(
                        BankStatementLine.bank_account_id == payload.bank_account_id,
                        BankStatementLine.month.in_(months),
                        BankStatementLine.matched_entry_id.is_(None))))).scalars():
                key = (local_day(ln.line_date).strftime("%Y-%m-%d")
                       if ln.line_date else "", int(ln.amount or 0))
                spare[key].append(ln)
        for r, ce in stmt_lines:
            key = (r.date, int(r.amount or 0))
            if spare[key]:
                spare[key].pop().matched_entry_id = ce.id       # 沿用工作台已有的那列
                continue
            session.add(BankStatementLine(
                id=uuid.uuid4().hex,
                bank_account_id=payload.bank_account_id,
                month=r.date[:7],
                line_date=_parse_day(r.date),
                description=(r.description or "")[:255],
                # 左欄記**銀行說的**金額。貸款列的收支金額也已經改記實扣，
                # 兩邊會一致 —— 工作台要求金額相等才算配對得上。
                amount=int(r.amount or 0),
                matched_entry_id=ce.id,
                created_by="銀行對帳單匯入"))
        # 分配表與發票收款狀態 —— 要等收支列有 id（flush）之後才做得了
        if linked:
            await session.flush()
            # 整批一次寫 —— 逐筆呼叫時，每列都要「查舊連結、刪、flush、重算」，
            # 而這些收支列是剛建出來的，舊連結必定是空的
            await replace_invoice_allocs_bulk(session, linked)
        await session.commit()
    return {"ok": True, "entries": made_entries, "loan_payments": made_payments,
            "statement_lines": len(stmt_lines),
            "linked_invoices": sum(len(v) for _ce, v in linked),
            "skipped_duplicates": skipped_dup}
