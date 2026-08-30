# -*- coding: utf-8 -*-
"""三層分類（owner 2026-08-27「類別、項目、子項目」）。

🔴 儲存維持複合鍵 category（`公司_專案`）—— 那是 finance_category_map 的鍵，
三表/卡債/家用往來/可掛專案判定/銀行匯入規則全部吃它；拆欄＝對映一次全毀。
畫面與篩選拆三層，兩邊由 core.cash_taxonomy 互轉。
"""
from pathlib import Path

from tests.unit._srcscan import js_func_body

from core.cash_taxonomy import book_of, item_of, join_category, split_category, taxonomy
from tests.unit._srcscan import finance_src

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
    src = finance_src()
    opt = src.split("async def cash_entry_options(")[1].split(chr(10) + "@router")[0]
    assert "CrmCashEntry.category" in opt and "distinct()" in opt, "值域要從實際資料來"
    assert "books_used" in opt and "book_of(c) in books_used" in opt


def test_backend_serves_three_levels():
    src = finance_src()
    opt = src.split("async def cash_entry_options(")[1].split(chr(10) + "@router")[0]
    assert '"taxonomy": taxonomy(' in opt
    lst = src.split("async def list_cash_entries(")[1].split(chr(10) + "@router")[0]
    assert "book: str = Query" in lst and "item: str = Query" in lst
    assert "_TAX_SEP" in lst, "前綴/後綴比對要走 core 的分隔符，別自己拼字串"
    dic = src.split("def _to_cash_dict(")[1][:900]
    assert '"book": _tax_split(e.category)[0]' in dic
    assert '_tax_split(e.category)[1]' in dic, "item 欄空時要由複合鍵補"


def test_cashbook_ui_three_columns_and_cascading_filters():
    """三個分類欄可排序、篩選會連動、格內編輯**點哪欄編哪欄**。

    2026-08-27 起機制換成分類樹（深度不限）：篩選是一排會長的下拉、編輯選到
    還有下層就再長一格。三欄仍在（它們是路徑前三層的鏡射），但值域與寫入都走
    節點 —— 前端不再自己組複合鍵。樹本身的行為釘在 test_cash_tree.py。
    """
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    for k in ('data-sort-key="book"', 'data-sort-key="item"', 'data-sort-key="sub_item"'):
        assert k in html, k
    assert 'id="cash-filter-tax"' in html, "分類篩選是一個會長的容器，不是寫死幾顆"
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "o.tree" in js and "_indexTax" in js, "樹要從後端來並建索引"
    # 連動：每一層只列上一層底下的（規則只有 _taxKidsAt 一份，篩選與編輯共用）
    assert "_taxKidsAt = (chain, i)" in js
    # 選「全部」＝退回上一層，不是整個清空（不然一路點下去就回不去了）
    seg = js_func_body(js, "function _syncTaxFilter()")
    assert "chain[i - 1].id" in seg
    ed = js.split("window._cashTaxEdit = (ev")[1].split("/** 刷卡金額")[0]
    assert "Math.min(level, chain.length)" in ed, "上層沒值時要從頭問（形不成路徑）"
    assert "_taxKidsAt(sel, i)" in ed, "每層的值域＝上一層的子節點（共用那份規則）"


# ── 分類同步不可以碰它管不到的欄（2026-08-30 回歸）────────────

def test_path_and_mirror_are_not_inverses_for_a_flat_category_with_a_sub_item():
    """🔴 `path_from_columns` 與 `mirror_from_path` **不是**互為反向。

    平的類別（母公司那本全是平的）配上子項目時，路徑的位置 1 放的是子項目，
    而 mirror 讀位置 1 當**項目** —— 反推回來類別就被改寫了：

        ('其他', '保險費') → ['其他','保險費'] → ('其他_保險費', '保險費', '')

    這一條釘的是「不要拿 mirror 去覆蓋三欄」。真的覆蓋下去的後果：類別變成
    finance_category_map 裡沒有的複合鍵（那一列掉進三表的未歸類），子項目不見。
    """
    from core.cash_taxonomy import mirror_from_path, path_from_columns
    assert mirror_from_path(path_from_columns("其他", "保險費")) != ("其他", "", "保險費")
    # 複合類別才成立（私帳那本）
    for cat, sub in (("公司_薪水", ""), ("家用_變動支出", "醫療保健")):
        m = mirror_from_path(path_from_columns(cat, sub))
        assert (m[0], m[2]) == (cat, sub), (cat, sub, m)


def test_sync_taxonomy_never_derives_item_from_category():
    """🔴 `item` 不是 category 的衍生欄 —— 母公司那本它是**獨立資料**。

    零用金放那張 AP 的類別、福委會放 AP_CATEGORY、CSV 匯入吃「項目」欄。
    `_sync_taxonomy` 一旦推導它（`item_of(category)`，平類別＝空字串），
    使用者從編輯視窗按一次存檔就把那一欄清掉了，而畫面上沒有任何提示。
    """
    from tests.unit._srcscan import (code_only, func_body)
    body = code_only(func_body(finance_src(),
                               "async def _sync_taxonomy("))
    tail = body.split("if not (")[1]      # 節點分支之後那一段（category 那條路）
    assert "item_of(" not in tail, "category 那條路不可以推導 item"
    assert "mirror_from_path(want)" not in tail, "更不可以拿 mirror 覆蓋三欄"


def test_editing_a_row_does_not_demote_a_node_deeper_than_three_levels():
    """🔴 三欄只裝得下 3 層，深度活在 `taxonomy_node_id` 裡。

    收支明細的編輯視窗每次存檔都會送 category＋sub_item，如果拿那三欄回頭反查
    節點，一定只找得到第 3 層那個 —— 使用者只是來改金額，分類就被降級了，
    而且每存一次降一次。私帳有 60 筆掛在第 4 層以後（2026-08-30 實查）。

    前三層真的被改掉時仍然要重新反查 —— 那是使用者確實換了分類。
    """
    import asyncio

    from routers.crm.cash import _sync_taxonomy

    class Row:
        entity = "mine"

        def __init__(self, cat, item, sub, nid):
            self.category, self.item, self.sub_item = cat, item, sub
            self.taxonomy_node_id = nid

    # 家用 ▸ 變動支出 ▸ 醫療保健 ▸ 乳癌治療 ▸ 台北馬偕（第 5 層）
    paths = {"n3": ["家用", "變動支出", "醫療保健"],
             "n5": ["家用", "變動支出", "醫療保健", "乳癌治療", "台北馬偕"],
             "other": ["家用", "固定支出"]}
    three = ("家用_變動支出", "變動支出", "醫療保健")

    def run(data, start="n5"):
        e = Row(*three, start)
        asyncio.run(_sync_taxonomy(None, e, data, paths))
        return e.taxonomy_node_id

    # 只是存檔、分類沒動 → 第 5 層要留著
    assert run({"category": three[0], "sub_item": three[2]}) == "n5"
    assert run({"category": three[0]}) == "n5"
    # 真的換了分類 → 照常重新反查（換到樹上沒有的組合就是沒有節點）
    assert run({"category": three[0], "sub_item": "牙科"}) is None
    assert run({"category": "家用_固定支出", "sub_item": ""}) == "other"
    # 本來就只掛在第 3 層的，行為不變
    assert run({"category": three[0], "sub_item": three[2]}, start="n3") == "n3"
