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


# ── 福委會（docs/BENEFIT_POOL_PLAN.md）純規則 ────────────────────────
#
# owner 2026-08-21：「我有幾個福利池，一個是快樂、一個是進修，這兩塊員工都可以
# 登記，他們登記後我審核通過，就進公司請款。我每年會撥一筆錢進這個池。」
# 池的名字是資料不是程式碼 —— 不寫死成枚舉，owner 想再開一個池就自己開。

BENEFIT_STATUSES = ("待審", "已核准", "已付款", "退回")
# 還在本人手上、可以自己改自己刪的（送出去之前 owner 還沒看過）
BENEFIT_EDITABLE = ("待審", "退回")
# 已經確定要花的錢 —— 餘額扣的是它。🔴 待審**不算**：待審就扣的話退件之後
# 餘額要回沖，那是一種很容易對不起來的帳（畫面另外顯示「審核中」就夠了）。
BENEFIT_COMMITTED = ("已核准", "已付款")
# （「不能改」是 BENEFIT_EDITABLE 的補集 —— 已核准／已付款進了公司請款，
#   帳上掛著一張應付款，這裡改金額兩邊會對不起來。不另立一個常數。）


def benefit_pool_balance(fundings, entries) -> dict:
    """池的用量。`fundings` 是金額序列（每年撥進來的），`entries` 是
    (status, amount) 序列（員工登記的花費，金額一律正數）。

    四個數字分開回，因為它們回答不同的問題：
      funded   歷年撥進來的總額
      used     已經確定要花的（已核准＋已付款）→ 餘額扣的是它
      pending  審核中 → 不扣餘額，但 owner 要看得到「還有多少在路上」
      balance  funded − used，**可以是負的** —— 超支要看得見，
               夾成 0 等於把問題藏起來（真的花超了，畫面就該是紅的）。
    """
    funded = sum(int(a or 0) for a in fundings)
    used = pending = 0
    for status, amount in entries:
        a = int(amount or 0)
        if status in BENEFIT_COMMITTED:
            used += a
        elif status == "待審":
            pending += a
    return {"funded": funded, "used": used, "pending": pending,
            "balance": funded - used, "over": funded - used < 0}


def validate_benefit_entry(title, amount) -> str:
    """員工登記一筆的欄位檢查。回錯誤訊息字串，空字串＝過關。

    項目是自由文字（電影名／餐廳／課程名）—— 不做枚舉。分類這件事由**池**
    承擔（快樂／進修），再加一層項目分類只是逼人每次多選一格。
    """
    if not (title or "").strip():
        return "請填項目（花在什麼上）"
    if int(amount or 0) <= 0:
        return "金額要大於 0"
    return ""


def benefit_can_edit(status: str) -> bool:
    """這筆還在本人手上嗎（可以自己改／自己刪）。"""
    return (status or "") in BENEFIT_EDITABLE
