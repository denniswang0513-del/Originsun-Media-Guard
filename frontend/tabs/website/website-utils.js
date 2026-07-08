/**
 * website-utils.js — Phase M 官網管理 Tab 共用工具
 *
 * API base = 同源（serve 這個頁面的 agent：生產 master 8000 / dev 8001）。
 * master 跑同一套 routers/website、寫同一顆 NAS Postgres，/uploads 也由
 * main.py mount — 同源直打不需要 CORS preflight、完全不經 Cloudflare。
 *
 * 歷史（為什麼不再跨域打 NAS website-api）：
 *   2026-04-29 ~ 07-03 admin Tab 跨域打 test.originsun-studio.com（NAS 8090）。
 *   07-03 test hostname 移除、改打正式 www 後，www 的 Cloudflare bot 對抗層
 *   會間歇擋掉帶 Authorization 的 admin fetch（preflight 放行、GET 消失在
 *   edge，07-05 全天 0 GET）。而「PM 在家 master 關機也能編輯」本來就不成立 —
 *   這個 UI 由 master serve，master 關機時頁面根本開不起來 → 同源永遠可用。
 *
 * localStorage `website_api_base` 覆寫仍保留（除錯 / 臨時直打 NAS 用）。
 * 其他基礎工具（esc、fmtNum、renderAvatar）沿用 CRM 的 crm-utils.js 避免重複。
 */

import { esc, fmtNum, renderAvatar } from '../crm/crm-utils.js';
export { esc, fmtNum, renderAvatar };

export function getApiBase() {
    try {
        const override = localStorage.getItem('website_api_base');
        if (override) return override;
    } catch { /* SSR / no localStorage */ }
    return '';
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
        throw new Error(`無法連線到 website-api (${getApiBase() || '同源'}): ${e.message}`);
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


// ── 📝 頁面文案編輯卡（共用：services / about / contact / blog / home 子視圖）──
//
// 對外 Astro 各頁的硬寫行銷文案改成 website_settings 的 `copy.<page>.<block>_<lang>`
// KV key。settings_service.get_meta 掃 copy.* 組成 meta.copy[page][block]，各 .astro
// 用 meta.copy?.<page>?.<block>_zh ?? "<硬寫 fallback>" 渲染（留空則維持原樣）。
//
// blocks descriptor 每筆：
//   { key, label, type, long, placeholderZh, placeholderEn, hint }
//   - type 'bilingual'（預設）→ 產 <prefix>.<key>_zh + _en 兩個輸入框
//   - type 'text'              → 產 <prefix>.<key> 單一輸入框（如 news hero_image URL）
//   - long: true               → 用 textarea（多行文案 / 介紹段落）
//
// 用法（subview render 內）：
//   container.insertAdjacentHTML('beforeend',
//       renderCopyCard('copy.services', settings, SERVICES_COPY_BLOCKS));
//   // settings = await websiteFetch('/api/website/admin/settings') 的 .settings
//
// 卡內「💾 儲存頁面文案」按鈕 onclick 統一呼叫 window._websiteSaveCopyCard(cardId)。

let _copyCardSeq = 0;

export function renderCopyCard(prefix, settings = {}, blocks = [], opts = {}) {
    const { title = '📝 頁面文案', note = '留空則對外網站維持原本的預設文案。' } = opts;
    const cardId = `copy-card-${++_copyCardSeq}`;

    const inputHtml = (fullKey, value, placeholder, long) => long
        ? `<textarea data-copy-key="${esc(fullKey)}" rows="3" style="width:100%;resize:vertical;" placeholder="${esc(placeholder || '')}">${esc(value || '')}</textarea>`
        : `<input data-copy-key="${esc(fullKey)}" value="${esc(value || '')}" placeholder="${esc(placeholder || '')}" style="width:100%;" />`;

    const rows = blocks.map(b => {
        const type = b.type || 'bilingual';
        if (type === 'text') {
            const k = `${prefix}.${b.key}`;
            return `
                <div style="margin-bottom:12px;">
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">${esc(b.label)}</label>
                    ${inputHtml(k, settings[k], b.placeholderZh, b.long)}
                    ${b.hint ? `<div style="color:#666;font-size:10px;margin-top:2px;">${esc(b.hint)}</div>` : ''}
                </div>`;
        }
        // bilingual：zh + en 並排
        const kZh = `${prefix}.${b.key}_zh`;
        const kEn = `${prefix}.${b.key}_en`;
        return `
            <div style="margin-bottom:12px;">
                <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">${esc(b.label)}</label>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
                    <div>${inputHtml(kZh, settings[kZh], b.placeholderZh || '中文', b.long)}</div>
                    <div>${inputHtml(kEn, settings[kEn], b.placeholderEn || 'English', b.long)}</div>
                </div>
                ${b.hint ? `<div style="color:#666;font-size:10px;margin-top:2px;">${esc(b.hint)}</div>` : ''}
            </div>`;
    }).join('');

    return `
        <div id="${cardId}" class="card" style="margin-bottom:16px;border-left:3px solid #3b82f6;">
            <h3 style="color:#fff;margin:0 0 4px 0;font-size:14px;">${esc(title)}</h3>
            <p style="color:#888;font-size:11px;margin:0 0 12px 0;">${esc(note)}</p>
            ${rows}
            <button class="btn btn-sm" onclick="window._websiteSaveCopyCard('${cardId}')">💾 儲存頁面文案</button>
        </div>`;
}

// 共用儲存：掃指定卡內 [data-copy-key] → 只送有「變更或非空」的值。
// 為了讓「清空欄位 = 回退到 Astro 硬寫 fallback」可行，空字串也會寫入（settings 存空字串，
// get_meta 掃出 ""，Astro 的 `?? fallback` 因 nullish 不觸發 → 仍顯示空）；因此這裡一律送全部值。
window._websiteSaveCopyCard = async (cardId) => {
    const card = document.getElementById(cardId);
    if (!card) { toastErr('找不到文案卡'); return; }
    const values = {};
    card.querySelectorAll('[data-copy-key]').forEach(el => {
        values[el.dataset.copyKey] = el.value;
    });
    if (!Object.keys(values).length) { toastOk('沒有可儲存的欄位'); return; }
    try {
        const r = await websiteFetch('/api/website/admin/settings', { method: 'PUT', body: { values } });
        toastOk(`已更新 ${r.updated} 項頁面文案`);
    } catch (e) { toastErr(e.message); }
};
