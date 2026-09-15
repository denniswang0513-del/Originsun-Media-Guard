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
RECORD_TYPES = ("病假", "事假", "婚假", "喪假", "其他")     # 只記事實（年度上限提醒，不扣帳）；公假 2026-09-15 拿掉（owner：沒有公假）
ALL_LEAVE_TYPES = ("特休", "補休", "病假", "事假", "婚假", "喪假", "其他")
# 員工自助（/me/leave、工作台假勤卡、手機假勤）只開這三種（owner 2026-09-15「這裡改特休、補修就好；病假需要上傳文件」）；
# 其他假別（事假／婚假／喪假／其他）由管理員在人事管理代登。管理端仍認 ALL_LEAVE_TYPES。
SELF_SERVICE_TYPES = ("特休", "補休", "病假")
# 要附證明才准核准（規章：病假須提出相關證明）：員工送單時上傳，檔案放收據根目錄底下 `_假勤證明/{年月}/`。
PROOF_REQUIRED_TYPES = ("病假",)
REQUEST_STATUSES = ("待審", "已核准", "已退回", "已撤回", "消假待審")
CREDIT_STATUSES = ("待審", "可用", "展延", "結算", "拒絕")
CREDIT_KINDS = ("特休", "補休", "其他")
CREDIT_SOURCES = ("annual_auto", "overtime", "manual", "import")
HOLIDAY_KINDS = ("國定假日", "補班日", "颱風假")
PARTS = ("all", "am", "pm", "range")
PART_LABELS = {"all": "整天", "am": "上午", "pm": "下午", "range": "時段"}

HOURS_PER_DAY = 8

# 彈性外出（owner 2026-09-15 拍板）：每人每天 2 小時、自己登記不用核准、一筆最多 2 小時、同一天合計也最多 2 小時、不累積。
FLEX_OUT_MAX_MINUTES = 120
FLEX_OUT_RULE = "每日可彈性外出兩小時"


