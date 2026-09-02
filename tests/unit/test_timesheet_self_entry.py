# -*- coding: utf-8 -*-
"""員工在系統裡自己填工時（docs/TIMESHEET_SELF_ENTRY_PLAN.md 階段 1）。

釘的規則：可改／可刪只認「本人＋手填＋未核可」（純函式真值表）；own-scope 只從 token
解析、絕不吃 client 的 staff_id；改列走同一支專案對映；前端打的是 /me 的端點。
"""
from types import SimpleNamespace as NS

from core.hr_logic import EDITABLE_STATUSES, can_edit_timesheet
from tests.unit._srcscan import code_only, func_body, repo_src


def test_can_edit_truth_table():
    mine = dict(staff_id="s1", source="manual", status="draft")
    assert can_edit_timesheet(NS(**mine), "s1") == ""
    assert can_edit_timesheet(NS(**mine), "s2") == "not_owner"
    assert can_edit_timesheet(NS(**mine), "") == "not_owner"            # 沒綁定＝誰的都不是
    assert can_edit_timesheet(NS(**{**mine, "source": "sheet"}), "s1") == "not_manual"
    assert can_edit_timesheet(NS(**{**mine, "source": "import"}), "s1") == "not_manual"
    for st in ("approved", "locked", "import", "confirmed"):   # 不審核：這些狀態沒人寫得出來
        assert can_edit_timesheet(NS(**{**mine, "status": st}), "s1") == "locked", st
    # 代碼→給人看的原句只有一份（端點的 detail 從這裡拿）
    from core.hr_logic import EDIT_BLOCK_TEXT
    assert "不是你的" in EDIT_BLOCK_TEXT["not_owner"] and "Sheet" in EDIT_BLOCK_TEXT["not_manual"]
    assert "不能再改" in EDIT_BLOCK_TEXT["locked"]
    assert EDITABLE_STATUSES == {"plan", "draft"}   # 不審核：計畫與實際都隨時可改


def test_own_scope_comes_from_the_token_never_the_body():
    src = code_only(repo_src("routers/api_me.py"))
    for fn in ("async def my_timesheets(", "async def add_my_timesheets(",
               "async def update_my_timesheet(", "async def delete_my_timesheet("):
        body = func_body(src, fn)
        assert "_me_ident(request)" in body, fn
        assert "body.staff_id" not in body and "request.query_params" not in body, fn
    # 改／刪都經 own_row → can_edit_timesheet（同一份規則），不各自比 staff_id
    svc = code_only(repo_src("services/timesheet_self.py"))
    assert "update_row(session, ident, row_id, body)" in func_body(src, "async def update_my_timesheet(")
    assert "delete_row(session, ident, row_id)" in func_body(src, "async def delete_my_timesheet(")
    for fn in ("async def update_row(", "async def delete_row("):
        assert "own_row(session, row_id, ident)" in func_body(svc, fn), fn
    # 總表（管理員改任一列）套欄位的是同一支 apply_update，只差守衛與 404
    assert "apply_update(session, r, body)" in func_body(svc, "async def update_row(")
    assert "apply_update(session, r, body)" in func_body(svc, "async def admin_update_row(")
    assert "can_edit_timesheet(r, ident" in func_body(svc, "async def own_row(")
    # 列表把 editable 算給前端（前端不自己判規則）
    assert "can_edit_timesheet(r, staff_id)" in func_body(svc, "def ts_dict(")
    # 員工頁與 CRM tab 的「我的一天」吃同一份服務（守衛／序列化／改列規則不各寫一份）
    assert "from services.timesheet_self import" in src
    assert "from services.timesheet_self import" in code_only(repo_src("routers/api_timesheets.py"))


def test_update_reuses_the_sheet_project_mapping():
    man = code_only(repo_src("services/timesheet_manual.py"))
    rule = func_body(man, "def normalize_row(")
    assert "resolve_project(pname, lk)" in rule and "parse_date(r.work_date)" in rule
    assert "row_state(r.hours, r.planned_hours)" in rule and "norm_work_type(r.work_type)" in rule
    # 插入與更新都吃這一支，不各寫一份
    upd = code_only(func_body(repo_src("services/timesheet_self.py"), "async def apply_update("))
    assert "normalize_row(" in upd and "load_project_lookup(session)" in upd
    assert "normalize_row(" in func_body(man, "async def insert_manual_rows(")


def test_my_page_talks_to_the_me_endpoints_only():
    html = repo_src("frontend/my.html")
    assert '"/api/v1/me/timesheets?month="' in html
    assert '"/api/v1/me/timesheets/batch"' in html
    assert 'method: "PUT"' in html and 'method: "DELETE"' in html
    assert "/api/v1/timesheets/manual" not in html, "員工頁不該打管理端代填端點"
    # 只有 editable 的列長「改／刪」；規則在後端
    assert "it.editable ?" in html
