/**
 * projects.js — 📁 逐案損益（帳本視角的專案清單，2026-08-24）。
 *
 * owner「跟我有關的專案我都要看到」。/my-ledger.html 刻意沒有 CRM 專案管理
 * （那是主系統的事：階段/派工/看板），但它缺的正是最在意的維度 —— 逐案的錢。
 * 這個子視圖＝原 Sheet「結案總表」的系統版：一列一案，帶合約／已收／應收／
 * 掛帳支出／未付應付／淨額，點開看逐筆收支與應付。
 *
 * 資料全部來自後端 /finance/project-ledger（口徑與注意事項見該檔 docstring：
 * 淨額**不等於** Sheet 的「實收」—— 稅金/買發票/代辦費在匯入時只留純文字，
 * 算不出來就不假裝算得出，明細把原始備註原樣附上供對照）。
 */
import { finFetch, esc, fmtNum, finToast } from '../fin-utils.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;
let _q = '';
let _unpaidOnly = false;

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _c.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">載入專案…</div>';
    await _load();
}

async function _load() {
    try {
        const p = new URLSearchParams();
        if (_q) p.set('q', _q);
        if (_unpaidOnly) p.set('unpaid_only', 'true');
        const d = await finFetch(`/project-ledger${p.toString() ? '?' + p : ''}`);
        if (!_isCurrent()) return;
        _data = d;
        _render();
    } catch (e) {
        _c.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">專案載入失敗：${esc(e.message)}</div>`;
    }
}

function _render() {
    const t = _data.totals || {};
    const rows = (_data.projects || []).map((p) => `
        <tr style="cursor:pointer;" onclick="window._finProjLedger.open('${p.id}')">
            <td style="white-space:nowrap;color:#888;">${esc(p.close_month || '—')}</td>
            <td>${esc(p.client)}</td>
            <td>${esc(p.name)}<div style="color:#666;font-size:10px;">${esc(p.type)}${p.status ? '｜' + esc(p.status) : ''}</div></td>
            <td style="text-align:right;">${fmtNum(p.contract)}</td>
            <td style="text-align:right;color:#86efac;">${fmtNum(p.received)}</td>
            <td style="text-align:right;color:${p.receivable ? '#fbbf24' : '#666'};">${p.receivable ? fmtNum(p.receivable) : '—'}</td>
            <td style="text-align:right;color:#fca5a5;">${p.spent ? fmtNum(p.spent) : '—'}</td>
            <td style="text-align:right;color:${p.ap_open ? '#fca5a5' : '#666'};">${p.ap_open ? fmtNum(p.ap_open) : '—'}</td>
            <td style="text-align:right;color:#eee;">${fmtNum(p.net)}</td>
        </tr>`).join('');
    _c.innerHTML = `
        <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-bottom:12px;">
            <input id="fpl-q" class="crm-input" placeholder="搜尋專案 / 客戶" style="width:220px;" value="${esc(_q)}">
            <label style="font-size:12px;color:#ccc;display:flex;align-items:center;gap:5px;">
                <input type="checkbox" id="fpl-unpaid" ${_unpaidOnly ? 'checked' : ''}> 只看未收清
            </label>
            <div style="flex:1;"></div>
            <span style="font-size:12px;color:#888;">${fmtNum(_data.count)} 案</span>
        </div>
        <div style="display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#ccc;margin-bottom:10px;
                    background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:12px 16px;">
            <span>營收(含稅) <b style="color:#eee;">$${fmtNum(t.contract)}</b></span>
            <span>已收 <b style="color:#86efac;">$${fmtNum(t.received)}</b></span>
            <span>應收 <b style="color:#fbbf24;">$${fmtNum(t.receivable)}</b></span>
            <span>掛帳支出 <b style="color:#fca5a5;">$${fmtNum(t.spent)}</b></span>
            <span>未付應付 <b style="color:#fca5a5;">$${fmtNum(t.ap_open)}</b></span>
            <span>淨額 <b style="color:#eee;">$${fmtNum(t.net)}</b></span>
        </div>
        <div style="overflow-x:auto;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr>
                <th>結案月</th><th>客戶</th><th>專案</th>
                <th style="text-align:right;">營收</th><th style="text-align:right;">已收</th>
                <th style="text-align:right;">應收</th><th style="text-align:right;">掛帳支出</th>
                <th style="text-align:right;">未付應付</th><th style="text-align:right;">淨額</th>
            </tr></thead>
            <tbody>${rows || '<tr><td colspan="9" style="color:#888;padding:24px;text-align:center;">沒有符合的專案</td></tr>'}</tbody>
        </table></div>
        <div style="color:#666;font-size:11px;margin-top:8px;">
            淨額 = 營收 − 掛帳支出 − 未付應付。<b>不等於</b>原表的「實收」——
            稅金／買發票／發票代辦費匯入時只保留為文字，點開單案可看原始備註對照。
        </div>
        <div id="fpl-modal"></div>`;
    const qEl = document.getElementById('fpl-q');
    let timer;
    qEl.addEventListener('input', (e) => {
        _q = e.target.value.trim();
        clearTimeout(timer);
        timer = setTimeout(_load, 300);
    });
    document.getElementById('fpl-unpaid').addEventListener('change', (e) => {
        _unpaidOnly = e.target.checked;
        _load();
    });
}

