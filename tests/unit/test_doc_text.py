# -*- coding: utf-8 -*-
"""core/doc_text 的 xlsx 讀取器（報價單多為 Excel — v2.4.62 報價分頁）。

fixture 用 openpyxl 現場生成 —— repo 裡放二進位測試檔會進 OTA ZIP。
"""
import pytest

from core.doc_text import SUPPORTED_EXTS, extract_text

openpyxl = pytest.importorskip("openpyxl")  # server-only 套件；agent 環境跳過


def _make_xlsx(path, sheets):
    """sheets: {sheet名: [[cell, ...], ...]}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(path)


def test_xlsx_extracts_cells_and_sheets(tmp_path):
    p = tmp_path / "報價單_v1.xlsx"
    _make_xlsx(p, {
        "報價": [["項目", "數量", "單價"], ["攝影師", 2, 12000], [None, None, None]],
        "備註": [["含稅", None, "付款 30/70"]],
    })
    text, err = extract_text(str(p))
    assert err == ""
    # 工作表分隔 + cell 以 | 相接、空 cell 跳過（比照 _pptx 表格輸出）
    assert "--- 工作表 報價 ---" in text
    assert "項目 | 數量 | 單價" in text
    assert "攝影師 | 2 | 12000" in text
    assert "含稅 | 付款 30/70" in text


def test_xlsx_in_supported_exts():
    assert ".xlsx" in SUPPORTED_EXTS


def test_xls_gets_actionable_error(tmp_path):
    p = tmp_path / "old.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0 fake ole2")
    text, err = extract_text(str(p))
    assert text == ""
    # 要講出路（另存 .xlsx），不是通用的「不支援」
    assert ".xlsx" in err and "xls" in err


def test_xlsx_broken_file_reports_error(tmp_path):
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"not a zip at all")
    text, err = extract_text(str(p))
    assert text == ""
    assert err  # 壞檔給錯誤訊息，不是 crash
