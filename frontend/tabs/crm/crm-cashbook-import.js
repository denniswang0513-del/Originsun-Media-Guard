// ────────────────────────────────────────────────────────────────────────────
// crm-cashbook-import.js —— 收支明細的一段：CSV 匯入、對帳／匯入面板（recon.js 懶載）、欄位選擇（哪些欄要出現）
//
// 2026-09-12 從 crm-cashbook.js（2,844 行，超過單次讀取上限）原樣切出來。主檔的狀態（_entries／_invoiceList…）
// 用 ES module 的 live binding 讀，**這裡不賦值**（賦值只在主檔）；主檔再 import 這裡的函式回去 ——
// 循環 import 只在函式內用到，模組頂層不碰對方的東西。掃原始碼的測試用 _srcscan.cashbook_src()（主檔＋五段串起來）。
// ────────────────────────────────────────────────────────────────────────────
import { esc as _esc } from './crm-utils.js';
import { ledgerHasInvoices } from '../finance/fin-utils.js';
import { closeDetail, loadEntries } from './crm-cashbook.js';

let _csvFile = null;
// ── CSV Import ──────────────────────────────────────────────

// ── 對帳／匯入面板 ───────────────────────────────────────────
// recon.js 有 1,500 行 —— 第一次點才載，不放在開 tab 的關鍵路徑上。
let _reconMod = null;

export async function openRecon() {
    const panel = document.getElementById('cash-recon-panel');
    const mount = document.getElementById('cash-recon-mount');
    if (!panel || !mount) return;
    closeDetail();
    document.getElementById('cash-list-panel').style.display = 'none';
    panel.style.display = 'flex';
    mount.innerHTML = '<div style="color:#888;padding:20px;">載入中…</div>';
    try {
        if (!_reconMod) _reconMod = await import('../finance/subviews/recon.js');
        // isCurrent：面板關掉之後才回來的 fetch 不要再動 DOM
        await _reconMod.default(mount, { isCurrent: () => panel.style.display !== 'none' });
    } catch (e) {
        mount.innerHTML = '<div style="color:#f87171;padding:20px;">對帳系統載入失敗：'
            + _esc(e.message) + '</div>';
    }
}

export function closeRecon() {
    const panel = document.getElementById('cash-recon-panel');
    if (panel) panel.style.display = 'none';
    const list = document.getElementById('cash-list-panel');
    if (list) list.style.display = '';
    // 對帳會寫帳（工作台「補記入帳」、對帳單匯入）→ 回來要看得到新的那幾筆
    loadEntries();
}

