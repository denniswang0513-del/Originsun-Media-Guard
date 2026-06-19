/**
 * nav.js — 🧭 導覽選單管理（頂部 NAV）+ 頁尾文案
 *
 * 全站 chrome（NAV + Footer）集中在此子視圖：
 *  - 上：頂部導覽選單 sortable CRUD（改名 / 排序 / 顯示隱藏 / 新增刪除）
 *  - 下：頁尾文案卡（copy.footer.* — 版權 + 區塊標題），複用 renderCopyCard
 *
 * 對外 Header.astro fetch /api/website/nav（visible=true ORDER BY sort_order），
 * 空則 fallback 到硬寫 7 筆；任一寫入 mark_dirty → 60s debounce → rebuild。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, emptyRow, renderCopyCard } from '../website-utils.js';

let _items = [];

// 頁尾文案（對應 Footer.astro 的 copy.footer.* fallback）
const FOOTER_COPY_BLOCKS = [
    { key: 'copyright', label: '版權列（可用 {year} 代入年份）', long: true,
      placeholderZh: '© {year} 源日影像 OriginsunStudio. All rights reserved.',
      placeholderEn: '© {year} OriginsunStudio. All rights reserved.',
      hint: '留空則維持預設版權文字；{year} 會自動換成當前年份。' },
    { key: 'contact_heading', label: '「聯絡」區塊標題', placeholderZh: '聯絡', placeholderEn: 'Contact' },
    { key: 'social_heading', label: '「社群」區塊標題', placeholderZh: '社群', placeholderEn: 'Follow' },
];

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>🧭 導覽選單</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let _settings = {};
    try {
        const [n, st] = await Promise.all([
            websiteFetch('/api/website/admin/nav'),
            websiteFetch('/api/website/admin/settings'),
        ]);
        if (!isCurrent()) return;
        _items = n?.items || [];
        _settings = st?.settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🧭 導覽選單', e);
        return;
    }

    container.innerHTML = `
        <h2>🧭 導覽選單 <span style="color:#888;font-size:13px;font-weight:400;">· ${_items.length} 項</span></h2>
        <p style="color:#888;font-size:12px;margin:-6px 0 14px;">對外官網頂部選單。排序用「排序」數字（小→左）；取消「可見」即隱藏。留空無項目時對外網站自動沿用預設 7 筆。</p>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 8px 0;font-size:13px;">新增選單項目</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr 1.2fr auto auto;gap:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">中文</label><input id="nav-new-zh" style="width:100%;" placeholder="作品集" /></div>
                <div><label style="color:#888;font-size:11px;">English</label><input id="nav-new-en" style="width:100%;" placeholder="Works" /></div>
                <div><label style="color:#888;font-size:11px;">連結（href）</label><input id="nav-new-href" style="width:100%;" placeholder="/works" /></div>
                <div><label style="color:#888;font-size:11px;">排序</label><input id="nav-new-sort" type="number" value="0" style="width:70px;" /></div>
                <button class="btn" onclick="window._websiteCreateNav()">+ 新增</button>
            </div>
        </div>

        <div class="card" style="padding:0;">
            <table id="nav-table"></table>
        </div>

        <div id="nav-footer-copy" style="margin-top:20px;"></div>
    `;
    _renderTable();

    const footerHost = document.getElementById('nav-footer-copy');
    if (footerHost) {
        footerHost.innerHTML = renderCopyCard('copy.footer', _settings, FOOTER_COPY_BLOCKS, {
            title: '📝 頁尾文案',
            note: '對應對外網站頁尾的版權列與「聯絡 / 社群」區塊標題；留空則維持預設文字。',
        });
    }
}

function _renderTable() {
    const t = document.getElementById('nav-table');
    if (!t) return;
    if (!_items.length) {
        t.innerHTML = emptyRow(5, '尚無選單項目（對外網站使用預設 7 筆）');
        return;
    }
    t.innerHTML = `
        <thead><tr>
            <th>排序</th><th>中文</th><th>English</th><th>連結</th><th>可見</th><th>操作</th>
        </tr></thead>
        <tbody>
            ${_items.map(n => `
                <tr>
                    <td><input type="number" data-id="${n.id}" data-field="sort_order" value="${n.sort_order}" style="width:55px;" /></td>
                    <td><input data-id="${n.id}" data-field="label_zh" value="${esc(n.label_zh)}" style="width:130px;" /></td>
                    <td><input data-id="${n.id}" data-field="label_en" value="${esc(n.label_en || '')}" style="width:130px;" /></td>
                    <td><input data-id="${n.id}" data-field="href" value="${esc(n.href)}" style="width:160px;" /></td>
                    <td><input type="checkbox" data-id="${n.id}" data-field="visible" ${n.visible ? 'checked' : ''} /></td>
                    <td>
                        <button class="btn btn-sm" onclick="window._websiteSaveNav(${n.id})">💾</button>
                        <button class="btn btn-sm btn-danger" onclick="window._websiteDeleteNav(${n.id})">🗑</button>
                    </td>
                </tr>
            `).join('')}
        </tbody>
    `;
}

window._websiteSaveNav = async (id) => {
    const patch = {};
    document.querySelectorAll(`#nav-table [data-id="${id}"]`).forEach(el => {
        const f = el.dataset.field;
        patch[f] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? Number(el.value) : el.value);
    });
    if (patch.label_zh !== undefined && !String(patch.label_zh).trim()) { toastErr('中文標籤必填'); return; }
    if (patch.href !== undefined && !String(patch.href).trim()) { toastErr('連結必填'); return; }
    try {
        await websiteFetch(`/api/website/admin/nav/${id}`, { method: 'PUT', body: patch });
        toastOk('已更新');
        const idx = _items.findIndex(n => n.id === id);
        if (idx >= 0) Object.assign(_items[idx], patch);
    } catch (e) { toastErr(e.message); }
};

window._websiteDeleteNav = async (id) => {
    if (!confirm('確定刪除此選單項目？')) return;
    try {
        await websiteFetch(`/api/website/admin/nav/${id}`, { method: 'DELETE' });
        toastOk('已刪除');
        _items = _items.filter(n => n.id !== id);
        _renderTable();
    } catch (e) { toastErr(e.message); }
};

window._websiteCreateNav = async () => {
    const body = {
        label_zh: document.getElementById('nav-new-zh').value.trim(),
        label_en: document.getElementById('nav-new-en').value.trim() || null,
        href: document.getElementById('nav-new-href').value.trim(),
        sort_order: Number(document.getElementById('nav-new-sort').value || 0),
        visible: true,
    };
    if (!body.label_zh || !body.href) { toastErr('中文標籤與連結必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/nav', { method: 'POST', body });
        _items.push(created);
        _items.sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
        toastOk('已新增');
        ['nav-new-zh', 'nav-new-en', 'nav-new-href'].forEach(id => document.getElementById(id).value = '');
        document.getElementById('nav-new-sort').value = '0';
        _renderTable();
    } catch (e) { toastErr(e.message); }
};
