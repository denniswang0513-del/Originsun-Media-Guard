/**
 * crm-payments.js — 請款管理子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton } from './crm-utils.js';

let _payments = [];
let _projects = [];
let _staffList = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', payment_status: '', project_id: '' };
let _csvFile = null;

async function loadPayments() {
    const params = new URLSearchParams();
    if (_filters.q)              params.set('q', _filters.q);
    if (_filters.category)       params.set('category', _filters.category);
    if (_filters.payment_status) params.set('payment_status', _filters.payment_status);
    if (_filters.project_id)     params.set('project_id', _filters.project_id);
    try { _payments = (await _fetch('/payments?' + params)).payments || []; }
    catch (_) { _payments = []; }
    renderList();
}

async function loadProjects() {
    try { _projects = (await _fetch('/projects')).projects || []; } catch(_) { _projects = []; }
    _populateProjectFilter();
}

function _populateProjectFilter() {
    const sel = document.getElementById('pay-filter-project');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">全部專案</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === current ? ' selected' : ''}>${_esc(p.name)}</option>`).join('');
}

async function loadStaffList() {
    try { _staffList = (await _fetch('/staff')).staff || []; } catch(_) { _staffList = []; }
}

function _statusBadge(s) {
    const cls = s === '已付款' ? 'crm-badge crm-pay-全額到帳' : 'crm-badge crm-pay-未到帳';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

function renderList() {
    const body = document.getElementById('pay-list-body');
    if (!body) return;
    if (_payments.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無請款${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    body.innerHTML = _payments.map(p => `
        <div class="crm-row${p.id === _selectedId ? ' selected' : ''}" onclick="window._paySelect('${p.id}')">
            <div class="crm-row-date">${p.request_date ? p.request_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-name">${_esc(p.summary)}</div>
            <div class="crm-row-amount">$${_fmtNum(p.amount)}</div>
            <div class="crm-row-cat">${_esc(p.category || '')}</div>
            <div class="crm-row-client">${_esc(p.payee_name)}</div>
            <div class="crm-row-inv">${p.category === '發票代開' && p.invoice_number ? _esc((() => { const inv = _invoiceList.find(i => i.invoice_number === p.invoice_number); return inv ? inv.title : p.invoice_number; })()) : ''}</div>
            <div class="crm-row-proj">${_esc(p.project_name || p.project_label || '')}</div>
            <div class="crm-row-status">${_statusBadge(p.payment_status)}</div>
            <div class="crm-row-actions" style="flex:0 0 50px;" onclick="event.stopPropagation()">
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._payDelete('${p.id}')">刪</button>
            </div>
        </div>
    `).join('');
}

let _currentDetail = null;

const _PAY_EDIT_FIELDS = [
    // 請款內容
    {name:'summary', label:'摘要', type:'text'},
    {name:'amount', label:'金額', type:'number'},
    {name:'category', label:'項目', type:'select', options:[
        {value:'',label:'—'},{value:'行政',label:'行政'},{value:'專案外包',label:'專案外包'},
        {value:'專案雜支',label:'專案雜支'},{value:'設備耗材',label:'設備耗材'},
        {value:'發票代開',label:'發票代開'},{value:'零用金',label:'零用金'},
        {value:'薪資',label:'薪資'},{value:'轉存',label:'轉存'},{value:'其他',label:'其他'},
    ]},
    {name:'request_date', label:'日期', type:'date'},
    // 付款資訊
    {name:'payee_name', label:'收款人', type:'text'},
    {name:'payee_id', label:'身分證', type:'text'},
    {name:'payment_status', label:'付款狀態', type:'select', options:[
        {value:'未付款',label:'未付款'},{value:'應付款',label:'應付款'},{value:'已付款',label:'已付款'},
    ]},
    {name:'payment_date', label:'付款日', type:'date'},
    {name:'planned_month', label:'預計付款月', type:'month'},
    // 補充資訊
    {name:'invoice_number', label:'發票號碼', type:'text'},
    {name:'notes', label:'附註', type:'text'},
];

function renderDetail(p) {
    _currentDetail = p;
    document.getElementById('pay-detail-title').textContent = p.summary;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    document.getElementById('pay-detail-content').innerHTML = `
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">請款內容</div>
        ${prop('摘要', p.summary)}
        ${prop('金額', '$' + _fmtNum(p.amount))}
        ${prop('項目', p.category)}
        ${prop('日期', p.request_date ? p.request_date.substring(0, 10) : '')}
        <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">付款資訊</div>
        ${prop('收款人', p.payee_name + (p.payee_id ? ' (' + p.payee_id + ')' : ''))}
        <div class="crm-detail-prop"><div class="crm-prop-label">付款狀態</div><div class="crm-prop-value">${_statusBadge(p.payment_status)}</div></div>
        ${p.payment_date ? prop('付款日', p.payment_date.substring(0, 10)) : ''}
        ${p.planned_month ? prop('預計付款月', p.planned_month) : ''}
        <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">補充資訊</div>
        ${p.category === '發票代開' && p.invoice_number ? (() => {
            const inv = _invoiceList.find(i => i.invoice_number === p.invoice_number);
            return prop('代開發票', inv ? inv.title + ' $' + (inv.amount_total||0).toLocaleString('zh-TW') : p.invoice_number);
        })() : ''}
        ${p.invoice_number ? prop('發票號碼', p.invoice_number) : ''}
        ${p.project_name ? prop('專案', p.project_name) : ''}
        ${prop('附註', p.notes)}
    `;
    // Restore action buttons to default (edit + close)
    const actions = document.getElementById('pay-bar-actions');
    if (actions) {
        actions.innerHTML = `<button id="payable-detail-close" class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('pay-bar-actions', async () => {
        if (_invoiceList.length === 0) await _loadInvoiceList();
        _startInlineEdit(p);
    });
}

function _startInlineEdit(p) {
    const content = document.getElementById('pay-detail-content');
    const actions = document.getElementById('pay-bar-actions');
    if (!content || !actions) return;

    const catOptions = ['','行政','其他','建構','專案外包','專案雜支','設備耗材','設備維護','軟體網路服務','發票代開','業務推廣','零用金','獎金','薪資','轉存']
        .map(v => `<option value="${v}"${v === (p.category||'') ? ' selected' : ''}>${v || '—'}</option>`).join('');
    const payeeOptions = `<option value="">— 選擇人員 —</option>` +
        _staffList.map(s => `<option value="${_esc(s.name)}" data-id="${_esc(s.id_number)}"${s.name === p.payee_name ? ' selected' : ''}>${_esc(s.name)} (${_esc(s.role)})</option>`).join('');
    const statusOptions = ['未付款','應付款','已付款']
        .map(v => `<option value="${v}"${v === (p.payment_status||'未付款') ? ' selected' : ''}>${v}</option>`).join('');

    content.innerHTML = `
        <div class="crm-form-section">請款內容</div>
        <div class="crm-form-grid">
            <div class="crm-field"><label>摘要</label><input id="ie-summary" type="text" class="crm-input" value="${_esc(p.summary)}"></div>
            <div class="crm-field"><label>金額</label><input id="ie-amount" type="number" class="crm-input" value="${p.amount || ''}"></div>
            <div class="crm-field"><label>項目</label><select id="ie-category" class="crm-input">${catOptions}</select></div>
            <div class="crm-field"><label>日期</label><input id="ie-request_date" type="date" class="crm-input" value="${p.request_date ? p.request_date.substring(0,10) : ''}"></div>
        </div>
        <div class="crm-form-section">付款資訊</div>
        <div class="crm-form-grid">
            <div class="crm-field"><label>收款人</label><select id="ie-payee_name" class="crm-input">${payeeOptions}</select></div>
            <div class="crm-field"><label>身分證</label><input id="ie-payee_id" type="text" class="crm-input" value="${_esc(p.payee_id)}" readonly style="opacity:0.7;"></div>
            <div class="crm-field"><label>付款狀態</label><select id="ie-payment_status" class="crm-input">${statusOptions}</select></div>
            <div class="crm-field" id="ie-date-field"><label>付款日</label><input id="ie-payment_date" type="date" class="crm-input" value="${p.payment_date ? p.payment_date.substring(0,10) : ''}"></div>
            <div class="crm-field" id="ie-planned-field" style="display:none;"><label>預計付款月</label><input id="ie-planned_month" type="month" class="crm-input" value="${_esc(p.planned_month || '')}"></div>
        </div>
        <div class="crm-form-section">補充資訊</div>
        <div class="crm-form-grid" id="ie-extra">
            <div class="crm-field" id="ie-project-field" style="display:none;"><label id="ie-project-label">專案</label><select id="ie-project_id" class="crm-input"></select></div>
            <div class="crm-field" id="ie-invoice-field" style="display:none;"><label>代開發票</label><select id="ie-invoice_sel" class="crm-input"></select></div>
            <div class="crm-field" id="ie-invnum-field" style="display:none;"><label>發票號碼</label><input id="ie-invoice_number" type="text" class="crm-input" value="${_esc(p.invoice_number || '')}"></div>
            <div class="crm-field crm-field-full"><label>附註</label><input id="ie-notes" type="text" class="crm-input" value="${_esc(p.notes || '')}"></div>
        </div>
    `;

    // Dynamic fields based on category
    function _ieUpdateExtra() {
        const cat = document.getElementById('ie-category').value;
        const status = document.getElementById('ie-payment_status').value;
        document.getElementById('ie-date-field').style.display = status === '已付款' ? '' : 'none';
        document.getElementById('ie-planned-field').style.display = status === '應付款' ? '' : 'none';
        document.getElementById('ie-project-field').style.display = _PROJECT_CATEGORIES.includes(cat) || cat === '發票代開' ? '' : 'none';
        document.getElementById('ie-invoice-field').style.display = cat === '發票代開' ? '' : 'none';
        document.getElementById('ie-invnum-field').style.display = cat === '發票代開' ? '' : 'none';

        // Update labels for invoice field
        const invLabel = document.getElementById('ie-invoice-field')?.querySelector('label');
        if (invLabel) invLabel.innerHTML = '代開發票 <span class="crm-required">*</span>';

        // Populate project dropdown directly from _projects
        const iep = document.getElementById('ie-project_id');
        if (iep) {
            iep.innerHTML = `<option value="">— 選擇專案 —</option>` +
                _projects.map(pr => `<option value="${pr.id}"${pr.id === (p.project_id||'') ? ' selected' : ''}>${_esc(pr.name)} (${_esc(pr.client_short_name || '')})</option>`).join('');
        }

        if (cat === '發票代開') {
            document.getElementById('ie-project-label').textContent = '專案';
            const invSel = document.getElementById('ie-invoice_sel');
            if (invSel) {
                const matchNum = p.invoice_number || '';
                invSel.innerHTML = `<option value="">— 選擇發票 —</option>` +
                    _invoiceList.filter(inv => inv.issue_status === '已開立').map(inv => {
                        const sel = (inv.invoice_number === matchNum || inv.id === p.project_id) ? ' selected' : '';
                        return `<option value="${inv.id}"${sel}>${_esc(inv.title)} $${(inv.amount_total||0).toLocaleString('zh-TW')} (${_esc(inv.company_name || '')})</option>`;
                    }).join('');
            }
        } else if (_PROJECT_CATEGORIES.includes(cat)) {
            document.getElementById('ie-project-label').innerHTML = '專案 <span class="crm-required">*</span>';
        }
    }
    _ieUpdateExtra();

    document.getElementById('ie-category').addEventListener('change', _ieUpdateExtra);
    document.getElementById('ie-payment_status').addEventListener('change', _ieUpdateExtra);
    document.getElementById('ie-payee_name').addEventListener('change', e => {
        const opt = e.target.selectedOptions[0];
        document.getElementById('ie-payee_id').value = opt?.dataset.id || '';
    });
    document.getElementById('ie-invoice_sel')?.addEventListener('change', e => {
        const inv = _invoiceList.find(i => i.id === e.target.value);
        if (inv) document.getElementById('ie-invoice_number').value = inv.invoice_number || '';
    });

    // Action buttons
    const closeBtn = actions.querySelector('.crm-detail-close');
    const closeHtml = closeBtn ? closeBtn.outerHTML : '';
    actions.innerHTML = `
        <button class="crm-btn crm-btn-secondary crm-btn-sm" id="_ie-cancel">取消</button>
        <button class="crm-btn crm-btn-primary crm-btn-sm" id="_ie-save">儲存</button>
        ${closeHtml}
    `;
    actions.querySelector('.crm-detail-close')?.addEventListener('click', closeDetail);
    document.getElementById('_ie-cancel').addEventListener('click', () => renderDetail(p));
    document.getElementById('_ie-save').addEventListener('click', async () => {
        const cat = document.getElementById('ie-category').value;
        // Validation
        if (_PROJECT_CATEGORIES.includes(cat) && !document.getElementById('ie-project_id')?.value) {
            alert('請選擇專案'); return;
        }
        if (cat === '發票代開' && !document.getElementById('ie-invoice_sel')?.value) {
            alert('請選擇代開發票'); return;
        }
        const btn = document.getElementById('_ie-save');
        btn.disabled = true; btn.textContent = '儲存中...';
        const payload = {
            summary: document.getElementById('ie-summary').value,
            amount: parseInt(document.getElementById('ie-amount').value) || 0,
            category: cat,
            request_date: document.getElementById('ie-request_date').value || null,
            payee_name: document.getElementById('ie-payee_name').value,
            payee_id: document.getElementById('ie-payee_id').value,
            payment_status: document.getElementById('ie-payment_status').value,
            payment_date: document.getElementById('ie-payment_date').value || null,
            planned_month: document.getElementById('ie-planned_month').value || '',
            invoice_number: document.getElementById('ie-invoice_number')?.value || '',
            project_id: document.getElementById('ie-project_id')?.value || null,
            notes: document.getElementById('ie-notes').value,
        };
        try {
            await _fetch('/payments/' + p.id, { method: 'PUT', body: JSON.stringify(payload) });
            const updated = await _fetch('/payments/' + p.id);
            renderDetail(updated);
            await loadPayments();
        } catch (e) {
            alert('儲存失敗: ' + e.message);
            btn.disabled = false; btn.textContent = '儲存';
        }
    });
}

async function selectPayment(id) {
    _selectedId = id; renderList();
    document.getElementById('pay-detail-panel').style.display = 'flex';
    document.getElementById('pay-resize-handle').style.display = '';
    if (_invoiceList.length === 0) await _loadInvoiceList();
    try { renderDetail(await _fetch('/payments/' + id)); } catch(_) {}
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('pay-detail-panel').style.display = 'none';
    document.getElementById('pay-resize-handle').style.display = 'none';
    renderList();
}

const _FIELDS = ['summary', 'amount', 'request_date', 'category', 'payee_name', 'payee_id',
    'payee_type', 'invoice_number', 'project_id',
    'payment_date', 'payment_status', 'planned_month', 'notes'];
const _DATE_FIELDS = ['request_date', 'payment_date'];
const _INT_FIELDS = ['amount'];

const _PROJECT_CATEGORIES = ['專案外包', '專案雜支'];
let _invoiceList = [];

async function _loadInvoiceList() {
    try { _invoiceList = (await _fetch('/invoices?payment_type=收款')).invoices || []; } catch(_) { _invoiceList = []; }
}

function _updateExtraFields(category) {
    const invoiceField = document.getElementById('pay-invoice-field');
    const invoiceNumField = document.getElementById('pay-invoice-number-field');
    const projectField = document.getElementById('pay-project-field');
    const dateField = document.getElementById('pay-date-field');
    const payStatus = document.getElementById('pay-f-payment_status')?.value;

    // Hide all first
    if (invoiceField) invoiceField.style.display = 'none';
    if (invoiceNumField) invoiceNumField.style.display = 'none';
    if (projectField) projectField.style.display = 'none';

    if (category === '發票代開') {
        // Show invoice fields + optional project
        if (invoiceField) invoiceField.style.display = '';
        if (invoiceNumField) invoiceNumField.style.display = '';
        if (projectField) {
            projectField.style.display = '';
            const lbl = projectField.querySelector('label');
            if (lbl) lbl.innerHTML = '專案';
        }
        // Populate invoice select
        const sel = document.getElementById('pay-f-project_id');
        if (sel) {
            const current = sel.value;
            sel.innerHTML = `<option value="">— 選擇發票 —</option>` +
                _invoiceList.filter(inv => inv.issue_status === '已開立').map(inv =>
                    `<option value="${inv.id}" data-num="${_esc(inv.invoice_number)}" data-amt="${inv.amount_total || 0}"${inv.id === current ? ' selected' : ''}>${_esc(inv.title)} $${(inv.amount_total||0).toLocaleString('zh-TW')} (${_esc(inv.company_name)})</option>`
                ).join('');
        }
    } else if (_PROJECT_CATEGORIES.includes(category)) {
        // Show project field (required)
        if (projectField) {
            projectField.style.display = '';
            const lbl = projectField.querySelector('label');
            if (lbl) lbl.innerHTML = '專案 <span class="crm-required">*</span>';
        }
        _populateProject2Select('');
    }

    // Payment date visibility
    if (dateField) dateField.style.display = (payStatus === '已付款') ? '' : 'none';

    _togglePlannedMonth(payStatus || '未付款');
}

function _populateProject2Select(selectedId) {
    const sel = document.getElementById('pay-f-project_id2');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇專案 —</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)} (${_esc(p.client_short_name || '')})</option>`).join('');
}

function _populatePayeeSelect(selectedName) {
    const sel = document.getElementById('pay-f-payee_name');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇人員 —</option>` +
        _staffList.map(s => `<option value="${_esc(s.name)}" data-id="${_esc(s.id_number)}"${s.name === selectedName ? ' selected' : ''}>${_esc(s.name)} (${_esc(s.role)})</option>`).join('');
}

function openModal(p = null) {
    _editingId = p ? p.id : null;
    document.getElementById('pay-modal-title').textContent = p ? '編輯請款' : '新增請款';
    const err = document.getElementById('pay-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populatePayeeSelect(p?.payee_name || '');
    _populateProject2Select(p?.project_id || '');
    for (const f of _FIELDS) {
        const el = document.getElementById('pay-f-' + f);
        if (!el) continue;
        if (f === 'payee_name') continue;
        if (_DATE_FIELDS.includes(f) && p?.[f]) el.value = p[f].substring(0, 10);
        else el.value = p ? (p[f] ?? '') : '';
    }
    if (!p) document.getElementById('pay-f-request_date').value = new Date().toISOString().substring(0, 10);
    _updateExtraFields(p?.category || '');
    document.getElementById('pay-modal').style.display = 'flex';
}

function _togglePlannedMonth(status) {
    const field = document.getElementById('pay-planned-month-field');
    if (field) field.style.display = status === '應付款' ? '' : 'none';
}

async function savePayment() {
    const summary = document.getElementById('pay-f-summary').value.trim();
    if (!summary) { _showErr('摘要為必填'); return; }
    const payload = {};
    const cat = document.getElementById('pay-f-category')?.value || '';
    for (const f of _FIELDS) {
        const el = document.getElementById('pay-f-' + f);
        let val = el ? el.value.trim() : '';
        if (_INT_FIELDS.includes(f)) val = val ? parseInt(val) : 0;
        if (_DATE_FIELDS.includes(f)) val = val || null;
        if (f === 'project_id') {
            // Use invoice select for 發票代開, project2 select for 專案外包/雜支
            if (cat === '發票代開') {
                val = document.getElementById('pay-f-project_id')?.value || null;
            } else {
                val = document.getElementById('pay-f-project_id2')?.value || null;
            }
        }
        payload[f] = val;
    }
    // Validation
    if (_PROJECT_CATEGORIES.includes(cat) && !document.getElementById('pay-f-project_id2')?.value) {
        _showErr('請選擇專案'); return;
    }
    if (cat === '發票代開' && !document.getElementById('pay-f-project_id')?.value) {
        _showErr('請選擇代開發票'); return;
    }
    const btn = document.getElementById('pay-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId) await _fetch('/payments/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
        else await _fetch('/payments', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('pay-modal').style.display = 'none';
        await loadPayments();
    } catch (e) { _showErr(e.message); }
    finally { btn.disabled = false; btn.textContent = '儲存'; }
}

async function deletePayment(p) {
    if (!confirm(`確定刪除「${p.summary}」？`)) return;
    try { await _fetch('/payments/' + p.id, { method: 'DELETE' }); closeDetail(); await loadPayments(); }
    catch (e) { alert(e.message); }
}

function _showErr(msg) { const el = document.getElementById('pay-modal-error'); el.textContent = msg; el.style.display = 'block'; }

// CSV Import
function openImportModal() {
    _csvFile = null;
    document.getElementById('pay-drop-filename').textContent = '';
    const r = document.getElementById('pay-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('pay-btn-do-import').disabled = true;
    document.getElementById('pay-import-modal').style.display = 'flex';
}
function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('pay-drop-filename').textContent = file ? file.name : '';
    document.getElementById('pay-btn-do-import').disabled = !file;
}
async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('pay-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        const form = new FormData(); form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/payments/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '匯入失敗');
        const data = await res.json();
        const result = document.getElementById('pay-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        result.style.display = 'block';
        await loadPayments();
    } catch (e) {
        const result = document.getElementById('pay-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally { btn.disabled = false; btn.textContent = '開始匯入'; }
}

export function initCrmPaymentsTab() {
    for (const id of ['pay-modal', 'pay-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
    window._paySelect = selectPayment;
    window._payRefresh = loadPayments;
    window._payEdit = async (id) => { try { openModal(await _fetch('/payments/' + id)); } catch(_) {} };
    window._payDelete = (id) => { const p = _payments.find(x => x.id === id); if (p) deletePayment(p); };

    let _t;
    document.getElementById('pay-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadPayments, 300);
    });
    document.getElementById('pay-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadPayments(); });
    document.getElementById('pay-filter-status').addEventListener('change', e => { _filters.payment_status = e.target.value; loadPayments(); });
    document.getElementById('pay-filter-project').addEventListener('change', e => { _filters.project_id = e.target.value; loadPayments(); });

    document.getElementById('pay-btn-add').addEventListener('click', () => openModal());

    // Auto-fill payee_id when payee_name changes
    document.getElementById('pay-f-payee_name').addEventListener('change', e => {
        const opt = e.target.selectedOptions[0];
        document.getElementById('pay-f-payee_id').value = opt?.dataset.id || '';
    });

    // Show/hide planned_month when payment_status changes
    // Payment status → toggle payment date + planned month
    document.getElementById('pay-f-payment_status').addEventListener('change', e => {
        _updateExtraFields(document.getElementById('pay-f-category')?.value || '');
    });

    // Category → toggle extra fields
    document.getElementById('pay-f-category').addEventListener('change', e => {
        _updateExtraFields(e.target.value);
    });

    // Invoice select → auto-fill invoice number
    document.getElementById('pay-f-project_id').addEventListener('change', e => {
        const cat = document.getElementById('pay-f-category').value;
        if (cat === '發票代開') {
            const inv = _invoiceList.find(i => i.id === e.target.value);
            if (inv) {
                document.getElementById('pay-f-invoice_number').value = inv.invoice_number || '';
                if (!document.getElementById('pay-f-amount').value) {
                    document.getElementById('pay-f-amount').value = inv.amount_total || '';
                }
            }
        }
    });
    document.getElementById('pay-btn-import').addEventListener('click', openImportModal);
    document.getElementById('pay-btn-save').addEventListener('click', savePayment);
    document.getElementById('pay-detail-close').addEventListener('click', closeDetail);
    document.getElementById('pay-btn-do-import').addEventListener('click', doImport);

    document.getElementById('pay-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('pay-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over');
        const f = e.dataTransfer.files[0]; if (f && f.name.endsWith('.csv')) _setCsvFile(f); });

    for (const id of ['pay-modal', 'pay-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('pay-resize-handle', 'pay-detail-panel');
    Promise.all([loadPayments(), loadProjects(), loadStaffList(), _loadInvoiceList()]);
}
