/**
 * crm-payables.js — 應付帳款子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum } from './crm-utils.js';

let _month = '';

async function loadPayables() {
    const monthInput = document.getElementById('payable-month');
    _month = monthInput?.value || '';
    const param = _month ? `?month=${_month}` : '';
    const container = document.getElementById('payable-list');
    const totalEl = document.getElementById('payable-total');

    try {
        const data = await _fetch('/payables/summary' + param);
        if (totalEl) totalEl.textContent = '$' + _fmtNum(data.grand_total);
        if (!monthInput.value && data.month) monthInput.value = data.month;

        if (!data.payees || data.payees.length === 0) {
            container.innerHTML = '<div class="crm-empty">本月無未付款項目</div>';
            return;
        }

        container.innerHTML = data.payees.map(p => `
            <div class="payable-card">
                <div class="payable-header" onclick="this.parentElement.classList.toggle('open')">
                    <span class="payable-name">${_esc(p.payee_name)}</span>
                    <span class="payable-amount">$${_fmtNum(p.total_amount)}</span>
                    <span class="payable-toggle">▾</span>
                </div>
                <div class="payable-bank">
                    ${p.bank_name ? _esc(p.bank_name) + ' ' + _esc(p.bank_account) : '<span style="color:#4b5563;">無銀行資料</span>'}
                    ${p.payee_id ? ' &nbsp; ' + _esc(p.payee_id) : ''}
                </div>
                <div class="payable-items">
                    ${p.items.map(it => `
                        <div class="payable-item">
                            <label class="payable-check">
                                <input type="checkbox" data-id="${it.id}" class="pay-cb">
                            </label>
                            <span class="payable-item-date">${_esc(it.date)}</span>
                            <span class="payable-item-summary">${_esc(it.summary)}</span>
                            <span class="payable-item-cat">${_esc(it.category)}</span>
                            <span class="payable-item-amt">$${_fmtNum(it.amount)}</span>
                        </div>
                    `).join('')}
                    <div class="payable-actions">
                        <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._payablePayAll(this, '${_esc(p.payee_name)}')">全部付款</button>
                    </div>
                </div>
            </div>
        `).join('');

        // Checkbox pay single item
        container.querySelectorAll('.pay-cb').forEach(cb => {
            cb.addEventListener('change', async e => {
                if (!e.target.checked) return;
                if (!confirm('確定標記此筆為已付款？')) { e.target.checked = false; return; }
                const id = e.target.dataset.id;
                try {
                    await _fetch('/payments/batch-pay', {
                        method: 'PATCH',
                        body: JSON.stringify({ payment_ids: [id], payment_date: new Date().toISOString().substring(0, 10) })
                    });
                    loadPayables();
                } catch (err) { alert(err.message); e.target.checked = false; }
            });
        });
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

window._payablePayAll = async (btn, payeeName) => {
    const card = btn.closest('.payable-card');
    const ids = Array.from(card.querySelectorAll('.pay-cb')).map(cb => cb.dataset.id);
    if (!ids.length) return;
    if (!confirm(`確定將 ${payeeName} 的 ${ids.length} 筆全部標記為已付款？`)) return;
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: ids, payment_date: new Date().toISOString().substring(0, 10) })
        });
        loadPayables();
    } catch (e) { alert(e.message); }
};

export function initCrmPayablesTab() {
    const monthInput = document.getElementById('payable-month');
    const now = new Date();
    monthInput.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    monthInput.addEventListener('change', loadPayables);
    loadPayables();
}
