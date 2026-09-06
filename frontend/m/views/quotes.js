/**
 * 報價分頁：GET /api/v1/crm/quotations 依狀態分組（順序＝options.quote_statuses），每組 10 筆一頁＋載入更多。
 * 狀態轉換由**位置**推：[0]草稿→寄出、[1]已寄出→簽回([2])／拒絕([3])；
 * 改狀態打 POST /api/v1/crm/m/quotations/{id}/status {status, activate}。
 * 按鈕文字是動作（寄出／簽回／拒絕），目標狀態字從 options 取，不寫死。
 * 每張卡都有「PDF」：GET /api/v1/crm/quotations/{id}/pdf（跟桌機同一份 PDF、同一個檔名規則）。
 *
 * 開報價單（owner 2026-09-06，推翻 CRM_MOBILE_PLAN §「報價項目編輯不做」）：底部抽屜開表單，
 * 新增打 POST /crm/projects/{id}/quotations、編輯打 PUT /crm/quotations/{id}——跟桌機同兩支端點。
 * 🔴 這兩支的守衛是 `_check_auth`＝**管理員限定**，所以入口只給管理員（不是 canWrite）。
 * 🔴 金額試算用 js/shared/quote-amounts.js（跟後端 _calc_quotation 同一份規則），手機不自己算稅。
 */
import { quoteTotals, parsePaymentStages, paymentStagesToText } from '/js/shared/quote-amounts.js';
import { mfetch, mdownload, toast, esc, money, fmtDate, todayLocal, quotePdfFilename } from '../shell.js';
import { list, skeleton, emptyBox, errBox, pill, withBusy, markStale, shouldLoad, renderPaged,
    isAdmin, openSheet, closeSheet, pickerHtml, mountPicker, projectLabel } from '../ui.js';

function transitions(status) {
    const v = list('quote_statuses');
    const [draft, sent, signed, rejected] = v;
    if (status === draft && sent) return [{ label: '寄出', to: sent }];
    if (status === sent) return [
        signed ? { label: '簽回', to: signed, ask: true } : null,
        rejected ? { label: '拒絕', to: rejected, danger: true } : null,
    ].filter(Boolean);
    return [];
}

// 動作列跟金額同一列：按鈕不吃 .m-actions 的「各半」flex，照內容寬、文字不換行（「寄出」被擠成兩行過）
const BTN = 'flex:0 0 auto;white-space:nowrap';

function cardHtml(q) {
    const btns = transitions(q.status).map(t =>
        `<button type="button" class="m-btn sm ${t.danger ? 'danger' : 'pri'}" style="${BTN}" data-id="${esc(q.id)}" data-to="${esc(t.to)}"${t.ask ? ' data-ask="1"' : ''}>${esc(t.label)}</button>`).join('')
        + (isAdmin() ? `<button type="button" class="m-btn sm" style="${BTN}" data-edit="${esc(q.id)}">編輯</button>` : '')
        + `<button type="button" class="m-btn sm" style="${BTN}" data-pdf="${esc(q.id)}">PDF</button>`;
    return `
      <div class="m-card">
        <div class="t"><div class="name">${esc(q.project_name || '（未連專案）')}</div>${pill(q.version)}</div>
        <div class="sub">${esc(q.client_short_name || '')}${q.quote_date ? ' · ' + esc(fmtDate(q.quote_date)) : ''}</div>
        <div class="row">${'total' in q ? `<span class="amt">${money(q.total)}</span>` : '<span></span>'}
          <span class="m-actions w" style="margin:0">${btns}</span></div>
      </div>`;
}

async function change(btn, host) {
    const to = btn.dataset.to, id = btn.dataset.id;
    let activate = false;
    if (btn.dataset.ask) activate = window.confirm('順便啟動專案（進入製作）？');
    await withBusy(btn, async () => {
        try {
            await mfetch(`/api/v1/crm/m/quotations/${encodeURIComponent(id)}/status`, { method: 'POST', body: { status: to, activate } });
            toast('報價已改為 ' + to + (activate ? '，專案已啟動' : ''));
            markStale('projects');     // 簽回啟動專案：專案分頁的階段與首頁數字都變了
            await load(host);
        } catch (e) { toast(e.message, 'err'); }
    });
}

