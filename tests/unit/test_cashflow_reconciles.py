# -*- coding: utf-8 -*-
"""現金流量表要**平**：期初 ＋ 淨流 ＝ 期末（owner 2026-08-29「我現在希望
我這裡是正確的」；修前私帳差 −3,134,476）。

破掉的原因有兩個，各自都足以讓表對不起來：
 ① `treatment='transfer'` 的列一律不列入活動 —— 但只有**配得成對**的轉存
    才是內部移動（兩腳都在追蹤中的帳戶、互相抵銷）。配不到對手的那些，
    錢是真的離開現金池了（買股票、繳卡費、代墊給家人）。
 ② 「什麼算現金」兩側判定不一致：餘額那側（split_bank_lines）把信用卡另分
    一桶，流量那側（cash_account_ids）沒排除 —— 刷卡列於是被當成現金流動。
"""
from core.finance_logic import (build_cashflow, cash_account_ids,
                                cashflow_lines, paired_transfer_ids)

CM = {("cash", "轉存"): {"treatment": "transfer"},
      ("cash", "買股票"): {"treatment": "transfer", "account_id": "F"},
      ("cash", "服務收入"): {"treatment": "direct_income"}}
ACCTS = {"F": {"name": "其他金融資產", "acct_type": "asset",
               "cf_activity": "investing"}}
BANKS = [{"id": "A", "name": "A 銀行", "acct_kind": "bank", "opening_balance": 100000},
         {"id": "B", "name": "B 銀行", "acct_kind": "bank", "opening_balance": 0},
         {"id": "C", "name": "信用卡", "acct_kind": "card", "opening_balance": 0}]


def _cf(ents, months=("2026-01",)):
    from core.finance_logic import bank_balances_asof, split_bank_lines
    op = split_bank_lines(BANKS, bank_balances_asof(BANKS, ents, "2025-12"))["cash"]
    cl = split_bank_lines(BANKS, bank_balances_asof(BANKS, ents, "2026-01"))["cash"]
    tot = lambda L: {"total": sum(x["amount"] for x in L), "by_account": L}
    return build_cashflow(list(months), opening=tot(op), closing=tot(cl),
                          cash_entries=ents, cat_map=CM, accounts=ACCTS,
                          bank_accounts=BANKS)


def test_paired_transfer_is_internal_unpaired_is_a_real_outflow():
    """A→B 成對＝內部移動（不列活動）；A→證券戶配不到對手＝投資活動流出。"""
    ents = [
        {"id": "o", "entry_date": "2026-01-05", "bank_account_id": "A",
         "expense": 20000, "category": "轉存"},
        {"id": "i", "entry_date": "2026-01-05", "bank_account_id": "B",
         "deposit": 20000, "category": "轉存"},
        {"id": "s", "entry_date": "2026-01-10", "bank_account_id": "A",
         "expense": 30000, "category": "買股票"},
    ]
    assert paired_transfer_ids(ents, CM, ACCTS) == {"o", "i"}
    r = _cf(ents)
    assert r["investing"] == -30000, "買股票＝投資活動流出"
    assert r["operating"] == 0, "成對的轉存不該落到任何活動"
    assert r["check"]["diff"] == 0, "期初＋淨流 ≠ 期末"


def test_card_rows_are_not_cash_on_either_side():
    """🔴 刷卡當下沒有動到銀行。餘額那側早就把卡片另分一桶了，流量這側也要 ——
    不一致的話，差額剛好等於刷卡流量（生產實例 1,508,164）。"""
    assert cash_account_ids(BANKS) == {"A", "B"}, "卡片不算現金"
    ents = [
        {"id": "swipe", "entry_date": "2026-01-08", "bank_account_id": "C",
         "expense": 5000, "category": "轉存"},          # 刷卡：不動銀行
        {"id": "pay", "entry_date": "2026-01-20", "bank_account_id": "A",
         "expense": 5000, "category": "轉存"},          # 繳卡費：錢真的出去
    ]
    rows, stats = cashflow_lines(ents, ["2026-01"], cat_map=CM, accounts=ACCTS,
                                 bank_accounts=BANKS)
    assert stats["noncash"] == 1
    assert [r["entry"]["id"] for r in rows] == ["pay"]
    assert _cf(ents)["check"]["diff"] == 0


def test_reconciles_with_income_and_everything_mixed():
    ents = [
        {"id": "rev", "entry_date": "2026-01-02", "bank_account_id": "A",
         "deposit": 50000, "category": "服務收入"},
        {"id": "o", "entry_date": "2026-01-05", "bank_account_id": "A",
         "expense": 20000, "bank_fee": 15, "category": "轉存"},
        {"id": "i", "entry_date": "2026-01-05", "bank_account_id": "B",
         "deposit": 20000, "category": "轉存"},
        {"id": "s", "entry_date": "2026-01-10", "bank_account_id": "A",
         "expense": 30000, "category": "買股票"},
        {"id": "swipe", "entry_date": "2026-01-08", "bank_account_id": "C",
         "expense": 5000, "category": "轉存"},
    ]
    r = _cf(ents)
    assert r["check"]["diff"] == 0
    assert r["operating"] == 50000 - 15, "收入進營運；成對轉存的手續費也是真流出"
    assert r["investing"] == -30000
