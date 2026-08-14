/**
 * prop-const.js — 提案的列舉與小工具。**零 import**。
 *
 * 跟 prop-actions 分家的理由是依賴輪廓不同：動作層要 prop-fetch（會再拉進
 * 警告浮出的動態 import），常數什麼都不要。客戶公開頁只為了畫一個「類型」
 * 下拉就把整條 fetcher 鏈拉下來並不划算 —— 那條路走的是免登入的慢隧道。
 *
 * 🔴 這裡是 **UI 鏡像**，不是權威。狀態機的正本在
 * `routers/api_proposals.py`（那裡也自稱正本）—— 後端加狀態時要記得同步這裡，
 * 反過來只改這裡不會有任何效果。
 */

export const API = '/api/v1/proposals';
export const STATUSES = ['草稿', '已提案', '入圍', '成案', '未成案', '擱置'];
/** 還沒定案的那幾個（CRM 專案列的提案帶用來判斷「這案還在談」）。 */
export const PRESALE_STATUSES = ['草稿', '已提案', '入圍', '擱置'];
export const PTYPES = ['形象', '廣告', '紀錄片', '政府標案', '社群', '其他'];
/**
 * 上傳簡報時要不要限制副檔名。**空字串＝不設 `accept`**（檔案選擇器不過濾）。
 *
 * 2026-08-11 起簡報不再只有 pdf/ppt 那五種 —— 報價 xlsx、腳本 docx 也是要給
 * 客戶看的那一份，後端改成擋可執行檔的黑名單。`accept` 是 allowlist，只要比
 * 後端窄，使用者就會在選檔視窗裡**選不到**後端其實收得下的檔（而且沒有任何
 * 錯誤訊息可看）。窄的那一邊留給後端擋就好。
 */
export const DECK_EXTS = '';

/**
 * 五軌的顏色。沿用四段進度條的既有色彙（備份藍/轉檔橘/串接綠/報表紫）——
 * 同事已經認得這套顏色語言，不另外發明一組。收割用灰藍（PARA 的 Resources，
 * 非主線）。
 *
 * 住在這裡是因為**兩處都要用**：詳情的五軌燈號（flow-view）與清單的微型完成條
 * （flow-badge）。同一條軌在兩個畫面不同色的話，「掃一眼看哪軌卡住」就廢了。
 * 鍵＝`core.project_flow.TRACKS` 的軌道 key。
 */
export const TRACK_COLOR = {
    plan: '#1f538d', prod: '#d48a04', biz: '#228b22',
    deliver: '#7c3aed', harvest: '#546e7a',
};

/**
 * 下拉選項：清單 + 現值。舊資料/CSV 匯入的自由文字不在清單裡，不補進去的話
 * `<select>` 會靜默把它換成第一個選項 —— 使用者只是點開看一眼，值就沒了。
 */
export function withCurrent(list, cur) {
    return !cur || list.includes(cur) ? list : [cur, ...list];
}

/**
 * 清單「專案」欄要顯示什麼。回的是原始字串，兩個介面各自用自己的 esc 逸出。
 *
 * 三種情況：
 *   未連結        → 行動呼籲（空白的話沒人知道那一格可以按）
 *   同名          → 「同名專案」。提案誕生會自動建殼專案、名字就用提案標題，
 *                   照實顯示等於同一句話在一列裡出現兩次。
 *   名字不一樣    → 專案名（那才是有資訊量的情況：掛到別的案子上了）
 * 完整名稱一律留在 title 屬性裡，滑過去看得到。
 */
export const projectLabel = (p) => {
    if (!p.project_id) return '＋ 連結';
    const name = p.project_name || '';
    if (!name) return p.project_id;
    return name === (p.title || '') ? '同名專案' : name;
};

/** 片庫裡還沒掛到這個提案的（掛載挑選器用）。id 型別不保證一致 → 一律轉字串比。 */
export function pickableRefs(linkedRefs, library) {
    const linked = new Set((linkedRefs || []).map(r => String(r.id)));
    return (library || []).filter(r => !linked.has(String(r.id)));
}
