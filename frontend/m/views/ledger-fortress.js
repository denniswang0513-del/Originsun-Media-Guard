/**
 * views/ledger-fortress.js — 士源帳本「堡壘」頁（docs/FORTRESS_PLAN.md §4）：個人的堡壘資產負債表。
 *
 * 隱藏路由 #fortress（tabbar 沒有它的鈕，從總覽頂卡點進來；上一頁回總覽）。
 * 資料＝桌機那支同一份：GET /api/v1/finance/fortress?entity=mine，一趟回全部（可撐月數／五層／預留／必要支出／壓力測試）。
 * 由上到下：大數字卡 → 五層資金（一層一列：名稱、目標規則、有多少／還差多少、水位條、帳戶名）→ 預留清單
 * （一列一筆；自動項（房貸／信用卡）標「自動」不能改；手填的點開抽屜改金額／到期日、標已付、刪）→
 * 每月必要支出（唯讀，要改去桌機）→ 壓力測試五題（收合列：色點＋題名＋一句結論；點開看算式各行）。
 * 能寫的只有預留清單；分層、目標、假設不在手機改。每次寫入後端都回整包 → 直接用它重畫，不再 GET 一次。
 * 日期一律 todayLocal()（不用 toISOString：那是 UTC，台北早上 8 點前會變昨天）。
 */
import { mfetch, money, todayLocal, toast, esc } from '../shell.js';
import { skeleton, errBox, emptyBox, pill, openSheet, closeSheet, withBusy, markStale } from '../ui.js';

export const FORTRESS_API = '/api/v1/finance/fortress?entity=mine';
const EARMARK_API = '/api/v1/finance/fortress/earmarks';

let _data = null;

// ── 數字寫法 ──
/** 元 → 「X 萬」（一位小數，.0 去掉）：大數字與說明句用；表格列仍用 money() 全位數。 */
export function wan(n) {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return '—';
    const a = Math.abs(Number(n));
    // 一億以上用「億」：階梯的門檻寫成「10,000 萬」沒人看得懂
    if (a >= 1e8) return (Number(n) / 1e8).toFixed(2).replace(/\.?0+$/, '') + ' 億';
    return (Number(n) / 10000).toFixed(1).replace(/\.0$/, '') + ' 萬';
}
/** 月數：null → '—'，否則一位小數（.0 去掉）。 */
export function months(n) {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return '—';
    return Number(n).toFixed(1).replace(/\.0$/, '');
}
/** runway.tone → 顏色 class（g／a／r；其他不上色）。 */
export const toneCls = (t) => (t === 'g' || t === 'a' || t === 'r' ? t : '');
/** 壓力測試 state → 色點 class。 */
const stateCls = (s) => ({ ok: 'g', warn: 'a', bad: 'r' })[s] || 'na';
/** 'YYYY-MM-DD' → 'MM/DD'（清單列）；空的回空。 */
const shortDate = (d) => (d ? String(d).slice(5, 10).replace('-', '/') : '');

/** 五層的一句摘要：第一個還差錢的層（由 1 往上找）＋ 五題壓力測試幾綠幾黃幾紅。總覽頂卡與這頁共用。 */
/** 五層由小到大（dir=-1 由大到小）；三個地方都要排，排法只留這一份。 */
const byNo = (d, dir = 1) => [...(d.layers || [])].sort((a, b) => (a.no - b.no) * dir);

export function summaryLine(d) {
    const layers = byNo(d);
    const tests = d.tests || [];
    const short = layers.find(l => Number(l.gap) > 0);
    const first = short ? `第 ${short.no} 層還差 ${wan(short.gap)}` : '五層都到位';
    const cnt = { ok: 0, warn: 0, bad: 0 };
    for (const t of tests) if (t.state in cnt) cnt[t.state] += 1;
    const parts = [[cnt.ok, '綠'], [cnt.warn, '黃'], [cnt.bad, '紅']].filter(x => x[0] > 0).map(x => `${x[0]} ${x[1]}`);
    const CN = { 5: '五題', 6: '六題' };
    const line = tests.length ? `${CN[tests.length] || tests.length + ' 題'}壓力測試 ${parts.join(' ')}` : '';
    return [first, line].filter(Boolean).join(' · ');
}

