/**
 * website-utils.js — Phase M 官網管理 Tab 共用工具
 *
 * 預設打 NAS website-api（cloudflared tunnel）— 這樣 admin Tab 不依賴 master
 * 開機。PM 在家用瀏覽器開 admin Tab → 透過 cloudflared 直接戳 NAS API。
 *
 * 規則：
 *   1. localStorage `website_api_base` 覆寫優先（除錯用）
 *   2. 同源 LAN（master 192.168.1.11:8000、localhost:8000） → 走 LAN 直連 NAS:8090
 *      省 cloudflared hop，且不需要 NAS 對外開 admin endpoint
 *   3. 其他來源（cloudflared 進來、PM 在家） → 走 cloudflared origin
 *
 * 其他基礎工具（esc、fmtNum、renderAvatar）沿用 CRM 的 crm-utils.js 避免重複。
 */

import { esc, fmtNum, renderAvatar } from '../crm/crm-utils.js';
export { esc, fmtNum, renderAvatar };


// NAS Website_Nginx LAN 入口（同公司網段時 admin Tab 直連、省 cloudflared）
const NAS_LAN_BASE = 'http://192.168.1.132:8090';
// 對外 cloudflared tunnel hostname（master 關機 / PM 在家走這條）
const NAS_PUBLIC_BASE = 'https://test.originsun-studio.com';

function _isLanOrigin() {
    const h = window.location.hostname;
    return h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.') || h === '192.168.1.11';
}

export function getApiBase() {
    try {
        const override = localStorage.getItem('website_api_base');
        if (override) return override;
    } catch { /* SSR / no localStorage */ }
    return _isLanOrigin() ? NAS_LAN_BASE : NAS_PUBLIC_BASE;
}

/**
 * 跨機呼叫 NAS website-api。
 * @param {string} path    — 相對路徑，如 '/api/website/admin/works'
 * @param {object} opts    — fetch options（method/headers/body）
 * @returns {Promise<any>} — 解析好的 JSON。非 2xx 會 throw 帶 detail。
 */
export async function websiteFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = {
        'Accept': 'application/json',
        ...(opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        ...(opts.headers || {}),
    };

    let body = opts.body;
    if (body && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof URLSearchParams)) {
        body = JSON.stringify(body);
    }

    const url = `${getApiBase()}${path}`;
    let resp;
    try {
        resp = await fetch(url, { ...opts, headers, body });
    } catch (e) {
        throw new Error(`無法連線到 website-api (${getApiBase()}): ${e.message}`);
    }

    if (resp.status === 204) return null;

    let data = null;
    const ct = resp.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
        data = await resp.json().catch(() => null);
    } else {
        data = await resp.text().catch(() => null);
    }

    if (!resp.ok) {
        const detail = (data && typeof data === 'object' && data.detail) || data || resp.statusText;
        const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        err.status = resp.status;
        err.detail = detail;
        throw err;
    }

    return data;
}

/** Toast helper — 成功訊息（綠） */
export function toastOk(msg) {
    _toast(msg, '#1f7a3e');
}

/** Toast helper — 錯誤訊息（紅） */
export function toastErr(msg) {
    _toast(`❌ ${msg}`, '#8b1d1d');
}

// 多 toast 同時出現時垂直 stack，避免互相蓋住（5 個 save 同時觸發只看到最後一個）
let _toastCount = 0;

function _toast(msg, bg) {
    const div = document.createElement('div');
    div.textContent = msg;
    const top = 20 + _toastCount * 50;
    _toastCount++;
    div.style.cssText = `
        position: fixed; top: ${top}px; right: 20px; z-index: 10000;
        background: ${bg}; color: white; padding: 10px 16px;
        border-radius: 6px; font-size: 13px; max-width: 400px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        transition: opacity 0.3s, transform 0.3s;
    `;
    document.body.appendChild(div);
    setTimeout(() => {
        div.style.opacity = '0';
        div.style.transform = 'translateX(20px)';
        setTimeout(() => {
            div.remove();
            _toastCount = Math.max(0, _toastCount - 1);
        }, 300);
    }, 3500);
}

/** ISO string → 本地時間顯示（精度到分） */
export function fmtDt(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    } catch {
        return iso;
    }
}

/** ISO string → 相對時間（X 分鐘前 / X 小時前 / X 天前） */
export function fmtRelative(iso) {
    if (!iso) return '';
    try {
        const diff = (Date.now() - new Date(iso).getTime()) / 1000;
        if (diff < 60) return '剛剛';
        if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
        if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
        if (diff < 2592000) return `${Math.floor(diff / 86400)} 天前`;
        return fmtDt(iso);
    } catch {
        return iso;
    }
}


