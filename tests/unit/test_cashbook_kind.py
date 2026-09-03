# -*- coding: utf-8 -*-
"""收支明細的列種類（owner 2026-09-04）：專案與發票代開用底色分開、沒填類別的淺紅底；工具列有同一套的快篩。

種類判定只有一份（crm-cashbook.js `_kindOf`）：專案＝category 在 project_link_categories、
發票代開＝category 在 passthrough_categories（後端 finance_category_map treatment=passthrough，不在前端寫死字）、
沒填類別＝none。底色與快篩都吃它。
"""
from tests.unit._srcscan import between, code_only, func_body, js_code_only, js_func_body, repo_src


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
    inc = between(js, "async function _cashPayForProject(e, pid, pname, o = {})", "function _cashCustomPay(")
    # 列的是專案頁「執行人員」那張表、開的單跟專案頁一樣（人|金額）——規則只有 crm-utils.groupCostStaff 一份
    assert "/cost-lines" in inc and "/payments?project_id=" in inc and "groupCostStaff(" in inc
    assert "payee_name: g.name, amount: g.subtotal, summary: g.items.join('、'), project_id: g.pid" in inc and "payee_type: ptype" in inc
    # 案可複數（owner「複數專案內部代開就開複數張請款單」）：每案一區、各開各的、可再連一個案（掛在發票的 project_ids）
    assert "const pids = Array.isArray(pid) ? pid : [pid];" in inc and 'data-act="more"' in inc
    assert "project_ids: [...(full.project_ids || []), picked]" in inc
    utils = js_code_only(repo_src("frontend/tabs/crm/crm-utils.js"))
    assert utils.count("export function groupCostStaff(") == 1
    grp = js_func_body(utils, "export function groupCostStaff(")
    assert "(p.advance_by || p.payee_name) + '|' + p.amount" in grp, "代墊單配費用歸屬人"
    proj = js_code_only(repo_src("frontend/tabs/crm/crm-projects-finance.js"))
    assert "groupCostStaff(lines, payments)" in js_func_body(proj, "async function _loadCostStaff(")
    assert "_payByOwnerAmount" not in proj, "分人配單的規則不可在專案頁再長一份"
    assert "batch-pay" not in js and "/cash-entries/${e.id}/payments" not in js, "請款不掛回這一列、不標已付"
    assert "payment_status: '應付款'" in js
    assert 'data-act="link"' in js and 'data-act="custom"' in js, "沒案時要有「連結專案」與「直接請款」"
    # 連結專案的挑選視窗多列私帳案（有 finance_mine 才抓）；選到私帳案不掛這一列，直接開它的費用配置、應付款記在私帳
    assert "hasModule('finance_mine')" in js and "'/projects?entity=mine'" in js and "'私帳｜' + (p.name || '')" in js
    assert "_cashPayForProject(e, picked, mp ? mp.name : '', { entity: 'mine', kai })" in js
    assert "...(ent ? { entity: ent } : {})" in js and "&entity=' + ent" in js


