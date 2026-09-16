/**
 * views/ledger-register.js — 士源帳本「登記餘額」頁（owner 2026-09-17「先讓我登記我的帳戶資產，明細我後面補」）。
 *
 * 隱藏路由 #register（tabbar 沒有它的鈕，從總覽的堡壘卡下方那一列點進來；上一頁回總覽）。
 * 資料＝桌機那支同一份：GET /api/v1/finance/balance-register?entity=mine；儲存 PUT 同一支，回整包重畫。
 * 由上到下：基準日 → 銀行／現金帳戶（一列一個：帳上算的、上次登記、還沒補的明細、輸入格）→ 證券戶（一家券商一列：
 * 已拆明細、未拆明細、輸入格）→ 底部一顆「儲存有填的」。信用卡不在手機登記（桌機那頁有）。
 * 只送有填數字的列；空著的帳戶不動。日期一律 todayLocal()（不用 toISOString：那是 UTC，台北早上 8 點前會變昨天）。
 */
import { mfetch, money, todayLocal, toast, esc } from '../shell.js';
import { skeleton, errBox, withBusy, markStale } from '../ui.js';

export const REGISTER_API = '/api/v1/finance/balance-register?entity=mine';

let _data = null;

const shortDate = (d) => (d ? String(d).slice(5, 10).replace('-', '/') : '');
const signed = (n) => (Number(n) > 0 ? '+' : '') + money(n);

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = skeleton(4);
        host.addEventListener('click', (ev) => {
            const go = ev.target.closest('[data-go]');
            if (go) { location.hash = go.dataset.go; return; }
            const b = ev.target.closest('#rg-save');
            if (b) saveAll(host, b);
        });
    }
    // 每次進來都重抓：它的數字是從收支明細推出來的，剛記的一筆要立刻反映在「帳上算的」
    await load(host);
}

async function load(host) {
    try {
        _data = await mfetch(REGISTER_API);
        host.innerHTML = draw(_data);
    } catch (e) {
        host.innerHTML = errBox(e);
    }
}

function draw(d) {
    return `
        <div class="m-card">
            <div class="m-form">
                <label>基準日</label><input id="rg-date" type="date" value="${esc(d.date || todayLocal())}" max="${esc(todayLocal())}">
            </div>
            <div class="lg-sub" style="margin-top:8px">對著銀行 App 把今天看到的數字填進去。空著的帳戶不動。之後補明細，基準日當天與之前的只當歷史，今天的餘額不會被重複加。</div>
        </div>
        <div class="m-h">銀行／現金帳戶</div>
        <div class="m-card">${(d.accounts || []).map(acctHtml).join('') || '<div class="m-empty">還沒有銀行／現金帳戶</div>'}</div>
        <div class="m-h">證券戶（每檔填今天的股數，或一家填一個總市值）</div>
        <div class="m-card">${(d.brokers || []).map(brokerHtml).join('') || '<div class="m-empty">還沒有持股</div>'}</div>
        <div class="m-card">
            <div class="lg-row"><span class="k">信用卡未繳（全部卡合計）</span><span class="v amt">${money(d.card_outstanding)}</span></div>
            <div class="lg-sub" style="margin-top:4px">要改請到桌機的登記餘額頁</div>
        </div>
        <button type="button" class="m-btn-primary" id="rg-save">儲存有填的</button>
        <div class="lg-sub" style="margin:8px 0 16px">儲存後堡壘、資產儀表板立刻用新數字；每次登記都會留一筆紀錄。</div>`;
}

function acctHtml(a) {
    const uf = a.unfilled === null || a.unfilled === undefined ? '還沒登記過'
        : (a.unfilled === 0 ? '已補齊' : `登記 ${shortDate(a.anchor_date)} 之後還沒補的明細 ${signed(a.unfilled)}`);
    const last = a.anchor_balance === null || a.anchor_balance === undefined ? '' : `・上次登記 ${money(a.anchor_balance)}（${shortDate(a.anchor_date)}）`;
    return `<div class="rg-row" data-acct="${esc(a.id)}">
        <div class="t"><span class="nm">${esc(a.name)}</span><span class="v">帳上 <b>${money(a.balance)}</b></span></div>
        <div class="lg-sub">${esc(uf)}${esc(last)}</div>
        <div class="m-form"><input class="rg-in" type="number" inputmode="numeric" step="1" placeholder="今天實際餘額（空著＝不動）"></div>
    </div>`;
}

