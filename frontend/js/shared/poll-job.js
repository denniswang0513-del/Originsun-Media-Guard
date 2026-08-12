/**
 * poll-job.js — 追一件**背景工作**到底。
 *
 * 後端有一票「起了就回」的端點（跑 claude 的生成/消化、素材掃描、官網 rebuild、
 * SEO runner…），前端只能靠輪詢知道它好了沒。不追的話畫面停在「生成中…」，
 * 看起來像按了沒反應。這段每次都被重寫，而每次重寫都會漏掉同一件事：
 * **一次抓取失敗不代表工作失敗**（NAS/網路抖一下），不能因此中止輪詢。
 *
 * 收斂條件刻意寬：`settled(item)` 為真，或那一列整個消失，或跑滿 MAX_TICKS。
 *
 * 用法：
 *   pollJob(id, seen, {
 *       alive: () => host.isConnected,          // 畫面拆掉了就別再打 API
 *       list:  () => fetchItems(),              // 重抓整份清單
 *       onSettled: (items) => render(items),    // 拿最新清單重畫
 *   });
 *
 * `seen` 由呼叫端持有（跨重畫存活）：同一顆按兩次不開兩條。**不要**改成模組層
 * 的 Set —— 對話框關掉再開，舊的那條 tick 會把新的剛登記的 id 刪掉。
 */

const TICK_MS = 5000;
const MAX_TICKS = 60;

/** 預設的「做完了」：`status` 不再是 pending。 */
const notPending = (item) => item.status !== 'pending';

// maxTicks / tickMs：預設 5 分鐘（60×5s）夠 claude 類工作；合法跑更久的
// （whisper 轉一小時錄音要幾十分鐘）由呼叫端聲明一次 —— 別在 onSettled 裡
// 遞迴重掛，那會讓這個安全上限看起來有、實際上沒有。慢工也順手放大 tickMs：
// 後端進度字本來就好幾十秒才換一次，5 秒問一次只是白跑 pool 連線。
export function pollJob(id, seen,
                        { alive, list, onSettled, settled = notPending,
                          maxTicks = MAX_TICKS, tickMs = TICK_MS }) {
    if (seen.has(id)) return;
    seen.add(id);
    let left = maxTicks;
    const tick = async () => {
        if (!alive()) { seen.delete(id); return; }
        try {
            const items = await list();
            const cur = items.find(x => x.id === id);
            if (!cur || settled(cur) || --left <= 0) {
                // 先 onSettled 再從 seen 移除：重畫常常會走回「還是 pending
                // 就開始輪詢」的路，早一步移除等於讓上限自動續約（跑滿
                // maxTicks 卻永遠停不下來）。留著 → 那次重掛是 no-op。
                await onSettled(items);
                seen.delete(id);
                return;
            }
        } catch { /* 抖一下不代表失敗 */ }
        setTimeout(tick, tickMs);
    };
    setTimeout(tick, tickMs);
}
