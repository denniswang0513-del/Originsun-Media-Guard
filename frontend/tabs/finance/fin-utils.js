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

/** 今天（本地時區）'YYYY-MM-DD' — 不用 toISOString（UTC 會差一天） */
export function todayStr() {
    const t = new Date();
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
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
    { v: 'loan',           label: '貸款往來（撥款/繳款）' },
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

// ── 期間選擇器（statements / dashboard 子視圖共用） ─────────────
// 兩個子視圖的期間列 element-id 前綴不同（finstmt- / findash-），
// 故 prefix 參數化；container 為子視圖根容器（各自的 _c）。
/**
 * 依 mode（月/季/年/自訂）畫期間輸入元件，塞進 container 內 #{prefix}-inputs。
 * mode 讀 container 內 #{prefix}-mode；所有動態 id 都帶 prefix。
 */
export function renderPeriodInputs(container, prefix) {
    const mode = container.querySelector('#' + prefix + '-mode').value;
    const span = container.querySelector('#' + prefix + '-inputs');
    const now = new Date();
    const curYear = now.getFullYear();
    const curYm = `${curYear}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    const years = [];
    for (let y = curYear + 1; y >= curYear - 6; y--) years.push(y);
    const yearOpts = (sel) => years.map(y => `<option value="${y}"${y === sel ? ' selected' : ''}>${y}</option>`).join('');

    if (mode === 'month') {
        span.innerHTML = `<input id="${prefix}-month" type="month" class="crm-input" value="${curYm}">`;
    } else if (mode === 'quarter') {
        const q = Math.floor(now.getMonth() / 3) + 1;
        span.innerHTML = `
            <select id="${prefix}-q-year" class="crm-select">${yearOpts(curYear)}</select>
            <select id="${prefix}-q" class="crm-select">${[1, 2, 3, 4].map(i => `<option value="${i}"${i === q ? ' selected' : ''}>Q${i}</option>`).join('')}</select>`;
    } else if (mode === 'year') {
        span.innerHTML = `<select id="${prefix}-year" class="crm-select">${yearOpts(curYear)}</select>`;
    } else {
        span.innerHTML = `
            <input id="${prefix}-from" type="month" class="crm-input" value="${curYear}-01">
            <span style="color:#888;">～</span>
            <input id="${prefix}-to" type="month" class="crm-input" value="${curYm}">`;
    }
}

/** 讀期間輸入 → {period, end}；不合法回 null（含 toast）。id 帶 prefix。 */
export function periodFromInputs(container, prefix) {
    const mode = container.querySelector('#' + prefix + '-mode').value;
    const g = (id) => container.querySelector('#' + prefix + '-' + id);
    if (mode === 'month') {
        const v = g('month')?.value;
        if (!v) { finToast('請選月份', true); return null; }
        return { period: v, end: v };
    }
    if (mode === 'quarter') {
        const y = g('q-year')?.value, q = parseInt(g('q')?.value, 10);
        if (!y || !q) { finToast('請選年與季', true); return null; }
        return { period: `${y}-Q${q}`, end: `${y}-${String(q * 3).padStart(2, '0')}` };
    }
    if (mode === 'year') {
        const y = g('year')?.value;
        if (!y) { finToast('請選年份', true); return null; }
        return { period: y, end: `${y}-12` };
    }
    const from = g('from')?.value, to = g('to')?.value;
    if (!from || !to) { finToast('請選起訖月份', true); return null; }
    if (from > to) { finToast('起始月不可晚於結束月', true); return null; }
    return { period: `${from}..${to}`, end: to };
}

/**
 * 指標卡：label + 大數值（valueHtml）+ 小註（subHtml）。
 * basis = flex-basis（statements 用 '160px'、dashboard 用 '180px'，故參數化）。
 */
export function metricCard(label, valueHtml, subHtml, basis = '160px') {
    return `
    <div style="background:#222;border:1px solid #333;border-radius:8px;padding:12px 16px;min-width:150px;flex:1 1 ${basis};">
        <div style="color:#888;font-size:11px;">${label}</div>
        <div style="font-size:20px;font-weight:700;margin-top:4px;white-space:nowrap;">${valueHtml}</div>
        ${subHtml ? `<div style="font-size:11px;margin-top:3px;">${subHtml}</div>` : ''}
    </div>`;
}

/** 比率百分比數字 → "38.7%"（35.2 = 35.2%）；null/NaN → '—' */
export function fmtPct(r) {
    if (r == null || isNaN(r)) return '—';
    return (Math.round(r * 10) / 10).toLocaleString('zh-TW') + '%';
}

// ── CSV 匯出（BOM，比照原 statements/dashboard 本地版一字不差） ──
/** CSV 儲存格跳脫：含逗號/引號/換行時包雙引號並倍化內部引號 */
export function csvCell(v) {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

/** rows（二維陣列）→ 觸發下載一份 CSV */
export function downloadCsv(rows, filename) {
    // 前置 UTF-8 BOM（U+FEFF）：讓 Excel 認出 UTF-8，中文才不會變亂碼
    const csv = String.fromCharCode(0xFEFF) + rows.map(r => r.map(csvCell).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/** 依序下載多份 CSV（每份間隔 350ms，避免瀏覽器阻擋多重下載） */
export async function downloadManyCsv(files) {
    for (let i = 0; i < files.length; i++) {
        downloadCsv(files[i].rows, files[i].name);
        if (i < files.length - 1) await new Promise(r => setTimeout(r, 350));
    }
}
