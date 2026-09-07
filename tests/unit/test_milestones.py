# -*- coding: utf-8 -*-
"""每週專案里程碑（owner 2026-09-07；示範 frontend/demo/milestones.html 定稿）。

規則：core.milestone_logic 純函式；I/O services.milestone_service；端點 /api/v1/milestones（登入即可，大家都能編）；
員工頁「團隊的一週」多「設定專案里程碑」鈕＋週表上方的帶＋負責人格子的小卡；「今天」那條帶本週里程碑。
彈窗預設帶出的案＝這週有里程碑／延過來的 ＋ 近 14 天有工時紀錄的（owner：最近有紀錄＋上週有紀錄），其餘打字找。
"""
from datetime import date
from types import SimpleNamespace as NS

from core.milestone_logic import (RECENT_DAYS, default_due, is_carried, is_late, milestone_dict, project_sort_key,
                                  shifted_week, sort_projects)
from tests.unit._srcscan import code_only, func_body, repo_src


def test_due_defaults_to_friday_and_carry_late_flags():
    assert default_due(date(2026, 9, 7)) == date(2026, 9, 11)
    assert is_carried(date(2026, 8, 31), date(2026, 9, 7)) and not is_carried(date(2026, 9, 7), date(2026, 9, 7))
    assert is_late(date(2026, 9, 5), "open", date(2026, 9, 8)) and not is_late(date(2026, 9, 5), "done", date(2026, 9, 8))
    assert not is_late(date(2026, 9, 9), "open", date(2026, 9, 8)) and not is_late(None, "open", date(2026, 9, 8))


def test_shifted_week_moves_to_next_week_and_keeps_due_inside_it():
    assert shifted_week(date(2026, 9, 7), date(2026, 9, 9), date(2026, 9, 7)) == (date(2026, 9, 14), date(2026, 9, 16))
    # 延自上週的（8/31 那週、到期 9/5）從 9/7 週延：到期補到新的一週裡
    w, d = shifted_week(date(2026, 8, 31), date(2026, 9, 5), date(2026, 9, 7))
    assert w == date(2026, 9, 14) and d >= w


def test_milestone_dict_carries_flags():
    m = NS(id="m1", project_id="p1", week_start=date(2026, 8, 31), title="配樂授權", due_date=date(2026, 9, 5),
           assignee_staff_id="s1", assignee_name="蘇家弘", note="", status="open", done_by=None, done_at=None, sort=0, created_by="a")
    d = milestone_dict(m, date(2026, 9, 7), date(2026, 9, 8))
    assert d["carried"] and d["late"] and not d["done"] and d["due_date"] == "2026-09-05" and d["week_start"] == "2026-08-31"


def test_projects_with_milestones_first_then_hours_then_recent():
    ps = [{"name": "a", "milestones": [], "hours_week": 3, "last_activity": "2026-09-01"},
          {"name": "b", "milestones": [{}], "hours_week": 0, "last_activity": ""},
          {"name": "c", "milestones": [], "hours_week": 3, "last_activity": "2026-09-05"},
          {"name": "d", "milestones": [], "hours_week": 9, "last_activity": ""}]
    assert [p["name"] for p in sort_projects(ps)] == ["b", "d", "c", "a"]
    assert project_sort_key(ps[1])[0] == 0


def test_service_defaults_to_recent_and_last_week_projects():
    svc = code_only(repo_src("services/milestone_service.py"))
    body = func_body(svc, "async def week_payload(")
    assert "await _hours_by_project(session, w0 - timedelta(days=RECENT_DAYS), w1)" in body, "近 14 天有工時的案"
    assert "ids = list({m.project_id for m in ms if m.project_id} | set(hours_recent))" in body
    wk = func_body(svc, "async def _milestones_for_week(")
    assert "M.status == STATUS_OPEN, M.week_start < week_start" in wk, "前幾週沒完成的延過來"
    assert RECENT_DAYS == 14
    save = func_body(svc, "async def save_week(")
    assert "await session.commit()" in save and save.count("await session.commit()") == 1
    from db.models import CrmProjectMilestone
    assert {"project_id", "week_start", "title", "due_date", "assignee_staff_id", "assignee_name", "status", "done_by", "done_at"} <= {c.name for c in CrmProjectMilestone.__table__.columns}


def test_router_is_registered_and_open_to_any_login():
    src = code_only(repo_src("routers/api_milestones.py"))
    for path in ('"/week"', '"/week/save"', '"/{mid}/done"', '"/{mid}/defer"', '"/project/{project_id}"'):
        assert path in src, path
    assert "check_logged_in(request)" in func_body(src, "def _who(")
    assert "'api_milestones'" in repo_src("main.py")
    today = code_only(func_body(repo_src("routers/api_me.py"), "async def my_today("))
    assert "today_summary(session, today)" in today and '"milestones": milestones' in today


def test_employee_page_has_button_band_cells_and_modal():
    html = repo_src("frontend/my.html")
    assert 'data-z1="ms-open">設定專案里程碑' in html
    assert "/api/v1/milestones/week?start=" in html and "_msBandHtml(ms)" in html
    assert "msOf(name, iso).forEach" in html, "負責人那格畫里程碑小卡"
    assert 'input[data-ms-done]' in html and "/done`, _POST({ done })" in html
    assert "/api/v1/milestones/week/save" in html and 'data-ms="save"' in html and 'data-ms="addproj"' in html and 'data-ms="defer"' in html
    assert 'list="ms-proj-dl"' in html, "還沒排的案打字找（project_options）"
    assert "t.milestones && t.milestones.total" in html, "今天那條"
