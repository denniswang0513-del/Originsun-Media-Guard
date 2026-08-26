# -*- coding: utf-8 -*-
"""收支雙備註（owner 2026-08-26「兩個備註欄：銀行帳戶本來的資訊／我的附註」
＋「列表直接點按就編輯、自動儲存」）。

行為釘：bank_memo=機器來源、note=人寫；卡單匯入不再拿標記汙染附註欄；
行內編輯走 PUT 部分更新（只送那一欄）且不觸發整列點擊。
"""
from pathlib import Path

from scripts.split_cash_notes import split_note

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_split_note_classifies_by_origin():
    # 銀行側特徵 → bank_memo
    assert split_note("本筆分期尚有未到期金額NT$ 8,656 [sheet-import]") == \
        ("本筆分期尚有未到期金額NT$ 8,656", "")
    assert split_note("國外交易服務費－161.00 [sheet-import]")[0].startswith("國外交易服務費")
    assert split_note("全支付－全聯福利中心TAIPEI [sheet-import]")[1] == ""
    assert split_note("中國信託 [sheet-import]")[1] == ""       # 跨轉對方行
    assert split_note("網路自轉 [sheet-import]")[1] == ""
    # 人寫的 → note 保留
    assert split_note("256G 記憶卡 [sheet-import]") == ("", "256G 記憶卡")
    assert split_note("昱涵牙刷牙膏等備品 [卡單匯入]") == ("", "昱涵牙刷牙膏等備品")
    # 純標記 → 兩欄都清空；沒標記的列不動
    assert split_note("[sheet-import]") == ("", "")
    assert split_note("客戶說月底匯") == (None, None)
    assert split_note("") == (None, None)


def test_backend_carries_bank_memo():
    src = _read("routers/crm/finance.py")
    assert '"bank_memo"' in src.split("def _to_cash_dict")[1][:1600]
    assert "CrmCashEntry.bank_memo.ilike(ql)" in src, "搜尋要涵蓋銀行資訊欄"
    models = _read("db/models.py")
    assert "bank_memo = Column(Text" in models


def test_card_import_stops_polluting_note():
    """卡單匯入的來源由 status='card' 說明 —— 不再把「[卡單匯入]」寫進附註欄
    （那正是 owner 截圖裡整欄的雜訊）。"""
    src = _read("routers/api_finance_card.py")
    assert 'note="[卡單匯入]"' not in src


def test_cashbook_has_two_note_columns_with_inline_edit():
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    assert 'data-sort-key="bank_memo"' in html and 'data-sort-key="note"' in html
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    fn = js.split("window._cashInline = (ev")[1]
    assert "stopPropagation()" in fn, "格子點擊不可觸發整列選取"
    assert "JSON.stringify({ [f]: v })" in fn, "自動儲存只送那一欄（部分更新）"
    for f in ("'sub_item'", "'bank_memo'", "'note'"):
        assert f"_cashInline(event,'${{e.id}}',{f})" in js, f


def test_cashbook_filters_date_sub_amount():
    """篩選列（owner 2026-08-26「可以篩選日期區間、分類、子項目、金額」）：
    日期含當日（迄日 +1 天開區間）、子項目精確、金額比**量級**
    （收入或 支出＋匯費 取大者 —— 比單邊會讓收款列全篩不到）。"""
    src = _read("routers/crm/finance.py")
    fn = src.split("async def list_cash_entries(")[1].split("\n@router")[0]
    for frag in ("date_from", "timedelta(days=1)", "sub_item == sub_item", "_fn.greatest"):
        assert frag in fn, frag
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    for i in ("cash-filter-from", "cash-filter-to", "cash-filter-sub",
              "cash-filter-amin", "cash-filter-amax"):
        assert i in html, i
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    for k in ("date_from", "date_to", "sub_item", "amount_min", "amount_max"):
        assert f"params.set('{k}'" in js, k


def test_category_cell_edits_with_cascading_selects():
    """類別欄點按填寫（owner 2026-08-26 起）＝格內下拉；2026-08-27 起改兩層
    （類別→項目，組回複合鍵存）—— 自由文字會打錯字長出新類別，選單保證值域。
    詳細行為釘在 test_cash_taxonomy.py。"""
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    fn = js.split("window._cashCatEdit = (ev")[1].split("/** 刷卡金額")[0]
    assert "searchableSelect(bSel" in fn and "searchableSelect(iSel" in fn
    assert "JSON.stringify({ category, item: iSel.value" in fn
    assert "_cashCatEdit(event,'${e.id}')" in js


def test_all_dropdowns_searchable_everywhere():
    """「所有下拉選單都要可以搜尋」：門檻 4（案源這種 4 選項的也要）；
    /my-ledger.html 是獨立頁沒載 app.js —— 要自己開 initSelectAutoUpgrade
    （owner 截圖裡客戶下拉還是原生的就是這個漏）。"""
    up = _read("frontend/js/shared/select-upgrade.js")
    assert "MIN_OPTIONS = 4" in up
    ml = _read("frontend/my-ledger.html")
    assert "select-upgrade.js" in ml and "initSelectAutoUpgrade" in ml
