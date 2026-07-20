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
