/**
 * fortress.js — 堡壘（私帳；docs/FORTRESS_PLAN.md §3）。
 *
 * 個人版「堡壘資產負債表」：五層資金、可撐月數、預留清單、壓力測試。
 * 數字全部由後端 GET /finance/fortress 一趟算好（core/fortress_logic.py），
 * 這頁只負責畫；每次寫入（設定／預留）都拿回整份 payload 重畫，前端不自己算。
 *
 * 只在私帳出現（finance.html 的 .fin-nav-mine-only），所以固定走 finFetchMine。
 * 版面照桌機 demo v3（淺色只是提案），配色換成 CRM 深色殼那組。
 */
import { finFetchMine, finSubviewBoot, esc, fmtNum, finToast } from '../fin-utils.js';

let _c = null;
let _isCurrent = () => true;
let _d = null;
let _warOpen = false;
let _layerTimer = null;

const CSS_ID = 'fortress-css';
const KIND_LABEL = { bank: '銀行', cash: '現金', holding: '證券', card: '信用卡' };
const STATE_PILL = { ok: '撐得住', warn: '撐得住，但很緊', bad: '會被迫', na: '資料不足' };
const FLAG_KEYS = ['physical', 'offshore', 'usd'];

// ── 格式 ────────────────────────────────────────────────────
/** 元 → 「12.5 萬」（負數前面加 −） */
function fmtWan(n) {
    if (n == null || isNaN(n)) return '—';
    const v = Math.round(Math.abs(n) / 1000) / 10;
    return (n < 0 ? '−' : '') + v.toLocaleString('zh-TW', { maximumFractionDigits: 1 }) + ' 萬';
}
/** 月數 → 「9.1 個月」；null → — */
function fmtMonths(m) {
    if (m == null || isNaN(m)) return '—';
    return (Math.round(m * 10) / 10).toLocaleString('zh-TW') + ' 個月';
}
/** 'YYYY-MM-DD' → 'MM/DD'；空 → '本期' */
function fmtDue(s) {
    if (!s) return '本期';
    return s.slice(5, 7) + '/' + s.slice(8, 10);
}
function fmtLine(l) {
    return l[2] === 'months' ? fmtMonths(l[1]) : fmtWan(l[1]);
}

