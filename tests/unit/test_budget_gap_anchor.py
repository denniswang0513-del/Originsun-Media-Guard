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


def test_actual_profit_absorbs_the_remaining_budget():
    """owner 2026-09-05「剩餘預算要加到實際毛利」：實際毛利＝未稅−實際成本
    （＝目標毛利＋實際剩餘預算），跟錨點列／財務摘要同一個數；預估格仍是目標毛利。

    東仁社宅金安獎：未稅 339,048、執行預算 271,239、實際成本 270,557
    → 目標毛利 67,809、實際毛利 68,491（多 682）。之前兩格都畫 budgetProfit，
    實際那格看起來永遠剛好等於目標，差額那格恆為 0。
    """
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "set('cd-pf-act', '$' + fmtNum(d.actualProfit)" in js, "實際毛利要用 actualProfit"
    assert "set('cd-pf-est', '$' + fmtNum(d.budgetProfit)" in js, "預估毛利仍是目標毛利"
    assert "drift('cd-pf-diff', d.actualProfit - d.budgetProfit);" in js
    assert "set('cd-pf-diff', '—'" not in js, "差額不再恆為 0，別再畫破折號"
    # 錨點列與對照表實際格同源 —— 兩處都是 actualProfit
    assert js.count("fmtNum(d.actualProfit)") == 2


def test_sub_table_budget_includes_misc_and_defaults_to_five_percent():
    """子表預算含委外與雜支；雜支預算沒設＝預算 × 5%（後端給 misc_budget_default／effective，卡片顯示預計雜支）。"""
    api = repo_src("routers/crm/costs.py")
    assert "total_budget = g.budget_amount or 0" in api and '"misc_budget_effective"' in api and '"misc_budget_default"' in api
    assert "+ sa_func.coalesce(CrmProjectCostGroup.misc_budget_amount, 0)" not in api, "雜支不再外加在子表預算上"
    fe = repo_src("frontend/tabs/crm/crm-projects-cost-groups.js")
    assert "const total = g.budget_amount || 0;" in fe and "預計雜支" in fe
    assert "* 0.05" not in fe, "子表雜支預設不在前端算（留空交給後端的專案雜支比）"


def test_sub_table_misc_section_uses_the_default_when_unset():
    """3min 協力影片：雜支預算沒設時小計預估曾是「—」（當 0 算），已用 3,745 就整筆標超支；改用預設 5%（2,514）→ 超 1,231。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "const miscBudget = groupMisc != null ? groupMisc : ((grp && grp.misc_budget_effective) || 0);" in js
    assert "const totalEst = grandEst + miscBudget;" in js


def test_project_misc_estimate_is_sum_of_sub_table_misc():
    """owner 2026-09-05「統一成同一個口徑」：整案預估雜支＝各子表預計雜支加總（設了用設的、沒設用子表預算 × 雜支比）；
    連子表預算都沒有才回 None（前端退回未稅 × 雜支比）。financial-summary 與 expenses 清單兩支都走同一條純函式。"""
    from core.crm_logic import misc_budget_total_of, group_misc_default
    assert group_misc_default(220_960, 5) == 11_048 and group_misc_default(50_280, 5) == 2_514 and group_misc_default(None, 5) == 0
    assert misc_budget_total_of([(220_960, None), (50_280, None)], 5) == 13_562
    assert misc_budget_total_of([(220_960, 9_000), (50_280, None)], 5) == 11_514
    assert misc_budget_total_of([(None, 0), (None, None)], 5) == 0      # 明填 0 算「有設」
    assert misc_budget_total_of([(None, None), (0, None)], 5) is None   # 什麼都沒填 → 退回未稅比例
    assert misc_budget_total_of([], None) is None
    api = repo_src("routers/crm/costs.py")
    assert api.count("misc_budget_total_of(") == 2, "financial-summary 與 list_project_expenses 都要走同一條"
    assert "misc_default = group_misc_default(total_budget, misc_pct)" in api
    assert "_cost_group_to_dict(g, summary)" not in api, "子表 dict 要帶專案雜支比，不能用預設 5"
    js = repo_src("frontend/tabs/crm/crm-projects-cost.js")
    assert "'子表加總'" in js and "'子表設定'" not in js
