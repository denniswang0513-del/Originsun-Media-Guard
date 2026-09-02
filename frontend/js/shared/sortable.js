/**
 * sortable.js — 清單排序的**唯一比較規則**。
 *
 * 從 tabs/crm/crm-utils.js 抽出來，因為 tabs/proposals 底下的檔案不能靜態
 * import crm-utils（NAS 對外容器只 serve tabs/proposals 與 js/shared，見
 * core/public_assets.py）。抽出來之前提案清單自己寫了第二份比較器，兩份的
 * 空值行為**相反** —— 同一批資料按「客戶」排，後台把沒客戶的排到最後、
 * 企劃頁把它們排到最前面蓋住有資料的列。
 *
 * crm-utils 仍 re-export `enumIndex`（`createSortable` 的正本一直在那邊，
 * 只是改用這裡的 sortRows 實作），既有呼叫端不用改。
 */

/** 工作流順序排序：`['草稿','已送','已簽']` 找 val 的 index，找不到回 arr.length（排尾）。 */
export const enumIndex = (arr, val, fallback) => {
    const i = arr.indexOf(val ?? fallback);
    return i === -1 ? arr.length : i;
};

const _isEmpty = (v) => v === '' || v == null;

// 排序用的 collator 建一次就好 —— `localeCompare(…, 'zh-Hant')` 每次呼叫多數
// 引擎會現建一個 collator，而 4,733 列排一次要比較約 11 萬次。
const _coll = new Intl.Collator('zh-Hant');

/** 排序鍵的正規化：大小寫收在這裡（中文是 no-op，英文混排的欄位才一致），
 *  所以 getter 不用各自背 `.toLowerCase()`。**在 decorate 那一步做**，
 *  每列一次；放進比較器就會變成每次比較一次（n → 2·n·log n）。 */
const _sortKey = (v) => (typeof v === 'number' || _isEmpty(v) ? v : String(v).toLowerCase());

/**
 * 兩個值怎麼比。**空值永遠排尾**（跟方向無關 —— 避免 desc 時一整排空值跑到
 * 頂端把有資料的壓下去）。收到的值已經過 `_sortKey` 正規化。
 */
function compareValues(va, vb, sign) {
    const ae = _isEmpty(va), be = _isEmpty(vb);
    if (ae !== be) return ae ? 1 : -1;
    if (ae) return 0;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign;
    return _coll.compare(String(va), String(vb)) * sign;
}

/**
 * 依 getter 排序（不改原陣列 —— 呼叫端多半還要保留輸入順序）。
 * `getValue` 回 undefined（沒有這個 key 的 getter）＝維持原順序。
 */
export function sortRows(items, getValue, { key, dir } = {}) {
    if (!key) return [...items];
    const sign = dir === 'desc' ? -1 : 1;
    // 🔴 先取值再排（decorate-sort-undecorate）：把 getter 放進比較器裡的話，
    // n 列要呼叫它約 2·n·log n 次 —— 收支明細 4,733 列就是 11 萬次，而其中
    // 有些 getter 是線性搜尋（帳戶名）或字串拼接。取值 n 次就夠了。
    return items
        .map((x) => [_sortKey(getValue(x, key)), x])
        .sort((a, b) => compareValues(a[0], b[0], sign))
        .map((pair) => pair[1]);
}
