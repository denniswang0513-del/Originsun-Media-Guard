/**
 * services.js — 服務項目 CRUD（首頁「服務」區塊用）
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, emptyRow } from '../website-utils.js';

let _services = [];
let _cats = [];

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>🧩 服務項目</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const [s, c] = await Promise.all([
            websiteFetch('/api/website/admin/services'),
            websiteFetch('/api/website/admin/categories'),
        ]);
        if (!isCurrent()) return;
        _services = s?.items || [];
        _cats = c?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🧩 服務項目', e);
        return;
    }

    const catOpts = _cats.map(c => `<option value="${c.id}">${esc(c.name_zh)}</option>`).join('');

    container.innerHTML = `
        <h2>🧩 服務項目 <span style="color:#888;font-size:13px;font-weight:400;">· ${_services.length} 項</span></h2>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 8px 0;font-size:13px;">新增服務</h3>
            <div style="display:grid;grid-template-columns:repeat(5,1fr) auto;gap:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">slug</label><input id="svc-new-slug" style="width:100%;" placeholder="e.g. tvc" /></div>
                <div><label style="color:#888;font-size:11px;">標題</label><input id="svc-new-title" style="width:100%;" placeholder="商業廣告" /></div>
                <div><label style="color:#888;font-size:11px;">圖示</label><input id="svc-new-icon" style="width:100%;" placeholder="video" /></div>
                <div><label style="color:#888;font-size:11px;">關聯分類</label><select id="svc-new-cat" style="width:100%;"><option value="">—</option>${catOpts}</select></div>
                <div><label style="color:#888;font-size:11px;">排序</label><input id="svc-new-sort" type="number" value="0" style="width:100%;" /></div>
                <button class="btn" onclick="window._websiteCreateService()">+ 新增</button>
            </div>
            <div style="margin-top:8px;">
                <input id="svc-new-short" style="width:100%;" placeholder="短描述（300 字內）" />
            </div>
        </div>

        <div class="card" style="padding:0;">
            <table id="svc-table"></table>
        </div>
    `;
    _renderTable();
}

function _renderTable() {
    const t = document.getElementById('svc-table');
    const catOpts = (currentId) => '<option value="">—</option>' +
        _cats.map(c => `<option value="${c.id}" ${c.id === currentId ? 'selected' : ''}>${esc(c.name_zh)}</option>`).join('');

    if (!_services.length) {
        t.innerHTML = emptyRow(7, '尚無服務');
        return;
    }
    t.innerHTML = `
        <thead><tr>
            <th>slug</th><th>標題</th><th>圖示</th><th>短描述</th><th>關聯分類</th><th>排序</th><th>可見</th><th>操作</th>
        </tr></thead>
        <tbody>
            ${_services.map(s => `
                <tr>
                    <td><input data-id="${s.id}" data-field="slug" value="${esc(s.slug)}" style="width:90px;" /></td>
                    <td><input data-id="${s.id}" data-field="title" value="${esc(s.title)}" style="width:140px;" /></td>
                    <td><input data-id="${s.id}" data-field="icon" value="${esc(s.icon || '')}" style="width:80px;" /></td>
                    <td><input data-id="${s.id}" data-field="short_desc" value="${esc(s.short_desc || '')}" style="width:240px;" /></td>
                    <td><select data-id="${s.id}" data-field="related_category_id" style="width:120px;">${catOpts(s.related_category_id)}</select></td>
                    <td><input type="number" data-id="${s.id}" data-field="sort_order" value="${s.sort_order}" style="width:50px;" /></td>
                    <td><input type="checkbox" data-id="${s.id}" data-field="visible" ${s.visible ? 'checked' : ''} /></td>
                    <td>
                        <button class="btn btn-sm" onclick="window._websiteSaveSvc(${s.id})">💾</button>
                        <button class="btn btn-sm btn-danger" onclick="window._websiteDeleteSvc(${s.id})">🗑</button>
                    </td>
                </tr>
            `).join('')}
        </tbody>
    `;
}

window._websiteSaveSvc = async (id) => {
    const patch = {};
    document.querySelectorAll(`#svc-table [data-id="${id}"]`).forEach(el => {
        const f = el.dataset.field;
        let v = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? Number(el.value) : el.value);
        if (f === 'related_category_id') v = v === '' ? null : Number(v);
        patch[f] = v;
    });
    try {
        await websiteFetch(`/api/website/admin/services/${id}`, { method: 'PUT', body: patch });
        toastOk('已更新');
        const idx = _services.findIndex(s => s.id === id);
        if (idx >= 0) Object.assign(_services[idx], patch);
    } catch (e) { toastErr(e.message); }
};

window._websiteDeleteSvc = async (id) => {
    if (!confirm('確定刪除此服務？')) return;
    try {
        await websiteFetch(`/api/website/admin/services/${id}`, { method: 'DELETE' });
        toastOk('已刪除');
        _services = _services.filter(s => s.id !== id);
        _renderTable();
    } catch (e) { toastErr(e.message); }
};

window._websiteCreateService = async () => {
    const catVal = document.getElementById('svc-new-cat').value;
    const body = {
        slug: document.getElementById('svc-new-slug').value.trim(),
        title: document.getElementById('svc-new-title').value.trim(),
        icon: document.getElementById('svc-new-icon').value.trim() || null,
        short_desc: document.getElementById('svc-new-short').value.trim() || null,
        related_category_id: catVal ? Number(catVal) : null,
        sort_order: Number(document.getElementById('svc-new-sort').value || 0),
        visible: true,
    };
    if (!body.slug || !body.title) { toastErr('slug 與標題必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/services', { method: 'POST', body });
        _services.push(created);
        toastOk('已新增');
        ['svc-new-slug','svc-new-title','svc-new-icon','svc-new-short'].forEach(id => document.getElementById(id).value = '');
        _renderTable();
    } catch (e) { toastErr(e.message); }
};
