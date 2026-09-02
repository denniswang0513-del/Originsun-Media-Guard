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

兩本帳（entity，2026-08-19 v2，見 docs/LEDGER_ENTITY_PLAN.md §4）：
- 對外函式全部帶 `entity: str = "parent"`（'parent'＝母公司 / 'mine'＝我的帳），
  預設 parent → 既有資料全屬母公司帳、既有呼叫端數字不變。entity 過濾只發生在
  本模組的取數層（_load_inputs / _advance_state / 快照查詢），
  core/finance_logic.py 純函式不知 entity 存在。
- 錢流六源（invoices/payments/cash_entries/adjustments/bank_accounts/loans）
  以 entity WHERE 過濾；loan_payments 經 loans 推導；科目表/科目對映兩本共用
  （plan §1.2）。**equipment 2026-08-24 起帶 entity（§8 階段 4）—— 兩本帳各餵
  各的折舊/淨值**；CRM 營運域（專案毛利/預支核銷）仍屬母公司 →
  entity!='parent'（我的帳）時不餵、對應回傳鍵給空值（形狀不變）。
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
    split_bank_lines,
    agency_fee_total,
    bank_fee_total,
    build_balance_sheet,
    build_cashflow,
    build_pnl,
    cashflow_lines,
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
    passthrough_fee_income,
    loan_outstanding_rows,
    local_day,
    map_account,
    paired_expense_category,
    restate_revenue_accrual,
    apply_ledger_project_costs,
    merge_cf,
    merge_pnl,
    month_of,
    month_range,
    out_amount,
    recognize_receipt_fee,
    resolve_client_name,
    runway_months,
    shift_month,
    statement_interpretation,
    statement_warnings,
    VALID_DRILL_KINDS,  # re-export：endpoint 與單元測試由本模組取用
    vat_position,
    equity_transfer_position,
    card_outstanding,)
from core.crm_logic import project_margin

logger = logging.getLogger(__name__)

DRILLDOWN_LIMIT = 500


# ── 輸入載入 ─────────────────────────────────────────────────

def _dump(rows, *cols) -> list:
    """ORM 列 → 純函式吃的 dict 投影（key == 欄位名）。"""
    return [{c: getattr(r, c) for c in cols} for r in rows]


def explode_cash_splits(cash_entries: list, splits_by_entry: dict) -> list:
    """拆項展開的算式正本（純函式，直接可測）。

    有拆項的父列 → N 個虛擬列（id=拆項 id、金額=拆項額、分類/專案=拆項的），
    其餘欄位（日期/帳戶/摘要/status）繼承父列。規則細節：

    🔴 金額差額不吞：Σ(拆項) ≠ 父列金額時（寫入端理應擋掉，但資料可能繞路進來
      —— 直接改 DB、舊版匯入），差額補一個 category=None 的殘項 → 落進三表的
      「未歸類」桶誠實外顯，statement_warnings 會數到它。靜默吞掉的話報表
      憑空少一塊錢，而且不會有人知道。
    🔴 bank_fee／claim 留在誰身上：掛在**第一個**虛擬列。匯費是整筆匯款收一次
      的真實費用（bank_fee_total 逐列加總，放兩列就重複計）。
    🔴 拆項自帶 fee（發票代開費）＝收款側匯費的數學（recognize_receipt_fee）：
      該虛擬列 deposit 補成毛額（amount+fee）、fee 掛上自己的 bank_fee ——
      營收進毛額、代開費落手續費費用桶、淨流（deposit − bank_fee）不變，
      對帳與銀行餘額鏈都不動。第一列的 bank_fee ＝ 父列的＋自己的。
    🔴 invoice_id／advance_payment_id／payment_request_id 不繼承：那些連結
      屬於父列整筆（分配表另有正本），複製 N 份會讓對應的沖銷邏輯算 N 次。
    🔴 虛擬列帶 `parent_id`＝真正的收支列 PK —— 展開後 `id` 是拆項 id，
      **不是**資料庫裡查得到的列。任何拿 inputs 的 id 回頭寫 DB 的消費端
      （如 recognize_transfer_fees_in）要用 parent_id 判斷／跳過。
    🔴 方向判定走 `core.crm_logic.split_side`（唯一那份）：兩側都有值的列
      判不出分母 → **整列原樣通過、不展開**（寫入端本來就擋，這裡防的是
      繞路進來的資料）—— 自己 if/else 判的話那種列的另一側會被 None 掉，
      錢從報表上直接消失。
    """
    from core.crm_logic import split_side
    if not splits_by_entry:
        return cash_entries
    out = []
    for e in cash_entries:
        subs = splits_by_entry.get(e.get("id"))
        if not subs:
            out.append(e)
            continue
        side = split_side(e.get("deposit"), e.get("expense"))
        if not side:
            out.append(e)
            continue
        parent_amount = int(e.get(side) or 0)
        remain = parent_amount - sum(s["amount"] for s in subs)
        pieces = list(subs)
        if remain:
            pieces = pieces + [{"id": f"{e.get('id')}#rest", "amount": remain,
                                "category": None, "note": "拆項差額", "project_id": None}]
        for i, s in enumerate(pieces):
            v = dict(e)
            v["id"] = s["id"]
            v["parent_id"] = e.get("id")
            v[side] = s["amount"]
            v["deposit" if side == "expense" else "expense"] = None
            v["category"] = s["category"]
            v["note"] = s.get("note") or e.get("note")
            v["project_id"] = s.get("project_id")
            if i > 0:                       # 費用類欄位只留第一列（見 docstring）
                v["bank_fee"] = None
                v["claim"] = None
            # 代開費：毛額進營收、fee 疊上本列 bank_fee（淨流不變，見 docstring）。
            # 🔴 補毛額的算式走 core 的正本（它擁有「淨流入不變」這條不變量與
            # fee<0 的守衛）—— 這裡只負責把 fee 疊到父列既有的匯費上（父列那筆
            # 掛在第 0 列，取代會把它吃掉）。
            fee = int(s.get("fee") or 0) if side == "deposit" else 0
            if fee:
                v["deposit"], _f = recognize_receipt_fee(s["amount"], 0, fee)
                v["bank_fee"] = int(v.get("bank_fee") or 0) + _f
                # 🔴 同時記在 `agency_fee`：金額與淨流完全不變（那條靠 bank_fee），
                # 這一欄只給**損益的名字**用 —— 代開費疊進匯費桶之後，那一行就叫
                # 「銀行手續費」，而它可能一毛真的匯費都沒有（owner 2026-09-02
                # 實際看到的：整整 66,960 全是代開費，同期真匯費 0 筆）。
                v["agency_fee"] = int(v.get("agency_fee") or 0) + _f
            for k in ("invoice_id", "advance_payment_id", "payment_request_id"):
                v[k] = None
            out.append(v)
    return out


