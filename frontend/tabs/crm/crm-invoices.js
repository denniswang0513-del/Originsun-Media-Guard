/**
 * crm-invoices.js — 帳務管理 Tab
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle } from './crm-utils.js';

let _invoices = [];
let _projects = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', payment_type: '', category: '' };
let _csvFile = null;

// ── Data ─────────────────────────────────────────────────────

async function loadInvoices() {
    const params = new URLSearchParams();
    if (_filters.q)            params.set('q', _filters.q);
    if (_filters.payment_type) params.set('payment_type', _filters.payment_type);
    if (_filters.category)     params.set('category', _filters.category);
    try {
        const data = await _fetch(`/invoices?${params}`);
        _invoices = data.invoices || [];
    } catch (_) { _invoices = []; }
    renderList();
}

async function loadProjects() {
    try { _projects = (await _fetch('/projects')).projects || []; } catch(_) { _projects = []; }
}

// ── Rendering ────────────────────────────────────────────────

function _payBadge(type, status) {
    if (status === '作廢') return `<span class="crm-badge crm-pay-badge-作廢">${_esc(status)}</span>`;
    const cls = type === '收款' ? 'crm-pay-badge-收款' : 'crm-pay-badge-付款';
    return `<span class="crm-badge ${cls}">${_esc(status || type)}</span>`;
}

function renderList() {
    const body = document.getElementById('inv-list-body');
    if (!body) return;
    if (_invoices.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無發票${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    body.innerHTML = _invoices.map(inv => `
        <div class="crm-row${inv.id === _selectedId ? ' selected' : ''}" onclick="window._invSelect('${inv.id}')">
            <div class="crm-row-name">${_esc(inv.title)}</div>
            <div class="crm-row-status">${_payBadge(inv.payment_type, inv.payment_status)}</div>
            <div class="crm-row-amount">$${_fmtNum(inv.amount_total)}</div>
            <div class="crm-row-client">${_esc(inv.company_name)}</div>
            <div class="crm-row-date">${inv.invoice_date ? inv.invoice_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-actions" onclick="event.stopPropagation()">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._invEdit('${inv.id}')">編輯</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._invDelete('${inv.id}')">刪</button>
            </div>
        </div>
    `).join('');
}

function renderDetail(inv) {
    document.getElementById('inv-detail-title').textContent = inv.title;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    document.getElementById('inv-detail-content').innerHTML = `
        <div class="crm-detail-prop"><div class="crm-prop-label">款項</div><div class="crm-prop-value">${_payBadge(inv.payment_type, inv.payment_status)}</div></div>
        <div class="crm-detail-prop"><div class="crm-prop-label">開立</div><div class="crm-prop-value">${inv.issue_status === '作廢' ? '<span style="color:#fca5a5;">作廢</span>' : _esc(inv.issue_status)}</div></div>
        ${prop('發票編號', inv.invoice_number)}
        ${prop('日期', inv.invoice_date ? inv.invoice_date.substring(0, 10) : '')}
        ${prop('類別', inv.category)}
        ${prop('發票種類', inv.invoice_kind)}
        ${prop('未稅價', inv.amount_ex_tax ? '$' + _fmtNum(inv.amount_ex_tax) : '')}
        ${prop('發票金額', inv.amount_total ? '$' + _fmtNum(inv.amount_total) : '')}
        ${prop('稅額', inv.tax_amount ? '$' + _fmtNum(inv.tax_amount) : '')}
        ${inv.commission ? prop('代開應區', '$' + _fmtNum(inv.commission)) : ''}
        ${prop('申請人', inv.applicant)}
        ${prop('抬頭', inv.company_name)}
        ${prop('統編', inv.tax_id)}
        ${prop('品項', inv.item_type)}
        ${inv.project_name ? prop('關聯專案', inv.project_name) : ''}
        ${prop('備註', inv.notes)}
    `;
}

// ── Detail ───────────────────────────────────────────────────

async function selectInvoice(id) {
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

const _FIELDS = ['payment_type', 'issue_status', 'invoice_number', 'invoice_date', 'title',
    'category', 'invoice_kind', 'amount_ex_tax', 'amount_total', 'tax_amount', 'commission',
    'applicant', 'company_name', 'tax_id', 'item_type', 'project_id', 'notes'];
const _INT_FIELDS = ['amount_ex_tax', 'amount_total', 'tax_amount', 'commission'];

function _populateProjectSelect(selectedId) {
    const sel = document.getElementById('inv-f-project_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 不關聯 —</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)}</option>`).join('');
}

function openModal(inv = null) {
    _editingId = inv ? inv.id : null;
    document.getElementById('inv-modal-title').textContent = inv ? '編輯發票' : '新增發票';
    const err = document.getElementById('inv-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populateProjectSelect(inv?.project_id || '');

    for (const f of _FIELDS) {
        const el = document.getElementById('inv-f-' + f);
        if (!el) continue;
        if (f === 'invoice_date' && inv?.invoice_date) el.value = inv.invoice_date.substring(0, 10);
        else el.value = inv ? (inv[f] ?? '') : '';
    }
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
    // Auto-set payment_status
    payload.payment_status = payload.payment_type === '收款' ? '已收款' : '已付款';
    if (payload.issue_status === '作廢') payload.payment_status = '作廢';

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

export function initCrmInvoicesTab() {
    for (const id of ['inv-modal', 'inv-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._invSelect = selectInvoice;
    window._invEdit = async (id) => { try { openModal(await _fetch('/invoices/' + id)); } catch(_) {} };
    window._invDelete = (id) => { const inv = _invoices.find(x => x.id === id); if (inv) deleteInvoice(inv); };

    let _t;
    document.getElementById('inv-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadInvoices, 300);
    });
    document.getElementById('inv-filter-type').addEventListener('change', e => { _filters.payment_type = e.target.value; loadInvoices(); });
    document.getElementById('inv-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadInvoices(); });

    document.getElementById('inv-btn-add').addEventListener('click', () => openModal());
    document.getElementById('inv-btn-import').addEventListener('click', openImportModal);
    document.getElementById('inv-btn-save').addEventListener('click', saveInvoice);
    document.getElementById('inv-detail-close').addEventListener('click', closeDetail);
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

    setupResizeHandle('inv-resize-handle', 'inv-detail-panel');
    // View switching
    let _paymentsLoaded = false, _paymentsLoading = false;
    let _cashbookLoaded = false, _cashbookLoading = false;
    let _payablesLoaded = false, _payablesLoading = false;
    const invView = document.getElementById('inv-invoices-view');
    const payView = document.getElementById('inv-payments-view');
    const cashView = document.getElementById('inv-cashbook-view');
    const payablesView = document.getElementById('inv-payables-view');
    const allViews = [invView, payView, cashView, payablesView];
    const allBtns = ['inv-view-invoices', 'inv-view-payments', 'inv-view-cashbook', 'inv-view-payables'];
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
                    mod.initCrmPaymentsTab();
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
                    mod.initCrmCashbookTab();
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

    // Global refresh — reloads current active sub-view
    document.getElementById('inv-global-refresh').addEventListener('click', () => {
        // Refresh invoices (always loaded)
        loadInvoices();
        // Refresh whichever lazy-loaded sub-view is active
        if (window._payRefresh) window._payRefresh();
        if (window._cashRefresh) window._cashRefresh();
        if (window._payableRefresh) window._payableRefresh();
    });

    Promise.all([loadInvoices(), loadProjects()]);
}
