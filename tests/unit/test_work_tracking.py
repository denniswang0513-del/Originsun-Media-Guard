# -*- coding: utf-8 -*-
"""工作追蹤 P1（docs/WORK_TRACKING_UI_PLAN.md）：計畫／實際同一列、工作分類固定清單、
每日看板與「我的一天」在 CRM tab；不審核（plan／draft 都可改）。"""

import pytest

from core.hr_logic import (WORK_TYPES, norm_work_type, row_state)
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_row_state_and_work_type_rules():
    assert row_state(3, None) == "draft"
    assert row_state(3, 2) == "draft"            # 有實際就是實際
    assert row_state(None, 2) == "plan"
    assert row_state(0, 2) == "plan"
    with pytest.raises(ValueError):
        row_state(None, None)
    with pytest.raises(ValueError):
        row_state(0, 0)
    assert len(WORK_TYPES) == 9 and "拍攝" in WORK_TYPES and "剪接" in WORK_TYPES
    assert norm_work_type("") is None and norm_work_type(None) is None
    assert norm_work_type(" 拍攝 ") == "拍攝"
    with pytest.raises(ValueError):
        norm_work_type("摸魚")


def test_manual_rows_accept_plan_only_and_store_the_two_columns():
    man = code_only(repo_src("services/timesheet_manual.py"))
    rule = func_body(man, "def normalize_row(")
    assert "row_state(r.hours, r.planned_hours)" in rule and "norm_work_type(r.work_type)" in rule
    assert "resolve_project(pname, lk)" in rule and "parse_date(r.work_date)" in rule
    body = func_body(man, "async def insert_manual_rows(")
    assert "normalize_row(" in body and 'source="manual"' in body
    model = repo_src("db/models/_workos.py")
    assert "planned_hours = Column(Float, nullable=True)" in model
    assert "work_type = Column(String(32), nullable=True)" in model
    main = repo_src("main.py")
    assert '("timesheets", "planned_hours", "DOUBLE PRECISION")' in main
    assert '("timesheets", "work_type", "VARCHAR(32)")' in main


def test_board_and_my_day_are_gated_by_the_timesheets_module():
    src = code_only(repo_src("routers/api_timesheets.py"))
    assert 'check_admin_or_module(request, "timesheets")' in func_body(src, "async def day_board(")
    for fn in ("async def my_day(", "async def my_add_rows(", "async def my_update_row(", "async def my_delete_row("):
        assert 'bound_ident(request, "timesheets")' in func_body(src, fn), fn
    # 看板不排名、不標紅：回的是每個人的工作項，沒有「漏填」欄位
    board = func_body(src, "async def day_board(")
    assert '"missing' not in board and '"rank' not in board
    # 改列走服務那一份（同員工頁）
    assert "update_row(session, ident, row_id, body)" in func_body(src, "async def my_update_row(")
    # 總表：看＝模組、改刪＝管理員；套欄位走服務那一份
    assert 'check_admin_or_module(request, "timesheets")' in func_body(src, "async def ledger_rows(")
    for fn in ("async def ledger_update_row(", "async def ledger_delete_row("):
        assert "check_admin(request)" in func_body(src, fn), fn
    assert "admin_update_row(session, row_id, body)" in func_body(src, "async def ledger_update_row(")
    # 管理員備註只進管理員的 JSON（總表依 is_admin、抽查列 admin-only）；ts_dict 預設不帶
    assert "ts_dict(r, with_note=is_admin)" in func_body(src, "async def ledger_rows(")
    svc = code_only(repo_src("services/timesheet_self.py"))
    assert "if with_note:" in func_body(svc, "def ts_dict(")


def test_tab_has_the_seven_views_and_the_daily_board_shows_what_not_how_much():
    js = repo_src("frontend/tabs/timesheets/timesheets.js")
    for key in ("today", "mine", "projects", "staff", "ledger", "dash", "settings"):
        assert f"b('{key}'," in js, key
    code = js_code_only(js)
    assert "/api/v1/timesheets/board?date=" in code
    assert "/api/v1/timesheets/mine?date=" in code and "/api/v1/timesheets/mine/rows" in code
    # 時數快捷鈕與「複製昨天」（員工角度的兩個減負擔）
    assert "data-ts-action=\"copy-yesterday\"" in js and "data-hq=" in js
