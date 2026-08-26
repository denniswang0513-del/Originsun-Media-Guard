# -*- coding: utf-8 -*-
"""收入類別底下的支出列＝成本，不是退款（owner 2026-08-27「數字結果得出來的應該是要一樣的」）。

私帳把專案收款與專案支出放在同一個類別鍵 `公司_專案`，方向才是判準。
對映只認類別文字時，支出列會去沖營收 —— 營收少一截、成本也憑空消失。
"""
from core.finance_logic import (build_pnl, iter_expense_items,
                                paired_expense_category)

CAT_MAP = {
    ("cash", "公司_專案"): {"treatment": "direct_income", "account_id": "4100"},
    ("cash", "公司_專案支出"): {"treatment": "direct_expense", "account_id": "5200"},
    ("cash", "利息收入"): {"treatment": "direct_income", "account_id": "4200"},
}
ACCOUNTS = {
    "4100": {"name": "營業收入", "pnl_group": "營業收入", "acct_type": "income"},
    "5200": {"name": "專案雜支", "pnl_group": "營業成本-費", "acct_type": "expense"},
    "4200": {"name": "其他收入", "pnl_group": "業外收入", "acct_type": "income"},
}
MONTHS = ["2026-01"]


def _entry(cat, deposit=0, expense=0, eid="x"):
    return {"id": eid, "entry_date": "2026-01-15", "category": cat,
            "deposit": deposit or None, "expense": expense or None, "summary": cat}


def test_paired_expense_category_needs_the_mapping_to_exist():
    assert paired_expense_category("公司_專案", CAT_MAP) == "公司_專案支出"
    assert paired_expense_category("利息收入", CAT_MAP) == ""      # 沒有支出版 → 維持沖回
    assert paired_expense_category(None, CAT_MAP) == ""


def test_project_expense_becomes_cost_not_negative_revenue():
    entries = [_entry("公司_專案", deposit=1_000_000, eid="in"),
               _entry("公司_專案", expense=300_000, eid="out")]
    pnl = build_pnl(MONTHS, cash_entries=entries, cat_map=CAT_MAP, accounts=ACCOUNTS)
    assert pnl["revenue"]["total"] == 1_000_000, "營收不該被支出沖掉"
    assert pnl["cost"]["total"] == 300_000, "支出要落在成本"
    assert pnl["net"]["amount"] == 700_000


def test_income_category_without_paired_mapping_still_nets_back():
    """沒有支出版對映的收入科目（例如利息被退回）維持原本行為 —— 沖回同科目。"""
    entries = [_entry("利息收入", deposit=5_000, eid="in"),
               _entry("利息收入", expense=2_000, eid="out")]
    pnl = build_pnl(MONTHS, cash_entries=entries, cat_map=CAT_MAP, accounts=ACCOUNTS)
    assert pnl["non_operating"]["total"] == 3_000
    assert pnl["cost"]["total"] == 0


def test_expense_side_appears_once_in_the_single_iteration_source():
    """iter_expense_items 是費用側唯一來源 —— drilldown 與損益表靠它對齊。"""
    entries = [_entry("公司_專案", expense=300_000, eid="out")]
    rows = list(iter_expense_items([], entries, CAT_MAP, ACCOUNTS, set(MONTHS)))
    assert len(rows) == 1
    _src, _row, group, label, amount = rows[0]
    assert (group, label, amount) == ("營業成本-費", "專案雜支", 300_000)


def test_restate_revenue_accrual_moves_the_whole_chain():
    """換成權責營收時，毛利/營益/稅前/稅後/比率/月均要一起走 —— 只換一列會前後矛盾。"""
    from core.finance_logic import restate_revenue_accrual
    entries = [_entry("公司_專案", deposit=1_000_000, eid="in"),
               _entry("公司_專案", expense=300_000, eid="out")]
    pnl = build_pnl(MONTHS, cash_entries=entries, cat_map=CAT_MAP, accounts=ACCOUNTS)
    out = restate_revenue_accrual(pnl, 1_500_000, cash_revenue=1_000_000, n_months=12)
    assert out["revenue"]["total"] == 1_500_000
    assert out["revenue"]["by_collection"]["cash"] == 1_000_000, "現金口徑要留著可互推"
    assert out["gross"]["amount"] == 1_200_000           # 1.5M − 300k 成本
    assert out["net"]["amount"] == 1_200_000
    assert out["monthly_avg"]["revenue"] == 125_000      # 1.5M / 12
    assert out["cost"]["total"] == pnl["cost"]["total"], "成本不該被動到"
    assert out["gross"]["rate"] == 80.0
