/**
 * works.js — 作品集管理子視圖
 * 列出所有作品（含未公開），可切換公開狀態、切精選、編輯對外欄位、刪除分類關聯。
 * 實際「對外欄位編輯」建議走 CRM 專案 Tab 的「對外展示」子區塊（M-D-4），
 * 這裡提供輕量編輯 + 排序 + 精選 toggle。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, debounce } from '../website-utils.js';

let _works = [];
let _categories = [];

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

        <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;">
            <input id="works-filter" type="text" placeholder="搜尋標題 / 客戶 / slug…" style="flex:1;max-width:320px;" />
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
                        <div style="color:#888;font-size:11px;">${esc(w.slug || '(未設 slug)')}</div>
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
                        <a href="http://localhost:8000/#crm_projects" class="btn btn-sm btn-ghost" style="text-decoration:none;">CRM 詳編</a>
                    </td>
                </tr>
                `;
            }).join('')}
        </tbody>
    `;
}

window._websiteTogglePublic = async (pid, val) => {
    try {
        await websiteFetch(`/api/website/admin/works/${pid}`, {
            method: 'PUT',
            body: { public: val },
        });
        const w = _works.find(x => x.id === pid);
        if (w) w.public = val;
        toastOk(`已${val ? '公開' : '下架'}作品`);
    } catch (e) {
        toastErr(e.message);
        _renderTable();
    }
};

window._websiteToggleFeatured = async (pid, val) => {
    try {
        await websiteFetch(`/api/website/admin/works/${pid}/featured`, {
            method: 'POST',
            body: { featured: val },
        });
        const w = _works.find(x => x.id === pid);
        if (w) w.featured = val;
        toastOk(`已${val ? '設為' : '取消'}精選`);
    } catch (e) {
        toastErr(e.message);
        _renderTable();
    }
};
