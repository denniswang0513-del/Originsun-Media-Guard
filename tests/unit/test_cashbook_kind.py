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
    assert "const _kindOf = (e) => (finIsMine() ? '' :" in js, "私帳不上底色（owner 2026-09-04）"
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
    # 案可複數：每案一區、各開各的；「再連一個案」在代開流程（_cashKaiAddProject，掛在發票的 project_ids）
    assert "const pids = Array.isArray(pid) ? pid : [pid];" in inc
    assert "project_ids: [...(full.project_ids || []), picked]" in js
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
    assert "_cashPayForProject(e, picked, mp ? mp.name : '', { entity: 'mine' })" in js
    assert "...(ent ? { entity: ent } : {})" in js and "&entity=' + ent" in js


def test_passthrough_income_row_requests_the_whole_remit_on_the_linked_projects():
    """發票代開的收入列（owner 2026-09-04 第三版「發票代開的請款就是整筆請過去，項目是發票代開」「收款人員可以勾選」）：
    一樣先連結專案（案掛在發票上、可複數），但不是執行人員表 —— 一張請款單＝整筆代開應匯（面額 − 代開費，費率後端算
    commission_due），類別「發票代開」、掛哪個案就開在哪個案、收款人可挑（預設代開人）、source_invoice_id 釘住發票（跟後端
    自動建的那張同鍵，不重複）。已請過的要講。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    disp = js_func_body(js, "window._cashRequestPay = async (id) => {")
    assert "const passthru = _kindOf(e) === 'passthrough';" in disp
    assert "remit: inv ? (inv.commission_due || inv.commission || 0) : 0" in disp and "0.92" not in disp
    assert "passthru ? '' : '<button" in disp, "代開列沒案時不給「直接請款」"
    assert "kai ? _cashKaiRemit(e, pids, pnames, kai) : _cashPayForProject(e, pids, pnames)" in disp
    assert "pids = inv.project_ids.slice()" in disp and "{ ...full, project_id: picked, project_ids: [picked] }" in disp
    assert "if (passthru && !e.invoice_id) {" in disp
    # 🔴 2026-09-05 翻案（owner：「發票代開的收支要可以連專案」）：
    # 原本這裡釘的是「案掛在發票上、不掛在代開的收支列」（2026-09-04 拍板）。
    # 實際用起來的後果是：快樂學游泳那案收到的 $161,700 記在一列 category=
    # 「發票代開」的收支上，那列**存不進專案** → 專案頁「客戶已匯 $0」，
    # 而錢明明收到了。所以改成收支列也可以掛。
    # 掛在發票上那條路沒有退場（整筆請款仍走 _cashKaiRemit），兩條並存。
    from core.project_link import CASH_CATEGORIES
    assert "發票代開" in CASH_CATEGORIES
    remit = between(js, "async function _cashKaiRemit(e, pids, pnames, kai)", "function _cashKaiAddProject(")
    assert "category: '發票代開'" in remit and "source_invoice_id: inv.id" in remit and "project_id: it.pid" in remit
    assert "p.source_invoice_id === e.invoice_id" in remit and "e.kai_payment_id" in remit, "已請過的（含沒掛案的自動單）要講"
    assert "data-kai-payee" in remit and "_fetch('/staff')" in remit, "收款人可挑"
    # 沒掛案的自動單（開給代開人、還沒付）可收回改開在案子上，不然「還可請 0」卡死（思沙龍）；付掉的不能收回
    assert "x.payment_status !== '已付款'" in remit and "_fetch('/payments/' + x.id, { method: 'DELETE' })" in remit
    assert 'id="kai-withdraw"' in remit
    assert "/cost-lines" not in remit and "groupCostStaff" not in remit, "代開列不是執行人員表"
    # 內部代開掛私帳案（思沙龍）：代開列選到私帳案一樣掛到發票、整筆請款開在私帳那本帳（owner 2026-09-04「思沙龍的私帳走到這裡就卡住了」）
    assert "if (mineIds.has(picked) && !passthru) {" in disp
    assert "const mineIds = new Set((await _mineProjectList()).map((p) => p.id));" in remit
    assert "...(entOf(it.pid) ? { entity: 'mine' } : {})" in remit and "'&entity=mine'" in remit
    add = between(js, "async function _cashKaiAddProject(e, pids)", "async function _cashPayForProject(")   # 註解會被 js_code_only 剝掉，錨下一個函式
    assert "_mineProjectList()" in add and "'私帳｜' + (p.name || '')" in add
    inc = between(js, "async function _cashPayForProject(e, pid, pname, o = {})", "function _cashCustomPay(")
    assert "kai" not in inc, "執行人員表只給一般收入列"
    assert "(e.project_pay_label || (e.kai_payment_label ?" in js
    get = code_only(func_body(repo_src("routers/crm/finance.py"), "async def get_invoice("))
    assert 'd["commission_due"] = int(inv.commission or 0) or passthrough_commission(' in get


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


def test_private_ledger_passthrough_requests_still_label_the_parent_row():
    """內部代開掛私帳案（思沙龍）：代開請款單開在私帳，母公司收支列的請款單欄也要標得到——只給看得到私帳的人
    （標籤帶收款人名字，對沒 mine scope 的人就是洩漏）。"""
    src = repo_src("routers/crm/cash.py")
    lst = code_only(func_body(src, "async def list_cash_entries("))
    assert "_inc_mine = not _hide_mine(request)" in lst
    assert "_kai_payments_map(session, [r[0].invoice_id for r in rows], ent, _inc_mine)" in lst
    assert "_project_payments_map(session, [p for ps in eff_pids.values() for p in ps], ent, _inc_mine)" in lst
    for fn in ("async def _kai_payments_map(", "async def _project_payments_map("):
        body = code_only(func_body(src, fn))
        assert 'include_mine and entity != "mine"' in body, fn