// ── 樣式（只注入一次） ────────────────────────────────────────
function _injectCss() {
    if (document.getElementById(CSS_ID)) return;
    const st = document.createElement('style');
    st.id = CSS_ID;
    st.textContent = `
.ft { color: #e0e0e0; font-size: 14px; line-height: 1.6; max-width: 1100px; }
.ft * { box-sizing: border-box; }
.ft .ft-num { font-variant-numeric: tabular-nums; font-family: ui-monospace, Consolas, "Courier New", monospace; }
.ft .ft-eyebrow { font-size: 11px; letter-spacing: .18em; color: #6b7280; text-transform: uppercase; font-family: ui-monospace, Consolas, monospace; }
.ft .ft-top { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; flex-wrap: wrap; padding: 0 0 10px; border-bottom: 1px solid #3a3a3a; }
.ft .ft-top h2 { margin: 0; font-size: 20px; color: #eee; }
.ft .ft-top .ft-where { color: #9ca3af; font-size: 12.5px; }
.ft .ft-top .ft-where b { color: #e0e0e0; font-weight: 500; }

.ft .ft-hero { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(0, 1.5fr); gap: 32px; align-items: end; padding: 26px 0 22px; }
.ft .ft-lead { font-size: 16px; color: #9ca3af; font-weight: 500; }
.ft .ft-big { display: flex; align-items: baseline; gap: 10px; margin: 6px 0 4px; }
.ft .ft-big .n { font-weight: 300; font-size: clamp(56px, 8vw, 96px); line-height: .95; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.ft .ft-big .u { font-size: 20px; color: #9ca3af; }
.ft .tone-g { color: #86efac; } .ft .tone-a { color: #fbbf24; } .ft .tone-r { color: #f87171; } .ft .tone-na { color: #6b7280; }
.ft .ft-formula { font-size: 12.5px; color: #9ca3af; margin-top: 2px; }
.ft .ft-formula .ft-num { color: #e0e0e0; }
.ft .ft-alt { margin-top: 14px; padding-top: 12px; border-top: 1px dashed #3a3a3a; font-size: 13px; color: #9ca3af; display: flex; flex-direction: column; gap: 4px; }
.ft .ft-alt .ft-num { color: #e0e0e0; }
.ft .ft-motto { margin-top: 18px; font-size: 13.5px; color: #9ca3af; line-height: 1.7; }

.ft .ft-sky { font-size: 11.5px; color: #6b7280; text-align: right; margin-bottom: 8px; font-family: ui-monospace, Consolas, monospace; letter-spacing: .08em; }
.ft .course { position: relative; margin: 0 auto 3px; height: 58px; border: 1px solid #444; color: #e0e0e0; background:
    repeating-linear-gradient(0deg, #444 0 1px, transparent 1px 14px),
    repeating-linear-gradient(90deg, #444 0 1px, transparent 1px 34px), #3a3a3a; background-position: 0 0, 17px 0, 0 0; }
.ft .course .fill { position: absolute; inset: 0; width: 0; background:
    repeating-linear-gradient(0deg, rgba(0,0,0,.18) 0 1px, transparent 1px 14px),
    repeating-linear-gradient(90deg, rgba(0,0,0,.18) 0 1px, transparent 1px 34px), #86efac; background-position: 0 0, 17px 0; transition: width .5s cubic-bezier(.2,.7,.2,1); }
.ft .course .fill.low { background-color: #fbbf24; }
.ft .course .fill.cap { background-color: #3b4d6b; }
.ft .course .lab { position: absolute; inset: 0; display: flex; justify-content: space-between; align-items: center; padding: 0 14px; gap: 10px; }
.ft .course .lab .l { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.ft .course .lab .no { font-size: 20px; font-weight: 500; opacity: .55; }
.ft .course .lab .nm { font-weight: 700; font-size: 15px; white-space: nowrap; }
.ft .course .lab .acc { font-size: 11.5px; opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ft .course .lab .r { text-align: right; white-space: nowrap; }
.ft .course .lab .r .have { font-size: 15px; font-weight: 500; }
.ft .course .lab .r .tg { font-size: 11px; opacity: .8; }
.ft .course.on .lab { color: #0f172a; }
.ft .course.on.cap .lab { color: #e0e0e0; }
.ft .course.on .lab .no { opacity: .8; }
.ft .course.on .lab .r { background: rgba(0,0,0,.28); padding: 3px 9px; border-radius: 3px; }
.ft .course.on.cap .lab .r { background: rgba(0,0,0,.35); }
.ft .course.w5 { width: 62%; } .ft .course.w4 { width: 72%; } .ft .course.w3 { width: 82%; } .ft .course.w2 { width: 91%; } .ft .course.w1 { width: 100%; }
.ft .course.w5::before { content: ""; position: absolute; left: -1px; right: -1px; top: -9px; height: 9px;
    background: repeating-linear-gradient(90deg, #3a3a3a 0 18px, transparent 18px 30px); border-top: 1px solid #444; }
.ft .ft-ground { height: 6px; background: #444; margin-top: 4px; border-radius: 0 0 3px 3px; }
.ft .ft-legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: #9ca3af; margin-top: 10px; }
.ft .ft-legend i { display: inline-block; width: 12px; height: 12px; vertical-align: -2px; margin-right: 5px; border: 1px solid #444; }
.ft .ft-legend .k1 i { background: #86efac; } .ft .ft-legend .k2 i { background: #fbbf24; } .ft .ft-legend .k3 i { background: #3a3a3a; } .ft .ft-legend .k4 i { background: #3b4d6b; }

.ft .ft-sec { display: flex; align-items: baseline; gap: 14px; margin: 30px 0 12px; flex-wrap: wrap; }
.ft .ft-sec h3 { font-size: 17px; margin: 0; color: #eee; }
.ft .ft-sec .why { font-size: 12.5px; color: #9ca3af; }
.ft .ft-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; }

.ft .ft-strip { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 6px; padding: 14px 16px 10px; }
.ft .ft-months { display: grid; grid-template-columns: repeat(12, 1fr); gap: 2px; align-items: end; height: 76px; border-bottom: 1px solid #3a3a3a; }
.ft .ft-months .m { position: relative; height: 100%; }
.ft .ft-months .m .pin { position: absolute; left: 50%; bottom: 0; transform: translateX(-50%); width: 2px; background: #86efac; }
.ft .ft-months .m .pin.auto { background: #5b7cad; }
.ft .ft-months .m .amt { position: absolute; left: 50%; transform: translateX(-50%); font-size: 11px; white-space: nowrap; color: #e0e0e0; font-variant-numeric: tabular-nums; }
.ft .ft-months .m .amt small { display: block; color: #9ca3af; font-size: 10px; text-align: center; }
.ft .ft-mlabels { display: grid; grid-template-columns: repeat(12, 1fr); font-size: 10.5px; color: #6b7280; text-align: center; padding-top: 6px; }

.ft .ft-tblwrap { overflow-x: auto; }
.ft .ft-tbl { width: 100%; border-collapse: collapse; background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 6px; overflow: hidden; }
.ft .ft-tbl th, .ft .ft-tbl td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #3a3a3a; font-size: 13px; vertical-align: middle; }
.ft .ft-tbl th { font-weight: 500; font-size: 11.5px; color: #9ca3af; background: #242424; letter-spacing: .06em; white-space: nowrap; }
.ft .ft-tbl tbody tr:last-child td { border-bottom: none; }
.ft .ft-tbl .n { text-align: right; white-space: nowrap; }
.ft .ft-tbl tfoot td { background: #242424; font-weight: 500; border-top: 1px solid #3a3a3a; }
.ft .ft-tbl tr.paid td { color: #6b7280; text-decoration: line-through; }
.ft .ft-tbl tr.paid td.ops { text-decoration: none; }
.ft .ft-src { font-size: 11px; color: #6b7280; white-space: nowrap; }
.ft .ft-sub { color: #9ca3af; }
.ft .ft-neg { color: #f87171; }
.ft .ft-tbl input.crm-input, .ft .ft-field input.crm-input { width: 108px; text-align: right; padding: 4px 7px; font-variant-numeric: tabular-nums; }
.ft .ft-tbl select.crm-input { width: auto; padding: 4px 6px; }
.ft .ft-tbl input[type=checkbox] { accent-color: #3b82f6; width: 15px; height: 15px; vertical-align: -2px; }
.ft .ft-chk { display: inline-flex; align-items: center; gap: 4px; margin-right: 10px; white-space: nowrap; font-size: 12px; color: #9ca3af; }
.ft .ft-ops { white-space: nowrap; }
.ft .ft-ops .crm-btn { margin-right: 4px; }

.ft .ft-field { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 6px; padding: 14px 16px; display: flex; justify-content: space-between; gap: 14px; align-items: center; flex-wrap: wrap; }
.ft .ft-field .t { font-weight: 700; color: #eee; }
.ft .ft-field .s { font-size: 12px; color: #9ca3af; }
.ft .ft-field .ctl { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ft .ft-form { background: #242424; border: 1px solid #3b82f6; border-radius: 6px; padding: 10px 12px; margin-top: 12px; display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.ft .ft-form label { color: #9ca3af; font-size: 11px; display: flex; flex-direction: column; gap: 3px; }
.ft .ft-form .crm-input { width: 130px; padding: 5px 8px; }

.ft .ft-tests { display: grid; grid-template-columns: repeat(5, 1fr); background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 6px; overflow: hidden; }
.ft .ft-test { padding: 14px; display: flex; flex-direction: column; gap: 7px; border-top: 4px solid #86efac; border-right: 1px solid #3a3a3a; min-width: 0; }
.ft .ft-test:last-child { border-right: none; }
.ft .ft-test.bad { border-top-color: #f87171; } .ft .ft-test.warn { border-top-color: #fbbf24; } .ft .ft-test.na { border-top-color: #555; }
.ft .ft-test .h { display: flex; flex-direction: column; gap: 5px; align-items: flex-start; font-weight: 700; font-size: 14.5px; line-height: 1.35; color: #eee; }
.ft .ft-test .q { font-size: 12px; color: #9ca3af; }
.ft .ft-test .line { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: #9ca3af; }
.ft .ft-test .line span:last-child { color: #e0e0e0; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ft .ft-test .assume { font-size: 11.5px; color: #6b7280; line-height: 1.5; }
.ft .ft-test .verdict { margin-top: auto; padding-top: 9px; border-top: 1px dashed #3a3a3a; font-size: 12.5px; color: #e0e0e0; }
.ft .ft-war { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11.5px; color: #9ca3af; margin-top: 4px; }
.ft .ft-war label { display: flex; flex-direction: column; gap: 2px; }
.ft .ft-war .crm-input { padding: 3px 6px; font-size: 12px; width: 100%; }
.ft .ft-war .full { grid-column: 1 / -1; display: flex; gap: 6px; }
.ft .pill { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; font-weight: 500; }
.ft .pill.ok { color: #86efac; background: rgba(134,239,172,.14); } .ft .pill.warn { color: #fbbf24; background: rgba(251,191,36,.14); }
.ft .pill.bad { color: #f87171; background: rgba(248,113,113,.14); } .ft .pill.na { color: #9ca3af; background: rgba(156,163,175,.14); }
.ft .ft-foot { margin-top: 28px; font-size: 12px; color: #6b7280; border-top: 1px solid #3a3a3a; padding-top: 12px; line-height: 1.7; }
.ft .ft-foot b { color: #9ca3af; font-weight: 500; }
.ft .ft-empty { color: #6b7280; padding: 12px; }

@media (max-width: 1000px) { .ft .ft-tests { grid-template-columns: repeat(3, 1fr); } .ft .ft-test { border-bottom: 1px solid #3a3a3a; } .ft .ft-test:nth-child(3n) { border-right: none; } }
@media (max-width: 760px) {
    .ft .ft-hero { grid-template-columns: 1fr; gap: 22px; }
    .ft .ft-grid2 { grid-template-columns: 1fr; }
    .ft .course .lab .acc, .ft .course .lab .r .tg, .ft .course .lab .no { display: none; }
    .ft .course { height: 46px; }
    .ft .course .lab { padding: 0 10px; }
    .ft .course .lab .nm { font-size: 13px; }
    .ft .course .lab .r .have { font-size: 13px; }
    .ft .course.on .lab .r { padding: 2px 6px; }
    .ft .course.w5 { width: 80%; } .ft .course.w4 { width: 85%; } .ft .course.w3 { width: 90%; } .ft .course.w2 { width: 95%; }
    .ft .ft-months .m .amt small { display: none; }
    .ft .ft-tests { grid-template-columns: 1fr; } .ft .ft-test { border-right: none; }
}
`;
    document.head.appendChild(st);
}

