# -*- coding: utf-8 -*-
"""電子發票檔的資料夾與檔名規則（owner 2026-08-19 拍板）。

規則一旦上線就會產生幾百個檔案，改名等於要人回頭搬檔 —— 所以釘死在測試裡。
正本：routers/crm/finance.py::_invoice_file_name / upload_invoice_file。

    {invoices_root}/{YYYY}/{YYYY-MM}/{YYYYMMDD}_{發票號碼}_{抬頭前20字}_{含稅金額}[_作廢].ext

每一段為什麼在那個位置，見 _invoice_file_name 的 docstring。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from routers.crm.finance import _invoice_file_name, _safe_part

TPE = timezone(timedelta(hours=8))


def _inv(**kw):
    base = dict(id="c0642185409247b6a423693e33d0fd2d",
                invoice_date=datetime(2026, 8, 13, tzinfo=TPE),
                invoice_number="DQ45891571",
                company_name="台灣歐姆龍健康事業股份有限公司",
                amount_total=166950, issue_status="已開立", payment_status="未收款")
    base.update(kw)
    return SimpleNamespace(**base)


def test_standard_name():
    assert _invoice_file_name(_inv(), ".pdf") == \
        "20260813_DQ45891571_台灣歐姆龍健康事業股份有限公司_166950.pdf"


def test_date_is_taipei_not_utc():
    """🔴 台北 00:00 存成 timestamptz 是前一天 16:00 UTC —— 直接 strftime 會差一天。

    收據那邊踩過（填 08-19 檔名變 20260818），同一個坑不踩第二次。"""
    midnight_tpe = datetime(2026, 8, 13, 0, 0, tzinfo=TPE)
    assert _invoice_file_name(_inv(invoice_date=midnight_tpe), ".pdf").startswith("20260813_")


def test_no_invoice_number_falls_back_to_id_prefix():
    """開立中的發票還沒有號碼 —— 用 id 前 8 碼頂替（比照收據的做法），
    不能讓檔名少一段而黏在一起。"""
    n = _invoice_file_name(_inv(invoice_number=""), ".pdf")
    assert n.startswith("20260813_c0642185_")


def test_voided_marker_is_a_suffix_not_a_prefix():
    """作廢標記放尾端 —— 放前綴會破壞資料夾內的日期排序。"""
    n = _invoice_file_name(_inv(issue_status="作廢"), ".pdf")
    assert n.endswith("_作廢.pdf")
    assert n.startswith("20260813_")
    # 款項狀態作廢也算
    assert _invoice_file_name(_inv(payment_status="作廢"), ".pdf").endswith("_作廢.pdf")


def test_company_name_truncated_to_20():
    """台灣公司名可以很長，不截斷會撞 Windows 260 字元路徑上限
    （根目錄若指到 NAS 深層路徑更吃緊）。"""
    long_name = "財團法人某某某某某某某某某某某某某某某某某某文教基金會"
    n = _invoice_file_name(_inv(company_name=long_name), ".pdf")
    assert long_name[:20] in n
    assert long_name[:21] not in n


def test_no_company_name():
    assert "_無抬頭_" in _invoice_file_name(_inv(company_name=""), ".pdf")


def test_no_date():
    assert _invoice_file_name(_inv(invoice_date=None), ".pdf").startswith("nodate_")


@pytest.mark.parametrize("raw,want", [
    ('台灣/歐姆龍', "台灣歐姆龍"),
    ('A:B*C?D"E<F>G|H\\I', "ABCDEFGHI"),
    ("  前後空白  ", "前後空白"),
    (None, ""),
])
def test_safe_part_strips_windows_illegal_chars(raw, want):
    """檔名片段不能帶 \\ / : * ? " < > | —— 帶了在 Windows 直接開不了檔。"""
    assert _safe_part(raw) == want


def test_amount_zero_still_present():
    """金額 0（或未填）仍要留一段，否則檔名段數不固定、之後想解析回來會麻煩。"""
    assert _invoice_file_name(_inv(amount_total=None), ".pdf").endswith("_0.pdf")
