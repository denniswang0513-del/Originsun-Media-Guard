# -*- coding: utf-8 -*-
"""/health 2026-09-16 特徵測試：services/timesheet_ingest 的純函式（Sheet 列指紋 row_hash、日期容錯 parse_date；
模組覆蓋率 18%）。row_hash 是去重與「刪過的列留墓碑」兩件事的鍵——公式一動，整本 Sheet 下次拉取會全部重複入庫
或墓碑全部失效，所以把現在的 digest 逐字釘住。只釘現在的行為，不判斷對錯。整檔可刪。"""
from datetime import datetime

from services import timesheet_ingest as ti


def test_row_hash_is_sha1_of_pipe_joined_stripped_fields():
    # 逐字釘住：改公式（欄位順序、分隔符、strip）這裡會紅
    assert ti.row_hash("2026/6/30", "小明", "案A", "剪接", 8) == "550140163d6abe6849a4c2854e4f9b66a019918e"
    # 文字欄兩端空白不算差異（Sheet 手打常有）
    assert ti.row_hash(" 2026/6/30 ", " 小明", "案A ", "剪接", 8) == ti.row_hash("2026/6/30", "小明", "案A", "剪接", 8)
    # None 一律當空字串
    assert ti.row_hash(None, None, None, None, None) == "e0cbc8ba9fc51d7ddddd296b56ba825492979d68"


def test_row_hash_is_sensitive_to_the_hours_type_so_callers_must_keep_passing_float():
    # hours 走 f-string：8（int）與 "8" 同、8.0 不同。ingest 走 TimesheetRow.hours: Optional[float]，
    # 所以現況永遠是 8.0 —— 別把 hours 改成 int 或字串再丟進來，整本 Sheet 會被當成新列。
    assert ti.row_hash("d", "s", "p", "t", 8) == ti.row_hash("d", "s", "p", "t", "8")
    assert ti.row_hash("d", "s", "p", "t", 8) != ti.row_hash("d", "s", "p", "t", 8.0)
    assert ti.row_hash("2026/6/30", "小明", "案A", "剪接", 8.0) == "356c91ee8b460831751ca9fb8d7fe5fd70e98eed"


def test_parse_date_accepts_three_separators_and_returns_none_otherwise():
    assert ti.parse_date("2026/6/30") == datetime(2026, 6, 30)
    assert ti.parse_date("2026-06-30") == datetime(2026, 6, 30)
    assert ti.parse_date("2026.06.30") == datetime(2026, 6, 30)
    assert ti.parse_date("  2026/06/30 ") == datetime(2026, 6, 30)     # 兩端空白容錯
    assert ti.parse_date("") is None and ti.parse_date(None) is None
    assert ti.parse_date("30/06/2026") is None and ti.parse_date("2026/6/30 10:00") is None
