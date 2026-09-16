/**
 * report.js — 私帳月報（docs/MONTHLY_REPORT.md；owner 2026-09-17「在我更新帳戶後自動提供一份月報」「給我財務建議與財務分析」）。
 *
 * 月報在「登記餘額」按儲存時自動產生（同月覆蓋），這頁只負責列出各月與畫那一份；「重新產生」用現在的數字重算本月。
 * 數字、體檢、建議全部是後端 core/monthly_report.py 算好的（規則不是文案），前端不自己算、不自己寫建議。
 * 只在私帳出現（finance.html 的 .fin-nav-mine-only），所以固定走 finFetchMine。
 */
import { finFetchMine, finSubviewBoot, esc, fmtNum, finToast } from '../fin-utils.js';

let _c = null;
let _isCurrent = () => true;
let _months = [];
let _month = '';
let _r = null;

const CSS_ID = 'report-css';
const LEVEL_LABEL = { bad: '要處理', warn: '注意', info: '提醒', ok: '做得好' };

function _injectCss() {
    if (document.getElementById(CSS_ID)) return;
    const st = document.createElement('style');
    st.id = CSS_ID;
    st.textContent = `
.mr { color: #d1d5db; font-size: 13.5px; max-width: 980px; }
.mr .mr-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 0 0 16px; }
.mr .mr-top .meta { color: #9ca3af; font-size: 12.5px; }
.mr .mr-card { background: #222; border: 1px solid #333; border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
.mr .mr-card h3 { margin: 0 0 10px; font-size: 16px; color: #eee; display: flex; gap: 10px; align-items: baseline; }
.mr .mr-card h3 .why { font-size: 12px; color: #9ca3af; font-weight: 400; }
.mr .big { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; }
.mr .big .k { border-left: 3px solid #3b82f6; padding-left: 12px; }
.mr .big .k .l { font-size: 12px; color: #9ca3af; }
.mr .big .k .v { font-size: 24px; font-weight: 700; color: #f3f4f6; line-height: 1.15; font-variant-numeric: tabular-nums; }
.mr .big .k .d { font-size: 12.5px; color: #9ca3af; }
.mr .up { color: #86efac; } .mr .down { color: #fca5a5; } .mr .amber { color: #fbbf24; }
.mr table { width: 100%; border-collapse: collapse; }
.mr th, .mr td { padding: 7px 8px; border-bottom: 1px solid #333; text-align: left; vertical-align: top; }
.mr th { font-size: 12px; color: #9ca3af; font-weight: 500; }
.mr th.n, .mr td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.mr td.sub { color: #9ca3af; font-size: 12px; }
.mr .tblwrap { overflow-x: auto; }
.mr .pill { display: inline-block; font-size: 11.5px; padding: 1px 8px; border-radius: 999px; font-weight: 600; white-space: nowrap; }
.mr .pill.ok { background: #14532d; color: #86efac; } .mr .pill.warn { background: #3a2f12; color: #fbbf24; }
.mr .pill.bad { background: #4c1d1d; color: #fca5a5; } .mr .pill.na, .mr .pill.info { background: #333; color: #d1d5db; }
.mr .score { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; }
.mr .score .s { border: 1px solid #333; border-radius: 8px; padding: 10px 12px; }
.mr .score .s .l { font-size: 12px; color: #9ca3af; } .mr .score .s .v { margin-top: 3px; }
.mr .bars { display: grid; gap: 7px; }
.mr .bar { display: grid; grid-template-columns: 110px 1fr 150px; gap: 10px; align-items: center; font-size: 13px; }
.mr .bar .t { height: 9px; background: #333; border-radius: 999px; overflow: hidden; }
.mr .bar .t i { display: block; height: 100%; background: #3b82f6; }
.mr .bar .t i.low { background: #fbbf24; } .mr .bar .t i.none { background: #ef4444; }
.mr .bar .r { text-align: right; color: #9ca3af; font-variant-numeric: tabular-nums; }
.mr .advice { display: grid; gap: 10px; }
.mr .a { display: grid; grid-template-columns: 30px 1fr; gap: 10px; padding: 11px 13px; border-radius: 8px; border: 1px solid #333; background: #1f1f1f; }
.mr .a.bad { border-color: #7f1d1d; } .mr .a.warn { border-color: #6b5a1e; } .mr .a.ok { border-color: #14532d; }
.mr .a .no { font-size: 20px; font-weight: 700; color: #93c5fd; line-height: 1; }
.mr .a b { display: block; color: #f3f4f6; margin-bottom: 2px; }
.mr .a p { margin: 0; font-size: 13px; }
.mr .a .how { margin-top: 4px; font-size: 12.5px; color: #9ca3af; }
.mr svg.chart { width: 100%; height: auto; }
.mr ul.todo { margin: 0; padding-left: 20px; } .mr ul.todo li { margin: 3px 0; }
.mr .foot { font-size: 12px; color: #6b7280; line-height: 1.7; border-top: 1px solid #3a3a3a; padding-top: 10px; }
.mr .empty { color: #9ca3af; padding: 20px; text-align: center; }
@media (max-width: 720px) { .mr .bar { grid-template-columns: 90px 1fr 110px; } }
`;
    document.head.appendChild(st);
}

