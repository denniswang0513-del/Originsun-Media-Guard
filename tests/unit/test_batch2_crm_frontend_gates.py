# -*- coding: utf-8 -*-
"""RBAC 稽核第二批 —— CRM 分頁「看得到做不到」的按鈕要藏（docs/RBAC_AUDIT.md §3.3、§3.4、§4 第二批）。

後端照分頁鑰匙放行之後，仍留管理員的只剩：刪除（客戶／員工／報價／雜支）、三支 CSV 匯入、
根目錄設定、歸檔 folders／scan。錢流（發票／請款／預支款）寫入要 crm_invoices＋money_view；
官網上架編輯要 website_admin；影像紀錄寫入要 media_log；派工寫入要 crm_projects。

這裡釘的是**畫面上那顆鈕的條件**（源碼掃描），不是後端守衛 —— 守衛在 test_batch2_crm_guards.py。
三個原則一起釘：
  1. 藏鈕的判斷只認 `hasModule()`／`canSeeMoney()`／`window._accessLevel >= 3`，跟後端同一把尺。
  2. tabs/proposals/ 的共用元件（住在公開頁的 import 封閉範圍）**不 import crm-utils**，旗標由呼叫端注入，
     而且只有明確給 false 才藏（Cloudflare 給 .js 4 小時快取：新元件配舊呼叫端不能把管理員的鈕藏掉）。
  3. 403 一律同一句「權限不足：需要「X」權限，請管理員在使用者管理開通」，經 `_U.permDeniedMsg?.(…)`
     取用（named import 在舊快取的 crm-utils 上會炸整頁）。
"""
import re

import pytest

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

CRM = "frontend/tabs/crm/"
PROP = "frontend/tabs/proposals/"
PERM_RE = re.compile(r"權限不足：需要「[^」]+」權限，請管理員在使用者管理開通")


def _js(rel):
    return js_code_only(repo_src(rel))


# ── 0. 工具：permDeniedMsg 與 e.status ─────────────────────────────

def test_crm_utils_exposes_one_perm_message_and_status_on_errors():
    js = _js(CRM + "crm-utils.js")
    fn = js_func_body(js, "export function permDeniedMsg(label, e) {")
    assert "e.status !== 403" in fn and "return null" in fn, "帶 e 時只在 403 才回訊息，其他錯誤讓呼叫端用自己的字"
    assert PERM_RE.search(fn.replace("${label}", "X")), "訊息格式漂了"
    # 呼叫端分得出「沒權限」還是「壞了」的前提：crmFetch 丟出來的 Error 要帶狀態碼
    do = js_func_body(js, "async function _doFetch(url, opts) {")
    assert "e.status = res.status" in do


@pytest.mark.parametrize("rel", [
    CRM + "crm.js", CRM + "crm-staff.js", CRM + "crm-quotes.js", CRM + "crm-projects-quotes.js",
    CRM + "crm-projects.js", CRM + "crm-projects-finance.js", CRM + "crm-projects-invoices.js",
    CRM + "crm-projects-media.js",
])
def test_perm_message_is_reached_through_the_namespace_only(rel):
    """🔴 `import { permDeniedMsg }` 在舊快取的 crm-utils 上是連結期 SyntaxError —— 整個分頁開不起來。"""
    js = _js(rel)
    assert "import * as _U from './crm-utils.js'" in js, rel
    assert "_U.permDeniedMsg?.(" in js, f"{rel} 沒用到一致的 403 訊息"
    for m in re.finditer(r"import \{([^}]*)\} from './crm-utils.js'", js):
        assert "permDeniedMsg" not in m.group(1), f"{rel} named import 了 permDeniedMsg"


def test_the_proposals_components_spell_the_same_sentence():
    """tabs/proposals 的元件 import 不到 crm-utils，字面複本要跟 permDeniedMsg 同一句。"""
    for rel in (PROP + "staff-view.js", PROP + "delivery-view.js"):
        js = _js(rel)
        assert "crm-utils" not in js, f"{rel} 不准 import tabs/crm（公開頁的 import 封閉範圍）"
        assert PERM_RE.search(js), f"{rel} 的 403 訊息跟 permDeniedMsg 不是同一句"
        assert "e.status === 403" in js


