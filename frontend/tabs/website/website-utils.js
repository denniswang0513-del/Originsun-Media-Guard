/**
 * website-utils.js — Phase M 官網管理 Tab 共用工具
 *
 * 跨機呼叫 NAS website-api（:8001）。預設 http://localhost:8001，可用
 * localStorage 覆寫：
 *     localStorage.setItem('website_api_base', 'http://192.168.1.132:8081')
 *
 * 其他基礎工具（esc、fmtNum、renderAvatar）沿用 CRM 的 crm-utils.js 避免重複。
 */

export {
    esc, fmtNum, renderAvatar,
} from '../crm/crm-utils.js';


const DEFAULT_API_BASE = 'http://localhost:8001';

export function getApiBase() {
    try {
        return localStorage.getItem('website_api_base') || DEFAULT_API_BASE;
    } catch {
        return DEFAULT_API_BASE;
    }
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
