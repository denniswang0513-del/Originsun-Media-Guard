"""overtime_logic.py — 加班申請的純規則（docs/PAYROLL_OVERTIME_PLAN.md §2.4／§3.2 第二批）。無 I/O。

owner 2026-09-18「請讓所有的設定吻合勞基法」：
  · 加班日種類分四種（§36／§37）：工作日、休息日（週六）、例假日（週日）、國定假日（假日表）；補班日算工作日；
    颱風假有上班法無規定，公司比照休息日費率（高於法定，可）。
  · 每日上限（§32）：正常＋延長 ≤ 12 小時 → 工作日加班最多 4 小時；休息日／假日一次最多 12 小時。
  · 每月上限（§32）：延長工時 ≤ 46 小時；經勞資會議同意可到 54、三個月 ≤ 138（費率表 extended 勾了才開）。
  · 加班費（§24／§39）與補休（§32-1）的倍率在 core.payroll_logic.DEFAULT_OVERTIME，這裡只呼叫。
員工填：加班日、起訖時間、換成什麼（補休／加班費）、事由、專案（選填）。時數由起訖算（0.5 小時一格），
種類由假日表判 —— 員工不填時數也不選種類（同請假：自助端點不收客戶端算好的數字）。
"""
from datetime import date, datetime
from typing import Iterable, List, Optional, Tuple

from core.leave_logic import as_date, holiday_kind
from core.payroll_logic import DAY_KINDS, DEFAULT_RATES, credit_hours_for, overtime_pay

PAYOUTS = ("補休", "加班費")
OT_STATUSES = ("待審", "已核准", "已退回", "已撤回")
ACTIVE_STATUSES = ("待審", "已核准")        # 佔本月額度的
MIN_HOURS = 0.5
WARN_BEFORE_CAP_HOURS = 8.0                 # 離每月上限不到 8 小時就先黃字


