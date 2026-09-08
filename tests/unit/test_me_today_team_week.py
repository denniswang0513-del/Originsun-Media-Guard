# -*- coding: utf-8 -*-
"""員工頁第一區「今天與這週」（docs/JOURNAL_WORKLOG_PLAN.md §8–§11）：/me/today、/me/team_week，
以及工作追蹤唯讀端點的守衛放寬（timesheets 模組或綁定人員）。

釘的規則：team_week 的人×日直接用看板那一份計算（不抄第二份）；場次 crew 比 staff_id 退回姓名；
放寬只碰非私帳 wall 的端點（/projects、/summary 2026-09-08 起開給 timesheets 鑰匙、寫入仍是 _require_mine_admin）。
"""
from tests.unit._srcscan import code_only, func_body, repo_src


def test_in_crew_matches_staff_id_then_falls_back_to_name():
    """正本在 core.leave_logic（請假撞場次與「今天」的場次同一份）；api_me 直接用它，不再包一層同名轉呼殼。"""
    from core.leave_logic import in_crew
    crew = [{"staff_id": "s1", "name": "王"}, {"staff_id": "", "name": "李"}]
    assert in_crew(crew, "s1", "別人")                 # id 命中
    assert not in_crew(crew, "s9", "王")               # 有 id 就不比名字（同名別人不算）
    assert in_crew(crew, "s9", "李")                   # 舊場次只存名字 → 比名字
    assert not in_crew([], "s1", "王")


def test_shoot_days_expands_multi_day_shoots():
    from datetime import date
    from types import SimpleNamespace as NS
    from routers.api_me import _shoot_days
    assert _shoot_days(NS(date=date(2026, 9, 1), end_date=None)) == [date(2026, 9, 1)]
    assert _shoot_days(NS(date=date(2026, 9, 1), end_date=date(2026, 9, 3))) == [date(2026, 9, i) for i in (1, 2, 3)]
    assert _shoot_days(NS(date=date(2026, 9, 3), end_date=date(2026, 9, 1))) == [date(2026, 9, 3)]   # 壞資料不爆


def test_today_and_team_week_are_gated_by_any_me_key_plus_binding():
    src = code_only(repo_src("routers/api_me.py"))
    for fn in ("async def my_today(", "async def team_week("):
        body = func_body(src, fn)
        assert "_me_bound(request, " in body, fn        # 2026-09-08：一顆功能一把，各端點帶自己的鑰匙
        assert "body.staff_id" not in body and "username=" not in body.split("select(")[0], fn
    gate = func_body(src, "async def _me_bound(")
    # 2026-09-06：守衛收成一行；2026-09-08：改成各子視圖自己的鑰匙（timesheets 模組整區恆過）
    assert "require_zone_staff(request, *keys)" in gate     # 2026-09-08 晚：總開關＋子鑰匙兩道（core.identity）
    assert "ME_MODULE_KEYS" not in gate        # 409 原句只在 core.identity
    assert '_me_bound(request, "me_worklog")' in func_body(src, "async def my_today(")
    assert '_me_bound(request, "me_team_week")' in func_body(src, "async def team_week(")
    assert '_me_bound(request, "me_project_lookup")' in func_body(src, "async def my_projects_burn(")


def test_team_week_reuses_the_board_calculation():
    src = code_only(repo_src("routers/api_me.py"))
    body = func_body(src, "async def team_week(")
    assert "board_days(session, d0, 7)" in body        # 搬到 services.timesheet_self（不跨 router import 底線函式）
    assert "select(Timesheet)" not in body                     # 不抄第二份看板查詢
    for k in ('"people"', '"shoots"', '"leave"', '"days"', '"week_start"'):
        assert k in body, k
    for k in ('"project"', '"note"', '"hours"', '"planned_hours"', '"status"', '"stage_name"', '"work_type"'):
        assert k in body, k
    assert "from services.timesheet_self import board_days" in src   # 看板計算住 service，不跨 router import 底線函式
    ts = code_only(repo_src("routers/api_timesheets.py"))
    assert "board_days(session, d0, days)" in func_body(ts, "async def day_board(")


def test_today_lists_my_shoots_todos_pending_leave_and_last_week_journal():
    src = code_only(repo_src("routers/api_me.py"))
    body = func_body(src, "async def my_today(")
    assert "_shoots_between(session, today, today)" in body and "in_crew(" in body
    assert "_todos_for(session" in body                        # 與 workspace 同一份待辦查詢
    assert 'HrLeaveRequest.status == "待審"' in body
    assert "shell_status(shell)" in body and "week_start_of(today) - timedelta(days=7)" in body
    for k in ('"shoots"', '"todos"', '"leave_pending"', '"last_week_journal"'):
        assert k in body, k
    shoots = func_body(src, "async def _shoots_between(")
    assert "CrmShoot.status != SHOOT_CANCELLED" in shoots and "_crew_list(s.crew)" in shoots
    ws = func_body(src, "async def my_workspace(")
    assert "_todos_for(session" in ws


def test_readonly_relaxation_does_not_touch_the_mine_wall():
    src = code_only(repo_src("routers/api_timesheets.py"))
    gate = func_body(src, "async def _ts_or_bound(")
    assert 'payload_grants(check_logged_in(request), "timesheets")' in gate   # 布林探針：不留假的授權不足紀錄
    assert "check_logged_in(request)" in gate                   # 沒登入的 401 由 check_logged_in 丟（布林探針前先驗登入）
    assert "resolve_current_staff(request)" in gate and 'ident["staff"] is None' in gate
    assert 'return ident["staff"].name' in gate                  # 靠綁定進來的回姓名（project_options 縮到本人最近填過的）
    for fn in ("async def project_file(", "async def timesheet_options("):
        assert "_ts_or_bound(request)" in func_body(src, fn), fn
    # owner 2026-09-07「在職員工要能完整使用今天與這週」：專案下拉不再只認 timesheets／me_finance
    # （只有 me_profile 的員工拿 403 → 前端吞掉 → 下拉「進行中（0）」）
    po = func_body(src, "async def get_project_options(")
    assert "staff_name = await _ts_or_bound(request)" in po
    # 只有 me_finance、沒綁人員的帳號原本就拿整份：放寬不收回（polish review 2026-09-07）
    assert 'payload_grants(_extract_token(request) or {}, "me_finance")' in po and "staff_name = None" in po
    # 權限稽核第二批（2026-09-08）：私帳案清單、burn 摘要放給 timesheets 分頁鑰匙（摘要抹私帳欄位，
    # 見 test_batch2_hr_guards）；寫私帳那半邊（改預算）仍守私帳 wall
    assert 'check_admin_or_module(request, "timesheets")' in func_body(src, "async def timesheet_projects(")
    assert 'check_admin_or_module(request, "timesheets")' in func_body(src, "async def burn_summary(")
    assert '_require_mine_admin(request, level="full")' in func_body(src, "async def set_project_budget(")
    assert "_ts_or_bound" not in func_body(src, "async def timesheet_projects(")


def test_my_rows_search_is_own_scope_and_list_only():
    src = code_only(repo_src("routers/api_timesheets.py"))
    body = func_body(src, "async def my_rows(")
    assert "_mine_ident(request)" in body and "search_rows(session, ident" in body   # timesheets 模組或 me_* 鑰匙（員工頁也打）
    assert '"/mine/rows"' in src
    assert "sum(" not in body                                   # 只列不算
    svc = code_only(repo_src("services/timesheet_self.py"))
    srch = func_body(svc, "async def search_rows(")
    assert "own_rows(ident)" in srch and "Timesheet.stage_id == stage_id" in srch and "limit(" in srch
