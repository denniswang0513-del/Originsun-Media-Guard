/**
 * works.js — 作品集管理子視圖
 *
 * 網站管理員（role=website_admin）的作品全流程：列表/搜尋/篩選、新增、編輯、
 * 公開/精選切換。編輯 UI 透過 iframe 嵌入 /showcase-edit.html?token=XXX —
 * 重用既有 showcase-edit.html 避免重寫 544 行的 CRM 完稿 Tab 編輯器。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, debounce } from '../website-utils.js';

let _works = [];
let _categories = [];

async function _reloadWorks() {
    try {
        const res = await websiteFetch('/api/website/admin/works?include_non_public=true');
        _works = res?.items || [];
        _renderTable();
    } catch { /* silently skip — user can reload manually */ }
}

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>🎬 作品集管理</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const [worksRes, catsRes] = await Promise.all([
            websiteFetch('/api/website/admin/works?include_non_public=true'),
            websiteFetch('/api/website/admin/categories'),
        ]);
        if (!isCurrent()) return;
        _works = worksRes?.items || [];
        _categories = catsRes?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🎬 作品集管理', e);
        return;
    }

    container.innerHTML = `
        <h2>🎬 作品集管理 <span style="color:#888;font-size:13px;font-weight:400;">· ${_works.length} 件作品</span></h2>

        <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <button class="btn" onclick="window._websiteNewWork()" style="background:#059669;">➕ 新增作品</button>
            <input id="works-filter" type="text" placeholder="搜尋標題 / 客戶 / slug…" style="flex:1;min-width:240px;max-width:320px;" />
            <select id="works-cat-filter" style="min-width:140px;">
                <option value="">所有分類</option>
                ${_categories.map(c => `<option value="${c.id}">${esc(c.name_zh)}</option>`).join('')}
            </select>
            <label style="color:#ccc;font-size:12px;display:flex;align-items:center;gap:6px;">
                <input type="checkbox" id="works-public-only" /> 只顯示已公開
            </label>
        </div>

        <div class="card" style="padding:0;">
            <table id="works-table"></table>
        </div>
    `;

    document.getElementById('works-filter').addEventListener('input', debounce(_renderTable, 150));
    document.getElementById('works-cat-filter').addEventListener('change', _renderTable);
    document.getElementById('works-public-only').addEventListener('change', _renderTable);
    _renderTable();
    _ensureEditPanel();
}

function _renderTable() {
    const table = document.getElementById('works-table');
    if (!table) return;
    const q = (document.getElementById('works-filter')?.value || '').toLowerCase().trim();
    const catId = document.getElementById('works-cat-filter')?.value || '';
    const publicOnly = document.getElementById('works-public-only')?.checked;

    const rows = _works.filter(w => {
        if (publicOnly && !w.public) return false;
        if (q && !`${w.public_title || ''}${w.name || ''}${w.client || ''}${w.slug || ''}`.toLowerCase().includes(q)) return false;
        if (catId) {
            const catSlug = _categories.find(c => c.id === Number(catId))?.slug;
            if (!catSlug || !w.categories?.includes(catSlug)) return false;
        }
        return true;
    });

    if (!rows.length) {
        table.innerHTML = '<tr><td colspan="7" style="color:#888;text-align:center;padding:30px;">沒有符合條件的作品</td></tr>';
        return;
    }

    table.innerHTML = `
        <thead>
            <tr>
                <th>縮圖</th>
                <th>標題 / slug</th>
                <th>分類</th>
                <th>年份</th>
                <th>公開</th>
                <th>精選</th>
                <th title="個別作品強制 noindex（站級允許索引仍會被擋）">noindex</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            ${rows.map(w => {
                const thumb = w.youtube_id
                    ? `<img src="https://img.youtube.com/vi/${esc(w.youtube_id)}/default.jpg" style="width:80px;height:45px;object-fit:cover;border-radius:3px;background:#000;" onerror="this.style.visibility='hidden'" />`
                    : '<div style="width:80px;height:45px;background:#333;border-radius:3px;"></div>';
                return `
                <tr data-id="${esc(w.id)}">
                    <td>${thumb}</td>
                    <td>
                        <div style="color:#fff;">${esc(w.public_title || w.name)}</div>
                        <div style="color:#888;font-size:11px;">
                            ${esc(w.slug || '(未設 slug)')}
                            ${w.redirect_count > 0
                                ? `<span title="此作品有 ${w.redirect_count} 條舊 slug 被 301 轉址到目前 slug" style="color:#3b82f6;margin-left:6px;">↪ ${w.redirect_count}</span>`
                                : ''}
                        </div>
                    </td>
                    <td>${(w.categories || []).map(s => `<span class="website-pill">${esc(s)}</span>`).join(' ') || '<span style="color:#666;">-</span>'}</td>
                    <td>${w.year ?? '-'}</td>
                    <td>
                        <label style="cursor:pointer;">
                            <input type="checkbox" ${w.public ? 'checked' : ''} onchange="window._websiteTogglePublic('${esc(w.id)}', this.checked)" />
                        </label>
                    </td>
                    <td>
                        <label style="cursor:pointer;">
                            <input type="checkbox" ${w.featured ? 'checked' : ''} onchange="window._websiteToggleFeatured('${esc(w.id)}', this.checked)" />
                        </label>
                    </td>
                    <td>
                        <label style="cursor:pointer;" title="勾選後此作品強制 noindex">
                            <input type="checkbox" ${w.noindex ? 'checked' : ''} onchange="window._websiteToggleNoindex('${esc(w.id)}', this.checked)" />
                        </label>
                    </td>
                    <td>
                        <button class="btn btn-sm" onclick="window._websiteEditWork('${esc(w.id)}')">✎ 編輯</button>
                    </td>
                </tr>
                `;
            }).join('')}
        </tbody>
    `;
}