def test_passthrough_income_row_goes_through_the_project_and_deducts_the_fee():
    """發票代開的收入列（owner 2026-09-04 第二版「代開發票不能直接跳出這個，要連結專案」）：請款一樣走專案的執行人員表，
    只是可請款的錢＝面額 − 代開費（費率後端算：GET /invoices/{id}.commission_due）；沒案→只給「連結專案」（不給直接請款）；
    案子裡已請過的要說明；發票已有自動建的代開應匯單也要講。請款單欄：掛了案標幾號請款的，沒有才退回代開應匯那張。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "_cashKaiPay" not in js, "代開列不再有自己的請款視窗"
    disp = js_func_body(js, "window._cashRequestPay = async (id) => {")
    assert "const passthru = _kindOf(e) === 'passthrough';" in disp
    assert "remit: inv ? (inv.commission_due || inv.commission || 0) : 0" in disp and "0.92" not in disp
    assert "passthru ? '' : '<button" in disp, "代開列沒案時不給「直接請款」"
    assert "if (pids.length) return _cashPayForProject(e, pids, pnames, { kai });" in disp
    assert "pids = inv.project_ids.slice()" in disp, "發票掛的案可複數"
    # 案掛在發票上、不掛在代開的收支列（後端 core.project_link.CASH_CATEGORIES 不含發票代開：代開的錢不是案子的收入）
    assert "{ ...full, project_id: picked, project_ids: [picked] }" in disp and "if (passthru && !e.invoice_id) {" in disp
    from core.project_link import CASH_CATEGORIES
    assert "發票代開" not in CASH_CATEGORIES
    inc = between(js, "async function _cashPayForProject(e, pid, pname, o = {})", "function _cashCustomPay(")
    assert "可請款 $${_fmtNum(kai.remit)}" in inc and "案子裡的錢已請過" in inc and "執行人員都請過了" in inc
    assert "e.kai_payment_id ? { label: e.kai_payment_label" in inc, "發票已自動建的代開應匯單要講（吃列上的 kai_payment_*）"
    assert "(e.project_pay_label || (e.kai_payment_label ?" in js
    get = code_only(func_body(repo_src("routers/crm/finance.py"), "async def get_invoice("))
    assert 'd["commission_due"] = int(inv.commission or 0) or passthrough_commission(' in get
    py = code_only(func_body(repo_src("routers/crm/cash.py"), "async def _kai_payments_map("))
    assert "CrmPaymentRequest.source_invoice_id.in_(ids)" in py


def test_project_income_row_shows_which_day_it_was_requested():
    """掛了專案的收入列（owner 2026-09-04「如果專案有連結上的話 標注幾號請款的」）：請款單欄寫這個案子
    幾號開了幾張請款單（已付幾張）；規則在 core（project_pay_label），後端一次 IN 撈，預支款不算。"""
    from datetime import datetime, timezone
    from core.crm_logic import project_pay_label
    d = lambda m, dd: datetime(2026, m, dd, tzinfo=timezone.utc)
    assert project_pay_label([]) == ""
    assert project_pay_label([(d(9, 4), "應付款")] * 4) == "9/4 請款 4 張"
    assert project_pay_label([(d(9, 4), "已付款"), (d(9, 4), "應付款"), (d(8, 20), "已付款")]) == "9/4 ×2、8/20 ×1（已付 2）"
    assert project_pay_label([(None, "應付款")]) == "日期空 ×1"
    py = repo_src("routers/crm/cash.py")
    body = code_only(func_body(py, "async def _project_payments_map("))
    assert "CrmPaymentRequest.is_advance != 1" in body
    lst = code_only(func_body(py, "async def list_cash_entries("))
    assert "_project_payments_map(" in lst and "projpay.get(r[0].id" in lst
    assert "project_pay_label([it for p in ps for it in projitems.get(p, [])])" in lst, "一列可能掛好幾個案：標籤合併各案的請款單"
    assert "inv_proj.get(r[0].invoice_id, [])" in lst, "發票代開列的案掛在發票上（可複數）：請款單欄也要標得到"
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "(e.project_pay_label || (e.kai_payment_label ?" in js, "掂了案先標幾號請款的，沒有才退回代開應匯那張"
    inc = between(js, "async function _cashPayForProject(e, pid, pname, o = {})", "function _cashCustomPay(")
    assert "loadEntries({ cards: false });" in inc, "請完款要重抓，請款單欄才會標日期"


def test_cash_pay_overlay_keeps_the_type_select_above_the_list():
    """報支項目下拉要在清單上面（owner 2026-09-04「這裡被遮住了」）：視窗 body overflow-y:auto，放在最底下拉往下開就被截掉。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    inc = between(js, "async function _cashPayForProject(e, pid, pname, o = {})", "function _cashCustomPay(")
    assert inc.index('id="cpay-type"') < inc.index("${rows ?"), "報支項目要在人員清單之前"
