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
            <div class="crm-row-actions" onclick="event.stopPropagation()">
                ${!allPaid ? `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._payablePayAll('${_esc(p.payee_name)}')">全部付款</button>` : ''}
            </div>
        </div>`;
    }).join('');
}

function renderDetail(p) {
    document.getElementById('payable-detail-title').textContent = p.payee_name;

    const bankHtml = p.bank_name
        ? `<div class="crm-detail-prop"><div class="crm-prop-label">銀行</div><div class="crm-prop-value">${_esc(p.bank_name)} ${_esc(p.bank_account)}</div></div>`
        : '';

    const itemsHtml = p.items.map(it => {
        const isPaid = it.payment_status === '已付款';
        return `
        <div class="payable-item" style="${isPaid ? 'opacity:0.5;' : ''}">
            <label class="payable-check">
                <input type="checkbox" data-id="${it.id}" class="pay-cb" ${isPaid ? 'checked disabled' : ''}>
            </label>
            <span class="payable-item-date">${_esc(it.date)}</span>
            <span class="payable-item-summary">${_esc(it.summary)}</span>
            <span class="payable-item-cat">${_esc(it.category)}</span>
            <span class="payable-item-amt">$${_fmtNum(it.amount)}</span>
            <span style="width:50px;text-align:center;font-size:11px;color:${isPaid ? '#86efac' : '#fca5a5'};">${isPaid ? '已付' : '未付'}</span>
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
        <div style="display:flex;gap:8px;padding:10px 0;border-top:1px solid #2e2e2e;margin-top:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._payableCopyInfo('${_esc(p.payee_name)}')">複製匯款資訊</button>
            ${p.items.some(it => it.payment_status !== '已付款') ? `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._payablePayAll('${_esc(p.payee_name)}')">全部付款</button>` : ''}
        </div>
    `;

    // Bind checkbox events
    document.querySelectorAll('#payable-detail-content .pay-cb:not(:disabled)').forEach(cb => {
        cb.addEventListener('change', async e => {
            if (!e.target.checked) return;
            if (!confirm('確定標記此筆為已付款？')) { e.target.checked = false; return; }
            try {
                await _fetch('/payments/batch-pay', {
                    method: 'PATCH',
                    body: JSON.stringify({ payment_ids: [e.target.dataset.id], payment_date: new Date().toISOString().substring(0, 10) })
                });
                await loadPayables();
                // Re-render detail
                const updated = _payees.find(x => x.payee_name === _selectedName);
                if (updated) renderDetail(updated);
            } catch (err) { alert(err.message); e.target.checked = false; }
        });
    });
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
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: unpaidIds, payment_date: new Date().toISOString().substring(0, 10) })
        });
        await loadPayables();
        if (_selectedName === name) {
            const updated = _payees.find(x => x.payee_name === name);
            if (updated) renderDetail(updated);
        }
    } catch (e) { alert(e.message); }
};

export function initCrmPayablesTab() {
    const monthInput = document.getElementById('payable-month');
    const now = new Date();
    monthInput.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    monthInput.addEventListener('change', loadPayables);
    document.getElementById('payable-filter-status').addEventListener('change', loadPayables);
    document.getElementById('payable-detail-close').addEventListener('click', closeDetail);
    setupResizeHandle('payable-resize-handle', 'payable-detail-panel');
    loadPayables();
}
