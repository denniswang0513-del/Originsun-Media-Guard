"""routers/crm/media_log.py 純函式 — 上傳檔名 sanitize / 分類正規化 /
副檔名白名單 / 撞名前綴（exists 用 callable 注入，不碰磁碟/DB/網路）。

資料夾命名規則的測試在 tests/unit/test_project_folders.py（正本已抽到
core.project_folders，三個資產子系統共用）。"""
from datetime import datetime

from routers.crm.media_log import (
    DEFAULT_MEDIA_LOG_CATEGORIES,
    _classify_ext,
    _dedup_filename,
    _norm_categories,
    _sanitize_filename,
)


class TestSanitizeFilename:
    def test_strips_path_components(self):
        assert _sanitize_filename(r"C:\Users\me\劇照01.jpg") == "劇照01.jpg"
        assert _sanitize_filename("a/b/c.png") == "c.png"

    def test_empty_falls_back_to_upload(self):
        assert _sanitize_filename("") == "upload"

    def test_extension_preserved(self):
        assert _sanitize_filename("片場?花絮*.mp4") == "片場花絮.mp4"


class TestNormCategories:
    def test_strip_dedup_drop_empty_keep_order(self):
        assert _norm_categories([" 劇照 ", "花絮", "劇照", "", "  ", "幕後"]) == \
            ["劇照", "花絮", "幕後"]

    def test_none_and_empty_input(self):
        assert _norm_categories(None) == []
        assert _norm_categories([]) == []

    def test_non_str_items_coerced(self):
        assert _norm_categories([1, "1", 2]) == ["1", "2"]


class TestClassifyExt:
    def test_image_exts(self):
        for name in ("a.jpg", "b.JPEG", "c.png", "d.webp", "e.heic",
                     "f.gif", "g.CR2", "h.cr3", "i.nef", "j.arw", "k.dng"):
            assert _classify_ext(name) == "image", name

    def test_video_exts(self):
        for name in ("a.mp4", "b.MOV", "c.m4v", "d.avi", "e.mts"):
            assert _classify_ext(name) == "video", name

    def test_rejected(self):
        for name in ("a.exe", "b.txt", "c.psd", "noext", "", None):
            assert _classify_ext(name) is None, name


class TestDedupFilename:
    NOW = datetime(2026, 7, 20, 14, 30, 5)

    def test_no_clash_returns_as_is(self):
        assert _dedup_filename("a.jpg", lambda n: False) == "a.jpg"

    def test_clash_prefixes_timestamp(self):
        existing = {"a.jpg"}
        out = _dedup_filename("a.jpg", existing.__contains__, now=self.NOW)
        assert out == "20260720_143005_a.jpg"

    def test_double_clash_still_unique_and_keeps_original_name(self):
        existing = {"a.jpg", "20260720_143005_a.jpg"}
        out = _dedup_filename("a.jpg", existing.__contains__, now=self.NOW)
        assert out not in existing
        assert out.endswith("_a.jpg")
        assert out.startswith("20260720_143005_")


class TestDefaultCategories:
    def test_shape(self):
        assert DEFAULT_MEDIA_LOG_CATEGORIES == \
            ["場勘照", "現場花絮", "劇照", "幕後", "其他"]
        # 去重去空後不變 — 常數本身必須已是正規形
        assert _norm_categories(DEFAULT_MEDIA_LOG_CATEGORIES) == \
            DEFAULT_MEDIA_LOG_CATEGORIES


# 子資料夾命名（{建立日期}_{專案名}、非法字元、撞名補號、空名 fallback）的
# 測試已隨規則搬到 tests/unit/test_project_folders.py —— 正本在
# core.project_folders.make_dated_folder_name，三個資產子系統共用。


class TestFindMissing:
    """資料夾→DB 同步的安全判定：只有「資料夾在、檔案不在」才算真被刪。"""

    def test_file_deleted_detected(self):
        from routers.crm.media_log import _find_missing
        out = _find_missing([("a", r"X:\f\1.jpg"), ("b", r"X:\f\2.jpg")],
                            isdir=lambda d: True,
                            isfile=lambda p: p.endswith("2.jpg"))
        assert out == ["a"]

    def test_folder_unreachable_skips_all(self):
        # NAS 斷線 → isdir False → 整批跳過，絕不誤刪
        from routers.crm.media_log import _find_missing
        out = _find_missing([("a", r"X:\f\1.jpg"), ("b", r"X:\f\2.jpg")],
                            isdir=lambda d: False,
                            isfile=lambda p: False)
        assert out == []

    def test_mixed_folders_independent(self):
        from routers.crm.media_log import _find_missing
        out = _find_missing(
            [("a", r"X:\ok\1.jpg"), ("b", r"X:\dead\2.jpg")],
            isdir=lambda d: d.endswith("ok"),
            isfile=lambda p: False)
        assert out == ["a"]   # dead 資料夾搆不到 → b 不動

    def test_empty_path_ignored(self):
        from routers.crm.media_log import _find_missing
        assert _find_missing([("a", "")], isdir=lambda d: True, isfile=lambda p: False) == []


