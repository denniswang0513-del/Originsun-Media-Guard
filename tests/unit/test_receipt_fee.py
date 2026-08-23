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

from core.finance_logic import (FEE_TOLERANCE, apply_payment_fee,
                                apply_receipt_fee, recognize_receipt_fee)
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

UTIL = "frontend/tabs/crm/crm-utils.js"


def test_frontend_tolerance_matches_the_backend():
    """🔴 前端沒辦法 import Python，所以容差有兩份。數字漂開的話：畫面自動填了
    匯費、後端的判讀卻說「還有發票沒掛上」—— 兩邊對同一筆講不同的話。

    前端那份**只能有一份**：對帳單挑發票視窗與收支明細的關聯面板都要用它。
    """
    util = js_code_only(repo_src(UTIL))
    assert f"export const FEE_TOLERANCE = {FEE_TOLERANCE};" in util, \
        f"共用層的容差跟 core.finance_logic.FEE_TOLERANCE（{FEE_TOLERANCE}）不一致"
    for path in (JS, "frontend/tabs/crm/crm-cashbook.js"):
        src = js_code_only(repo_src(path))
        assert "FEE_TOLERANCE = " not in src, f"{path} 又自己留了一份容差"


def test_autofill_only_inside_the_tolerance():
    """差 30 元是匯費；差 30,000 是分期收款，自動填就是亂填。"""
    util = js_code_only(repo_src(UTIL))
    assert "gap > 0 && gap <= FEE_TOLERANCE ? gap : 0" in util, \
        "匯費自動填沒有上限 —— 分期收款會被填成一筆天價匯費"


def test_the_invoice_alloc_carries_gross_plus_fee():
    """送出去的 amount 必須是 amt + fee，否則發票永遠差那幾十塊收不齊。

    ⚠ 只有**收款側**這樣（perItemFee）—— 請款單那側沒有逐張的匯費，加上去會
    讓分配額比實際多（見 test_stmt_picker 的那兩條）。"""
    js = js_code_only(repo_src(JS))
    assert "(a.amt || 0) + (S2.perItemFee ? (a.fee || 0) : 0)" in js, \
        "分配金額不再是「分到的現金＋被扣的匯費」"
    assert "one.fee = Math.round(a.fee || 0)" in js, "匯費沒有一起送回後端"


def test_the_write_path_grosses_up_the_deposit():
    """後端要真的補 deposit —— 只收下 fee 不補的話帳戶餘額會少掉。

    寫回的動作收在 `apply_receipt_fee`（deposit 與 bank_fee 一起動），這裡只確認
    對帳單匯入那條路確實借用它、沒有自己再寫一份 —— 三條寫入路徑（這裡、關聯
    面板、編輯視窗）各寫一份的話，規則遲早漂開。不變量本身由上面那幾條
    `apply_*` 的行為測試釘住。
    """
    from tests.unit._srcscan import code_only
    src = code_only(repo_src("routers/api_finance_stmt.py"))
    assert "apply_receipt_fee(ce, fee_total)" in src, \
        "沒走共用的那支規則（自己寫一份遲早跟付款側漂開）"
    assert "ce.bank_fee =" not in src, "又在呼叫端自己寫回 bank_fee 了"


def test_schema_accepts_the_fee():
    from core.schemas import CashInvoiceLink
    assert CashInvoiceLink(invoice_id="x", amount=149900, fee=30).fee == 30
    assert CashInvoiceLink(invoice_id="x", amount=100).fee == 0, "舊呼叫端要還能用"


class _Entry:
    """收支明細列的替身 —— 只要有這四欄就夠了。"""

    def __init__(self, **kw):
        self.deposit = self.expense = self.bank_fee = self.claim = None
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def flow(self):
        """cash_entry_flow = deposit − expense − bank_fee − claim。"""
        return ((self.deposit or 0) - (self.expense or 0)
                - (self.bank_fee or 0) - (self.claim or 0))


