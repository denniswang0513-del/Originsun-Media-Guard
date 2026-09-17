"""payroll_logic.py — 薪資單的純規則（docs/PAYROLL_OVERTIME_PLAN.md §3）。無 I/O、無 DB。

一列薪資單怎麼算（owner 2026-09-17 拍板）：
    應發 gross_pay  = base_pay + meal_allowance + overtime_pay + bonus_pay − leave_deduction
    實發 net_pay    = gross_pay − labor_self − health_self − pension_self − other_deduction
    公司總成本      = gross_pay + labor_employer + health_employer + pension_employer
勞健保、勞退全部從「費率表」查（一年一份，DEFAULT_RATES 是系統帶的 2026 版；管理員在畫面改），
不再手算。時薪制 base_pay ＝ 時薪 × 手填時數；月薪制 base_pay ＝ 底薪。

加班費照勞基法（owner 2026-09-18「請讓所有的設定吻合勞基法」）：§24 工作日前 2 小時 ×1.34、再 2 小時 ×1.67；
休息日前 2 小時 ×1.34、第 3–8 小時 ×1.67、第 9–12 小時 ×2.67；§39 國定假日／例假日 8 小時內加發一日工資、超過的比照工作日延長。
基數＝每小時工資（月薪 ÷ 240 或時薪）。每日正常＋延長 ≤ 12 小時、每月 ≤ 46（勞資會議同意可到 54、三個月 ≤ 138）。
倍率與上限放費率表 `overtime`（DEFAULT_OVERTIME），管理員只能改「是否經勞資會議同意延長」與補休換算（法定 1:1，公司給假日 1:2）。
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional

PAY_TYPES = ("月薪", "時薪", "日薪")
PAYROLL_ENTITIES = ("公司", "代發")          # 代發＝股東自己的人，公司只是過帳（docs/CASHBOOK_CLASSIFICATION.md）
RUN_STATUSES = ("草稿", "已確認")
DAY_KINDS = ("工作日", "休息日", "國定假日", "例假日")   # §36／§37：週六休息日、週日例假日、假日表的國定假日；颱風假比照休息日
MIN_WAGE_MONTHLY_2026 = 29500                # 基本工資（2026）：月薪 29,500、時薪 196 —— 主檔低於這個只提醒不擋
MIN_WAGE_HOURLY_2026 = 196
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
    "min_wage_monthly": MIN_WAGE_MONTHLY_2026,
    "min_wage_hourly": MIN_WAGE_HOURLY_2026,
}

# 勞基法 §24／§32／§39 的加班規則（2026 現行）。tiers＝[[小時數, 倍率], …] 依序吃完，超過最後一段用最後一段的倍率。
DEFAULT_OVERTIME: Dict = {
    "workday_tiers": [[2, 1.34], [2, 1.67]],            # §24-1 工作日：前 2 h ×1.34、再 2 h ×1.67
    "restday_tiers": [[2, 1.34], [6, 1.67], [4, 2.67]], # §24-2 休息日：前 2 h ×1.34、3–8 h ×1.67、9–12 h ×2.67
    "holiday_day_wage_hours": 8,                        # §39 國定假日／例假日：8 h 內加發一日工資
    "holiday_over_tiers": [[2, 1.34], [2, 1.67]],       # 超過 8 h 的部分比照工作日延長工時
    "daily_max": {"工作日": 4, "休息日": 12, "國定假日": 12, "例假日": 12},   # §32-2 一日正常＋延長 ≤ 12 h
    "month_cap": 46,                                    # §32-2 每月延長工時 ≤ 46
    "month_cap_extended": 54,                           # 經勞資會議同意 → 54
    "quarter_cap": 138,                                 # 且三個月 ≤ 138
    "extended": False,                                  # 公司有勞資會議同意才勾（費率表頁）
    "holiday_multiplier": 2.0,                          # 公司規定：假日（休息日／國定假日／例假日）加班費一律 ×2（owner 2026-09-18「假日×2 要留著」）；
                                                        # 系統取「×2」與「法定」較高者，所以永遠不低於勞基法
    "credit_multiplier": {"工作日": 1, "休息日": 2, "國定假日": 2, "例假日": 2},   # §32-1 補休法定 1:1；假日 1:2 是公司給的（owner 2026-09-07）
}
DEFAULT_RATES["overtime"] = DEFAULT_OVERTIME
RULE_TEXT = "加班費：工作日照勞基法（前 2 小時 ×1.34、再 2 小時 ×1.67）；假日一律 ×2（公司規定；勞基法標準是休息日 ×1.34／1.67／2.67、國定假日加發一日工資，法定較高時取法定）。補休：工作日 1:1、假日 1:2。"
DEFAULT_RATES["rule_text"] = RULE_TEXT

_RATE_KEYS = tuple(k for k in DEFAULT_RATES if k not in ("year", "levels", "overtime", "rule_text"))


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
    out["overtime"] = _normalize_overtime(src.get("overtime"))
    out["rule_text"] = RULE_TEXT
    return out


def _tiers(raw, default):
    if not isinstance(raw, list):
        return [list(t) for t in default]
    tiers = []
    for t in raw:
        try:
            h, m = float(t[0]), float(t[1])
        except (TypeError, ValueError, IndexError):
            continue
        if h > 0 and m > 0:
            tiers.append([h, m])
    return tiers or [list(t) for t in default]


def _normalize_overtime(raw) -> dict:
    """費率表的 overtime 段：法定倍率與上限一律從 DEFAULT_OVERTIME 帶（畫面不給改）；只收 extended 與補休換算。"""
    src = raw if isinstance(raw, dict) else {}
    ot = {k: (dict(v) if isinstance(v, dict) else ([list(t) for t in v] if isinstance(v, list) else v)) for k, v in DEFAULT_OVERTIME.items()}
    ot["extended"] = bool(src.get("extended", False))
    try:
        hm = float(src.get("holiday_multiplier", ot["holiday_multiplier"]))
        ot["holiday_multiplier"] = hm if hm >= 1 else ot["holiday_multiplier"]
    except (TypeError, ValueError):
        pass
    cm = src.get("credit_multiplier")
    if isinstance(cm, dict):
        for k in DAY_KINDS:
            try:
                if cm.get(k) is not None and float(cm[k]) >= 1:   # §32-1：補休不得低於 1:1
                    ot["credit_multiplier"][k] = float(cm[k])
            except (TypeError, ValueError):
                pass
    return ot


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


def _tier_pay(hourly: float, hours: float, tiers: list) -> float:
    """依序吃各段：[[2, 1.34], [2, 1.67]] → 前 2 小時 ×1.34、再 2 小時 ×1.67；超過最後一段的用最後一段倍率。"""
    left, total, last = float(hours or 0), 0.0, 1.0
    for span, mult in tiers:
        take = min(left, float(span))
        total += take * float(mult)
        left -= take
        last = float(mult)
        if left <= 0:
            break
    if left > 0:
        total += left * last
    return hourly * total


def legal_overtime_pay(hourly: float, hours: float, day_kind: str, rates: Optional[dict] = None) -> int:
    """勞基法標準（§24／§39）：工作日與休息日分段倍率；國定假日／例假日 8 小時內加發一日工資（每小時工資 × 8），
    超過 8 小時的部分比照工作日延長工時。回整數元。"""
    ot = (rates or DEFAULT_RATES)["overtime"]
    h = float(hours or 0)
    if h <= 0:
        return 0
    if day_kind == "休息日":
        return _r(_tier_pay(hourly, h, ot["restday_tiers"]))
    if day_kind in ("國定假日", "例假日"):
        day_hours = float(ot["holiday_day_wage_hours"])
        base = hourly * day_hours
        over = max(0.0, h - day_hours)
        return _r(base + (_tier_pay(hourly, over, ot["holiday_over_tiers"]) if over else 0))
    return _r(_tier_pay(hourly, h, ot["workday_tiers"]))


def overtime_pay(hourly: float, hours: float, day_kind: str, rates: Optional[dict] = None) -> int:
    """實際發的加班費：工作日照勞基法；假日（休息日／國定假日／例假日）公司規定一律 ×2（owner 2026-09-18「假日×2 要留著」），
    但取「×2」與「法定」較高者 —— 永遠不低於勞基法（休息日第 9–12 小時法定 ×2.67、國定假日只做 3 小時法定仍給一日工資，這兩種法定較高）。"""
    ot = (rates or DEFAULT_RATES)["overtime"]
    legal = legal_overtime_pay(hourly, hours, day_kind, rates)
    if day_kind == "工作日":
        return legal
    company = _r(float(hourly) * float(hours or 0) * float(ot.get("holiday_multiplier") or 2))
    return max(company, legal)


def credit_hours_for(hours: float, day_kind: str, rates: Optional[dict] = None) -> float:
    """加班換補休的時數：法定 1:1（§32-1），公司給假日 1:2（費率表 credit_multiplier）。"""
    cm = (rates or DEFAULT_RATES)["overtime"]["credit_multiplier"]
    return round(float(hours or 0) * float(cm.get(day_kind, 1)), 2)


def min_wage_warnings(pay_type: str, base_amount: int, rates: Optional[dict] = None) -> list:
    """主檔低於基本工資 → 一句提醒（不擋：兼職工時制另有算法，owner 自己判斷）。"""
    r = rates or DEFAULT_RATES
    base = int(base_amount or 0)
    if pay_type == "月薪" and 0 < base < int(r.get("min_wage_monthly") or 0):
        return [f"月薪 {base:,} 低於 {r['year']} 年基本工資 {int(r['min_wage_monthly']):,}"]
    if pay_type == "時薪" and 0 < base < int(r.get("min_wage_hourly") or 0):
        return [f"時薪 {base} 低於 {r['year']} 年基本時薪 {int(r['min_wage_hourly'])}"]
    return []


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
            "overtime": dict(r.get("overtime") or DEFAULT_OVERTIME), "rule_text": r.get("rule_text", RULE_TEXT),
            "min_wage_monthly": r.get("min_wage_monthly"), "min_wage_hourly": r.get("min_wage_hourly")}
