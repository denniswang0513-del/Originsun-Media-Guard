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
    assert can_edit_timesheet(NS(**{**mine, "status": "confirmed"}), "s1") == ""
    assert "不是你的" in can_edit_timesheet(NS(**mine), "s2")
    assert "不是你的" in can_edit_timesheet(NS(**mine), "")            # 沒綁定＝誰的都不是
    assert "Sheet" in can_edit_timesheet(NS(**{**mine, "source": "sheet"}), "s1")
    assert "Sheet" in can_edit_timesheet(NS(**{**mine, "source": "import"}), "s1")
    for st in ("approved", "locked", "import"):
        assert "不能再改" in can_edit_timesheet(NS(**{**mine, "status": st}), "s1"), st
    assert EDITABLE_STATUSES == {"draft", "confirmed"}


def test_own_scope_comes_from_the_token_never_the_body():
    src = code_only(repo_src("routers/api_me.py"))
    for fn in ("async def my_timesheets(", "async def add_my_timesheets(",
               "async def update_my_timesheet(", "async def delete_my_timesheet("):
        body = func_body(src, fn)
        assert "_bound_ident(request)" in body, fn
        assert "body.staff_id" not in body and "request.query_params" not in body, fn
    # 改／刪都經 _own_row → can_edit_timesheet（同一份規則），不各自比 staff_id
    for fn in ("async def update_my_timesheet(", "async def delete_my_timesheet("):
        assert "_own_row(session, row_id, ident)" in func_body(src, fn), fn
    assert "can_edit_timesheet(r, ident" in func_body(src, "async def _own_row(")
    # 列表把 editable 算給前端（前端不自己判規則）
    assert "can_edit_timesheet(r, staff_id)" in func_body(src, "def _ts_dict(")


def test_update_reuses_the_sheet_project_mapping():
    body = code_only(func_body(repo_src("routers/api_me.py"), "async def update_my_timesheet("))
    assert "resolve_project(pname, await load_project_lookup(session))" in body
    assert "_parse_date(body.work_date)" in body


def test_my_page_talks_to_the_me_endpoints_only():
    html = repo_src("frontend/my.html")
    assert '"/api/v1/me/timesheets?month="' in html
    assert '"/api/v1/me/timesheets/batch"' in html
    assert 'method: "PUT"' in html and 'method: "DELETE"' in html
    assert "/api/v1/timesheets/manual" not in html, "員工頁不該打管理端代填端點"
    # 只有 editable 的列長「改／刪」；規則在後端
    assert "it.editable ?" in html
