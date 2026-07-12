"""services/finance_statements.py — 財務三表聚合服務（階段三）。

分層：規則全部在 core/finance_logic.py 純函式（有黃金測試）；本模組只做
「DB 撈數 → 餵純函式 → 快照合併」。routers/api_finance.py（/statements、
/statements/drilldown）與 routers/api_cashflow.py（close_month 快照 v2）
共用 — 放 services/ 避免兩個 router 互相 import（services 在 OTA AGENT_DIRS
內，機隊可收到）。

設計取捨（v1）：
- 全表載入一次（發票/請款/收支/器材/調整/科目/對映/帳戶）→ 純函式自行按月
  過濾。公司規模千列級，一趟載入比多段窗口 SQL 簡單且三表數字保證同源。
- 鎖定月優先讀快照 v2：PnL/CF 逐月線性可加（merge_pnl/merge_cf）；
  BS 取期末月快照、期末月未鎖則 live。CF 的期初/期末餘額永遠 live 重算
  （餘額推導不受鎖月影響，鎖定月資料不可改 → live == 快照當時值）。
- CrmProjectExpense 查證結論（2026-07-11，見 build_pnl docstring）：只餵
  「有掛 advance_id」的支出明細進損益（現金對應 treatment='advance' 不進
  損益 → 不重複）；未掛 advance_id 的專案雜支與請款單/收支明細重疊，不計。
  該表無支出日期欄，以 created_at 定月。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from core.finance_logic import (
    aging_buckets,
    ap_open_payments,
    ar_open_invoices,
    ar_overdue_amount,
    bank_balances_asof,
    bank_fee_total,
    build_balance_sheet,
    build_cashflow,
    build_pnl,
    cash_entry_activity,
    cash_entry_flow,
    classify_cash_entry,
    client_concentration,
    depreciation_rows,
    in_amount,
    invoice_collected,
    invoice_ex_tax,
    invoice_tax,
    invoice_vat_side,
    iter_expense_items,
    iter_loan_interest,
    iter_revenue_invoices,
    loan_outstanding_rows,
    local_day,
    map_account,
    merge_cf,
    merge_pnl,
    month_of,
    month_range,
    out_amount,
    resolve_client_name,
    runway_months,
    shift_month,
    statement_interpretation,
    statement_warnings,
    VALID_DRILL_KINDS,  # re-export：endpoint 與單元測試由本模組取用
    vat_position,
)
from core.crm_logic import project_margin

logger = logging.getLogger(__name__)

DRILLDOWN_LIMIT = 500


# ── 輸入載入 ─────────────────────────────────────────────────

def _dump(rows, *cols) -> list:
    """ORM 列 → 純函式吃的 dict 投影（key == 欄位名）。"""
    return [{c: getattr(r, c) for c in cols} for r in rows]


async def _load_inputs(session) -> dict:
    """全表載入 → 純函式吃的 dict/list（欄位子集，含 drilldown 需要的識別欄）。"""
    from sqlalchemy import select
    from db.models import (BankAccount, CrmCashEntry, CrmInvoice,
                           CrmPaymentRequest, Equipment, FinanceAccount,
                           FinanceAdjustment, FinanceCategoryMap, FinanceLoan,
                           FinanceLoanPayment)

    async def _all(model, order_by=None):
        q = select(model)
        if order_by is not None:
            q = q.order_by(*order_by)
        return (await session.execute(q)).scalars().all()

    invoices = _dump(await _all(CrmInvoice),
                     "id", "payment_type", "issue_status", "payment_status",
                     "category", "amount_total", "amount_ex_tax", "tax_amount",
                     "commission", "invoice_date", "paid_date", "title",
                     "invoice_number", "company_name", "tax_id", "project_id")
    payments = _dump(await _all(CrmPaymentRequest),
                     "id", "is_advance", "request_date", "amount", "category",
                     "summary", "payee_name", "payment_status", "payment_date",
                     "payee_id", "payee_type", "invoice_amount")
    cash_entries = _dump(await _all(CrmCashEntry),
                         "id", "entry_date", "deposit", "expense", "bank_fee",
                         "claim", "category", "summary", "invoice_id",
                         "advance_payment_id", "payment_request_id",
                         "bank_account_id")
    equipment = _dump(await _all(Equipment),
                      "id", "name", "purchase_cost", "purchase_date",
                      "depreciation_months", "retired_date", "status")
    adjustments = _dump(await _all(FinanceAdjustment),
                        "id", "adj_date", "amount", "adj_type", "description",
                        "account_id")
    bank_accounts = _dump(await _all(BankAccount,
                                     order_by=(BankAccount.sort_order,
                                               BankAccount.created_at)),
                          "id", "name", "opening_balance", "opening_date",
                          "acct_kind", "active")
    for b in bank_accounts:
        b["active"] = bool(b["active"])
    loans = _dump(await _all(FinanceLoan),
                  "id", "name", "principal", "opening_balance", "start_date",
                  "bank_account_id")
    loan_payments = _dump(await _all(FinanceLoanPayment),
                          "id", "loan_id", "period_no", "due_date",
                          "principal_due", "interest_due", "status", "paid_at")

    accounts = {r.id: {"code": r.code, "name": r.name, "pnl_group": r.pnl_group,
                       "cf_activity": r.cf_activity, "acct_type": r.acct_type}
                for r in (await session.execute(select(FinanceAccount))).scalars().all()}

    cat_map = {(r.source, r.category_text): {"treatment": r.treatment,
                                             "account_id": r.account_id}
               for r in (await session.execute(
                   select(FinanceCategoryMap).where(
                       FinanceCategoryMap.active.is_(True)))).scalars().all()}

    return {"invoices": invoices, "payments": payments,
            "cash_entries": cash_entries, "equipment": equipment,
            "adjustments": adjustments, "bank_accounts": bank_accounts,
            "loans": loans, "loan_payments": loan_payments,
            "accounts": accounts, "cat_map": cat_map}


async def _advance_state(session) -> dict:
    """未結清預支餘額合計 + 預支核銷支出列（餵損益 營業成本-費）。

    三來源一次 GROUP BY（不逐預支 N+1），逐預支套 core.crm_logic
    compute_advance_status（與 /payments/advances 端點同一套判定）。
    """
    from sqlalchemy import select, func as safunc
    from core.crm_logic import compute_advance_status
    from db.models import CrmCashEntry, CrmPaymentRequest, CrmProjectExpense

    advances = (await session.execute(
        select(CrmPaymentRequest.id, CrmPaymentRequest.amount)
        .where(CrmPaymentRequest.is_advance == 1))).all()
    exp_by_adv = {row[0]: int(row[1] or 0) for row in (await session.execute(
        select(CrmProjectExpense.advance_id, safunc.sum(CrmProjectExpense.actual))
        .where(CrmProjectExpense.advance_id.isnot(None),
               CrmProjectExpense.advance_id != "")
        .group_by(CrmProjectExpense.advance_id))).all()}
    pay_by_adv = {row[0]: int(row[1] or 0) for row in (await session.execute(
        select(CrmCashEntry.advance_payment_id, safunc.sum(CrmCashEntry.expense))
        .where(CrmCashEntry.advance_payment_id.isnot(None),
               CrmCashEntry.advance_payment_id != "",
               CrmCashEntry.expense > 0)
        .group_by(CrmCashEntry.advance_payment_id))).all()}
    ret_by_adv = {row[0]: int(row[1] or 0) for row in (await session.execute(
        select(CrmCashEntry.advance_payment_id, safunc.sum(CrmCashEntry.deposit))
        .where(CrmCashEntry.advance_payment_id.isnot(None),
               CrmCashEntry.advance_payment_id != "",
               CrmCashEntry.deposit > 0)
        .group_by(CrmCashEntry.advance_payment_id))).all()}

    balance_total = 0
    for adv_id, amount in advances:
        st = compute_advance_status(amount or 0, exp_by_adv.get(adv_id, 0),
                                    pay_by_adv.get(adv_id, 0),
                                    ret_by_adv.get(adv_id, 0))
        if not st["is_settled"]:
            balance_total += st["balance"]

    exp_rows = (await session.execute(
        select(CrmProjectExpense).where(
            CrmProjectExpense.advance_id.isnot(None),
            CrmProjectExpense.advance_id != ""))).scalars().all()
    expenses = [{"id": x.id, "date": x.created_at, "amount": int(x.actual or 0),
                 "label": (x.sub_item or x.category or "預支核銷"),
                 "category": x.category or ""}
                for x in exp_rows if (x.actual or 0)]
    return {"balance_total": balance_total, "expenses": expenses}


def _resolve_baseline(inputs: dict):
    """基準月：settings finance.baseline_month → 資料最早月 → None。"""
    try:
        from config import load_settings
        b = ((load_settings().get("finance") or {}).get("baseline_month") or "").strip()
        if re.match(r"^\d{4}-\d{2}$", b):
            return b
    except Exception:
        pass
    candidates = []
    for inv in inputs["invoices"]:
        candidates.append(month_of(inv.get("invoice_date")))
    for p in inputs["payments"]:
        candidates.append(month_of(p.get("request_date")))
    for e in inputs["cash_entries"]:
        candidates.append(month_of(e.get("entry_date")))
    candidates = [m for m in candidates if m]
    return min(candidates) if candidates else None


def _cf_side(bank_lines: list) -> dict:
    return {"total": sum(int(b.get("amount") or 0) for b in bank_lines),
            "by_account": [{"name": b.get("name") or "?",
                            "amount": int(b.get("amount") or 0)}
                           for b in bank_lines]}


# ── live 三表計算 ────────────────────────────────────────────

async def compute_live(session, months, inputs=None, adv=None) -> dict:
    """指定月集合的 live 三表（不看快照）。BS as_of = 期末月，累積段 =
    baseline..as_of（baseline 缺值時退資料最早月，再退期首月）。"""
    months = sorted(set(months))
    if inputs is None:
        inputs = await _load_inputs(session)
    if adv is None:
        adv = await _advance_state(session)
    as_of = months[-1]
    baseline = _resolve_baseline(inputs) or months[0]
    kw = _pnl_kw(inputs, adv)
    pnl = build_pnl(months, **kw)
    cum_months = month_range(min(baseline, as_of), as_of)
    cum_pnl = pnl if cum_months == months else build_pnl(cum_months, **kw)
    warn = statement_warnings(inputs["cash_entries"], inputs["payments"],
                              inputs["cat_map"], months)
    bank_lines = bank_balances_asof(inputs["bank_accounts"],
                                    inputs["cash_entries"], as_of)
    ar_rows = ar_open_invoices(inputs["invoices"], baseline, as_of)
    ap_rows = ap_open_payments(inputs["payments"], inputs["cat_map"],
                               baseline, as_of)
    vat_cum = vat_position(inputs["invoices"], inputs["cash_entries"],
                           inputs["cat_map"], cum_months)
    bs = build_balance_sheet(
        as_of, bank_lines=bank_lines,
        receivable_total=sum(int(i.get("amount_total") or 0) for i in ar_rows),
        advance_balance=adv["balance_total"],
        equipment=inputs["equipment"], adjustments=inputs["adjustments"],
        payable_total=sum(int(p.get("amount") or 0) for p in ap_rows),
        vat_payable=vat_cum["net"],
        loan_rows=loan_outstanding_rows(inputs["loans"],
                                        inputs["loan_payments"], as_of),
        cumulative_net=cum_pnl["net"]["amount"], note_counts=warn)
    opening_lines = bank_balances_asof(inputs["bank_accounts"],
                                       inputs["cash_entries"],
                                       shift_month(months[0], -1))
    cf = build_cashflow(months, opening=_cf_side(opening_lines),
                        closing=_cf_side(bank_lines),
                        cash_entries=inputs["cash_entries"],
                        cat_map=inputs["cat_map"], accounts=inputs["accounts"])
    return {"pnl": pnl, "bs": bs, "cf": cf, "baseline_month": baseline,
            "warnings": warn}


# ── /statements：快照 v2 合併 ────────────────────────────────

async def statements_for_period(session, months) -> dict:
    """期間三表：已鎖月（未重開、快照 v:2）優先讀快照 — PnL/CF 逐月可加總
    （merge_pnl/merge_cf）；BS 取期末月快照、期末月未鎖則 live。
    v1（無 pnl 鍵）快照視同未鎖 → 該月 live 算（讀取端相容）。"""
    from sqlalchemy import select
    from db.models import FinanceMonthClose

    months = sorted(set(months))
    inputs = await _load_inputs(session)

    rows = (await session.execute(
        select(FinanceMonthClose).where(
            FinanceMonthClose.month.in_(months),
            FinanceMonthClose.reopened_at.is_(None)))).scalars().all()
    snaps = {r.month: r.snapshot for r in rows
             if isinstance(r.snapshot, dict) and r.snapshot.get("v") == 2
             and "pnl" in r.snapshot}
    live_months = [m for m in months if m not in snaps]
    # 預支狀態只有 live 計算需要 → 全期間皆快照時不掃三張表
    live = (await compute_live(session, live_months, inputs)
            if live_months else None)

    pnl_parts = [snaps[m]["pnl"] for m in months if m in snaps]
    cf_parts = [snaps[m]["cf"] for m in months if m in snaps]
    if live:
        pnl_parts.append(live["pnl"])
        cf_parts.append(live["cf"])
    pnl = merge_pnl(pnl_parts, len(months))
    # CF 期初/期末餘額永遠 live 重算（推導餘額不受鎖月影響）
    opening = _cf_side(bank_balances_asof(
        inputs["bank_accounts"], inputs["cash_entries"], shift_month(months[0], -1)))
    closing = _cf_side(bank_balances_asof(
        inputs["bank_accounts"], inputs["cash_entries"], months[-1]))
    cf = merge_cf(cf_parts, opening, closing)
    if months[-1] in snaps:
        bs = snaps[months[-1]]["bs"]
    else:
        bs = live["bs"]  # 期末月未鎖 → 必在 live_months 內

    baseline = ((live or {}).get("baseline_month")
                or _resolve_baseline(inputs) or months[0])
    # live 已對同一期間掃過 warnings → 直接複用；否則（有鎖定月）重掃全期間
    warn = (live["warnings"] if live and live_months == months
            else statement_warnings(inputs["cash_entries"], inputs["payments"],
                                    inputs["cat_map"], months))
    interpretation = statement_interpretation(
        pnl, bs, cf, ar_over_60=ar_overdue_amount(inputs["invoices"], baseline))
    return {
        "pnl": pnl, "bs": bs, "cf": cf, "interpretation": interpretation,
        "meta": {"locked_months": sorted(snaps), "live_months": live_months,
                 "baseline_month": baseline, "period_months": months,
                 "warnings": warn["messages"]},
    }


# ── 月結快照 v2 ──────────────────────────────────────────────

async def month_close_extras(session, month: str) -> tuple:
    """close_month 快照 v2 的三表部分 + 鎖帳前檢核警示。

    回 (extras, warnings)：extras = {pnl, bs, cf, checks}（api_cashflow 併上
    v:2 與 cash v1 四欄後入庫）；warnings = 未歸類/未掛帳戶/未填日期 +
    該月各銀行帳戶對帳缺漏（不擋鎖帳，誠實回報）。"""
    inputs = await _load_inputs(session)
    adv = await _advance_state(session)
    live = await compute_live(session, [month], inputs, adv)
    extras = {"pnl": live["pnl"], "bs": live["bs"], "cf": live["cf"],
              "checks": {"bs_diff": live["bs"]["check"]["diff"],
                         "cf_diff": live["cf"]["check"]["diff"]}}
    warnings = list(live["warnings"]["messages"])
    warnings += await _recon_warnings(session, month, inputs["bank_accounts"])
    return extras, warnings


async def _recon_warnings(session, month: str, bank_accounts) -> list:
    """該月對帳缺漏：active 的 bank 類帳戶（零用金 cash 類無對帳單）沒有
    當月 BankReconciliation 列 → 一帳戶一句。"""
    from sqlalchemy import select
    from db.models import BankReconciliation
    done = set((await session.execute(
        select(BankReconciliation.bank_account_id)
        .where(BankReconciliation.month == month))).scalars().all())
    return [f"帳戶「{b['name']}」{month} 尚未對帳"
            for b in bank_accounts
            if b.get("active") and (b.get("acct_kind") or "bank") == "bank"
            and b.get("id") not in done]


# ── Drilldown ────────────────────────────────────────────────

def _row(source, rid, date, label, amount, category="", status="") -> dict:
    # timestamptz 回讀是 UTC 表示 → 轉回本地時區再取日（與 month_of 同理，
    # 否則月初列的顯示日期會少一天）
    if isinstance(date, datetime) and date.tzinfo is not None:
        date = date.astimezone()
    return {"source": source, "id": rid,
            "date": (date.strftime("%Y-%m-%d") if hasattr(date, "strftime")
                     else (date or None)),
            "label": label or "", "amount": int(amount or 0),
            "category": category or "", "status": status or ""}


async def drilldown(session, kind: str, months) -> dict:
    """三表列 → 底層明細（發票/請款/收支簡化 dict + 合計，上限 500 列）。

    kind 值域 = VALID_DRILL_KINDS（與報表行 drill 欄位同一組值 — 單一來源在
    core/finance_logic.py，前端只回傳後端給的 drill）。認列規則同樣不手抄：
    營收吃 iter_revenue_invoices、成本/費用吃 iter_expense_items、折舊/匯費
    derived 虛擬列吃 depreciation_rows/bank_fee_total — 明細合計必然對得上
    報表數字。drilldown 永遠 live 查（快照不存列級資料）。
    kind 無效 → ValueError（endpoint 轉 422）。
    """
    if kind not in VALID_DRILL_KINDS:
        raise ValueError(f"kind 無效: {kind}")
    months = sorted(set(months))
    mset = set(months)
    inputs = await _load_inputs(session)
    cat_map, accounts = inputs["cat_map"], inputs["accounts"]
    items: list = []

    if kind == "revenue":
        for inv in iter_revenue_invoices(inputs["invoices"], mset):
            items.append(_row("invoice", inv["id"], inv.get("invoice_date"),
                              inv.get("title"), invoice_ex_tax(inv),
                              inv.get("category"),
                              "已收" if invoice_collected(inv) else "未收"))
        # 未開票現金收入（direct_income 對映到營業收入科目）
        for e in inputs["cash_entries"]:
            if month_of(e.get("entry_date")) not in mset:
                continue
            if classify_cash_entry(e, cat_map) != "direct_income":
                continue
            acct = map_account(cat_map, accounts, "cash", e.get("category"))
            if (acct or {}).get("pnl_group") == "營業收入":
                items.append(_row("cash", e["id"], e.get("entry_date"),
                                  e.get("summary"),
                                  in_amount(e) - out_amount(e),
                                  e.get("category"), "現金收入"))

    elif kind.startswith("cost.") or kind.startswith("opex."):
        side, _, glabel = kind.partition(".")
        group = ("營業成本-" if side == "cost" else "營業費用-") + glabel
        for source, row, g, _label, amount in iter_expense_items(
                inputs["payments"], inputs["cash_entries"], cat_map,
                accounts, mset):
            if g != group:
                continue
            if source == "payment":
                items.append(_row("payment", row["id"], row.get("request_date"),
                                  row.get("summary"), amount,
                                  row.get("category"),
                                  row.get("payment_status") or ""))
            else:
                unmapped = classify_cash_entry(row, cat_map) == "unmapped"
                items.append(_row("cash", row["id"], row.get("entry_date"),
                                  row.get("summary"), amount,
                                  row.get("category"),
                                  "未歸類" if unmapped else ""))
        if group == "營業成本-費":
            adv = await _advance_state(session)
            for x in adv["expenses"]:
                if month_of(x.get("date")) in mset:
                    items.append(_row("expense", x["id"], x.get("date"),
                                      x.get("label"), x.get("amount"),
                                      x.get("category"), "預支核銷"))
            for r in depreciation_rows(inputs["equipment"], months):
                m = r["month"]
                items.append(_row("derived", f"dep:{m}", f"{m}-01",
                                  f"器材折舊（{m}）", r["amount"], "折舊", ""))
        if group == "營業費用-管理":
            fees = bank_fee_total(inputs["cash_entries"], mset)
            if fees:
                items.append(_row("derived", "bank_fee", None,
                                  "銀行手續費（各筆匯費合計）", fees, "匯費", ""))

    elif kind == "non_operating":
        for inv in inputs["invoices"]:
            if (inv.get("issue_status") or "") == "作廢":
                continue
            if month_of(inv.get("invoice_date")) not in mset:
                continue
            if (inv.get("category") or "") in ("內部代開", "外部代開") and \
                    int(inv.get("commission") or 0):
                items.append(_row("invoice", inv["id"], inv.get("invoice_date"),
                                  inv.get("title"), inv.get("commission"),
                                  inv.get("category"), "代開手續費"))
        for e in inputs["cash_entries"]:
            if month_of(e.get("entry_date")) not in mset:
                continue
            t = classify_cash_entry(e, cat_map)
            if t not in ("direct_income", "direct_expense", "unmapped"):
                continue
            acct = map_account(cat_map, accounts, "cash", e.get("category"))
            g = (acct or {}).get("pnl_group")
            flow = in_amount(e) - out_amount(e)
            if t == "direct_income" and g != "營業收入":
                # 業外收入科目照科目；誤映/無 pnl_group 者 build_pnl 落
                # 「未歸類收入」— 同樣屬業外區，一併列出
                items.append(_row("cash", e["id"], e.get("entry_date"),
                                  e.get("summary"), flow, e.get("category"),
                                  "業外收入" if g == "業外收入" else "未歸類收入"))
            elif t == "direct_expense" and g == "業外支出":
                items.append(_row("cash", e["id"], e.get("entry_date"),
                                  e.get("summary"), flow,
                                  e.get("category"), "業外支出"))
            elif t == "unmapped" and in_amount(e):
                # 對齊 build_pnl：未歸類收支的 deposit 側列業外「未歸類收入」
                items.append(_row("cash", e["id"], e.get("entry_date"),
                                  e.get("summary"), in_amount(e),
                                  e.get("category"), "未歸類收入"))
        # 貸款利息（權責按 due_date，derived 虛擬列 — 認列謂詞與 build_pnl 共用
        # iter_loan_interest；業外支出側金額取負，與本 kind 其他列一致）
        loan_names = {ln.get("id"): (ln.get("name") or "貸款")
                      for ln in inputs["loans"]}
        for p, amt in iter_loan_interest(inputs["loan_payments"], mset):
            items.append(_row(
                "derived", f"loanint:{p.get('id')}", p.get("due_date"),
                f"{loan_names.get(p.get('loan_id'), '貸款')} "
                f"第{p.get('period_no')}期利息", -amt, "利息費用",
                "已繳" if (p.get("status") or "") == "paid" else "未繳"))

    elif kind == "receivable":
        baseline = _resolve_baseline(inputs)
        for inv in ar_open_invoices(inputs["invoices"], baseline, months[-1]):
            items.append(_row("invoice", inv["id"], inv.get("invoice_date"),
                              inv.get("title"), inv.get("amount_total"),
                              inv.get("category"), "未收"))

    elif kind == "payable":
        baseline = _resolve_baseline(inputs)
        for p in ap_open_payments(inputs["payments"], cat_map,
                                  baseline, months[-1]):
            items.append(_row("payment", p["id"], p.get("request_date"),
                              p.get("summary"), p.get("amount"),
                              p.get("category"),
                              p.get("payment_status") or "未付款"))

    else:  # cash.<operating|investing|financing>（值域已由 VALID_DRILL_KINDS 把關）
        act = kind.split(".", 1)[1]
        for e in inputs["cash_entries"]:
            if month_of(e.get("entry_date")) not in mset:
                continue
            if not e.get("bank_account_id"):
                continue
            t = classify_cash_entry(e, cat_map)
            if t in ("advance", "transfer"):
                continue
            if cash_entry_activity(e, cat_map, accounts) == act:
                items.append(_row("cash", e["id"], e.get("entry_date"),
                                  e.get("summary"), cash_entry_flow(e),
                                  e.get("category"), t))

    items.sort(key=lambda x: (x["date"] or "", x["id"] or ""))
    total = sum(x["amount"] for x in items)
    truncated = len(items) > DRILLDOWN_LIMIT
    return {"kind": kind, "period_months": months,
            "items": items[:DRILLDOWN_LIMIT], "total": total,
            "count": len(items), "truncated": truncated}


# ═════════════════════════════════════════════════════════════════
# 階段五：儀表板 + 稅務包（reuse _load_inputs + core/finance_logic 純函式）
# ═════════════════════════════════════════════════════════════════

def _period_label(months) -> str:
    """月清單 → 顯示字串（單月直接回；多月 'first..last'）— period 缺省時 fallback。"""
    return months[0] if len(months) == 1 else f"{months[0]}..{months[-1]}"


def _day_str(dt):
    """timestamptz/date/str → 'YYYY-MM-DD'（過 local_day 修時區）；None → None。"""
    d = local_day(dt)
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else (d or None)


def _pnl_kw(inputs, adv) -> dict:
    """build_pnl 的共用關鍵字組（compute_live 與儀表板路徑同源，改一處全到位）。"""
    return dict(invoices=inputs["invoices"], payments=inputs["payments"],
                cash_entries=inputs["cash_entries"], equipment=inputs["equipment"],
                advance_expenses=adv["expenses"],
                loan_payments=inputs["loan_payments"], cat_map=inputs["cat_map"],
                accounts=inputs["accounts"])


def _bucket_by_month(rows, date_key, month_set) -> dict:
    """依 date_key 的認列月把 rows 分桶 {month: [rows]}（只收 month_set 內的月）。
    儀表板 trend 逐月 build_pnl 用：三大列各分一次，取代每月各全掃一次（O(N) 取代
    O(12×N)）。每列只屬一個認列月，故切片即完整——build_pnl 對單月切片結果不變。"""
    out = {m: [] for m in month_set}
    for r in rows:
        m = month_of(r.get(date_key))
        if m in out:
            out[m].append(r)
    return out


async def _project_client_map(session) -> dict:
    """{project_id: 客戶代稱}（crm_projects LEFT JOIN clients）— 集中度客戶解析用。"""
    from sqlalchemy import select
    from db.models import Client, CrmProject
    rows = (await session.execute(
        select(CrmProject.id, Client.short_name)
        .outerjoin(Client, Client.id == CrmProject.client_id))).all()
    return {pid: (name or "") for pid, name in rows}


async def _project_margins(session, *, top=5) -> dict:
    """專案毛利 Top/Bottom（best-effort）。批次 SQL 聚合各專案的雜支/派工實際成本，
    毛利公式走 core.crm_logic.project_margin（與 costs.py 專案財務摘要單一來源）。
    無合約金額的專案不列；任何例外 → available:false + log（不擋儀表板）。"""
    try:
        from sqlalchemy import func as safunc, select
        from db.models import CrmProject, CrmProjectExpense, CrmProjectStaff
        projects = (await session.execute(
            select(CrmProject.id, CrmProject.name, CrmProject.contract_amount,
                   CrmProject.tax_rate)
            .where(CrmProject.contract_amount.isnot(None),
                   CrmProject.contract_amount > 0))).all()
        if not projects:
            return {"available": False, "top": [], "bottom": []}
        exp_by = {pid: int(t or 0) for pid, t in (await session.execute(
            select(CrmProjectExpense.project_id,
                   safunc.sum(CrmProjectExpense.actual))
            .group_by(CrmProjectExpense.project_id))).all()}
        staff_by = {pid: int(t or 0) for pid, t in (await session.execute(
            select(CrmProjectStaff.project_id,
                   safunc.sum(safunc.coalesce(CrmProjectStaff.actual_cost,
                                              CrmProjectStaff.cost)))
            .group_by(CrmProjectStaff.project_id))).all()}
        rows = []
        for pid, name, contract, tax_rate in projects:
            m = project_margin(contract, tax_rate, exp_by.get(pid, 0),
                               staff_by.get(pid, 0))
            rows.append({
                "project_id": pid, "name": name or "?", "revenue": m["ex_tax"],
                "cost": m["cost"], "margin": m["margin"],
                "margin_pct": m["margin_pct"]})
        rows.sort(key=lambda x: x["margin"], reverse=True)
        bottom = sorted(rows, key=lambda x: x["margin"])[:top]
        return {"available": True, "top": rows[:top], "bottom": bottom}
    except Exception as e:  # noqa: BLE001 — best-effort，壞了不擋儀表板
        logger.warning("project_margins 計算失敗，回 available:false: %s", e)
        return {"available": False, "top": [], "bottom": []}


async def dashboard_summary(session, months, period: str = "") -> dict:
    """財務儀表板彙總（trend / cash / aging / concentration / project_margins / meta）。

    reuse _load_inputs + core/finance_logic 純函式為單一來源。period = 原查詢
    字串（endpoint 傳入以填 meta；缺省時 caller 已補當月）。"""
    months = sorted(set(months))
    mset = set(months)
    inputs = await _load_inputs(session)
    adv = await _advance_state(session)
    as_of = months[-1]
    baseline = _resolve_baseline(inputs) or months[0]

    # trend：含 as_of 往前 12 個月，逐月 build_pnl。三大列（發票/請款/收支）先按
    # 認列月分桶一次（避免每月各全掃歷史）；折舊跨月、貸款利息按 due_date 月 →
    # equipment/loan_payments/advance 整份帶入由 build_pnl 自行按月過濾。
    trend_months = [shift_month(as_of, d) for d in range(-11, 1)]
    tset = set(trend_months)
    inv_by = _bucket_by_month(inputs["invoices"], "invoice_date", tset)
    pay_by = _bucket_by_month(inputs["payments"], "request_date", tset)
    cash_by = _bucket_by_month(inputs["cash_entries"], "entry_date", tset)
    series = []
    for m in trend_months:
        p = build_pnl([m], invoices=inv_by[m], payments=pay_by[m],
                      cash_entries=cash_by[m], equipment=inputs["equipment"],
                      advance_expenses=adv["expenses"],
                      loan_payments=inputs["loan_payments"],
                      cat_map=inputs["cat_map"], accounts=inputs["accounts"])
        series.append({"month": m, "revenue": p["revenue"]["total"],
                       "cost": p["cost"]["total"], "opex": p["opex"]["total"],
                       "net": p["net"]["amount"]})
    trend = {"months": trend_months, "series": series}

    # cash：期末各帳戶推導餘額 + 近 3 月 net 均 → runway
    bank_lines = bank_balances_asof(inputs["bank_accounts"],
                                    inputs["cash_entries"], as_of)
    cash_total = sum(int(b.get("amount") or 0) for b in bank_lines)
    recent = series[-3:]  # 近 3 月（不足取現有月）
    avg_net = round(sum(s["net"] for s in recent) / len(recent)) if recent else 0
    cash = {"total": cash_total,
            "by_account": [{"name": b.get("name") or "?",
                            "amount": int(b.get("amount") or 0)}
                           for b in bank_lines],
            "avg_monthly_net": avg_net,
            "runway_months": runway_months(cash_total, avg_net)}

    # aging：baseline..as_of 的未收發票 → 帳齡桶
    aging = aging_buckets(
        ar_open_invoices(inputs["invoices"], baseline, as_of), as_of)

    # concentration：期間營收發票，client 名 = project_id→客戶 → 抬頭 → 未指定
    proj_client = await _project_client_map(session)
    rev_by_client: dict = {}
    for inv in iter_revenue_invoices(inputs["invoices"], mset):
        name = resolve_client_name(inv, proj_client)
        rev_by_client[name] = rev_by_client.get(name, 0) + invoice_ex_tax(inv)
    concentration = client_concentration(rev_by_client)

    return {
        "trend": trend, "cash": cash, "aging": aging,
        "concentration": concentration,
        "project_margins": await _project_margins(session),
        "meta": {"period": period or _period_label(months), "as_of": as_of,
                 "baseline_month": baseline, "warnings": []},
    }


def _tax_invoice_row(inv) -> dict:
    """發票 → 銷項/進項明細列（ex_tax/tax 走既有 invoice_ex_tax/invoice_tax）。"""
    return {"date": _day_str(inv.get("invoice_date")),
            "number": inv.get("invoice_number") or "",
            "buyer": inv.get("company_name") or "",
            "tax_id": inv.get("tax_id") or "",
            "ex_tax": invoice_ex_tax(inv), "tax": invoice_tax(inv),
            "total": int(inv.get("amount_total") or 0),
            "category": inv.get("category") or ""}


def _vat_side(rows) -> dict:
    return {"rows": rows,
            "total_ex_tax": sum(r["ex_tax"] for r in rows),
            "total_tax": sum(r["tax"] for r in rows),
            "total": sum(r["total"] for r in rows),
            "count": len(rows)}


async def tax_package(session, months, period: str = "") -> dict:
    """稅務包：銷項/進項發票明細 + 分類支出 + 勞報彙總 + 營業稅位置 + meta。

    直接吃 _load_inputs 的 crm_invoices / crm_payment_requests / cash_entries。"""
    months = sorted(set(months))
    mset = set(months)
    inputs = await _load_inputs(session)
    invoices = inputs["invoices"]
    payments = inputs["payments"]
    cash_entries = inputs["cash_entries"]
    cat_map = inputs["cat_map"]

    # 銷項/進項 — 方向判定走 invoice_vat_side（與 vat_position 摘要同源，作廢不計、
    # 第三種 payment_type 值統一歸銷項 → 明細與摘要不會對不上）
    output_rows, input_rows = [], []
    for inv in invoices:
        side = invoice_vat_side(inv)
        if side is None:
            continue
        if month_of(inv.get("invoice_date")) not in mset:
            continue
        (output_rows if side == "output" else input_rows).append(_tax_invoice_row(inv))
    output_rows.sort(key=lambda r: (r["date"] or "", r["number"] or ""))
    input_rows.sort(key=lambda r: (r["date"] or "", r["number"] or ""))

    # 分類支出：收支 direct_expense（out−in）+ 請款非預支（amount）依 category 分組
    cat_agg: dict = {}

    def _bucket(category, amount):
        c = cat_agg.setdefault(category or "未分類", {"amount": 0, "count": 0})
        c["amount"] += amount
        c["count"] += 1

    for p in payments:
        if p.get("is_advance"):
            continue
        if month_of(p.get("request_date")) not in mset:
            continue
        amt = int(p.get("amount") or 0)
        if amt:
            _bucket(p.get("category"), amt)
    for e in cash_entries:
        if month_of(e.get("entry_date")) not in mset:
            continue
        if classify_cash_entry(e, cat_map) != "direct_expense":
            continue
        amt = out_amount(e) - in_amount(e)
        if amt:
            _bucket(e.get("category"), amt)
    exp_rows = sorted(
        ({"category": k, "amount": v["amount"], "count": v["count"]}
         for k, v in cat_agg.items()), key=lambda x: -x["amount"])
    expense_by_category = {"rows": exp_rows,
                           "total": sum(r["amount"] for r in exp_rows)}

    # 勞報彙總：CrmPaymentRequest payee_type=='勞報'，依 (payee_name, payee_id) 分組
    labor_agg: dict = {}
    for p in payments:
        if (p.get("payee_type") or "") != "勞報":
            continue
        if month_of(p.get("request_date")) not in mset:
            continue
        key = (p.get("payee_name") or "", p.get("payee_id") or "")
        row = labor_agg.setdefault(
            key, {"count": 0, "total": 0, "invoice_total": 0})
        row["count"] += 1
        row["total"] += int(p.get("amount") or 0)
        row["invoice_total"] += int(p.get("invoice_amount") or 0)
    labor_rows = sorted(
        ({"payee_name": name, "payee_id": pid, "count": v["count"],
          "total": v["total"], "invoice_total": v["invoice_total"]}
         for (name, pid), v in labor_agg.items()), key=lambda x: -x["total"])
    labor_fees = {"rows": labor_rows,
                  "total": sum(r["total"] for r in labor_rows),
                  "count": sum(r["count"] for r in labor_rows)}

    v = vat_position(invoices, cash_entries, cat_map, months)
    vat = {"output_tax": v["output"], "input_tax": v["input"],
           "paid": v["paid"], "net": v["net"]}

    return {
        "output_vat": _vat_side(output_rows),
        "input_vat": _vat_side(input_rows),
        "expense_by_category": expense_by_category,
        "labor_fees": labor_fees, "vat": vat,
        "meta": {"period": period or _period_label(months), "months": months,
                 "warnings": []},
    }