// ── 入口 ────────────────────────────────────────────────────
export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _injectCss();
    const r = await finSubviewBoot(_c, {
        title: '堡壘', isCurrent: _isCurrent,
        fetchers: [() => finFetchMine('/fortress')],
        retry: 'window._finFortress.reload()',
    });
    if (!r) return;
    _d = r[0];
    _render();
}

/** 找到真正在捲的那層（主系統是 body、/my-ledger.html 是固定高的殼），重畫後把位置放回去 */
function _scrollKeeper() {
    let el = _c;
    while (el && el !== document.body && !(el.scrollTop > 0 && el.scrollHeight > el.clientHeight)) el = el.parentElement;
    const y = el && el !== document.body ? el.scrollTop : window.scrollY;
    return () => { if (el && el !== document.body) el.scrollTop = y; else window.scrollTo(0, y); };
}

function _render() {
    const d = _d;
    const restore = _scrollKeeper();
    _c.innerHTML = `<div class="ft">
        ${_hero(d)}
        <div class="ft-grid2">
            <div>${_earmarks(d)}</div>
            <div>${_monthly(d)}${_targets(d)}</div>
        </div>
        <div class="ft-sec"><h3>壓力測試</h3><span class="why">每季看一次，五題都用上面的數字自動回答；第 5 題的假設可以自己調</span></div>
        <div class="ft-tests">${(d.tests || []).map((t, i) => _tower(t, i, d)).join('')}</div>
        <div class="ft-sec"><h3>帳戶分層</h3><span class="why">一次設好就不用再動；證券預設第 5 層。改了會自動儲存</span></div>
        ${_accounts(d)}
        <div class="ft-foot">
            <b>資料從哪來：</b>帳戶餘額是私帳銀行頁現有的數字；證券現值是資產頁現有的數字；信用卡欠款、房貸下一期來自現有的卡帳與貸款表；
            必要支出是收支明細裡「固定支出」與「家用」近 6 個月的平均。新增的只有「帳戶分層」「預留清單」「目標倍數」三份設定，不動任何金額規則。
        </div>
    </div>`;
    _c.querySelector('#ft-acct-body')?.addEventListener('change', _onLayerChange);
    _c.querySelectorAll('.ft-target-mult').forEach((el) => el.addEventListener('change', () => _ff.saveTargets()));
    restore();
}

