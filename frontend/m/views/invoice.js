/**
 * 發票分頁：登記表單（欄位名＝core/schemas.InvoicePayload）＋最近 10 張。
 * 🔴 款項狀態的字不在這裡：選項由 /options.invoice.statuses_by_type 給（後端規則算的，
 *    第一個＝後端預設「登記＝錢收到了」，第二個＝未收／未付）；issue_status 送空字串，
 *    後端依有沒有發票號碼決定。這頁若自己決定狀態，桌機改名後手機送出的票就落在沒人
 *    認得的狀態（計畫 §7）。
 * 字彙（款項／種類／類別／品項）全部來自 /options.invoice。
 */
import { invoiceAmounts } from '/js/shared/invoice-amounts.js';
import { mfetch, toast, esc, todayLocal, money, fmtDate } from '../shell.js';
import { state, opt, selectOpts, skeleton, emptyBox, errBox, pill, withBusy, shouldLoad, pickerHtml, mountPicker, segHtml, mountSeg, setSeg } from '../ui.js';

const F = (id) => document.getElementById('inv-' + id);
const VOLATILE = ['title', 'invoice_number', 'amount_ex_tax', 'amount_total', 'company_name', 'tax_id', 'item_type', 'notes'];

// 稅率是後端的那一份（/options.invoice.vat_pct）；沒給就讓 invoiceAmounts 用它的預設
const vatPct = () => (opt().invoice || {}).vat_pct;

function formHtml() {
    const inv = opt().invoice || {};
    return `
      <div class="m-h">登記發票</div>
      <form class="m-form m-card w" id="inv-form" autocomplete="off">
        <label class="req">專案</label>${pickerHtml('inv-project_id')}
        <div class="m-hint" id="inv-client-hint">選了專案會自動帶客戶、抬頭、統編</div>
        <div class="row2">
          <div><label>抬頭</label><input id="inv-company_name"></div>
          <div><label>統編</label><input id="inv-tax_id" maxlength="8" inputmode="numeric" pattern="[0-9]*"></div>
        </div>
        <label>品項</label>${pickerHtml('inv-item_type')}
        <label>發票種類</label>${segHtml('inv-invoice_kind', inv.kinds || [])}
        <label>款項</label>${segHtml('inv-payment_type', inv.payment_types || [])}
        <label>款項狀態</label>${segHtml('inv-payment_status', [])}
        <div class="row2">
          <div><label>未稅</label><input id="inv-amount_ex_tax" type="number" inputmode="numeric" min="0"></div>
          <div><label>含稅</label><input id="inv-amount_total" type="number" inputmode="numeric" min="0"></div>
        </div>
        <details class="m-more"><summary>更多欄位（名稱、發票編號、日期、類別、備註）</summary>
          <label>名稱</label><input id="inv-title" placeholder="空白＝用案名">
          <div class="row2">
            <div><label>發票編號</label><input id="inv-invoice_number" autocapitalize="characters"></div>
            <div><label>日期</label><input id="inv-invoice_date" type="date" value="${todayLocal()}"></div>
          </div>
          <label>類別</label><select id="inv-category">${selectOpts(inv.categories || [], (inv.categories || [])[0], null)}</select>
          <label>備註</label><input id="inv-notes">
        </details>
        <button type="submit" class="m-btn-primary" id="inv-submit">送出</button>
      </form>
      ${state.canWrite ? '' : '<div class="m-empty">此帳號沒有登記權限</div>'}
      <div class="m-h">最近 10 張</div>
      <div id="inv-recent">${skeleton(3)}</div>`;
}

// 款項狀態跟著方向走：收款→[已收款, 未收款]、付款→[已撥款, 未付款]（字都來自 options）
function syncStatusOptions() {
    const by = (opt().invoice || {}).statuses_by_type || {};
    const list = by[F('payment_type').value] || [];
    setSeg('inv-payment_status', list, list[0]);
}

// 選了專案：客戶、抬頭、統編從客戶檔帶進來，名稱空著就用案名（都可以再改）
function applyProjectDefaults(pid) {
    const p = (F('project_id')._rows || []).find(x => x.id === pid);
    const hint = document.getElementById('inv-client-hint');
    if (!p) { hint.textContent = '選了專案會自動帶客戶、抬頭、統編'; return; }
    const c = (opt().clients || []).find(x => x.id === p.client_id) || {};
    hint.textContent = '客戶：' + (c.short_name || p.client_short_name || '—');
    if (c.full_name) F('company_name').value = c.full_name;
    if (c.tax_id) F('tax_id').value = c.tax_id;
    if (!F('title').value.trim()) F('title').value = p.name || '';
}

