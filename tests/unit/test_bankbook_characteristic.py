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


def test_bankbook_local_path_follows_the_invoice_whitelist(tmp_path, monkeypatch):
    """沒設→空；設了但檔不在→空；在根目錄外→空（就算檔存在）；在根目錄內且存在→絕對路徑。"""
    root = tmp_path / "invoices"
    (root / "_公司").mkdir(parents=True)
    inside = root / "_公司" / "存摺影本.pdf"
    inside.write_bytes(b"%PDF-1.4\n")
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(IF, "_invoices_root", lambda: str(root))

    assert IF._bankbook_local_path({}) == ""
    assert IF._bankbook_local_path({"bankbook_path": ""}) == ""
    assert IF._bankbook_local_path({"bankbook_path": str(root / "_公司" / "missing.pdf")}) == ""
    assert IF._bankbook_local_path({"bankbook_path": str(outside)}) == ""
    assert IF._bankbook_local_path({"bankbook_path": str(inside)}) == os.path.abspath(str(inside))


def test_remit_view_trims_and_treats_blank_as_missing():
    v = IS.remit_view({"account_name": "  源日有限公司 ", "bank": "   ", "account_no": None}, bankbook=True)
    assert v == {"account_name": "源日有限公司", "bank": None, "account_no": None, "bankbook": True}
    assert IS.remit_view({"account_name": " ", "bank": "", "account_no": "  "}) is None
    assert IS.remit_view(None) is None
