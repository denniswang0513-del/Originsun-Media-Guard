/**
 * crm-quotes.js — 報價管理 Tab
 */

import { quoteTotals, parsePaymentStages, paymentStagesToText } from '../../js/shared/quote-amounts.js';
import { crmFetch as _fetch, esc as _esc, populateClientSelect, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, enumIndex, quotePdfFilename, initRootFolderCard } from './crm-utils.js';
import { authDownload, copyText } from '../../js/shared/utils.js';

// ── State ────────────────────────────────────────────────────

let _quotations = [];
let _projects = [];
let _clients = [];
let _users = [];
let _templates = [];
let _selectedId = null;
let _editingId = null;  // null=新增, string=編輯
let _editingQuote = null;   // 正在編的那筆（openModal 帶進來的物件；不從 _quotations 查——那份清單有狀態篩選，查不到就被當成新增）
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

// 正本在後端 core.finance_logic.QUOTE_STATUSES（手機版從 /crm/m/options 拿）；這裡是桌機鏡射，改要一起改
const _QUOTE_STATUSES = ['草稿', '已寄送', '已簽核', '已拒絕'];

function _qBadge(status) {
    const s = status || '草稿';
    const cls = _QUOTE_STATUSES.includes(s) ? `crm-badge crm-quote-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

// _QUOTE_STATUSES 已定義在上面 — 工作流順序:草稿→已寄送→已簽核→已拒絕
const _sorter = createSortable({
    storageKey: 'crm_quotes_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'quote-list-panel',
    onChange: () => renderList(),
    getters: {
        name:   q => `${q.project_name || ''}-v${q.version || 0}`.toLowerCase(),
        client: q => (q.client_short_name || q.project_name || '').toLowerCase(),
        status: q => enumIndex(_QUOTE_STATUSES, q.status, '草稿'),
        amount: q => q.final_price ?? q.total ?? 0,
        date:   q => q.quote_date || '',
    },
});

function renderList() {
    const body = document.getElementById('quote-list-body');
    if (!body) return;
    _sorter.attach();
    if (_quotations.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無報價單${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }
    body.innerHTML = _sorter.sorted(_quotations).map(q => {
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
    {name:'spec', label:'規格', type:'textarea'},
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
        ${prop('規格', q.spec)}
        ${prop('小計', '$' + _fmtNum(q.subtotal))}
        ${prop('折扣', q.discount ? '-$' + _fmtNum(q.discount) : '')}
        ${prop('稅額', '$' + _fmtNum(q.tax_amount) + ' (' + q.tax_rate + '%)')}
        ${prop('含稅總計', '$' + _fmtNum(q.total))}
        ${prop('最終報價', q.final_price !== null ? '$' + _fmtNum(q.final_price) : '')}
        ${prop('付款階段', (q.payment_stages || []).map(s => s.label + ' ' + s.pct + '%').join(' / '))}
        ${prop('備註', q.terms)}
        ${q.status === '已簽核' ? `<div style="padding:12px 0;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._quoteActivateProject('${q.project_id}')">啟動專案 (切為製作)</button></div>` : ''}
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
        // PDF 與分享連結要寄出之後才出現（owner 2026-09-07「送出再產生連結與 pdf 按鈕」）：草稿還在改，不該流出去
        const sent = q.status !== _QUOTE_STATUSES[0];
        // 鑄連結是管理員限定（POST /share）：非管理員只有已經有連結時才給「複製」（同手機版）
        const canShare = q.share_url || (window._accessLevel || 0) >= 3;
        actions.innerHTML = (sent ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="quote-btn-pdf">下載 PDF</button>`
            + (canShare ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="quote-btn-share">${q.share_url ? '複製連結' : '分享連結'}</button>` : '') : '')
            + `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
        actions.querySelector('#quote-btn-pdf')?.addEventListener('click', () =>
            authDownload('/api/v1/crm/quotations/' + q.id + '/pdf', quotePdfFilename(q), '下載 PDF'));
        // 線上檢視連結（免登入、頁上可下載 PDF）：沒有就鑄一條（冪等），然後複製完整網址
        actions.querySelector('#quote-btn-share')?.addEventListener('click', async (ev) => {
            const btn = ev.currentTarget;      // await 之後 currentTarget 就是 null 了，先抓住
            try {
                if (!q.share_url) {
                    const r = await _fetch('/quotations/' + q.id + '/share', { method: 'POST' });
                    q.share_url = r.share_url;
                    btn.textContent = '複製連結';
                }
                const full = location.origin + q.share_url;
                // 共用的 copyText：非 https（LAN 直連）走 execCommand 退路，按鈕閃「已複製」；真的不行才 prompt
                if (!(await copyText(full, btn))) prompt('連結（請自行複製）：', full);
            } catch (e) { alert('建立連結失敗：' + e.message); }
        });
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
    });
}

