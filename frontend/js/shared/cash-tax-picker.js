/**
 * cash-tax-picker.js — 收支分類樹的「一排會長的下拉」（跨 tab 共用）
 *
 * 選到哪一層就再長一格，選到葉就停。私帳的收支明細（篩選器／格內編輯／批次
 * 分類）與對帳單匯入預覽**共用這一份**。
 *
 * 🔴 為什麼抽出來：owner 2026-08-30 要求匯入預覽「比照私帳收支表的模式」。
 * 原本那邊是單一下拉列完整路徑，欄位窄的時候每一項都截成「公司 ▸ 薪水…」，
 * 七個選項長得一模一樣（他的截圖）。而 crm-cashbook 這支的邏輯本來就寫著
 * 「三處共用，各寫一份的話樹一長深只有其中一份會跟上」—— 再抄第四份到財務
 * tab 正好是它警告的那件事。
 *
 * 樹的形狀（後端 core.cash_tree.build_tree）：
 *   [{ id, name, depth, path:[名稱…], children:[…] }, …]
 */

// 逃脫走 js/shared/dom.js 那支（同一個資料夾，它的存在理由就是「輕量
// render 模組不必為了逃脫拖進整個 utils.js」）—— 這裡再寫一份就是樹裡第四份。
import { esc as _esc } from './dom.js';

/** `{id: [根, …, 自己]}` —— 每個節點從根到它的**節點物件鏈**。
 *  編輯與篩選都要問「這一層的上層是誰」，每次現爬會爬很多次。 */
export function indexTax(tree) {
    const byId = {};
    const walk = (nodes, chain) => (nodes || []).forEach((n) => {
        const c = chain.concat([n]);
        byId[n.id] = c;
        walk(n.children, c);
    });
    walk(tree, []);
    return byId;
}

/** 第 i 層的值域＝上一層節點的子節點（第 0 層＝整棵樹的根）。 */
export const taxKidsAt = (tree, chain, i) =>
    (i === 0 ? (tree || []) : ((chain[i - 1] && chain[i - 1].children) || []));

/**
 * 把一排下拉畫進 `box`。
 *
 * o.tree      整棵樹（必填）
 * o.chain     目前選到的節點鏈（[] ＝還沒選）
 * o.start     從第幾層開始畫（格內編輯只畫它那一層以後）
 * o.cls       每個 select 的 class
 * o.style     每個 select 的 inline style
 * o.blank(i)  第 i 格「沒選」時的文字
 * o.extraFirst 第一格額外插的 option（如「（未分類）」快篩）
 * o.custom    最後補一個「＋ 自訂…」
 * o.keepOne   鏈已經到葉也至少留一格（格內編輯：點了那格就是要編它）
 * o.wrap      包一層 flex（收支明細的格內編輯用）
 * o.onPick(i, value)  選了之後由呼叫端決定怎麼記
 * o.searchable(sel, i) 選填：把 select 升級成可搜尋（各 tab 的實作不同）
 */
/**
 * 「這一排下拉選出來的是哪個節點」—— 回 `{ id, cat }`。
 *
 * 兩件事收在這裡：
 *  ① 選「（不細分）」＝**停在上一層**（那一層本身就是有效的分類，家用底下
 *    很多列就停在第二層）。
 *  ② `category` 用**節點自帶的 `cat`**（後端 `core.cash_taxonomy.mirror_from_path`
 *    算的），不在瀏覽器裡自己 `path.slice(0,2).join('_')` 拼第二份鏡射 ——
 *    那條規則的 docstring 寫著「規則只有這一份」，分隔符或層數一改，畫面上的
 *    「科目未對映」提醒就會跟實際存進去的不一致。
 *
 * `byId`＝`indexTax(tree).byId`；`curId`＝目前選中的節點；`level`/`value` 是
 * `taxSelects` 的 `onPick` 給的那兩個。
 */
export function taxPick(byId, curId, level, value) {
    const chain = (byId || {})[curId] || [];
    const node = value ? (byId || {})[value] : null;
    const picked = node ? node[node.length - 1]
        : (level > 0 && chain[level - 1] ? chain[level - 1] : null);
    return { id: picked ? picked.id : '', cat: picked ? (picked.cat || '') : '' };
}


export function taxSelects(box, o) {
    const tree = o.tree || [];
    const chain = o.chain || [];
    const start = o.start || 0;
    const parts = [];
    for (let i = start; i <= chain.length; i++) {
        if (i === chain.length && !taxKidsAt(tree, chain, i).length
            && !(o.keepOne && i === start)) { break; }
        parts.push(`<select class="${o.cls}" data-i="${i}"${
            o.style ? ` style="${o.style}"` : ''}></select>`);
    }
    box.innerHTML = o.wrap
        ? `<span class="cash-cat-edit" style="display:flex;gap:4px;">${parts.join('')}</span>`
        : parts.join('');
    box.querySelectorAll('select[data-i]').forEach((sel) => {
        const i = Number(sel.dataset.i);
        const cur = chain[i];
        sel.innerHTML = `<option value="">${o.blank(i)}</option>`
            + (i === start ? (o.extraFirst || '') : '')
            + taxKidsAt(tree, chain, i).map((n) =>
                `<option value="${_esc(n.id)}"${cur && cur.id === n.id ? ' selected' : ''}>${
                    _esc(n.name)}</option>`).join('')
            + (o.custom ? '<option value="__custom__">＋ 自訂…</option>' : '');
        sel.addEventListener('click', (k) => k.stopPropagation());
        sel.addEventListener('change', () => o.onPick(i, sel.value));
        if (o.searchable) { o.searchable(sel, i); }
    });
}
