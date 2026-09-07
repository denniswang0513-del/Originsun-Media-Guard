"""core/milestone_logic.py — 每週專案里程碑的純函式（owner 2026-09-07；示範 frontend/demo/milestones.html 定稿）。

一列＝一個案在某一週要做到的事：week_start＝屬於哪一週（週一）。沒完成的會一直在之後的週出現（帶「延自上週」）；
「延到下週」＝week_start／due_date 各 +7。I/O 在 services/milestone_service.py，端點 routers/api_milestones.py。
"""
from datetime import date, datetime, timedelta

from core.hr_logic import day_iso, tw_day

#: 彈窗預設帶出的案＝這週有里程碑／延過來的 ＋ 近 RECENT_DAYS 天有工時紀錄的案（owner：最近有紀錄＋上週有紀錄）
RECENT_DAYS = 14
STATUS_OPEN, STATUS_DONE = "open", "done"


def as_date(v):
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


def default_due(week_start: date) -> date:
    """到期預設本週五（週一 +4）。"""
    return week_start + timedelta(days=4)


def is_carried(m_week_start, week_start: date) -> bool:
    """屬於更早的週但還沒完成 → 在這週顯示「延自上週」。"""
    w = as_date(m_week_start)
    return w is not None and w < week_start


def is_late(due, status: str, today: date) -> bool:
    d = as_date(due)
    return status != STATUS_DONE and d is not None and d < today


def shifted_week(m_week_start, m_due, week_start: date) -> tuple:
    """「延到下週」：搬到「目前看的這週」的下一週；到期若早於新的一週就同步 +7 週的距離。"""
    new_week = week_start + timedelta(days=7)
    due = as_date(m_due)
    if due is not None and due < new_week:
        due = due + timedelta(days=7 * max(1, (new_week - as_date(m_week_start)).days // 7))
        if due < new_week:
            due = default_due(new_week)
    return new_week, due


def milestone_dict(m, week_start: date, today: date) -> dict:
    """ORM／任何有同名屬性的物件 → API dict（帶 carried／late 旗標）。"""
    g = lambda k, d=None: getattr(m, k, d)   # noqa: E731
    status = g("status") or STATUS_OPEN
    return {
        "id": g("id"), "project_id": g("project_id") or "",
        "week_start": (as_date(g("week_start")) or week_start).isoformat(),
        "title": g("title") or "", "due_date": (as_date(g("due_date")).isoformat() if as_date(g("due_date")) else ""),
        "assignee_staff_id": g("assignee_staff_id") or "", "assignee_name": g("assignee_name") or "",
        "note": g("note") or "", "status": status, "done": status == STATUS_DONE,
        "done_by": g("done_by") or "", "done_at": day_iso(g("done_at")),
        "carried": is_carried(g("week_start"), week_start), "late": is_late(g("due_date"), status, today),
        "sort": g("sort") or 0, "created_by": g("created_by") or "",
    }


def sort_projects(projects: list) -> list:
    """有里程碑的在前，再照這週工時多的、最近有動靜的排。"""
    return sorted(projects, key=project_sort_key)


def project_sort_key(p: dict) -> tuple:
    """sort_projects 的鍵（給測試釘）：有里程碑 → 這週工時多 → 最近有動靜。"""
    return (0 if p.get("milestones") else 1, -float(p.get("hours_week") or 0), _desc(p.get("last_activity") or ""))


def _desc(s: str) -> str:
    """字串倒序鍵：把每個字元反轉，讓 ascending 排出 descending（日期 ISO 字串用）。"""
    return "".join(chr(0x10FFFF - ord(c)) for c in s)
