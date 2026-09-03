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
};

export const TABS = ['invoice', 'petty', 'projects', 'quotes', 'payments'];
export const DEFAULT_TAB = 'invoice';

export function currentTab() {
    const h = (location.hash || '').replace(/^#/, '').split('?')[0];
    return TABS.includes(h) ? h : DEFAULT_TAB;
}

export function switchTab(tab) {
    if (location.hash === '#' + tab) window.dispatchEvent(new HashChangeEvent('hashchange'));
    else location.hash = tab;
}

// ── 字彙（全部來自 options）──
export const opt = () => state.options || {};
export const list = (k) => (Array.isArray(opt()[k]) ? opt()[k] : []);
export const invoiceVocab = (k) => ((opt().invoice || {})[k]) || [];
/** 已付款＝payment_statuses 的最後一項（後端排序：應付…→已付）。 */
export const paidStatus = () => { const l = list('payment_statuses'); return l.length ? l[l.length - 1] : ''; };
export const lostPhase = () => opt().lost_phase || '';

export function selectOpts(values, selected = '', blank = '') {
    const head = blank !== null && blank !== undefined && blank !== false
        ? `<option value="">${esc(blank)}</option>` : '';
    return head + values.map(v => {
        const val = typeof v === 'object' ? v.value : v;
        const lab = typeof v === 'object' ? v.label : v;
        return `<option value="${esc(val)}"${String(val) === String(selected) ? ' selected' : ''}>${esc(lab)}</option>`;
    }).join('');
}

export const clientName = (id) => {
    const c = list('clients').find(x => String(x.id) === String(id));
    return c ? (c.short_name || c.name || '') : '';
};

// ── 狀態畫面 ──
export const skeleton = (n = 4) => Array.from({ length: n }, (_, i) =>
    `<div class="m-card"><div class="m-sk w60"></div><div class="m-sk w40"></div>${i % 2 ? '' : '<div class="m-sk"></div>'}</div>`).join('');
export const emptyBox = (msg = '沒有資料') => `<div class="m-empty">${esc(msg)}</div>`;
export const errBox = (e) => `<div class="m-err">${esc((e && e.message) || String(e))}</div>`;
export const pill = (text, cls = '') => text ? `<span class="pill ${cls}">${esc(text)}</span>` : '';
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
export function sheetBody() { return document.getElementById('m-sheet-body'); }

export function initSheet() {
    sheet().querySelector('.bd').addEventListener('click', closeSheet);
}
