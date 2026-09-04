/**
 * 專案詳情「收付款」分頁（owner 2026-09-04：人員配置改成收付款、與發票整合，用業務的觀點——
 * 這一案的錢走到哪、下一步該做什麼，一頁看完、一頁做完；版面照示範頁，派工拿掉）。
 *
 * 這一頁**不新增資料**：錢的正本＝費用配置／請款單／發票／收支列。它只是同一份資料的業務視角：
 *   資訊列（客戶／類型／狀態／AM／PM／稅率）
 *   狀態列（合約／已開發票／已收／未收／應付／已付／未付／毛利）＋ 下一步提示（規則產生）
 *   收款＝本案發票一張一列（號碼／日期／抬頭／面額／狀態／收到／入帳日）＋ 開發票／標已收款
 *   付款＝執行人員一人一列（角色／金額／狀態鏈 未請款→已請款→已付款）＋ 委外／雜支／代墊／預支 各一列
 *         動作沿用 crm-projects-finance 那組（_costCreatePayment／_costPayBtns／_costCreateAdvance）
 *   掛在本案的收支（收支明細裡掛到本案的列）
 *   結案檢查（四項；軟擋：推到結案時沒結清要人確認，見 confirmClosing）
 *
 * 金額權限：沒有 money_view 的人看得到狀態字（幾張、到帳了沒、誰還沒請款），看不到金額。
 * 發票模組（crm-projects-invoices）仍載進一個藏著的容器：開發票視窗與 setMeta／del 靠它的狀態。
 */
import { crmFetch as _fetch, esc as _esc, fmtNum, canSeeMoney, groupCostStaff, invoicePayBadge } from './crm-utils.js';
import { state } from './crm-projects-state.js';
import { loadInvoicesTab } from './crm-projects-invoices.js';

const INVOICE_VOID = new Set(['作廢']);
const PAID = '已付款';
const COLLECTED_RE = /已收款|待撥款|已撥款/;

/** 一案的收付狀態：純算式（給狀態列、提示、結案檢查三處共用）。 */
export function payStatus(project, invoices, payments, costLines) {
    const p = project || {};
    const inv = (invoices || []).filter((i) => !INVOICE_VOID.has(i.issue_status) && !INVOICE_VOID.has(i.payment_status) && (i.payment_type || '收款') === '收款');
    const pays = (payments || []).filter((x) => !x.is_advance);
    const contract = Number(p.contract_amount || 0);
    const invoiced = inv.reduce((a, i) => a + Number(i.amount_total || 0), 0);
    const received = Number(p.amount_received || 0), fee = Number(p.transfer_fee || 0);
    const unreceived = p.amount_receivable != null ? Number(p.amount_receivable) : contract - received - fee;
    const groups = groupCostStaff(costLines || [], pays);
    const matched = new Set(groups.filter((g) => g.payment).map((g) => g.payment.id));
    const others = pays.filter((x) => !matched.has(x.id));
    const payable = groups.reduce((a, g) => a + g.subtotal, 0) + others.reduce((a, x) => a + Number(x.amount || 0), 0);
    const paid = pays.filter((x) => x.payment_status === PAID).reduce((a, x) => a + Number(x.amount || 0), 0);
    const requested = pays.filter((x) => x.payment_status !== PAID).reduce((a, x) => a + Number(x.amount || 0), 0);
    const unrequestedGroups = groups.filter((g) => !g.payment);
    const unrequested = unrequestedGroups.reduce((a, g) => a + g.subtotal, 0);
    const kai = inv.filter((i) => /代開/.test(i.category || ''));
    return {
        invoices: inv, contract, invoiced, invoiceCount: inv.length, received, fee, unreceived,
        status: p.payment_status || '未到帳',
        payable, paid, requested, unrequested, unrequestedGroups, paidCount: pays.filter((x) => x.payment_status === PAID).length,
        payCount: pays.length, margin: contract - payable, groups, others, kai,
        uncollected: inv.filter((i) => !COLLECTED_RE.test(i.payment_status || '')),
    };
}

