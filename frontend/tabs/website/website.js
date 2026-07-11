/**
 * website.js — 官網管理 Tab 主進入點
 *
 * 職責：
 * - 子視圖 lazy dynamic import + DOM 掛載
 * - API base 顯示 + health check（3 秒 ping 一次 /healthz）
 * - 未處理詢問 badge 輪詢
 *
 * 子視圖實作：./subviews/<name>.js，匯出 default async function render(container)
 */

import { getApiBase, websiteFetch, esc } from './website-utils.js';
import { initRebuildBar, destroyRebuildBar } from './rebuild-bar.js';
import { createSubviewLoader } from '../../js/shared/subview-loader.js';

const SUBVIEWS = [
    'dashboard', 'home', 'works', 'categories',
    'services', 'credits', 'about', 'inquiries', 'blog', 'seo', 'translation', 'social', 'redirects', 'awards', 'initiatives', 'nav', 'settings', 'backup',
];

let _activeSubview = 'dashboard';
let _switchToken = 0;          // bump on每次 switch，讓 async 延續能辨識自己是否還在檯面上
let _healthTimer = null;
let _badgeTimer = null;

export async function initWebsiteTab() {
    // 顯示當前 API base（給使用者除錯參考）；空 = 同源打 serve 本頁的 agent
    const apiDisplay = document.getElementById('website-api-base-display');
    if (apiDisplay) apiDisplay.textContent = getApiBase() || '同源（本機 agent）';

    // 綁左側導覽
    document.querySelectorAll('.website-nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.subview;
            if (view && view !== _activeSubview) switchSubview(view);
        });
    });

    // 「網站工具」可收合（狀態記 localStorage，預設收合）
    _initToolsCollapse();

    // Rebuild 狀態列（網站 Tab 頂部，跨 subview 共用）
    _initRebuildBarOnce();

    // 初次載入預設子視圖
    await switchSubview('dashboard');

    // 背景任務
    _startHealthCheck();
    _startBadgeRefresh();
}

function _initToolsCollapse() {
    const toggle = document.getElementById('website-tools-toggle');
    const group = document.getElementById('website-tools-group');
    const arrow = document.getElementById('website-tools-arrow');
    if (!toggle || !group || !arrow) return;
    const KEY = 'website_tools_collapsed';
    const apply = (collapsed) => {
        group.style.display = collapsed ? 'none' : 'block';
        arrow.textContent = collapsed ? '▸' : '▾';
    };
    // 預設收合；但若目前所在子視圖屬於工具群組，強制展開才看得到 active 項
    let collapsed = localStorage.getItem(KEY) !== '0';
    if (group.querySelector(`[data-subview="${_activeSubview}"]`)) collapsed = false;
    apply(collapsed);
    toggle.addEventListener('click', () => {
        collapsed = group.style.display !== 'none';   // 目前展開 → 要收合
        apply(collapsed);
        localStorage.setItem(KEY, collapsed ? '1' : '0');
    });
}

function _initRebuildBarOnce() {
    const nav = document.getElementById('website-nav');
    if (!nav || nav.querySelector('#website-rebuild-bar')) return;
    const bar = document.createElement('div');
    // 加在 nav 末尾（既有「API: ... 狀態: ...」資訊 block 後面）
    nav.appendChild(bar);
    initRebuildBar(bar);
}

window.initWebsiteTab = initWebsiteTab;

const _LOADING_HTML = '<div style="color:#888;padding:40px;text-align:center;">載入中…</div>';

async function switchSubview(name) {
    if (!SUBVIEWS.includes(name)) return;
    _activeSubview = name;
    const myToken = ++_switchToken;
    const isCurrent = () => myToken === _switchToken;

    document.querySelectorAll('.website-nav-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.subview === name)
    );

    const content = document.getElementById('website-content');
    if (!content) return;
    content.innerHTML = _LOADING_HTML;

    await _loadSubviewInto(content, name, isCurrent, false);
}
window.websiteSwitchSubview = switchSubview;

// 共用 loader（含 retry/cache-bust/isCurrent 護欄與「ES module map 永久快取
// rejected import」說明，見 js/shared/subview-loader.js）；importer closure
// 留在本檔，./subviews/ 相對路徑才會以 website/ 為基準。
const _loadSubviewInto = createSubviewLoader({
    importer: (name, cacheBust) => import(cacheBust
        ? `./subviews/${name}.js?t=${Date.now()}`
        : `./subviews/${name}.js`),
    esc,
    tag: 'website',
    retryBtnClass: 'btn btn-sm',
});

function _tabIsVisible() {
    // 瀏覽器分頁不在前景 → 停
    if (document.visibilityState === 'hidden') return false;
    // Tab section 被 switchTab 設 hidden（使用者切到 CRM 等別的 tab）→ 停
    const section = document.getElementById('tab_website');
    return !!section && !section.classList.contains('hidden');
}

function _startHealthCheck() {
    const el = document.getElementById('website-api-health');
    if (!el) return;
    const ping = async () => {
        if (!_tabIsVisible()) return;
        try {
            await websiteFetch('/healthz');
            el.textContent = '✓ 連線正常';
            el.style.color = '#4ade80';
        } catch (e) {
            el.textContent = '⚠ 無法連線';
            el.style.color = '#f87171';
            el.title = e.message;
        }
    };
    ping();
    if (_healthTimer) clearInterval(_healthTimer);
    _healthTimer = setInterval(ping, 30000);
}

async function _refreshInquiryBadge() {
    if (!_tabIsVisible()) return;
    const badge = document.getElementById('website-inq-badge');
    if (!badge) return;
    try {
        const data = await websiteFetch('/api/website/admin/inquiries?status=new&limit=1');
        const cnt = data?.total || 0;
        if (cnt > 0) {
            badge.textContent = cnt > 99 ? '99+' : String(cnt);
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    } catch {
        badge.style.display = 'none';
    }
}

function _startBadgeRefresh() {
    _refreshInquiryBadge();
    if (_badgeTimer) clearInterval(_badgeTimer);
    _badgeTimer = setInterval(_refreshInquiryBadge, 60000);
}

export { switchSubview };
