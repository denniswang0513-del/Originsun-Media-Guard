/**
 * 發票分頁：「請同事開票」——手機登記一張待開立的請款發票，送出後給一段可複製的通知（owner 2026-09-03）。
 * 順序：專案（必選，自動帶客戶／抬頭／統編／名稱）→類別→申請人→抬頭→品項→電子或紙本→金額；
 * 其餘自動：方向＝收款、款項狀態＝未收、開立狀態由後端看有沒有號碼（沒有＝未開立）。
 * 🔴 狀態與類別的字不在這裡：全部來自 /options.invoice（後端規則算的）；這頁若自己決定狀態，
 *    桌機改名後手機送出的票就落在沒人認得的狀態（計畫 §7）。
 */
import { invoiceAmounts } from '/js/shared/invoice-amounts.js';
import { mfetch, toast, esc, todayLocal, money, fmtDate } from '../shell.js';
import { state, opt, skeleton, emptyBox, errBox, pill, withBusy, shouldLoad, pickerHtml, mountPicker, segHtml, mountSeg, setSeg, copyText } from '../ui.js';

const F = (id) => document.getElementById('inv-' + id);
const VOLATILE = ['title', 'amount_ex_tax', 'amount_total', 'item_type', 'notes'];

// 稅率是後端的那一份（/options.invoice.vat_pct）；沒給就讓 invoiceAmounts 用它的預設
const vatPct = () => (opt().invoice || {}).vat_pct;

function formHtml() {
    const inv = opt().invoice || {};
    return `
      <div class="m-h">請開發票</div>
      <div id="inv-notice" hidden></div>
      <form class="m-form m-card w" id="inv-form" autocomplete="off">
        <label class="req">專案</label>${pickerHtml('inv-project_id')}
        <div class="m-hint" id="inv-client-hint">選了專案會自動帶客戶、抬頭、統編</div>
        <label>類別</label>${segHtml('inv-category', inv.categories || [])}
        <label>申請人</label>${segHtml('inv-applicant', inv.applicants || [])}
        <label>抬頭</label>${pickerHtml('inv-company_name')}
        <label>統編</label><input id="inv-tax_id" maxlength="8" inputmode="numeric" pattern="[0-9]*">
        <label>品項</label>${pickerHtml('inv-item_type')}
        <label>電子或紙本</label>${segHtml('inv-invoice_kind', inv.kinds || [])}
        <div class="row2">
          <div><label>未稅</label><input id="inv-amount_ex_tax" type="number" inputmode="numeric" min="0"></div>
          <div><label>含稅</label><input id="inv-amount_total" type="number" inputmode="numeric" min="0"></div>
        </div>
        <details class="m-more"><summary>更多欄位（名稱、備註）</summary>
          <label>名稱</label><input id="inv-title" placeholder="空白＝用案名">
          <label>備註</label><input id="inv-notes">
        </details>
        <div class="m-hint">送出＝登記一張待開立、還沒收到錢的請款發票，並產生一段可複製的通知給同事開票</div>
        <button type="submit" class="m-btn-primary" id="inv-submit">送出並產生通知</button>
      </form>
      ${state.canWrite ? '' : '<div class="m-empty">此帳號沒有登記權限</div>'}
      <div class="m-h">最近 10 張</div>
      <div id="inv-recent">${skeleton(3)}</div>`;
}

// 手機登記的一定是「請款發票、還沒開」（owner 2026-09-03）：方向＝options 第一個（收款），
// 款項狀態＝那個方向的第二個（未收），開立狀態交給後端（沒號碼＝未開立）。字都來自 options。
const receivableType = () => ((opt().invoice || {}).payment_types || [])[0] || '';
const unpaidStatus = () => (((opt().invoice || {}).statuses_by_type || {})[receivableType()] || [])[1] || '';

// 抬頭：客戶檔的全稱可打字找，選到就帶統編；也可以自己打（free）
const clientItems = () => (opt().clients || []).filter(c => c.full_name).map(c => ({ value: c.full_name, label: c.full_name }));
function applyCompany(name) {
    const c = (opt().clients || []).find(x => x.full_name === name);
    if (c && c.tax_id) F('tax_id').value = c.tax_id;
}

