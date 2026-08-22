/**
 * crm-cashbook.js — 收支明細子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, projectOptionsHtml, today } from './crm-utils.js';
// 兩本帳（公司實體）— docs/LEDGER_ENTITY_PLAN.md §5。帳本由頁面隱形 pin：
// 財務 tab＝'parent'（預設）、/my-ledger.html＝'mine'（該頁在載入財務模組前設
// window._finEntity）。無使用者可見的帳本選單（單一 tab 單一帳本）。query 一律帶
// pin 值；payload 只在 'mine' 才帶 entity:'mine'（後端 None 語意：建立落 parent、
// 更新維持既有值 —— 不洗欄位）。pin 與 /api/v1/finance 的 fetch 都用財務模組那份，
// 不在這裡複寫（finFetch 會自己附 entity）。
import { finEntity as _pinEntity, finFetch as _finFetch } from '../finance/fin-utils.js';

let _entries = [];
let _invoiceList = [];
let _paymentList = [];   // 請款單（付款側的分配用）
let _projectList = [];
let _clientList = [];
let _bankAccounts = null;   // 財務模組銀行帳戶；null = 載入失敗/未啟用（優雅降級：不顯示帳戶欄）
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', bank_account_id: '', direction: '' };
let _csvFile = null;
const _catVocab = new Set();   // 類別篩選器的詞彙（只增不減，見 _syncFilterOptions）

function _toggleAdvanceFields(isAdv) {
    var ids = ['cash-advance-section', 'cash-field-project', 'cash-field-invoice', 'cash-field-bankfee'];
    for (var i = 0; i < ids.length; i++) {
        var el = document.getElementById(ids[i]);
        if (el) el.style.display = (i === 0 ? isAdv : !isAdv) ? '' : 'none';
    }
}

// ── Data Loading ────────────────────────────────────────────

async function loadEntries({ render = true } = {}) {
    const params = new URLSearchParams();
    if (_filters.q)               params.set('q', _filters.q);
    if (_filters.category)        params.set('category', _filters.category);
    if (_filters.bank_account_id) params.set('bank_account_id', _filters.bank_account_id);
    if (_filters.direction)       params.set('direction', _filters.direction);
    params.set('entity', _pinEntity());
    try { _entries = (await _fetch('/cash-entries?' + params)).entries || []; }
    catch (_) { _entries = []; }
    _syncFilterOptions();     // 資料換了才要重算選項（不放 renderList —— 那支連點一列都會跑）
    if (render) renderList();
}

async function _loadInvoiceList() {
    // 發票是帶 entity 的表 — 關聯選單只列同一本帳的發票
    try { _invoiceList = (await _fetch('/invoices?entity=' + _pinEntity())).invoices || []; } catch(_) { _invoiceList = []; }
    try { _paymentList = (await _fetch('/payments?entity=' + _pinEntity())).payments || []; } catch(_) { _paymentList = []; }
}

async function _loadProjectList() {
    try { _projectList = (await _fetch('/projects')).projects || []; } catch(_) { _projectList = []; }
}

async function _loadClientList() {
    try { _clientList = (await _fetch('/clients')).clients || []; } catch(_) { _clientList = []; }
}

async function _loadBankAccounts() {
    // 財務模組帳戶清單（prefix /api/v1/finance，非 crm）— 失敗優雅降級：隱藏帳戶欄、不擋原功能
    // 要帶餘額（with_balances 預設 1）—— 帳戶切換列每顆都標當下餘額
    // 帳戶屬於哪家公司（兩本帳）：finFetch 自動附頁面 pin 的 entity，選單只列該帳本的帳戶
    try {
        _bankAccounts = (await _finFetch('/bank-accounts')).items || [];
    } catch (_) { _bankAccounts = null; }
}

// ── List ────────────────────────────────────────────────────

const _sorter = createSortable({
    storageKey: 'crm_cashbook_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'cash-list-panel',
    onChange: () => renderList(),
    getters: {
        date:     e => e.entry_date || '',
        summary:  e => (e.summary || '').toLowerCase(),
        deposit:  e => e.deposit || 0,
        expense:  e => (e.expense || 0) + (e.bank_fee || 0),
        category: e => (e.category || '').toLowerCase(),
        project:  e => (e.project_name || '').toLowerCase(),
        invoice:  e => (e.invoice_title || '').toLowerCase(),
        account:  e => _acctName(e.bank_account_id).toLowerCase(),
        note:     e => (e.note || '').toLowerCase(),
    },
});

/** 多行備註壓成一行（列表格子只有一行高，換行會被吃掉看不出斷點）。 */
function _flat(text, sep) {
    return String(text || '').split('\n').filter(Boolean).join(sep);
}

/** 帳戶 id → 顯示名。帳戶清單載入失敗（_bankAccounts=null）時回空字串，不炸。 */
function _acctName(id) {
    if (!id || !_bankAccounts) return '';
    const a = _bankAccounts.find(x => String(x.id) === String(id));
    return a ? (a.name || '') : '';
}

/** 篩選器選項依**實際資料**生成 —— 寫死的清單會跟使用中的類別脫節，
 *  選了卻篩不到東西（原本的「請款/收支」兩個選項就是這樣，永遠 0 筆）。 */
function _syncFilterOptions() {
    const cat = document.getElementById('cash-filter-cat');
    if (cat) {
        // 🔴 詞彙要**累積**，不能每次拿當下篩出來的結果重建 —— 那樣選了「專案雜支」
        // 之後選單裡就只剩「專案雜支」一個選項，再也切不到別的類別（2026-08-20 實測：
        // 從專案雜支切轉存變成整個篩選被清空）。首次載入是未篩選的，詞彙那時就齊了；
        // 之後只增不減。
        _entries.forEach(e => { if (e.category) _catVocab.add(e.category); });
        const used = [..._catVocab].sort();
        cat.innerHTML = '<option value="">全部類別</option>'
            + used.map(v => `<option value="${_esc(v)}"${v === _filters.category ? ' selected' : ''}>${_esc(v)}</option>`).join('');
    }
    _renderAcctTabs();
}

/** 帳戶切換列：總表 + 每個帳戶各一顆，附當下餘額。
 *  餘額由後端 /finance/bank-accounts 算（current_balance）—— 前端不自己加總，
 *  那樣只會算到「目前篩出來的列」，切到單一帳戶時總表數字就會跟著縮水。 */
