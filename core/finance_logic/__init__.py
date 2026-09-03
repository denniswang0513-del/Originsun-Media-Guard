"""財務管理階段二/三純邏輯（core/finance_logic.py — 比照 core/crm_logic.py 慣例）。

零 DB / FastAPI 依賴 — 收支分類、銀行流水/餘額、對帳差額、月份取值、
權責制三表推導（損益/資產負債/現金流量）都是公司帳務的判定規則，
抽成純函式讓「規則」與「SQL 聚合」分離：endpoint / service 只負責把
DB 撈出來的值餵進來。單元測試在 tests/unit/test_finance_logic.py
（27 個 cash category 全覆蓋）+ tests/unit/test_finance_statements.py
（三表黃金測試 + 恆等式）。

階段三新增（三表推導引擎）：
- period_months / month_range / shift_month：期間字串 → 月清單
- depreciation_for_month / accumulated_depreciation / equipment_net_rows：器材直線折舊
- build_pnl / merge_pnl：損益表（權責認列，locked 月快照可加總）
- build_balance_sheet：資產負債表（推導式，check.diff 誠實外顯）
- build_cashflow / merge_cf / cash_entry_activity：現金流量表（直接法）
- vat_position：營業稅位置（銷項/進項/已繳/淨額）
- ar_open_invoices / ap_open_payments / bank_balances_asof：期末部位
- statement_warnings / statement_interpretation：檢核警示 + 白話解讀

階段四新增（銀行貸款）：
- amortization_schedule：攤還表（annuity/straight/interest_only + 寬限期）
- loan_interest_total：利息費用權責認列（按 due_date，不管繳沒繳）
- loan_outstanding_rows：BS 非流動負債逐筆貸款餘額（單純看繳款事實）
- treatment 'loan'（貸款撥款/繳款收支）：不進損益，CF 走科目 cf_activity=financing

對帳工作台（月底對帳升級 — 逐筆勾銷）：
- statement_line_status：明細列狀態推導（matched/noted/unmatched，不落庫）
- auto_match_statement_lines：金額相等+日期最近的確定性自動配對
- workbench_summary：配對計數/未配對金額加總（差額解釋交給人）

──────────────────────────────────────────────────────────────────
2026-08-30 拆成套件（原本 2,548 行 —— 超過一次讀得完的量，而它是改動第二頻繁
的財務檔）。**切法是純行段切割，一支函式都沒搬家**：先用 AST 建呼叫圖、算強
連通分量確認零環，再把界畫在既有的分節註解上，所以跨檔的邊全部同向：

    _core  ←  _statements  ←  _flows        （箭頭 = 誰 import 誰）

  · _core.py       月份運算／器材折舊／銀行貸款／發票款項狀態／損益認列迭代器
                   ／營業稅／期末部位／股東往來          99 個名字
  · _statements.py 損益表 + transfer 流量位置 + 資產負債表  22 個名字
  · _flows.py      現金流量表 + 檢核警示 + 儀表板/稅務包    18 個名字

下面的再匯出清單＝這個模組的目錄（哪個名字住哪一塊）。全部 139 個名字照舊從
`core.finance_logic` 匯入，41 個呼叫端一行都不用改。

🔴 新增函式要**同時**加進下面對應那一塊的清單，否則
`from core.finance_logic import 新函式` 會 ImportError —— 這是刻意的：
寧可在 import 時大聲炸掉，也不要讓半個模組悄悄消失在對外表面之外。
"""