/** 大數字（可撐月數）那一行：總覽頂卡與這頁同一個長相。 */
export function bigHtml(d) {
    const r = d.runway || {};
    return `<div class="ft-big"><span class="n ${toneCls(r.tone)}">${esc(months(r.months))}</span><span class="u">個月</span></div>`;
}

/** 五格迷你水位（層 1..5 由左到右；第 5 層主色、不到一半的黃）。 */
export function miniHtml(d) {
    const layers = byNo(d);
    const cells = layers.map(l => {
        const pct = Math.max(0, Math.min(100, Number(l.pct) || 0));
        const cls = l.no === 5 ? 'cap' : (pct < 50 ? 'low' : '');
        return `<div class="c"><i class="${cls}" style="height:${pct}%"></i><b>${l.no}</b></div>`;
    }).join('');
    const labels = layers.map(l => `<span>${esc(String(l.name || '').slice(0, 2))}</span>`).join('');
    return `<div class="ft-mini">${cells}</div><div class="ft-mini-l">${labels}</div>`;
}

/** 總覽頂卡（views/ledger-overview.js 用；點卡 → #fortress 由那邊接）。 */
export function fortressCardHtml(d) {
    const need = (d.monthly_need || {}).used, cash = d.cash || {};
    return `<div class="m-card ft-card tap" data-go="fortress" role="button">
        <div class="lg-sub" style="margin:0 0 2px">事情不照計畫走，家裡還能撐</div>
        ${bigHtml(d)}
        <div class="lg-sub">第 1 到 3 層 <span class="num">${esc(wan(cash.l1_3))}</span> − 預留 <span class="num">${esc(wan(d.earmark_total))}</span> ÷ 必要支出 <span class="num">${esc(wan(need))}</span></div>
        ${miniHtml(d)}
        <div class="ft-tap"><span>${esc(summaryLine(d))}</span><span>看堡壘 ›</span></div>
    </div>`;
}

// ── 這一頁 ──
export async function render(host, { first }) {
    if (first) {
        host.innerHTML = skeleton(4);
        host.addEventListener('click', (ev) => {
            if (ev.target.closest('#ft-add')) { openAddSheet(host); return; }
            const r = ev.target.closest('[data-earmark]');
            if (!r || !_data) return;
            const e = (_data.earmarks || []).find(x => String(x.id) === r.dataset.earmark);
            if (e && (e.source || 'manual') === 'manual') openEditSheet(host, e);
        });
    }
    // 【重要】這頁每次進來都重抓，不吃 60 秒快取：它的數字是從收支明細推出來的（帳戶餘額、生活支出），
    //    而記一筆收支的 ledger-cash／ledger-household 只 markStale 自己那幾頁、不知道有堡壘。
    //    進來的路徑只有「總覽頂卡點一下」，重抓一次不貴，卻能保證不會跟剛剛那張卡對不起來。
    await load(host);
}

async function load(host) {
    try {
        await apply(host, await mfetch(FORTRESS_API));
    } catch (e) {
        host.innerHTML = errBox(e);
        markStale('fortress');     // 失敗不要被當成「剛載過」：退出去再進來要能重試
    }
}

/** 拿到整包（GET 或寫入的回應）就重畫；回應長得不像整包（沒有 layers）就再 GET 一次。 */
async function apply(host, d) {
    if (!d || !Array.isArray(d.layers)) d = await mfetch(FORTRESS_API);
    _data = d;
    host.innerHTML = draw(d);
}