def _minutes(hhmm: str) -> int:
    try:
        h, m = str(hhmm or "").strip().split(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        raise ValueError("時間要像 18:30")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("時間要像 18:30")
    return h * 60 + m


def ot_hours(start_time: str, end_time: str, max_hours: float = 12.0) -> float:
    """起訖 → 小時（四捨五入到 0.5）；結束要晚於開始、最少 0.5、最多 max_hours（依當天種類）。錯 raise ValueError。"""
    a, b = _minutes(start_time), _minutes(end_time)
    if b <= a:
        raise ValueError("結束要晚於開始")
    h = round((b - a) / 60 * 2) / 2
    if h < MIN_HOURS:
        raise ValueError("至少 0.5 小時")
    if h > max_hours:
        raise ValueError(f"這種日子一次最多 {max_hours:g} 小時（勞基法：一天正常＋延長工時不超過 12 小時）")
    return h


def day_kind_for(d, holidays=None) -> str:
    """工作日／休息日／國定假日／例假日：假日表的國定假日→國定假日、補班日→工作日、颱風假→比照休息日；
    其餘週一～五工作日、週六休息日、週日例假日（§36 每七日一例假一休息日，公司排班週六休息、週日例假）。"""
    dd = as_date(d)
    if dd is None:
        return DAY_KINDS[0]
    kind = holiday_kind(dd, holidays)
    if kind == "國定假日":
        return "國定假日"
    if kind == "補班日":
        return "工作日"
    if kind == "颱風假":
        return "休息日"
    if dd.weekday() < 5:
        return "工作日"
    return "休息日" if dd.weekday() == 5 else "例假日"


def daily_max_for(day_kind: str, rates: Optional[dict] = None) -> float:
    ot = (rates or DEFAULT_RATES)["overtime"]
    return float(ot["daily_max"].get(day_kind, 12))


def month_cap_for(rates: Optional[dict] = None) -> float:
    ot = (rates or DEFAULT_RATES)["overtime"]
    return float(ot["month_cap_extended"] if ot.get("extended") else ot["month_cap"])


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return _minutes(a_start) < _minutes(b_end) and _minutes(b_start) < _minutes(a_end)


def expires_on_for(d) -> date:
    """補休到期：加班那個曆年的 12/31（owner 2026-09-07；隔年 1 月結算，未休完依原標準折發工資 §32-1）。"""
    dd = as_date(d)
    return date(dd.year, 12, 31)


def evaluate(on, start_time: str, end_time: str, payout: str, *, month_hours: float = 0.0, quarter_hours: float = 0.0,
             same_day: Iterable[Tuple[str, str]] = (), holidays=None, hourly: Optional[float] = None,
             rates: Optional[dict] = None) -> dict:
    """一張單的試算：{hours, day_kind, credit_hours, pay_amount, month_total, month_cap, errors:[{code,msg}], warnings:[{code,msg}]}。
    month_hours＝這個人這個月其他單（待審＋已核准）的時數；quarter_hours＝含這個月往前三個月的其他單；
    same_day＝同一天其他單的 (start,end)；hourly＝每小時工資（None＝沒主檔）。"""
    r = rates or DEFAULT_RATES
    ot = r["overtime"]
    errors: List[dict] = []
    warnings: List[dict] = []
    d = as_date(on)
    if d is None:
        errors.append({"code": "bad_date", "msg": "加班日必填"})
    if payout not in PAYOUTS:
        errors.append({"code": "bad_payout", "msg": "要選換補休還是加班費"})
    kind = day_kind_for(d, holidays) if d else DAY_KINDS[0]
    hours = 0.0
    try:
        hours = ot_hours(start_time, end_time, daily_max_for(kind, r))
    except ValueError as e:
        errors.append({"code": "bad_range", "msg": str(e)})
    if hours and any(overlaps(start_time, end_time, s, e) for s, e in same_day):
        errors.append({"code": "overlap", "msg": "這天已經報過重疊的時段"})
    cap = month_cap_for(r)
    total = round(float(month_hours or 0) + hours, 2)
    if hours and total > cap:
        errors.append({"code": "month_max", "msg": f"這個月加班會到 {total:g} 小時，超過勞基法每月 {cap:g} 小時的上限"
                                               + ("" if ot.get("extended") else "（經勞資會議同意才能延長到 54 小時，費率表可勾）")})
    elif hours and total > cap - WARN_BEFORE_CAP_HOURS:
        warnings.append({"code": "month_warn", "msg": f"這個月加班累計 {total:g} 小時，離每月 {cap:g} 小時的上限不到 {WARN_BEFORE_CAP_HOURS:g} 小時"})
    if hours and ot.get("extended") and float(quarter_hours or 0) + hours > float(ot["quarter_cap"]):
        errors.append({"code": "quarter_max", "msg": f"三個月加班合計會超過 {ot['quarter_cap']:g} 小時（勞基法延長工時的三個月上限）"})
    if kind == "例假日":
        warnings.append({"code": "regular_dayoff", "msg": "例假日出勤依勞基法只限天災、事變或突發事件，事後還要補假；請確認事由"})
    credit = credit_hours_for(hours, kind, r) if hours else 0.0
    pay = None
    if payout == "加班費":
        if hourly is None:
            warnings.append({"code": "no_profile", "msg": "還沒有薪資主檔，加班費核准時算不出金額；先請管理員到人事管理 › 薪資填"})
        else:
            pay = overtime_pay(hourly, hours, kind, r)
    return {"hours": hours, "day_kind": kind, "credit_hours": credit, "pay_amount": pay, "month_total": total, "month_cap": cap,
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
    ot = r["overtime"]
    return {"payouts": list(PAYOUTS), "statuses": list(OT_STATUSES), "day_kinds": list(DAY_KINDS),
            "month_cap": month_cap_for(r), "month_warn_hours": month_cap_for(r) - WARN_BEFORE_CAP_HOURS,
            "month_max_hours": month_cap_for(r), "daily_max": dict(ot["daily_max"]),
            "credit_multiplier": dict(ot["credit_multiplier"]), "overtime": ot, "rule_text": r.get("rule_text", ""),
            "today": datetime.now().date().isoformat()}
