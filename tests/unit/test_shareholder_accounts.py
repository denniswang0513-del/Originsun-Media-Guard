# -*- coding: utf-8 -*-
"""股東往來帳戶（owner 2026-08-21）。

owner：「多三個帳戶，是股東向的借款或是股東放在公司裡的錢。需要股東支付的費用
可以從這裡扣款，匯給股東的費用這裡則會增加。」方向已與 owner 確認：

    餘額 ＝ **公司欠股東多少**
    股東墊付／把錢放進公司 → deposit → 餘額增加
    公司匯還股東           → expense → 餘額減少

🔴 這種帳戶**不是現金**。整份測試守的就是這一句：混進 bank_lines 的話，
資產負債表的現金會憑空多出股東墊付的錢（那些錢從來沒進過公司的銀行帳戶），
現金流量表的期初/期末、儀表板的 runway 全部跟著失真。

借款與投資款分開（owner「分開沒問題」），因為報表落點不同：
    shareholder_loan    → 負債（其他應付款－股東往來）
    shareholder_capital → 權益（股東投入的資本）
"""
import pytest

from core.finance_logic import (SHAREHOLDER_KINDS, bank_balances_asof,
                                build_balance_sheet, is_shareholder_kind,
                                split_bank_lines)

ACCTS = [
    {"id": "fubon", "name": "台北富邦", "acct_kind": "bank", "opening_balance": 100000},
    {"id": "petty", "name": "零用金", "acct_kind": "cash", "opening_balance": 5000},
    {"id": "sh1", "name": "王士源", "acct_kind": "shareholder_loan", "opening_balance": 0},
    {"id": "sh2", "name": "蘇家弘", "acct_kind": "shareholder_loan", "opening_balance": 0},
    {"id": "sh3", "name": "黃聖鈞", "acct_kind": "shareholder_capital", "opening_balance": 0},
]


def _entry(acct, deposit=0, expense=0, day="2026-08-10"):
    return {"bank_account_id": acct, "entry_date": day,
            "deposit": deposit, "expense": expense}


# ── 分類 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,want", [
    ("bank", False), ("cash", False),
    ("shareholder_loan", True), ("shareholder_capital", True),
    ("", False), (None, False),
])
def test_is_shareholder_kind(kind, want):
    assert is_shareholder_kind(kind) is want


def test_both_shareholder_kinds_are_registered():
    assert set(SHAREHOLDER_KINDS) == {"shareholder_loan", "shareholder_capital"}


def test_split_puts_each_kind_in_its_own_bucket():
    lines = bank_balances_asof(ACCTS, [], "2026-08")
    got = split_bank_lines(ACCTS, lines)
    assert [x["id"] for x in got["cash"]] == ["fubon", "petty"]
    assert [x["id"] for x in got["shareholder_loan"]] == ["sh1", "sh2"]
    assert [x["id"] for x in got["shareholder_capital"]] == ["sh3"]


def test_unknown_kind_falls_back_to_cash():
    """未知的 acct_kind 當現金 —— 舊資料沒有這一欄時不能整個消失。"""
    accts = [{"id": "x", "name": "舊帳戶", "opening_balance": 7}]
    got = split_bank_lines(accts, bank_balances_asof(accts, [], "2026-08"))
    assert [x["id"] for x in got["cash"]] == ["x"]


# ── 方向（owner 確認過的那條）────────────────────────────────────

def test_shareholder_pays_a_company_expense_increases_what_we_owe():
    """股東墊付 → 公司欠款變多。"""
    lines = bank_balances_asof(ACCTS, [_entry("sh1", deposit=5000)], "2026-08")
    owed = next(x for x in lines if x["id"] == "sh1")["amount"]
    assert owed == 5000


def test_company_repays_shareholder_reduces_what_we_owe():
    """公司匯還股東 → 公司欠款變少。"""
    entries = [_entry("sh1", deposit=5000), _entry("sh1", expense=2000)]
    lines = bank_balances_asof(ACCTS, entries, "2026-08")
    assert next(x for x in lines if x["id"] == "sh1")["amount"] == 3000


def test_overpaying_a_shareholder_goes_negative():
    """匯超過了 → 負數＝股東反而欠公司。不夾成 0（藏起來只會更晚發現）。"""
    lines = bank_balances_asof(ACCTS, [_entry("sh1", expense=800)], "2026-08")
    assert next(x for x in lines if x["id"] == "sh1")["amount"] == -800


# ── 🔴 不是現金 ───────────────────────────────────────────────────

def test_shareholder_money_is_not_counted_as_cash():
    """整份測試的核心：股東墊付 50,000 之後，公司的現金**一毛都不該變多**。"""
    entries = [_entry("sh1", deposit=50000)]
    got = split_bank_lines(ACCTS, bank_balances_asof(ACCTS, entries, "2026-08"))
    cash_total = sum(x["amount"] for x in got["cash"])
    assert cash_total == 105000, "現金被股東往來污染了"


def test_balance_sheet_puts_loan_in_liabilities_not_assets():
    lines = split_bank_lines(
        ACCTS, bank_balances_asof(ACCTS, [_entry("sh1", deposit=50000)], "2026-08"))
    bs = build_balance_sheet(
        "2026-08", bank_lines=lines["cash"],
        shareholder_loan_lines=lines["shareholder_loan"],
        shareholder_capital_lines=lines["shareholder_capital"])
    asset_labels = " ".join(x["label"] for x in bs["assets"]["current"])
    assert "王士源" not in asset_labels, "股東往來跑到資產去了"
    liab_labels = {x["label"]: x["amount"] for x in bs["liabilities"]["current"]}
    assert liab_labels.get("股東往來－王士源") == 50000


