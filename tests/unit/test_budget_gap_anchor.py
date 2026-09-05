"""預算結算頂列的「距目標」（owner 2026-09-05「我應該要知道已經超支 682」）：實際毛利 − 目標利潤，
負＝超支（owner 手動在自己的費用扣）、正＝餘裕、0＝剛好；附雜支比預估超／剩多少。只把字寫對，不自動調節。"""
from tests.unit._srcscan import js_code_only, repo_src


def test_gap_anchor_is_actual_profit_minus_target():
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert 'id="cd-anchor-gap"' in src
    assert "const gap = d.actualProfit - d.profitTarget;" in src
    assert "const miscDiff = d.miscActual - d.miscEstimated;" in src
    assert "set('cd-anchor-gap'" in src


def test_allocation_alert_flags_budget_above_plan():
    """子表預算合計比規劃（人員預估＋預估雜支）多時要顯示（owner 2026-09-05）；超過執行預算仍是原本的紅色警告。"""
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "const planned = (d.costEstimated || 0) + (d.miscEstimated || 0);" in src
    assert "const overPlan = allocated - planned;" in src
    assert "比規劃" in repo_src("frontend/tabs/crm/crm-projects-cost.js")