/** 下一步提示（規則）：回 [{level, text, act}]，level＝bad／warn／good。 */
export function nextSteps(s, opts = {}) {
    const money = opts.money !== false;
    const m = (n) => (money ? '$' + fmtNum(n) : '');
    const out = [];
    if (!s.invoiceCount) out.push({ level: 'bad', text: '發票還沒開', act: 'invoice' });
    if (s.uncollected.length) out.push({ level: 'warn', text: `${s.uncollected.length} 張發票還沒收到款${money && s.unreceived > 0 ? '，未收 ' + m(s.unreceived) : ''}`, act: 'invoices' });
    if (s.invoiceCount && !s.uncollected.length && s.unreceived > 0) out.push({ level: 'warn', text: `發票都標已收，但帳上未收還有 ${m(s.unreceived)} —— 匯款列可能還沒連結到本案`, act: 'cash' });
    if (s.unrequestedGroups.length) out.push({ level: 'warn', text: `${s.unrequestedGroups.length} 人還沒請款${money ? '，合計 ' + m(s.unrequested) : ''}：${s.unrequestedGroups.map((g) => g.name).join('、')}`, act: 'staff' });
    if (s.requested > 0 || (s.payCount - s.paidCount) > 0) out.push({ level: 'warn', text: `${s.payCount - s.paidCount} 張應付款還沒付${money ? '，合計 ' + m(s.requested) : ''}`, act: 'staff' });
    if (!out.length) out.push({ level: 'good', text: '收付都完成了，可以結案', act: '' });
    return out;
}

/** 結案檢查四項（軟擋：confirmClosing 用同一份）。 */
export function closingChecks(s, advances, expenses) {
    const adv = advances || [], exp = expenses || [];
    return [
        { label: '發票全開且已收', ok: s.invoiceCount > 0 && !s.uncollected.length && s.unreceived <= 0,
          sub: s.invoiceCount ? `${s.invoiceCount} 張${s.uncollected.length ? '，' + s.uncollected.length + ' 張未收' : '，已收齊'}` : '沒有發票' },
        { label: '應付全付', ok: s.payCount > 0 && s.paidCount === s.payCount && !s.unrequestedGroups.length,
          sub: s.payCount ? `${s.paidCount} / ${s.payCount} 張已付${s.unrequestedGroups.length ? '，' + s.unrequestedGroups.length + ' 人未請款' : ''}` : (s.groups.length ? `${s.groups.length} 人都還沒請款` : '沒有應付') },
        { label: '雜支結清', ok: exp.every((e) => e.claim_id || e.status === '已付款' || e.paid), sub: exp.length ? `${exp.length} 筆` : '沒有雜支' },
        { label: '預支款結清', ok: adv.every((a) => a.is_settled || a.settled), sub: adv.length ? `${adv.length} 筆` : '沒有預支' },
    ];
}