// 報價表單的狀態（跟手機版 _form 同形）：客戶、要連結的案、優惠／最終報價哪一格是人打的
let _form = { client_id: '', project_id: '', anchor: null };

function _recalcTotals() {
    // 金額規則跟後端 _calc_quotation 同一份（js/shared/quote-amounts.js）。稅前折扣已退場（同手機版）：
    // 優惠一律走「最終報價」倒算，報價單上印成「專案優惠」。舊報價存著的稅前折扣仍要算進去，
    // 不然畫面的含稅總計跟後端存的對不上、優惠↔最終報價也會用錯底數
    const taxRate = parseInt(document.getElementById('quote-f-tax_rate')?.value) || 0;
    const { subtotal, tax, total } = quoteTotals({ items: _itemRows, taxRate, discount: _editingQuote?.discount || 0 });

    document.getElementById('quote-calc-subtotal').textContent = '$' + _fmtNum(subtotal);
    document.getElementById('quote-calc-tax-pct').textContent = taxRate;
    document.getElementById('quote-calc-tax').textContent = '$' + _fmtNum(tax);
    document.getElementById('quote-calc-total').textContent = '$' + _fmtNum(total);

    // 優惠 ↔ 最終報價 互推：只寫「不是正在打的那一格」，免得游標被搶（同手機版 recalc）
    const promoEl = document.getElementById('quote-f-promo'), finalEl = document.getElementById('quote-f-final_price');
    if (_form.anchor === 'promo') {
        finalEl.value = promoEl.value === '' ? '' : Math.max(total - (parseInt(promoEl.value) || 0), 0);
    } else if (_form.anchor === 'final') {
        promoEl.value = finalEl.value === '' ? '' : Math.max(total - (parseInt(finalEl.value) || 0), 0);
    }
    // 0 是合法的最終報價（全免）：只有空字串才算「沒填」（同手機版 recalc、同存檔規則）
    const finalPrice = finalEl.value.trim() === '' ? null : (parseInt(finalEl.value) || 0);
    document.getElementById('quote-calc-final').textContent = '$' + _fmtNum(finalPrice != null ? finalPrice : total);

    document.querySelectorAll('.quote-item-edit-row').forEach(row => {
        const idx = parseInt(row.dataset.idx);
        const it = _itemRows[idx];
        row.querySelector('.qi-amount').textContent = '$' + _fmtNum(it.quantity * it.unit_price);
    });
}

// ── Modal: Open / Save ───────────────────────────────────────

