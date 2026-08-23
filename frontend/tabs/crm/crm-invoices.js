/**
 * crm-invoices.js — 帳務管理 Tab
 */
import { crmFetch as _fetch, crmCacheFetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, enumIndex, invoiceAmounts } from './crm-utils.js';
// 兩本帳（公司實體）— docs/LEDGER_ENTITY_PLAN.md §5。帳本由頁面隱形 pin：
// 財務 tab＝'parent'（預設）、/my-ledger.html＝'mine'（該頁在載入財務模組前設
// window._finEntity）。無使用者可見的帳本選單（單一 tab 單一帳本）。query 一律帶
// pin 值；payload 只在 'mine' 才帶 entity:'mine'（後端 None 語意：建立落 parent、
// 更新維持既有值 —— 不洗欄位）。pin 用財務模組那份，不在這裡複寫。
import { finEntity as _pinEntity } from '../finance/fin-utils.js';
// 🔴 複製一律走 shared 那份：同事多半從 http://192.168.1.x 連進來（非安全內容），
// navigator.clipboard 根本不存在 —— 那支有 textarea+execCommand 的 fallback。
import { copyText } from '../../js/shared/utils.js';

let _invoices = [];
let _projects = [];
let _clients = [];
let _selectedId = null;
let _editingId = null;
let _editingPaymentStatus = null;
let _editingPaymentType = null;
let _filters = { q: '', issue_status: '', category: '', project_id: '' };
let _csvFile = null;

// ── 代開手續費率 ────────────────────────────────────────────
//
// 🔴 存伺服器不存 localStorage（比照下面的申請人名單）。費率是公司政策 ——
// 每台電腦各一份的話，同一張發票在不同人手上會算出不同的應匯金額，而那個數字
// 會變成一張真的要匯出去的應付款。而且後端本來另外寫死 0.92：外部代開畫面說
// 10%、實際產生的應付款卻是 8%，少匯給對方 2%（2026-08-21 /simplify 抓到）。
// 這裡只是顯示用的快取；真正算錢的是 core/finance_logic.passthrough_commission。
let _fees = { 內部代開: 8, 外部代開: 10 };

async function _loadFees() {
    try {
        const r = await _fetch('/invoice-fee-rates');
        if (r && r.rates) _fees = r.rates;
    } catch (_) { /* 讀不到就用預設，跟後端的預設同一組 */ }
    return _fees;
}

const _feeOf = (cat) => Number(_fees[cat] ?? 8);
const _commissionOf = (total, cat) =>
    total ? Math.round(total * (1 - _feeOf(cat) / 100)) : null;

// ── Applicant list (localStorage) ───────────────────────────
// 申請人清單（誰能被選為這張發票的申請人）。
//
// 🔴 存在伺服器（settings 的 invoice_applicants），不是瀏覽器的 localStorage ——
// 那是每台電腦各自一份，在辦公室設好、回家開就只剩「—」，而且沒人知道少了誰
// （2026-08-20 owner 實際踩到）。這是全公司共用的一份名單。
// 沒設定過時後端會用發票資料裡實際用過的人當預設，所以永遠不會是空的；
// 用⚙️管理視窗改過之後，就以那份為準（可以刪掉不再開票的人）。
let _applicants = [];

async function _loadApplicants() {
    try {
        _applicants = (await _fetch('/invoice-applicants')).applicants || [];
    } catch (_) { _applicants = []; }
    return _applicants;
}

async function _saveApplicants(list) {
    await _fetch('/invoice-applicants',
                 { method: 'PUT', body: JSON.stringify({ applicants: list }) });
    _applicants = list;
}

function _populateApplicantSelect(current) {
    const sel = document.getElementById('inv-f-applicant');
    if (!sel) return;
    const list = _applicants;
    sel.innerHTML = '<option value="">— 選擇 —</option>' +
        list.map(n => `<option value="${_esc(n)}"${n === current ? ' selected' : ''}>${_esc(n)}</option>`).join('');
}

function _renderApplicantList() {
    const container = document.getElementById('inv-applicant-list');
    if (!container) return;
    if (_applicants.length === 0) {
        container.innerHTML = '<div style="color:#6b7280;font-size:12px;">'
            + '尚無申請人（清空後會退回用發票資料推導）</div>';
        return;
    }
    container.innerHTML = _applicants.map((n, i) =>
        `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;">` +
        `<span style="flex:1;font-size:13px;">${_esc(n)}</span>` +
        `<button type="button" class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._invRemoveApplicant(${i})">刪除</button>` +
        `</div>`
    ).join('');
}

// ── Data ─────────────────────────────────────────────────────

async function loadInvoices() {
    const params = new URLSearchParams();
    if (_filters.q)            params.set('q', _filters.q);
    if (_filters.issue_status) params.set('issue_status', _filters.issue_status);
    if (_filters.category)     params.set('category', _filters.category);
    if (_filters.project_id)   params.set('project_id', _filters.project_id);
    params.set('entity', _pinEntity());
    try {
        const data = await _fetch(`/invoices?${params}`);
        _invoices = data.invoices || [];
    } catch (_) { _invoices = []; }
    renderList();
}

async function loadProjects() {
    try { _projects = (await crmCacheFetch('projects', '/projects')).projects || []; } catch(_) { _projects = []; }
    _populateProjectFilter();
}

async function loadClients() {
    try { _clients = (await crmCacheFetch('clients', '/clients')).clients || []; } catch(_) { _clients = []; }
}

// ── Rendering ────────────────────────────────────────────────

/** 開立狀態 badge。class 是語意 token（同 _payBadge）—— 中文只出現在顯示的字。 */
/** 工具列的專案篩選下拉。重畫時保留目前選的值 —— 不然載入一次就跳回「全部專案」。 */
function _populateProjectFilter() {
    const sel = document.getElementById('inv-filter-project');
    if (!sel) return;
    const cur = sel.value;
    const opt = (p) => '<option value="' + p.id + '"'
        + (p.id === cur ? ' selected' : '') + '>' + _esc(p.name) + '</option>';
    sel.innerHTML = '<option value="">全部專案</option>' + _projects.map(opt).join('');
}


function _statusBadge(status) {
    const cls = status === '作廢' ? 'void'
        : status === '已開立' ? 'collected' : 'unpaid';
    return `<span class="crm-badge crm-pay-badge-${cls}">${_esc(status || '開立中')}</span>`;
}

// 開立中 → 已開立 → 作廢:工作流順序,asc 把待處理(開立中)排前面
const _INV_STATUS_ORDER = ['開立中', '已開立', '作廢'];
// 款項狀態排序：待處理(未收/未付)在前、空白墊底（空白＝來源沒填，不是一種進度）
// 代開發票的三段（owner 2026-08-21 定名，後端 crm/finance.py 同一組字）：
//   未收款 ──客戶匯錢進來──▶ 待撥款 ──應付帳款把請款單付掉──▶ 已撥款
// 代開不用「已收款」是因為收到錢只是換公司欠代開人 —— 「待撥款」一眼就看得出
// 還有一筆錢要出去。跟請款單的「已付款」（我們真的付掉一筆費用）是兩件事。
const INV_PENDING_REMIT = '待撥款';
const INV_REMITTED = '已撥款';
const _INV_PAY_ORDER = ['未收款', '未付款', INV_PENDING_REMIT, '已收款',
                        INV_REMITTED, '作廢', ''];

/** 款項狀態 badge。🔴 空白就顯示空白 —— 舊寫法 `payment_status || '未收款'` 會把
 *  「來源沒填」畫成「未收款」，等於替沒表態的資料表態（匯入歷史發票時有 21 張）。 */
function _payBadge(status) {
    const s = (status || '').trim();
    if (!s) return '<span class="crm-badge crm-pay-badge-unset">未設定</span>';
    // 三段各一個顏色 —— 共用綠色的話一整欄看起來都一樣，分不出哪些還沒撥款。
    // 待撥款＝紫（還有一筆錢要出去）、已撥款＝藍（收尾了）、已收款＝綠。
    // 🔴 class 用語意 token 不用中文狀態字：拿中文當 class 名的話，改一次用詞
    // 就得連 CSS 一起改（已轉撥→已撥款那次就是），而顯示的字只該住在這一行。
    const cls = s === INV_REMITTED ? 'remitted'
        : s === INV_PENDING_REMIT ? 'pending-remit'
        : s === '已收款' ? 'collected' : s === '作廢' ? 'void' : 'unpaid';
    return `<span class="crm-badge crm-pay-badge-${cls}">${_esc(s)}</span>`;
}
const _sorter = createSortable({
    storageKey: 'crm_invoices_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'inv-list-panel',
    onChange: () => renderList(),
    getters: {
        date:      i => i.invoice_date || '',
        applicant: i => (i.applicant || '').toLowerCase(),
        title:    i => (i.title || '').toLowerCase(),
        amount:   i => i.amount_total || 0,
        company:  i => (i.company_name || '').toLowerCase(),
        tax_id:   i => (i.tax_id || '').toLowerCase(),
        item:     i => (i.item_type || '').toLowerCase(),
        category: i => (i.category || '').toLowerCase(),
        kind:     i => (i.invoice_kind || '').toLowerCase(),
        pay:      i => enumIndex(_INV_PAY_ORDER, (i.payment_status || '').trim(), ''),
        status:   i => enumIndex(_INV_STATUS_ORDER, i.issue_status, '開立中'),
    },
});

