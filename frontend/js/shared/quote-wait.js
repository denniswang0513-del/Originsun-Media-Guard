/**
 * quote-wait.js — 等 AI 回覆時那句「處理中…」的文字（純函式、零 import 的葉節點）。
 *
 * 為什麼要一支共用的：實測一輪 35–50 秒，其中前 35 秒左右**畫面上什麼都不會動**
 * （claude 的 agent 前置作業佔掉大半，真正吐字很快）—— 靜止太久看起來就像當掉了。
 * 所以這裡給三樣**真實**的東西，不做假進度條：
 *   1. 跳動的點（證明頁面還活著）
 *   2. 已經等了幾秒（證明工作還在跑）
 *   3. 「排隊中」vs「處理中」——後端 `_chat_stage` 給的真狀態
 *      （互動式的閘只有 2，撞到夜間 SEO 批次真的會排隊，那時該講實話）
 *
 * 桌機（crm-quotes.js）與手機（m/views/quote-chat.js）共用這一份。
 *
 * 2026-09-10 起「生成報價單」也借這支（那是 Playwright 開一顆 Chromium 畫一頁，5–15 秒）——
 * 同樣是「按下去之後畫面靜止好幾秒」的形狀，只有典型秒數不一樣，所以開一個參數給呼叫端帶，
 * 不要為了那一句提示語再寫第二套跳動的點。
 */

/** 典型一輪的秒數（實測 2026-09-09：37–47 秒開始出字、44–52 秒完成） */
export const TYPICAL_SECONDS = [30, 50];

/** 「生成報價單」典型秒數（master 開一顆 Chromium 把版面畫成 PDF）。
 *  跟 AI 那一輪不是同一個量級，所以帶進 waitingText 免得它對著 8 秒的工作說「通常 30–50 秒」。
 *  🔴 三個入口（報價分頁／專案頁子頁／手機卡片）各寫一份的話，調過的那一份會靜默走鐘。 */
export const GEN_SECONDS = [5, 15];

/** 畫「生成狀態」那句話。文案正本是**後端**的 `pdf_state.label`
 *  （`core/quote_snapshot.state`）—— 前端不拼中文，兩邊各一份的話改字一定漏掉一邊。
 *  `stale` 才上警示色：那是一件待辦（客戶現在拿到的是舊版），不是單純的資訊。 */
export function paintGenNote(el, st) {
    if (!el) return;
    el.textContent = st ? st.label : '';
    el.classList.toggle('warn', !!(st && st.stale));
}

const DOTS = ['', '.', '..', '...'];

/**
 * @param {number} elapsedMs 從按下送出到現在
 * @param {string} stage 後端回的 'queued' / 'running' / ''
 * @param {number[]} typical [下限, 上限] 秒；預設是 AI 那一輪的量級
 * @returns {string} 要顯示的字（純文字，呼叫端自己跳脫）
 */
export function waitingText(elapsedMs, stage = '', typical = TYPICAL_SECONDS) {
    const secs = Math.max(0, Math.floor((elapsedMs || 0) / 1000));
    const dots = DOTS[Math.floor(secs % DOTS.length)];
    if (stage === 'queued') {
        // 真的卡在閘門（前面還有別的 AI 工作）—— 講清楚，不要讓人以為是壞了
        return `排隊中${dots}（前面還有其他 AI 工作）${secs} 秒`;
    }
    const [lo, hi] = Array.isArray(typical) && typical.length === 2 ? typical : TYPICAL_SECONDS;
    // 前 10 秒不吵；超過典型時間才提醒「還在跑，只是比較久」
    const hint = secs < 10 ? '' : (secs > hi ? `　比平常久，還在跑` : `　通常 ${lo}–${hi} 秒`);
    return `處理中${dots} ${secs} 秒${hint}`;
}
