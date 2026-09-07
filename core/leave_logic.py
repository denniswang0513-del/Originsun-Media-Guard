"""core/leave_logic.py — 假勤（時數帳＋申請單）的純規則：無 DB、無 I/O，全部可單元測試。

規劃 docs/LEAVE_PLAN.md（§7 是 API 契約）。四件事住這裡：
  1. 字彙：假別／狀態／半天時段（前端從 `/api/v1/me/leave/summary.vocab` 拿，不寫死）
  2. 時數：工作日怎麼算（週末、假日表、補班日）、一張單幾小時、勞基法特休級距
  3. 時數帳：FIFO 分配（先到期先扣）、餘額、加班換補休（假日 ×2）
  4. 規章：最晚一週前（黃字）、消假前兩天（撤回／申請消假／颱風假鎖）、Google 日曆事件長相

🔴 `core.hr_logic.is_workday` 是工時參考用的（只看週末），請假扣天一律用這裡的 `is_workday(d, holidays)`。
🔴 `core.hr_logic.LEAVE_TYPES / LEAVE_STATUSES` 是舊前端（hr_leave.js 的常數）還在比對的鏡射，刻意不動；
   新端點認的是這裡的 ALL_LEAVE_TYPES / REQUEST_STATUSES（超集）。
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta

from core.hr_logic import LEAVE_STATUSES as _LEGACY_STATUSES, LEAVE_TYPES as _LEGACY_TYPES, tw_day

# ── 字彙（§7.1）────────────────────────────────────────────────────────────
LEDGER_TYPES = ("特休", "補休")                                  # 走時數帳（核准時 FIFO 扣 credit）
RECORD_TYPES = ("病假", "事假", "公假", "婚假", "喪假", "其他")     # 只記事實（年度上限提醒，不扣帳）
ALL_LEAVE_TYPES = ("特休", "補休", "病假", "事假", "公假", "婚假", "喪假", "其他")
REQUEST_STATUSES = ("待審", "已核准", "已退回", "已撤回", "消假待審")
CREDIT_STATUSES = ("待審", "可用", "展延", "結算", "拒絕")
CREDIT_KINDS = ("特休", "補休", "其他")
CREDIT_SOURCES = ("annual_auto", "overtime", "manual", "import")
HOLIDAY_KINDS = ("國定假日", "補班日", "颱風假")
PARTS = ("all", "am", "pm", "range")
PART_LABELS = {"all": "整天", "am": "上午", "pm": "下午", "range": "時段"}

HOURS_PER_DAY = 8
HOLIDAY_OT_MULTIPLIER = 2       # 假日（週末／國定假日／颱風假）加班 1:2（owner 2026-09-07）
NOTICE_DAYS = 7                 # 規章：最晚一週前提出；不足黃字提醒不擋
CANCEL_FREE_DAYS = 2            # 規章：開始前 ≥2 天可自己撤回，<2 天只能申請消假
SICK_CAP_DAYS = 30              # 勞基法：未住院病假一年合計 30 天
ANNUAL_CAP_DAYS = 30            # 特休上限（10 年以上每年加 1 日至 30 日）
EXPIRING_WINDOW_DAYS = 60       # 「快到期」的視窗（summary.expiring）

# 半天的時段（日曆事件用；4 小時）
AM_SPAN = ("09:00", "13:00")
PM_SPAN = ("14:00", "18:00")
TAIPEI_TZ = "Asia/Taipei"

# 「還占著期間」的申請單狀態：重疊檢查、同期間誰休、保留時數都看這三個
ACTIVE_STATUSES = ("待審", "已核准", "消假待審")

# 舊常數（core.hr_logic）必須是新字彙的子集 —— import 時就炸，不要等到 422
assert set(_LEGACY_TYPES) <= set(ALL_LEAVE_TYPES)
assert set(_LEGACY_STATUSES) <= set(REQUEST_STATUSES)


class InsufficientHours(Exception):
    """時數帳不夠：short 是差幾小時（給 422 訊息用）。"""

    def __init__(self, short: float):
        self.short = round(float(short), 2)
        super().__init__(f"時數不足，差 {self.short:g} 小時")


def vocab() -> dict:
    """前端讀的字彙（summary.vocab）；鍵名是契約，加不刪。"""
    return {
        "leave_types": list(ALL_LEAVE_TYPES),
        "ledger_types": list(LEDGER_TYPES),
        "record_types": list(RECORD_TYPES),
        "request_statuses": list(REQUEST_STATUSES),
        "credit_statuses": list(CREDIT_STATUSES),
        "credit_kinds": list(CREDIT_KINDS),
        "credit_sources": list(CREDIT_SOURCES),
        "holiday_kinds": list(HOLIDAY_KINDS),
        "parts": list(PARTS),
        "part_labels": dict(PART_LABELS),
        "hours_per_day": HOURS_PER_DAY,
        "holiday_ot_multiplier": HOLIDAY_OT_MULTIPLIER,
        "notice_days": NOTICE_DAYS,
        "cancel_free_days": CANCEL_FREE_DAYS,
        "sick_cap_days": SICK_CAP_DAYS,
    }


# ── 小工具 ──────────────────────────────────────────────────────────────────

def as_date(v) -> date | None:
    """date／datetime／'YYYY-MM-DD' → date；空回 None。datetime 走 tw_day（timestamptz 讀回是 UTC）。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return tw_day(v)
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def hours_to_days(hours) -> float:
    """8 小時＝1 天；顯示用（兩位小數、去尾零）。"""
    return round(float(hours or 0) / HOURS_PER_DAY, 2)