def hm_minutes(hhmm: str) -> int | None:
    """'10:30' → 630；壞格式回 None。"""
    try:
        h, m = str(hhmm or "").strip()[:5].split(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def hm_text(v) -> str:
    """'9:00'／' 9:00 '／'09:00:00' → '09:00'；壞格式回空字串。存進 DB 前一律過這支：
    🔴 存原字串的話 `ORDER BY start_time` 會把 '9:00' 排到 '10:00' 後面（字串比大小），清單與日曆的順序就亂了。"""
    m = hm_minutes(v)
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def flex_out_overlaps(existing, start_time: str, end_time: str) -> bool:
    """existing＝同一天已登記的 [(起, 迄)…]；新的這段跟任何一段重疊就 True。
    同一段時間登記兩次不會超過每天 2 小時的上限，但等於把額度白燒掉一份，而且日曆上會疊兩條。"""
    a, b = hm_minutes(start_time), hm_minutes(end_time)
    if a is None or b is None:
        return False
    for s0, e0 in existing or []:
        c, d = hm_minutes(s0), hm_minutes(e0)
        if c is None or d is None:
            continue
        if a < d and c < b:      # 半開區間：10:00–11:00 與 11:00–12:00 不算重疊
            return True
    return False


def flex_out_check(start_time: str, end_time: str, used_today: int = 0) -> tuple[int, str]:
    """回 (這筆的分鐘, 錯誤字串)；錯誤字串空＝可以登記。used_today＝同一天已登記的分鐘（一天合計也不能超過 2 小時）。"""
    a, b = hm_minutes(start_time), hm_minutes(end_time)
    if a is None or b is None:
        return 0, "時間格式要是 HH:MM"
    mins = b - a
    if mins <= 0:
        return 0, "結束要晚於開始"
    if mins > FLEX_OUT_MAX_MINUTES:
        return mins, "一次最多 2 小時，超過的請另外請假（特休／補休／事假）"
    if used_today + mins > FLEX_OUT_MAX_MINUTES:
        left = max(FLEX_OUT_MAX_MINUTES - used_today, 0)
        return mins, f"今天已登記 {used_today} 分鐘，剩 {left} 分鐘可外出（每天最多 2 小時）"
    return mins, ""
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
        "self_service_types": list(SELF_SERVICE_TYPES),
        "proof_required_types": list(PROOF_REQUIRED_TYPES),
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


def check_hours_step(hours) -> float:
    """時數要 > 0 且是 0.5 的倍數；不合就 raise ValueError（呼叫端轉 422）。回四捨五入到兩位的值。

    這條規則原本在送單、管理端改單、手開 credit 三處各寫一次條件與訊息，訊息還已經漂成兩種說法。
    """
    h = float(hours or 0)
    if h <= 0 or round(h * 2) != h * 2:
        raise ValueError("時數需大於 0，且以 0.5 小時為最小單位")
    return round(h, 2)


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


def day_off_fraction(marks) -> float:
    """一天休了多少（1＝整天、0.5＝半天）。marks＝那天的假 [{part, start_time, end_time}…]（routers.api_me._leave_mark 的形狀）。
    同一天多張單要**加總**（上午特休＋下午補休＝整天，週表要照這個鎖整欄），時段假照時數換算（10:00–11:00＝1h＝0.125 天），
    上限 1（重複送的單不會讓一天變成 1.5 天）。"""
    total = 0.0
    for m in marks or []:
        part = (m.get("part") or "all").strip() or "all"
        if part == "all":
            total += 1.0
        elif part in ("am", "pm"):
            total += 0.5
        elif part == "range":
            a, b = hm_minutes(m.get("start_time")), hm_minutes(m.get("end_time"))
            if a is not None and b is not None and b > a:
                total += (b - a) / 60 / HOURS_PER_DAY
    return min(round(total, 3), 1.0)


def leave_days_total(by_day: dict, holidays=None) -> float:
    """一段期間休了幾天：**只算工作日**（週末／國定假日不算、補班的週六算）。by_day＝{ISO 日期: marks}。
    🔴 不要改回「一個 part=='all' 算一天」：跨週末的喪假 9/14–9/20 會寫成 7 天，而板上只畫 5 欄。"""
    total = 0.0
    for iso, marks in (by_day or {}).items():
        d = as_date(iso)
        if not marks or (d and not is_workday(d, holidays)):
            continue
        total += day_off_fraction(marks)
    return round(total, 2)


MAX_BATCH_DATES = 31        # 一次最多挑幾天（owner 2026-09-15「一次挑好幾個不連續的日期」）


# ── 申請單（owner 2026-09-15 三步：日期算小時 → 自己挑要扣的假 → 一整張單送出、一次核准、核准前可編輯）────────
# 第 2 步的清單除了特休／補休的每一筆 credit，還有這幾種不走時數帳的假別；meta：要不要附證明、給不給薪、年上限（天）。
# 公假整個拿掉（owner 2026-09-15「我沒有公假 所以可以把公假都先拿掉」）；要用再加回 ALL_LEAVE_TYPES／RECORD_META。
PICKABLE_RECORD_TYPES = ("病假", "事假", "婚假", "喪假")
SICK_TYPE = "病假"
# 規章（owner 2026-09-15）：病假 1 天給薪、不扣假；2 天以上半薪 → 折抵＝總天數 ÷ 2（2 天扣 1、3 天扣 1.5、30 天扣 15）。
SICK_FREE_HOURS = HOURS_PER_DAY   # 1 天（含）以內：全薪、折抵 0
RECORD_META = {
    "病假": {"proof": True, "paid": "1 天給薪、之後半薪", "cap_days": SICK_CAP_DAYS},
    "事假": {"proof": False, "paid": "不給薪"},
    "婚假": {"proof": True, "paid": "給薪", "cap_days": 8},
    "喪假": {"proof": True, "paid": "給薪"},
}


def record_item_id(kind: str) -> str:
    """清單裡不走時數帳的假別用這個當 id（credit 用自己的 id）。"""
    return "type:" + kind


def fit_items(picks: list, needed_hours: float) -> tuple:
    """員工挑的假依順序把「需要的小時」填滿：picks＝[{id, kind, credit_id, available（None＝不限）, …}]。
    回 (takes, remain)：takes＝每筆多帶 take（真的扣幾小時），挑超過的最後那一筆只扣還需要的部分、後面的不扣（take 0）；
    remain＞0＝挑不夠。（owner：「挑了 3 天 8 小時、實際只要休 20 個小時，那需要有一天假剩下 4 小時」）"""
    remain = round(float(needed_hours or 0), 2)
    takes = [dict(p, take=0.0) for p in picks]

    def _cap(p):
        a = p.get("available")
        return float("inf") if a is None else float(a)

    # 病假折抵（owner 2026-09-15「如果休的是病假 要可以選其他假折抵時數 只是照規則扣」）：
    # 挑了病假又挑了會扣帳的假（特休／補休）→ 系統照規章自動分：病假先留 1 天（給薪），其餘先用折抵的假吃（照挑的順序＝全薪），
    # 吃不完的再回病假（半薪）。沒挑病假、或只挑病假沒挑折抵的假 → 照一般順序填（下面 order 就是原順序）。
    sick_i = next((i for i, p in enumerate(picks) if p.get("kind") == SICK_TYPE and not p.get("credit_id")), None)
    has_credit = any(p.get("credit_id") for p in picks)
    if sick_i is not None and has_credit:
        # 病假折抵：1 天以內全薪＝折抵 0；超過 1 天半薪＝折抵一半（總時數 ÷ 2）。折抵的假先吃、上限就是這一半，
        # 其餘（含另一半病假、與折抵的假吃不完的）都回病假。
        offset_cap = 0.0 if remain <= float(SICK_FREE_HOURS) else round(remain / 2.0, 2)
        budget = offset_cap
        for i in [j for j in range(len(picks)) if j != sick_i]:
            p = takes[i]
            take = round(min(_cap(p) - p["take"], budget, remain), 2)
            if take > 0:
                p["take"] = round(p["take"] + take, 2)
                remain = round(remain - take, 2)
                budget = round(budget - take, 2)
        takes[sick_i]["take"] = round(min(_cap(takes[sick_i]), remain), 2)
        remain = round(remain - takes[sick_i]["take"], 2)
        return takes, max(remain, 0.0)

    for i in range(len(picks)):
        p = takes[i]
        room = round(_cap(p) - p["take"], 2)
        take = round(min(room, remain), 2)
        if take > 0:
            p["take"] = round(p["take"] + take, 2)
            remain = round(remain - take, 2)
    return takes, max(remain, 0.0)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def plan_children(day_slots: list, takes: list) -> list:
    """把挑好的假依日期順序鋪到每一天：day_slots＝[{date, hours, part, start_time, end_time}]（試算過、沒錯的天）。
    同一天同一種假（就算跨兩筆 credit）＝一張子單、allocations 帶兩筆；換了一種假才拆成第二張：整天剛好 4／4 拆上午／下午，
    其他照時段接續（上午／整天從 09:00 起、下午從 13:00 起）。回 [{date, kind, hours, part, start_time, end_time, allocations:[[credit_id, h]…]}]。"""
    pool = [dict(t, left=float(t.get("take") or 0)) for t in takes if float(t.get("take") or 0) > 0]
    out = []
    for slot in day_slots:
        need = float(slot["hours"] or 0)
        segs = []                                   # [(kind, credit_id|None, h)]
        for t in pool:
            if need <= 1e-9:
                break
            h = round(min(t["left"], need), 2)
            if h <= 0:
                continue
            segs.append((t["kind"], t.get("credit_id"), h))
            t["left"] = round(t["left"] - h, 2)
            need = round(need - h, 2)
        # 同種假合併成一張
        merged = []
        for kind, cid, h in segs:
            if merged and merged[-1]["kind"] == kind:
                merged[-1]["hours"] = round(merged[-1]["hours"] + h, 2)
                if cid:
                    merged[-1]["allocations"].append([cid, h])
            else:
                merged.append({"kind": kind, "hours": h, "allocations": [[cid, h]] if cid else []})
        part = slot.get("part") or "all"
        if len(merged) == 1:
            m = merged[0]
            out.append({"date": slot["date"], "kind": m["kind"], "hours": m["hours"], "part": part,
                        "start_time": slot.get("start_time") if part == "range" else None,
                        "end_time": slot.get("end_time") if part == "range" else None, "allocations": m["allocations"]})
            continue
        if part == "all" and len(merged) == 2 and merged[0]["hours"] == 4 and merged[1]["hours"] == 4:
            for m, p in zip(merged, ("am", "pm")):
                out.append({"date": slot["date"], "kind": m["kind"], "hours": 4.0, "part": p, "start_time": None, "end_time": None,
                            "allocations": m["allocations"]})
            continue
        base = {"pm": 13 * 60, "range": parse_hhmm(slot.get("start_time")) or 9 * 60}.get(part, 9 * 60)
        cur = base
        for m in merged:
            end = cur + int(round(m["hours"] * 60))
            out.append({"date": slot["date"], "kind": m["kind"], "hours": m["hours"], "part": "range",
                        "start_time": _hhmm(cur), "end_time": _hhmm(end), "allocations": m["allocations"]})
            cur = end
    return out


def normalize_dates(dates) -> list:
    """挑日期送單：去重、排序、都要是 YYYY-MM-DD；空或超過 MAX_BATCH_DATES raise ValueError。回 ISO 字串清單。"""
    out = set()
    for d in dates or []:
        v = as_date(d)
        if v is None:
            raise ValueError(f"日期格式錯：{d}")
        out.add(v.isoformat())
    if not out:
        raise ValueError("至少挑一天")
    if len(out) > MAX_BATCH_DATES:
        raise ValueError(f"一次最多 {MAX_BATCH_DATES} 天")
    return sorted(out)


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
        granted = as_date(_g(c, "granted_on"))
        if granted is not None and granted > on:          # 生效日還沒到（例如滿半年才給的那 3 天）
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