// ── 畫面 ──────────────────────────────────────────────────────
let _cur = null;
const _d = (v) => (v ? String(v).slice(0, 10) : '');
const _md = (v) => { const s = _d(v); return s ? s.slice(5).replace('-', '/') : ''; };
const _q = (path) => _fetch(path).catch(() => null);
const _sq = (s) => _esc(s || '').replace(/'/g, "\\'");

async function _load(projectId) {
    const ent = state.projects.find((p) => p.id === projectId)?.entity === 'mine' ? '&entity=mine' : '';
    const [proj, inv, pays, lines, cash, adv, exp] = await Promise.all([
        _q('/projects/' + projectId), _q('/invoices?project_id=' + encodeURIComponent(projectId)),
        _q('/payments?project_id=' + encodeURIComponent(projectId) + ent),
        _q(`/projects/${projectId}/cost-lines`), _q('/cash-entries?project_id=' + encodeURIComponent(projectId) + ent),
        _q('/payments/advances?project_id=' + encodeURIComponent(projectId)), _q(`/projects/${projectId}/expenses`)]);
    return { proj, inv: inv?.invoices || [], pays: pays?.payments || [], lines: lines?.cost_lines || [],
             cash: cash ? (cash.entries || []) : null, adv: adv?.advances || [], exp: exp?.expenses || [] };
}

function _strip(s, money) {
    const m = (n) => (money ? '$' + fmtNum(n) : '—');
    const st = s.status;
    const tile = (l, v, sub, cls = '') => `<div class="ppay-kpi ${cls}"><div class="ppay-l">${l}</div><div class="ppay-v">${v}</div><div class="ppay-s">${sub || ''}</div></div>`;
    return `<div class="ppay-kpis">
        ${tile('合約金額', m(s.contract), '含稅', 'hl')}
        ${tile('已開發票', m(s.invoiced), `${s.invoiceCount} 張`)}
        ${tile('已收（含匯費）', m(s.received + s.fee), money && s.fee ? '匯費 ' + m(s.fee) : '', 'good')}
        ${tile('未收', m(Math.max(s.unreceived, 0)), `<span class="crm-badge crm-pay-${st === '全額到帳' ? '全額到帳' : '未到帳'}">${_esc(st)}</span>`, s.unreceived > 0 ? 'bad' : '')}
        ${tile('應付合計', m(s.payable), `${s.groups.length} 人${s.others.length ? '＋' + s.others.length + ' 筆其他' : ''}`)}
        ${tile('已付', m(s.paid), `${s.paidCount} 張`, 'good')}
        ${tile('未付', m(s.payable - s.paid), money ? `已請未付 ${m(s.requested)}` : `${s.payCount - s.paidCount} 張`, s.payable - s.paid > 0 ? 'warn' : '')}
        ${money ? tile('毛利', m(s.margin), s.contract ? Math.round(s.margin / s.contract * 100) + '%' : '', s.margin >= 0 ? 'good' : 'bad') : ''}
    </div>`;
}

const _GO = {
    invoice: ['開發票', () => window._projInv?.create?.()],
    invoices: ['看發票', () => document.getElementById('proj-pay-recv')?.scrollIntoView({ behavior: 'smooth' })],
    cash: ['看收支', () => document.getElementById('proj-pay-cash')?.scrollIntoView({ behavior: 'smooth' })],
    staff: ['看付款', () => document.getElementById('proj-pay-pay')?.scrollIntoView({ behavior: 'smooth' })],
};
function _hints(list) {
    return `<div class="ppay-next"><span class="ppay-next-t">下一步</span>${list.map((h) => `<span class="ppay-hint ${h.level}">${_esc(h.text)}${h.act && _GO[h.act] ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-ppay-act="${h.act}">${_GO[h.act][0]}</button>` : ''}</span>`).join('')}</div>`;
}

/** 收款：本案發票一張一列（同示範頁）。 */
function _recvHtml(s, money) {
    const m = (n) => (money ? '$' + fmtNum(n) : '—');
    const rows = s.invoices.map((i) => {
        const got = Number(i.collected || 0);
        const collected = COLLECTED_RE.test(i.payment_status || '');
        return `<tr>
            <td class="num">${_esc(i.invoice_number || '（未開立）')}</td>
            <td class="num">${_esc(_d(i.invoice_date))}</td>
            <td>${_esc(i.title || '')}${i.category && i.category !== '專案' ? ` <span class="crm-badge" style="background:#2a2a2a;color:#9ca3af;">${_esc(i.category)}</span>` : ''}</td>
            <td class="r num">${m(i.amount_total || 0)}</td>
            <td>${invoicePayBadge(i.payment_status)}</td>
            <td class="r num">${got ? m(got) : '<span class="dim">—</span>'}</td>
            <td class="num">${_esc(_d(i.last_paid_date)) || '<span class="dim">—</span>'}</td>
            <td><div class="ppay-acts">
                ${collected ? '' : `<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projPay.markCollected('${_esc(i.id)}')">標已收款</button>`}
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projInv.del('${_esc(i.id)}')" title="刪除這張發票">刪除</button>
            </div></td></tr>`;
    }).join('');
    return `<div class="ppay-tbl"><table class="ppay-table">
        <thead><tr><th>發票號碼</th><th>日期</th><th>抬頭／摘要</th><th class="r">面額</th><th>狀態</th><th class="r">收到</th><th>入帳日</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="8" class="dim" style="padding:12px;">還沒開發票</td></tr>'}
        <tr class="tot"><td colspan="3">合計</td><td class="r num">${m(s.invoiced)}</td><td></td><td class="r num">${m(s.received)}</td><td colspan="2"></td></tr></tbody></table></div>
        ${s.kai.length ? `<p class="ppay-note">代開發票：客戶匯的是面額，公司留代開費，其餘要匯給代開人 —— 在收支明細的那筆收款列按「請款」整筆請過去。</p>` : ''}`;
}

/** 狀態鏈：未請款 → 已請款（日期）→ 已付款（日期）。 */
function _chain(p) {
    const stage = !p ? 1 : p.payment_status === PAID ? 3 : 2;
    const st = (i, label, date) => `<span class="st ${stage > i ? 'done' : stage === i ? (i === 1 ? 'on' : 'wait') : ''}">${label}${date ? ` <span class="d">${_esc(date)}</span>` : ''}</span>`;
    return `<span class="ppay-chain">${st(1, '未請款')}<span class="sep ${stage > 1 ? 'done' : ''}"></span>${st(2, '已請款', p ? _md(p.request_date) : '')}<span class="sep ${stage > 2 ? 'done' : ''}"></span>${st(3, '已付款', p ? _md(p.payment_date) : '')}</span>`;
}

/** 付款：一人一列＋其他請款單＋預支款（同示範頁）。動作沿用 crm-projects-finance 那組。 */
function _payHtml(s, adv, money) {
    const m = (n) => (money ? '$' + fmtNum(n) : '—');
    const rows = [];
    for (const g of s.groups) {
        const p = g.payment;
        const items = g.items.join('、');
        const advTag = p && p.advance_by ? `<span style="color:#fb923c;font-size:10px;margin-right:6px;" title="這筆費用由 ${_esc(p.payee_name || '')} 先代墊，公司要還的是他">${_esc(p.payee_name || '')} 代墊</span>` : '';
        const acts = p
            ? `${advTag}<span class="ppay-link" onclick="window._costViewPayment('${_esc(p.id)}')">看單</span>${window._costPayBtns ? window._costPayBtns(p) : ''}`
            : `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._costCreatePayment('${_sq(g.name)}',${g.subtotal},'${_sq(items)}','應付款')">請款</button>
               <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costCreatePayment('${_sq(g.name)}',${g.subtotal},'${_sq(items)}','已付款')">現金已付款</button>
               <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costCreatePayment('${_sq(g.name)}',${g.subtotal},'${_sq(items)}','應付款',true)" title="這筆費用由別人先代墊 —— 收款人改成代墊人">費用已代墊</button>`;
        rows.push(`<tr><td class="who">${_esc(g.name)}</td><td class="role">${_esc(items)}</td><td class="r num">${m(g.subtotal)}</td><td>${_chain(p)}</td><td><div class="ppay-acts">${acts}</div></td></tr>`);
    }
    for (const x of s.others) {
        rows.push(`<tr><td class="who">${_esc(x.payee_name || '—')} <span class="crm-badge" style="background:#2a2a2a;color:#9ca3af;">${_esc(x.category || '其他')}</span></td>
            <td class="role">${_esc(x.summary || '')}${x.advance_by ? ` · <span style="color:#fb923c;">代墊（費用歸屬 ${_esc(x.advance_by)}）</span>` : ''}</td>
            <td class="r num">${m(x.amount || 0)}</td><td>${_chain(x)}</td>
            <td><div class="ppay-acts"><span class="ppay-link" onclick="window._costViewPayment('${_esc(x.id)}')">看單</span>${window._costPayBtns ? window._costPayBtns(x) : ''}</div></td></tr>`);
    }
    for (const a of adv) {
        const st = a.is_settled ? '<span style="color:#86efac;font-size:12px;">已結清</span>'
            : a.is_paid ? `<span style="color:#fb923c;font-size:12px;">已發款${money && a.balance ? '，餘額 ' + m(a.balance) : ''}</span>`
            : '<span style="color:#fbbf24;font-size:12px;">未發款</span>';
        rows.push(`<tr><td class="who">${_esc(a.payee_name || '')} <span class="crm-badge" style="background:#2a2a2a;color:#9ca3af;">預支</span></td>
            <td class="role">${_esc(a.summary || '')}</td><td class="r num">${m(a.amount || 0)}</td><td>${st}</td>
            <td><div class="ppay-acts"><button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._advShareLink('${_esc(a.id)}')">分享登記連結</button>
                ${a.is_settled ? '' : `<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._advDeleteAdvance('${_esc(a.id)}','${_sq(a.payee_name)}')">刪除</button>`}</div></td></tr>`);
    }
    const proj = state.projects.find((p) => p.id === state.selectedId);
    const budget = s.contract ? Math.round(s.contract * (1 - Number(proj?.profit_target_pct ?? 20) / 100)) : 0;
    return `<div class="ppay-tbl"><table class="ppay-table">
        <thead><tr><th>人員</th><th>角色／項目</th><th class="r">金額</th><th>狀態</th><th></th></tr></thead>
        <tbody>${rows.join('') || '<tr><td colspan="5" class="dim" style="padding:12px;">還沒有執行人員（預算結算分頁的費用配置還沒填人）</td></tr>'}
        <tr class="tot"><td colspan="2">合計${money && budget ? ` <span class="dim" style="font-weight:400;">額度 ${m(budget)}</span>` : ''}</td>
            <td class="r num">${m(s.payable)}</td><td colspan="2"><span class="dim" style="font-weight:400;">已付 ${m(s.paid)} · 已請未付 ${m(s.requested)} · 未請 ${m(s.unrequested)}</span></td></tr></tbody></table></div>`;
}