def fmt_days(hours) -> str:
    d = hours_to_days(hours)
    return f"{d:g}"


def parse_hhmm(raw) -> int | None:
    """'HH:MM' → 當天第幾分鐘；壞格式回 None。"""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(raw or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59 or (h == 24 and mi > 0):
        return None
    return h * 60 + mi


def holiday_kind(d: date, holidays) -> str | None:
    """假日表查一天：holidays 可以是 {date: kind}、{'YYYY-MM-DD': kind} 或 {date: {'kind':…}}。"""
    if not holidays:
        return None
    v = holidays.get(d)
    if v is None:
        v = holidays.get(d.isoformat())
    if isinstance(v, dict):
        v = v.get("kind")
    return v or None


# ── 時數（§7.3）─────────────────────────────────────────────────────────────

def annual_days_for(hire_date, on) -> int:
    """勞基法 §38 特休級距（規章那張表），以 `on` 當天的年資算：
    6 個月～1 年 3 日；1～2 年 7 日；2～3 年 10 日；3～5 年 14 日；5～10 年 15 日；
    10 年以上每多一年加 1 日，至 30 日為止（滿 10 年＝16）。未滿 6 個月 0。"""
    h, o = as_date(hire_date), as_date(on)
    if not h or not o or o < h:
        return 0
    months = (o.year - h.year) * 12 + (o.month - h.month) - (1 if o.day < h.day else 0)
    years = months // 12
    if months < 6:
        return 0
    if years < 1:
        return 3
    if years < 2:
        return 7
    if years < 3:
        return 10
    if years < 5:
        return 14
    if years < 10:
        return 15
    return min(ANNUAL_CAP_DAYS, 16 + (years - 10))


def is_workday(d: date, holidays=None) -> bool:
    """週一～五且不在假日表；補班日的週六算工作日；國定假日／颱風假不算。"""
    kind = holiday_kind(d, holidays)
    if kind == "補班日":
        return True
    if kind in ("國定假日", "颱風假"):
        return False
    return d.weekday() < 5


def workdays_between(start: date, end: date, holidays=None) -> list:
    """[start, end] 之間的工作日清單（含首尾）。"""
    out = []
    d = start
    while d <= end:
        if is_workday(d, holidays):
            out.append(d)
        d += timedelta(days=1)
    return out


def working_hours(start, end, part="all", start_time=None, end_time=None, holidays=None) -> float:
    """一張單的時數。all：工作日數 × 8；am／pm：4（限單日，且那天要是工作日）；
    range：迄−起四捨五入到 0.5 小時、上限 8（限單日）。規則錯 raise ValueError（給 422／bad_range）。"""
    s, e = as_date(start), as_date(end)
    if not s or not e:
        raise ValueError("起訖日期必填")
    if e < s:
        raise ValueError("迄日不可早於起日")
    part = (part or "all").strip() or "all"
    if part not in PARTS:
        raise ValueError(f"part 需為：{'/'.join(PARTS)}")
    if part == "all":
        return float(len(workdays_between(s, e, holidays)) * HOURS_PER_DAY)
    if s != e:
        raise ValueError("上午／下午／時段只能單日")
    if not is_workday(s, holidays):
        return 0.0
    if part in ("am", "pm"):
        return HOURS_PER_DAY / 2
    a, b = parse_hhmm(start_time), parse_hhmm(end_time)
    if a is None or b is None:
        raise ValueError("時段需填起訖時間（HH:MM）")
    if b <= a:
        raise ValueError("結束時間需晚於開始時間")
    h = round((b - a) / 60 * 2) / 2
    return float(min(HOURS_PER_DAY, max(0.5, h)))


# ── 時數帳（§7.3）───────────────────────────────────────────────────────────

def _g(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def credit_remaining(credit) -> float:
    return round(float(_g(credit, "hours", 0) or 0) - float(_g(credit, "used", 0) or 0), 2)


def usable_credits(credits, kind=None, on=None) -> list:
    """可扣的 credit：status=可用、未到期（expires_on 空＝永不）、還有餘量；先到期先排（空到期最後），同到期先發先扣。"""
    on = as_date(on) or date.today()
    out = []
    for c in credits:
        if _g(c, "status") != "可用":
            continue
        if kind and _g(c, "kind") != kind:
            continue
        exp = as_date(_g(c, "expires_on"))
        if exp is not None and exp < on:
            continue
        if credit_remaining(c) <= 0:
            continue
        out.append(c)
    out.sort(key=lambda c: (as_date(_g(c, "expires_on")) is None,
                            as_date(_g(c, "expires_on")) or date.max,
                            as_date(_g(c, "granted_on")) or date.min))
    return out


def allocate(credits, hours, on=None) -> list:
    """FIFO 分配：回 [(credit_id, hours)]；不夠 raise InsufficientHours(short)。
    credits 每筆要有 id／hours／status／expires_on／granted_on，`used`＝已被別張單扣掉的（沒給算 0）。"""
    need = round(float(hours or 0), 2)
    if need <= 0:
        return []
    out = []
    for c in usable_credits(credits, on=on):
        if need <= 0:
            break
        take = min(need, credit_remaining(c))
        out.append((_g(c, "id"), round(take, 2)))
        need = round(need - take, 2)
    if need > 0:
        raise InsufficientHours(need)
    return out


def balance(credits, allocations=None, pending_hours=0.0, kind=None, on=None) -> dict:
    """{available, reserved, expiring:[{hours, expires_on, reason}], expiring_soon}。
    available＝Σ 可用且未到期的 (hours − used)；used 沒放在 credit 上時從 allocations（credit_id, hours）補算。
    reserved＝待審單占用的小時（呼叫端算好傳進來）。不存快照，每次重算。"""
    on = as_date(on) or date.today()
    used: dict = {}
    for a in allocations or []:
        cid, h = (a[0], a[1]) if isinstance(a, (tuple, list)) else (_g(a, "credit_id"), _g(a, "hours", 0))
        used[cid] = used.get(cid, 0.0) + float(h or 0)
    rows = []
    for c in credits:
        d = dict(c) if isinstance(c, dict) else {k: getattr(c, k, None) for k in
                                                ("id", "kind", "hours", "status", "expires_on", "granted_on", "reason", "used")}
        if d.get("used") is None:
            d["used"] = used.get(d.get("id"), 0.0)
        rows.append(d)
    live = usable_credits(rows, kind=kind, on=on)
    available = round(sum(credit_remaining(c) for c in live), 2)
    horizon = on + timedelta(days=EXPIRING_WINDOW_DAYS)
    expiring = [{"hours": credit_remaining(c), "expires_on": as_date(c["expires_on"]).isoformat(),
                 "reason": c.get("reason") or ""}
                for c in live if as_date(c.get("expires_on")) is not None and as_date(c["expires_on"]) <= horizon]
    return {"available": available, "reserved": round(float(pending_hours or 0), 2),
            "expiring": expiring, "expiring_soon": round(sum(x["hours"] for x in expiring), 2)}


def overtime_credit_hours(hours, on, holidays=None) -> float:
    """加班換補休：平日 1:1、假日（週末／國定假日／颱風假）1:2。"""
    h = float(hours or 0)
    d = as_date(on)
    if d is not None and not is_workday(d, holidays):
        return round(h * HOLIDAY_OT_MULTIPLIER, 2)
    return round(h, 2)


# ── 規章（§1.1 請假時間點／消假）────────────────────────────────────────────

def notice_warning(start, today=None) -> str | None:
    """距開始不足 NOTICE_DAYS 天 → 黃字提醒文字；夠回 None。"""
    s = as_date(start)
    t = as_date(today) or date.today()
    if s is None:
        return None
    gap = (s - t).days
    if gap >= NOTICE_DAYS:
        return None
    if gap < 0:
        return "假期已經開始或過去（規章：最晚一週前提出，請與主管說明）"
    return f"距開始只剩 {gap} 天（規章：最晚一週前提出，請在事由簡述原因）"


def cancel_mode(start, today=None, holidays=None) -> str:
    """已核准的單能不能自己撤：free（≥2 天，直接撤回）／apply（<2 天，申請消假由主管決定）／
    locked（今天是颱風假公告日，不可消）。"""
    t = as_date(today) or date.today()
    if holiday_kind(t, holidays) == "颱風假":
        return "locked"
    s = as_date(start)
    if s is None:
        return "apply"
    return "free" if (s - t).days >= CANCEL_FREE_DAYS else "apply"


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    """兩張單的日期區間（含首尾）有沒有碰到。"""
    a0, a1 = as_date(a_start), as_date(a_end) or as_date(a_start)
    b0, b1 = as_date(b_start), as_date(b_end) or as_date(b_start)
    if not (a0 and b0):
        return False
    return a0 <= b1 and b0 <= a1


def in_crew(crew: list, staff_id: str, name: str) -> bool:
    """場次 crew 含這個人：比 staff_id，退回比姓名（舊場次只存名字）。"""
    for c in crew or []:
        if c.get("staff_id") and c["staff_id"] == staff_id:
            return True
        if not c.get("staff_id") and name and c.get("name") == name:
            return True
    return False


# ── Google 日曆事件（二期接線；形狀同 core.shoot_logic.event_body）────────────

def leave_event_body(req: dict, staff_name: str = "", link: str = "") -> dict:
    """`休假｜蔡念栩（特休 1 天）`；整天用 date（結束日＋1，Google 是開區間），半天／時段用 dateTime＋Asia/Taipei。"""
    name = (staff_name or req.get("staff_name") or "").strip()
    hours = float(req.get("hours") or 0)
    part = req.get("part") or "all"
    summary = f"休假｜{name}（{req.get('leave_type') or '假'} {fmt_days(hours)}天）"
    start_d = as_date(req.get("start_date"))
    end_d = as_date(req.get("end_date")) or start_d
    if part == "all":
        start = {"date": start_d.isoformat()}
        end = {"date": (end_d + timedelta(days=1)).isoformat()}
    else:
        if part == "am":
            st, et = AM_SPAN
        elif part == "pm":
            st, et = PM_SPAN
        else:
            st, et = (req.get("start_time") or "").strip(), (req.get("end_time") or "").strip()
        start = {"dateTime": f"{start_d.isoformat()}T{st}:00", "timeZone": TAIPEI_TZ}
        end = {"dateTime": f"{start_d.isoformat()}T{et or st}:00", "timeZone": TAIPEI_TZ}
    lines = [f"假別：{req.get('leave_type') or ''}", f"時數：{hours:g} 小時（{fmt_days(hours)} 天）"]
    if part != "all":
        lines.append("時段：" + (PART_LABELS.get(part, part) if part != "range"
                                else f"{req.get('start_time') or ''}–{req.get('end_time') or ''}"))
    if req.get("reason"):
        lines.append("事由：" + str(req["reason"]))
    if link:
        lines.append("系統：" + link)
    return {
        "summary": summary,
        "description": "\n".join(lines),
        "start": start, "end": end,
        "extendedProperties": {"private": {"originsun_leave_id": req.get("id") or ""}},
    }


# ── 行政院行事曆 CSV（§7.5 /hr/holidays/import）────────────────────────────

_TRUE = {"2", "1", "是", "yes", "y", "true"}
_FALSE = {"0", "否", "no", "n", "false"}


def _pick_col(headers: list, *needles: str) -> int | None:
    for i, h in enumerate(headers):
        hl = h.strip().lower().lstrip("\ufeff")
        if any(n in hl for n in needles):
            return i
    return None


def _parse_gov_date(raw: str) -> date | None:
    s = (raw or "").strip().replace("/", "-").replace(".", "-")
    if re.fullmatch(r"\d{8}", s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_gov_calendar_csv(text: str) -> list:
    """人事總處「政府行政機關辦公日曆表」CSV → [{date, name, kind}]。
    欄名容錯（西元日期／date、是否放假／isholiday、備註／name／description）；
    平日「是否放假=2」→ 國定假日；週六「是否放假=0」→ 補班日；週末放假與平日上班略過。"""
    text = (text or "").lstrip("\ufeff").strip()
    if not text:
        return []
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    headers = rows[0]
    ci_date = _pick_col(headers, "日期", "date")
    ci_flag = _pick_col(headers, "放假", "isholiday", "holiday")
    ci_name = _pick_col(headers, "備註", "name", "description", "說明")
    if ci_date is None or ci_flag is None:
        raise ValueError("看不懂欄位：需要「西元日期」與「是否放假」欄")
    out = []
    for r in rows[1:]:
        if len(r) <= max(ci_date, ci_flag):
            continue
        d = _parse_gov_date(r[ci_date])
        if d is None:
            continue
        flag = r[ci_flag].strip().lower()
        name = (r[ci_name].strip() if ci_name is not None and len(r) > ci_name else "")
        if flag in _TRUE and d.weekday() < 5:
            out.append({"date": d, "name": name or "國定假日", "kind": "國定假日"})
        elif flag in _FALSE and d.weekday() == 5:
            out.append({"date": d, "name": name or "補班日", "kind": "補班日"})
    return out
