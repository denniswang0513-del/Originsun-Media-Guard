/**
 * views/ledger-receivable.js — 士源帳本「應收」分頁：誰還沒付（未收 > 0 的案，按金額排）。
 *
 * 資料同專案分頁（/project-ledger）。每卡「收到錢」→ 切到收支分頁、把「記一筆」預填成
 * 收入＋這案＋未收金額 —— 已收是從收支推的（增量制），手機上**沒有**直接標已收的動作。
 */
import { esc, money } from '../shell.js';
import { state, skeleton, emptyBox, errBox, shouldLoad, markStale } from '../ui.js';
import { fetchLedger, projectCard, openProjectSheet, toCollect } from './ledger-projects.js';

let _rows = [];

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `<div id="rc-sum"></div><div id="rc-list">${skeleton(4)}</div>`;
        host.querySelector('#rc-list').addEventListener('click', (ev) => {
            const btn = ev.target.closest('button[data-collect]');
            if (btn) {
                const p = _rows.find(x => x.id === btn.dataset.collect); if (!p) return;
                state.cashPreset = { kind: 'deposit', project_id: p.id, amount: toCollect(p), summary: `${p.name} 收款` };
                state.gotoTab('cash');
                return;
            }
            const c = ev.target.closest('.m-card[data-id]');
            if (c) openProjectSheet(c.dataset.id, () => load(host));
        });
    }
    // 60 秒內切回來不重抓、寫過的分頁由 markStale 標髒（同 CRM 手機版七個 view 的做法）
    if (shouldLoad('receivable', { first })) await load(host);
}

async function load(host) {
    const list = host.querySelector('#rc-list'), sum = host.querySelector('#rc-sum');
    try {
        const all = await fetchLedger();
        _rows = all.filter(p => toCollect(p) > 0).sort((a, b) => toCollect(b) - toCollect(a));
        const total = _rows.reduce((n, p) => n + toCollect(p), 0);
        sum.innerHTML = `<div class="m-card"><div class="lg-big">${money(total)}</div><div class="lg-sub">${_rows.length} 案還沒收齊</div></div>`;
        list.innerHTML = _rows.map(p => projectCard(p, {
            extra: `<div class="m-actions"><button type="button" class="m-btn pri" data-collect="${esc(p.id)}">收到錢</button></div>` })).join('')
            || emptyBox('沒有未收的案');
    } catch (e) { list.innerHTML = errBox(e); }
}