function draw(d) {
    const r = d.runway || {}, cash = d.cash || {}, need = d.monthly_need || {};
    return `
        ${(d.warnings || []).map((w) => `<div class="m-err ft-warn">${esc(w)}</div>`).join('')}
        <div class="m-card">
            ${bigHtml(d)}
            <div class="lg-sub">加上第 4 層機會資金可撐 <span class="num">${esc(months(r.with_l4))} 個月</span>・可動用現金 <span class="num">${esc(wan(cash.l1_4))}</span></div>
        </div>
        <div class="m-h">五層資金</div>
        <div class="m-card">${byNo(d, -1).map(layerHtml).join('') || emptyBox('還沒有分層')}</div>
        <div class="m-h">預留清單 · ${esc(wan(d.earmark_total))}</div>
        <div class="m-card">
            ${(d.earmarks || []).map(earmarkHtml).join('') || '<div class="m-empty" style="padding:10px 0">還沒有預留</div>'}
            <button type="button" class="m-btn-primary" id="ft-add" style="margin-top:10px">＋記一筆預留</button>
        </div>
        <div class="m-h">每月必要支出</div>
        <div class="m-card">
            <div class="lg-row"><span class="k">${need.override !== null && need.override !== undefined ? '你認定的數字' : '自動算的'}<div class="lg-sub">自動算是 ${money(need.auto)}（${need.sample_months ? `近 ${need.sample_months} 個月` : '近半年'}生活支出的月平均）</div></span>
                <span class="v amt">${money(need.used)}</span></div>
            <div class="lg-sub" style="margin-top:6px">要改請到桌機的堡壘分頁</div>
        </div>
        ${ladderHtml(d)}
        ${growthHtml(d)}
        <div class="m-h">壓力測試</div>
        <div class="m-card ft-tests">${(d.tests || []).map(testHtml).join('') || emptyBox('還沒有壓力測試')}</div>`;
}

/** 財富階梯：你在第幾階、不用想能花多少、距離下一階、贏過台灣多少家庭。唯讀，規劃欄位在桌機。 */
function ladderHtml(d) {
    const L = d.ladder;
    if (!L || !L.rungs) return '';
    const p = L.plan || {}, f = L.focus || {};
    const bars = [...L.rungs].map(r => {
        const w = r.you ? Math.max(6, Math.min(100, Number(L.pct_in_rung) || 0)) : 0;
        return `<div class="ft-lad${r.you ? ' you' : ''}">
            <div class="t"><span class="nm">${esc(String(r.no))} ${esc(r.name)}</span><span class="v">${esc(wan(r.floor))}${r.ceiling == null ? ' 以上' : ' – ' + wan(r.ceiling)}</span></div>
            ${r.you ? `<div class="bar"><i style="width:${w}%"></i></div><div class="acc">你 ${esc(wan(L.net_worth))}・這階走了 ${esc(String(L.pct_in_rung))}%・不用想就能花 ${esc(money(L.free_amount))}</div>` : ''}
        </div>`;
    }).join('');
    const plan = p.years_at_current == null
        ? `照現在的速度，${esc(String(p.years))} 年內到不了 ${esc(wan(p.target))}`
        : `照現在的速度 ${esc(String(p.years_at_current))} 年到 ${esc(wan(p.target))}（你設 ${esc(String(p.years))} 年）`;
    return `<div class="m-h">財富階梯</div>
        <div class="m-card">
            ${bars}
            <div class="lg-sub" style="margin-top:8px">${esc(L.percentile || '')}</div>
            <div class="lg-row"><span class="k">距離第 ${esc(String(L.rung + 1))} 階</span><span class="v num">${esc(L.to_next == null ? '—' : wan(L.to_next))}</span></div>
            <div class="lg-row"><span class="k">資產一年自己長<div class="lg-sub">你一年存 ${esc(wan(f.added))}</div></span><span class="v num">${esc(wan(f.passive))}</span></div>
            <div class="lg-sub" style="margin-top:6px">${plan}・目標與門檻到桌機改</div>
        </div>`;
}


