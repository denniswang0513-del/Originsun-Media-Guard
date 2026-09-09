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
 */

/** 典型一輪的秒數（實測 2026-09-09：37–47 秒開始出字、44–52 秒完成） */
export const TYPICAL_SECONDS = [30, 50];

const DOTS = ['', '.', '..', '...'];

/**
 * @param {number} elapsedMs 從按下送出到現在
 * @param {string} stage 後端回的 'queued' / 'running' / ''
 * @returns {string} 要顯示的字（純文字，呼叫端自己跳脫）
 */
export function waitingText(elapsedMs, stage = '') {
    const secs = Math.max(0, Math.floor((elapsedMs || 0) / 1000));
    const dots = DOTS[Math.floor(secs % DOTS.length)];
    if (stage === 'queued') {
        // 真的卡在閘門（前面還有別的 AI 工作）—— 講清楚，不要讓人以為是壞了
        return `排隊中${dots}（前面還有其他 AI 工作）${secs} 秒`;
    }
    const [lo, hi] = TYPICAL_SECONDS;
    // 前 10 秒不吵；超過典型時間才提醒「還在跑，只是比較久」
    const hint = secs < 10 ? '' : (secs > hi ? `　比平常久，還在跑` : `　通常 ${lo}–${hi} 秒`);
    return `處理中${dots} ${secs} 秒${hint}`;
}