async def _load_inputs(session, entity: str = "parent") -> dict:
    """全表載入 → 純函式吃的 dict/list（欄位子集，含 drilldown 需要的識別欄）。

    entity：錢流六源以 entity WHERE 過濾；loan_payments 經該 entity 的 loans；
    equipment 是母公司域（plan §1.2）→ entity!='parent' 回空；科目表兩本共用。
    對映表只有 `source='cash'` 那批按帳本分家（2026-08-30），payment／invoice 共用。
    """
    from sqlalchemy import or_, select
    from db.models import (BankAccount, CrmCashEntry, CrmInvoice,
                           CrmPaymentRequest, Equipment, FinanceAccount,
                           FinanceAdjustment, FinanceCategoryMap, FinanceLoan,
                           FinanceLoanPayment)

    async def _all(model, order_by=None, where=None):
        q = select(model)
        if where is not None:
            q = q.where(where)
        if order_by is not None:
            q = q.order_by(*order_by)
        return (await session.execute(q)).scalars().all()

    # ── 拆項展開（帳目一筆、內容拆裂；owner 2026-08-31 規劃 v3）──
    # 🔴 鐵則：展開只發生在這個載入層，引擎純函式不知道拆項存在。
    # 有拆項的父列被替換成 N 個虛擬列（各帶自己的分類/專案/金額，繼承父列的
    # 日期/帳戶/摘要）—— build_pnl/bs/cf/drilldown 一行不用改。
    from db.models import CrmCashSplit
    _split_rows = (await session.execute(
        select(CrmCashSplit)
        .join(CrmCashEntry, CrmCashEntry.id == CrmCashSplit.entry_id)
        .where(CrmCashEntry.entity == entity)
        .order_by(CrmCashSplit.sort))).scalars().all()
    splits_by_entry: dict = {}
    for _s in _split_rows:
        splits_by_entry.setdefault(_s.entry_id, []).append(
            {"id": _s.id, "amount": int(_s.amount or 0),
             "fee": int(_s.fee or 0),
             "category": _s.category or None, "note": _s.note or "",
             "project_id": _s.project_id or None})

    invoices = _dump(await _all(CrmInvoice, where=CrmInvoice.entity == entity),
                     "id", "payment_type", "issue_status", "payment_status",
                     "category", "amount_total", "amount_ex_tax", "tax_amount",
                     "commission", "invoice_date", "paid_date", "title",
                     "invoice_number", "company_name", "tax_id", "project_id")
    payments = _dump(await _all(CrmPaymentRequest,
                                where=CrmPaymentRequest.entity == entity),
                     "id", "is_advance", "request_date", "amount", "category",
                     "summary", "payee_name", "payment_status", "payment_date",
                     "payee_id", "payee_type", "invoice_amount")
    cash_entries = _dump(await _all(CrmCashEntry,
                                    where=CrmCashEntry.entity == entity),
                         "id", "entry_date", "deposit", "expense", "bank_fee",
                         # status：卡債負債列靠它認刷卡列（status='card'）——
                         # 引擎原本看不到這個欄，卡片帳對 BS 是隱形的
                         "status",
                         "claim", "category", "summary", "invoice_id",
                         # 三表鑽取的明細要看得懂：摘要常常只有交易類型
                         # （「網路自轉」），用途寫在銀行資訊／附註
                         # （owner 2026-08-29「明細多一點內容」）
                         "bank_memo", "note",
                         "advance_payment_id", "payment_request_id",
                         "bank_account_id")
    cash_entries = explode_cash_splits(cash_entries, splits_by_entry)
    # 器材 2026-08-24 起帶 entity（§8 階段 4）：兩本帳各餵各的折舊/淨值。
    # （plan §1.2 的「我的帳不餵器材」自此作廢 —— owner 私帳有 123 項器材。）
    equipment = _dump(await _all(Equipment, where=Equipment.entity == entity),
                      "id", "name", "purchase_cost", "purchase_date",
                      "depreciation_months", "retired_date", "status")
    adjustments = _dump(await _all(FinanceAdjustment,
                                   where=FinanceAdjustment.entity == entity),
                        "id", "adj_date", "amount", "adj_type", "description",
                        "account_id")
    bank_accounts = _dump(await _all(BankAccount,
                                     order_by=(BankAccount.sort_order,
                                               BankAccount.created_at),
                                     where=BankAccount.entity == entity),
                          "id", "name", "opening_balance", "opening_date",
                          "acct_kind", "active")
    for b in bank_accounts:
        b["active"] = bool(b["active"])
    loans = _dump(await _all(FinanceLoan, where=FinanceLoan.entity == entity),
                  "id", "name", "principal", "opening_balance", "start_date",
                  "bank_account_id")
    loan_ids = [ln["id"] for ln in loans]
    loan_payments = _dump(
        (await _all(FinanceLoanPayment,
                    where=FinanceLoanPayment.loan_id.in_(loan_ids))
         if loan_ids else []),
        "id", "loan_id", "period_no", "due_date",
        "principal_due", "interest_due", "status", "paid_at")

    # 私帳的應收正本＝執行專案（owner 不開發票 → invoice AR 恆 0）；
    # 權責/現金橋接警語也吃這份。母公司不載（照舊走發票）。
    projects = []
    if entity == "mine":
        from db.models import CrmProject
        projects = _dump(await _all(CrmProject, where=CrmProject.entity == "mine"),
                         "id", "name", "completion_date", "contract_amount",
                         "amount_received", "amount_receivable",
                         # 逐案的委外/稅款/雜支（owner 年度表的成本與稅就是這三欄加總）
                         "ledger_detail")

    accounts = {r.id: {"code": r.code, "name": r.name, "pnl_group": r.pnl_group,
                       "cf_activity": r.cf_activity, "acct_type": r.acct_type}
                for r in (await session.execute(select(FinanceAccount))).scalars().all()}

    # 🔴 `cash` 的對映按帳本分家（母公司的平面科目 vs 私帳分類樹的複合鍵，
    # 見 db.models.FinanceCategoryMap）—— 不過濾的話，兩本帳哪天有同名類別，
    # 這個 (source, category_text) 的字典就會有一邊蓋掉另一邊，而它決定的是
    # 科目與 treatment：那批帳會被算進錯的科目裡。
    # `payment`／`invoice` 兩本帳共用，照舊全收。
    cat_map = {(r.source, r.category_text): {"treatment": r.treatment,
                                             "account_id": r.account_id}
               for r in (await session.execute(
                   select(FinanceCategoryMap).where(
                       FinanceCategoryMap.active.is_(True),
                       or_(FinanceCategoryMap.source != "cash",
                           FinanceCategoryMap.entity == entity)))).scalars().all()}

    return {"invoices": invoices, "payments": payments,
            "cash_entries": cash_entries, "equipment": equipment,
            "adjustments": adjustments, "bank_accounts": bank_accounts,
            "loans": loans, "loan_payments": loan_payments,
            "projects": projects,
            "accounts": accounts, "cat_map": cat_map,
            # _resolve_baseline 要分帳本（settings 的基準月只屬於母公司）
            "entity": entity}


