# -*- coding: utf-8 -*-
"""收支明細的列種類（owner 2026-09-04）：專案與發票代開用底色分開、沒填類別的淺紅底；工具列有同一套的快篩。

種類判定只有一份（crm-cashbook.js `_kindOf`）：專案＝category 在 project_link_categories、
發票代開＝category 在 passthrough_categories（後端 finance_category_map treatment=passthrough，不在前端寫死字）、
沒填類別＝none。底色與快篩都吃它。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src


def test_backend_options_expose_passthrough_categories_from_category_map():
    body = code_only(func_body(repo_src("routers/crm/cash.py"), "async def cash_entry_options("))
    assert '_FCM.treatment == "passthrough"' in body and '"passthrough_categories": passthrough' in body
    assert "CASH_INVOICE_PASSTHROUGH_CATEGORIES" in body, "只認發票代開（代收薪資／代收代付也是 passthrough，但不是代開）"
    from core.finance_logic import CASH_INVOICE_PASSTHROUGH_CATEGORIES
    assert CASH_INVOICE_PASSTHROUGH_CATEGORIES == ("發票代開",)


def test_frontend_kind_is_one_rule_for_color_and_filter():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "const _kindOf = (e) =>" in js and js.count("_kindOf(") >= 2     # 底色（快篩鈕已改成打字框）
    kind = js[js.index("const _kindOf = (e) =>"):js.index("let _catQ")]
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
    inc = code_only(func_body(js, "async function _cashPayForProject(e, pid, pname, o = {})"))
    assert "/cost-lines" in inc and "/payments?project_id=" in inc and "cost_line_id: l.id" in inc
    assert "batch-pay" not in js and "/cash-entries/${e.id}/payments" not in js, "請款不掛回這一列、不標已付"
    assert "payment_status: '應付款'" in js
    assert 'data-act="link"' in js and 'data-act="custom"' in js, "沒案時要有「連結專案」與「直接請款」"
    # 連結專案的挑選視窗多列私帳案（有 finance_mine 才抓）；選到私帳案不掛這一列，直接開它的費用配置、應付款記在私帳
    assert "hasModule('finance_mine')" in js and "'/projects?entity=mine'" in js and "'私帳｜' + (p.name || '')" in js
    assert "_cashPayForProject(e, picked, mp ? mp.name : '', { entity: 'mine' })" in js
    assert "...(ent ? { entity: ent } : {})" in js and "&entity=' + ent" in js


def test_passthrough_income_row_requests_the_remit_payment_and_shows_it():
    """發票代開的收入列（owner 2026-09-04）：請款＝這張發票的代開應匯（面額−代開費）開給代開人——
    後端在發票標已收款時自動建那張（source_invoice_id 為鍵），前端確保它在、沒有就照同一套補一張；
    列的請款單欄標註它（kai_payment_*），**不**掛成分配（掛了會被當成已付款）。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "if (_kindOf(e) === 'passthrough') return _cashKaiPay(e);" in js
    kai = js_func_body(js, "async function _cashKaiPay(e)")     # func_body 的結尾只認 def，JS 會吃到檔尾
    assert "p.source_invoice_id === e.invoice_id" in kai and "source_invoice_id: inv.id" in kai
    assert "inv.commission_due || inv.commission" in kai and "/cash-entries/" not in kai and "batch-pay" not in kai
    assert "0.92" not in kai and "* 8" not in kai, "費率不在前端猜：commission_due 由後端照 settings 費率算"
    get = code_only(func_body(repo_src("routers/crm/finance.py"), "async def get_invoice("))
    assert 'd["commission_due"] = int(inv.commission or 0) or passthrough_commission(' in get
    assert "e.kai_payment_amount" in js, "同一個代開人很多張，請款單欄要帶金額才分得出是哪一張"
    assert 'data-act="proj"' in kai and "_cashPayForProject(e, inv.project_id" in kai, "連到的發票不是代開類別→改走案子的費用配置"
    assert "e.kai_payment_label" in js
    py = code_only(func_body(repo_src("routers/crm/cash.py"), "async def _kai_payments_map("))
    assert "CrmPaymentRequest.source_invoice_id.in_(ids)" in py
    lst = code_only(func_body(repo_src("routers/crm/cash.py"), "async def list_cash_entries("))
    assert "_kai_payments_map(session" in lst and "kaimap.get(r[0].invoice_id)" in lst
    assert '"kai_payment_label": kai.get("label", "")' in repo_src("routers/crm/cash.py")
