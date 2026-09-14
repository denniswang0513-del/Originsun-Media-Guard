"""core/schedule_logic.py — 行事曆「工作登記」（排班）的純規則（docs/CALENDAR_PLAN.md §4.1）。無 I/O。

字彙（種類／狀態／時段）、人員清洗（內部人 staff_id、外部人員只有名字）、衝突判定（同人同日：請假／別場拍攝／
已排工作）、Google 事件形狀（工作登記／里程碑／請假，同 core.shoot_logic.event_body 的規矩）、
「做了 ✓」→ 工時列形狀、六類別顏色（Google 的 11 個 colorId，系統畫面用同一張表）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from core.hr_logic import HOURS_PER_WORKDAY
from core.leave_logic import as_date, leave_event_body
from core.shoot_logic import TAIPEI_TZ, overlaps

# ── 字彙 ──
KINDS = ("work", "meeting", "out", "other")
KIND_LABELS = {"work": "工作", "meeting": "會議", "out": "外出", "other": "其他"}
STATUSES = ("planned", "done", "cancelled")
PLANNED, DONE, CANCELLED = STATUSES
#: 上午／下午快捷鈕只是把時間填進去；存的仍是 start_time／end_time
SLOTS = {"am": ("09:00", "13:00"), "pm": ("13:00", "18:00")}
SLOT_LABELS = {"all": "全天", "am": "上午", "pm": "下午", "custom": "自訂"}

# ── 顏色：Google 日曆事件的 11 個 colorId（不能自訂色碼）；系統畫面吃同一張表（§3.3）──
GOOGLE_COLORS = {
    "1": ("薰衣草", "#7986cb"), "2": ("鼠尾草", "#33b679"), "3": ("葡萄", "#8e24aa"), "4": ("火鶴", "#e67c73"),
    "5": ("香蕉", "#f6c026"), "6": ("橘子", "#f5511d"), "7": ("孔雀", "#039be5"), "8": ("石墨", "#616161"),
    "9": ("藍莓", "#3f51b5"), "10": ("羅勒", "#0b8043"), "11": ("番茄", "#d60000"),
}
COLOR_KINDS = ("shoot", "work", "meeting", "out", "milestone", "leave")
COLOR_KIND_LABELS = {"shoot": "拍攝", "work": "工作", "meeting": "會議", "out": "外出", "milestone": "里程碑", "leave": "休假"}
DEFAULT_COLORS = {"shoot": "6", "work": "9", "meeting": "7", "out": "10", "milestone": "3", "leave": "8"}


def color_map(raw) -> dict:
    """settings `google_calendar.colors` → 六類別都有值的表（不合法的 colorId 退預設）。"""
    out = dict(DEFAULT_COLORS)
    for k, v in (raw or {}).items() if isinstance(raw, dict) else []:
        if k in out and str(v) in GOOGLE_COLORS:
            out[k] = str(v)
    return out


def color_hex(colors: dict, kind: str) -> str:
    return GOOGLE_COLORS.get(colors.get(kind, DEFAULT_COLORS.get(kind, "9")), ("", "#3f51b5"))[1]


# ── 人員 ──

def attendee_norm(raw) -> list:
    """清洗 attendees：名字必填（外部人員只有名字）、staff_id 字串、role／contact 去頭尾；同一人（staff_id，
    沒有就名字）只留一個。回 `[{staff_id, name, role, external, contact}]`。"""
    out, seen = [], set()
    for a in (raw if isinstance(raw, list) else []):
        if not isinstance(a, dict):
            continue
        sid = str(a.get("staff_id") or "").strip()
        name = str(a.get("name") or "").strip()[:64]
        if not name and not sid:
            continue
        key = sid or ("name:" + name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"staff_id": sid, "name": name, "role": str(a.get("role") or "").strip()[:32],
                    "external": (not sid) or bool(a.get("external")), "contact": str(a.get("contact") or "").strip()[:64]})
    return out


def attendee_names(attendees) -> list:
    return [a.get("name") for a in (attendees or []) if a.get("name")]


def is_attendee(attendees, staff_id: str, name: str) -> bool:
    """本人在不在人員裡：認 staff_id，退回比名字（外部人員／舊列）。"""
    for a in attendees or []:
        if (staff_id and a.get("staff_id") == staff_id) or (name and a.get("name") == name):
            return True
    return False


def slot_times(slot: str, start_time: str = "", end_time: str = "") -> tuple:
    """上午／下午 → 固定時段；自訂 → 照給的；全天 → 空。"""
    if slot in SLOTS:
        return SLOTS[slot]
    if slot == "custom":
        return (start_time or "").strip()[:5], (end_time or "").strip()[:5]
    return "", ""


def slot_of(start_time: str, end_time: str) -> str:
    """反推：時段快捷鈕該亮哪一顆。"""
    st, et = (start_time or "")[:5], (end_time or "")[:5]
    if not st and not et:
        return "all"
    for k, (a, b) in SLOTS.items():
        if (st, et) == (a, b):
            return k
    return "custom"


def hours_between(start_time: str, end_time: str) -> float:
    """'09:00'–'13:00' → 4.0；空或反向 → 0。"""
    try:
        a = datetime.strptime(start_time[:5], "%H:%M")
        b = datetime.strptime(end_time[:5], "%H:%M")
    except (TypeError, ValueError):
        return 0.0
    mins = (b - a).total_seconds() / 60
    return round(mins / 60, 2) if mins > 0 else 0.0


# ── 衝突：只提醒不擋（owner）──

def conflicts(attendees, d0, d1, leaves, shoots, schedules, exclude_id: str = "") -> list:
    """同人同日：已核准的假／別場拍攝的 crew／已排的工作。
    `leaves`＝[{staff_name, staff_id, start_date, end_date, leave_type}]、`shoots`＝[{id, title, date, end_date, crew:[{name,staff_id}], status}]、
    `schedules`＝[{id, title, date, end_date, attendees:[…], status}]。外部人員用名字比。回 [{name, kind, what}]。"""
    d0 = as_date(d0)
    d1 = as_date(d1) or d0
    out = []
    if not d0:
        return out
    for a in attendee_norm(attendees):
        sid, name = a["staff_id"], a["name"]
        for lv in leaves or []:
            same = (sid and lv.get("staff_id") == sid) or (name and lv.get("staff_name") == name)
            if same and overlaps(d0, d1, as_date(lv.get("start_date")), as_date(lv.get("end_date"))):
                out.append({"name": name, "kind": "leave", "what": f"{name} 那天請假（{lv.get('leave_type') or '假'}）"})
        for sh in shoots or []:
            if sh.get("status") == "取消":
                continue
            if is_attendee(sh.get("crew") or [], sid, name) and overlaps(d0, d1, as_date(sh.get("date")), as_date(sh.get("end_date"))):
                out.append({"name": name, "kind": "shoot", "what": f"{name} 那天已排拍攝：{sh.get('title') or ''}"})
        for sc in schedules or []:
            if sc.get("id") == exclude_id or sc.get("status") != PLANNED:
                continue
            if is_attendee(sc.get("attendees") or [], sid, name) and overlaps(d0, d1, as_date(sc.get("date")), as_date(sc.get("end_date"))):
                out.append({"name": name, "kind": "schedule", "what": f"{name} 那天已排：{sc.get('title') or ''}"})
    return out


# ── Google 事件形狀（同 core.shoot_logic.event_body）──

def _span(start_d: date, end_d, st: str, et: str) -> tuple:
    end_d = end_d or start_d
    if st:
        if et and et < st and end_d == start_d:
            end_d = end_d + timedelta(days=1)      # 跨午夜
        return ({"dateTime": f"{start_d.isoformat()}T{st}:00", "timeZone": TAIPEI_TZ},
                {"dateTime": f"{end_d.isoformat()}T{et or st}:00", "timeZone": TAIPEI_TZ})
    return {"date": start_d.isoformat()}, {"date": (end_d + timedelta(days=1)).isoformat()}


def _ext(kind: str, ident: str) -> dict:
    return {"private": {"originsun_kind": kind, "originsun_id": ident or ""}}


def event_body_schedule(row: dict, link: str = "", colors: dict | None = None) -> dict:
    """`工作｜標題（案名）`；會議／外出照種類。做完的標題前加 ✓（事件留著）。"""
    colors = colors or DEFAULT_COLORS
    kind = row.get("kind") if row.get("kind") in KINDS else "work"
    label = KIND_LABELS[kind]
    summary = f"{label}｜{(row.get('title') or '').strip()}" + (f"（{row['project_name']}）" if row.get("project_name") else "")
    if row.get("status") == DONE:
        summary = "✓ " + summary
    start, end = _span(as_date(row["date"]), as_date(row.get("end_date")), (row.get("start_time") or "")[:5], (row.get("end_time") or "")[:5])
    lines = []
    people = []
    for a in row.get("attendees") or []:
        s = a.get("name") or ""
        if a.get("role"):
            s += f"（{a['role']}）"
        if a.get("external"):
            s += "・外部" + (f" {a['contact']}" if a.get("contact") else "")
        people.append(s)
    if people:
        lines.append("人員：" + "、".join(people))
    if row.get("location_text"):
        lines.append("地點：" + row["location_text"])
    if row.get("notes"):
        lines.append("備註：" + row["notes"])
    if link:
        lines.append("系統：" + link)
    color_kind = kind if kind in COLOR_KINDS else "work"
    return {"summary": summary, "location": row.get("location_text") or "", "description": "\n".join(lines),
            "start": start, "end": end, "colorId": colors.get(color_kind, DEFAULT_COLORS["work"]),
            "extendedProperties": _ext("schedule", row.get("id") or "")}


def event_body_milestone(m: dict, project_name: str = "", link: str = "", colors: dict | None = None) -> dict:
    """全天 `里程碑｜案名：標題`；完成加 ✓。"""
    colors = colors or DEFAULT_COLORS
    d = as_date(m.get("due_date")) or as_date(m.get("week_start"))
    summary = f"里程碑｜{project_name + '：' if project_name else ''}{m.get('title') or ''}"
    if m.get("done") or m.get("status") == "done":
        summary = "✓ " + summary
    start, end = _span(d, None, "", "")
    lines = []
    if m.get("assignee_name"):
        lines.append("負責：" + m["assignee_name"])
    if m.get("note"):
        lines.append("備註：" + m["note"])
    if link:
        lines.append("系統：" + link)
    return {"summary": summary, "description": "\n".join(lines), "start": start, "end": end,
            "colorId": colors.get("milestone", DEFAULT_COLORS["milestone"]), "extendedProperties": _ext("milestone", m.get("id") or "")}


def event_body_leave(req: dict, staff_name: str = "", link: str = "", colors: dict | None = None) -> dict:
    """沿用 core.leave_logic.leave_event_body，加顏色與通用鍵（舊鍵 originsun_leave_id 保留相容）。"""
    colors = colors or DEFAULT_COLORS
    body = leave_event_body(req, staff_name, link)
    body["colorId"] = colors.get("leave", DEFAULT_COLORS["leave"])
    body["extendedProperties"]["private"].update({"originsun_kind": "leave", "originsun_id": req.get("id") or ""})
    return body


# ── 「做了 ✓」→ 工時列 ──

def timesheet_row_for_done(row: dict, hours=None) -> dict:
    """工作登記 → TimesheetManualRow 形狀（本人一列）。時數：明給 > 起訖算 > 全天＝HOURS_PER_WORKDAY。"""
    st, et = (row.get("start_time") or "")[:5], (row.get("end_time") or "")[:5]
    h = float(hours) if hours is not None else (hours_between(st, et) or float(HOURS_PER_WORKDAY))
    return {"work_date": as_date(row["date"]).isoformat(), "project_id": row.get("project_id") or None,
            "project_name": row.get("project_name") or "", "task_note": (row.get("title") or "")[:255],
            "start_time": st or None, "end_time": et or None, "hours": h}
