/**
 * 專案詳情「收付款」分頁（owner 2026-09-04：人員配置改成收付款、與發票整合，用業務的觀點——
 * 這一案的錢走到哪、下一步該做什麼，一頁看完、一頁做完）。
 *
 * 這一頁**不新增資料**：人的正本＝派工、錢的正本＝費用配置／請款單／發票／收支列。它只是同一份資料的業務視角：
 *   狀態列（合約／已開發票／已收／未收／應付／已付／未付／毛利）＋ 下一步提示（規則產生）
 *   收款區＝本案發票（crm-projects-invoices 原分頁整個嵌進來）
 *   付款區＝執行人員（crm-projects-finance._loadCostStaff，三顆建立鈕與付款動作都是它的）＋ 預支款
 *   派工設定（收著；派工＝人的正本，tabs/proposals/staff-view）
 *   掛在本案的收支（收支明細裡掛到本案的列）
 *   結案檢查（四項；先只警告不硬擋——owner 沒拍板前不擋人）
 *
 * 金額權限：沒有 money_view 的人看得到狀態字（幾張、到帳了沒、誰還沒請款），看不到金額；
 * 執行人員那張表本來就由 moneyGate 擋（錢的正本跟著金額權走）。
 */
import { crmFetch as _fetch, esc as _esc, fmtNum, canSeeMoney, groupCostStaff } from './crm-utils.js';
import { state } from './crm-projects-state.js';
import { loadInvoicesTab } from './crm-projects-invoices.js';
import { _loadCostStaff, _loadAdvances, _loadProjectStaff } from './crm-projects-finance.js';

const INVOICE_VOID = new Set(['作廢']);
const PAID = '已付款';

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
        contract, invoiced, invoiceCount: inv.length, received, fee, unreceived,
        status: p.payment_status || '未到帳',
        payable, paid, requested, unrequested, unrequestedGroups, paidCount: pays.filter((x) => x.payment_status === PAID).length,
        payCount: pays.length, margin: contract - payable, groups, others, kai,
        uncollected: inv.filter((i) => !/已收款|待撥款|已撥款/.test(i.payment_status || '')),
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

/** 結案檢查四項：先只警告（owner 未拍板硬擋）。 */
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

let _cur = null;

