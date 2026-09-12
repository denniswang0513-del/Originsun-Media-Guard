# -*- coding: utf-8 -*-
"""存摺影本（2026-09-12）的特徵測試 —— 釘住現在的行為，不判對錯。

`routers/crm/invoice_files.py` 的 `_bankbook_ext`／`_bankbook_local_path` 與
`core/invoice_share.remit_view`。源頭掃描的那幾條在 test_invoice_share.py，這裡是真的呼叫。
"""
import os

import pytest
from fastapi import HTTPException

from core import invoice_share as IS
from routers.crm import invoice_files as IF


@pytest.mark.parametrize("head,ext", [
    (b"%PDF-1.4\n", ".pdf"),
    (b"\x89PNG\r\n\x1a\n\x00", ".png"),
    (b"\xff\xd8\xff\xe0JFIF", ".jpg"),
])
def test_bankbook_ext_reads_the_magic_not_the_filename(head, ext):
    assert IF._bankbook_ext(head) == ext


@pytest.mark.parametrize("head", [b"", b"hello", b"RIFF....WEBP", b"GIF89a", b"PK\x03\x04"])
def test_bankbook_ext_rejects_everything_else_with_400(head):
    with pytest.raises(HTTPException) as e:
        IF._bankbook_ext(head)
    assert e.value.status_code == 400


def test_bankbook_local_path_is_the_fixed_spot_only(tmp_path, monkeypatch):
    """只認 `_公司/存摺影本.<已知副檔名>`：半成品（.part）與別的檔不算；沒有→空；
    根目錄不存在（NAS 沒掛）→ 空、不炸。位置不進設定，所以兩台都直接找得到。"""
    root = tmp_path / "invoices"
    (root / "_公司").mkdir(parents=True)
    monkeypatch.setattr(IF, "_invoices_root", lambda: str(root))
    assert IF._bankbook_local_path() == ""

    jpg = root / "_公司" / "存摺影本.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")
    (root / "_公司" / "存摺影本.pdf.part").write_bytes(b"x")      # 半成品不算
    (root / "_公司" / "別的.pdf").write_bytes(b"%PDF")               # 別的檔不算
    assert IF._bankbook_local_path() == os.path.abspath(str(jpg))

    # 兩個副檔名並存（舊檔正被下載刪不掉）→ 給最新的那份，不是字母序第一個
    import time
    pdf = root / "_公司" / "存摺影本.pdf"
    pdf.write_bytes(b"%PDF")
    os.utime(pdf, (time.time() + 5, time.time() + 5))
    assert IF._bankbook_local_path() == os.path.abspath(str(pdf))
    assert [os.path.basename(c) for c in IF._bankbook_candidates()] == ["存摺影本.pdf", "存摺影本.jpg"]
    pdf.unlink()

    jpg.unlink()
    assert IF._bankbook_local_path() == ""
    monkeypatch.setattr(IF, "_invoices_root", lambda: str(tmp_path / "nowhere"))
    assert IF._bankbook_local_path() == ""


def test_remit_view_trims_and_treats_blank_as_missing():
    v = IS.remit_view({"account_name": "  源日有限公司 ", "bank": "   ", "account_no": None}, bankbook=True)
    assert v == {"account_name": "源日有限公司", "bank": None, "account_no": None, "bankbook": True}
    assert IS.remit_view({"account_name": " ", "bank": "", "account_no": "  "}) is None
    assert IS.remit_view(None) is None
