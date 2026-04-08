/**
 * crm-payments.js — 請款管理子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

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
        <div class="crm-row pay-row${p.id === _selectedId ? ' selected' : ''}" onclick="window._paySelect('${p.id}')">
            <span>${p.request_date ? p.request_date.substring(0, 10) : '—'}</span>
            <span style="font-weight:600;color:#e0e0e0;">${_esc(p.summary)}</span>
            <span style="font-weight:600;color:#e0e0e0;">$${_fmtNum(p.amount)}</span>
            <span>${_esc(p.category || '')}</span>
            <span>${_esc(p.payee_name)}</span>
            <span>${p.category === '發票代開' && p.invoice_number ? _esc((() => { const inv = _invoiceList.find(i => i.invoice_number === p.invoice_number); return inv ? inv.title : p.invoice_number; })()) : ''}</span>
            <span>${_esc(p.project_name || p.project_label || '')}</span>
            <span>${_statusBadge(p.payment_status)}</span>
            ${kebabMenuHtml(p.id, { onEdit: '_payEdit', onDuplicate: '_payDup', onDelete: '_payDelete' })}
        </div>
    `).join('');
}

function _buildEditFields() {
    const catOpts = ['','行政','其他','建構','專案雜支','設備耗材','設備維護','軟體網路服務','發票代開','業務推廣','零用金','獎金','薪資','轉存']
        .map(v => ({value:v, label:v || '—'}));
    const payeeTypeOpts = ['','內部人員','現金','勞報','核銷'].map(v => ({value:v, label:v || '—'}));
    const payeeOpts = [{value:'', label:'— 選擇人員 —'}].concat(
        _staffList.map(s => ({value:s.name, label:s.name + ' (' + s.role + ')'})));
    const statusOpts = [{value:'應付款',label:'應付款'},{value:'已付款',label:'已付款'}];
    const projectOpts = [{value:'',label:'— 選擇專案 —'}].concat(
        _projects.map(pr => ({value:pr.id, label:pr.name + ' (' + (pr.client_short_name || '') + ')'})));
    const invoiceOpts = [{value:'',label:'— 選擇發票 —'}].concat(
        _invoiceList.filter(inv => inv.issue_status === '已開立').map(inv => ({
            value:inv.id, label:inv.title + ' $' + (inv.amount_total||0).toLocaleString('zh-TW') + ' (' + (inv.company_name||'') + ')'
        })));

    return [
        {name:'summary', label:'摘要', type:'text'},
        {name:'amount', label:'金額', type:'number'},
        {name:'category', label:'項目', type:'select', options:catOpts},
        {name:'payee_type', label:'報支項目', type:'select', options:payeeTypeOpts, _group:'payee-type'},
        {name:'request_date', label:'日期', type:'date'},
        // 付款資訊
        {name:'payee_name', label:'收款人', type:'select', options:payeeOpts},
        {name:'_payee_id_display', label:'身分證', type:'readonly'},
        {name:'planned_month', label:'預計付款月', type:'month'},
        // 補充資訊
        {name:'_invoice_sel', label:'代開發票', type:'select', options:invoiceOpts, _group:'invoice'},
        {name:'invoice_number', label:'發票號碼', type:'text', _group:'invoice'},
        {name:'_project_sel', label:'專案', type:'select', options:projectOpts, _group:'project'},
        {name:'notes', label:'附註', type:'text'},
    ];
}

function _wireEditDynamics(p) {
    const content = document.getElementById('pay-detail-content');
    if (!content) return;
    const _fields = _buildEditFields();

    function _findRow(fieldName) {
        const f = _fields.find(x => x.name === fieldName);
        for (const row of content.querySelectorAll('.crm-detail-prop')) {
            const el = row.querySelector(`[data-field="${fieldName}"]`);
            if (el) return row;
            if (f) {
                const label = row.querySelector('.crm-prop-label');
                if (label && label.textContent.trim() === f.label) return row;
            }
        }
        return null;
    }

    const catSel = content.querySelector('[data-field="category"]');
    const payeeSel = content.querySelector('[data-field="payee_name"]');
    const invSel = content.querySelector('[data-field="_invoice_sel"]');

    const payeeTypeRow = _findRow('payee_type');
    const invoiceSelRow = _findRow('_invoice_sel');
    const invoiceNumRow = _findRow('invoice_number');
    const projectRow = _findRow('_project_sel');
    function _toggle() {
        const cat = catSel?.value || '';
        if (payeeTypeRow) payeeTypeRow.style.display = cat === '專案外包' ? '' : 'none';
        if (invoiceSelRow) invoiceSelRow.style.display = cat === '發票代開' ? '' : 'none';
        if (invoiceNumRow) invoiceNumRow.style.display = cat === '發票代開' ? '' : 'none';
        if (projectRow) projectRow.style.display = (_PROJECT_CATEGORIES.includes(cat) || cat === '發票代開') ? '' : 'none';
    }
    _toggle();

    if (catSel) catSel.addEventListener('change', _toggle);

    // 收款人 → 身分證 auto-fill
    if (payeeSel) {
        payeeSel.addEventListener('change', () => {
            const staff = _staffList.find(s => s.name === payeeSel.value);
            const idRow = _findRow('_payee_id_display');
            if (idRow) {
                const val = idRow.querySelector('.crm-prop-value');
                if (val) val.textContent = staff?.id_number || '';
            }
        });
    }

    // 發票選擇 → 自動填發票號碼
    if (invSel) {
        invSel.addEventListener('change', () => {
            const inv = _invoiceList.find(i => i.id === invSel.value);
            const numEl = content.querySelector('[data-field="invoice_number"]');
            if (inv && numEl) numEl.value = inv.invoice_number || '';
        });
    }

    // Set initial value for _invoice_sel and _project_sel
    if (invSel && p.category === '發票代開') {
        const matchInv = _invoiceList.find(i => i.invoice_number === p.invoice_number);
        if (matchInv) invSel.value = matchInv.id;
    }
    const projSel = content.querySelector('[data-field="_project_sel"]');
    if (projSel && p.project_id) projSel.value = p.project_id;
}

function renderDetail(p) {
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
        ${p.category === '專案外包' && p.payee_type ? prop('報支項目', p.payee_type) : ''}
        ${prop('日期', p.request_date ? p.request_date.substring(0, 10) : '')}
        <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">付款資訊</div>
        ${prop('收款人', p.payee_name + (p.payee_id ? ' (' + p.payee_id + ')' : ''))}
        ${prop('預計付款月', p.planned_month)}
        <div class="crm-detail-prop"><div class="crm-prop-label">付款狀態</div><div class="crm-prop-value">${_statusBadge(p.payment_status)}</div></div>
        ${p.payment_status === '已付款' && p.payment_date ? prop('付款日', p.payment_date.substring(0, 10)) : ''}
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
    addEditButton('pay-bar-actions', () => {
        const editData = { ...p,
            _payee_id_display: p.payee_id || '',
            _invoice_sel: '',
            _project_sel: p.project_id || '',
        };
        enableInlineEdit('pay-detail-content', 'pay-bar-actions', _buildEditFields(), editData,
            async (payload) => {
                payload.amount = parseInt(payload.amount) || 0;
                payload.request_date = payload.request_date || null;
                // Preserve existing payment_status and payment_date (managed by 應付帳款)
                payload.payment_status = p.payment_status || '應付款';
                payload.payment_date = p.payment_date || null;
                // Resolve project_id from the right field depending on category
                if (payload.category === '發票代開') {
                    payload.project_id = payload._invoice_sel || null;
                    const inv = _invoiceList.find(i => i.id === payload._invoice_sel);
                    if (inv) payload.invoice_number = inv.invoice_number || '';
                } else if (_PROJECT_CATEGORIES.includes(payload.category)) {
                    payload.project_id = payload._project_sel || null;
                } else {
                    payload.project_id = null;
                }
                if (payload.category !== '專案外包') payload.payee_type = '';
                // Resolve payee_id from staff list
                const staff = _staffList.find(s => s.name === payload.payee_name);
                payload.payee_id = staff?.id_number || p.payee_id || '';
                // Clean up internal fields
                delete payload._invoice_sel;
                delete payload._project_sel;
                delete payload._payee_id_display;
                await _fetch('/payments/' + p.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/payments/' + p.id);
                renderDetail(updated);
                await loadPayments();
            },
            () => renderDetail(p)
        );
        _wireEditDynamics(p);
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
    const payeeTypeField = document.getElementById('pay-payee-type-field');

    // Hide all first
    if (invoiceField) invoiceField.style.display = 'none';
    if (invoiceNumField) invoiceNumField.style.display = 'none';
    if (projectField) projectField.style.display = 'none';
    if (payeeTypeField) {
        payeeTypeField.style.display = category === '專案外包' ? 'flex' : 'none';
    }

    if (category === '發票代開') {
        if (invoiceField) invoiceField.style.display = '';
        if (invoiceNumField) invoiceNumField.style.display = '';
        if (projectField) {
            projectField.style.display = '';
            const lbl = projectField.querySelector('label');
            if (lbl) lbl.innerHTML = '專案';
        }
        const sel = document.getElementById('pay-f-project_id');
        if (sel) {
            const current = sel.value;
            sel.innerHTML = `<option value="">— 選擇發票 —</option>` +
                _invoiceList.filter(inv => inv.issue_status === '已開立').map(inv =>
                    `<option value="${inv.id}" data-num="${_esc(inv.invoice_number)}" data-amt="${inv.amount_total || 0}"${inv.id === current ? ' selected' : ''}>${_esc(inv.title)} $${(inv.amount_total||0).toLocaleString('zh-TW')} (${_esc(inv.company_name)})</option>`
                ).join('');
        }
    } else if (_PROJECT_CATEGORIES.includes(category)) {
        if (projectField) {
            projectField.style.display = '';
            const lbl = projectField.querySelector('label');
            if (lbl) lbl.innerHTML = '專案 <span class="crm-required">*</span>';
        }
        _populateProject2Select('');
    }
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
    // Auto-set payment_status for new payments
    if (!_editingId) payload.payment_status = '應付款';
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
        if (_editingId) {
            await _fetch('/payments/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
            // Refresh detail panel if this payment is currently selected
            if (_selectedId === _editingId) {
                try { renderDetail(await _fetch('/payments/' + _editingId)); } catch(_) {}
            }
        }
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

export async function initCrmPaymentsTab() {
    for (const id of ['pay-modal', 'pay-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
    window._paySelect = selectPayment;
    window._payRefresh = loadPayments;
    window._payEdit = async (id) => { try { openModal(await _fetch('/payments/' + id)); } catch(_) {} };
    window._payDelete = (id) => { const p = _payments.find(x => x.id === id); if (p) deletePayment(p); };
    window._payDup = async (id) => {
        try {
            const p = await _fetch('/payments/' + id);
            openModal(p); _editingId = null;
            document.getElementById('pay-modal-title').textContent = '複製請款';
        } catch (_) {}
    };

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
    await Promise.all([loadPayments(), loadProjects(), loadStaffList(), _loadInvoiceList()]);
}
