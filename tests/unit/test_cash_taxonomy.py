# -*- coding: utf-8 -*-
"""三層分類（owner 2026-08-27「類別、項目、子項目」）。

🔴 儲存維持複合鍵 category（`公司_專案`）—— 那是 finance_category_map 的鍵，
三表/卡債/家用往來/可掛專案判定/銀行匯入規則全部吃它；拆欄＝對映一次全毀。
畫面與篩選拆三層，兩邊由 core.cash_taxonomy 互轉。
"""
from pathlib import Path

from core.cash_taxonomy import book_of, item_of, join_category, split_category, taxonomy

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_split_and_join_roundtrip():
    assert split_category("公司_專案") == ("公司", "專案")
    assert split_category("家用_變動支出") == ("家用", "變動支出")
    # 只切第一個底線 —— 轉匯與定存_公司信用卡 的項目是「公司信用卡」
    assert split_category("轉匯與定存_公司信用卡") == ("轉匯與定存", "公司信用卡")
    assert split_category("信用卡") == ("信用卡", "")     # 沒有第二層
    assert split_category("") == ("", "")
    assert split_category(None) == ("", "")
    for c in ("公司_專案", "信用卡", "轉匯與定存_公司信用卡"):
        assert join_category(*split_category(c)) == c
    assert book_of("個人_生活") == "個人" and item_of("個人_生活") == "生活"


def test_taxonomy_tree_from_live_categories():
    cats = ["公司_專案", "公司_薪水", "個人_生活", "家用_變動支出", "信用卡"]
    t = taxonomy(cats, ["外出用餐", "交通", ""])
    assert t["books"] == ["公司", "個人", "家用", "信用卡"]   # KNOWN_BOOKS 排序在前
    assert t["items_by_book"]["公司"] == ["專案", "薪水"]
    assert t["items_by_book"]["信用卡"] == []               # 無第二層
    assert t["sub_items"] == ["交通", "外出用餐"]            # 去空、排序


def test_taxonomy_scoped_to_this_ledger():
    """🔴 值域＝這本帳實際用到的類別 ∪ 同類別底下未用過的 —— 直接餵整份
    finance_category_map（兩本帳共用）會讓母公司的平面類別（行政/薪資…）
    各自變成一個「類別」，私帳的類別下拉從 5 個爆成 37 個（實測）。"""
    src = _read("routers/crm/finance.py")
    opt = src.split("async def cash_entry_options(")[1].split(chr(10) + "@router")[0]
    assert "CrmCashEntry.category" in opt and "distinct()" in opt, "值域要從實際資料來"
    assert "books_used" in opt and "book_of(c) in books_used" in opt


def test_backend_serves_three_levels():
    src = _read("routers/crm/finance.py")
    opt = src.split("async def cash_entry_options(")[1].split(chr(10) + "@router")[0]
    assert '"taxonomy": taxonomy(' in opt
    lst = src.split("async def list_cash_entries(")[1].split(chr(10) + "@router")[0]
    assert "book: str = Query" in lst and "item: str = Query" in lst
    assert "_TAX_SEP" in lst, "前綴/後綴比對要走 core 的分隔符，別自己拼字串"
    dic = src.split("def _to_cash_dict(")[1][:900]
    assert '"book": _tax_split(e.category)[0]' in dic
    assert '_tax_split(e.category)[1]' in dic, "item 欄空時要由複合鍵補"


def test_cashbook_ui_three_columns_and_cascading_filters():
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    for k in ('data-sort-key="book"', 'data-sort-key="item"', 'data-sort-key="sub_item"'):
        assert k in html, k
    assert 'id="cash-filter-book"' in html and 'id="cash-filter-item"' in html
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "_taxonomy" in js and "o.taxonomy" in js
    # 連動：選了類別，項目只列該類別底下的
    assert "map[_filters.book]" in js
    # 換類別要清掉項目（否則篩出空白）
    seg = js.split("cash-filter-book').addEventListener")[1][:400]
    assert "_filters.item = ''" in seg
    # 格內編輯＝類別→項目兩層，組回複合鍵存
    ed = js.split("window._cashCatEdit = (ev")[1].split("/** 刷卡金額")[0]
    assert "cash-cat-book" in ed and "cash-cat-item" in ed
    assert "book + '_' + iSel.value" in ed, "存回去要組成複合鍵"
    assert "searchableSelect(bSel" in ed and "searchableSelect(iSel" in ed