async function _toggleWorkFlag(pid, val, opts) {
    // opts: { endpoint, method='PUT', body, stateField, onLabel, offLabel }
    try {
        await websiteFetch(opts.endpoint, { method: opts.method || 'PUT', body: opts.body });
        const w = _works.find(x => x.id === pid);
        if (w) w[opts.stateField] = val;
        toastOk(`已${val ? opts.onLabel : opts.offLabel}`);
    } catch (e) {
        toastErr(e.message);
        _renderTable();
    }
}

window._websiteTogglePublic = (pid, val) => _toggleWorkFlag(pid, val, {
    endpoint: `/api/website/admin/works/${pid}`,
    body: { public: val },
    stateField: 'public', onLabel: '公開作品', offLabel: '下架作品',
});

window._websiteToggleFeatured = (pid, val) => _toggleWorkFlag(pid, val, {
    endpoint: `/api/website/admin/works/${pid}/featured`, method: 'POST',
    body: { featured: val },
    stateField: 'featured', onLabel: '設為精選', offLabel: '取消精選',
});

window._websiteToggleNoindex = (pid, val) => _toggleWorkFlag(pid, val, {
    endpoint: `/api/website/admin/works/${pid}`,
    body: { public_noindex: val },
    stateField: 'noindex', onLabel: '設為 noindex', offLabel: '取消 noindex',
});


// ══════════════════════════════════════════════════════════
// Edit panel（iframe 嵌 /showcase-edit.html?token=XXX）
// ══════════════════════════════════════════════════════════

function _ensureEditPanel() {
    if (document.getElementById('website-edit-panel-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'website-edit-panel-overlay';
    overlay.style.cssText = `
        position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9000;
        display:none;align-items:stretch;justify-content:flex-end;
    `;
    overlay.innerHTML = `
        <div id="website-edit-panel" style="
            width:75%;max-width:960px;height:100%;background:#0e0e0e;
            border-left:1px solid #2a2a2a;display:flex;flex-direction:column;
            box-shadow:-8px 0 24px rgba(0,0,0,0.6);
        ">
            <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #2a2a2a;background:#161616;flex-shrink:0;">
                <strong id="website-edit-panel-title" style="color:#fff;font-size:14px;flex:1;">編輯作品</strong>
                <button class="btn btn-sm btn-ghost" onclick="window._websiteCloseEditPanel()">✕ 關閉並重新整理</button>
            </div>
            <iframe id="website-edit-panel-iframe" style="flex:1;width:100%;border:0;background:#0e0e0e;"></iframe>
        </div>
    `;
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) window._websiteCloseEditPanel();
    });
    document.body.appendChild(overlay);
}

