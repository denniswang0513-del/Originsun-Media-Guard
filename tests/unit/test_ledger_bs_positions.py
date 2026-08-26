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
    "hh": {"name": "家用往來", "acct_type": "asset"},
    "gear": {"name": "器材設備", "acct_type": "asset"},
    "bank": {"name": "銀行存款", "acct_type": "asset"},
}
CMAP = {
    ("cash", "個人_生活"): {"treatment": "transfer", "account_id": "eq"},
    ("cash", "個人_主動收入"): {"treatment": "transfer", "account_id": "eq"},
    ("cash", "公司_代墊"): {"treatment": "transfer", "account_id": "adv"},
    ("cash", "家用"): {"treatment": "transfer", "account_id": "hh"},
    ("cash", "家用_變動支出"): {"treatment": "transfer", "account_id": "hh"},
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


def test_household_position_is_out_minus_back():
    """家用代墊（owner 2026-08-26）：支出＝墊出、還款＝沖銷 —— 是資產不是
    業主提取（刷卡或銀行付都算，owner 拍板「代墊不分支付方式」）。"""
    rows = [_e("2024-01", exp=3000, cat="家用_變動支出", status="card"),
            _e("2024-02", exp=500, cat="家用_變動支出"),
            _e("2024-03", dep=1200, cat="家用")]
    pos = equity_transfer_position(rows, CMAP, ACCTS, "2026-01")
    assert pos["household_net"] == 3000 + 500 - 1200
    assert pos["owner_net"] == 0


def test_gear_and_bank_transfers_create_no_position():
    """器材走清冊（再記流量＝重複）、銀行存款走卡債邏輯 —— 都不進位置。
    器材流出只以 cap_flow 順手量出（資本化差額警語用，不是 BS 位置）。"""
    rows = [_e("2024-01", exp=9000, cat="公司_器材"),
            _e("2024-02", exp=5000, cat="信用卡")]
    pos = equity_transfer_position(rows, CMAP, ACCTS, "2026-01")
    assert pos == {"owner_net": 0, "advance_net": 0, "household_net": 0,
                   "cap_flow": 9000}
    # cap_floor＝期初累計月（> floor 才算）：期初裡的購置不再重複計入
    assert equity_transfer_position(rows, CMAP, ACCTS, "2026-01",
                                    cap_floor="2024-01")["cap_flow"] == 0


def test_mine_receivable_and_accrual_from_projects():
    """「兩張表對不起來」根因修正（owner 2026-08-26）：owner 年度表＝權責
    （結案日認列，FY2026 分毫實證 8,103,670）、系統私帳損益＝現金。
    BS 應收正本＝執行專案（發票 AR 對 mine 恆 0）：結案 ≤ as_of 且應收 > 0；
    溢收不抵別案；未結案/期後結案不進本期。accrual_revenue＝橋接警語用。"""
    from core.finance_logic import mine_project_positions
    projs = [
        {"id": "a", "name": "A", "completion_date": "2026-05-01",
         "contract_amount": 1000, "amount_receivable": 400},
        {"id": "b", "name": "B", "completion_date": "2026-08-01",     # as_of 之後結案
         "contract_amount": 2000, "amount_receivable": 2000},
        {"id": "c", "name": "C", "completion_date": "2026-04-01",     # 溢收
         "contract_amount": 500, "amount_receivable": -50},
        {"id": "d", "name": "D", "completion_date": None,             # 未結案
         "contract_amount": 700, "amount_receivable": 700},
    ]
    mp = mine_project_positions(projs, ["2026-04", "2026-05", "2026-06"], "2026-06")
    assert mp["accrual_revenue"] == 1500          # a + c（結案月在期間內）
    assert mp["receivable"] == 400                # b 期後、c 溢收、d 未結案都不進
    assert mp["receivable_rows"][0]["id"] == "a"


def test_mine_bs_receivable_uses_project_ledger():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "services/finance_statements.py"
           ).read_text(encoding="utf-8")
    assert '_mp["receivable"] if _mp is not None' in src, "BS 應收線要走專案口徑"
    assert "權責" in src, "橋接警語（權責 vs 現金）要在頁面上講出來"
    drill = src.split('elif kind == "receivable":')[1][:900]
    assert "mine_project_positions" in drill, "下鑽也要換資料源（發票下鑽對 mine 恆空）"


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
    → 刷卡消費 800（生活）＋ 刷卡家用 400 → 還卡款 600 → 代墊出 1,000
    → 家人還款 300 入帳戶。
    現金 = 10,000+5,000−2,000−600−1,000+300 = 11,700；卡債 = 1,200−600 = 600；
    代墊資產 1,000；家用代墊 = 400−300 = 100；
    權益 = 期初 10,000 + 損益 5,000 − 提取(2,000+800) = 12,200（家用不是提取）。
    資產 12,800 − 負債 600 − 權益 12,200 = 0。
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
        _e("2026-02", exp=400, cat="家用_變動支出", status="card"),
        {**_e("2026-03", exp=600, cat="信用卡"), "bank_account_id": "b1"},
        {**_e("2026-03", exp=1000, cat="公司_代墊"), "bank_account_id": "b1"},
        {**_e("2026-03", dep=300, cat="家用"), "bank_account_id": "b1"},
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
    assert bs["assets"]["total"] == 12800
    assert bs["liabilities"]["total"] == 600
    assert bs["equity"]["total"] == 12200
    assert bs["check"]["diff"] == 0
    hh = next(x for x in bs["assets"]["current"] if x["key"] == "household")
    assert hh["amount"] == 100


def test_card_rows_never_batch_assigned_to_a_bank_account():
    """🔴 銀行頁的「整批掛到預設帳戶」不可撈到刷卡列 —— 掛了＝刷卡金額直接
    打進銀行餘額、與月底還款重複計（私帳 2,793 筆，一按就中）。未掛計數同口徑。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "routers/api_finance.py").read_text(encoding="utf-8")
    cnt = src.split("async def _unassigned_count")[1].split("\nasync def ")[0]
    assert 'status.is_distinct_from("card")' in cnt
    prefix = src.split("cond = [CrmCashEntry.status.is_distinct_from(\"card\")]")
    assert len(prefix) == 2, "整批掛帳的 cond 必須以排除刷卡列開頭（不分 only_unassigned）"