async def _advance_state(session, entity: str = "parent") -> dict:
    """未結清預支餘額合計 + 預支核銷支出列（餵損益 營業成本-費）。

    三來源一次 GROUP BY（不逐預支 N+1），逐預支套 core.crm_logic
    compute_advance_status（與 /payments/advances 端點同一套判定）。

    entity：預支/核銷是母公司域（CrmProjectExpense 無 entity 欄，plan §1.2）
    → entity!='parent'（我的帳）直接回空結構；parent 時 payments/cash_entries
    仍帶 entity 過濾。
    """
    if entity != "parent":
        return {"balance_total": 0, "expenses": []}
    from sqlalchemy import select, func as safunc
    from core.crm_logic import compute_advance_status
    from core.ledger import not_mine_project
    from db.models import CrmCashEntry, CrmPaymentRequest, CrmProjectExpense

    advances = (await session.execute(
        select(CrmPaymentRequest.id, CrmPaymentRequest.amount)
        .where(CrmPaymentRequest.is_advance == 1,
               CrmPaymentRequest.entity == entity))).all()
    # 🔴 私帳專案的雜支不算母公司成本（owner 2026-08-28）—— 這幾列會經由
    # build_pnl 的 advance_expenses 進母公司損益表的「營業成本-費」。專案搬帳本
    # 時這張表沒跟著搬（它沒有 entity 欄），所以要在取數層擋掉。
    exp_by_adv = {row[0]: int(row[1] or 0) for row in (await session.execute(
        select(CrmProjectExpense.advance_id, safunc.sum(CrmProjectExpense.actual))
        .where(CrmProjectExpense.advance_id.isnot(None),
               CrmProjectExpense.advance_id != "",
               not_mine_project(CrmProjectExpense))
        .group_by(CrmProjectExpense.advance_id))).all()}
    pay_by_adv = {row[0]: int(row[1] or 0) for row in (await session.execute(
        select(CrmCashEntry.advance_payment_id, safunc.sum(CrmCashEntry.expense))
        .where(CrmCashEntry.advance_payment_id.isnot(None),
               CrmCashEntry.advance_payment_id != "",
               CrmCashEntry.expense > 0,
               CrmCashEntry.entity == entity)
        .group_by(CrmCashEntry.advance_payment_id))).all()}
    ret_by_adv = {row[0]: int(row[1] or 0) for row in (await session.execute(
        select(CrmCashEntry.advance_payment_id, safunc.sum(CrmCashEntry.deposit))
        .where(CrmCashEntry.advance_payment_id.isnot(None),
               CrmCashEntry.advance_payment_id != "",
               CrmCashEntry.deposit > 0,
               CrmCashEntry.entity == entity)
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
            CrmProjectExpense.advance_id != "",
            not_mine_project(CrmProjectExpense)))).scalars().all()
    expenses = [{"id": x.id, "date": x.created_at, "amount": int(x.actual or 0),
                 "label": (x.sub_item or x.category or "預支核銷"),
                 "category": x.category or ""}
                for x in exp_rows if (x.actual or 0)]
    return {"balance_total": balance_total, "expenses": expenses}


