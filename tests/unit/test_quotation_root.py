# -*- coding: utf-8 -*-
"""報價單資料夾（owner 2026-09-07「跟發票一樣有個地方指定儲存位置」）：
settings.quotes_root → 每次產 PDF 存一份到 {根}/{年}/{年-月}/{檔名}（best-effort）；設定端點與發票共用同一份路徑驗證；
前端「資料夾」卡只有 crm-utils 一份，發票與報價都用它。"""
import os
import pytest
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_pdf_response_archives_a_copy_but_never_blocks_download():
    src = repo_src("routers/crm/quotes.py")
    body = code_only(func_body(src, "async def _quotation_pdf_response("))
    assert "_archive_quotation_pdf(tmp_pdf, view)" in body
    assert "except OSError as exc:" in body and "warning(" in body, "資料夾不通只記 log，不擋下載"
    assert body.index("html_to_pdf(") < body.index("_archive_quotation_pdf(") < body.index("no_store_file(")


def test_archive_path_is_root_year_month_filename(tmp_path, monkeypatch):
    import routers.crm.quotes as Q
    monkeypatch.setattr(Q, "_quotes_root", lambda: str(tmp_path))
    src = tmp_path / "src.pdf"; src.write_bytes(b"%PDF-1.4 x")
    dest = Q._archive_quotation_pdf(str(src), {"filename": "20260907_客戶_專案_源日報價單.pdf"})
    assert dest == str(tmp_path / "2026" / "2026-09" / "20260907_客戶_專案_源日報價單.pdf")
    assert os.path.isfile(dest)
    Q._archive_quotation_pdf(str(src), {"filename": "20260907_客戶_專案_源日報價單.pdf"})   # 同名覆蓋不炸


def test_quotes_root_setting_and_default():
    import routers.crm.quotes as Q
    from config import load_settings
    assert Q._QUOTES_DEFAULT_ROOT.endswith(os.path.join("uploads", "quotations"))
    assert Q._quotes_root() == ((load_settings().get("quotes_root") or "").strip() or Q._QUOTES_DEFAULT_ROOT)


def test_root_validation_is_shared_with_invoices():
    inv = repo_src("routers/crm/invoice_files.py")
    assert "def validate_root_dir(root: str) -> None:" in inv
    assert "validate_root_dir(root)" in code_only(func_body(inv, "async def set_invoices_root("))
    q = repo_src("routers/crm/quotes.py")
    assert "from .invoice_files import validate_root_dir" in code_only(func_body(q, "async def set_quotations_root("))
    assert q.count("ntpath.splitdrive") == 0, "路徑規則不准在報價這邊再長一份"
    from fastapi import HTTPException
    from routers.crm.invoice_files import validate_root_dir
    with pytest.raises(HTTPException):
        validate_root_dir("192.168.1.132\\Archive")          # 少打開頭兩個反斜線＝相對路徑
    validate_root_dir("")                                       # 留空＝預設，直接過


def test_endpoints_are_admin_only():
    src = repo_src("routers/crm/quotes.py")
    for fn in ("async def get_quotations_root(", "async def set_quotations_root("):
        assert "check_admin(request)" in code_only(func_body(src, fn)), fn


def test_frontend_root_card_is_one_shared_component():
    utils = js_code_only(repo_src("frontend/tabs/crm/crm-utils.js"))
    assert utils.count("export async function initRootFolderCard(") == 1
    inv = js_code_only(repo_src("frontend/tabs/crm/crm-invoices.js"))
    assert "initRootFolderCard({" in inv and "endpoint: '/invoices-root', key: 'invoices_root'" in inv
    assert "_fetch('/invoices-root'" not in inv, "發票那份面板要改吃共用卡，不留第二份"
    quotes = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "endpoint: '/quotations-root', key: 'quotes_root'" in quotes
    html = repo_src("frontend/tabs/crm/crm-quotes.html")
    assert 'id="quote-root-toggle"' in html and 'id="quote-root-panel"' in html