function wireAmounts() {
    mountSeg('inv-invoice_kind');
    mountSeg('inv-payment_type', syncStatusOptions);
    mountSeg('inv-payment_status');
    syncStatusOptions();
    mountPicker('inv-item_type', { items: ((opt().invoice || {}).item_types || []).map(t => ({ value: t, label: t })),
                                   placeholder: '打字找或自己打', free: true });
    const ex = F('amount_ex_tax'), tot = F('amount_total');
    ex.addEventListener('input', () => { tot.value = invoiceAmounts(ex.value, 'ex', vatPct()).amount_total ?? ''; });
    tot.addEventListener('input', () => { ex.value = invoiceAmounts(tot.value, 'total', vatPct()).amount_ex_tax ?? ''; });
}

async function loadProjects() {
    let items = [], placeholder = '打字找案名或客戶（不掛專案：不建議）';
    try {
        const d = await mfetch('/api/v1/crm/m/projects?limit=100&offset=0');
        F('project_id')._rows = d.projects || [];
        items = (d.projects || []).map(p => ({ value: p.id, label: [p.client_short_name, p.name].filter(Boolean).join('｜') }));
    } catch (e) {
        placeholder = '專案清單載入失敗：' + e.message;
    }
    mountPicker('inv-project_id', { items, placeholder, value: F('project_id').value, onPick: applyProjectDefaults });
    applyPreset();
}

function applyPreset() {
    if (!state.invoicePreset) return;
    const hidden = F('project_id');
    if (hidden && hidden._set && (hidden._items || []).some(i => String(i.value) === String(state.invoicePreset))) {
        hidden._set(String(state.invoicePreset));
        state.invoicePreset = null;
    }
}

function payload() {
    const ex = parseInt(F('amount_ex_tax').value) || null;
    const total = parseInt(F('amount_total').value) || null;
    return {
        payment_type: F('payment_type').value,
        invoice_kind: F('invoice_kind').value,
        title: F('title').value.trim(),
        invoice_number: F('invoice_number').value.trim(),
        invoice_date: F('invoice_date').value || null,
        amount_ex_tax: ex, amount_total: total,
        tax_amount: (ex && total) ? total - ex : null,
        company_name: F('company_name').value.trim(),
        tax_id: F('tax_id').value.trim(),
        project_id: F('project_id').value || null,
        item_type: F('item_type').value.trim(),
        category: F('category').value,
        notes: F('notes').value.trim(),
        // payment_status 是 options 給的字（空字串＝請後端依方向決定）；issue_status 由後端看發票號碼
        payment_status: F('payment_status').value || '', issue_status: '',
    };
}

async function submit(ev) {
    ev.preventDefault();
    const body = payload();
    if (!body.title) { toast('請選專案，或在「更多欄位」填名稱', 'err'); F('project_id-q').focus(); return; }
    if (body.tax_id && !/^\d{8}$/.test(body.tax_id)) { toast('統編要 8 位數字', 'err'); F('tax_id').focus(); return; }
    // 發票要跟專案綁（owner 2026-09-03）：主路徑是專案抽屜的「開發票」；沒選就先問一句
    if (!body.project_id && !window.confirm('這張發票不掛任何專案？（不建議——之後對帳要自己找）')) { F('project_id-q').focus(); return; }
    await withBusy(F('submit'), async () => {
        try {
            await mfetch('/api/v1/crm/invoices', { method: 'POST', body });
            toast('發票已登記');
            for (const k of VOLATILE) F(k).value = '';
            F('item_type-q').value = '';
            F('invoice_date').value = todayLocal();
            window.scrollTo({ top: 0, behavior: 'smooth' });
            loadRecent();
        } catch (e) { toast(e.message || '建立失敗', 'err'); }
    });
}

async function loadRecent() {
    const box = document.getElementById('inv-recent');
    try {
        // 排序與筆數交給後端（order=recent：建立時間新→舊）
        const d = await mfetch('/api/v1/crm/invoices?limit=10&order=recent');
        const rows = d.invoices || [];
        if (!rows.length) { box.innerHTML = emptyBox('尚無發票'); return; }
        box.innerHTML = rows.map(inv => `
          <div class="m-card">
            <div class="t"><div class="name">${esc(inv.title)}</div>${pill(inv.payment_status, 'pri')}</div>
            <div class="sub">${esc(inv.payment_type || '')}${inv.invoice_number ? ' · ' + esc(inv.invoice_number) : ''}${inv.project_name ? ' · ' + esc(inv.project_name) : ''}</div>
            <div class="row"><span class="sub">${esc(fmtDate(inv.invoice_date))}</span>
              ${'amount_total' in inv ? `<span class="amt">${money(inv.amount_total)}</span>` : ''}</div>
          </div>`).join('');
    } catch (e) { box.innerHTML = errBox(e); }
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = formHtml();
        wireAmounts();
        document.getElementById('inv-form').addEventListener('submit', submit);
    }
    // 專案下拉跟最近 10 張一起重抓：專案分頁新建案子會 markStale('invoice')，切過來才看得到它
    if (shouldLoad('invoice', { first })) await Promise.all([loadProjects(), loadRecent()]);
    // 從專案抽屜「開發票」過來（60 秒內沒被標髒）：清單可能是舊的，重載一次再套預設
    else if (state.invoicePreset) await loadProjects();
}
