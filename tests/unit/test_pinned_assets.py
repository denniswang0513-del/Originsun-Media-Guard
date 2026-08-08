# -*- coding: utf-8 -*-
"""core/pinned_assets.py — 「重點提案」勾選。

測試重心壓在 `allows()`：客戶能不能拿到某個檔完全由它決定，而提案資產夾裡
混著報價與成本。漏一個分支不是功能 bug，是把價格表寄給客戶。
"""
from datetime import datetime, timezone

import pytest

from core import pinned_assets as pa

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _pinned(*specs):
    """specs: (rel, is_dir) → 已勾選的 stored 值。"""
    cur = []
    for rel, is_dir in specs:
        cur, err = pa.pin(cur, rel, is_dir=is_dir, now=NOW)
        assert not err, err
    return cur


# ── allows：安全核心 ─────────────────────────────────────────

def test_allows_exact_file():
    cur = _pinned(("PPM/提案_v3.pdf", False))
    assert pa.allows(cur, "PPM/提案_v3.pdf")
    assert not pa.allows(cur, "PPM/報價單.xlsx")
    assert not pa.allows(cur, "PPM")             # 勾檔案不等於開放它的目錄


def test_allows_inside_pinned_dir():
    cur = _pinned(("場勘照", True))
    assert pa.allows(cur, "場勘照/DSC001.jpg")
    assert pa.allows(cur, "場勘照/第二天/DSC099.jpg")     # 深層也算
    assert pa.allows(cur, "場勘照")                      # 夾本身


def test_allows_requires_separator_boundary():
    """🔴 只比 startswith 的話，勾了「提案」會連「提案外流備份」一起放出去。"""
    cur = _pinned(("提案", True))
    assert pa.allows(cur, "提案/deck.pdf")
    assert not pa.allows(cur, "提案外流備份/成本.xlsx")
    assert not pa.allows(cur, "提案備份")


def test_file_pin_does_not_open_a_directory():
    """勾的是檔案 → 就算有人拿它當前綴，底下也不放行。"""
    cur = _pinned(("deck", False))          # 沒有副檔名的檔案
    assert not pa.allows(cur, "deck/內部成本.xlsx")


@pytest.mark.parametrize("evil", [
    "../../../settings.json", "..", "PPM/../../etc/passwd",
    "PPM/./../報價.xlsx", "", "   ", "/", "//",
])
def test_allows_rejects_traversal(evil):
    cur = _pinned(("PPM", True))
    assert not pa.allows(cur, evil)


def test_allows_is_case_insensitive_like_the_filesystem():
    """目的地是 Windows/SMB —— `Deck.pdf` 與 `deck.pdf` 在那裡是同一個檔。
    比對若分大小寫，就會出現「比對擋得住、開檔擋不住」的落差。"""
    cur = _pinned(("PPM/Deck.pdf", False))
    assert pa.allows(cur, "ppm/deck.PDF")


def test_backslash_and_slash_are_the_same_path():
    cur = _pinned(("場勘照\\第二天", True))
    assert pa.allows(cur, "場勘照/第二天/a.jpg")


def test_nothing_pinned_allows_nothing():
    """沒勾 = 客戶什麼都拿不到（不是「全部開放」）。"""
    for stored in (None, [], [{"bad": 1}], "壞資料"):
        assert not pa.allows(stored, "PPM/deck.pdf")


# ── 勾 / 取消 / 排序 ────────────────────────────────────────

def test_pin_is_idempotent():
    cur = _pinned(("a.pdf", False))
    again, err = pa.pin(cur, "a.pdf", is_dir=False, now=NOW)
    assert not err and len(again) == 1


def test_pin_rejects_bad_path():
    _cur, err = pa.pin([], "../x", is_dir=False, now=NOW)
    assert err


def test_pin_respects_cap():
    cur = []
    for i in range(pa.MAX_PINS):
        cur, err = pa.pin(cur, f"f{i}.pdf", is_dir=False, now=NOW)
        assert not err
    _cur, err = pa.pin(cur, "one-more.pdf", is_dir=False, now=NOW)
    assert "最多" in err


def test_unpin_revokes_immediately():
    cur = _pinned(("場勘照", True))
    assert pa.allows(cur, "場勘照/a.jpg")
    cur = pa.unpin(cur, "場勘照")
    assert not pa.allows(cur, "場勘照/a.jpg")


def test_reorder_puts_named_first_and_keeps_the_rest():
    cur = _pinned(("a", False), ("b", False), ("c", False))
    out = [r["rel"] for r in pa.reorder(cur, ["c", "a"])]
    assert out == ["c", "a", "b"]


def test_rows_dedupes_and_drops_garbage():
    stored = [{"rel": "a.pdf"}, {"rel": "A.PDF"}, {"rel": "../x"},
              "not a dict", {"rel": ""}, {"rel": "b.pdf", "is_dir": True}]
    out = pa.rows(stored)
    assert [r["rel"] for r in out] == ["a.pdf", "b.pdf"]
    assert out[1]["is_dir"] is True


# ── 縮圖欄位 ────────────────────────────────────────────────

def test_thumb_roundtrip():
    cur = _pinned(("deck.pdf", False), ("場勘照", True))
    assert pa.needs_thumb(cur) == ["deck.pdf"]        # 資料夾不算
    cur = pa.set_thumb(cur, "deck.pdf", "/img/x.webp")
    assert pa.needs_thumb(cur) == []
    assert pa.rows(cur)[0]["thumb_url"] == "/img/x.webp"


# ── 舊分享夾接管 ────────────────────────────────────────────

def test_adopt_legacy_share_dir():
    """客戶手上可能已經有指向舊夾的連結 —— 轉成勾選項後行為不變。"""
    cur = pa.adopt_legacy_share_dir(None, exists=True, now=NOW)
    assert pa.allows(cur, f"{pa.LEGACY_SHARE_DIR}/給客戶.pdf")
    # 冪等：再跑一次不會變兩筆
    assert len(pa.adopt_legacy_share_dir(cur, exists=True, now=NOW)) == 1


def test_adopt_does_nothing_when_absent():
    assert pa.adopt_legacy_share_dir(None, exists=False) == []