const _INV_CATEGORIES = ['專案', '內部代開', '外部代開'];
const _INV_KINDS = ['電子發票', '紙本發票'];

/** 發票種類 badge。紙本但沒填收件資訊 → 標成待補（53 筆歷史紙本發票全都沒有收件人，
 *  這個記號就是拿來一眼掃出哪些要補的）。點一下在電子⇄紙本之間切換。 */
function _kindBadge(inv) {
    const k = (inv.invoice_kind || '').trim();
    const known = _INV_KINDS.includes(k);
    const needRecipient = k === '紙本發票' && !(inv.recipient || '').trim();
    const cls = !k ? '未設定' : !known ? '異常' : k === '紙本發票' ? '紙本' : '電子';
    const label = !k ? '未設定' : known ? k.replace('發票', '') : k;
    const title = !known && k ? `不是合法的發票種類（${k}）—— 點一下改成電子/紙本`
        : needRecipient ? '紙本發票，但還沒填收件資訊 —— 點該列到詳情補'
            : '點一下切換 電子 ⇄ 紙本';
    return `<span class="crm-badge inv-kind-badge inv-kind-${cls}${needRecipient ? ' need-recipient' : ''}"
                  title="${_esc(title)}"
                  onclick="event.stopPropagation();window._invToggleKind('${inv.id}')">${_esc(label)}</span>`;
}

/** 在電子⇄紙本之間切換。只有兩個值、按一下就換回來，所以不加確認；
 *  但改成紙本時提醒去補收件資訊（後端 PUT 不碰 file_url，見 _to_invoice_dict 註解）。 */
window._invToggleKind = async function (id) {
    const inv = _invoices.find(i => i.id === id);
    if (!inv) return;
    const next = (inv.invoice_kind || '').trim() === '紙本發票' ? '電子發票' : '紙本發票';
    try {
        const full = await _fetch('/invoices/' + id);
        await _fetch('/invoices/' + id, {
            method: 'PUT',
            // 🔴 invoice_date 原樣帶回完整 ISO，不要自己 substring(0,10)：
            // 若該列是舊的台北午夜資料（'2024-01-01T16:00:00+00:00'），前 10 碼是
            // UTC 那天、比實際少一天。後端 _parse_shoot_date 會把完整 ISO 轉成台北
            // 日期再存成 UTC 午夜，兩種慣例都對。
            body: JSON.stringify({ ...full, invoice_kind: next }),
        });
        await loadInvoices();
        if (_selectedId === id) renderDetail(await _fetch('/invoices/' + id));
    } catch (e) { alert('切換失敗：' + e.message); }
};

// 金額欄輸入的是未稅還是含稅 —— 來源有時記未稅、有時記含稅，所以是可切換的。
// 記在 localStorage：同一個人的來源資料通常一致，不該每開一次都重選。
const _QA_MODE_KEY = 'inv_qa_amount_mode';
const _qaMode = () => (localStorage.getItem(_QA_MODE_KEY) === 'total' ? 'total' : 'ex');
const _qaModeLabel = (m) => (m === 'total' ? '含稅' : '未稅');

/** 切換未稅／含稅。刻意**不動已輸入的數字** —— 切換的是「這個數字是什麼」，
 *  不是把它換算掉；打錯方向時按一下就對，不用重打。 */
window._invQuickToggleMode = function () {
    const next = _qaMode() === 'ex' ? 'total' : 'ex';
    localStorage.setItem(_QA_MODE_KEY, next);
    const btn = document.getElementById('inv-qa-mode');
    if (btn) btn.textContent = _qaModeLabel(next);
    const inp = document.getElementById('inv-qa-amt');
    if (inp) inp.placeholder = _qaModeLabel(next) + '價';
    window._invQuickCalc();
};

/** 就地新增列 —— 日常登記的正路（modal 留給要填發票號/紙本收件資訊/自訂代開匯款的情況）。
 *
 * 欄位與列表欄目一一對齊，所以打字的位置就是這筆資料之後會出現的位置。
 * 金額欄收的是**未稅價**（與 modal 同一個輸入方向，避免兩條路各自進位造成尾差），
 * 旁邊即時顯示推算出來的含稅價。Enter 直接送出，送完焦點回名稱欄可以連續登記。 */
function _quickAddRow() {
    const opts = (arr, sel) => arr.map(v =>
        `<option${v === sel ? ' selected' : ''}>${_esc(v)}</option>`).join('');
    const clientOpts = '<option value="">—</option>' + _clients.map(c =>
        `<option value="${_esc(c.short_name)}">${_esc(c.short_name)}</option>`).join('');
    // 🔴 刻意**不**綁 Enter 送出 —— owner 2026-08-20：這排格子就在列表最上面，
    // 打字時很容易誤按，一按就直接寫進一筆。要新增就按右邊那顆 ＋。
    const mode = _qaMode();
    return `
      <div class="inv-qa-wrap">
      <div class="crm-row inv-qa">
        <div class="crm-row-date"><input id="inv-qa-date" type="date" value="${_todayStr()}"></div>
        <div><select id="inv-qa-applicant">${
            ['<option value="">—</option>'].concat(_applicants.map(n =>
                `<option value="${_esc(n)}">${_esc(n)}</option>`)).join('')
        }</select></div>
        <div class="crm-row-name"><input id="inv-qa-title" placeholder="＋ 名稱（填完按右側 ＋）"
               title="填好名稱後按這一列最右邊的 ＋ 新增；其餘欄位可留白，之後點該列補齊"></div>
        <div class="crm-row-amount">
          <div class="inv-qa-amt">
            <button type="button" id="inv-qa-mode" class="inv-qa-mode"
                    title="切換：這格輸入的是未稅價還是含稅價（按一下換，數字不變）"
                    onclick="window._invQuickToggleMode()">${_qaModeLabel(mode)}</button>
            <input id="inv-qa-amt" type="number" min="0" placeholder="${_qaModeLabel(mode)}價"
                   oninput="window._invQuickCalc()">
          </div>
          <div id="inv-qa-conv" class="inv-qa-hint"></div>
        </div>
        <div class="crm-row-client">
          <select id="inv-qa-company" onchange="window._invQuickCalc()">${clientOpts}</select>
        </div>
        <div><span id="inv-qa-taxid" class="inv-qa-hint">—</span></div>
        <div><input id="inv-qa-item" placeholder="品項"></div>
        <div><select id="inv-qa-cat" onchange="window._invQuickCalc()">${opts(_INV_CATEGORIES, '專案')}</select></div>
        <div><select id="inv-qa-kind" onchange="window._invQuickKind()">${
            _INV_KINDS.map(v => `<option value="${_esc(v)}"${v === '電子發票' ? ' selected' : ''}>${_esc(v.replace('發票', ''))}</option>`).join('')
        }</select></div>
        <div><select id="inv-qa-pay">${opts(['未收款', '已收款', INV_PENDING_REMIT, INV_REMITTED, '作廢'], '未收款')}</select></div>
        <div><select id="inv-qa-iss">${opts(['開立中', '已開立', '作廢'], '開立中')}</select></div>
        <span class="crm-kebab-wrap">
          <button class="crm-btn crm-btn-primary crm-btn-sm inv-qa-btn"
                  title="新增這筆發票" onclick="window._invQuickAdd()">＋</button>
        </span>
      </div>
      <!-- 紙本發票才要的收件資訊：選了紙本才展開，平常不佔版面 -->
      <div class="crm-row inv-qa inv-qa-paper" id="inv-qa-paper" style="display:none;">
        <div class="inv-qa-paper-label">紙本寄送</div>
        <input id="inv-qa-recipient" placeholder="收件人">
        <input id="inv-qa-recipient_phone" placeholder="收件電話">
        <input id="inv-qa-recipient_address" placeholder="收件地址">
      </div>
      </div>`;
}

/** 發票種類切到紙本 → 展開收件資訊那列（電子發票不需要，平常不佔版面）。 */
window._invQuickKind = function () {
    const paper = document.getElementById('inv-qa-kind')?.value === '紙本發票';
    const row = document.getElementById('inv-qa-paper');
    if (row) row.style.display = paper ? '' : 'none';
    if (paper) document.getElementById('inv-qa-recipient')?.focus();
};

/** 就地新增列的即時推算：換算另一邊的金額 + 統編帶出（與送出時同一支 _deriveInvoice）。 */
window._invQuickCalc = function () {
    const mode = _qaMode();
    const preview = _deriveInvoice({
        company_name: document.getElementById('inv-qa-company')?.value || '',
        category: document.getElementById('inv-qa-cat')?.value || '專案',
    }, { amount: document.getElementById('inv-qa-amt')?.value, mode });
    const cEl = document.getElementById('inv-qa-conv');
    // 提示永遠顯示「你沒在打的那一邊」——打未稅就給含稅，打含稅就給未稅
    if (cEl) {
        cEl.textContent = preview.amount_total
            ? (mode === 'total' ? '未稅 $' + _fmtNum(preview.amount_ex_tax)
                                : '含稅 $' + _fmtNum(preview.amount_total))
            : '';
    }
    const xEl = document.getElementById('inv-qa-taxid');
    if (xEl) xEl.textContent = preview.tax_id || '—';
};

