/**
 * views/ledger-household.js — 士源帳本「家用」分頁（owner 2026-09-12 拍板：家用單獨一頁）。
 *
 * 跟收支分頁共用同一支端點與同一套表單／卡片／抽屜（ledger-cash.js）—— 差別只在分類是「家用」子樹、固定支出、不掛專案。
 * 本月合計與按分類小計在前端從清單算（近 90 天的家用列就在手上，不另打 API）。
 */
import { money, todayLocal, toast, esc } from '../shell.js';
import { skeleton, emptyBox, errBox, withBusy } from '../ui.js';
import { entryFormHtml, mountEntryForm, readEntryForm, createEntry, entryCard, openEntrySheet,
         fetchEntries, isHousehold } from './ledger-cash.js';

const DAYS = 90;
let _days = DAYS;
let _rows = [];

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
            <div class="m-h">記一筆家用</div>
            <div class="m-card">${entryFormHtml('hh', { mode: 'household' })}<button type="button" class="m-btn-primary" id="hh-go">記下</button></div>
            <div class="m-h">本月家用</div>
            <div id="hh-sum">${skeleton(1)}</div>
            <div class="m-h">近 <span id="hh-days">${DAYS}</span> 天</div>
            <div id="hh-list">${skeleton(3)}</div>
            <button type="button" class="m-more" id="hh-more">載入更早 90 天</button>`;
        mountEntryForm('hh', { mode: 'household' });
        host.querySelector('#hh-go').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
            let body;
            try { body = readEntryForm('hh', { mode: 'household' }); } catch (err) { toast(err.message, 'err'); return; }
            if (!body.taxonomy_node_id) { toast('家用要選一個分類', 'err'); return; }
            try {
                await createEntry(body);
                toast('已記下');
                for (const id of ['hh-amount', 'hh-summary', 'hh-note']) document.getElementById(id).value = '';
                await load(host);
            } catch (err) { toast(err.message, 'err'); }
        }));
        host.querySelector('#hh-list').addEventListener('click', (ev) => {
            const c = ev.target.closest('.m-card[data-id]'); if (!c) return;
            const e = _rows.find(x => x.id === c.dataset.id);
            if (e) openEntrySheet(e, { mode: 'household', onDone: () => load(host) });
        });
        host.querySelector('#hh-more').addEventListener('click', async () => { _days += DAYS; await load(host); });
    }
    await load(host);
}

async function load(host) {
    const list = host.querySelector('#hh-list'), sum = host.querySelector('#hh-sum');
    try {
        const { entries } = await fetchEntries(_days);
        _rows = entries.filter(isHousehold);
        host.querySelector('#hh-days').textContent = _days;
        list.innerHTML = _rows.map(entryCard).join('') || emptyBox('這段時間沒有家用');
        sum.innerHTML = monthSummary(_rows);
    } catch (e) {
        list.innerHTML = errBox(e);
    }
}

/** 本月合計＋按第二層分類小計（family／家用 的子樹第二層）。 */
function monthSummary(rows) {
    const ym = todayLocal().slice(0, 7);
    const mine = rows.filter(e => String(e.entry_date || '').slice(0, 7) === ym && e.expense);
    const total = mine.reduce((n, e) => n + (e.expense || 0), 0);
    const by = {};
    for (const e of mine) { const k = (e.taxonomy_path || [])[1] || '未分類'; by[k] = (by[k] || 0) + (e.expense || 0); }
    const lines = Object.entries(by).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `<div class="lg-row"><span class="k">${esc(k)}</span><span class="v amt out">${money(v)}</span></div>`).join('');
    return `<div class="m-card"><div class="lg-big">${money(total)}</div><div class="lg-sub">${esc(ym)}・${mine.length} 筆</div>
        ${lines ? `<div style="margin-top:8px">${lines}</div>` : ''}</div>`;
}
