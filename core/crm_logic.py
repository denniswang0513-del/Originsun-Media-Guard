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


# ── 官網作品（1:N 改造，2026-07）──────────────────────────────────────────

WEBSITE_WORK_STAGES = ("待製作", "製作中", "已上線", "不上官網")


def work_stage(published: bool, prod_stage) -> str:
    """單一作品的官網階段推導（結案看板 / 收件匣共用，避免兩處漂移）。

    published=True → '已上線'；否則看 prod_stage（製作中/不上官網）；
    其餘（含 None / '待製作'）→ '待製作'。
    """
    if published:
        return "已上線"
    if prod_stage in ("製作中", "不上官網"):
        return prod_stage
    return "待製作"


def work_completeness(*, video_url=None, youtube_id=None, extra_videos=None,
                      gallery=None, cover_url=None, featured_image=None,
                      description=None, credits=None, credits_text=None) -> dict:
    """單一作品的素材完成度（結案看板 chips / 收件匣完成度欄共用）。

    參數皆為 crm_project_showcase 的欄位值（1:N 後為單一真相 — 舊版
    「sc 或 project.public_* 任一有值」的 fallback 在 backfill 後恆等，故收斂）。
    回傳四項 bool：video / images / description / credits。
    """
    return {
        "video": bool((video_url or "").strip() or (youtube_id or "").strip()
                      or (extra_videos and len(extra_videos) > 0)),
        "images": bool((gallery and len(gallery) > 0)
                       or (featured_image or "").strip() or (cover_url or "").strip()),
        "description": bool((description or "").strip()),
        "credits": bool((credits and len(credits) > 0) or (credits_text or "").strip()),
    }


def project_works_summary(works) -> dict:
    """專案層作品聚合 — 結案看板卡片「2/3 已上線」進度 + 全上線判定。

    works: iterable of dict，至少含 stage（work_stage 推導值）+ verified（bool）。
    分母 = 總數 − 標「不上官網」者；all_live = 分母 > 0 且全部已上線
    （全部上線專案才算完成 — owner 2026-07-10 拍板）。
    """
    ws = list(works)
    total = len(ws)
    skipped = sum(1 for w in ws if w.get("stage") == "不上官網")
    live = sum(1 for w in ws if w.get("stage") == "已上線")
    verified = sum(1 for w in ws if w.get("stage") == "已上線" and w.get("verified"))
    denominator = total - skipped
    return {
        "total": total,
        "live": live,
        "verified": verified,
        "skipped": skipped,
        "pending": max(0, denominator - live),
        "all_live": bool(denominator > 0 and live >= denominator),
    }
