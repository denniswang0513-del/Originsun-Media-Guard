"""預算結算頂列的「距目標」（owner 2026-09-05「我應該要知道已經超支 682」）：實際毛利 − 目標利潤，
負＝超支（owner 手動在自己的費用扣）、正＝餘裕、0＝剛好；附雜支比預估超／剩多少。只把字寫對，不自動調節。"""
from tests.unit._srcscan import js_code_only, repo_src


def test_gap_anchor_is_actual_profit_minus_target():
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert 'id="cd-anchor-gap"' in src
    assert "const gap = d.actualProfit - d.profitTarget;" in src
    assert "const miscDiff = d.miscActual - d.miscEstimated;" in src
    assert "set('cd-anchor-gap'" in src
