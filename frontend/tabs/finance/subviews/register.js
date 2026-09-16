/**
 * register.js — 登記餘額（私帳；owner 2026-09-17「先讓我登記我的帳戶資產，明細我後面補」）。
 *
 * 一張表：對著銀行 App／券商 App 把「今天看到的數字」填進去，按一次儲存。
 * - 銀行／現金帳戶 → 寫基準點（anchor）。之後餘額 = 登記餘額 + 基準日之後的明細；
 *   基準日當天與之前的明細只當歷史，補多少都不動今天的數字（規則：core.finance_logic.derive_balance）。
 * - 證券戶（一家券商一列）→ 總市值 − 已拆明細 落到那家的「未拆明細」列，之後拆明細它自然縮小。
 * - 信用卡 → 走既有的 PUT /card-summary derive_opening_from（那支本來就是「現在實際欠多少」）。
 * 數字全由後端 GET /finance/balance-register 一趟算好；每次儲存拿回整包重畫，前端不自己算餘額。
 * 只在私帳出現（finance.html 的 .fin-nav-mine-only），所以固定走 finFetchMine。
 */
import { finFetchMine, finSubviewBoot, esc, fmtNum, finToast, todayStr } from '../fin-utils.js';

let _c = null;
let _isCurrent = () => true;
let _d = null;

const CSS_ID = 'register-css';
const KIND_LABEL = { bank: '銀行', cash: '現金' };

function _injectCss() {
    if (document.getElementById(CSS_ID)) return;
    const st = document.createElement('style');
    st.id = CSS_ID;
    st.textContent = `
.rg { color: #d1d5db; font-size: 13.5px; }
.rg .rg-top { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin: 0 0 14px; }
.rg .rg-top .why { color: #9ca3af; font-size: 12.5px; max-width: 640px; line-height: 1.6; }
.rg .rg-top label { display: inline-flex; align-items: center; gap: 6px; color: #9ca3af; font-size: 12.5px; }
.rg .rg-top input[type=date] { width: 150px; }
.rg .rg-sec { display: flex; align-items: baseline; gap: 14px; margin: 22px 0 8px; flex-wrap: wrap; }
.rg .rg-sec h3 { font-size: 16px; margin: 0; color: #eee; }
.rg .rg-sec .why { font-size: 12.5px; color: #9ca3af; }
.rg .rg-tblwrap { overflow-x: auto; }
.rg table { width: 100%; border-collapse: collapse; }
.rg th, .rg td { padding: 8px 10px; border-bottom: 1px solid #333; text-align: left; vertical-align: middle; white-space: nowrap; }
.rg th { color: #9ca3af; font-weight: 500; font-size: 12px; }
.rg th.n, .rg td.n { text-align: right; font-variant-numeric: tabular-nums; }
.rg td.sub { color: #9ca3af; font-size: 12px; }
.rg td input.rg-in { width: 150px; text-align: right; font-variant-numeric: tabular-nums; }
.rg td input.rg-h { width: 120px; }
.rg tr.rg-broker td { background: #202020; }
.rg tr.rg-holding td { padding-top: 5px; padding-bottom: 5px; }
.rg .rg-diff { color: #fbbf24; }
.rg .rg-zero { color: #86efac; }
.rg .rg-link { color: #93c5fd; cursor: pointer; font-size: 12px; background: none; border: 0; padding: 0; }
.rg .rg-link:hover { text-decoration: underline; }
.rg .rg-foot { margin-top: 26px; font-size: 12px; color: #6b7280; border-top: 1px solid #3a3a3a; padding-top: 12px; line-height: 1.7; }
.rg .rg-foot b { color: #9ca3af; font-weight: 500; }
.rg .rg-bar { position: sticky; bottom: 0; background: #1a1a1a; border-top: 1px solid #333; padding: 10px 0; margin-top: 18px;
              display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.rg .rg-bar .hint { color: #9ca3af; font-size: 12.5px; }
.rg .rg-empty { color: #6b7280; text-align: center; padding: 14px; }
`;
    document.head.appendChild(st);
}

