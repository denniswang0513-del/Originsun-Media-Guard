/**
 * 付款分頁：GET /api/v1/crm/payments，前端分「未付」（payment_status ≠ 已付）與「最近已付 10 筆」。
 * 已付的字＝options.payment_statuses 最後一項（不寫死）。
 * 標已付 → PATCH /payments/batch-pay {payment_ids:[id], payment_date: 今天(本地)}；
 * 取消   → PATCH /payments/batch-unpay {payment_ids:[id]}。月結鎖帳 409 的 detail 原樣進 toast。
 */
import { mfetch, toast, esc, money, fmtDate, todayLocal } from '../shell.js';
import { paidStatus, skeleton, emptyBox, errBox, pill, withBusy, markStale, shouldLoad } from '../ui.js';

function cardHtml(p, paid) {
    const btn = paid
        ? `<button type="button" class="m-btn sm danger w" data-unpay="${esc(p.id)}">取消</button>`
        : `<button type="button" class="m-btn sm pri w" data-pay="${esc(p.id)}">標已付</button>`;
    return `
      <div class="m-card">
        <div class="t"><div class="name">${esc(p.summary || p.invoice_title || '（無摘要）')}</div>${pill(p.payment_status, paid ? 'ok' : 'warn')}</div>
        <div class="sub">${esc(p.payee_name || '')}${p.project_name ? ' · ' + esc(p.project_name) : ''}${p.category ? ' · ' + esc(p.category) : ''}</div>
        <div class="row">
          <span>${'amount' in p ? `<span class="amt">${money(p.amount)}</span>` : ''}
            <span class="sub" style="color:var(--sub);font-size:12px"> ${paid ? '付款 ' + esc(fmtDate(p.payment_date)) : (p.planned_month ? '預計 ' + esc(p.planned_month) : '')}</span></span>
          ${btn}
        </div>
      </div>`;
}

async function load(host) {
    const box = host.querySelector('#py-list');
    box.innerHTML = skeleton(3);
    try {
        const d = await mfetch('/api/v1/crm/payments');
        const rows = (d.payments || []).filter(p => !p.is_advance);
        const PAID = paidStatus();
        const unpaid = rows.filter(p => p.payment_status !== PAID)
            .sort((a, b) => String(a.planned_month || '9999').localeCompare(String(b.planned_month || '9999')));
        const paid = rows.filter(p => p.payment_status === PAID)
            .sort((a, b) => String(b.payment_date || '').localeCompare(String(a.payment_date || ''))).slice(0, 10);
        box.innerHTML = `
          <div class="m-h">待付（${unpaid.length}）</div>
          ${unpaid.length ? unpaid.map(p => cardHtml(p, false)).join('') : emptyBox('沒有待付的請款')}
          <details class="m-fold"><summary>最近已付 ${paid.length} 筆</summary>
            ${paid.length ? paid.map(p => cardHtml(p, true)).join('') : emptyBox('沒有已付紀錄')}
          </details>`;
    } catch (e) { box.innerHTML = errBox(e); }
}

async function act(btn, host) {
    const payId = btn.dataset.pay, unpayId = btn.dataset.unpay;
    await withBusy(btn, async () => {
        try {
            if (payId) {
                await mfetch('/api/v1/crm/payments/batch-pay', { method: 'PATCH', body: { payment_ids: [payId], payment_date: todayLocal() } });
                toast('已標記付款');
            } else {
                await mfetch('/api/v1/crm/payments/batch-unpay', { method: 'PATCH', body: { payment_ids: [unpayId] } });
                toast('已取消付款');
            }
            markStale('payments');
            await load(host);
        } catch (e) { toast(e.message, 'err'); }
    });
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = '<div id="py-list"></div>';
        host.addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-pay],button[data-unpay]');
            if (b) act(b, host);
        });
    }
    if (shouldLoad('payments', { first })) await load(host);
}
