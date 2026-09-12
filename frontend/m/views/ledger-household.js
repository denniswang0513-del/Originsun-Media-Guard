/**
 * views/ledger-household.js — 士源帳本「家用」分頁（owner 2026-09-12 拍板：家用單獨一頁）。
 *
 * 跟收支分頁共用同一支端點與同一套表單／卡片／抽屜（ledger-cash.js）—— 差別只在分類是「家用」子樹、固定支出、不掛專案。
 * L2：按月看（‹ 2026-09 ›）—— 清單、合計、按分類小計都是**該月**的（date_from／date_to 直接給後端，
 * 前端只再濾掉非家用的列）；「記一筆家用」的日期預設仍是今天，不跟著看的月份走。
 * 日期字串一律本地 YYYY-MM-DD（不用 toISOString：那是 UTC，台北早上 8 點前會變昨天）。
 */
import { mfetch, money, todayLocal, toast, esc } from '../shell.js';
import { skeleton, emptyBox, errBox, withBusy } from '../ui.js';
import { entryFormHtml, mountEntryForm, readEntryForm, createEntry, entryCard, openEntrySheet,
         isHousehold } from './ledger-cash.js';

const API = '/api/v1/crm/cash-entries';
let _ym = todayLocal().slice(0, 7);      // 看的月份 'YYYY-MM'
let _rows = [];

/** 'YYYY-MM' ± n 個月。 */
export function shiftMonth(ym, n) {
    const [y, m] = ym.split('-').map(Number);
    const d = new Date(y, m - 1 + n, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** 'YYYY-MM' → 該月頭尾（本地日，含頭含尾）。 */
export function monthRange(ym) {
    const [y, m] = ym.split('-').map(Number);
    const last = new Date(y, m, 0).getDate();
    return { from: `${ym}-01`, to: `${ym}-${String(last).padStart(2, '0')}` };
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
            <div class="m-h">記一筆家用</div>
            <div class="m-card">${entryFormHtml('hh', { mode: 'household' })}<button type="button" class="m-btn-primary" id="hh-go">記下</button></div>
            <div class="lg-month">
                <button type="button" id="hh-prev" aria-label="上個月">‹</button>
                <span class="ym" id="hh-ym">${esc(_ym)}</span>
                <button type="button" id="hh-next" aria-label="下個月">›</button>
            </div>
            <div id="hh-sum">${skeleton(1)}</div>
            <div class="m-h">這個月的家用</div>
            <div id="hh-list">${skeleton(3)}</div>`;
        mountEntryForm('hh', { mode: 'household' });
        host.querySelector('#hh-go').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
            let body;
            try { body = readEntryForm('hh', { mode: 'household' }); } catch (err) { toast(err.message, 'err'); return; }
            if (!body.taxonomy_node_id) { toast('家用要選一個分類', 'err'); return; }
            try {
                await createEntry(body);
                toast('已記下');
                for (const id of ['hh-amount', 'hh-summary', 'hh-note']) document.getElementById(id).value = '';
                // 記的那筆若不在看的月份（例如看上個月、記的是今天），切回它所在的月份讓人看到它
                const ym = String(body.entry_date || todayLocal()).slice(0, 7);
                if (ym !== _ym) _ym = ym;
                await load(host);
            } catch (err) { toast(err.message, 'err'); }
        }));
        host.querySelector('#hh-list').addEventListener('click', (ev) => {
            const c = ev.target.closest('.m-card[data-id]'); if (!c) return;
            const e = _rows.find(x => x.id === c.dataset.id);
            if (e) openEntrySheet(e, { mode: 'household', onDone: () => load(host) });
        });
        host.querySelector('#hh-prev').addEventListener('click', async () => { _ym = shiftMonth(_ym, -1); await load(host); });
        host.querySelector('#hh-next').addEventListener('click', async () => { _ym = shiftMonth(_ym, 1); await load(host); });
    }
    await load(host);
}

async function load(host) {
    const list = host.querySelector('#hh-list'), sum = host.querySelector('#hh-sum');
    host.querySelector('#hh-ym').textContent = _ym;
    // 「下個月」到本月為止：未來的月份不會有家用
    host.querySelector('#hh-next').disabled = _ym >= todayLocal().slice(0, 7);
    list.innerHTML = skeleton(3);
    try {
        const { from, to } = monthRange(_ym);
        const r = await mfetch(`${API}?entity=mine&date_from=${from}&date_to=${to}`);
        _rows = (r.entries || []).filter(isHousehold);
        list.innerHTML = _rows.map(entryCard).join('') || emptyBox(`${_ym} 沒有家用`);
        sum.innerHTML = monthSummary(_rows, _ym);
    } catch (e) {
        list.innerHTML = errBox(e);
    }
}

/** 該月合計＋按第二層分類小計（家用子樹的第二層）。 */
function monthSummary(rows, ym) {
    const mine = rows.filter(e => e.expense);
    const total = mine.reduce((n, e) => n + (e.expense || 0), 0);
    const by = {};
    for (const e of mine) { const k = (e.taxonomy_path || [])[1] || '未分類'; by[k] = (by[k] || 0) + (e.expense || 0); }
    const lines = Object.entries(by).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `<div class="lg-row"><span class="k">${esc(k)}</span><span class="v amt out">${money(v)}</span></div>`).join('');
    return `<div class="m-card"><div class="lg-big">${money(total)}</div><div class="lg-sub">${esc(ym)}・${mine.length} 筆</div>
        ${lines ? `<div style="margin-top:8px">${lines}</div>` : ''}</div>`;
}
