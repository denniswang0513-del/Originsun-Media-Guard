# -*- coding: utf-8 -*-
"""core_engine 的特徵測試（/health 2026-09-16「都處理」的結構債那一項）。

core_engine.py 是唯一的紅檔（2350 行、覆蓋率 27%），要拆它得先有網 —— 這份**不判對錯、只釘現在的行為**：
拆檔或改寫時只要這裡變紅，就是行為被動到了。挑的是純函式與只碰暫存檔的那幾支（不碰 ffmpeg／不碰網路／不起工作）。

釘住的是三件真的出過事的事：
- 半成品的約定（`*.part.<ext>`、0 byte）——2026-08-13 事故：交付夾留下頂著正式檔名的半截檔，所有檢查都說沒事。
- `AtomicOutput`：沒 commit 就離開＝正式檔不受影響；commit 撞鎖＝暫存檔**保留**（內容是好的，人可以搶救）。
- 系統垃圾檔（macOS 的 `._*` sidecar 等）不進備份／驗收清單。
"""
import os

import pytest

from core_engine import (AtomicOutput, MediaGuardEngine, PART_MARK, _short_hash, is_incomplete_output,
                         is_junk_file, is_partial_name, part_path_for)


@pytest.mark.parametrize("name, junk", [
    ("._B001_C002.mov", True),          # macOS Finder 摸過就會長出來的 sidecar
    ("/nas/2026/專案/._clip.mp4", True),  # 完整路徑也要認得
    ("Thumbs.db", True), (".DS_Store", True), ("desktop.ini", True),
    ("reel.part.mp4", True),            # 寫到一半的產出也算垃圾（掃來源／備份／驗收都不該看到）
    ("B001_C002.mov", False), ("正式檔.mp4", False), ("my._file.mov", False),
])
def test_junk_file_predicate(name, junk):
    assert is_junk_file(name) is junk


def test_part_path_keeps_the_extension():
    """副檔名要跟最終檔一致：ffmpeg 靠副檔名選 muxer，寫成 foo.mov.part 它不知道要輸出什麼格式。"""
    assert part_path_for("/x/reel.mp4") == "/x/reel" + PART_MARK + ".mp4"
    assert part_path_for(r"C:\out\串帶.mov") == r"C:\out\串帶" + PART_MARK + ".mov"
    assert part_path_for("/x/noext") == "/x/noext" + PART_MARK + ".tmp"   # 沒副檔名 → .tmp
    assert is_partial_name(part_path_for("/x/reel.mp4")) is True
    assert is_partial_name("/x/reel.mp4") is False
    assert is_partial_name("/x/REEL.PART.MP4") is True                     # 大小寫不分


def test_incomplete_output_covers_part_zero_byte_and_missing(tmp_path):
    good = tmp_path / "ok.mp4"; good.write_bytes(b"\x00" * 16)
    empty = tmp_path / "empty.mp4"; empty.write_bytes(b"")
    part = tmp_path / ("half" + PART_MARK + ".mp4"); part.write_bytes(b"\x00" * 16)
    assert is_incomplete_output(str(good)) is False
    assert is_incomplete_output(str(empty)) is True                        # 剛建檔就被砍斷的殼
    assert is_incomplete_output(str(part)) is True
    assert is_incomplete_output(str(tmp_path / "nope.mp4")) is True        # stat 不到＝當作沒有


def test_atomic_output_leaves_the_final_file_alone_until_commit(tmp_path):
    final = tmp_path / "reel.mp4"
    final.write_bytes(b"OLD")                                              # 上一份好的成品
    with AtomicOutput(str(final)) as out:
        open(out.path, "wb").write(b"NEW-half")
        # 沒 commit 就離開（失敗／中止）
    assert final.read_bytes() == b"OLD", "沒 commit 不能動到正式檔"
    assert not os.path.exists(out.path), "暫存檔要清掉"

    with AtomicOutput(str(final)) as out2:
        open(out2.path, "wb").write(b"NEW")
        out2.commit()
    assert final.read_bytes() == b"NEW" and not os.path.exists(out2.path)


def test_atomic_output_reset_and_enter_clear_leftovers(tmp_path):
    final = tmp_path / "reel.mp4"
    out = AtomicOutput(str(final))
    open(out.path, "wb").write("殘骸".encode())                                     # 上一輪失敗留下的
    with out:                                                              # __enter__ 會先清掉
        assert not os.path.exists(out.path)
        open(out.path, "wb").write("NVENC 失敗的半截".encode())
        out.reset()                                                        # NVENC → x264 重試
        assert not os.path.exists(out.path)
        open(out.path, "wb").write("x264 的結果".encode())
        out.commit()
    assert final.read_bytes() == "x264 的結果".encode()


def test_atomic_output_keeps_the_temp_file_when_commit_fails(tmp_path, monkeypatch):
    """commit 撞到目的檔被鎖：拋 OSError，而且暫存檔**留著** —— 內容是好的，人可以手動搶救。"""
    final = tmp_path / "reel.mp4"
    def boom(src, dst): raise OSError("被鎖住")
    with pytest.raises(OSError):
        with AtomicOutput(str(final)) as out:
            open(out.path, "wb").write("好的內容".encode())
            monkeypatch.setattr(os, "replace", boom)
            out.commit()
    assert os.path.exists(out.path), "撞鎖時暫存檔不能刪"
    assert out.committed is False


def test_xxh64_is_chunked_and_matches_the_library(tmp_path):
    """備份的「比對驗證」就靠這個值；分塊讀不能改變結果（2026-09-16 升 xxhash 4.0.1 前後都驗過）。"""
    import xxhash
    blob = bytes(range(256)) * 8192                                        # > 1MB，會走到第二塊
    f = tmp_path / "big.bin"; f.write_bytes(blob)
    assert MediaGuardEngine.get_xxh64(str(f)) == xxhash.xxh64(blob).hexdigest()
    empty = tmp_path / "empty.bin"; empty.write_bytes(b"")
    assert MediaGuardEngine.get_xxh64(str(empty)) == xxhash.xxh64(b"").hexdigest()


def test_short_hash_is_the_first_eight_chars():
    assert _short_hash("0123456789abcdef") == "01234567"
    assert _short_hash("abc") == "abc"
    assert _short_hash("") == "None" and _short_hash(None) == "None"
