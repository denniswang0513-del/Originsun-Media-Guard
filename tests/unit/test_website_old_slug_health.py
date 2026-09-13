# -*- coding: utf-8 -*-
"""services/website/project_service.py 舊網址正規化 —— 特徵測試（/health 2026-09-13）。

PM 貼進來的舊網址長什麼樣都有；這兩支決定 301 表的 key 長什麼樣，沒測過。
"""
from services.website.project_service import normalize_old_slug_input, normalize_old_slugs_input


class TestNormalizeOldSlugInput:
    def test_full_url_keeps_only_path_and_trims_trailing_slash(self):
        assert normalize_old_slug_input("https://www.originsun-studio.com/portfolio/abc/") == "/portfolio/abc"
        assert normalize_old_slug_input("http://other.example.com/x/y") == "/x/y"

    def test_url_with_no_path_is_none(self):
        assert normalize_old_slug_input("https://www.originsun-studio.com") is None

    def test_relative_path_and_bare_slug_pass_through(self):
        assert normalize_old_slug_input("/portfolio/abc/") == "/portfolio/abc"
        assert normalize_old_slug_input("  old-name ") == "old-name"
        assert normalize_old_slug_input("/") == "/"          # 只有根不砍

    def test_empty_is_none(self):
        assert normalize_old_slug_input("") is None
        assert normalize_old_slug_input(None) is None

    def test_whitespace_quotes_or_control_chars_are_rejected(self):
        assert normalize_old_slug_input("/a b") is None
        assert normalize_old_slug_input("/a'b") is None
        assert normalize_old_slug_input('/a"b') is None
        assert normalize_old_slug_input("/a\x01b") is None


class TestNormalizeOldSlugsInput:
    def test_dedupes_preserving_order_and_drops_non_strings(self):
        assert normalize_old_slugs_input(["/a/", "/a", 3, None, "", "/b", "https://x.com/a"]) == ["/a", "/b"]

    def test_empty_inputs(self):
        assert normalize_old_slugs_input(None) == []
        assert normalize_old_slugs_input([]) == []
