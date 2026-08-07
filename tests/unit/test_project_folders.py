# -*- coding: utf-8 -*-
"""core/project_folders.py 單元測試（命名 / 路徑前綴替換 / 改名決策 /
改名程序的 savepoint 與補償）。"""
import io
import os
from datetime import datetime

import pytest

from core.project_folders import (clean_filename, clean_name, create_subfolder,
                                  dedupe, list_folder_files, list_folder_level,
                                  make_dated_folder_name,
                                  make_reference_folder_name, remap_prefix,
                                  rename_and_remap, rename_dir, safe_rel_dir,
                                  safe_rel_path, safe_sub_dirs, safe_subfolder,
                                  save_uploads,
                                  validate_folder_name)

CREATED = datetime(2026, 8, 6, 15, 30)


# ── 命名 ────────────────────────────────────────────────────

def test_clean_name_strips_illegal_chars():
    assert clean_name('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"
    assert clean_name("  空白  ") == "空白"
    assert clean_name(None) == ""


def test_dated_folder_name_is_date_underscore_project():
    assert make_dated_folder_name("臺北城市形象片", CREATED) == "20260806_臺北城市形象片"


def test_dated_folder_name_uses_created_not_today():
    """日期取專案建立日 —— 改名時資料夾不會跳到今天。"""
    old = datetime(2024, 1, 2)
    assert make_dated_folder_name("案子", old).startswith("20240102_")


def test_dated_folder_name_empty_project_name_falls_back():
    assert make_dated_folder_name("///", CREATED) == "20260806_project"


def test_dated_folder_name_dedupes_against_taken():
    taken = {"20260806_案", "20260806_案-2"}
    assert make_dated_folder_name("案", CREATED, taken) == "20260806_案-3"


def test_rename_keeps_existing_date_prefix():
    """改名不該讓資料夾日期跳掉 —— 沿用舊夾的日期前綴。"""
    got = make_dated_folder_name("新名", CREATED, existing="20240102_舊名")
    assert got == "20240102_新名"


def test_rename_without_date_prefix_uses_created():
    """手動建的夾（沒有日期前綴）→ 補上專案建立日。"""
    assert make_dated_folder_name("新名", CREATED, existing="隨手建的夾") == "20260806_新名"


def test_reference_folder_name_is_title_underscore_brand():
    assert make_reference_folder_name("關於島嶼", "台灣觀光局") == "關於島嶼_台灣觀光局"


def test_reference_folder_name_without_brand_is_title_only():
    assert make_reference_folder_name("關於島嶼", "") == "關於島嶼"


def test_reference_folder_name_falls_back_to_video_id_then_literal():
    assert make_reference_folder_name("", "", fallback="dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert make_reference_folder_name("", "") == "reference"


def test_reference_folder_name_dedupes():
    assert make_reference_folder_name("片", "牌", taken={"片_牌"}) == "片_牌-2"


def test_long_names_are_truncated():
    """路徑總長 260 的保險 —— 單段不超過 80 字。"""
    assert len(make_dated_folder_name("長" * 500, CREATED)) <= 9 + 80


# ── 路徑前綴替換 ─────────────────────────────────────────────

def test_remap_prefix_swaps_folder_and_keeps_tail():
    got = remap_prefix(r"\\NAS\Media\20260806_舊名\劇照\a.jpg",
                       r"\\NAS\Media\20260806_舊名",
                       r"\\NAS\Media\20260806_新名")
    assert got == r"\\NAS\Media\20260806_新名\劇照\a.jpg"


def test_remap_prefix_handles_the_folder_itself():
    assert remap_prefix(r"\\NAS\M\old", r"\\NAS\M\old", r"\\NAS\M\new") == r"\\NAS\M\new"


def test_remap_prefix_is_case_and_slash_insensitive():
    got = remap_prefix(r"\\nas\media/20260806_A\x.jpg",
                       r"\\NAS\Media\20260806_A",
                       r"\\NAS\Media\20260806_B")
    assert got.startswith(r"\\NAS\Media\20260806_B")


def test_remap_prefix_leaves_unrelated_paths_alone():
    """別的專案的檔案不可以被順手改掉。"""
    other = r"\\NAS\Media\20260806_別案\x.jpg"
    assert remap_prefix(other, r"\\NAS\Media\20260806_本案",
                        r"\\NAS\Media\20260806_新名") == other


def test_remap_prefix_does_not_match_sibling_with_same_stem():
    """`..._案` 不可以吃到 `..._案外案`（前綴比對必須認資料夾邊界）。"""
    sibling = r"\\NAS\M\20260806_案外案\x.jpg"
    assert remap_prefix(sibling, r"\\NAS\M\20260806_案", r"\\NAS\M\new") == sibling


def test_remap_prefix_empty_inputs_are_noop():
    assert remap_prefix("", "a", "b") == ""
    assert remap_prefix(r"\\a\b", "", "b") == r"\\a\b"


# ── 改名決策 ────────────────────────────────────────────────

def test_rename_dir_missing_source_is_success_noop():
    """還沒建過資料夾 → 視為成功（呼叫端照樣更新名稱，下次建夾用新名）。"""
    called = []
    ok, err = rename_dir("/old", "/new", exists=lambda p: False,
                         rename=lambda a, b: called.append((a, b)))
    assert (ok, err, called) == (True, "", [])


def test_rename_dir_same_path_is_noop():
    called = []
    ok, _ = rename_dir(r"\\n\a", r"\\n/a", exists=lambda p: True,
                       rename=lambda a, b: called.append(1))
    assert ok and not called


def test_rename_dir_refuses_to_clobber_existing_target():
    called = []
    ok, err = rename_dir("/old", "/new", exists=lambda p: True,
                         rename=lambda a, b: called.append(1))
    assert not ok and "已存在" in err and not called


def test_rename_dir_reports_locked_folder_without_raising():
    """檔案被開著 → 回錯誤不拋，呼叫端才能讓「名稱本身照樣改成功」。"""
    def _boom(a, b):
        raise PermissionError("被另一個程序使用中")

    ok, err = rename_dir("/old", "/new",
                         exists=lambda p: p == "/old", rename=_boom)
    assert not ok and "PermissionError" in err


def test_rename_dir_happy_path():
    called = []
    ok, err = rename_dir("/old", "/new", exists=lambda p: p == "/old",
                         rename=lambda a, b: called.append((a, b)))
    assert (ok, err) == (True, "") and called == [("/old", "/new")]


# ── 檔名撞名（deck 上傳）────────────────────────────────────

def test_dedupe_folder_style_appends_at_end():
    assert dedupe("20260806_案", {"20260806_案"}) == "20260806_案-2"


def test_dedupe_filename_puts_suffix_before_extension():
    assert dedupe("企劃書.pdf", {"企劃書.pdf"}, keep_ext=True) == "企劃書-2.pdf"


def test_dedupe_filename_handles_uppercase_extension():
    """副檔名在函式內自己拆 —— 大小寫不再需要呼叫端保持一致。"""
    assert dedupe("企劃書.PDF", {"企劃書.PDF"}, keep_ext=True) == "企劃書-2.PDF"


def test_dedupe_filename_no_collision_returns_original():
    assert dedupe("企劃書.pdf", set(), keep_ext=True) == "企劃書.pdf"


def test_clean_filename_strips_path_and_falls_back():
    assert clean_filename(r"C:\Users\me\企劃書.pdf") == "企劃書.pdf"
    assert clean_filename("") == "upload"


# ── 路徑防護（三個資產子系統共用；規則從 media_log 搬來時測試一起搬）──

class TestSafeSubfolder:
    """只放行 root 下的直接子資料夾。"""

    def test_valid_direct_subfolder(self, tmp_path):
        (tmp_path / "shoot").mkdir()
        assert safe_subfolder(str(tmp_path), "shoot") == os.path.join(str(tmp_path), "shoot")

    def test_rejects_path_separators(self, tmp_path):
        (tmp_path / "a").mkdir()
        assert safe_subfolder(str(tmp_path), "a/b") is None
        assert safe_subfolder(str(tmp_path), "a\\b") is None

    def test_rejects_dotdot_traversal(self, tmp_path):
        assert safe_subfolder(str(tmp_path), "..") is None
        assert safe_subfolder(str(tmp_path), ".") is None

    def test_rejects_nonexistent_and_file(self, tmp_path):
        (tmp_path / "f.jpg").write_bytes(b"x")
        assert safe_subfolder(str(tmp_path), "nope") is None
        assert safe_subfolder(str(tmp_path), "f.jpg") is None   # 檔案不是資料夾

    def test_empty_inputs(self, tmp_path):
        assert safe_subfolder("", "x") is None
        assert safe_subfolder(str(tmp_path), "") is None


class TestSafeRelPath:
    """擋 .. / 絕對路徑逃出資料夾。"""

    def test_valid_file_incl_subdir(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.jpg").write_bytes(b"x")
        got = safe_rel_path(str(tmp_path), "sub/a.jpg")
        assert got == os.path.normpath(os.path.join(str(tmp_path), "sub", "a.jpg"))

    def test_backslash_relpath_accepted(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        assert safe_rel_path(str(tmp_path), "a.jpg") is not None

    def test_rejects_traversal_out_of_folder(self, tmp_path):
        (tmp_path / "secret.txt").write_bytes(b"x")
        folder = tmp_path / "folder"
        folder.mkdir()
        assert safe_rel_path(str(folder), "../secret.txt") is None

    def test_rejects_absolute_and_missing(self, tmp_path):
        assert safe_rel_path(str(tmp_path), "/etc/passwd") is None
        assert safe_rel_path(str(tmp_path), "gone.jpg") is None
        assert safe_rel_path(str(tmp_path), "") is None


class TestListFolderFiles:
    """列檔（含子夾、依 mtime 新→舊、cap 標 truncated、accept 過濾）。"""

    def test_lists_all_files_recursively(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.pdf").write_bytes(b"12345")
        (tmp_path / "b.docx").write_bytes(b"xx")
        files, truncated = list_folder_files(str(tmp_path))
        assert truncated is False
        assert sorted(f["rel"] for f in files) == ["b.docx", "sub/a.pdf"]
        assert {f["filename"]: f["size_bytes"] for f in files}["a.pdf"] == 5

    def test_accept_filters(self, tmp_path):
        (tmp_path / "a.pdf").write_bytes(b"x")
        (tmp_path / "b.exe").write_bytes(b"x")
        files, _ = list_folder_files(str(tmp_path), accept=lambda n: n.endswith(".pdf"))
        assert [f["filename"] for f in files] == ["a.pdf"]

    def test_skips_dot_and_underscore_dirs(self, tmp_path):
        for d in (".git", "_tmp"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "x.pdf").write_bytes(b"x")
        (tmp_path / "ok.pdf").write_bytes(b"x")
        files, _ = list_folder_files(str(tmp_path))
        assert [f["filename"] for f in files] == ["ok.pdf"]

    def test_cap_marks_truncated(self, tmp_path):
        for i in range(6):
            (tmp_path / f"{i}.pdf").write_bytes(b"x")
        files, truncated = list_folder_files(str(tmp_path), cap=4)
        assert len(files) == 4 and truncated is True

    def test_missing_folder_is_empty_not_error(self, tmp_path):
        assert list_folder_files(str(tmp_path / "nope")) == ([], False)


class TestSafeRelDir:
    """逐層瀏覽的目錄版守衛（safe_rel_path 的孿生）。"""

    def test_empty_rel_is_the_folder_itself(self, tmp_path):
        assert safe_rel_dir(str(tmp_path), "") == str(tmp_path)

    def test_nested_dir(self, tmp_path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        got = safe_rel_dir(str(tmp_path), "a/b")
        assert got == os.path.normpath(os.path.join(str(tmp_path), "a", "b"))

    def test_rejects_traversal_file_and_missing(self, tmp_path):
        (tmp_path / "secret").mkdir()
        folder = tmp_path / "folder"
        folder.mkdir()
        (folder / "f.pdf").write_bytes(b"x")
        assert safe_rel_dir(str(folder), "../secret") is None
        assert safe_rel_dir(str(folder), "f.pdf") is None      # 檔案不是目錄
        assert safe_rel_dir(str(folder), "gone") is None


class TestListFolderLevel:
    """單層列舉：子夾與檔案分開、rel 相對資料夾根、cap 涵蓋兩者、
    路徑防護包在裡面（逃逸/不存在回 None）。"""

    def test_splits_dirs_and_files_at_this_level(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.pdf").write_bytes(b"x")
        (tmp_path / "top.docx").write_bytes(b"xx")
        dirs, files, truncated = list_folder_level(str(tmp_path))
        assert truncated is False
        assert [d["name"] for d in dirs] == ["sub"]
        assert [d["rel"] for d in dirs] == ["sub"]
        # 深層的檔案這一層看不到（那正是逐層的重點）
        assert [f["filename"] for f in files] == ["top.docx"]

    def test_rel_prefixes_children_for_download_endpoints(self, tmp_path):
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "c.pdf").write_bytes(b"x")
        (deep / "d").mkdir()
        dirs, files, _ = list_folder_level(str(tmp_path), "a/b")
        assert files[0]["rel"] == "a/b/c.pdf"      # 相對根，可直接餵下載端點
        assert dirs[0]["rel"] == "a/b/d"

    def test_skips_dot_and_underscore_dirs(self, tmp_path):
        for d in (".git", "_tmp", "ok"):
            (tmp_path / d).mkdir()
        dirs, _files, _ = list_folder_level(str(tmp_path))
        assert [d["name"] for d in dirs] == ["ok"]

    def test_cap_counts_dirs_and_files_together(self, tmp_path):
        for i in range(3):
            (tmp_path / f"d{i}").mkdir()
            (tmp_path / f"f{i}.pdf").write_bytes(b"x")
        dirs, files, truncated = list_folder_level(str(tmp_path), cap=4)
        assert len(dirs) + len(files) == 4 and truncated is True

    def test_missing_or_escaping_returns_none(self, tmp_path):
        (tmp_path / "secret").mkdir()
        folder = tmp_path / "folder"
        folder.mkdir()
        (folder / "f.pdf").write_bytes(b"x")
        assert list_folder_level(str(tmp_path / "nope")) is None   # 資料夾不存在
        assert list_folder_level(str(folder), "gone") is None      # 這一層不存在
        assert list_folder_level(str(folder), "../secret") is None  # 逃逸
        assert list_folder_level(str(folder), "f.pdf") is None     # 檔案不是目錄


class TestCreateSubfolder:
    """建夾的正本（影像紀錄的新增資料夾、提案資產夾的就地開夾共用）。"""

    def test_creates_and_returns_cleaned_name(self, tmp_path):
        name, err = create_subfolder(str(tmp_path), " 對外分享 ")
        assert (name, err) == ("對外分享", "")
        assert (tmp_path / "對外分享").is_dir()

    def test_existing_is_an_error_not_a_silent_pass(self, tmp_path):
        (tmp_path / "已有").mkdir()
        name, err = create_subfolder(str(tmp_path), "已有")
        assert name == "" and "已經有" in err

    def test_invalid_names_never_touch_disk(self, tmp_path):
        for bad in ("壞/名字", "..", "", "   ", "*"):
            name, err = create_subfolder(str(tmp_path), bad)
            assert name == "" and err, bad
        assert list(tmp_path.iterdir()) == []

    def test_taken_set_is_honoured(self, tmp_path):
        name, err = create_subfolder(str(tmp_path), "重複", {"重複"})
        assert name == "" and err
        assert not (tmp_path / "重複").exists()

    def test_missing_parent_reports_and_creates_nothing(self, tmp_path):
        """只開這一層 —— makedirs 會把打錯字的 root 整條造出來，人還以為存進去了。
        而且要回錯誤不是拋例外（拋了呼叫端就是 500 不是 400）。"""
        missing = tmp_path / "根本不存在"
        name, err = create_subfolder(str(missing), "x")
        assert name == "" and "上層" in err
        assert not missing.exists()


class TestSafeSubDirs:
    """拖進來的相對路徑 → 目錄段。**這是敵意輸入**，逐段清洗、可疑就整筆拒收。"""

    def test_strips_filename_and_cleans_each_segment(self):
        assert safe_sub_dirs("我的夾/子層/檔案.pdf") == ["我的夾", "子層"]
        assert safe_sub_dirs("有:非法*字元/x.txt") == ["有非法字元"]
        assert safe_sub_dirs("只有檔名.pdf") == []

    def test_rejects_traversal_anywhere_in_the_path(self):
        for bad in ("../x.pdf", "a/../../x.pdf", "a/b/../x.pdf", "./x.pdf"):
            assert safe_sub_dirs(bad) is None, bad

    def test_backslashes_are_separators_not_content(self):
        """Windows 拖放給的是反斜線 —— 當成分隔而不是檔名的一部分，
        否則 `..\\..\\x` 會整串變成一個「檔名」躲過 .. 檢查。"""
        assert safe_sub_dirs(r"我的夾\子層\x.pdf") == ["我的夾", "子層"]
        assert safe_sub_dirs(r"..\..\x.pdf") is None

    def test_rejects_segment_that_cleans_to_nothing(self):
        assert safe_sub_dirs("*/x.pdf") is None      # 清完為空 → 整筆不要
        assert safe_sub_dirs('<>:"|?/x.pdf') is None

    def test_empty_segments_are_ignored_not_fatal(self):
        assert safe_sub_dirs("/a//b/x.pdf") == ["a", "b"]

    def test_long_segment_is_truncated(self):
        got = safe_sub_dirs("長" * 200 + "/x.pdf")
        assert got and len(got[0]) <= 80


class TestValidateFolderName:
    """使用者自己打的資料夾名（新增/改名共用的規則正本）。"""

    def test_accepts_and_cleans(self, tmp_path):
        assert validate_folder_name(str(tmp_path), "  20220615_台新專案 ") == \
            ("20220615_台新專案", "")
        assert validate_folder_name(str(tmp_path), "有:非法*字元")[0] == "有非法字元"

    def test_rejects_separators_instead_of_silently_stripping(self, tmp_path):
        # 清成「壞名字」會讓使用者以為改成了他打的那個 —— 一定要擋
        name, err = validate_folder_name(str(tmp_path), "壞/名字")
        assert name == "" and "不可含" in err
        assert validate_folder_name(str(tmp_path), "壞\\名字")[0] == ""

    def test_rejects_empty_and_dots(self, tmp_path):
        for bad in ("", "   ", "*", ".", ".."):
            assert validate_folder_name(str(tmp_path), bad)[0] == "", bad

    def test_rejects_taken(self, tmp_path):
        name, err = validate_folder_name(str(tmp_path), "已有的", {"已有的"})
        assert name == "" and "已經有" in err


# ── 上傳落地：黑名單 / 大小上限 / 撞名（寫進共用磁碟的安全規則）──

class _FakeUpload:
    """UploadFile 的最小替身（save_uploads 只讀 filename / size / file）。"""
    def __init__(self, filename, data, declare_size=True):
        self.filename = filename
        self.file = io.BytesIO(data)
        if declare_size:
            self.size = len(data)


@pytest.mark.asyncio
async def test_save_uploads_writes_files_with_original_names(tmp_path):
    saved, skipped = await save_uploads(
        str(tmp_path), [_FakeUpload("企劃書 v1.pdf", b"%PDF")], max_bytes=1000)
    assert (saved, skipped) == (["企劃書 v1.pdf"], [])
    assert (tmp_path / "企劃書 v1.pdf").read_bytes() == b"%PDF"


@pytest.mark.asyncio
async def test_save_uploads_blocks_executables(tmp_path):
    """NAS 是共用磁碟 —— 可執行檔一個位元組都不能落地。"""
    saved, skipped = await save_uploads(
        str(tmp_path), [_FakeUpload("壞東西.exe", b"MZ")], max_bytes=1000)
    assert saved == [] and ".exe" in skipped[0]["reason"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_save_uploads_rejects_oversize_without_writing(tmp_path):
    """大小事前就知道 → 超限的不該先寫上去再刪。"""
    saved, skipped = await save_uploads(
        str(tmp_path), [_FakeUpload("big.mov", b"x" * 500)], max_bytes=100)
    assert saved == [] and "上限" in skipped[0]["reason"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_save_uploads_oversize_without_declared_size_leaves_no_debris(tmp_path):
    """拿不到 size 時走串流那道防線 —— 半成品要刪乾淨，不留垃圾在 NAS。"""
    saved, skipped = await save_uploads(
        str(tmp_path), [_FakeUpload("big.mov", b"x" * 500, declare_size=False)],
        max_bytes=100)
    assert saved == [] and "上限" in skipped[0]["reason"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_save_uploads_dedupes_against_existing_and_within_batch(tmp_path):
    (tmp_path / "稿.pdf").write_bytes(b"old")
    saved, _ = await save_uploads(
        str(tmp_path), [_FakeUpload("稿.pdf", b"a"), _FakeUpload("稿.pdf", b"b")],
        max_bytes=1000)
    assert saved == ["稿-2.pdf", "稿-3.pdf"]        # 補號插在副檔名前
    assert (tmp_path / "稿.pdf").read_bytes() == b"old"   # 既有檔沒被蓋掉


@pytest.mark.asyncio
async def test_save_uploads_mixed_batch_reports_both(tmp_path):
    saved, skipped = await save_uploads(str(tmp_path), [
        _FakeUpload("ok.pdf", b"x"),
        _FakeUpload("bad.bat", b"x"),
    ], max_bytes=1000)
    assert saved == ["ok.pdf"] and len(skipped) == 1


# ── 改名程序：savepoint + 補償 ──────────────────────────────

class _FakeNested:
    """session.begin_nested() 的最小替身（記錄有沒有進出）。"""
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        self._log.append("enter")
        return self

    async def __aexit__(self, *exc):
        self._log.append("exit")
        return False


class _FakeSession:
    def __init__(self):
        self.log = []

    def begin_nested(self):
        return _FakeNested(self.log)


@pytest.mark.asyncio
async def test_rename_and_remap_happy_path(monkeypatch, tmp_path):
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()
    s = _FakeSession()
    seen = []

    async def _remap(o, n):
        seen.append((o, n))

    changed, err = await rename_and_remap(s, str(old), str(new), remap=_remap)
    assert (changed, err) == (True, "")
    assert new.is_dir() and not old.exists()
    assert seen == [(str(old), str(new))] and s.log == ["enter", "exit"]


@pytest.mark.asyncio
async def test_rename_and_remap_compensates_when_remap_raises(tmp_path):
    """DB 同步失敗 → 資料夾要改回舊名，否則磁碟與紀錄就此不一致。"""
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()

    async def _boom(o, n):
        raise RuntimeError("DB 炸了")

    changed, err = await rename_and_remap(_FakeSession(), str(old), str(new),
                                          remap=_boom)
    assert changed is False
    assert "已改回舊名" in err and "DB 炸了" in err
    assert old.is_dir() and not new.exists()


@pytest.mark.asyncio
async def test_rename_and_remap_reports_unrecoverable_state(tmp_path, monkeypatch):
    """補償也失敗 = 真的不一致 → 訊息要講實話、可辨識，不能謊稱已還原。"""
    import core.project_folders as pf
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()
    calls = {"n": 0}
    real_rename_dir = pf.rename_dir

    def _rename_dir_then_fail(a, b, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_rename_dir(a, b, **kw)
        return False, "PermissionError: 還原時被佔用"   # 補償這趟失敗

    monkeypatch.setattr(pf, "rename_dir", _rename_dir_then_fail)

    async def _boom(o, n):
        raise RuntimeError("DB 炸了")

    changed, err = await rename_and_remap(_FakeSession(), str(old), str(new),
                                          remap=_boom)
    assert changed is False
    assert "需人工處理" in err and "無法改回舊名" in err
    assert calls["n"] == 2                     # 有試過補償


@pytest.mark.asyncio
async def test_rename_and_remap_skips_remap_when_rename_fails(tmp_path):
    """rename 失敗 → 什麼都不動（remap 不可以跑，否則 DB 指向不存在的路徑）。"""
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()
    new.mkdir()                      # 目標已存在 → rename_dir 拒絕
    seen = []

    async def _remap(o, n):
        seen.append(1)

    changed, err = await rename_and_remap(_FakeSession(), str(old), str(new),
                                          remap=_remap)
    assert changed is False and "已存在" in err and not seen


# ── 拖整個資料夾上傳：結構重建 + 路徑安全（save_uploads 的 rel_paths）──

async def test_save_uploads_rebuilds_folder_structure(tmp_path):
    """拖一個夾進來 = 檔案帶著「夾名/子層/檔名」→ 照著建出來（含夾本身）。"""
    saved, skipped = await save_uploads(
        str(tmp_path),
        [_FakeUpload("a.pdf", b"1"), _FakeUpload("b.pdf", b"2"), _FakeUpload("c.pdf", b"3")],
        max_bytes=1024,
        rel_paths=["我的夾/a.pdf", "我的夾/子層/b.pdf", "c.pdf"])
    assert skipped == []
    assert (tmp_path / "我的夾" / "a.pdf").read_bytes() == b"1"
    assert (tmp_path / "我的夾" / "子層" / "b.pdf").read_bytes() == b"2"
    assert (tmp_path / "c.pdf").read_bytes() == b"3"          # 沒帶路徑的照樣平放
    assert set(saved) == {"我的夾/a.pdf", "我的夾/子層/b.pdf", "c.pdf"}


async def test_save_uploads_dedupes_per_directory_not_globally(tmp_path):
    """`a/圖.jpg` 與 `b/圖.jpg` 是兩個不同的檔 —— 全域一份 taken 會讓其中
    一個平白變成 圖-2.jpg。"""
    saved, _ = await save_uploads(
        str(tmp_path), [_FakeUpload("圖.jpg", b"1"), _FakeUpload("圖.jpg", b"2")],
        max_bytes=1024, rel_paths=["a/圖.jpg", "b/圖.jpg"])
    assert set(saved) == {"a/圖.jpg", "b/圖.jpg"}
    assert (tmp_path / "a" / "圖.jpg").exists() and (tmp_path / "b" / "圖.jpg").exists()


async def test_save_uploads_still_dedupes_within_the_same_directory(tmp_path):
    saved, _ = await save_uploads(
        str(tmp_path), [_FakeUpload("圖.jpg", b"1"), _FakeUpload("圖.jpg", b"2")],
        max_bytes=1024, rel_paths=["a/圖.jpg", "a/圖.jpg"])
    assert set(saved) == {"a/圖.jpg", "a/圖-2.jpg"}


async def test_save_uploads_refuses_traversal_and_writes_nothing_outside(tmp_path):
    """🔴 相對路徑來自瀏覽器的拖放事件 = 敵意輸入。逃出去一個位元組都不行。"""
    root = tmp_path / "root"
    root.mkdir()
    saved, skipped = await save_uploads(
        str(root),
        [_FakeUpload("evil.pdf", b"x"), _FakeUpload("evil2.pdf", b"x"),
         _FakeUpload("evil3.pdf", b"x")],
        max_bytes=1024,
        rel_paths=["../evil.pdf", "a/../../evil2.pdf", r"..\..\evil3.pdf"])
    assert saved == []
    assert all(s["reason"] == "路徑不合法" for s in skipped), skipped
    assert list(tmp_path.iterdir()) == [root]      # root 之外什麼都沒長出來
    assert list(root.iterdir()) == []


async def test_save_uploads_blocked_ext_inside_subfolder_still_blocked(tmp_path):
    saved, skipped = await save_uploads(
        str(tmp_path), [_FakeUpload("壞.exe", b"x")], max_bytes=1024,
        rel_paths=["我的夾/壞.exe"])
    assert saved == [] and len(skipped) == 1
    assert not (tmp_path / "我的夾" / "壞.exe").exists()
