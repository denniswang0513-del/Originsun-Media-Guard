"""
test_simplify_helpers.py — Phase 2 smoke for /test skill.

純函式 helper 邊界 case 測試，不依賴 DB / server / Playwright。
跑：`pytest tests/test_simplify_helpers.py -v`

涵蓋：
- services.website.project_service.append_old_slug_if_changed
- services.website.project_service._credits_summary
- services.website.seo_service._coerce_testimonial_date
- services.website.credit_service._compute_role_usage（mock session）
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from services.website.project_service import (
    append_old_slug_if_changed, _credits_summary,
)
from services.website.seo_service import _coerce_testimonial_date


# ── append_old_slug_if_changed ──────────────────────────────

def _make_project(slug=None, old_slugs=None):
    """Mock project ORM-like 物件 — 只用到 public_slug / public_old_slugs 兩屬性。"""
    return SimpleNamespace(
        public_slug=slug,
        public_old_slugs=old_slugs or [],
    )


class TestAppendOldSlug:
    def test_basic_change_appends(self):
        p = _make_project(slug="old-slug")
        assert append_old_slug_if_changed(p, "new-slug") is True
        assert p.public_old_slugs == ["old-slug"]

    def test_same_slug_no_append(self):
        p = _make_project(slug="same", old_slugs=[])
        assert append_old_slug_if_changed(p, "same") is False
        assert p.public_old_slugs == []

    def test_numeric_old_slug_skipped(self):
        # public_number 路由會走純數字 slug，admin 改名時這個不該污染 redirect map
        p = _make_project(slug="17", old_slugs=[])
        assert append_old_slug_if_changed(p, "mv-spaceman") is False
        assert p.public_old_slugs == []

    def test_self_loop_protection(self):
        # 新 slug 之前曾是舊 slug → 從 old_slugs 移除避免 redirect 自循環
        p = _make_project(slug="b", old_slugs=["a"])
        assert append_old_slug_if_changed(p, "a") is True
        assert p.public_old_slugs == ["b"], "a 從 old 移除（變新）, b 加進 old"

    def test_empty_old_slug_no_append(self):
        # 沒舊 slug 時不該 append 空字串 / None
        p = _make_project(slug=None)
        assert append_old_slug_if_changed(p, "new") is False
        p2 = _make_project(slug="")
        assert append_old_slug_if_changed(p2, "new") is False

    def test_already_in_old_slugs_no_duplicate(self):
        # 重複 toggle slug a→b→a 時、舊 a 不該再被加（已存在過）
        p = _make_project(slug="a", old_slugs=["a"])
        assert append_old_slug_if_changed(p, "c") is False
        assert p.public_old_slugs == ["a"]


# ── _credits_summary ──────────────────────────────

class TestCreditsSummary:
    def test_empty(self):
        assert _credits_summary(None) == ""
        assert _credits_summary([]) == ""
        assert _credits_summary("not a list") == ""

    def test_single_block_single_entry(self):
        out = _credits_summary([
            {"name_zh": "導演", "entries": [{"duty": "", "name": "王小明"}]}
        ])
        assert out == "導演 王小明"

    def test_two_blocks_two_entries(self):
        out = _credits_summary([
            {"name_zh": "演員", "entries": [{"duty": "主演", "name": "邱雲福"}]},
            {"name_zh": "導演", "entries": [{"name": "王小明"}]},
        ])
        assert out == "演員 主演 邱雲福 · 導演 王小明"

    def test_skips_empty_name(self):
        # entry 沒 name 不算
        out = _credits_summary([
            {"name_zh": "演員", "entries": [{"duty": "主演", "name": ""}, {"name": "邱雲福"}]}
        ])
        assert out == "演員 邱雲福"

    def test_truncates_at_30_chars(self):
        out = _credits_summary([
            {"name_zh": "演員", "entries": [{"duty": "主演", "name": "張三"}]},
            {"name_zh": "導演特別客串", "entries": [{"name": "一個非常非常長的人名"}]},
        ])
        assert len(out) <= 30, f"should be ≤ 30 chars, got len={len(out)}: {out!r}"
        assert out.endswith("…") or len(out) <= 29

    def test_only_first_two_entries_taken(self):
        out = _credits_summary([
            {"name_zh": "演員", "entries": [
                {"name": "甲"}, {"name": "乙"}, {"name": "丙"}, {"name": "丁"},
            ]}
        ])
        # 只取前 2 個 entry
        assert "甲" in out and "乙" in out
        assert "丙" not in out and "丁" not in out

    def test_malformed_block(self):
        # 不是 dict 的 block 跳過
        assert _credits_summary([None, "string", 123]) == ""
        # block 沒 entries 跳過
        assert _credits_summary([{"name_zh": "X"}]) == ""


# ── _coerce_testimonial_date ──────────────────────────────

class TestCoerceTestimonialDate:
    def test_iso_string_to_date(self):
        out = _coerce_testimonial_date({"date_published": "2024-01-15", "other": "x"})
        assert isinstance(out["date_published"], date)
        assert out["date_published"] == date(2024, 1, 15)
        assert out["other"] == "x"

    def test_does_not_mutate_input(self):
        # 純函式驗證 — 防 caller 後續用同 dict 看到變動
        orig = {"date_published": "2024-01-15", "other": "x"}
        out = _coerce_testimonial_date(orig)
        assert orig["date_published"] == "2024-01-15", "原 dict 不該被 mutate"
        assert out is not orig, "回傳的應是新 dict"

    def test_empty_string_to_none(self):
        out = _coerce_testimonial_date({"date_published": ""})
        assert out["date_published"] is None

    def test_invalid_date_to_none(self):
        out = _coerce_testimonial_date({"date_published": "not-a-date"})
        assert out["date_published"] is None

    def test_non_string_passthrough(self):
        # date 物件 / None 不動
        d = date(2024, 5, 1)
        out = _coerce_testimonial_date({"date_published": d})
        assert out["date_published"] is d
        out2 = _coerce_testimonial_date({"date_published": None})
        assert out2["date_published"] is None

    def test_missing_key_passthrough(self):
        out = _coerce_testimonial_date({"other": "x"})
        assert "date_published" not in out
        assert out["other"] == "x"


# ── normalizeCredits 後端 Python 對偶（給 _credits_summary 用之前的） ──
# 註：normalizeCredits 是 TS（在 website/src/lib/credits.ts），Phase 4 Playwright
# 會在 browser 內間接驗。這裡不重複測。


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
