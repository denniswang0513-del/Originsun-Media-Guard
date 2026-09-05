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