/** 資產預期成長：第 5 層複利資本往後推（名目／今天的購買力）。假設要改到桌機。 */
function growthHtml(d) {
    const g = d.projection || {};
    const rows = g.rows || [];
    if (!rows.length) return '';
    const pc = (v) => Math.round((Number(v) || 0) * 1000) / 10;
    const list = rows.map(r => `<div class="lg-row"><span class="k">${esc(String(r.year))} 年後<div class="lg-sub">今天的購買力 ${esc(wan(r.real))}</div></span>
        <span class="v num">${esc(wan(r.nominal))}</span></div>`).join('');
    return `<div class="m-h">資產預期成長</div>
        <div class="m-card">
            <div class="lg-sub" style="margin-bottom:6px">起點是現在的第 5 層 ${esc(wan(g.base))}・年報酬 ${esc(String(pc(g.rate)))}%・通膨 ${esc(String(pc(g.inflation)))}%${Number(g.annual_add) ? `・每年再投入 ${esc(wan(g.annual_add))}` : ''}</div>
            ${list}
            <div class="lg-sub" style="margin-top:6px">報酬與通膨都是假設，不是保證；要改到桌機的堡壘分頁</div>
        </div>`;
}


function layerHtml(l) {
    const pct = Math.max(0, Math.min(100, Number(l.pct) || 0));
    const cls = l.no === 5 ? 'cap' : (pct < 50 ? 'low' : '');
    const hasTarget = l.target !== null && l.target !== undefined;
    let diff = '';
    if (hasTarget && Number(l.gap) > 0) diff = ` <span class="a">還差 ${esc(wan(l.gap))}</span>`;
    else if (hasTarget && Number(l.gap) < 0) diff = ` 多 ${esc(wan(-Number(l.gap)))}`;
    const rule = hasTarget ? `目標 ${wan(l.target)}` : (l.target_rule || '');
    const acc = (l.accounts || []).map(a => a.name).filter(Boolean);
    return `<div class="ft-layer">
        <div class="t"><span class="nm">${l.no} ${esc(l.name || '')}<span>${esc(rule)}</span></span><span class="v"><b>${esc(wan(l.have))}</b>${diff}</span></div>
        <div class="bar"><i class="${cls}" style="width:${pct}%"></i></div>
        <div class="acc">${acc.length ? esc(acc.join('、')) : '沒有帳戶歸在這層'}</div>
    </div>`;
}

function earmarkHtml(e) {
    const auto = (e.source || 'manual') !== 'manual';
    const sub = [shortDate(e.due_date), auto ? '自動' : ''].filter(Boolean).join('・');
    return `<div class="lg-row${auto ? '' : ' ft-tap-row'}"${auto ? '' : ` data-earmark="${esc(e.id)}"`}>
        <span class="k">${esc(e.label || '')}${e.paid ? ' ' + pill('已付', 'ok') : ''}${sub ? `<div class="lg-sub">${esc(sub)}</div>` : ''}</span>
        <span class="v amt${e.paid ? ' ft-paid' : ''}">${money(e.amount)}</span></div>`;
}

const lineVal = (v, unit) => (unit === 'months' ? `${months(v)} 個月` : money(v));

function testHtml(t) {
    const lines = (t.lines || []).map(([k, v, u]) => `<div class="line"><span>${esc(k)}</span><span>${esc(lineVal(v, u))}</span></div>`).join('');
    return `<details class="ft-test">
        <summary><span class="dot ${stateCls(t.state)}"></span><span class="st">${esc(t.title || '')}<small>${esc(t.verdict || t.question || '')}</small></span><span class="chev">›</span></summary>
        <div class="det">${t.assume ? `<div class="assume">${esc(t.assume)}</div>` : ''}${lines}${t.verdict ? `<div class="vd">${esc(t.verdict)}</div>` : ''}</div>
    </details>`;
}

