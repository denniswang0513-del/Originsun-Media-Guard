/**
 * home.js — 首頁設定（Hero YouTube + 標語 + CTA + 精選作品）
 * 實際資料存在 website_settings（home.* key），寫入共用 /admin/settings。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError } from '../website-utils.js';

const _FIELDS = [
    { key: 'home.hero_youtube_id', label: 'Hero 影片 YouTube ID', placeholder: 'e.g. lQYKHJ7sryM', hint: '首頁背景播放的影片（建議 muted + loop）' },
    { key: 'home.tagline_zh', label: '主標語（中文）', placeholder: 'Best Story, Best Production' },
    { key: 'home.tagline_en', label: '主標語（英文）', placeholder: 'Best Story, Best Production' },
    { key: 'home.cta_text', label: 'CTA 按鈕文字', placeholder: '與我們聯繫' },
    { key: 'home.cta_url', label: 'CTA 連結', placeholder: '/contact' },
];

export default async function render(container) {
    container.innerHTML = '<h2>🏠 首頁設定</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let settings = {};
    try {
        const data = await websiteFetch('/api/website/admin/settings');
        if (!container.isConnected) return;
        settings = data?.settings || {};
    } catch (e) {
        renderLoadError(container, '🏠 首頁設定', e);
        return;
    }

    const heroId = settings['home.hero_youtube_id'] || '';

    container.innerHTML = `
        <h2>🏠 首頁設定</h2>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
            <div class="card">
                <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">Hero 區</h3>
                ${_FIELDS.map(f => `
                    <div style="margin-bottom:10px;">
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">${esc(f.label)}</label>
                        <input data-key="${esc(f.key)}" value="${esc(settings[f.key] || '')}" placeholder="${esc(f.placeholder || '')}" style="width:100%;" />
                        ${f.hint ? `<div style="color:#666;font-size:10px;margin-top:2px;">${esc(f.hint)}</div>` : ''}
                    </div>
                `).join('')}
                <button class="btn" onclick="window._websiteSaveHome()">💾 儲存</button>
            </div>

            <div class="card">
                <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">Hero 預覽</h3>
                ${heroId
                    ? `<div style="position:relative;padding-top:56.25%;background:#000;border-radius:4px;overflow:hidden;">
                          <iframe src="https://www.youtube-nocookie.com/embed/${esc(heroId)}?autoplay=0&controls=1"
                                  style="position:absolute;inset:0;width:100%;height:100%;border:0;"></iframe>
                       </div>`
                    : '<div style="color:#666;padding:30px;text-align:center;border:1px dashed #333;border-radius:4px;">尚未設定 Hero 影片</div>'}
            </div>
        </div>

        <div class="card">
            <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">首頁精選作品</h3>
            <p style="color:#888;font-size:12px;margin:0;">請到「作品集管理」子視圖切換作品的 ⭐ 精選 checkbox，最多顯示 6 件精選作品。不足時會自動補最新公開作品。</p>
            <button class="btn btn-ghost btn-sm" onclick="window.websiteSwitchSubview && window.websiteSwitchSubview('works')" style="margin-top:8px;">→ 前往作品集管理</button>
        </div>
    `;
}

window._websiteSaveHome = async () => {
    const values = {};
    document.querySelectorAll('#website-content [data-key]').forEach(el => {
        values[el.dataset.key] = el.value;
    });
    try {
        const r = await websiteFetch('/api/website/admin/settings', { method: 'PUT', body: { values } });
        toastOk(`已更新 ${r.updated} 項`);
    } catch (e) { toastErr(e.message); }
};
