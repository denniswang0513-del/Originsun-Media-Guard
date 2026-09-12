// ────────────────────────────────────────────────────────────────────────────
// crm-cashbook-batch.js —— 收支明細的一段：批次分類（勾選幾列一次改分類；owner 2026-09-03）
//
// 2026-09-12 從 crm-cashbook.js（2,844 行，超過單次讀取上限）原樣切出來。主檔的狀態（_entries／_invoiceList…）
// 用 ES module 的 live binding 讀，**這裡不賦值**（賦值只在主檔）；主檔再 import 這裡的函式回去 ——
// 循環 import 只在函式內用到，模組頂層不碰對方的東西。掃原始碼的測試用 _srcscan.cashbook_src()（主檔＋五段串起來）。
// ────────────────────────────────────────────────────────────────────────────
import { esc as _esc, crmFetch as _fetch, crmToast, searchableSelect } from './crm-utils.js';
import { _CATEGORIES, _batch, _entries, _grossOut, _shown, _taxKidsAt, _taxSelects, _taxTree, closeDetail, renderList } from './crm-cashbook.js';

// ── 批次分類 ──────────────────────────────────────────────────

/** 批次模式時點列＝選取；平常點列不做事（詳情走最右邊的編輯鈕）。
 *  Shift＋點＝從上一次點的那列選到這列（照畫面順序）。 */
window._cashRowClick = (ev, id) => {
    if (!_batch.on) { return; }
    const order = _shown.map((e) => e.id);
    if (ev && ev.shiftKey && _batch.last && order.includes(_batch.last)) {
        const a = order.indexOf(_batch.last), b = order.indexOf(id);
        const [lo, hi] = a < b ? [a, b] : [b, a];
        // 範圍一律**加選**（不是 toggle）—— toggle 會把中間已選的取消掉，
        // 那是使用者最不想要的結果
        for (let i = lo; i <= hi; i++) { _batch.sel.add(order[i]); }
    } else if (_batch.sel.has(id)) {
        _batch.sel.delete(id);
    } else {
        _batch.sel.add(id);
    }
    _batch.last = id;
    _batchPaint();
};

/** 只把選取狀態刷到**已經畫出來**的列上 —— 不重建 DOM。
 *  （整表重畫要 ~680ms，而且分批繪製時重畫會把捲軸拉回頂端。） */
export function _batchPaint() {
    document.querySelectorAll('#cash-list-body .crm-row[data-id]').forEach((el) => {
        el.classList.toggle('batch-picked', _batch.sel.has(el.dataset.id));
    });
    _batchRefreshBar();
}

export function _batchRefreshBar() {
    const bar = document.getElementById('cash-batch-bar');
    if (!bar) { return; }
    bar.style.display = _batch.on ? 'flex' : 'none';
    const el = document.getElementById('cash-batch-count');
    if (!el) { return; }
    el.innerHTML = _batch.sel.size
        ? `已選 <b style="color:#eee;">${_batch.sel.size}</b> 筆`
        : `<span style="color:#6b7280;">點列選取，按住 Shift 選一整段（目前篩出 ${_shown.length} 筆）</span>`;
}

/** 批次列的分類下拉 —— 與篩選器、格內編輯同一個生成器。
 *  🔴 母公司那本**沒有分類樹**（種子只種私帳），類別是平的一層 ——
 *  那邊退回一顆 `_CATEGORIES` 的下拉。不退的話那本按下批次分類會看到一個
 *  空下拉，等於這顆按鈕在公司帳上是壞的。 */
export function _batchTaxDraw() {
    const box = document.getElementById('cash-batch-tax');
    if (!box) { return; }
    if (!_taxTree.length) {
        box.innerHTML = '<select id="cash-batch-cat" class="crm-select" style="min-width:160px;">'
            + '<option value="">要套哪個類別…</option>'
            + _CATEGORIES.map((c) => `<option value="${_esc(c)}">${_esc(c)}</option>`).join('')
            + '</select>';
        searchableSelect(box.querySelector('select'), { placeholder: '搜尋類別…' });
        return;
    }
    _taxSelects(box, {
        chain: _batch.chain, keepOne: true,
        cls: 'crm-select cash-batch-sel', style: 'min-width:120px;',
        blank: (i) => (i === 0 ? '要套哪個分類…' : '（不再細分）'),
        onPick: (i, v) => {
            _batch.chain = _batch.chain.slice(0, i);
            const n = _taxKidsAt(_batch.chain, i).find((x) => x.id === v);
            if (n) { _batch.chain.push(n); }
            _batchTaxDraw();
        },
    });
}

export function _batchSetMode(on) {
    _batch.on = on;
    _batch.sel.clear();
    _batch.last = null;
    const btn = document.getElementById('cash-btn-batch');
    if (btn) {
        btn.textContent = on ? '離開批次' : '批次分類';
        btn.classList.toggle('crm-btn-primary', on);
        btn.classList.toggle('crm-btn-secondary', !on);
    }
    if (on) { closeDetail(); _batchTaxDraw(); }
    renderList();                     // 底色與 selected 狀態都要跟著換
    _batchRefreshBar();
}

/** 目前挑到的分類：`{label, body}` —— body 直接就是要送的欄位
 *  （有樹送 taxonomy_node_id、平的那本送 category）。 */
export function _batchPick() {
    if (!_taxTree.length) {
        const v = document.getElementById('cash-batch-cat')?.value || '';
        return { label: v, body: { category: v } };
    }
    const node = _batch.chain[_batch.chain.length - 1];
    return { label: node ? node.name : '', body: { taxonomy_node_id: node ? node.id : '' } };
}

/** 套用（label 為空＝把選取的這幾筆的分類清掉）。 */
export async function _batchApply(pick) {
    const ids = [..._batch.sel];
    if (!ids.length) { crmToast('還沒選任何一筆'); return; }
    try {
        const r = await _fetch('/cash-entries/batch-taxonomy', {
            method: 'PATCH',
            body: JSON.stringify({ entry_ids: ids, ...pick.body }),
        });
        // 整批同一個分類 → 鏡射也只有一份，就地套到那幾列（不重載 4,700 列）
        _entries.forEach((e) => { if (_batch.sel.has(e.id)) { Object.assign(e, r.entry); } });
        crmToast(pick.label
            ? `${r.updated} 筆已分類到「${pick.label}」`
            : `${r.updated} 筆的分類已清掉`);
        _batch.sel.clear();
        _batch.last = null;
        renderList();
        _batchRefreshBar();
    } catch (e) {
        // 後端是**整批擋下**並說明原因（鎖月、掛了專案的類別不合）—— 原話顯示，
        // 吞成「操作失敗」的話使用者不知道要取消勾選哪幾筆
        crmToast(e.message || '批次分類失敗', 8000);
    }
}

/** 刷卡金額：status='card' 的列（刷卡當下不動銀行，所以不算銀行支出）。 */
export function _cardAmt(e) {
    return e.status === 'card' ? _grossOut(e) : 0;
}

/** 銀行支出：卡費列不算（那筆錢還在卡上，月底繳款才真的離開帳戶）。 */
export function _bankOut(e) {
    return e.status === 'card' ? 0 : _grossOut(e);
}