// ── 主題：可撐月數 ＋ 堡壘剖面 ───────────────────────────────
function _hero(d) {
    const rw = d.runway || {};
    const mn = d.monthly_need || {};
    const big = rw.months == null ? '—' : (Math.round(rw.months * 10) / 10).toLocaleString('zh-TW');
    const courses = (d.layers || []).map((L) => {
        const cap = L.target == null;
        const pct = cap ? 100 : Math.max(0, Math.min(100, Number(L.pct) || 0));
        const cls = cap ? 'cap' : (pct < 50 ? 'low' : '');
        const on = pct >= 55;
        const accs = (L.accounts || []).map((a) => a.name).join('、');
        return `<div class="course w${L.no}${on ? ' on' : ''}${cap ? ' cap' : ''}">
            <div class="fill ${cls}" style="width:${pct}%"></div>
            <div class="lab"><div class="l"><span class="no">${L.no}</span><span class="nm">${esc(L.name)}</span>
                <span class="acc" title="${esc(accs)}">${accs ? esc(accs) : '還沒有帳戶標到這層'}</span></div>
            <div class="r"><div class="have ft-num">${fmtWan(L.have)}</div><div class="tg">${cap ? '沒有上限' : '目標 ' + fmtWan(L.target)}</div></div></div>
        </div>`;
    }).join('');
    return `
    <div class="ft-top">
        <h2>堡壘</h2>
        <span class="ft-where">財務 · <b>私帳</b> · 只有你看得到 · 更新到 ${esc(d.today || '')}</span>
    </div>
    <div class="ft-hero">
        <div>
            <div class="ft-eyebrow">Liquidity runway</div>
            <div class="ft-lead">事情不照計畫走的時候，家裡還能撐</div>
            <div class="ft-big"><span class="n tone-${esc(rw.tone || 'na')}">${big}</span><span class="u">個月</span></div>
            <div class="ft-formula">（第 1 到 3 層現金 <span class="ft-num">${fmtWan((d.cash || {}).l1_3)}</span> − 預留 <span class="ft-num">${fmtWan(d.earmark_total)}</span>）÷ 每月必要支出 <span class="ft-num">${fmtWan(mn.used)}</span></div>
            <div class="ft-alt">
                <span>加上第 4 層機會資金，可撐 <span class="ft-num">${fmtMonths(rw.with_l4)}</span></span>
                <span>可動用現金（第 1 到 4 層）<span class="ft-num">${fmtWan((d.cash || {}).l1_4)}</span></span>
            </div>
            <div class="ft-motto">真正的堡壘，不是永遠不出事，<br>而是出事時仍不用被迫做出錯誤決策。</div>
        </div>
        <div aria-label="五層資金結構">
            <div class="ft-sky">五層資金 · 由上往下先填滿 1 到 4 層</div>
            <div>${courses}</div>
            <div class="ft-ground"></div>
            <div class="ft-legend"><span class="k1"><i></i>已到位</span><span class="k2"><i></i>不到一半</span><span class="k3"><i></i>還差的</span><span class="k4"><i></i>複利資本，沒有上限</span></div>
        </div>
    </div>`;
}

