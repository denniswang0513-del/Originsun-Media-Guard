# -*- coding: utf-8 -*-
"""批次分類（owner 2026-08-28「我想要有一個可以批次處理分類的按鈕功能」）。

一次改幾百列的那條路，守衛只能比單筆更嚴 —— 單筆錯了看得出來，批次錯了是
幾百列一起錯而且畫面上看不出哪幾列。互動那半在 tests/e2e/ui_cashbook_batch_tax.py。
"""
from pathlib import Path

from tests.unit._srcscan import finance_src

ROOT = Path(__file__).resolve().parents[2]
SRC = finance_src()
from tests.unit._srcscan import cashbook_src
JS = cashbook_src()      # 2026-09-12 拆成主檔＋五段
FN = SRC.split("async def batch_set_taxonomy(")[1].split("\n@router")[0]


def test_batch_keeps_every_guard_the_single_write_has():
    """鏡射三欄、可掛專案的不變式、鎖月 —— 單筆更新有的，批次一個都不能少。"""
    assert "_sync_taxonomy(" in FN            # 三欄鏡射只有這一份規則
    assert "_enforce_cash_project_link(" in FN   # 行政/家用類別不能留在掛專案的列上
    assert "_locked_month_set(" in FN and "_raise_locked_batch(" in FN
    assert "check_logged_in(" in FN and "_mine_or_admin_write_rows(" in FN


def test_locked_rows_abort_the_whole_batch():
    """🔴 違規列不是跳過 —— 整批擋下。跳過會讓人以為全都成功了，而畫面上
    看不出哪幾列沒改到（批次掛專案踩過同一個判準）。"""
    i = FN.index("_raise_locked_batch(")
    # 擋下要發生在任何一次寫入之前
    assert i < FN.index("_sync_taxonomy(")


def test_mixed_entities_are_refused_with_a_readable_reason():
    """節點只長在一本帳的樹上 —— 混選必然有一半「找不到節點」，那訊息看起來
    像樹壞了。說清楚是選錯了。"""
    assert "橫跨兩本帳" in FN


def test_flat_ledger_can_batch_too():
    """母公司那本沒有分類樹（種子只種私帳）—— 送 category 走同一支端點。
    不支援的話，那本按下批次分類會看到一個空下拉。"""
    assert 'body.get("category")' in FN
    assert 'if not _taxTree.length' in JS or '!_taxTree.length' in JS
    assert "cash-batch-cat" in JS


def test_select_all_covers_the_whole_filtered_list():
    """🔴 分批繪製一次只畫 200 列 —— 全選只選畫面上的，會在 800 筆的清單上
    默默漏掉 600 筆（而使用者以為全選了）。"""
    seg = JS.split("cash-batch-all")[1].split("});")[0]
    assert "_shown.forEach" in seg


def test_batch_mode_takes_over_the_cell_click():
    """兩種點擊語意疊在同一個格子上：批次模式要選這一列，不能開行內編輯器。"""
    for fn in ("window._cashInline = ", "window._cashTaxEdit = "):
        seg = JS.split(fn)[1][:400]
        assert "_batch.on" in seg and "_cashRowClick" in seg
        # 不 stopPropagation 的話會冒泡到列的 onclick，一次點擊選兩次＝沒選到
        assert "stopPropagation" in seg.split("_cashRowClick")[0]
