// blog/categories.js — 拆自 blog.js：Sub-tab 2 📚 分類 CRUD（純搬移，行為不變）
import { websiteFetch, esc, toastOk, toastErr, readRowPatch } from '../../website-utils.js';
import { _state, _blog } from './shared.js';
import { _renderShell } from './shell.js';

// ══════════════════════════════════════════════════════════
// Sub-tab 2: 📚 分類 CRUD
// ══════════════════════════════════════════════════════════

function _viewCategories() {
    return `
        <div class="card" style="margin-bottom:12px;background:#1f1f1f;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:13px;">新增分類</h3>
            <div style="display:grid;grid-template-columns:120px 1fr 1fr 100px auto;gap:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">slug</label>
                    <input id="cat-new-slug" type="text" placeholder="workflow" style="width:100%;font-family:monospace;" /></div>
                <div><label style="color:#888;font-size:11px;">中文名</label>
                    <input id="cat-new-zh" type="text" placeholder="工作流程" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">英文名（選填）</label>
                    <input id="cat-new-en" type="text" placeholder="Workflow" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">排序</label>
                    <input id="cat-new-sort" type="number" value="0" style="width:100%;" /></div>
                <button class="btn" onclick="window._blog.createCategory()">+ 新增</button>
            </div>
        </div>

        <table>
            <thead><tr>
                <th style="width:40px;">ID</th>
                <th style="width:120px;">slug</th>
                <th>中文</th>
                <th>英文</th>
                <th style="width:60px;">文章</th>
                <th style="width:60px;">排序</th>
                <th style="width:50px;">可見</th>
                <th style="width:90px;">操作</th>
            </tr></thead>
            <tbody>${
                _state.categories.length
                    ? _state.categories.map(_categoryRow).join('')
                    : '<tr><td colspan="8" style="padding:30px;text-align:center;color:#888;">尚無分類</td></tr>'
            }</tbody>
        </table>
    `;
}

function _categoryRow(c) {
    return `
        <tr>
            <td style="color:#666;">${c.id}</td>
            <td><input data-id="${c.id}" data-field="slug" value="${esc(c.slug)}" style="width:100%;font-family:monospace;" /></td>
            <td><input data-id="${c.id}" data-field="label_zh" value="${esc(c.label_zh)}" style="width:100%;" /></td>
            <td><input data-id="${c.id}" data-field="label_en" value="${esc(c.label_en || '')}" style="width:100%;" /></td>
            <td style="text-align:center;color:#aaa;">${c.post_count ?? 0}</td>
            <td><input type="number" data-id="${c.id}" data-field="sort_order" value="${c.sort_order}" style="width:100%;" /></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${c.id}" data-field="visible" ${c.visible ? 'checked' : ''} /></td>
            <td>
                <button class="btn btn-sm" onclick="window._blog.saveCategory(${c.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._blog.deleteCategory(${c.id})">🗑</button>
            </td>
        </tr>
    `;
}

_blog.createCategory = async () => {
    const body = {
        slug: document.getElementById('cat-new-slug').value.trim(),
        label_zh: document.getElementById('cat-new-zh').value.trim(),
        label_en: document.getElementById('cat-new-en').value.trim() || null,
        sort_order: Number(document.getElementById('cat-new-sort').value || 0),
        visible: true,
    };
    if (!body.slug || !body.label_zh) { toastErr('slug 與中文名必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/post_categories', { method: 'POST', body });
        _state.categories.push({ ...created, post_count: 0 });
        toastOk('已新增');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

_blog.saveCategory = async (id) => {
    try {
        const updated = await websiteFetch(`/api/website/admin/post_categories/${id}`, {
            method: 'PUT', body: readRowPatch('#blog-tab-body', id),
        });
        const idx = _state.categories.findIndex(c => c.id === id);
        if (idx >= 0) _state.categories[idx] = { ..._state.categories[idx], ...updated };
        toastOk('已更新');
    } catch (e) { toastErr(e.message); }
};

_blog.deleteCategory = async (id) => {
    const c = _state.categories.find(x => x.id === id);
    if (c?.post_count > 0 &&
        !confirm(`「${c.label_zh}」有 ${c.post_count} 篇文章引用，會被一起拿掉分類。確定？`)) return;
    if (!c?.post_count && !confirm(`刪除分類「${c?.label_zh}」？`)) return;
    try {
        await websiteFetch(`/api/website/admin/post_categories/${id}`, { method: 'DELETE' });
        _state.categories = _state.categories.filter(x => x.id !== id);
        toastOk('已刪除');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

export { _viewCategories };
