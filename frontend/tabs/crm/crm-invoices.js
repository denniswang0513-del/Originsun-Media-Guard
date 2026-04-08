/**
 * crm-invoices.js — 帳務管理 Tab
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

let _invoices = [];
let _projects = [];
let _clients = [];
let _selectedId = null;
let _editingId = null;
let _editingPaymentStatus = null;
let _filters = { q: '', issue_status: '', category: '' };
let _csvFile = null;

// ── Commission fee settings (localStorage) ──────────────────
function _loadFees() {
    try {
        const s = localStorage.getItem('inv_commission_fees');
        if (s) return JSON.parse(s);
    } catch (_) {}
    return { internal: 8, external: 10 };
}
function _saveFees(fees) { localStorage.setItem('inv_commission_fees', JSON.stringify(fees)); }

// ── Applicant list (localStorage) ───────────────────────────
function _loadApplicants() {
    try {
        const s = localStorage.getItem('inv_applicants');
        if (s) return JSON.parse(s);
    } catch (_) {}
    return [];
}
function _saveApplicants(list) { localStorage.setItem('inv_applicants', JSON.stringify(list)); }

function _populateApplicantSelect(current) {
    const sel = document.getElementById('inv-f-applicant');
    if (!sel) return;
    const list = _loadApplicants();
    sel.innerHTML = '<option value="">— 選擇 —</option>' +
        list.map(n => `<option value="${_esc(n)}"${n === current ? ' selected' : ''}>${_esc(n)}</option>`).join('');
}

function _renderApplicantList() {
    const container = document.getElementById('inv-applicant-list');
    if (!container) return;
    const list = _loadApplicants();
    if (list.length === 0) {
        container.innerHTML = '<div style="color:#6b7280;font-size:12px;">尚無申請人</div>';
        return;
    }
    container.innerHTML = list.map((n, i) =>
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
    try {
        const data = await _fetch(`/invoices?${params}`);
        _invoices = data.invoices || [];
    } catch (_) { _invoices = []; }
    renderList();
}

async function loadProjects() {
    try { _projects = (await _fetch('/projects')).projects || []; } catch(_) { _projects = []; }
}

async function loadClients() {
    try { _clients = (await _fetch('/clients')).clients || []; } catch(_) { _clients = []; }
}

// ── Rendering ────────────────────────────────────────────────

function _statusBadge(status) {
    if (status === '作廢') return `<span class="crm-badge crm-pay-badge-作廢">${_esc(status)}</span>`;
    if (status === '已開立') return `<span class="crm-badge crm-pay-badge-收款">${_esc(status)}</span>`;
    return `<span class="crm-badge crm-pay-badge-付款">${_esc(status || '開立中')}</span>`;
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
            <div class="crm-row-date">${inv.invoice_date ? inv.invoice_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-name">${_esc(inv.title)}</div>
            <div class="crm-row-amount">$${_fmtNum(inv.amount_total)}</div>
            <div class="crm-row-client">${_esc(inv.company_name)}</div>
            <div>${_esc(inv.tax_id)}</div>
            <div>${_esc(inv.item_type)}</div>
            <div>${_esc(inv.category)}</div>
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
        {name:'amount_ex_tax', label:'未稅價', type:'number'},
        {name:'_amount_total_display', label:'含稅價', type:'readonly'},
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

function renderDetail(inv) {
    document.getElementById('inv-detail-title').textContent = inv.title;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    const section = (title, extra = '') => `<div class="crm-detail-section">${title}${extra ? '<span style="margin-left:auto;">' + extra + '</span>' : ''}</div>`;
    const commLabel = inv.category === '外部代開' ? '代開應匯' : inv.category === '內部代開' ? '代開匯款' : '';

    let html = '';
    // ── 頂部
    html += prop('日期', inv.invoice_date ? inv.invoice_date.substring(0, 10) : '');
    // ── 開立資訊
    const payBadge = `<span class="crm-badge crm-pay-badge-${_esc(inv.payment_status === '已收款' ? '收款' : inv.payment_status || '未收款')}">${_esc(inv.payment_status || '未收款')}</span>`;
    html += section('開立資訊', payBadge);
    html += prop('發票編號', inv.invoice_number);
    html += prop('開立狀態', inv.issue_status);
    html += prop('未稅價', inv.amount_ex_tax ? '$' + _fmtNum(inv.amount_ex_tax) : '');
    html += prop('含稅價', inv.amount_total ? '$' + _fmtNum(inv.amount_total) : '');
    html += prop('稅額', inv.tax_amount ? '$' + _fmtNum(inv.tax_amount) : '');
    html += prop('抬頭', inv.company_name);
    html += prop('統編', inv.tax_id);
    html += prop('品項', inv.item_type);
    html += prop('發票種類', inv.invoice_kind);
    if (inv.invoice_kind === '紙本發票') {
        html += prop('收件人', inv.recipient);
        html += prop('收件電話', inv.recipient_phone);
        html += prop('收件地址', inv.recipient_address);
    }
    // ── 補充資訊
    html += section('補充資訊');
    html += prop('申請人', inv.applicant);
    html += prop('發票類別', inv.category);
    if (inv.category === '專案') html += prop('關聯專案', inv.project_name);
    if (commLabel) html += prop(commLabel, inv.commission ? '$' + _fmtNum(inv.commission) : '');
    html += prop('備註', inv.notes);

    document.getElementById('inv-detail-content').innerHTML = html;

    const actions = document.getElementById('inv-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('inv-bar-actions', () => {
        const editData = { ...inv,
            _amount_total_display: inv.amount_total ? '$' + _fmtNum(inv.amount_total) : '',
            _tax_amount_display: inv.tax_amount ? '$' + _fmtNum(inv.tax_amount) : '',
            _commission_display: inv.commission ? '$' + _fmtNum(inv.commission) : '',
        };
        enableInlineEdit('inv-detail-content', 'inv-bar-actions', _buildEditFields(), editData,
            async (payload) => {
                // auto: 有編號→已開立，無編號→開立中（除非作廢）
                if (payload.issue_status !== '作廢') {
                    payload.issue_status = payload.invoice_number?.trim() ? '已開立' : '開立中';
                }
                payload.payment_type = '收款';
                if (payload.issue_status === '作廢') payload.payment_status = '作廢';
                else payload.payment_status = inv.payment_status || '未收款';
                // auto-calc from 未稅價
                const ex = parseInt(payload.amount_ex_tax) || 0;
                payload.amount_total = ex ? Math.round(ex * 1.05) : null;
                payload.tax_amount = ex ? (payload.amount_total - ex) : null;
                // resolve tax_id from selected client
                const client = _clients.find(c => c.short_name === payload.company_name);
                payload.tax_id = client?.tax_id || inv.tax_id || '';
                // resolve commission from amount
                const tot = payload.amount_total || 0;
                const fees = _loadFees();
                if (payload.category === '內部代開') payload.commission = tot ? Math.round(tot * (1 - fees.internal / 100)) : null;
                else if (payload.category === '外部代開') payload.commission = tot ? Math.round(tot * (1 - fees.external / 100)) : null;
                else payload.commission = null;
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

function _todayStr() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function _updateTaxCalc() {
    const exTax = parseInt(document.getElementById('inv-f-amount_ex_tax').value) || 0;
    const total = Math.round(exTax * 1.05);
    document.getElementById('inv-f-amount_total').value = exTax ? total : '';
    _updateCommission();
}

function _updateCommission() {
    const total = parseInt(document.getElementById('inv-f-amount_total').value) || 0;
    const cat = document.getElementById('inv-f-category').value;
    const fees = _loadFees();
    const intEl = document.getElementById('inv-f-commission');
    const extEl = document.getElementById('inv-f-commission_ext');
    if (cat === '內部代開') {
        intEl.value = total ? Math.round(total * (1 - fees.internal / 100)) : '';
    } else if (cat === '外部代開') {
        extEl.value = total ? Math.round(total * (1 - fees.external / 100)) : '';
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
    document.getElementById('inv-modal-title').textContent = inv ? '編輯發票' : '新增發票';
    // Payment status badge
    const badgeEl = document.getElementById('inv-modal-pay-badge');
    if (badgeEl) {
        const ps = inv?.payment_status || '未收款';
        const cls = ps === '已收款' ? '收款' : ps === '作廢' ? '作廢' : '未收款';
        badgeEl.innerHTML = inv ? `<span class="crm-badge crm-pay-badge-${cls}">${_esc(ps)}</span>` : '';
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
    // payment_type / payment_status (auto-derive)
    payload.payment_type = '收款';
    if (payload.issue_status === '作廢') payload.payment_status = '作廢';
    else if (_editingId && _editingPaymentStatus) payload.payment_status = _editingPaymentStatus;
    else payload.payment_status = '未收款';

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
        _calcExTax = Math.round(incl / 1.05);
        const tax = incl - _calcExTax;
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

function _initCommissionSettings() {
    const fees = _loadFees();
    const intEl = document.getElementById('inv-fee-internal');
    const extEl = document.getElementById('inv-fee-external');
    intEl.value = fees.internal;
    extEl.value = fees.external;
    const save = () => {
        _saveFees({ internal: parseFloat(intEl.value) || 0, external: parseFloat(extEl.value) || 0 });
        _updateCommission();
    };
    intEl.addEventListener('input', save);
    extEl.addEventListener('input', save);
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

    document.getElementById('inv-applicant-add-btn').addEventListener('click', () => {
        const input = document.getElementById('inv-applicant-new');
        const name = input.value.trim();
        if (!name) return;
        const list = _loadApplicants();
        if (!list.includes(name)) {
            list.push(name);
            _saveApplicants(list);
        }
        input.value = '';
        _renderApplicantList();
        _populateApplicantSelect(document.getElementById('inv-f-applicant').value);
    });

    window._invRemoveApplicant = (idx) => {
        const list = _loadApplicants();
        list.splice(idx, 1);
        _saveApplicants(list);
        _renderApplicantList();
        _populateApplicantSelect(document.getElementById('inv-f-applicant').value);
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
    const invView = document.getElementById('inv-invoices-view');
    const payView = document.getElementById('inv-payments-view');
    const cashView = document.getElementById('inv-cashbook-view');
    const payablesView = document.getElementById('inv-payables-view');
    const receivablesView = document.getElementById('inv-receivables-view');
    const allViews = [invView, payView, cashView, payablesView, receivablesView];
    const allBtns = ['inv-view-invoices', 'inv-view-payments', 'inv-view-cashbook', 'inv-view-payables', 'inv-view-receivables'];
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

    // Global refresh — reloads current active sub-view
    document.getElementById('inv-global-refresh').addEventListener('click', () => {
        loadInvoices();
        if (window._payRefresh) window._payRefresh();
        if (window._cashRefresh) window._cashRefresh();
        if (window._payableRefresh) window._payableRefresh();
        if (window._recvRefresh) window._recvRefresh();
    });

    await Promise.all([loadInvoices(), loadProjects(), loadClients()]);
}
