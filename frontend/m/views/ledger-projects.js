/**
 * views/ledger-projects.js — 士源帳本「專案」分頁：可搜尋清單＋抽屜（改結案日／營收／案源、記雜支／委外費、工項唯讀、掛在本案的收支）。
 *
 * 資料＝桌機執行專案同一支 /api/v1/finance/project-ledger（顯示名／實收／應收都是它算的）。
 * 儲存只送有動的鍵（後端 PUT 是 exclude_unset）。🔴 費用鍵送的是**手填值**：畫面上的合計＝手填＋CRM 成本行
 * （project.cost_sources[key].crm），送合計會把 CRM 那半存成一份會走味的副本。
 * 連著母帳的案（locked_fields）結案日鎖住，到母帳改。
 */
import { mfetch, toast, esc, money } from '../shell.js';
import { openSheet, closeSheet, selectOpts, skeleton, emptyBox, errBox, withBusy } from '../ui.js';

const API = '/api/v1/finance/project-ledger';
const MAIN_FEES = ['outsource', 'misc'];      // 委外費用／行政雜支：顯眼；其餘收進「更多」

let _rows = [];
let _q = '';

export async function fetchLedger() {
    const r = await mfetch(`${API}?entity=mine`);
    _rows = r.projects || [];
    return _rows;
}

