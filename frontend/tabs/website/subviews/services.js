/**
 * services.js — 服務項目 CRUD（首頁「服務」區塊用）
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, emptyRow, renderCopyCard } from '../website-utils.js';

let _services = [];
let _cats = [];

// /services 頁面行銷文案（對應 services.astro 的 copy.services.* fallback）
const COPY_BLOCKS = [
    { key: 'hero_eyebrow', label: 'Hero 上方小字（eyebrow）', type: 'text', placeholderZh: 'Services' },
    { key: 'hero_title', label: 'Hero 標題', placeholderZh: '我們能為您做的', placeholderEn: 'What We Can Do' },
    { key: 'hero_intro', label: 'Hero 介紹段', long: true, placeholderZh: '從前期腳本發想到後期交付…' },
    { key: 'cta_heading', label: '底部 CTA 標題', placeholderZh: '有影像製作需求？', placeholderEn: 'Have a Project in Mind?' },
    { key: 'cta_body', label: '底部 CTA 內文', long: true, placeholderZh: '不論是品牌形象片…' },
    { key: 'cta_button', label: '底部 CTA 按鈕', placeholderZh: '聯絡我們', placeholderEn: 'Get in Touch' },
];

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>🧩 服務項目</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let _settings = {};
    try {
        const [s, c, st] = await Promise.all([
            websiteFetch('/api/website/admin/services'),
            websiteFetch('/api/website/admin/categories'),
            websiteFetch('/api/website/admin/settings'),
        ]);
        if (!isCurrent()) return;
        _services = s?.items || [];
        _cats = c?.items || [];
        _settings = st?.settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🧩 服務項目', e);
        return;
    }

    const catOpts = _cats.map(c => `<option value="${c.id}">${esc(c.name_zh)}</option>`).join('');

    container.innerHTML = `
        <h2>🧩 服務項目 <span style="color:#888;font-size:13px;font-weight:400;">· ${_services.length} 項</span></h2>

        ${renderCopyCard('copy.services', _settings, COPY_BLOCKS, { title: '📝 服務頁文案', note: '對應 /services 頁的標題與 CTA；留空則維持預設文案。' })}

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
            <div style="margin-top:8px;">
                <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">完整描述（full_desc · 服務頁詳細段落，選填）</label>
                <textarea id="svc-new-full" rows="3" style="width:100%;box-sizing:border-box;resize:vertical;" placeholder="服務頁展開後的詳細說明段落"></textarea>
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
                        <button class="btn btn-sm btn-ghost" title="編輯完整描述" onclick="window._websiteToggleFullDesc(${s.id})">📝</button>
                        <button class="btn btn-sm btn-danger" onclick="window._websiteDeleteSvc(${s.id})">🗑</button>
                    </td>
                </tr>
                <tr class="svc-detail-row" data-detail-for="${s.id}" style="display:none;">
                    <td colspan="8" style="padding:8px 12px;background:#151515;">
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">完整描述（full_desc · 服務頁詳細段落）</label>
                        <textarea data-id="${s.id}" data-field="full_desc" rows="4" style="width:100%;box-sizing:border-box;resize:vertical;">${esc(s.full_desc || '')}</textarea>
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

window._websiteToggleFullDesc = (id) => {
    const row = document.querySelector(`#svc-table [data-detail-for="${id}"]`);
    if (row) row.style.display = (row.style.display === 'none') ? 'table-row' : 'none';
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
        full_desc: document.getElementById('svc-new-full').value.trim() || null,
        related_category_id: catVal ? Number(catVal) : null,
        sort_order: Number(document.getElementById('svc-new-sort').value || 0),
        visible: true,
    };
    if (!body.slug || !body.title) { toastErr('slug 與標題必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/services', { method: 'POST', body });
        _services.push(created);
        toastOk('已新增');
        ['svc-new-slug','svc-new-title','svc-new-icon','svc-new-short','svc-new-full'].forEach(id => document.getElementById(id).value = '');
        _renderTable();
    } catch (e) { toastErr(e.message); }
};
