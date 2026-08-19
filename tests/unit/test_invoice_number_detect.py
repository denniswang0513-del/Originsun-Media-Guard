# -*- coding: utf-8 -*-
"""從電子發票證明聯抽發票號碼（上傳後自動帶入的來源）。

owner 2026-08-19：「電子發票上傳之後可以選擇自動更新發票號碼，電子發票的位置
都是固定的」。位置固定＝可以靠「發票號碼：」這個標籤定位，不需要 OCR 也不需要
座標解析。

🔴 抽到的號碼只**回報**、不自動寫入：發票號碼是法定識別，套不套用由人按一下決定
（見 routers/crm/finance.upload_invoice_file 的回應 detected_invoice_number）。
"""
import io
import os

import pytest

from routers.crm.finance import _detect_invoice_number, _INVOICE_NO_LABELLED


# ── 標籤正則（真 PDF 的抽取結果會長成什麼樣）──────────────

@pytest.mark.parametrize("line", [
    "發票號碼：DQ45891570",                 # 全形冒號
    "發票號碼: DQ45891570",                 # NFKC 之後的半形冒號（core.doc_text 會轉）
    "發 票 號 碼 ： DQ45891570",             # 證明聯常把字距拉開
    "發票號碼DQ45891570",                   # 冒號被字型吃掉
    "電子發票證明聯\n2026-08-14\n發票號碼: DQ45891570\n買  方: 某某公司",
])
def test_labelled_forms(line):
    m = _INVOICE_NO_LABELLED.search(line)
    assert m and m.group(1) == "DQ45891570"


def test_label_wins_over_other_codes():
    """證明聯上除了發票號碼還可能有隨機碼/載具號碼 —— 有標籤時以標籤為準。"""
    text = "隨機碼 AB12345678\n發票號碼: DQ45891570\n載具 CD87654321"
    assert _INVOICE_NO_LABELLED.search(text).group(1) == "DQ45891570"


# ── 檔案層（真的跑 pypdf）────────────────────────────────

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")


def _fx(name):
    """真的電子發票版面 PDF（committed fixture）。

    🔴 為什麼是 fixture 而不是測試時用 playwright 現產：Windows 的
    SelectorEventLoop（repo 為 asyncpg 穩定性刻意設的）**不支援 subprocess**，
    playwright 的 sync API 內部要 spawn —— 單獨跑會過、跟著整套跑就炸。
    Fixture 也讓這幾條變成純讀檔，快且確定。
    重新產生的方式見 tests/fixtures/README.md。
    """
    return os.path.join(FIXTURES, name)


def test_real_pdf_roundtrip():
    """整條鏈：pypdf 抽字 → CJK 正規化 → 標籤正則。手打字串證明不了 pypdf
    抽出來長怎樣（實測它把全形冒號吐成 '發票號碼: '，中間有一格）。"""
    assert _detect_invoice_number(_fx("einvoice_labelled.pdf")) == "DQ45891570"


def test_real_pdf_without_label_single_match():
    """沒有標籤但全文恰好只有一組合法號碼 → 仍可推定。"""
    assert _detect_invoice_number(_fx("einvoice_no_label_single.pdf")) == "DQ45891570"


def test_real_pdf_without_label_multiple_matches_gives_up():
    """🔴 沒標籤又有多組 → 回空字串讓人自己填。

    猜錯的代價是把法定號碼寫錯，遠大於讓人多打十個字。"""
    assert _detect_invoice_number(_fx("einvoice_no_label_multi.pdf")) == ""


def test_images_are_not_ocred(tmp_path):
    """圖片檔（紙本拍照）不做 OCR —— 直接回空，不是丟例外。"""
    p = tmp_path / "scan.jpg"
    io.open(p, "wb").write(b"\xff\xd8\xff\xe0 not really a jpeg")
    assert _detect_invoice_number(str(p)) == ""


def test_missing_or_broken_file_returns_empty(tmp_path):
    """壞檔/不存在都不能讓上傳整個失敗 —— 偵測只是加值。"""
    assert _detect_invoice_number(str(tmp_path / "nope.pdf")) == ""
    bad = tmp_path / "bad.pdf"
    io.open(bad, "wb").write(b"not a pdf at all")
    assert _detect_invoice_number(str(bad)) == ""


def test_upload_reports_but_does_not_apply():
    """上傳端點只回報偵測結果，不自己寫進 invoice_number。"""
    import re
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "routers/crm/finance.py"),
        encoding="utf-8").read()
    i = src.index("async def upload_invoice_file(")
    body = src[i:i + 3000]
    assert "detected_invoice_number" in body, "上傳回應沒有帶偵測結果"
    assert not re.search(r"inv\.invoice_number\s*=", body), \
        "上傳端點不該自己寫入 invoice_number —— 那是使用者按下去才發生的事"
