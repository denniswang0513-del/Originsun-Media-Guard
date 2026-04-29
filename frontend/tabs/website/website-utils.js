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

function _toast(msg, bg) {
    const div = document.createElement('div');
    div.textContent = msg;
    div.style.cssText = `
        position: fixed; top: 20px; right: 20px; z-index: 10000;
        background: ${bg}; color: white; padding: 10px 16px;
        border-radius: 6px; font-size: 13px; max-width: 400px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    `;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 3500);
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
