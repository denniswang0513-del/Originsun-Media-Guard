# -*- coding: utf-8 -*-
"""/health 2026-09-17 特徵測試：釘住 `services/timesheet_manual.normalize_row` 現在的行為
（30 天改 20 次、覆蓋 41%；09-16 報告點名「空白列 422」規則值得釘）。只釘現況、不判對錯；整檔可刪。

normalize_row 是手填列（管理端代填／員工自填／總表改列）**唯一**的欄位規則：
分類白名單、日期格式、時數由起訖推、空白列不存、project_id ＞ keep ＞ 名稱對映。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.hr_logic import ProjectLookup
from services.timesheet_manual import normalize_row


def _row(**kw):
    base = dict(work_date="2026-09-17", project_id=None, project_name="", task_note=None, remark=None,
                start_time=None, end_time=None, hours=None, planned_hours=None, work_type=None,
                stage_id=None, plan=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _lk(rows=(), project_map=None):
    return ProjectLookup.build(project_map or {}, rows)


def test_hours_from_range_only_when_hours_not_given_and_times_are_normalized():
    # 起訖給了、時數沒給 → 算出來；跨午夜 +24h；起訖字串落庫前正規化成 HH:MM
    f, why = normalize_row(_row(project_name="A案", start_time="9", end_time="1730"), _lk([("p1", "A案", "客")]), {})
    assert (f["start_time"], f["end_time"], f["hours"]) == ("09:00", "17:30", 8.5)
    assert f["status"] == "draft" and (f["project_id"], why) == ("p1", "exact")
    f, _ = normalize_row(_row(project_name="A案", start_time="22:00", end_time="02:00"), _lk([("p1", "A案", "客")]), {})
    assert f["hours"] == 4.0
    # 時數給了就以它為準，不重算
    f, _ = normalize_row(_row(project_name="A案", start_time="9:00", end_time="17:00", hours=3), _lk([("p1", "A案", "客")]), {})
    assert f["hours"] == 3.0


def test_blank_row_is_422_unless_any_content_field_is_filled():
    lk = _lk()
    with pytest.raises(HTTPException) as e:
        normalize_row(_row(), lk, {})
    assert e.value.status_code == 422 and "空白列不存" in e.value.detail
    # plan:true 的空 POST 一樣擋（plan 旗標不能繞過）
    with pytest.raises(HTTPException):
        normalize_row(_row(plan=True), lk, {})
    # 四種「填了任何一格」各自放行：專案／做了什麼／備註／工作階段
    for kw in (dict(project_name="X"), dict(task_note="做了"), dict(remark="備"), dict(stage_id="s1")):
        f, _ = normalize_row(_row(**kw), lk, {})
        assert f["status"] == "pending" and f["hours"] == 0.0, kw
    # 有時數就不是空白列，內容全空也收
    f, _ = normalize_row(_row(hours=1), lk, {})
    assert f["status"] == "draft"


def test_status_is_draft_plan_or_pending_and_sheet_rows_keep_status_none():
    lk = _lk()
    assert normalize_row(_row(task_note="x", hours=2), lk, {})[0]["status"] == "draft"
    assert normalize_row(_row(task_note="x", planned_hours=2), lk, {})[0]["status"] == "plan"
    assert normalize_row(_row(task_note="x", plan=True), lk, {})[0]["status"] == "plan"
    assert normalize_row(_row(task_note="x"), lk, {})[0]["status"] == "pending"
    # manual=False（改 Sheet 列）：status None、0 小時也放行、空白列也不擋（Sheet 本來就收）
    f, _ = normalize_row(_row(), lk, {}, manual=False)
    assert f["status"] is None and f["hours"] == 0.0


def test_work_type_whitelist_and_date_formats_are_422_with_the_rule_sentence():
    lk = _lk()
    with pytest.raises(HTTPException) as e:
        normalize_row(_row(task_note="x", work_type="打混"), lk, {})
    assert e.value.status_code == 422 and "工作分類只能是" in e.value.detail
    assert normalize_row(_row(task_note="x", work_type=" 拍攝 "), lk, {})[0]["work_type"] == "拍攝"
    assert normalize_row(_row(task_note="x", work_type=""), lk, {})[0]["work_type"] is None
    for raw in ("2026/9/17", "2026-09-17", "2026.09.17"):
        assert normalize_row(_row(work_date=raw, task_note="x"), lk, {})[0]["work_date"] == datetime(2026, 9, 17), raw
    with pytest.raises(HTTPException) as e:
        normalize_row(_row(work_date="17/09/2026", task_note="x"), lk, {})
    assert "日期格式錯誤" in e.value.detail


def test_project_resolution_order_is_id_then_keep_then_name_lookup():
    lk = _lk([("p1", "A案", "客"), ("p2", "B案", "客"), ("p3", "B案", "客")], project_map={"對映字": "p9"})
    # 給了 project_id → "map"，名稱空白就用 id_to_name 回填
    f, why = normalize_row(_row(project_id="p1", task_note="x"), lk, {"p1": "A案"})
    assert (f["project_id"], f["project_name"], why) == ("p1", "A案", "map")
    # 現況：只帶 project_id、沒帶案名也沒別的內容 → 仍是「空白列」422（id→名稱回填在空白檢查**之後**；
    # 兩個前端都會一起送 project_name，所以沒人踩到 —— 釘住是為了讓改順序的人看見）
    with pytest.raises(HTTPException):
        normalize_row(_row(project_id="p1"), lk, {"p1": "A案"})
    # keep=(案名, pid) → 沿用、不查表（lk 可為 None）
    f, why = normalize_row(_row(project_name="舊名"), None, {}, keep=("舊名", "p7"))
    assert (f["project_id"], why) == ("p7", "map")
    # 名稱走 resolve_project：對映表優先、內部桶不對映、撞案不猜、找不到 none
    assert normalize_row(_row(project_name="對映字"), lk, {})[1] == "map"
    assert normalize_row(_row(project_name="行政庶務"), lk, {})[0]["project_id"] is None
    assert normalize_row(_row(project_name="行政庶務"), lk, {})[1] == "bucket"
    assert normalize_row(_row(project_name="B案"), lk, {})[1] == "ambiguous"
    assert normalize_row(_row(project_name="沒這案"), lk, {})[1] == "none"
    # 文字欄位 strip、空 → None；planned_hours 沒給 → None
    f, _ = normalize_row(_row(project_name=" A案 ", task_note="  ", remark=" 備 "), lk, {})
    assert (f["project_name"], f["task_note"], f["remark"], f["planned_hours"]) == ("A案", None, "備", None)