function _renderAcctTabs() {
    const box = document.getElementById('cash-acct-tabs');
    if (!box) return;
    if (!_bankAccounts || !_bankAccounts.length) { box.innerHTML = ''; return; }
    const money = (n) => (n == null ? '—'
        : `<span class="${n < 0 ? 'neg' : 'pos'}">$${_fmtNum(n)}</span>`);
    const total = _bankAccounts.reduce((sum, a) => sum + (a.current_balance || 0), 0);
    const tab = (id, name, bal) => `
        <button type="button" class="cash-acct-tab${String(id) === _filters.bank_account_id ? ' active' : ''}"
                data-acct="${_esc(id)}">${_esc(name)}<b>${money(bal)}</b></button>`;
    box.innerHTML = tab('', '總表', total)
        + _bankAccounts.map(a => tab(a.id, a.name, a.current_balance)).join('');
    box.querySelectorAll('[data-acct]').forEach(btn => {
        btn.addEventListener('click', () => {
            _filters.bank_account_id = btn.dataset.acct;
            loadEntries();
        });
    });
}

function renderList() {
    const body = document.getElementById('cash-list-body');
    if (!body) return;
    _sorter.attach();
    if (_entries.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無收支紀錄${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    body.innerHTML = _sorter.sorted(_entries).map(e => `
        <div class="crm-row${e.id === _selectedId ? ' selected' : ''}" onclick="window._cashSelect('${e.id}')">
            <div class="crm-row-date">${e.entry_date ? e.entry_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-name">${_esc(e.summary)}</div>
            <div style="color:#86efac;">${e.deposit ? '$' + _fmtNum(e.deposit) : ''}</div>
            <div style="color:#fca5a5;">${((e.expense || 0) + (e.bank_fee || 0)) ? '$' + _fmtNum((e.expense || 0) + (e.bank_fee || 0)) : ''}</div>
            <div>${_esc(e.category || '')}</div>
            <div title="${_esc(_flat(e.note, ' '))}">${_esc(_flat(e.note, ' · '))}</div>
            <div>${_esc(e.project_name || '')}</div>
            <div>${_esc(e.invoice_title || '')}</div>
            <div>${_esc(_acctName(e.bank_account_id))}</div>
            ${kebabMenuHtml(e.id, { onEdit: '_cashEdit', onDuplicate: '_cashDup', onDelete: '_cashDelete' })}
        </div>
    `).join('');
}

// ── Detail Panel ────────────────────────────────────────────

// 「哪些類別可以連結專案」由後端供應（/cash-entries/options，比照零用金的
// /petty/options）。這裡的值只是**斷線時的 fallback** —— 規則的正本在
// core/project_link.py，前端寫死一份就會跟後端漂（同一個問題本來散在三個檔案）。
let _LINKABLE = ['專案', '專案雜支', '專案外包'];
// 類別下拉的選項（正本是後端 finance_category_map）。這裡的值只是斷線 fallback ——
// 寫死一份就會跟種子脫節：貸款繳款／貸款補貼／銀行借款 就是這樣漏掉的，
// 結果對帳單匯入自己寫出來的列，使用者在編輯視窗選不到它的類別。
let _CATEGORIES = ['水電網路', '交際應酬', '行政', '其他', '其他收入', '房租', '建構',
    '專案', '專案外包', '專案雜支', '教育訓練', '設備耗材', '設備維護', '軟體網路服務',
    '勞健保', '發票代開', '會計', '業務推廣', '製作金', '銀行利息', '獎金', '請款單',
    '辦公室管理費', '營所稅', '營業稅', '薪資', '轉存'];

async function _loadCashOptions() {
    try {
        const o = await _fetch('/cash-entries/options?entity=' + _pinEntity());
        if (o.project_link_categories?.length) _LINKABLE = o.project_link_categories;
        if (o.categories?.length) _CATEGORIES = o.categories;
    } catch (_) { /* 用 fallback，不擋畫面 */ }
}

/** 專案／發票的即時連結下拉。直接打 PUT /cash-entries/{id} 只送要改的那一欄 ——
 *  該端點是 exclude_unset 部分更新，沒送的欄位不會被動到。 */
function _renderQuickLink(e) {
    const box = document.getElementById('cash-quicklink');
    if (!box) return;
    const row = (label, id, optsHtml) => `
        <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
            <span style="color:#9ca3af;font-size:12px;flex:0 0 48px;">${label}</span>
            <select id="${id}" style="flex:1;min-width:0;">${optsHtml}</select>
        </div>`;
    box.innerHTML = `
        ${row('專案', 'cash-ql-proj', _projOptsHtml(e.project_id, '— 未連結 —'))}
        ${/* 發票下拉只在支出列出現：收入列的發票走上面可掛多張的「關聯發票」區 */ ''}
        ${e.deposit ? '' : row('發票', 'cash-ql-inv', _invOptsHtml(e.invoice_id, '— 未連結 —'))}
        <div id="cash-ql-msg" style="font-size:11px;color:#666;margin-top:5px;">選了就直接存</div>`;

    const save = async (patch) => {
        const msg = document.getElementById('cash-ql-msg');
        msg.textContent = '儲存中…';
        msg.style.color = '#888';
        try {
            await _fetch('/cash-entries/' + e.id,
                { method: 'PUT', body: JSON.stringify(patch) });
            msg.textContent = '已儲存';
            msg.style.color = '#86efac';
            await loadEntries();
        } catch (err) {
            msg.textContent = err.message;
            msg.style.color = '#fca5a5';
        }
    };
    const pj = document.getElementById('cash-ql-proj');
    if (pj) pj.addEventListener('change', () => save({ project_id: pj.value }));
    const iv = document.getElementById('cash-ql-inv');
    if (iv) iv.addEventListener('change', () => save({ invoice_id: iv.value }));
}

function renderDetail(e) {
    document.getElementById('cash-detail-title').textContent = e.summary;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    const section = (title) => `<div class="crm-detail-section">${title}</div>`;

    let html = '';
    // 收支
    html += prop('日期', e.entry_date ? e.entry_date.substring(0, 10) : '');
    if (e.deposit) html += prop('收入', '$' + _fmtNum(e.deposit));
    if (e.expense) html += prop('支出', '$' + _fmtNum(e.expense));
    html += prop('內容', e.summary);
    if (e.category) html += prop('類別', e.category);
    if (e.note) html += prop('備註', e.note);

    html += prop('帳戶', _acctName(e.bank_account_id));
    if (e.bank_fee) html += prop('匯費', '$' + _fmtNum(e.bank_fee));

    // 關聯資訊：**有值才印**。收支明細絕大多數列的專案/發票/客戶都是空的，
    // 每列都掛三四行「空」只是把真正有內容的欄目往下擠。
    const matchedClient = _clientList.find(c => {
        const note = (c.payment_note || '').trim();
        return note && e.summary && (e.summary.includes(note) || note.includes(e.summary));
    });
    const clientLabel = matchedClient
        ? matchedClient.short_name + (matchedClient.payment_info ? ' (' + matchedClient.payment_info + ')' : '')
        : '';
    const isProjectish = _LINKABLE.includes(e.category || '');
    const showInvoiceProp = !e.deposit;   // 收入列的發票由下面「關聯發票」區負責，不重複列
    const rel = [
        // 有快速連結下拉時就不印唯讀版 —— 下拉本身已經顯示目前選的是什麼，
        // 印兩份會變成「專案 X」下面又一個「專案」標籤
        ...(isProjectish ? [] : [['專案', e.project_name || '']]),
        ...(showInvoiceProp && !isProjectish ? [['發票', e.invoice_title || '']] : []),
        ['客戶', clientLabel],
        ...(e.advance_payment_id ? [['預支關聯', '已關聯']] : []),
    ].filter(([, v]) => v);
    if (rel.length || isProjectish) {
        html += section('關聯資訊');
        rel.forEach(([k, v]) => { html += prop(k, v); });
        // 專案／專案雜支：直接在詳情面板掛下拉連結，不用先按編輯
        if (isProjectish) html += '<div id="cash-quicklink"></div>';
    }

    // 關聯發票（合併匯款 / 分期收款）—— 只有收入列有，內容由 loadCashInvoiceAllocs
    // 非同步填。舊的「單張發票驗算」被這區塊取代：它只看得到一張發票，客戶合併
    // 匯款時必然報「不平衡」，等於在對的資料上顯示假警告。
    if (e.deposit) {
        html += section('關聯發票');
        html += '<div id="cash-alloc-box" style="font-size:12px;color:#888;">載入中…</div>';
    }
    // 關聯請款單（合併匯款 / 分次支付）—— 只有支出列有。出納統一匯款時一個人的
    // 多張請款單常併成一筆匯出，payment_request_id 一對一掛不上去（實測生產
    // 322 筆結清請款單的收支，硬連結一筆都沒有）。
    if (e.expense) {
        html += section('關聯請款單');
        html += '<div id="cash-pay-box" style="font-size:12px;color:#888;">載入中…</div>';
    }

    document.getElementById('cash-detail-content').innerHTML = html;
    if (_LINKABLE.includes(e.category || '')) _renderQuickLink(e);
    if (e.deposit) loadCashInvoiceAllocs(e.id);
    if (e.expense) loadCashPaymentAllocs(e.id);

    const actions = document.getElementById('cash-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('cash-bar-actions', () => {
        const editData = { ...e, invoice_id: e.invoice_id || '', project_id: e.project_id || '', bank_fee: e.bank_fee || 0,
            bank_account_id: e.bank_account_id != null ? String(e.bank_account_id) : '' };
        enableInlineEdit('cash-detail-content', 'cash-bar-actions', _buildEditFields(e.bank_account_id, e.invoice_id), editData,
            async (payload) => {
                _cleanPayload(payload);
                await _fetch('/cash-entries/' + e.id, { method: 'PUT', body: JSON.stringify(payload) });
                await loadEntries();
                const updated = _entries.find(x => x.id === e.id);
                renderDetail(updated || e);
            },
            () => renderDetail(e)
        );
    });
}

async function selectEntry(id) {
    _selectedId = id; renderList();
    document.getElementById('cash-detail-panel').style.display = 'flex';
    document.getElementById('cash-resize-handle').style.display = '';
    const e = _entries.find(x => x.id === id);
    if (e) renderDetail(e);
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('cash-detail-panel').style.display = 'none';
    document.getElementById('cash-resize-handle').style.display = 'none';
    renderList();
}

// ── Edit Fields (for inline edit) ───────────────────────────

/** 下拉 option 標記的單一正本 —— 這三個下拉（詳情快速連結、編輯表單、modal）
 *  本來各自把同一串 <option> 寫一遍，改標籤要改三處。 */
function _invOptsHtml(selectedId, placeholder = '— 不關聯 —') {
    return `<option value="">${placeholder}</option>` + _invoiceCandidates(selectedId).map(inv =>
        `<option value="${_esc(inv.id)}"${inv.id === selectedId ? ' selected' : ''}>${_esc(_invoiceLabel(inv))}</option>`).join('');
}

const _projOptsHtml = (selectedId, placeholder = '— 不關聯 —') =>
    projectOptionsHtml(_projectList, placeholder, selectedId);

/** 這張發票還差多少錢沒收。outstanding 由後端從收支分配算（GET /invoices），
 *  沒有這欄的舊回應退回面額。 */
function _outstanding(inv) {
    return inv.outstanding != null ? inv.outstanding : (inv.amount_total || 0);
}

/** 收齊了沒。
 *  🔴 讀後端的 settled，不要自己用 `outstanding <= 0` 判 —— 真正的規則是
 *  invoice_is_settled（含 NT$50 匯費容差，394 張歷史發票裡有 42 張靠它）。
 *  自己判的話，被匯費短收 30 元的那張會在應收帳款「收齊」、在這裡「尚欠 $30」。 */
function _settled(inv) {
    return inv.settled != null ? !!inv.settled : _outstanding(inv) <= 0;
}

/** 發票下拉的候選清單：**還沒收齊的**才列（394 張裡多數早就結案，全倒進下拉
 *  等於在已結案名單裡大海撈針）。
 *
 *  🔴 判準用 outstanding（面額 − 實收）而**不是** payment_status 字串：
 *  「已收款」只要收到第一筆就會被標上（sync_invoice_paid 與 _mark_invoice_received
 *  都只看有沒有收款、不看金額），拿它當濾網會讓「10 萬收了 4 萬」的發票從下拉裡
 *  消失 —— 而那正是要用這個下拉記第二期的時候。作廢仍排除（終態，不是欠款）。
 *
 *  🔴 `keepId` 一定要留：編輯既有收支時它掛的那張多半已經收齊，濾掉的話下拉
 *  選不到目前值，一存檔就把關聯洗掉。
 */
function _invoiceCandidates(keepId) {
    return _invoiceList.filter(inv =>
        inv.issue_status === '已開立'
        && (inv.payment_status || '') !== '作廢'
        && (!_settled(inv) || inv.id === keepId));
}

/** 下拉顯示：內容 · 金額 · 客戶 · 發票號碼 —— 四項一起才分得出同名同額的兩張
 *  （實測「台灣歐姆龍 第五、六品 $166,950」與「第7品 $166,950」只差品名）。
 *  收過一部分的另外標出尚欠多少，分期時才知道這次該填多少。 */
function _invoiceLabel(inv) {
    const parts = [inv.title || '(無標題)', '$' + (inv.amount_total || 0).toLocaleString('zh-TW')];
    if (inv.company_name) parts.push(inv.company_name);
    if (inv.invoice_number) parts.push(inv.invoice_number);
    const left = _outstanding(inv);
    if (_settled(inv)) parts.push('已結清');
    else if (inv.collected) parts.push(`已收 $${_fmtNum(inv.collected)}，尚欠 $${_fmtNum(left)}`);
    return parts.join(' · ');
}

function _buildEditFields(currentBankAccountId, currentInvoiceId) {
    const invoiceOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _invoiceCandidates(currentInvoiceId).map(inv =>
            ({value:inv.id, label:_invoiceLabel(inv)})));
    const projectOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _projectList.map(p => ({value:p.id, label:p.name + (p.client_short_name ? ' (' + p.client_short_name + ')' : '')})));
    const catOpts = [''].concat(_CATEGORIES).map(v => ({ value: v, label: v || '—' }));
    const fields = [
        {name:'entry_date', label:'日期', type:'date'},
        {name:'deposit', label:'收入', type:'number'},
        {name:'expense', label:'支出', type:'number'},
        {name:'summary', label:'內容', type:'text'},
        {name:'category', label:'類別', type:'select', options:catOpts},
        {name:'note', label:'備註', type:'text'},
        {name:'project_id', label:'專案', type:'select', options:projectOpts},
        {name:'invoice_id', label:'發票', type:'select', options:invoiceOpts},
        {name:'bank_fee', label:'匯費', type:'number'},
    ];
    // 帳戶（財務模組）— 清單載入成功才提供（降級時不出現，PUT payload 不含此鍵、不洗掉既有值）
    if (_bankAccounts && _bankAccounts.length) {
        fields.push({name:'bank_account_id', label:'帳戶', type:'select',
            options: _bankAcctOpts(String(currentBankAccountId ?? ''))});
    }
    return fields;
}

