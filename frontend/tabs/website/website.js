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

import { getApiBase, websiteFetch, esc, detectDevMode } from './website-utils.js';
import { initRebuildBar, destroyRebuildBar } from './rebuild-bar.js';

const SUBVIEWS = [
    'dashboard', 'home', 'works', 'categories',
    'services', 'credits', 'about', 'inquiries', 'blog', 'seo', 'awards', 'nav', 'settings',
];

let _activeSubview = 'dashboard';
let _switchToken = 0;          // bump on每次 switch，讓 async 延續能辨識自己是否還在檯面上
let _healthTimer = null;
let _badgeTimer = null;

export async function initWebsiteTab() {
    // dev(8001) 偵測：決定 getApiBase 走同源（本地 dev 資料）還是 NAS（正式）。
    // 必須在任何 websiteFetch 之前完成。
    await detectDevMode();

    // 顯示當前 API base（給使用者除錯參考）
    const apiDisplay = document.getElementById('website-api-base-display');
    if (apiDisplay) apiDisplay.textContent = getApiBase() || '(本地 dev 同源)';

    // 綁左側導覽
    document.querySelectorAll('.website-nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.subview;
            if (view && view !== _activeSubview) switchSubview(view);
        });
    });

    // Rebuild 狀態列（網站 Tab 頂部，跨 subview 共用）
    _initRebuildBarOnce();

    // 初次載入預設子視圖
    await switchSubview('dashboard');

    // 背景任務
    _startHealthCheck();
    _startBadgeRefresh();
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

/**
 * 動態 import 子視圖並 render 進 content。
 *
 * @param cacheBust  true 時在 module URL 後加 ?t=<timestamp>。ES module loader 會
 *   把「載入失敗的結果」永久快取在 module map — server 短暫 down（發布/重啟）後，
 *   後續 import() 同一 URL 只會回傳同一個 rejected promise，使用者只能整頁重整。
 *   重試時帶不同 query 等於換一個 module map key，強制重新 fetch。
 */
async function _loadSubviewInto(content, name, isCurrent, cacheBust) {
    const url = cacheBust
        ? `./subviews/${name}.js?t=${Date.now()}`
        : `./subviews/${name}.js`;
    try {
        const mod = await import(url);
        if (!isCurrent()) return;  // 使用者在 import 期間切走了
        if (typeof mod.default === 'function') {
            await mod.default(content, { isCurrent });
        } else {
            content.innerHTML = `<div style="color:#f88;padding:24px;">子視圖 ${name} 缺少 default export</div>`;
        }
    } catch (e) {
        console.error(`[website] load subview '${name}' failed:`, e);
        if (!isCurrent()) return;
        content.innerHTML = `
            <div style="color:#f88;padding:24px;">
                <div style="margin-bottom:12px;">子視圖載入失敗：${esc(e.message || e)}</div>
                <button id="website-subview-retry" class="btn btn-sm">🔄 重試</button>
            </div>`;
        content.querySelector('#website-subview-retry')?.addEventListener('click', () => {
            if (!isCurrent()) return;  // 按鈕還在但已切走 → 不動
            content.innerHTML = _LOADING_HTML;
            _loadSubviewInto(content, name, isCurrent, true);
        });
    }
}

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
