# -*- coding: utf-8 -*-
"""專案管理清單的「帳本」欄＋詳情彈窗（owner 2026-09-14「紅框處的狀態可以新增一欄來呈現」「點擊的專案詳情希望用彈窗」）。"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

CORE = repo_src("frontend/tabs/crm/crm-projects-core.js")


def test_badges_live_in_their_own_column_not_after_the_name():
    row = js_code_only(js_func_body(CORE, "export function renderList() {"))
    assert '<div class="crm-row-ledger">${_ledgerCellHtml(p)}</div>' in row
    assert '<div class="crm-row-name" title="${_esc(p.name)}">${_esc(p.name)}</div>' in row   # 案名格只剩案名
    cell = js_code_only(js_func_body(CORE, "function _ledgerCellHtml(p) {"))
    for tag in ("已連結私帳", "後期專案", "私帳", "billingTagHtml(p)"):
        assert tag in cell
    html = repo_src("frontend/tabs/crm/crm-projects.html")
    assert 'data-sort-key="ledger"' in html and '>帳本 <span class="crm-sort-ind">' in html and 'ledger: p => _ledgerSortKey(p),' in CORE
    css = repo_src("frontend/tabs/crm/crm.css")
    assert ".crm-col-ledger" in css and ".crm-row-ledger" in css


def test_ledger_sort_key_orders_linked_first():
    from tests.unit._srcscan import js_func_body as fb
    fn = fb(CORE, "function _ledgerSortKey(p) {")
    assert "'0-' + mode" in fn and "'1-linked'" in fn and "'2-' + mode" in fn and "'3-私帳'" in fn


def test_detail_opens_as_a_modal_with_backdrop_and_esc():
    sel = js_code_only(js_func_body(CORE, "export function selectProject(id) {"))
    assert "panel.classList.add('as-modal')" in sel and "_detailBackdrop(true);" in sel
    close = js_code_only(js_func_body(CORE, "export function closeDetail() {"))
    assert "panel.classList.remove('as-modal')" in close and "_detailBackdrop(false);" in close
    bd = js_code_only(js_func_body(CORE, "function _detailBackdrop(show) {"))
    assert "b.addEventListener('click', () => closeDetail());" in bd and "_detailEsc" in bd
    esc = js_code_only(js_func_body(CORE, "function _detailEsc(e) {"))
    assert "getComputedStyle(o).display !== 'none'" in esc and '[style*=' not in esc     # BUG-2：看 computed，不比 style 字串
    css = repo_src("frontend/tabs/crm/crm.css")
    assert ".crm-detail-panel.as-modal {" in css and "z-index: 900;" in css and ".crm-detail-backdrop" in css
