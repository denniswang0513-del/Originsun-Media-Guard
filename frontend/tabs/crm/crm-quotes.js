/**
 * crm-quotes.js — 報價管理 Tab
 */

import { crmFetch as _fetch, esc as _esc, populateClientSelect, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

// ── State ────────────────────────────────────────────────────

let _quotations = [];
let _projects = [];
let _clients = [];
let _users = [];
let _templates = [];
let _selectedId = null;
let _editingId = null;  // null=新增, string=編輯
let _editingProjectId = null;
let _filters = { q: '', status: '', client_id: '' };

// ── Data Loading ─────────────────────────────────────────────

export async function loadQuotations() {
    const params = new URLSearchParams();
    if (_filters.q)         params.set('q', _filters.q);
    if (_filters.status)    params.set('status', _filters.status);
    if (_filters.client_id) params.set('client_id', _filters.client_id);
    try {
        const data = await _fetch(`/quotations?${params}`);
        _quotations = data.quotations || [];
    } catch (e) {
        _quotations = [];
    }
    renderList();
}

async function loadStats() {
    try {
        const s = await _fetch('/quotations/stats');
        document.getElementById('quote-stat-month').textContent = '$' + _fmtNum(s.month_total);
        document.getElementById('quote-stat-pending').textContent = s.pending_count + ' 筆';
        document.getElementById('quote-stat-rate').textContent = s.sign_rate + '%';
    } catch (_) {}
}

async function loadProjects() {
    try {
        const data = await _fetch('/projects');
        _projects = data.projects || [];
    } catch (_) { _projects = []; }
}

async function loadClients() {
    try {
        const data = await _fetch('/clients');
        _clients = data.clients || [];
        _populateClientFilter();
    } catch (_) { _clients = []; }
}

async function loadUsers() {
    try {
        const data = await _fetch('/users');
        _users = data.users || [];
    } catch (_) { _users = []; }
}

async function loadTemplates() {
    try {
        const data = await _fetch('/quotation-templates');
        _templates = data.templates || [];
    } catch (_) { _templates = []; }
}

// ── Rendering ────────────────────────────────────────────────

const _QUOTE_STATUSES = ['草稿', '已寄送', '已簽核', '已拒絕'];

function _qBadge(status) {
    const s = status || '草稿';
    const cls = _QUOTE_STATUSES.includes(s) ? `crm-badge crm-quote-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

function renderList() {
    const body = document.getElementById('quote-list-body');
    if (!body) return;
    if (_quotations.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無報價單${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }
    body.innerHTML = _quotations.map(q => {
        const price = q.final_price !== null && q.final_price !== undefined ? q.final_price : q.total;
        return `
        <div class="crm-row${q.id === _selectedId ? ' selected' : ''}" onclick="window._quoteSelect('${q.id}')">
            <div class="crm-row-name">Q-${_esc(q.project_name)}-v${q.version}</div>
            <div class="crm-row-client">${_esc(q.project_name)}<br><span class="crm-muted">${_esc(q.client_short_name)}</span></div>
            <div class="crm-row-status">${_qBadge(q.status)}</div>
            <div class="crm-row-amount">$${_fmtNum(price)}</div>
            <div class="crm-row-date">${q.quote_date ? q.quote_date.substring(0, 10) : '—'}</div>
            ${kebabMenuHtml(q.id, { onEdit: '_quoteEdit', onDuplicate: '_quoteDup', onDelete: '_quoteDelete' })}
        </div>`;
    }).join('');
}

const _QUOTE_EDIT_FIELDS = [
    {name:'status', label:'狀態', type:'select', options:[
        {value:'草稿',label:'草稿'},{value:'已寄送',label:'已寄送'},
        {value:'已簽核',label:'已簽核'},{value:'已拒絕',label:'已拒絕'},
    ]},
    {name:'quote_date', label:'報價日期', type:'date'},
    {name:'valid_until', label:'有效期限', type:'date'},
    {name:'discount', label:'折扣', type:'number'},
    {name:'tax_rate', label:'稅率(%)', type:'number'},
    {name:'final_price', label:'最終報價', type:'number'},
    {name:'terms', label:'備註/條款', type:'textarea'},
];

function renderDetail(q) {
    document.getElementById('quote-detail-title').textContent = `Q-${q.project_name || ''}-v${q.version}`;

    const price = q.final_price !== null && q.final_price !== undefined ? q.final_price : q.total;
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `<div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(String(value))}</div>
        </div>`;
    };

    document.getElementById('quote-detail-info').innerHTML = `
        ${prop('專案', q.project_name)}
        ${prop('客戶', q.client_short_name)}
        <div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value">${_qBadge(q.status)}</div></div>
        ${prop('報價日期', q.quote_date ? q.quote_date.substring(0, 10) : '')}
        ${prop('有效期限', q.valid_until ? q.valid_until.substring(0, 10) : '')}
        ${prop('小計', '$' + _fmtNum(q.subtotal))}
        ${prop('折扣', q.discount ? '-$' + _fmtNum(q.discount) : '')}
        ${prop('稅額', '$' + _fmtNum(q.tax_amount) + ' (' + q.tax_rate + '%)')}
        ${prop('含稅總計', '$' + _fmtNum(q.total))}
        ${prop('最終報價', q.final_price !== null ? '$' + _fmtNum(q.final_price) : '')}
        ${prop('付款階段', (q.payment_stages || []).map(s => s.label + ' ' + s.pct + '%').join(' / '))}
        ${prop('備註', q.terms)}
        ${q.status === '已簽核' ? `<div style="padding:12px 0;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._quoteActivateProject('${q.project_id}')">啟動專案 (切為進行中)</button></div>` : ''}
    `;

    // Items tab
    const items = q.items || [];
    const groups = {};
    items.forEach(it => {
        const g = it.group_name || '其他';
        if (!groups[g]) groups[g] = [];
        groups[g].push(it);
    });
    let itemsHtml = '';
    for (const [gName, gItems] of Object.entries(groups)) {
        itemsHtml += `<div class="quote-group-title">${_esc(gName)}</div>`;
        itemsHtml += gItems.map(it => `
            <div class="quote-item-row">
                <span class="quote-item-desc">${_esc(it.description)}</span>
                <span class="quote-item-qty">${it.quantity} ${_esc(it.unit)}</span>
                <span class="quote-item-price">$${_fmtNum(it.unit_price)}</span>
                <span class="quote-item-amount">$${_fmtNum(it.amount)}</span>
            </div>
        `).join('');
    }
    document.getElementById('quote-detail-items').innerHTML = itemsHtml || '<div class="crm-empty">尚無項目</div>';

    const actions = document.getElementById('quote-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('quote-bar-actions', () => {
        enableInlineEdit('quote-detail-info', 'quote-bar-actions', _QUOTE_EDIT_FIELDS, q,
            async (payload) => {
                // Keep existing items when editing info fields
                payload.items = (q.items || []).map(it => ({
                    group_name: it.group_name || '', description: it.description,
                    unit: it.unit || '式', quantity: it.quantity || 1,
                    unit_price: it.unit_price || 0, internal_cost: it.internal_cost || 0,
                }));
                await _fetch('/quotations/' + q.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/quotations/' + q.id);
                renderDetail(updated);
                await loadQuotations();
            },
            () => renderDetail(q)
        );
    });
}

// ── Detail Panel ─────────────────────────────────────────────

async function selectQuotation(id) {
    _selectedId = id;
    renderList();
    const panel = document.getElementById('quote-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('quote-resize-handle');
    if (handle) handle.style.display = '';

    try {
        const q = await _fetch(`/quotations/${id}`);
        renderDetail(q);
    } catch (_) {}
}

function closeDetail() {
    _selectedId = null;
    const panel = document.getElementById('quote-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('quote-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Modal: Items ─────────────────────────────────────────────

let _itemRows = [];

function addItemRow(data = null) {
    const d = data || { group_name: '', description: '', unit: '式', quantity: 1, unit_price: 0, internal_cost: 0, note: '' };
    _itemRows.push(d);
    _renderItemRows();
}

function removeItemRow(idx) {
    _itemRows.splice(idx, 1);
    _renderItemRows();
    _recalcTotals();
}

function _renderItemRows() {
    const container = document.getElementById('quote-items-list');
    if (!container) return;
    container.innerHTML = _itemRows.map((it, i) => `
        <div class="quote-item-edit-row" data-idx="${i}">
            <input type="text" class="crm-input qi-group" value="${_esc(it.group_name)}" placeholder="群組" style="width:80px;">
            <input type="text" class="crm-input qi-desc" value="${_esc(it.description)}" placeholder="項目描述" style="flex:2;">
            <input type="text" class="crm-input qi-unit" value="${_esc(it.unit)}" placeholder="單位" style="width:50px;">
            <input type="number" class="crm-input qi-qty" value="${it.quantity}" min="1" style="width:55px;text-align:right;">
            <input type="number" class="crm-input qi-price" value="${it.unit_price}" min="0" style="width:90px;text-align:right;">
            <input type="number" class="crm-input qi-cost" value="${it.internal_cost || 0}" min="0" style="width:80px;text-align:right;" placeholder="成本">
            <span class="qi-amount">$${_fmtNum(it.quantity * it.unit_price)}</span>
            <button type="button" class="crm-btn crm-btn-danger crm-btn-sm qi-remove" onclick="window._quoteRemoveItem(${i})">&#x2715;</button>
        </div>
    `).join('');

    // Attach input listeners for recalc
    container.querySelectorAll('.quote-item-edit-row').forEach(row => {
        const idx = parseInt(row.dataset.idx);
        row.querySelector('.qi-group').addEventListener('input', e => { _itemRows[idx].group_name = e.target.value; });
        row.querySelector('.qi-desc').addEventListener('input', e => { _itemRows[idx].description = e.target.value; });
        row.querySelector('.qi-unit').addEventListener('input', e => { _itemRows[idx].unit = e.target.value; });
        row.querySelector('.qi-qty').addEventListener('input', e => { _itemRows[idx].quantity = parseInt(e.target.value) || 0; _recalcTotals(); });
        row.querySelector('.qi-price').addEventListener('input', e => { _itemRows[idx].unit_price = parseInt(e.target.value) || 0; _recalcTotals(); });
        row.querySelector('.qi-cost').addEventListener('input', e => { _itemRows[idx].internal_cost = parseInt(e.target.value) || 0; _recalcTotals(); });
    });
}

function _recalcTotals() {
    const subtotal = _itemRows.reduce((s, it) => s + (it.quantity * it.unit_price), 0);
    const costTotal = _itemRows.reduce((s, it) => s + ((it.internal_cost || 0) * (it.quantity || 1)), 0);
    const discount = parseInt(document.getElementById('quote-f-discount')?.value) || 0;
    const taxRate = parseInt(document.getElementById('quote-f-tax_rate')?.value) || 0;
    const taxable = Math.max(subtotal - discount, 0);
    const taxAmount = Math.floor(taxable * taxRate / 100);
    const total = taxable + taxAmount;
    const profitRate = subtotal > 0 ? Math.round((subtotal - costTotal) / subtotal * 100) : 0;

    document.getElementById('quote-calc-subtotal').textContent = '$' + _fmtNum(subtotal);
    document.getElementById('quote-calc-tax-pct').textContent = taxRate;
    document.getElementById('quote-calc-tax').textContent = '$' + _fmtNum(taxAmount);
    document.getElementById('quote-calc-total').textContent = '$' + _fmtNum(total);

    const costEl = document.getElementById('quote-calc-cost');
    if (costEl) costEl.textContent = '$' + _fmtNum(costTotal);
    const profitEl = document.getElementById('quote-calc-profit');
    if (profitEl) profitEl.textContent = profitRate + '%';

    document.querySelectorAll('.quote-item-edit-row').forEach(row => {
        const idx = parseInt(row.dataset.idx);
        const it = _itemRows[idx];
        row.querySelector('.qi-amount').textContent = '$' + _fmtNum(it.quantity * it.unit_price);
    });
}

// ── Modal: Open / Save ───────────────────────────────────────

function _populateProjectSelect(selectedId) {
    const sel = document.getElementById('quote-f-project_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇專案 —</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)} (${_esc(p.client_short_name)})</option>`).join('') +
        `<option value="__new__">+ 新增專案</option>`;
}

