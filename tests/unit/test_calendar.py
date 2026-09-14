# -*- coding: utf-8 -*-
"""公布欄「行事曆」（docs/CALENDAR_PLAN.md；owner 2026-09-14：專案詳情分頁／外部人員可排／排別人不通知／全部同步 Google／顏色依類別可調）。
規則 core.schedule_logic 純函式；同步 services.calendar_sync；路由 routers/api_calendar.py 用 _srcscan 釘住。"""
from datetime import date

import pytest

from core import schedule_logic as L
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src


# ── 人員 ──

def test_attendees_keep_internal_ids_and_accept_external_names():
    out = L.attendee_norm([{"staff_id": "S1", "name": "劉禮瑜", "role": "剪接"}, {"name": "王小明", "contact": "0912"},
                           {"staff_id": "S1", "name": "重複"}, {"name": ""}, "junk"])
    assert out == [{"staff_id": "S1", "name": "劉禮瑜", "role": "剪接", "external": False, "contact": ""},
                   {"staff_id": "", "name": "王小明", "role": "", "external": True, "contact": "0912"}]
    assert L.is_attendee(out, "S1", "") and L.is_attendee(out, "", "王小明") and not L.is_attendee(out, "S9", "誰")


def test_slots_and_hours():
    assert L.slot_times("am") == ("09:00", "13:00") and L.slot_times("pm") == ("13:00", "18:00")
    assert L.slot_times("custom", "10:00", "12:30") == ("10:00", "12:30") and L.slot_times("all") == ("", "")
    assert L.slot_of("09:00", "13:00") == "am" and L.slot_of("", "") == "all" and L.slot_of("10:00", "11:00") == "custom"
    assert L.hours_between("09:00", "13:00") == 4.0 and L.hours_between("18:00", "09:00") == 0.0


# ── 衝突：只提醒不擋 ──

def test_conflicts_flag_leave_shoot_and_planned_work_for_the_same_person():
    att = [{"staff_id": "S1", "name": "蔡念栩"}, {"name": "外部阿明"}]
    leaves = [{"staff_id": "S1", "staff_name": "蔡念栩", "start_date": "2026-09-18", "end_date": "2026-09-18", "leave_type": "特休"}]
    shoots = [{"id": "sh1", "title": "iWIN 年會", "date": "2026-09-18", "end_date": None, "crew": [{"name": "外部阿明"}], "status": "排定"},
              {"id": "sh2", "title": "取消的", "date": "2026-09-18", "end_date": None, "crew": [{"staff_id": "S1", "name": "蔡念栩"}], "status": "取消"}]
    scheds = [{"id": "w1", "title": "調光", "date": "2026-09-17", "end_date": "2026-09-19", "attendees": att, "status": "planned"},
              {"id": "w2", "title": "自己", "date": "2026-09-18", "end_date": None, "attendees": att, "status": "planned"}]
    hits = L.conflicts(att, "2026-09-18", None, leaves, shoots, scheds, exclude_id="w2")
    kinds = sorted((h["name"], h["kind"]) for h in hits)
    assert kinds == [("外部阿明", "schedule"), ("外部阿明", "shoot"), ("蔡念栩", "leave"), ("蔡念栩", "schedule")]
    assert not L.conflicts(att, "2026-09-20", None, leaves, shoots, scheds)


# ── Google 事件 ──

def test_schedule_event_body_follows_the_shoot_shape_and_carries_colour_and_people():
    row = {"id": "w1", "kind": "work", "title": "A-copy 粗剪", "project_name": "iWIN 兒少安全年會", "date": "2026-09-18", "end_date": "",
           "start_time": "13:00", "end_time": "18:00", "attendees": [{"name": "劉禮瑜", "role": "剪接", "external": False},
                                                                     {"name": "阿明", "external": True, "contact": "0912"}],
           "location_text": "剪接室", "notes": "先看 3 分鐘版", "status": "planned"}
    b = L.event_body_schedule(row, "http://x", {"work": "9"})
    assert b["summary"] == "工作｜A-copy 粗剪（iWIN 兒少安全年會）" and b["colorId"] == "9"
    assert b["start"] == {"dateTime": "2026-09-18T13:00:00", "timeZone": "Asia/Taipei"}
    assert b["end"]["dateTime"] == "2026-09-18T18:00:00"
    assert "人員：劉禮瑜（剪接）、阿明・外部 0912" in b["description"] and "地點：剪接室" in b["description"] and "系統：http://x" in b["description"]
    assert b["extendedProperties"] == {"private": {"originsun_kind": "schedule", "originsun_id": "w1"}}
    # 全天多日：date／end.date 開區間；做完加 ✓；會議用會議字
    b2 = L.event_body_schedule({**row, "kind": "meeting", "start_time": "", "end_time": "", "end_date": "2026-09-19", "status": "done"})
    assert b2["summary"].startswith("✓ 會議｜") and b2["start"] == {"date": "2026-09-18"} and b2["end"] == {"date": "2026-09-20"}
    from core.shoot_logic import event_body
    assert set(b) - {"colorId"} == set(event_body({"id": "s", "date": "2026-09-10", "project_name": "案"}))


