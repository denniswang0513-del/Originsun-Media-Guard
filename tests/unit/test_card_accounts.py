# -*- coding: utf-8 -*-
"""信用卡：卡片頁籤切明細 ＋ 匯入區分卡別（owner 2026-08-27）。

owner：「我希望切這個按鈕的時候是可以切換成信用卡的明細，信用卡再匯入的時候
也可以區隔哪一個銀行的信用卡」。

設計：一張卡＝一個 bank_accounts 帳戶（acct_kind='card'），刷卡列把它掛在
bank_account_id 當**卡別身分**。🔴 卡片帳戶不是現金也不是資產 —— 卡債由
card_outstanding（期初＋刷卡−還款）出，卡片帳戶若落進 cash 就是同一筆錢
一邊當資產一邊當負債。
"""
from pathlib import Path

from core.card_statement import charges_by_card
from core.finance_logic import CARD_KIND, is_card_kind, split_bank_lines

ROOT = Path(__file__).resolve().parents[2]
NL = chr(10)


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_card_accounts_never_land_in_cash():
    accts = [{"id": "b1", "acct_kind": "bank"}, {"id": "c1", "acct_kind": CARD_KIND},
             {"id": "s1", "acct_kind": "shareholder_loan"}]
    lines = [{"id": "b1", "amount": 100}, {"id": "c1", "amount": -50},
             {"id": "s1", "amount": 30}]
    out = split_bank_lines(accts, lines)
    assert [x["id"] for x in out["cash"]] == ["b1"], "卡片帳戶不可進現金"
    assert [x["id"] for x in out[CARD_KIND]] == ["c1"]
    assert [x["id"] for x in out["shareholder_loan"]] == ["s1"]
    assert is_card_kind(CARD_KIND) and not is_card_kind("bank")


def test_charges_by_card_splits_only_the_charge_side():
    rows = [
        {"entry_date": "2026-07-20", "expense": 300, "status": "card", "bank_account_id": "c1"},
        {"entry_date": "2026-08-02", "expense": 200, "status": "card", "bank_account_id": "c2"},
        {"entry_date": "2026-08-03", "deposit": 50, "status": "card", "bank_account_id": "c1"},
        {"entry_date": "2026-08-04", "expense": 90, "status": "card"},          # 未指定卡別
        {"entry_date": "2026-08-05", "expense": 999, "status": "", "bank_account_id": "b1"},
    ]
    by = charges_by_card(rows)
    assert by["c1"] == 250 and by["c2"] == 200      # 退刷 50 沖掉
    assert by[""] == 90                              # 未指定那堆
    assert "b1" not in by, "非刷卡列不進卡片統計"
    # as_of 是**月份**（與引擎其餘處同一個口徑，不是日期）
    cut = charges_by_card(rows, "2026-07")
    assert cut["c1"] == 300 and "c2" not in cut, "as_of 之後的月份不算"


def test_import_carries_the_card():
    sch = _read("core/schemas.py")
    assert "card_account_id" in sch.split("class CardImportApply")[1][:900]
    api = _read("routers/api_finance_card.py")
    fn = api.split("async def apply_card_statement(")[1].split(NL + "@router")[0]
    assert "bank_account_id=card_id" in fn
    assert "卡別不存在或不是信用卡帳戶" in fn, "卡別必須是這本帳的卡片帳戶"
    js = _read("frontend/tabs/finance/subviews/recon.js")
    assert "_cardPickerHtml" in js and "cardOnly(_accounts)" in js
    # 🔴 卡別要在預覽換掉 modal 之前收起來
    assert "d.card_account_id = document.getElementById('fincard-acct')" in js
    assert "card_account_id: (_cardPreview" in js


def test_card_tab_switches_to_card_detail():
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "data-card=" in js, "卡片頁籤"
    tabs = js.split("function _renderAcctTabs()")[1].split("function _cardChipHtml")[0]
    assert "_bankOnly(_bankAccounts)" in tabs, "銀行頁籤只列真銀行帳戶"
    assert "_filters.status = 'card'" in tabs, "點卡片頁籤＝切換成信用卡明細"
    assert "_filters.status = ''" in tabs, "點回銀行頁籤要清掉卡片視角"
    assert "params.set('status'" in js
    api = _read("routers/crm/finance.py")
    lst = api.split("async def list_cash_entries(")[1].split(NL + "@router")[0]
    assert "status: str = Query" in lst and "CrmCashEntry.status == status" in lst
    fu = _read("frontend/tabs/finance/fin-utils.js")
    assert "isCardAcct" in fu and "!isCardAcct(a.acct_kind)" in fu.split("bankOnly")[1][:400]