class TestNewMediaRel:
    """匯入向：資料夾（含子夾）裡有、DB 沒記錄的媒體（_new_media_rel 純函式）。
    身分＝canonical stored_path（測試環境無 NAS env → to_canonical 為 no-op，
    canonical == os.path.join(folder, rel)）。"""

    def _canon(self, folder, rel):
        from routers.crm.media_log import to_canonical_path
        import os
        return to_canonical_path(os.path.join(folder, rel))

    def test_picks_untracked_incl_subdir(self, monkeypatch):
        monkeypatch.delenv("NAS_LOCAL_SHARE_ROOT", raising=False)
        from routers.crm.media_log import _new_media_rel
        folder = r"C:\proj"
        rels = ["a.jpg", r"2014\b.jpg", "c.mov"]
        known = {self._canon(folder, "a.jpg")}   # a 已在 DB
        assert _new_media_rel(rels, known, folder) == [r"2014\b.jpg", "c.mov"]

    def test_same_basename_diff_subdir_both_new(self, monkeypatch):
        monkeypatch.delenv("NAS_LOCAL_SHARE_ROOT", raising=False)
        from routers.crm.media_log import _new_media_rel
        # 跨子夾同名檔 → 兩個都算新（身分是完整路徑，不是 basename）
        folder = r"C:\proj"
        rels = [r"d1\IMG.jpg", r"d2\IMG.jpg"]
        assert _new_media_rel(rels, set(), folder) == rels

    def test_all_known_empty(self, monkeypatch):
        monkeypatch.delenv("NAS_LOCAL_SHARE_ROOT", raising=False)
        from routers.crm.media_log import _new_media_rel
        folder = r"C:\proj"
        rels = ["a.jpg", r"x\b.mp4"]
        known = {self._canon(folder, r) for r in rels}
        assert _new_media_rel(rels, known, folder) == []

    def test_empty_inputs(self):
        from routers.crm.media_log import _new_media_rel
        assert _new_media_rel([], {"x"}, r"C:\p") == []
        assert _new_media_rel(None, None, r"C:\p") == []


class TestScanRootFoldersRecursive:
    """根目錄掃描（_scan_root_folders，遞迴）：(圖片, 影片, capped)，跳過 _/. 開頭。"""

    def test_recurses_into_subfolders(self, tmp_path):
        from routers.crm.media_log import _scan_root_folders
        # 小飛俠劇照 結構：照片在日期子夾裡（頂層 0）
        (tmp_path / "shoot" / "2014-07-12").mkdir(parents=True)
        (tmp_path / "shoot" / "2014-07-12" / "a.jpg").write_bytes(b"x")
        (tmp_path / "shoot" / "2014-07-12" / "b.jpg").write_bytes(b"x")
        (tmp_path / "shoot" / "part2").mkdir()
        (tmp_path / "shoot" / "part2" / "c.mov").write_bytes(b"x")
        (tmp_path / "shoot" / "note.txt").write_bytes(b"x")   # 非媒體不計
        out = _scan_root_folders(str(tmp_path))
        assert out["shoot"] == (2, 1, False)   # 遞迴數到 2 圖 1 影片

    def test_flat_folder_still_works(self, tmp_path):
        from routers.crm.media_log import _scan_root_folders
        (tmp_path / "flat").mkdir()
        (tmp_path / "flat" / "a.jpg").write_bytes(b"x")
        assert _scan_root_folders(str(tmp_path))["flat"] == (1, 0, False)

    def test_skips_underscore_and_dot_recursively(self, tmp_path):
        from routers.crm.media_log import _scan_root_folders
        (tmp_path / "real" / "_trash").mkdir(parents=True)
        (tmp_path / "real" / "_trash" / "x.jpg").write_bytes(b"x")   # _trash 不計
        (tmp_path / "real" / "keep.jpg").write_bytes(b"x")
        assert _scan_root_folders(str(tmp_path))["real"] == (1, 0, False)

    def test_cap_marks_capped(self, tmp_path):
        from routers.crm.media_log import _folder_media_stats
        d = tmp_path / "big"
        d.mkdir()
        for i in range(12):
            (d / f"{i}.jpg").write_bytes(b"x")
        img, vid, capped = _folder_media_stats(str(d), cap=5)
        assert capped is True and img + vid == 5

    def test_unreachable_root_returns_none(self):
        from routers.crm.media_log import _scan_root_folders
        assert _scan_root_folders("/no/such/root/xyz") is None


class TestListMediaRel:
    """遞迴列相對路徑（_list_media_rel）：含子夾、跳 _/.、搆不到回 None。"""

    def test_recursive_relpaths(self, tmp_path):
        from routers.crm.media_log import _list_media_rel
        import os
        (tmp_path / "d1").mkdir()
        (tmp_path / "d1" / "a.jpg").write_bytes(b"x")
        (tmp_path / "top.png").write_bytes(b"x")
        rels = set(_list_media_rel(str(tmp_path)))
        assert rels == {os.path.join("d1", "a.jpg"), "top.png"}

    def test_unreachable_returns_none(self):
        from routers.crm.media_log import _list_media_rel
        assert _list_media_rel("/no/such/xyz") is None


