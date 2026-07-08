/**
 * equipment.js — 🎥 器材庫 Tab（B4 器材管理，docs/BIZ_PLAN.md B4 段）
 *
 * 同源打 /api/v1/equipment/*（帶 auth token）。卡片牆 + 篩選（搜尋/分類/狀態）
 * + 統計 chips（總數/出勤中/維修/逾期未還）+ 詳情 overlay（封面上傳、欄位編輯、
 * 領用/歸還、領用歷史、保養紀錄、折舊/稼動率統計）。
 * overlay 掛在 body 下（tab section 有 transform class，fixed 定位會被困住）。
 */

import { esc } from '../website/website-utils.js';

const API = '/api/v1/equipment';
const STATUSES = ['在庫', '出勤', '維修', '除役'];
const CATEGORIES = ['機身', '鏡頭', '燈光', '收音', '週邊', '其他'];
const STATUS_CLS = { '在庫': 'st-in', '出勤': 'st-out', '維修': 'st-fix', '除役': 'st-off' };

async function tfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Accept': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}) };
    if (opts.json !== undefined) {
        headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.json);
        delete opts.json;
    }
    const r = await fetch(path, { ...opts, headers });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.json();
}

let _content = null;
let _all = [];           // 最近一次「無篩選」清單（供統計 chips）
let _filters = { q: '', category: '', status: '' };
let _projectsCache = null;
let _qTimer = null;
let _escHandler = null;

export async function initEquipmentTab() {
    _content = document.getElementById('eq-content');
    if (!_content) return;
    _content.style.cssText = '';   // 移除「載入中…」的置中/padding inline 樣式
    _renderShell();
    await refreshGrid();
}

// ── 卡片牆 ─────────────────────────────────────────────

function _renderShell() {
    _content.innerHTML = `
        <div style="text-align:left;color:#ccc;">
            <h2>🎥 器材庫</h2>
            <div class="eq-sub">成本真相的最後一塊：領用/歸還 + 折舊攤提 + 稼動率（買 vs 租有據可依）</div>
            <div class="eq-toolbar">
                <input id="eq-q" type="text" placeholder="🔍 搜尋名稱/序號…" style="width:200px;">
                <select id="eq-f-category"><option value="">全部分類</option>
                    ${CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join('')}</select>
                <select id="eq-f-status"><option value="">全部狀態</option>
                    ${STATUSES.map(s => `<option value="${s}">${s}</option>`).join('')}</select>
                <button id="eq-add" class="eq-btn">＋ 新增器材</button>
            </div>
            <div id="eq-stats" class="eq-stats"></div>
            <div id="eq-grid" class="eq-grid"></div>
        </div>`;

    document.getElementById('eq-q').addEventListener('input', (e) => {
        clearTimeout(_qTimer);
        _qTimer = setTimeout(() => { _filters.q = e.target.value.trim(); refreshGrid(); }, 300);
    });
    for (const [id, key] of [['eq-f-category', 'category'], ['eq-f-status', 'status']]) {
        document.getElementById(id).addEventListener('change', (e) => {
            _filters[key] = e.target.value;
            refreshGrid();
        });
    }
    document.getElementById('eq-add').addEventListener('click', () => _openEditor(null));
}

function _hasFilters() {
    return Object.values(_filters).some(v => v);
}

async function refreshGrid() {
    const grid = document.getElementById('eq-grid');
    if (!grid) return;
    try {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(_filters)) if (v) params.set(k, v);
        const qs = params.toString();
        const d = await tfetch(API + (qs ? '?' + qs : ''));
        const items = d.equipment || [];
        if (!_hasFilters()) _all = items;
        else if (!_all.length) {  // 首次載入就帶篩選 → 補抓一次全量供 chips
            try { _all = (await tfetch(API)).equipment || []; } catch { _all = items; }
        }
        _renderStats();
        grid.innerHTML = items.length ? items.map(_card).join('')
            : '<div style="grid-column:1/-1;color:#666;padding:40px;text-align:center;">尚無器材 — 按「＋ 新增器材」建立第一筆</div>';
        grid.querySelectorAll('.eq-card').forEach(el => {
            el.addEventListener('click', () => openDetail(el.dataset.id));
        });
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:#f87171;padding:30px;text-align:center;">器材載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _renderStats() {
    const el = document.getElementById('eq-stats');
    if (!el) return;
    const total = _all.length;
    const out = _all.filter(i => i.status === '出勤').length;
    const fix = _all.filter(i => i.status === '維修').length;
    const overdue = _all.filter(i => i.overdue).length;
    el.innerHTML = `
        <div class="eq-chip">總數<b>${total}</b></div>
        <div class="eq-chip">出勤中<b>${out}</b></div>
        <div class="eq-chip">維修<b>${fix}</b></div>
        <div class="eq-chip${overdue ? ' alert' : ''}">⚠ 逾期未還<b>${overdue}</b></div>`;
}

