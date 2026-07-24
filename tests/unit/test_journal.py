"""core/journal_logic 純函式單元測試（週一正規化 / 可編輯窗 / 條目清洗）。"""
from datetime import date, datetime

from core.journal_logic import (MAX_ENTRIES_PER_SECTION, MAX_ENTRY_LEN,
                                clean_entries, editable_window_ok,
                                week_start_of)


class TestWeekStartOf:
    def test_monday_maps_to_itself(self):
        assert week_start_of(date(2026, 7, 20)) == date(2026, 7, 20)  # 週一

    def test_midweek(self):
        assert week_start_of(date(2026, 7, 23)) == date(2026, 7, 20)  # 週四 → 週一

    def test_sunday_belongs_to_preceding_monday(self):
        # 週以週一起算 — 週日屬於「前面那個週一」開始的那一週
        assert week_start_of(date(2026, 7, 26)) == date(2026, 7, 20)

    def test_cross_year(self):
        # 2027-01-01（週五）→ 該週週一在前一年 2026-12-28
        assert week_start_of(date(2027, 1, 1)) == date(2026, 12, 28)

    def test_cross_year_sunday(self):
        # 2028-01-02（週日）→ 週一 2027-12-27（跨年 + 週日輸入雙重邊界）
        assert week_start_of(date(2028, 1, 2)) == date(2027, 12, 27)

    def test_accepts_datetime(self):
        assert week_start_of(datetime(2026, 7, 23, 14, 30)) == date(2026, 7, 20)


class TestEditableWindow:
    TODAY = date(2026, 7, 23)  # 週四；本週週一 = 2026-07-20

    def test_past_week_ok(self):
        assert editable_window_ok(date(2026, 6, 1), self.TODAY)

    def test_distant_past_ok(self):
        assert editable_window_ok(date(2020, 1, 6), self.TODAY)

    def test_current_week_ok(self):
        assert editable_window_ok(date(2026, 7, 20), self.TODAY)

    def test_next_week_ok(self):
        # owner 規則 2026-07-22：下一週恆可編（週一 + 7 天 = 邊界含）
        assert editable_window_ok(date(2026, 7, 27), self.TODAY)

    def test_week_after_next_rejected(self):
        assert not editable_window_ok(date(2026, 8, 3), self.TODAY)

    def test_far_future_rejected(self):
        assert not editable_window_ok(date(2027, 1, 4), self.TODAY)


class TestCleanEntries:
    def test_strip_and_drop_empty(self):
        cleaned, err = clean_entries(["  a  ", "", "   ", "b"])
        assert err is None
        assert cleaned == ["a", "b"]

    def test_order_preserved(self):
        cleaned, err = clean_entries(["3", "1", "2"])
        assert err is None
        assert cleaned == ["3", "1", "2"]

    def test_none_and_empty_input(self):
        assert clean_entries(None) == ([], None)
        assert clean_entries([]) == ([], None)

    def test_max_entries_boundary(self):
        ok, err = clean_entries(["x"] * MAX_ENTRIES_PER_SECTION)
        assert err is None and len(ok) == MAX_ENTRIES_PER_SECTION
        _, err = clean_entries(["x"] * (MAX_ENTRIES_PER_SECTION + 1))
        assert err is not None

    def test_blank_entries_dont_count_toward_limit(self):
        # 上限在清洗後檢查 — 純空白條目不佔額度
        raw = ["x"] * MAX_ENTRIES_PER_SECTION + ["  ", ""]
        cleaned, err = clean_entries(raw)
        assert err is None
        assert len(cleaned) == MAX_ENTRIES_PER_SECTION

    def test_max_length_boundary(self):
        ok, err = clean_entries(["字" * MAX_ENTRY_LEN])
        assert err is None and len(ok[0]) == MAX_ENTRY_LEN
        _, err = clean_entries(["字" * (MAX_ENTRY_LEN + 1)])
        assert err is not None

    def test_length_checked_after_strip(self):
        # 前後空白 strip 後才量長度 — 剛好貼上限的內容不因空白誤殺
        padded = "  " + "y" * MAX_ENTRY_LEN + "  "
        cleaned, err = clean_entries([padded])
        assert err is None
        assert cleaned == ["y" * MAX_ENTRY_LEN]


class TestEntryTablesIsomorphic:
    """四張 entry 表（wins/challenges/learnings/others）owner 要求分表，但 router 的
    _SECTION_MODELS 假設同構 — 未來加欄位漏改某一張，在這裡先紅。"""

    def test_columns_identical(self):
        from db.models import (JournalChallenge, JournalLearning, JournalOther,
                               JournalWin)

        def cols(model):
            return {(c.name, type(c.type).__name__) for c in model.__table__.columns}

        assert cols(JournalWin) == cols(JournalChallenge) \
            == cols(JournalLearning) == cols(JournalOther)

    def test_section_models_cover_all_entry_tables(self):
        # registry 漏列新表 → API 靜默不讀不寫該區，這裡先紅
        from routers.api_journal import _SECTION_MODELS
        assert [k for k, _ in _SECTION_MODELS] == \
            ["wins", "challenges", "learnings", "others"]
