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

export function pollJob(id, seen, { alive, list, onSettled, settled = notPending }) {
    if (seen.has(id)) return;
    seen.add(id);
    let left = MAX_TICKS;
    const tick = async () => {
        if (!alive()) { seen.delete(id); return; }
        try {
            const items = await list();
            const cur = items.find(x => x.id === id);
            if (!cur || settled(cur) || --left <= 0) {
                seen.delete(id);
                await onSettled(items);
                return;
            }
        } catch { /* 抖一下不代表失敗 */ }
        setTimeout(tick, TICK_MS);
    };
    setTimeout(tick, TICK_MS);
}