/** 帳戶下拉選項（含「未指定」）：停用帳戶只在「就是現值」時保留，避免洗掉既有值 */
function _bankAcctOpts(cur) {
    return [{ value: '', label: '— 未指定 —' }].concat(
        _bankAccounts.filter(a => a.active !== false || String(a.id) === cur)
            .map(a => ({ value: String(a.id), label: a.name })));
}

// ── Modal ───────────────────────────────────────────────────

const _FIELDS = ['summary', 'entry_date', 'expense', 'deposit', 'note', 'invoice_id', 'bank_fee', 'project_id', 'category', 'advance_payment_id'];
const _INT_FIELDS = ['expense', 'deposit', 'bank_fee'];
const _DATE_FIELDS = ['entry_date'];

function _populateInvoiceSelect(selectedId) {
    const sel = document.getElementById('cash-f-invoice_id');
    if (!sel) return;
    sel.innerHTML = _invOptsHtml(selectedId);
}

/** 新增/編輯 modal 的類別下拉（HTML 不再寫死選項）。 */
function _populateCategorySelect(selected) {
    const sel = document.getElementById('cash-f-category');
    if (!sel) return;
    sel.innerHTML = '<option value="">—</option>' + _CATEGORIES.map(v =>
        `<option value="${_esc(v)}"${v === selected ? ' selected' : ''}>${_esc(v)}</option>`).join('');
}