def _resolve_baseline(inputs: dict):
    """基準月：settings finance.baseline_month（🔴 只有母公司）→ 資料最早月 → None。

    finance.baseline_month 是**母公司設定精靈**寫的全域單值（那支精靈只服務
    母公司帳本）。2026-08-25 之前這裡不分帳本直接套 —— 私帳的三表被母公司的
    2025-07 攔腰切掉（owner 的私帳資料從 2021/3 起，之前的應收應付全被基準月
    排除）。owner：「我的私帳的對帳起始點先不用設」→ mine 一律走資料最早月；
    哪天真要給私帳設起點，再開 per-entity 的鍵，別把這個全域值借去用。
    """
    if (inputs.get("entity") or "parent") == "parent":
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

async def compute_live(session, months, inputs=None, adv=None,
                       entity: str = "parent") -> dict:
    """指定月集合的 live 三表（不看快照）。BS as_of = 期末月，累積段 =
    baseline..as_of（baseline 缺值時退資料最早月，再退期首月）。
    inputs/adv 若由 caller 預載，須是同一 entity 的載入結果。"""
    months = sorted(set(months))
    if inputs is None:
        inputs = await _load_inputs(session, entity)
    if adv is None:
        adv = await _advance_state(session, entity)
    as_of = months[-1]
    baseline = _resolve_baseline(inputs) or months[0]
    # 🔴 基準月異常早（超出累計上限）→ clamp ＋ 警語，不准整張三表 500。
    # 沒有 settings 基準月的帳本（私帳）baseline＝資料最早月 —— 一筆打錯年份
    # 的日期（實例：Sheet 上兩筆 2024/3/14 打成 2014/3/14）就會把累計區間撐到
    # 148 個月、month_range 直接 raise、整個財務三表打不開。打錯字要讓人看見，
    # 但看見的方式是警語，不是白畫面。
    from core.finance_logic import _MAX_PERIOD_MONTHS, shift_month
    _floor = shift_month(as_of, -(_MAX_PERIOD_MONTHS - 1))
    _baseline_clamped = None
    if min(baseline, as_of) < _floor:
        _baseline_clamped = baseline
        baseline = _floor
    kw = _pnl_kw(inputs, adv)
    pnl = build_pnl(months, **kw)
    cum_months = month_range(min(baseline, as_of), as_of)
    cum_pnl = pnl if cum_months == months else build_pnl(cum_months, **kw)
    warn = statement_warnings(inputs["cash_entries"], inputs["payments"],
                              inputs["cat_map"], months,
                              inputs["loan_payments"])
    if _baseline_clamped:
        warn["messages"].insert(0,
            f"資料最早月 {_baseline_clamped} 超出累計上限（{_MAX_PERIOD_MONTHS} 個月），"
            f"期初累計自 {baseline} 起算 —— 更早的列不在期初裡。"
            "通常是打錯年份的日期，請到收支明細搜出那幾筆修正。")
    # 🔴 股東往來帳戶不是現金 —— 拆出來分別進負債（借款）與權益（投資款）。
    # 混在 bank_lines 裡的話，現金會憑空多出股東墊付的錢（那些錢從來沒進過
    # 公司的銀行帳戶），資產負債表與現金流量表全部失真。
    _split = split_bank_lines(
        inputs["bank_accounts"],
        bank_balances_asof(inputs["bank_accounts"], inputs["cash_entries"], as_of))
    bank_lines = _split["cash"]
    ar_rows = ar_open_invoices(inputs["invoices"], baseline, as_of)
    # 私帳：應收正本＝執行專案（發票 AR 恆 0）＋ 權責/現金橋接警語
    #（owner 2026-08-26「兩張表對不起來」：他的年度表按結案日認列營收＝權責，
    # 本表損益按入帳＝現金 —— 差額就是應收變動，指名講出來別讓人對著猜）
    _mp = None
    if (inputs.get("entity") or entity) == "mine":
        from core.finance_logic import mine_project_positions
        _mp = mine_project_positions(inputs.get("projects") or [], months, as_of)
    ap_rows = ap_open_payments(inputs["payments"], inputs["cat_map"],
                               baseline, as_of)
    vat_cum = vat_position(inputs["invoices"], inputs["cash_entries"],
                           inputs["cat_map"], cum_months)
    # transfer 流量的位置（業主往來/代墊）＋卡債 —— BS 的另一半（詳見
    # core/finance_logic.equity_transfer_position 檔頭；對母公司是空操作）
    _pos = equity_transfer_position(inputs["cash_entries"], inputs["cat_map"],
                                    inputs["accounts"], as_of, cap_floor=baseline)
    from core.card_statement import card_cfg as _card_cfg
    from config import load_settings as _ls
    _card = card_outstanding(inputs["cash_entries"],
                             _card_cfg(_ls(), inputs.get("entity") or entity), as_of)
    # 器材資本化差額外顯：對到「器材設備」的 transfer 流出理應等於清冊在同一
    # 窗口的購置成本（資本化＝現金變資產）。流出 > 清冊＝清冊外的小額配件其實
    # 是費用（該改類別或補清冊）；< 清冊＝有購置沒走收支。這個差就是 BS diff
    # 的具名成分之一 —— 指名數字，別讓人對著一個總差額猜。
    _cap_flow = _pos["cap_flow"]
    _cap_reg = sum(int(q.get("purchase_cost") or 0) for q in inputs["equipment"]
                   if baseline < (month_of(q.get("purchase_date")) or "") <= as_of)
    if _cap_flow != _cap_reg and (_cap_flow or _cap_reg):
        warn["messages"].append(
            f"器材資本化差額 {_cap_flow - _cap_reg:+,}：收支的器材流出 {_cap_flow:,} vs "
            f"清冊在期購置 {_cap_reg:,} —— 流出較多＝清冊外的小額配件其實是費用"
            "（把那些列改類別，或補進器材清冊）")
    if _mp is not None:
        # 私帳的損益表＝**權責**（owner 的年度表就是這個口徑：本期結案案的合約
        # 合計，2026-08-27 三年逐一核對，5,928,950／6,955,899／8,103,670 全中）。
        # 現金認列數留在 by_collection.cash 與警語裡，兩個口徑都看得到、可互推。
        _cash_rev = int(pnl["revenue"]["total"] or 0)
        _acc = _mp["accrual_revenue"]
        pnl = restate_revenue_accrual(pnl, _acc, cash_revenue=_cash_rev,
                                      n_months=len(months))
        # 委外/雜支/稅改用逐案（權責），並拿掉收支那一面避免重複計
        pnl = apply_ledger_project_costs(
            pnl, outsource=_mp["outsource"], tax=_mp["tax"], misc=_mp["misc"])
        warn["messages"].append(
            f"損益表為權責基礎（本期結案案合約合計 {_acc:,}）；同期現金入帳為 "
            f"{_cash_rev:,}，差 {_acc - _cash_rev:+,} ＝ 應收/跨期收款的變動。")
        # 🔴 累積損益要跟 BS 的應收**同一個口徑**：那條應收線是累計權責
        # （結案 ≤ as_of 的未收），cum_pnl 卻是現金 —— 應收長出來的錢在權益那側
        # 沒有對應，資產負債表就差那麼多。
        #
        # 加的是**應收本身**，不是把 cum_pnl 整份 restate 成權責：
        # 2026-08-29 試過整份 restate（連 apply_ledger_project_costs 一起），
        # 同一份資料前後對照好壞參半 —— 2026 兩期各降到 723,003／747,079，
        # 但 2025-12 從 90,265 惡化到 −1,224,185。那支 restate 是為**期間**設計的
        # （對齊 owner 年度表），套在「基準月以來的累計」窗口上會連成本也一起
        # 換掉，多移了一百多萬。
        #
        # 這條式子成立的前提，2026-08-29 實測生產成立：**基準月(2023-06)之前
        # 結案而仍未收的案＝0 筆**（43 筆未收全部在 2025 之後結案）。哪天有了
        # 那種案，它的營收落在基準之前、應收卻掛在表上，就要另外進期初調整。
        _cum_recv = _mp["receivable"]
    bs = build_balance_sheet(
        as_of, bank_lines=bank_lines,
        receivable_total=(_mp["receivable"] if _mp is not None
                          else sum(int(i.get("amount_total") or 0) for i in ar_rows)),
        advance_balance=adv["balance_total"] + _pos["advance_net"],
        equipment=inputs["equipment"], adjustments=inputs["adjustments"],
        payable_total=sum(int(p.get("amount") or 0) for p in ap_rows),
        vat_payable=vat_cum["net"],
        loan_rows=loan_outstanding_rows(inputs["loans"],
                                        inputs["loan_payments"], as_of),
        cumulative_net=cum_pnl["net"]["amount"] + (_cum_recv if _mp is not None else 0),
        note_counts=warn,
        shareholder_loan_lines=_split["shareholder_loan"],
        shareholder_capital_lines=_split["shareholder_capital"],
        owner_flow_net=_pos["owner_net"], card_outstanding=_card,
        household_net=_pos["household_net"],
        financial_net=_pos["financial_net"])
    opening_lines = split_bank_lines(inputs["bank_accounts"], bank_balances_asof(
        inputs["bank_accounts"], inputs["cash_entries"],
        shift_month(months[0], -1)))["cash"]
    # 🔴 帳戶清單整包傳進去 —— 期初/期末只算現金類帳戶，迭代那邊也必須一致。
    #    本來這裡自己再做一次 `not is_shareholder_kind(...)`，那是同一條規則的
    #    第二份；現在「什麼算現金」只由 build_cashflow／split_bank_lines 定義。
    cf = build_cashflow(months, opening=_cf_side(opening_lines),
                        closing=_cf_side(bank_lines),
                        cash_entries=inputs["cash_entries"],
                        cat_map=inputs["cat_map"], accounts=inputs["accounts"],
                        bank_accounts=inputs["bank_accounts"])
    return {"pnl": pnl, "bs": bs, "cf": cf, "baseline_month": baseline,
            "warnings": warn}


