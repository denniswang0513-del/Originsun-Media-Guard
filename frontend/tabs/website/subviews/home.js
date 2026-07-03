/**
 * home.js — 首頁設定（Showreel + WhoWeAre stats + Testimonials 評分）
 *
 * 實際資料存在 website_settings（home.* key），寫入共用 /admin/settings，
 * 透過 public /api/website/meta 暴露給 Astro build → 首頁元件即時渲染。
 *
 * 對映關係（這裡編的 = 首頁實際渲染的）：
 *   home.showreel_id      → WhoWeAre Showreel（「關於我們」段中央的影片）
 *   home.hero_youtube_id  → 上面 showreel_id 留空時的備援值（首頁最上方 Hero 是
 *                            精選作品輪播 HomeSlideshow，讀作品縮圖，不吃這兩個影片欄）
 *   home.stat{1..4}_*     → WhoWeAre 4 欄信任數字
 *   home.rating_value     → Testimonials 整體評分數字
 *   home.rating_count     → Testimonials 評價則數
 *
 * 註：FAQ / 客戶證言內容請到「SEO 內容」子視圖編；服務卡內容到「服務項目」編。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, renderCopyCard } from '../website-utils.js';

// 首頁各段落的標題 / 文案（對應首頁元件的 copy.home.* fallback）
// 註：影片 ID / 信任數字 / 評分由下方既有欄位編；FAQ / 證言 / 服務內容到各自子視圖編。
const COPY_BLOCKS = [
    { key: 'whoweare_eyebrow', label: '關於段 · eyebrow', type: 'text', placeholderZh: 'About Us' },
    { key: 'whoweare_heading', label: '關於段 · 標題（可換行）', long: true, hint: '按 Enter 換行，網站會照你斷的行顯示', placeholderZh: '我們是源日影像——十多年來，說了上百個品牌故事' },
    { key: 'whoweare_intro', label: '關於段 · 介紹', long: true, placeholderZh: '從商業廣告到紀實短片…' },
    { key: 'services_eyebrow', label: '服務段 · eyebrow', type: 'text', placeholderZh: 'Services' },
    { key: 'services_title', label: '服務段 · 標題', placeholderZh: '我們做四件事', placeholderEn: 'We Do Four Things' },
    { key: 'services_intro', label: '服務段 · 介紹', long: true, placeholderZh: '並把每一件做到專精…' },
    { key: 'featured_eyebrow', label: '精選作品段 · eyebrow', type: 'text', placeholderZh: 'Selected' },
    { key: 'featured_title', label: '精選作品段 · 標題', placeholderZh: '精選作品', placeholderEn: 'Featured Work' },
    { key: 'testimonials_eyebrow', label: '證言段 · eyebrow', type: 'text', placeholderZh: 'Testimonials' },
    { key: 'testimonials_title', label: '證言段 · 標題', placeholderZh: '客戶的真實回饋', placeholderEn: 'What Clients Say' },
    { key: 'insight_eyebrow', label: '專欄段 · eyebrow', type: 'text', placeholderZh: 'Insight' },
    { key: 'insight_title', label: '專欄段 · 標題', placeholderZh: '影像思考', placeholderEn: 'Behind the Craft' },
    { key: 'faq_eyebrow', label: 'FAQ 段 · eyebrow', type: 'text', placeholderZh: 'FAQ' },
    { key: 'faq_title', label: 'FAQ 段 · 標題', placeholderZh: '還有疑問？', placeholderEn: 'Any Questions?' },
    { key: 'cta_eyebrow', label: '底部 CTA · eyebrow', type: 'text', placeholderZh: 'Contact' },
    { key: 'cta_heading', label: '底部 CTA · 標題', placeholderZh: '準備好說你的故事了嗎？', placeholderEn: 'Ready to tell your story?' },
    { key: 'cta_body', label: '底部 CTA · 內文', long: true, placeholderZh: '第一次諮詢 30 分鐘，免費、無負擔…' },
    { key: 'cta_button', label: '底部 CTA · 按鈕', placeholderZh: '開始對話', placeholderEn: 'Start a conversation' },
];

// 關於我們區 Showreel 影片（首頁最上方 Hero 是精選作品輪播，不讀這兩個欄位）
const _VIDEO_FIELDS = [
    { key: 'home.showreel_id', label: '關於我們區 Showreel YouTube ID', placeholder: '預設 DRavYkTojAo', hint: '「關於我們」段落中央播放的 Showreel 影片。（首頁最上方 Hero 是精選作品輪播，不使用此欄）' },
    { key: 'home.hero_youtube_id', label: 'Showreel 備援 YouTube ID', placeholder: 'e.g. lQYKHJ7sryM', hint: '僅在上方 Showreel ID 留空時，作為關於我們區 Showreel 的備援來源。首頁最上方是精選作品輪播（HomeSlideshow），不使用此欄。' },
];

// WhoWeAre 4 欄信任數字（stat1..stat4，各 value + 中/英 label）
const _STAT_DEFAULTS = [
    { value: '10+',  zh: '年製作經驗', en: 'Years' },
    { value: '300+', zh: '完成作品',   en: 'Projects' },
    { value: '80+',  zh: '合作品牌',   en: 'Brands' },
    { value: '4.9',  zh: '平均評分',   en: 'Rating' },
];

// Testimonials 整體評分 badge
const _RATING_FIELDS = [
    { key: 'home.rating_value', label: '整體評分', placeholder: '4.9', hint: '客戶證言段右上方的大數字' },
    { key: 'home.rating_count', label: '評價則數', placeholder: '25', hint: '「基於 N 則真實評價」中的 N' },
];

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>🏠 首頁設定</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let settings = {};
    try {
        const data = await websiteFetch('/api/website/admin/settings');
        if (!isCurrent()) return;
        settings = data?.settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🏠 首頁設定', e);
        return;
    }

    const heroId = settings['home.hero_youtube_id'] || '';
    const showId = settings['home.showreel_id'] || heroId || '';

    // 單一 data-key 設定欄位（label + input + 選填 hint）— video / rating 共用同一份模板。
    const simpleField = f => `
        <div style="margin-bottom:10px;">
            <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">${esc(f.label)}</label>
            <input data-key="${esc(f.key)}" value="${esc(settings[f.key] || '')}" placeholder="${esc(f.placeholder || '')}" style="width:100%;" />
            ${f.hint ? `<div style="color:#666;font-size:10px;margin-top:2px;">${esc(f.hint)}</div>` : ''}
        </div>`;
    const videoInputs = _VIDEO_FIELDS.map(simpleField).join('');
    const ratingInputs = _RATING_FIELDS.map(simpleField).join('');

    const statRows = _STAT_DEFAULTS.map((d, idx) => {
        const n = idx + 1;
        return `
        <div style="display:grid;grid-template-columns:90px 1fr 1fr;gap:8px;margin-bottom:8px;align-items:end;">
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">數字 ${n}</label>
                <input data-key="home.stat${n}_value" value="${esc(settings[`home.stat${n}_value`] || '')}" placeholder="${esc(d.value)}" style="width:100%;" />
            </div>
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">標籤（中）</label>
                <input data-key="home.stat${n}_label_zh" value="${esc(settings[`home.stat${n}_label_zh`] || '')}" placeholder="${esc(d.zh)}" style="width:100%;" />
            </div>
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">標籤（英）</label>
                <input data-key="home.stat${n}_label_en" value="${esc(settings[`home.stat${n}_label_en`] || '')}" placeholder="${esc(d.en)}" style="width:100%;" />
            </div>
        </div>`;
    }).join('');

    container.innerHTML = `
        <h2>🏠 首頁設定</h2>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
            <div class="card">
                <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">影片</h3>
                ${videoInputs}
            </div>

            <div class="card">
                <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">Showreel 預覽</h3>
                ${showId
                    ? `<div style="position:relative;padding-top:56.25%;background:#000;border-radius:4px;overflow:hidden;">
                          <iframe src="https://www.youtube-nocookie.com/embed/${esc(showId)}?autoplay=0&controls=1"
                                  style="position:absolute;inset:0;width:100%;height:100%;border:0;"></iframe>
                       </div>`
                    : '<div style="color:#666;padding:30px;text-align:center;border:1px dashed #333;border-radius:4px;">尚未設定影片</div>'}
            </div>
        </div>

        <div class="card" style="margin-bottom:16px;">
            <h3 style="color:#fff;margin:0 0 4px 0;font-size:14px;">信任數字（關於我們段 4 欄）</h3>
            <p style="color:#888;font-size:11px;margin:0 0 12px 0;">留空則使用預設值（placeholder 顯示的數字）。</p>
            ${statRows}
        </div>

        <div class="card" style="margin-bottom:16px;">
            <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">客戶證言整體評分</h3>
            ${ratingInputs}
        </div>

        <div style="margin-bottom:16px;">
            <button class="btn" onclick="window._websiteSaveHome()">💾 儲存全部（影片 / 數字 / 評分）</button>
        </div>

        ${renderCopyCard('copy.home', settings, COPY_BLOCKS, { title: '📝 首頁各段標題與文案', note: '對應首頁各區塊的 eyebrow / 標題 / 介紹 / CTA；留空則維持預設文案。此卡有獨立的儲存按鈕。' })}

        <div class="card">
            <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">其他首頁內容（在別的子視圖編輯）</h3>
            <p style="color:#888;font-size:12px;margin:0 0 8px 0;">
                · 精選作品 ⭐：到「作品集管理」切換作品的精選 checkbox（最多 6 件）。<br>
                · FAQ 常見問題 / 客戶證言文字：到「SEO 內容」子視圖編輯。<br>
                · 服務卡（四件事 + 作品數）：到「服務項目」子視圖編輯，作品數依分類自動統計。
            </p>
            <button class="btn btn-ghost btn-sm" onclick="window.websiteSwitchSubview && window.websiteSwitchSubview('works')" style="margin-right:6px;">→ 作品集管理</button>
            <button class="btn btn-ghost btn-sm" onclick="window.websiteSwitchSubview && window.websiteSwitchSubview('seo')" style="margin-right:6px;">→ SEO 內容</button>
            <button class="btn btn-ghost btn-sm" onclick="window.websiteSwitchSubview && window.websiteSwitchSubview('services')">→ 服務項目</button>
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
