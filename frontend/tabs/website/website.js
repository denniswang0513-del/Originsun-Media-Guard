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

import { getApiBase, websiteFetch } from './website-utils.js';

const SUBVIEWS = [
    'dashboard', 'home', 'works', 'categories',
    'services', 'about', 'inquiries', 'blog', 'settings',
];

let _activeSubview = 'dashboard';
let _healthTimer = null;
let _badgeTimer = null;

export async function initWebsiteTab() {
    // 顯示當前 API base（給使用者除錯參考）
    const apiDisplay = document.getElementById('website-api-base-display');
    if (apiDisplay) apiDisplay.textContent = getApiBase();

    // 綁左側導覽
    document.querySelectorAll('.website-nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.subview;
            if (view && view !== _activeSubview) switchSubview(view);
        });
    });

    // 初次載入預設子視圖
    await switchSubview('dashboard');

    // 背景任務
    _startHealthCheck();
    _startBadgeRefresh();
}

window.initWebsiteTab = initWebsiteTab;

async function switchSubview(name) {
    if (!SUBVIEWS.includes(name)) return;
    _activeSubview = name;

    document.querySelectorAll('.website-nav-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.subview === name)
    );

    const content = document.getElementById('website-content');
    if (!content) return;
    content.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">載入中…</div>';

    try {
        const mod = await import(`./subviews/${name}.js`);
        if (typeof mod.default === 'function') {
            await mod.default(content);
        } else {
            content.innerHTML = `<div style="color:#f88;padding:24px;">子視圖 ${name} 缺少 default export</div>`;
        }
    } catch (e) {
        console.error(`[website] load subview '${name}' failed:`, e);
        content.innerHTML = `<div style="color:#f88;padding:24px;">子視圖載入失敗：${e.message}</div>`;
    }
}
window.websiteSwitchSubview = switchSubview;

function _startHealthCheck() {
    const el = document.getElementById('website-api-health');
    if (!el) return;
    const ping = async () => {
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