function _showInlineProjectForm() {
    const area = document.getElementById('quote-inline-project');
    if (!area) return;
    area.style.display = 'block';
    area.innerHTML = `
        <div class="crm-form-grid" style="margin-top:8px;padding:10px;background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;">
            <div class="crm-field">
                <label>專案名稱 <span class="crm-required">*</span></label>
                <input id="quote-np-name" type="text" class="crm-input" placeholder="例：形象影片">
            </div>
            <div class="crm-field">
                <label>客戶 <span class="crm-required">*</span></label>
                <select id="quote-np-client" class="crm-input">
                    <option value="">— 選擇客戶 —</option>
                    ${_clients.map(c => `<option value="${c.id}">${_esc(c.short_name)}</option>`).join('')}
                </select>
            </div>
            <div class="crm-field" style="display:flex;align-items:flex-end;gap:6px;">
                <button type="button" id="quote-np-save" class="crm-btn crm-btn-primary crm-btn-sm">建立</button>
                <button type="button" id="quote-np-cancel" class="crm-btn crm-btn-secondary crm-btn-sm">取消</button>
            </div>
        </div>`;
    document.getElementById('quote-np-save').addEventListener('click', _createInlineProject);
    document.getElementById('quote-np-cancel').addEventListener('click', () => {
        area.style.display = 'none';
        document.getElementById('quote-f-project_id').value = '';
    });
}