// ── InquiryStatus 集中定義（對應 core/schemas_website.py InquiryStatus Literal） ──

export const INQUIRY_STATUSES = [
    { value: 'new',         labelZh: '新詢問', labelShort: '新' },
    { value: 'in_progress', labelZh: '處理中', labelShort: '處理中' },
    { value: 'converted',   labelZh: '已轉換', labelShort: '已轉換' },
    { value: 'spam',        labelZh: '垃圾',   labelShort: '垃圾' },
];

const _STATUS_MAP = Object.fromEntries(INQUIRY_STATUSES.map(s => [s.value, s]));

export function inquiryStatusLabel(status, short = false) {
    const s = _STATUS_MAP[status];
    return s ? (short ? s.labelShort : s.labelZh) : status;
}


// ── 空狀態統一渲染（10+ 處原本散落 inline padding/color 字串） ──

/** 表格 tbody 空狀態（colspan 配對欄位數）。 */
export function emptyRow(colspan, msg) {
    return `<tr><td colspan="${colspan}" style="padding:30px;text-align:center;color:#888;">${esc(msg)}</td></tr>`;
}

/** Card / div 空狀態（給 grid / list / chip 用）。 */
export function emptyHint(msg, opts = {}) {
    const { padding = 20, fontSize = 12, gridFull = false } = opts;
    const gridStyle = gridFull ? 'grid-column:1/-1;' : '';
    return `<div style="${gridStyle}color:#888;font-size:${fontSize}px;padding:${padding}px;text-align:center;">${esc(msg)}</div>`;
}


// ── 載入失敗統一渲染（8 個 subviews 原本各自寫一次 try/catch innerHTML） ──

export function renderLoadError(container, title, err, hint = '') {
    if (!container) return;
    container.innerHTML = `
        <h2>${esc(title)}</h2>
        <div class="card" style="color:#f87171;">
            <strong>無法載入：</strong> ${esc(err?.message || String(err))}
            ${hint ? `<div style="color:#888;margin-top:8px;font-size:12px;">${esc(hint)}</div>` : ''}
        </div>
    `;
}


// ── Debounce (給 works filter input 用) ──

export function debounce(fn, ms = 150) {
    let t = null;
    return (...args) => {
        if (t) clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
    };
}


// ── Inline-edit row helper（categories / seo / blog 子視圖共用） ──

/**
 * 把指定 row 內的 input/checkbox/number/textarea 收成 patch dict。
 * - data-id="<rowId>"   標出哪一列
 * - data-field="<key>"  標 patch 欄位名稱
 * type=number 自動 Number()，NaN → null
 */
export function readRowPatch(scopeSel, id) {
    const patch = {};
    document.querySelectorAll(`${scopeSel} [data-id="${id}"]`).forEach(el => {
        const f = el.dataset.field;
        if (el.type === 'checkbox') {
            patch[f] = el.checked;
        } else if (el.type === 'number') {
            const n = Number(el.value);
            patch[f] = Number.isNaN(n) ? null : n;
        } else {
            patch[f] = el.value;
        }
    });
    return patch;
}


// ── Modal overlay helper（admin Tab 多處 modal 共用 chrome） ──

/**
 * 建立 modal overlay，append 到 body，回 modal element。
 * 同 id 的舊 modal 會先 remove（防 double-open 殘骸）。
 *
 * 用法：
 *   const m = openModal('my-modal', '<div>...</div>', { width: '720px' });
 *   // 內容裡的 onclick 用 closeModal('my-modal') 關閉
 */
export function openModal(id, innerHtml, { width = '720px', onClose } = {}) {
    closeModal(id);
    const m = document.createElement('div');
    m.id = id;
    m.style.cssText = `
        position:fixed;inset:0;z-index:9999;
        background:rgba(0,0,0,0.7);
        display:flex;align-items:center;justify-content:center;padding:20px;
    `;
    m.innerHTML = `
        <div style="
            background:#1a1a1a;border:1px solid #3a3a3a;border-radius:8px;
            width:100%;max-width:${width};max-height:90vh;overflow-y:auto;
            box-shadow:0 12px 40px rgba(0,0,0,0.5);
        ">${innerHtml}</div>
    `;
    document.body.appendChild(m);
    if (onClose) m.dataset._onClose = '1';   // marker；暫時用 ad-hoc 機制
    return m;
}

export function closeModal(id) {
    const m = document.getElementById(id);
    if (m) m.remove();
}
