# -*- coding: utf-8 -*-
"""報價彈窗 2026-09-09 那批（owner 當面提的幾件事）：

1. **自動存草稿**：「打了客戶與專案之後就可以自動儲存了，要不不小心點掉就消失了」——
   客戶＋案名（或已經固定的案）一齊，動到表單就先 POST 出一張草稿；之後 debounce PUT；
   關窗把沒送的補送完。只有「新增」這條路自動存，編輯既有報價維持按儲存才寫。
2. **大項目可以更改順序**、3. **小項目可以更改排序**（拖曳；子項目可以拖到別的大項目）。
4. **備註用一列一列開**，每列可拖曳排序、前面有編號。
5. 詳情面板「報價資訊／項目明細」合成一頁，「編輯」開的是編輯報價彈窗（就地編欄位那套退場）。
"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

JS = "frontend/tabs/crm/crm-quotes.js"
HTML = "frontend/tabs/crm/crm-quotes.html"
CSS = "frontend/tabs/crm/crm.css"


# ── 1. 自動存草稿 ─────────────────────────────────────────────

def test_autosave_only_creates_when_client_and_project_are_both_there():
    js = js_code_only(repo_src(JS))
    target = js_func_body(js, "function _autoTarget()")
    assert "if (_editingProjectId) return" in target and "if (_form.project_id)  return" in target
    assert "if (_form.client_id && name) return" in target, "客戶＋案名齊了才算數"
    create = js_func_body(js, "async function _autoCreateDraft()")
    assert "if (!_autoOn || _editingId) return;" in create, "已經有草稿就不再建第二張"
    assert "const t = _autoTarget();" in create and "if (!t) return;" in create
    assert "'/projects'" in create and "status: '提案'" in create, "沒連案就照案名開殼案（同按儲存那條路）"
    assert "/quotations" in create and "method: 'POST'" in create
    assert "_editingId = q.id;" in create and "_lockProjectFields();" in create


def test_autosave_is_new_quote_only_and_survives_a_click_away():
    js = js_code_only(repo_src(JS))
    open_ = js_func_body(js, "async function openModal(")
    assert "_autoReset(!quotation);" in open_, "編輯既有報價不自動存（會在使用者決定前改掉舊資料）"
    assert "_autoReset(true);" in js_func_body(js, "export async function quoteDup("), "複製＝新增，要自動存"

    touch = js_func_body(js, "function _autoTouch()")
    assert "if (!_autoOn) return;" in touch
    assert "if (!_editingId) { _autoQueue(_autoCreateDraft); return; }" in touch
    assert "setTimeout(() => _autoQueue(_autoFlush), AUTOSAVE_DEBOUNCE_MS)" in touch

    close = js_func_body(js, "async function closeModal()")
    assert "_autoQueue(_autoFlush)" in close and "await pending" in close, "關窗前把沒送的補送完"
    assert "style.display = 'none'" in close

    init = js_func_body(js, "export async function initCrmQuotesTab(")
    # X／取消／點外面三個入口都走 closeModal（原本是 inline onclick 直接關 display）
    assert "getElementById('quote-modal-close').addEventListener('click', closeModal)" in init
    assert "getElementById('quote-btn-cancel').addEventListener('click', closeModal)" in init
    assert "if (e.target === e.currentTarget) closeModal();" in init
    html = repo_src(HTML)
    # 🔴 ✕／取消要**同時**有 id（新 js 綁 closeModal）與 inline onclick（給舊快取 js）。
    #    CF 給 .js 4 小時、html 即時 —— 那一輪的舊 js 不會綁這兩個 id，只留 id 的話
    #    兩顆鈕完全按不動（只有點外面能關）。新 js 兩個都會跑，closeModal 照樣補送。
    #    跟 quote-f-terms 相容殼同一個到期條件：**發版滿一輪之後**兩者一起拿掉。
    assert html.count("getElementById('quote-modal').style.display='none'") == 2, \
        "✕ 與取消各要留一個 inline onclick 當舊快取 js 的退路（發版滿一輪後可刪）"


def test_project_name_typing_does_not_open_a_project_per_keystroke():
    """案名的 input 不進自動存（不然打第一個字就開一個案）——靠 change（離開欄位）才進來。"""
    init = js_func_body(js_code_only(repo_src(JS)), "export async function initCrmQuotesTab(")
    assert "e.target.id !== 'quote-f-project_name' && _fromForm(e.target)" in init
    assert "e.target.id === 'quote-f-project_name' && _editingId" in init, "存過之後改案名＝改那個殼案"
    # AI 分頁不算「動到表單」：對話框每打一個字都會冒泡到彈窗，跟著就是一發整張報價的
    # PUT（後端 _save_items 砍光重插），而那些字根本不是報價內容。
    assert "!el.closest('#quote-pane-ai')" in init


def test_locked_fields_after_draft_and_shell_rename():
    js = js_code_only(repo_src(JS))
    lock = js_func_body(js, "function _lockProjectFields()")
    for fid in ("quote-f-client_id", "quote-btn-new-client", "quote-f-link_project"):
        assert fid in lock, f"{fid}：草稿建好之後案子就定了（PUT 報價改不動案子）"
    assert "nameEl.disabled = !_autoShellProject" in lock, "只有自己剛建的殼案還能改名"
    rename = js_func_body(js, "async function _autoRenameShell()")
    assert "if (!shell || !name || name === shell.name) return;" in rename
    assert "method: 'PUT'" in rename and "/projects/" in rename
    reset = js_func_body(js, "function _autoReset(on)")
    assert "el.disabled = false" in reset, "下次開窗要解鎖"


def test_manual_save_and_autosave_share_one_payload():
    js = js_code_only(repo_src(JS))
    assert "function _buildPayload()" in js
    for fn in ("async function _autoCreateDraft()", "async function _autoFlush()", "async function saveQuotation()"):
        assert "_buildPayload()" in js_func_body(js, fn), fn


# ── 2/3. 大項目、子項目拖曳排序 ────────────────────────────────

def test_grip_is_the_only_draggable_thing():
    js = js_code_only(repo_src(JS))
    assert 'class="qi-grip" draggable="true"' in js
    # 整列 draggable 會搶掉輸入框的選字 —— 只有把手可以拖
    assert 'class="quote-item-edit-row" draggable' not in js
    assert 'class="quote-term-row" draggable' not in js
    assert ".qi-grip {" in repo_src(CSS)


def test_groups_and_items_reorder_by_drag():
    js = js_code_only(repo_src(JS))
    rows = js_func_body(js, "function _renderItemRows()")
    assert rows.count("_gripHtml(") == 2, "大項目一根、子項目一根"
    assert "_bindGrip(gEl.querySelector('.quote-group-edit-head .qi-grip'), { kind: 'group', gi })" in rows
    assert "_bindDropTarget(gEl, 'group'" in rows and "_moveInArray(_groups, d.gi, gi)" in rows
    assert "_bindGrip(row.querySelector('.qi-grip'), { kind: 'item', gi, i })" in rows
    assert "_bindDropTarget(row, 'item'" in rows
    assert "_moveInArray(g.items, d.i, i)" in rows, "同一組內換順序"
    assert "_groups[d.gi].items.splice(d.i, 1)" in rows, "也可以拖到別的大項目"
    assert rows.count("_autoTouch();") == 3, "拖完沒有 input 事件，要自己進自動存"

    drop = js_func_body(js, "function _bindDropTarget(el, kind, onDrop)")
    assert "_drag.kind !== kind" in drop, "落點只吃同一種（大項目／子項目／備註不會互相亂放）"
    assert "e.preventDefault()" in drop and "e.stopPropagation()" in drop


# ── 4. 備註一列一條 ───────────────────────────────────────────

def test_terms_are_one_row_each_numbered_and_sortable():
    html = repo_src(HTML)
    assert 'id="quote-terms-list"' in html and 'id="quote-btn-add-term"' in html
    # 整塊 textarea 退場，但**不能直接從 html 拿掉**：CF 給 .js 4 小時快取、html 是即時的，
    # 發版後會出現「新 html ＋ 舊 crm-quotes.js」，而舊的 openModal 直接
    # `getElementById('quote-f-terms').value` —— 元素不在＝TypeError＝報價彈窗打不開。
    # 留一個隱藏的相容殼一輪（reference_cloudflare_js_cache），新 js 不准碰它。
    assert '<textarea id="quote-f-terms" hidden' in html, (
        "舊快取 js 的相容殼要在，而且是隱藏的。"
        "🔴 到期條件：報價助理那批**發版滿一輪之後**（CF 的 4 小時 js 快取全部過期、"
        "沒有人再跑得到舊的 crm-quotes.js）就可以把它跟這條斷言一起刪掉 —— "
        "沒寫到期條件的相容殼會變成永久資產。")
    assert "'quote-f-terms'" not in js_code_only(repo_src(JS)), "新 js 不准再讀寫那個 textarea"

    js = js_code_only(repo_src(JS))
    rows = js_func_body(js, "function _renderTermRows()")
    assert '<span class="quote-term-no">${i + 1}.</span>' in rows, "每列前面有編號"
    assert "_gripHtml(" in rows and "_bindDropTarget(row, 'term'" in rows and "_moveInArray(_terms, d.i, i)" in rows
    assert "_terms[i] = e.target.value" in rows, "打字不重繪（不然游標被搶）"

    # DB 還是同一個 terms 文字欄（一行一條）；報價單模板本來就照行切成 <ol>
    assert "_terms.map(t => t.trim()).filter(Boolean).join('\\n')" in js
    assert "String(text || '').split('\\n')" in js
    assert "_termsText()" in js_func_body(js, "function _buildPayload()")
    assert "_setTerms(q.terms)" in js_func_body(js, "async function openModal(")
    assert "_termsText()" in js_func_body(js, "function _saveCurrentAsTemplate(name)")
    assert "{% for t in v.terms_lines %}<li>{{ t }}</li>{% endfor %}" in repo_src("templates/quotation_pdf.html")


# ── 5. 詳情面板合成一頁、編輯＝開彈窗 ──────────────────────────

def test_detail_is_one_page_and_edit_opens_the_modal():
    html = repo_src(HTML)
    assert 'id="quote-detail-tabs"' not in html, "報價資訊／項目明細合成一頁"
    assert 'id="quote-detail-items" class="hidden"' not in html
    assert 'id="quote-detail-items"' in html

    js = js_code_only(repo_src(JS))
    detail = js_func_body(js, "function renderDetail(")
    assert 'id="quote-btn-edit"' in detail
    assert "querySelector('#quote-btn-edit')?.addEventListener('click', () => quoteEdit(q.id))" in detail
    for gone in ("enableInlineEdit", "addEditButton", "_QUOTE_EDIT_FIELDS", "quote-detail-tabs"):
        assert gone not in js, f"{gone}：就地編欄位那套退場（它編不到項目明細，跟彈窗兩套規則）"
