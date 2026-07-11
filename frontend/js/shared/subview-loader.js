/**
 * subview-loader.js — 子視圖 dynamic import 共用 loader
 * （finance.js / website.js 共用；原本兩檔各一份幾乎相同的 _loadSubviewInto）
 *
 * importer(name, cacheBust) 必須由呼叫端提供 — dynamic import 的相對路徑以
 * 「寫下 import() 的那個模組」為基準，寫在這個共用檔會整體位移到 js/shared/
 * 底下，各 tab 的 ./subviews/ 就解析不到了。
 *
 * cacheBust 為什麼存在：ES module loader 會把「載入失敗的結果」永久快取在
 * module map — server 短暫 down（發布/重啟）後，後續 import() 同一 URL 只會
 * 回傳同一個 rejected promise，使用者只能整頁重整。重試時帶不同 query
 * （?t=<timestamp>）等於換一個 module map key，強制重新 fetch。
 */

const _LOADING_HTML = '<div style="color:#888;padding:40px;text-align:center;">載入中…</div>';

/**
 * @param {object} opts
 *   importer      (name, cacheBust) => Promise<module> — 呼叫端 closure（保住相對路徑）
 *   esc           HTML escape 函式（沿用各 tab 自己的，行為不變）
 *   tag           console 前綴 + 重試鈕 id 前綴（'finance' / 'website'）
 *   retryBtnClass 重試鈕 class（各 tab 樣式系統不同）
 * @returns {(content: HTMLElement, name: string, isCurrent: () => boolean, cacheBust: boolean) => Promise<void>}
 */
export function createSubviewLoader({ importer, esc, tag, retryBtnClass }) {
    async function loadSubviewInto(content, name, isCurrent, cacheBust) {
        try {
            const mod = await importer(name, cacheBust);
            if (!isCurrent()) return;  // 使用者在 import 期間切走了
            if (typeof mod.default === 'function') {
                await mod.default(content, { isCurrent });
            } else {
                content.innerHTML = `<div style="color:#f88;padding:24px;">子視圖 ${name} 缺少 default export</div>`;
            }
        } catch (e) {
            console.error(`[${tag}] load subview '${name}' failed:`, e);
            if (!isCurrent()) return;
            content.innerHTML = `
                <div style="color:#f88;padding:24px;">
                    <div style="margin-bottom:12px;">子視圖載入失敗：${esc(e.message || e)}</div>
                    <button id="${tag}-subview-retry" class="${retryBtnClass}">🔄 重試</button>
                </div>`;
            content.querySelector(`#${tag}-subview-retry`)?.addEventListener('click', () => {
                if (!isCurrent()) return;  // 按鈕還在但已切走 → 不動
                content.innerHTML = _LOADING_HTML;
                loadSubviewInto(content, name, isCurrent, true);
            });
        }
    }
    return loadSubviewInto;
}
