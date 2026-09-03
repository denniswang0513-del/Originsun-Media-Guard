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
import { state, opt, selectOpts, skeleton, emptyBox, errBox, pill, withBusy, markStale, shouldLoad } from '../ui.js';

const F = (id) => document.getElementById('inv-' + id);
const VOLATILE = ['title', 'invoice_number', 'amount_ex_tax', 'amount_total', 'company_name', 'tax_id', 'item_type', 'notes'];

// 稅率是後端的那一份（/options.invoice.vat_pct）；沒給就讓 invoiceAmounts 用它的預設
const vatPct = () => (opt().invoice || {}).vat_pct;

function formHtml() {
    const inv = opt().invoice || {};
    return `
      <div class="m-h">登記發票</div>
      <form class="m-form m-card w" id="inv-form" autocomplete="off">
        <label class="req">專案</label><select id="inv-project_id"><option value="">選擇專案（從專案分頁點「開發票」會自動帶入）</option></select>
        <div class="row2">
          <div><label>款項</label><select id="inv-payment_type">${selectOpts(inv.payment_types || [], (inv.payment_types || [])[0], null)}</select></div>
          <div><label>款項狀態</label><select id="inv-payment_status"></select></div>
        </div>
        <label>發票種類</label><select id="inv-invoice_kind">${selectOpts(inv.kinds || [], (inv.kinds || [])[0], null)}</select>
        <label class="req">名稱</label><input id="inv-title" placeholder="案件／項目名稱" required>
        <div class="row2">
          <div><label>發票編號</label><input id="inv-invoice_number" autocapitalize="characters"></div>
          <div><label>日期</label><input id="inv-invoice_date" type="date" value="${todayLocal()}"></div>
        </div>
        <div class="row2">
          <div><label>未稅</label><input id="inv-amount_ex_tax" type="number" inputmode="numeric" min="0"></div>
          <div><label>含稅</label><input id="inv-amount_total" type="number" inputmode="numeric" min="0"></div>
        </div>
        <div class="row2">
          <div><label>抬頭</label><input id="inv-company_name"></div>
          <div><label>統編</label><input id="inv-tax_id" maxlength="8" inputmode="numeric" pattern="[0-9]*"></div>
        </div>
        <div class="row2">
          <div><label>類別</label><select id="inv-category">${selectOpts(inv.categories || [], (inv.categories || [])[0], null)}</select></div>
          <div><label>品項</label><input id="inv-item_type" list="inv-item-list"><datalist id="inv-item-list">${(inv.item_types || []).map(t => `<option value="${esc(t)}">`).join('')}</datalist></div>
        </div>
        <label>備註</label><input id="inv-notes">
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
    F('payment_status').innerHTML = selectOpts(list, list[0], null);
}

function wireAmounts() {
    F('payment_type').addEventListener('change', syncStatusOptions);
    syncStatusOptions();
    const ex = F('amount_ex_tax'), tot = F('amount_total');
    ex.addEventListener('input', () => { tot.value = invoiceAmounts(ex.value, 'ex', vatPct()).amount_total ?? ''; });
    tot.addEventListener('input', () => { ex.value = invoiceAmounts(tot.value, 'total', vatPct()).amount_ex_tax ?? ''; });
}

async function loadProjects() {
    const sel = F('project_id');
    try {
        const d = await mfetch('/api/v1/crm/m/projects?limit=100&offset=0');
        const rows = (d.projects || d.items || []).slice()
            .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
        sel.innerHTML = '<option value="">選擇專案（不掛專案：不建議）</option>' + rows.map(p =>
            `<option value="${esc(p.id)}">${esc([p.client_short_name, p.name].filter(Boolean).join('｜'))}</option>`).join('');
    } catch (e) {
        sel.innerHTML = `<option value="">專案清單載入失敗：${esc(e.message)}</option>`;
    }
    applyPreset();
}

function applyPreset() {
    if (!state.invoicePreset) return;
    const sel = F('project_id');
    if (sel && [...sel.options].some(o => o.value === String(state.invoicePreset))) {
        sel.value = String(state.invoicePreset);
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
    if (!body.title) { toast('請填寫名稱', 'err'); F('title').focus(); return; }
    if (body.tax_id && !/^\d{8}$/.test(body.tax_id)) { toast('統編要 8 位數字', 'err'); F('tax_id').focus(); return; }
    // 發票要跟專案綁（owner 2026-09-03）：主路徑是專案抽屜的「開發票」；沒選就先問一句
    if (!body.project_id && !window.confirm('這張發票不掛任何專案？（不建議——之後對帳要自己找）')) { F('project_id').focus(); return; }
    await withBusy(F('submit'), async () => {
        try {
            await mfetch('/api/v1/crm/invoices', { method: 'POST', body });
            toast('發票已登記');
            for (const k of VOLATILE) F(k).value = '';
            F('invoice_date').value = todayLocal();
            window.scrollTo({ top: 0, behavior: 'smooth' });
            markStale('invoice');
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
        shouldLoad('invoice', { first });
        await Promise.all([loadProjects(), loadRecent()]);
        return;
    }
    // 從專案抽屜「開發票」過來：新案子可能還不在清單裡，重載一次再套預設
    if (state.invoicePreset) await loadProjects();
    if (shouldLoad('invoice', { first })) loadRecent();
}
