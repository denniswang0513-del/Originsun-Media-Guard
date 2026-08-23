# -*- coding: utf-8 -*-
"""收款側的匯費（owner 2026-08-23：「新增一欄匯費，數字相減有餘額時自動幫我填」）。

客戶匯 149,900、銀行只入 149,870 —— 那 30 元被匯出行扣走了。三件事同時要成立：
  · 發票算**收齊**（149,870 + 30），不是尚欠 30
  · 帳戶只增加 149,870（銀行說多少就是多少）
  · 那 30 元是管理費用，不是憑空消失的差額

🔴 只寫 bank_fee 而不把 deposit 補回去，帳戶餘額就會少 30 ——
   而對帳工作台比的是**淨流**（/statement-lines/{id}/match），那一列從此永遠
   配不上。付款側 2026-08-22 就是這樣寫錯的（v2.4.143 → 144），當時的測試只
   斷言 bank_fee == 10，錯的實作照樣過。所以這裡釘的是不變量，不是欄位。
"""
import pytest

from core.finance_logic import FEE_TOLERANCE, recognize_receipt_fee
from tests.unit._srcscan import js_code_only, repo_src

JS = "frontend/tabs/finance/subviews/recon.js"


def _net(dep, fee):
    """cash_entry_flow 對一筆純收入列 = deposit − bank_fee。"""
    return dep - fee


def test_net_inflow_is_unchanged():
    """不變量：認列匯費前後，真正進帳戶的錢一樣多。"""
    dep, fee = recognize_receipt_fee(149870, None, 30)
    assert _net(dep, fee) == 149870, "帳戶餘額被匯費吃掉了"
    assert dep == 149900, "deposit 沒有補成客戶實際付的金額"
    assert fee == 30


def test_invoice_gets_credited_the_gross_amount():
    """發票該認列的是客戶實付 —— 不然它會永遠差 30 元收不齊。"""
    dep, _fee = recognize_receipt_fee(149870, None, 30)
    assert dep == 149900


def test_calling_twice_changes_nothing():
    """冪等 —— 重存一次不該讓 deposit 一直長大。"""
    a = recognize_receipt_fee(149870, None, 30)
    b = recognize_receipt_fee(*a[:2], 30)
    assert a == b == (149900, 30)


def test_changing_the_fee_keeps_the_bank_number():
    """改匯費金額時，基準是淨額不是 deposit —— 否則第二次會疊上去。"""
    dep, fee = recognize_receipt_fee(*recognize_receipt_fee(149870, None, 30)[:2], 15)
    assert (dep, fee) == (149885, 15)
    assert _net(dep, fee) == 149870


def test_zero_fee_clears_it():
    """取消匯費要回到銀行原本的數字。"""
    assert recognize_receipt_fee(149900, 30, 0) == (149870, 0)


def test_negative_fee_is_refused():
    with pytest.raises(ValueError):
        recognize_receipt_fee(149870, None, -1)


def test_it_mirrors_the_payment_side():
    """付款側守「總流出不變」、收款側守「淨流入不變」—— 兩支是鏡像。
    任一支被改成另一種基準，這條會倒。"""
    from core.finance_logic import recognize_bank_fee
    out_exp, out_fee = recognize_bank_fee(8010, None, 10)
    assert out_exp + out_fee == 8010, "付款側的總流出變了"
    in_dep, in_fee = recognize_receipt_fee(8010, None, 10)
    assert in_dep - in_fee == 8010, "收款側的淨流入變了"
    assert out_exp != in_dep, "兩側被寫成同一個方向了"


# ── 前端那格自動填多少 ────────────────────────────────────────

def test_frontend_tolerance_matches_the_backend():
    """🔴 前端沒辦法 import Python，所以容差有兩份。數字漂開的話：畫面自動填了
    匯費、後端的判讀卻說「還有發票沒掛上」—— 兩邊對同一筆講不同的話。"""
    js = js_code_only(repo_src(JS))
    assert f"const _FEE_TOLERANCE = {FEE_TOLERANCE};" in js, \
        f"recon.js 的容差跟 core.finance_logic.FEE_TOLERANCE（{FEE_TOLERANCE}）不一致"


def test_autofill_only_inside_the_tolerance():
    """差 30 元是匯費；差 30,000 是分期收款，自動填就是亂填。"""
    js = js_code_only(repo_src(JS))
    assert "gap > 0 && gap <= _FEE_TOLERANCE ? gap : 0" in js, \
        "匯費自動填沒有上限 —— 分期收款會被填成一筆天價匯費"


def test_the_invoice_alloc_carries_gross_plus_fee():
    """送出去的 amount 必須是 amt + fee，否則發票永遠差那幾十塊收不齊。"""
    js = js_code_only(repo_src(JS))
    assert "const amount = Math.round((a.amt || 0) + (a.fee || 0));" in js
    assert "fee: Math.round(a.fee || 0)" in js, "匯費沒有一起送回後端"


def test_the_write_path_grosses_up_the_deposit():
    """後端要真的補 deposit —— 只收下 fee 不補的話帳戶餘額會少掉。"""
    from tests.unit._srcscan import code_only
    src = code_only(repo_src("routers/api_finance_stmt.py"))
    assert "recognize_receipt_fee(ce.deposit, ce.bank_fee," in src, \
        "apply 沒走共用的那支規則（自己寫一份遲早跟付款側漂開）"


def test_schema_accepts_the_fee():
    from core.schemas import CashInvoiceLink
    assert CashInvoiceLink(invoice_id="x", amount=149900, fee=30).fee == 30
    assert CashInvoiceLink(invoice_id="x", amount=100).fee == 0, "舊呼叫端要還能用"


def test_the_grid_header_and_rows_all_have_the_same_number_of_columns():
    """🔴 多一欄就要改三個地方：grid-template-columns、表頭、資料列。
    漏掉任一個，整張表會從那一欄開始錯位 —— 而且不會有任何錯誤訊息，
    只是「已收」的數字跑到「尚欠」底下。"""
    import re
    src = repo_src(JS)
    grid = re.search(r"grid-template-columns:'\s*\+\s*'([^']+);", src).group(1)
    n_cols = len(grid.split())
    head = re.search(r"<div></div>\$\{th\('發票號'\).*?\n.*?匯費', 1\)\}", src, re.S).group(0)
    n_head = 1 + head.count("${th(")
    i = src.index("<input type=\"checkbox\" ${on ? 'checked' : ''}")
    row = src[i:src.index("</div>`;", i)]
    n_row = 1 + row.count("${cell(") + row.count('<input class="crm-input"')
    assert n_cols == n_head == n_row == 10, \
        f"欄數對不上：grid {n_cols} / 表頭 {n_head} / 資料列 {n_row}"
