/**
 * views/ledger-report.js — 士源帳本「月報」頁（docs/MONTHLY_REPORT.md）。
 *
 * 隱藏路由 #report（tabbar 沒有它的鈕；從總覽堡壘卡下方的「月報」列、或登記餘額存完的那一列進來；上一頁回總覽）。
 * 資料＝桌機那支同一份：GET /api/v1/finance/monthly-reports（各月）＋ /monthly-reports/{month}（那一份）。
 * 數字、體檢、建議全部後端算好（core/monthly_report.py），這頁只畫。頂端可以切月份、可以「重新產生本月」。
 */
import { mfetch, money, toast, esc } from '../shell.js';
import { skeleton, errBox, withBusy } from '../ui.js';

export const REPORT_API = '/api/v1/finance/monthly-reports';
const Q = '?entity=mine';

let _months = [];
let _month = '';
let _r = null;

const wan = (n) => {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return '—';
    const a = Math.abs(Number(n));
    const s = a >= 1e8 ? `${(a / 1e8).toFixed(2).replace(/\.?0+$/, '')} 億` : `${(a / 1e4).toFixed(1).replace(/\.0$/, '')} 萬`;
    return (Number(n) < 0 ? '−' : '') + s;
};
const delta = (n) => {
    if (n === null || n === undefined) return '<span style="color:var(--sub)">—</span>';
    const cls = n > 0 ? 'in' : (n < 0 ? 'out' : '');
    return `<span class="amt ${cls}">${n > 0 ? '+' : ''}${money(n)}</span>`;
};
const pill = (state, text) => `<span class="pill ${esc(state === 'na' ? '' : state)}">${esc(text)}</span>`;

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = skeleton(4);
        host.addEventListener('click', (ev) => {
            const b = ev.target.closest('#mr-regen');
            if (b) regen(host, b);
        });
        host.addEventListener('change', (ev) => {
            if (ev.target.id === 'mr-month') pick(host, ev.target.value);
        });
    }
    // 每次進來都重抓：登記完會自動產生新的一份
    await load(host);
}

async function load(host) {
    try {
        _months = (await mfetch(REPORT_API + Q)).items || [];
        _month = _months.length ? _months[0].month : '';
        _r = _month ? (await mfetch(`${REPORT_API}/${_month}${Q}`)).report : null;
        host.innerHTML = draw();
    } catch (e) {
        host.innerHTML = errBox(e);
    }
}

async function pick(host, month) {
    if (!month) return;
    try {
        _month = month;
        const r = (await mfetch(`${REPORT_API}/${month}${Q}`)).report;
        if (_month !== month) return;          // 等的時候又切了別月：慢的那次不能蓋掉快的
        _r = r;
        host.innerHTML = draw();
    } catch (e) { toast(e.message, 'err'); }
}

async function regen(host, btn) {
    await withBusy(btn, async () => {
        try {
            const d = await mfetch(`${REPORT_API}/generate${Q}`, { method: 'POST', body: {} });   // mfetch 自己 stringify
            _months = (await mfetch(REPORT_API + Q)).items || [];
            _month = d.month;
            _r = d.report;
            host.innerHTML = draw();
            toast(`已重新產生 ${d.month} 的月報`);
            window.scrollTo(0, 0);
        } catch (err) { toast(err.message, 'err'); }
    });
}

