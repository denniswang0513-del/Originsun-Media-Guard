/**
 * projlinks.js — 🔗 專案對應（私帳 ↔ 母帳）。
 *
 * owner 2026-09-05：「母私帳連結對應表裡，除了連結之外也給我幾個按鈕，可以在
 * 母帳以私帳的狀態建立新專案並且連結」「我的目標是母帳的專案私帳都可以對齊」。
 *
 * 這頁是 clients.js（客戶對應表）的專案版，三顆按鈕：
 *   連結  → 挑一個既有母帳案（格內可搜尋下拉，同 clients 的 pick）
 *   建立  → 在母帳補一個對應的專案並連結（金額先帶私帳的，標為待確認）
 *   解除  → 只解連結，兩邊資料都不動
 *
 * 🔴 連結記在**母帳那一側**（crm_projects.mine_link_id）—— 一個私帳案可以承接
 * 多個母帳案，所以「連結」不會動到已經連上來的別案，「解除」則是把指向這個
 * 私帳案的母帳連結全部清掉。判定正本在 routers/crm/projects.resolve_mine_link。
 *
 * 入口同 clients/projects：由 .fin-nav-mine-only 指名門把關。
 */
import { finSubviewBoot, esc, fmtNum, finToast } from '../fin-utils.js';
import { crmFetch, searchableSelect } from '../../crm/crm-utils.js';

let _c = null;
let _isCurrent = () => true;
let _d = null;
let _q = '';
let _only = 'unlinked';     // unlinked / linked / all

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    const r = await finSubviewBoot(_c, {
        title: '🔗 專案對應', isCurrent: _isCurrent,
        fetchers: [() => crmFetch('/projects-mine-links')],
        retry: 'window._finPL.reload()',
    });
    if (!r) return;
    _d = r[0];
    _render();
}

function _linked(m) { return (m.parent_names || []).length > 0; }

function _visible() {
    const q = _q.trim().toLowerCase();
    return (_d.mine || []).filter((m) =>
        (_only === 'all' || (_only === 'linked') === _linked(m))
        && (!q || [m.name, m.client, ...(m.parent_names || [])]
            .filter(Boolean).some((n) => n.toLowerCase().includes(q))));
}

function _render() {
    const d = _d;
    const linked = (d.mine || []).filter(_linked);
    const rows = _visible();

    // 按鈕放最左欄（同 clients.js：擠在名字右邊很容易按到隔壁那列）
    const cell = (m) => _linked(m)
        ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="color:#f87171;width:60px;"
                   title="解除與母帳的對應（只解連結，兩邊資料都不動）"
                   onclick="window._finPL.link('${m.id}','')">解除</button>`
        : `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="width:60px;"
                   title="連到一個既有的母帳專案（只記對應，不搬資料）"
                   onclick="window._finPL.pick(event,'${m.id}')">連結</button>
           <button class="crm-btn crm-btn-primary crm-btn-sm" style="width:60px;margin-top:3px;"
                   title="在母帳補建一個對應的專案並連結（案名／客戶／結案日照帶，金額先用私帳的）"
                   onclick="window._finPL.create('${m.id}')">建立</button>`;

    const row = (m) => `
        <tr><td class="fpl-link" data-id="${m.id}" style="overflow:visible;width:76px;">${cell(m)}</td>
            <td style="color:#e0e0e0;">${esc(m.name)}${
                m.display_name ? ` <span style="font-size:10px;color:#6b7280;">顯示：${esc(m.display_name)}</span>` : ''}</td>
            <td style="color:#9ca3af;">${esc(m.client)}${
                m.client_state === 'none'
                    ? ' <span style="font-size:10px;color:#fbbf24;" title="這個客戶在母帳還沒有對應的一筆。按「建立」時會順手建起來並連結。">客戶未對應</span>'
                    : ''}</td>
            <td style="color:#888;white-space:nowrap;">${esc(m.close_date || '未結案')}</td>
            <td style="text-align:right;">${fmtNum(m.contract)}</td>
            <td style="color:${_linked(m) ? '#86efac' : '#777'};">${
                _linked(m)
                    ? (m.parent_names.length > 1
                        ? `→ ${esc(m.parent_names.join('、'))} <span style="font-size:10px;color:#6b7280;">連 ${m.parent_names.length} 案</span>`
                        : '→ ' + esc(m.parent_names[0]))
                    : m.suggest_id
                        ? `建議：${esc(m.suggest_name)}
                           <button class="crm-btn crm-btn-primary crm-btn-sm" style="margin-left:6px;"
                                   onclick="window._finPL.link('${m.id}','${m.suggest_id}')">採用</button>`
                        : '（未對應）'}</td></tr>`;

    _c.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px;">
            <h2 style="color:#eee;margin:0;font-size:18px;">🔗 專案對應</h2>
            <span style="color:#888;font-size:12px;">私帳 ${(d.mine || []).length} 案・
                <b style="color:#86efac;">已對應 ${linked.length}</b>・
                未對應 ${(d.mine || []).length - linked.length}・
                母帳 ${(d.parents || []).length} 案</span>
            <div style="flex:1;"></div>
            <input id="fpl-q" class="crm-input" placeholder="搜尋案名 / 客戶"
                   style="width:190px;" value="${esc(_q)}">
            <select id="fpl-only" class="crm-input" style="width:110px;">
                <option value="unlinked"${_only === 'unlinked' ? ' selected' : ''}>未對應</option>
                <option value="linked"${_only === 'linked' ? ' selected' : ''}>已對應</option>
                <option value="all"${_only === 'all' ? ' selected' : ''}>全部</option>
            </select>
        </div>
        <div style="max-height:calc(100vh - 300px);overflow-y:auto;background:#202020;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                <th>對應</th><th>私帳專案</th><th>客戶</th><th>結案日</th>
                <th style="text-align:right;">私帳金額</th>
                <th>對應的母帳專案</th></tr></thead>
            <tbody>${rows.map(row).join('')
                || '<tr><td colspan="6" style="color:#666;padding:14px;">（這個篩選下沒有案子）</td></tr>'}</tbody>
        </table></div>
        <div style="color:#666;font-size:11px;margin-top:10px;">
            對應＝確認「這兩筆是同一個案」，兩邊資料都不動；對應後案名顯示母帳那一份（可在執行專案頁自訂）。<br>
            「建立」會在母帳補一個專案：案名／客戶／結案日照帶，
            <b style="color:#fbbf24;">金額先用私帳的並標為待確認</b> ——
            那是你拿到的那段、不是公司跟客戶的合約額，記得回母帳改。
        </div>`;

    const q = document.getElementById('fpl-q');
    if (q) {
        q.oninput = () => { _q = q.value; _render(); document.getElementById('fpl-q').focus(); };
    }
    const only = document.getElementById('fpl-only');
    if (only) { only.onchange = () => { _only = only.value; _render(); }; }
}