function _populateProjectSelect(selectedId) {
    const sel = document.getElementById('cash-f-project_id');
    if (!sel) return;
    sel.innerHTML = _projOptsHtml(selectedId);
}

function _populateBankAccountSelect(selectedId) {
    // selectedId：null = 新增（預設選 is_default 帳戶）；'' / id = 編輯帶入現值
    const field = document.getElementById('cash-field-bankaccount');
    const sel = document.getElementById('cash-f-bank_account_id');
    if (!field || !sel) return;
    if (!_bankAccounts || _bankAccounts.length === 0) { field.style.display = 'none'; return; }
    field.style.display = '';
    const def = _bankAccounts.find(a => a.is_default && a.active !== false);
    const cur = (selectedId == null) ? (def ? String(def.id) : '') : String(selectedId);
    sel.innerHTML = _bankAcctOpts(cur)
        .map(o => `<option value="${o.value}"${o.value === cur ? ' selected' : ''}>${_esc(o.label)}</option>`).join('');
}

// 這裡本來有 _updateVerifyRow()：把「發票面額 = 收入 + 匯費」畫成一行綠/紅字。
// 已刪 —— 它只看得到**一張**發票，客戶合併匯款（一筆錢對三張發票）時必然報
// 「✗ 差額 $…」，在完全正確的資料上顯示假警告。詳情面板早就改用「關聯發票」
// 分配區（後端 _alloc_verdict 是唯一正本，看得到全部分配），編輯視窗這份是漏改的。

function _updateClientMatch() {
    const el = document.getElementById('cash-client-match');
    if (!el) return;
    if (_clientList.length === 0) { el.textContent = '—'; return; }
    const summary = (document.getElementById('cash-f-summary')?.value || '').trim();
    const note = (document.getElementById('cash-f-note')?.value || '').trim();
    const text = summary + ' ' + note;
    if (!text.trim()) { el.textContent = '—'; return; }
    // 用「內容」和「備註」比對客戶的「匯款備註」（payment_note）
    const matched = _clientList.find(c => {
        const pn = (c.payment_note || '').trim();
        if (!pn) return false;
        return text.includes(pn) || pn.includes(summary) || (note && pn.includes(note));
    });
    if (matched) {
        el.innerHTML = `<span style="color:#86efac;">${_esc(matched.short_name)}</span>` +
            (matched.payment_info ? ` <span style="color:#6b7280;font-size:11px;">(${_esc(matched.payment_info)})</span>` : '');
    } else {
        el.textContent = '—';
    }
}

function openModal(e = null) {
    _editingId = e ? e.id : null;
    document.getElementById('cash-modal-title').textContent = e ? '編輯收支' : '新增收支';
    const err = document.getElementById('cash-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populateInvoiceSelect(e?.invoice_id || '');
    _populateProjectSelect(e?.project_id || '');
    _populateCategorySelect(e?.category || '');
    _populateBankAccountSelect(e ? (e.bank_account_id ?? '') : null);
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        if (!el) continue;
        if (f === 'invoice_id' || f === 'project_id') continue; // already populated
        if (_DATE_FIELDS.includes(f) && e?.[f]) el.value = e[f].substring(0, 10);
        else el.value = e ? (e[f] ?? '') : '';
    }
    if (!e) {
        document.getElementById('cash-f-entry_date').value = today();
        document.getElementById('cash-f-bank_fee').value = '0';
    }
    _updateClientMatch();
    // Reset advance fields + toggle project/invoice visibility
    var cat = e ? (e.category || '') : '';
    _toggleAdvanceFields(cat === '專案雜支');
    var advCheck = document.getElementById('cash-f-advance-check');
    if (advCheck) advCheck.checked = !!(e && e.advance_payment_id);
    var advList = document.getElementById('cash-advance-list');
    if (advList) advList.style.display = (e && e.advance_payment_id) ? 'block' : 'none';
    var advHidden = document.getElementById('cash-f-advance_payment_id');
    if (advHidden) advHidden.value = (e && e.advance_payment_id) || '';
    document.getElementById('cash-modal').style.display = 'flex';
}

