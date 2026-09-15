# -*- coding: utf-8 -*-
"""/health 2026-09-16 特徵測試：services/timesheet_self 的純函式（唯一序列化 ts_dict、month_or_422、
metrics_input／rows_by_month、stage 唯一寫入點 set_stage；模組覆蓋率 22%、30 天改 24 次）。
只釘現在的行為，不判斷對錯。整檔可刪。"""
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from services import timesheet_self as ts


def _row(**kw):
    base = dict(id="T1", work_date=datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc), staff_id="s1", staff_name="小明",
                project_name="案A", project_id="P1", task_note="剪接", remark=None, hours=7.999, planned_hours=None,
                work_type="後期", source="manual", status="draft", edited_at=None, note="主管的話")
    base.update(kw)
    return NS(**base)


def test_ts_dict_shape_dates_in_taipei_and_missing_optional_columns_become_empty():
    d = ts.ts_dict(_row())
    assert d["date"] == "2026-09-15"                       # UTC 16:00 ＝ 台北隔天 00:00
    assert d["hours"] == 8.0 and d["planned_hours"] is None
    assert d["edited"] is False and d["remark"] == ""
    # 舊列沒有這些欄位 → getattr 退空字串，不炸
    for k in ("start_time", "end_time", "planned_by", "stage_id", "stage_name", "bulletin_id"):
        assert d[k] == ""
    assert "note" not in d and "editable" not in d         # 沒要求就不帶
    d = ts.ts_dict(_row(planned_hours=3.456, edited_at=datetime(2026, 9, 15), work_date=date(2026, 9, 1)))
    assert d["planned_hours"] == 3.46 and d["edited"] is True and d["date"] == "2026-09-01"


def test_ts_dict_note_only_with_flag_and_editable_only_with_staff_view():
    assert ts.ts_dict(_row(), with_note=True)["note"] == "主管的話"
    assert ts.ts_dict(_row(note=None), with_note=True)["note"] == ""
    assert ts.ts_dict(_row(), "s1")["editable"] is True                     # 本人＋手填＋草稿
    assert ts.ts_dict(_row(), "s2")["editable"] is False                    # 不是本人
    assert ts.ts_dict(_row(status="confirmed"), "s1")["editable"] is False  # 手填但已鎖
    assert ts.ts_dict(_row(source="sheet", status="confirmed"), "s1")["editable"] is True  # Sheet 列本人可改（claim）


def test_month_or_422_spans_a_month_rolls_year_and_rejects_bad_format():
    m0, m1 = ts.month_or_422("2026-09")
    assert (m0, m1) == (datetime(2026, 9, 1), datetime(2026, 10, 1))
    assert ts.month_or_422("2026-12")[1] == datetime(2027, 1, 1)
    m0, m1 = ts.month_or_422("")                                            # 空＝本月
    assert m0.day == 1 and m1 > m0
    with pytest.raises(HTTPException) as ei:
        ts.month_or_422("2026/09")
    assert ei.value.status_code == 422


def test_metrics_input_and_rows_by_month_bucket_in_taipei_and_skip_plan_rows():
    rows = [_row(hours=2), _row(hours=3, work_date=datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)),
            _row(hours=0, status="plan"), _row(hours=1.5, work_date=None)]
    mi = ts.metrics_input(rows)
    assert mi[0] == (date(2026, 9, 15), "小明", "後期", 2)
    assert mi[1][0] == date(2026, 10, 1)                                    # UTC 9/30 20:00 → 台北 10/1
    assert mi[3][0] is None
    assert ts.rows_by_month(rows) == [("2026-09", 2.0), ("2026-10", 3.0)]   # 0 小時與沒日期的不進


def test_set_stage_writes_both_mirror_columns_and_clears_on_none():
    r = NS(stage_id="old", stage_name="舊")
    ts.set_stage(r, {"id": "st1", "name": "粗剪", "extra": 1})
    assert (r.stage_id, r.stage_name) == ("st1", "粗剪")
    ts.set_stage(r, None)
    assert (r.stage_id, r.stage_name) == (None, None)
