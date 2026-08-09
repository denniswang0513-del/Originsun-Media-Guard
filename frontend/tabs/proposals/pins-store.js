/**
 * pins-store.js — 「重點提案」的狀態與端點，兩個介面共用同一份。
 *
 * 消費者：CRM 專案詳情的「提案企劃」分頁（深色 SPA）與 /proposal-plan.html
 * 的登入模式（官網白底）。兩邊只差 `base` 與怎麼發請求。
 *
 * 🔴 為什麼是 store 而不是各自呼叫端點：勾選框、卡片列、授權開關是三個不同
 * 的元件，卻在講同一件事。之前每個介面各寫一份 pin/unpin/pins-public 的
 * URL 與 method，結果就是同一顆勾選框在兩邊長得不一樣、狀態存在不同地方
 * （一邊模組層變數、一邊寄生在資料夾那一層的 payload 裡，走回上一層還會被
 * 舊快照蓋回去）。端點與狀態收在這裡，兩邊要漂也漂不了。
 *
 * 狀態的生命週期是**一個提案**，不是一層資料夾 —— 所以 store 由呼叫端在
 * 「載入這個提案」時建一顆，不是模組層單例。
 *
 * 用法：
 *   const pins = makePinsStore({ base, request });
 *   pins.watch(() => repaint());         // 立刻畫一次，之後狀態變了才會再叫你
 *   pins.sync(await load(rel));          // 任何帶完整 pins 欄位的回應都可以餵
 */

/**
 * 兩份狀態畫出來會不會不一樣 —— 導覽時每一層回應都帶 pinned，內容沒變就別重畫
 * （重畫＝把 ≤30 個 <img> 全部拆掉重建，看得見地閃一下）。
 *
 * 比的是**面板真的會用到的欄位**（rel / is_dir / thumb_url），不是整包：
 * pinned_at / pinned_by 沒有渲染，拿它們當差異依據只會白重畫。
 */
function _same(a, b) {
    if (a.pins_public !== b.pins_public || a.pinned.length !== b.pinned.length) return false;
    return a.pinned.every((p, i) => p.rel === b.pinned[i].rel
        && !p.is_dir === !b.pinned[i].is_dir
        && (p.thumb_url || '') === (b.pinned[i].thumb_url || ''));
}

/**
 * @param opts.base    端點前綴。⚠️ 兩個呼叫端**不同層**：企劃頁給的是絕對路徑
 *                     `/api/v1/crm/projects/{id}/proposal-assets`（fetch 直接吃），
 *                     CRM 端給的是 crmFetch 的相對路徑 `/projects/{id}/proposal-assets`。
 *                     所以 base 不能拿來組下載網址，只配 request 用。
 *                     這是暫時的：等三個資料夾瀏覽器收斂時一起統一 base 的層級
 *                     （在那之前只有兩處各一行的重複，不值得先猜一個抽象）。
 * @param opts.request async (url, init?) => 已解析的 JSON；錯誤要 throw
 *
 * 註：key 是 project_id，但狀態的真正擁有者是提案（preprod_proposals.pinned_assets）；
 * 兩者的對應由後端 pinned_of() 負責，前端不需要知道。
 *
 * @returns { get, isPinned, toggle, sync, setPublic, watch }
 *
 * 這顆 store 剛好滿足 folder-view 的 `pin` port（{isPinned, toggle}）—— 直接把
 * 它整顆傳過去即可，不需要轉接層。`toggle` 不是為了那個 port 而生的翻譯，
 * 它就是這個領域的操作：「把某一項的重點狀態設成 want」。
 */
export function makePinsStore({ base, request }) {
    let state = Object.freeze({ pinned: Object.freeze([]), pins_public: false });
    const subs = new Set();
    const q = (rel) => '?rel=' + encodeURIComponent(rel || '');

    /**
     * 從任何帶完整 {pinned, pins_public} 的回應更新；回傳原值好接在 await 上。
     *
     * 🔴 兩個欄位**都要在**才吃。只認 pinned 的話，任何帶 pinned 卻沒帶
     * pins_public 的回應會把授權旗標靜靜顯示成「關」—— 畫面與伺服器不一致，
     * 而那正是這個模組最不能顯示錯的一格。
     */
    function sync(d) {
        if (!d || !Array.isArray(d.pinned) || !('pins_public' in d)) return d;
        // 凍結而不是每次 get() 複製一份：狀態是唯讀快照，訂閱者改不動它
        // （淺拷貝擋不住 `get().pinned.push(...)` —— 陣列還是同一個參照）
        const next = Object.freeze({
            pinned: Object.freeze(d.pinned.slice()),
            pins_public: !!d.pins_public,
        });
        if (_same(state, next)) return d;
        state = next;
        subs.forEach(fn => fn(state));
        return d;
    }

    const pin = async (rel, isDir) => sync(await request(base + '/pin',
        { method: 'POST', body: JSON.stringify({ rel, is_dir: !!isDir }) }));
    const unpin = async (rel) => sync(await request(base + '/pin' + q(rel),
        { method: 'DELETE' }));
    // 目前是精確比對。後端的曝光規則是「勾了資料夾 → 整個子樹放行」
    // （core/pinned_assets.allows），所以走進已勾選的資料夾時，裡面的檔案
    // 會顯示未勾選 —— 那是刻意的：勾選框代表「這一項被勾了」，不代表
    // 「這一項對客戶可見」。要改成顯示繼承狀態的話改這裡一處就好。
    const isPinned = (rel) => state.pinned.some(p => p.rel === rel);

    return {
        get: () => state,          // 已凍結，直接給
        isPinned,
        /** 把某一項的重點狀態設成 want。folder-view 的 `pin` port 要的就是這支。 */
        toggle: (rel, isDir, want) => (want ? pin(rel, isDir) : unpin(rel)),
        sync,
        setPublic: async (want) => sync(await request(base + '/pins/public',
            { method: 'POST', body: JSON.stringify({ public: !!want }) })),
        /** 訂閱 + 立刻畫一次。分成兩步的話，忘記補第一次的人會拿到空白面板。 */
        watch(fn) { subs.add(fn); fn(state); return () => subs.delete(fn); },
    };
}