# ── /statements：快照 v2 合併 ────────────────────────────────

async def statements_for_period(session, months, entity: str = "parent") -> dict:
    """期間三表：已鎖月（未重開、快照 v:2）優先讀快照 — PnL/CF 逐月可加總
    （merge_pnl/merge_cf）；BS 取期末月快照、期末月未鎖則 live。
    v1（無 pnl 鍵）快照視同未鎖 → 該月 live 算（讀取端相容）。
    快照按 (entity, month) 讀 — 兩本帳各自鎖月（plan §1.1）。"""
    from sqlalchemy import select
    from db.models import FinanceMonthClose

    months = sorted(set(months))
    inputs = await _load_inputs(session, entity)

    rows = (await session.execute(
        select(FinanceMonthClose).where(
            FinanceMonthClose.month.in_(months),
            FinanceMonthClose.entity == entity,
            FinanceMonthClose.reopened_at.is_(None)))).scalars().all()
    snaps = {r.month: r.snapshot for r in rows
             if isinstance(r.snapshot, dict) and r.snapshot.get("v") == 2
             and "pnl" in r.snapshot}
    live_months = [m for m in months if m not in snaps]
    # 預支狀態只有 live 計算需要 → 全期間皆快照時不掃三張表
    live = (await compute_live(session, live_months, inputs, entity=entity)
            if live_months else None)

    pnl_parts = [snaps[m]["pnl"] for m in months if m in snaps]
    cf_parts = [snaps[m]["cf"] for m in months if m in snaps]
    if live:
        pnl_parts.append(live["pnl"])
        cf_parts.append(live["cf"])
    pnl = merge_pnl(pnl_parts, len(months))
    # CF 期初/期末餘額永遠 live 重算（推導餘額不受鎖月影響）
    def _cash_only(m):
        return split_bank_lines(inputs["bank_accounts"], bank_balances_asof(
            inputs["bank_accounts"], inputs["cash_entries"], m))["cash"]
    opening = _cf_side(_cash_only(shift_month(months[0], -1)))
    closing = _cf_side(_cash_only(months[-1]))
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
                                    inputs["cat_map"], months,
                                    inputs["loan_payments"]))
    interpretation = statement_interpretation(
        pnl, bs, cf, ar_over_60=ar_overdue_amount(inputs["invoices"], baseline))
    return {
        "pnl": pnl, "bs": bs, "cf": cf, "interpretation": interpretation,
        "meta": {"locked_months": sorted(snaps), "live_months": live_months,
                 "baseline_month": baseline, "period_months": months,
                 "warnings": warn["messages"]},
    }