# ── 1. 刪除／匯入／根目錄只在 Lv3 畫 ────────────────────────────────

@pytest.mark.parametrize("rel,fn_header,handler", [
    (CRM + "crm.js", "function renderList() {", "_crmDeleteClient"),
    (CRM + "crm-staff.js", "function renderList() {", "_staffDelete"),
    (CRM + "crm-quotes.js", "function renderList() {", "_quoteDelete"),
])
def test_list_kebab_only_offers_delete_to_admins(rel, fn_header, handler):
    js = _js(rel)
    assert "const _isAdmin = () => (window._accessLevel || 0) >= 3;" in js, rel
    fn = js_func_body(js, fn_header)
    assert f"onDelete: _isAdmin() ? '{handler}' : undefined" in fn, f"{rel} 的 ⋮ 刪除沒依管理員藏"
    # 全域 handler 仍在（kebabMenuHtml 的 onclick 字串靠它）
    assert f"window.{handler} = " in js


def test_project_quote_detail_delete_is_admin_only():
    js = _js(CRM + "crm-projects-quotes.js")
    fn = js_func_body(js, "async function _renderQuoteDetail(quoteId) {")
    assert "${_isAdmin() ? `<button" in fn and "window._pqDelete('${q.id}')" in fn
    assert "window._pqDelete = async" in js


@pytest.mark.parametrize("rel,btn_id", [
    (CRM + "crm.html", "crm-btn-import"),
    (CRM + "crm-staff.html", "staff-btn-import"),
    (CRM + "crm-projects.html", "proj-btn-import"),
])
def test_csv_import_buttons_are_admin_only_in_markup(rel, btn_id):
    """既有作法：`.admin-only` 由 auth-state._applyAuthState 統一開關，分頁載入後 app.js 會再套一次。
    預設 display:none —— 不然非管理員在 init 抓資料那幾百毫秒會先看到它。"""
    html = repo_src(rel)
    m = re.search(rf'<button id="{btn_id}"[^>]*>', html)
    assert m, btn_id
    assert "admin-only" in m.group(0) and "display:none" in m.group(0), m.group(0)


def test_proposal_assets_root_gear_is_admin_only():
    js = _js(CRM + "crm-projects-plan.js")
    fn = js_func_body(js, "function _mountAssetsCard(projectId, pid, host, d, pins) {")
    assert "(window._accessLevel || 0) >= 3 ? '<button id=\"pp-cfg\"" in fn
    assert "box.querySelector('#pp-cfg')?.addEventListener(" in fn, "沒那顆鈕時不能 null.addEventListener"


def test_expense_row_delete_is_admin_only_and_claim_needs_invoice_keys():
    js = _js(CRM + "crm-projects-cost.js")
    assert "const _isAdmin = () => (window._accessLevel || 0) >= 3;" in js
    assert "const _canInvoice = () => hasModule('crm_invoices') && canSeeMoney();" in js
    fn = js_func_body(js, "function _renderCostLines(grouped, expenses, financialSummary) {")
    assert "${_isAdmin() ? `" in fn and "window._projDeleteExpense('${e.id}')" in fn, "雜支 ✕ 沒依管理員藏"
    # 雜支「請款」開的是請款單 —— 錢流那把（零用金列那條 `if (e.staff_id) return '';` 另有測試釘著，別併行）
    claim = js_func_body(fn, "const claimCell = (e) => {")
    assert "if (!_canInvoice()) return '';" in claim


# ── 2. 專案頁錢流按鈕：crm_invoices ＋ money_view ─────────────────────