def test_milestone_and_leave_bodies():
    m = L.event_body_milestone({"id": "m1", "title": "A-copy 交客戶", "due_date": date(2026, 9, 19), "assignee_name": "劉禮瑜", "status": "open"}, "iWIN", "", {"milestone": "3"})
    assert m["summary"] == "里程碑｜iWIN：A-copy 交客戶" and m["start"] == {"date": "2026-09-19"} and m["end"] == {"date": "2026-09-20"} and m["colorId"] == "3"
    assert m["extendedProperties"]["private"] == {"originsun_kind": "milestone", "originsun_id": "m1"}
    lv = L.event_body_leave({"id": "r1", "leave_type": "特休", "hours": 8, "part": "all", "start_date": "2026-09-18", "end_date": "2026-09-18"}, "蔡念栩", "", {"leave": "8"})
    assert lv["colorId"] == "8" and lv["extendedProperties"]["private"]["originsun_leave_id"] == "r1"     # 舊鍵保留相容
    assert lv["extendedProperties"]["private"]["originsun_id"] == "r1"


def test_colours_are_google_ids_with_defaults():
    assert L.color_map({"work": "11", "shoot": "99", "junk": "1"}) == {**L.DEFAULT_COLORS, "work": "11"}
    assert L.color_hex(L.DEFAULT_COLORS, "shoot") == "#f5511d" and set(L.DEFAULT_COLORS) == set(L.COLOR_KINDS)
    assert len(L.GOOGLE_COLORS) == 11


def test_done_turns_into_a_timesheet_row():
    row = {"date": "2026-09-18", "project_id": "p1", "project_name": "iWIN", "title": "粗剪", "start_time": "13:00", "end_time": "18:00"}
    assert L.timesheet_row_for_done(row) == {"work_date": "2026-09-18", "project_id": "p1", "project_name": "iWIN", "task_note": "粗剪",
                                             "start_time": "13:00", "end_time": "18:00", "hours": 5.0}
    assert L.timesheet_row_for_done({**row, "start_time": "", "end_time": ""})["hours"] == 8.0        # 全天＝HOURS_PER_WORKDAY
    assert L.timesheet_row_for_done(row, 2.5)["hours"] == 2.5


# ── 同步線：generic key、四種事件、刪除 ──

def test_upsert_accepts_the_generic_idempotency_key():
    up = code_only(func_body(repo_src("services/google_calendar.py"), "def upsert_event("))
    assert 'priv.get("originsun_id")' in up and 'k.startswith("originsun_") and k.endswith("_id")' in up


def test_sync_line_covers_four_kinds_and_deletes_on_cancel_or_non_approved_leave():
    from services import calendar_sync as C
    assert C.KINDS == ("schedule", "milestone", "leave", "shoot")
    from types import SimpleNamespace as NS
    assert C._wants_delete("schedule", NS(status="cancelled")) and not C._wants_delete("schedule", NS(status="planned"))
    assert C._wants_delete("leave", NS(status="已撤回")) and not C._wants_delete("leave", NS(status="已核准"))
    assert not C._wants_delete("milestone", NS(status="done"))
    src = code_only(repo_src("services/calendar_sync.py"))
    assert "asyncio.to_thread(gc.upsert_event" in src and "asyncio.to_thread(gc.delete_event" in src
    assert 'from routers.api_shoots import _sync_calendar' in src            # 場次沿用既有那條（測試釘住只從 _finish 進）
    # 場次的 body 帶同一張顏色表
    assert 'body["colorId"] = calendar_colors()["shoot"]' in code_only(func_body(repo_src("routers/api_shoots.py"), "async def _sync_calendar("))