function _strip(s, money) {
    const m = (n) => (money ? '$' + fmtNum(n) : '—');
    const st = s.status;
    const stCls = st === '全額到帳' ? 'good' : st === '部分到帳' ? 'warn' : 'bad';
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

function _hints(list) {
    const go = { invoice: ['開發票', () => window._projInv?.create?.()], invoices: ['看發票', () => document.getElementById('proj-pay-invoices')?.scrollIntoView({ behavior: 'smooth' })],
                 cash: ['看收支', () => document.getElementById('proj-pay-cash')?.scrollIntoView({ behavior: 'smooth' })], staff: ['看付款', () => document.getElementById('proj-cost-staff')?.scrollIntoView({ behavior: 'smooth' })] };
    return `<div class="ppay-next"><span class="ppay-next-t">下一步</span>${list.map((h, i) => `<span class="ppay-hint ${h.level}">${_esc(h.text)}${h.act && go[h.act] ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-ppay-act="${i}">${go[h.act][0]}</button>` : ''}</span>`).join('')}</div>`;
}

function _cashHtml(entries, money) {
    if (!entries) return '';
    if (!entries.length) return '<div class="crm-empty" style="padding:8px 0;font-size:12px;">收支明細裡還沒有掛到本案的列</div>';
    const m = (n) => (money ? fmtNum(n) : '—');
    const dep = entries.reduce((a, e) => a + Number(e.deposit || 0), 0), exp = entries.reduce((a, e) => a + Number(e.expense || 0), 0);
    return `<table class="crm-table" style="width:100%;font-size:12px;">
        <thead><tr><th>日期</th><th>摘要</th><th style="text-align:right;">存入</th><th style="text-align:right;">支出</th><th>對到</th></tr></thead>
        <tbody>${entries.map((e) => `<tr>
            <td style="white-space:nowrap;">${_esc((e.entry_date || '').slice(0, 10))}</td>
            <td>${_esc(e.summary || '')}</td>
            <td style="text-align:right;color:#86efac;">${e.deposit ? m(e.deposit) : ''}</td>
            <td style="text-align:right;color:#fca5a5;">${e.expense ? m(e.expense) : ''}</td>
            <td style="color:#888;">${_esc(e.invoice_title || e.payment_label || e.project_pay_label || '')}</td></tr>`).join('')}
        <tr style="font-weight:700;"><td colspan="2">合計</td><td style="text-align:right;color:#86efac;">${m(dep)}</td><td style="text-align:right;color:#fca5a5;">${m(exp)}</td><td></td></tr></tbody></table>`;
}

function _checksHtml(checks) {
    return `<div class="ppay-checks">${checks.map((c) => `<div class="ppay-ck ${c.ok ? 'ok' : 'no'}"><span class="ppay-dot"></span><div><div class="ppay-ck-l">${_esc(c.label)}</div><div class="ppay-ck-s">${_esc(c.sub)}</div></div></div>`).join('')}</div>`;
}

/** 狀態列＋提示＋掛帳收支＋結案檢查 —— 請款／付款／開票之後由 window._projPay.refresh() 重算。 */
export async function loadPayStrip(projectId) {
    const strip = document.getElementById('proj-pay-strip');
    if (!strip || !projectId) return;
    const money = canSeeMoney();
    const ent = state.projects.find((p) => p.id === projectId)?.entity === 'mine' ? '&entity=mine' : '';
    const q = (path) => _fetch(path).catch(() => null);
    const [proj, inv, pays, lines, cash, adv, exp] = await Promise.all([
        q('/projects/' + projectId), q('/invoices?project_id=' + encodeURIComponent(projectId)),
        q('/payments?project_id=' + encodeURIComponent(projectId) + ent),
        q(`/projects/${projectId}/cost-lines`), q('/cash-entries?project_id=' + encodeURIComponent(projectId) + ent),
        q('/payments/advances?project_id=' + encodeURIComponent(projectId)), q(`/projects/${projectId}/expenses`)]);
    if (state.selectedId !== projectId) return;      // 切走了就別畫到別的案上
    const s = payStatus(proj, inv?.invoices, pays?.payments, lines?.cost_lines);
    _cur = s;
    strip.innerHTML = _strip(s, money) + _hints(nextSteps(s, { money }));
    strip.querySelectorAll('[data-ppay-act]').forEach((b) => b.addEventListener('click', () => {
        const h = nextSteps(s, { money })[Number(b.dataset.ppayAct)];
        const go = { invoice: () => window._projInv?.create?.(), invoices: () => document.getElementById('proj-pay-invoices')?.scrollIntoView({ behavior: 'smooth' }),
                     cash: () => document.getElementById('proj-pay-cash')?.scrollIntoView({ behavior: 'smooth' }), staff: () => document.getElementById('proj-cost-staff')?.scrollIntoView({ behavior: 'smooth' }) };
        go[h.act]?.();
    }));
    const cashHost = document.getElementById('proj-pay-cash');
    if (cashHost) cashHost.innerHTML = cash ? _cashHtml(cash.entries || [], money) : '<div class="crm-empty" style="padding:8px 0;font-size:12px;">沒有權限看收支明細</div>';
    const ck = document.getElementById('proj-pay-check');
    if (ck) ck.innerHTML = _checksHtml(closingChecks(s, adv?.advances, exp?.expenses));
}

/** 整個分頁：狀態列 → 發票（原分頁嵌入）→ 執行人員／預支 → 派工 → 掛帳收支 → 結案檢查。 */
export async function loadPayTab(projectId) {
    if (!projectId) return;
    loadPayStrip(projectId);
    loadInvoicesTab(projectId, 'proj-pay-invoices');
    _loadCostStaff(projectId);
    _loadAdvances(projectId);
    const staff = document.getElementById('proj-staff-list');
    if (staff && staff.closest('details')?.open) _loadProjectStaff(projectId);
}

/** 完稿結案分頁頂端的「收付檢查」提示條（規劃第二期：結案檢查與完稿結案連動；只提醒不擋）。 */
export async function loadClosingBanner(projectId, host) {
    if (!host || !projectId) return;
    let box = host.querySelector('.ppay-closing');
    if (!box) { box = document.createElement('div'); box.className = 'ppay-closing'; host.insertAdjacentElement('afterbegin', box); }
    box.innerHTML = '<div class="ppay-sh"><span class="ppay-h">收付檢查</span><span class="ppay-sub">載入中…</span></div>';
    const q = (path) => _fetch(path).catch(() => null);
    const [proj, inv, pays, lines, adv, exp] = await Promise.all([
        q('/projects/' + projectId), q('/invoices?project_id=' + encodeURIComponent(projectId)),
        q('/payments?project_id=' + encodeURIComponent(projectId)), q(`/projects/${projectId}/cost-lines`),
        q('/payments/advances?project_id=' + encodeURIComponent(projectId)), q(`/projects/${projectId}/expenses`)]);
    if (state.selectedId !== projectId) return;
    const s = payStatus(proj, inv?.invoices, pays?.payments, lines?.cost_lines);
    const checks = closingChecks(s, adv?.advances, exp?.expenses);
    const open = checks.filter((c) => !c.ok);
    box.innerHTML = `<div class="ppay-sh"><span class="ppay-h">收付檢查</span>
        <span class="ppay-sub">${open.length ? `${open.length} 項還沒結清 —— 先提醒，不擋結案` : '收付都結清了'}</span>
        <span style="margin-left:auto;"><button class="crm-btn crm-btn-secondary crm-btn-sm" data-ppay-go-pay>到收付款分頁</button></span></div>
        ${_checksHtml(checks)}`;
    box.querySelector('[data-ppay-go-pay]')?.addEventListener('click', () => document.querySelector('#proj-detail-tabs .crm-tab[data-tab="team"]')?.click());
}

/** 推到結案前的「軟擋」（owner 2026-09-04 採建議：不硬擋，但沒結清要列出來讓人確認）。
 *  回 true＝可以推；false＝使用者取消。抓不到資料（沒權限）就放行——不能因為看不到錢就不准結案。 */
export async function confirmClosing(projectId) {
    if (!projectId) return true;
    const q = (path) => _fetch(path).catch(() => null);
    const [proj, inv, pays, lines, adv, exp] = await Promise.all([
        q('/projects/' + projectId), q('/invoices?project_id=' + encodeURIComponent(projectId)),
        q('/payments?project_id=' + encodeURIComponent(projectId)), q(`/projects/${projectId}/cost-lines`),
        q('/payments/advances?project_id=' + encodeURIComponent(projectId)), q(`/projects/${projectId}/expenses`)]);
    if (!proj) return true;
    const s = payStatus(proj, inv?.invoices, pays?.payments, lines?.cost_lines);
    const open = closingChecks(s, adv?.advances, exp?.expenses).filter((c) => !c.ok);
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
    refresh: () => { if (state.selectedId) loadPayStrip(state.selectedId); },
    staff: () => { if (state.selectedId) _loadProjectStaff(state.selectedId); },
    current: () => _cur,
};
