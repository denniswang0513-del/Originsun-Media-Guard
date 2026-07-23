# -*- coding: utf-8 -*-
"""core/drive_map — 磁碟代號 ↔ UNC 翻譯（2026-07-21 煥民新村備份 6 連敗的治本）。"""
from types import SimpleNamespace

import pytest

import config
from core.drive_map import (DEFAULT_DRIVE_MAP, effective_map, translate_path,
                            translate_job_request)

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
    @pytest.fixture(autouse=True)
    def _reset_cache(self, monkeypatch):
        # effective_map 以 settings.json mtime 快取 — 測試各自 monkeypatch
        # load_settings，快取不清會吃到上一個測試的合併結果
        import core.drive_map as dm
        monkeypatch.setattr(dm, "_map_cache", (None, None))

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


class TestTranslateJobRequest:
    """enqueue_job 中央咽喉的欄位翻譯 — HTTP/排程/書籤三條進件路共用。"""

    @pytest.fixture(autouse=True)
    def _pure_defaults(self, monkeypatch):
        import core.drive_map as dm
        monkeypatch.setattr(dm, "_map_cache", (None, None))
        monkeypatch.setattr(config, "load_settings", lambda: {})

    def test_backup_fields(self):
        r = SimpleNamespace(local_root=r"T:\p\00_Edit", nas_root=r"V:\備份",
                            proxy_root=r"S:\p", report_output="",
                            cards=[("Card_A", r"E:\卡"), ("Card_B", r"T:\backup")])
        ch = translate_job_request(r, "backup")
        assert r.local_root.startswith(r"\\192.168.1.132\Project_Longterm")
        assert r.nas_root.startswith(r"\\192.168.1.130\storage 01")
        assert r.cards[0] == ("Card_A", r"E:\卡")          # 本機碟不動
        assert r.cards[1][1].startswith(r"\\192.168.1.132")
        assert len(ch) == 4                                 # local/nas/proxy/卡B（report_output 空跳過）

    def test_concat_and_transcode(self):
        r = SimpleNamespace(sources=[r"T:\a", r"E:\b"], dest_dir=r"S:\out")
        translate_job_request(r, "concat")
        assert r.sources[0].startswith("\\\\") and r.sources[1] == r"E:\b"
        assert r.dest_dir.startswith(r"\\192.168.1.132\Project_Midterm")

    def test_verify_pairs_both_sides(self):
        r = SimpleNamespace(pairs=[(r"T:\a", r"V:\b")])
        translate_job_request(r, "verify")
        assert r.pairs[0][0].startswith(r"\\192.168.1.132")
        assert r.pairs[0][1].startswith(r"\\192.168.1.130")

    def test_report_fields(self):
        r = SimpleNamespace(source_dir=r"T:\p", output_dir=r"S:\p", nas_root="",
                            exclude_dirs=[r"S:\p\proxy"])
        translate_job_request(r, "report")
        assert r.source_dir.startswith("\\\\") and r.exclude_dirs[0].startswith("\\\\")

    def test_missing_attr_skipped(self):
        r = SimpleNamespace(output_dir=r"T:\out")   # TtsRequest 沒 reference_audio
        assert translate_job_request(r, "tts")
        assert r.output_dir.startswith("\\\\")

    def test_unknown_task_type_noop(self):
        assert translate_job_request(SimpleNamespace(x=1), "unknown") == []

    def test_registry_fields_exist_on_real_schemas(self):
        """防漂移守衛：_JOB_PATH_FIELDS 登記的欄位必須真的存在於對應 schema
        （欄位改名時 getattr 靜默跳過 → 無聲退回未翻譯的失敗模式，這裡先紅）。"""
        from core import schemas
        from core.drive_map import _JOB_PATH_FIELDS
        owners = {
            "backup": [schemas.BackupRequest],
            "transcode": [schemas.TranscodeRequest],
            "concat": [schemas.ConcatRequest],
            "verify": [schemas.VerifyRequest],
            "transcribe": [schemas.TranscribeRequest],
            "report": [schemas.ReportJobRequest],
            "tts": [schemas.TtsRequest, schemas.TtsCloneRequest],
        }
        for task_type, fields in _JOB_PATH_FIELDS.items():
            classes = owners.get(task_type)
            assert classes, f"registry 有 {task_type} 但守衛表沒有對應 schema — 兩邊要同步"
            for name, _kind in fields:
                assert any(name in c.model_fields for c in classes), \
                    f"{task_type}.{name} 不存在於 {[c.__name__ for c in classes]}"


class TestInvalidPathReason:
    """路徑語法快篩 — 2026-07-23 C:/A_TEST_PROXYS: 黏路徑實案的進件防線。"""

    def test_glued_paths_caught(self):
        from core.drive_map import invalid_path_reason
        assert invalid_path_reason("C:/A_TEST_PROXYS:/20260706_x/99_Transcode") is not None

    def test_normal_paths_ok(self):
        from core.drive_map import invalid_path_reason
        for p in (r"T:\專案\00_Edit", r"\192.168.1.132\Project_Longterm\a",
                  "C:/A_TEST_PROXY", "", r"E:\卡匣"):
            assert invalid_path_reason(p) is None, p

    def test_bad_chars(self):
        from core.drive_map import invalid_path_reason
        assert invalid_path_reason(r"C:\a<b") is not None
        assert invalid_path_reason(r"C:\a|b") is not None

    def test_first_invalid_job_path_names_field(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: {})
        import core.drive_map as dm
        monkeypatch.setattr(dm, "_map_cache", (None, None))
        from core.drive_map import first_invalid_job_path
        r = SimpleNamespace(sources=[r"E:\ok"], dest_dir="C:/A_TEST_PROXYS:/x")
        msg = first_invalid_job_path(r, "concat")
        assert msg and "dest_dir" in msg and "冒號" in msg

    def test_cards_and_pairs_expanded(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: {})
        from core.drive_map import first_invalid_job_path
        r = SimpleNamespace(local_root="", nas_root="", proxy_root="", report_output="",
                            cards=[("A", "C:/bad:/path")])
        assert "cards" in (first_invalid_job_path(r, "backup") or "")
        r2 = SimpleNamespace(pairs=[(r"T:\ok", "C:/bad:/x")])
        assert "pairs" in (first_invalid_job_path(r2, "verify") or "")
