"""core/hr_logic.py — 人事（請假/工時）純函式：判定邏輯 + 解析/序列化。

照 CLAUDE.md 慣例：純函式、無 I/O、單元測試在 tests/unit/test_hr_logic.py。
api_hr 與 api_me 共用（router 端只留 I/O）。
"""
from datetime import datetime
from typing import Optional

LEAVE_TYPES = ("特休", "病假", "事假", "公假", "婚假", "喪假", "其他")
LEAVE_STATUSES = ("待審", "已核准", "已退回")
ANNUAL_TYPE = "特休"


def parse_ymd(raw: Optional[str]) -> Optional[datetime]:
    """YYYY-MM-DD → datetime；空/壞格式回 None（呼叫端決定要不要 422）。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def leave_to_dict(o) -> dict:
    """HrLeaveRequest → API dict（api_hr / api_me 共用序列化）。"""
    return {
        "id": o.id, "staff_id": o.staff_id, "staff_name": o.staff_name or "",
        "leave_type": o.leave_type,
        "start_date": o.start_date.strftime("%Y-%m-%d") if o.start_date else "",
        "end_date": o.end_date.strftime("%Y-%m-%d") if o.end_date else "",
        "days": o.days, "reason": o.reason or "",
        "status": o.status,
        "approved_by": o.approved_by or "",
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "created_by": o.created_by or "",
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


def validate_leave(leave_type: str, start: Optional[datetime],
                   end: Optional[datetime], days: float) -> Optional[str]:
    """請假單欄位驗證。合法回 None，否則回錯誤訊息（中文，直接給 422 detail）。"""
    if leave_type not in LEAVE_TYPES:
        return f"假別需為：{'/'.join(LEAVE_TYPES)}"
    if start is None or end is None:
        return "起訖日期必填"
    if end < start:
        return "迄日不可早於起日"
    if days <= 0:
        return "天數需大於 0"
    if round(days * 2) != days * 2:
        return "天數以 0.5 天為最小單位"
    return None


def leave_balance(annual_days: Optional[int], approved_annual_sum: float) -> dict:
    """特休餘額 — 即時算，不另存 ledger（HR_FIN_PLAN H2）。

    annual_days 未設定（None）→ annual/remaining 回 None（前端顯示「未設定」），
    used 照算讓管理者仍看得到已休天數。
    """
    used = round(approved_annual_sum or 0.0, 1)
    if annual_days is None:
        return {"annual": None, "used": used, "remaining": None}
    return {"annual": annual_days, "used": used,
            "remaining": round(annual_days - used, 1)}


def manual_dup_key(staff_name: str, work_date: Optional[datetime],
                   project_name: str) -> tuple:
    """工時雙來源去重鍵（藍圖 §3.6 階段3：同人+日+專案，手填優先於 Sheet）。

    ingest 落列前與 source='manual' 既有列比對此鍵。日期一律以**本地時區**取
    date()：timestamptz 欄位寫入時是 naive（PG 依伺服器時區解讀）、asyncpg 讀回
    是 aware UTC —— 直接 .date() 在 +08 時區會差一天（17 日 00:00 寫入 → 讀回
    16 日 16:00Z）。astimezone() 對 naive 視為本地時間、對 aware 轉回本地，
    兩種型態都落在同一個本地日。
    """
    return ((staff_name or "").strip(),
            work_date.astimezone().date() if work_date else None,
            (project_name or "").strip())


# ── 福利池（docs/BENEFIT_POOL_PLAN.md）純規則 ────────────────────────

BENEFIT_CATEGORIES = ("生日禮金", "三節獎金", "婚喪喜慶", "健康檢查",
                      "教育訓練", "員工旅遊", "其他")
BENEFIT_KINDS = ("給付", "核銷")
# 狀態字刻意與零用金同一組（docs/PETTY_CASH_PLAN.md）—— 同一個心智模型，
# UI 的顏色/排序/文案都能沿用，使用者不用學第二套。
BENEFIT_STATUSES = ("草稿", "待審", "已核准", "已付款", "退回")
# 只有這兩個狀態還在本人手上（同零用金 EDITABLE）
BENEFIT_EDITABLE = ("草稿", "退回")
# 已經吃掉預算的狀態。🔴 待審**不算** —— 待審就扣，退件後餘額要回沖，
# 那是一種很容易對不起來的帳（畫面另外顯示「審核中」金額就夠了）。
BENEFIT_COMMITTED = ("已核准", "已付款")
# 進了應付帳款之後就不該再讓人改金額（改了帳上那張應付款會對不起來）
BENEFIT_LOCKED = ("已核准", "已付款")


def benefit_pool_balance(budget, grants) -> dict:
    """池的用量。`grants` 是 (status, amount) 序列。

    三個數字分開回，因為它們回答不同的問題：
      used     已經確定要花的（已核准＋已付款）→ 餘額扣的是它
      pending  審核中 → 不扣餘額，但主管要看得到「還有多少在路上」
      balance  budget − used，**可以是負的** —— 超支要看得見，
               夾成 0 等於把問題藏起來（真的超編了，畫面就該是紅的）。
    """
    used = pending = 0
    for status, amount in grants:
        a = int(amount or 0)
        if status in BENEFIT_COMMITTED:
            used += a
        elif status == "待審":
            pending += a
    budget = int(budget or 0)
    return {"budget": budget, "used": used, "pending": pending,
            "balance": budget - used, "over": budget - used < 0}


def validate_benefit_grant(category, kind, amount, taxable) -> str:
    """建立/修改一筆動支的欄位檢查。回錯誤訊息字串，空字串＝過關。"""
    if category not in BENEFIT_CATEGORIES:
        return f"福利項目不在清單裡：{category}"
    if kind not in BENEFIT_KINDS:
        return f"動支方式只能是 {' / '.join(BENEFIT_KINDS)}"
    if int(amount or 0) <= 0:
        return "金額要大於 0"
    if int(taxable or 0) not in (0, 1):
        return "是否併入個人所得只能是 0 或 1"
    return ""


def benefit_accounting_split(grants) -> dict:
    """會計交付的兩區分法。`grants` 是 dict 序列（要有 taxable/amount/staff_name/
    category/staff_id）。

    分區的唯一依據是 `taxable` —— 併入個人所得的那些，會計年底要開扣繳憑單，
    所以按**人**彙總；不併的是公司費用，按**項目**彙總（會計要的是科目）。
    兩區的分母不同，這也是為什麼不能只給一張總表。
    """
    taxed, company = [], []
    for g in grants:
        (taxed if int(g.get("taxable") or 0) == 1 else company).append(g)

    by_staff: dict = {}
    for g in taxed:
        k = g.get("staff_id") or g.get("staff_name") or ""
        row = by_staff.setdefault(k, {"staff_id": g.get("staff_id") or "",
                                      "staff_name": g.get("staff_name") or "",
                                      "count": 0, "total": 0})
        row["count"] += 1
        row["total"] += int(g.get("amount") or 0)

    by_category: dict = {}
    for g in company:
        k = g.get("category") or "其他"
        row = by_category.setdefault(k, {"category": k, "count": 0, "total": 0})
        row["count"] += 1
        row["total"] += int(g.get("amount") or 0)

    return {
        "personal_income": {
            "summary": sorted(by_staff.values(), key=lambda r: -r["total"]),
            "rows": taxed,
            "total": sum(int(g.get("amount") or 0) for g in taxed),
        },
        "company_expense": {
            "summary": sorted(by_category.values(), key=lambda r: -r["total"]),
            "rows": company,
            "total": sum(int(g.get("amount") or 0) for g in company),
        },
    }
