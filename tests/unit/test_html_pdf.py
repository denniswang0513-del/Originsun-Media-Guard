# -*- coding: utf-8 -*-
"""services/html_pdf 的特徵測試（釘住現況；Playwright 那段不在單元套件跑）＋
人員履歷 PDF 改走共用管線後的源碼掃描。"""
import os

from core.quotation_pdf import build_quotation_view, ymd
from services.html_pdf import TEMPLATES_DIR, file_data_uri, render_template, unlink_later
from tests.unit._srcscan import code_only, func_body, repo_src


def test_templates_dir_points_at_repo_templates():
    assert os.path.isdir(TEMPLATES_DIR)
    assert {"quotation_pdf.html", "resume_pdf.html"} <= set(os.listdir(TEMPLATES_DIR))


def test_render_template_autoescapes_and_takes_context():
    v = build_quotation_view({"items": [], "total": 0}, {"name": "<源日>"}, client_name="A<b>", project_name="P")
    html = render_template("quotation_pdf.html", v=v, logo_src="", seal_src="")
    assert "A&lt;b&gt;" in html and "&lt;源日&gt;" in html and "<b>" not in html.split("<body>")[1]


def test_file_data_uri_reads_png_and_returns_empty_when_missing(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    uri = file_data_uri(str(p))
    assert uri.startswith("data:image/png;base64,iVBORw0KGgo")
    assert file_data_uri("") == ""
    assert file_data_uri(str(tmp_path / "nope.png")) == ""
    # 相對路徑以 repo 根目錄解析（settings 的 logo_path 寫 frontend/img/… 就行）
    assert file_data_uri("frontend/img/originsun-logo.webp").startswith("data:image/webp;base64,")
    assert file_data_uri("frontend/img/definitely-missing.webp") == ""


def test_unlink_later_removes_file_once_and_tolerates_missing(tmp_path):
    p = tmp_path / "tmp.pdf"
    p.write_bytes(b"%PDF-")
    rm = unlink_later(str(p))
    rm()
    assert not p.exists()
    rm()        # 再呼叫不炸（BackgroundTask 可能重跑）


def test_ymd_formats_and_handles_none():
    from datetime import date
    assert (ymd(date(2026, 9, 6)), ymd(None)) == ("2026.09.06", "")


def test_resume_pdf_uses_the_shared_pipeline_only():
    """履歷 PDF 與報價 PDF 同一條 HTML→PDF 管線；不准再各自 inline 一份 Playwright。"""
    src = repo_src("routers/crm/staff.py")
    body = code_only(func_body(src, "async def staff_resume_pdf("))
    assert "render_template(" in body and "html_to_pdf(" in body and "unlink_later(" in body
    assert "async_playwright" not in body and "tempfile" not in body
    quotes = code_only(func_body(repo_src("routers/crm/quotes.py"), "async def quotation_pdf("))
    assert "async_playwright" not in quotes and "tempfile" not in quotes
