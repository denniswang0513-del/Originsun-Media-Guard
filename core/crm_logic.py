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

def group_payables(rows) -> dict:
    """應付帳款分組（/payables/summary 的純聚合段，SQL 取數後餵進來）。

    rows: iterable of (payment, staff_id_number, staff_bank_name, staff_bank_account)
          — payment 為 CrmPaymentRequest（或測試用同形 namespace）。
    outerjoin 可能因同名 staff 多列造成重複 → 以 payment.id 去重（保留第一列）。
    回傳 {"payees": [...按 total_amount 降冪...], "grand_total": N}。
    """
    payee_groups: dict = {}
    seen_ids: set = set()
    for p, staff_id_number, staff_bank_name, staff_bank_account in rows:
        if p.id in seen_ids:
            continue
        seen_ids.add(p.id)
        name = p.payee_name or "未指定"
        if name not in payee_groups:
            payee_groups[name] = {
                "payee_name": name,
                "payee_id": p.payee_id or staff_id_number or "",
                "bank_name": staff_bank_name or "",
                "bank_account": staff_bank_account or "",
                "total_amount": 0, "items": [],
            }
        payee_groups[name]["total_amount"] += p.amount or 0
        payee_groups[name]["items"].append({
            "id": p.id,
            "date": p.request_date.strftime("%Y/%m/%d") if p.request_date else "",
            "amount": p.amount or 0,
            "summary": p.summary or "",
            "category": p.category or "",
            "payment_status": p.payment_status or "",
            "payment_date": p.payment_date.strftime("%Y-%m-%d") if p.payment_date else "",
            "planned_month": p.planned_month or "",
        })

    payees = sorted(payee_groups.values(), key=lambda x: x["total_amount"], reverse=True)
    return {"payees": payees, "grand_total": sum(pg["total_amount"] for pg in payees)}


def group_receivables(rows, now) -> dict:
    """應收帳款分組（/receivables/summary 的純聚合段）。

    rows: iterable of (invoice, proj_name, c_tax_id, c_payment_info, c_payment_note)。
    now: datetime — 逾期天數的基準時間（由 caller 注入，維持純函式可測）。
    回傳 {"clients": [...按 total_amount 降冪...], "grand_total": N}。
    """
    client_groups: dict = {}
    seen_ids: set = set()
    for inv, proj_name, c_tax_id, c_payment_info, c_payment_note in rows:
        if inv.id in seen_ids:
            continue
        seen_ids.add(inv.id)
        name = inv.company_name or "未指定"
        if name not in client_groups:
            client_groups[name] = {
                "company_name": name,
                "tax_id": inv.tax_id or c_tax_id or "",
                "payment_info": c_payment_info or "",
                "payment_note": c_payment_note or "",
                "total_amount": 0, "items": [],
            }
        client_groups[name]["total_amount"] += inv.amount_total or 0
        days = 0
        if inv.invoice_date:
            days = (now - inv.invoice_date).days
        client_groups[name]["items"].append({
            "id": inv.id,
            "title": inv.title or "",
            "invoice_number": inv.invoice_number or "",
            "invoice_date": inv.invoice_date.strftime("%Y/%m/%d") if inv.invoice_date else "",
            "amount_total": inv.amount_total or 0,
            "payment_status": inv.payment_status or "",
            "project_name": proj_name or "",
            "days_since_issued": days,
            "category": inv.category or "",
        })

    clients = sorted(client_groups.values(), key=lambda x: x["total_amount"], reverse=True)
    return {"clients": clients, "grand_total": sum(c["total_amount"] for c in clients)}