def test_pay_tab_gates_every_money_action_and_tells_403_from_empty():
    js = _js(CRM + "crm-projects-pay.js")
    assert "const _canInvoice = () => hasModule('crm_invoices') && canSeeMoney();" in js
    # _q 不再把 403 吞成「沒有發票」
    assert "const _q = (path) => _fetch(path).catch((e) => (e && e.status === 403 ? DENIED : null));" in js
    load = js_func_body(js, "async function _load(projectId, { full = true } = {}) {")
    assert "[inv, pays, adv].some((v) => v === DENIED)" in load and "denied }" in load
    assert "沒有帳務權限（需要財務管理＋金額檢視）" in js
    tab = js_func_body(js, "export async function loadPayTab(projectId) {")
    assert "const canInv = _canInvoice() && !d.denied;" in tab
    for btn in ("window._projPay.linkInvoice()", "window._projInv.create()",
                "window._costCreatePayment('',0,'委外','應付款')", "window._costCreateAdvance()"):
        seg = tab[:tab.index(btn)]
        assert "${canInv ? `" in seg[-400:], f"{btn} 沒包在 canInv 裡"
    assert "${d.denied ? NO_INVOICE_HTML : _recvHtml(s, money, canInv)}" in tab
    assert "${d.denied ? NO_INVOICE_HTML : _payHtml(s, d.adv, money, canInv)}" in tab
    recv = js_func_body(js, "function _recvHtml(s, money, canInv = true) {")
    for btn in ("markCollected", "_projInv.del", "doLink"):
        assert btn in recv
    assert recv.count("${canInv ? `") == 2, "收款列的 標已收款／刪除／取消連結 要包在 canInv 裡"
    pay = js_func_body(js, "function _payHtml(s, adv, money, canInv = true) {")
    assert ": (canInv ? `${payBtn(g, items, '應付款', '請款')}" in pay
    assert "a.is_settled || !canInv ? ''" in pay
    hints = js_func_body(js, "function _hints(list, canInv = true) {")
    assert "h.act !== 'invoice'" in hints, "下一步的「開發票」是寫入動作"
    banner = js_func_body(js, "export async function loadClosingBanner(projectId, host) {")
    assert "if (d.denied)" in banner and "沒有帳務權限" in banner
    closing = js_func_body(js, "export async function confirmClosing(projectId) {")
    assert "if (d.denied) return true;" in closing, "沒帳務權限不能擋結案"


def test_finance_handlers_guard_at_the_entry_not_only_at_the_button():
    """window.* 是全域入口（收付款分頁、預算結算的雜支列、舊快取的分頁都會叫）——入口自己也要擋。"""
    js = _js(CRM + "crm-projects-finance.js")
    assert "const _canInvoice = () => hasModule('crm_invoices') && canSeeMoney();" in js
    for header in ("window._costCreateAdvance = function() {",
                   "window._costCreatePayment = function(payeeName, amount, summary, status, advanced, opts) {",
                   "window._advDeleteAdvance = async function(advanceId, payeeName) {",
                   "window._costPayMark = async function(id, paid, onDone) {",
                   "window._costPayWithdraw = async function(id, summary, onDone) {"):
        fn = js_func_body(js, header)
        assert "if (!_canInvoice()) { _denyInvoice(); return; }" in fn, header
    btns = js_func_body(js, "window._costPayBtns = function(p, onDoneName) {")
    assert "if (!_canInvoice()) return '';" in btns


def test_invoice_panel_gates_create_delete_and_meta_edits():
    js = _js(CRM + "crm-projects-invoices.js")
    assert "const _canInvoice = () => hasModule('crm_invoices') && canSeeMoney();" in js
    for header in ("_P.create = function _openCreate() {", "_P.del = async (id) => {"):
        assert "if (!_canInvoice()) { _denyInvoice(); return; }" in js_func_body(js, header), header
    meta = js_func_body(js, "_P.setMeta = async (id, field, el) => {")
    assert "if (!_canInvoice()) { _denyInvoice(); el.value = inv[field] || ''; return; }" in meta
    tab = js_func_body(js, "function _renderTab() {")
    assert "${_canInvoice() ? `<button" in tab and "window._projInv.create()" in tab
    lst = js_func_body(js, "function _listHtml() {")
    assert "const canInv = _canInvoice();" in lst
    assert lst.count("${canInv ? `") == 2, "品項輸入格與刪除鈕各包一次"
    cell = js_func_body(js, "function _applicantCell(inv) {")
    assert "if (!_canInvoice()) return" in cell, "申請人下拉沒權限時要退成純文字"


# ── 3. 完稿結案：website_admin ＋ 歸檔 folders／scan 管理員 ─────────────

