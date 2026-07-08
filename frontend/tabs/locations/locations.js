/**
 * locations.js — 🗺️ 場景庫 Tab（P-a：場勘成果資產化，docs/PREPROD_PLAN.md A 段）
 *
 * 同源打 /api/v1/locations/*（帶 auth token）。卡片牆 + 篩選（搜尋/分類/縣市/狀態）
 * + 詳情 overlay（照片牆上傳、欄位編輯、狀態即改、使用履歷＝專案+評分+踩雷心得）。
 * overlay 掛在 body 下（tab section 有 transform class，fixed 定位會被困住）。
 */

import { esc } from '../website/website-utils.js';

const API = '/api/v1/locations';
const STATUSES = ['可用', '黑名單', '已消失'];
const CATEGORY_PRESETS = ['咖啡廳', '餐廳', '工廠', '辦公室', '住宅', '戶外', '官署', '商場', '攝影棚', '其他'];

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
let _all = [];           // 最近一次「無篩選」清單（供分類/縣市下拉選項）
let _filters = { q: '', category: '', region: '', status: '' };
let _projectsCache = null;
let _qTimer = null;
let _escHandler = null;

export async function initLocationsTab() {
    _content = document.getElementById('loc-content');
    if (!_content) return;
    _content.style.cssText = '';   // 移除「載入中…」的置中/padding inline 樣式
    _renderShell();
    await refreshGrid();
}

// ── 卡片牆 ─────────────────────────────────────────────

function _renderShell() {
    _content.innerHTML = `
        <div style="text-align:left;color:#ccc;">
            <h2>🗺️ 場景庫</h2>
            <div class="loc-sub">場勘成果資產化：勘過一次＝永久資產（含照片、屬性、使用履歷與踩雷紀錄）</div>
            <div class="loc-toolbar">
                <input id="loc-q" type="text" placeholder="🔍 搜尋名稱/地址…" style="width:200px;">
                <select id="loc-f-category"><option value="">全部分類</option></select>
                <select id="loc-f-region"><option value="">全部縣市</option></select>
                <select id="loc-f-status"><option value="">全部狀態</option>
                    ${STATUSES.map(s => `<option value="${s}">${s}</option>`).join('')}</select>
                <button id="loc-add" class="loc-btn">＋ 新增場景</button>
            </div>
            <div id="loc-grid" class="loc-grid"></div>
        </div>`;

    document.getElementById('loc-q').addEventListener('input', (e) => {
        clearTimeout(_qTimer);
        _qTimer = setTimeout(() => { _filters.q = e.target.value.trim(); refreshGrid(); }, 300);
    });
    for (const [id, key] of [['loc-f-category', 'category'], ['loc-f-region', 'region'], ['loc-f-status', 'status']]) {
        document.getElementById(id).addEventListener('change', (e) => {
            _filters[key] = e.target.value;
            refreshGrid();
        });
    }
    document.getElementById('loc-add').addEventListener('click', () => _openEditor(null));
}

function _hasFilters() {
    return Object.values(_filters).some(v => v);
}

async function refreshGrid() {
    const grid = document.getElementById('loc-grid');
    if (!grid) return;
    try {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(_filters)) if (v) params.set(k, v);
        const qs = params.toString();
        const d = await tfetch(API + (qs ? '?' + qs : ''));
        const locs = d.locations || [];
        if (!_hasFilters()) { _all = locs; _syncFilterOptions(); }
        grid.innerHTML = locs.length ? locs.map(_card).join('')
            : '<div style="grid-column:1/-1;color:#666;padding:40px;text-align:center;">尚無場景 — 按「＋ 新增場景」建立第一筆</div>';
        grid.querySelectorAll('.loc-card').forEach(el => {
            el.addEventListener('click', () => openDetail(el.dataset.id));
        });
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:#f87171;padding:30px;text-align:center;">場景載入失敗：${esc(e.message || e)}</div>`;
    }
}

