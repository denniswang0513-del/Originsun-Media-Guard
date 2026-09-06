# -*- coding: utf-8 -*-
"""空拍監控遇到原廠 RAW（.r3d／.braw）：**看得到、但不餵 ffmpeg**（owner 2026-09-06）。

隨附的 ffmpeg 8 沒有 REDCODE 解碼器、沒有 BRAW demuxer；丟進 drone_meta 只會每支失敗一次，
而且失敗的檔不進 manifest → 下一次排程又派一次，天天如此。所以掃描把它們挑出來另外記
（掃描紀錄 skipped_raw＋通知），派工清單裡不放。2026-08-30「素材不能靜默消失」的拍板照樣成立：
_MEDIA_EXTS 仍含 RAW（tests/unit/test_media_exts_sync.py），只是走另一條路。
"""
import os

from core import drone_watcher as dw
from core.media_exts import RAW_CAMERA_EXTS, TRANSCRIBE_EXTS


def test_raw_exts_are_public_and_outside_the_ffmpeg_set():
    assert RAW_CAMERA_EXTS == frozenset({".r3d", ".braw"})
    assert not (RAW_CAMERA_EXTS & TRANSCRIBE_EXTS)


def test_scan_sees_raw_but_does_not_dispatch_it(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "A").mkdir(parents=True)
    (src / "B").mkdir()
    for fn in ("DJI_0001.MP4", "DJI_0002.mov", "A001_C001.R3D", "A001_C002.braw"):
        (src / "A" / fn).write_bytes(b"x")
    (src / "B" / "only.BRAW").write_bytes(b"x")          # 只有 RAW 的資料夾：整包不派
    dest.mkdir()

    cands = dw._scan_candidates(str(src), str(dest))
    by_folder = {os.path.basename(p): [os.path.basename(f) for f in files] for p, files in cands}
    assert set(by_folder) == {"A"}, by_folder
    assert sorted(by_folder["A"]) == ["DJI_0001.MP4", "DJI_0002.mov"]
    assert dw._SKIPPED_RAW == {"A": ["A001_C001.R3D", "A001_C002.braw"], "B": ["only.BRAW"]}


def test_scan_record_and_notification_mention_the_skipped_raw():
    src = dw.__file__ and open(dw.__file__, encoding="utf-8").read()
    body = src[src.index("def run_watcher_scan("):]
    assert 'entry["skipped_raw"]' in body and "略過 RAW" in body, "掃描紀錄要記下沒處理的 RAW（不能靜默）"
    assert "notify_tab(\"drone_watcher_success\"" in body