const money = (n, { signed = false } = {}) => {
    if (n === null || n === undefined) return '—';
    const v = Number(n);
    const s = `$${fmtNum(Math.abs(v))}`;
    if (v < 0) return `−${s}`;
    return signed && v > 0 ? `+${s}` : s;
};
const shortDay = (d) => (d ? String(d).slice(5, 10).replace('-', '/') : '');

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _injectCss();
    const r = await finSubviewBoot(_c, {
        title: '登記餘額', isCurrent: _isCurrent,
        fetchers: [() => finFetchMine('/balance-register')],
        retry: 'window._finRegister.reload()',
    });
    if (!r) return;
    _d = r[0];
    _render();
}

function _render() {
    const d = _d;
    _c.innerHTML = `<div class="rg">
        <div class="rg-top">
            <label>基準日 <input class="crm-input" type="date" id="rg-date" value="${esc(d.date || todayStr())}" max="${esc(todayStr())}"></label>
            <div class="why">對著銀行 App、券商 App 把<b>今天看到的數字</b>填進去，按一次儲存。空著的帳戶不會動。
                之後補明細：基準日當天與之前的明細只當歷史，今天的餘額不會被重複加；基準日之後記的明細才會動餘額。</div>
        </div>
        <div class="rg-sec"><h3>銀行／現金帳戶</h3><span class="why">「還沒補的明細」＝登記的數字 − 帳上算到基準日的數字；補齊會歸 0</span></div>
        ${_accountsTable(d)}
        <div class="rg-sec"><h3>證券戶</h3><span class="why">每檔可以填今天的股數、現價、成本（市值＝股數 × 現價）；一家券商也可以只填一個總市值，比已拆明細多出來的部分先記成那家的「未拆明細」，之後拆明細它自然縮小</span></div>
        ${_brokersTable(d)}
        <div class="rg-sec"><h3>信用卡</h3><span class="why">全部卡合計的目前未繳；跟信用卡頁是同一個數字</span></div>
        ${_cardTable(d)}
        <div class="rg-bar">
            <button class="crm-btn crm-btn-primary" onclick="window._finRegister.saveAll(this)">儲存有填的</button>
            <span class="hint" id="rg-status">只送有填數字的列；儲存後堡壘、資產儀表板、財富階梯立刻用新數字。</span>
        </div>
        <div class="rg-foot">
            <b>取消登記</b>會讓那個帳戶回到「期初＋全部明細」的老算法。<b>每次登記</b>都會在對帳紀錄留一筆（哪一天、登記多少、帳上算多少），以後可以看每個月的走勢。
            證券的「未拆明細」是資產儀表板持股清單裡的一列（券商名＋未拆明細），要拆的時候把真的持股加進去、再回來登記一次總市值就會自己歸零。
        </div>
    </div>`;
}

