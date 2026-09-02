/**
 * fin-utils.js — 財務管理 Tab 共用工具
 *
 * finFetch：打 /api/v1/finance prefix 的小 helper（token / 錯誤處理比照
 * crm-utils.js 的 crmFetch + _doFetch — 4xx/5xx 時抽出 detail 丟 Error）。
 * esc / fmtNum：與 crm-utils 同名同義的小工具（避免跨 tab import 依賴）。
 */

const API = '/api/v1/finance';

// ── 兩本帳（公司實體）—— docs/LEDGER_ENTITY_PLAN.md §5 ─────────
// 'parent' = 母公司（預設；財務 tab 固定這本）/ 'mine' = 我的帳（owner 私帳，
// 只在獨立頁 /my-ledger.html 出現 —— 該頁在載入財務模組前把 window._finEntity
// 釘成 'mine'）。無使用者可見的帳本切換器（v2 已移除 v1 的切換 pill），所以
// 前端不需要 entity → 顯示名稱的對照表（要顯示的那一處直接寫死）。

// scope 判定（crm-utils.js hasModule 的同義複寫 —— 本檔刻意零 import，見檔頭）：
// Lv3 admin 的 modules 經 _enrich_user 已含全 key，Lv3 那半是安全冗餘。
function _hasMod(key) {
    return (window._accessLevel || 0) >= 3 || (window._modules || []).includes(key);
}
/** 母公司 full scope ⟺ crm_invoices AND money_view
 *（鏡射 core/ledger.py allowed_entities level="full"）。
 * 前端只需要這一條：view scope 多出來的 finance_partner 那半是「報表唯讀」，
 * 對 nav 而言＝沒有 full 的一切，((A&&M)||P) && !(P && !(A&&M)) 恆等於 A&&M。 */
export function finHasFullParentScope() {
    return _hasMod('crm_invoices') && _hasMod('money_view');
}

/** 目前帳本 — 頁面 pin（/my-ledger.html 設 window._finEntity='mine'），預設母公司 */
export function finEntity() { return window._finEntity || 'parent'; }

/** 現在這一本是不是私帳 —— **全樹唯一一支**（tests/unit/
 *  test_project_entity_wall.py 有一條掃全 frontend/ 的斷言在守）。
 *
 *  🔴 `'mine'` 哪天不再是單一字面常數（第二本私帳、逐人帳本 id），要改的就只有
 *  這一行；散在各處的 `xxxEntity() === 'mine'` 連 grep 都沒有名字可以找。
 *  `finFetchMine` 的說明講的是同一件事，只是它管的是寫那一半。
 */
export const finIsMine = () => finEntity() === 'mine';

/** 這本帳有沒有「發票」這回事 —— owner 2026-09-01「私帳不會開發票，連結發票
 *  都用連結專案替代」。
 *  🔴 **一條產品規則一個名字**，而且要跨檔：`finIsMine()` 在這個 repo 至少
 *  承載四種語意（帳本 pin、期間預設、發票有無、代收薪資）。哪天私帳也要開
 *  發票，改的是這一支；裸寫 `finIsMine()` 的地方改不到。 */
export const ledgerHasInvoices = () => !finIsMine();

export async function finFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    // 一律帶 entity（既有呼叫端有的 path 已帶 '?'，判斷後拼接）。
    // opts.entity＝單次覆寫（逐案損益在主系統也固定打私帳，見 subviews/projects.js）
    const { entity, ...rest } = opts;
    const sep = path.includes('?') ? '&' : '?';
    const res = await fetch(API + path + sep + 'entity=' + (entity || finEntity()), { ...rest, headers });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        const detail = Array.isArray(err.detail)
            ? err.detail.map(e => e.msg || e.message || JSON.stringify(e)).join('; ')
            : (err.detail || '請求失敗');
        throw new Error(detail);
    }
    return res.json();
}

/** 私帳子視圖（執行專案/器材清冊/應收）固定打 'mine' —— 逐呼叫手釘的話，
 *  忘了第 12 個呼叫點不會炸，只會靜靜打到母公司帳，所以收成一支。 */
export const finFetchMine = (path, opts = {}) => finFetch(path, { ...opts, entity: 'mine' });

// HTML 逃脫只有 js/shared/dom.js 一份（2026-08-30 收斂：全前端曾有八份，
// 而且逃脫的字元各不相同）。這裡 re-export，呼叫端一個字都不用改。
// 🔴 `import` 再 `export`，不能只寫 `export { esc } from …` ——
// 那是純轉出，**不會在本模組建立區域繫結**，本檔自己用到 esc 的地方會
// ReferenceError（2026-08-30 收斂時就這樣炸過一次）。
import { esc } from '../../js/shared/dom.js';