// 分類/縣市下拉選項 = 既有資料的 distinct 值（保留目前選取）
function _syncFilterOptions() {
    const fill = (id, values, placeholder) => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const cur = sel.value;
        sel.innerHTML = `<option value="">${placeholder}</option>`
            + values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
        if (values.includes(cur)) sel.value = cur;
    };
    fill('loc-f-category', [...new Set(_all.map(l => l.category).filter(Boolean))].sort(), '全部分類');
    fill('loc-f-region', [...new Set(_all.map(l => l.region).filter(Boolean))].sort(), '全部縣市');
}

function _card(l) {
    const black = l.status === '黑名單';
    const gone = l.status === '已消失';
    const cover = l.cover_url
        ? `<img class="loc-cover" src="${esc(l.cover_url)}" alt="" loading="lazy">`
        : '<div class="loc-cover-ph">🗺️</div>';
    const tags = (l.tags || []).slice(0, 4).map(t => `<span class="loc-tag">${esc(t)}</span>`).join('');
    return `
        <div class="loc-card${black ? ' blacklisted' : ''}" data-id="${esc(l.id)}">
            ${cover}
            <div class="loc-card-body">
                <div class="loc-card-name">${black ? '⛔ ' : ''}${esc(l.name)}${gone ? ' <span style="color:#777;font-size:11px;">（已消失）</span>' : ''}</div>
                <div>
                    ${l.category ? `<span class="loc-pill">${esc(l.category)}</span>` : ''}
                    ${l.region ? `<span class="loc-pill region">${esc(l.region)}</span>` : ''}
                    ${l.permit_required ? '<span class="loc-pill gray">需申請</span>' : ''}
                </div>
                ${l.fee_note ? `<div class="loc-card-fee">💰 ${esc(l.fee_note)}</div>` : ''}
                ${tags ? `<div>${tags}</div>` : ''}
                <div class="loc-card-meta">📷 ${l.photo_count || 0}　🎬 用過 ${l.usage_count || 0} 次</div>
            </div>
        </div>`;
}

// ── 詳情 overlay ──────────────────────────────────────

