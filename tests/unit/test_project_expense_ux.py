# -*- coding: utf-8 -*-
"""專案頁雜支區的三個介面坑（soca 2026-09-07 回報）釘住不回退：
1. 快速新增列的收款人下拉要能打字搜尋（不准再標 data-no-search 退出 select-upgrade）。
2. 空的可編輯格要有可點面積（flex 列裡空 span 沒高度）。
3. 就地改收款人時要掛員工 datalist。
另釘設計：零用金列（staff_id）不給「請款」鈕 —— 那些錢走零用金批次，兩條路都走＝同一筆錢請兩次。
"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

COST_JS = "frontend/tabs/crm/crm-projects-cost.js"
CSS = "frontend/tabs/crm/crm.css"


def test_quick_add_payee_select_is_searchable():
    src = repo_src(COST_JS)
    assert 'id="exp-qa-payee"' in src
    line = next(ln for ln in src.splitlines() if 'id="exp-qa-payee"' in ln)
    assert "data-no-search" not in line, "收款人下拉標了 data-no-search 就變回原生選單，打字找不到人"
    # 類別那顆只有 5 個選項，維持原生是刻意的
    cat = next(ln for ln in src.splitlines() if 'id="exp-qa-cat"' in ln)
    assert "data-no-search" in cat


def test_empty_editable_cell_has_click_area():
    css = repo_src(CSS)
    assert ".cost-row-expense .cost-editable:empty { min-height" in css
    assert ".cost-row-expense:hover .cost-editable:empty::after" in css


def test_inline_payee_editor_offers_staff_names():
    body = js_code_only(js_func_body(repo_src(COST_JS), "window._expEdit = function("))
    assert "field === 'payee'" in body and "exp-payee-list" in body and "setAttribute('list'" in body
    assert "state.staffList" in body


def test_petty_rows_keep_no_claim_button_by_design():
    """零用金列不給請款鈕是設計不是漏：那條路是零用金頁「送出請款」→ 應付款。"""
    src = js_code_only(repo_src(COST_JS))
    assert "if (e.staff_id) return '';" in src
    assert "window._expCreatePayment(" in src