// 選了專案：客戶、抬頭、統編從客戶檔帶進來，名稱空著就用案名（都可以再改）
function applyProjectDefaults(pid) {
    const p = (F('project_id')._rows || []).find(x => x.id === pid);
    const hint = document.getElementById('inv-client-hint');
    if (!p) { hint.textContent = '選了專案會自動帶客戶、抬頭、統編'; return; }
    const c = (opt().clients || []).find(x => x.id === p.client_id) || {};
    hint.textContent = '客戶：' + (c.short_name || p.client_short_name || '—');
    if (c.full_name && F('company_name')._set) F('company_name')._set(c.full_name);
    if (c.tax_id) F('tax_id').value = c.tax_id;
    if (!F('title').value.trim()) F('title').value = p.name || '';
}

const LAST_APPLICANT = 'm.invoice.applicant';   // 申請人記在這支手機上（純方便，不是資料）
function wireAmounts() {
    mountSeg('inv-invoice_kind');
    mountSeg('inv-category');
    const applicants = (opt().invoice || {}).applicants || [];
    let last = '';
    try { last = localStorage.getItem(LAST_APPLICANT) || ''; } catch (_) { /* 私密模式 */ }
    setSeg('inv-applicant', applicants, applicants.includes(last) ? last : applicants[0]);
    mountSeg('inv-applicant', (v) => { try { localStorage.setItem(LAST_APPLICANT, v); } catch (_) { /* 同上 */ } });
    mountPicker('inv-company_name', { items: clientItems(), placeholder: '打字找抬頭或自己打', free: true, onPick: applyCompany });
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
        payment_type: receivableType(),
        invoice_kind: F('invoice_kind').value,
        title: F('title').value.trim(),
        invoice_number: '',                       // 還沒開：沒號碼，後端會給「未開立」
        invoice_date: todayLocal(),
        amount_ex_tax: ex, amount_total: total,
        tax_amount: (ex && total) ? total - ex : null,
        company_name: F('company_name').value.trim(),
        tax_id: F('tax_id').value.trim(),
        project_id: F('project_id').value || null,
        item_type: F('item_type').value.trim(),
        category: F('category').value,
        applicant: F('applicant').value,
        notes: F('notes').value.trim(),
        // 請款發票＝還沒收：狀態字是 options 給的；issue_status 由後端看發票號碼
        payment_status: unpaidStatus(), issue_status: '',
    };
}

// 給同事開票的通知：純文字，長按或按「複製」貼到 LINE／Chat
function noticeText(body) {
    const p = (F('project_id')._rows || []).find(x => x.id === body.project_id) || {};
    const who = (state.me || {}).username || '';
    const lines = [
        '請開發票',
        `專案：${[p.client_short_name, p.name].filter(Boolean).join('｜') || body.title}`,
        `類別：${body.category}`,
        `申請人：${body.applicant || '—'}`,
        `抬頭：${body.company_name || '—'}${body.tax_id ? `（統編 ${body.tax_id}）` : ''}`,
        `品項：${body.item_type || '—'}`,
        `種類：${body.invoice_kind}`,
        `金額：未稅 ${money(body.amount_ex_tax)}／含稅 ${money(body.amount_total)}`,
        `（${todayLocal()} ${who} 從手機登記，發票本待開立）`,
    ];
    return lines.join('\n');
}

function showNotice(text) {
    const box = document.getElementById('inv-notice');
    box.hidden = false;
    box.innerHTML = `<div class="m-notice"><pre id="inv-notice-text">${esc(text)}</pre>
      <button type="button" class="m-btn pri wide" id="inv-copy">複製通知</button></div>`;
    box.querySelector('#inv-copy').addEventListener('click', async () => {
        toast((await copyText(text)) ? '已複製，貼到 LINE 給同事' : '複製失敗，請長按文字複製', 'ok');
    });
}

async function submit(ev) {
    ev.preventDefault();
    const body = payload();
    if (!body.project_id) { toast('請先選專案', 'err'); F('project_id-q').focus(); return; }
    if (!body.title) { toast('請選專案，或在「更多欄位」填名稱', 'err'); F('project_id-q').focus(); return; }
    if (body.tax_id && !/^\d{8}$/.test(body.tax_id)) { toast('統編要 8 位數字', 'err'); F('tax_id').focus(); return; }
    await withBusy(F('submit'), async () => {
        try {
            await mfetch('/api/v1/crm/invoices', { method: 'POST', body });
            toast('已登記，通知在上方');
            showNotice(noticeText(body));
            for (const k of VOLATILE) F(k).value = '';          // 專案／類別／申請人／抬頭留著，同一案常連開幾張
            F('item_type-q').value = '';
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
