"""overtime_logic.py — 加班申請的純規則（docs/PAYROLL_OVERTIME_PLAN.md §2.4／§3.2 第二批）。無 I/O。

員工填：加班日、起訖時間、換成什麼（補休／加班費）、事由、專案（選填）。時數由起訖算（0.5 小時一格），
種類（工作日／假日）由假日表判 —— 員工不填時數也不選種類（同請假：自助端點不收客戶端算好的數字）。
核准：補休 → hr_leave_credits 一列（時數＝core.leave_logic.overtime_credit_hours，平日 1:1、假日 1:2）；
      加班費 → 金額＝每小時工資 × 時數 × 公司倍率（core.payroll_logic.overtime_pay），掛到該月薪資單草稿。
守衛：每月加班（待審＋已核准）超過 46 小時標黃、超過 54 小時擋下（勞基法 §32）；同一天同一人時段重疊擋下。
"""
from datetime import date, datetime
from typing import Iterable, List, Optional, Tuple

from core.leave_logic import as_date, is_workday, overtime_credit_hours
from core.payroll_logic import DAY_KINDS, DEFAULT_RATES, overtime_pay

PAYOUTS = ("補休", "加班費")
OT_STATUSES = ("待審", "已核准", "已退回", "已撤回")
ACTIVE_STATUSES = ("待審", "已核准")        # 佔本月額度的
MONTH_WARN_HOURS = 46.0                     # 勞基法 §32：每月延長工時上限 46
MONTH_MAX_HOURS = 54.0                      # 經工會／勞資會議同意可到 54 —— 系統以此擋下
MAX_HOURS_PER_REQUEST = 12.0
MIN_HOURS = 0.5


def _minutes(hhmm: str) -> int:
    try:
        h, m = str(hhmm or "").strip().split(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        raise ValueError("時間要像 18:30")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("時間要像 18:30")
    return h * 60 + m


def ot_hours(start_time: str, end_time: str) -> float:
    """起訖 → 小時（四捨五入到 0.5）；結束要晚於開始、一次最多 12 小時、最少 0.5。錯 raise ValueError。"""
    a, b = _minutes(start_time), _minutes(end_time)
    if b <= a:
        raise ValueError("結束要晚於開始")
    h = round((b - a) / 60 * 2) / 2
    if h < MIN_HOURS:
        raise ValueError("至少 0.5 小時")
    if h > MAX_HOURS_PER_REQUEST:
        raise ValueError(f"一次最多 {MAX_HOURS_PER_REQUEST:g} 小時")
    return h


def day_kind_for(d, holidays=None) -> str:
    """工作日／假日（週末、國定假日、颱風假；補班日算工作日）。"""
    dd = as_date(d)
    return DAY_KINDS[0] if dd is not None and is_workday(dd, holidays) else DAY_KINDS[1]


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return _minutes(a_start) < _minutes(b_end) and _minutes(b_start) < _minutes(a_end)


def expires_on_for(d) -> date:
    """補休到期：加班那個曆年的 12/31（owner 2026-09-07；隔年 1 月結算）。"""
    dd = as_date(d)
    return date(dd.year, 12, 31)


def evaluate(on, start_time: str, end_time: str, payout: str, *, month_hours: float = 0.0,
             same_day: Iterable[Tuple[str, str]] = (), holidays=None, hourly: Optional[float] = None,
             rates: Optional[dict] = None) -> dict:
    """一張單的試算：{hours, day_kind, credit_hours, pay_amount, month_total, errors:[{code,msg}], warnings:[{code,msg}]}。
    month_hours＝這個人這個月其他單（待審＋已核准）的時數；same_day＝同一天其他單的 (start,end)；hourly＝每小時工資（None＝沒主檔）。"""
    errors: List[dict] = []
    warnings: List[dict] = []
    d = as_date(on)
    if d is None:
        errors.append({"code": "bad_date", "msg": "加班日必填"})
    if payout not in PAYOUTS:
        errors.append({"code": "bad_payout", "msg": "要選換補休還是加班費"})
    hours = 0.0
    try:
        hours = ot_hours(start_time, end_time)
    except ValueError as e:
        errors.append({"code": "bad_range", "msg": str(e)})
    if hours and any(overlaps(start_time, end_time, s, e) for s, e in same_day):
        errors.append({"code": "overlap", "msg": "這天已經報過重疊的時段"})
    kind = day_kind_for(d, holidays) if d else DAY_KINDS[0]
    total = round(float(month_hours or 0) + hours, 2)
    if hours and total > MONTH_MAX_HOURS:
        errors.append({"code": "month_max", "msg": f"這個月加班會到 {total:g} 小時，超過 {MONTH_MAX_HOURS:g} 小時的上限"})
    elif hours and total > MONTH_WARN_HOURS:
        warnings.append({"code": "month_warn", "msg": f"這個月加班累計 {total:g} 小時，超過勞基法每月 {MONTH_WARN_HOURS:g} 小時"})
    credit = overtime_credit_hours(hours, d, holidays) if (hours and d) else 0.0
    pay = None
    if payout == "加班費":
        if hourly is None:
            warnings.append({"code": "no_profile", "msg": "還沒有薪資主檔，加班費核准時算不出金額；先請管理員到人事管理 › 薪資填"})
        else:
            pay = overtime_pay(hourly, hours, kind, rates or DEFAULT_RATES)
    return {"hours": hours, "day_kind": kind, "credit_hours": credit, "pay_amount": pay, "month_total": total,
            "errors": errors, "warnings": warnings}


def month_of(d) -> str:
    dd = as_date(d)
    return f"{dd.year}-{dd.month:02d}"


def pay_month_for(on, confirmed_months: Iterable[str]) -> str:
    """加班費掛哪個月的薪資單：加班日那個月；那個月已確認就往後找第一個沒確認的月。"""
    from core.payroll_logic import next_month
    m = month_of(on)
    done = set(confirmed_months)
    guard = 0
    while m in done and guard < 24:
        m = next_month(m)
        guard += 1
    return m


def summarize_month(items: Iterable[dict], month: str) -> dict:
    """{hours, credit_hours, pay_amount, count} —— 這個月（待審＋已核准）的合計，給卡片與佇列用。"""
    hours = credit = pay = 0.0
    n = 0
    for it in items:
        if it.get("status") not in ACTIVE_STATUSES or str(it.get("date") or "")[:7] != month:
            continue
        n += 1
        hours += float(it.get("hours") or 0)
        if it.get("payout") == "補休":
            credit += float(it.get("credit_hours") or 0)
        else:
            pay += float(it.get("pay_amount") or 0)
    return {"hours": round(hours, 2), "credit_hours": round(credit, 2), "pay_amount": int(pay), "count": n}


def vocab(rates: Optional[dict] = None) -> dict:
    r = rates or DEFAULT_RATES
    return {"payouts": list(PAYOUTS), "statuses": list(OT_STATUSES), "day_kinds": list(DAY_KINDS),
            "month_warn_hours": MONTH_WARN_HOURS, "month_max_hours": MONTH_MAX_HOURS, "max_hours_per_request": MAX_HOURS_PER_REQUEST,
            "credit_multiplier": {"工作日": 1, "假日": 2}, "pay_multiplier": dict(r.get("overtime_multiplier") or {}),
            "today": datetime.now().date().isoformat()}