function _cleanPayload(payload) {
    for (const f of _INT_FIELDS) {
        const v = payload[f];
        payload[f] = (v !== '' && v != null) ? parseInt(v) || 0 : null;
    }
    for (const f of _DATE_FIELDS) payload[f] = payload[f] || null;
    payload.invoice_id = payload.invoice_id || null;
    payload.project_id = payload.project_id || null;
    if ('bank_account_id' in payload) payload.bank_account_id = payload.bank_account_id || null;
    payload.summary = payload.summary || '';
    payload.note = payload.note || '';
    payload.category = payload.category || '';
}

async function saveEntry() {
    const summary = document.getElementById('cash-f-summary').value.trim();
    if (!summary) { _showErr('內容為必填'); return; }
    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        payload[f] = el ? el.value.trim() : '';
    }
    // 帳戶（財務模組）— 清單載入成功才送；降級時不含此鍵，避免把既有值洗掉
    if (_bankAccounts && _bankAccounts.length) {
        const bel = document.getElementById('cash-f-bank_account_id');
        if (bel) payload.bank_account_id = bel.value;
    }
    // 帳本（兩本帳）— pin 是 'mine' 才帶；parent 不送（後端 None→parent，PUT 不洗欄位）
    if (_pinEntity() === 'mine') payload.entity = 'mine';
    _cleanPayload(payload);
    // 專案雜支 + 預支關聯 → 自動帶入預支款的專案
    if (payload.advance_payment_id && !payload.project_id) {
        var selRadio = document.querySelector('input[name="advance-select"]:checked');
        if (selRadio && selRadio.dataset.projectId) {
            payload.project_id = selRadio.dataset.projectId;
        }
    }
    const btn = document.getElementById('cash-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId) await _fetch('/cash-entries/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
        else await _fetch('/cash-entries', { method: 'POST', body: JSON.stringify(payload) });
        // 發款/收款狀態由後端自動計算，不需手動更新
        document.getElementById('cash-modal').style.display = 'none';
        await Promise.all([loadEntries(), _loadInvoiceList()]);
    } catch (e) { _showErr(e.message); }
    finally { btn.disabled = false; btn.textContent = '儲存'; }
}

async function deleteEntry(e) {
    if (!confirm(`確定刪除「${e.summary}」？`)) return;
    try { await _fetch('/cash-entries/' + e.id, { method: 'DELETE' }); closeDetail(); await loadEntries(); }
    catch (err) { alert(err.message); }
}

window._cashToggleAdvance = async function(checked) {
    var list = document.getElementById('cash-advance-list');
    var hiddenInput = document.getElementById('cash-f-advance_payment_id');
    if (!list) return;
    if (!checked) {
        list.style.display = 'none';
        if (hiddenInput) hiddenInput.value = '';
        return;
    }
    list.style.display = 'block';
    list.innerHTML = '<div style="padding:8px;color:#9ca3af;font-size:11px;">載入中...</div>';
    try {
        var data = await _fetch('/payments/advances?returned=0');
        var advances = data.advances || [];
        if (advances.length === 0) {
            list.innerHTML = '<div style="padding:8px;color:#6b7280;font-size:11px;">無預支款</div>';
            return;
        }
        var html = '<div style="font-size:12px;color:#6b7280;margin-bottom:6px;">選擇預支款：</div>';
        for (var i = 0; i < advances.length; i++) {
            var a = advances[i];
            var balance = (a.balance != null) ? a.balance : a.amount - a.expense_total;
            var balanceText = balance > 0 ? '需還款 $' + _fmtNum(balance) : balance < 0 ? '需補款 $' + _fmtNum(Math.abs(balance)) : '已結清';
            var balanceColor = balance > 0 ? '#fb923c' : balance < 0 ? '#fca5a5' : '#86efac';
            var payTag = a.is_paid ? '<span style="color:#86efac;font-size:10px;">已發款</span>' : '<span style="color:#6b7280;font-size:10px;">未發款</span>';
            var retTag = a.is_settled ? '<span style="color:#86efac;font-size:10px;">已結清</span>' : a.is_returned ? '<span style="color:#fb923c;font-size:10px;">已收款</span>' : '';
            html += '<label style="display:flex;align-items:flex-start;gap:8px;padding:8px;border:1px solid #2e2e2e;border-radius:6px;margin-bottom:4px;cursor:pointer;background:#1a1a1a;">';
            html += '<input type="radio" name="advance-select" value="' + a.id + '" data-project-id="' + _esc(a.project_id) + '" style="margin-top:3px;" onchange="document.getElementById(\'cash-f-advance_payment_id\').value=this.value;">';
            html += '<div style="flex:1;">';
            html += '<div style="font-weight:600;color:#d1d5db;">' + _esc(a.project_name) + ' — ' + _esc(a.payee_name) + ' ' + payTag + ' ' + retTag + '</div>';
            html += '<div style="font-size:11px;color:#6b7280;">預支 $' + _fmtNum(a.amount) + '　支出 $' + _fmtNum(a.expense_total) + '　<span style="color:' + balanceColor + ';">' + balanceText + '</span></div>';
            html += '</div></label>';
        }
        list.innerHTML = html;
    } catch (e) {
        list.innerHTML = '<div style="padding:8px;color:#fca5a5;">載入失敗</div>';
    }
};

function _showErr(msg) { const el = document.getElementById('cash-modal-error'); el.textContent = msg; el.style.display = 'block'; }

// ── CSV Import ──────────────────────────────────────────────