def test_delivery_view_hides_the_editor_only_when_told_so():
    js = _js(PROP + "delivery-view.js")
    fn = js_func_body(js, "export async function loadDeliveryTab(projectId, opts = {}) {")
    # 旗標檢查要在 works／edit-token 之前 —— 那兩支對非 website_admin 都是 403
    guard = fn.index("opts.canEditShowcase === false")
    assert guard < fn.index("/works") and guard < fn.index("generate-edit-token")
    assert "_mountArchive(container.querySelector('.delivery-archive'), projectId, _fetch, opts)" in fn
    mount = js_func_body(js, "async function _mountArchive(host, projectId, fetcher, opts = {}) {")
    assert "canManageFolders: opts.canManageFolders" in mount
    # 只有明確 false 才藏（見檔頭：舊呼叫端不帶旗標時不能把管理員的編輯器藏掉）
    assert "opts.canEditShowcase === false" in fn and "!opts.canEditShowcase" not in fn


def test_archive_card_folder_actions_are_opt_out():
    js = _js(PROP + "archive-card.js")
    fn = js_func_body(js, "export async function renderArchiveCard(host, projectId, crmFetch, opts = {}) {")
    assert "const canFolders = opts.canManageFolders !== false;" in fn
    assert "${canFolders ? `<button" in fn and 'data-act="folders"' in fn and 'data-act="scan"' in fn
    assert "?.addEventListener('click', () => folderAction('/archive/folders'" in fn
    assert "?.addEventListener('click', () => folderAction('/archive/scan'" in fn


def test_crm_caller_injects_website_and_admin_flags():
    js = _js(CRM + "crm-projects.js")
    fn = js_func_body(js, "const _openDelivery = (pid) => {")
    assert "canEditShowcase: hasModule('website_admin')" in fn
    assert "canManageFolders: (window._accessLevel || 0) >= 3" in fn


# ── 4. 影像紀錄：寫入要 media_log ───────────────────────────────────

def test_media_log_write_buttons_need_the_media_log_key():
    js = _js(CRM + "crm-projects-media.js")
    assert "const _canWrite = () => hasModule('media_log');" in js
    render = js_func_body(js, "function _render() {")
    assert "const canW = _canWrite();" in render
    for gated in ('pm-root-save', 'pm-cat-add', 'pm-cat-save', 'pm-reset'):
        i = render.index(f'id="{gated}"')
        # 分類那一整列（輸入格＋新增＋儲存分類）是包在同一個 canW 裡的，所以視窗要放到整列的長度
        assert "canW ?" in render[max(0, i - 420):i], f"{gated} 沒依 media_log 藏"
    assert "${canW ? '' : ' readonly'}" in render and "${canW ? '' : ' disabled'}" in render
    for el in ('pm-root-save', 'pm-cat-add', 'pm-cat-new', 'pm-cat-save', 'pm-reset'):
        assert f"document.getElementById('{el}')?.addEventListener(" in render, f"{el} 的監聽沒 ?."
    item = js_func_body(js, "function _itemHtml(f, i) {")
    assert "${_canWrite() ? '<button data-act=\"del\"" in item
    chips = js_func_body(js, "function _renderChips() {")
    assert "${canW ? `<button data-ci=" in chips
    # 上傳走公開 token 端點，不在這把鑰匙底下 —— 別順手藏掉
    assert "document.getElementById('pm-pick').addEventListener(" in render


# ── 5. 派工：crm_projects；片庫「解除」維持 ───────────────────────────

def test_staff_view_add_and_remove_are_opt_out_and_crm_callers_pass_the_key():
    js = _js(PROP + "staff-view.js")
    add = js_func_body(js, "function _wireAdd(host, projectId, opts) {")
    assert add.lstrip().splitlines()[1].strip().startswith("if (opts.canAssign === false) return;")
    load = js_func_body(js, "export async function loadProjectStaff(projectId, opts = {}) {")
    assert "const canAssign = opts.canAssign !== false;" in load
    assert '${canAssign ? `<button class="pstaff-del"' in load
    for rel in (CRM + "crm.js", CRM + "crm-projects-finance.js"):
        assert "canAssign: hasModule('crm_projects')" in _js(rel), f"{rel} 沒把 crm_projects 注入派工視圖"


def test_reference_unlink_button_stays_for_the_references_family():
    """後端 DELETE /references/links/{id} 改成 references 家族放行 —— 前端那顆「解除」不動。"""
    js = _js(PROP + "reference-page.js")
    assert 'class="rfc-btn danger rfc-link-del"' in js
    assert "_accessLevel" not in js and "hasModule" not in js
