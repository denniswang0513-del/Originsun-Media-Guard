/**
 * crm-payables.js — 應付帳款子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle } from './crm-utils.js';

let _payees = [];
let _selectedName = null;
let _statusFilter = '應付款';

async function loadPayables() {
    const monthInput = document.getElementById('payable-month');
    const month = monthInput?.value || '';
    _statusFilter = document.getElementById('payable-filter-status')?.value || '';

    try {
        const data = await _fetch('/payables/summary?month=' + month + '&status=' + _statusFilter);
        _payees = data.payees || [];
        document.getElementById('payable-total').textContent = '$' + _fmtNum(data.grand_total);
        if (!monthInput.value && data.month) monthInput.value = data.month;
    } catch (_) {
        _payees = [];
    }
    renderList();
    // If detail is open, refresh it
    if (_selectedName) {
        const updated = _payees.find(x => x.payee_name === _selectedName);
        if (updated) renderDetail(updated);
    }
}

function renderList() {
    const body = document.getElementById('payable-list-body');
    if (!body) return;
    if (_payees.length === 0) {
        body.innerHTML = `<div class="crm-empty">本月無${_statusFilter || ''}款項</div>`;
        return;
    }
    body.innerHTML = _payees.map(p => {
        const allPaid = p.items.every(it => it.payment_status === '已付款');
        const statusCls = allPaid ? 'crm-badge crm-pay-全額到帳' : 'crm-badge crm-pay-未到帳';
        const statusText = allPaid ? '已付款' : '未付款';
        return `
        <div class="crm-row${p.payee_name === _selectedName ? ' selected' : ''}" onclick="window._payableSelect('${_esc(p.payee_name)}')">
            <div class="crm-row-name">${_esc(p.payee_name)}</div>
            <div class="crm-row-amount">$${_fmtNum(p.total_amount)}</div>
            <div class="crm-row-client" style="font-size:11px;">${_esc(p.bank_name ? p.bank_name + ' ' + p.bank_account : '')}</div>
            <div class="crm-row-status"><span class="${statusCls}">${statusText}</span></div>
            <div class="crm-row-actions" onclick="event.stopPropagation()"></div>
        </div>`;
    }).join('');
}

function renderDetail(p) {
    // Title bar: name + buttons on the right
    const titleEl = document.getElementById('payable-detail-title');
    titleEl.textContent = p.payee_name;

    // Inject action buttons into the detail bar actions area
    const actionsArea = document.getElementById('payable-bar-actions');
    if (actionsArea) {
        const hasUnpaid = p.items.some(it => it.payment_status !== '已付款');
        actionsArea.innerHTML = `
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._payableCopyInfo('${_esc(p.payee_name)}')">複製匯款資訊</button>
            ${hasUnpaid ? `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._payablePayAll('${_esc(p.payee_name)}')">全部付款</button>` : ''}
            <button id="payable-detail-close" class="crm-detail-close" title="關閉" onclick="window._payableClose()">&#x2715;</button>
        `;
    }

    const bankHtml = p.bank_name
        ? `<div class="crm-detail-prop"><div class="crm-prop-label">銀行</div><div class="crm-prop-value">${_esc(p.bank_name)} ${_esc(p.bank_account)}</div></div>`
        : '';

    const today = new Date().toISOString().substring(0, 10);
    const itemsHtml = p.items.map(it => {
        const isPaid = it.payment_status === '已付款';
        return `
        <div class="payable-item" id="payable-row-${it.id}">
            <span class="payable-item-summary">${_esc(it.summary)}</span>
            <span class="payable-item-cat">${_esc(it.category)}</span>
            <span class="payable-item-amt">$${_fmtNum(it.amount)}</span>
            ${isPaid
                ? `<span style="min-width:160px;text-align:right;font-size:11px;color:#86efac;">${it.payment_date || ''} 已付款</span>`
                : `<button class="crm-btn crm-btn-danger crm-btn-sm" style="min-width:80px;" data-id="${it.id}" onclick="window._payableSinglePay(this,'${it.id}','${_esc(p.payee_name)}')">未付款</button>`
            }
        </div>`;
    }).join('');

    document.getElementById('payable-detail-content').innerHTML = `
        <div class="crm-detail-prop"><div class="crm-prop-label">收款人</div><div class="crm-prop-value" style="font-weight:700;">${_esc(p.payee_name)}</div></div>
        <div class="crm-detail-prop"><div class="crm-prop-label">身分證</div><div class="crm-prop-value">${_esc(p.payee_id)}</div></div>
        ${bankHtml}
        <div class="crm-detail-prop"><div class="crm-prop-label">應付總額</div><div class="crm-prop-value" style="font-weight:700;color:#fbbf24;">$${_fmtNum(p.total_amount)}</div></div>
        <div style="border-top:1px solid #2e2e2e;margin:10px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;margin-bottom:6px;">付款明細</div>
        ${itemsHtml}
    `;
}

function selectPayee(name) {
    _selectedName = name;
    renderList();
    const panel = document.getElementById('payable-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('payable-resize-handle');
    if (handle) handle.style.display = '';
    const p = _payees.find(x => x.payee_name === name);
    if (p) renderDetail(p);
}

function closeDetail() {
    _selectedName = null;
    document.getElementById('payable-detail-panel').style.display = 'none';
    document.getElementById('payable-resize-handle').style.display = 'none';
    renderList();
}

window._payableSelect = selectPayee;
window._payableClose = closeDetail;
window._payableRefresh = () => loadPayables();

// Single pay: immediately update UI, then call API
window._payableSinglePay = async (btn, paymentId, payeeName) => {
    if (!confirm('確定標記此筆為已付款？')) return;
    const today = new Date().toISOString().substring(0, 10);
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], payment_date: today })
        });
        // Immediately replace button with paid text (no reload)
        const row = document.getElementById('payable-row-' + paymentId);
        if (row) {
            btn.outerHTML = `<span style="min-width:160px;text-align:right;font-size:11px;color:#86efac;">${today} 已付款</span>`;
        }
    } catch (e) { alert(e.message); }
};

window._payableCopyInfo = (name) => {
    const p = _payees.find(x => x.payee_name === name);
    if (!p) return;
    const unpaid = p.items.filter(it => it.payment_status !== '已付款');
    const amount = unpaid.reduce((s, it) => s + it.amount, 0);
    const text = [
        '收款人: ' + p.payee_name,
        p.payee_id ? '身分證: ' + p.payee_id : '',
        p.bank_name ? '銀行: ' + p.bank_name : '',
        p.bank_account ? '帳號: ' + p.bank_account : '',
        '金額: $' + amount.toLocaleString('zh-TW'),
    ].filter(Boolean).join('\n');
    navigator.clipboard.writeText(text).then(() => alert('已複製匯款資訊')).catch(() => {
        prompt('請手動複製:', text);
    });
};

window._payablePayAll = async (name) => {
    const p = _payees.find(x => x.payee_name === name);
    if (!p) return;
    const unpaidIds = p.items.filter(it => it.payment_status !== '已付款').map(it => it.id);
    if (!unpaidIds.length) return;
    if (!confirm(`確定將 ${name} 的 ${unpaidIds.length} 筆全部標記為已付款？`)) return;
    const today = new Date().toISOString().substring(0, 10);
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: unpaidIds, payment_date: today })
        });
        // Immediately update all buttons
        for (const id of unpaidIds) {
            const row = document.getElementById('payable-row-' + id);
            if (row) {
                const btn = row.querySelector('.crm-btn-danger');
                if (btn) btn.outerHTML = `<span style="min-width:160px;text-align:right;font-size:11px;color:#86efac;">${today} 已付款</span>`;
            }
        }
    } catch (e) { alert(e.message); }
};

export function initCrmPayablesTab() {
    const monthInput = document.getElementById('payable-month');
    const now = new Date();
    monthInput.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    monthInput.addEventListener('change', loadPayables);
    document.getElementById('payable-filter-status').addEventListener('change', loadPayables);
    setupResizeHandle('payable-resize-handle', 'payable-detail-panel');
    loadPayables();
}
