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
    assert "CASH_INVOICE_PASSTHROUGH_CATEGORIES" in body, "只認發票代開（代收薪資／代收代付也是 passthrough，但不是代開）"
    from core.finance_logic import CASH_INVOICE_PASSTHROUGH_CATEGORIES
    assert CASH_INVOICE_PASSTHROUGH_CATEGORIES == ("發票代開",)


def test_frontend_kind_is_one_rule_for_color_and_filter():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "const _kindOf = (e) =>" in js and js.count("_kindOf(") >= 2     # 底色（快篩鈕已改成打字框）
    kind = js[js.index("const _kindOf = (e) =>"):js.index("let _kindOnly")]
    assert "代開" not in kind, "種類判定不寫死類別名：代開那組從後端 passthrough_categories 拿"
    assert "_PASSTHROUGH = o.passthrough_categories" in js
    assert "' cash-kind-' + _kindOf(e)" in js
    # 快篩鈕已改成一個打字的類別框（owner 2026-09-04）；種類判定只剩底色在用
    html = repo_src("frontend/tabs/crm/crm-cashbook.html")
    assert 'id="cash-filter-kind"' not in html and 'id="cash-filter-off"' not in html
    css = repo_src("frontend/tabs/crm/crm.css")
    for k in ("project", "passthrough"):
        assert f".crm-row.cash-kind-{k} {{" in css, k
    assert ".crm-row.cash-kind-none {" not in css, "只有專案與發票代開上色（owner 2026-09-04）"
    assert ".crm-row.cash-kind-project.selected" in css, "選中色要蓋得過種類底色"


def test_request_payment_from_cash_row():
    """收入列的「請款」（owner 2026-09-04）：收到案子的款→幫案子裡的人開應付款（多選費用配置、一行一張、帶 cost_line_id、
    不掛回這一列、不標已付）。沒掛案→先看發票的案；再沒有→提示連結專案或直接開一張。支出列不放（owner：支出的請款拿掉）。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "const _isIncomeRow = (e) =>" in js and "..._payMenu(e)" in js
    assert "if (e.split_count || !_isIncomeRow(e)) return [];" in code_only(func_body(js, "function _payMenu(e)"))
    assert "window._cashRequestPay = async (id) =>" in js and "/invoices/' + e.invoice_id" in js
    inc = code_only(func_body(js, "async function _cashPayForProject(e, pid, pname)"))
    assert "/cost-lines" in inc and "/payments?project_id=" in inc and "cost_line_id: l.id" in inc
    assert "batch-pay" not in js and "/cash-entries/${e.id}/payments" not in js, "請款不掛回這一列、不標已付"
    assert "payment_status: '應付款'" in js
    assert 'data-act="link"' in js and 'data-act="custom"' in js, "沒案時要有「連結專案」與「直接請款」"