const wan = (n) => {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const a = Math.abs(Number(n));
    const s = a >= 1e8 ? `${(a / 1e8).toFixed(2).replace(/\.?0+$/, '')} 億` : `${(a / 1e4).toFixed(1).replace(/\.0$/, '')} 萬`;
    return (Number(n) < 0 ? '−' : '') + s;
};
const money = (n) => (n === null || n === undefined ? '—' : (Number(n) < 0 ? '−' : '') + '$' + fmtNum(Math.abs(Number(n))));
const delta = (n, { pct = null } = {}) => {
    if (n === null || n === undefined) return '<span class="sub">—</span>';
    const cls = n > 0 ? 'up' : (n < 0 ? 'down' : '');
    return `<span class="${cls}">${n > 0 ? '+' : ''}${money(n)}${pct !== null && pct !== undefined ? `（${n > 0 ? '+' : ''}${(pct * 100).toFixed(1)}%）` : ''}</span>`;
};

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _injectCss();
    const r = await finSubviewBoot(_c, {
        title: '月報', isCurrent: _isCurrent,
        fetchers: [() => finFetchMine('/monthly-reports')],
        retry: 'window._finReport.reload()',
    });
    if (!r) return;
    _months = r[0].items || [];
    _month = _months.length ? _months[0].month : '';
    _r = null;
    if (_month) {
        try { _r = (await finFetchMine(`/monthly-reports/${_month}`)).report; } catch (e) { finToast(e.message, true); }
    }
    if (!_isCurrent()) return;
    _render();
}

function _render() {
    const r = _r;
    _c.innerHTML = `<div class="mr">
        <div class="mr-top">
            <select class="crm-input" id="mr-month" style="width:140px" onchange="window._finReport.pick(this.value)">
                ${_months.map((m) => `<option value="${esc(m.month)}"${m.month === _month ? ' selected' : ''}>${esc(m.month.replace('-', ' 年 '))} 月</option>`).join('') || '<option value="">還沒有月報</option>'}
            </select>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finReport.regen(this)">用現在的數字重新產生本月</button>
            <span class="meta">${r ? `數字是 ${esc(r.basis_date)} 的；產生於 ${esc(r.generated_at || '')}${r.first ? '・第一份（下個月開始有上月可比）' : ''}` : '登記餘額按儲存時會自動產生當月月報'}</span>
        </div>
        ${r ? _report(r) : '<div class="mr-card"><div class="empty">還沒有月報。到「登記餘額」填今天的數字按儲存，或按上面「重新產生」。</div></div>'}
    </div>`;
}

