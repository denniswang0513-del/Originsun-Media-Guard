# -*- coding: utf-8 -*-
"""報價項目「先建大項目、再加子項目」（owner 2026-09-07：一列一列填、同名大項目被隔開，報價單上「後期製作」印成兩段）。

釘的規則：
- 檢視模型 core.quotation_pdf._group_items 同名就是同一組（順序＝第一次出現），舊資料也不會斷開（行為測試在 test_quotation_pdf）。
- 桌機／手機表單的編輯模型都是 groupQuoteItems／flattenQuoteGroups（js/shared/quote-amounts.js 一份），
  存檔時攤平；不再各自維護扁平列＋每列一格「分組」。
- 手機草稿卡：「寄出」改叫「建立」，多一顆「預覽」→ GET /quotations/{id}/preview 回 HTML（不改狀態、不存檔）。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

SHARED = "frontend/js/shared/quote-amounts.js"
MOBILE = "frontend/m/views/quotes.js"
DESKTOP = "frontend/tabs/crm/crm-quotes.js"


def test_shared_group_helpers_exist_and_are_leaf():
    src = repo_src(SHARED)
    assert "export function groupQuoteItems(items)" in src and "export function flattenQuoteGroups(groups)" in src
    assert "import " not in js_code_only(src), "quote-amounts.js 是零 import 葉節點"


def test_mobile_form_is_group_first():
    src = js_code_only(repo_src(MOBILE))
    assert "groupQuoteItems" in src and "flattenQuoteGroups" in src
    assert "groups: groupsOf(q && q.items)" in src, "開表單就收成大項目"
    assert 'data-gk="name"' in src and 'data-add="${gi}"' in src and 'data-delg="${gi}"' in src
    assert "＋ 大項目" in src and "＋ 子項目" in src
    assert 'data-k="group_name"' not in src, "每列一格「分組」退場"
    save = js_func_body(src, "async function save(host)")
    assert "flatItems().filter(" in save, "存檔＝大項目一組接一組攤平"
    assert "items: flatItems()" in js_func_body(src, "function recalc()")


def test_desktop_form_is_group_first():
    src = js_code_only(repo_src(DESKTOP))
    assert "groupQuoteItems, flattenQuoteGroups" in src
    assert "_itemRows" not in src and "_quoteRemoveItem" not in src, "扁平列模型退場"
    assert 'class="quote-group-edit"' in src and "qi-add-sub" in src and "qi-remove-group" in src
    assert "addGroup(); _recalcTotals();" in src
    save = js_func_body(src, "async function saveQuotation()")
    assert "const rows = _flatItems();" in save and "items: rows.filter(it => it.description)" in save
    assert "_flatItems().filter(it => it.description)" in js_func_body(src, "function _saveCurrentAsTemplate(name)")
    assert "+ 新增大項目" in repo_src("frontend/tabs/crm/crm-quotes.html")
    assert ".quote-group-edit {" in repo_src("frontend/tabs/crm/crm.css")


def test_mobile_draft_card_has_create_and_preview():
    src = js_code_only(repo_src(MOBILE))
    assert "label: '建立', to: sent" in src and "label: '寄出'" not in src
    card = js_func_body(src, "function cardHtml(q)")
    assert 'data-preview="${esc(q.id)}">預覽' in card and "sent ? '' :" in card, "預覽只在草稿卡"
    pv = js_func_body(src, "async function previewQuote(btn)")
    assert "mfetchText(`/api/v1/crm/quotations/${encodeURIComponent(btn.dataset.preview)}/preview`)" in pv
    assert ".srcdoc = html" in pv
    assert "export async function mfetchText(path)" in repo_src("frontend/m/shell.js")


def test_preview_endpoint_renders_web_layout_without_side_effects():
    src = code_only(repo_src("routers/crm/quotes.py"))
    assert '@router.get("/quotations/{quotation_id}/preview", dependencies=[Depends(money_dep)])' in src
    body = func_body(src, "async def quotation_preview(")
    assert "_render_quotation_html(view, company, web=True)" in body and "HTMLResponse(" in body
    for forbidden in ("html_to_pdf", "_archive_quotation_pdf", "q.status =", "commit("):
        assert forbidden not in body, forbidden
    render = func_body(src, "def _render_quotation_html(")
    assert "web=bool(web or web_pdf_url)" in render
    tpl = repo_src("templates/quotation_pdf.html")
    assert "{% if web %}" in tpl and "{% if web_pdf_url %}<div class=\"webbar\">" in tpl, "版面看 web、下載列只看 web_pdf_url"
