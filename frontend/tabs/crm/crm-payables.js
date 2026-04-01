/**
 * crm-payables.js — 應付帳款子視圖（按月份分組）
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle } from './crm-utils.js';

let _payees = [];       // raw API data (grouped by payee)
let _monthGroups = [];  // restructured: grouped by month, then payee
let _selectedKey = null; // "payeeName|month"

/* ── 資料重組：payee-first → month-first ── */
function _buildMonthGroups() {
    const monthMap = {};
    const seen = new Set();
    for (const p of _payees) {
        for (const it of p.items) {
            if (seen.has(it.id)) continue;
            seen.add(it.id);
            // 已付款按實際付款月份歸類，應付款按預計月份
            const isPaid = it.payment_status === '已付款';
            const m = isPaid && it.payment_date
                ? it.payment_date.substring(0, 7)
                : (it.planned_month || '未指定月份');
            if (!monthMap[m]) monthMap[m] = {};
            if (!monthMap[m][p.payee_name]) {
                monthMap[m][p.payee_name] = {
                    payee_name: p.payee_name,
                    payee_id: p.payee_id,
                    bank_name: p.bank_name,
                    bank_account: p.bank_account,
                    month_amount: 0,
                    items: [],
                };
            }
            monthMap[m][p.payee_name].month_amount += it.amount || 0;
            monthMap[m][p.payee_name].items.push(it);
        }
    }

    const sorted = Object.keys(monthMap).sort((a, b) => {
        if (a === '未指定月份') return 1;
        if (b === '未指定月份') return -1;
        return b.localeCompare(a);
    });

    _monthGroups = sorted.map(m => {
        const payees = Object.values(monthMap[m]).sort((a, b) => b.month_amount - a.month_amount);
        return {
            month: m,
            label: m === '未指定月份' ? '未指定月份' : m.replace(/^(\d{4})-(\d{2})$/, '$1年$2月'),
            payees,
            month_total: payees.reduce((s, p) => s + p.month_amount, 0),
        };
    });
}

/* ── 載入 ── */
async function loadPayables() {
    const monthInput = document.getElementById('payable-month');
    const month = monthInput?.value || '';
    const statusSel = document.getElementById('payable-filter-status');
    const status = statusSel?.value || '';

    try {
        const params = new URLSearchParams();
        if (month) params.set('month', month);
        if (status) params.set('status', status);
        const data = await _fetch('/payables/summary?' + params);
        _payees = data.payees || [];
        document.getElementById('payable-total').textContent = '$' + _fmtNum(data.grand_total);
    } catch (_) {
        _payees = [];
    }
    _buildMonthGroups();
    renderList();

    if (_selectedKey) {
        const [name, m] = _selectedKey.split('|');
        const grp = _monthGroups.find(g => g.month === m);
        const p = grp?.payees.find(x => x.payee_name === name);
        if (p) renderDetail(p, m);
    }
}

/* ── 列表渲染（月份標題 + 收款人行）── */
function renderList() {
    const body = document.getElementById('payable-list-body');
    if (!body) return;
    if (_monthGroups.length === 0) {
        body.innerHTML = '<div class="crm-empty">無應付帳款</div>';
        return;
    }
    let html = '';
    for (const g of _monthGroups) {
        html += `<div class="payable-month-header">
            <span>${_esc(g.label)}</span>
            <span>小計 $${_fmtNum(g.month_total)}</span>
        </div>`;
        for (const p of g.payees) {
            const key = p.payee_name + '|' + g.month;
            const allPaid = p.items.every(it => it.payment_status === '已付款');
            const statusCls = allPaid ? 'crm-badge crm-pay-全額到帳' : 'crm-badge crm-pay-未到帳';
            const statusText = allPaid ? '已付款' : '應付款';
            html += `
            <div class="crm-row${key === _selectedKey ? ' selected' : ''}" onclick="window._payableSelect('${_esc(p.payee_name)}','${_esc(g.month)}')">
                <div class="crm-row-name">${_esc(p.payee_name)}</div>
                <div class="crm-row-amount">$${_fmtNum(p.month_amount)}</div>
                <div class="crm-row-client" style="font-size:11px;">${_esc(p.bank_name ? p.bank_name + ' ' + p.bank_account : '')}</div>
                <div class="crm-row-status"><span class="${statusCls}">${statusText}</span></div>
            </div>`;
        }
    }
    body.innerHTML = html;
}