function openImportModal() {
    _csvFile = null;
    document.getElementById('cash-drop-filename').textContent = '';
    const r = document.getElementById('cash-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('cash-btn-do-import').disabled = true;
    document.getElementById('cash-import-modal').style.display = 'flex';
}
function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('cash-drop-filename').textContent = file ? file.name : '';
    document.getElementById('cash-btn-do-import').disabled = !file;
}
async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('cash-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        const form = new FormData(); form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/cash-entries/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '匯入失敗');
        const data = await res.json();
        const result = document.getElementById('cash-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        result.style.display = 'block';
        await loadEntries();
    } catch (e) {
        const result = document.getElementById('cash-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally { btn.disabled = false; btn.textContent = '開始匯入'; }
}

// ── Init ────────────────────────────────────────────────────

export async function initCrmCashbookTab() {
    for (const id of ['cash-modal', 'cash-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
    window._cashSelect = selectEntry;
    window._cashRefresh = loadEntries;
    window._cashEdit = (id) => { const e = _entries.find(x => x.id === id); if (e) openModal(e); };
    window._cashDelete = (id) => { const e = _entries.find(x => x.id === id); if (e) deleteEntry(e); };
    window._cashDup = (id) => {
        const e = _entries.find(x => x.id === id);
        if (e) { openModal(e); _editingId = null; document.getElementById('cash-modal-title').textContent = '複製收支'; }
    };

    let _t;
    document.getElementById('cash-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadEntries, 300);
    });
    document.getElementById('cash-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadEntries(); });
    document.getElementById('cash-filter-dir').addEventListener('change', e => { _filters.direction = e.target.value; loadEntries(); });

    document.getElementById('cash-btn-add').addEventListener('click', () => openModal());
    document.getElementById('cash-btn-import').addEventListener('click', openImportModal);
    // 對帳系統：整套（上傳對帳單／分類規則／對帳工作台／核對餘額）都在
    // finance 的 banking 子視圖裡。這裡**不複製一份** —— 複製就會有兩個畫面
    // 各講各的（分類規則尤其致命）。直接點側欄那顆，走既有的 lazy-load 與
    // active 狀態切換，一行導覽而已。
    const recon = document.getElementById('cash-btn-recon');
    if (recon) {
        recon.addEventListener('click', () => {
            const btn = document.querySelector('.finance-nav-btn[data-subview="banking"]');
            if (btn) {
                btn.click();
                btn.scrollIntoView({ block: 'nearest' });
            } else {
                // 不在財務管理 tab 裡（例如未來被別處嵌入）—— 出聲，不要靜默沒反應
                alert('找不到「銀行帳戶」子視圖 —— 請從財務管理 › 銀行與設定進入。');
            }
        });
    }
    document.getElementById('cash-btn-save').addEventListener('click', saveEntry);
    document.getElementById('cash-detail-close').addEventListener('click', closeDetail);
    document.getElementById('cash-btn-do-import').addEventListener('click', doImport);

    // Show/hide advance section + project/invoice based on category
    var catEl = document.getElementById('cash-f-category');
    if (catEl) catEl.addEventListener('change', function() { _toggleAdvanceFields(this.value === '專案雜支'); });

    // Modal dynamic: client match
    for (const id of ['cash-f-invoice_id', 'cash-f-deposit', 'cash-f-bank_fee', 'cash-f-summary', 'cash-f-note']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input',
            _updateClientMatch);
    }

    document.getElementById('cash-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('cash-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over');
        const f = e.dataTransfer.files[0]; if (f && f.name.endsWith('.csv')) _setCsvFile(f); });

    for (const id of ['cash-modal', 'cash-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('cash-resize-handle', 'cash-detail-panel');
    // 🔴 loadEntries 這裡**不畫**：這幾支是並行的，它內部的 renderList 幾乎一定跑在
    // _loadBankAccounts 回來之前 —— 那時 _bankAccounts 還是 null，帳戶切換列畫不出來、
    // 列表的「帳戶」欄也是空的。等全部到齊再畫一次就好（畫兩次是 1,600 列 ×2）。
    await Promise.all([loadEntries({ render: false }), _loadInvoiceList(), _loadProjectList(),
                       _loadClientList(), _loadBankAccounts(), _loadCashOptions()]);
    // 🔴 這裡要連 _syncFilterOptions 一起補畫，不是只有 renderList。
    // 帳戶切換列由 _syncFilterOptions → _renderAcctTabs 畫，而 loadEntries 內部那次
    // 幾乎一定跑在 _loadBankAccounts 回來之前（六支並行）—— 那時 _bankAccounts 還是
    // null，_renderAcctTabs 會把容器清成空字串，然後就再也沒有人重畫它。
    // 2026-08-20 實測：三顆帳戶鈕與總表全部消失，而且時好時壞（看誰先回來）。
    _syncFilterOptions();
    renderList();
}

// ── 關聯發票（合併匯款 / 分期收款）──────────────────────────
//
// 一筆收款可以掛多張發票、各自帶分配金額。金額檢查由後端算（唯一正本，
// 前端不重算一次規則 —— 兩邊各寫一套遲早會講出不同的話）。
//
// 只有收入列才有這區：支出列掛發票是代開付出去那側，語意不同，不在這裡管。

const _CASH_ALLOC = { entryId: null, items: [], check: null };

/** 狀態列。合計即時反映畫面上的數字；**判讀語**只在畫面與後端一致時才顯示 ——
 *  改了還沒存就把舊判讀掛在新數字旁邊，等於拿過期的話騙人。判讀規則的唯一
 *  正本在後端（_alloc_verdict），前端不重寫一份。 */
function _allocStatusLine(check, items) {
    if (!check) return '';
    const live = items.reduce((n, x) => n + (Number(x.amount) || 0), 0);
    const dirty = live !== check.allocated;
    const color = dirty ? '#fbbf24' : _allocColor(check.state);
    const tail = dirty ? '尚未儲存 —— 存檔後才會重新檢查' : _esc(check.message);
    return `<div style="margin-top:8px;font-size:12px;color:${color};">`
        + `分配 $${_fmtNum(live)} / 實收 $${_fmtNum(check.received)} —— ${tail}</div>`;
}

function _allocColor(state) {
    return { ok: '#86efac', fee: '#fbbf24', over: '#fca5a5',
             under: '#fca5a5', empty: '#888' }[state] || '#888';
}

async function loadCashInvoiceAllocs(entryId) {
    _CASH_ALLOC.entryId = entryId;
    try {
        const r = await _fetch(`/cash-entries/${entryId}/invoices`);
        _CASH_ALLOC.items = r.items || [];
        _CASH_ALLOC.check = r.check || null;
    } catch (e) {
        _CASH_ALLOC.items = [];
        _CASH_ALLOC.check = { state: 'error', message: '讀取失敗：' + e.message };
    }
    _renderCashAllocs();
}

function _renderCashAllocs() {
    const box = document.getElementById('cash-alloc-box');
    if (!box) return;
    const { items, check } = _CASH_ALLOC;
    const rows = items.map((it, i) => `
        <div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #2a2a2a;">
            <div style="flex:1;min-width:0;">
                <div style="color:#ddd;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                    ${_esc(it.title || '(無標題)')}${it.missing ? ' <span style="color:#fca5a5;">（發票已刪除）</span>' : ''}</div>
                <div style="color:#888;font-size:11px;">
                    ${_esc(it.invoice_number || '無號碼')} · 發票 $${_fmtNum(it.amount_total)}
                    ${it.collected != null ? ` · 這張總共已收 $${_fmtNum(it.collected)}` : ''}
                    ${(it.outstanding || 0) > 0 ? `<span style="color:#fbbf24;">，尚欠 $${_fmtNum(it.outstanding)}</span>` : ''}</div>
            </div>
            <input type="number" value="${it.amount}" data-alloc-i="${i}"
                   style="width:96px;text-align:right;background:#1a1a1a;border:1px solid #333;
                          color:#eee;border-radius:4px;padding:3px 6px;font-size:12px;">
            <button data-alloc-del="${i}" title="移除這張"
                    style="background:none;border:none;color:#888;cursor:pointer;font-size:14px;">✕</button>
        </div>`).join('');
    box.innerHTML = `
        ${rows || '<div style="color:#666;font-size:12px;padding:4px 0;">還沒掛任何發票</div>'}
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
            <input id="cash-alloc-search" placeholder="輸入發票號碼／抬頭／專案名稱找發票…"
                   style="flex:1;background:#1a1a1a;border:1px solid #333;color:#eee;
                          border-radius:4px;padding:4px 8px;font-size:12px;">
        </div>
        <div id="cash-alloc-results" style="max-height:150px;overflow:auto;"></div>
        ${_allocStatusLine(check, items)}
        <div style="margin-top:8px;">
            <button id="cash-alloc-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存發票分配</button>
        </div>`;

    box.querySelectorAll('[data-alloc-i]').forEach((inp) => {
        inp.addEventListener('change', () => {
            _CASH_ALLOC.items[Number(inp.dataset.allocI)].amount = Number(inp.value) || 0;
        });
    });
    box.querySelectorAll('[data-alloc-del]').forEach((btn) => {
        btn.addEventListener('click', () => {
            _CASH_ALLOC.items.splice(Number(btn.dataset.allocDel), 1);
            _renderCashAllocs();
        });
    });
    const search = box.querySelector('#cash-alloc-search');
    if (search) search.addEventListener('input', () => _allocSearch(search.value));
    const save = box.querySelector('#cash-alloc-save');
    if (save) save.addEventListener('click', () => _allocSave(save));
}

const _CASH_PAY = { entryId: null, items: [], check: null };

async function loadCashPaymentAllocs(entryId) {
    _CASH_PAY.entryId = entryId;
    try {
        const r = await _fetch(`/cash-entries/${entryId}/payments`);
        _CASH_PAY.items = r.items || [];
        _CASH_PAY.check = r.check || null;
    } catch (e) {
        _CASH_PAY.items = [];
        _CASH_PAY.check = { state: 'error', msg: '讀取失敗：' + e.message };
    }
    _renderCashPayAllocs();
}

/** 狀態列。判讀語只在畫面與後端一致時才顯示（同發票那側的理由）。 */
function _payStatusLine(check, items) {
    if (!check) return '';
    const live = items.reduce((n, x) => n + (Number(x.amount) || 0), 0);
    const dirty = live !== check.allocated;
    const color = dirty ? '#fbbf24' : _allocColor(check.state);
    const tail = dirty ? '尚未儲存 —— 存檔後才會重新檢查' : _esc(check.msg || '');
    return `<div style="margin-top:8px;font-size:12px;color:${color};">`
        + `分配 $${_fmtNum(live)} —— ${tail}</div>`;
}

function _renderCashPayAllocs() {
    const box = document.getElementById('cash-pay-box');
    if (!box) return;
    const { items, check } = _CASH_PAY;
    const rows = items.map((it, i) => `
        <div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #2a2a2a;">
            <div style="flex:1;min-width:0;">
                <div style="color:#ddd;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                    ${_esc(it.summary || '(無摘要)')}${it.missing ? ' <span style="color:#fca5a5;">（請款單已刪除）</span>' : ''}</div>
                <div style="color:#888;font-size:11px;">
                    ${_esc(it.payee_name || '無收款人')} · 單據 $${_fmtNum(it.request_total)}
                    ${it.request_paid ? ` · 這張總共已付 $${_fmtNum(it.request_paid)}` : ''}
                    ${(it.request_open || 0) > 0 ? `<span style="color:#fbbf24;">，尚欠 $${_fmtNum(it.request_open)}</span>` : ''}</div>
            </div>
            <input type="number" value="${it.amount}" data-pay-i="${i}"
                   style="width:96px;text-align:right;background:#1a1a1a;border:1px solid #333;
                          color:#eee;border-radius:4px;padding:3px 6px;font-size:12px;">
            <button data-pay-del="${i}" title="移除這張"
                    style="background:none;border:none;color:#888;cursor:pointer;font-size:14px;">✕</button>
        </div>`).join('');
    // 判為手續費時給一顆「認列成匯費」—— 那 10 元本來只是對不起來的差額，
    // 寫進 bank_fee 之後就是管理費用（bank_fee_total 那條路）。
    const feeBtn = (check && check.state === 'fee' && check.fee)
        ? `<button id="cash-pay-fee" class="crm-btn crm-btn-sm"
                   style="margin-left:8px;">把 $${_fmtNum(check.fee)} 認列成匯費</button>`
        : '';
    box.innerHTML = `
        ${rows || '<div style="color:#666;font-size:12px;padding:4px 0;">還沒掛任何請款單</div>'}
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
            <input id="cash-pay-search" placeholder="輸入收款人／摘要找請款單…"
                   style="flex:1;background:#1a1a1a;border:1px solid #333;color:#eee;
                          border-radius:4px;padding:4px 8px;font-size:12px;">
        </div>
        <div id="cash-pay-results" style="max-height:150px;overflow:auto;"></div>
        ${_payStatusLine(check, items)}
        <div style="margin-top:8px;">
            <button id="cash-pay-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存請款單分配</button>
            ${feeBtn}
        </div>`;

    box.querySelectorAll('[data-pay-i]').forEach((inp) => {
        inp.addEventListener('change', () => {
            _CASH_PAY.items[Number(inp.dataset.payI)].amount = Number(inp.value) || 0;
        });
    });
    box.querySelectorAll('[data-pay-del]').forEach((btn) => {
        btn.addEventListener('click', () => {
            _CASH_PAY.items.splice(Number(btn.dataset.payDel), 1);
            _renderCashPayAllocs();
        });
    });
    const search = box.querySelector('#cash-pay-search');
    if (search) search.addEventListener('input', () => _paySearch(search.value));
    const save = box.querySelector('#cash-pay-save');
    if (save) save.addEventListener('click', () => _paySave(save, null));
    const fee = box.querySelector('#cash-pay-fee');
    if (fee) fee.addEventListener('click', () => _paySave(fee, check.fee));
}

function _paySearch(q) {
    const out = document.getElementById('cash-pay-results');
    if (!out) return;
    q = (q || '').trim().toLowerCase();
    if (q.length < 1) { out.innerHTML = ''; return; }
    const picked = new Set(_CASH_PAY.items.map(x => x.payment_request_id));
    const hits = _paymentList.filter(p => !picked.has(p.id) && [
        p.payee_name, p.summary, p.project_label, p.category,
    ].some(v => (v || '').toLowerCase().includes(q))).slice(0, 12);
    out.innerHTML = hits.length ? hits.map(p => `
        <div data-pay-add="${_esc(p.id)}"
             style="padding:4px 6px;cursor:pointer;font-size:12px;color:#ccc;border-bottom:1px solid #262626;">
            ${_esc(p.payee_name || '無收款人')} · ${_esc((p.summary || '').substring(0, 26))}
            <span style="color:#fbbf24;">$${_fmtNum(p.amount || 0)}</span>
            <span style="color:#666;"> · ${_esc(p.payment_status || '')}</span>
        </div>`).join('') : '<div style="color:#666;font-size:12px;padding:4px;">找不到符合的請款單</div>';
    out.querySelectorAll('[data-pay-add]').forEach((el) => {
        el.addEventListener('click', () => {
            const ap = _paymentList.find(x => x.id === el.dataset.payAdd);
            if (!ap) return;
            _CASH_PAY.items.push({
                payment_request_id: ap.id, amount: Number(ap.amount) || 0,
                summary: ap.summary || '', payee_name: ap.payee_name || '',
                request_total: Number(ap.amount) || 0, request_paid: 0,
                request_open: Number(ap.amount) || 0,
                payment_status: ap.payment_status || '', missing: false,
            });
            _renderCashPayAllocs();
        });
    });
}

async function _paySave(btn, fee) {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
        const body = {
            items: _CASH_PAY.items.map(x => ({
                payment_request_id: x.payment_request_id,
                amount: Number(x.amount) || 0,
            })),
        };
        if (fee != null) body.fee = fee;
        const r = await _fetch(`/cash-entries/${_CASH_PAY.entryId}/payments`, {
            method: 'PUT', body: JSON.stringify(body),
        });
        _CASH_PAY.items = r.items || [];
        _CASH_PAY.check = r.check || null;
        _renderCashPayAllocs();
        await loadEntries();      // 主要請款單欄與匯費可能變了
    } catch (e) {
        const box = document.getElementById('cash-pay-box');
        if (box) {
            const err = document.createElement('div');
            err.style.cssText = 'color:#fca5a5;font-size:12px;margin-top:6px;';
            err.textContent = e.message;
            box.appendChild(err);
        }
    } finally {
        btn.disabled = false;
        btn.textContent = label;
    }
}

function _allocSearch(q) {
    const out = document.getElementById('cash-alloc-results');
    if (!out) return;
    q = (q || '').trim().toLowerCase();
    if (q.length < 1) { out.innerHTML = ''; return; }
    const picked = new Set(_CASH_ALLOC.items.map(x => x.invoice_id));
    const hits = _invoiceList.filter(i => !picked.has(i.id) && [
        i.invoice_number, i.title, i.company_name, i.project_name,
    ].some(v => (v || '').toLowerCase().includes(q))).slice(0, 12);
    out.innerHTML = hits.length ? hits.map(i => `
        <div data-alloc-add="${_esc(i.id)}"
             style="padding:4px 6px;cursor:pointer;font-size:12px;color:#ccc;border-bottom:1px solid #262626;">
            ${_esc(i.invoice_number || '無號碼')} · ${_esc((i.title || '').substring(0, 26))}
            <span style="color:#fbbf24;">$${_fmtNum(i.amount_total || 0)}</span>
            ${(i.collected || 0) > 0 ? `<span style="color:#86efac;"> · 已收 $${_fmtNum(i.collected)}${(i.outstanding || 0) > 0 ? `，尚欠 $${_fmtNum(i.outstanding)}` : '（收齊）'}</span>` : ''}
            ${i.company_name ? `<span style="color:#666;"> · ${_esc(i.company_name)}</span>` : ''}
        </div>`).join('') : '<div style="color:#666;font-size:12px;padding:4px;">找不到符合的發票</div>';
    out.querySelectorAll('[data-alloc-add]').forEach((el) => {
        el.addEventListener('click', () => {
            const inv = _invoiceList.find(x => x.id === el.dataset.allocAdd);
            if (!inv) return;
            // 預設帶「還沒收的部分」而不是面額 —— 分期收款時面額會是錯的
            // （發票 100,000 已收 60,000，第三期預帶 100,000 只會讓人重打一次）。
            // 沒有收款紀錄時 outstanding 就等於面額，行為與原本一致。
            const remain = _outstanding(inv);
            _CASH_ALLOC.items.push({
                invoice_id: inv.id, amount: remain,
                invoice_number: inv.invoice_number || '', title: inv.title || '',
                amount_total: inv.amount_total || 0,
                payment_status: inv.payment_status || '', missing: false,
            });
            _renderCashAllocs();
        });
    });
}

async function _allocSave(btn) {
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
        const r = await _fetch(`/cash-entries/${_CASH_ALLOC.entryId}/invoices`, {
            method: 'PUT',
            body: JSON.stringify({
                items: _CASH_ALLOC.items.map(x => ({
                    invoice_id: x.invoice_id, amount: Number(x.amount) || 0,
                })),
            }),
        });
        _CASH_ALLOC.items = r.items || [];
        _CASH_ALLOC.check = r.check || null;
        _renderCashAllocs();
        await loadEntries();          // 主要發票欄可能變了，列表要跟著更新
    } catch (e) {
        const box = document.getElementById('cash-alloc-box');
        if (box) {
            const err = document.createElement('div');
            err.style.cssText = 'color:#fca5a5;font-size:12px;margin-top:6px;';
            err.textContent = e.message;
            box.appendChild(err);
        }
    } finally {
        btn.disabled = false;
        btn.textContent = '儲存發票分配';
    }
}