function _overdueLabel(co) {
    if (!co || !co.overdue) return '';
    return co.overdue_days > 0 ? `⚠ 逾期 ${co.overdue_days} 天` : '⚠ 今日應還';
}

function _card(it) {
    const retired = it.status === '除役';
    const co = it.current_checkout;
    const cover = it.cover_url
        ? `<img class="eq-cover" src="${esc(it.cover_url)}" alt="" loading="lazy">`
        : '<div class="eq-cover-ph">🎥</div>';
    const holder = co
        ? `<div class="eq-card-holder">👤 ${esc(co.person)}${co.project_name ? ' → ' + esc(co.project_name) : ''}</div>`
        : '';
    const overdue = _overdueLabel(co);
    return `
        <div class="eq-card${retired ? ' retired' : ''}" data-id="${esc(it.id)}">
            ${cover}
            <div class="eq-card-body">
                <div class="eq-card-name">${esc(it.name)}</div>
                <div>
                    ${it.category ? `<span class="eq-pill">${esc(it.category)}</span>` : ''}
                    <span class="eq-pill ${STATUS_CLS[it.status] || 'st-off'}">${esc(it.status)}</span>
                </div>
                ${holder}
                ${overdue ? `<div class="eq-card-overdue">${overdue}</div>` : ''}
                <div class="eq-card-meta">${[
                    it.serial ? 'SN: ' + esc(it.serial) : '',
                    it.monthly_depreciation ? '月折舊 $' + it.monthly_depreciation.toLocaleString() : '',
                ].filter(Boolean).join('　')}</div>
            </div>
        </div>`;
}

// ── 詳情 overlay ──────────────────────────────────────

