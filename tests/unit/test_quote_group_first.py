# -*- coding: utf-8 -*-
"""報價項目「先建大項目、再加子項目」（owner 2026-09-07：一列一列填、同名大項目被隔開，報價單上「後期製作」印成兩段）。

釘的規則：
- 檢視模型 core.quotation_pdf._group_items 同名就是同一組（順序＝第一次出現），舊資料也不會斷開（行為測試在 test_quotation_pdf）。
- 桌機／手機表單的編輯模型都是 groupQuoteItems／flattenQuoteGroups（js/shared/quote-amounts.js 一份），
  存檔時攤平；不再各自維護扁平列＋每列一格「分組」。
- 手機草稿卡：「寄出」改叫「建立」，多一顆「預覽」→ GET /quotations/{id}/preview 回 HTML（不改狀態、不存檔）。
"""
from tests.unit._srcscan import code_only, crm_css_src, func_body, js_code_only, js_func_body, repo_src

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
    assert "_QA.groupQuoteItems ||" in src and "_QA.flattenQuoteGroups ||" in src
    assert "_itemRows" not in src and "_quoteRemoveItem" not in src, "扁平列模型退場"
    assert 'class="quote-group-edit"' in src and "qi-add-sub" in src and "qi-remove-group" in src
    assert "addGroup(); _recalcTotals();" in src
    # payload 抽成 _buildPayload（按儲存與自動存草稿共用一份）
    payload = js_func_body(src, "function _buildPayload()")
    assert "const rows = _flatItems();" in payload and "items: rows.filter(it => it.description)" in payload
    save = js_func_body(src, "async function saveQuotation()")
    assert "const payload = _buildPayload();" in save
    assert "_flatItems().filter(it => it.description)" in js_func_body(src, "function _saveCurrentAsTemplate(name)")
    assert "+ 新增大項目" in repo_src("frontend/tabs/crm/crm-quotes.html")
    assert ".quote-group-edit {" in crm_css_src()


def test_mobile_draft_card_has_create_and_preview():
    src = js_code_only(repo_src(MOBILE))
    assert "label: '建立', to: sent" in src and "label: '寄出'" not in src
    card = js_func_body(src, "function cardHtml(q)")
    assert 'data-preview="${esc(q.id)}">預覽' in card and "sent ? '' :" in card, "預覽只在草稿卡"
    pv = js_func_body(src, "async function previewQuote(btn)")
    assert "mfetch(`/api/v1/crm/quotations/${encodeURIComponent(btn.dataset.preview)}/preview?as=json`)).html" in pv
    assert ".srcdoc = html" in pv
    assert "mfetchText" not in repo_src("frontend/m/shell.js"), "預覽不在 shell.js 加新 export（舊快取的 shell 會炸）"


def test_new_shared_exports_are_taken_with_a_fallback_for_one_release():
    """🔴 reference_cloudflare_js_cache：CF 給 .js 4 小時瀏覽器快取，新分頁 js 配舊共用檔時 named import 拿不到的 export
    會讓整個模組載入失敗（2026-09-07 報價管理分頁變「連不到伺服器」）。這一輪新加的 export 一律命名空間拿＋本地退路。"""
    for f in (DESKTOP, MOBILE):
        src = js_code_only(repo_src(f))
        assert "import * as _QA from" in src and "_QA.groupQuoteItems ||" in src and "_QA.flattenQuoteGroups ||" in src, f
        assert "paymentStagesToText, groupQuoteItems" not in src, f"{f}：新 export 不准走 named import（舊快取會炸）"
    mob = js_code_only(repo_src(MOBILE))
    assert "import { mfetch, mdownload," in mob and "mfetchText" not in mob


def test_preview_endpoint_renders_web_layout_without_side_effects():
    src = code_only(repo_src("routers/crm/quotes.py"))
    assert '@router.get("/quotations/{quotation_id}/preview", dependencies=[Depends(money_dep)])' in src
    body = func_body(src, "async def quotation_preview(")
    assert "_render_quotation_html(view, company, web=True)" in body and "HTMLResponse(" in body
    assert 'if as_ == "json":' in body and 'return {"html": html_doc}' in body
    for forbidden in ("html_to_pdf", "_archive_quotation_pdf", "q.status =", "commit("):
        assert forbidden not in body, forbidden
    render = func_body(src, "def _render_quotation_html(")
    assert "web=bool(web or web_pdf_url)" in render
    tpl = repo_src("templates/quotation_pdf.html")
    assert "{% if web %}" in tpl and "{% if web_pdf_url %}<div class=\"webbar\">" in tpl, "版面看 web、下載列只看 web_pdf_url"
