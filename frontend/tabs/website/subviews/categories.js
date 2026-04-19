/**
 * categories.js — 作品分類 CRUD
 */
import { websiteFetch, esc, toastOk, toastErr } from '../website-utils.js';

let _cats = [];

export default async function render(container) {
    container.innerHTML = '<h2>🏷️ 作品分類</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const data = await websiteFetch('/api/website/admin/categories');
        _cats = data?.items || [];
    } catch (e) {
        container.innerHTML = `<h2>🏷️ 作品分類</h2><div class="card" style="color:#f87171;">${esc(e.message)}</div>`;
        return;
    }

    container.innerHTML = `
        <h2>🏷️ 作品分類 <span style="color:#888;font-size:13px;font-weight:400;">· ${_cats.length} 類</span></h2>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 8px 0;font-size:13px;">新增分類</h3>
            <div style="display:grid;grid-template-columns:repeat(4,1fr) auto;gap:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">slug</label><input id="cat-new-slug" type="text" style="width:100%;" placeholder="e.g. tvc" /></div>
                <div><label style="color:#888;font-size:11px;">中文名</label><input id="cat-new-name-zh" type="text" style="width:100%;" placeholder="商業廣告" /></div>
                <div><label style="color:#888;font-size:11px;">英文名（選填）</label><input id="cat-new-name-en" type="text" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">排序</label><input id="cat-new-sort" type="number" value="0" style="width:100%;" /></div>
                <button class="btn" onclick="window._websiteCreateCategory()">+ 新增</button>
            </div>
        </div>

        <div class="card" style="padding:0;">
            <table id="cat-table"></table>
        </div>
    `;
    _renderTable();
}

function _renderTable() {
    const t = document.getElementById('cat-table');
    if (!_cats.length) {
        t.innerHTML = '<tr><td colspan="6" style="padding:30px;text-align:center;color:#888;">尚無分類</td></tr>';
        return;
    }
    t.innerHTML = `
        <thead><tr>
            <th>ID</th><th>Slug</th><th>中文名</th><th>英文名</th><th>作品數</th><th>可見</th><th>排序</th><th>操作</th>
        </tr></thead>
        <tbody>
            ${_cats.map(c => `
                <tr>
                    <td style="color:#666;font-size:11px;">${c.id}</td>
                    <td><input data-id="${c.id}" data-field="slug" value="${esc(c.slug)}" style="width:100px;" /></td>
                    <td><input data-id="${c.id}" data-field="name_zh" value="${esc(c.name_zh)}" style="width:140px;" /></td>
                    <td><input data-id="${c.id}" data-field="name_en" value="${esc(c.name_en || '')}" style="width:140px;" /></td>
                    <td>${c.project_count ?? 0}</td>
                    <td><input type="checkbox" data-id="${c.id}" data-field="visible" ${c.visible ? 'checked' : ''} /></td>
                    <td><input type="number" data-id="${c.id}" data-field="sort_order" value="${c.sort_order}" style="width:60px;" /></td>
                    <td>
                        <button class="btn btn-sm" onclick="window._websiteSaveCat(${c.id})">💾</button>
                        <button class="btn btn-sm btn-danger" onclick="window._websiteDeleteCat(${c.id})">🗑</button>
                    </td>
                </tr>
            `).join('')}
        </tbody>
    `;
}

window._websiteSaveCat = async (id) => {
    const patch = {};
    document.querySelectorAll(`#cat-table [data-id="${id}"]`).forEach(el => {
        const f = el.dataset.field;
        patch[f] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? Number(el.value) : el.value);
    });
    try {
        await websiteFetch(`/api/website/admin/categories/${id}`, { method: 'PUT', body: patch });
        toastOk('已更新');
        const idx = _cats.findIndex(c => c.id === id);
        if (idx >= 0) Object.assign(_cats[idx], patch);
    } catch (e) { toastErr(e.message); }
};

window._websiteDeleteCat = async (id) => {
    if (!confirm('確定刪除此分類？所有作品的此分類關聯會一併清除。')) return;
    try {
        await websiteFetch(`/api/website/admin/categories/${id}`, { method: 'DELETE' });
        toastOk('已刪除');
        _cats = _cats.filter(c => c.id !== id);
        _renderTable();
    } catch (e) { toastErr(e.message); }
};

window._websiteCreateCategory = async () => {
    const body = {
        slug: document.getElementById('cat-new-slug').value.trim(),
        name_zh: document.getElementById('cat-new-name-zh').value.trim(),
        name_en: document.getElementById('cat-new-name-en').value.trim() || null,
        sort_order: Number(document.getElementById('cat-new-sort').value || 0),
        visible: true,
    };
    if (!body.slug || !body.name_zh) { toastErr('slug 與中文名必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/categories', { method: 'POST', body });
        _cats.push({ ...created, project_count: 0 });
        toastOk('已新增');
        ['cat-new-slug','cat-new-name-zh','cat-new-name-en'].forEach(id => document.getElementById(id).value = '');
        _renderTable();
    } catch (e) { toastErr(e.message); }
};
