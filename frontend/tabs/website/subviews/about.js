/**
 * about.js — 關於我們（公司介紹文案 + 團隊成員顯示切換）
 * 公司文案寫 website_settings (about.* keys)；團隊顯示透過 /api/website/team 讀、
 * 修改 crm_staff.show_on_website 的部分暫時未做（需要後端 admin endpoint）。
 * 此版本僅顯示已勾選的團隊成員清單 + 編輯公司文案。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError } from '../website-utils.js';

const _ABOUT_FIELDS = [
    { key: 'about.intro_zh', label: '公司介紹（中文）', long: true, placeholder: '源日影像是一間位於台北的...' },
    { key: 'about.intro_en', label: '公司介紹（英文）', long: true },
    { key: 'about.founded_year', label: '成立年份', placeholder: '2018' },
    { key: 'about.team_intro_zh', label: '團隊介紹（中文）', long: true },
];

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>👥 關於我們</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let settings = {};
    let team = [];
    try {
        const [s, t] = await Promise.all([
            websiteFetch('/api/website/admin/settings'),
            websiteFetch('/api/website/team'),
        ]);
        if (!isCurrent()) return;
        settings = s?.settings || {};
        team = t?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '👥 關於我們', e);
        return;
    }

    container.innerHTML = `
        <h2>👥 關於我們</h2>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">公司介紹文案</h3>
            ${_ABOUT_FIELDS.map(f => `
                <div style="margin-bottom:10px;">
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">${esc(f.label)}</label>
                    ${f.long
                        ? `<textarea data-key="${esc(f.key)}" rows="3" style="width:100%;resize:vertical;" placeholder="${esc(f.placeholder || '')}">${esc(settings[f.key] || '')}</textarea>`
                        : `<input data-key="${esc(f.key)}" value="${esc(settings[f.key] || '')}" placeholder="${esc(f.placeholder || '')}" style="width:100%;" />`}
                </div>
            `).join('')}
            <button class="btn" onclick="window._websiteSaveAbout()">💾 儲存公司文案</button>
        </div>

        <div class="card">
            <h3 style="color:#fff;margin:0 0 8px 0;font-size:14px;">團隊成員（show_on_website = true）</h3>
            <p style="color:#888;font-size:12px;margin:0 0 12px 0;">${team.length} 人將顯示在官網團隊頁。調整 show_on_website 請到 CRM「人力資源」Tab 編輯個別員工。</p>
            ${team.length
                ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;">
                      ${team.map(t => `
                          <div style="background:#1a1a1a;border:1px solid #333;padding:12px;border-radius:6px;text-align:center;">
                              ${t.photo_url ? `<img src="${esc(t.photo_url)}" style="width:64px;height:64px;border-radius:50%;object-fit:cover;" onerror="this.style.display='none'" />` : ''}
                              <div style="color:#fff;font-weight:500;margin-top:6px;">${esc(t.name)}</div>
                              <div style="color:#888;font-size:11px;">${esc(t.role || '-')}</div>
                          </div>
                      `).join('')}
                   </div>`
                : '<div style="color:#666;padding:20px;text-align:center;">尚無團隊成員標記顯示於官網</div>'}
        </div>
    `;
}

window._websiteSaveAbout = async () => {
    const values = {};
    document.querySelectorAll('#website-content [data-key]').forEach(el => {
        values[el.dataset.key] = el.value;
    });
    try {
        const r = await websiteFetch('/api/website/admin/settings', { method: 'PUT', body: { values } });
        toastOk(`已更新 ${r.updated} 項`);
    } catch (e) { toastErr(e.message); }
};