const _p = (window._finPL = window._finPL || {});

_p.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

_p.link = async (id, parentId) => {
    try {
        await crmFetch(`/projects/${id}/parent-link`, {
            method: 'PUT', body: JSON.stringify({ parent_id: parentId || null }),
        });
        finToast(parentId ? '已對應' : '已解除');
        _p.reload();
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
    }
};

_p.create = async (id) => {
    const m = (_d.mine || []).find((x) => x.id === id) || {};
    const extra = m.client_state === 'none'
        ? `\n（客戶「${m.client}」在母帳也會一併建立並連結）` : '';
    if (!window.confirm(`在母帳建立專案「${m.name}」並對應？${extra}`)) return;
    try {
        await crmFetch(`/projects/${id}/parent-create`, { method: 'POST' });
        finToast('已在母帳建立並對應');
        _p.reload();
    } catch (e) {
        finToast('建立失敗：' + e.message, 'error');
    }
};

_p.pick = (ev, id) => {
    // 格內可搜尋下拉挑母帳案（同 clients.js 的 pick）——選定即存
    ev.stopPropagation();
    const cell = ev.currentTarget.closest('.fpl-link');
    if (!cell || cell.querySelector('select')) return;
    const sel = document.createElement('select');
    sel.className = 'crm-input';
    sel.innerHTML = '<option value="">— 選母帳專案 —</option>'
        + (_d.parents || []).map((p) => `<option value="${p.id}"${p.linked_mine_id ? ' disabled' : ''}>${
            esc(p.name)}${p.client ? '（' + esc(p.client) + '）' : ''}${
            p.linked_mine_id ? ' — 已對應' : ''}</option>`).join('');
    cell.innerHTML = '';
    cell.appendChild(sel);
    searchableSelect(sel, { placeholder: '搜尋母帳專案…' });
    // 🔴 這格只有 76px —— 讓搜尋框浮起來蓋在表格上，不然打不了字也看不到案名
    // （clients.js 那頁 owner 已經回報過一次「小到無法作業」）
    cell.style.position = 'relative';
    const wrap = cell.querySelector('.ss-wrap');
    if (wrap) wrap.style.cssText += ';position:absolute;top:2px;left:2px;width:340px;z-index:320;';
    const panel = cell.querySelector('.ss-panel');
    if (panel) panel.style.maxHeight = '320px';
    const inp = cell.querySelector('.ss-input');
    if (inp) {
        inp.style.cssText += ';font-size:13px;padding:6px 10px;';
        inp.focus();
        inp.addEventListener('click', (k) => k.stopPropagation());
    }
    let saved = false;
    sel.addEventListener('change', () => {
        if (saved || !sel.value) return;
        saved = true;
        _p.link(id, sel.value);
    });
    if (inp) inp.addEventListener('blur', () => setTimeout(() => { if (!saved) _render(); }, 200));
};
