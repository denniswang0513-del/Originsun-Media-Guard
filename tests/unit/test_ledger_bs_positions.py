# -*- coding: utf-8 -*-
"""transfer 流量的「位置」＋卡債負債列（owner 2026-08-25「希望我的資產負債表
與現金流量表都是正確的」）。

背景：私帳的設計把個人/家用支出標 treatment='transfer'（不進損益，對映
「業主往來」）。引擎原本只做「排除」這一半 —— 位置全沒記，BS 差 -7,188,302。
這裡釘住補上的另一半：業主往來自動推導、卡債負債、代墊資產、以及
「合成小帳本必須平」的總不變量。
"""
import asyncio

from core.finance_logic import (build_balance_sheet, card_outstanding,
                                equity_transfer_position, statement_warnings)

ACCTS = {
    "eq": {"name": "業主往來", "acct_type": "equity"},
    "adv": {"name": "員工往來-預支", "acct_type": "asset"},
    "gear": {"name": "器材設備", "acct_type": "asset"},
    "bank": {"name": "銀行存款", "acct_type": "asset"},
}
CMAP = {
    ("cash", "個人_生活"): {"treatment": "transfer", "account_id": "eq"},
    ("cash", "個人_主動收入"): {"treatment": "transfer", "account_id": "eq"},
    ("cash", "公司_代墊"): {"treatment": "transfer", "account_id": "adv"},
    ("cash", "公司_器材"): {"treatment": "transfer", "account_id": "gear"},
    ("cash", "信用卡"): {"treatment": "transfer", "account_id": "bank"},
    ("cash", "公司_專案"): {"treatment": "direct_income", "account_id": "rev"},
}


def _e(month, *, dep=0, exp=0, cat=None, status=""):
    return {"entry_date": f"{month}-15", "deposit": dep, "expense": exp,
            "category": cat, "status": status, "bank_account_id": None}


def test_owner_position_nets_injections_minus_draws():
    rows = [
        _e("2024-01", exp=1000, cat="個人_生活"),                 # 提取（銀行）
        _e("2024-02", exp=500, cat="個人_生活", status="card"),   # 提取（刷卡也算）
        _e("2024-03", dep=300, cat="個人_主動收入"),              # 注入
        _e("2024-04", exp=999, cat="公司_專案"),                  # 非 transfer → 不進位置
        _e("2099-01", exp=777, cat="個人_生活"),                  # 超過 as_of → 不算
    ]
    pos = equity_transfer_position(rows, CMAP, ACCTS, "2026-01")
    assert pos["owner_net"] == 300 - 1000 - 500
    assert pos["advance_net"] == 0


def test_advance_position_is_out_minus_back():
    rows = [_e("2024-01", exp=4000, cat="公司_代墊"),
            _e("2024-02", dep=1500, cat="公司_代墊")]
    pos = equity_transfer_position(rows, CMAP, ACCTS, "2026-01")
    assert pos["advance_net"] == 2500
    assert pos["owner_net"] == 0


def test_gear_and_bank_transfers_create_no_position():
    """器材走清冊（再記流量＝重複）、銀行存款走卡債邏輯 —— 都不進位置。"""
    rows = [_e("2024-01", exp=9000, cat="公司_器材"),
            _e("2024-02", exp=5000, cat="信用卡")]
    pos = equity_transfer_position(rows, CMAP, ACCTS, "2026-01")
    assert pos == {"owner_net": 0, "advance_net": 0}


def test_card_outstanding_formula_and_guard():
    cfg = {"opening": 100, "repay_categories": ["信用卡"]}
    rows = [_e("2024-01", exp=800, status="card"),
            _e("2024-02", exp=700, cat="信用卡")]          # 還款
    assert card_outstanding(rows, cfg, "2026-01") == 100 + 800 - 700
    # 🔴 防呆：沒有卡片帳（無期初、無刷卡列）→ 一律 0 —— 否則一筆恰好叫
    # 「信用卡」的雜列會在母公司 BS 憑空長出負數負債
    assert card_outstanding([_e("2024-02", exp=700, cat="信用卡")],
                            {"opening": 0, "repay_categories": ["信用卡"]},
                            "2026-01") == 0


