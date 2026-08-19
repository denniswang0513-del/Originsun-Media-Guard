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

BS = chr(92)

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


# ── 上傳後的兩個連帶行為（owner 2026-08-19 回報）──────────

def _finance_src():
    return io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "routers/crm/finance.py"),
        encoding="utf-8").read()


def test_upload_marks_issued_but_not_voided():
    """上傳電子發票證明聯＝這張已經開出去了 → issue_status 轉『已開立』。
    但作廢的不動：作廢也會留存證明聯，那不是『開立中』。"""
    src = _finance_src()
    i = src.index("async def upload_invoice_file(")
    body = src[i:i + 3000]
    assert 'inv.issue_status = "已開立"' in body, "上傳後沒有轉成已開立"
    assert '!= "作廢"' in body, "作廢的發票不該被改成已開立"


def test_invoice_root_must_be_absolute():
    """🔴 相對路徑要擋。os.makedirs('192.168.1.132\Archive\…') 會**成功** ——
    在 agent 工作目錄底下建出一整串資料夾，於是「存到 NAS」變成靜靜存進
    C:\OriginsunAgent\192.168.1.132\… 而畫面上一切正常
    （owner 少打開頭兩個反斜線，三個電子發票檔全落在本機）。

    判斷細節見 test_invoice_root_accepts_only_drive_or_unc —— isabs 不夠。"""
    src = _finance_src()
    i = src.index("async def set_invoices_root(")
    body = src[i:i + 2500]
    guard = body.index("ntpath.splitdrive")
    assert guard < body.index("os.makedirs(root"),         "路徑檢查必須在 makedirs **之前** —— 否則資料夾已經被建出來了"
    assert "不可寫入" in body, "只 makedirs 不夠：NAS 可能給列目錄卻不給寫，要實際寫一個檔驗"


def test_file_name_resyncs_on_update():
    """檔名是上傳當下組的；發票號碼/日期之後才補上時要重新對齊。

    最常見：上傳時號碼還空著 → 檔名落到 id 前 8 碼（fallback），之後號碼補上，
    檔名還停在 `20260806_42ec03d2_…`。"""
    src = _finance_src()
    assert "def _resync_invoice_file(" in src
    i = src.index("async def update_invoice(")
    assert "_resync_invoice_file" in src[i:i + 2500], "update_invoice 沒有重新對齊檔名"
    j = src.index("def _resync_invoice_file(")
    helper = src[j:j + 1400]
    assert "os.path.exists(target)" in helper, \
        "目標同名檔已存在時不可覆蓋 —— 那是別人的稅務憑證"
    assert "except OSError" in helper, \
        "搬移失敗只能維持原狀，不能讓『改個發票號碼』整個失敗"


def test_migrate_writes_back_db_path():
    """🔴 搬檔之後 **一定要**把新路徑寫回 inv.file_url。

    _resync_invoice_file 只搬檔、不動 DB。少了寫回那行＝檔案搬走了、DB 還指著
    舊路徑，稅務憑證全變孤兒檔（2026-08-19 dev 實測到，幸好沒先對生產跑）。
    """
    src = _finance_src()
    i = src.index("async def migrate_invoice_files(")
    body = src[i:i + 3000]
    assert "inv.file_url = new" in body, "搬移後沒有把新路徑寫回 DB"
    assert body.index("_resync_invoice_file") < body.index("inv.file_url = new"), \
        "順序必須是「先搬檔、再寫回 DB」"


def test_migrate_is_dry_run_by_default():
    """比照匯入腳本：預設只出計畫，?apply=true 才真的動檔案。"""
    src = _finance_src()
    i = src.index("async def migrate_invoice_files(")
    sig = src[i:i + 260]
    assert "apply: bool = Query(False)" in sig, "migrate 預設就該是 dry-run"


@pytest.mark.parametrize("root,ok,why", [
    ("192.168.1.132" + BS + "Archive",        False, "相對路徑（NAS 少打兩個反斜線）"),
    (BS + "192.168.1.132" + BS + "Archive",   False, "只有一個反斜線 → 其實是 C: 根目錄"),
    (BS * 2 + "192.168.1.132" + BS + "Ar",    True,  "正確 UNC"),
    ("//192.168.1.132/Archive",               True,  "正斜線 UNC"),
    ("D:" + BS + "Invoices",                  True,  "本機磁碟"),
    ("uploads" + BS + "invoices",             False, "相對"),
])
def test_invoice_root_accepts_only_drive_or_unc(root, ok, why):
    """🔴 `os.path.isabs()` 不足以擋：Windows 對「單一個反斜線開頭」也回 True，
    但那是**目前磁碟的根目錄**。少打一個反斜線的 \\192.168.1.132\Archive 會變成
    C:\192.168.1.132\Archive，而且 makedirs 與寫入測試都會成功 —— 完全看不出
    存錯地方。2026-08-19 少打兩個、又少打一個，各中一次。"""
    import ntpath
    drive, _ = ntpath.splitdrive(root)
    looks_unc = root.startswith(BS * 2) or root.startswith("//")
    assert bool(looks_unc or (drive and drive.endswith(":"))) is ok, why


def test_invoice_root_validation_uses_splitdrive_not_isabs():
    """實作要真的用 splitdrive/UNC 判斷，不是回頭只靠 os.path.isabs。"""
    src = _finance_src()
    i = src.index("async def set_invoices_root(")
    body = src[i:i + 2500]
    assert "ntpath.splitdrive" in body, "沒有用 splitdrive 判斷磁碟機"
    assert 'startswith("' + BS * 4 + '")' in body or "startswith('" + BS * 4 + "')" in body,         "沒有判斷 UNC 的兩個反斜線"