window._invQuickAdd = async function () {
    const val = (id) => (document.getElementById(id)?.value || '').trim();
    const title = val('inv-qa-title');
    if (!title) { document.getElementById('inv-qa-title')?.focus(); return; }   // 空列不送

    const issue = val('inv-qa-iss');
    const payload = _deriveInvoice({
        title,
        invoice_date: val('inv-qa-date') || null,
        company_name: val('inv-qa-company'),
        item_type: val('inv-qa-item'),
        applicant: val('inv-qa-applicant'),
        category: val('inv-qa-cat') || '專案',
        invoice_kind: val('inv-qa-kind') || '電子發票',
        // 紙本才有收件資訊；選電子時那列是隱藏的，讀到的是空字串，正好不覆寫
        recipient: val('inv-qa-recipient'),
        recipient_phone: val('inv-qa-recipient_phone'),
        recipient_address: val('inv-qa-recipient_address'),
        issue_status: issue,
        // 作廢的發票款項狀態一律作廢（與 modal / inline 編輯同一條規則）
        payment_status: issue === '作廢' ? '作廢' : val('inv-qa-pay'),
        // 方向由後端從狀態推（core/finance_logic.invoice_direction）——
        // 前端本來自己判 `=== 已撥款 ? 付款 : 收款`，漏掉「待撥款」那個也是付款
        // 方向的狀態，於是那張代開發票會被當成收款、跑進應收帳款
    }, { amount: val('inv-qa-amt'), mode: _qaMode() });
    if (_pinEntity() === 'mine') payload.entity = 'mine';   // 帳本 pin，parent 不送

    const btn = document.querySelector('.inv-qa-btn');
    if (btn) btn.disabled = true;
    try {
        await _fetch('/invoices', { method: 'POST', body: JSON.stringify(payload) });
        await loadInvoices();
        // 重載後焦點回名稱欄 —— 連續登記（打字、Enter、打字、Enter）不用重新點
        document.getElementById('inv-qa-title')?.focus();
    } catch (e) {
        alert('新增失敗：' + e.message);
        if (btn) btn.disabled = false;
    }
};

