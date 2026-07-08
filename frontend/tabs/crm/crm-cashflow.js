/**
 * crm-cashflow.js — 帳務管理 › 💧 現金流 子視圖（BIZ_PLAN B3 + HR_FIN_PLAN F1）
 *
 * 四塊：⚠️ 逾期應收（該催了）→ 📈 90 天週現金流（節點流入−未付請款−固定成本）
 * → 🧩 付款節點管理（含 30/40/30 套模板、狀態循環）→ 🔒 月結鎖帳。
 * API：/api/v1/cashflow/*（master 同源，帶 auth token）；專案清單走既有 /api/v1/crm/projects。
 */

import { esc } from '../crm/crm-utils.js';

const MS_STATUSES = ['未到期', '待請款', '已請款', '已收款'];
const MS_COLORS = { '未到期': '#3f3f46', '待請款': '#78350f', '已請款': '#1e3a5f', '已收款': '#064e3b' };

async function cfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        ...opts,
        headers: {
            'Accept': 'application/json',
            ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
            ...(token ? { 'Authorization': 'Bearer ' + token } : {}),
        },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.json();
}

const _fmt = (n) => (n == null ? '—' : Number(n).toLocaleString());
let _el = null;
let _projects = [];   // [{id, name}]

export async function initCrmCashflowTab() {
    _el = document.getElementById('cf-content');
    window._cashflowRefresh = refresh;
    await refresh();
}