async function downloadPdf(btn, rows) {
    const q = rows.find(r => r.id === btn.dataset.pdf);
    if (!q) return;
    await withBusy(btn, async () => {
        try { await mdownload(`/api/v1/crm/quotations/${encodeURIComponent(q.id)}/pdf`, quotePdfFilename(q)); }
        catch (e) { toast(e.message, 'err'); }
    });
}

// ── 開報價單 ─────────────────────────────────────────────────

const F = (k) => document.getElementById('qf-' + k);
const EMPTY_ROW = { group_name: '', description: '', unit: '式', quantity: 1, unit_price: 0 };
let _form = null;      // { id, project_id, project_label, status, items[] }；null＝抽屜沒開

const itemsHtml = () => _form.items.map((it, i) => `
  <div class="m-card" data-i="${i}" style="padding:10px;margin-bottom:8px">
    <input data-k="description" value="${esc(it.description || '')}" placeholder="項目說明（例：剪輯、調光、動態字卡）">
    <div class="row2">
      <div><label>數量</label><input data-k="quantity" type="number" inputmode="numeric" min="0" value="${it.quantity ?? 1}"></div>
      <div><label>單價</label><input data-k="unit_price" type="number" inputmode="numeric" min="0" value="${it.unit_price ?? 0}"></div>
    </div>
    <div class="row2">
      <div><label>單位</label><input data-k="unit" value="${esc(it.unit || '')}" placeholder="式／支／人次"></div>
      <div><label>分組</label><input data-k="group_name" value="${esc(it.group_name || '')}" placeholder="拍攝／後期"></div>
    </div>
    <button type="button" class="m-btn sm danger" data-del="${i}" style="${BTN}">刪除這列</button>
  </div>`).join('');

function drawItems() {
    document.getElementById('qf-items').innerHTML = _form.items.length ? itemsHtml() : '<div class="m-empty">還沒有項目</div>';
    recalc();
}

function recalc() {
    const t = quoteTotals({ items: _form.items, taxRate: F('tax_rate').value });
    const finalPrice = parseInt(F('final_price').value) || 0;
    const promo = finalPrice && finalPrice < t.total ? t.total - finalPrice : 0;
    document.getElementById('qf-calc').innerHTML = `
      <div class="row"><span>合計</span><span class="amt">${money(t.subtotal)}</span></div>
      <div class="row"><span>營業稅 ${parseInt(F('tax_rate').value) || 0}%</span><span class="amt">${money(t.tax)}</span></div>
      ${promo ? `<div class="row"><span>專案優惠</span><span class="amt">−${money(promo)}</span></div>` : ''}
      <div class="row"><span><b>報價總額</b>（含稅）</span><span class="amt"><b>${money(finalPrice || t.total)}</b></span></div>`;
}

