# -*- coding: utf-8 -*-
"""收支列表的「發票」格就地連結、可多張（owner 2026-09-02「這裡的發票要可以
連結（多筆），像是連結專案那樣」）。

收款一次對到好幾張發票是常態（合併匯款：客戶一次匯 3 張的錢）。
"""
from tests.unit._srcscan import (code_only, func_body, js_code_only,
                                 js_func_body, repo_src)

CB = "frontend/tabs/crm/crm-cashbook.js"
PICKER = "frontend/js/shared/project-picker.js"


def test_the_list_carries_every_linked_invoice():
    """🔴 只帶 `invoice_id`（金額最大那張）的話，挑選視窗只勾得回一張 ——
    存檔就把同一筆收款上的其餘幾張連結洗掉（同 payment_ids 那顆）。"""
    src = repo_src("routers/crm/cash.py")
    assert '"invoice_ids": list(invoice_ids or []),' in src
    fn = code_only(func_body(src, "async def list_cash_entries("))
    assert "load_alloc_links_map(session, 'invoice', entity=ent)" in fn


def test_one_loader_serves_both_sides():
    """兩側（發票／請款單）撈連結走**同一支** —— 形狀只差表與欄名。"""
    src = code_only(repo_src("routers/crm/cash.py"))
    assert "async def load_alloc_links_map(session, kind: str, *, entity=None)" in src
    assert "load_payment_links_map" not in src, "舊名字的薄殼又回來了（零呼叫端）"


def test_the_fee_prefill_uses_this_row_not_the_panels_last_one():
    """🔴 這一輪最容易靜默寫錯的地方。

    分配面板的 `toItem` 把匯費預帶成 `autoFee(尚欠, _allocRemainCash())`，
    而 `_allocRemainCash()` 讀的是**面板上一次開的那一列**的殘額。從列表的就地
    連結叫它，會拿別列的數字算出一個錯的匯費、而且直接寫進 DB —— 畫面上完全
    看不出來。就地連結要餵**這一列自己的**殘額。
    """
    js = js_code_only(repo_src(CB))
    fn = js_func_body(js, "window._cashInvPick = (ev, id) => {")
    # 同一支 toItem，殘額由呼叫端餵：面板餵自己的（預設參數），就地連結餵這一列的
    assert "_ALLOC_SIDES.invoice.toItem(inv, Math.max(0, left))" in fn
    assert "_allocRemainCash" not in fn, "就地連結又去讀面板的殘額了"
    assert "toItem: (i, remainCash = _allocRemainCash()) =>" in js
    assert "autoFee(_outstanding(i), remainCash)" in js
    assert "autoFee(" not in fn, "匯費規則長出第二份了"


def test_already_linked_invoices_keep_their_amount_and_fee():
    """🔴 本來就掛著的那幾張，金額與匯費要原封保留 —— 就地勾一下就把人在分配
    面板調好的匯費洗掉，是這個功能最貴的失敗方式（發票側是 per-item fee）。"""
    js = js_code_only(repo_src(CB))
    fn = js_func_body(js, "window._cashInvPick = (ev, id) => {")
    assert "await _fetch(`/cash-entries/${e.id}/invoices`)" in fn, "沒撈現有分配"
    assert "keep[iid].amount" in fn and "keep[iid].fee" in fn


def test_only_income_rows_get_the_invoice_picker():
    """支出列沒有「這筆收款對到哪張發票」這回事 —— 那一欄在私帳讓給請款單，
    在母公司則只有收入列可點。"""
    js = js_code_only(repo_src(CB))
    seg = js.split("cls: 'cash-col-inv',")[-1].split("</div>")[0]
    assert "e.deposit ?" in seg


def test_the_picker_says_it_does_not_touch_amounts():
    """挑選視窗只決定「掛哪幾張」；逐張金額與匯費在分配面板調。
    這條寫在共用件的檔頭 —— 下一個接它的人才不會以為那裡可以改錢。"""
    src = repo_src(PICKER)
    fn = js_func_body(js_code_only(src), "export function openInvoicePicker(o) {")
    assert "multi: true" in fn
    assert "extraRows: o.linkedRows || []," in fn, "已收齊的那幾張會被存檔洗掉"
