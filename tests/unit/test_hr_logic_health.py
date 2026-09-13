# -*- coding: utf-8 -*-
"""core/hr_logic.py 四支沒測過的小 helper —— 特徵測試（/health 2026-09-13）。

模組 30 天改了 33 次、整體 91% 覆蓋，就這四支漏網：bucket_hours 是專案各月／
人員熱圖／團隊各月的共用加總，parse_ymd 是所有日期查詢參數的入口。
"""
from datetime import date, datetime

from core import hr_logic as hl


class TestBucketHours:
    def test_sums_per_key_rounded_to_one_decimal(self):
        assert hl.bucket_hours([("a", 1.25), ("a", 1.25), ("b", 0.05)]) == {"a": 2.5, "b": 0.1}

    def test_skips_none_key_and_non_positive_hours(self):
        assert hl.bucket_hours([(None, 5), ("a", 0), ("a", None), ("a", -1), ("b", 2)]) == {"b": 2.0}

    def test_empty_is_empty_dict(self):
        assert hl.bucket_hours([]) == {}


class TestMonthsBack:
    def test_zero_is_same_month(self):
        m0 = datetime(2026, 9, 1)
        assert hl.months_back(m0, 0) == m0

    def test_eleven_back_spans_a_year(self):
        assert hl.months_back(datetime(2026, 9, 1), 11) == datetime(2025, 10, 1)

    def test_crosses_multiple_years(self):
        assert hl.months_back(datetime(2026, 1, 1), 25) == datetime(2023, 12, 1)


class TestPrevWorkday:
    def test_monday_goes_back_to_friday(self):
        assert hl.prev_workday(date(2026, 9, 14)) == date(2026, 9, 11)   # 一 → 上週五

    def test_sunday_and_saturday_go_back_to_friday(self):
        assert hl.prev_workday(date(2026, 9, 13)) == date(2026, 9, 11)   # 日
        assert hl.prev_workday(date(2026, 9, 12)) == date(2026, 9, 11)   # 六

    def test_midweek_is_yesterday(self):
        assert hl.prev_workday(date(2026, 9, 16)) == date(2026, 9, 15)   # 三 → 二


class TestParseYmd:
    def test_empty_or_none_is_none(self):
        assert hl.parse_ymd(None) is None
        assert hl.parse_ymd("") is None
        assert hl.parse_ymd("   ") is None

    def test_takes_first_ten_chars_so_iso_datetime_is_fine(self):
        assert hl.parse_ymd("2026-09-13T10:20:30") == datetime(2026, 9, 13)
        assert hl.parse_ymd(" 2026-09-13 ") == datetime(2026, 9, 13)

    def test_bad_format_is_none_not_exception(self):
        assert hl.parse_ymd("13/09/2026") is None
        assert hl.parse_ymd("2026-13-01") is None
