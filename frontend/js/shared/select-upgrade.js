/**
 * select-upgrade.js — 全域下拉統一：把「選項夠多」的原生 <select> 自動升級成
 * 打字搜尋下拉（searchableSelect），涵蓋目前 + 未來動態渲染的所有下拉。
 *
 * 規則：
 *   - 只升級 option 數 ≥ MIN_OPTIONS 的 <select>（短固定選單如 解析度/編碼/是否
 *     保持原生，體驗更快）。
 *   - 跳過 multiple、已升級（data-searchable）、以及手動排除的 data-no-search。
 *   - 用 MutationObserver 監看整份文件：任何 tab 重繪 / modal 開啟 / 表格重建
 *     產生的新下拉都會被補升級；option 是後來才灌進去的也會在灌入那次 mutation
 *     被重新掃到（跨過門檻才升級）。
 *   - 只跑在主 SPA（index.html）。手機 RWD 頁（expense/invoice…）是獨立 HTML，
 *     不載入本模組 → 保持原生選單（手機原生 picker 更好按）。
 *
 * 個別下拉要維持原生：在該 <select> 加 data-no-search 屬性即可。
 */
import { searchableSelect } from '../../tabs/crm/crm-utils.js';

const MIN_OPTIONS = 8;
const SEL = 'select:not([data-searchable]):not([multiple]):not([data-no-search])';

function _sweep() {
    for (const sel of document.querySelectorAll(SEL)) {
        if (sel.options.length >= MIN_OPTIONS) {
            try { searchableSelect(sel, { placeholder: '搜尋…' }); } catch (_) { /* per-select isolation */ }
        }
    }
}

let _pending = false;
function _scheduleSweep() {
    if (_pending) return;
    _pending = true;
    setTimeout(() => { _pending = false; _sweep(); }, 150);
}

export function initSelectAutoUpgrade() {
    if (window._selUpgradeInit) return;
    window._selUpgradeInit = true;

    _sweep();  // upgrade whatever is already in the DOM

    // Catch everything added/populated later (tab HTML injection, modals, tables).
    const obs = new MutationObserver((muts) => {
        for (const m of muts) {
            // A childList mutation whose target is a <select> = options got added;
            // added nodes elsewhere may contain new <select>s. Either way, re-sweep.
            if (m.addedNodes.length || m.target?.tagName === 'SELECT') { _scheduleSweep(); return; }
        }
    });
    obs.observe(document.body, { childList: true, subtree: true });
}