function _openEditPanel(url, title) {
    _ensureEditPanel();
    const overlay = document.getElementById('website-edit-panel-overlay');
    const iframe = document.getElementById('website-edit-panel-iframe');
    const titleEl = document.getElementById('website-edit-panel-title');
    if (titleEl) titleEl.textContent = title || '編輯作品';
    iframe.src = url;
    overlay.style.display = 'flex';
}

window._websiteCloseEditPanel = () => {
    const overlay = document.getElementById('website-edit-panel-overlay');
    if (!overlay) return;
    overlay.style.display = 'none';
    const iframe = document.getElementById('website-edit-panel-iframe');
    if (iframe) iframe.src = 'about:blank';
    // Fire-and-forget reload to pick up changes saved in iframe
    _reloadWorks();
};

window._websiteEditWork = async (pid) => {
    try {
        const r = await websiteFetch(`/api/website/admin/works/${pid}/edit-url`, { method: 'POST' });
        const w = _works.find(x => x.id === pid);
        _openEditPanel(r.edit_url, `編輯：${w?.public_title || w?.name || pid}`);
    } catch (e) {
        toastErr(e.message);
    }
};


// ══════════════════════════════════════════════════════════
// 新增作品 modal（走 window._createFormModal，styling 統一）
// ══════════════════════════════════════════════════════════

window._websiteNewWork = async () => {
    // 並行抓客戶 + 分類，兩個都允許失敗（給空陣列 fallback）— 任一個壞掉
    // 不該擋住整個新增流程。失敗時 console.warn 留痕跡。
    const [clients, allCats] = await Promise.all([
        websiteFetch('/api/website/admin/clients/lookup')
            .then(r => r?.items || [])
            .catch(e => { console.warn('[website/works] clients/lookup 失敗:', e.message || e); return []; }),
        websiteFetch('/api/website/admin/categories')
            .then(r => r?.items || [])
            .catch(e => { console.warn('[website/works] categories 失敗:', e.message || e); return []; }),
    ]);

    const cats = allCats.filter(c => (c.kind || 'category') === 'category');
    const tags = allCats.filter(c => c.kind === 'tag');
    const _opt = (c) => ({ value: c.id, label: c.name_zh });

    const fields = [
        { key: 'name', label: '作品名稱', type: 'text', required: true, autofocus: true },
        { key: 'client_id', label: '客戶（可選）', type: 'select', searchable: true,
          placeholder: '輸入客戶名稱搜尋…',
          options: [{ value: '', label: '（不指定）' },
                    ...clients.map(c => ({ value: c.id, label: c.name }))] },
        { key: 'year', label: '年份（可選）', type: 'number',
          placeholder: `例如 ${new Date().getFullYear()}` },
    ];
    if (cats.length) {
        fields.push({ type: 'divider' });
        fields.push({ key: 'category_ids', label: '分類（可複選）', type: 'checkboxes',
                      options: cats.map(_opt) });
    }
    if (tags.length) {
        if (!cats.length) fields.push({ type: 'divider' });
        fields.push({ key: 'tag_ids', label: '標籤（可複選）', type: 'checkboxes',
                      options: tags.map(_opt) });
    }

    window._createFormModal({
        id: 'website-new-work-modal',
        title: '➕ 新增作品',
        submitLabel: '建立並開編輯',
        fields,
        onSubmit: async (vals, setError, close) => {
            const name = (vals.name || '').trim();
            if (!name) { setError('請輸入作品名稱'); return; }
            const payload = { name };
            if (vals.client_id) payload.client_id = vals.client_id;
            const year = Number(vals.year);
            if (year) payload.year = year;
            // checkbox 收回的是 string[]，後端要 int[]
            const ids = [...(vals.category_ids || []), ...(vals.tag_ids || [])]
                .map(Number).filter(n => Number.isFinite(n));
            if (ids.length) payload.category_ids = ids;
            try {
                const r = await websiteFetch('/api/website/admin/works/create', {
                    method: 'POST', body: payload,
                });
                close();
                toastOk('作品已建立');
                _openEditPanel(r.edit_url, `編輯:${name}`);
                _reloadWorks();
            } catch (e) {
                setError(e.message || '建立失敗');
            }
        },
    });
};
