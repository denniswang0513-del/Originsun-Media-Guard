/**
 * projlinks.js — 🔗 專案對應（私帳 ↔ 母帳）。
 *
 * owner 2026-09-05：「母私帳連結對應表裡，除了連結之外也給我幾個按鈕，可以在
 * 母帳以私帳的狀態建立新專案並且連結」「我的目標是母帳的專案私帳都可以對齊」。
 *
 * 這頁是 clients.js（客戶對應表）的專案版，三顆按鈕：
 *   連結  → 挑一個既有的對面案（格內可搜尋下拉，同 clients 的 pick）
 *   建立  → 在對面補一個對應的案並連結
 *   解除  → 只解連結，兩邊資料都不動
 *
 * **兩個方向**（owner 2026-09-05「增加一個切換鈕 是母帳的專案項目 對應私帳」）：
 *   私帳 → 母帳：一列一個私帳案；「建立」在母帳補案，金額先帶私帳的（標待確認）
 *   母帳 → 私帳：一列一個母帳案；「建立」在私帳補案，金額＝掛給我的成本行加總
 *
 * 🔴 連結記在**母帳那一側**（crm_projects.mine_link_id）—— 一個私帳案可以承接
 * 多個母帳案。所以兩個方向的「未對應」不會是同一批，而且**解除的粒度不同**：
 * 私帳側解除＝清掉所有連上來的母帳案，母帳側解除＝只清這一案。
 * 判定正本在 routers/crm/projects.resolve_mine_link，寫入正本是 _write_link。
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
// 方向（owner 2026-09-05「增加一個切換鈕 是母帳的專案項目 對應私帳」）：
// mine＝一列一個私帳案、parent＝一列一個母帳案。同一份資料兩個看法 ——
// 一個私帳案可以承接多個母帳案，所以兩邊的「未對應」不會是同一批。
let _dir = 'mine';

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

// 兩個方向的「有沒有對應」判定：私帳側看連上來的母帳案、母帳側看 linked_mine_id
function _linked(m) { return (m.parent_names || []).length > 0; }
function _linkedP(p) { return !!p.linked_mine_id; }

function _visible() {
    const q = _q.trim().toLowerCase();
    const src = _dir === 'mine' ? (_d.mine || []) : (_d.parents || []);
    const on = _dir === 'mine' ? _linked : _linkedP;
    const names = (x) => _dir === 'mine'
        ? [x.name, x.client, ...(x.parent_names || [])]
        : [x.name, x.client, x.linked_mine_name];
    return src.filter((x) =>
        (_only === 'all' || (_only === 'linked') === on(x))
        && (!q || names(x).filter(Boolean).some((n) => n.toLowerCase().includes(q))));
}

/** 母帳 → 私帳那個方向的一列（欄位順序與私帳側對齊，切換時眼睛不用重找）。 */
function _rowParent(p) {
    const cell = _linkedP(p)
        ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="color:#f87171;width:60px;"
                   title="解除這一案與私帳的對應（只解連結，兩邊資料都不動）"
                   onclick="window._finPL.linkP('${p.id}','')">解除</button>`
        : `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="width:60px;"
                   title="連到一個既有的私帳案（只記對應，不搬資料）"
                   onclick="window._finPL.pickP(event,'${p.id}')">連結</button>
           <button class="crm-btn crm-btn-primary crm-btn-sm" style="width:60px;margin-top:3px;"
                   title="在私帳補建一個對應的案並連結（金額＝掛給我的成本行加總，沒有就開 0）"
                   onclick="window._finPL.createMine('${p.id}')">建立</button>`;
    return `
        <tr><td class="fpl-link" data-id="${p.id}" style="overflow:visible;width:76px;">${cell}</td>
            <td style="color:#e0e0e0;">${esc(p.name)}</td>
            <td style="color:#9ca3af;">${esc(p.client)}</td>
            <td style="color:#888;white-space:nowrap;">${esc(p.close_date || '未結案')}</td>
            <td style="text-align:right;">${fmtNum(p.contract)}${
                p.placeholder
                    ? ' <span style="font-size:10px;color:#fbbf24;" title="這個金額是從私帳帶過來的佔位（你拿到的那段），不是公司跟客戶的合約額。請回專案頁填上實際金額。">待確認</span>'
                    : ''}</td>
            <td style="color:${_linkedP(p) ? '#86efac' : '#777'};">${
                _linkedP(p)
                    ? '→ ' + esc(p.linked_mine_name || '（私帳案）')
                    : p.suggest_id
                        ? `建議：${esc(p.suggest_name)}
                           <button class="crm-btn crm-btn-primary crm-btn-sm" style="margin-left:6px;"
                                   onclick="window._finPL.linkP('${p.id}','${p.suggest_id}')">採用</button>`
                        : '（未對應）'}</td></tr>`;
}


function _render() {
    const d = _d;
    const mineSide = _dir === 'mine';
    const linked = mineSide
        ? (d.mine || []).filter(_linked) : (d.parents || []).filter(_linkedP);
    const total = (mineSide ? d.mine : d.parents) || [];
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
            <div style="display:flex;gap:2px;">
                ${[['mine', '私帳 → 母帳'], ['parent', '母帳 → 私帳']].map(([k, t]) => `
                <button class="crm-btn ${_dir === k ? 'crm-btn-primary' : 'crm-btn-secondary'} crm-btn-sm"
                        onclick="window._finPL.dir('${k}')">${t}</button>`).join('')}
            </div>
            <span style="color:#888;font-size:12px;">${mineSide ? '私帳' : '母帳'} ${total.length} 案・
                <b style="color:#86efac;">已對應 ${linked.length}</b>・
                未對應 ${total.length - linked.length}・
                ${mineSide ? '母帳' : '私帳'} ${((mineSide ? d.parents : d.mine) || []).length} 案</span>
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
                <th>對應</th><th>${mineSide ? '私帳專案' : '母帳專案'}</th><th>客戶</th><th>結案日</th>
                <th style="text-align:right;">${mineSide ? '私帳金額' : '合約金額'}</th>
                <th>對應的${mineSide ? '母帳' : '私帳'}專案</th></tr></thead>
            <tbody>${rows.map(mineSide ? row : _rowParent).join('')
                || '<tr><td colspan="6" style="color:#666;padding:14px;">（這個篩選下沒有案子）</td></tr>'}</tbody>
        </table></div>
        <div style="color:#666;font-size:11px;margin-top:10px;">
            對應＝確認「這兩筆是同一個案」，兩邊資料都不動；對應後案名顯示母帳那一份（可在執行專案頁自訂）。<br>
            ${mineSide
                ? `「建立」會在母帳補一個專案：案名／客戶／結案日照帶，
                   <b style="color:#fbbf24;">金額先用私帳的並標為待確認</b> ——
                   那是你拿到的那段、不是公司跟客戶的合約額，記得回母帳改。`
                : `「建立」會在私帳補一個案：<b style="color:#fbbf24;">金額＝這一案掛給你的成本行加總</b>，
                   一筆都沒有就開 0 由你自己填。<br>
                   一個私帳案可以承接多個母帳案 —— 所以兩個方向的「未對應」不會是同一批，
                   母帳這邊解除只解這一案。`}
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

_p.dir = (d) => { _dir = d; _render(); };

_p.linkP = async (parentId, mineId) => {
    try {
        await crmFetch(`/projects/${parentId}/mine-link`, {
            method: 'PUT', body: JSON.stringify({ mine_id: mineId || null }),
        });
        finToast(mineId ? '已對應' : '已解除');
        _p.reload();
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
    }
};

_p.createMine = async (parentId) => {
    const p = (_d.parents || []).find((x) => x.id === parentId) || {};
    if (!window.confirm(`在私帳建立對應的案「${p.name}」？\n（金額＝這一案掛給你的成本行加總，沒有就開 0）`)) return;
    try {
        const r = await crmFetch(`/projects/${parentId}/mine-create`, { method: 'POST' });
        // 金額 0 有兩個原因，要說清楚是哪一個：沒有掛給我的成本行，
        // 或是這個帳號根本沒綁人員檔案（那樣系統認不出哪幾行是我的）
        finToast(r.amount ? `已在私帳建立並對應（金額 ${fmtNum(r.amount)}）`
            : r.staff_bound === false
                ? '已在私帳建立並對應（帳號沒綁人員檔案，認不出哪幾行是你的，金額先開 0）'
                : '已在私帳建立並對應（沒有掛給你的成本行，金額先開 0）');
        _p.reload();
    } catch (e) {
        finToast('建立失敗：' + e.message, 'error');
    }
};

_p.pickP = (ev, parentId) => {
    _pickCell(ev, (id) => _p.linkP(parentId, id),
              (_d.mine || []).map((m) => ({
                  id: m.id, label: m.name + (m.client ? `（${m.client}）` : ''),
                  // 已被連的照樣可選 —— 一個私帳案可以承接多個母帳案（owner 2026-09-01）
                  note: (m.parent_names || []).length
                      ? ` — 已連 ${m.parent_names.length} 案` : '' })),
              '搜尋私帳案…');
};

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
    _pickCell(ev, (pid) => _p.link(id, pid),
              (_d.parents || []).map((p) => ({
                  id: p.id, label: p.name + (p.client ? `（${p.client}）` : ''),
                  // 已經連到別的私帳案的不給選（母帳側是 1 對 1 —— 那一欄只有一個）
                  disabled: !!p.linked_mine_id,
                  note: p.linked_mine_id ? ' — 已對應' : '' })),
              '搜尋母帳專案…');
};


/** 格內可搜尋下拉（同 clients.js 的 pick）——選定即存。兩個方向共用。
 *
 *  🔴 這格只有 76px：搜尋框與選單跟著縮成 76px 就打不了字也看不到案名，
 *  所以讓它**浮起來蓋在表格上**（cell 當定位原點）。owner 在 clients.js
 *  那頁已經回報過一次「小到無法作業」，不要在這裡重演。
 */
function _pickCell(ev, onPick, options, placeholder) {
    ev.stopPropagation();
    const cell = ev.currentTarget.closest('.fpl-link');
    if (!cell || cell.querySelector('select')) return;
    const sel = document.createElement('select');
    sel.className = 'crm-input';
    sel.innerHTML = `<option value="">— ${placeholder.replace('搜尋', '選').replace('…', '')} —</option>`
        + options.map((o) => `<option value="${o.id}"${o.disabled ? ' disabled' : ''}>${
            esc(o.label)}${esc(o.note || '')}</option>`).join('');
    cell.innerHTML = '';
    cell.appendChild(sel);
    searchableSelect(sel, { placeholder });
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
        onPick(sel.value);
    });
    if (inp) inp.addEventListener('blur', () => setTimeout(() => { if (!saved) _render(); }, 200));
}
