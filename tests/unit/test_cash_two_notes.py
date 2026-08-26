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