function _accountsTable(d) {
    const rows = (d.accounts || []).map((a) => {
        const unfilled = a.unfilled;
        const uf = unfilled === null || unfilled === undefined ? '<span class="sub">還沒登記過</span>'
            : (unfilled === 0 ? '<span class="rg-zero">已補齊</span>'
                : `<span class="rg-diff">${money(unfilled, { signed: true })}</span> <span class="sub">（登記 ${esc(shortDay(a.anchor_date))} 之後帳上${unfilled < 0 ? '少' : '多'}了這麼多，還沒補的明細）</span>`);
        const last = a.anchor_balance === null || a.anchor_balance === undefined ? '—'
            : `${money(a.anchor_balance)} <span class="sub">${esc(shortDay(a.anchor_date))}</span>
               <button type="button" class="rg-link" onclick="window._finRegister.clearOne('${esc(a.id)}', this)">取消登記</button>`;
        return `<tr data-acct="${esc(a.id)}">
            <td>${esc(a.name)}<div class="sub">${esc(a.bank_name || '')}</div></td>
            <td class="sub">${KIND_LABEL[a.acct_kind] || esc(a.acct_kind || '')}</td>
            <td class="n">${money(a.balance)}</td>
            <td class="n">${last}</td>
            <td class="n"><input class="crm-input rg-in" type="number" step="1" inputmode="numeric" placeholder="${esc(fmtNum(a.balance ?? 0).replace(/,/g, ''))}"></td>
            <td>${uf}</td>
        </tr>`;
    }).join('') || '<tr><td colspan="6" class="rg-empty">私帳還沒有銀行／現金帳戶</td></tr>';
    return `<div class="rg-tblwrap"><table>
        <thead><tr><th>帳戶</th><th>種類</th><th class="n">帳上算的</th><th class="n">上次登記</th><th class="n">今天實際餘額</th><th>還沒補的明細</th></tr></thead>
        <tbody id="rg-acct-body">${rows}</tbody></table></div>`;
}

/** 一家券商一列（總市值輸入格）＋ 底下每檔持股一列（股數／現價／成本輸入格）。
 *  市值＝股數 × 現價（外幣再乘匯率）；手填市值的那檔會標「手填」，登記股數與現價後就改用算的。 */
function _brokersTable(d) {
    const num = (v, dp = 2) => (v === null || v === undefined ? '' : String(Math.round(Number(v) * 10 ** dp) / 10 ** dp));
    const rows = (d.brokers || []).map((b) => `<tr data-broker="${esc(b.broker)}" class="rg-broker">
            <td><b>${esc(b.broker || '（未指定券商）')}</b> <span class="sub">${b.count} 檔</span></td>
            <td class="n sub">已拆 ${money(b.detail)}</td>
            <td class="n">${b.plug ? `<span class="rg-diff">未拆 ${money(b.plug)}</span>` : '<span class="sub">未拆 0</span>'}</td>
            <td class="n">合計 ${money(b.total)}</td>
            <td class="n" colspan="2"><input class="crm-input rg-in" type="number" step="1" inputmode="numeric" placeholder="今天總市值 ${esc(String(b.total || 0))}"></td>
        </tr>` + (b.holdings || []).map((h) => `<tr data-holding="${esc(h.id)}" class="rg-holding">
            <td class="sub">　${esc(h.name)}${h.symbol ? ` <span class="sub">${esc(h.symbol)}</span>` : ''}${h.currency !== 'TWD' ? ` <span class="sub">${esc(h.currency)}</span>` : ''}</td>
            <td class="n sub">${h.manual_value !== null && h.manual_value !== undefined ? '手填市值' : `${num(h.shares, 4) || '—'} 股 × ${num(h.last_price) || '—'}`}</td>
            <td class="n">${money(h.value_twd)}</td>
            <td class="n"><input class="crm-input rg-in rg-h" data-k="shares" type="number" step="any" inputmode="decimal" placeholder="股數 ${esc(num(h.shares, 4) || '0')}"></td>
            <td class="n"><input class="crm-input rg-in rg-h" data-k="last_price" type="number" step="any" inputmode="decimal" placeholder="現價 ${esc(num(h.last_price) || '0')}"></td>
            <td class="n"><input class="crm-input rg-in rg-h" data-k="cost_total" type="number" step="any" inputmode="decimal" placeholder="成本 ${esc(num(h.cost_total) || '0')}"></td>
        </tr>`).join('')).join('') || '<tr><td colspan="6" class="rg-empty">私帳還沒有持股；先到資產儀表板加券商與持股</td></tr>';
    return `<div class="rg-tblwrap"><table>
        <thead><tr><th>券商／持股</th><th class="n">現在怎麼算</th><th class="n">現在市值</th><th class="n">今天股數</th><th class="n">今天現價</th><th class="n">成本合計</th></tr></thead>
        <tbody id="rg-broker-body">${rows}</tbody></table></div>`;
}