// ── 預留清單的寫入（抽屜）──
const formHtml = (p, e = {}) => `
    <div class="m-form">
        <label class="req">項目</label><input id="${p}-label" value="${esc(e.label || '')}" placeholder="例：年繳保費、綜所稅">
        <label class="req">金額</label><input id="${p}-amount" type="number" inputmode="decimal" min="0" step="1" value="${esc(e.amount ?? '')}">
        <label>到期日</label><input id="${p}-due" type="date" value="${esc(e.due_date || '')}">
        <label>備註</label><textarea id="${p}-note">${esc(e.note || '')}</textarea>
    </div>`;

function readForm(p) {
    const v = (id) => (document.getElementById(`${p}-${id}`) || {}).value;
    const label = String(v('label') || '').trim();
    const amount = Math.round(Number(v('amount')));      // 欄位是整數；送小數會換來後端一句看不懂的 422
    if (!label) throw new Error('項目要填');
    if (!Number.isFinite(amount) || amount <= 0) throw new Error('金額要大於 0');
    return { label, amount, due_date: v('due') || '', note: String(v('note') || '').trim() };
}

/** 寫入完成：整包重畫、告訴總覽下次要重抓。 */
async function done(host, r, msg = '已記下') {
    await apply(host, r);
    markStale('overview');
    toast(msg);
    closeSheet();
}

/** 抽屜裡的寫入：按鈕忙碌狀態＋一層 try/catch（四個鈕本來各寫一份一模一樣的）。
 *  readForm 的丟出也一起接住，不必再包第二層。 */
const sheetWrite = (btn, fn) => withBusy(btn, async () => {
    try { await fn(); } catch (err) { toast(err.message, 'err'); }
});

function openAddSheet(host) {
    const body = openSheet(`<div class="ttl">記一筆預留</div>${formHtml('fa', { due_date: todayLocal() })}
        <button type="button" class="m-btn-primary" id="fa-save">記下</button>`);
    body.querySelector('#fa-save').addEventListener('click', (ev) => sheetWrite(ev.currentTarget, async () => {
        const r = await mfetch(`${EARMARK_API}?entity=mine`, { method: 'POST', body: readForm('fa') });
        await done(host, r);
    }));
}

function openEditSheet(host, e) {
    const url = `${EARMARK_API}/${encodeURIComponent(e.id)}?entity=mine`;
    const body = openSheet(`<div class="ttl">${esc(e.label || '這一筆預留')}</div>${formHtml('fe', e)}
        <div class="m-actions">
            <button type="button" class="m-btn" id="fe-paid">${e.paid ? '改回未付' : '標為已付'}</button>
            <button type="button" class="m-btn danger" id="fe-del">刪除</button>
        </div>
        <button type="button" class="m-btn-primary" id="fe-save" style="margin-top:8px">儲存</button>`);
    body.querySelector('#fe-save').addEventListener('click', (ev) => sheetWrite(ev.currentTarget, async () => {
        const next = readForm('fe');
        // 只送有動的鍵（後端 PUT 只改有給的欄位）
        const was = { label: e.label || '', amount: Number(e.amount), due_date: e.due_date || '', note: e.note || '' };
        const diff = {};
        for (const k of Object.keys(was)) if (String(next[k]) !== String(was[k])) diff[k] = next[k];
        if (!Object.keys(diff).length) { toast('沒有改動'); closeSheet(); return; }
        const r = await mfetch(url, { method: 'PUT', body: diff });
        await done(host, r, '已更新');
    }));
    body.querySelector('#fe-paid').addEventListener('click', (ev) => sheetWrite(ev.currentTarget, async () => {
        const r = await mfetch(url, { method: 'PUT', body: { paid: !e.paid } });
        await done(host, r, e.paid ? '已改回未付' : '已標為已付');
    }));
    body.querySelector('#fe-del').addEventListener('click', (ev) => sheetWrite(ev.currentTarget, async () => {
        if (!window.confirm(`刪除預留「${e.label || '這一筆'}」？`)) return;
        const r = await mfetch(url, { method: 'DELETE' });
        await done(host, r, '已刪除');
    }));
}
