/**
 * views/ledger-assets.js — 士源帳本「資產」分頁（owner 2026-09-12 拍板：資產表獨立一頁）。唯讀。
 *
 * 資料＝桌機資產儀表板同三支：/assets/overview（自動桶＋銀行分列＋持股＋上次快照）、
 * /assets/equipment（器材與淨值）、/assets/snapshots（最近兩筆算增減）。
 * 總資產＝「現在估計」（系統自動桶＋上次快照裡未被取代的手填桶）—— 規則在 js/shared/asset-buckets.js，跟桌機儀表板同一份。
 * L2：淨值成長線＝純 inline SVG（不載圖表庫），x＝快照日期、y＝total，最多最近 60 筆；點一點看「日期・金額」。
 * 拍快照、改持股、更新報價留桌機，這頁沒有任何寫入鈕。
 */
import { mfetch, esc, money } from '../shell.js';
import { skeleton, errBox, shouldLoad, markStale } from '../ui.js';
import { manualBuckets, estimatedTotal } from '/js/shared/asset-buckets.js';

const API = '/api/v1/finance/assets';

export async function render(host, { first }) {
    if (first) host.innerHTML = skeleton(4);
    // 60 秒內切回來不重抓、寫過的分頁由 markStale 標髒（同 CRM 手機版七個 view 的做法）
    if (shouldLoad('assets', { first })) await load(host);
}

async function load(host) {
    try {
        const [ov, eq, sn] = await Promise.all([
            mfetch(`${API}/overview?entity=mine`),
            mfetch(`${API}/equipment?entity=mine`),
            mfetch(`${API}/snapshots?entity=mine`),
        ]);
        host.innerHTML = draw(ov, eq, sn);
        mountChart(host);
    } catch (e) { host.innerHTML = errBox(e); }
}

const CHART_MAX = 60;      // 只畫最近 60 筆快照（Sheet 匯入 116 列＋系統拍的；再多線就糊成一片）

/** 快照序列 → SVG 折線的 HTML。快照 ≤1 筆畫不出線，回一句話。 */
export function growthChartHtml(snapshots) {
    const rows = (snapshots || []).filter(x => x && x.date && Number.isFinite(Number(x.total))).slice(-CHART_MAX);
    if (rows.length < 2) return '<div class="m-empty" style="padding:8px 0">快照不足兩筆，桌機拍幾次再回來看</div>';
    const W = 340, H = 120, PX = 6, PY = 8;
    const vals = rows.map(r => Number(r.total));
    const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
    const x = (i) => PX + (i * (W - 2 * PX)) / (rows.length - 1);
    const y = (v) => PY + (H - 2 * PY) * (1 - (v - min) / span);
    const pts = rows.map((r, i) => [x(i), y(Number(r.total))]);
    const d = pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
    const [lx, ly] = pts[pts.length - 1];
    // 每一點一顆小圓（選中的那顆填色）。🔴 觸控**不是**靠圓：60 點擠在 340 單位裡、圓會互相蓋住，
    // 點到的永遠是 DOM 後面那顆 —— 改成在整張 svg 上接事件、找 x 最近的那一點（見 mountChart）。
    const dots = pts.map(([px, py], i) => `<circle class="pt${i === pts.length - 1 ? ' on' : ''}" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3" data-i="${i}"></circle>`).join('');
    const first = rows[0], last = rows[rows.length - 1];
    return `<div class="lg-chart-tip" id="as-tip">${esc(last.date)}・${money(last.total)}</div>
        <svg class="lg-chart" id="as-chart" viewBox="0 0 ${W} ${H}" data-px="${PX}" data-w="${W}" role="img" aria-label="淨值成長線">
            <path d="${d}" fill="none" stroke="var(--pri)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
            <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4" fill="var(--pri)"></circle>
            ${dots}
        </svg>
        <div class="lg-chart-axis"><span>${esc(first.date)}<br>${money(first.total)}</span><span style="text-align:right">${esc(last.date)}<br>${money(last.total)}</span></div>
        <div class="lg-chart-axis" style="margin-top:4px"><span>最低 ${money(min)}</span><span>最高 ${money(max)}</span></div>`;
}

let _chartRows = [];

function mountChart(host) {
    const svg = host.querySelector('#as-chart');
    if (!svg) return;
    const tip = host.querySelector('#as-tip');
    const n = _chartRows.length;
    if (n < 2) return;
    const PX = Number(svg.dataset.px), W = Number(svg.dataset.w);
    // 手指落在哪個 x → 最近的那一點（把螢幕座標換回 viewBox 座標；svg 是等比縮放）
    const pick = (clientX) => {
        const rc = svg.getBoundingClientRect();
        const vx = (clientX - rc.left) * (W / rc.width);
        const i = Math.max(0, Math.min(n - 1, Math.round((vx - PX) / ((W - 2 * PX) / (n - 1)))));
        const r = _chartRows[i]; if (!r) return;
        svg.querySelectorAll('circle.pt.on').forEach(el => el.classList.remove('on'));
        const c = svg.querySelector(`circle.pt[data-i="${i}"]`); if (c) c.classList.add('on');
        tip.textContent = `${r.date}・${money(r.total)}`;
    };
    svg.addEventListener('click', (ev) => pick(ev.clientX));
    svg.addEventListener('touchstart', (ev) => { const t = ev.touches && ev.touches[0]; if (t) pick(t.clientX); }, { passive: true });
}

function draw(ov, eq, sn) {
    const auto = ov.buckets || {};
    const last = ov.last_snapshot || null;
    const manual = manualBuckets(auto, last);          // 未被系統桶取代的手填桶（規則只有那一份）
    const total = estimatedTotal(auto, last);
    const snaps = sn.snapshots || [];                   // /assets/snapshots 由舊到新
    _chartRows = snaps.filter(x => x && x.date && Number.isFinite(Number(x.total))).slice(-CHART_MAX);
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
        <div class="m-h">淨值成長線</div>
        <div class="m-card">${growthChartHtml(snaps)}</div>
        <div class="m-h">快照</div>
        <div class="m-card">${last
            ? `<div class="lg-row"><span class="k">上次快照 ${esc(last.date)}</span><span class="v amt">${money(last.total)}</span></div>
               ${delta !== null ? `<div class="lg-row"><span class="k">跟 ${esc(prev.date)} 比</span><span class="v amt ${delta >= 0 ? 'in' : 'out'}">${delta >= 0 ? '+' : ''}${money(delta)}</span></div>` : ''}
               <div class="lg-sub" style="padding-top:8px">拍快照在桌機的資產儀表板。</div>`
            : '<div class="m-empty">還沒拍過快照</div>'}</div>`;
}
