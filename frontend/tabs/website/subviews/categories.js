/**
 * categories.js — 作品分類 + 標籤 CRUD（兩區塊獨立管理）
 *
 * 「分類」（kind=category）放上排，主要用來標製作類型（形象/廣告/MV…）。
 * 「標籤」（kind=tag）放下排，標使用場景（展覽/講座/海外…）。
 * 兩者共用同一張 website_categories 表，差別只在 kind 欄。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, readRowPatch, emptyRow } from '../website-utils.js';

let _cats = [];

const KIND_META = {
    category: {
        title: '🎬 分類（製作類型）',
        hint: '製作類型例：商業廣告、紀錄片、MV、動畫等。對外作品頁第一排。',
        slugPlaceholder: 'e.g. commercial / documentary',
        namePlaceholder: '商業廣告',
        addBtn: '+ 新增分類',
        emptyMsg: '尚無分類，從上方表單新增第一個',
    },
    tag: {
        title: '🏷️ 標籤（使用場景）',
        hint: '使用場景例：展覽、講座、海外、線上獨家等。對外作品頁第二排（標籤雲樣式）。',
        slugPlaceholder: 'e.g. tag-exhibition / tag-overseas',
        namePlaceholder: '展覽',
        addBtn: '+ 新增標籤',
        emptyMsg: '尚無標籤，從上方表單新增第一個',
    },
};


export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>🏷️ 作品分類 / 標籤</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const data = await websiteFetch('/api/website/admin/categories');
        if (!isCurrent()) return;
        _cats = data?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🏷️ 作品分類 / 標籤', e);
        return;
    }

    const catCount = _cats.filter(c => (c.kind || 'category') === 'category').length;
    const tagCount = _cats.filter(c => c.kind === 'tag').length;

    container.innerHTML = `
        <h2>🏷️ 作品分類 / 標籤
            <span style="color:#888;font-size:13px;font-weight:400;">· ${catCount} 分類 + ${tagCount} 標籤</span>
        </h2>
        <p style="color:#666;font-size:12px;margin:0 0 20px;">
            兩者共用 slug 命名空間（不能撞）。對外作品集頁會分兩個區塊顯示。
        </p>

        ${_renderSectionHtml('category')}

        <div style="height:24px;"></div>

        ${_renderSectionHtml('tag')}
    `;

    _renderTable('category');
    _renderTable('tag');
}


function _renderSectionHtml(kind) {
    const meta = KIND_META[kind];
    const accent = kind === 'tag' ? '#9333ea' : '#3b82f6';  // 紫=tag, 藍=category
    return `
        <div style="border-left:3px solid ${accent};padding-left:14px;margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 4px;font-size:15px;">${meta.title}</h3>
            <p style="color:#888;font-size:11px;margin:0 0 10px;">${meta.hint}</p>
        </div>

        <div class="card" style="margin-bottom:12px;">
            <h4 style="color:#fff;margin:0 0 8px;font-size:12px;">新增</h4>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr 80px auto;gap:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">slug</label>
                    <input id="${kind}-new-slug" type="text" style="width:100%;" placeholder="${meta.slugPlaceholder}" /></div>
                <div><label style="color:#888;font-size:11px;">中文名</label>
                    <input id="${kind}-new-name-zh" type="text" style="width:100%;" placeholder="${meta.namePlaceholder}" /></div>
                <div><label style="color:#888;font-size:11px;">英文名（選填）</label>
                    <input id="${kind}-new-name-en" type="text" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">排序</label>
                    <input id="${kind}-new-sort" type="number" value="0" style="width:100%;" /></div>
                <button class="btn" onclick="window._websiteCreateCat('${kind}')">${meta.addBtn}</button>
            </div>
            <div style="margin-top:8px;">
                <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">描述（選填）</label>
                <textarea id="${kind}-new-desc" rows="2" style="width:100%;box-sizing:border-box;resize:vertical;" placeholder="分類頁上方說明段落，並作為該頁 SEO 描述（建議 30–200 字）"></textarea>
            </div>
        </div>

        <div class="card" style="padding:0;">
            <table id="${kind}-table"></table>
        </div>
    `;
}


function _renderTable(kind) {
    const meta = KIND_META[kind];
    const rows = _cats.filter(c => (c.kind || 'category') === kind);
    const t = document.getElementById(`${kind}-table`);
    if (!t) return;
    if (!rows.length) {
        t.innerHTML = emptyRow(8, meta.emptyMsg);
        return;
    }
    t.innerHTML = `
        <thead><tr>
            <th>ID</th><th>Slug</th><th>中文名</th><th>英文名</th>
            <th>作品數</th><th>可見</th><th>排序</th><th>操作</th>
        </tr></thead>
        <tbody>
            ${rows.map(c => `
                <tr>
                    <td style="color:#666;font-size:11px;">${c.id}</td>
                    <td><input data-id="${c.id}" data-field="slug" value="${esc(c.slug)}" style="width:120px;" /></td>
                    <td><input data-id="${c.id}" data-field="name_zh" value="${esc(c.name_zh)}" style="width:160px;" /></td>
                    <td><input data-id="${c.id}" data-field="name_en" value="${esc(c.name_en || '')}" style="width:160px;" /></td>
                    <td>${c.project_count ?? 0}</td>
                    <td><input type="checkbox" data-id="${c.id}" data-field="visible" ${c.visible ? 'checked' : ''} /></td>
                    <td><input type="number" data-id="${c.id}" data-field="sort_order" value="${c.sort_order}" style="width:60px;" /></td>
                    <td>
                        <button class="btn btn-sm" onclick="window._websiteSaveCat(${c.id}, '${kind}')">💾</button>
                        <button class="btn btn-sm btn-ghost" title="編輯描述" onclick="window._websiteToggleCatDesc(${c.id}, '${kind}')">📝</button>
                        <button class="btn btn-sm btn-danger" onclick="window._websiteDeleteCat(${c.id}, '${kind}')">🗑</button>
                        ${kind === 'category'
                            ? `<button class="btn btn-sm btn-ghost" title="改為標籤" onclick="window._websiteSwitchKind(${c.id}, 'tag')">→ 標籤</button>`
                            : `<button class="btn btn-sm btn-ghost" title="改為分類" onclick="window._websiteSwitchKind(${c.id}, 'category')">→ 分類</button>`}
                    </td>
                </tr>
                <tr class="cat-detail-row" data-detail-for="${c.id}" style="display:none;">
                    <td colspan="8" style="padding:8px 12px;background:#151515;">
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">描述（分類頁說明段落 · 該頁 SEO 描述）</label>
                        <textarea data-id="${c.id}" data-field="description" rows="3" style="width:100%;box-sizing:border-box;resize:vertical;">${esc(c.description || '')}</textarea>
                    </td>
                </tr>
            `).join('')}
        </tbody>
    `;
}


window._websiteToggleCatDesc = (id, kind) => {
    const row = document.querySelector(`#${kind}-table [data-detail-for="${id}"]`);
    if (row) row.style.display = (row.style.display === 'none') ? 'table-row' : 'none';
};


window._websiteSaveCat = async (id, kind) => {
    const patch = readRowPatch(`#${kind}-table`, id);
    try {
        await websiteFetch(`/api/website/admin/categories/${id}`, { method: 'PUT', body: patch });
        toastOk('已更新');
        const idx = _cats.findIndex(c => c.id === id);
        if (idx >= 0) Object.assign(_cats[idx], patch);
    } catch (e) { toastErr(e.message); }
};


window._websiteDeleteCat = async (id, kind) => {
    const label = kind === 'tag' ? '標籤' : '分類';
    if (!confirm(`確定刪除此${label}？所有作品的此關聯會一併清除。`)) return;
    try {
        await websiteFetch(`/api/website/admin/categories/${id}`, { method: 'DELETE' });
        toastOk('已刪除');
        _cats = _cats.filter(c => c.id !== id);
        _renderTable(kind);
    } catch (e) { toastErr(e.message); }
};


window._websiteSwitchKind = async (id, newKind) => {
    const target = newKind === 'tag' ? '標籤' : '分類';
    if (!confirm(`將此項移到「${target}」區塊？`)) return;
    try {
        await websiteFetch(`/api/website/admin/categories/${id}`, {
            method: 'PUT', body: { kind: newKind },
        });
        toastOk(`已移到${target}`);
        const idx = _cats.findIndex(c => c.id === id);
        if (idx >= 0) _cats[idx].kind = newKind;
        _renderTable('category');
        _renderTable('tag');
    } catch (e) { toastErr(e.message); }
};


window._websiteCreateCat = async (kind) => {
    const body = {
        kind,
        slug: document.getElementById(`${kind}-new-slug`).value.trim(),
        name_zh: document.getElementById(`${kind}-new-name-zh`).value.trim(),
        name_en: document.getElementById(`${kind}-new-name-en`).value.trim() || null,
        description: document.getElementById(`${kind}-new-desc`).value.trim() || null,
        sort_order: Number(document.getElementById(`${kind}-new-sort`).value || 0),
        visible: true,
    };
    if (!body.slug || !body.name_zh) { toastErr('slug 與中文名必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/categories', { method: 'POST', body });
        _cats.push({ ...created, project_count: 0 });
        const label = kind === 'tag' ? '標籤' : '分類';
        toastOk(`已新增${label}`);
        [`${kind}-new-slug`, `${kind}-new-name-zh`, `${kind}-new-name-en`, `${kind}-new-desc`].forEach(
            id => document.getElementById(id).value = ''
        );
        _renderTable(kind);
    } catch (e) { toastErr(e.message); }
};
