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

import { esc, finEntity, finHasFullParentScope } from './fin-utils.js';
import { createSubviewLoader } from '../../js/shared/subview-loader.js';

let _inited = false;
let _currentSubview = null;   // null = 帳務內嵌模式；否則為子視圖名稱
let _shellAllowed = false;    // 這個身分能不能看帳務殼（合夥人不行）
let _shellReady = null;       // 帳務殼的載入 Promise（只跑一次）

/** 帳務殼（crm-invoices）**用到才載**。
 *
 *  🔴 原本在 initFinanceTab 就 await 載完再 display:none 掉：那一段會打
 *  /invoices-root、/invoices、/projects、/clients、申請人、代辦費六支 API
 *  （/projects 還沒帶篩選，私帳 402 案全撈），再做一次完整 renderList ——
 *  全部落在首次繪製的阻塞路徑上，而預設落地是儀表板、mine 模式下六個帳務
 *  視圖只有兩個看得到（/simplify 2026-08-25）。
 */
function _ensureShell() {
    if (_shellReady) return _shellReady;
    const wrap = document.getElementById('finance-invoices-wrap');
    wrap.innerHTML = _LOADING_HTML;   // 抓 HTML + 六支 API 這段期間不能是一片空白
    _shellReady = (async () => {
        const resp = await fetch('./tabs/crm/crm-invoices.html');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        wrap.innerHTML = await resp.text();
        const mod = await import('../crm/crm-invoices.js');
        await mod.initCrmInvoicesTab();
    })().catch((e) => {
        wrap.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">
            帳務載入失敗：${esc(e.message)}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                    onclick="location.reload()">重新整理頁面</button></div>`;
        _shellReady = null;      // 讓下次點擊可以重試
        throw e;
    });
    return _shellReady;
}

const _LOADING_HTML = '<div style="color:#888;padding:40px;text-align:center;">載入中…</div>';

export async function initFinanceTab() {
    if (_inited) return;   // app.js loadTabs 只呼叫一次；防禦性去重
    _inited = true;

    // ── 兩本帳 nav 三模式（LEDGER_ENTITY_PLAN §5）──
    // full-parent（記帳者/owner）＝全功能；mine（只在 /my-ledger.html 發生，該頁
    // 在 import 本模組前把 window._finEntity 釘成 'mine'）＝隱藏母公司 CRM 域
    // （.fin-nav-full：請款/應付/應收/零用金/現金流預測）；合夥人（finance_partner
    // 唯讀）＝只留 儀表板 + 財務三表，不載帳務殼（其 CRM 讀取端點對合夥人本來
    // 就 403，載了只會畫一排空殼）。
    const mineMode = finEntity() === 'mine';
    const fullParent = finHasFullParentScope();
    const loadShell = mineMode || fullParent;
    const hideNav = (sel) => document.querySelectorAll('#finance-nav ' + sel)
        .forEach((el) => { el.style.display = 'none'; });
    if (mineMode || !fullParent) hideNav('.fin-nav-full');
    if (!mineMode && !fullParent) hideNav('.fin-nav-mine-ok');
    // 私帳專屬子視圖（.fin-nav-mine-only：執行專案/器材清冊/私帳應收），入口
    // 只給帳號上**真的有** finance_mine 的人。🔴 直接看 _modules、不走 hasModule
    // 的 Lv3 bypass —— 後端 grant_admin_all_modules 已把 finance_mine 列為
    // 「指名才有」（管理員不隱含），這裡用同一個口徑，兩邊才不會一邊給看一邊 403。
    if (!((window._modules || []).includes('finance_mine'))) hideNav('.fin-nav-mine-only');

    _shellAllowed = loadShell;
    _bindSideNav();

    // 兩邊互通（owner 2026-08-25）：切回財務分頁時，逐案損益要拿到專案管理
    // 那邊剛改的東西（同一列資料，「同步」=回來時重抓）；從專案管理的
    // 「逐案損益 ↗」跳過來則接棒切子視圖（open 交給 projects.js 的 render 收尾）。
    document.addEventListener('tab-changed', (e) => {
        if (e.detail?.tab !== 'tab_crm_invoices') return;
        const btn = document.querySelector("#finance-nav [data-subview='projects']");
        if (sessionStorage.getItem('omgJumpLedgerProject')) {
            if (btn && btn.offsetParent !== null) btn.click();
            return;
        }
        // 切回財務 tab 一律自動重新整理（owner 2026-08-26）：
        // 執行專案走自己的 refresh（有未存編修讓路的 dirty guard）；其他子視圖
        // 整個重 render（它們是查看型，重畫＝重抓）；帳務內嵌模式按全域重新整理。
        if (_currentSubview === 'projects') window._finProjLedger?.refresh?.();
        else if (_currentSubview) _showSubview(_currentSubview);
        else if (_shellAllowed) document.getElementById('inv-global-refresh')?.click();
    });

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
            if (!_shellAllowed) return;
            _showInvoicesMode();
            _ensureShell().then(() => {
                const inner = document.getElementById(`inv-view-${btn.dataset.invView}`);
                inner?.click();   // 走既有切換邏輯（crm-invoices.js 內含各視圖 lazy-load）
            }).catch(() => {});
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
            // 殼可能還在載 —— 等它好了再按，否則這顆鈕在那段期間按了沒反應
            if (_shellAllowed) {
                _ensureShell().then(() => {
                    document.getElementById('inv-global-refresh')?.click();
                }).catch(() => {});
            }
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
