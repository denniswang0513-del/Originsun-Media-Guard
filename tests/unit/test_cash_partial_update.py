# -*- coding: utf-8 -*-
"""收支明細的寫入不變式（不碰 DB）。

每一條都對應一件在生產資料上實際發生過、或實測會發生的事，不是理論問題。

掃描式（`_srcscan`）與行為式並存是刻意的：掃描式擋「這行程式碼被刪掉」，行為式
擋「程式碼還在但已經不對了」。單靠掃描會被自己的字面寫法綁死（見
test_project_link_rule_has_one_source 裡那段說明）。
"""
from types import SimpleNamespace

from core.project_link import CASH_CATEGORIES, PAYMENT_CATEGORIES, PETTY_ITEMS  # noqa: E402
from tests.unit._srcscan import code_only, func_body  # noqa: E402
from tests.unit._srcscan import finance_src


def test_update_cash_entry_is_a_partial_update():
    """🔴 PUT 一定要 exclude_unset。

    編輯視窗只送 10 個欄位（前端 _FIELDS），但收支明細有 20 幾欄。整包 model_dump
    寫回會把沒送的 status／project_label／invoice_number／payee 洗成預設值 ——
    匯進來的「待確認」標記、專案標籤、發票號碼會在有人按一下編輯之後消失。
    """
    body = code_only(func_body(finance_src(),
                               'async def update_cash_entry('))
    assert 'exclude_unset=True' in body, 'PUT 又變回整包寫回了 —— 會洗掉沒送的欄位'


def test_project_link_rule_has_one_source():
    """三個入口的「可連結專案」清單集中在 core/project_link.py。

    值刻意不同（零用金只開放專案雜支是 owner 2026-08-17 的決定），所以這裡釘的是
    「還在同一個檔案裡」而不是「三個值相等」。
    """
    assert PETTY_ITEMS == ("專案雜支",)          # 零用金：刻意排除專案外包
    assert "專案雜支" in PAYMENT_CATEGORIES
    assert "專案" in CASH_CATEGORIES             # 收支明細還有專案收入
    # 🔴 行為版而不是掃描版：舊寫法斷言原始碼裡有 `PROJECT_LINK_ITEMS =
    # _PETTY_PROJECT_ITEMS` 這串字，改成同樣正確的 `= PETTY_ITEMS` 會假紅，
    # 而在檔尾補一行 `PROJECT_LINK_ITEMS = ("專案雜支","專案外包")` 反而照樣綠。
    # 用 `is` 比對物件本身：有人重新賦值成別的 tuple 就一定抓到。
    from routers.crm.petty import PROJECT_LINK_ITEMS
    assert PROJECT_LINK_ITEMS is PETTY_ITEMS, \
        '零用金的可連結專案清單不是 core.project_link 那份了'


def test_cash_project_link_is_enforced_on_both_write_paths():
    """建立與更新都要過守衛 —— 只擋前端的話，API 直接打就繞過去了。"""
    src = finance_src()
    for fn in ('async def create_cash_entry(', 'async def update_cash_entry('):
        body = code_only(func_body(src, fn))
        assert '_enforce_cash_project_link' in body, f'{fn} 沒有強制專案連結規則'


def test_partial_update_really_omits_unsent_fields():
    """行為版（上面那條是掃描版）：只送 3 個欄位，dump 出來就只有 3 個。

    掃描測試只保證那串字還在程式碼裡；這條保證它真的有效 —— 沒送的 status／
    project_label／invoice_number 不會出現在 dump 裡，也就洗不掉 DB 既有值。
    """
    from core.schemas import CashEntryPayload
    d = CashEntryPayload(entry_date="2026-08-20", summary="測試", deposit=1000
                         ).model_dump(exclude_unset=True)
    assert set(d) == {"entry_date", "summary", "deposit"}
    for gone in ("status", "project_label", "invoice_number", "payee"):
        assert gone not in d, f"{gone} 混進 partial update 了 —— 會把既有值洗掉"


def test_enforce_cash_project_link_actually_raises():
    """行為版：行政支出掛專案要擋下來（409），專案類的放行。"""
    import pytest
    from fastapi import HTTPException
    from routers.crm.cash import _enforce_cash_project_link

    with pytest.raises(HTTPException) as ei:
        _enforce_cash_project_link(SimpleNamespace(project_id="p1", category="行政"))
    assert ei.value.status_code == 409
    assert "行政" in ei.value.detail

    for ok in CASH_CATEGORIES:                       # 專案類全部放行
        _enforce_cash_project_link(SimpleNamespace(project_id="p1", category=ok))
    # 沒掛專案的列，類別是什麼都不該擋
    _enforce_cash_project_link(SimpleNamespace(project_id=None, category="行政"))


def test_normalize_cash_fks_turns_empty_strings_into_null():
    """soft FK 欄位的 '' 要收成 None。

    🔴 留著空字串的話 `WHERE project_id IS NULL` 這類查詢會靜默漏掉那些列 ——
    HTML select 的空選項送出來就是 ''，不是 None，所以每一筆從表單建立的收支
    都會踩到。
    """
    from routers.crm.cash import _CASH_FK_FIELDS, _normalize_cash_fks
    e = SimpleNamespace(**{f: "" for f in _CASH_FK_FIELDS})
    _normalize_cash_fks(e)
    for f in _CASH_FK_FIELDS:
        assert getattr(e, f) is None, f
    # 有值的不能被動到
    e2 = SimpleNamespace(**{f: "abc" for f in _CASH_FK_FIELDS})
    _normalize_cash_fks(e2)
    assert all(getattr(e2, f) == "abc" for f in _CASH_FK_FIELDS)


def test_set_primary_invoice_writes_and_clears_all_three_columns():
    """三個反正規化欄位必須一起動 —— 分開寫就會出現「有 id 沒號碼」的半套狀態。"""
    from routers.crm.finance import _set_primary_invoice
    e = SimpleNamespace(invoice_id=None, invoice_number=None, has_invoice=0)
    _set_primary_invoice(e, SimpleNamespace(id="i1", invoice_number="AB12345678"))
    assert (e.invoice_id, e.invoice_number, e.has_invoice) == ("i1", "AB12345678", 1)
    _set_primary_invoice(e, None)
    assert (e.invoice_id, e.invoice_number, e.has_invoice) == (None, None, 0)


def test_statement_text_decodes_big5_bank_exports():
    """台灣網銀匯出的 CSV 幾乎都是 big5 —— 解錯就整份對帳單變亂碼、一列都解析不出來。"""
    from routers.api_finance_stmt import _statement_text
    line = "2026/08/01 跨行轉入 500,000.00 1,500,000.00"
    assert _statement_text("a.csv", line.encode("big5")) == line
    assert _statement_text("a.csv", line.encode("utf-8-sig")) == line
    assert _statement_text("a.txt", line.encode("utf-8")) == line
    # 壞位元組不可以炸 —— 換成替代字元，讓解析器去說「找不到交易列」
    assert _statement_text("a.txt", b"\xff\xfe\x00bad") is not None