function _cashHtml(entries, money) {
    if (!entries) return '<div class="crm-empty" style="padding:8px 0;font-size:12px;">沒有權限看收支明細</div>';
    if (!entries.length) return '<div class="crm-empty" style="padding:8px 0;font-size:12px;">收支明細裡還沒有掛到本案的列</div>';
    const m = (n) => (money ? fmtNum(n) : '—');
    const dep = entries.reduce((a, e) => a + Number(e.deposit || 0), 0), exp = entries.reduce((a, e) => a + Number(e.expense || 0), 0);
    return `<div class="ppay-tbl"><table class="ppay-table">
        <thead><tr><th>日期</th><th>摘要</th><th class="r">存入</th><th class="r">支出</th><th>對到</th></tr></thead>
        <tbody>${entries.map((e) => `<tr>
            <td class="num">${_esc(_d(e.entry_date))}</td><td>${_esc(e.summary || '')}</td>
            <td class="r num" style="color:#86efac;">${e.deposit ? m(e.deposit) : ''}</td>
            <td class="r num" style="color:#fca5a5;">${e.expense ? m(e.expense) : ''}</td>
            <td class="dim">${_esc(e.invoice_title || e.payment_label || e.project_pay_label || '')}</td></tr>`).join('')}
        <tr class="tot"><td colspan="2">合計</td><td class="r num" style="color:#86efac;">${m(dep)}</td><td class="r num" style="color:#fca5a5;">${m(exp)}</td><td></td></tr></tbody></table></div>`;
}

