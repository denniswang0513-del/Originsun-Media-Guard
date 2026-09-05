"""預估雜支的來源（owner 2026-09-05「預算也要連雜支一起包」）：
子表有設雜支預算 → 加總；沒設但子表有預算 → Σ子表預算 − Σ工項預估；連子表預算都沒有才退回未稅 × 雜支比。
東仁社宅：子表預算 271,239、工項預估 253,605 → 預估雜支 17,634 ＝ 實際，預估毛利與實際毛利同 20%。"""
from tests.unit._srcscan import js_code_only, repo_src


def test_calc_dashboard_prefers_allocated_budget_minus_cost_lines():
    calc = js_code_only(repo_src("frontend/tabs/crm/crm-projects-calc.js"))
    assert "const fromAlloc = f.misc_budget_total == null && alloc > 0;" in calc
    assert "const miscAuto = f.misc_budget_total == null && !fromAlloc;" in calc
    assert "Math.max(0, alloc - costEst)" in calc
    assert "miscFrom: fromAlloc ? 'alloc' : (miscAuto ? 'auto' : 'groups')" in calc


def test_header_label_says_where_the_estimate_came_from():
    cost = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "d.miscFrom === 'alloc'" in cost and "miscFrom: d.miscFrom" in cost and "miscFrom: _dashBase.miscFrom" in cost
