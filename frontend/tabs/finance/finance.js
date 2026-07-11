/**
 * finance.js — 💰 財務管理 Tab（階段一：帳務六視圖搬家，零新功能）
 *
 * 架構：內嵌既有 crm-invoices 殼（一次載入、DOM 常駐 — 其六視圖與內部 lazy-load
 * 一行不改），左側導覽按鈕「代理」內部 view bar 的 #inv-view-* 按鈕（bar 以 CSS
 * 隱藏但功能保留 — 等價優先、回退安全）。
 *
 * 之後階段的新子視圖（🏦 銀行貸款 / 📑 財務三表 / 📊 儀表板 / ⚙️ 設定）比照
 * 官網管理 website.js 的 data-subview + ./subviews/<name>.js lazy-load 模式加入，
 * 與帳務內嵌並存（data-inv-view = 代理帳務、data-subview = 真子視圖）。
 */

let _inited = false;

export async function initFinanceTab() {
    if (_inited) return;   // app.js loadTabs 只呼叫一次；防禦性去重
    _inited = true;

    const content = document.getElementById('finance-content');
    try {
        // 1. 載入既有帳務殼（含發票視圖 + 其餘五視圖的內部 lazy-load 容器）
        const resp = await fetch('./tabs/crm/crm-invoices.html');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        content.innerHTML = await resp.text();

        // 2. 啟動既有帳務邏輯（六視圖切換/CSV 匯入/月結卡全部沿用）
        const mod = await import('../crm/crm-invoices.js');
        await mod.initCrmInvoicesTab();
    } catch (e) {
        content.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">
            帳務載入失敗：${e.message}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                    onclick="location.reload()">重新整理頁面</button></div>`;
        _inited = false;
        return;
    }

    _bindSideNav();
}

// 左側導覽 → 代理內部 view bar（#inv-view-<name>）；active 高亮跟著走
function _bindSideNav() {
    const nav = document.getElementById('finance-nav');
    if (!nav) return;
    nav.addEventListener('click', (e) => {
        const btn = e.target.closest('.finance-nav-btn[data-inv-view]');
        if (!btn) return;
        const inner = document.getElementById(`inv-view-${btn.dataset.invView}`);
        if (!inner) return;
        inner.click();   // 走既有切換邏輯（crm-invoices.js 內含各視圖 lazy-load）
        nav.querySelectorAll('.finance-nav-btn').forEach((b) => b.classList.toggle('active', b === btn));
    });
    const refresh = document.getElementById('finance-refresh');
    if (refresh) {
        refresh.addEventListener('click', () => {
            const inner = document.getElementById('inv-global-refresh');
            if (inner) inner.click();
        });
    }
}