function _checksHtml(checks) {
    return `<div class="ppay-checks">${checks.map((c) => `<div class="ppay-ck ${c.ok ? 'ok' : 'no'}"><span class="ppay-dot"></span><div><div class="ppay-ck-l">${_esc(c.label)}</div><div class="ppay-ck-s">${_esc(c.sub)}</div></div></div>`).join('')}</div>`;
}

function _metaHtml(p) {
    const proj = p || {};
    return `<div class="ppay-meta">
        <span>客戶 <b>${_esc(proj.client_short_name || '—')}</b></span>
        <span>類型 <b>${_esc(proj.project_type || '—')}</b></span>
        <span>狀態 <b>${_esc(proj.status || '—')}</b></span>
        <span class="ppay-meta-ampm" data-ppay-ampm></span>
        <span>稅率 <b>${_esc(String(proj.tax_rate ?? 5))}%</b></span>
        ${(proj.entity || 'parent') === 'mine' ? '<span class="crm-badge" style="background:#2a2a2a;color:#9ca3af;">私帳</span>' : ''}
    </div>`;
}

/** 整頁重畫（動作做完也走這裡：window._projPay.refresh）。 */
export async function loadPayTab(projectId) {
    const host = document.getElementById('proj-pay-root');
    if (!host || !projectId) return;
    const money = canSeeMoney();
    const d = await _load(projectId);
    if (state.selectedId !== projectId) return;      // 切走了就別畫到別的案上
    const s = payStatus(d.proj, d.inv, d.pays, d.lines);
    _cur = s;
    // AM／PM 可編輯格由 detail.js 畫在 #proj-pay-ampm-src；第一次在 host 外、重畫時已經搬進 host 裡 ——
    // 先抓住再覆寫 innerHTML，不然第二次 loadPayTab 會把它連同舊畫面一起清掉
    const ampm = document.getElementById('proj-pay-ampm-src');
    host.innerHTML = `
        ${_metaHtml(d.proj)}
        ${_strip(s, money)}
        ${_hints(nextSteps(s, { money }))}
        <div class="ppay-sec" id="proj-pay-recv">
            <div class="ppay-sh"><span class="ppay-h">收款</span><span class="ppay-sub">本案的發票，和它連到的匯款</span>
                <span class="ppay-sh-act"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projInv.create()">開發票</button></span></div>
            ${_recvHtml(s, money)}
        </div>
        <div class="ppay-sec" id="proj-pay-pay">
            <div class="ppay-sh"><span class="ppay-h">付款</span><span class="ppay-sub">人員與費用一列一人，狀態鏈：未請款 → 已請款 → 已付款</span>
                <span class="ppay-sh-act">
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" data-ppay-go-budget title="人員與金額在預算結算分頁的費用配置">加人員</button>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costCreatePayment('',0,'委外','應付款')">委外</button>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" data-ppay-go-budget title="行政雜支在預算結算分頁登記">行政雜支</button>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costCreateAdvance()">預支</button>
                </span></div>
            ${_payHtml(s, d.adv, money)}
        </div>
        <div class="ppay-sec" id="proj-pay-cash">
            <div class="ppay-sh"><span class="ppay-h">掛在本案的收支</span><span class="ppay-sub">收支明細裡掛到本案（或本案發票）的列</span></div>
            ${_cashHtml(d.cash, money)}
        </div>
        <div class="ppay-sec" id="proj-pay-check">
            <div class="ppay-sh"><span class="ppay-h">結案檢查</span><span class="ppay-sub">四項都綠才算收付結清（推到結案時沒結清會先問，不擋結案）</span></div>
            ${_checksHtml(closingChecks(s, d.adv, d.exp))}
        </div>
        <div id="proj-pay-invoices" hidden></div>`;
    const slot = host.querySelector('[data-ppay-ampm]');
    if (ampm && slot) { slot.appendChild(ampm); ampm.style.display = 'inline-flex'; }
    host.querySelectorAll('[data-ppay-act]').forEach((b) => b.addEventListener('click', () => _GO[b.dataset.ppayAct]?.[1]?.()));
    host.querySelectorAll('[data-ppay-go-budget]').forEach((b) => b.addEventListener('click', () => document.querySelector('#proj-detail-tabs .crm-tab[data-tab="finance"]')?.click()));
    // 發票模組載進藏著的容器：開發票視窗、setMeta／del 都靠它的狀態
    loadInvoicesTab(projectId, 'proj-pay-invoices');
}

