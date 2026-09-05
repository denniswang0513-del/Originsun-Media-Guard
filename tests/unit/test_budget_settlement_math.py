# -*- coding: utf-8 -*-
"""預算結算（CRM 專案管理分頁）的數學不變式（2026-09-05 稽核）。

生產 25 個有成本資料的案逐條驗過，這裡把當時查的那幾條釘住，
不要靠下次再手動跑一遍稽核腳本才發現漂掉。
"""
from tests.unit._srcscan import js_code_only, repo_src


def test_dashboard_chain_is_closed():
    """對照表五欄的鏈：成本＝人員＋雜支、剩餘＝執行預算−成本、毛利兩格不同源。

    推得出來的恆等式（生產 25 案全部成立）：
      未稅 ＝ 成本 ＋ 剩餘預算 ＋ 毛利（預估、實際兩列各自成立）
      剩餘預算差額 ＝ −成本差額
      毛利差額 ＝ 實際剩餘預算
    """
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-calc.js"))
    assert "const totalEstimated = p.costEstimated + p.miscEstimated;" in js
    assert "const totalActual = p.costActual + p.miscActual;" in js
    assert "remaining: execBudget - totalEstimated," in js
    assert "remainingActual: execBudget - totalActual," in js
    # 預估格＝目標毛利（未稅−執行預算）、實際格＝未稅−實際成本
    assert "const budgetProfit = p.exTax - execBudget;" in js
    assert "const actualProfit = p.exTax - totalActual;" in js


def test_estimated_misc_is_always_the_sub_table_sum():
    """🔴 預估雜支**永遠**是各子表預計雜支加總（owner 2026-09-05 拍板 A 案）。

    一張子表都沒設預算時它就是 0；未稅×雜支比只當**建議值**顯示，不進數字。
    原本那條退路讓對照表寫 6,666 而子表小計是 0 —— 兩個基準本來就不同
    （子表預設＝**子表預算**×%、整案退路＝**未稅**×%），生產 3 案上下差
    6,666／1,904／19,047。
    """
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-calc.js"))
    assert "miscEstimated: miscAuto ? 0 : f.misc_budget_total," in js
    assert "miscSuggested: f.misc_budget || 0," in js, "建議值要另外帶，不能混進 miscEstimated"
    cost = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "d.miscSuggested" in cost, "沒設預算時要把建議值講出來，不然那格只是個 0"
    # 建議值不准回頭參與任何加總
    assert "miscEstimated: miscAuto ? (f.misc_budget" not in js


def test_the_unallocated_slice_is_shown():
    """🔴 執行預算沒分配到任何子表的那塊要講出來（owner 2026-09-05）。

    沒有它，「子表卡的剩餘加總」永遠對不上上面那格「剩餘預算」，而畫面上
    沒有任何地方解釋差在哪 —— 東仁社宅：2min 剩 23,381 ＋ 3min 剩 0 ＝ 23,381，
    剩餘預算(實際) 卻是 24,063，差的 682 ＝ 執行預算 271,239 − Σ子表預算 270,557。
    """
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-cost.js"))
    assert "const unalloc = execBudget - allocated;" in js
    assert "未分配到子表" in js
    # 只有真的有沒分配的才畫（同這支函式其他兩條提示的規矩：正常時安靜）
    assert "execBudget > 0 && allocated > 0 && unalloc > 0" in js


def test_admin_phase_cost_lines_never_double_count():
    """🔴 phase='行政雜支' 的成本行不算人員成本 —— 那個階段值跟
    crm_project_expenses 講的是同一件事，兩邊都算＝同一筆錢在對照表的
    「人員」與「雜支」兩欄各出現一次。

    整案（financial-summary）與子表（cost-groups）**兩處都要濾**，
    只濾一邊會讓 Σ子表 ≠ 整案。
    """
    src = repo_src("routers/crm/costs.py")
    assert 'ADMIN_PHASE = "行政雜支"' in src, "述詞要有單一來源"
    assert src.count("CrmProjectCostLine.phase != ADMIN_PHASE") == 2, \
        "整案與子表兩處聚合都要排除行政雜支階段"
    # 逐案損益那支早就這樣濾了 —— 三處口徑要一致
    assert 'CrmProjectCostLine.phase != "行政雜支"' in \
        repo_src("routers/api_finance_projects.py")


def test_group_summary_and_project_totals_share_one_basis():
    """子表 summary 與整案的加總要吃同一批列，否則 Σ子表 ≠ 整案。"""
    src = repo_src("routers/crm/costs.py")
    # 子表的雜支比只查一次，而且在 session 還開著的時候
    assert "_pct = await _project_misc_pct(session, project_id)" in src
    assert "_cost_group_to_dict(g, summary, _pct)" in src
    # 🔴 雜支比一律在 `async with` **裡面**查完 —— 五個子表端點原本都寫在
    # return 那一行（那時 session 已經 close，靠 SQLAlchemy 自動再開一條連線
    # 才沒炸），清單那支還是每張子表查一次。
    assert "_cost_group_to_dict(g, summary, await _project_misc_pct(" not in src
    assert "_cost_group_to_dict(new_g, summary, await _project_misc_pct(" not in src


def test_no_orphan_derived_fields():
    """算了沒人用的衍生欄位不要留 —— 對照表的「預估毛利」畫的是 budgetProfit，
    留著一個 estProfit 只會讓下一個人以為那格是 未稅−預估成本。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-calc.js"))
    for dead in ("estProfit", "miscRemaining"):
        assert dead not in js, dead