/* ── 詳情面板 ── */
function renderDetail(p, month) {
    const monthLabel = month === '未指定月份' ? '未指定月份' : month.replace(/^(\d{4})-(\d{2})$/, '$1年$2月');
    document.getElementById('payable-detail-title').textContent = p.payee_name + ' — ' + monthLabel;

    const actionsArea = document.getElementById('payable-bar-actions');
    if (actionsArea) {
        const hasUnpaid = p.items.some(it => it.payment_status !== '已付款');
        const key = p.payee_name + '|' + month;
        actionsArea.innerHTML = `
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._payableCopyInfo('${_esc(p.payee_name)}','${_esc(month)}')">複製匯款資訊</button>
            ${hasUnpaid ? `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._payablePayAll('${_esc(p.payee_name)}','${_esc(month)}')">全部付款</button>` : ''}
            <button id="payable-detail-close" class="crm-detail-close" title="關閉" onclick="window._payableClose()">&#x2715;</button>
        `;
    }

    const bankHtml = p.bank_name
        ? `<div class="crm-detail-prop"><div class="crm-prop-label">銀行</div><div class="crm-prop-value">${_esc(p.bank_name)} ${_esc(p.bank_account)}</div></div>`
        : '';

    let itemsHtml = '';
    for (const it of p.items) {
        const isPaid = it.payment_status === '已付款';
        itemsHtml += `
        <div class="payable-item" id="payable-row-${it.id}">
            <span class="payable-item-summary">${_esc(it.summary)}</span>
            <span class="payable-item-cat">${_esc(it.category)}</span>
            <span class="payable-item-amt">$${_fmtNum(it.amount)}</span>
            <span style="min-width:260px;display:flex;align-items:center;justify-content:flex-end;gap:6px;font-size:11px;">
            ${isPaid
                ? `<input type="date" id="payable-di-${it.id}" value="${it.payment_date || ''}" data-orig="${it.payment_date || ''}" style="background:#1a1a1a;border:1px solid #3a3a3a;color:#86efac;font-size:11px;padding:2px 6px;border-radius:4px;width:130px;" onchange="window._payableDateChanged('${it.id}')">
                    <button id="payable-ds-${it.id}" class="crm-btn crm-btn-primary crm-btn-sm" style="min-width:50px;display:none;" onclick="window._payableSaveDate('${it.id}')">儲存</button>
                    <span id="payable-dl-${it.id}" style="color:#86efac;min-width:42px;text-align:center;">已付款</span>
                    <button id="payable-du-${it.id}" class="crm-btn crm-btn-sm" style="font-size:10px;padding:1px 6px;color:#9ca3af;border:1px solid #3a3a3a;background:transparent;" onclick="window._payableUnpay('${it.id}')" title="改回應付款">↩</button>`
                : `<input type="month" id="payable-mi-${it.id}" value="${it.planned_month || ''}" data-orig="${it.planned_month || ''}" style="background:#1a1a1a;border:1px solid #3a3a3a;color:#e0e0e0;font-size:11px;padding:2px 6px;border-radius:4px;width:130px;" onchange="window._payableMonthChanged('${it.id}')" title="調整月份">
                    <button id="payable-ms-${it.id}" class="crm-btn crm-btn-primary crm-btn-sm" style="min-width:50px;display:none;" onclick="window._payableSaveMonth('${it.id}')">儲存</button>
                    <button id="payable-mb-${it.id}" class="crm-btn crm-btn-danger crm-btn-sm" style="min-width:60px;" onclick="window._payableSinglePay(this,'${it.id}')">應付款</button>`
            }
            </span>
        </div>`;
    }

    document.getElementById('payable-detail-content').innerHTML = `
        <div class="crm-detail-prop"><div class="crm-prop-label">收款人</div><div class="crm-prop-value" style="font-weight:700;">${_esc(p.payee_name)}</div></div>
        <div class="crm-detail-prop"><div class="crm-prop-label">身分證</div><div class="crm-prop-value">${_esc(p.payee_id)}</div></div>
        ${bankHtml}
        <div class="crm-detail-prop"><div class="crm-prop-label">應付總額</div><div class="crm-prop-value" style="font-weight:700;color:#fbbf24;">$${_fmtNum(p.month_amount)}</div></div>
        <div style="border-top:1px solid #2e2e2e;margin:10px 0;"></div>
        ${itemsHtml}
    `;
}

/* ── 選取 / 關閉 ── */
function selectPayee(name, month) {
    _selectedKey = name + '|' + month;
    renderList();
    document.getElementById('payable-detail-panel').style.display = 'flex';
    document.getElementById('payable-resize-handle').style.display = '';
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
    if (p) renderDetail(p, month);
}

function closeDetail() {
    _selectedKey = null;
    document.getElementById('payable-detail-panel').style.display = 'none';
    document.getElementById('payable-resize-handle').style.display = 'none';
    renderList();
}

/* ── 全域綁定 ── */
window._payableSelect = selectPayee;
window._payableClose = closeDetail;
window._payableRefresh = () => loadPayables();

window._payableMonthChanged = (paymentId) => {
    const input = document.getElementById('payable-mi-' + paymentId);
    const saveBtn = document.getElementById('payable-ms-' + paymentId);
    const payBtn = document.getElementById('payable-mb-' + paymentId);
    if (!input || !saveBtn) return;
    const changed = input.value !== input.dataset.orig;
    saveBtn.style.display = changed ? '' : 'none';
    if (payBtn) payBtn.style.display = changed ? 'none' : '';
};

window._payableSaveMonth = async (paymentId) => {
    const input = document.getElementById('payable-mi-' + paymentId);
    if (!input) return;
    const newMonth = input.value;
    try {
        await _fetch('/payments/batch-month', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], planned_month: newMonth })
        });
        // 更新 selectedKey 到新月份，讓詳情面板跟著刷新
        if (_selectedKey) {
            const [name] = _selectedKey.split('|');
            _selectedKey = name + '|' + (newMonth || '未指定月份');
        }
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableSinglePay = async (btn, paymentId) => {
    if (!confirm('確定標記此筆為已付款？')) return;
    const today = new Date().toISOString().substring(0, 10);
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], payment_date: today })
        });
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableUnpay = async (paymentId) => {
    if (!confirm('確定將此筆改回應付款？')) return;
    try {
        await _fetch('/payments/batch-unpay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId] })
        });
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableDateChanged = (paymentId) => {
    const input = document.getElementById('payable-di-' + paymentId);
    const saveBtn = document.getElementById('payable-ds-' + paymentId);
    const label = document.getElementById('payable-dl-' + paymentId);
    const unpayBtn = document.getElementById('payable-du-' + paymentId);
    if (!input || !saveBtn) return;
    const changed = input.value !== input.dataset.orig;
    saveBtn.style.display = changed ? '' : 'none';
    if (label) label.style.display = changed ? 'none' : '';
    if (unpayBtn) unpayBtn.style.display = changed ? 'none' : '';
};

window._payableSaveDate = async (paymentId) => {
    const input = document.getElementById('payable-di-' + paymentId);
    if (!input || !input.value) return;
    const newDate = input.value;
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], payment_date: newDate })
        });
        // 已付款按付款日月份歸類，更新 selectedKey
        if (_selectedKey) {
            const [name] = _selectedKey.split('|');
            _selectedKey = name + '|' + newDate.substring(0, 7);
        }
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableCopyInfo = (name, month) => {
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
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

window._payablePayAll = async (name, month) => {
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
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
        await loadPayables();
    } catch (e) { alert(e.message); }
};

export function initCrmPayablesTab() {
    const monthInput = document.getElementById('payable-month');
    monthInput.value = '';
    monthInput.addEventListener('change', loadPayables);
    document.getElementById('payable-filter-status').addEventListener('change', loadPayables);
    setupResizeHandle('payable-resize-handle', 'payable-detail-panel');
    loadPayables();
}