const _fp = (window._finProjLedger = window._finProjLedger || {});

_fp.open = async (id) => {
    const host = document.getElementById('fpl-modal');
    host.innerHTML = `<div style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;
        display:flex;align-items:center;justify-content:center;" onclick="if(event.target===this)this.remove()">
        <div style="background:#202020;border:1px solid #333;border-radius:8px;padding:20px;
                    width:820px;max-height:86vh;overflow:auto;color:#888;">載入中…</div></div>`;
    try {
        const d = await finFetch(`/project-ledger/${id}`);
        const p = d.project;
        const entries = (d.entries || []).map((e) => `
            <tr><td style="white-space:nowrap;color:#888;">${esc(e.date)}</td>
                <td>${esc(e.summary)}<div style="color:#666;font-size:10px;">${esc(e.category)}</div></td>
                <td style="text-align:right;color:#86efac;">${e.deposit ? fmtNum(e.deposit) : ''}</td>
                <td style="text-align:right;color:#fca5a5;">${e.expense ? fmtNum(e.expense) : ''}</td></tr>`).join('');
        const pays = (d.payments || []).map((x) => `
            <tr><td>${esc(x.summary)}<div style="color:#666;font-size:10px;">${esc(x.category)}${x.payee ? '｜' + esc(x.payee) : ''}</div></td>
                <td style="text-align:right;">${fmtNum(x.amount)}</td>
                <td style="white-space:nowrap;color:${x.payment_status === '已付款' ? '#86efac' : '#fbbf24'};">${esc(x.payment_status)}${x.payment_date ? ' ' + esc(x.payment_date) : ''}</td></tr>`).join('');
        host.querySelector('div > div').outerHTML = `
            <div style="background:#202020;border:1px solid #333;border-radius:8px;padding:20px;width:820px;max-height:86vh;overflow:auto;">
                <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;">
                    <h3 style="color:#eee;margin:0;font-size:15px;">${esc(p.client)}／${esc(p.name)}</h3>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm"
                            onclick="document.getElementById('fpl-modal').innerHTML=''">關閉</button>
                </div>
                <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:#ccc;margin:10px 0 14px;">
                    <span>${esc(p.close_month || '—')}</span><span>${esc(p.status)}</span>
                    <span>營收 <b style="color:#eee;">$${fmtNum(p.contract)}</b></span>
                    <span>已收 <b style="color:#86efac;">$${fmtNum(p.received)}</b></span>
                    <span>應收 <b style="color:#fbbf24;">$${fmtNum(p.receivable)}</b></span>
                    <span>${esc(p.payment_status)}</span>
                </div>
                <h4 style="color:#ddd;font-size:13px;margin:12px 0 6px;">掛在本案的收支（${(d.entries || []).length}）</h4>
                <table class="crm-table" style="width:100%;font-size:12px;">
                    <thead><tr><th>日期</th><th>摘要</th><th style="text-align:right;">存入</th><th style="text-align:right;">支出</th></tr></thead>
                    <tbody>${entries || '<tr><td colspan="4" style="color:#666;padding:12px;">（無）</td></tr>'}</tbody></table>
                <h4 style="color:#ddd;font-size:13px;margin:16px 0 6px;">應付／請款單（${(d.payments || []).length}）</h4>
                <table class="crm-table" style="width:100%;font-size:12px;">
                    <thead><tr><th>項目</th><th style="text-align:right;">金額</th><th>狀態</th></tr></thead>
                    <tbody>${pays || '<tr><td colspan="3" style="color:#666;padding:12px;">（無）</td></tr>'}</tbody></table>
                <h4 style="color:#ddd;font-size:13px;margin:16px 0 6px;">原始備註（匯入保留：案碼／案源／税別／稅務欄／工種拆分）</h4>
                <pre style="white-space:pre-wrap;color:#aaa;font-size:11px;background:#1a1a1a;
                            border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin:0;">${esc(p.notes || '（無）')}</pre>
            </div>`;
    } catch (e) {
        host.innerHTML = '';
        finToast('載入單案明細失敗：' + e.message, 'error');
    }
};