function _cardTable(d) {
    return `<div class="rg-tblwrap"><table>
        <thead><tr><th>項目</th><th class="n">現在</th><th class="n">今天實際未繳</th></tr></thead>
        <tbody><tr><td>信用卡未繳（全部卡合計）</td><td class="n">${money(d.card_outstanding)}</td>
            <td class="n"><input class="crm-input rg-in" type="number" step="1" min="0" inputmode="numeric" id="rg-card" placeholder="${esc(String(d.card_outstanding || 0))}"></td></tr></tbody>
    </table></div>`;
}

/** 有填的輸入格 → 整數；空白／非數字 → null（不送） */
function _val(el) {
    if (!el || el.value.trim() === '') return null;
    const v = Math.round(Number(el.value));
    return Number.isFinite(v) ? v : null;
}

const _rg = (window._finRegister = window._finRegister || {});

_rg.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

_rg.saveAll = async (btn) => {
    const date = (document.getElementById('rg-date') || {}).value || todayStr();
    const accounts = [];
    _c.querySelectorAll('#rg-acct-body tr[data-acct]').forEach((tr) => {
        const v = _val(tr.querySelector('input.rg-in'));
        if (v !== null) accounts.push({ id: tr.dataset.acct, balance: v });
    });
    const brokers = [];
    _c.querySelectorAll('#rg-broker-body tr[data-broker]').forEach((tr) => {
        const v = _val(tr.querySelector('input.rg-in'));
        if (v !== null) brokers.push({ broker: tr.dataset.broker, total: v });
    });
    // 逐檔：股數／現價／成本有填哪個送哪個（小數照送，碎股與美股價格都有小數）
    const holdings = [];
    _c.querySelectorAll('#rg-broker-body tr[data-holding]').forEach((tr) => {
        const h = { id: tr.dataset.holding };
        tr.querySelectorAll('input.rg-h').forEach((el) => {
            if (el.value.trim() !== '' && Number.isFinite(Number(el.value))) h[el.dataset.k] = Number(el.value);
        });
        if (Object.keys(h).length > 1) holdings.push(h);
    });
    const card = _val(document.getElementById('rg-card'));
    if (!accounts.length && !brokers.length && !holdings.length && card === null) { finToast('還沒填任何數字', true); return; }
    btn.disabled = true;
    try {
        let d = null;
        if (accounts.length || brokers.length || holdings.length) {
            d = await finFetchMine('/balance-register', { method: 'PUT', body: JSON.stringify({ date, accounts, holdings, brokers }) });
        }
        if (card !== null) {
            // 信用卡走既有那支：derive_opening_from ＝「現在實際欠多少」反推期初
            await finFetchMine('/card-summary', { method: 'PUT', body: JSON.stringify({ derive_opening_from: card }) });
            d = null;    // 卡的數字要重抓整包才會對
        }
        if (!_isCurrent()) return;
        _d = d || await finFetchMine('/balance-register');
        _render();
        finToast(`已登記：${accounts.length} 個帳戶、${holdings.length} 檔持股、${brokers.length} 家券商${card !== null ? '、信用卡' : ''}`);
    } catch (e) {
        finToast('儲存失敗：' + e.message, true);
        btn.disabled = false;
    }
};

_rg.clearOne = async (id, btn) => {
    if (!confirm('取消這個帳戶的登記？餘額會回到「期初＋全部明細」的老算法。')) return;
    btn.disabled = true;
    try {
        const date = (document.getElementById('rg-date') || {}).value || todayStr();
        _d = await finFetchMine('/balance-register', { method: 'PUT', body: JSON.stringify({ date, accounts: [{ id, balance: null }] }) });
        if (!_isCurrent()) return;
        _render();
        finToast('已取消登記');
    } catch (e) {
        finToast('取消失敗：' + e.message, true);
        btn.disabled = false;
    }
};