# ── 月結快照 v2 ──────────────────────────────────────────────

async def month_close_extras(session, month: str, entity: str = "parent") -> tuple:
    """close_month 快照 v2 的三表部分 + 鎖帳前檢核警示。

    回 (extras, warnings)：extras = {pnl, bs, cf, checks}（api_cashflow 併上
    v:2 與 cash v1 四欄後入庫）；warnings = 未歸類/未掛帳戶/未填日期 +
    該月各銀行帳戶對帳缺漏（不擋鎖帳，誠實回報）。
    entity：取數走該 entity；對帳缺漏只掃該 entity 的銀行帳戶。"""
    inputs = await _load_inputs(session, entity)
    adv = await _advance_state(session, entity)
    live = await compute_live(session, [month], inputs, adv, entity=entity)
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

def _row(source, rid, date, label, amount, category="", status="",
         account="", note="") -> dict:
    """鑽取明細的一列。

    `account`／`note` 是給收支列的（帳戶名、銀行資訊）—— owner 2026-08-29
    「明細多一點內容 現在內容太少看不懂」：摘要欄常常只有「網路自轉」，
    真正說明用途的是銀行資訊那欄（「生活開支南京東路分行」「定期定額買台股」）。
    """
    # timestamptz 回讀是 UTC 表示 → 轉回本地時區再取日（與 month_of 同理，
    # 否則月初列的顯示日期會少一天）
    if isinstance(date, datetime) and date.tzinfo is not None:
        date = date.astimezone()
    return {"source": source, "id": rid,
            "date": (date.strftime("%Y-%m-%d") if hasattr(date, "strftime")
                     else (date or None)),
            "label": label or "", "amount": int(amount or 0),
            "category": category or "", "status": status or "",
            "account": account or "", "note": note or ""}


