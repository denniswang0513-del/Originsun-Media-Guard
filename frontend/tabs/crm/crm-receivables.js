/**
 * crm-receivables.js — 應收帳款子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, createSortable } from './crm-utils.js';

let _clients = [];
let _selectedName = null;

async function loadReceivables() {
    const statusSel = document.getElementById('recv-filter-status');
    const status = statusSel?.value || '';
    try {
        const params = status === 'all' ? '' : '?status=' + encodeURIComponent(status);
        const data = await _fetch('/receivables/summary' + params);
        _clients = data.clients || [];
        document.getElementById('recv-total').textContent = '$' + _fmtNum(data.grand_total);
    } catch (_) {
        _clients = [];
    }
    renderList();
    if (_selectedName) {
        const updated = _clients.find(x => x.company_name === _selectedName);
        if (updated) renderDetail(updated);
    }
}

// `c.items` 預期非空(API 過濾過 ≥1 張未收發票),空時 _maxDays 回 0
const _maxDays = (c) => Math.max(0, ...c.items.map(it => it.days_since_issued || 0));
const _sorter = createSortable({
    storageKey: 'crm_receivables_sort',
    defaultSort: { key: 'days', dir: 'desc' },
    panelId: 'recv-list-panel',
    onChange: () => renderList(),
    getters: {
        client: c => (c.company_name || '').toLowerCase(),
        amount: c => c.total_amount || 0,
        count:  c => c.items?.length || 0,
        days:   c => _maxDays(c),
    },
});

function renderList() {
    const body = document.getElementById('recv-list-body');
    if (!body) return;
    _sorter.attach();
    if (_clients.length === 0) {
        body.innerHTML = '<div class="crm-empty">無應收帳款</div>';
        return;
    }
    body.innerHTML = _sorter.sorted(_clients).map(c => {
        const maxDays = _maxDays(c);
        const daysColor = maxDays > 60 ? '#ef4444' : maxDays > 30 ? '#fbbf24' : '#9ca3af';
        return `
        <div class="crm-row${c.company_name === _selectedName ? ' selected' : ''}" onclick="window._recvSelect('${_esc(c.company_name)}')">
            <div class="crm-row-name">${_esc(c.company_name)}</div>
            <div class="crm-row-amount" style="color:#86efac;">$${_fmtNum(c.total_amount)}</div>
            <div>${c.items.length} 張</div>
            <div style="color:${daysColor};">${maxDays} 天</div>
        </div>`;
    }).join('');
}

function renderDetail(c) {
    document.getElementById('recv-detail-title').textContent = c.company_name;

    const actionsArea = document.getElementById('recv-bar-actions');
    if (actionsArea) {
        actionsArea.innerHTML = `<button id="recv-detail-close" class="crm-detail-close" title="關閉" onclick="window._recvClose()">&#x2715;</button>`;
    }

    const itemsHtml = c.items.map(it => {
        const isReceived = it.payment_status === '已收款';
        const daysColor = it.days_since_issued > 60 ? '#ef4444' : it.days_since_issued > 30 ? '#fbbf24' : '#9ca3af';
        return `
        <div class="payable-item" id="recv-row-${it.id}">
            <span class="payable-item-date">${it.invoice_date || '—'}</span>
            <span class="payable-item-summary">${_esc(it.title)}${it.invoice_number ? ' (' + _esc(it.invoice_number) + ')' : ''}</span>
            <span class="payable-item-cat">${_esc(it.project_name || it.category)}</span>
            <span class="payable-item-amt">$${_fmtNum(it.amount_total)}</span>
            <span style="min-width:50px;text-align:center;color:${daysColor};font-size:11px;">${it.days_since_issued}天</span>
            <span style="min-width:60px;text-align:right;font-size:11px;color:${isReceived ? '#86efac' : '#fbbf24'};">${isReceived ? '已收款' : '未收款'}</span>
        </div>`;
    }).join('');

    document.getElementById('recv-detail-content').innerHTML = `
        <div class="crm-detail-prop"><div class="crm-prop-label">客戶</div><div class="crm-prop-value" style="font-weight:700;">${_esc(c.company_name)}</div></div>
        ${c.tax_id ? `<div class="crm-detail-prop"><div class="crm-prop-label">統編</div><div class="crm-prop-value">${_esc(c.tax_id)}</div></div>` : ''}
        ${c.payment_info ? `<div class="crm-detail-prop"><div class="crm-prop-label">匯款資訊</div><div class="crm-prop-value">${_esc(c.payment_info)}</div></div>` : ''}
        <div class="crm-detail-prop"><div class="crm-prop-label">應收總額</div><div class="crm-prop-value" style="font-weight:700;color:#86efac;">$${_fmtNum(c.total_amount)}</div></div>
        <div style="border-top:1px solid #2e2e2e;margin:10px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;margin-bottom:6px;">發票明細</div>
        ${itemsHtml}
    `;
}

function selectClient(name) {
    _selectedName = name;
    renderList();
    document.getElementById('recv-detail-panel').style.display = 'flex';
    document.getElementById('recv-resize-handle').style.display = '';
    const c = _clients.find(x => x.company_name === name);
    if (c) renderDetail(c);
}

function closeDetail() {
    _selectedName = null;
    document.getElementById('recv-detail-panel').style.display = 'none';
    document.getElementById('recv-resize-handle').style.display = 'none';
    renderList();
}

window._recvSelect = selectClient;
window._recvClose = closeDetail;
window._recvRefresh = () => loadReceivables();


export function initCrmReceivablesTab() {
    document.getElementById('recv-filter-status').addEventListener('change', loadReceivables);
    setupResizeHandle('recv-resize-handle', 'recv-detail-panel');
    loadReceivables();
}