function formHtml(projects, templates, q) {
    const editing = !!q;
    return `
      <div class="m-h">${editing ? `編輯報價 v${q.version}` : '開報價單'}</div>
      <form class="m-form" id="qf-form" autocomplete="off">
        ${editing
            ? `<label>專案</label><div class="m-card" style="padding:10px">${esc(_form.project_label || '（未連專案）')}</div>`
            : `<label class="req">專案</label>${pickerHtml('qf-project_id')}`}
        ${editing ? '' : `<label>套用範本</label>${pickerHtml('qf-template')}
        <div class="m-hint">選了範本會帶入項目、稅率、備註、付款方式，帶進來還可以改</div>`}
        <label>規格</label><input id="qf-spec" placeholder="用、分隔，例：形象短片 90 秒 1 支、含中文字幕">
        <div class="m-h">項目</div>
        <div id="qf-items"></div>
        <button type="button" class="m-more" id="qf-add">＋ 加一列</button>
        <div class="row2">
          <div><label>稅率 %</label><input id="qf-tax_rate" type="number" inputmode="numeric" min="0" value="5"></div>
          <div><label>最終報價（含稅）</label><input id="qf-final_price" type="number" inputmode="numeric" min="0" placeholder="空白＝照算出來的"></div>
        </div>
        <div class="m-hint">最終報價填了就是它，差額會印成「專案優惠」</div>
        <label>付款方式</label><input id="qf-stages" placeholder="簽約 30%, 拍攝 40%, 交片 30%">
        <label>備註</label><textarea id="qf-terms" rows="3" placeholder="一行一條"></textarea>
        <div class="m-card" id="qf-calc" style="margin-top:12px"></div>
        <div id="qf-err" class="m-err" hidden></div>
        <button type="submit" class="m-btn-primary" id="qf-submit">${editing ? '儲存修改' : '建立報價'}</button>
        <button type="button" class="m-btn wide" id="qf-cancel">取消</button>
      </form>`;
}

async function openForm(host, id) {
    let projects = [], templates = [], q = null;
    try {
        if (id) {
            q = await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(id)}`);
        } else {
            const [pj, tp] = await Promise.all([
                mfetch('/api/v1/crm/m/projects?limit=100&offset=0'),
                mfetch('/api/v1/crm/quotation-templates').catch(() => ({ templates: [] })),
            ]);
            projects = pj.projects || [];
            templates = tp.templates || [];
        }
    } catch (e) { toast(e.message, 'err'); return; }

    _form = {
        id: id || null,
        project_id: q ? q.project_id : '',
        project_label: q ? [q.client_short_name, q.project_name].filter(Boolean).join('｜') : '',
        status: q ? q.status : (list('quote_statuses')[0] || ''),   // 字彙只從 options 來
        items: q && q.items && q.items.length ? q.items.map(it => ({ ...it })) : [{ ...EMPTY_ROW }],
    };

    openSheet(formHtml(projects, templates, q));

    if (!id) {
        mountPicker('qf-project_id', {
            items: projects.map(p => ({ value: p.id, label: projectLabel(p) })),
            placeholder: '打字找案名或客戶',
            onPick: (v) => { _form.project_id = v; },
        });
        mountPicker('qf-template', {
            items: templates.map(t => ({ value: t.id, label: t.name })),
            placeholder: '（不套用）',
            onPick: (v) => applyTemplate(templates.find(t => t.id === v)),
        });
    }
    if (q) {
        F('spec').value = q.spec || '';
        F('tax_rate').value = q.tax_rate ?? 5;
        F('final_price').value = q.final_price ?? '';
        F('stages').value = paymentStagesToText(q.payment_stages);
        F('terms').value = q.terms || '';
    }
    drawItems();

    document.getElementById('qf-add').onclick = () => { _form.items.push({ ...EMPTY_ROW }); drawItems(); };
    document.getElementById('qf-cancel').onclick = () => { _form = null; closeSheet(); };
    document.getElementById('qf-items').oninput = (ev) => {
        const inp = ev.target.closest('input[data-k]');
        if (!inp) return;
        const i = Number(inp.closest('[data-i]').dataset.i), k = inp.dataset.k;
        _form.items[i][k] = (k === 'quantity' || k === 'unit_price') ? (parseInt(inp.value) || 0) : inp.value;
        recalc();                       // 只重算，不重畫（重畫會把游標踢掉）
    };
    document.getElementById('qf-items').onclick = (ev) => {
        const b = ev.target.closest('button[data-del]');
        if (!b) return;
        _form.items.splice(Number(b.dataset.del), 1);
        drawItems();
    };
    F('tax_rate').oninput = recalc;
    F('final_price').oninput = recalc;
    document.getElementById('qf-form').onsubmit = (ev) => { ev.preventDefault(); save(host); };
}

function applyTemplate(t) {
    if (!t) return;
    if (Array.isArray(t.items) && t.items.length) _form.items = t.items.map(it => ({ ...EMPTY_ROW, ...it }));
    if (t.tax_rate != null) F('tax_rate').value = t.tax_rate;
    if (t.terms) F('terms').value = t.terms;
    F('stages').value = paymentStagesToText(t.payment_stages);
    drawItems();
}

async function save(host) {
    const err = document.getElementById('qf-err');
    const items = _form.items.filter(it => (it.description || '').trim());
    if (!_form.id && !_form.project_id) { err.hidden = false; err.textContent = '請先選專案'; return; }
    if (!items.length) { err.hidden = false; err.textContent = '至少要有一個有說明的項目'; return; }
    err.hidden = true;

    const body = {
        ...(_form.status ? { status: _form.status } : {}),   // 拿不到字彙就不送，讓後端給它的預設
        tax_rate: parseInt(F('tax_rate').value) || 0,
        final_price: parseInt(F('final_price').value) || null,
        spec: F('spec').value.trim(),
        terms: F('terms').value.trim(),
        payment_stages: parsePaymentStages(F('stages').value),
        items: items.map((it, i) => ({
            group_name: (it.group_name || '').trim(), description: it.description.trim(),
            unit: (it.unit || '式').trim(), quantity: parseInt(it.quantity) || 0,
            unit_price: parseInt(it.unit_price) || 0, internal_cost: it.internal_cost || 0,
            note: it.note || '', sort_order: i,
        })),
    };
    if (!_form.id) body.quote_date = todayLocal();

    const btn = document.getElementById('qf-submit');
    await withBusy(btn, async () => {
        try {
            if (_form.id) await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(_form.id)}`, { method: 'PUT', body });
            else await mfetch(`/api/v1/crm/projects/${encodeURIComponent(_form.project_id)}/quotations`, { method: 'POST', body });
            toast(_form.id ? '報價已更新' : '報價已建立');
            _form = null;
            closeSheet();
            await load(host);
        } catch (e) { err.hidden = false; err.textContent = e.message; }
    });
}

