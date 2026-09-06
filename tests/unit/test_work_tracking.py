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
    assert "row_state(hours, r.planned_hours)" in rule   # hours＝body 的或起訖算出來的 and "norm_work_type(r.work_type)" in rule
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
    # 我的一天：timesheets 模組 **或** 任何 me_* 鑰匙（員工頁 /my.html 也打同一支）；兩條路都要綁定人員檔案
    for fn in ("async def my_day(", "async def my_add_rows(", "async def my_update_row(", "async def my_delete_row("):
        assert "_mine_ident(request)" in func_body(src, fn), fn
    mine = func_body(src, "async def _mine_ident(")
    assert 'require_bound_staff(request, "timesheets", *_ME_KEYS)' in mine   # 2026-09-06：一支守衛收多把鑰匙
    # 看板不排名、不標紅：回的是每個人的工作項，沒有「漏填」欄位
    board = func_body(src, "async def day_board(")
    assert '"missing' not in board and '"rank' not in board
    # 改列走服務那一份（同員工頁）
    assert "update_row(session, ident, row_id, body)" in func_body(src, "async def my_update_row(")
    # 總表：看＝模組、改刪＝管理員；套欄位走服務那一份
    assert 'check_admin_or_module(request, "timesheets")' in func_body(src, "async def ledger_rows(")
    for fn in ("async def ledger_update_row(", "async def ledger_delete_row("):
        assert "check_admin(request)" in func_body(src, fn), fn
    assert "admin_update_row(session, row_id, body, current_username(request))" in func_body(src, "async def ledger_update_row(")
    # 管理員備註只進管理員的 JSON（總表依 is_admin、抽查列 admin-only）；ts_dict 預設不帶
    assert "ts_dict(r, with_note=is_admin)" in func_body(src, "async def ledger_rows(")
    svc = code_only(repo_src("services/timesheet_self.py"))
    assert "if with_note:" in func_body(svc, "def ts_dict(")
    # 總表刪掉的 Sheet 列留指紋、ingest 看到就跳過（總表為準；手填列不留）
    dele = func_body(svc, "async def admin_delete_row(")
    assert "add_tombstone(session, r.row_hash" in dele and 'r.source != "manual"' in dele
    ing = code_only(repo_src("services/timesheet_ingest.py"))
    assert "select(TimesheetTombstone.row_hash)" in func_body(ing, "async def ingest_context(")
    assert "if h in tombstones:" in func_body(ing, "async def ingest(")
    # 總表改過的同一列、Sheet 又變 → 記衝突不插不蓋；改過就標 edited_at；三種決定只在 services
    ib = func_body(ing, "async def ingest(")
    assert "edited_keys.get(manual_dup_key(" in ib and "TimesheetConflict(" in ib
    assert ib.index("if h in tombstones:") < ib.index("edited_keys.get(")
    upd = func_body(svc, "async def admin_update_row(")
    assert 'r.source != "manual"' in upd and "r.edited_at" in upd
    cf = code_only(repo_src("services/timesheet_conflicts.py"))
    assert 'CHOICES = ("keep_mine", "use_sheet", "keep_both")' in cf
    rc = func_body(cf, "async def resolve_conflict(")
    assert "add_tombstone(" in rc and "r.row_hash = c.incoming_hash" in rc and '"sheet", ctx)' in rc
    for fn in ("async def ledger_conflicts(", "async def ledger_resolve_conflict("):
        assert "check_admin(request)" in func_body(src, fn), fn
    assert '("timesheets", "edited_at", "TIMESTAMPTZ")' in repo_src("main.py")


def test_tab_has_the_seven_views_and_the_daily_board_shows_what_not_how_much():
    js = repo_src("frontend/tabs/timesheets/timesheets.js")
    for key in ("today", "mine", "projects", "staff", "ledger", "dash", "settings"):
        assert f"b('{key}'," in js, key
    code = js_code_only(js)
    assert "/api/v1/timesheets/board?date=" in code
    assert "/api/v1/timesheets/mine?date=" in code
    # 「複製昨天」＋ Sheet 式格子（起訖自動算、Enter／↑↓ 走列、走到底自動長列）—— 員工角度的減負擔。
    # 2026-09-05 起格子本體抽到 js/shared/ts-sheet.js（/my.html 同一份），tab 只剩「複製昨天」動作與掛載
    assert "data-ts-action=\"copy-yesterday\"" in js and "renderSheet(" in code
    sheet = js_code_only(repo_src("frontend/js/shared/ts-sheet.js"))
    assert 'data-f="t0"' in sheet and "/api/v1/timesheets/mine/rows" in sheet
    assert "function _keydown(" in sheet and "function _grow(" in sheet and "applyTimeRange(" in sheet