/** 完稿結案分頁頂端的「收付檢查」提示條（只提醒不擋）。 */
export async function loadClosingBanner(projectId, host) {
    if (!host || !projectId) return;
    let box = host.querySelector('.ppay-closing');
    if (!box) { box = document.createElement('div'); box.className = 'ppay-closing'; host.insertAdjacentElement('afterbegin', box); }
    box.innerHTML = '<div class="ppay-sh"><span class="ppay-h">收付檢查</span><span class="ppay-sub">載入中…</span></div>';
    const d = await _load(projectId);
    if (state.selectedId !== projectId) return;
    const s = payStatus(d.proj, d.inv, d.pays, d.lines);
    const checks = closingChecks(s, d.adv, d.exp);
    const open = checks.filter((c) => !c.ok);
    box.innerHTML = `<div class="ppay-sh"><span class="ppay-h">收付檢查</span>
        <span class="ppay-sub">${open.length ? `${open.length} 項還沒結清 —— 先提醒，不擋結案` : '收付都結清了'}</span>
        <span class="ppay-sh-act"><button class="crm-btn crm-btn-secondary crm-btn-sm" data-ppay-go-pay>到收付款分頁</button></span></div>
        ${_checksHtml(checks)}`;
    box.querySelector('[data-ppay-go-pay]')?.addEventListener('click', () => document.querySelector('#proj-detail-tabs .crm-tab[data-tab="team"]')?.click());
}

