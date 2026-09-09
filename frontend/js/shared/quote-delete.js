/**
 * quote-delete.js — 刪除報價的確認規則（純函式、零 import 的葉節點）。
 *
 * 為什麼要一支共用的：刪報價**不可復原**，而且已經寄出去的那些還牽著客戶手上的
 * 線上檢視連結 —— `/q/{code}` 的資料列一沒了，客戶那邊就是 404，而你不會知道。
 * 所以「已寄送／已簽核」要比草稿更難刪：要打字確認案名，不是按一下 OK。
 *
 * 桌機（crm-quotes.js）與手機（m/views/quotes.js）共用這一份，兩邊不會漂掉。
 *
 * 🔴 這是**新檔案**：Cloudflare 給 .js 4 小時瀏覽器快取，往既有共用檔加 export
 *    會讓舊分頁的 named import 整個模組載入失敗（reference_cloudflare_js_cache）。
 *    新檔沒有這個問題 —— 舊快取的 js 根本不會引用它。
 */

/** 這張報價「還沒流出去」嗎（草稿才算）。正本狀態表在 core.finance_logic.QUOTE_STATUSES */
const DRAFT = '草稿';

/**
 * @param {object} q 報價（要有 status／project_name／version；share_url 可選）
 * @returns {{strict: boolean, title: string, message: string, expect: string}}
 *   strict=true → 要使用者打字輸入 `expect`（案名）才准刪。
 */
export function deleteConfirmSpec(q) {
    const name = (q && q.project_name) || '（未連專案）';
    const title = `Q-${name}-v${(q && q.version) || 1}`;
    const status = (q && q.status) || DRAFT;
    const strict = status !== DRAFT;

    const lines = [`確定刪除報價「${title}」？`, '刪掉就沒了，無法復原。'];
    if (strict) {
        lines.push('');
        lines.push(`這張的狀態是「${status}」—— 已經給客戶了。`);
        if (q && q.share_url) {
            lines.push('客戶手上的線上檢視連結會立刻失效（他點開只會看到找不到頁面）。');
        }
        lines.push('');
        lines.push(`要繼續請輸入案名：${name}`);
    }
    return { strict, title, message: lines.join('\n'), expect: name };
}

/**
 * 跑一次確認流程。`ask` 預設用瀏覽器的 confirm/prompt，測試可以換掉。
 * @returns {boolean} 使用者是不是真的要刪
 */
export function confirmQuoteDelete(q, ask = {}) {
    const spec = deleteConfirmSpec(q);
    const confirmFn = ask.confirm || ((m) => window.confirm(m));
    const promptFn = ask.prompt || ((m) => window.prompt(m));
    if (!spec.strict) return !!confirmFn(spec.message);
    // 打字確認：空白不計較，但字要對（避免「反正按 OK 就好」的肌肉記憶）
    const typed = promptFn(spec.message);
    return String(typed || '').trim() === spec.expect.trim();
}
