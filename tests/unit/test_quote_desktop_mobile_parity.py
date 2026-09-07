# -*- coding: utf-8 -*-
"""電腦版報價表單以手機版為主（owner 2026-09-07）：客戶→案名／連結案、優惠↔最終報價互推、項目列無成本欄、
稅前折扣退場（舊值原樣帶回）。內部成本相關功能之後再規劃（表單裡不再有）。"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

HTML = "frontend/tabs/crm/crm-quotes.html"
JS = "frontend/tabs/crm/crm-quotes.js"


def test_project_entry_is_client_first_like_mobile():
    html = repo_src(HTML)
    for i in ("quote-f-client_id", "quote-btn-new-client", "quote-f-project_name", "quote-f-link_project", "quote-f-project-fixed"):
        assert f'id="{i}"' in html, i
    assert 'id="quote-f-project_id"' not in html and 'id="quote-inline-project"' not in html
    js = js_code_only(repo_src(JS))
    assert "_showInlineProjectForm" not in js and "_populateProjectSelect" not in js
    save = js_func_body(js, "async function saveQuotation()")
    assert "status: '提案'" in save and "client_id: _form.client_id" in save, "沒連結案＝照案名建殼案（階段提案）"
    assert "_showModalError('請先選客戶')" in save and "或連結一個既有專案" in save


def test_promo_and_final_price_are_reciprocal_and_pre_tax_discount_is_gone():
    html = repo_src(HTML)
    assert 'id="quote-f-promo"' in html and 'id="quote-calc-final"' in html
    assert 'id="quote-f-discount"' not in html
    js = js_code_only(repo_src(JS))
    rc = js_func_body(js, "function _recalcTotals()")
    assert "_form.anchor === 'promo'" in rc and "_form.anchor === 'final'" in rc
    assert "discount" not in rc, "稅前折扣退場：重算不再吃它"
    save = js_func_body(js, "async function saveQuotation()")
    assert "discount: editing ? (editing.discount || 0) : 0" in save, "舊報價的折扣值要原樣帶回，不洗掉"


def test_internal_cost_is_out_of_the_form_for_now():
    html = repo_src(HTML)
    assert 'id="quote-calc-cost"' not in html and 'id="quote-calc-profit"' not in html
    js = js_code_only(repo_src(JS))
    assert "qi-cost" not in js
    # 既有 internal_cost 值不動：項目物件整個展開帶回
    save = js_func_body(js, "async function saveQuotation()")
    assert "internal_cost: it.internal_cost || 0" in save