function _closeOverlay() {
    document.getElementById('loc-overlay')?.remove();
    if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

function _mountOverlay(innerHTML) {
    _closeOverlay();
    const ov = document.createElement('div');
    ov.id = 'loc-overlay';
    ov.innerHTML = innerHTML;
    ov.addEventListener('click', (e) => { if (e.target === ov) _closeOverlay(); });
    document.body.appendChild(ov);
    _escHandler = (e) => { if (e.key === 'Escape') _closeOverlay(); };
    document.addEventListener('keydown', _escHandler);
    ov.querySelector('.loc-close')?.addEventListener('click', _closeOverlay);
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

async function openDetail(lid) {
    let loc;
    try {
        loc = (await tfetch(`${API}/${lid}`)).location;
    } catch (e) {
        alert('場景載入失敗：' + (e.message || e));
        return;
    }
    const projects = await _loadProjects();

    const photos = (loc.photos || []).map(p => `
        <div class="loc-photo" data-pid="${esc(p.id)}">
            <img src="${esc(p.url)}" alt="${esc(p.caption || '')}" loading="lazy" data-url="${esc(p.url)}">
            <button class="loc-photo-del" title="刪除照片">✕</button>
        </div>`).join('');

    const kv = (k, v) => v ? `<div class="loc-kv"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>` : '';
    const attrRows = Object.entries(loc.attributes || {})
        .map(([k, v]) => `<div class="loc-kv"><span class="k">${esc(k)}</span><span class="v">${esc(String(v))}</span></div>`).join('');
    const tagChips = (loc.tags || []).map(t => `<span class="loc-tag">${esc(t)}</span>`).join('');

    const usages = (loc.usages || []).map(u => `
        <div class="loc-usage" data-uid="${esc(u.id)}">
            <div style="display:flex;gap:8px;align-items:center;">
                <b style="color:#ddd;">${esc(u.project_name || '（未關聯專案）')}</b>
                <span style="color:#777;">${esc(u.used_date || '')}</span>
                <span class="stars">${u.rating ? '★'.repeat(u.rating) + '☆'.repeat(5 - u.rating) : ''}</span>
                <span style="flex:1;"></span>
                <button class="loc-btn danger loc-usage-del" style="padding:2px 8px;font-size:11px;">刪</button>
            </div>
            ${u.lesson ? `<div class="lesson">${esc(u.lesson)}</div>` : ''}
        </div>`).join('');

    const ov = _mountOverlay(`
        <div class="loc-panel">
            <div class="loc-panel-head">
                <h3>${loc.status === '黑名單' ? '⛔ ' : ''}${esc(loc.name)}</h3>
                <select id="ld-status" title="狀態（選了即改）">
                    ${STATUSES.map(s => `<option value="${s}"${s === loc.status ? ' selected' : ''}>${s}</option>`).join('')}
                </select>
                <button id="ld-edit" class="loc-btn ghost">✏️ 編輯</button>
                <button id="ld-del" class="loc-btn danger">🗑 刪除</button>
                <button class="loc-close" title="關閉">✕</button>
            </div>
            <div class="loc-panel-body">
                <div class="loc-photos-col">
                    <div class="loc-card-sec">
                        <h4>📷 照片牆（${(loc.photos || []).length}）</h4>
                        <div class="loc-photo-grid">${photos || ''}</div>
                        ${photos ? '' : '<div style="color:#666;font-size:12px;margin-bottom:8px;">尚無照片</div>'}
                        <button id="ld-upload" class="loc-btn ghost">⬆️ 上傳照片</button>
                        <input id="ld-file" type="file" multiple accept=".jpg,.jpeg,.png,.webp" style="display:none;">
                        <div class="loc-note">支援 jpg / png / webp，可多選；第一張自動成為封面。點縮圖開新視窗看原圖。</div>
                    </div>
                </div>
                <div class="loc-info-col">
                    <div class="loc-card-sec">
                        <h4>📋 基本資訊</h4>
                        ${kv('分類', loc.category)}
                        ${kv('縣市', loc.region)}
                        ${kv('地址', loc.address)}
                        ${kv('聯絡人', loc.contact_name)}
                        ${kv('電話', loc.contact_phone)}
                        <div class="loc-kv"><span class="k">需申請</span><span class="v">${loc.permit_required ? '是' + (loc.permit_note ? '：' + esc(loc.permit_note) : '') : '否'}</span></div>
                        ${kv('費用', loc.fee_note)}
                        ${kv('備註', loc.note)}
                        ${tagChips ? `<div class="loc-kv"><span class="k">標籤</span><span class="v">${tagChips}</span></div>` : ''}
                    </div>
                    <div class="loc-card-sec">
                        <h4>⚙️ 場地屬性</h4>
                        ${attrRows || '<div style="color:#666;font-size:12px;">尚未填寫（電源/收音/自然光/停車/廁所…）</div>'}
                    </div>
                    <div class="loc-card-sec">
                        <h4>🎬 使用履歷（${(loc.usages || []).length}）</h4>
                        <div id="ld-usages">${usages || '<div style="color:#666;font-size:12px;">尚無紀錄 — 用過就留下評分與踩雷心得</div>'}</div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;">
                            <select id="lu-project" style="flex:1;min-width:140px;">
                                <option value="">（選擇專案，可空）</option>
                                ${projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}
                            </select>
                            <input id="lu-date" type="date" title="使用日期">
                            <select id="lu-rating" title="評分">
                                <option value="">評分</option>
                                ${[5, 4, 3, 2, 1].map(n => `<option value="${n}">${'★'.repeat(n)}</option>`).join('')}
                            </select>
                        </div>
                        <textarea id="lu-lesson" rows="2" placeholder="心得 / 踩雷紀錄（例：下午西曬嚴重、管理員只到 17:00）"
                            style="width:100%;box-sizing:border-box;margin-top:6px;"></textarea>
                        <button id="lu-add" class="loc-btn" style="margin-top:6px;">＋ 新增履歷</button>
                    </div>
                </div>
            </div>
        </div>`);

    // 狀態下拉即改
    ov.querySelector('#ld-status').addEventListener('change', async (e) => {
        try {
            await tfetch(`${API}/${loc.id}`, { method: 'PUT', json: { status: e.target.value } });
            refreshGrid();
            openDetail(loc.id);
        } catch (err) { alert('狀態更新失敗：' + (err.message || err)); }
    });

    ov.querySelector('#ld-edit').addEventListener('click', () => _openEditor(loc));

    ov.querySelector('#ld-del').addEventListener('click', async () => {
        if (!confirm(`確定刪除場景「${loc.name}」？照片與使用履歷會一併刪除。`)) return;
        try {
            await tfetch(`${API}/${loc.id}`, { method: 'DELETE' });
            _closeOverlay();
            refreshGrid();
        } catch (err) { alert('刪除失敗：' + (err.message || err)); }
    });

    // 照片：點圖開新視窗 / 刪除 / 上傳
    ov.querySelectorAll('.loc-photo img').forEach(img => {
        img.addEventListener('click', () => window.open(img.dataset.url, '_blank'));
    });
    ov.querySelectorAll('.loc-photo-del').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const pid = btn.closest('.loc-photo').dataset.pid;
            if (!confirm('確定刪除這張照片？')) return;
            try {
                await tfetch(`${API}/photos/${pid}`, { method: 'DELETE' });
                refreshGrid();
                openDetail(loc.id);
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        });
    });
    const fileInput = ov.querySelector('#ld-file');
    ov.querySelector('#ld-upload').addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
        if (!fileInput.files.length) return;
        const fd = new FormData();
        for (const f of fileInput.files) fd.append('files', f);
        try {
            await tfetch(`${API}/${loc.id}/photos`, { method: 'POST', body: fd });
            refreshGrid();
            openDetail(loc.id);
        } catch (err) { alert('上傳失敗：' + (err.message || err)); }
    });

    // 使用履歷：新增 / 刪除
    ov.querySelector('#lu-add').addEventListener('click', async () => {
        const body = {
            project_id: ov.querySelector('#lu-project').value || null,
            used_date: ov.querySelector('#lu-date').value || null,
            rating: ov.querySelector('#lu-rating').value ? Number(ov.querySelector('#lu-rating').value) : null,
            lesson: ov.querySelector('#lu-lesson').value.trim() || null,
        };
        if (!body.project_id && !body.lesson && !body.rating) { alert('至少填一項（專案/評分/心得）'); return; }
        try {
            await tfetch(`${API}/${loc.id}/usages`, { method: 'POST', json: body });
            refreshGrid();
            openDetail(loc.id);
        } catch (err) { alert('新增履歷失敗：' + (err.message || err)); }
    });
    ov.querySelectorAll('.loc-usage-del').forEach(btn => {
        btn.addEventListener('click', async () => {
            const uid = btn.closest('.loc-usage').dataset.uid;
            if (!confirm('確定刪除這筆履歷？')) return;
            try {
                await tfetch(`${API}/usages/${uid}`, { method: 'DELETE' });
                refreshGrid();
                openDetail(loc.id);
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        });
    });
}