function _populateClientSelect(selectedId = '') {
    const sel = document.getElementById('quote-f-client_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 打字找客戶 —</option>` +
        _clients.map(c => `<option value="${c.id}"${c.id === selectedId ? ' selected' : ''}>${_esc(c.short_name || c.full_name || c.id)}</option>`).join('');
    sel._ssSync?.();
}

/** 連結既有專案：拿這個客戶的全部案子（免得打同名再建一個重複的案）——同手機版 loadClientProjects */
async function _loadClientProjects(clientId) {
    const sel = document.getElementById('quote-f-link_project');
    if (!sel) return;
    let rows = [];
    if (clientId) {
        try { rows = (await _fetch('/projects?client_id=' + encodeURIComponent(clientId))).projects || []; }
        catch (_) { rows = []; }
    }
    sel.innerHTML = `<option value="">${rows.length ? '（不連結，照上面的案名建新案）' : (clientId ? '這個客戶還沒有案子' : '先選客戶')}</option>` +
        rows.map(p => `<option value="${p.id}">${_esc(p.name)}</option>`).join('');
    sel._rows = rows;
}

async function _createClientInline() {
    const name = (prompt('客戶名稱（代稱）：') || '').trim();
    if (!name) return;
    try {
        const r = await _fetch('/clients', { method: 'POST', body: JSON.stringify({ short_name: name }) });
        const c = r.client || r;
        _clients.push({ id: c.id, short_name: c.short_name || name, full_name: c.full_name || '' });
        _populateClientSelect(c.id);
        _form.client_id = c.id;
        await _loadClientProjects(c.id);
        _populateClientFilter();
    } catch (e) { alert('建立客戶失敗：' + e.message); }
}

/** 編輯／從專案頁開＝案子固定；新增＝客戶／案名／連結案三格（同手機版 formHtml 的分流） */
function _setProjectMode(fixedLabel) {
    const fixed = !!fixedLabel;
    document.getElementById('quote-f-project-fixed').style.display = fixed ? '' : 'none';
    document.getElementById('quote-f-project-label').textContent = fixedLabel || '';
    for (const id of ['quote-f-client-field', 'quote-f-name-field', 'quote-f-link-field']) {
        document.getElementById(id).style.display = fixed ? 'none' : '';
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

async function openModal(quotation = null, projectId = null) {
    _editingId = quotation ? quotation.id : null;
    _editingQuote = quotation;
    _editingProjectId = projectId;
    document.getElementById('quote-modal-title').textContent = quotation ? '編輯報價' : '新增報價';
    const errEl = document.getElementById('quote-modal-error');
    errEl.textContent = ''; errEl.style.display = 'none';

    _populateTemplateSelect();
    _form = { client_id: '', project_id: '', anchor: quotation && quotation.final_price != null ? 'final' : null };
    if (quotation) {
        _setProjectMode([quotation.client_short_name, quotation.project_name].filter(Boolean).join('｜') || '（未連專案）');
    } else if (projectId) {
        const p = _projects.find(x => x.id === projectId);
        _setProjectMode(p ? [p.client_short_name, p.name].filter(Boolean).join('｜') : projectId);
    } else {
        _setProjectMode('');
        _populateClientSelect('');
        document.getElementById('quote-f-project_name').value = '';
        await _loadClientProjects('');
    }

    // Fill fields
    const q = quotation || {};
    document.getElementById('quote-f-status').value = q.status || '草稿';
    document.getElementById('quote-f-quote_date').value = q.quote_date ? q.quote_date.substring(0, 10) : '';
    document.getElementById('quote-f-valid_until').value = q.valid_until ? q.valid_until.substring(0, 10) : '';
    document.getElementById('quote-f-tax_rate').value = q.tax_rate ?? 5;
    document.getElementById('quote-f-promo').value = '';
    document.getElementById('quote-f-final_price').value = q.final_price ?? '';
    document.getElementById('quote-f-payment_stages').value = paymentStagesToText(q.payment_stages);
    document.getElementById('quote-f-terms').value = q.terms || '';
    document.getElementById('quote-f-spec').value = q.spec || '';
    document.getElementById('quote-f-template').value = '';

    _itemRows = (q.items || []).map(it => ({ ...it }));
    if (_itemRows.length === 0 && !quotation) addItemRow();
    else _renderItemRows();
    _recalcTotals();

    document.getElementById('quote-modal').style.display = 'flex';
}

async function saveQuotation() {
    const editing = _editingQuote;
    let projectId = editing ? editing.project_id : (_editingProjectId || _form.project_id || '');
    const projectName = document.getElementById('quote-f-project_name').value.trim();
    if (!editing && !_editingProjectId) {
        if (!_form.client_id) { _showModalError('請先選客戶'); return; }
        if (!projectId && !projectName) { _showModalError('請填專案名稱，或連結一個既有專案'); return; }
    }
    if (_itemRows.length === 0 || !_itemRows.some(it => it.description)) { _showModalError('請至少新增一個項目'); return; }

    const payload = {
        status: document.getElementById('quote-f-status').value,
        quote_date: document.getElementById('quote-f-quote_date').value || null,
        valid_until: document.getElementById('quote-f-valid_until').value || null,
        tax_rate: parseInt(document.getElementById('quote-f-tax_rate').value) || 5,
        discount: editing ? (editing.discount || 0) : 0,          // 稅前折扣已退場：舊值原樣帶回，不洗掉
        final_price: document.getElementById('quote-f-final_price').value ? parseInt(document.getElementById('quote-f-final_price').value) : null,
        payment_stages: parsePaymentStages(document.getElementById('quote-f-payment_stages').value),
        terms: document.getElementById('quote-f-terms').value,
        spec: document.getElementById('quote-f-spec').value.trim(),
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
            if (!projectId) {              // 案子還沒成立：先照案名開一個殼案（同手機版；階段＝提案）
                const np = await _fetch('/projects', { method: 'POST', body: JSON.stringify({ name: projectName, client_id: _form.client_id, status: '提案' }) });
                projectId = (np.project || np).id;
                _projects.push(np.project || np);
            }
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
    document.getElementById('quote-f-payment_stages').value = paymentStagesToText(t.payment_stages);
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

async function _createTemplate(body) {
    await _fetch('/quotation-templates', { method: 'POST', body: JSON.stringify(body) });
    await loadTemplates();
    _renderTemplateList();
    _populateTemplateSelect();
}

// 報價彈窗目前的表單（稅率／條款／付款階段／項目）→ 一個範本
function _saveCurrentAsTemplate(name) {
    return _createTemplate({
        name, items: _itemRows.filter(it => it.description),
        tax_rate: parseInt(document.getElementById('quote-f-tax_rate').value) || 5,
        terms: document.getElementById('quote-f-terms').value,
        payment_stages: parsePaymentStages(document.getElementById('quote-f-payment_stages').value),
    });
}

// 兩個入口都會問名字：範本管理彈窗的「+ 新增範本」建空範本（之後套用它再調整）、
// 報價彈窗的「存成範本」把目前表單存起來。兩顆都曾經沒綁事件、_saveCurrentAsTemplate 沒人呼叫。
// 🔴 別把「表單開著就存表單」塞進 _addTemplate：範本彈窗從工具列開，那時報價彈窗必定是關的。
async function _promptTemplate(save) {
    const name = (prompt('範本名稱：') || '').trim();
    if (!name) return;
    try { await save(name); } catch (e) { alert('儲存失敗：' + e.message); }
}

const _addTemplate = () => _promptTemplate(
    name => _createTemplate({ name, items: [], tax_rate: 5, terms: '', payment_stages: [] }));

const _addTemplateFromForm = () => _promptTemplate(_saveCurrentAsTemplate);

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

// 三個彈窗入口也給專案頁的報價子頁 import（crm-projects-quotes.js）——它們在這裡是 window.* 給
// inline onclick 用，在那邊是模組匯出，名字錯了會在載入時就炸，不會變成按了沒反應的死按鈕
export async function quoteEdit(id) {
    try {
        const q = await _fetch(`/quotations/${id}`);
        openModal(q);
    } catch (_) {}
}
export async function quoteDup(id) {
    try {
        const q = await _fetch('/quotations/' + id);
        openModal(q, q.project_id);
        _editingId = null; _editingQuote = null;   // 複製＝新增：不帶舊的稅前折扣（表單沒有那一欄，帶了看不到也拿不掉）
        _recalcTotals();
        document.getElementById('quote-modal-title').textContent = '複製報價';
    } catch (_) {}
}
export const openQuoteForProject = (projectId) => openModal(null, projectId);

export async function initCrmQuotesTab() {
    // 只有 app.js 的載入器會呼叫（它自己去重：載過不再載、進行中只載一次）；以前這裡另有一個
    // init 守衛擋第二條載入路，那條路 2026-09-03 已收進載入器——守衛留著反而會在 init
    // 中途炸掉時把重試也擋掉
    for (const id of ['quote-modal', 'quote-template-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._quoteSelect = selectQuotation;
    window._quoteEdit = quoteEdit;
    window._quoteDelete = (id) => {
        const q = _quotations.find(x => x.id === id);
        if (q) deleteQuotation(q);
    };
    window._quoteDup = quoteDup;
    window._quoteRemoveItem = removeItemRow;
    window._quoteActivateProject = async (projectId) => {
        if (!confirm('確定將此專案狀態切為「製作」？')) return;
        try {
            await _fetch(`/projects/${projectId}/status`, {
                method: 'PATCH', body: JSON.stringify({ status: '製作' })
            });
            alert('專案已切為製作');
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
    // 公司資訊（報價單抬頭／匯款／Logo／章）就近開：系統設定 → 公司資訊分頁
    // （owner 2026-09-07「公司資訊的按鈕要放在報價管理的範本欄」）。設定只有管理員讀得到，別人不顯示這顆。
    // 報價單資料夾（owner 2026-09-07「跟發票一樣有個地方指定儲存位置」）：每次產 PDF 都存一份到那裡。管理員限定，不 await
    initRootFolderCard({
        linkId: 'quote-root-toggle', panelId: 'quote-root-panel', endpoint: '/quotations-root', key: 'quotes_root',
        intro: `報價單 PDF 的存放根目錄：每次下載／線上檢視產出的 PDF 都會存一份到這裡（同名覆蓋）。
        要集中到 NAS 就填那個路徑；底下會自動分 <code>{年}/{年-月}/</code>，檔名跟下載的 PDF 一樣（日期_客戶_專案）。`,
        placeholder: '例：\\\\192.168.1.132\\Archive\\Quotations 或 T:\\報價單',
        savedMsg: '已儲存（之後產出的報價單存到新位置；舊檔不搬）',
    });
    const companyBtn = document.getElementById('quote-btn-company');
    if (companyBtn && (window._accessLevel || 0) >= 3) {
        companyBtn.style.display = '';
        companyBtn.addEventListener('click', () => {
            document.getElementById('btnOpenSettings')?.click();
            [...document.querySelectorAll('#settingsModal .tab-btn')].find((b) => b.textContent.trim() === '公司資訊')?.click();
            // 只要公司資訊（owner 2026-09-07「請拉出公司資訊就好，其他 tab 不用」）：藏分頁列、標題改成公司資訊
            const modal = document.getElementById('settingsModal');
            if (modal) {
                modal.classList.add('company-only');
                const h3 = modal.querySelector('.modal-header h3');
                if (h3) { h3.dataset.full = h3.dataset.full || h3.textContent; h3.textContent = '公司資訊'; }
            }
        });
    }
    document.getElementById('quote-btn-templates').addEventListener('click', () => {
        _renderTemplateList();
        document.getElementById('quote-template-modal').style.display = 'flex';
    });
    document.getElementById('quote-tpl-btn-add').addEventListener('click', _addTemplate);
    document.getElementById('quote-btn-as-template').addEventListener('click', _addTemplateFromForm);

    // Template apply
    document.getElementById('quote-f-template').addEventListener('change', e => {
        if (e.target.value) _applyTemplate(e.target.value);
    });

    // 客戶 → 撈他的案子給「連結既有專案」；打案名＝要建新案（剛連的就不算）；連結案＝案名帶進來
    document.getElementById('quote-f-client_id').addEventListener('change', e => { _form.client_id = e.target.value; _loadClientProjects(e.target.value); });
    document.getElementById('quote-btn-new-client').addEventListener('click', _createClientInline);
    document.getElementById('quote-f-project_name').addEventListener('input', () => {
        _form.project_id = '';
        const link = document.getElementById('quote-f-link_project'); if (link) link.value = '';
    });
    document.getElementById('quote-f-link_project').addEventListener('change', e => {
        _form.project_id = e.target.value;
        const hit = (e.target._rows || []).find(r => r.id === e.target.value);
        if (hit) document.getElementById('quote-f-project_name').value = hit.name;
    });

    // Recalc on tax / promo / final change（優惠與最終報價互推）
    document.getElementById('quote-f-tax_rate').addEventListener('input', _recalcTotals);
    document.getElementById('quote-f-promo').addEventListener('input', () => { _form.anchor = 'promo'; _recalcTotals(); });
    document.getElementById('quote-f-final_price').addEventListener('input', () => { _form.anchor = 'final'; _recalcTotals(); });

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
