# -*- coding: utf-8 -*-
"""員工工作台「今天與這週」一顆功能一把鑰匙（owner 2026-09-08「在權限管理裡控制員工各個功能的顯示狀態，包含兼職」）。

四把新鑰匙：me_worklog（專案紀錄＋我的一週）、me_team_week（團隊的一週＋里程碑寫入）、me_project_lookup（專案查詢）、
me_plan_parttime（兼職排班）。拆之前「任一把 me_* 就整區出現」；拆了要（1）後端每支端點認自己那把、
（2）前端每顆按鈕看自己那把、（3）權限管理有勾、（4）上線那一刻一次性回填，不然大家的整區一起消失。
"""
import re

from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

NEW = ("me_worklog", "me_team_week", "me_project_lookup", "me_plan_parttime")


def test_keys_are_registered_in_every_place_that_mirrors_them():
    from core.auth import ALL_MODULES, ME_MODULE_KEYS, ME_ZONE1_KEYS, ME_ZONE1_BACKFILL_FROM
    for k in NEW:
        assert k in ALL_MODULES and k in ME_MODULE_KEYS, k
    assert ME_ZONE1_KEYS == ("me_worklog", "me_team_week", "me_project_lookup")
    assert "me_plan_parttime" not in ME_ZONE1_KEYS, "兼職排班不是子視圖，不該讓整區出現"
    assert set(ME_ZONE1_BACKFILL_FROM) == {"me_projects", "me_todos", "me_finance", "me_leave", "me_petty", "me_benefits"}
    assert ALL_MODULES[-4:] == list(NEW), "新 key 一律 append 在尾端（modules[0] 決定 admin 落地頁）"
    labels = repo_src("frontend/js/admin/user-mgmt.js")
    for k, zh in (("me_worklog", "專案紀錄"), ("me_team_week", "團隊的一週"), ("me_project_lookup", "專案查詢"), ("me_plan_parttime", "兼職排班")):
        assert f"{k}:'{zh}'" in labels, k
    tab = repo_src("frontend/js/shared/tab-config.js")
    me_group = tab[tab.index("id: 'me',"):tab.index("]", tab.index("id: 'me',"))]
    for k in NEW:
        assert f"'{k}'" in me_group, k


def test_each_endpoint_takes_its_own_key():
    me = code_only(repo_src("routers/api_me.py"))
    assert '_me_bound(request, "me_worklog")' in func_body(me, "async def my_today(")
    assert '_me_bound(request, "me_team_week")' in func_body(me, "async def team_week(")
    assert '_me_bound(request, "me_project_lookup")' in func_body(me, "async def my_projects_burn(")
    assert 'require_bound_staff(request, "me_leave")' in func_body(me, "async def my_leave_summary(")
    ts = code_only(repo_src("routers/api_timesheets.py"))
    assert 'require_bound_staff(request, "timesheets", "me_worklog")' in func_body(ts, "async def _mine_ident(")
    ms = code_only(repo_src("routers/api_milestones.py"))
    assert 'check_admin_or_module(request, "timesheets", "me_team_week")' in func_body(ms, "def _writer(")
    for fn in ("async def milestones_save(", "async def milestone_done(", "async def milestone_defer("):
        assert "_writer(request)" in func_body(ms, fn), fn
    for fn in ("async def milestones_week(", "async def milestones_of_project("):
        assert "_who(request)" in func_body(ms, fn) and "_writer(" not in func_body(ms, fn), f"{fn} 讀仍是登入即可"


def test_workspace_draws_each_view_by_its_key_and_falls_back():
    html = repo_src("frontend/my.html")
    assert 'const Z1_KEYS = ["me_worklog", "me_team_week", "me_project_lookup"];' in html
    assert 'const Z1_VIEW_KEY = { log: "me_worklog", plan: "me_worklog", week: "me_team_week", find: "me_project_lookup" };' in html
    z = js_code_only(html)
    build = func_body(z, "function buildZone1()")
    assert '_z1Can(v) ? `<button type="button" class="view-btn" data-view="${v}">' in build, "沒那把就不畫那顆按鈕"
    sw = func_body(z, "function switchZ1(v)")
    assert '["log", "plan", "week", "find"].find(_z1Can)' in sw, "記住的視圖沒鑰匙要退到第一個有鑰匙的"
    assert "ws.allowed.some(k => Z1_KEYS.includes(k))" in html


def test_admin_ui_shows_staff_status_and_blocks_parttime_planning_for_parttimers():
    js = repo_src("frontend/js/admin/user-mgmt.js")
    assert "const boundStaff = _staffListCache.find(s => s.id === u.staff_id)" in js
    assert "staffStatus === '兼職'" in js, "帳號列旁要看得到在職／兼職"
    assert "const canPlanParttime = !!boundStaff && ['在職', '合夥'].includes(staffStatus)" in js
    assert "m === 'me_plan_parttime' && !opts.canPlanParttime" in js, "兼職（或沒綁）不能勾兼職排班"
    assert "_renderUserPermCell(u.username, modules, isAdminUser, locked, { canPlanParttime })" in js


def test_one_time_backfill_runs_at_startup_through_the_dual_write_path():
    src = repo_src("main.py")
    blk = src[src.index("拆鑰匙的一次性回填"):src.index("公布欄欄位 migration")]
    assert 'get("me_zone_split_backfilled")' in blk and '["me_zone_split_backfilled"] = True' in blk, "要有旗標，只跑一次"
    assert "_get_all_users" in blk and "_persist_user" in blk, "走雙寫，不下 raw SQL（users.json 會漂開）"
    assert "ME_ZONE1_BACKFILL_FROM" in blk and "ME_ZONE1_KEYS" in blk
    assert "access_level" in blk and ">= 3" in blk, "管理員不用補（grant_admin_all_modules 塞滿）"
    assert re.search(r"except Exception as \w+:\s*\n\s*print", blk), "回填失敗不能擋開機"
