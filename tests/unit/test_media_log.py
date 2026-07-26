"""routers/crm/media_log.py 純函式 — 資料夾名 sanitize / 分類正規化 /
副檔名白名單 / 撞名前綴（exists 用 callable 注入，不碰磁碟/DB/網路）。"""
from datetime import datetime

from routers.crm.media_log import (
    DEFAULT_MEDIA_LOG_CATEGORIES,
    _classify_ext,
    _dedup_filename,
    _norm_categories,
    _sanitize_filename,
    _sanitize_folder_name,
)


class TestSanitizeFolderName:
    def test_removes_windows_illegal_chars(self):
        assert _sanitize_folder_name('A<B>C:D"E/F\\G|H?I*J') == "ABCDEFGHIJ"

    def test_removes_control_chars_and_strips(self):
        assert _sanitize_folder_name("  形象影片\t2026\n  ") == "形象影片2026"

    def test_empty_and_all_illegal_fall_back_to_project(self):
        assert _sanitize_folder_name("") == "project"
        assert _sanitize_folder_name(None) == "project"
        assert _sanitize_folder_name('<>:"?*') == "project"

    def test_normal_chinese_name_untouched(self):
        assert _sanitize_folder_name("技術展示影片") == "技術展示影片"


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


class TestMakeFolderName:
    """子資料夾命名：{建立日期}_{專案名}（owner 指定）+ 同日撞名尾綴。"""

    def test_date_prefix_and_name(self):
        from datetime import datetime
        from routers.crm.media_log import _make_folder_name
        n = _make_folder_name("泓電樓梯升降椅 廣告", datetime(2026, 7, 20), set())
        assert n == "20260720_泓電樓梯升降椅 廣告"

    def test_illegal_chars_cleaned(self):
        from datetime import datetime
        from routers.crm.media_log import _make_folder_name
        n = _make_folder_name('A/B:C*D?', datetime(2026, 1, 2), set())
        assert n == "20260102_ABCD"

    def test_collision_suffix(self):
        from datetime import datetime
        from routers.crm.media_log import _make_folder_name
        taken = {"20260720_同名專案", "20260720_同名專案-2"}
        n = _make_folder_name("同名專案", datetime(2026, 7, 20), taken)
        assert n == "20260720_同名專案-3"

    def test_empty_name_falls_back(self):
        from datetime import datetime
        from routers.crm.media_log import _make_folder_name
        n = _make_folder_name("", datetime(2026, 7, 20), set())
        assert n == "20260720_project"


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


class TestSafeSubfolder:
    """孤兒資料夾唯讀瀏覽的路徑防護（_safe_subfolder）：只放行 root 下的直接子夾。"""

    def test_valid_direct_subfolder(self, tmp_path):
        from routers.crm.media_log import _safe_subfolder
        import os
        (tmp_path / "shoot").mkdir()
        assert _safe_subfolder(str(tmp_path), "shoot") == os.path.join(str(tmp_path), "shoot")

    def test_rejects_path_separators(self, tmp_path):
        from routers.crm.media_log import _safe_subfolder
        (tmp_path / "a").mkdir()
        assert _safe_subfolder(str(tmp_path), "a/b") is None
        assert _safe_subfolder(str(tmp_path), "a\\b") is None

    def test_rejects_dotdot_traversal(self, tmp_path):
        from routers.crm.media_log import _safe_subfolder
        assert _safe_subfolder(str(tmp_path), "..") is None
        assert _safe_subfolder(str(tmp_path), ".") is None

    def test_rejects_nonexistent_and_file(self, tmp_path):
        from routers.crm.media_log import _safe_subfolder
        (tmp_path / "f.jpg").write_bytes(b"x")
        assert _safe_subfolder(str(tmp_path), "nope") is None
        assert _safe_subfolder(str(tmp_path), "f.jpg") is None   # 檔案不是資料夾

    def test_empty_inputs(self, tmp_path):
        from routers.crm.media_log import _safe_subfolder
        assert _safe_subfolder("", "x") is None
        assert _safe_subfolder(str(tmp_path), "") is None


class TestSafeRelPath:
    """孤兒資料夾內單檔路徑防護（_safe_rel_path）：擋 .. / 絕對路徑逃出資料夾。"""

    def test_valid_file_incl_subdir(self, tmp_path):
        from routers.crm.media_log import _safe_rel_path
        import os
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.jpg").write_bytes(b"x")
        got = _safe_rel_path(str(tmp_path), "sub/a.jpg")
        assert got == os.path.normpath(os.path.join(str(tmp_path), "sub", "a.jpg"))

    def test_backslash_relpath_accepted(self, tmp_path):
        from routers.crm.media_log import _safe_rel_path
        (tmp_path / "a.jpg").write_bytes(b"x")
        assert _safe_rel_path(str(tmp_path), "a.jpg") is not None

    def test_rejects_traversal_out_of_folder(self, tmp_path):
        from routers.crm.media_log import _safe_rel_path
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"x")
        folder = tmp_path / "folder"
        folder.mkdir()
        assert _safe_rel_path(str(folder), "../secret.txt") is None

    def test_rejects_absolute_and_missing(self, tmp_path):
        from routers.crm.media_log import _safe_rel_path
        assert _safe_rel_path(str(tmp_path), "/etc/passwd") is None
        assert _safe_rel_path(str(tmp_path), "gone.jpg") is None
        assert _safe_rel_path(str(tmp_path), "") is None


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
