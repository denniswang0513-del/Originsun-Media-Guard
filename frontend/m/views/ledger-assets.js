/**
 * views/ledger-assets.js — 士源帳本「資產」分頁（owner 2026-09-12 拍板：資產表獨立一頁）。唯讀。
 *
 * 資料＝桌機資產儀表板同三支：/assets/overview（自動桶＋銀行分列＋持股＋上次快照）、
 * /assets/equipment（器材與淨值）、/assets/snapshots（最近兩筆算增減）。
 * 總資產＝「現在估計」（系統自動桶＋上次快照裡未被取代的手填桶）—— 規則在 js/shared/asset-buckets.js，跟桌機儀表板同一份。
 * 拍快照、改持股、更新報價留桌機，這頁沒有任何寫入鈕。
 */
import { mfetch, esc, money } from '../shell.js';
import { skeleton, errBox } from '../ui.js';
import { manualBuckets, estimatedTotal } from '/js/shared/asset-buckets.js';

const API = '/api/v1/finance/assets';

export async function render(host, { first }) {
    if (first) host.innerHTML = skeleton(4);
    await load(host);
}

async function load(host) {
    try {
        const [ov, eq, sn] = await Promise.all([
            mfetch(`${API}/overview?entity=mine`),
            mfetch(`${API}/equipment?entity=mine`),
            mfetch(`${API}/snapshots?entity=mine`),
        ]);
        host.innerHTML = draw(ov, eq, sn);
    } catch (e) { host.innerHTML = errBox(e); }
}

function draw(ov, eq, sn) {
    const auto = ov.buckets || {};
    const last = ov.last_snapshot || null;
    const manual = manualBuckets(auto, last);          // 未被系統桶取代的手填桶（規則只有那一份）
    const total = estimatedTotal(auto, last);
    const snaps = sn.snapshots || [];                   // /assets/snapshots 由舊到新
    const prev = snaps.length > 1 ? snaps[snaps.length - 2] : null;
    const delta = last && prev ? Number(last.total || 0) - Number(prev.total || 0) : null;

    const row = (k, v) => `<div class="lg-row"><span class="k">${esc(k)}</span><span class="v amt">${money(v)}</span></div>`;
    const fold = (title, amount, inner) => `<details class="m-fold m-card"><summary><span>${esc(title)}</span><span class="amt">${money(amount)}</span></summary>${inner}</details>`;

    const bank = (ov.bank_lines || []).map(b => row(b.name, b.amount)).join('') || '<div class="m-empty">沒有帳戶</div>';
    const holdings = (ov.holdings || []).map(h => `<div class="lg-row"><span class="k">${esc(h.name)}${h.broker ? `<div class="lg-sub">${esc(h.broker)}${h.currency && h.currency !== 'TWD' ? '・' + esc(h.currency) : ''}</div>` : ''}</span>
        <span class="v"><span class="amt">${money(h.value_twd)}</span>${h.pnl !== null && h.pnl !== undefined ? `<div class="lg-sub ${Number(h.pnl) >= 0 ? 'amt in' : 'amt out'}">${Number(h.pnl) >= 0 ? '+' : ''}${money(h.pnl)}</div>` : ''}</span></div>`).join('')
        || '<div class="m-empty">沒有持股</div>';
    const gear = (eq.items || []).filter(x => x.counted).map(x => row(x.name, x.net)).join('') || '<div class="m-empty">沒有計入的器材</div>';
    const gt = eq.totals || {};

    return `
        <div class="m-card"><div class="lg-big">${money(total)}</div>
            <div class="lg-sub">總資產（自動桶${Object.keys(manual).length ? '＋手填桶' : ''}）${ov.usd_twd ? `・USD/TWD ${esc(ov.usd_twd)}` : ''}</div></div>
        ${fold('銀行現金', auto['銀行現金'], bank)}
        ${fold('應收帳款', auto['應收帳款'], '<div class="lg-sub" style="padding:8px 0">＝私帳各案的未收合計（應收分頁）</div>')}
        ${fold('固定資產淨值', auto['固定資產淨值'], `<div class="lg-sub" style="padding:8px 0">${gt.counted || 0} 件計入・成本 ${money(gt.cost)}・截至 ${esc(eq.as_of || '')}</div>${gear}`)}
        ${fold('證券現值', auto['證券現值'], holdings)}
        ${Object.keys(manual).length ? `<div class="m-h">手填桶（上次快照）</div><div class="m-card">${Object.entries(manual).map(([k, v]) => row(k, v)).join('')}</div>` : ''}
        <div class="m-h">快照</div>
        <div class="m-card">${last
            ? `<div class="lg-row"><span class="k">上次快照 ${esc(last.date)}</span><span class="v amt">${money(last.total)}</span></div>
               ${delta !== null ? `<div class="lg-row"><span class="k">跟 ${esc(prev.date)} 比</span><span class="v amt ${delta >= 0 ? 'in' : 'out'}">${delta >= 0 ? '+' : ''}${money(delta)}</span></div>` : ''}
               <div class="lg-sub" style="padding-top:8px">拍快照在桌機的資產儀表板。</div>`
            : '<div class="m-empty">還沒拍過快照</div>'}</div>`;
}
