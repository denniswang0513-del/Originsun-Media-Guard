"""core/shoot_logic.py — 拍攝場次（行事曆）的純規則，無 DB、無 I/O，方便單元測試。

規劃：docs/SHOOT_CALENDAR_PLAN.md。三件事住這裡：
  1. 字彙：場次狀態 SHOOT_STATUSES、器材預約狀態 EQUIPMENT_STATES（前端從 /shoots/options 拿，不寫死）
  2. 規則：日期區間重疊（器材衝突）、專案「拍攝日」怎麼從場次算、一列領用紀錄現在算什麼狀態
  3. Google 日曆事件的 JSON 長相（services/google_calendar 只負責送，不負責組）
"""
from __future__ import annotations

from datetime import date, timedelta

SHOOT_STATUSES = ("排定", "完成", "取消")
SCHEDULED, DONE, CANCELLED = SHOOT_STATUSES

# 器材預約列（equipment_checkouts 掛 shoot_id）的三種狀態：
# 預約＝out_at 空、還沒領；已領＝out_at 有值；已還＝returned_at 有值
EQUIPMENT_STATES = ("預約", "已領", "已還")
RESERVED, PICKED_UP, RETURNED = EQUIPMENT_STATES

TAIPEI_TZ = "Asia/Taipei"


def span(start: date, end: date | None) -> tuple[date, date]:
    """場次的日期區間（含首尾）；沒有結束日＝當天。結束日早於開始日就當成當天。"""
    if end is None or end < start:
        return start, start
    return start, end


def overlaps(a_start: date, a_end: date | None, b_start: date, b_end: date | None) -> bool:
    """兩個含首尾的日期區間有沒有碰到（同一天也算）。"""
    a0, a1 = span(a_start, a_end)
    b0, b1 = span(b_start, b_end)
    return a0 <= b1 and b0 <= a1


def derive_project_shoot_date(rows, today: date) -> date | None:
    """專案的「拍攝日」＝最近一場**未來**（含今天）的排定／完成場次；沒有未來的就取最後一場；
    全部取消（或沒有場次）→ None。rows：(date, status) 可迭代。"""
    live = [d for d, s in rows if d is not None and s != CANCELLED]
    if not live:
        return None
    upcoming = [d for d in live if d >= today]
    return min(upcoming) if upcoming else max(live)


def checkout_state(out_at, returned_at) -> str:
    if returned_at:
        return RETURNED
    if out_at:
        return PICKED_UP
    return RESERVED


def event_body(s: dict, link: str = "") -> dict:
    """組 Google Calendar 事件。全天：date／end.date＝結束日＋1（Google 的結束日是開區間）；
    有開始時間：dateTime＋Asia/Taipei，沒給結束時間就跟開始時間相同（Google 接受零長度）。"""
    title = (s.get("title") or s.get("project_name") or "拍攝").strip()
    client = (s.get("client_short_name") or "").strip()
    summary = f"拍攝｜{title}" + (f"（{client}）" if client else "")
    start_d = s["date"]
    end_d = s.get("end_date") or start_d
    st, et = (s.get("start_time") or "").strip(), (s.get("end_time") or "").strip()
    if st:
        start = {"dateTime": f"{start_d}T{st}:00", "timeZone": TAIPEI_TZ}
        end = {"dateTime": f"{end_d}T{et or st}:00", "timeZone": TAIPEI_TZ}
    else:
        nxt = date.fromisoformat(end_d) + timedelta(days=1)
        start = {"date": start_d}
        end = {"date": nxt.isoformat()}
    lines = []
    crew = [c.get("name") for c in (s.get("crew") or []) if c.get("name")]
    if crew:
        lines.append("人員：" + "、".join(crew))
    gear = [e.get("name") for e in (s.get("equipment") or []) if e.get("name")]
    if gear:
        lines.append("器材：" + "、".join(gear))
    if s.get("notes"):
        lines.append("備註：" + s["notes"])
    if link:
        lines.append("系統：" + link)
    body = {
        "summary": summary,
        "location": s.get("location_name") or s.get("location_text") or "",
        "description": "\n".join(lines),
        "start": start, "end": end,
        "extendedProperties": {"private": {"originsun_shoot_id": s.get("id") or ""}},
    }
    return body