function draw() {
    const r = _r;
    const top = `<div class="m-card">
        <div class="m-form"><label>哪個月</label><select id="mr-month">${_months.map((m) => `<option value="${esc(m.month)}"${m.month === _month ? ' selected' : ''}>${esc(m.month.replace('-', ' 年 '))} 月</option>`).join('') || '<option value="">還沒有月報</option>'}</select></div>
        <div class="lg-sub" style="margin-top:6px">${r ? `數字是 ${esc(r.basis_date)} 的${r.first ? '・第一份，下個月開始有上月可比' : ''}` : '登記餘額按儲存時會自動產生當月月報'}</div>
        <button type="button" class="m-btn-secondary" id="mr-regen" style="margin-top:8px">用現在的數字重新產生本月</button>
    </div>`;
    if (!r) return top + '<div class="m-empty">還沒有月報。到登記餘額填今天的數字按儲存，或按上面重新產生。</div>';
    const t = r.totals, d = r.delta || {}, f = r.flow || {}, ft = r.fortress || {}, lf = r.ladder_fire || {};
    const prevLabel = r.prev ? r.prev.label : '上月';
    const row = (k, v, sub = '') => `<div class="lg-row"><span class="k">${esc(k)}${sub ? `<div class="lg-sub">${esc(sub)}</div>` : ''}</span><span class="v">${v}</span></div>`;
    return `${top}
    <div class="m-h">這個月的錢</div>
    <div class="m-card">
        <div class="mr-big"><span class="n">${esc(wan(t.net_worth))}</span><span class="u">淨值</span></div>
        <div class="lg-sub">${d.assets !== null && d.assets !== undefined ? `總資產比${esc(prevLabel)} ${delta(d.assets)}` : '第一份，還沒有上月可比'}</div>
        ${row('可動用現金', `<b>${money(t.cash)}</b>`, `可撐 ${ft.runway ?? '—'} 個月`)}
        ${row('證券現值', `<b>${money(t.securities)}</b>`, `佔總資產 ${t.assets ? Math.round(t.securities / t.assets * 100) : 0}%`)}
        ${row('應收帳款', money(t.receivable), '帳面，收回來才算數')}
        ${row('負債', money(t.liabilities), t.loan ? `卡費 ${money(t.card)}、貸款 ${money(t.loan)}` : '只有卡費，沒有貸款')}
        ${row('總資產', `<b>${money(t.assets)}</b>`, r.prev ? `${prevLabel} ${money(r.prev.totals.assets)}` : '')}
    </div>
    <div class="m-h">多出來的錢從哪來</div>
    <div class="m-card">
        ${row('本月收入', money(f.deposit), f.has_entries ? '' : '本月明細還沒記')}
        ${row('本月支出', money(f.expense), f.has_entries ? `其中家用 ${money(f.household)}` : '')}
        ${row('存下來的', f.has_entries ? delta(f.net) : '<span style="color:var(--sub)">—</span>', f.savings_rate !== null && f.savings_rate !== undefined ? `存款率 ${(f.savings_rate * 100).toFixed(0)}%` : '')}
        ${row('證券漲跌', delta(f.securities_change), f.securities_change === null ? '要有上一份月報才算得出來' : '')}
        ${row('應收增減', delta(f.receivable_change))}
    </div>
    <div class="m-h">各帳戶</div>
    <div class="m-card">
        ${(r.accounts || []).map((a) => row(a.name, `<b>${money(a.balance)}</b>${a.delta !== null && a.delta !== undefined ? `<div class="lg-sub">${delta(a.delta)}</div>` : ''}`,
            a.unfilled === null || a.unfilled === undefined ? (a.registered_this_month ? '已登記' : '這個月還沒登記') : (a.unfilled === 0 ? '已登記・明細補齊' : `還沒補的明細 ${money(a.unfilled)}`))).join('')}
        ${(r.brokers || []).map((b) => row(`證券 ${b.broker || '（未指定券商）'}`, `<b>${money(b.total)}</b>${b.delta !== null && b.delta !== undefined ? `<div class="lg-sub">${delta(b.delta)}</div>` : ''}`,
            b.plug ? `未拆明細 ${money(b.plug)}` : `${b.count} 檔`)).join('')}
    </div>
    <div class="m-h">堡壘</div>
    <div class="m-card">
        <div class="mr-big"><span class="n ${ft.tone === 'g' ? 'g' : (ft.tone === 'r' ? 'r' : 'a')}">${esc(String(ft.runway ?? '—'))}</span><span class="u">個月</span></div>
        <div class="lg-sub">必要支出 ${esc(wan(ft.need))}／月・壓力測試 ${ft.counts ? `${ft.counts.ok} 綠 ${ft.counts.warn} 黃 ${ft.counts.bad} 紅` : ''}</div>
        ${(ft.layers || []).map((L) => {
            const pct = Math.max(0, Math.min(100, Number(L.pct) || 0));
            return `<div class="ft-layer"><div class="t"><span class="nm">${L.no} ${esc(L.name)}</span><span class="v"><b>${esc(wan(L.have))}</b>${L.target ? ` / ${esc(wan(L.target))}` : ''}</span></div>
                <div class="bar"><i class="${L.no === 5 ? 'cap' : (pct < 50 ? 'low' : '')}" style="width:${pct}%"></i></div></div>`;
        }).join('')}
    </div>
    <div class="m-h">財富階梯與財富自由</div>
    <div class="m-card">
        ${row('階梯位置', `第 ${esc(String(lf.rung ?? '—'))} 階 ${esc(lf.rung_name || '')}`, lf.to_next ? `離下一階${lf.next_name || ''}還差 ${wan(lf.to_next)}` : '')}
        ${row('不工作每月可花', `<b>${money(lf.fire_allowed)}</b>`, `現在每月花 ${money(lf.fire_spend)}${lf.fire_pretax ? '・稅前' : ''}`)}
        ${row('財富自由達成率', lf.fire_ratio33 !== null && lf.fire_ratio33 !== undefined ? `<b>${Math.round(lf.fire_ratio33 * 100)}%</b>` : '—', '33 倍法則')}
        ${row('10 年後資產', wan(lf.y10_nominal), `實質購買力 ${wan(lf.y10_real)}`)}
    </div>
    <div class="m-h">財務體檢</div>
    <div class="m-card">${(r.health || []).map((h) => row(h.label, pill(h.state, h.grade), h.text)).join('')}</div>
    <div class="m-h">財務建議</div>
    ${(r.advice || []).map((a) => `<div class="m-card mr-a mr-${esc(a.level)}"><div class="t"><span class="name">${a.no}. ${esc(a.title)}</span></div>
        ${a.text ? `<div class="lg-sub" style="margin-top:4px">${esc(a.text)}</div>` : ''}${a.how ? `<div class="lg-sub mr-how">做法：${esc(a.how)}</div>` : ''}</div>`).join('') || '<div class="m-empty">沒有建議</div>'}
    <div class="m-h">待辦</div>
    <div class="m-card">${(r.todo || []).map((x) => `<div class="lg-row"><span class="k">${esc(x)}</span></div>`).join('') || '<div class="m-empty">沒有待辦</div>'}</div>
    <div class="lg-sub" style="margin:4px 0 16px">建議是規則算出來的，數字變了就變；「上月」看上一份月報。要看完整表格請到桌機。</div>`;
}