async function _createInlineProject() {
    const name = document.getElementById('quote-np-name').value.trim();
    const clientId = document.getElementById('quote-np-client').value;
    if (!name || !clientId) { alert('請填寫專案名稱和選擇客戶'); return; }
    try {
        const r = await _fetch('/projects', { method: 'POST', body: JSON.stringify({ name, client_id: clientId, status: '洽談中' }) });
        _projects.push(r.project);
        _populateProjectSelect(r.project.id);
        document.getElementById('quote-inline-project').style.display = 'none';
    } catch (e) {
        alert('建立專案失敗：' + e.message);
    }
}

function _populateTemplateSelect() {
    const sel = document.getElementById('quote-f-template');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 不使用範本 —</option>` +
        _templates.map(t => `<option value="${t.id}">${_esc(t.name)}</option>`).join('');
}

function _populateClientFilter() {
    populateClientSelect('quote-filter-client', _clients);
}

function _parsePaymentStages(text) {
    if (!text) return [];
    return text.split(/[,，\/]/).map(s => {
        const m = s.trim().match(/^(.+?)\s*(\d+)%?$/);
        return m ? { label: m[1].trim(), pct: parseInt(m[2]) } : null;
    }).filter(Boolean);
}

function _paymentStagesToText(stages) {
    return (stages || []).map(s => `${s.label} ${s.pct}%`).join(', ');
}

async function openModal(quotation = null, projectId = null) {
    _editingId = quotation ? quotation.id : null;
    _editingProjectId = projectId;
    document.getElementById('quote-modal-title').textContent = quotation ? '編輯報價' : '新增報價';
    const errEl = document.getElementById('quote-modal-error');
    errEl.textContent = ''; errEl.style.display = 'none';

    _populateProjectSelect(quotation ? quotation.project_id : (projectId || ''));
    _populateTemplateSelect();

    // Fill fields
    const q = quotation || {};
    document.getElementById('quote-f-status').value = q.status || '草稿';
    document.getElementById('quote-f-quote_date').value = q.quote_date ? q.quote_date.substring(0, 10) : '';
    document.getElementById('quote-f-valid_until').value = q.valid_until ? q.valid_until.substring(0, 10) : '';
    document.getElementById('quote-f-tax_rate').value = q.tax_rate ?? 5;
    document.getElementById('quote-f-discount').value = q.discount || 0;
    document.getElementById('quote-f-final_price').value = q.final_price ?? '';
    document.getElementById('quote-f-payment_stages').value = _paymentStagesToText(q.payment_stages);
    document.getElementById('quote-f-terms').value = q.terms || '';
    document.getElementById('quote-f-template').value = '';

    _itemRows = (q.items || []).map(it => ({ ...it }));
    if (_itemRows.length === 0 && !quotation) addItemRow();
    else _renderItemRows();
    _recalcTotals();

    document.getElementById('quote-modal').style.display = 'flex';
}

async function saveQuotation() {
    const projectId = _editingProjectId || document.getElementById('quote-f-project_id').value;
    if (!projectId || projectId === '__new__') { _showModalError('請選擇專案'); return; }
    if (_itemRows.length === 0 || !_itemRows.some(it => it.description)) { _showModalError('請至少新增一個項目'); return; }

    const payload = {
        status: document.getElementById('quote-f-status').value,
        quote_date: document.getElementById('quote-f-quote_date').value || null,
        valid_until: document.getElementById('quote-f-valid_until').value || null,
        tax_rate: parseInt(document.getElementById('quote-f-tax_rate').value) || 5,
        discount: parseInt(document.getElementById('quote-f-discount').value) || 0,
        final_price: document.getElementById('quote-f-final_price').value ? parseInt(document.getElementById('quote-f-final_price').value) : null,
        payment_stages: _parsePaymentStages(document.getElementById('quote-f-payment_stages').value),
        terms: document.getElementById('quote-f-terms').value,
        items: _itemRows.filter(it => it.description).map(it => ({
            group_name: it.group_name || '', description: it.description,
            unit: it.unit || '式', quantity: it.quantity || 1,
            unit_price: it.unit_price || 0, internal_cost: it.internal_cost || 0, note: it.note || '',
        })),
    };

    const btn = document.getElementById('quote-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';

    try {
        if (_editingId) {
            await _fetch(`/quotations/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            await _fetch(`/projects/${projectId}/quotations`, { method: 'POST', body: JSON.stringify(payload) });
        }
        document.getElementById('quote-modal').style.display = 'none';
        await Promise.all([loadQuotations(), loadStats()]);
        // Refresh project detail quotation sub-tab if open
        if (window._projRefreshQuotes) window._projRefreshQuotes(projectId);
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false; btn.textContent = '儲存';
    }
}

