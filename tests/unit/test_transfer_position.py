# -*- coding: utf-8 -*-
"""transfer 的兩個意思要分開（owner 2026-08-29「我只有富邦的幾個帳戶互轉有記帳，
你表列的這些都不是互轉」）。

`treatment='transfer'` 同時代表：
  ① 這筆不進損益（私人開銷掛業主往來 —— 匯入時刻意這樣對映）
  ② 這是帳戶間轉存的一腳（配對面板的候選）
配對面板拿①當②用，於是 3,415 筆個人消費進了配對池，在 3 天窗口＋50 元容差下
配出 48 組假的（$136 配 $90 說手續費 46）。分辨的依據是**錢去了哪裡**。
"""
from core.finance_logic import (ACCOUNT_MOVE_POSITIONS, equity_transfer_position,
                                is_account_move, transfer_position)


def test_position_buckets():
    """equity 由 acct_type 判、資產桶按科目名指認、其餘＝銀行互轉（無位置）。"""
    assert transfer_position({"acct_type": "equity", "name": "業主往來"}) == "equity"
    assert transfer_position({"acct_type": "asset", "name": "員工往來-預支"}) == "advance"
    assert transfer_position({"acct_type": "asset", "name": "家用往來"}) == "household"
    assert transfer_position({"acct_type": "asset", "name": "器材設備"}) == "capital"
    assert transfer_position({"acct_type": "asset", "name": "其他金融資產"}) == "financial"
    assert transfer_position({"acct_type": "asset", "name": "銀行存款"}) == "bank"
    assert transfer_position(None) == "bank"      # 認不得的落到現金內移動


def test_only_real_account_moves_are_pairing_candidates():
    """🔴 配對池只收「搬到另一個帳戶／金融資產」的那兩桶。

    業主往來（個人消費）與家用往來若進了池，隨機的消費金額就會互相配對成功 ——
    那正是 owner 看到的 48 組假轉存。"""
    assert ACCOUNT_MOVE_POSITIONS == {"bank", "financial"}
    cat_map = {
        ("cash", "轉匯與定存"): {"treatment": "transfer", "account_id": "fin"},
        ("cash", "信用卡"): {"treatment": "transfer", "account_id": "bank"},
        ("cash", "家用_變動支出"): {"treatment": "transfer", "account_id": "house"},
        ("cash", "個人_生活"): {"treatment": "transfer", "account_id": "eq"},
        ("cash", "公司_專案"): {"treatment": "direct_expense", "account_id": "bank"},
    }
    accounts = {"fin": {"acct_type": "asset", "name": "其他金融資產"},
                "bank": {"acct_type": "asset", "name": "銀行存款"},
                "house": {"acct_type": "asset", "name": "家用往來"},
                "eq": {"acct_type": "equity", "name": "業主往來"}}
    ok = {"轉匯與定存", "信用卡"}
    for cat in ("轉匯與定存", "信用卡", "家用_變動支出", "個人_生活", "公司_專案", ""):
        got = is_account_move({"category": cat}, cat_map, accounts)
        assert got is (cat in ok), cat


def test_financial_position_is_an_asset_not_a_draw():
    """換匯／定存／買股票＝錢換了個地方放，不是業主提取。

    2026-08-29 實測私帳：搬之前『轉匯與定存』的 7,045,554 全落在業主往來
    （帳上等於說 owner 提走了七百萬）。搬完 BS 兩側同幅上移、勾稽差額不變。"""
    accounts = {"fin": {"acct_type": "asset", "name": "其他金融資產"},
                "eq": {"acct_type": "equity", "name": "業主往來"}}
    cat_map = {("cash", "轉匯與定存"): {"treatment": "transfer", "account_id": "fin"},
               ("cash", "個人_生活"): {"treatment": "transfer", "account_id": "eq"}}
    entries = [
        {"category": "轉匯與定存", "entry_date": "2026-04-10", "expense": 500000},
        {"category": "轉匯與定存", "entry_date": "2026-04-12", "deposit": 100000},
        {"category": "個人_生活", "entry_date": "2026-04-15", "expense": 3000},
    ]
    pos = equity_transfer_position(entries, cat_map, accounts, "2026-08")
    assert pos["financial_net"] == 400000, "支出−存入＝還放在外面的錢"
    assert pos["owner_net"] == -3000, "只有真的花掉的才是提取"


def test_balance_sheet_shows_the_financial_asset_line():
    """有值才畫（零列＝雜訊，同家用代墊那條的慣例）。"""
    from tests.unit._srcscan import finance_logic_src
    fn = finance_logic_src("def build_balance_sheet(")
    assert "if financial_net:" in fn and '"label": "其他金融資產"' in fn
