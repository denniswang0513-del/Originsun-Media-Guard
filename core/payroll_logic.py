"""payroll_logic.py — 薪資單的純規則（docs/PAYROLL_OVERTIME_PLAN.md §3）。無 I/O、無 DB。

一列薪資單怎麼算（owner 2026-09-17 拍板）：
    應發 gross_pay  = base_pay + meal_allowance + overtime_pay + bonus_pay − leave_deduction
    實發 net_pay    = gross_pay − labor_self − health_self − pension_self − other_deduction
    公司總成本      = gross_pay + labor_employer + health_employer + pension_employer
勞健保、勞退全部從「費率表」查（一年一份，DEFAULT_RATES 是系統帶的 2026 版；管理員在畫面改），
不再手算。時薪制 base_pay ＝ 時薪 × 手填時數；月薪制 base_pay ＝ 底薪。

加班費（owner：「照公司規定」）：工作日 ×1、假日 ×2，基數＝每小時工資（月薪 ÷ 240 或時薪）。
倍率放費率表 overtime_multiplier，管理員可改；法定最低（1.34／1.67）只在畫面旁提醒，不擋。
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional

PAY_TYPES = ("月薪", "時薪", "日薪")
PAYROLL_ENTITIES = ("公司", "代發")          # 代發＝股東自己的人，公司只是過帳（docs/CASHBOOK_CLASSIFICATION.md）
RUN_STATUSES = ("草稿", "已確認")
DAY_KINDS = ("工作日", "假日")                 # 加班日種類：假日＝休息日／國定假日／例假／颱風假有上班
LINE_EDITABLE = ("work_hours", "overtime_pay", "bonus_pay", "leave_deduction", "other_deduction", "note")
HOURS_PER_MONTH = 240                       # 勞動部算法：30 天 × 8 小時
MEAL_TAX_FREE = 3000                        # 伙食費免稅上限

# 2026（民國 115 年）投保金額分級：勞保 11 級到 45,800、勞退到 150,000、健保到 313,000。
# 三張表共用同一串級距（勞保／勞退只是取前段），跟勞保局公告的表一致；每年 1 月在費率表頁貼新表。
LEVELS_2026: List[int] = [
    29500, 30300, 31800, 33300, 34800, 36300, 38200, 40100, 42000, 43900, 45800,
    48200, 50600, 53000, 55400, 57800, 60800, 63800, 66800, 69800, 72800, 76500, 80200, 83900, 87600,
    92100, 96600, 101100, 105600, 110100, 115500, 120900, 126300, 131700, 137100, 142500, 147900, 150000,
    156400, 162800, 169200, 175600, 182000, 189500, 197000, 204500, 212000, 219500, 227000, 234500,
    242000, 249500, 257000, 264500, 272000, 279500, 287000, 294500, 302000, 309500, 313000,
]

DEFAULT_RATES: Dict = {
    "year": 2026,
    "labor_rate": 0.125,            # 勞保普通事故 11.5% ＋ 就業保險 1%
    "labor_employee_share": 0.2,    # 勞工 20%／雇主 70%／政府 10%
    "labor_employer_share": 0.7,
    "accident_rate": 0.0013,        # 職災保險費率（雇主全額）：影片及電視節目業 0.06% ＋ 上下班 0.07%（114 年公告，115 年沿用）
    "health_rate": 0.0517,          # 健保費率 5.17%
    "health_employee_share": 0.3,   # 本人 30%（眷屬每人再加一份）／雇主 60%／政府 10%
    "health_employer_share": 0.6,
    "avg_dependents": 0.56,         # 雇主負擔含平均眷口數 0.56 → ×1.56
    "pension_employer_rate": 0.06,  # 勞退雇主提繳 6%
    "labor_max_level": 45800,
    "pension_max_level": 150000,
    "health_max_level": 313000,
    "levels": LEVELS_2026,
    "hours_per_month": HOURS_PER_MONTH,
    "meal_tax_free": MEAL_TAX_FREE,
    "overtime_multiplier": {"工作日": 1.0, "假日": 2.0},   # 公司規定；法定最低 1.34／1.67 只提醒
}

_RATE_KEYS = tuple(k for k in DEFAULT_RATES if k not in ("year", "levels", "overtime_multiplier"))


def _r(x) -> int:
    """四捨五入到整數元（勞保局的表也是這樣進位；Python 的 round 是四捨六入五成雙，不能用）。"""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_rates(raw: Optional[dict], year: int = 0) -> dict:
    """費率表：只留認得的鍵、數字轉 float、級距去重排序；缺的用 2026 預設補。"""
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_RATES)
    out["year"] = int(year or src.get("year") or DEFAULT_RATES["year"])
    for k in _RATE_KEYS:
        v = src.get(k)
        if v is None or v == "":
            continue
        try:
            out[k] = float(v) if isinstance(DEFAULT_RATES[k], float) else int(float(v))
        except (TypeError, ValueError):
            continue
    lv = src.get("levels")
    if isinstance(lv, list):
        clean = sorted({int(float(x)) for x in lv if str(x).strip() and float(x) > 0})
        if clean:
            out["levels"] = clean
    om = src.get("overtime_multiplier")
    if isinstance(om, dict):
        mult = dict(DEFAULT_RATES["overtime_multiplier"])
        for k in DAY_KINDS:
            try:
                if om.get(k) is not None:
                    mult[k] = float(om[k])
            except (TypeError, ValueError):
                pass
        out["overtime_multiplier"] = mult
    else:
        out["overtime_multiplier"] = dict(DEFAULT_RATES["overtime_multiplier"])
    return out


def grade_for(amount: int, max_level: int, levels: Iterable[int]) -> int:
    """投保級距：第一個 ≥ 月薪的級距；低於最低級距用最低級距，超過上限用上限。"""
    lv = sorted(int(x) for x in levels)
    if not lv:
        return int(amount)
    cap = min(max_level, lv[-1]) if max_level else lv[-1]
    for x in lv:
        if x >= amount:
            return min(x, cap)
    return cap


def default_grades(pay_type: str, base_amount: int, meal_allowance: int, rates: dict) -> dict:
    """新主檔的預設投保級距：月薪制看底薪＋伙食費；時薪制先用最低級距（實際依當月工資，owner 自己改）。"""
    monthly = int(base_amount or 0) + int(meal_allowance or 0) if pay_type == "月薪" else 0
    lv = rates["levels"]
    return {"labor_grade": grade_for(monthly, rates["labor_max_level"], lv),
            "health_grade": grade_for(monthly, rates["health_max_level"], lv)}


def insurance_for(labor_grade: int, health_grade: int, dependents: int, pension_self_rate: float, rates: dict) -> dict:
    """一個人每月的勞保／健保／勞退：自負額（從薪水扣）與雇主負擔（不進實發、進公司成本）。
    勞退提繳工資另有上限（150,000），級距同健保那串取前段。"""
    lg, hg = int(labor_grade or 0), int(health_grade or 0)
    pg = grade_for(hg, rates["pension_max_level"], rates["levels"]) if hg else 0
    dep = max(0, int(dependents or 0))
    labor_self = _r(lg * rates["labor_rate"] * rates["labor_employee_share"])
    labor_employer = _r(lg * rates["labor_rate"] * rates["labor_employer_share"]) + _r(lg * rates["accident_rate"])
    health_self = _r(hg * rates["health_rate"] * rates["health_employee_share"]) * (1 + dep)
    health_employer = _r(hg * rates["health_rate"] * rates["health_employer_share"] * (1 + rates["avg_dependents"]))
    pension_employer = _r(pg * rates["pension_employer_rate"])
    pension_self = _r(pg * max(0.0, min(float(pension_self_rate or 0), 6.0)) / 100)
    return {"labor_self": labor_self, "labor_employer": labor_employer,
            "health_self": health_self, "health_employer": health_employer,
            "pension_self": pension_self, "pension_employer": pension_employer, "pension_grade": pg}


def hourly_wage(pay_type: str, base_amount: int, rates: Optional[dict] = None) -> float:
    """每小時工資（加班費基數）：月薪 ÷ 240；時薪照填；日薪 ÷ 8。"""
    base = float(base_amount or 0)
    if pay_type == "時薪":
        return base
    if pay_type == "日薪":
        return base / 8
    return base / float((rates or DEFAULT_RATES).get("hours_per_month") or HOURS_PER_MONTH)


def overtime_pay(hourly: float, hours: float, day_kind: str, rates: Optional[dict] = None) -> int:
    """加班費＝每小時工資 × 時數 × 公司倍率（工作日 1、假日 2）。"""
    mult = (rates or DEFAULT_RATES)["overtime_multiplier"]
    return _r(float(hourly) * float(hours or 0) * float(mult.get(day_kind, mult["工作日"])))


def base_pay_for(pay_type: str, base_amount: int, work_hours: float) -> int:
    """底薪那一格：月薪制＝底薪；時薪制＝時薪 × 手填時數；日薪制＝日薪 × （時數 ÷ 8）。"""
    base = int(base_amount or 0)
    if pay_type == "時薪":
        return _r(base * float(work_hours or 0))
    if pay_type == "日薪":
        return _r(base * float(work_hours or 0) / 8)
    return base


def line_totals(line: dict) -> dict:
    """應發／實發／公司總成本；輸入輸出都是整數元。"""
    g = lambda k: int(line.get(k) or 0)  # noqa: E731
    gross = g("base_pay") + g("meal_allowance") + g("overtime_pay") + g("bonus_pay") - g("leave_deduction")
    net = gross - g("labor_self") - g("health_self") - g("pension_self") - g("other_deduction")
    employer = gross + g("labor_employer") + g("health_employer") + g("pension_employer")
    return {"gross_pay": gross, "net_pay": net, "employer_total": employer}


def build_line(profile: dict, rates: dict, work_hours: float = 0.0, extras: Optional[dict] = None) -> dict:
    """主檔一列 ＋ 費率表 ＋ 手填 → 薪資單一列的全部數字。extras＝手改欄（LINE_EDITABLE）。"""
    ex = extras or {}
    pay_type = profile.get("pay_type") or "月薪"
    base_amount = int(profile.get("base_amount") or 0)
    hours = float(ex.get("work_hours", work_hours) or 0)
    ins = insurance_for(profile.get("labor_grade") or 0, profile.get("health_grade") or 0,
                        profile.get("dependents") or 0, profile.get("pension_self_rate") or 0, rates)
    line = {
        "pay_type": pay_type, "payroll_entity": profile.get("payroll_entity") or "公司",
        "base_amount": base_amount, "work_hours": hours,
        "base_pay": base_pay_for(pay_type, base_amount, hours),
        "meal_allowance": int(profile.get("meal_allowance") or 0),
        "overtime_pay": int(ex.get("overtime_pay") or 0),
        "bonus_pay": int(ex.get("bonus_pay") or 0),
        "leave_deduction": int(ex.get("leave_deduction") or 0),
        "other_deduction": int(ex.get("other_deduction") or 0),
        "labor_grade": int(profile.get("labor_grade") or 0), "health_grade": int(profile.get("health_grade") or 0),
        "dependents": int(profile.get("dependents") or 0),
        "pension_self_rate": float(profile.get("pension_self_rate") or 0),
        "note": ex.get("note") or "",
    }
    line.update({k: ins[k] for k in ("labor_self", "labor_employer", "health_self", "health_employer", "pension_self", "pension_employer")})
    line.update(line_totals(line))
    return line


def profile_as_of(profiles: Iterable[dict], month: str) -> Optional[dict]:
    """同一個人的多段主檔裡，`month`（YYYY-MM）適用哪一段：effective_from ≤ month 的最後一段。"""
    hit = None
    for p in sorted(profiles, key=lambda x: str(x.get("effective_from") or "")):
        if str(p.get("effective_from") or "") <= month:
            hit = p
    return hit


def check_month(month: str) -> str:
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-" or not (month[:4] + month[5:]).isdigit():
        raise ValueError("月份要像 2026-09")
    if not 1 <= int(month[5:]) <= 12:
        raise ValueError("月份要像 2026-09")
    return month


def next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def vocab(rates: Optional[dict] = None) -> dict:
    """給前端的字彙（鍵名是契約，加不刪）。"""
    r = rates or DEFAULT_RATES
    return {"pay_types": list(PAY_TYPES), "payroll_entities": list(PAYROLL_ENTITIES), "run_statuses": list(RUN_STATUSES),
            "day_kinds": list(DAY_KINDS), "line_editable": list(LINE_EDITABLE),
            "hours_per_month": r.get("hours_per_month", HOURS_PER_MONTH), "meal_tax_free": r.get("meal_tax_free", MEAL_TAX_FREE),
            "overtime_multiplier": dict(r.get("overtime_multiplier") or DEFAULT_RATES["overtime_multiplier"]),
            "legal_overtime_min": {"工作日前2小時": 1.34, "工作日第3-4小時": 1.67, "休息日前2小時": 1.34, "休息日第3-8小時": 1.67}}
