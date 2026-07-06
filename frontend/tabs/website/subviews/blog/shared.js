// blog/shared.js — 拆自 blog.js：共用常數 + _state + window._blog 命名空間單例（純搬移，行為不變）
// _state 物件本身不重新賦值（各子模組只做屬性讀寫），可安全跨模組 import。
import { getApiBase } from '../../website-utils.js';

const SUB_TABS = ['posts', 'categories', 'notion', 'seo-migration'];

// /news（影像專欄列表）頁面行銷文案（對應 news/index.astro 的 copy.news.* fallback）
const NEWS_COPY_BLOCKS = [
    { key: 'hero_eyebrow', label: 'Hero 上方小字（eyebrow）', type: 'text', placeholderZh: 'Insight' },
    { key: 'hero_title', label: 'Hero 標題', placeholderZh: '影像專欄', placeholderEn: 'Insight' },
    { key: 'hero_image', label: 'Hero 背景圖（URL / 路徑）', type: 'text', placeholderZh: '留空用預設 placeholder 圖', hint: '深色 hero 的滿版背景圖；可貼完整 URL 或 /uploads/... 路徑。' },
    { key: 'intro', label: '介紹段落', long: true, placeholderZh: '源日影像專欄內容涵蓋影像製作的常見流程…' },
];

// upload endpoint 回的 URL 是相對路徑 /uploads/posts/{id}/{name}.webp，
// 但 admin Tab 是從 master Web UI 開啟的（origin = master），那邊沒這個路徑。
// 圖片實際存在 NAS website 容器 → 預覽時要 prepend NAS API base。
// 完整 URL（http/https/data/blob） 原樣 passthrough。
function _resolveImageUrl(url) {
    if (!url) return '';
    if (/^(https?:|data:|blob:)/i.test(url)) return url;
    if (url.startsWith('/')) return getApiBase() + url;
    return url;
}

// 文章狀態 metadata 集中（label + 顯示色 + emoji 一處改全套同步）
const STATUS = {
    draft:     { label: '草稿',   color: '#5f3f1e', emoji: '📝' },
    published: { label: '已發布', color: '#1e5f2e', emoji: '🚀' },
    archived:  { label: '已下架', color: '#3a3a3a', emoji: '🗄' },
};
const STATUS_FALLBACK = { label: '?', color: '#444', emoji: '❓' };

const EMPTY_POST = Object.freeze({
    id: null, slug: '', title: '', excerpt: '', cover_url: '',
    body: [], category_slugs: [], status: 'draft', published_at: null,
    seo_title: '', seo_description: '', og_image_url: '', canonical_url: '',
    noindex: false, author_name: '', author_url: '',
    ai_allow_override: null, old_urls: [], faqs: [],
});

let _state = {
    activeTab: 'posts',
    posts: [],
    categories: [],
    notionStatus: { connected: false, has_token: false, has_database_id: false },
    rebuildStatus: { state: 'idle' },
    redirectCount: 0,
    redirectSyncOk: null,    // null = 未試過 / true / false
    lastRedirectSyncAt: null,
    filters: { status: '', category: '', q: '' },
    settings: {},            // website_settings（給 /news 頁面文案卡用）
};
const _blog = (window._blog = window._blog || {});

export { SUB_TABS, NEWS_COPY_BLOCKS, _resolveImageUrl, STATUS, STATUS_FALLBACK, EMPTY_POST, _state, _blog };