def test_milestones_and_leave_hook_the_sync_after_commit():
    ms = code_only(repo_src("services/milestone_service.py"))
    save = func_body(ms, "async def save_week(")
    assert save.count("await session.commit()") == 1 and "await _calendar_sync(touched, gone)" in save
    assert "gone.append(getattr(m, \"google_event_id\", None))" in save
    assert "await _calendar_sync([mid])" in func_body(ms, "async def set_done(") and "await _calendar_sync([mid])" in func_body(ms, "async def defer(")
    hr = code_only(repo_src("routers/api_hr.py"))
    assert "await _calendar_sync_leave(leave_id)" in func_body(hr, "async def approve_leave(")
    assert "await _calendar_sync_leave(leave_id)" in func_body(hr, "async def decide_cancel(")
    mig = repo_src("db/migrations.py")
    assert '("crm_project_milestones", "google_event_id", "VARCHAR(255)")' in mig
    from db.models import CrmSchedule
    assert {"kind", "project_id", "title", "date", "end_date", "attendees", "status", "timesheet_ids", "plan_row_id", "google_event_id"} <= {c.name for c in CrmSchedule.__table__.columns}


# ── 路由 ──

def test_router_registered_guards_and_endpoints():
    assert "'api_calendar'" in repo_src("main.py")
    src = code_only(repo_src("routers/api_calendar.py"))
    for path in ('"/events"', '"/options"', '"/conflicts"', '"/schedule"', '"/schedule/{sid}"', '"/schedule/{sid}/status"', '"/schedule/{sid}/done"',
                 '"/schedule/{sid}/resync"', '"/resync-all"', '"/colors"', '"/day"'):
        assert path in src, path
    assert 'READ_KEYS = ("bulletin", "crm_projects", "timesheets", ME_ZONE_MASTER, "me_team_week")' in src
    w = func_body(src, "async def _writer(")
    assert 'payload_grants(payload, WRITE_OTHERS_KEY)' in w and "403" in w and "409" in w          # 排別人＝crm_projects；登記自己＝綁定
    assert "check_admin(request)" in func_body(src, "async def put_colors(") and "check_admin(request)" in func_body(src, "async def resync_all(")
    ev = func_body(src, "async def calendar_events(")
    assert 'if scope == "me":' in ev and "_plan_events(" in ev and "_holiday_events(" in ev and "_leave_events(" in ev
    assert "_pid_for(" in src and "hide_mine_projects" in src                                      # 私帳案只給名不給 id
    done = func_body(src, "async def schedule_done(")
    assert "timesheet_row_for_done(row, req.hours)" in done and "add_rows(session, ident, [TimesheetManualRow(**fields)])" in done
    assert 'card.status == "plan"' in done                                                          # 計畫卡直接變實際，不多插一列
    assert done.count("await _finish(sid)") == 2 and '{"schedule": row' not in done                 # /polish BUG-2：回同步後的最新列，不是動手前那份
    assert "await calendar_sync.delete_events([eid])" in func_body(src, "async def delete_schedule(")
    assert 'await calendar_sync.sync("schedule", sid)' in func_body(src, "async def _finish(")


@pytest.mark.parametrize("kw", ["notifier", "google_chat", "digest"])
def test_no_notifications_by_owner_decision(kw):
    assert kw not in repo_src("routers/api_calendar.py")


# ── 前端：一份元件三個宿主 ──

def test_three_hosts_mount_the_same_component():
    idx = repo_src("frontend/js/shared/calendar/index.js")
    assert "export async function mountCalendar(" in idx
    assert "mountCalendar({" in repo_src("frontend/tabs/bulletin/bulletin.js")
    assert 'data-subview="calendar"' in repo_src("frontend/tabs/bulletin/bulletin.html")
    pc = repo_src("frontend/tabs/crm/crm-projects-calendar.js")
    assert "lockedScope: 'project:' + projectId" in pc and "mountCalendar({" in pc
    assert 'data-tab="calendar"' in repo_src("frontend/tabs/crm/crm-projects.html") and 'id="proj-detail-calendar"' in repo_src("frontend/tabs/crm/crm-projects.html")
    pj = repo_src("frontend/tabs/crm/crm-projects.js")
    assert "if (tab === 'calendar' && state.selectedId) { loadCalendarTab(state.selectedId); }" in pj
    assert "else if (tab === 'calendar') loadCalendarTab(projectId);" in pj
    m = repo_src("frontend/m/views/calendar.js")
    assert "const CAL = '/api/v1/calendar';" in m and "${CAL}/day?" in m and "${CAL}/events?" in m and "${CAL}/schedule" in m