/** 推到結案前的「軟擋」（owner 2026-09-04 採建議：不硬擋，但沒結清要列出來讓人確認）。
 *  回 true＝可以推；false＝使用者取消。抓不到資料（沒權限）就放行——不能因為看不到錢就不准結案。 */
export async function confirmClosing(projectId) {
    if (!projectId) return true;
    const d = await _load(projectId);
    if (!d.proj) return true;
    const s = payStatus(d.proj, d.inv, d.pays, d.lines);
    const open = closingChecks(s, d.adv, d.exp).filter((c) => !c.ok);
    if (!open.length) return true;
    return confirm(`收付還有 ${open.length} 項沒結清：\n${open.map((c) => `• ${c.label}（${c.sub}）`).join('\n')}\n\n仍要推到結案？`);
}

/** 別的分頁（應付帳款、客戶）直接跳到某案的收付款分頁。 */
window._crmGoToProjectPay = (projectId) => {
    if (!projectId) return;
    if (window._crmGoToProject) window._crmGoToProject(projectId);
    else if (window._projSelect) window._projSelect(projectId);
    setTimeout(() => document.querySelector('#proj-detail-tabs .crm-tab[data-tab="team"]')?.click(), 700);
};

window._projPay = {
    confirmClosing,
    refresh: () => { if (state.selectedId) loadPayTab(state.selectedId); },
    current: () => _cur,
    /** 標已收款：走發票模組的 setMeta（PUT 整包寫回），做完重畫。 */
    markCollected: async (id) => {
        if (!window._projInv?.setMeta) return;
        await window._projInv.setMeta(id, 'payment_status', { value: '已收款' });
        window._projPay.refresh();
    },
};