// ── 清單 ────────────────────────────────────────────────────

let _rows = [];      // 目前畫面上的報價（PDF 鈕要拿 quote_date／專案／客戶組檔名）

async function load(host) {
    const box = host.querySelector('#qt-list');
    box.innerHTML = skeleton(3);
    try {
        const d = await mfetch('/api/v1/crm/quotations');
        const rows = d.quotations || [];
        _rows = rows;
        if (!rows.length) { box.innerHTML = emptyBox('沒有報價'); return; }
        const order = list('quote_statuses');
        const groups = new Map(order.map(s => [s, []]));
        for (const q of rows) {
            if (!groups.has(q.status)) groups.set(q.status, []);
            groups.get(q.status).push(q);
        }
        const shown = [...groups].filter(([, arr]) => arr.length);
        box.innerHTML = shown.map(([s, arr], i) => `<div class="m-h">${esc(s)}（${arr.length}）</div><div data-group="${i}"></div>`).join('');
        shown.forEach(([, arr], i) => renderPaged(box.querySelector(`[data-group="${i}"]`),
            arr.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))), cardHtml));
    } catch (e) { box.innerHTML = errBox(e); }
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `${isAdmin() ? '<button type="button" class="m-btn-primary" id="qt-new">開報價單</button>' : ''}<div id="qt-list"></div>`;
        if (isAdmin()) document.getElementById('qt-new').onclick = () => openForm(host, null);
        host.addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-to]');
            if (b) return change(b, host);
            const e = ev.target.closest('button[data-edit]');
            if (e) return openForm(host, e.dataset.edit);
            const p = ev.target.closest('button[data-pdf]');
            if (p) downloadPdf(p, _rows);
        });
    }
    if (shouldLoad('quotes', { first })) await load(host);
}