function _closeOverlay() {
    document.getElementById('eq-overlay')?.remove();
    if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

function _mountOverlay(innerHTML) {
    _closeOverlay();
    const ov = document.createElement('div');
    ov.id = 'eq-overlay';
    ov.innerHTML = innerHTML;
    ov.addEventListener('click', (e) => { if (e.target === ov) _closeOverlay(); });
    document.body.appendChild(ov);
    _escHandler = (e) => { if (e.key === 'Escape') _closeOverlay(); };
    document.addEventListener('keydown', _escHandler);
    ov.querySelector('.eq-close')?.addEventListener('click', _closeOverlay);
    return ov;
}

async function _loadProjects() {
    if (_projectsCache) return _projectsCache;
    try {
        const d = await tfetch('/api/v1/crm/projects');
        _projectsCache = (d.projects || []).map(p => ({ id: p.id, name: p.name }));
    } catch {
        _projectsCache = [];
    }
    return _projectsCache;
}

function _fmtDay(iso) {
    return iso ? String(iso).slice(0, 10) : '';
}

async function openDetail(eid) {
    let eq;
    try {
        eq = (await tfetch(`${API}/${eid}`)).equipment;
    } catch (e) {
        alert('器材載入失敗：' + (e.message || e));
        return;
    }
    const projects = await _loadProjects();
    const co = eq.current_checkout;
    const stats = eq.stats || {};

    const kv = (k, v) => v ? `<div class="eq-kv"><span class="k">${k}</span><span class="v">${esc(String(v))}</span></div>` : '';
    const money = (n) => (n || n === 0) ? '$' + Number(n).toLocaleString() : '';

    const checkoutRows = (eq.checkouts || []).map(c => `
        <tr>
            <td>${esc(c.person)}</td>
            <td>${esc(c.project_name || '—')}</td>
            <td>${_fmtDay(c.out_at)}</td>
            <td${c.overdue ? ' style="color:#f87171;font-weight:600;"' : ''}>${c.due_at ? esc(c.due_at) : '—'}${c.overdue ? '<br>' + _overdueLabel(c) : ''}</td>
            <td>${c.returned_at ? _fmtDay(c.returned_at) : '<span style="color:#93c5fd;">出勤中</span>'}</td>
            <td>${c.days}</td>
            <td>${esc(c.condition_note || '')}</td>
        </tr>`).join('');

    const maintRows = (eq.maintenance || []).map(m => `
        <tr data-mid="${esc(m.id)}">
            <td>${m.date ? esc(m.date) : ''}</td>
            <td>${money(m.cost)}</td>
            <td>${esc(m.note || '')}</td>
            <td style="text-align:right;"><button class="eq-btn danger eq-maint-del" style="padding:1px 8px;font-size:11px;">刪</button></td>
        </tr>`).join('');

    const actionSec = co ? `
        <div class="eq-kv"><span class="k">領用人</span><span class="v">👤 ${esc(co.person)}${co.project_name ? ' → ' + esc(co.project_name) : ''}</span></div>
        <div class="eq-kv"><span class="k">領用時間</span><span class="v">${_fmtDay(co.out_at)}</span></div>
        <div class="eq-kv"><span class="k">應還日</span><span class="v" ${co.overdue ? 'style="color:#f87171;font-weight:600;"' : ''}>${co.due_at || '未設定'}${co.overdue ? '　' + _overdueLabel(co) : ''}</span></div>
        <input id="er-note" placeholder="歸還狀況備註（可空，例：鏡頭蓋遺失）" style="width:100%;box-sizing:border-box;margin:8px 0;">
        <button id="er-return" class="eq-btn big green" style="width:100%;">📥 歸還</button>
    ` : `
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <input id="ec-person" placeholder="領用人 *" style="flex:1;min-width:120px;">
            <select id="ec-project" style="flex:1;min-width:140px;">
                <option value="">（選擇專案，可空）</option>
                ${projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}
            </select>
            <input id="ec-due" type="date" title="應還日（可空）">
        </div>
        <button id="ec-checkout" class="eq-btn big" style="width:100%;margin-top:8px;">📤 領用</button>
    `;

    const ov = _mountOverlay(`
        <div class="eq-panel">
            <div class="eq-panel-head">
                <h3>🎥 ${esc(eq.name)}</h3>
                <select id="ed-status" title="狀態（選了即改）">
                    ${STATUSES.map(s => `<option value="${s}"${s === eq.status ? ' selected' : ''}>${s}</option>`).join('')}
                </select>
                <button id="ed-edit" class="eq-btn ghost">✏️ 編輯</button>
                <button id="ed-del" class="eq-btn danger">🗑 刪除</button>
                <button class="eq-close" title="關閉">✕</button>
            </div>
            <div class="eq-panel-body">
                <div class="eq-left-col">
                    <div class="eq-card-sec">
                        <h4>📷 封面</h4>
                        ${eq.cover_url
                            ? `<img class="eq-cover-lg" src="${esc(eq.cover_url)}" alt="" data-url="${esc(eq.cover_url)}">`
                            : '<div class="eq-cover-lg-ph">🎥</div>'}
                        <button id="ed-upload" class="eq-btn ghost" style="margin-top:8px;">⬆️ 上傳封面</button>
                        <input id="ed-file" type="file" accept=".jpg,.jpeg,.png,.webp" style="display:none;">
                        <div class="eq-note">支援 jpg / png / webp，單張；重傳會覆蓋舊封面。</div>
                    </div>
                    <div class="eq-card-sec">
                        <h4>📊 統計</h4>
                        <div class="eq-stat-grid">
                            <div class="eq-stat"><div class="n">${stats.usage_days_365 ?? 0}</div><div class="l">年度使用天數</div></div>
                            <div class="eq-stat"><div class="n">${stats.utilization_pct ?? 0}%</div><div class="l">稼動率</div></div>
                            <div class="eq-stat"><div class="n">${money(stats.monthly_depreciation) || '$0'}</div><div class="l">月折舊額</div></div>
                        </div>
                        ${stats.maintenance_total ? `<div class="eq-note">保養費累計 ${money(stats.maintenance_total)}</div>` : ''}
                    </div>
                    <div class="eq-card-sec">
                        <h4>📋 基本資訊</h4>
                        ${kv('分類', eq.category)}
                        ${kv('序號', eq.serial)}
                        ${kv('購入日', eq.purchase_date)}
                        ${kv('購入成本', money(eq.purchase_cost))}
                        ${kv('攤提月數', eq.depreciation_months)}
                        ${kv('備註', eq.note)}
                    </div>
                </div>
                <div class="eq-right-col">
                    <div class="eq-card-sec">
                        <h4>${co ? '📥 歸還' : '📤 領用'}</h4>
                        ${actionSec}
                    </div>
                    <div class="eq-card-sec">
                        <h4>🕘 領用歷史（${(eq.checkouts || []).length}）</h4>
                        <div style="overflow-x:auto;">
                            ${checkoutRows ? `<table class="eq-table">
                                <thead><tr><th>領用人</th><th>專案</th><th>領用日</th><th>應還</th><th>歸還</th><th>天數</th><th>備註</th></tr></thead>
                                <tbody>${checkoutRows}</tbody>
                            </table>` : '<div style="color:#666;font-size:12px;">尚無紀錄</div>'}
                        </div>
                    </div>
                    <div class="eq-card-sec">
                        <h4>🔧 保養紀錄（${(eq.maintenance || []).length}）</h4>
                        <div style="overflow-x:auto;">
                            ${maintRows ? `<table class="eq-table">
                                <thead><tr><th>日期</th><th>費用</th><th>內容</th><th></th></tr></thead>
                                <tbody>${maintRows}</tbody>
                            </table>` : '<div style="color:#666;font-size:12px;">尚無保養紀錄</div>'}
                        </div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;">
                            <input id="em-date" type="date" title="保養日期">
                            <input id="em-cost" type="number" min="0" placeholder="費用" style="width:90px;">
                            <input id="em-note" placeholder="內容（例：CMOS 清潔）" style="flex:1;min-width:140px;">
                            <button id="em-add" class="eq-btn">＋ 新增</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>`);

    // 狀態下拉即改
    ov.querySelector('#ed-status').addEventListener('change', async (e) => {
        try {
            await tfetch(`${API}/${eq.id}`, { method: 'PUT', json: { status: e.target.value } });
            refreshGrid();
            openDetail(eq.id);
        } catch (err) { alert('狀態更新失敗：' + (err.message || err)); }
    });

    ov.querySelector('#ed-edit').addEventListener('click', () => _openEditor(eq));

    ov.querySelector('#ed-del').addEventListener('click', async () => {
        if (!confirm(`確定刪除器材「${eq.name}」？領用歷史與保養紀錄會一併刪除。`)) return;
        try {
            await tfetch(`${API}/${eq.id}`, { method: 'DELETE' });
            _closeOverlay();
            refreshGrid();
        } catch (err) { alert('刪除失敗：' + (err.message || err)); }
    });

    // 封面：點圖開新視窗 / 上傳
    ov.querySelector('.eq-cover-lg')?.addEventListener('click', (e) => window.open(e.target.dataset.url, '_blank'));
    const fileInput = ov.querySelector('#ed-file');
    ov.querySelector('#ed-upload').addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
        if (!fileInput.files.length) return;
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        try {
            await tfetch(`${API}/${eq.id}/photo`, { method: 'POST', body: fd });
            refreshGrid();
            openDetail(eq.id);
        } catch (err) { alert('上傳失敗：' + (err.message || err)); }
    });

    // 領用 / 歸還
    ov.querySelector('#ec-checkout')?.addEventListener('click', async () => {
        const person = ov.querySelector('#ec-person').value.trim();
        if (!person) { alert('領用人必填'); return; }
        const body = {
            person,
            project_id: ov.querySelector('#ec-project').value || null,
            due_at: ov.querySelector('#ec-due').value || null,
        };
        try {
            await tfetch(`${API}/${eq.id}/checkout`, { method: 'POST', json: body });
            refreshGrid();
            openDetail(eq.id);
        } catch (err) { alert('領用失敗：' + (err.message || err)); }
    });
    ov.querySelector('#er-return')?.addEventListener('click', async () => {
        const body = { condition_note: ov.querySelector('#er-note').value.trim() || null };
        try {
            await tfetch(`${API}/${eq.id}/return`, { method: 'POST', json: body });
            refreshGrid();
            openDetail(eq.id);
        } catch (err) { alert('歸還失敗：' + (err.message || err)); }
    });

    // 保養紀錄：新增 / 刪除
    ov.querySelector('#em-add').addEventListener('click', async () => {
        const date = ov.querySelector('#em-date').value;
        if (!date) { alert('保養日期必填'); return; }
        const cost = ov.querySelector('#em-cost').value;
        const body = {
            date,
            cost: cost ? Number(cost) : null,
            note: ov.querySelector('#em-note').value.trim() || null,
        };
        try {
            await tfetch(`${API}/${eq.id}/maintenance`, { method: 'POST', json: body });
            refreshGrid();
            openDetail(eq.id);
        } catch (err) { alert('新增保養紀錄失敗：' + (err.message || err)); }
    });
    ov.querySelectorAll('.eq-maint-del').forEach(btn => {
        btn.addEventListener('click', async () => {
            const mid = btn.closest('tr').dataset.mid;
            if (!confirm('確定刪除這筆保養紀錄？')) return;
            try {
                await tfetch(`${API}/maintenance/${mid}`, { method: 'DELETE' });
                refreshGrid();
                openDetail(eq.id);
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        });
    });
}

