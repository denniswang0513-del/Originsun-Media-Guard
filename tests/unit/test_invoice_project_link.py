# -*- coding: utf-8 -*-
"""發票掛專案（owner 2026-09-04）：三種類別都可以掛；私帳案只能掛在「內部代開」、且要有私帳權限。
清單依類別上底色（專案／內部代開／外部代開／沒填類別淺紅）。

規則正本：core.finance_logic.MINE_LINK_INVOICE_CATEGORY；後端 _assert_project_link 在 create／update 都擋
（下拉不給只是 UX，直接送 id 也要擋）；清單對沒私帳權限的人不顯示私帳案名。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_backend_rule_is_enforced_on_create_and_update_and_list_hides_mine_names():
    from core.finance_logic import INVOICE_PASSTHROUGH_CATEGORIES, MINE_LINK_INVOICE_CATEGORY
    assert MINE_LINK_INVOICE_CATEGORY in INVOICE_PASSTHROUGH_CATEGORIES
    src = repo_src("routers/crm/finance.py")
    body = code_only(func_body(src, "async def _assert_project_link("))
    assert "MINE_LINK_INVOICE_CATEGORY" in body and "hide_mine_projects(request)" in body
    assert "status_code=422" in body and "status_code=403" in body
    for header in ("async def create_invoice(", "async def update_invoice("):
        assert "_assert_project_link(" in code_only(func_body(src, header)), header
    lst = code_only(func_body(src, "async def list_invoices("))
    assert 'CrmProject.entity.label("pent")' in lst and '== "mine"' in lst and "hide_mine_projects(request)" in lst


def test_frontend_project_field_for_all_categories_and_mine_only_for_internal():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-invoices.js"))
    toggle = js[js.index("function _toggleCategory()"):js.index("if (kindSel)")]
    assert "projectRow.style.display = ''" in toggle and "cat === '專案' ? '' : 'none'" not in toggle
    assert "_populateProjectSelect(projSel.value, cat)" in toggle
    vis = code_only(func_body(js, "function _updateCategoryVisibility()"))     # 編輯視窗那套（跟詳情面板分開）
    assert "inv-cond-project').style.display = ''" in vis and "_populateProjectSelect(" in vis and "cat === '專案' ? '' : 'none'" not in vis
    pop = code_only(func_body(js, "function _populateProjectSelect(selectedId, category)"))
    assert "cat === _MINE_LINK_CAT" in pop and "_mineProjects" in pop and "私帳｜" in pop
    assert "hasModule('finance_mine')" in js and "'/projects?entity=mine'" in js, "沒私帳權限的人不打私帳專案清單"
    # 發票清單不上底色（owner 2026-09-04「發票的部分不用改色」）——底色只在收支明細
    # （inv-kind-badge／inv-kind-電子 是既有的發票種類 badge，不是列底色）
    assert "_kindClass(" not in js and ".crm-row.inv-kind-" not in repo_src("frontend/tabs/crm/crm.css")


def test_invoice_can_hang_on_several_projects_and_the_first_stays_project_id():
    """owner 2026-09-04「連結的專案可以複數；複數專案內部代開就開複數張請款單」：crm_invoices.project_ids（JSON），
    project_id 永遠＝第一個；讀法／正規化只有 core.project_link 一份；create／update 每個案都過 _assert_project_link。"""
    from core.project_link import invoice_project_ids, normalize_invoice_projects
    assert invoice_project_ids("a", None) == ["a"]
    assert invoice_project_ids("a", '["a","b"]') == ["a", "b"]
    assert invoice_project_ids("z", '["a","b"]') == ["z", "a", "b"], "project_id 不在清單＝插到最前"
    assert invoice_project_ids(None, "not json") == []
    d = {"project_id": "a", "project_ids": ["b", "a", "c"]}; normalize_invoice_projects(d)
    assert d["project_id"] == "a" and d["project_ids"] == '["a", "b", "c"]'
    d = {"project_id": "a", "project_ids": None}; normalize_invoice_projects(d, ["a", "b"])
    assert d["project_ids"] == '["a", "b"]', "單案表單沒送清單＝保留既有"
    d = {"project_id": "z", "project_ids": None}; normalize_invoice_projects(d, ["a", "b"])
    assert d["project_id"] == "z" and d["project_ids"] is None, "表單換成清單外的案＝清單重來"
    d = {"project_id": None, "project_ids": None}; normalize_invoice_projects(d, ["a", "b"])
    assert d["project_id"] is None and d["project_ids"] is None
    src = repo_src("routers/crm/finance.py")
    for header in ("async def create_invoice(", "async def update_invoice("):
        body = code_only(func_body(src, header))
        assert "_norm_inv_projects(" in body and "for _pid in _inv_pids(" in body, header
    assert '"project_ids": _inv_pids(inv.project_id' in src
    assert '("crm_invoices", "project_ids", "TEXT")' in repo_src("main.py")
