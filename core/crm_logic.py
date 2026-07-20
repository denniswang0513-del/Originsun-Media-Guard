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

def project_margin(contract, tax_rate, expense_actual, staff_actual) -> dict:
    """專案毛利（毛利公式單一來源）— routers/crm/costs.py 專案財務摘要與
    財務儀表板 Top/Bottom 共用，避免同一「毛利」定義在兩處各算一份而漂移。

    revenue(ex_tax) = 合約金額 ÷ (1 + 稅率%)（稅率缺值視同 5）；
    cost = 雜支實際 + 派工實際；margin = revenue − cost；
    margin_pct = margin ÷ revenue × 100（1 位小數；revenue ≤ 0 → None）。
    （呼叫端若要整數百分比自行 round(m["margin"]/m["ex_tax"]*100)，顯示精度各自決定。）
    """
    ex_tax = round(int(contract or 0) / (1 + (tax_rate or 5) / 100))
    cost = int(expense_actual or 0) + int(staff_actual or 0)
    margin = ex_tax - cost
    return {"ex_tax": ex_tax, "cost": cost, "margin": margin,
            "margin_pct": round(margin * 100 / ex_tax, 1) if ex_tax > 0 else None}


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

# showcase-edit token（mint 在 routers/crm/showcase.py、child work 建立在
# services/website/project_service.py — scope/效期常數共用一份，漂移 = token 驗證失敗）
SHOWCASE_EDIT_SCOPE = "showcase_edit"
PERMANENT_TOKEN_EXPIRES_DAYS = 36500  # ~100 年，實務上永久（各 scope token 共用政策）
SHOWCASE_EDIT_EXPIRES_DAYS = PERMANENT_TOKEN_EXPIRES_DAYS


def showcase_edit_url(token: str) -> str:
    """token → 編輯器 URL（後端各端點回 edit_url/url 的單一組法）。"""
    return f"/showcase-edit.html?token={token}"


def is_main_work(sc) -> bool:
    """主作品 = id == project_id 那列（歷史 1:1 時代 PK 直接用 project_id）。
    舊 project-scoped 端點與過渡期 dual-write 都以此判定 — 全庫唯一定義處。"""
    return bool(sc.id and sc.id == sc.project_id)


def work_url_slug(sc) -> "str | None":
    """作品 URL slug：admin 自訂 slug > number（首發配號）> None（無法組 URL）。

    sc = CrmProjectShowcase 或同形物件。'work' 兜底版見
    services/website/project_service._slug_or_fallback（委派本函式）。"""
    custom = (sc.slug or "").strip()
    if custom:
        return custom
    if sc.number is not None:
        return str(sc.number)
    return None


# wire key（歷史沿用 public_* 命名，前端/token 編輯器不用改）→ 作品欄位 對照的
# 單一正本。project_service 的寫入映射與 showcase.py 的 token 身分欄位子集都由此派生。
# 不含 public_client（專案層資料）與 credits/cover（只能從 showcase-edit 直寫）。
WORK_WIRE_FIELD_MAP = {
    "public": "published",
    "public_slug": "slug",
    "public_title": "title",
    "public_youtube_id": "youtube_id",
    "public_description": "description",
    "public_year": "year",
    "public_featured": "featured",
    "public_featured_image": "featured_image",
    "public_sort_order": "sort_order",
    "public_published_at": "published_at",
    "public_old_slugs": "old_slugs",
    "public_noindex": "noindex",
}


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


def rebuild_status_public(st: dict, now: float) -> dict:
    """rebuild_service.get_rebuild_status() → token 端點可見的白名單子集。

    給 showcase-edit 發布時間線用。**絕不**帶出 output_tail / error 內文 ——
    build log 不可洩漏給 token 持有者（state 字串本身無害）。
    auto_fires_in_sec 伺服端算（免 client 時鐘偏差），無排程時為 None。
    """
    st = st or {}
    fires_at = st.get("auto_rebuild_fires_at")
    return {
        "state": st.get("state") or "idle",
        "pending_count": int(st.get("pending_count") or 0),
        "auto_fires_in_sec": max(0, round(fires_at - now)) if fires_at else None,
        "last_success_at": st.get("last_success_at"),
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
