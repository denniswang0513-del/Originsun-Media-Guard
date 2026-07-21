# -*- coding: utf-8 -*-
"""core/drive_map — 磁碟代號 ↔ UNC 翻譯（2026-07-21 煥民新村備份 6 連敗的治本）。"""
import config
from core.drive_map import DEFAULT_DRIVE_MAP, effective_map, translate_path

MAP = {"T": r"\\192.168.1.132\Project_Longterm", "V": r"\\192.168.1.130\storage 01"}


class TestTranslatePath:
    def test_basic(self):
        assert translate_path(r"T:\專案\00_EditFootage", MAP) == \
            r"\\192.168.1.132\Project_Longterm\專案\00_EditFootage"

    def test_root_only(self):
        assert translate_path("T:\\", MAP) == r"\\192.168.1.132\Project_Longterm"
        assert translate_path("T:", MAP) == r"\\192.168.1.132\Project_Longterm"

    def test_lowercase_letter(self):
        assert translate_path(r"t:\x", MAP).startswith(r"\\192.168.1.132")

    def test_forward_slash(self):
        assert translate_path("T:/a/b", MAP) == \
            r"\\192.168.1.132\Project_Longterm\a" + "/b"  # 只正規化開頭，餘下原樣

    def test_unmapped_letter_untouched(self):
        assert translate_path(r"E:\卡匣", MAP) == r"E:\卡匣"

    def test_unc_passthrough(self):
        p = r"\\192.168.1.132\Archive\x"
        assert translate_path(p, MAP) == p

    def test_share_with_space(self):
        assert translate_path(r"V:\拍攝檔案備份", MAP) == \
            r"\\192.168.1.130\storage 01\拍攝檔案備份"

    def test_empty_and_relative(self):
        assert translate_path("", MAP) == ""
        assert translate_path("relative/path", MAP) == "relative/path"


class TestEffectiveMap:
    def test_defaults_when_no_override(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: {})
        assert effective_map() == DEFAULT_DRIVE_MAP

    def test_override_add_and_replace(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: {
            "drive_map": {"z": r"\\10.0.0.9\zzz\\", "T:": r"\\10.0.0.9\newT"}})
        m = effective_map()
        assert m["Z"] == r"\\10.0.0.9\zzz"          # 小寫正規化 + 去尾斜線
        assert m["T"] == r"\\10.0.0.9\newT"          # 覆寫預設
        assert m["S"] == DEFAULT_DRIVE_MAP["S"]      # 其餘預設保留

    def test_tombstone_removes_default(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: {"drive_map": {"T": ""}})
        assert "T" not in effective_map()

    def test_bad_keys_ignored(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: {
            "drive_map": {"TT": r"\\x\y", "1": r"\\x\y", "": r"\\x\y"}})
        assert effective_map() == DEFAULT_DRIVE_MAP