const num = (v, dp = 2) => (v === null || v === undefined ? '' : String(Math.round(Number(v) * 10 ** dp) / 10 ** dp));

/** 一家券商一塊：總市值輸入格＋底下每檔一列（只有股數一格 —— owner「證券我只需要更改單位數」）。 */
function brokerHtml(b) {
    const rows = (b.holdings || []).map((h) => `<div class="rg-hold" data-holding="${esc(h.id)}">
        <div class="t"><span class="nm">${esc(h.name)}${h.currency !== 'TWD' ? ` <span>${esc(h.currency)}</span>` : ''}</span>
            <span class="v">${money(h.value_twd)}</span></div>
        <div class="lg-sub">${h.manual_value !== null && h.manual_value !== undefined ? '手填市值' : `${num(h.shares, 4) || '—'} 股 × ${num(h.last_price) || '—'}`}${h.cost_total ? `・成本 ${num(h.cost_total)}` : ''}</div>
        <div class="m-form"><input class="rg-h" data-k="shares" type="number" inputmode="decimal" step="any" placeholder="今天股數 ${esc(num(h.shares, 4) || '0')}（空著＝不動）"></div>
    </div>`).join('');
    return `<div class="rg-row" data-broker="${esc(b.broker)}">
        <div class="t"><span class="nm">${esc(b.broker || '（未指定券商）')}</span><span class="v">合計 <b>${money(b.total)}</b></span></div>
        <div class="lg-sub">已拆明細 ${b.count} 筆 ${money(b.detail)}${b.plug ? `・未拆明細 ${money(b.plug)}` : ''}</div>
        <div class="m-form"><input class="rg-in" type="number" inputmode="numeric" step="1" placeholder="今天總市值（空著＝不動）"></div>
        ${rows}
    </div>`;
}

/** 有填的輸入格 → 整數；空白／非數字 → null（不送） */
function val(el) {
    if (!el || String(el.value).trim() === '') return null;
    const v = Math.round(Number(el.value));
    return Number.isFinite(v) ? v : null;
}

async function saveAll(host, btn) {
    const date = (host.querySelector('#rg-date') || {}).value || todayLocal();
    const accounts = [];
    host.querySelectorAll('.rg-row[data-acct]').forEach((r) => {
        const v = val(r.querySelector('input.rg-in'));
        if (v !== null) accounts.push({ id: r.dataset.acct, balance: v });
    });
    const brokers = [];
    host.querySelectorAll('.rg-row[data-broker]').forEach((r) => {
        const v = val(r.querySelector(':scope > .m-form > input.rg-in'));
        if (v !== null) brokers.push({ broker: r.dataset.broker, total: v });
    });
    const holdings = [];
    host.querySelectorAll('.rg-hold[data-holding]').forEach((r) => {
        const h = { id: r.dataset.holding };
        r.querySelectorAll('input.rg-h').forEach((el) => {
            if (String(el.value).trim() !== '' && Number.isFinite(Number(el.value))) h[el.dataset.k] = Number(el.value);
        });
        if (Object.keys(h).length > 1) holdings.push(h);
    });
    if (!accounts.length && !brokers.length && !holdings.length) { toast('還沒填任何數字', 'err'); return; }
    await withBusy(btn, async () => {
        try {
            // mfetch 自己會 JSON.stringify(opts.body)（shell.js）：這裡傳物件，再包一層就是送字串字面值 → 422
            _data = await mfetch(REGISTER_API, { method: 'PUT', body: { date, accounts, holdings, brokers } });
            host.innerHTML = draw(_data);
            // 月報：登記完後端已經把當月那份算好，頁頂給一條路過去（#report 隱藏路由）
            if (_data && _data.report_month) {
                host.insertAdjacentHTML('afterbegin', `<div class="m-card tap rg-go rg-report" data-go="report" role="button"><span>${esc(_data.report_month.replace('-', ' 年 '))} 月的月報已經更新，點這裡看</span><span>›</span></div>`);
            }
            markStale('overview', 'assets');     // 總覽的堡壘卡與資產頁下次要重抓
            toast(`已登記 ${accounts.length} 個帳戶、${holdings.length} 檔持股、${brokers.length} 家券商`);
            window.scrollTo(0, 0);
        } catch (err) {
            toast(err.message, 'err');
        }
    });
}
