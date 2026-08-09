/**
 * autosave.js — 輸入欄自動儲存的共用機制
 * ---
 * 這套「停手 800ms 或離開欄位就送、沒改過就不送」的手感在專案裡已經被抄了好幾份
 * （企劃矩陣、參考影片研究格、截圖說明、CRM 專案的引用備註…），而且開始分岔：
 * 有的有 change flush、有的沒有；有的會 dirty-check、有的每次 blur 都打 API。
 * 這裡收成一份，行為只有一個答案。
 *
 * 用法：
 *   const save = autosave(el, async (value, el) => { await api(...); }, {
 *       onOk: () => say('已儲存 ✓'),
 *       onError: (err) => say('儲存失敗：' + err.message, true),
 *   });
 *   save.flush();     // 需要時手動催（例如關閉前）
 *   save.dispose();   // 取消待送的 timer（重畫前呼叫，避免對已卸下的節點送舊值）
 *
 * send 拋錯 → onError；dirty 基準退回去（下次還會再試，除非期間又改過）。
 * 委派版（節點會被重畫的清單）用 autosaveDelegated(host, selector, send, opts)。
 */

const DEBOUNCE_MS = 800;

/** 綁單一輸入元素。回 { flush, dispose }。 */
export function autosave(el, send, opts = {}) {
    const ms = opts.ms ?? DEBOUNCE_MS;
    let timer = null;
    if (el._asSaved === undefined) el._asSaved = el.value;

    const save = async () => {
        clearTimeout(timer);
        if (el.value === el._asSaved) return;      // dirty-check：沒改過不打 API
        const want = el.value, prev = el._asSaved;
        el._asSaved = want;                        // 基準**先**進：blur 與手動 flush
        try {                                      // 會前後腳來（點鈕＝blur 再 click），
            await send(want, el);                  // 晚進的話同一份內容會送兩次
            opts.onOk?.(el);
        } catch (err) {
            if (el._asSaved === want) el._asSaved = prev;   // 期間沒再改過才退回去重試
            opts.onError?.(err, el);
        }
    };
    const queue = () => { clearTimeout(timer); timer = setTimeout(save, ms); };

    el.addEventListener('input', queue);
    el.addEventListener('change', save);           // date / select / datalist 點選
    el.addEventListener('blur', save);
    return { flush: save, dispose: () => clearTimeout(timer) };
}

/**
 * 委派版：host 底下符合 selector 的輸入欄都自動儲存 —— 清單重畫換新節點也不用重綁
 * （逐顆綁在重畫後會漏，而重畫時重綁又會疊加監聽器；兩個坑都踩過）。
 * 重畫後記得把新節點的 `_asSaved` 設成當下值（`syncBaseline(host, selector)`）。
 */
export function autosaveDelegated(host, selector, send, opts = {}) {
    const ms = opts.ms ?? DEBOUNCE_MS;
    const timers = new Map();

    const save = async (el) => {
        clearTimeout(timers.get(el));
        if (el.value === el._asSaved) return;
        const want = el.value, prev = el._asSaved;
        el._asSaved = want;                        // 同上：基準先進，避免重複送
        try {
            await send(want, el);
            opts.onOk?.(el);
        } catch (err) {
            if (el._asSaved === want) el._asSaved = prev;
            opts.onError?.(err, el);
        }
    };
    host.addEventListener('input', (e) => {
        const el = e.target.closest(selector);
        if (!el) return;
        if (el._asSaved === undefined) el._asSaved = '';
        clearTimeout(timers.get(el));
        timers.set(el, setTimeout(() => save(el), ms));
    });
    host.addEventListener('focusout', (e) => {
        const el = e.target.closest(selector);
        if (el) save(el);
    });
    return { dispose: () => { timers.forEach(clearTimeout); timers.clear(); } };
}

/** 重畫後把 dirty-check 基準對齊當下值（否則第一次編輯可能被誤判為「沒改」）。 */
export function syncBaseline(host, selector) {
    host.querySelectorAll(selector).forEach(el => { el._asSaved = el.value; });
}