# 月份運算／器材折舊／銀行貸款／發票款項狀態／損益認列迭代器／營業稅／期末部位／股東往來
from ._core import (  # noqa: F401
    BOOKKEEPING_EXTRA_ON_MONTH, CARD_KIND, CF_ACTIVITY_DRILLS, COST_GROUPS,
    DEFAULT_BOOKKEEPING_FEE_RATES, FEE_TOLERANCE, INTERNAL_MOVEMENT_TREATMENTS,
    INVOICE_COLLECTED_STATUSES, INVOICE_ISSUED, INVOICE_NOT_ISSUED,
    INVOICE_PASSTHROUGH_CATEGORIES, INVOICE_PASSTHROUGH_COLLECTED,
    INVOICE_PENDING_REMIT, INVOICE_RECEIVED, INVOICE_REMITTED, INVOICE_VOID,
    NON_PNL_TREATMENTS, OPEX_GROUPS, PASSTHROUGH_FEE_RATES, SHAREHOLDER_KINDS,
    VALID_DRILL_KINDS, VAT_DIVISOR, VAT_PCT,
    _ADVANCE_EXPENSE_LABEL, _AGENCY_FEE_LABEL,
    _ALLOC_SIDES, _BANK_FEE_LABEL,
    _DEPRECIATION_LABEL, _INVOICE_REMITTED_LEGACY, _LINK_PRIORITY,
    _LOAN_INTEREST_LABEL, _LOCAL_TZ, _MAX_PERIOD_MONTHS, _PAIRED_EXPENSE_SUFFIX,
    _UNMAPPED_EXPENSE_LABEL, _UNMAPPED_INCOME_LABEL, _as_date, _in_amount,
    _map_account, _map_info, _month_index, _monthly_due_date, _out_amount, _pct,
    accumulated_depreciation, alloc_verdict, amortization_schedule,
    amount_is_settled, ap_open_payments, apply_payment_fee, apply_receipt_fee,
    ar_open_invoices, ar_overdue_amount, auto_match_statement_lines,
    bank_balances_asof, bank_fee_breakdown,
    bank_fee_total, bank_running_balance,
    bookkeeping_fee,
    cash_entry_flow, classify_cash_entry, depreciation_for_month, depreciation_rows,
    equipment_net_rows, expense_slot, in_amount, initial_invoice_status,
    invoice_collected, invoice_direction, invoice_ex_tax, invoice_tax,
    invoice_vat_side, is_card_kind, is_passthrough_category, is_shareholder_kind,
    issue_status_for, iter_expense_items, iter_loan_interest, iter_revenue_invoices,
    load_bookkeeping_fee_rates, loan_interest_total, loan_outstanding_rows,
    local_day, map_account, map_info, month_of, month_range,
    normalize_invoice_status, out_amount, paired_expense_category,
    passthrough_commission, passthrough_fee_income, period_months,
    recognize_bank_fee, recognize_receipt_fee, reconciliation_diff,
    resolve_client_name, shift_month, split_bank_lines, statement_line_status,
    today_start, vat_position, workbench_summary)

# 損益表 + transfer 流量位置 + 資產負債表
from ._statements import (  # noqa: F401
    ACCOUNT_MOVE_POSITIONS, _OUTSOURCE_LABEL, _PROJECT_CASH_MIRROR_LABEL,
    _PROJECT_MISC_LABEL, _TRANSFER_POSITION_BY_NAME, _bump, _bump_line,
    _dispatch_income, _dispatch_slot, _finalize_pnl, _implied_months, _new_pnl_prim,
    apply_ledger_project_costs, build_balance_sheet, build_pnl, card_outstanding,
    equity_transfer_position, is_account_move, merge_pnl, mine_project_positions,
    restate_revenue_accrual, transfer_position)

# 現金流量表（直接法）+ 檢核警示與白話解讀 + 儀表板/稅務包
from ._flows import (  # noqa: F401
    REVERSAL_MARKERS, TRANSFER_PAIR_WINDOW_DAYS, _AGING_BUCKETS, _EPOCH,
    _is_reversal, _month_end_date, aging_buckets, build_cashflow, cash_account_ids,
    cash_entry_activity, cashflow_lines, client_concentration, merge_cf,
    paired_transfer_ids, runway_months, statement_interpretation,
    statement_warnings, transfer_pairs)
from ._core import (DEFAULT_MARGIN_MODEL, load_margin_model, margin_for_type,  # noqa: F401,E402
                    save_margin_model, suggested_budget_hours)