async def drilldown(session, kind: str, months, entity: str = "parent") -> dict:
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
    inputs = await _load_inputs(session, entity)
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
                # 有「支出版」對映的類別，支出側是成本不是退款（見
                # core.finance_logic.paired_expense_category）—— 這裡只列收入側，
                # 支出側會出現在 cost./opex. 的 drilldown
                amount = (in_amount(e) if paired_expense_category(e.get("category"), cat_map)
                          else in_amount(e) - out_amount(e))
                if amount:
                    items.append(_row("cash", e["id"], e.get("entry_date"),
                                      e.get("summary"), amount,
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
            adv = await _advance_state(session, entity)
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
            # 代開費從匯費桶裡拆出來 —— 兩行相加＝原本那一行（見 build_pnl 那段）
            agency = agency_fee_total(inputs["cash_entries"], mset)
            fees = bank_fee_total(inputs["cash_entries"], mset) - agency
            if fees:
                items.append(_row("derived", "bank_fee", None,
                                  "銀行手續費（各筆匯費合計）", fees, "匯費", ""))
            if agency:
                items.append(_row("derived", "agency_fee", None,
                                  "發票代開費（收款時被扣，各筆合計）", agency,
                                  "發票代開費", ""))

    elif kind == "non_operating":
        for inv in inputs["invoices"]:
            if (inv.get("issue_status") or "") == "作廢":
                continue
            if month_of(inv.get("invoice_date")) not in mset:
                continue
            # 🔴 金額要跟 build_pnl 用**同一支**函式算 —— 這裡放 commission、
            # 表頭放淨手續費的話，明細加起來永遠對不上表頭。
            fee = passthrough_fee_income(inv)
            if fee:
                items.append(_row("invoice", inv["id"], inv.get("invoice_date"),
                                  inv.get("title"), fee,
                                  inv.get("category"), "代開手續費（已扣銷項稅）"))
        for e in inputs["cash_entries"]:
            if month_of(e.get("entry_date")) not in mset:
                continue
            t = classify_cash_entry(e, cat_map)
            if t not in ("direct_income", "direct_expense", "unmapped"):
                continue
            acct = map_account(cat_map, accounts, "cash", e.get("category"))
            g = (acct or {}).get("pnl_group")
            flow = in_amount(e) - out_amount(e)
            if (t == "direct_income" and g != "營業收入"
                    and not (out_amount(e) and paired_expense_category(
                        e.get("category"), cat_map))):
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
        if (inputs.get("entity") or "parent") == "mine":
            # 私帳應收＝執行專案（與 BS 線同一份 mine_project_positions）
            from core.finance_logic import mine_project_positions
            _mp = mine_project_positions(inputs.get("projects") or [],
                                         months, months[-1])
            for r in _mp["receivable_rows"]:
                items.append(_row("project", r["id"], r["close_month"],
                                  r["name"], r["amount"], "執行專案", "未收"))
        else:
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
        # 🔴 「哪幾列算數、各算多少」不在這裡判 —— 走 build_cashflow 用的同一支
        #    迭代器。各自判過兩次，各自漏過一次（非現金帳戶差 777,000、
        #    轉存手續費差 150）。現在鑽取合計恆等於表上那格。
        rows, _stats = cashflow_lines(inputs["cash_entries"], months,
                                      cat_map=cat_map, accounts=accounts,
                                      bank_accounts=inputs["bank_accounts"])
        acct_names = {b.get("id"): b.get("name") or ""
                      for b in (inputs["bank_accounts"] or [])}
        for r in rows:
            if r["activity"] != act:
                continue
            e = r["entry"]
            label = (e.get("summary") or "")
            if r["is_fee"]:
                label += "（跨行手續費）"   # 本金是內部移動，這一列只有手續費
            items.append(_row("cash", e["id"], e.get("entry_date"),
                              label, r["amount"], e.get("category"),
                              r["treatment"],
                              account=acct_names.get(e.get("bank_account_id"), ""),
                              # 銀行資訊優先、沒有才退回附註 —— 摘要是交易類型
                              # （「網路自轉」），用途寫在這兩欄
                              note=(e.get("bank_memo") or e.get("note") or "")))

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
        from core.ledger import not_mine
        from db.models import CrmProject, CrmProjectExpense, CrmProjectStaff
        projects = (await session.execute(
            select(CrmProject.id, CrmProject.name, CrmProject.contract_amount,
                   CrmProject.tax_rate)
            # 兩本帳 §8：專案毛利是母公司報表的元件 —— mine 專案的合約額
            # 混進來會讓母公司儀表板憑空多出 owner 私帳的營收（述詞正本
            # core/ledger.not_mine）
            .where(CrmProject.contract_amount.isnot(None),
                   CrmProject.contract_amount > 0,
                   not_mine(CrmProject.entity)))).all()
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


async def dashboard_summary(session, months, period: str = "",
                            entity: str = "parent") -> dict:
    """財務儀表板彙總（trend / cash / aging / concentration / project_margins / meta）。

    reuse _load_inputs + core/finance_logic 純函式為單一來源。period = 原查詢
    字串（endpoint 傳入以填 meta；缺省時 caller 已補當月）。
    entity!='parent'（我的帳）時專案毛利/專案客戶對映（母公司 CRM 域）跳過 →
    project_margins 回 available:false 空結構、集中度客戶名退發票抬頭（形狀不變）。"""
    months = sorted(set(months))
    mset = set(months)
    inputs = await _load_inputs(session, entity)
    adv = await _advance_state(session, entity)
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
    # 🔴 股東往來不算現金 —— 算進去 runway 會虛長（那些錢不在公司帳上）
    bank_lines = split_bank_lines(inputs["bank_accounts"], bank_balances_asof(
        inputs["bank_accounts"], inputs["cash_entries"], as_of))["cash"]
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
    # （crm_projects/clients 是母公司域 → 我的帳不查對映，客戶名退發票抬頭）
    proj_client = await _project_client_map(session) if entity == "parent" else {}
    rev_by_client: dict = {}
    for inv in iter_revenue_invoices(inputs["invoices"], mset):
        name = resolve_client_name(inv, proj_client)
        rev_by_client[name] = rev_by_client.get(name, 0) + invoice_ex_tax(inv)
    concentration = client_concentration(rev_by_client)

    # 專案毛利是母公司域（plan §1.2）→ 我的帳回空結構（形狀不變，前端不炸）
    project_margins = (await _project_margins(session) if entity == "parent"
                       else {"available": False, "top": [], "bottom": []})

    return {
        "trend": trend, "cash": cash, "aging": aging,
        "concentration": concentration,
        "project_margins": project_margins,
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


async def tax_package(session, months, period: str = "",
                      entity: str = "parent") -> dict:
    """稅務包：銷項/進項發票明細 + 分類支出 + 勞報彙總 + 營業稅位置 + meta。

    直接吃 _load_inputs 的 crm_invoices / crm_payment_requests / cash_entries
    （取數已按 entity 過濾）。"""
    months = sorted(set(months))
    mset = set(months)
    inputs = await _load_inputs(session, entity)
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