export { esc };

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
    // 股東往來（owner 2026-08-21）。餘額＝公司欠該股東多少。
    // 🔴 這兩種**不是現金** —— 報表上借款進負債、投資款進權益
    //    （規則正本 core.finance_logic.split_bank_lines）。
    { v: 'shareholder_loan', label: '股東往來－借款' },
    { v: 'shareholder_capital', label: '股東往來－投資款' },
    // 信用卡（owner 2026-08-27「區隔哪一個銀行的信用卡」）：一張卡一個帳戶，
    // 刷卡列掛它當**卡別身分**。🔴 不是現金也不是資產 —— 卡債由卡片帳的
    // 期初＋刷卡−還款算（core.finance_logic.card_outstanding）。
    { v: 'card', label: '信用卡' },
];
// 哪些 acct_kind 是股東往來（鏡射 core.finance_logic.SHAREHOLDER_KINDS）
export const SHAREHOLDER_KINDS = ['shareholder_loan', 'shareholder_capital'];
export const isShareholderAcct = (k) => SHAREHOLDER_KINDS.includes(k || '');
export const CARD_KIND = 'card';                    // 鏡射 core.finance_logic.CARD_KIND
export const isCardAcct = (k) => (k || '') === CARD_KIND;
/** 這本帳的信用卡帳戶（啟用中）—— 卡別下拉與卡片頁籤共用這一份判定 */
export const cardOnly = (accounts) =>
    (accounts || []).filter(a => a.active !== false && isCardAcct(a.acct_kind));

/** 啟用中的**真銀行**帳戶（排除股東往來）。
 *  對帳單匯入、分類規則、貸款扣款、對帳工作台都只該看到這些 —— 股東往來沒有
 *  銀行對帳單、也不會拿來扣貸款；信用卡帳戶是卡別身分不是錢包（2026-08-27）。
 *  收支明細那邊的帳戶下拉不受此限（股東墊付的費用本來就要掛到股東帳戶上）。
 *  住在這裡而不是各自寫一份：banking.js 與 recon.js 拆開後兩邊都要問這句話。 */
export const bankOnly = (accounts) =>
    (accounts || []).filter(a => a.active !== false && !isShareholderAcct(a.acct_kind)
                                 && !isCardAcct(a.acct_kind));

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

// 私帳的會計年度（owner 2026-08-26「我的結帳月份是每年 6 月 30」）：
// 年度＝前一年 7/1 〜 當年 6/30，以**結束年**命名（FY2026＝2025/07–2026/06）。
// 母公司照曆年不受影響。之後若要可設定再搬 settings，先寫死一份正本在這。
const FISCAL_END_MONTH = 6;

/** 今天落在哪個會計年度（以結束年命名）：2026-08 → FY2027（2026/07–2027/06） */
export function currentFiscalYear() {
    const t = new Date();
    return t.getFullYear() + (t.getMonth() + 1 > FISCAL_END_MONTH ? 1 : 0);
}

/** 期間 mode 預設：私帳＝年（owner 的節奏是年度結帳）、母公司＝月 */
export const defaultPeriodMode = () => (finIsMine() ? 'year' : 'month');

/** FY 的起訖（period 字串用）：FY2026 → ['2025-07', '2026-06'] */
export function fiscalRange(y) {
    const mm = String(FISCAL_END_MONTH).padStart(2, '0');
    const mm1 = String(FISCAL_END_MONTH + 1).padStart(2, '0');
    return [`${y - 1}-${mm1}`, `${y}-${mm}`];
}
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
        if (finIsMine()) {
            // 私帳：年＝會計年度（7/1–6/30），選項直接把區間寫在臉上
            const fy = currentFiscalYear();
            const opts = [];
            for (let y = fy; y >= fy - 7; y--) {
                const [a, b] = fiscalRange(y);
                opts.push(`<option value="${y}"${y === fy ? ' selected' : ''}>${a.replace('-', '/')} – ${b.replace('-', '/')}</option>`);
            }
            span.innerHTML = `<select id="${prefix}-year" class="crm-select" data-fiscal="1">${opts.join('')}</select>`;
        } else {
            span.innerHTML = `<select id="${prefix}-year" class="crm-select">${yearOpts(curYear)}</select>`;
        }
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
        if (g('year')?.dataset.fiscal) {
            // 私帳會計年度 → 後端本來就吃的自訂區間格式（不用動後端）
            const [a, b] = fiscalRange(parseInt(y, 10));
            return { period: `${a}..${b}`, end: b };
        }
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
