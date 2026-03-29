/**
 * crm-payments.js — 請款管理子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle } from './crm-utils.js';

let _payments = [];
let _projects = [];
let _staffList = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', payment_status: '' };
let _csvFile = null;

async function loadPayments() {
    const params = new URLSearchParams();
    if (_filters.q)              params.set('q', _filters.q);
    if (_filters.category)       params.set('category', _filters.category);
    if (_filters.payment_status) params.set('payment_status', _filters.payment_status);
    try { _payments = (await _fetch('/payments?' + params)).payments || []; }
    catch (_) { _payments = []; }
    renderList();
}

async function loadProjects() {
    try { _projects = (await _fetch('/projects')).projects || []; } catch(_) { _projects = []; }
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
            <div class="crm-row-client">${_esc(p.payee_name)}</div>
            <div class="crm-row-status">${_statusBadge(p.payment_status)}</div>
            <div class="crm-row-actions" onclick="event.stopPropagation()">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._payEdit('${p.id}')">編輯</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._payDelete('${p.id}')">刪</button>
            </div>
        </div>
    `).join('');
}

function renderDetail(p) {
    document.getElementById('pay-detail-title').textContent = p.summary;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    document.getElementById('pay-detail-content').innerHTML = `
        ${prop('日期', p.request_date ? p.request_date.substring(0, 10) : '')}
        ${prop('金額', '$' + _fmtNum(p.amount))}
        ${prop('項目', p.category)}
        ${prop('收款人', p.payee_name + (p.payee_id ? ' (' + p.payee_id + ')' : ''))}
        ${prop('狀態', p.payee_type)}
        ${prop('代開發票', p.needs_invoice ? '是 — ' + (p.invoice_number || '待開') : '否')}
        ${prop('專案', p.project_label || p.project_name)}
        <div class="crm-detail-prop"><div class="crm-prop-label">付款狀態</div><div class="crm-prop-value">${_statusBadge(p.payment_status)}</div></div>
        ${prop('付款日', p.payment_date ? p.payment_date.substring(0, 10) : '')}
        ${prop('附註', p.notes)}
    `;
}

async function selectPayment(id) {
    _selectedId = id; renderList();
    document.getElementById('pay-detail-panel').style.display = 'flex';
    document.getElementById('pay-resize-handle').style.display = '';
    try { renderDetail(await _fetch('/payments/' + id)); } catch(_) {}
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('pay-detail-panel').style.display = 'none';
    document.getElementById('pay-resize-handle').style.display = 'none';
    renderList();
}

const _FIELDS = ['summary', 'amount', 'request_date', 'category', 'payee_name', 'payee_id',
    'payee_type', 'needs_invoice', 'invoice_number', 'project_label', 'project_id',
    'payment_date', 'payment_status', 'notes'];
const _DATE_FIELDS = ['request_date', 'payment_date'];
const _INT_FIELDS = ['amount', 'needs_invoice'];

const _PROJECT_CATEGORIES = ['專案外包', '專案雜支', '發票代開'];

function _populateProjectSelect(selectedId, category) {
    const sel = document.getElementById('pay-f-project_id');
    const labelEl = document.getElementById('pay-f-project_id')?.closest('.crm-field');
    if (!sel) return;

    if (_PROJECT_CATEGORIES.includes(category)) {
        if (labelEl) labelEl.style.display = '';
        sel.innerHTML = `<option value="">— 選擇專案 —</option>` +
            _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)} (${_esc(p.client_short_name || '')})</option>`).join('');
    } else {
        if (labelEl) labelEl.style.display = '';
        sel.innerHTML = `<option value="">— 不關聯 —</option>` +
            _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)}</option>`).join('');
    }
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
    _populateProjectSelect(p?.project_id || '', p?.category || '');
    _populatePayeeSelect(p?.payee_name || '');
    for (const f of _FIELDS) {
        const el = document.getElementById('pay-f-' + f);
        if (!el) continue;
        if (f === 'payee_name') continue; // handled by _populatePayeeSelect
        if (_DATE_FIELDS.includes(f) && p?.[f]) el.value = p[f].substring(0, 10);
        else el.value = p ? (p[f] ?? '') : '';
    }
    if (!p) document.getElementById('pay-f-request_date').value = new Date().toISOString().substring(0, 10);
    document.getElementById('pay-modal').style.display = 'flex';
}

async function savePayment() {
    const summary = document.getElementById('pay-f-summary').value.trim();
    if (!summary) { _showErr('摘要為必填'); return; }
    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById('pay-f-' + f);
        let val = el ? el.value.trim() : '';
        if (_INT_FIELDS.includes(f)) val = val ? parseInt(val) : 0;
        if (_DATE_FIELDS.includes(f)) val = val || null;
        if (f === 'project_id') val = val || null;
        payload[f] = val;
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
    window._payEdit = async (id) => { try { openModal(await _fetch('/payments/' + id)); } catch(_) {} };
    window._payDelete = (id) => { const p = _payments.find(x => x.id === id); if (p) deletePayment(p); };

    let _t;
    document.getElementById('pay-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadPayments, 300);
    });
    document.getElementById('pay-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadPayments(); });
    document.getElementById('pay-filter-status').addEventListener('change', e => { _filters.payment_status = e.target.value; loadPayments(); });

    document.getElementById('pay-btn-add').addEventListener('click', () => openModal());

    // Auto-fill payee_id when payee_name changes
    document.getElementById('pay-f-payee_name').addEventListener('change', e => {
        const opt = e.target.selectedOptions[0];
        document.getElementById('pay-f-payee_id').value = opt?.dataset.id || '';
    });

    // Re-populate project select when category changes
    document.getElementById('pay-f-category').addEventListener('change', e => {
        _populateProjectSelect('', e.target.value);
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
    Promise.all([loadPayments(), loadProjects(), loadStaffList()]);
}
