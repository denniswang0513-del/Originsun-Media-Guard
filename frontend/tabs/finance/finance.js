/**
 * finance.js — 💰 財務管理 Tab（階段二：帳務代理 + 子視圖雙模式）
 *
 * 架構：
 * - data-inv-view 按鈕 = 代理內嵌 crm-invoices 殼的 #inv-view-* 切換（階段一原樣保留，
 *   殼包在 #finance-invoices-wrap 內以便 display 切換）。
 * - data-subview 按鈕 = 真子視圖，比照官網管理 website.js 的
 *   dynamic import('./subviews/<name>.js') → mod.default(container, { isCurrent })
 *   模式（含載入失敗 retry + cache-bust — ES module map 會把 rejected import 永久
 *   快取，重試必須換 query 才會重新 fetch）。
 * - 兩模式互斥切換：點 inv-view → 顯示帳務 wrapper、隱藏子視圖容器；點 subview 反向。
 * - 決策註記：帳務六視圖維持 data-inv-view 代理為長期設計（crm-invoices 殼整組重用、
 *   行為等價優先）；除非帳務視圖天然重寫，不做真子視圖化拆殼。
 */

import { esc } from './fin-utils.js';
import { createSubviewLoader } from '../../js/shared/subview-loader.js';

let _inited = false;
let _currentSubview = null;   // null = 帳務內嵌模式；否則為子視圖名稱

const _LOADING_HTML = '<div style="color:#888;padding:40px;text-align:center;">載入中…</div>';

export async function initFinanceTab() {
    if (_inited) return;   // app.js loadTabs 只呼叫一次；防禦性去重
    _inited = true;

    const wrap = document.getElementById('finance-invoices-wrap');
    try {
        // 1. 載入既有帳務殼（含發票視圖 + 其餘五視圖的內部 lazy-load 容器）
        const resp = await fetch('./tabs/crm/crm-invoices.html');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        wrap.innerHTML = await resp.text();

        // 2. 啟動既有帳務邏輯（六視圖切換/CSV 匯入/月結卡全部沿用）
        const mod = await import('../crm/crm-invoices.js');
        await mod.initCrmInvoicesTab();
    } catch (e) {
        wrap.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">
            帳務載入失敗：${esc(e.message)}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                    onclick="location.reload()">重新整理頁面</button></div>`;
        _inited = false;
        return;
    }

    _bindSideNav();

    // 預設落地 = 📊 儀表板子視圖（帳務殼已初始化但隱藏，點帳務按鈕仍可切回）。
    // finance.html 已把 nav active 標在儀表板按鈕上，故此處不需再改 active class。
    _showSubview('dashboard');
}

// 左側導覽：data-inv-view → 代理內部 view bar；data-subview → lazy-load 子視圖
function _bindSideNav() {
    const nav = document.getElementById('finance-nav');
    if (!nav) return;
    nav.addEventListener('click', (e) => {
        const btn = e.target.closest('.finance-nav-btn');
        if (!btn) return;
        if (btn.dataset.invView) {
            const inner = document.getElementById(`inv-view-${btn.dataset.invView}`);
            if (!inner) return;
            _showInvoicesMode();
            inner.click();   // 走既有切換邏輯（crm-invoices.js 內含各視圖 lazy-load）
        } else if (btn.dataset.subview) {
            _showSubview(btn.dataset.subview);
        } else {
            return;
        }
        nav.querySelectorAll('.finance-nav-btn').forEach((b) => b.classList.toggle('active', b === btn));
    });
    const refresh = document.getElementById('finance-refresh');
    if (refresh) {
        refresh.addEventListener('click', () => {
            if (_currentSubview) {   // 子視圖模式 → 重新 render 目前子視圖
                _showSubview(_currentSubview);
                return;
            }
            const inner = document.getElementById('inv-global-refresh');
            if (inner) inner.click();
        });
    }
}

function _showInvoicesMode() {
    _currentSubview = null;
    const sub = document.getElementById('finance-subview');
    const wrap = document.getElementById('finance-invoices-wrap');
    if (sub) sub.style.display = 'none';
    if (wrap) wrap.style.display = '';
}

function _showSubview(name) {
    _currentSubview = name;
    const wrap = document.getElementById('finance-invoices-wrap');
    const content = document.getElementById('finance-subview');
    if (!content) return;
    if (wrap) wrap.style.display = 'none';
    content.style.display = '';
    content.innerHTML = _LOADING_HTML;
    const isCurrent = () => _currentSubview === name;
    _loadSubviewInto(content, name, isCurrent, false);
}

// 共用 loader（含 retry/cache-bust/isCurrent 護欄，見 js/shared/subview-loader.js）；
// importer closure 留在本檔，./subviews/ 相對路徑才會以 finance/ 為基準。
const _loadSubviewInto = createSubviewLoader({
    importer: (name, cacheBust) => import(cacheBust
        ? `./subviews/${name}.js?t=${Date.now()}`
        : `./subviews/${name}.js`),
    esc,
    tag: 'finance',
    retryBtnClass: 'crm-btn crm-btn-secondary crm-btn-sm',
});