def test_bs_owner_line_adds_flow_and_card_line_only_when_nonzero():
    bs = build_balance_sheet("2026-01", owner_flow_net=-1200, card_outstanding=0)
    owner = next(x for x in bs["equity"]["lines"] if x["key"] == "owner")
    assert owner["amount"] == -1200
    assert not any(x["key"] == "card" for x in bs["liabilities"]["current"])
    bs2 = build_balance_sheet("2026-01", card_outstanding=2428)
    card = next(x for x in bs2["liabilities"]["current"] if x["key"] == "card")
    assert card["amount"] == 2428


def test_card_rows_are_not_counted_as_unassigned():
    """刷卡列刻意不掛帳戶 —— 算進「未掛帳戶」會讓私帳永遠掛著假警語。"""
    rows = [_e("2026-01", exp=100, cat="個人_生活", status="card"),
            _e("2026-01", exp=100, cat="個人_生活")]        # 這筆才是真的忘了掛
    w = statement_warnings(rows, [], CMAP, ["2026-01"])
    assert w["unassigned"] == 1


def test_synthetic_book_balances_to_zero(monkeypatch):
    """🔴 皇冠不變量：一本乾淨的合成小帳本，BS 的 diff 必須是 0。

    劇本：期初現金 10,000（有期初調整列）→ 收入 5,000 入帳戶 → 提取 2,000
    → 刷卡消費 800（生活）→ 還卡款 600 → 代墊出 1,000。
    現金 = 10,000+5,000−2,000−600−1,000 = 11,400；卡債 = 800−600 = 200；
    代墊資產 1,000；權益 = 期初 10,000 + 損益 5,000 − 提取(2,000+800) = 12,200。
    資產 12,400 − 負債 200 − 權益 12,200 = 0。
    """
    import config

    from services.finance_statements import compute_live
    monkeypatch.setattr(config, "load_settings",
                        lambda: {"card_ledger": {"mine": {"opening": 0,
                                                          "repay_categories": ["信用卡"]}}})
    entries = [
        {**_e("2026-01", dep=5000, cat="公司_專案"), "bank_account_id": "b1"},
        {**_e("2026-02", exp=2000, cat="個人_生活"), "bank_account_id": "b1"},
        _e("2026-02", exp=800, cat="個人_生活", status="card"),
        {**_e("2026-03", exp=600, cat="信用卡"), "bank_account_id": "b1"},
        {**_e("2026-03", exp=1000, cat="公司_代墊"), "bank_account_id": "b1"},
    ]
    accounts = dict(ACCTS, rev={"name": "營業收入", "acct_type": "income",
                                "pnl_group": "營業收入", "cf_activity": "operating"})
    inputs = {
        "entity": "mine", "invoices": [], "payments": [],
        "cash_entries": entries, "equipment": [],
        "adjustments": [{"adj_date": "2026-01-01", "adj_type": "opening",
                         "amount": 10000}],
        "bank_accounts": [{"id": "b1", "name": "帳戶", "opening_balance": 10000,
                           "opening_date": "2026-01-01", "acct_kind": "bank"}],
        "loans": [], "loan_payments": [], "accounts": accounts, "cat_map": CMAP,
    }
    r = asyncio.run(compute_live(None, ["2026-01", "2026-02", "2026-03"],
                                 inputs=inputs, adv={"balance_total": 0, "expenses": []},
                                 entity="mine"))
    bs = r["bs"]
    assert bs["assets"]["total"] == 12400
    assert bs["liabilities"]["total"] == 200
    assert bs["equity"]["total"] == 12200
    assert bs["check"]["diff"] == 0
