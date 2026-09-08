# -*- coding: utf-8 -*-
"""今天的專案紀錄「儲存草稿」（owner 2026-09-07）：有內容就存、沒時數＝pending 草稿、彙整只算 hours>0、
近 30 天的草稿日期在今天那條提醒。"""
import pytest
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src


def test_pending_state_and_blank_row_guard():
    from core.hr_logic import EDITABLE_STATUSES, PENDING_STATUS, row_state
    assert row_state(0, None) == PENDING_STATUS == "pending" and PENDING_STATUS in EDITABLE_STATUSES
    body = code_only(func_body(repo_src("services/timesheet_manual.py"), "def normalize_row("))
    # 2026-09-08：守衛從「只擋 pending」擴到「pending 與 plan 都擋」——「我的一週」的 plan 旗標會讓
    # row_state 直接回 "plan"，只看 pending 的話，帶 plan:true 的空 POST 就繞過去存進一列垃圾
    assert 'if status in ("pending", "plan") and hours <= 0 and not (' in body and "空白列不存" in body


def test_aggregation_ignores_pending_rows_by_hours_filter():
    """彙整不用認 pending：燒錄／summary／團隊的一週本來就 hours>0。"""
    assert "Timesheet.hours > 0" in code_only(func_body(repo_src("services/timesheet_lookup.py"), "async def burn_rows("))
    assert "hours > 0" in repo_src("routers/api_me.py")


def test_incomplete_endpoint_lists_pending_days_and_precedes_row_id_route():
    src = repo_src("routers/api_timesheets.py")
    assert src.index('@router.get("/mine/incomplete")') < src.index('@router.put("/mine/{row_id}")')
    body = code_only(func_body(src, "async def my_incomplete_days("))
    assert "Timesheet.status == PENDING_STATUS" in body and "own_filter(ident)" in body and "group_by(Timesheet.work_date)" in body


def test_sheet_saves_any_content_and_marks_drafts():
    js = js_code_only(repo_src("frontend/js/shared/ts-sheet.js"))
    body = js_func_body(js, "export function wireAutosave(host, cfg = {})")
    assert "const content = body.project_name || body.task_note || body.stage_id || body.remark;" in body
    assert "const pending = !((body.hours || 0) > 0 || (body.planned_hours || 0) > 0);" in body
    assert "再填時數" not in body, "沒時數不再擋，改存草稿"
    assert "pending: r.status === 'pending'" in js and "草稿（沒時數）" in js


def test_workspace_strip_reminds_incomplete_days_and_import_buttons_are_gone():
    html = repo_src("frontend/my.html")
    assert "/api/v1/timesheets/mine/incomplete?days=30" in html
    assert 'data-z1="day-goto"' in html and "專案紀錄未完成" in html
    assert 'data-z1="save">儲存草稿</button>' in html
    assert "import-shoots" not in html and "import-todos" not in html and "從場次帶入" not in html
