/**
 * fin-utils.js — 財務管理 Tab 共用工具
 *
 * finFetch：打 /api/v1/finance prefix 的小 helper（token / 錯誤處理比照
 * crm-utils.js 的 crmFetch + _doFetch — 4xx/5xx 時抽出 detail 丟 Error）。
 * esc / fmtNum：與 crm-utils 同名同義的小工具（避免跨 tab import 依賴）。
 */

const API = '/api/v1/finance';

export async function finFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(API + path, { ...opts, headers });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        const detail = Array.isArray(err.detail)
            ? err.detail.map(e => e.msg || e.message || JSON.stringify(e)).join('; ')
            : (err.detail || '請求失敗');
        throw new Error(detail);
    }
    return res.json();
}

export function esc(str) {
    return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function fmtNum(n) {
    return (n || 0).toLocaleString('zh-TW');
}

// ⚠ 值域對齊 routers/api_finance.py 的 TREATMENTS/ACCT_KINDS — 改任一邊要同步
export const TREATMENT_OPTIONS = [
    { v: 'direct_expense', label: '一般支出（進損益）' },
    { v: 'direct_income',  label: '一般收入（進損益）' },
    { v: 'ap_settlement',  label: '付掉之前的請款（不進損益）' },
    { v: 'ar_settlement',  label: '收回之前的發票款（不進損益）' },
    { v: 'transfer',       label: '帳戶間轉帳' },
    { v: 'tax_vat',        label: '繳營業稅' },
    { v: 'tax_income',     label: '繳營所稅' },
    { v: 'advance',        label: '預支相關' },
    { v: 'passthrough',    label: '代收代付（發票代開）' },
];
export const ACCT_KIND_OPTIONS = [
    { v: 'bank', label: '銀行帳戶' },
    { v: 'cash', label: '零用金' },
];

/**
 * 子視圖開場殼：loading → Promise.all → isCurrent 防競態 → 失敗畫重試鈕。
 *
 * @param {HTMLElement} container 子視圖容器
 * @param {object} opts
 *   title      <h2> 標題（含 emoji，靜態字串）
 *   isCurrent  ctx.isCurrent — 切走後不再動 DOM
 *   fetchers   () => Promise 陣列（同時發；個別要降級的自己 .catch）
 *   retry      重試鈕 onclick 字串（如 'window._finBank.reload()'）
 * @returns {Promise<Array|null>} 成功回 Promise.all 結果；失敗或已切走回 null
 */
export async function finSubviewBoot(container, { title, isCurrent = () => true, fetchers, retry }) {
    const h2 = `<h2 style="margin:0 0 12px;color:#eee;">${title}</h2>`;
    container.innerHTML = h2 + '<div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const results = await Promise.all(fetchers.map(f => f()));
        if (!isCurrent()) return null;
        return results;
    } catch (e) {
        if (!isCurrent()) return null;
        container.innerHTML = `${h2}
            <div style="color:#f87171;padding:20px;">載入失敗：${esc(e.message)}
                <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                        onclick="${retry}">🔄 重試</button>
            </div>`;
        return null;
    }
}

/** 右下角小 toast（成功綠 / 失敗紅），自動消失 */
export function finToast(msg, isErr = false) {
    const el = document.createElement('div');
    el.textContent = msg;
    el.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:99999;padding:10px 16px;'
        + 'border-radius:6px;font-size:13px;color:#fff;box-shadow:0 4px 12px rgba(0,0,0,.45);'
        + 'background:' + (isErr ? '#b91c1c' : '#166534') + ';';
    document.body.appendChild(el);
    setTimeout(() => el.remove(), isErr ? 5000 : 2500);
}
