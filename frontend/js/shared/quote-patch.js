/**
 * quote-patch.js — 把 AI 回的 patch 套到報價草稿上（純函式、零 import 的葉節點）。
 *
 * 規劃正本：docs/QUOTE_ASSISTANT_PLAN.md。後端那一半在 core/quote_chat.py。
 *
 * 🔴 **編號規則是前後端的共同契約**：項目照「大項目一組接一組」攤平後從 1 編號，
 *    patch 的 update.n / remove 用的就是這個編號。後端 core/quote_chat.item_lines()
 *    產生提示時用同一套。送出前一定要先把自動存 flush 掉，讓 DB 的順序＝畫面的順序，
 *    否則 AI 說「改第 3 項」會改到別人。
 *
 * 🔴 **只動 patch 指名的項目**：沒被指名的項目原封不動（使用者可能剛手動改過那格）。
 *    絕不整包覆寫 —— 聊到第 8 輪把第 3 輪手改的單價默默洗掉，是這個功能最容易犯的錯。
 */

/** 攤平：[{name, items[]}] → [{...item, _g: 大項目索引, _i: 組內索引}]（順序＝編號順序） */
export function flattenForPatch(groups) {
    const out = [];
    (groups || []).forEach((g, gi) => {
        (g.items || []).forEach((it, i) => out.push({ ...it, _g: gi, _i: i }));
    });
    return out;
}

const _emptyRow = () => ({
    description: '', unit: '式', quantity: 1, unit_price: 0, internal_cost: 0, note: '',
});

/** 深拷貝一份大項目結構（純函式：不動呼叫端傳進來的陣列） */
function _cloneGroups(groups) {
    return (groups || []).map(g => ({ name: g.name || '', items: (g.items || []).map(it => ({ ...it })) }));
}

const _UPDATABLE = ['group_name', 'description', 'unit', 'quantity', 'unit_price'];

/**
 * @param {Array} groups  目前的大項目結構（不會被改到）
 * @param {Array} terms   目前的備註列（字串陣列，不會被改到）
 * @param {Object} patch  {add:[], update:[{n,...}], remove:[n]} ＋ 可選 terms_add:[]
 * @returns {{groups: Array, terms: Array, applied: {added:number, updated:number, removed:number, terms:number}}}
 */
export function applyQuotePatch(groups, terms, patch) {
    const next = _cloneGroups(groups);
    const flat = flattenForPatch(next);          // 編號 n → flat[n-1]
    const p = patch || {};
    const applied = { added: 0, updated: 0, removed: 0, terms: 0 };

    // ── update：只寫 patch 真的帶了的欄位 ──
    (p.update || []).forEach(u => {
        const ref = flat[(u.n | 0) - 1];
        if (!ref) return;                        // 編號對不上（草稿被改過）就跳過，不亂猜
        const target = next[ref._g]?.items?.[ref._i];
        if (!target) return;
        let touched = false;
        _UPDATABLE.forEach(k => {
            if (u[k] === undefined || u[k] === null) return;
            if (k === 'group_name') { next[ref._g].name = String(u[k]); touched = true; return; }
            target[k] = (k === 'quantity' || k === 'unit_price')
                ? Math.max(parseInt(u[k]) || 0, 0) : String(u[k]);
            touched = true;
        });
        if (touched) applied.updated++;
    });

    // ── remove：由後往前刪，才不會把還沒處理的編號位移掉 ──
    const dead = [...new Set((p.remove || []).map(n => (n | 0) - 1))]
        .filter(i => i >= 0 && i < flat.length)
        .sort((a, b) => b - a);
    dead.forEach(i => {
        const ref = flat[i];
        if (next[ref._g]?.items?.[ref._i]) {
            next[ref._g].items.splice(ref._i, 1);
            applied.removed++;
        }
    });

    // ── add：同名大項目就掛進去，沒有就開一個新的（同 groupQuoteItems 的「同名即同組」） ──
    (p.add || []).forEach(row => {
        const name = String(row.group_name || '').trim();
        let g = next.find(x => (x.name || '').trim() === name);
        if (!g) { g = { name, items: [] }; next.push(g); }
        g.items.push({
            ..._emptyRow(),
            description: String(row.description || ''),
            unit: String(row.unit || '式') || '式',
            quantity: Math.max(parseInt(row.quantity) || 0, 0),
            unit_price: Math.max(parseInt(row.unit_price) || 0, 0),
        });
        applied.added++;
    });

    // 空掉的大項目收掉（remove 把整組刪光時）；一個都不剩就留一列空的給人接著填
    let outGroups = next.filter(g => (g.items || []).length > 0 || (g.name || '').trim());
    if (!outGroups.length) outGroups = [{ name: '', items: [_emptyRow()] }];

    // ── 備註：追加沒有過的（避免每輪重複加同一條） ──
    const outTerms = [...(terms || [])];
    const seen = new Set(outTerms.map(t => String(t).trim()).filter(Boolean));
    (p.terms_add || []).forEach(t => {
        const s = String(t || '').trim();
        if (!s || seen.has(s)) return;
        seen.add(s);
        // 最後那列如果是空的（新增報價預設會有一列空的），填進去而不是另起一列
        const lastIdx = outTerms.length - 1;
        if (lastIdx >= 0 && !String(outTerms[lastIdx]).trim()) outTerms[lastIdx] = s;
        else outTerms.push(s);
        applied.terms++;
    });

    return { groups: outGroups, terms: outTerms, applied };
}
