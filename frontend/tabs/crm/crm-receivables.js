/**
 * crm-receivables.js — 應收帳款子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle } from './crm-utils.js';

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

function renderList() {
    const body = document.getElementById('recv-list-body');
    if (!body) return;
    if (_clients.length === 0) {
        body.innerHTML = '<div class="crm-empty">無應收帳款</div>';
        return;
    }
    body.innerHTML = _clients.map(c => {
        const maxDays = Math.max(0, ...c.items.map(it => it.days_since_issued || 0));
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
        const hasUnpaid = c.items.some(it => it.payment_status !== '已收款');
        actionsArea.innerHTML = `
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._recvCopyInfo('${_esc(c.company_name)}')">複製客戶資訊</button>
            ${hasUnpaid ? `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._recvReceiveAll('${_esc(c.company_name)}')">全部收款</button>` : ''}
            <button id="recv-detail-close" class="crm-detail-close" title="關閉" onclick="window._recvClose()">&#x2715;</button>
        `;
    }

    const itemsHtml = c.items.map(it => {
        const isReceived = it.payment_status === '已收款';
        const daysColor = it.days_since_issued > 60 ? '#ef4444' : it.days_since_issued > 30 ? '#fbbf24' : '#9ca3af';
        return `
        <div class="payable-item" id="recv-row-${it.id}">
            <span class="payable-item-summary">${_esc(it.title)}${it.invoice_number ? ' (' + _esc(it.invoice_number) + ')' : ''}</span>
            <span class="payable-item-cat">${_esc(it.project_name || it.category)}</span>
            <span class="payable-item-amt">$${_fmtNum(it.amount_total)}</span>
            <span style="min-width:50px;text-align:center;color:${daysColor};font-size:11px;">${it.days_since_issued}天</span>
            ${isReceived
                ? '<span style="min-width:80px;text-align:right;font-size:11px;color:#86efac;">已收款</span>'
                : `<button class="crm-btn crm-btn-primary crm-btn-sm" style="min-width:80px;" onclick="window._recvSingleReceive(this,'${it.id}','${_esc(c.company_name)}')">未收款</button>`
            }
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

window._recvSingleReceive = async (btn, invoiceId, companyName) => {
    if (!confirm('確定標記此發票為已收款？')) return;
    try {
        await _fetch('/invoices/batch-receive', {
            method: 'PATCH',
            body: JSON.stringify({ invoice_ids: [invoiceId] })
        });
        const row = document.getElementById('recv-row-' + invoiceId);
        if (row) {
            btn.outerHTML = '<span style="min-width:80px;text-align:right;font-size:11px;color:#86efac;">已收款</span>';
        }
    } catch (e) { alert(e.message); }
};

window._recvCopyInfo = (name) => {
    const c = _clients.find(x => x.company_name === name);
    if (!c) return;
    const unpaid = c.items.filter(it => it.payment_status !== '已收款');
    const amount = unpaid.reduce((s, it) => s + it.amount_total, 0);
    const text = [
        '客戶: ' + c.company_name,
        c.tax_id ? '統編: ' + c.tax_id : '',
        c.payment_info ? '匯款資訊: ' + c.payment_info : '',
        '應收金額: $' + amount.toLocaleString('zh-TW'),
        '發票: ' + unpaid.map(it => it.invoice_number || it.title).join(', '),
    ].filter(Boolean).join('\n');
    navigator.clipboard.writeText(text).then(() => alert('已複製客戶資訊')).catch(() => {
        prompt('請手動複製:', text);
    });
};

window._recvReceiveAll = async (name) => {
    const c = _clients.find(x => x.company_name === name);
    if (!c) return;
    const unpaidIds = c.items.filter(it => it.payment_status !== '已收款').map(it => it.id);
    if (!unpaidIds.length) return;
    if (!confirm(`確定將 ${name} 的 ${unpaidIds.length} 張發票全部標記為已收款？`)) return;
    try {
        await _fetch('/invoices/batch-receive', {
            method: 'PATCH',
            body: JSON.stringify({ invoice_ids: unpaidIds })
        });
        for (const id of unpaidIds) {
            const row = document.getElementById('recv-row-' + id);
            if (row) {
                const btn = row.querySelector('.crm-btn-primary');
                if (btn) btn.outerHTML = '<span style="min-width:80px;text-align:right;font-size:11px;color:#86efac;">已收款</span>';
            }
        }
    } catch (e) { alert(e.message); }
};

export function initCrmReceivablesTab() {
    document.getElementById('recv-filter-status').addEventListener('change', loadReceivables);
    setupResizeHandle('recv-resize-handle', 'recv-detail-panel');
    loadReceivables();
}