async function deleteQuotation(q) {
    if (!confirm(`確定刪除報價 Q-${q.project_name}-v${q.version}？`)) return;
    try {
        await _fetch(`/quotations/${q.id}`, { method: 'DELETE' });
        closeDetail();
        await Promise.all([loadQuotations(), loadStats()]);
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

function _showModalError(msg) {
    const el = document.getElementById('quote-modal-error');
    el.textContent = msg; el.style.display = 'block';
}

// ── Template: Apply ──────────────────────────────────────────

function _applyTemplate(templateId) {
    const t = _templates.find(x => x.id === templateId);
    if (!t) return;
    _itemRows = (t.items || []).map(it => ({ ...it }));
    _renderItemRows();
    document.getElementById('quote-f-tax_rate').value = t.tax_rate ?? 5;
    document.getElementById('quote-f-terms').value = t.terms || '';
    document.getElementById('quote-f-payment_stages').value = _paymentStagesToText(t.payment_stages);
    _recalcTotals();
}

function _renderTemplateList() {
    const container = document.getElementById('quote-template-list');
    if (!container) return;
    if (_templates.length === 0) {
        container.innerHTML = '<div class="crm-empty">尚無範本</div>';
        return;
    }
    container.innerHTML = _templates.map(t => {
        const total = (t.items || []).reduce((s, it) => s + (it.quantity || 1) * (it.unit_price || 0), 0);
        return `
        <div class="quote-template-row">
            <div><strong>${_esc(t.name)}</strong> <span class="crm-muted">${(t.items || []).length} 項 $${_fmtNum(total)}</span></div>
            <div class="crm-muted">${_esc(t.description || '')}</div>
            <div style="margin-top:4px;">
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._quoteDeleteTemplate('${t.id}')">刪除</button>
            </div>
        </div>`;
    }).join('');
}

async function _saveCurrentAsTemplate() {
    const name = prompt('範本名稱：');
    if (!name) return;
    try {
        await _fetch('/quotation-templates', {
            method: 'POST',
            body: JSON.stringify({
                name, items: _itemRows.filter(it => it.description),
                tax_rate: parseInt(document.getElementById('quote-f-tax_rate').value) || 5,
                terms: document.getElementById('quote-f-terms').value,
                payment_stages: _parsePaymentStages(document.getElementById('quote-f-payment_stages').value),
            }),
        });
        await loadTemplates();
        _populateTemplateSelect();
        alert('範本已儲存');
    } catch (e) {
        alert('儲存失敗：' + e.message);
    }
}

async function _deleteTemplate(id) {
    if (!confirm('確定刪除此範本？')) return;
    try {
        await _fetch(`/quotation-templates/${id}`, { method: 'DELETE' });
        await loadTemplates();
        _renderTemplateList();
        _populateTemplateSelect();
    } catch (e) { alert(e.message); }
}

// ── Init ─────────────────────────────────────────────────────

export function initCrmQuotesTab() {
    for (const id of ['quote-modal', 'quote-template-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._quoteSelect = selectQuotation;
    window._quoteEdit = async (id) => {
        try {
            const q = await _fetch(`/quotations/${id}`);
            openModal(q);
        } catch (_) {}
    };
    window._quoteDelete = (id) => {
        const q = _quotations.find(x => x.id === id);
        if (q) deleteQuotation(q);
    };
    window._quoteDup = async (id) => {
        try {
            const q = await _fetch('/quotations/' + id);
            openModal(q, q.project_id);
            _editingId = null;
            document.getElementById('quote-modal-title').textContent = '複製報價';
        } catch (_) {}
    };
    window._quoteRemoveItem = removeItemRow;
    window._openQuoteModalForProject = (projectId) => openModal(null, projectId);
    window._quoteActivateProject = async (projectId) => {
        if (!confirm('確定將此專案狀態切為「進行中」？')) return;
        try {
            await _fetch(`/projects/${projectId}/status`, {
                method: 'PATCH', body: JSON.stringify({ status: '進行中' })
            });
            alert('專案已切為進行中');
        } catch (e) {
            alert('操作失敗：' + e.message);
        }
    };
    window._quoteDeleteTemplate = _deleteTemplate;

    // Search + filters
    let _searchTimer;
    document.getElementById('quote-search').addEventListener('input', e => {
        _filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadQuotations, 300);
    });
    document.getElementById('quote-filter-status').addEventListener('change', e => {
        _filters.status = e.target.value; loadQuotations();
    });
    document.getElementById('quote-filter-client').addEventListener('change', e => {
        _filters.client_id = e.target.value; loadQuotations();
    });

    // Buttons
    document.getElementById('quote-btn-add').addEventListener('click', () => openModal());
    document.getElementById('quote-btn-save').addEventListener('click', saveQuotation);
    document.getElementById('quote-btn-add-item').addEventListener('click', () => { addItemRow(); _recalcTotals(); });
    document.getElementById('quote-detail-close').addEventListener('click', closeDetail);
    document.getElementById('quote-btn-templates').addEventListener('click', () => {
        _renderTemplateList();
        document.getElementById('quote-template-modal').style.display = 'flex';
    });

    // Template apply
    document.getElementById('quote-f-template').addEventListener('change', e => {
        if (e.target.value) _applyTemplate(e.target.value);
    });

    // Project select: handle "+ 新增專案"
    document.getElementById('quote-f-project_id').addEventListener('change', e => {
        if (e.target.value === '__new__') {
            _showInlineProjectForm();
        } else {
            document.getElementById('quote-inline-project').style.display = 'none';
        }
    });

    // Recalc on discount/tax change
    document.getElementById('quote-f-discount').addEventListener('input', _recalcTotals);
    document.getElementById('quote-f-tax_rate').addEventListener('input', _recalcTotals);

    // Detail sub-tabs
    document.querySelectorAll('#quote-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#quote-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('quote-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('quote-detail-items').classList.toggle('hidden', tab !== 'items');
        });
    });

    // Modal overlay close
    for (const id of ['quote-modal', 'quote-template-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('quote-resize-handle', 'quote-detail-panel');

    await Promise.all([loadQuotations(), loadStats(), loadProjects(), loadClients(), loadUsers(), loadTemplates()]);
}