// ── 預留清單：12 個月時間帶 ＋ 表 ＋ 新增列 ────────────────────
function _monthIndex(today, due) {
    // 今天所在的月＝第 0 格；沒有到期日（卡帳）也放第 0 格；逾期的一律擠到第 0 格
    if (!due) return 0;
    const ty = +today.slice(0, 4), tm = +today.slice(5, 7);
    const dy = +due.slice(0, 4), dm = +due.slice(5, 7);
    if (!dy || !dm) return 0;
    return Math.max(0, (dy - ty) * 12 + (dm - tm));
}

function _earmarks(d) {
    const today = d.today || new Date().toISOString().slice(0, 10);
    const tm = +today.slice(5, 7) || 1;
    const labels = Array.from({ length: 12 }, (_, i) => `${((tm - 1 + i) % 12) + 1}月`);
    const byM = {};
    let beyond = 0;
    (d.earmarks || []).filter((e) => !e.paid).forEach((e) => {
        const m = _monthIndex(today, e.due_date);
        if (m > 11) { beyond += e.amount; return; }
        byM[m] = byM[m] || { amt: 0, auto: false, n: 0 };
        byM[m].amt += e.amount; byM[m].n++;
        byM[m].auto = byM[m].auto || e.source !== 'manual';
    });
    const maxAmt = Math.max(1, ...Object.values(byM).map((x) => x.amt));
    const months = labels.map((_, m) => {
        const x = byM[m];
        if (!x) return '<div class="m"></div>';
        const h = Math.max(10, x.amt / maxAmt * 46);
        return `<div class="m"><span class="amt" style="bottom:${h + 4}px">${fmtWan(x.amt)}<small>${x.n > 1 ? x.n + ' 筆' : (x.auto ? '自動' : '手動')}</small></span><span class="pin${x.auto ? ' auto' : ''}" style="height:${h}px"></span></div>`;
    }).join('');
    const srcLabel = (e) => e.source === 'loan' ? '自動 · 貸款表' : e.source === 'card' ? '自動 · 卡帳' : '手動';
    const rows = (d.earmarks || []).map((e) => {
        const manual = e.source === 'manual';
        const ops = manual ? `<span class="ft-ops">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finFortress.togglePaid('${esc(e.id)}', ${e.paid ? 'false' : 'true'}, this)">${e.paid ? '未付' : '已付'}</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._finFortress.delEarmark('${esc(e.id)}', this)">刪除</button></span>`
            : '<span class="ft-src">自動</span>';
        return `<tr class="${e.paid ? 'paid' : ''}">
            <td>${esc(e.label)}${e.note ? `<div class="ft-src">${esc(e.note)}</div>` : ''}</td>
            <td class="ft-num">${fmtDue(e.due_date)}</td>
            <td><span class="ft-src">${srcLabel(e)}</span></td>
            <td class="n ft-num">$${fmtNum(e.amount)}</td>
            <td class="ops">${ops}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="ft-empty">還沒有預留項目</td></tr>';
    return `
    <div class="ft-sec"><h3>預留清單</h3><span class="why">已經知道要付、還沒付的錢</span></div>
    <div class="ft-strip">
        <div class="ft-months">${months}</div>
        <div class="ft-mlabels">${labels.map((l) => `<span>${l}</span>`).join('')}</div>
        ${beyond ? `<div class="ft-src" style="margin-top:6px;">12 個月以後還有 ${fmtWan(beyond)}（在下表裡）</div>` : ''}
    </div>
    <div class="ft-tblwrap" style="margin-top:12px;">
    <table class="ft-tbl">
        <thead><tr><th>項目</th><th>到期</th><th>來源</th><th class="n">金額</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><td colspan="3">未付合計，也就是第 2 層的目標</td><td class="n ft-num">$${fmtNum(d.earmark_total)}</td><td></td></tr></tfoot>
    </table>
    </div>
    <div class="ft-form">
        <label>項目<input class="crm-input" id="ft-ear-label" placeholder="例：綜所稅"></label>
        <label>金額<input class="crm-input" id="ft-ear-amt" type="number" min="1" step="1000" style="width:110px;"></label>
        <label>到期日<input class="crm-input" id="ft-ear-due" type="date" style="width:150px;"></label>
        <label>備註<input class="crm-input" id="ft-ear-note" placeholder="（可空）" style="width:160px;"></label>
        <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finFortress.addEarmark(this)">記一筆預留</button>
    </div>`;
}

// ── 每月必要支出 ＋ 各層目標 ───────────────────────────────────
function _monthly(d) {
    const mn = d.monthly_need || {};
    const isAuto = mn.override == null;
    return `
    <div class="ft-sec"><h3>每月必要支出</h3><span class="why">可撐月數的分母</span></div>
    <div class="ft-field">
        <div><div class="t">你認定的數字</div>
            <div class="s">自動算出來的是近 6 個月固定支出加家用的平均 <span class="ft-num">$${fmtNum(mn.auto)}</span>${isAuto ? '，現在就用這個' : '，現在用的是你填的'}</div></div>
        <div class="ctl">
            <input class="crm-input ft-num" id="ft-monthly" type="number" min="0" step="1000" value="${Number(mn.used) || 0}">
            <span class="ft-sub" style="font-size:12px;">元／月</span>
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finFortress.saveMonthly(this)">儲存</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finFortress.autoMonthly(this)"${isAuto ? ' disabled' : ''}>用自動</button>
        </div>
    </div>`;
}

function _targets(d) {
    const targets = (d.settings || {}).targets || {};
    const rows = (d.layers || []).filter((L) => L.no <= 4).sort((a, b) => a.no - b.no).map((L) => {
        const editable = L.no === 1 || L.no === 3 || L.no === 4;
        const rule = editable
            ? `<input class="crm-input ft-target-mult" data-layer="${L.no}" type="number" min="0" step="0.5" value="${targets[String(L.no)] ?? ''}" style="width:64px;"> 個月必要支出`
            : esc(L.target_rule || '預留清單合計');
        let gapHtml = '<span class="pill na">—</span>';
        if (L.gap != null) {
            if (L.gap > 0) gapHtml = `<span class="pill ${(Number(L.pct) || 0) < 50 ? 'bad' : 'warn'}">還差 ${fmtWan(L.gap)}</span>`;
            else gapHtml = `<span class="pill ok">多 ${fmtWan(-L.gap)}</span>`;
        }
        return `<tr><td style="white-space:nowrap;">${L.no} ${esc(L.name)}</td>
            <td class="ft-sub" style="white-space:nowrap;">${rule} <span class="ft-num" style="color:#e0e0e0;">${fmtWan(L.target)}</span></td>
            <td class="n ft-num">${fmtWan(L.have)}</td><td class="n">${gapHtml}</td></tr>`;
    }).join('');
    return `
    <div class="ft-sec"><h3>各層目標</h3><span class="why">用必要支出的倍數算，改了倍數會直接存</span></div>
    <div class="ft-tblwrap">
    <table class="ft-tbl">
        <thead><tr><th>層</th><th>目標</th><th class="n">現在</th><th class="n">差距</th></tr></thead>
        <tbody>${rows}</tbody>
    </table>
    </div>`;
}

// ── 壓力測試：五座塔同一個框 ──────────────────────────────────
function _tower(t, i, d) {
    const state = STATE_PILL[t.state] ? t.state : 'na';
    const war = t.key === 'war';
    const w = (d.settings || {}).war || {};
    const pct = (v) => Math.round((Number(v) || 0) * 100);
    const warForm = war && _warOpen ? `<div class="ft-war">
        <label>收入斷幾個月<input class="crm-input" id="ft-war-months" type="number" min="1" step="1" value="${Number(w.months) || 12}"></label>
        <label>銀行幾週領不到錢<input class="crm-input" id="ft-war-freeze" type="number" min="0" step="1" value="${Number(w.bank_freeze_weeks) || 0}"></label>
        <label>台股跌 %<input class="crm-input" id="ft-war-tw" type="number" min="0" max="100" step="5" value="${pct(w.tw_drop)}"></label>
        <label>美股跌 %<input class="crm-input" id="ft-war-us" type="number" min="0" max="100" step="5" value="${pct(w.us_drop)}"></label>
        <label>台幣貶 %<input class="crm-input" id="ft-war-fx" type="number" min="0" max="200" step="5" value="${pct((Number(w.fx) || 1) - 1)}"></label>
        <div class="full"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finFortress.saveWar(this)">儲存假設</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finFortress.toggleWar()">收起</button></div>
    </div>` : '';
    return `<div class="ft-test ${state === 'ok' ? '' : state}">
        <div class="h"><span class="pill ${state}">${STATE_PILL[state]}</span><span>${i + 1}. ${esc(t.title)}</span></div>
        <div class="q">${esc(t.question || '')}</div>
        ${t.assume ? `<div class="assume">${esc(t.assume)}${war && !_warOpen ? ' <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:1px 7px;font-size:11px;" onclick="window._finFortress.toggleWar()">改假設</button>' : ''}</div>` : ''}
        ${warForm}
        ${(t.lines || []).map((l) => `<div class="line"><span>${esc(l[0])}</span><span>${fmtLine(l)}</span></div>`).join('')}
        <div class="verdict">${esc(t.verdict || '')}</div>
    </div>`;
}

// ── 帳戶分層 ─────────────────────────────────────────────────
function _accounts(d) {
    const names = {};
    (d.layers || []).forEach((L) => { names[L.no] = L.name; });
    const rows = (d.accounts || []).map((a) => {
        const f = a.flags || {};
        const neg = a.balance < 0;
        return `<tr data-id="${esc(a.id)}">
            <td>${esc(a.name)}${a.currency && a.currency !== 'TWD' ? ` <span class="ft-src">${esc(a.currency)}</span>` : ''}</td>
            <td class="ft-sub">${KIND_LABEL[a.kind] || esc(a.kind || '')}</td>
            <td class="n ft-num${neg ? ' ft-neg' : ''}">${neg ? '−' : ''}$${fmtNum(Math.abs(a.balance))}</td>
            <td><select class="crm-input ft-layer">${[1, 2, 3, 4, 5].map((l) => `<option value="${l}"${a.layer === l ? ' selected' : ''}>${l} ${esc(names[l] || '')}</option>`).join('')}</select></td>
            <td><label class="ft-chk"><input type="checkbox" class="ft-flag" data-flag="physical"${f.physical ? ' checked' : ''}>實體</label>
                <label class="ft-chk"><input type="checkbox" class="ft-flag" data-flag="offshore"${f.offshore ? ' checked' : ''}>海外</label>
                <label class="ft-chk"><input type="checkbox" class="ft-flag" data-flag="usd"${f.usd ? ' checked' : ''}>美元</label></td>
        </tr>`;
    }).join('') || '<tr><td colspan="5" class="ft-empty">私帳還沒有帳戶或持股</td></tr>';
    return `
    <div class="ft-tblwrap">
    <table class="ft-tbl">
        <thead><tr><th>帳戶</th><th>類型</th><th class="n">目前餘額</th><th>層別</th><th>標記（戰爭題用：實體＝銀行停擺也拿得到、海外＝台灣的銀行停擺也拿得到、美元＝台幣貶值時變厚）</th></tr></thead>
        <tbody id="ft-acct-body">${rows}</tbody>
    </table>
    </div>
    <div class="ft-src" id="ft-layer-status" style="margin-top:6px;">改層別或勾標記後半秒內自動儲存。</div>`;
}

function _onLayerChange(ev) {
    if (!ev.target.matches('.ft-layer, .ft-flag')) return;
    const st = document.getElementById('ft-layer-status');
    if (st) st.textContent = '儲存中…';
    clearTimeout(_layerTimer);
    _layerTimer = setTimeout(() => _ff.saveLayers(), 400);
}

// ── 寫入（每次都拿回整份 payload 重畫） ────────────────────────
async function _put(path, body, okMsg, btn) {
    if (btn) btn.disabled = true;
    try {
        _d = await finFetchMine(path, { method: 'PUT', body: JSON.stringify(body) });
        if (!_isCurrent()) return;
        _render();
        if (okMsg) finToast(okMsg);
    } catch (e) {
        finToast('儲存失敗：' + e.message, true);
        if (btn) btn.disabled = false;
    }
}

const _ff = (window._finFortress = window._finFortress || {});

_ff.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

_ff.saveMonthly = (btn) => {
    const v = parseInt(document.getElementById('ft-monthly').value, 10);
    if (!(v > 0)) { finToast('請填每月必要支出（元）', true); return; }
    _put('/fortress/settings', { monthly_need_override: v }, '已改必要支出', btn);
};
_ff.autoMonthly = (btn) => _put('/fortress/settings', { monthly_need_override: 0 }, '改回自動平均', btn);

_ff.saveTargets = () => {
    const targets = {};
    _c.querySelectorAll('.ft-target-mult').forEach((el) => {
        const v = parseFloat(el.value);
        if (v >= 0) targets[el.dataset.layer] = v;
    });
    if (!Object.keys(targets).length) return;
    _put('/fortress/settings', { targets }, '已改目標倍數');
};

_ff.toggleWar = () => { _warOpen = !_warOpen; _render(); };
_ff.saveWar = (btn) => {
    const g = (id) => Number(document.getElementById(id).value) || 0;
    const war = {
        months: Math.max(1, Math.round(g('ft-war-months'))),
        bank_freeze_weeks: Math.max(0, Math.round(g('ft-war-freeze'))),
        tw_drop: g('ft-war-tw') / 100,
        us_drop: g('ft-war-us') / 100,
        fx: 1 + g('ft-war-fx') / 100,
    };
    _warOpen = false;
    _put('/fortress/settings', { war }, '已改戰爭假設', btn);
};

_ff.saveLayers = () => {
    const account_layers = {}, account_flags = {};
    _c.querySelectorAll('#ft-acct-body tr[data-id]').forEach((tr) => {
        const id = tr.dataset.id;
        account_layers[id] = parseInt(tr.querySelector('.ft-layer').value, 10) || 1;
        const flags = {};
        FLAG_KEYS.forEach((k) => { flags[k] = !!tr.querySelector(`.ft-flag[data-flag="${k}"]`)?.checked; });
        account_flags[id] = flags;
    });
    _put('/fortress/settings', { account_layers, account_flags }, '已存分層');
};

_ff.addEarmark = async (btn) => {
    const g = (id) => document.getElementById(id).value.trim();
    const label = g('ft-ear-label');
    const amount = parseInt(g('ft-ear-amt'), 10) || 0;
    if (!label) { finToast('項目必填', true); return; }
    if (amount <= 0) { finToast('金額必填', true); return; }
    btn.disabled = true;
    try {
        _d = await finFetchMine('/fortress/earmarks', {
            method: 'POST', body: JSON.stringify({ label, amount, due_date: g('ft-ear-due') || '', note: g('ft-ear-note') }),
        });
        if (!_isCurrent()) return;
        _render();
        finToast('已記預留');
    } catch (e) {
        finToast('新增失敗：' + e.message, true);
        btn.disabled = false;
    }
};

_ff.togglePaid = (id, paid, btn) =>
    _put(`/fortress/earmarks/${encodeURIComponent(id)}`, { paid }, paid ? '標成已付' : '標回未付', btn);

_ff.delEarmark = async (id, btn) => {
    const e = (_d.earmarks || []).find((x) => x.id === id);
    if (!confirm(`刪除預留「${e ? e.label : id}」？`)) return;
    btn.disabled = true;
    try {
        _d = await finFetchMine(`/fortress/earmarks/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!_isCurrent()) return;
        _render();
        finToast('已刪除');
    } catch (err) {
        finToast('刪除失敗：' + err.message, true);
        btn.disabled = false;
    }
};
