"""預算結算的分配提示（owner 2026-09-05）。「距目標」那行做過又拿掉（owner：這一行可以移除了）。"""
from tests.unit._srcscan import js_code_only, repo_src


def test_allocation_alert_flags_budget_above_plan():
    """子表預算合計比規劃（人員預估＋預估雜支）多時要顯示（owner 2026-09-05）；超過執行預算仍是原本的紅色警告。"""
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "const planned = (d.costEstimated || 0) + (d.miscEstimated || 0);" in src
    assert "const overPlan = allocated - planned;" in src
    assert "比規劃" in repo_src("frontend/tabs/crm/crm-projects-cost.js")