class TestStatNewFiles:
    """只 stat 新檔（_stat_new_files）：回 {rel:(size,mtime)}，跳過子資料夾項。"""

    def test_stats_only_given_rels(self, tmp_path):
        from routers.crm.media_log import _stat_new_files
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.jpg").write_bytes(b"12345")
        import os
        out = _stat_new_files(str(tmp_path), [os.path.join("sub", "a.jpg")])
        assert list(out) == [os.path.join("sub", "a.jpg")]
        assert out[os.path.join("sub", "a.jpg")][0] == 5

    def test_skips_subdir(self, tmp_path):
        from routers.crm.media_log import _stat_new_files
        (tmp_path / "fake.jpg").mkdir()
        (tmp_path / "real.jpg").write_bytes(b"x")
        assert set(_stat_new_files(str(tmp_path), ["fake.jpg", "real.jpg"])) == {"real.jpg"}

    def test_missing_skipped(self, tmp_path):
        from routers.crm.media_log import _stat_new_files
        assert _stat_new_files(str(tmp_path), ["gone.jpg"]) == {}


# 路徑防護（safe_subfolder / safe_rel_path）的測試已隨規則搬到
# tests/unit/test_project_folders.py —— 正本在 core.project_folders，
# 三個資產子系統共用。這裡只留 media_log 自己的媒體判定薄殼。


class TestListFolderForView:
    """唯讀瀏覽列檔（_list_folder_for_view）：含子夾、依 mtime 新→舊、cap 標 truncated。"""

    def test_lists_recursive_with_meta(self, tmp_path):
        from routers.crm.media_log import _list_folder_for_view
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.jpg").write_bytes(b"12345")
        (tmp_path / "clip.mp4").write_bytes(b"xx")
        (tmp_path / "note.txt").write_bytes(b"x")   # 非媒體不列
        files, truncated = _list_folder_for_view(str(tmp_path))
        assert truncated is False
        names = sorted(f["filename"] for f in files)
        assert names == ["a.jpg", "clip.mp4"]
        by = {f["filename"]: f for f in files}
        assert by["clip.mp4"]["media_type"] == "video"
        assert by["a.jpg"]["media_type"] == "image"
        assert by["a.jpg"]["size_bytes"] == 5

    def test_cap_marks_truncated(self, tmp_path):
        from routers.crm.media_log import _list_folder_for_view
        for i in range(6):
            (tmp_path / f"{i}.jpg").write_bytes(b"x")
        files, truncated = _list_folder_for_view(str(tmp_path), cap=4)
        assert truncated is True and len(files) == 4


class TestCheckMediaLogAuthToken:
    """子系統授權的 ?token= 路徑（_check_media_log_auth）：<img> src 無法帶 header 用它。
    admin 或 media_log 模組放行；其他/無效 token → 403。"""

    def test_admin_token_passes(self):
        from core.auth import create_token
        from routers.crm.media_log import _check_media_log_auth
        t = create_token({"sub": "a", "username": "a", "access_level": 3, "modules": []})
        _check_media_log_auth(None, t)   # 不 raise 即通過

    def test_media_log_module_passes(self):
        from core.auth import create_token
        from routers.crm.media_log import _check_media_log_auth
        t = create_token({"sub": "u", "username": "u", "access_level": 1,
                          "modules": ["media_log"]})
        _check_media_log_auth(None, t)

    def test_without_module_rejected(self):
        import pytest
        from fastapi import HTTPException
        from core.auth import create_token
        from routers.crm.media_log import _check_media_log_auth
        t = create_token({"sub": "u", "username": "u", "access_level": 1,
                          "modules": ["crm_clients"]})
        with pytest.raises(HTTPException):
            _check_media_log_auth(None, t)

    def test_invalid_token_rejected(self):
        import pytest
        from fastapi import HTTPException
        from routers.crm.media_log import _check_media_log_auth
        with pytest.raises(HTTPException):
            _check_media_log_auth(None, "garbage.not.a.jwt")


class TestFolderLogId:
    """免專案 QR 的 folder-only media log 合成主鍵（_folder_log_id / _is_folder_log）。"""

    def test_prefix_and_length(self):
        from routers.crm.media_log import _folder_log_id, _FOLDER_LOG_PREFIX
        fid = _folder_log_id("20140815_小飛俠劇照")
        assert fid.startswith(_FOLDER_LOG_PREFIX)
        assert len(fid) <= 64   # ProjectMediaLog.id 是 String(64)

    def test_deterministic_idempotent(self):
        from routers.crm.media_log import _folder_log_id
        assert _folder_log_id("A夾") == _folder_log_id("A夾")
        assert _folder_log_id("A夾") != _folder_log_id("B夾")

    def test_is_folder_log(self):
        from routers.crm.media_log import _folder_log_id, _is_folder_log
        assert _is_folder_log(_folder_log_id("x")) is True
        assert _is_folder_log("abc123realprojectid") is False
        assert _is_folder_log("") is False
        assert _is_folder_log(None) is False
