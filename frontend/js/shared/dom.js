/**
 * dom.js — 最小的 DOM 小工具。**零 import**（含遞迴閉包）。
 *
 * 存在的理由是**重量**：`utils.js` 是 57KB 的大雜燴（而且它靜態拉
 * `clip_utils.js` 再 +8KB，主控端對前端一律 `no-store`，所以每次開頁都重抓）。
 * 只為了 `esc` 與 `ensureStyle` 這 11 行去 import 它，會把 68KB 掛到那個頁面
 * 的路徑上 —— 提案清單就實際踩過這一次（進度格子的渲染件因此被迫改成動態
 * import，deferred 了 68KB 卻沒有省掉）。
 *
 * `utils.js` 繼續 re-export 這兩支，既有的 35 個 importer 一行都不用改。
 * 要加東西進來前先問：它是不是也「零依賴、幾乎每個渲染件都要」？不是的話
 * 它屬於 utils.js。
 */

/**
 * HTML escape。**不依賴任何模組**的那一份 —— 公開頁（訪客、未登入）也
 * import 得起，不會像 tabs/crm/crm-utils.js 那樣把 CRM state 一起拖進來。
 */
export function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * 元件自帶的 stylesheet：同一個 id 只注入一次。
 *
 * ⚠️ **會反覆改寫內容的動態 stylesheet 不適用**（例如 website/subviews/works.js
 * 的欄位顯示切換：它每次都重寫 textContent）。這支第一行就 early-return，
 * 換過去只有第一次會生效，之後靜默失效。
 */
export function ensureStyle(id, css) {
    if (document.getElementById(id)) return;
    const st = document.createElement('style');
    st.id = id;
    st.textContent = css;
    document.head.appendChild(st);
}