function renderList() {
    const body = document.getElementById('inv-list-body');
    if (!body) return;
    _sorter.attach();
    if (_invoices.length === 0) {
        body.innerHTML = _quickAddRow()
            + `<div class="crm-empty">尚無發票${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    body.innerHTML = _quickAddRow() + _sorter.sorted(_invoices).map(inv => `
        <div class="crm-row${inv.id === _selectedId ? ' selected' : ''}" onclick="window._invSelect('${inv.id}')">
            <div class="crm-row-date">${inv.invoice_date ? inv.invoice_date.substring(0, 10) : '—'}</div>
            <div title="${_esc(inv.applicant)}">${_esc(inv.applicant)}</div>
            <!-- 名稱後接專案：清單 11 欄本來沒有一欄看得到「這張是哪個案子的」，
                 從專案頁開的票綁好了也看不出來（owner 2026-08-23）。名稱欄是彈性欄，
                 接在後面不動任何 CSS 寬度。 -->
            <div class="crm-row-name" title="${_esc(inv.title)}${inv.project_name ? '　（' + _esc(inv.project_name) + '）' : ''}">${_esc(inv.title)}${
                inv.project_name
                    ? `<span style="color:#6b7280;font-size:11px;"> · ${_esc(inv.project_name)}</span>`
                    : ''}</div>
            <div class="crm-row-amount">$${_fmtNum(inv.amount_total)}</div>
            <div class="crm-row-client" title="${_esc(inv.company_name)}">${_esc(inv.company_name)}</div>
            <div>${_esc(inv.tax_id)}</div>
            <div title="${_esc(inv.item_type)}">${_esc(inv.item_type)}</div>
            <div>${_esc(inv.category)}</div>
            <div class="crm-row-status">${_kindBadge(inv)}</div>
            <div class="crm-row-status">${_payBadge(inv.payment_status)}</div>
            <div class="crm-row-status">${_statusBadge(inv.issue_status)}</div>
            ${kebabMenuHtml(inv.id, { onEdit: '_invEdit', onDuplicate: '_invDup', onDelete: '_invDelete' })}
        </div>
    `).join('');
}

function _buildEditFields() {
    return [
        // ── 頂部
        {name:'invoice_date', label:'日期', type:'date'},
        {name:'title', label:'名稱', type:'text'},
        // ── 開立資訊
        {name:'invoice_number', label:'發票編號', type:'text'},
        {name:'issue_status', label:'開立狀態', type:'select', options:[{value:'開立中',label:'開立中'},{value:'已開立',label:'已開立'},{value:'作廢',label:'作廢'}]},
        // 🔴 兩欄都可以填 —— 對方報價給的有時是未稅、有時是含稅，只開一邊就要
        // 有人自己按計算機再回填（owner 2026-08-20）。改哪一欄就以哪一欄為準，
        // 另一欄與稅額由 _deriveInvoice 推（同一支，不另寫一套換算）。
        {name:'amount_ex_tax', label:'未稅價', type:'number'},
        {name:'amount_total', label:'含稅價', type:'number'},
        {name:'_tax_amount_display', label:'稅額', type:'readonly'},
        {name:'company_name', label:'抬頭', type:'select',
            options:[{value:'',label:'— 選擇客戶 —'}].concat(_clients.map(c => ({value:c.short_name,label:c.short_name + (c.tax_id ? ' (' + c.tax_id + ')' : '')})))},
        {name:'tax_id', label:'統編', type:'readonly'},
        {name:'item_type', label:'品項', type:'text'},
        {name:'invoice_kind', label:'發票種類', type:'select', options:[{value:'電子發票',label:'電子發票'},{value:'紙本發票',label:'紙本發票'}]},
        // ── 紙本發票條件欄位（動態顯隱）
        {name:'recipient', label:'收件人', type:'text', _group:'paper'},
        {name:'recipient_phone', label:'收件電話', type:'text', _group:'paper'},
        {name:'recipient_address', label:'收件地址', type:'text', _group:'paper'},
        // ── 補充資訊
        {name:'applicant', label:'申請人', type:'text'},
        {name:'category', label:'發票類別', type:'select', options:[{value:'專案',label:'專案'},{value:'內部代開',label:'內部代開'},{value:'外部代開',label:'外部代開'}]},
        // ── 類別條件欄位（動態顯隱）
        {name:'project_id', label:'關聯專案', type:'select', _group:'cat-project',
            options:[{value:'',label:'— 不關聯 —'}].concat(_projects.map(p => ({value:p.id,label:p.name})))},
        {name:'_commission_display', label:'代開匯款', type:'readonly', _group:'cat-commission'},
        {name:'notes', label:'備註', type:'text'},
    ];
}

/** After enableInlineEdit, wire up dynamic show/hide + auto-fill logic */
function _wireEditDynamics() {
    const content = document.getElementById('inv-detail-content');
    if (!content) return;

    // Helper: find the .crm-detail-prop row containing a [data-field] or readonly for given field name
    function _findRow(fieldName) {
        for (const row of content.querySelectorAll('.crm-detail-prop')) {
            const el = row.querySelector(`[data-field="${fieldName}"]`);
            if (el) return row;
            // readonly fields don't have data-field, match by label text
            const label = row.querySelector('.crm-prop-label');
            const fields = _buildEditFields();
            const f = fields.find(x => x.name === fieldName);
            if (f && label && label.textContent.trim() === f.label) return row;
        }
        return null;
    }

    const paperFields = ['recipient', 'recipient_phone', 'recipient_address'];
    const kindSel = content.querySelector('[data-field="invoice_kind"]');
    const catSel = content.querySelector('[data-field="category"]');
    const projectRow = _findRow('project_id');
    const commRow = _findRow('_commission_display');
    const paperRows = paperFields.map(f => _findRow(f));

    function _togglePaper() {
        const show = kindSel?.value === '紙本發票';
        paperRows.forEach(r => { if (r) r.style.display = show ? '' : 'none'; });
    }

    function _toggleCategory() {
        const cat = catSel?.value;
        if (projectRow) projectRow.style.display = cat === '專案' ? '' : 'none';
        if (commRow) {
            commRow.style.display = (cat === '內部代開' || cat === '外部代開') ? '' : 'none';
            // Update label
            const label = commRow.querySelector('.crm-prop-label');
            if (label) label.textContent = cat === '外部代開' ? '代開應匯' : '代開匯款';
        }
    }

    if (kindSel) { kindSel.addEventListener('change', _togglePaper); _togglePaper(); }
    if (catSel) { catSel.addEventListener('change', _toggleCategory); _toggleCategory(); }

    // 發票編號 → 開立狀態
    const invNumEl = content.querySelector('[data-field="invoice_number"]');
    const statusSel = content.querySelector('[data-field="issue_status"]');
    if (invNumEl && statusSel) {
        invNumEl.addEventListener('input', () => {
            if (statusSel.value === '作廢') return;
            statusSel.value = invNumEl.value.trim() ? '已開立' : '開立中';
        });
    }

    // 抬頭 → 統編
    const compSel = content.querySelector('[data-field="company_name"]');
    if (compSel) {
        compSel.addEventListener('change', () => {
            const client = _clients.find(c => c.short_name === compSel.value);
            const taxRow = _findRow('tax_id');
            if (taxRow) {
                const val = taxRow.querySelector('.crm-prop-value');
                if (val) val.textContent = client?.tax_id || '';
            }
        });
    }
}

// ── 電子發票檔（詳情面板右欄）────────────────────────────────
// 檔案存磁碟（根目錄 settings.invoices_root，後台可設），DB 只記絕對路徑。
// 檔名/資料夾規則的正本在後端 routers/crm/finance.py::_invoice_file_name。

let _fileBusy = false;
// 上傳後從電子發票 PDF 抽到、但還沒經人確認的發票號碼（見 _renderFilePane 的提示條）
let _pendingNumber = null;
// 上傳時 PDF 與表單的比對結果（Soca 2026-08-21：「一次多張，怕會傳錯張」）。
// 🔴 是警示不是閘門 —— 檔案已經存好了，這裡只是提醒去看一眼。
// 三態都要能表達：相符 / 不符 / 讀不到 PDF —— 只存 warnings 的話，
// 「檢查過而且沒問題」跟「根本沒檢查」在畫面上長得一樣。
let _fileMatch = null;   // { checked: bool, warnings: string[] }
let _matchOpen = false;  // 不符時的明細有沒有展開

/** 比對結果徽章 —— 掛在「電子發票」標題右邊（owner 2026-08-21 指定位置）。
 *  相符也要出聲：不然「檢查過沒問題」跟「根本沒檢查」在畫面上一樣，
 *  使用者無從知道這個防呆到底有沒有在運作。 */
function _matchBadge() {
    if (!_fileMatch) return '';
    if (!_fileMatch.checked) {
        return `<span class="inv-match none" title="這個檔抽不到文字（掃描件或圖片），沒辦法比對">未比對</span>`;
    }
    const n = (_fileMatch.warnings || []).length;
    if (!n) return `<span class="inv-match ok" title="PDF 上的統編／金額／號碼／抬頭都與表單一致">與表單相符</span>`;
    return `<span class="inv-match bad" onclick="window._invToggleMatch()"
                  title="點一下看哪裡不一樣">${n} 項對不上 ${_matchOpen ? '▴' : '▾'}</span>`;
}

/** 不符時的明細，點徽章才展開 —— 平常不佔位置。 */
function _matchDetail() {
    const ws = (_fileMatch && _fileMatch.warnings) || [];
    if (!ws.length || !_matchOpen) return '';
    return `
      <div class="inv-file-warn">
        <b>確認一下是不是傳錯張</b>
        <ul>${ws.map(w => `<li>${_esc(w)}</li>`).join('')}</ul>
      </div>`;
}

function _renderFilePane(inv) {
    const pane = document.getElementById('inv-file-pane');
    if (!pane) return;
    const hasFile = !!inv.file_url;
    pane.innerHTML = `
      <h4>電子發票${_matchBadge()}</h4>
      ${_matchDetail()}
      ${hasFile ? `
        <div class="inv-file-card">
          <div class="fn">${_esc(inv.file_name || inv.file_url)}</div>
          <div class="inv-file-actions">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._invFileOpen()">開啟</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._invFilePick()">重新上傳</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._invFileClear()">解除關聯</button>
          </div>
        </div>
        ${_pendingNumber ? `
        <div class="inv-file-detected">
          從 PDF 讀到發票號碼<br><b>${_esc(_pendingNumber)}</b>
          ${inv.invoice_number ? `<div class="inv-file-note" style="margin-top:4px;">
              目前是 ${_esc(inv.invoice_number)}，套用會取代它</div>` : ''}
          <div class="inv-file-actions" style="margin-top:6px;">
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._invApplyNumber()">套用</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._invDismissNumber()">不要</button>
          </div>
        </div>` : ''}
        <div class="inv-file-share">
          <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._invShareLink(this)">
            ${inv.has_share ? '複製客戶下載連結' : '產生客戶下載連結'}</button>
          ${inv.has_share ? `<button class="crm-btn crm-btn-secondary crm-btn-sm"
                onclick="window._invShareRevoke()">停用連結</button>` : ''}
        </div>
        <div class="inv-file-note">下載連結<b>免登入</b>，拿到網址的人就能下載這張發票 ——
          寄錯人時按「停用連結」，舊網址立刻失效（之後可再產一張新的）。</div>
        <div class="inv-file-note">「解除關聯」只是把這筆的連結拿掉，<b>不會刪磁碟上的檔</b>
          —— 稅務憑證誤刪救不回來。</div>
      ` : `
        <div class="inv-file-drop" id="inv-file-drop" onclick="window._invFilePick()">
          拖曳檔案到這裡<br>或點一下選擇<br>
          <span style="font-size:10px;">PDF / JPG / PNG，30MB 內</span>
        </div>
        <div class="inv-file-note">存到<b>發票根目錄</b>底下的
          <code>${_esc((inv.invoice_date || '').substring(0, 4) || '年')}/${_esc((inv.invoice_date || '').substring(0, 7) || '年-月')}/</code>，
          檔名自動組成「日期_發票號碼_抬頭_金額」。</div>
      `}
      <input type="file" id="inv-file-input" style="display:none;"
             accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/*"
             onchange="window._invFileUpload(this.files[0])">`;

    const drop = document.getElementById('inv-file-drop');
    if (drop) {
        ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, (ev) => {
            ev.preventDefault(); drop.classList.add('dragover');
        }));
        ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, (ev) => {
            ev.preventDefault(); drop.classList.remove('dragover');
        }));
        drop.addEventListener('drop', (ev) => {
            const f = ev.dataTransfer?.files?.[0];
            if (f) window._invFileUpload(f);
        });
    }
}

window._invFilePick = function () { document.getElementById('inv-file-input')?.click(); };

window._invFileOpen = function () {
    const inv = _invoices.find(i => i.id === _selectedId);
    if (!inv?.file_url) return;
    // 走後端白名單守衛的取檔端點（檔案可能在 NAS，不在 web root 底下）
    window.open('/api/v1/crm/invoice-file?path=' + encodeURIComponent(inv.file_url), '_blank');
};

window._invFileUpload = async function (file) {
    if (!file || _fileBusy || !_selectedId) return;
    _fileBusy = true;
    const pane = document.getElementById('inv-file-pane');
    if (pane) pane.querySelector('h4').textContent = '電子發票（上傳中…）';
    try {
        const fd = new FormData();
        fd.append('file', file);
        // crmFetch 對 FormData 會刻意不設 Content-Type（否則蓋掉 multipart boundary），
        // token 與錯誤 detail 也都幫忙處理好了 —— 不要在這裡再手刻一次 fetch。
        const up = await _fetch(`/invoices/${_selectedId}/file`, { method: 'POST', body: fd });
        await loadInvoices();
        // 偵測到的號碼與現值不同才問 —— 一樣的話沒有打擾的理由。
        // 🔴 不自動套用：發票號碼是法定識別，改它要人點頭（owner 要的是「可以選擇」）。
        if (up.detected_differs) _pendingNumber = up.detected_invoice_number;
        _fileMatch = { checked: !!up.checked, warnings: up.warnings || [] };
        _matchOpen = (up.warnings || []).length > 0;   // 有問題就先展開給人看
        const fresh = await _fetch('/invoices/' + _selectedId);
        renderDetail(fresh);
    } catch (e) {
        alert('上傳失敗：' + e.message);
        const inv = _invoices.find(i => i.id === _selectedId);
        if (inv) _renderFilePane(inv);
    } finally { _fileBusy = false; }
};

/** 產生（或取回）客戶下載連結並複製到剪貼簿。
 *  後端是冪等的：已經有一張有效的就原樣回傳 —— 同一張發票寄兩次信，先寄出去的
 *  那個連結不該失效。 */
/** 套用偵測到的發票號碼。走既有的 PUT（與種類切換同一條路），所以
 *  「有編號→已開立」那條自動規則、月結守衛都照常生效。 */
window._invToggleMatch = function () {
    _matchOpen = !_matchOpen;
    const inv = _invoices.find(i => i.id === _selectedId);
    if (inv) _renderFilePane(inv);
};

window._invApplyNumber = async function () {
    if (!_selectedId || !_pendingNumber) return;
    const num = _pendingNumber;
    _pendingNumber = null;
    try {
        const full = await _fetch('/invoices/' + _selectedId);
        await _fetch('/invoices/' + _selectedId, {
            method: 'PUT',
            body: JSON.stringify({ ...full, invoice_number: num }),
        });
        await loadInvoices();
        renderDetail(await _fetch('/invoices/' + _selectedId));
    } catch (e) { alert('套用失敗：' + e.message); }
};

window._invDismissNumber = function () {
    _pendingNumber = null;
    const inv = _invoices.find(i => i.id === _selectedId);
    if (inv) _renderFilePane(inv);
};

window._invShareLink = async function (btn) {
    if (!_selectedId) return;
    try {
        const d = await _fetch(`/invoices/${_selectedId}/share`, { method: 'POST' });
        // 後端只回 path：網址要用「使用者現在是從哪個網域進來的」組（內網 IP、
        // localhost、還是 cloudflared 的對外網域），寫死任何一個都會寄出打不開的連結。
        // 複製「檔名 換行 連結」兩行（owner 指定）—— 貼進信裡對方一眼知道那是什麼，
        // 光一條網址看不出是哪張發票。
        const url = location.origin + d.path;
        await copyText(`${d.file_name || ''}\n${url}`.trim(), btn);
        await loadInvoices();
        // 等 copyText 的「已複製」回饋（1.5s）走完再重畫這一區，否則按鈕會在
        // 使用者看到回饋之前就被換掉，變成「按了好像沒反應」。
        setTimeout(async () => {
            if (!_selectedId) return;
            try { _renderFilePane(await _fetch('/invoices/' + _selectedId)); } catch (_) { /* 已切走 */ }
        }, 1700);
    } catch (e) { alert('產生連結失敗：' + e.message); }
};

window._invShareRevoke = async function () {
    if (!_selectedId || !confirm('停用這張發票的下載連結？已寄出去的網址會立刻失效。')) return;
    try {
        await _fetch(`/invoices/${_selectedId}/share`, { method: 'DELETE' });
        await loadInvoices();
        renderDetail(await _fetch('/invoices/' + _selectedId));
    } catch (e) { alert('停用失敗：' + e.message); }
};

window._invFileClear = async function () {
    if (!_selectedId || !confirm('解除這張發票與檔案的關聯？（磁碟上的檔不會被刪除）')) return;
    try {
        await _fetch('/invoices/' + _selectedId + '/file', { method: 'DELETE' });
        await loadInvoices();
        renderDetail(await _fetch('/invoices/' + _selectedId));
    } catch (e) { alert('解除失敗：' + e.message); }
};

/** 發票根目錄設定卡（管理員限定）。
 *
 * 端點是 check_admin —— 非管理員拿到 403，就讓入口保持隱藏（不畫一個按下去
 * 必然失敗的按鈕）。比照零用金的「收據資料夾」卡，同一個互動形狀。 */
async function _initInvoicesRootCard() {
    const link = document.getElementById('inv-root-toggle');
    const panel = document.getElementById('inv-root-panel');
    if (!link || !panel) return;
    let cfg;
    try {
        cfg = await _fetch('/invoices-root');   // 403（非管理員）→ 進 catch，入口不顯示
    } catch (_) { return; }

    link.style.display = '';
    panel.innerHTML = `
        已開立的電子發票檔存放根目錄。要集中到 NAS 或會計師的共用資料夾就填那個路徑；
        留空＝主控機預設 <span style="color:#aaa;">${_esc(cfg.default)}</span>。
        底下會自動分 <code>{年}/{年-月}/</code>，檔名為
        <code>日期_發票號碼_抬頭_含稅金額</code>。
        <div style="display:flex;gap:8px;margin-top:8px;">
          <input id="inv-root-input" class="crm-input" value="${_esc(cfg.invoices_root)}"
                 placeholder="例：\\\\OriginsunNAS\\Invoices 或 T:\\發票">
          <button class="crm-btn crm-btn-primary crm-btn-sm" id="inv-root-save">儲存</button>
          <span id="inv-root-msg" style="align-self:center;"></span>
        </div>
        <div style="margin-top:6px;">目前生效：<span id="inv-root-eff">${_esc(cfg.effective)}</span></div>`;

    link.onclick = () => { panel.style.display = panel.style.display === 'none' ? '' : 'none'; };
    panel.querySelector('#inv-root-save').onclick = async () => {
        const msg = panel.querySelector('#inv-root-msg');
        msg.textContent = '儲存中…';
        try {
            const d = await _fetch('/invoices-root', {
                method: 'POST',
                body: JSON.stringify({ invoices_root: panel.querySelector('#inv-root-input').value.trim() }),
            });
            panel.querySelector('#inv-root-eff').textContent = d.effective || '';
            msg.textContent = '已儲存（之後上傳的發票存到新位置；舊檔不搬）';
        } catch (e) { msg.textContent = '失敗：' + (e && e.message || e); }
    };
}

/** 收款紀錄區塊：這張發票實際收了幾次、每次多少、哪一天、還欠多少。
 *
 *  資料來自收支明細的發票分配（crm_cash_invoice_links），也就是**帳上真的進了
 *  多少錢** —— 比手填的「款項狀態」可靠，而且分期收款時看得到全貌。
 *  `payments` 只有 GET /invoices/{id} 才回（清單那支只回合計，不拖慢列表）。
 */
function _collectionSection(inv, section, prop) {
    const pays = inv.payments || [];
    const collected = inv.collected || 0;
    const total = inv.amount_total || 0;
    const outstanding = inv.outstanding != null ? inv.outstanding : (total - collected);
    if (!pays.length && !collected) {
        // 沒有任何收款紀錄就不佔版面 —— 但已標已收款卻查無紀錄要講出來，
        // 那是「狀態說收到了、帳上卻沒有這筆錢」，值得被看見。
        // 只在收支明細涵蓋得到這張發票的期間時才提醒 —— 早於收支表起始日的
        // 發票，帳上本來就查不到對應收款，那不是資料有問題。
        if ((inv.payment_status === '已收款'
             || inv.payment_status === INV_PENDING_REMIT)
            && inv.collection_checkable) {
            return section('收款紀錄')
                + `<div style="padding:6px 0;font-size:12px;color:#fbbf24;">`
                + `標示為已收款，但收支明細裡查不到對應的收款 ——`
                + ` 可能是還沒在收支明細掛上這張發票。</div>`;
        }
        return '';
    }
    // 收齊了沒由後端判（含匯費容差）—— 自己用 outstanding <= 0 算會跟應收帳款打架
    const done = inv.settled != null ? !!inv.settled : outstanding <= 0;
    let h = section('收款紀錄',
        `<span style="font-size:11px;color:${done ? '#86efac' : '#fbbf24'};">`
        + `已收 $${_fmtNum(collected)} / $${_fmtNum(total)}`
        + (done ? '（收齊）' : `，尚欠 $${_fmtNum(outstanding)}`) + '</span>');
    pays.forEach((p, i) => {
        const label = pays.length > 1 ? `第 ${i + 1} 次到款` : '到款日';
        const bits = [p.date ? p.date.substring(0, 10) : '(無日期)',
                      '$' + _fmtNum(p.amount)];
        if (p.bank_account) bits.push(p.bank_account);   // 錢進了哪個銀行
        if (p.summary) bits.push(p.summary);
        h += prop(label, bits.join('　'), p.date ? p.date.substring(0, 10) : '');
    });
    return h;
}

function renderDetail(inv) {
    document.getElementById('inv-detail-title').textContent = inv.title;
    // 每列右側一顆低調的「複製」—— 開發票的同事要把抬頭/統編/品項/金額逐項貼到
    // 開立系統，逐欄反白很容易多帶到空白或漏字。平常淡到幾乎看不見，滑到該列才浮出。
    // 空值不給按鈕（沒東西可複製）。金額類複製**純數字**：貼進開立系統時
    // 「$12,600」的錢字號與逗號多半要再手動清掉。
    const prop = (label, value, copyValue = null) => {
        const empty = !value;
        const raw = copyValue != null ? String(copyValue) : String(value ?? '');
        const btn = empty ? '' :
            `<button type="button" class="crm-prop-copy" data-copy="${_esc(raw)}"
                     title="複製${_esc(label)}">複製</button>`;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div>` +
            `<div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div>` +
            `${btn}</div>`;
    };
    const section = (title, extra = '') => `<div class="crm-detail-section">${title}${extra ? '<span style="margin-left:auto;">' + extra + '</span>' : ''}</div>`;
    const commLabel = inv.category === '外部代開' ? '代開應匯' : inv.category === '內部代開' ? '代開匯款' : '';

    let html = '';
    // ── 頂部
    html += prop('日期', inv.invoice_date ? inv.invoice_date.substring(0, 10) : '');
    // ── 開立資訊
    html += section('開立資訊', _payBadge(inv.payment_status));
    html += prop('發票編號', inv.invoice_number);
    html += prop('開立狀態', inv.issue_status);
    html += prop('未稅價', inv.amount_ex_tax ? '$' + _fmtNum(inv.amount_ex_tax) : '', inv.amount_ex_tax);
    html += prop('含稅價', inv.amount_total ? '$' + _fmtNum(inv.amount_total) : '', inv.amount_total);
    html += prop('稅額', inv.tax_amount ? '$' + _fmtNum(inv.tax_amount) : '', inv.tax_amount);
    html += prop('抬頭', inv.company_name);
    html += prop('統編', inv.tax_id);
    html += prop('品項', inv.item_type);
    html += prop('發票種類', inv.invoice_kind);
    if (inv.invoice_kind === '紙本發票') {
        html += prop('收件人', inv.recipient);
        html += prop('收件電話', inv.recipient_phone);
        html += prop('收件地址', inv.recipient_address);
    }
    // ── 收款紀錄（從收支明細的發票分配推導，不是手填的狀態欄）
    html += _collectionSection(inv, section, prop);
    // ── 補充資訊
    html += section('補充資訊');
    html += prop('申請人', inv.applicant);
    html += prop('發票類別', inv.category);
    if (inv.category === '專案') html += prop('關聯專案', inv.project_name);
    if (commLabel) html += prop(commLabel, inv.commission ? '$' + _fmtNum(inv.commission) : '', inv.commission);
    html += prop('備註', inv.notes);

    const content = document.getElementById('inv-detail-content');
    content.innerHTML = html;
    // 委派：renderDetail 每次重建 innerHTML，逐顆綁 listener 會隨著重建流失
    if (!content.dataset.copyWired) {
        content.dataset.copyWired = '1';
        content.addEventListener('click', (e) => {
            const btn = e.target.closest('.crm-prop-copy');
            if (btn) copyText(btn.dataset.copy || '', btn);
        });
    }
    _renderFilePane(inv);

    const actions = document.getElementById('inv-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('inv-bar-actions', () => {
        const editData = { ...inv,

            _tax_amount_display: inv.tax_amount ? '$' + _fmtNum(inv.tax_amount) : '',
            _commission_display: inv.commission ? '$' + _fmtNum(inv.commission) : '',
        };
        enableInlineEdit('inv-detail-content', 'inv-bar-actions', _buildEditFields(), editData,
            async (payload) => {
                // auto: 有編號→已開立，無編號→開立中（除非作廢）
                if (payload.issue_status !== '作廢') {
                    payload.issue_status = payload.invoice_number?.trim() ? '已開立' : '開立中';
                }
                // 🔴 這裡是「編別的欄位」的路徑，不是改收付狀態的路徑 —— 兩欄一律原值帶回。
                // 舊寫法硬塞 payment_type='收款'（匯入的 183 張付款發票一被編輯就翻面）、
                // payment_status 空值塞 '未收款'（替來源沒填的 21 張表態）。
                payload.payment_type = inv.payment_type || '收款';
                if (payload.issue_status === '作廢') payload.payment_status = '作廢';
                else payload.payment_status = inv.payment_status || '';
                // 兩欄都可編：看使用者動了哪一欄就從那一欄推另一欄。
                // 兩欄都改（或都沒改）時以含稅價為準 —— 含稅是實際收到/付出的數字，
                // 而未稅是它除出來的；反過來推會因為四捨五入讓含稅價自己跳動。
                const exChanged = (payload.amount_ex_tax ?? null) !== (inv.amount_ex_tax ?? null);
                const totChanged = (payload.amount_total ?? null) !== (inv.amount_total ?? null);
                const useTotal = totChanged || !exChanged;
                _deriveInvoice(payload, {
                    amount: useTotal ? payload.amount_total : payload.amount_ex_tax,
                    mode: useTotal ? 'total' : 'ex',
                    fallbackTaxId: inv.tax_id });
                await _fetch('/invoices/' + inv.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/invoices/' + inv.id);
                renderDetail(updated);
                await loadInvoices();
            },
            () => renderDetail(inv)
        );
        _wireEditDynamics();
    });
}

// ── Detail ───────────────────────────────────────────────────

async function selectInvoice(id) {
    // 🔴 切到別張發票一定要清掉上一張還沒確認的偵測號碼 —— 留著的話那個「套用」
    // 會把 A 的發票號碼寫進 B。
    if (_selectedId !== id) { _pendingNumber = null; _fileMatch = null; _matchOpen = false; }
    _selectedId = id;
    renderList();
    document.getElementById('inv-detail-panel').style.display = 'flex';
    document.getElementById('inv-resize-handle').style.display = '';
    try {
        const inv = await _fetch('/invoices/' + id);
        renderDetail(inv);
    } catch (_) {}
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('inv-detail-panel').style.display = 'none';
    document.getElementById('inv-resize-handle').style.display = 'none';
    renderList();
}

// ── Modal ────────────────────────────────────────────────────

const _FIELDS = ['issue_status', 'invoice_number', 'invoice_date', 'title',
    'category', 'amount_ex_tax', 'amount_total', 'tax_amount', 'commission',
    'applicant', 'company_name', 'tax_id', 'item_type', 'project_id', 'notes',
    'recipient', 'recipient_phone', 'recipient_address'];
const _INT_FIELDS = ['amount_ex_tax', 'amount_total', 'tax_amount', 'commission'];

function _populateProjectSelect(selectedId) {
    const sel = document.getElementById('inv-f-project_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 不關聯 —</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)}</option>`).join('');
}

function _populateClientSelect(selectedName) {
    const sel = document.getElementById('inv-f-company_name');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇客戶 —</option>` +
        _clients.map(c => `<option value="${_esc(c.short_name)}" data-taxid="${_esc(c.tax_id || '')}"${c.short_name === selectedName ? ' selected' : ''}>${_esc(c.short_name)}${c.tax_id ? ' (' + c.tax_id + ')' : ''}</option>`).join('');
}

// 稅率規則住在 crm-utils.invoiceAmounts —— 專案頁的「開發票」也走同一支。
const _amountsFrom = invoiceAmounts;

/** 金額三欄 + 客戶 → 統編 + 類別 → 代開匯款。
 *
 * 就地新增列與詳情頁 inline 編輯共用。modal 那條路**不**走這裡 —— 它把含稅價、
 * 代開匯款都攤開給人直接填（`_updateTaxCalc` 即時算給你看），是刻意的差異。 */
function _deriveInvoice(payload, { amount, mode = 'ex', fallbackTaxId = '' } = {}) {
    Object.assign(payload, _amountsFrom(amount, mode));
    const client = _clients.find(c => c.short_name === payload.company_name);
    payload.tax_id = client?.tax_id || fallbackTaxId || '';
    const tot = payload.amount_total || 0;
    if (payload.category === '內部代開' || payload.category === '外部代開') {
        payload.commission = _commissionOf(tot, payload.category);
    }
    else payload.commission = null;
    return payload;
}

function _todayStr() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function _updateTaxCalc() {
    // 稅率只有 _amountsFrom 一個出口 —— 本來這裡與計算機各自寫死 1.05，
    // TAX_RATE 只管得到三分之一的地方（稅率不是永恆的 5%）
    const a = _amountsFrom(document.getElementById('inv-f-amount_ex_tax').value, 'ex');
    document.getElementById('inv-f-amount_total').value = a.amount_total ?? '';
    _updateCommission();
}

function _updateCommission() {
    const total = parseInt(document.getElementById('inv-f-amount_total').value) || 0;
    const cat = document.getElementById('inv-f-category').value;
    const intEl = document.getElementById('inv-f-commission');
    const extEl = document.getElementById('inv-f-commission_ext');
    if (cat === '內部代開') {
        intEl.value = _commissionOf(total, '內部代開') ?? '';
    } else if (cat === '外部代開') {
        extEl.value = _commissionOf(total, '外部代開') ?? '';
    }
}

function _updateCategoryVisibility() {
    const cat = document.getElementById('inv-f-category').value;
    document.getElementById('inv-cond-project').style.display = cat === '專案' ? '' : 'none';
    document.getElementById('inv-cond-internal').style.display = cat === '內部代開' ? '' : 'none';
    document.getElementById('inv-cond-external').style.display = cat === '外部代開' ? '' : 'none';
    _updateCommission();
}

function _updateInvoiceKindVisibility() {
    const kind = document.querySelector('input[name="inv-invoice-kind"]:checked')?.value || '電子發票';
    document.getElementById('inv-paper-fields').style.display = kind === '紙本發票' ? '' : 'none';
}

function _onClientChange() {
    const sel = document.getElementById('inv-f-company_name');
    const opt = sel.options[sel.selectedIndex];
    document.getElementById('inv-f-tax_id').value = opt?.dataset?.taxid || '';
}

function _onInvoiceNumberInput() {
    const status = document.getElementById('inv-f-issue_status');
    if (status.value === '作廢') return;
    const num = document.getElementById('inv-f-invoice_number').value.trim();
    status.value = num ? '已開立' : '開立中';
}

function openModal(inv = null) {
    _editingId = inv ? inv.id : null;
    _editingPaymentStatus = inv?.payment_status || null;
    _editingPaymentType = inv?.payment_type || null;
    document.getElementById('inv-modal-title').textContent = inv ? '編輯發票' : '新增發票';
    // Payment status badge
    const badgeEl = document.getElementById('inv-modal-pay-badge');
    if (badgeEl) {
        badgeEl.innerHTML = inv ? _payBadge(inv.payment_status) : '';
    }
    const err = document.getElementById('inv-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populateProjectSelect(inv?.project_id || '');
    _populateClientSelect(inv?.company_name || '');
    _populateApplicantSelect(inv?.applicant || '');

    // Reset all fields
    for (const f of _FIELDS) {
        const el = document.getElementById('inv-f-' + f);
        if (!el) continue;
        if (f === 'invoice_date') {
            el.value = inv?.invoice_date ? inv.invoice_date.substring(0, 10) : _todayStr();
        } else if (f === 'issue_status') {
            el.value = inv?.issue_status || '開立中';
        } else if (f === 'category') {
            el.value = inv?.category || '專案';
        } else {
            el.value = inv ? (inv[f] ?? '') : '';
        }
    }

    // Radio: invoice_kind
    const kind = inv?.invoice_kind || '電子發票';
    const radio = document.querySelector(`input[name="inv-invoice-kind"][value="${kind}"]`);
    if (radio) radio.checked = true;

    // Auto-fill tax_id from selected client
    _onClientChange();
    // If editing, override tax_id with saved value
    if (inv?.tax_id) document.getElementById('inv-f-tax_id').value = inv.tax_id;

    // Trigger visibility & calculations
    _updateCategoryVisibility();
    _updateInvoiceKindVisibility();
    if (inv?.amount_ex_tax) _updateTaxCalc();

    document.getElementById('inv-modal').style.display = 'flex';
}

async function saveInvoice() {
    const title = document.getElementById('inv-f-title').value.trim();
    if (!title) { _showErr('名稱為必填'); return; }

    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById('inv-f-' + f);
        let val = el ? el.value.trim() : '';
        if (_INT_FIELDS.includes(f)) val = val ? parseInt(val) : null;
        if (f === 'invoice_date') val = val || null;
        if (f === 'project_id') val = val || null;
        payload[f] = val;
    }
    // invoice_kind from radio
    payload.invoice_kind = document.querySelector('input[name="inv-invoice-kind"]:checked')?.value || '電子發票';
    // commission: use whichever is visible
    const cat = payload.category;
    if (cat === '外部代開') {
        const extVal = document.getElementById('inv-f-commission_ext').value.trim();
        payload.commission = extVal ? parseInt(extVal) : null;
    } else if (cat === '內部代開') {
        // already from inv-f-commission
    } else {
        payload.commission = null;
    }
    // tax_amount
    const exTax = payload.amount_ex_tax || 0;
    const total = payload.amount_total || 0;
    payload.tax_amount = total - exTax;
    // payment_type / payment_status —— 編輯時**一律原值帶回**。
    // 🔴 這行本來是無條件 `= '收款'`。列表的 inline 編輯那條路早就修好了
    //（見 _quickEditSave 的說明），但這個編輯視窗漏掉：打開任何一張代開發票
    // 按儲存，方向就被翻成收款。生產有 183 張 payment_type='付款'、合計
    // 10,656,093 —— 那正是 receivables_summary 註解裡「應收虛增成 4.7 倍」的同一批。
    payload.payment_type = _editingPaymentType || '收款';
    if (payload.issue_status === '作廢') payload.payment_status = '作廢';
    else if (_editingId && _editingPaymentStatus) payload.payment_status = _editingPaymentStatus;
    else payload.payment_status = '未收款';
    // 帳本（兩本帳）— pin 是 'mine' 才帶；parent 不送（後端 None→parent，PUT 不洗欄位）
    if (_pinEntity() === 'mine') payload.entity = 'mine';

    const btn = document.getElementById('inv-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId) await _fetch('/invoices/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
        else await _fetch('/invoices', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('inv-modal').style.display = 'none';
        await loadInvoices();
    } catch (e) { _showErr(e.message); }
    finally { btn.disabled = false; btn.textContent = '儲存'; }
}

async function deleteInvoice(inv) {
    if (!confirm(`確定刪除「${inv.title}」？`)) return;
    try { await _fetch('/invoices/' + inv.id, { method: 'DELETE' }); closeDetail(); await loadInvoices(); }
    catch (e) { alert(e.message); }
}

function _showErr(msg) { const el = document.getElementById('inv-modal-error'); el.textContent = msg; el.style.display = 'block'; }

// ── Tax Conversion Popup ─────────────────────────────────────

function _initCalculator() {
    const inclEl = document.getElementById('inv-calc-tax-incl');
    const applyBtn = document.getElementById('inv-calc-apply');
    let _calcExTax = 0;

    const calc = () => {
        const incl = parseInt(inclEl.value) || 0;
        if (!incl) {
            document.getElementById('inv-calc-r-ex').textContent = '—';
            document.getElementById('inv-calc-r-tax').textContent = '—';
            applyBtn.disabled = true;
            _calcExTax = 0;
            return;
        }
        const a = _amountsFrom(incl, 'total');       // 稅率同上，只有一個出口
        _calcExTax = a.amount_ex_tax || 0;
        const tax = a.tax_amount || 0;
        document.getElementById('inv-calc-r-ex').textContent = '$' + _calcExTax.toLocaleString('zh-TW');
        document.getElementById('inv-calc-r-tax').textContent = '$' + tax.toLocaleString('zh-TW');
        applyBtn.disabled = false;
    };
    inclEl.addEventListener('input', calc);

    applyBtn.addEventListener('click', () => {
        if (!_calcExTax) return;
        document.getElementById('inv-f-amount_ex_tax').value = _calcExTax;
        _updateTaxCalc();
        document.getElementById('inv-calc-popup').style.display = 'none';
    });

    document.getElementById('inv-calc-btn').addEventListener('click', () => {
        inclEl.value = '';
        document.getElementById('inv-calc-r-ex').textContent = '—';
        document.getElementById('inv-calc-r-tax').textContent = '—';
        applyBtn.disabled = true;
        _calcExTax = 0;
        document.getElementById('inv-calc-popup').style.display = 'block';
        setTimeout(() => inclEl.focus(), 50);
    });
}

// ── Commission Settings Popup ────────────────────────────────

async function _initCommissionSettings() {
    const intEl = document.getElementById('inv-fee-internal');
    const extEl = document.getElementById('inv-fee-external');
    await _loadFees();
    intEl.value = _feeOf('內部代開');
    extEl.value = _feeOf('外部代開');
    // 存伺服器（全公司一份）—— 打字時不要每個鍵都送，離開欄位才存
    const save = async () => {
        const rates = { 內部代開: parseFloat(intEl.value) || 0,
                        外部代開: parseFloat(extEl.value) || 0 };
        try {
            const r = await _fetch('/invoice-fee-rates',
                                   { method: 'PUT', body: JSON.stringify({ rates }) });
            _fees = r.rates || rates;
        } catch (e) { alert('費率存不起來：' + e.message); }
        _updateCommission();
    };
    const preview = () => {
        _fees = { 內部代開: parseFloat(intEl.value) || 0,
                  外部代開: parseFloat(extEl.value) || 0 };
        _updateCommission();
    };
    intEl.addEventListener('input', preview);
    extEl.addEventListener('input', preview);
    intEl.addEventListener('change', save);
    extEl.addEventListener('change', save);
    document.getElementById('inv-commission-settings-btn').addEventListener('click', () => {
        document.getElementById('inv-commission-popup').style.display = 'block';
    });
}

// ── Applicant Settings Popup ─────────────────────────────────

function _initApplicantSettings() {
    _renderApplicantList();
    _populateApplicantSelect();

    document.getElementById('inv-applicant-settings-btn').addEventListener('click', () => {
        _renderApplicantList();
        document.getElementById('inv-applicant-popup').style.display = 'block';
    });

    document.getElementById('inv-applicant-add-btn').addEventListener('click', async () => {
        const input = document.getElementById('inv-applicant-new');
        const name = input.value.trim();
        if (!name) return;
        if (!_applicants.includes(name)) {
            try { await _saveApplicants([..._applicants, name]); }
            catch (e) { alert('存不起來：' + e.message); return; }
        }
        input.value = '';
        _renderApplicantList();
        _populateApplicantSelect(document.getElementById('inv-f-applicant').value);
        renderList();          // 快速新增列的下拉跟著更新
    });

    window._invRemoveApplicant = async (idx) => {
        const next = _applicants.filter((_, i) => i !== idx);
        try { await _saveApplicants(next); }
        catch (e) { alert('存不起來：' + e.message); return; }
        _renderApplicantList();
        _populateApplicantSelect(document.getElementById('inv-f-applicant').value);
        renderList();
    };
}

// ── CSV Import ───────────────────────────────────────────────

function openImportModal() {
    _csvFile = null;
    document.getElementById('inv-drop-filename').textContent = '';
    const r = document.getElementById('inv-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('inv-btn-do-import').disabled = true;
    document.getElementById('inv-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('inv-drop-filename').textContent = file ? file.name : '';
    document.getElementById('inv-btn-do-import').disabled = !file;
}

async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('inv-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        const form = new FormData();
        form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/invoices/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '匯入失敗');
        const data = await res.json();
        const result = document.getElementById('inv-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        result.style.display = 'block';
        await loadInvoices();
    } catch (e) {
        const result = document.getElementById('inv-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally { btn.disabled = false; btn.textContent = '開始匯入'; }
}

// ── Init ─────────────────────────────────────────────────────

export async function initCrmInvoicesTab() {
    for (const id of ['inv-modal', 'inv-import-modal', 'inv-calc-popup', 'inv-commission-popup', 'inv-applicant-popup']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._invSelect = selectInvoice;
    window._invEdit = async (id) => { try { openModal(await _fetch('/invoices/' + id)); } catch(_) {} };
    window._invDelete = (id) => { const inv = _invoices.find(x => x.id === id); if (inv) deleteInvoice(inv); };
    window._invDup = async (id) => {
        try {
            const inv = await _fetch('/invoices/' + id);
            openModal(inv); _editingId = null;
            document.getElementById('inv-modal-title').textContent = '複製發票';
        } catch (_) {}
    };

    let _t;
    document.getElementById('inv-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadInvoices, 300);
    });
    document.getElementById('inv-filter-type').addEventListener('change', e => { _filters.issue_status = e.target.value; loadInvoices(); });
    document.getElementById('inv-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadInvoices(); });
    // 專案篩選：/invoices 早就收 project_id，工具列一直沒有那顆 —— 從專案頁
    // 開的票綁好了，卻沒地方按「只看這個案子的」（owner 2026-08-23）。
    document.getElementById('inv-filter-project').addEventListener('change', e => { _filters.project_id = e.target.value; loadInvoices(); });

    _initInvoicesRootCard();   // 管理員限定，非管理員入口保持隱藏（不 await，別擋住 tab 載入）
    document.getElementById('inv-btn-add').addEventListener('click', () => openModal());
    document.getElementById('inv-btn-import').addEventListener('click', openImportModal);
    document.getElementById('inv-btn-save').addEventListener('click', saveInvoice);
    document.getElementById('inv-detail-close').addEventListener('click', closeDetail);

    // Modal dynamic behavior
    document.getElementById('inv-f-amount_ex_tax').addEventListener('input', _updateTaxCalc);
    document.getElementById('inv-f-category').addEventListener('change', _updateCategoryVisibility);
    document.getElementById('inv-f-company_name').addEventListener('change', _onClientChange);
    document.getElementById('inv-f-invoice_number').addEventListener('input', _onInvoiceNumberInput);
    document.querySelectorAll('input[name="inv-invoice-kind"]').forEach(r =>
        r.addEventListener('change', _updateInvoiceKindVisibility)
    );

    // Popups
    _initCalculator();
    _initCommissionSettings();
    _initApplicantSettings();
    document.getElementById('inv-btn-do-import').addEventListener('click', doImport);

    document.getElementById('inv-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('inv-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over');
        const f = e.dataTransfer.files[0]; if (f && f.name.endsWith('.csv')) _setCsvFile(f); });

    for (const id of ['inv-modal', 'inv-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }
    // Close popups on outside click
    document.addEventListener('click', e => {
        for (const pid of ['inv-calc-popup', 'inv-commission-popup', 'inv-applicant-popup']) {
            const p = document.getElementById(pid);
            if (p && p.style.display !== 'none' && !p.contains(e.target)
                && e.target.id !== 'inv-calc-btn' && e.target.id !== 'inv-commission-settings-btn'
                && e.target.id !== 'inv-applicant-settings-btn') {
                p.style.display = 'none';
            }
        }
    });

    setupResizeHandle('inv-resize-handle', 'inv-detail-panel');
    // View switching
    let _paymentsLoaded = false, _paymentsLoading = false;
    let _cashbookLoaded = false, _cashbookLoading = false;
    let _payablesLoaded = false, _payablesLoading = false;
    let _receivablesLoaded = false, _receivablesLoading = false;
    let _cashflowLoaded = false, _cashflowLoading = false;
    const invView = document.getElementById('inv-invoices-view');
    const payView = document.getElementById('inv-payments-view');
    const cashView = document.getElementById('inv-cashbook-view');
    const payablesView = document.getElementById('inv-payables-view');
    const receivablesView = document.getElementById('inv-receivables-view');
    const cashflowView = document.getElementById('inv-cashflow-view');
    const allViews = [invView, payView, cashView, payablesView, receivablesView, cashflowView];
    const allBtns = ['inv-view-invoices', 'inv-view-payments', 'inv-view-cashbook', 'inv-view-payables', 'inv-view-receivables', 'inv-view-cashflow'];
    const baseUrl = location.origin;

    function _switchView(showView, activeBtn) {
        allViews.forEach(v => { if (v) v.style.display = 'none'; });
        allBtns.forEach(b => document.getElementById(b)?.classList.remove('active'));
        if (showView) showView.style.display = 'flex';
        document.getElementById(activeBtn)?.classList.add('active');
    }

    document.getElementById('inv-view-invoices').addEventListener('click', () => _switchView(invView, 'inv-view-invoices'));

    document.getElementById('inv-view-payments').addEventListener('click', async () => {
        if (_paymentsLoading) return;
        _switchView(payView, 'inv-view-payments');
        if (!_paymentsLoaded) {
            _paymentsLoading = true;
            try {
                const _cb = '?t=' + Date.now();
                const res = await fetch(baseUrl + '/tabs/crm/crm-payments.html' + _cb);
                if (res.ok) {
                    payView.innerHTML = await res.text();
                    const mod = await import(baseUrl + '/tabs/crm/crm-payments.js' + _cb);
                    await mod.initCrmPaymentsTab();
                    _paymentsLoaded = true;
                }
            } catch (e) { console.warn('[Payments] load failed:', e); }
            finally { _paymentsLoading = false; }
        }
    });

    document.getElementById('inv-view-cashbook').addEventListener('click', async () => {
        if (_cashbookLoading) return;
        _switchView(cashView, 'inv-view-cashbook');
        if (!_cashbookLoaded) {
            _cashbookLoading = true;
            try {
                const _cb = '?t=' + Date.now();
                const res = await fetch(baseUrl + '/tabs/crm/crm-cashbook.html' + _cb);
                if (res.ok) {
                    cashView.innerHTML = await res.text();
                    const mod = await import(baseUrl + '/tabs/crm/crm-cashbook.js' + _cb);
                    await mod.initCrmCashbookTab();
                    _cashbookLoaded = true;
                }
            } catch (e) { console.warn('[Cashbook] load failed:', e); }
            finally { _cashbookLoading = false; }
        }
    });

    document.getElementById('inv-view-payables').addEventListener('click', async () => {
        if (_payablesLoading) return;
        _switchView(payablesView, 'inv-view-payables');
        if (!_payablesLoaded) {
            _payablesLoading = true;
            try {
                const _cb = '?t=' + Date.now();
                const res = await fetch(baseUrl + '/tabs/crm/crm-payables.html' + _cb);
                if (res.ok) {
                    payablesView.innerHTML = await res.text();
                    const mod = await import(baseUrl + '/tabs/crm/crm-payables.js' + _cb);
                    mod.initCrmPayablesTab();
                    _payablesLoaded = true;
                }
            } catch (e) { console.warn('[Payables] load failed:', e); }
            finally { _payablesLoading = false; }
        }
    });

    document.getElementById('inv-view-receivables').addEventListener('click', async () => {
        if (_receivablesLoading) return;
        _switchView(receivablesView, 'inv-view-receivables');
        if (_receivablesLoaded) {
            if (window._recvRefresh) window._recvRefresh();
            return;
        }
        _receivablesLoading = true;
        try {
            const _cb = '?t=' + Date.now();
            const res = await fetch(baseUrl + '/tabs/crm/crm-receivables.html' + _cb);
            if (res.ok) {
                receivablesView.innerHTML = await res.text();
                const mod = await import(baseUrl + '/tabs/crm/crm-receivables.js' + _cb);
                mod.initCrmReceivablesTab();
                _receivablesLoaded = true;
            }
        } catch (e) { console.warn('[Receivables] load failed:', e); }
        finally { _receivablesLoading = false; }
    });

    document.getElementById('inv-view-cashflow').addEventListener('click', async () => {
        if (_cashflowLoading) return;
        _switchView(cashflowView, 'inv-view-cashflow');
        if (_cashflowLoaded) {
            if (window._cashflowRefresh) window._cashflowRefresh();
            return;
        }
        _cashflowLoading = true;
        try {
            const _cb = '?t=' + Date.now();
            const res = await fetch(baseUrl + '/tabs/crm/crm-cashflow.html' + _cb);
            if (res.ok) {
                cashflowView.innerHTML = await res.text();
                const mod = await import(baseUrl + '/tabs/crm/crm-cashflow.js' + _cb);
                await mod.initCrmCashflowTab();
                _cashflowLoaded = true;
            }
        } catch (e) { console.warn('[Cashflow] load failed:', e); }
        finally { _cashflowLoading = false; }
    });

    // Global refresh — reloads current active sub-view
    document.getElementById('inv-global-refresh').addEventListener('click', () => {
        loadInvoices();
        if (window._payRefresh) window._payRefresh();
        if (window._cashRefresh) window._cashRefresh();
        if (window._payableRefresh) window._payableRefresh();
        if (window._recvRefresh) window._recvRefresh();
        if (window._cashflowRefresh) window._cashflowRefresh();
    });

    // 🔴 申請人清單要跟其他資料一起載，而且載完要重畫一次列表 ——
    // 快速新增列的申請人下拉是在 renderList 裡組的，清單晚到就會是空的
    // （帳戶切換列同一種競態剛咬過一次，見 crm-cashbook.initCrmCashbookTab）。
    // 費率同理：一起載，晚到的話新增發票時會用預設值算出錯的代開匯款
    await Promise.all([loadInvoices(), loadProjects(), loadClients(),
                       _loadApplicants(), _loadFees()]);
    _populateApplicantSelect();
    renderList();
}
