"""預算結算的分配提示（owner 2026-09-05）。「距目標」那行做過又拿掉（owner：這一行可以移除了）。"""
from tests.unit._srcscan import js_code_only, repo_src


def test_allocation_alert_flags_budget_above_plan():
    """子表預算合計比規劃（人員預估＋預估雜支）多時要顯示（owner 2026-09-05）；超過執行預算仍是原本的紅色警告。"""
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "const planned = (d.costEstimated || 0) + (d.miscEstimated || 0);" in src
    assert "const overPlan = allocated - planned;" in src
    assert "比規劃" in repo_src("frontend/tabs/crm/crm-projects-cost.js")


def test_dashboard_columns_are_staff_misc_cost_remaining_profit():
    """owner 2026-09-05：對照表欄位＝人員｜雜支｜成本（人員＋雜支）｜剩餘預算（預算−成本）｜毛利（未稅−預算）。"""
    src = repo_src("frontend/tabs/crm/crm-projects-cost.js")
    for i in ("cd-staff-est", "cd-cost-est", "cd-misc-est", "cd-rem-est", "cd-pf-est", "cd-staff-diff"):
        assert f'id="{i}"' in src, i
    calc = js_code_only(repo_src("frontend/tabs/crm/crm-projects-calc.js"))
    assert "const budgetProfit = p.exTax - execBudget;" in calc
    js = js_code_only(src)
    assert "set('cd-cost-est', '$' + fmtNum(d.totalEstimated));" in js and "fmtNum(d.budgetProfit)" in js
    assert "repeat(5, 1fr)" in repo_src("frontend/tabs/crm/crm.css")


def test_sub_table_budget_includes_misc_and_defaults_to_five_percent():
    """子表預算含委外與雜支；雜支預算沒設＝預算 × 5%（後端給 misc_budget_default／effective，卡片顯示預計雜支）。"""
    api = repo_src("routers/crm/costs.py")
    assert "total_budget = g.budget_amount or 0" in api and '"misc_budget_effective"' in api and '"misc_budget_default"' in api
    assert "+ sa_func.coalesce(CrmProjectCostGroup.misc_budget_amount, 0)" not in api, "雜支不再外加在子表預算上"
    fe = repo_src("frontend/tabs/crm/crm-projects-cost-groups.js")
    assert "const total = g.budget_amount || 0;" in fe and "預計雜支" in fe and "mEl.dataset.auto" in fe


def test_sub_table_misc_section_uses_the_default_when_unset():
    """3min 協力影片：雜支預算沒設時小計預估曾是「—」（當 0 算），已用 3,745 就整筆標超支；改用預設 5%（2,514）→ 超 1,231。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "const miscBudget = groupMisc != null ? groupMisc : ((grp && grp.misc_budget_effective) || 0);" in js
    assert "const totalEst = grandEst + miscBudget;" in js