// ── 新增 / 編輯表單（eq=null 為新增） ─────────────────

function _openEditor(eq) {
    const isNew = !eq;
    const v = (k) => esc((eq && eq[k]) != null ? String(eq[k]) : '');
    const row = (label, html) => `<div class="eq-form-row"><label>${label}</label>${html}</div>`;
    const ov = _mountOverlay(`
        <div class="eq-panel" style="width:min(560px,96vw);">
            <div class="eq-panel-head">
                <h3>${isNew ? '＋ 新增器材' : '✏️ 編輯：' + esc(eq.name)}</h3>
                <button class="eq-close" title="關閉">✕</button>
            </div>
            <div class="eq-panel-body" style="display:block;">
                ${row('名稱 *', `<input id="ee-name" value="${v('name')}" placeholder="例：Sony FX3 A 機">`)}
                <div style="display:flex;gap:8px;">
                    <div style="flex:1;">${row('分類', `<select id="ee-category">
                        <option value="">（未分類）</option>
                        ${CATEGORIES.map(c => `<option value="${c}"${eq && eq.category === c ? ' selected' : ''}>${c}</option>`).join('')}
                    </select>`)}</div>
                    <div style="flex:1;">${row('序號', `<input id="ee-serial" value="${v('serial')}" placeholder="SN">`)}</div>
                </div>
                <div style="display:flex;gap:8px;">
                    <div style="flex:1;">${row('購入日', `<input id="ee-pdate" type="date" value="${v('purchase_date')}">`)}</div>
                    <div style="flex:1;">${row('購入成本', `<input id="ee-pcost" type="number" min="0" value="${v('purchase_cost')}" placeholder="例：120000">`)}</div>
                    <div style="flex:1;">${row('攤提月數', `<input id="ee-dep" type="number" min="1" value="${eq ? esc(String(eq.depreciation_months || 36)) : '36'}" placeholder="36">`)}</div>
                </div>
                ${row('備註', `<textarea id="ee-note" rows="3">${v('note')}</textarea>`)}
                <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
                    <button id="ee-cancel" class="eq-btn ghost">取消</button>
                    <button id="ee-save" class="eq-btn">${isNew ? '建立器材' : '儲存變更'}</button>
                </div>
            </div>
        </div>`);

    ov.querySelector('#ee-cancel').addEventListener('click', () => {
        if (isNew) _closeOverlay(); else openDetail(eq.id);
    });
    ov.querySelector('#ee-save').addEventListener('click', async () => {
        const name = ov.querySelector('#ee-name').value.trim();
        if (!name) { alert('名稱必填'); return; }
        const pcost = ov.querySelector('#ee-pcost').value;
        const dep = ov.querySelector('#ee-dep').value;
        const body = {
            name,
            category: ov.querySelector('#ee-category').value,
            serial: ov.querySelector('#ee-serial').value.trim(),
            purchase_date: ov.querySelector('#ee-pdate').value || '',
            purchase_cost: pcost ? Number(pcost) : null,
            depreciation_months: dep ? Number(dep) : 36,
            note: ov.querySelector('#ee-note').value.trim(),
        };
        try {
            const d = isNew
                ? await tfetch(API, { method: 'POST', json: body })
                : await tfetch(`${API}/${eq.id}`, { method: 'PUT', json: body });
            refreshGrid();
            openDetail(d.equipment.id);
        } catch (err) {
            alert((isNew ? '建立' : '儲存') + '失敗：' + (err.message || err));
        }
    });
}