def test_views_do_not_hardcode_colours_or_status_words():
    v = js_code_only(repo_src("frontend/js/shared/calendar/views.js"))
    assert "colorOf(" in v and "z.s.colors" in v
    f = js_code_only(js_func_body(repo_src("frontend/js/shared/calendar/form.js"), "export async function openForm(ev = null, preset = {}) {"))
    assert "api('/conflicts?'" in f and "'照排'" in f                     # 衝突只提醒不擋
    fsrc = repo_src("frontend/js/shared/calendar/form.js")
    assert "＋ 外部人員" in fsrc and "external: true" in fsrc              # 外部人員可排
    assert "'/colors'" in repo_src("frontend/js/shared/calendar/form.js") and "apply=1" in repo_src("frontend/js/shared/calendar/form.js")


# ── /polish 2026-09-14 安全網＋BUG-1：守衛 _writer 的三條路 ──

async def _writer_with(monkeypatch, *, grants: bool, staff_id: str, attendees: list):
    from routers import api_calendar as C
    monkeypatch.setattr(C, "_reader", lambda request: {"sub": "u"})
    monkeypatch.setattr(C, "payload_grants", lambda payload, *keys: grants)

    async def _me(request):
        return {"username": "u", "staff_id": staff_id, "name": "小明" if staff_id else ""}
    monkeypatch.setattr(C, "_me", _me)
    return await C._writer(None, attendees)


async def test_writer_lets_a_bound_employee_register_only_themselves(monkeypatch):
    from fastapi import HTTPException
    me, att = await _writer_with(monkeypatch, grants=False, staff_id="S1", attendees=[{"staff_id": "S1", "name": "小明", "role": "", "external": False, "contact": ""}])
    assert me["staff_id"] == "S1" and att[0]["staff_id"] == "S1"
    # BUG-1：手機「登記工作」送 attendees: []＝登記自己 —— 以前這裡 403，現在＝本人
    me, att = await _writer_with(monkeypatch, grants=False, staff_id="S1", attendees=[])
    assert att == [{"staff_id": "S1", "name": "小明", "role": "", "external": False, "contact": ""}]
    with pytest.raises(HTTPException) as e:        # 排別人／外部人員要 crm_projects
        await _writer_with(monkeypatch, grants=False, staff_id="S1", attendees=[{"staff_id": "", "name": "外部", "role": "", "external": True, "contact": ""}])
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:        # 沒綁人員檔案的一般帳號：登記自己也不行
        await _writer_with(monkeypatch, grants=False, staff_id="", attendees=[])
    assert e.value.status_code == 409
    # 有 crm_projects 的管理員帳號沒綁人員檔：可以排別人（attendees 給誰就是誰）
    me, att = await _writer_with(monkeypatch, grants=True, staff_id="", attendees=[{"staff_id": "S2", "name": "小華", "role": "", "external": False, "contact": ""}])
    assert att[0]["staff_id"] == "S2"


# ── 每種事件各自一本日曆（owner 2026-09-15「這個是人員休假的」）──

def test_each_kind_can_have_its_own_calendar(monkeypatch):
    from services import google_calendar as gc
    monkeypatch.setattr(gc, "calendars", lambda: {"leave": "leave-cal", "shoot": ""})
    assert gc.calendar_for("leave", "shared") == "leave-cal"
    assert gc.calendar_for("schedule", "shared") == "shared"            # 沒填退回共用
    assert gc.CALENDAR_KINDS == ("shoot", "schedule", "milestone", "leave")
    sync = code_only(func_body(repo_src("services/calendar_sync.py"), "async def sync("))
    assert 'cal_id = gc.calendar_for(kind, shared_id)' in sync
    assert 'cal_id = gc.calendar_for("shoot", shared_id)' in code_only(func_body(repo_src("routers/api_shoots.py"), "async def _sync_calendar("))
    assert 'await calendar_sync.delete_events(list(gone_event_ids), "milestone")' in repo_src("services/milestone_service.py")
    cfg = code_only(func_body(repo_src("routers/api_shoots.py"), "async def calendar_config("))
    assert "if req.calendars is not None:" in cfg and "k in gc.CALENDAR_KINDS" in cfg
    m = repo_src("frontend/m/views/calendar.js")
    assert "cal-cfg-k-${k}" in m and "calendars }" in m
