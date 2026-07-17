"""core/hr_logic 純函式單元測試（請假驗證 / 特休餘額 / 工時去重鍵）。"""
from datetime import datetime

from core.hr_logic import (LEAVE_TYPES, leave_balance, manual_dup_key,
                           validate_leave)

D = datetime


class TestValidateLeave:
    def test_valid(self):
        assert validate_leave("特休", D(2026, 7, 20), D(2026, 7, 21), 2.0) is None

    def test_half_day_ok(self):
        assert validate_leave("事假", D(2026, 7, 20), D(2026, 7, 20), 0.5) is None

    def test_bad_type(self):
        assert validate_leave("補休", D(2026, 7, 20), D(2026, 7, 20), 1.0) is not None

    def test_all_types_accepted(self):
        for t in LEAVE_TYPES:
            assert validate_leave(t, D(2026, 1, 1), D(2026, 1, 1), 1.0) is None

    def test_missing_dates(self):
        assert validate_leave("特休", None, D(2026, 7, 20), 1.0) is not None
        assert validate_leave("特休", D(2026, 7, 20), None, 1.0) is not None

    def test_end_before_start(self):
        assert validate_leave("特休", D(2026, 7, 21), D(2026, 7, 20), 1.0) is not None

    def test_zero_or_negative_days(self):
        assert validate_leave("特休", D(2026, 7, 20), D(2026, 7, 20), 0) is not None
        assert validate_leave("特休", D(2026, 7, 20), D(2026, 7, 20), -1) is not None

    def test_quarter_day_rejected(self):
        assert validate_leave("特休", D(2026, 7, 20), D(2026, 7, 20), 0.25) is not None


class TestLeaveBalance:
    def test_normal(self):
        assert leave_balance(10, 3.5) == {"annual": 10, "used": 3.5, "remaining": 6.5}

    def test_unset_quota(self):
        # 額度未設定 → annual/remaining None，used 照算
        assert leave_balance(None, 2.0) == {"annual": None, "used": 2.0, "remaining": None}

    def test_zero_used(self):
        assert leave_balance(7, 0) == {"annual": 7, "used": 0, "remaining": 7}

    def test_overdrawn(self):
        # 超休呈現負數（提醒管理者），不 clamp
        assert leave_balance(3, 4.5)["remaining"] == -1.5


class TestManualDupKey:
    def test_same_key(self):
        a = manual_dup_key("王士源", D(2026, 7, 17, 9, 0), "官網改版")
        b = manual_dup_key(" 王士源 ", D(2026, 7, 17, 23, 59), "官網改版 ")
        assert a == b  # strip + 日期去時分 → 同鍵（同人+日+專案）

    def test_aware_utc_vs_naive_local(self):
        # timestamptz 讀回 aware UTC vs 寫入時 naive 本地 — 必須落同一本地日
        # （e2e 實抓的 bug：naive 17 日寫入 → 讀回 16 日 16:00Z）
        from datetime import timedelta, timezone as tz
        naive_local = D(2026, 7, 17, 0, 0)
        aware_utc = naive_local.astimezone(tz.utc)   # 依本機時區換算成 UTC
        assert manual_dup_key("x", naive_local, "p") == manual_dup_key("x", aware_utc, "p")

    def test_diff_project(self):
        a = manual_dup_key("王士源", D(2026, 7, 17), "A案")
        b = manual_dup_key("王士源", D(2026, 7, 17), "B案")
        assert a != b

    def test_none_date(self):
        assert manual_dup_key("x", None, "p")[1] is None
