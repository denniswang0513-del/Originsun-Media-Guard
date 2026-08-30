# -*- coding: utf-8 -*-
"""代開發票真正留在公司的錢（owner 2026-08-21/22 抓到的錯帳）。

現象：2026-08 損益表的業外收入是 $927,222、稅前淨利 $1,802,923。
實際上那 927,222 是**要匯給代開人的錢**，公司只賺 8%。

根因是欄位名字騙人：`crm_invoices.commission` 存的是
`passthrough_commission()` 算出來的「應匯金額」＝ 面額 × (1 − 費率)，
不是手續費。`build_pnl` 照著名字把它整筆記成「代開手續費收入」。

再往下一層（owner 2026-08-21 拍板）：代開的對方是個人、開不出發票給公司
抵進項，所以整張發票的銷項稅由公司自己吞 —— 手續費要再扣掉那筆稅。
8 月：實收 80,628、扣稅 47,993 → 真正只剩 32,635（毛利率 8% → 3.2%）。
"""
import pytest

from core.finance_logic import (invoice_tax, passthrough_commission,
                                passthrough_fee_income)


def _inv(total, category="內部代開", commission=None, tax=None):
    if commission is None:
        commission = passthrough_commission(total, category)
    return {"category": category, "amount_total": total,
            "commission": commission, "tax_amount": tax}


# ── 欄位語意 ─────────────────────────────────────────────────────

def test_commission_column_is_the_payout_not_the_fee():
    """🔴 這條就是整個錯帳的來源，釘死它。"""
    assert passthrough_commission(95000, "內部代開") == 87400   # 95000 × 92%
    assert passthrough_commission(95000, "外部代開") == 85500   # 95000 × 90%


def test_fee_is_what_is_left_after_paying_out_and_after_tax():
    """8% 的手續費裡還含著 5% 營業稅，公司自己吞。"""
    inv = _inv(95000)                       # 應匯 87,400、稅 4,524
    assert invoice_tax(inv) == 4524
    assert passthrough_fee_income(inv) == 95000 - 87400 - 4524   # 3,076


def test_fee_is_much_smaller_than_the_commission_column():
    """如果哪天有人把它改回 commission，這條會炸。"""
    inv = _inv(95000)
    assert passthrough_fee_income(inv) < inv["commission"] / 10


@pytest.mark.parametrize("total,expected", [
    (42000, 42000 - 38640 - 2000),
    (95000, 95000 - 87400 - 4524),
    (12600, 12600 - 11592 - 600),
    (385000, 385000 - 354200 - 18333),
    (83200, 83200 - 76544 - 3962),
])
def test_matches_the_real_august_invoices(total, expected):
    """生產 2026-08 的真實發票（逐張對過）。"""
    assert passthrough_fee_income(_inv(total)) == expected


def test_august_total_is_32635():
    """九張加起來 = 32,635，不是 927,222 也不是 80,628。"""
    totals = [42000, 95000, 95000, 12600, 74550, 385000, 63000, 157500, 83200]
    assert sum(passthrough_fee_income(_inv(t)) for t in totals) == 32635


# ── 邊界 ─────────────────────────────────────────────────────────

def test_non_passthrough_is_zero():
    """一般專案發票走營業收入，不該在這裡再算一次。"""
    assert passthrough_fee_income(_inv(95000, "專案", commission=0)) == 0
    assert passthrough_fee_income(_inv(95000, "專案", commission=87400)) == 0


def test_external_passthrough_counts_too():
    """外部代開也是代開（費率不同而已）。"""
    inv = _inv(95000, "外部代開")
    assert inv["commission"] == 85500
    assert passthrough_fee_income(inv) == 95000 - 85500 - 4524


def test_missing_commission_is_zero_not_the_whole_invoice():
    """🔴 沒填應匯金額時要回 0（不知道要匯多少），不能把整張面額當成賺到 ——
    那會比原本的錯帳更誇張。"""
    assert passthrough_fee_income(_inv(95000, commission=0)) == 0
    assert passthrough_fee_income(
        {"category": "內部代開", "amount_total": 95000}) == 0


def test_uses_the_shared_tax_helper():
    """稅額走 invoice_tax（三層 fallback 的正本），不自己再算一次 ——
    發票有填 tax_amount 時要用填的那個。"""
    inv = _inv(95000, commission=87400, tax=9999)
    assert passthrough_fee_income(inv) == 95000 - 87400 - 9999


# ── 接線 ─────────────────────────────────────────────────────────

def test_pnl_and_drilldown_use_the_same_function():
    """🔴 表頭用淨額、明細用 commission 的話，明細加起來永遠對不上表頭。"""
    from tests.unit._srcscan import finance_logic_src, repo_src
    assert "passthrough_fee_income(inv)" in finance_logic_src()
    assert "passthrough_fee_income(inv)" in repo_src("services/finance_statements.py")


def test_label_says_tax_was_deducted():
    """標籤要講出「已扣銷項稅」—— 不然看的人會以為那是 8% 的全額。"""
    from tests.unit._srcscan import finance_logic_src
    src = finance_logic_src()
    assert "代開手續費（已扣銷項稅）" in src
    assert '"代開手續費收入", c)' not in src, "舊的整筆記法還在"
