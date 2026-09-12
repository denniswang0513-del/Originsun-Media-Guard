/**
 * views/ledger-overview.js — 士源帳本「總覽」分頁：本月／本年／全部 → GET /api/v1/finance/m/home?period=。
 * 案子四卡（結案日落在區間的案）＋現金流卡（收支明細）＋最近 5 筆＋未收前 5 案。唯讀。
 */
import { mfetch, esc, money } from '../shell.js';
import { segHtml, mountSeg, skeleton, errBox, shouldLoad, markStale } from '../ui.js';
import { openProjectSheet } from './ledger-projects.js';

const PERIODS = [['month', '本月'], ['year', '本年'], ['all', '全部']];
let _period = 'month';

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `${segHtml('ov-period', PERIODS.map(p => p[1]), '本月')}<div id="ov-body">${skeleton(4)}</div>`;
        mountSeg('ov-period', (label) => { _period = (PERIODS.find(p => p[1] === label) || PERIODS[0])[0]; load(host); });
        host.querySelector('#ov-body').addEventListener('click', (ev) => {
            const r = ev.target.closest('[data-project]');
            if (r) openProjectSheet(r.dataset.project, () => load(host));
        });
    }
    // 60 秒內切回來不重抓、寫過的分頁由 markStale 標髒（同 CRM 手機版七個 view 的做法）
    if (shouldLoad('overview', { first })) await load(host);
}

async function load(host) {
    const body = host.querySelector('#ov-body');
    try {
        const h = await mfetch(`/api/v1/finance/m/home?period=${_period}`);
        const pj = h.projects || {}, cash = h.cash || {};
        const rng = h.range && h.range.from ? `${h.range.from} ～ ${h.range.to}` : '所有年份';
        const card = (n, l, cls = '') => `<div class="k"><div class="n ${cls}">${money(n)}</div><div class="l">${esc(l)}</div></div>`;
        body.innerHTML = `
            <div class="lg-sub" style="margin:-4px 0 8px">${esc(rng)}・${pj.count || 0} 案（結案日在區間內）</div>
            <div class="m-strip">${card(pj.contract, '營收')}${card(pj.received, '實收')}${card(pj.receivable, '應收')}${card(pj.net, '淨收')}</div>
            <div class="m-h">現金流（收支明細）</div>
            <div class="m-card">
                <div class="lg-row"><span class="k">收入</span><span class="v amt in">${money(cash.deposit)}</span></div>
                <div class="lg-row"><span class="k">支出</span><span class="v amt out">${money(cash.expense)}</span></div>
                <div class="lg-row"><span class="k">其中家用</span><span class="v">${money(cash.household_expense)}</span></div>
                <div class="lg-row"><span class="k">淨</span><span class="v amt ${Number(cash.net) < 0 ? 'out' : 'in'}">${money(cash.net)}</span></div>
            </div>
            <div class="m-h">最近 5 筆</div>
            <div class="m-card">${(h.recent || []).map(e => `<div class="lg-row"><span class="k">${esc(e.date)} ${esc(e.summary)}${e.project_name ? `<div class="lg-sub">${esc(e.project_name)}</div>` : ''}</span>
                <span class="v ${e.deposit ? 'amt in' : 'amt out'}">${e.deposit ? '+' + money(e.deposit) : '−' + money(e.expense)}</span></div>`).join('') || '<div class="m-empty">還沒有收支</div>'}</div>
            <div class="m-h">未收前 5 案</div>
            <div class="m-card">${(h.to_collect || []).map(p => `<div class="lg-row" data-project="${esc(p.id)}"><span class="k">${esc(p.name)}<div class="lg-sub">${esc(p.client || '')}</div></span>
                <span class="v amt out">${money(p.receivable)}</span></div>`).join('') || '<div class="m-empty">都收齊了</div>'}</div>`;
    } catch (e) { body.innerHTML = errBox(e); }
}