// ── 新增 / 編輯表單（loc=null 為新增） ─────────────────

function _attrsToText(attrs) {
    return Object.entries(attrs || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
}

function _parseAttrs(text) {
    const out = {};
    for (const line of (text || '').split('\n')) {
        const m = line.match(/^\s*([^:：]+)[:：]\s*(.*)$/);
        if (m && m[1].trim()) out[m[1].trim()] = m[2].trim();
    }
    return out;
}

function _openEditor(loc) {
    const isNew = !loc;
    const v = (k) => esc((loc && loc[k]) || '');
    const row = (label, html) => `<div class="loc-form-row"><label>${label}</label>${html}</div>`;
    const ov = _mountOverlay(`
        <div class="loc-panel" style="width:min(560px,96vw);">
            <div class="loc-panel-head">
                <h3>${isNew ? '＋ 新增場景' : '✏️ 編輯：' + esc(loc.name)}</h3>
                <button class="loc-close" title="關閉">✕</button>
            </div>
            <div class="loc-panel-body" style="display:block;">
                ${row('名稱 *', `<input id="le-name" value="${v('name')}" placeholder="例：淡水某咖啡廳 2 樓">`)}
                <div style="display:flex;gap:8px;">
                    <div style="flex:1;">${row('分類', `<input id="le-category" list="le-cat-list" value="${v('category')}" placeholder="咖啡廳/工廠/戶外…">
                        <datalist id="le-cat-list">${CATEGORY_PRESETS.map(c => `<option value="${c}"></option>`).join('')}</datalist>`)}</div>
                    <div style="flex:1;">${row('縣市', `<input id="le-region" value="${v('region')}" placeholder="台北市">`)}</div>
                </div>
                ${row('地址', `<input id="le-address" value="${v('address')}">`)}
                <div style="display:flex;gap:8px;">
                    <div style="flex:1;">${row('聯絡人', `<input id="le-contact-name" value="${v('contact_name')}">`)}</div>
                    <div style="flex:1;">${row('電話', `<input id="le-contact-phone" value="${v('contact_phone')}">`)}</div>
                </div>
                <div class="loc-form-row" style="display:flex;align-items:center;gap:8px;">
                    <label style="margin:0;">需申請拍攝許可</label>
                    <input id="le-permit" type="checkbox" style="width:auto;"${loc && loc.permit_required ? ' checked' : ''}>
                    <input id="le-permit-note" value="${v('permit_note')}" placeholder="申請流程/窗口" style="flex:1;">
                </div>
                ${row('費用註記', `<input id="le-fee" value="${v('fee_note')}" placeholder="例：半天 $8,000 含電">`)}
                ${row('場地屬性（每行一項 key: value）', `<textarea id="le-attrs" rows="4" placeholder="電源: 有，2 迴路\n收音: 臨馬路偏吵\n停車: 路邊白線">${esc(_attrsToText(loc && loc.attributes))}</textarea>`)}
                ${row('標籤（逗號分隔）', `<input id="le-tags" value="${esc(((loc && loc.tags) || []).join(', '))}" placeholder="夕陽, 工業風, 好停車">`)}
                ${row('備註', `<textarea id="le-note" rows="3">${v('note')}</textarea>`)}
                <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
                    <button id="le-cancel" class="loc-btn ghost">取消</button>
                    <button id="le-save" class="loc-btn">${isNew ? '建立場景' : '儲存變更'}</button>
                </div>
            </div>
        </div>`);

    ov.querySelector('#le-cancel').addEventListener('click', () => {
        if (isNew) _closeOverlay(); else openDetail(loc.id);
    });
    ov.querySelector('#le-save').addEventListener('click', async () => {
        const name = ov.querySelector('#le-name').value.trim();
        if (!name) { alert('名稱必填'); return; }
        const body = {
            name,
            category: ov.querySelector('#le-category').value.trim(),
            region: ov.querySelector('#le-region').value.trim(),
            address: ov.querySelector('#le-address').value.trim(),
            contact_name: ov.querySelector('#le-contact-name').value.trim(),
            contact_phone: ov.querySelector('#le-contact-phone').value.trim(),
            permit_required: ov.querySelector('#le-permit').checked ? 1 : 0,
            permit_note: ov.querySelector('#le-permit-note').value.trim(),
            fee_note: ov.querySelector('#le-fee').value.trim(),
            attributes: _parseAttrs(ov.querySelector('#le-attrs').value),
            tags: ov.querySelector('#le-tags').value.split(/[,，]/).map(t => t.trim()).filter(Boolean),
            note: ov.querySelector('#le-note').value.trim(),
        };
        try {
            const d = isNew
                ? await tfetch(API, { method: 'POST', json: body })
                : await tfetch(`${API}/${loc.id}`, { method: 'PUT', json: body });
            refreshGrid();
            openDetail(d.location.id);
        } catch (err) {
            alert((isNew ? '建立' : '儲存') + '失敗：' + (err.message || err));
        }
    });
}