export function openImportModal() {
    _csvFile = null;
    document.getElementById('cash-drop-filename').textContent = '';
    const r = document.getElementById('cash-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('cash-btn-do-import').disabled = true;
    document.getElementById('cash-import-modal').style.display = 'flex';
}
export function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('cash-drop-filename').textContent = file ? file.name : '';
    document.getElementById('cash-btn-do-import').disabled = !file;
}
export async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('cash-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        const form = new FormData(); form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/cash-entries/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '匯入失敗');
        const data = await res.json();
        const result = document.getElementById('cash-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        result.style.display = 'block';
        await loadEntries();
    } catch (e) {
        const result = document.getElementById('cash-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally { btn.disabled = false; btn.textContent = '開始匯入'; }
}

// ── 欄位選擇（owner 2026-09-03：「一個編輯按鈕，讓我選擇哪一些欄要出現」）──
//
// 表頭與列的每一格都帶 cash-c-<key>；藏＝display:none（欄寬綁 class，抽不抽節點都不會位移，藏最省事），
// 規則寫進 #cash-col-style。選擇記在瀏覽器（localStorage），跟排序一樣是個人偏好。
// 日期／內容固定不給藏；私帳沒有發票，發票欄預設藏（還是可以自己打開，只是空的）。
const _COLS = [
    ['deposit', '收入'], ['card', '信用卡'], ['expense', '支出'], ['book', '類別'], ['item', '項目'],
    ['sub_item', '子項目'], ['bank_memo', '銀行資訊'], ['note', '附註'], ['project', '專案'],
    ['invoice', '發票'], ['payment', '請款單'], ['account', '帳戶'],
];
const _COLS_KEY = 'cash_hidden_cols';
function _hiddenCols() {
    try {
        const v = JSON.parse(localStorage.getItem(_COLS_KEY) || 'null');
        if (Array.isArray(v)) return new Set(v);
    } catch (_) { /* 壞值當沒存 */ }
    return new Set(ledgerHasInvoices() ? [] : ['invoice']);
}
function _applyCols(hidden) {
    let st = document.getElementById('cash-col-style');
    if (!st) { st = document.createElement('style'); st.id = 'cash-col-style'; document.head.appendChild(st); }
    st.textContent = [...hidden].map((k) => `#cash-list-panel .cash-c-${k}{display:none;}`).join('');
}
export function _initColumnChooser() {
    const btn = document.getElementById('cash-btn-cols'), pop = document.getElementById('cash-cols-pop');
    if (!btn || !pop) return;
    let hidden = _hiddenCols();
    _applyCols(hidden);
    // 樣式全部內嵌：這個小視窗不靠 crm.css（瀏覽器快取到舊 CSS 時也不會擠成一團）
    const chip = (k, l) => {
        const on = !hidden.has(k);
        // 一直列、每列一欄（owner：「最重要是要直列排整齊」）：勾選框固定寬、文字靠左對齊
        return `<label style="display:grid;grid-template-columns:18px 1fr;align-items:center;gap:10px;padding:6px 8px;border-radius:6px;cursor:pointer;
                    color:${on ? '#eee' : '#6b7280'};font-size:13px;line-height:1.2;background:${on ? '#232b36' : 'transparent'};">
                <input type="checkbox" data-col="${k}" ${on ? 'checked' : ''} style="accent-color:#3b82f6;margin:0;width:15px;height:15px;justify-self:center;">
                <span style="text-align:left;">${l}</span></label>`;
    };
    const draw = () => {
        pop.style.cssText = 'position:absolute;right:0;top:36px;z-index:50;width:220px;background:#1f1f1f;border:1px solid #3a3a3a;border-radius:10px;padding:12px 14px;box-shadow:0 10px 28px rgba(0,0,0,.55);text-align:left;';
        pop.innerHTML = `<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:10px;">
                <b style="color:#eee;font-size:13px;">顯示哪些欄</b>
                <span style="color:#777;font-size:11px;">${hidden.size ? `藏了 ${hidden.size} 欄` : '全部顯示中'}</span></div>
            <div style="display:flex;flex-direction:column;gap:2px;">${_COLS.map(([k, l]) => chip(k, l)).join('')}</div>
            <div style="color:#666;font-size:11px;margin:10px 0 8px;">日期、內容固定顯示；選擇只記在這台瀏覽器。</div>
            <div style="display:flex;gap:8px;justify-content:flex-end;">
                <button type="button" class="crm-btn crm-btn-secondary crm-btn-sm" data-cols-all>全部顯示</button>
                <button type="button" class="crm-btn crm-btn-primary crm-btn-sm" data-cols-close>完成</button></div>`;
    };
    btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (pop.style.display === 'none') { draw(); pop.style.display = 'block'; } else { pop.style.display = 'none'; }
    });
    pop.addEventListener('click', (ev) => ev.stopPropagation());
    pop.addEventListener('change', (ev) => {
        const cb = ev.target.closest('input[data-col]');
        if (!cb) return;
        if (cb.checked) hidden.delete(cb.dataset.col); else hidden.add(cb.dataset.col);
        try { localStorage.setItem(_COLS_KEY, JSON.stringify([...hidden])); } catch (_) { /* 私密視窗 */ }
        _applyCols(hidden);
        draw();
    });
    pop.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-cols-all]')) {
            hidden = new Set();
            try { localStorage.setItem(_COLS_KEY, '[]'); } catch (_) { /* 同上 */ }
            _applyCols(hidden); draw();
        }
        if (ev.target.closest('[data-cols-close]')) pop.style.display = 'none';
    });
    document.addEventListener('click', () => { pop.style.display = 'none'; });
}
