/**
 * frontend/m/ui.js — 手機 CRM 頁內共用：狀態、底部抽屜、骨架／空／錯狀態、字彙工具。
 * 字彙一律從 state.options 取（GET /api/v1/crm/m/options）；這裡不寫死任何狀態字。
 */
import { esc } from './shell.js';

export const state = {
    me: null,
    options: null,        // /options 整包
    canWrite: false,
    invoicePreset: null,  // 從專案抽屜「開發票」帶過來的 project_id
    expensePreset: null,  // 從專案抽屜「記雜支」帶過來的 project_id
};

export const TABS = ['invoice', 'petty', 'projects', 'quotes', 'payments'];
export const DEFAULT_TAB = 'invoice';
/** 有畫面但不在分頁列的路由（從別的畫面進、上一頁回去）：記雜支。 */
export const ROUTES = [...TABS, 'expense'];

export function currentTab() {
    const h = (location.hash || '').replace(/^#/, '').split('?')[0];
    return ROUTES.includes(h) ? h : DEFAULT_TAB;
}

/** 換分頁＝改 hash（crm.js 聽 hashchange 畫）；已經在那一頁就什麼都不做。 */
export function switchTab(tab) {
    location.hash = tab;
}

// ── 重抓守門：切回來的分頁 60 秒內沒人改過就不再打 API ──
const _loadedAt = {};
const _stale = new Set();
/** 寫入路徑呼叫：這幾個分頁的資料變了，下次切到要重抓。 */
export function markStale(...tabs) { tabs.forEach(t => _stale.add(t)); }
/** render 呼叫：第一次、被標髒、或上次抓到現在超過 maxAgeMs → true（並記下這次抓的時間）。 */
export function shouldLoad(tab, { first = false, maxAgeMs = 60000 } = {}) {
    const fresh = _loadedAt[tab] && Date.now() - _loadedAt[tab] < maxAgeMs;
    if (!first && !_stale.has(tab) && fresh) return false;
    _stale.delete(tab);
    _loadedAt[tab] = Date.now();
    return true;
}

// ── 字彙（全部來自 options）──
export const opt = () => state.options || {};
export const list = (k) => (Array.isArray(opt()[k]) ? opt()[k] : []);
/** 已付款＝payment_statuses 的最後一項（後端排序：應付…→已付）。 */
export const paidStatus = () => { const l = list('payment_statuses'); return l.length ? l[l.length - 1] : ''; };
export const lostPhase = () => opt().lost_phase || '';

// 打字就過濾的選擇器（owner 2026-09-03「所有的搜尋都要可以打字搜尋」）：手機上原生 <select> 不能打字，
// 清單一長（138 個客戶、上百個案子）就找不到。結構＝hidden input（id＝欄位名，既有程式照讀 .value）
// ＋搜尋框（id-q）＋結果列（id-list，最多 40 筆）。用 pointerdown 選，因為 blur 會先於 click 把結果列收掉。
export const pickerHtml = (id) =>
    `<input type="hidden" id="${id}"><input type="search" id="${id}-q" autocomplete="off" autocorrect="off">` +
    `<div class="m-pick" id="${id}-list" hidden></div>`;

// 分段按鈕：兩三個選項的欄位（電子／紙本、收款／付款、已收／未收）用按鈕比 <select> 好按；
// 值放 hidden input（id＝欄位名），既有程式照讀 .value
export const segHtml = (id, values, selected) =>
    `<input type="hidden" id="${id}" value="${esc(selected ?? values[0] ?? '')}"><div class="m-seg" id="${id}-seg">` +
    values.map(v => `<button type="button" data-v="${esc(v)}" class="${v === (selected ?? values[0]) ? 'on' : ''}">${esc(v)}</button>`).join('') + `</div>`;

export function mountSeg(id, onChange) {
    const hidden = document.getElementById(id), seg = document.getElementById(id + '-seg');
    if (!hidden || !seg) return;
    seg.onclick = (ev) => {
        const b = ev.target.closest('button[data-v]'); if (!b) return;
        hidden.value = b.dataset.v;
        seg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
        if (onChange) onChange(hidden.value);
    };
}

export function setSeg(id, values, selected) {
    const seg = document.getElementById(id + '-seg'), hidden = document.getElementById(id);
    if (!seg || !hidden) return;
    const v = values.includes(selected) ? selected : values[0] || '';
    hidden.value = v;
    seg.innerHTML = values.map(x => `<button type="button" data-v="${esc(x)}" class="${x === v ? 'on' : ''}">${esc(x)}</button>`).join('');
}

// free：沒選到清單項目時保留打的字當值（品項這種「有建議但可以自己打」的欄位）
export function mountPicker(id, { items = [], placeholder = '', value = '', free = false, onPick = null } = {}) {
    const hidden = document.getElementById(id), q = document.getElementById(id + '-q'), box = document.getElementById(id + '-list');
    if (!hidden || !q || !box) return;
    const label = (v) => (items.find(i => String(i.value) === String(v)) || {}).label || (free ? String(v || '') : '');
    const set = (v) => { hidden.value = label(v) ? String(v) : ''; q.value = label(v); box.hidden = true; if (onPick) onPick(hidden.value); };
    const draw = () => {
        const s = q.value.trim().toLowerCase();
        const hits = items.filter(i => !s || i.label.toLowerCase().includes(s)).slice(0, 40);
        box.innerHTML = hits.map(i => `<div class="m-pick-row" data-v="${esc(i.value)}">${esc(i.label)}</div>`).join('')
            || '<div class="m-pick-none">沒有符合的</div>';
        box.hidden = false;
    };
    hidden._items = items; hidden._set = set;          // applyPreset／表單重置用
    q.placeholder = placeholder;
    q.oninput = draw; q.onfocus = draw;
    // 收起結果列；打了字卻沒選 → 退回已選的那個（或清空），不留下「看起來選了其實沒選」
    q.onblur = () => setTimeout(() => {
        box.hidden = true;
        if (free) { hidden.value = q.value.trim(); if (onPick) onPick(hidden.value); }
        else q.value = label(hidden.value);
    }, 150);
    box.onpointerdown = (ev) => { const r = ev.target.closest('.m-pick-row'); if (r) { ev.preventDefault(); set(r.dataset.v); } };
    set(value);
}

export async function copyText(text) {
    try {
        if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (_) { /* 退回下面 */ }
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    document.body.removeChild(ta);
    return ok;
}

export function selectOpts(values, selected = '', blank = '') {
    const head = blank !== null && blank !== undefined && blank !== false
        ? `<option value="">${esc(blank)}</option>` : '';
    return head + values.map(v => {
        const val = typeof v === 'object' ? v.value : v;
        const lab = typeof v === 'object' ? v.label : v;
        return `<option value="${esc(val)}"${String(val) === String(selected) ? ' selected' : ''}>${esc(lab)}</option>`;
    }).join('');
}

// ── 狀態畫面 ──
export const skeleton = (n = 4) => Array.from({ length: n }, (_, i) =>
    `<div class="m-card"><div class="m-sk w60"></div><div class="m-sk w40"></div>${i % 2 ? '' : '<div class="m-sk"></div>'}</div>`).join('');
export const emptyBox = (msg = '沒有資料') => `<div class="m-empty">${esc(msg)}</div>`;
export const errBox = (e) => `<div class="m-err">${esc((e && e.message) || String(e))}</div>`;
export const pill = (text, cls = '') => text ? `<span class="pill ${cls}">${esc(text)}</span>` : '';
/** 前端分頁清單（整批資料已在手上）：先顯示 page 筆，底下「載入更多」每按一次多 page 筆，沒更多就藏——
 *  跟專案分頁（後端 offset 分頁）同一顆 .m-more 按鈕、同一種手感。同一容器可重複呼叫（重抓時整個重畫）。 */
export function renderPaged(el, rows, cardFn, { page = 10, empty = '沒有資料' } = {}) {
    if (!rows.length) { el._pg = null; el.innerHTML = emptyBox(empty); return; }
    el._pg = { rows, cardFn, page, shown: 0 };
    el.innerHTML = '<div class="pg-items"></div><button type="button" class="m-more" data-more hidden>載入更多</button>';
    if (!el._pgBound) {
        el._pgBound = true;
        el.addEventListener('click', (ev) => { if (ev.target.closest('button[data-more]')) _showMore(el); });
    }
    _showMore(el);
}
function _showMore(el) {
    const s = el._pg; if (!s) return;
    const next = s.rows.slice(s.shown, s.shown + s.page);
    el.querySelector('.pg-items').insertAdjacentHTML('beforeend', next.map(s.cardFn).join(''));
    s.shown += next.length;
    el.querySelector('button[data-more]').hidden = s.shown >= s.rows.length;
}
/** 狀態 pill 的顏色：依它在字彙清單裡的位置（前段=進行中、最後=完成），不認字。 */
export function statusPill(text, vocab) {
    const i = vocab.indexOf(text);
    if (i < 0) return pill(text);
    if (i === vocab.length - 1) return pill(text, 'ok');
    return pill(text, i === 0 ? '' : 'pri');
}

/** 金額格：鍵不在＝被抹掉（沒 money_view）→ 整格不畫；有鍵沒值→「—」。 */
export function moneyCell(obj, key, label, moneyFn) {
    if (!obj || !(key in obj)) return '';
    return `<span class="sub">${esc(label)} </span><span class="amt">${moneyFn(obj[key])}</span>`;
}

export async function withBusy(btn, fn) {
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = '處理中…';
    try { return await fn(); }
    finally { btn.disabled = false; btn.textContent = old; }
}

// ── 底部抽屜 ──
const sheet = () => document.getElementById('m-sheet');
export function openSheet(html) {
    const s = sheet();
    document.getElementById('m-sheet-body').innerHTML = html;
    s.hidden = false;
    document.body.style.overflow = 'hidden';
    s.querySelector('.pn').scrollTop = 0;
    return document.getElementById('m-sheet-body');
}
export function closeSheet() {
    sheet().hidden = true;
    document.body.style.overflow = '';
}

export function initSheet() {
    sheet().querySelector('.bd').addEventListener('click', closeSheet);
}
