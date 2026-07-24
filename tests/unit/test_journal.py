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


class TestSectionRegistryConsistency:
    """entry 表 owner 要求分表、router 假設同構，且三方（registry / JournalPut /
    前端 BLOCKS）必須同一組 key —— 任何一方漏改都是靜默壞法，這裡先紅。"""

    def test_columns_identical(self):
        # model 清單從 registry 導出 — 加第五表只改 registry，本測試自動涵蓋
        from routers.api_journal import _SECTION_MODELS

        def cols(model):
            return {(c.name, type(c.type).__name__) for c in model.__table__.columns}

        models = [m for _, m in _SECTION_MODELS]
        base = cols(models[0])
        for m in models[1:]:
            assert cols(m) == base, f"{m.__tablename__} 與 {models[0].__tablename__} 欄位不同構"

    def test_registry_matches_put_schema(self):
        # registry key 缺 JournalPut 欄位 → PUT getattr 直接 500；反向漏加欄
        # → 該區永遠收不到資料。兩邊互為鏡像，這裡釘死。
        from core.schemas import JournalPut
        from routers.api_journal import _SECTION_MODELS
        assert [k for k, _ in _SECTION_MODELS] == list(JournalPut.model_fields)

    def test_frontend_blocks_match_registry(self):
        # 前端 BLOCKS 漏一個後端 key → saveMine 的 PUT body 少該欄 → 預設 []
        # → 全量替換把該區已存資料「靜默清空」。失敗模式=資料流失，值得跨語言守。
        import re
        from pathlib import Path

        from routers.api_journal import _SECTION_MODELS
        root = Path(__file__).resolve().parents[2]   # tests/unit/ → repo root
        js = (root / "frontend/js/shared/journal-core.js").read_text(encoding="utf-8")
        m = re.search(r"export const BLOCKS = \[(.*?)\];", js, re.S)
        assert m, "journal-core.js 找不到 BLOCKS 陣列（改名了？測試要跟上）"
        keys = re.findall(r"\['(\w+)',", m.group(1))
        assert keys == [k for k, _ in _SECTION_MODELS], \
            f"前端 BLOCKS {keys} != 後端 registry {[k for k, _ in _SECTION_MODELS]}"
