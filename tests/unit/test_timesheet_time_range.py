# -*- coding: utf-8 -*-
"""我的一天格子的「起／訖」要存起來（owner 2026-09-06：重新整理會消失）。

鏈：格子 rowBody 送 start_time／end_time → TimesheetManualRow 收 → normalize_row 進 fields
（沒帶＝不碰既有值）→ Timesheet.start_time／end_time → ts_dict 回 → rowFromItem 填回 t0／t1。
少一環，起訖就又會在重新整理後消失。
"""
from services.timesheet_manual import hhmm_or_none
from tests.unit._srcscan import js_code_only, js_func_body, migration_sql, repo_src, schemas_src


def test_hhmm_normalizes_like_the_grid():
    assert hhmm_or_none("9:30") == "09:30"
    assert hhmm_or_none("1330") == "13:30"
    assert hhmm_or_none("17:30") == "17:30"
    for bad in ("", None, "25:00", "9:75", "abc"):
        assert hhmm_or_none(bad) is None, bad


def test_columns_migration_schema_and_serializer_are_wired():
    model = repo_src("db/models/_workos.py")
    assert "start_time = Column(String(5), nullable=True)" in model and "end_time = Column(String(5), nullable=True)" in model
    main = migration_sql()
    assert '("timesheets", "start_time", "VARCHAR(5)")' in main and '("timesheets", "end_time", "VARCHAR(5)")' in main
    schema = schemas_src()
    assert "start_time: Optional[str] = None" in schema and "end_time: Optional[str] = None" in schema
    manual = repo_src("services/timesheet_manual.py")
    # body 沒帶＝不進 fields（總表管理員改列不能把員工填的起訖洗掉）
    assert '"start_time": start, "end_time": end' in manual
    # 「body 沒帶＝沿用列上的」只有 apply_update 的 carry 一條規則（含起訖／備註／計畫）
    self_src = repo_src("services/timesheet_self.py")
    assert 'for k in ("planned_hours", "remark", "start_time", "end_time"' in self_src
    self_ = repo_src("services/timesheet_self.py")
    assert '"start_time": getattr(r, "start_time", None) or ""' in self_ and '"end_time": getattr(r, "end_time", None) or ""' in self_


def test_grid_reads_and_writes_the_times():
    js = js_code_only(repo_src("frontend/js/shared/ts-sheet.js"))
    assert "t0: i.start_time || '', t1: i.end_time || ''" in js_func_body(js, "export function rowFromItem(")
    body = js_func_body(js, "export function rowBody(")
    assert "if (tr.querySelector('[data-f=\"t0\"]')) { body.start_time = v('t0'); body.end_time = v('t1'); }" in body
