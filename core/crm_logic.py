"""CRM 純計算邏輯 — 無 DB / FastAPI 依賴，供單元測試直接驗證。

錢流判定規則是公司帳務的 source of truth，抽成純函式讓「規則」與
「SQL 聚合」分離：endpoint 只負責把 DB 加總餵進來。
"""


def compute_advance_status(amount: float, expense_total: float,
                           cash_pay_total: float, cash_return_total: float) -> dict:
    """預支款結算三狀態（規則見 CLAUDE.md §7.15「預支款自動結算」）。

    參數皆為 DB 聚合值：
      amount            預支金額（crm_payment_requests.amount, is_advance=1）
      expense_total     關聯此預支的專案雜支合計（crm_project_expenses.actual）
      cash_pay_total    收支明細關聯此預支的「支出」合計（發款，cash_entries.expense）
      cash_return_total 收支明細關聯此預支的「收入」合計（還款，cash_entries.deposit）

    回傳：
      is_paid     已發款 = 發款合計 ≥ 預支金額（金額必須 > 0）
      is_returned 已收款 = 還款合計 > 0
      balance     餘額 = 預支金額 − 專案支出 − 已收回款
      is_settled  已結清 = 已發款 + 已收款 + 餘額為 0
    """
    amount = amount or 0
    expense_total = expense_total or 0
    cash_pay_total = cash_pay_total or 0
    cash_return_total = cash_return_total or 0
    is_paid = cash_pay_total >= amount > 0
    is_returned = cash_return_total > 0
    balance = amount - expense_total - cash_return_total
    return {
        "is_paid": is_paid,
        "is_returned": is_returned,
        "balance": balance,
        "is_settled": bool(is_paid and is_returned and balance == 0),
    }