def test_balance_sheet_puts_capital_in_equity():
    """投資款是權益不是負債 —— 這就是借款/投資款分開的意義。"""
    lines = split_bank_lines(
        ACCTS, bank_balances_asof(ACCTS, [_entry("sh3", deposit=300000)], "2026-08"))
    bs = build_balance_sheet(
        "2026-08", bank_lines=lines["cash"],
        shareholder_loan_lines=lines["shareholder_loan"],
        shareholder_capital_lines=lines["shareholder_capital"])
    eq = {x["label"]: x["amount"] for x in bs["equity"]["lines"]}
    assert eq.get("股東投資款－黃聖鈞") == 300000
    liab = " ".join(x["label"] for x in bs["liabilities"]["current"])
    assert "黃聖鈞" not in liab, "投資款被當成負債了"


def test_shareholder_lines_default_to_empty():
    """沒傳這兩個參數時行為不變 —— 既有呼叫端（快照重算等）不該壞。"""
    bs = build_balance_sheet("2026-08", bank_lines=[{"id": "a", "name": "銀行",
                                                     "amount": 100}])
    labels = " ".join(x["label"] for x in bs["liabilities"]["current"])
    assert "股東往來" not in labels


# ── 引擎有沒有真的用上（防「純函式寫好了但沒接」）────────────────

def test_statements_engine_splits_before_building():
    from tests.unit._srcscan import repo_src
    src = repo_src("services/finance_statements.py")
    assert "split_bank_lines" in src, "三表引擎沒有把股東往來拆出來"
    # 🔴 只檢查有呼叫是不夠的：把 shareholder_loan 也併進 bank_lines 一樣有呼叫
    #    （破壞測試實測沒咬到）。要釘的是「餵給資產負債表的**只有** cash 那堆」。
    assert 'bank_lines = _split["cash"]' in src, "bank_lines 不是只吃 cash"
    # 四個會把帳戶餘額當現金的地方都要過濾：BS 主路徑、CF 期初、快照路徑、儀表板
    assert src.count("split_bank_lines(") >= 4, (
        "有呼叫點沒過濾 —— 漏一個就是那張報表把股東往來當現金")
    assert "shareholder_loan_lines=" in src and "shareholder_capital_lines=" in src


def test_api_accepts_the_two_new_kinds():
    from routers.api_finance import ACCT_KINDS
    assert {"shareholder_loan", "shareholder_capital"} <= ACCT_KINDS


def test_frontend_mirrors_the_kinds():
    """值域鏡射 —— 後端收但前端選不到等於沒做。"""
    from tests.unit._srcscan import repo_src
    js = repo_src("frontend/tabs/finance/fin-utils.js")
    for k in SHAREHOLDER_KINDS:
        assert k in js, f"前端 ACCT_KIND_OPTIONS 少了 {k}"
    assert "isShareholderAcct" in js


def test_bank_only_dropdowns_exclude_shareholder():
    """🔴 對帳單匯入／分類規則／貸款扣款／對帳工作台只該看到真銀行帳戶
    （股東往來沒有銀行對帳單，也不會拿來扣貸款）。

    2026-08-22 對帳整段搬去 recon.js，四個呼叫點因此散在兩支檔案：
    貸款扣款留在 banking.js，另外三個跟著對帳走。規則本身收進 fin-utils.bankOnly。
    """
    from tests.unit._srcscan import repo_src
    utils = repo_src("frontend/tabs/finance/fin-utils.js")
    assert "export const bankOnly" in utils, "規則正本不見了"

    banking = repo_src("frontend/tabs/finance/subviews/banking.js")
    recon = repo_src("frontend/tabs/finance/subviews/recon.js")
    for js in (banking, recon):
        assert "function _bankOnly()" in js
        assert "return bankOnly(_accounts);" in js, "沒有走共用那條規則"
    # 🔴 逐一釘四個呼叫點，不要只數次數 —— 數次數時拿掉一個仍然過
    #    （定義那行自己也含 "_bankOnly()"，破壞測試實測沒咬到）。
    assert "const actives = _bankOnly();" in banking, "貸款扣款下拉沒換成 _bankOnly"
    for site in ("const actives = _bankOnly();",           # 對帳工作台
                 "const opts = _bankOnly().map(a =>",       # 對帳單匯入
                 "+ _bankOnly()"):                          # 分類規則
        assert site in recon, f"這個下拉沒換成 _bankOnly：{site}"
    for js in (banking, recon):
        assert "_accounts.filter(a => a.active !== false)" not in js, (
            "還有地方在用未過濾的啟用帳戶清單")


def test_cashbook_can_still_charge_to_shareholder_accounts():
    """收支明細那邊**不受限** —— 股東墊付的費用本來就要掛到股東帳戶上。"""
    from tests.unit._srcscan import repo_src
    js = repo_src("frontend/tabs/finance/fin-utils.js")   # 規則與取捨都住這裡
    assert "收支明細那邊的帳戶下拉不受此限" in js, "這個取捨要寫下來，不然下一個人會一起濾掉"
