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
    assert "quote-f-discount" not in rc, "稅前折扣退場：表單沒有它"
    # 舊報價存著的折扣仍要算進總計（後端 _calc_quotation 有吃），不然畫面的含稅總計跟存的對不上、優惠也用錯底數
    assert "discount: _editingQuote?.discount || 0" in rc
    mob = js_code_only(repo_src("frontend/m/views/quotes.js"))
    assert "discount: _form.discount" in js_func_body(mob, "function recalc()")
    save = js_func_body(js, "function _buildPayload()")   # 按儲存與自動存草稿共用的 payload
    assert "discount: _editingQuote ? (_editingQuote.discount || 0) : 0" in save, "舊報價的折扣值要原樣帶回，不洗掉"
    # 正在編的那筆從 openModal 帶進來（_editingQuote），不從有狀態篩選的 _quotations 查（查不到＝被當成新增、要客戶）
    assert "_quotations.find" not in save
    assert "const editing = _editingQuote;" in js_func_body(js, "async function saveQuotation()")


def test_share_button_and_update_do_not_regress_on_review():
    """polish review 2026-09-07 抓到的三個回歸：await 之後 ev.currentTarget 是 null；分享鈕對非管理員是死鈕（POST /share 管理員限定）；
    手機 PUT 不帶 quote_date／valid_until／discount／status，後端整包寫回會洗掉。"""
    js = js_code_only(repo_src(JS))
    assert "const btn = ev.currentTarget;" in js and "ev.currentTarget.textContent" not in js
    assert "const canShare = q.share_url || hasModule('crm_quotes');" in js, "第二批：分享開給 crm_quotes（後端 /share 同步放行）"
    from tests.unit._srcscan import code_only, func_body
    upd = code_only(func_body(repo_src("routers/crm/quotes.py"), "async def update_quotation("))
    assert "sent = req.model_fields_set" in upd
    for k in ('"discount" in sent', '"status" in sent', '"quote_date" in sent', '"valid_until" in sent'):
        assert k in upd, k
    assert "_calc_quotation(items_data, discount, req.tax_rate)" in upd and "q.discount = discount" in upd
    mob = js_code_only(repo_src("frontend/m/views/quotes.js"))
    assert "final_price: F('final_price').value.trim() === '' ? null" in js_func_body(mob, "async function save(host)")


def test_internal_cost_is_out_of_the_form_for_now():
    html = repo_src(HTML)
    assert 'id="quote-calc-cost"' not in html and 'id="quote-calc-profit"' not in html
    js = js_code_only(repo_src(JS))
    assert "qi-cost" not in js
    # 既有 internal_cost 值不動：項目物件整個展開帶回
    save = js_func_body(js, "function _buildPayload()")
    assert "internal_cost: it.internal_cost || 0" in save


def test_backend_refuses_share_link_for_drafts_and_archives_off_the_loop():
    """polish simplify 2026-09-07：「送出再產生連結」不能只靠兩個前端藏鈕（舊 .js 有 4 小時快取、API 可直打）——
    POST /share 對草稿回 422。存檔 copy（可能是 NAS）丟執行緒，不卡 event loop。"""
    from tests.unit._srcscan import code_only, func_body
    src = code_only(repo_src("routers/crm/quotes.py"))
    share = func_body(src, "async def share_quotation(")
    assert "q.status == QUOTE_STATUSES[0]" in share and "status_code=422" in share
    assert share.index("QUOTE_STATUSES[0]") < share.index("new_short_token()")
    for fn in ("async def generate_quotation_snapshot(", "async def _quotation_pdf_response("):
        assert "asyncio.to_thread(lambda: _archive_quotation_pdf(tmp_pdf, view))" in func_body(src, fn), fn
    # 快照（客戶連結送的檔）也是走 SMB 寫 NAS，同樣不能在 event loop 上做
    assert "asyncio.to_thread(lambda: _write_snapshot(tmp_pdf, html_doc, names))" in \
        func_body(src, "async def generate_quotation_snapshot(")
    # 桌機分享鈕改走共用 copyText（非 https 有 execCommand 退路），不再自己 navigator.clipboard
    js = js_code_only(repo_src(JS))
    assert "copyText(full, btn)" in js and "navigator.clipboard" not in js