export function projectCard(p, { extra = '' } = {}) {
    const recv = Number(p.receivable || 0);
    const src = (p.detail || {}).source || '';
    const parents = p.parent_names || [];
    return `<div class="m-card tap" data-id="${esc(p.id)}">
        <div class="t"><div class="name">${esc(p.name)}</div><span class="amt">${money(p.contract)}</span></div>
        <div class="sub">${esc(p.client || '')}${src ? '・' + esc(src) : ''}${p.close_date ? '・' + esc(p.close_date) : '・未結案'}${
            parents.length ? `<br>母帳：${esc(parents.join('、'))}` : ''}</div>
        <div class="row"><span>實收 <span class="amt in">${money(p.received)}</span></span>
            <span>未收 <span class="amt ${recv > 0 ? 'out' : ''}">${money(recv)}</span></span></div>${extra}
    </div>`;
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
            <input class="m-search" id="pj-q" type="search" placeholder="搜案名／客戶／年份" autocomplete="off">
            <div id="pj-list">${skeleton(4)}</div>`;
        host.querySelector('#pj-q').addEventListener('input', (ev) => { _q = ev.target.value.trim().toLowerCase(); draw(host); });
        host.querySelector('#pj-list').addEventListener('click', (ev) => {
            const c = ev.target.closest('.m-card[data-id]'); if (!c) return;
            openProjectSheet(c.dataset.id, () => load(host));
        });
    }
    await load(host);
}

async function load(host) {
    const list = host.querySelector('#pj-list');
    try { await fetchLedger(); draw(host); }
    catch (e) { list.innerHTML = errBox(e); }
}

function draw(host) {
    const hit = (p) => !_q || [p.name, p.orig_name, p.client, p.close_date, ...(p.parent_names || [])]
        .filter(Boolean).some(s => String(s).toLowerCase().includes(_q));
    const rows = _rows.filter(hit).slice(0, 60);
    host.querySelector('#pj-list').innerHTML = rows.map(p => projectCard(p)).join('')
        || emptyBox(_q ? '沒有符合的案' : '私帳還沒有案');
}

/** 案源下拉：可選的那幾個 ＋ 舊案正在用的歷史值（「自接」不再可選，但舊案選著它時要就地補一個選項，
 *  否則畫面顯示成空、存檔會把值洗掉 —— 同桌機 projects.js 那條規則）。 */
function sourceOpts(selectable, current) {
    return current && !selectable.includes(current) ? [{ value: current, label: current + '（歷史值）' }, ...selectable] : selectable;
}

/** 抽屜：讀單筆（含 locked_fields／cost_sources／entries）。 */
export async function openProjectSheet(id, onDone) {
    const body = openSheet(skeleton(3));
    let d;
    try { d = await mfetch(`${API}/${encodeURIComponent(id)}?entity=mine`); }
    catch (e) { body.innerHTML = errBox(e); return; }
    const p = d.project, det = p.detail || {}, src = p.cost_sources || {};
    const locked = (p.locked_fields || []).includes('close_date');
    const fees = d.cost_fields || [];
    const manual = (k) => Number(det[k] || 0) - Number((src[k] || {}).crm || 0);    // 畫面合計 − CRM 那半 ＝ 手填
    const feeRow = (f) => `<div class="lg-fee" data-key="${esc(f.key)}">
        <div>${esc(f.label)}${src[f.key] ? `<div class="crm">＋CRM 成本行 ${money(src[f.key].crm)}</div>` : ''}</div>
        <input type="number" inputmode="numeric" min="0" step="1" id="pf-${esc(f.key)}" value="${manual(f.key)}">
        <button type="button" class="m-btn sm" data-add="${esc(f.key)}">＋加一筆</button></div>`;
    const main = fees.filter(f => MAIN_FEES.includes(f.key)), rest = fees.filter(f => !MAIN_FEES.includes(f.key));
    const split = Object.entries(det.split || {});
    const parents = p.parent_links || [];
    body.innerHTML = `
        <div class="ttl">${esc(p.name)}</div>
        <div class="lg-sub">${esc(p.client || '')}${parents.length ? '・母帳：' + esc(parents.map(x => x.name).join('、')) : ''}</div>
        <div class="m-strip" style="margin-top:10px">
            <div class="k"><div class="n">${money(p.received)}</div><div class="l">實收</div></div>
            <div class="k"><div class="n">${money(p.receivable)}</div><div class="l">未收</div></div>
        </div>
        <div class="m-form">
            <label>結案日${locked ? '<span class="lg-lock" title="以母帳為準：到母帳那一案改，會自動同步過來">母帳</span>' : ''}</label>
            <input type="date" id="pj-close" value="${esc(p.close_date || '')}" ${locked ? 'disabled' : ''}>
            <label>營收（含稅）</label>
            <input type="number" inputmode="numeric" min="0" step="1" id="pj-contract" value="${esc(p.contract || 0)}">
            <label>案源</label>
            <select id="pj-source">${selectOpts(sourceOpts(d.sources || [], det.source || ''), det.source || '', '—')}</select>
        </div>
        <div class="m-h">費用（手填；CRM 掛過來的另計）</div>
        <div class="m-card">${main.map(feeRow).join('')}
            ${rest.length ? `<details class="m-fold"><summary>更多費用欄</summary>${rest.map(feeRow).join('')}</details>` : ''}
        </div>
        <button type="button" class="m-btn-primary" id="pj-save">儲存</button>
        <div class="m-h">工項拆分（唯讀）</div>
        <div class="m-card">${split.length ? split.map(([k, v]) => `<div class="lg-row"><span class="k">${esc(k)}</span><span class="v">${money(v)}</span></div>`).join('')
            : '<div class="m-empty" style="padding:8px 0">還沒拆工項</div>'}
            <div class="lg-row" style="border-top:1px solid var(--line);margin-top:4px"><span class="k">實收檢查</span><span class="v">${money(p.net)}${p.check ? ` <span class="pill warn">差 ${money(p.check)}</span>` : ''}</span></div>
        </div>
        <div class="m-h">掛在本案的收支</div>
        <div class="m-card">${(d.entries || []).length ? d.entries.map(e => `<div class="lg-row"><span class="k">${esc(e.date || '')} ${esc(e.summary || '')}</span>
            <span class="v ${e.deposit ? 'amt in' : 'amt out'}">${e.deposit ? '+' + money(e.deposit) : '−' + money(e.expense)}</span></div>`).join('')
            : '<div class="m-empty" style="padding:8px 0">還沒有收支掛在這案</div>'}</div>
        ${p.notes ? `<div class="m-h">備註</div><div class="notes">${esc(p.notes)}</div>` : ''}`;

    // 「＋加一筆」：手填值加上去（純前端加總，存的時候一起送）
    body.querySelectorAll('button[data-add]').forEach(b => b.addEventListener('click', () => {
        const key = b.dataset.add;
        const raw = window.prompt('加多少？（金額）', '');
        const add = parseInt(raw || '', 10);
        if (!Number.isFinite(add) || add <= 0) return;
        const inp = body.querySelector(`#pf-${CSS.escape(key)}`);
        inp.value = (parseInt(inp.value || '0', 10) || 0) + add;
        inp.classList.add('dirty');
    }));
    body.querySelector('#pj-save').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        const payload = {};
        const close = body.querySelector('#pj-close');
        if (!locked && close.value !== (p.close_date || '')) payload.close_date = close.value;
        const contract = parseInt(body.querySelector('#pj-contract').value || '0', 10) || 0;
        if (contract !== Number(p.contract || 0)) payload.contract_amount = contract;
        const source = body.querySelector('#pj-source').value;
        if (source !== (det.source || '')) payload.source = source;
        for (const f of fees) {
            const v = parseInt(body.querySelector(`#pf-${CSS.escape(f.key)}`).value || '0', 10) || 0;
            if (v !== manual(f.key)) payload[f.key] = v;      // 手填值，不是合計
        }
        if (!Object.keys(payload).length) { toast('沒有改動'); return; }
        try {
            await mfetch(`${API}/${encodeURIComponent(id)}?entity=mine`, { method: 'PUT', body: payload });
            toast('已儲存'); closeSheet(); if (onDone) await onDone();
        } catch (e) { toast(e.message, 'err'); }
    }));
}
