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
export const DECK_EXTS = '.pdf,.ppt,.pptx,.key,.zip';

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
