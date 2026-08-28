/**
 * receivable.js — 📥 應收帳款（私帳；owner 2026-08-25「除了應付以外，也需要
 * 有應收」）。
 *
 * 🔴 私帳的應收**不是**發票（owner 不開發票，CRM 應收視圖查 crm_invoices 對
 * 私帳恆空）—— 正確資料源是執行專案的逐案應收（營收 − 已收）。所以這頁是
 * /finance/project-ledger 的投影：應收 ≠ 0 的案子按客戶分組，收款動作本身在
 * 收支明細（記收入勾專案，到齊自動變收款）。
 *
 * 同 projects/gear：固定打私帳（entity:'mine'），入口由 finance.js 以
 * finance_mine 指名門把關。
 */
import { finFetchMine, finSubviewBoot, esc, fmtNum } from '../fin-utils.js';
import { crmFetch } from '../../crm/crm-utils.js';

let _c = null;
let _isCurrent = () => true;

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    const r = await finSubviewBoot(_c, {
        title: '📥 應收帳款', isCurrent: _isCurrent,
        fetchers: [
            () => finFetchMine('/project-ledger'),
            // 零用金那側是**源日欠我**（收支明細推過去的那些單據）。取不到就
            // 當沒有 —— 帳號沒綁人員檔案會 409，不該整頁掛掉。
            () => crmFetch('/petty/me').catch(() => null),
        ],
        retry: 'window._finRecv.reload()',
    });
    if (r) _render(r[0], r[1]);
}

/** 源日請款待收 —— 收支明細推過去、源日還沒把錢匯回來的部分。
 *  （UI 叫「源日請款」，資料/端點仍是零用金 petty —— 見 crm-cashbook.js 的命名註解）
 *
 *  🔴 兩段要分開講：草稿還沒送出（公司那邊還不知道有這筆），已核准才是真的
 *  應收。混成一個數字的話，看到「應收 4 萬」卻在公司的應付款裡找不到。 */
function _pettyBlock(petty) {
    if (!petty) { return ''; }
    const draft = petty.pending_total || 0;
    const claims = (petty.claims || []).filter((c) => c.status !== '已付款');
    const approved = claims.reduce((a, c) => a + (c.total_claim || 0), 0);
    if (!draft && !approved) { return ''; }
    return `
        <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;
                    padding:12px 14px;margin-bottom:14px;">
            <div style="color:#ddd;font-weight:600;font-size:13px;margin-bottom:6px;">源日請款待收</div>
            <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12px;">
                <span style="color:#888;">已核准待匯款
                    <b style="color:#fbbf24;">$${fmtNum(approved)}</b>
                    <span style="color:#666;">（${claims.length} 批）</span></span>
                <span style="color:#888;">草稿未送出
                    <b style="color:#9ccfa4;">$${fmtNum(draft)}</b>
                    <span style="color:#666;">（${(petty.pending || []).length} 筆）</span></span>
            </div>
            <div style="color:#666;font-size:11px;margin-top:6px;">
                從收支明細的 ⋮「源日請款」進來。草稿要到「財務管理 → 零用金 →
                我的請款」按送出，源日那邊才會產生應付款。</div>
        </div>`;
}

function _render(d, petty) {
    const rows = (d.projects || []).filter((p) => (p.receivable || 0) !== 0);
    const total = rows.reduce((a, p) => a + p.receivable, 0);
    const over = rows.filter((p) => p.receivable < 0);

    // 按客戶分組、小計大者在前 —— 「誰欠我最多」一眼看到
    const groups = new Map();
    rows.forEach((p) => {
        const k = p.client || '（未定客戶）';
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(p);
    });
    const ordered = [...groups.entries()]
        .map(([client, list]) => ({
            client, list: list.sort((a, b) => b.receivable - a.receivable),
            sub: list.reduce((a, p) => a + p.receivable, 0),
        }))
        .sort((a, b) => b.sub - a.sub);

    // 付款狀態 badge 用 crm.css 既有的三個 class（同一套詞彙不畫第三份色票）
    const badge = (st) => {
        const s = ['未到帳', '部分到帳', '全額到帳'].includes(st) ? st : '未到帳';
        return `<span class="crm-badge crm-pay-${s}">${esc(st || '未到帳')}</span>`;
    };
    const body = ordered.map((g) => `
        <tr style="background:#242424;">
            <td colspan="4" style="color:#ddd;font-weight:600;">${esc(g.client)}
                <span style="color:#666;font-size:11px;">（${g.list.length} 案）</span></td>
            <td style="text-align:right;color:#fbbf24;font-weight:600;">$${fmtNum(g.sub)}</td>
            <td></td>
        </tr>
        ${g.list.map((p) => `
        <tr style="cursor:pointer;" onclick="window._finRecv.open('${p.id}')"
            title="到執行專案開啟這一案">
            <td style="padding-left:18px;">${esc(p.name)}</td>
            <td style="color:#888;white-space:nowrap;">${esc(p.close_date || '未結案')}</td>
            <td style="text-align:right;">$${fmtNum(p.contract)}</td>
            <td style="text-align:right;color:#86efac;">${p.received ? '$' + fmtNum(p.received) : ''}</td>
            <td style="text-align:right;color:${p.receivable < 0 ? '#fca5a5' : '#eee'};font-weight:600;">$${fmtNum(p.receivable)}</td>
            <td>${badge(p.payment_status)}</td>
        </tr>`).join('')}`).join('');

    _c.innerHTML = `
        ${_pettyBlock(petty)}
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px;">
            <h2 style="color:#eee;margin:0;font-size:18px;">📥 應收帳款</h2>
            <span style="color:#888;font-size:12px;">${rows.length} 案・應收合計
                <b style="color:#fbbf24;">$${fmtNum(total)}</b>${over.length
                    ? `・溢收 ${over.length} 案（紅字，通常是表上帳不平）` : ''}</span>
        </div>
        <div style="max-height:calc(100vh - 240px);overflow-y:auto;background:#202020;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                <th>專案</th><th>結案日</th>
                <th style="text-align:right;">營收</th>
                <th style="text-align:right;">已收</th>
                <th style="text-align:right;">應收</th><th>狀態</th></tr></thead>
            <tbody>${body || '<tr><td colspan="6" style="color:#666;padding:16px;">（沒有未收的案子 🎉）</td></tr>'}</tbody>
        </table></div>
        <div style="color:#666;font-size:11px;margin-top:8px;">
            點任一列到「執行專案」開啟該案。收款＝到收支明細記收入並勾選專案，金額到齊會自動變「全額到帳」並離開這張表。</div>`;
}

const _fr2 = (window._finRecv = window._finRecv || {});

_fr2.reload = () => {
    // 重試鈕：容器還在時整段重跑（render 本身冪等）
    if (_c) render(_c, { isCurrent: _isCurrent });
};

_fr2.open = (id) => {
    // 交棒同專案管理→執行專案那條路：projects.js 的 render 收尾會接住並開啟
    sessionStorage.setItem('omgJumpLedgerProject', id);
    document.querySelector("#finance-nav [data-subview='projects']")?.click();
};
