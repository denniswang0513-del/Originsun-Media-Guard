# -*- coding: utf-8 -*-
"""「費用已代墊」（owner 2026-09-02）—— 人員費用那一列的第三顆按鈕。

這筆錢別人先掏了：公司要還的是**代墊人**，但費用歸屬仍是原本那個人。

🔴 為什麼歸屬不能跟著換人：連結私帳的收入鏡射看的是**成本行的
`actual_staff_id`**（core.ledger_project.mirror_lines）。代墊如果改成本行的
歸屬，那筆錢就會被鏡射成代墊人的私帳**收入** —— 而它是要還他的錢，不是他賺的，
私帳營收會憑空多一個代墊金額。所以代墊只動請款單，成本行一個欄位都不碰。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from tests.unit._srcscan import js_code_only, js_func_body, repo_src  # noqa: E402

JS = "frontend/tabs/crm/crm-projects-finance.js"


def test_the_button_reuses_the_existing_payment_flow():
    """不另建一套代墊流程 —— 機制本來就在（視窗裡的勾選框），只是藏著沒人
    找得到（生產庫 0 筆用過）。再刻一份的話，「誰去領這筆錢」就會有兩條規則。"""
    js = js_code_only(repo_src(JS))
    assert "'費用已代墊'" in js or "費用已代墊" in js
    # 第三顆按鈕走同一支，只多帶 advanced 旗標
    assert "window._costCreatePayment = function(payeeName, amount, summary, status, advanced)" in js
    assert "',true)" in js and "費用已代墊</button>" in js
    # 代墊沒有自己的建立端點 —— `_costCreatePayment` 裡只有一次 POST
    # （檔案裡另一次是「新增預支」，那是 is_advance=1 的另一個功能）
    fn = js_func_body(js, "window._costCreatePayment = function(payeeName, amount, summary, status, advanced) {")
    assert fn.count("await _fetch('/payments', {") == 1


def test_the_cost_line_is_never_reassigned():
    """🔴 代墊只動請款單。成本行的歸屬（actual_staff_id）一個字都不能改 ——
    改了，連結私帳就會把代墊的錢鏡射成代墊人的收入。"""
    js = js_code_only(repo_src(JS))
    fn = js_func_body(js, "window._costCreatePayment = function(payeeName, amount, summary, status, advanced) {")
    for bad in ("actual_staff_id", "estimated_staff_id", "cost-lines", "cost_lines"):
        assert bad not in fn, bad


def test_the_payee_becomes_the_person_who_fronted_the_cash():
    """公司要還的是先掏錢的那個人 —— 收款人換成代墊人，原本那個人記進
    `advance_by`（費用歸屬）。"""
    js = js_code_only(repo_src(JS))
    assert "payee_name: isAdvance && advanceBy ? advanceBy : originalPayee," in js
    assert "advance_by: isAdvance ? originalPayee : ''," in js


def test_a_ticked_advance_without_a_person_is_blocked():
    """🔴 勾了代墊卻沒選人＝靜靜變成一張付給原本那個人的單：代墊人根本拿不到
    錢，而畫面上看起來一切正常。當場擋，不要讓它存進去。"""
    js = js_code_only(repo_src(JS))
    assert "if (isAdvance && !advanceBy) {" in js
    assert "if (isAdvance && advanceBy === originalPayee) {" in js
    # 擋下來要**在送出之前**（try 之外），不然按鈕已經 disabled 又沒還原
    fn = js_func_body(js, "window._costCreatePayment = function(payeeName, amount, summary, status, advanced) {")
    head = fn.split("await _fetch('/payments', {")[0]
    assert "if (isAdvance && !advanceBy) {" in head
    assert head.index("if (isAdvance && !advanceBy) {") < head.index("try {")


def test_the_detail_panel_no_longer_labels_the_owner_as_the_payee():
    """🔴 `advance_by` 裝的是**費用歸屬人**，詳情面板原本把它標成
    「代墊人（實際收款人）」—— 剛好相反，看的人會以為錢匯給他。"""
    js = js_code_only(repo_src(JS))
    assert "'（實際收款人）'" not in js
    assert "' 代墊　·　費用歸屬 '" in js
    # model 的註解也要說清楚（下一個讀的人只會看那裡）
    m = repo_src("db/models/_crm.py")
    seg = m.split("advance_by = Column")[0].rsplit("planned_month", 1)[-1]
    assert "費用歸屬人" in seg and "不是代墊人" in seg