function _report(r) {
    const t = r.totals, d = r.delta || {}, f = r.flow || {}, ft = r.fortress || {}, lf = r.ladder_fire || {};
    const prevLabel = r.prev ? r.prev.label : '';
    return `
    <div class="mr-card"><h3>這個月的錢 <span class="why">總資產＝現金＋證券＋應收＋器材淨值；負債＝卡費＋貸款</span></h3>
        <div class="big">
            <div class="k"><div class="l">淨值</div><div class="v">${esc(wan(t.net_worth))}</div><div class="d">${d.assets !== null && d.assets !== undefined ? `總資產比${esc(prevLabel)} ${delta(d.assets, { pct: r.prev && r.prev.totals.assets ? d.assets / r.prev.totals.assets : null })}` : '第一份，還沒有上月可比'}</div></div>
            <div class="k"><div class="l">可動用現金</div><div class="v">${esc(wan(t.cash))}</div><div class="d">可撐 ${esc(String(ft.runway ?? '—'))} 個月必要支出</div></div>
            <div class="k"><div class="l">證券現值</div><div class="v">${esc(wan(t.securities))}</div><div class="d">佔總資產 ${t.assets ? Math.round(t.securities / t.assets * 100) : 0}%</div></div>
            <div class="k"><div class="l">負債</div><div class="v">${esc(wan(t.liabilities))}</div><div class="d">卡費 ${esc(money(t.card))}${t.loan ? `、貸款 ${esc(money(t.loan))}` : '，沒有貸款'}</div></div>
        </div>
        <div class="tblwrap" style="margin-top:12px"><table>
            <thead><tr><th>項目</th><th class="n">本月（${esc(r.basis_date)}）</th><th class="n">${esc(prevLabel || '上月')}</th><th class="n">變化</th></tr></thead>
            <tbody>
                ${[['銀行現金', 'cash'], ['證券現值', 'securities'], ['應收帳款', 'receivable'], ['器材淨值', 'equipment'], ['總資產', 'assets'], ['負債', 'liabilities'], ['淨值', 'net_worth']].map(([l, k]) => `<tr>
                    <td>${k === 'assets' || k === 'net_worth' ? `<b>${l}</b>` : l}</td><td class="n">${esc(money(t[k]))}</td>
                    <td class="n">${r.prev && r.prev.totals[k] !== undefined && r.prev.totals[k] !== null ? esc(money(r.prev.totals[k])) : '<span class="sub">—</span>'}</td>
                    <td class="n">${delta(d[k])}</td></tr>`).join('')}
            </tbody></table></div>
    </div>
    <div class="mr-card"><h3>多出來的錢從哪來 <span class="why">存出來的、還是漲出來的</span></h3>
        <div class="tblwrap"><table><tbody>
            <tr><td>本月收入（收支明細）</td><td class="n">${esc(money(f.deposit))}</td><td class="sub">${f.has_entries ? '' : '本月明細還沒記，這兩格是空的'}</td></tr>
            <tr><td>本月支出</td><td class="n">${esc(money(f.expense))}</td><td class="sub">${f.has_entries ? `其中家用 ${esc(money(f.household))}` : ''}</td></tr>
            <tr><td>存下來的</td><td class="n">${f.has_entries ? delta(f.net) : '<span class="sub">—</span>'}</td><td class="sub">${f.savings_rate !== null && f.savings_rate !== undefined ? `存款率 ${(f.savings_rate * 100).toFixed(0)}%` : ''}</td></tr>
            <tr><td>證券漲跌</td><td class="n">${delta(f.securities_change)}</td><td class="sub">${f.securities_change === null ? '要有上一份月報的分項才算得出來' : ''}</td></tr>
            <tr><td>應收增減</td><td class="n">${delta(f.receivable_change)}</td><td class="sub">帳面，收回來才算數</td></tr>
            ${f.unexplained !== null && f.unexplained !== undefined ? `<tr><td>解釋不了的差額</td><td class="n">${delta(f.unexplained)}</td><td class="sub">通常是還沒記的明細或未拆的證券</td></tr>` : ''}
        </tbody></table></div>
    </div>
    <div class="mr-card"><h3>總資產走勢 <span class="why">淨值快照＋這個月</span></h3>${_chart(r.trend || [])}</div>
    <div class="mr-card"><h3>各帳戶 <span class="why">變動大的先排</span></h3>
        <div class="tblwrap"><table>
            <thead><tr><th>帳戶</th><th class="n">本月</th><th class="n">上月</th><th class="n">變化</th><th>狀態</th></tr></thead>
            <tbody>
                ${(r.accounts || []).map((a) => `<tr><td>${esc(a.name)}</td><td class="n">${esc(money(a.balance))}</td><td class="n">${a.prev === null || a.prev === undefined ? '<span class="sub">—</span>' : esc(money(a.prev))}</td><td class="n">${delta(a.delta)}</td>
                    <td>${a.unfilled === null || a.unfilled === undefined ? (a.registered_this_month ? '<span class="pill ok">已登記</span>' : '<span class="pill na">這個月還沒登記</span>') : (a.unfilled === 0 ? '<span class="pill ok">已登記・明細補齊</span>' : `<span class="pill warn">還沒補的明細 ${esc(money(a.unfilled))}</span>`)}</td></tr>`).join('')}
                ${(r.brokers || []).map((b) => `<tr><td><b>證券</b> ${esc(b.broker || '（未指定券商）')}</td><td class="n">${esc(money(b.total))}</td><td class="n">${b.prev === null || b.prev === undefined ? '<span class="sub">—</span>' : esc(money(b.prev))}</td><td class="n">${delta(b.delta)}</td>
                    <td>${b.plug ? `<span class="pill warn">未拆明細 ${esc(money(b.plug))}</span>` : `<span class="sub">${b.count} 檔</span>`}</td></tr>`).join('')}
            </tbody></table></div>
    </div>
    <div class="mr-card"><h3>堡壘 <span class="why">五層資金與六題壓力測試</span></h3>
        <div class="big" style="margin-bottom:12px">
            <div class="k"><div class="l">可撐月數</div><div class="v ${ft.tone === 'g' ? 'up' : (ft.tone === 'r' ? 'down' : 'amber')}">${esc(String(ft.runway ?? '—'))} 個月</div><div class="d">必要支出 ${esc(wan(ft.need))}／月${ft.need_override ? '（你認定的）' : `（${ft.need_sample_months} 個月平均）`}</div></div>
            <div class="k"><div class="l">壓力測試</div><div class="v">${ft.counts ? `${ft.counts.ok} 綠 · ${ft.counts.warn} 黃 · <span class="${ft.counts.bad ? 'down' : ''}">${ft.counts.bad} 紅</span>` : '—'}</div><div class="d">${(ft.tests || []).filter((x) => x.state === 'bad').map((x) => esc(x.title)).join('、') || '沒有紅燈'}</div></div>
        </div>
        <div class="bars">${(ft.layers || []).map((L) => {
            const pct = Math.max(0, Math.min(100, Number(L.pct) || 0));
            const cls = L.target === null || L.target === undefined ? '' : (pct === 0 ? 'none' : (pct < 50 ? 'low' : ''));
            return `<div class="bar"><span>${L.no} ${esc(L.name)}</span><div class="t"><i class="${cls}" style="width:${Math.max(pct, 2)}%"></i></div><span class="r">${esc(wan(L.have))}${L.target ? ` / ${esc(wan(L.target))}` : ' · 沒有目標'}</span></div>`;
        }).join('')}</div>
    </div>
    <div class="mr-card"><h3>財富階梯與財富自由</h3>
        <div class="big">
            <div class="k"><div class="l">階梯位置</div><div class="v">第 ${esc(String(lf.rung ?? '—'))} 階 ${esc(lf.rung_name || '')}</div><div class="d">${lf.to_next ? `離第 ${Number(lf.rung) + 1} 階${esc(lf.next_name || '')}還差 ${esc(wan(lf.to_next))}` : '最高階'}</div></div>
            <div class="k"><div class="l">不工作每月可花</div><div class="v">${esc(wan(lf.fire_allowed))}</div><div class="d">現在每月花 ${esc(wan(lf.fire_spend))}${lf.fire_pretax ? '・稅前' : ''}</div></div>
            <div class="k"><div class="l">財富自由達成率</div><div class="v ${lf.fire_ratio33 >= 1 ? 'up' : ''}">${lf.fire_ratio33 !== null && lf.fire_ratio33 !== undefined ? `${Math.round(lf.fire_ratio33 * 100)}%` : '—'}</div><div class="d">33 倍法則；實際提領率 ${lf.fire_rate !== null && lf.fire_rate !== undefined ? (lf.fire_rate * 100).toFixed(2) + '%' : '—'}</div></div>
            <div class="k"><div class="l">10 年後資產</div><div class="v">${esc(wan(lf.y10_nominal))}</div><div class="d">實質購買力 ${esc(wan(lf.y10_real))}（報酬 ${Math.round((lf.growth_rate || 0) * 100)}%、通膨 ${Math.round((lf.inflation || 0) * 100)}%）</div></div>
        </div>
    </div>
    <div class="mr-card"><h3>財務體檢 <span class="why">四個面向</span></h3>
        <div class="score">${(r.health || []).map((h) => `<div class="s"><div class="l">${esc(h.label)}</div><div class="v"><span class="pill ${esc(h.state)}">${esc(h.grade)}</span> ${esc(h.text)}</div></div>`).join('')}</div>
    </div>
    <div class="mr-card"><h3>財務建議 <span class="why">照急迫度排；每一條都有門檻，數字變了建議就變</span></h3>
        <div class="advice">${(r.advice || []).map((a) => `<div class="a ${esc(a.level)}"><div class="no">${a.no}</div><div><b>${esc(a.title)}</b>${a.text ? `<p>${esc(a.text)}</p>` : ''}${a.how ? `<div class="how">做法：${esc(a.how)}</div>` : ''}</div></div>`).join('') || '<div class="empty">沒有建議</div>'}</div>
    </div>
    <div class="mr-card"><h3>待辦 <span class="why">下個月登記前處理</span></h3>
        ${(r.todo || []).length ? `<ul class="todo">${r.todo.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<div class="sub">沒有待辦</div>'}
    </div>
    <div class="foot">怎麼產生：你在「登記餘額」按儲存的那一刻，系統把當月數字存成這份月報（同一個月再登記就覆蓋）。
        分析與建議是規則算出來的：每一條對應一個門檻（例如單一持股佔證券 25% 以上才出現集中度那條），數字變了建議就變、消失。
        「上月」欄看上一份月報；第一份只能拿最近一次淨值快照比總資產。</div>`;
}

/** 走勢：SVG 折線，只畫有的點；標最早、最高與現在 */
function _chart(trend) {
    const pts = trend.filter((p) => p && Number.isFinite(Number(p.total)));
    if (pts.length < 2) return '<div class="sub">要有兩個以上的快照才畫得出走勢</div>';
    const W = 860, H = 200, L = 60, R = 20, T = 20, B = 30;
    const max = Math.max(...pts.map((p) => Number(p.total))) * 1.08;
    // 全 0 或全負：除以 max 每個座標都是 NaN，SVG 會畫出一坨壞掉的線；這種走勢沒東西好看，直接說明
    if (!(max > 0)) return '<div class="sub">快照都是 0，還畫不出走勢</div>';
    const x = (i) => L + (W - L - R) * (i / (pts.length - 1));
    const y = (v) => T + (H - T - B) * (1 - Number(v) / max);
    const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.total).toFixed(1)}`).join(' ');
    const grid = [0.25, 0.5, 0.75, 1].map((g) => `<line x1="${L}" y1="${y(max * g).toFixed(1)}" x2="${W - R}" y2="${y(max * g).toFixed(1)}" stroke="#333" stroke-dasharray="3 4"/><text x="${L - 6}" y="${(y(max * g) + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="#9ca3af">${esc(wan(max * g))}</text>`).join('');
    const last = pts[pts.length - 1], first = pts[0];
    const labels = [[0, first], [pts.length - 1, last]].map(([i, p]) => `<text x="${x(i).toFixed(1)}" y="${(y(p.total) - 8).toFixed(1)}" text-anchor="${i === 0 ? 'start' : 'end'}" font-size="11" fill="#e5e7eb" font-weight="600">${esc(wan(p.total))}</text><text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : 'end'}" font-size="11" fill="#9ca3af">${esc(String(p.date))}</text>`).join('');
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="總資產走勢">${grid}
        <polyline fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linejoin="round" points="${line}"/>
        <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last.total).toFixed(1)}" r="4" fill="#3b82f6"/>${labels}</svg>`;
}

const _fr = (window._finReport = window._finReport || {});
_fr.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };
_fr.pick = async (month) => {
    if (!month) return;
    _month = month;
    try {
        _r = (await finFetchMine(`/monthly-reports/${month}`)).report;
        if (_isCurrent()) _render();
    } catch (e) { finToast(e.message, true); }
};
_fr.regen = async (btn) => {
    btn.disabled = true;
    try {
        const d = await finFetchMine('/monthly-reports/generate', { method: 'POST', body: JSON.stringify({}) });
        _months = (await finFetchMine('/monthly-reports')).items || [];
        _month = d.month;
        _r = d.report;
        if (!_isCurrent()) return;
        _render();
        finToast(`已重新產生 ${d.month} 的月報`);
    } catch (e) {
        finToast('產生失敗：' + e.message, true);
        btn.disabled = false;
    }
};
