# -*- coding: utf-8 -*-
"""收支明細的列種類（owner 2026-09-04）：專案與發票代開用底色分開、沒填類別的淺紅底；工具列有同一套的快篩。

種類判定只有一份（crm-cashbook.js `_kindOf`）：專案＝category 在 project_link_categories、
發票代開＝category 在 passthrough_categories（後端 finance_category_map treatment=passthrough，不在前端寫死字）、
沒填類別＝none。底色與快篩都吃它。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_backend_options_expose_passthrough_categories_from_category_map():
    body = code_only(func_body(repo_src("routers/crm/cash.py"), "async def cash_entry_options("))
    assert '_FCM.treatment == "passthrough"' in body and '"passthrough_categories": passthrough' in body


def test_frontend_kind_is_one_rule_for_color_and_filter():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "const _kindOf = (e) =>" in js and js.count("_kindOf(") >= 3
    kind = js[js.index("const _kindOf = (e) =>"):js.index("let _kindOnly")]
    assert "代開" not in kind, "種類判定不寫死類別名：代開那組從後端 passthrough_categories 拿"
    assert "_PASSTHROUGH = o.passthrough_categories" in js
    assert "' cash-kind-' + _kindOf(e)" in js
    assert "_rows.filter((e) => _kindOf(e) === _kindOnly)" in js
    html = repo_src("frontend/tabs/crm/crm-cashbook.html")
    assert 'id="cash-filter-kind"' in html and html.count("data-kind=") == 3
    css = repo_src("frontend/tabs/crm/crm.css")
    for k in ("project", "passthrough", "none"):
        assert f".crm-row.cash-kind-{k} {{" in css, k
    assert ".crm-row.cash-kind-project.selected" in css, "選中色要蓋得過種類底色"


def test_request_payment_from_cash_row():
    """收支明細列的「請款」（owner 2026-09-04）：有連專案→列該案費用配置挑一行開單（帶 cost_line_id，後端擋重複）；
    沒連→提示連結專案或直接自訂一張。開完掛回這一列並標已付（錢已經出去了）。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "function _payMenu(e)" in js and "..._payMenu(e)" in js
    assert "window._cashRequestPay = async (id) =>" in js and "/cost-lines" in js
    link = code_only(func_body(js, "async function _cashCreateAndLinkPayment(e, body)"))
    assert "_fetch('/payments', { method: 'POST'" in link
    assert "/payments`, { method: 'PUT'" in link and "/payments/batch-pay" in link, "開單後要掛回這一列並標已付"
    assert "cost_line_id: pre.cost_line_id || null" in js
    assert "data-act=\"link\"" in js and "data-act=\"custom\"" in js, "沒專案時要有「連結專案」與「直接請款」"
