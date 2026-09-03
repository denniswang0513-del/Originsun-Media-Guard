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
        <label>標題</label><input id="inv-title" placeholder="空白＝用案名或品項（CRM 發票的「名稱」）">
        <label>申請人</label>${segHtml('inv-applicant', inv.applicants || [])}
        <label>類別</label>${segHtml('inv-category', inv.categories || [])}
        <label id="inv-project-label">專案</label>${pickerHtml('inv-project_id')}
        <div class="m-hint" id="inv-client-hint">選了專案會自動帶客戶、抬頭、統編</div>
        <div class="row2">
          <div><label>未稅</label><input id="inv-amount_ex_tax" type="number" inputmode="numeric" min="0"></div>
          <div><label>含稅</label><input id="inv-amount_total" type="number" inputmode="numeric" min="0"></div>
        </div>
        <label>品項</label>${pickerHtml('inv-item_type')}
        <label>客戶（抬頭）</label>${pickerHtml('inv-company_name')}
        <label>統編</label><input id="inv-tax_id" maxlength="8" inputmode="numeric" pattern="[0-9]*">
        <label>電子或紙本</label>${segHtml('inv-invoice_kind', inv.kinds || [])}
        <div id="inv-paper" hidden>
          <label class="req">收件人</label><input id="inv-recipient">
          <label>收件電話</label><input id="inv-recipient_phone" type="tel" inputmode="tel">
          <label class="req">收件地址</label><input id="inv-recipient_address">
        </div>
        <div id="inv-issue-row" hidden>
          <label>開立狀態</label>${segHtml('inv-issue_status', [])}
          <div id="inv-number-row" hidden><label class="req">發票號碼</label><input id="inv-invoice_number" autocapitalize="characters" placeholder="同事開好了把號碼填回來"></div>
          <div class="m-hint" id="inv-void-hint" hidden>儲存後這張會作廢（開立狀態與款項狀態都記作廢）</div>
        </div>
        <details class="m-more"><summary>更多欄位（備註）</summary>
          <label>備註</label><input id="inv-notes">
        </details>
        <div class="m-hint" id="inv-mode-hint">送出＝登記一張待開立、還沒收到錢的請款發票，並產生一段可複製的通知給同事開票</div>
        <button type="submit" class="m-btn-primary" id="inv-submit">送出並產生通知</button>
        <button type="button" class="m-btn wide" id="inv-cancel-edit" hidden>取消修改</button>
      </form>
      ${state.canWrite ? '' : '<div class="m-empty">此帳號沒有登記權限</div>'}
      <div class="m-h">最近登記</div>
      <div id="inv-recent">${skeleton(3)}</div>
      <button type="button" class="m-more" id="inv-more" hidden>載入更多</button>`;
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

// 專案只在類別是「專案」時必填（代開的票可以不掛專案）；哪一種類別由 options.invoice.project_category 說
const needsProject = () => F('category').value === ((opt().invoice || {}).project_category || '');
const syncProjectLabel = () => { document.getElementById('inv-project-label').classList.toggle('req', needsProject()); };

// 紙本發票要寄：收件人／電話／地址（同桌機發票本），電子的不用
const isPaper = () => F('invoice_kind').value === ((opt().invoice || {}).paper_kind || '');
const syncPaper = () => { document.getElementById('inv-paper').hidden = !isPaper(); };

const LAST_APPLICANT = 'm.invoice.applicant';   // 申請人記在這支手機上（純方便，不是資料）
function wireAmounts() {
    mountSeg('inv-invoice_kind', syncPaper); syncPaper();
    mountSeg('inv-category', syncProjectLabel); syncProjectLabel();
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
        title: F('title').value.trim() || projectName() || F('item_type').value.trim() || F('company_name').value.trim() || F('category').value,
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
        recipient: isPaper() ? F('recipient').value.trim() : '',
        recipient_phone: isPaper() ? F('recipient_phone').value.trim() : '',
        recipient_address: isPaper() ? F('recipient_address').value.trim() : '',
        notes: F('notes').value.trim(),
        // 請款發票＝還沒收：狀態字是 options 給的；issue_status 由後端看發票號碼
        payment_status: unpaidStatus(), issue_status: '',
    };
}

// 給同事開票的通知：純文字，長按或按「複製」貼到 LINE／Chat
const projectName = () => ((F('project_id')._rows || []).find(x => x.id === F('project_id').value) || {}).name || '';

function noticeText(body) {
    const p = (F('project_id')._rows || []).find(x => x.id === body.project_id) || {};
    const who = (state.me || {}).username || '';
    const lines = [
        body.issue_status === voided() ? '發票作廢' : '請開發票',
        ...(body.invoice_number ? [`號碼：${body.invoice_number}`] : []),
        `專案：${[p.client_short_name, p.name].filter(Boolean).join('｜') || '—（未掛專案）'}`,
        `類別：${body.category}`,
        `申請人：${body.applicant || '—'}`,
        `抬頭：${body.company_name || '—'}${body.tax_id ? `（統編 ${body.tax_id}）` : ''}`,
        `品項：${body.item_type || '—'}`,
        `種類：${body.invoice_kind}`,
        ...(body.recipient || body.recipient_address
            ? [`收件：${body.recipient || '—'}${body.recipient_phone ? ' ' + body.recipient_phone : ''}`, `地址：${body.recipient_address || '—'}`] : []),
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

// ── 修改還沒開立的票（owner 2026-09-03）：清單按「修改」把資料帶回表單，送出走 PUT ──
// PUT 是整包寫回，手機表單沒有的欄位（號碼、日期、款項狀態、代開費）從原票帶著，不能靠預設值
let _editing = null;
const invo = () => opt().invoice || {};
const unissued = () => invo().unissued_status || '';
const voided = () => invo().void_status || '';
const issuedStatus = () => (invo().issue_statuses || [])[1] || '';   // 有號碼那一種

// 修改模式的開立狀態：三選（未開立／已開立／作廢）；選已開立要填號碼，選作廢會提示款項也一起作廢
function syncIssueRow() {
    const row = document.getElementById('inv-issue-row');
    row.hidden = !_editing;
    if (!_editing) return;
    const v = F('issue_status').value;
    document.getElementById('inv-number-row').hidden = v !== issuedStatus();
    document.getElementById('inv-void-hint').hidden = !(v === voided() && _editing.issue_status !== voided());
}

function startEdit(inv) {
    _editing = inv;
    setSeg('inv-issue_status', invo().issue_statuses || [], inv.issue_status);
    F('invoice_number').value = inv.invoice_number || '';
    setSeg('inv-applicant', invo().applicants || [], inv.applicant);
    setSeg('inv-category', invo().categories || [], inv.category); syncProjectLabel();
    setSeg('inv-invoice_kind', invo().kinds || [], inv.invoice_kind); syncPaper();
    F('project_id')._set?.(inv.project_id || '');
    F('amount_ex_tax').value = inv.amount_ex_tax ?? ''; F('amount_total').value = inv.amount_total ?? '';
    F('item_type')._set?.(inv.item_type || '');
    F('company_name')._set?.(inv.company_name || ''); F('tax_id').value = inv.tax_id || '';
    F('recipient').value = inv.recipient || ''; F('recipient_phone').value = inv.recipient_phone || ''; F('recipient_address').value = inv.recipient_address || '';
    F('title').value = inv.title || ''; F('notes').value = inv.notes || '';
    document.getElementById('inv-mode-hint').textContent = '修改中：' + (inv.title || '') + '（改完送出會覆蓋這張；要作廢或補號碼在「開立狀態」那列）';
    F('submit').textContent = '儲存修改'; F('cancel-edit').hidden = false;
    syncIssueRow();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function stopEdit() {
    _editing = null;
    syncIssueRow();
    document.getElementById('inv-mode-hint').textContent = '送出＝登記一張待開立、還沒收到錢的請款發票，並產生一段可複製的通知給同事開票';
    F('submit').textContent = '送出並產生通知'; F('cancel-edit').hidden = true;
}

async function submit(ev) {
    ev.preventDefault();
    const body = payload();
    if (_editing) {
        // 表單沒有的欄位照原票；有的以表單為準
        const issue = F('issue_status').value;
        if (issue === issuedStatus() && !F('invoice_number').value.trim()) { toast('已開立要填發票號碼', 'err'); F('invoice_number').focus(); return; }
        const wasVoid = _editing.issue_status === voided();
        Object.assign(body, {
            // 號碼：選已開立就用填的；選未開立就清掉（後端看號碼決定開立狀態）；作廢維持原號碼
            invoice_number: issue === issuedStatus() ? F('invoice_number').value.trim() : (issue === voided() ? (_editing.invoice_number || '') : ''),
            invoice_date: _editing.invoice_date || body.invoice_date,   // 完整 ISO 原樣送回，後端會歸一（切前 10 碼會落到 UTC 那天）
            commission: _editing.commission ?? null,
            // 作廢＝開立與款項一起記作廢（同桌機發票本）；從作廢改回來＝款項回到未收；其餘維持原款項狀態
            issue_status: issue === voided() ? voided() : '',
            payment_status: issue === voided() ? voided() : (wasVoid ? body.payment_status : (_editing.payment_status || body.payment_status)),
        });
    }
    if (needsProject() && !body.project_id) { toast('類別是專案就要選專案', 'err'); F('project_id-q').focus(); return; }
    if (!body.title) { toast('請填標題，或填品項／抬頭讓它自動補', 'err'); F('title').focus(); return; }
    if (body.tax_id && !/^\d{8}$/.test(body.tax_id)) { toast('統編要 8 位數字', 'err'); F('tax_id').focus(); return; }
    if (isPaper() && !(body.recipient && body.recipient_address)) { toast('紙本發票要填收件人與地址', 'err'); F(body.recipient ? 'recipient_address' : 'recipient').focus(); return; }
    const editing = _editing;
    let saved = false;
    await withBusy(F('submit'), async () => {
        try {
            if (editing) {
                await mfetch('/api/v1/crm/invoices/' + editing.id, { method: 'PUT', body });
                toast('已修改，通知在上方');
            } else {
                await mfetch('/api/v1/crm/invoices', { method: 'POST', body });
                toast('已登記，通知在上方');
            }
            saved = true;
            showNotice(noticeText(body));
            for (const k of VOLATILE) F(k).value = '';          // 專案／類別／申請人／抬頭留著，同一案常連開幾張
            F('item_type-q').value = '';
            window.scrollTo({ top: 0, behavior: 'smooth' });
            loadRecent();
        } catch (e) { toast(e.message || '建立失敗', 'err'); }
    });
    if (saved && editing) stopEdit();   // withBusy 收尾會把按鈕文字寫回，所以要在它之後
}

const PAGE = 10;
const recent = { offset: 0, rows: [] };   // 後端 offset 分頁（同專案分頁）：載入更多＝再抓下一頁接在後面

function recentCardHtml(inv) {
    return `
      <div class="m-card">
        <div class="t"><div class="name">${esc(inv.title)}</div>${pill(inv.issue_status, inv.issue_status === unissued() ? '' : 'pri')}</div>
        <div class="sub">${esc(inv.payment_type || '')} · ${esc(inv.payment_status || '')}${inv.invoice_number ? ' · ' + esc(inv.invoice_number) : ''}${inv.project_name ? ' · ' + esc(inv.project_name) : ''}</div>
        <div class="row"><span class="sub">${esc(fmtDate(inv.invoice_date))}</span>
          ${'amount_total' in inv ? `<span class="amt">${money(inv.amount_total)}</span>` : ''}</div>
        ${state.canWrite ? `<div class="row"><button type="button" class="m-btn w" data-edit="${esc(inv.id)}">修改</button></div>` : ''}
      </div>`;
}

async function loadRecent(reset = true) {
    const box = document.getElementById('inv-recent'), more = document.getElementById('inv-more');
    if (reset) { recent.offset = 0; recent.rows = []; box.innerHTML = skeleton(3); more.hidden = true; }
    try {
        // 排序與筆數交給後端（order=recent：建立時間新→舊）
        const d = await mfetch(`/api/v1/crm/invoices?limit=${PAGE}&offset=${recent.offset}&order=recent`);
        const seen = new Set(recent.rows.map(x => x.id));
        const rows = (d.invoices || []).filter(x => !seen.has(x.id));   // 後端不認 offset（舊版）時會回同一頁：去重後按鈕自動藏
        recent.rows = recent.rows.concat(rows);
        recent.offset += rows.length;
        box._rows = recent.rows;
        box.innerHTML = recent.rows.length ? recent.rows.map(recentCardHtml).join('') : emptyBox('尚無發票');
        more.hidden = rows.length < PAGE;
    } catch (e) { box.innerHTML = errBox(e); }
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = formHtml();
        wireAmounts();
        document.getElementById('inv-form').addEventListener('submit', submit);
        F('cancel-edit').addEventListener('click', () => { stopEdit(); for (const k of VOLATILE) F(k).value = ''; F('item_type-q').value = ''; });
        mountSeg('inv-issue_status', syncIssueRow);
        document.getElementById('inv-more').addEventListener('click', () => loadRecent(false));
        document.getElementById('inv-recent').addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-edit]'); if (!b) return;
            const inv = (document.getElementById('inv-recent')._rows || []).find(x => x.id === b.dataset.edit);
            if (inv) startEdit(inv);
        });
    }
    // 專案下拉跟最近登記清單一起重抓：專案分頁新建案子會 markStale('invoice')，切過來才看得到它
    if (shouldLoad('invoice', { first })) await Promise.all([loadProjects(), loadRecent()]);
    // 從專案抽屜「開發票」過來（60 秒內沒被標髒）：清單可能是舊的，重載一次再套預設
    else if (state.invoicePreset) await loadProjects();
}