async function refresh() {
    if (!_el) return;
    try {
        const [fc, ms, mc, settings, projs] = await Promise.all([
            cfetch('/api/v1/cashflow/forecast?days=90'),
            cfetch('/api/v1/cashflow/milestones'),
            cfetch('/api/v1/cashflow/month-close'),
            fetch('/api/settings/load').then(r => r.json()).catch(() => ({})),
            cfetch('/api/v1/crm/projects').catch(() => ({ projects: [] })),
        ]);
        _projects = (projs.projects || []).map(p => ({ id: p.id, name: p.name }));
        _el.innerHTML = _renderOverdue(fc) + _renderForecast(fc, settings) + _renderMilestones(ms) + _renderMonthClose(mc);
        _bind();
    } catch (e) {
        _el.innerHTML = `<div style="color:#f87171;padding:24px;">現金流載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _renderOverdue(fc) {
    if (!fc.overdue.length) return '';
    return `<div class="cf-card" style="border-color:#7f1d1d;">
        <h3 style="color:#fca5a5;">⚠️ 逾期應收（${fc.overdue.length} 筆 — 該催了）</h3>
        <table><thead><tr><th>專案</th><th>節點</th><th class="num">金額</th><th>應收日</th><th>狀態</th></tr></thead>
        <tbody>${fc.overdue.map(m => `
            <tr><td>${esc(m.project_name)}</td><td>${esc(m.label)}</td>
                <td class="num cf-neg">$${_fmt(m.amount)}</td>
                <td class="cf-neg">${esc(m.due_date || '')}</td><td>${esc(m.status)}</td></tr>`).join('')}
        </tbody></table></div>`;
}

function _renderForecast(fc, settings) {
    const fixed = (settings.finance || {}).monthly_fixed_costs || fc.fixed_monthly || 0;
    const rows = fc.weeks.map(w => `
        <tr><td>${esc(w.start)} 週</td>
            <td class="num cf-pos">${w.inflow ? '$' + _fmt(w.inflow) : ''}</td>
            <td class="num">${w.outflow ? '$' + _fmt(w.outflow) : ''}</td>
            <td class="num ${w.net < 0 ? 'cf-neg' : ''}">${w.net ? '$' + _fmt(w.net) : ''}</td>
            <td class="num ${w.cum < 0 ? 'cf-neg' : 'cf-pos'}" style="font-weight:600;">$${_fmt(w.cum)}</td></tr>`).join('');
    return `<div class="cf-card">
        <h3>📈 90 天現金流（流入=付款節點、流出=未付請款+固定成本）</h3>
        <table><thead><tr><th>週</th><th class="num">流入</th><th class="num">流出</th><th class="num">淨額</th><th class="num">累計</th></tr></thead>
        <tbody>${rows}</tbody></table>
        <div class="cf-note">
            未排期節點（沒填應收日）合計 <b style="color:#f59e0b;">$${_fmt(fc.unscheduled_inflow)}</b> 未計入上表 —
            節點填上日期預測才準。固定月成本
            <input id="cf-fixed" type="number" value="${fixed}" style="width:110px;" min="0"> 元/月
            <button class="cf-btn ghost" data-cf="save-fixed">存</button>
            （人事+房租+固定支出的月合計，粗估即可）
        </div></div>`;
}

function _renderMilestones(ms) {
    const byProject = {};
    ms.milestones.forEach(m => { (byProject[m.project_id] = byProject[m.project_id] || []).push(m); });
    const groups = Object.entries(byProject).map(([pid, list]) => {
        const rows = list.map(m => `
            <tr><td>${esc(m.label)}</td>
                <td class="num">$${_fmt(m.amount)}</td>
                <td><input type="date" value="${m.due_date || ''}" data-cf="due" data-id="${m.id}" style="width:130px;"></td>
                <td><span class="cf-pill" style="background:${MS_COLORS[m.status] || '#333'};color:#ddd;cursor:pointer;"
                          data-cf="cycle" data-id="${m.id}" data-status="${esc(m.status)}"
                          title="點擊切換狀態">${esc(m.status)}</span></td>
                <td><button class="cf-btn ghost" data-cf="del" data-id="${m.id}" style="padding:2px 8px;">✕</button></td></tr>`).join('');
        return `<div style="margin-bottom:12px;">
            <div style="color:#bbb;font-weight:600;font-size:13px;margin-bottom:4px;">${esc(list[0].project_name || pid)}</div>
            <table><thead><tr><th>節點</th><th class="num">金額</th><th>應收日</th><th>狀態</th><th></th></tr></thead>
            <tbody>${rows}</tbody></table></div>`;
    }).join('');

    const projOpts = _projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    return `<div class="cf-card">
        <h3>🧩 付款節點（訂金 / 期中 / 尾款排程）</h3>
        ${groups || '<div class="cf-note">尚無節點 — 選專案套模板或手動新增。</div>'}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px;">
            <select id="cf-proj" style="max-width:260px;"><option value="">選擇專案…</option>${projOpts}</select>
            <button class="cf-btn" data-cf="template">套 30/40/30 模板</button>
            <input id="cf-new-label" placeholder="節點名" style="width:90px;">
            <input id="cf-new-amount" type="number" placeholder="金額" style="width:110px;" min="0">
            <input id="cf-new-due" type="date" style="width:130px;">
            <button class="cf-btn ghost" data-cf="add">+ 手動新增</button>
        </div>
        <div class="cf-note">狀態點擊循環：未到期 → 待請款 → 已請款 → 已收款。已收款不再計入預測流入。</div>
    </div>`;
}

function _renderMonthClose(mc) {
    const rows = mc.months.map(m => `
        <tr><td>${esc(m.month)}</td>
            <td>${m.locked ? '🔒 已鎖' : `↩︎ 已重開（${esc(m.reopened_by || '')}）`}</td>
            <td class="num cf-pos">$${_fmt((m.snapshot || {}).income)}</td>
            <td class="num">$${_fmt((m.snapshot || {}).expense)}</td>
            <td class="num">${(m.snapshot || {}).entry_count ?? ''}</td>
            <td style="color:#777;">${esc(m.closed_by)} ${esc(m.closed_at)}</td>
            <td>${m.locked
                ? `<button class="cf-btn ghost" data-cf="reopen" data-month="${esc(m.month)}" style="padding:2px 8px;">重開</button>`
                : `<button class="cf-btn ghost" data-cf="close" data-month="${esc(m.month)}" style="padding:2px 8px;">重鎖</button>`}</td></tr>`).join('');
    const prevMonth = (() => {
        const d = new Date(); d.setDate(1); d.setDate(0);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    })();
    return `<div class="cf-card">
        <h3>🔒 月結鎖帳（鎖定月的收支不可增改刪 — 報表數字可重現的地基）</h3>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;">
            <input id="cf-close-month" type="month" value="${prevMonth}">
            <button class="cf-btn" data-cf="close" data-month="">鎖定該月</button>
        </div>
        ${rows ? `<table><thead><tr><th>月份</th><th>狀態</th><th class="num">收入</th><th class="num">支出</th>
            <th class="num">筆數</th><th>鎖帳人/時間</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
            : '<div class="cf-note">尚未鎖過任何月份。建議每月初鎖上個月。</div>'}
    </div>`;
}

function _bind() {
    _el.querySelectorAll('[data-cf]').forEach(node => {
        const act = node.dataset.cf;
        if (act === 'due') {
            node.addEventListener('change', async () => {
                try { await cfetch('/api/v1/cashflow/milestones/' + node.dataset.id, { method: 'PUT', body: { due_date: node.value || '' } }); refresh(); }
                catch (e) { alert('更新失敗: ' + e.message); }
            });
            return;
        }
        node.addEventListener('click', async () => {
            try {
                if (act === 'save-fixed') {
                    const v = parseInt(document.getElementById('cf-fixed').value) || 0;
                    await fetch('/api/settings/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + localStorage.getItem('auth_token') },
                        body: JSON.stringify({ finance: { monthly_fixed_costs: v } }),
                    });
                    refresh();
                } else if (act === 'cycle') {
                    const next = MS_STATUSES[(MS_STATUSES.indexOf(node.dataset.status) + 1) % MS_STATUSES.length];
                    await cfetch('/api/v1/cashflow/milestones/' + node.dataset.id, { method: 'PUT', body: { status: next } });
                    refresh();
                } else if (act === 'del') {
                    if (!confirm('刪除此節點？')) return;
                    await cfetch('/api/v1/cashflow/milestones/' + node.dataset.id, { method: 'DELETE' });
                    refresh();
                } else if (act === 'template') {
                    const pid = document.getElementById('cf-proj').value;
                    if (!pid) return alert('先選專案');
                    await cfetch('/api/v1/cashflow/milestones/template/' + pid, { method: 'POST' });
                    refresh();
                } else if (act === 'add') {
                    const pid = document.getElementById('cf-proj').value;
                    if (!pid) return alert('先選專案');
                    await cfetch('/api/v1/cashflow/milestones', { method: 'POST', body: {
                        project_id: pid,
                        label: document.getElementById('cf-new-label').value || '付款節點',
                        amount: parseInt(document.getElementById('cf-new-amount').value) || 0,
                        due_date: document.getElementById('cf-new-due').value || '',
                    } });
                    refresh();
                } else if (act === 'close') {
                    const month = node.dataset.month || document.getElementById('cf-close-month').value;
                    if (!month) return alert('先選月份');
                    if (!confirm(`鎖定 ${month}？鎖定後該月收支不可增改刪。`)) return;
                    await cfetch('/api/v1/cashflow/month-close', { method: 'POST', body: { month } });
                    refresh();
                } else if (act === 'reopen') {
                    if (!confirm(`重開 ${node.dataset.month}？重開會留稽核紀錄。`)) return;
                    await cfetch('/api/v1/cashflow/month-close/reopen', { method: 'POST', body: { month: node.dataset.month } });
                    refresh();
                }
            } catch (e) { alert('操作失敗: ' + e.message); }
        });
    });
}
