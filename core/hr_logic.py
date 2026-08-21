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


# ── 每人額度（LAZY KIT 那種年度活動，docs/BENEFIT_POOL_PLAN.md §9）──────

QUOTA_MODES = ("shared", "per_person")


def in_window(day, start, end) -> bool:
    """day 落在 [start, end] 之內嗎。三個都是 date（不是 datetime）。

    🔴 呼叫端一律先用 local_day() 把 timestamptz 轉成本地日期再進來。
    直接拿 datetime 比會差一天 —— 讀回來是 UTC，這個 repo 已經被咬過三次。
    邊界含在內：券寫 2026/01/01–12/31，那兩天當然算數。
    """
    if day is None:
        return False
    if start is not None and day < start:
        return False
    if end is not None and day > end:
        return False
    return True


def staff_allowance_balance(amount, entries) -> dict:
    """某個人在某個活動裡的額度用量。

    `entries` 是 (status, amount, in_window) 序列 —— **期間判定在呼叫端做完**
    再傳進來，這支保持無日期、無 I/O（好測、也不會偷偷多一套時區邏輯）。

    期間外的登記不計入 used —— 它本來就不該被核准（見 validate_against_allowance）。
    """
    quota = int(amount or 0)
    used = pending = 0
    for status, amt, inside in entries:
        if not inside:
            continue
        a = int(amt or 0)
        if status in BENEFIT_COMMITTED:
            used += a
        elif status == "待審":
            pending += a
    # 🔴 `available` 跟共用池不一樣：**待審也佔住**自己的額度。
    #    共用池「待審不扣」是對的（桶子大、退件不用回沖）；但個人額度只有
    #    10,000 時，那條規則會讓同一個人連送三筆 10,000 全部待審都不被擋
    #    —— 擋超額等於形同虛設，等 owner 一核准就爆了。這筆是他自己的待審、
    #    佔的是他自己的額度，退回就放回來，不牽涉別人，所以沒有回沖問題。
    return {"quota": quota, "used": used, "pending": pending,
            "balance": quota - used, "available": quota - used - pending,
            "over": quota - used < 0}


def pool_allowance_rollup(allowances, used_by_staff) -> dict:
    """整個活動的配發概況（管理端看的）。

    `allowances` 是金額序列，`used_by_staff` 是每個人已用金額的序列。
    「還沒動用的人數」是 owner 真正想知道的 —— 券發下去沒人用才是問題。
    """
    granted = sum(int(a or 0) for a in allowances)
    used = sum(int(u or 0) for u in used_by_staff)
    return {"granted": granted, "used": used, "balance": granted - used,
            "people": len(list(allowances)),
            "untouched": sum(1 for u in used_by_staff if int(u or 0) == 0)}


def validate_against_allowance(amount, spend_day, allowance) -> str:
    """每人額度模式下，這筆登記過得了嗎。回錯誤訊息，空字串＝過關。

    🔴 這裡**要擋**，跟共用池不同。共用池超支只轉紅不擋 —— 那是公司該知道的
    事實；個人額度超額是「這張券本來就沒這麼多」，讓它過去只是把問題推到
    請款那一關才爆（那時已經有人以為自己花得起了）。

    `allowance` 是 dict：{amount, valid_from, valid_to, available}，None ＝沒發給他。
    `available` ＝ 額度 − 已用 − **審核中**（見 staff_allowance_balance 的 🔴）。
    """
    if not allowance:
        return "這個活動沒有發給你額度，請找管理員"
    if not in_window(spend_day, allowance.get("valid_from"),
                     allowance.get("valid_to")):
        lo = allowance.get("valid_from")
        hi = allowance.get("valid_to")
        span = f"{lo or '不限'} ~ {hi or '不限'}"
        return f"日期不在活動期間內（{span}）"
    left = int(allowance.get("available") or 0)
    a = int(amount or 0)
    if a > left:
        return f"超過你的額度（還可以用 ${left:,}）"
    return ""
