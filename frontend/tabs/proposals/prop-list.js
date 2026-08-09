/**
 * prop-list.js — 提案清單的查詢、排序、統計（後台提案庫與獨立企劃頁共用）。
 *
 * 抽的是**邏輯不是版面**。兩份渲染要留著，理由不是欄位不同（現在幾乎一樣），
 * 而是**互動語意不同**：後台點一列開 overlay（就地看），企劃頁點一列換頁
 * （導航，所以那邊的列是 `<a href>`）。視覺語言也不同（深色後台 vs 官網白底）。
 * 但下面這幾件事一旦兩份就會漂：
 *   - 篩選狀態 → query string 的對映（少一個 key 就少一個篩選）
 *   - 「狀態」要照**流程順序**排（草稿→已提案→…），不是字串序
 *   - 年份下拉的選項＝既有資料 pitch_date 的 distinct 年
 *   - 成案率的分母（後端算，但 hover 細目的排版在前端）
 *
 * 靜態相依刻意只有 prop-const（列舉）、prop-fetch（tfetch）、js/shared/sortable
 * （比較規則），合計約 12KB。**不要**為了一個小工具去 import js/shared/utils.js
 * ——那是 53KB，會落在清單的關鍵路徑上。（utils.js 仍在遞迴閉包裡，但那是
 * prop-fetch 為了警告 toast 與 authDownload 走的**動態** import，不擋首屏。）
 * 清單只有登入模式看得到（客戶拿到的是單一提案的 ?t= 連結）。
 *
 * 🔴 這是**提案清單**的共用件，不是清單框架。設備/場地那些頁也有同構的
 * 「篩選 + 全集 + 選項同步」，不要併進來 —— 資料與 key 都不同，併進來的
 * 第一天就會開始長 options.keys / options.yearField。
 */

import { enumIndex, sortRows } from '../../js/shared/sortable.js';
import { API, STATUSES } from './prop-const.js';
import { tfetch } from './prop-fetch.js';

export const hasFilters = (f) => Object.values(f || {}).some(Boolean);

/** 篩選 → query string（含 `?`；沒有任何條件時回空字串）。 */
function listQuery(filters) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(filters || {})) if (v) p.set(k, v);
    const qs = p.toString();
    return qs ? '?' + qs : '';
}

export async function fetchProposals(filters) {
    return (await tfetch(API + listQuery(filters))).proposals || [];
}

/**
 * 可排序的欄位：**一份清單**同時餵後台的表頭與企劃頁的排序下拉。
 * 分兩份的話，加一欄要改三個地方（getter、表頭、下拉），而漏改不會報錯。
 *
 * **狀態照流程順序**（草稿→已提案→…→擱置），不是字串序 —— 按狀態排是為了
 * 看管線進度。清單外的舊值走 enumIndex 排到最後。
 */
export const SORT_COLUMNS = [
    { key: 'title', label: '標題', get: p => p.title || '' },
    { key: 'client', label: '客戶', get: p => p.client_name || '' },
    // 這一欄同時是「換綁專案」的入口（格子裡放的是按鈕，見各介面的列渲染）
    { key: 'project', label: '專案', get: p => p.project_name || '' },
    { key: 'ptype', label: '類型', get: p => p.ptype || '' },
    { key: 'status', label: '狀態', get: p => enumIndex(STATUSES, p.status) },
    { key: 'pitch', label: '提案日', get: p => p.pitch_date || '' },
    { key: 'budget', label: '預算範圍', get: p => p.budget_range || '' },
    { key: 'refs', label: '參考', get: p => p.refs_count ?? 0 },
];

export const SORT_GETTERS = Object.fromEntries(SORT_COLUMNS.map(c => [c.key, c.get]));

/**
 * 依 key/dir 排序（不改原陣列）。比較規則在 js/shared/sortable.js ——
 * 空值排尾、大小寫正規化，與後台的點欄頭排序**同一份**。
 *
 * ⚠️ 這是前端排序，看得到的是全集。哪天後端 GET /proposals 加了分頁，
 * 這裡會靜默變成「只排本頁」—— 到時要一起改成後端排序。
 */
export const sortProposals = (rows, key, dir) =>
    sortRows(rows, (p, k) => SORT_GETTERS[k]?.(p), { key, dir });

/**
 * 填年份下拉：選項＝既有資料 pitch_date 的 distinct 年（新到舊），
 * 並**保留目前選取**（重整清單不該把使用者選的年份洗掉）。
 */
export function fillYearSelect(sel, rows) {
    if (!sel) return;
    const cur = sel.value;
    const years = [...new Set((rows || []).map(p => (p.pitch_date || '').slice(0, 4))
        .filter(Boolean))].sort().reverse();
    // 用 new Option 不是 innerHTML —— 年份不必逃逸（見檔頭的相依說明）
    sel.replaceChildren(new Option('全部年份', ''), ...years.map(y => new Option(y, y)));
    if (years.includes(cur)) sel.value = cur;
}

/**
 * 綁篩選控件（收元素不收 id —— 兩個介面的 id 不一樣）。
 * 搜尋 debounce 300ms：每打一個字打一次 API 太吵。
 * @returns 讀出目前篩選的函式
 */
export function wireFilters(els, onChange) {
    let timer = null;
    if (els.q) els.q.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(onChange, 300);
    });
    for (const k of ['status', 'ptype', 'year']) {
        if (els[k]) els[k].addEventListener('change', onChange);
    }
    return () => ({
        q: (els.q?.value || '').trim(),
        status: els.status?.value || '',
        ptype: els.ptype?.value || '',
        year: els.year?.value || '',
    });
}

/**
 * 抓統計並塗進兩個 chip。兩個介面的 chip 內部契約已經一樣（`.n` / `.l` / title），
 * 只有外框的 CSS 各畫各的 —— 所以「填哪裡」可以共用，「長什麼樣」不共用。
 *
 * 與 fetchProposals 相反，這支**不往上丟錯**：統計失敗不該擋住清單。
 *
 * 失敗就什麼都不做：統計不該擋住清單，chip 停在上一個值（或初始的 `–`）。
 */
export async function paintStats(totalEl, rateEl) {
    // 整支包起來：不只 fetch 會失敗，chip 的結構被改動時 querySelector 也會回
    // null —— 統計不該擋清單，更不該變成一條 unhandled rejection
    try {
        const s = await tfetch(`${API}/stats`);
        const ov = s.overall || { total: 0, won: 0, rate: 0 };
        const lines = (items, head) =>
            [head, ...(items || []).map(b => `　${b.key}：${b.won}/${b.total}（${b.rate}%）`)];
        totalEl.querySelector('.n').textContent = s.total_all ?? 0;
        totalEl.title = '含草稿/擱置的全部提案數；成案率分母只算已提案/入圍/成案/未成案';
        rateEl.querySelector('.n').textContent = `${ov.rate}%`;
        rateEl.querySelector('.l').textContent = `成案率（${ov.won}/${ov.total}）`;
        rateEl.title = [...lines(s.by_type, '── by 類型 ──'),
                        ...lines(s.by_year, '── by 年度 ──')].join('\n');
    } catch { /* chip 停在上一個值 */ }
}
