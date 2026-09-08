# -*- coding: utf-8 -*-
"""掃原始碼那組工具自己的測試（tests/unit/_srcscan.py）。

這些 helper 是幾十支測試的地基：它們安靜地少剝或多剝一段，受害的測試只會回報
「某個常數不見了」或「這段字串還在」，看起來像產品程式碼壞了。2026-09-08 就這樣
被咬過一次 —— `js_code_only` 把行註解裡的 `/*` 當成區塊註解開頭，吃掉了三十行真程式碼。
"""
from tests.unit._srcscan import js_code_only


def test_strips_both_comment_kinds():
    src = "const a = 1; // 說明\n/* 區塊\n   註解 */\nconst b = 2;"
    out = js_code_only(src)
    assert "說明" not in out and "區塊" not in out
    assert "const a = 1;" in out and "const b = 2;" in out


def test_a_block_marker_inside_a_line_comment_never_eats_real_code():
    """🔴 迴歸：檔頭用 `//` 寫了帶 `/*` 的路徑，檔案後面又有一段 JSDoc 收尾的 `*/`。

    先剝區塊再剝行註解的話，那兩個記號會配成一對，把中間的常數與函式全部吃掉。"""
    src = "\n".join([
        "// API: /api/v1/me/leave/*。",
        "const API = '/api/v1/hr';",
        "const KEEP = 42;",
        "/** 這是真的區塊註解 */",
        "const AFTER = 1;",
    ])
    out = js_code_only(src)
    assert "const API = '/api/v1/hr';" in out, "行註解裡的 /* 不可以吃掉後面的程式碼"
    assert "const KEEP = 42;" in out and "const AFTER = 1;" in out
    assert "這是真的區塊註解" not in out and "API: /api/v1" not in out


def test_urls_keep_their_double_slash():
    src = "const u = 'https://example.com/x'; // 註解"
    out = js_code_only(src)
    assert "https://example.com/x" in out and "註解" not in out


def test_a_line_comment_inside_a_block_comment_does_not_break_the_terminator():
    """區塊註解裡寫了 `//` 也要整段剝掉（先剝行註解的話會把 `*/` 一起吃掉、區塊就收不了尾）。"""
    out = js_code_only("/* 前面\n// 裡面這行\n*/\nconst z = 3;")
    assert "前面" not in out and "裡面這行" not in out and "const z = 3;" in out