def test_applying_a_receipt_fee_moves_both_fields_and_keeps_net_inflow():
    """🔴 兩個欄位**必須一起動**。這條釘的是不變量（淨流入不變），
    不是單一欄位 —— 只斷言 bank_fee == 30 的話，忘了補 deposit 也會過。"""
    e = _Entry(deposit=149870)
    apply_receipt_fee(e, 30)
    assert e.deposit == 149900, "deposit 沒補成客戶實付的金額"
    assert e.bank_fee == 30
    assert e.flow == 149870, "淨流入變了 —— 那一列在對帳工作台就配不上了"


def test_applying_a_payment_fee_moves_both_fields_and_keeps_total_outflow():
    """付款側的鏡像：守「總流出不變」，從 expense 搬進 bank_fee。"""
    e = _Entry(expense=5000)
    apply_payment_fee(e, 10)
    assert e.expense == 4990, "沒有從 expense 搬出來（帳上會多流出一筆）"
    assert e.bank_fee == 10
    assert e.flow == -5000, "總流出變了"


@pytest.mark.parametrize("apply_fee, kw", [(apply_receipt_fee, {"deposit": 149870}),
                                           (apply_payment_fee, {"expense": 5000})])
def test_applying_the_same_fee_twice_changes_nothing(apply_fee, kw):
    """重存一次不能疊加 —— 關聯面板每次儲存都會再跑一遍這一步。"""
    e = _Entry(**kw)
    apply_fee(e, 30)
    first = (e.deposit, e.expense, e.bank_fee)
    apply_fee(e, 30)
    assert (e.deposit, e.expense, e.bank_fee) == first


@pytest.mark.parametrize("apply_fee, kw", [(apply_receipt_fee, {"deposit": 149900}),
                                           (apply_payment_fee, {"expense": 5000})])
def test_clearing_the_fee_puts_the_amount_back(apply_fee, kw):
    """改成 0 要退回銀行原本說的數字，而且 bank_fee 要變回 None（不是 0）——
    列表用 `or None` 判有沒有匯費。"""
    e = _Entry(**kw)
    apply_fee(e, 30)
    apply_fee(e, 0)
    assert e.bank_fee is None
    assert (e.deposit, e.expense) == (kw.get("deposit"), kw.get("expense"))


def test_the_grid_header_and_rows_all_have_the_same_number_of_columns():
    """🔴 多一欄就要改三個地方：grid-template-columns、表頭（heads）、資料列（cells）。
    漏掉任一個，整張表會從那一欄開始錯位 —— 而且不會有任何錯誤訊息，
    只是「已收」的數字跑到「尚欠」底下。

    兩側各有自己的欄數（付款側少一欄匯費，它的匯費是整列一個），所以逐側算 ——
    表頭與資料列都是從 `_SIDES` 那張表推出來的，這條就是在釘那張表自己一致。
    """
    import re
    src = repo_src(JS)
    sides = src[src.index('const _SIDES = {'):src.index('const _sideOf =')]

    for key, grid_name in (('inv', '_INV_GRID'), ('pay', '_PAY_GRID')):
        i = sides.index(f'    {key}: {{')
        nxt = sides.find('\n    pay: {', i + 1) if key == 'inv' else -1
        block = sides[i:nxt if nxt > 0 else len(sides)]

        cols = re.search(rf"{grid_name} = _GRID\('([^']+)'\)", src).group(1)
        n_cols = len(cols.split())
        # 收到外層陣列的 `]],`（heads 是跨行寫的，抓到第一個 `],` 只會數到一半）
        n_heads = len(re.findall(r"\['[^']+'", re.search(
            r"heads: \[(.*?)\]\],\n", block, re.S).group(1)))
        # cells 陣列裡每一項自成一行、以 12 個空白 + '[' 開頭
        n_cells = len(re.findall(r"\n            \[", re.search(
            r"cells: \(v, hit\) => \[(.*?)\n        \],", block, re.S).group(1)))
        fee_col = 1 if 'perItemFee: true' in block else 0
        # 勾選框 + 各欄 + 分配金額 + （收款側才有的）匯費
        want = 1 + n_heads + 1 + fee_col
        assert n_heads == n_cells, \
            f"_SIDES.{key}：表頭 {n_heads} 欄、資料列 {n_cells} 欄 —— 會整排錯位"
        assert n_cols == want, \
            f"_SIDES.{key}：grid {n_cols} 欄，但表頭+輸入格共 {want} 欄"
