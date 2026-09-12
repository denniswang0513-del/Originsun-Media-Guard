/**
 * frontend/m/shell.js — 手機頁**唯一**的殼（docs/CRM_MOBILE_PLAN.md §4）。
 *
 * 登入三視圖（密碼＋Google GIS）、mfetch（帶 Bearer；401 → 清 token 回登入）、
 * toast、esc、本地日期、千分位、token 靜默續期。
 * 🔴 新手機頁只准 `import './shell.js'`——五個獨立頁各抄一份登入／fetch／日期
 *    已經漂過兩次（expense / invoice / petty-cash / my / media-log）。
 * 🔴 日期一律 todayLocal()：`toISOString()` 是 UTC，台北早上 8 點前會寫成昨天，
 *    跨月時連會計月都錯。
 */
// esc 只有 dom.js 一份（test_one_esc）；GIS 登入按鈕跟 SPA 殼共用 google-signin.js
import { esc } from '/js/shared/dom.js';
import { initGoogleSignIn } from '/js/shared/google-signin.js';

export { esc };
export { quotePdfFilename } from '/js/shared/quote-file.js';

const TOKEN_KEY = 'auth_token';     // 與內部 App 同 key（js/auth/auth-state.js）
const REFRESH_BEFORE_SEC = 2 * 86400;

/** 登入卡上的名字：跟著頁面的主畫面名稱走（CRM＝「源日 CRM」、士源帳本＝「士源帳本」），殼只有這一份。 */
const _brand = () => {
    const m = document.querySelector('meta[name="apple-mobile-web-app-title"]');
    return (m && m.content) || '源日 CRM';
};
const _brandHtml = () => `<img src="/img/originsun-logo.webp" alt="源日">${esc(_brand())}`;

let _gate = null;
let _started = false;
let _resolveBoot = null;

/** Date → 本地 'YYYY-MM-DD'（不是 toISOString：那是 UTC，台北早上 8 點前會變昨天）。 */
export function dateIso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export const todayLocal = () => dateIso(new Date());
/** 本地日期 ±n 天（行事曆、工作紀錄的前一天／後一天）。 */
export function addDays(iso, n) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return dateIso(d); }

export function money(n) {
    if (n === null || n === undefined || n === '') return '—';
    const v = Number(n);
    return Number.isFinite(v) ? v.toLocaleString('zh-TW') : '—';
}

export function fmtDate(iso) {
    if (!iso) return '';
    return String(iso).slice(0, 10);
}

export function toast(msg, kind = 'ok', opts = {}) {
    let el = document.getElementById('m-toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'm-toast';
        document.body.appendChild(el);
    }
    el.textContent = msg;
    // `opts.copy`：複製失敗時給一顆鈕 —— 那一下是**新的**使用者手勢，iPhone Safari 才肯寫剪貼簿
    // （自動複製失敗的原因就是手勢過期）。點了之後同步呼叫 execCommand 那條路，不再有 await。
    if (opts.copy) {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'm-btn sm'; b.style.marginLeft = '10px'; b.textContent = '複製';
        b.addEventListener('click', () => {
            const ta = document.createElement('textarea');
            ta.value = opts.copy; ta.style.position = 'fixed'; ta.style.left = '-9999px';
            document.body.appendChild(ta); ta.select();
            let ok = false;
            try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
            document.body.removeChild(ta);
            if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(opts.copy).catch(() => {});
            b.textContent = ok ? '已複製' : '請長按複製';
        });
        el.appendChild(b);
    }
    el.className = 'show ' + (kind === 'err' ? 'err' : 'ok');
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = ''; }, opts.copy ? 9000 : (kind === 'err' ? 4500 : 2200));
}

function _token() { return localStorage.getItem(TOKEN_KEY) || ''; }

function _decodePayload(tok) {
    try {
        const part = tok.split('.')[1] || '';
        const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_) { return null; }
}

/** 剩不到 2 天就靜默換一顆新 token；任何失敗都吞掉（下次再試）。 */
export async function refreshTokenIfNeeded() {
    const tok = _token();
    const p = tok && _decodePayload(tok);
    if (!p || !p.exp) return;
    if (p.exp - Date.now() / 1000 >= REFRESH_BEFORE_SEC) return;
    try {
        const r = await fetch('/api/v1/auth/refresh', {
            method: 'POST', headers: { Authorization: 'Bearer ' + tok },
        });
        if (!r.ok) return;
        const d = await r.json();
        if (d && d.token) localStorage.setItem(TOKEN_KEY, d.token);
    } catch (_) { /* 靜默 */ }
}

/** JSON 進 JSON 出；`opts.body` 傳物件。!ok 丟 Error(伺服器 detail)，`.status` 帶狀態碼。 */
export async function mfetch(path, opts = {}) {
    const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;   // multipart（收據上傳）：讓瀏覽器自己定 boundary
    const headers = Object.assign(isForm ? {} : { 'Content-Type': 'application/json' }, opts.headers || {});
    const tok = _token();
    if (tok) headers.Authorization = 'Bearer ' + tok;
    const r = await fetch(path, Object.assign({}, opts, {
        headers, body: isForm ? opts.body : (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    }));
    if (r.status === 401) {
        localStorage.removeItem(TOKEN_KEY);
        _showLogin('登入已過期，請重新登入');
        const e = new Error('登入已過期'); e.status = 401; throw e;
    }
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    if (!r.ok) {
        const d = data && data.detail;
        const msg = typeof d === 'string' ? d
            : (d && (d.message || d.reason)) || (d ? JSON.stringify(d) : `HTTP ${r.status}`);
        const e = new Error(msg); e.status = r.status; e.data = data; throw e;
    }
    return data;
}

/** 帶權限下載檔案（報價 PDF）：`<a href>` 送不了 Authorization，fetch 成 blob 再開。
 *  Android 會直接存檔；iOS 會在分頁開 PDF 再由使用者分享／存檔。401 一樣導回登入。 */
export async function mdownload(path, filename) {
    const tok = _token();
    const r = await fetch(path, { headers: tok ? { Authorization: 'Bearer ' + tok } : {} });
    if (r.status === 401) {
        localStorage.removeItem(TOKEN_KEY);
        _showLogin('登入已過期，請重新登入');
        const e = new Error('登入已過期'); e.status = 401; throw e;
    }
    if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(typeof d.detail === 'string' ? d.detail : `HTTP ${r.status}`);
    }
    const href = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = href; a.download = filename || 'file'; a.rel = 'noopener';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
}

// ── 視圖 ──────────────────────────────────────────────────
function _authRoot() {
    let el = document.getElementById('m-auth');
    if (!el) {
        el = document.createElement('div');
        el.id = 'm-auth';
        document.body.prepend(el);
    }
    return el;
}

function _hideApp(hide) {
    const app = document.getElementById('m-app');
    if (app) app.hidden = hide;
}

function _showLogin(msg = '') {
    _hideApp(true);
    const root = _authRoot();
    root.hidden = false;
    root.innerHTML = `
      <div class="m-login">
        <div class="m-login-brand">${_brandHtml()}</div>
        <div id="m-gsi" style="display:none"></div>
        <div id="m-gsi-div" class="m-login-div" style="display:none">或</div>
        <form id="m-login-form" autocomplete="on">
          <label>帳號</label><input id="m-login-user" autocomplete="username" autocapitalize="none">
          <label>密碼</label><input id="m-login-pass" type="password" autocomplete="current-password">
          <button type="submit" class="m-btn-primary">登入</button>
        </form>
        <div id="m-login-err" class="m-login-err" ${msg ? '' : 'hidden'}>${esc(msg)}</div>
      </div>`;
    root.querySelector('#m-login-form').addEventListener('submit', _pwdLogin);
    _initGoogle();
}

function _showNoPerm(me) {
    _hideApp(true);
    const root = _authRoot();
    root.hidden = false;
    root.innerHTML = `
      <div class="m-login">
        <div class="m-login-brand">${_brandHtml()}</div>
        <div class="m-login-err">「${esc(me.username || '')}」沒有「${esc(_brand())}」的權限，請找管理員開通。</div>
        <button type="button" class="m-btn-primary" id="m-logout">改用其他帳號登入</button>
      </div>`;
    root.querySelector('#m-logout').addEventListener('click', () => {
        localStorage.removeItem(TOKEN_KEY); _showLogin();
    });
}

function _loginErr(msg) {
    const el = document.getElementById('m-login-err');
    if (el) { el.textContent = msg; el.hidden = false; }
}

async function _pwdLogin(ev) {
    ev.preventDefault();
    const username = document.getElementById('m-login-user').value.trim();
    const password = document.getElementById('m-login-pass').value;
    try {
        const r = await fetch('/api/v1/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.token) { _loginErr(d.detail || '登入失敗'); return; }
        _onLoginOk(d);
    } catch (_) { _loginErr('連線失敗，請稍後再試'); }
}

function _initGoogle() {
    return initGoogleSignIn({
        container: document.getElementById('m-gsi'),
        divider: document.getElementById('m-gsi-div'),
        width: 300, onSuccess: _onLoginOk, onError: _loginErr,
    });
}

function _onLoginOk(d) {
    localStorage.setItem(TOKEN_KEY, d.token);
    if (_started) { location.reload(); return; }
    _tryStart();
}

async function _tryStart() {
    if (!_token()) { _showLogin(); return; }
    await refreshTokenIfNeeded();
    let me;
    try {
        me = await mfetch('/api/v1/auth/me');
    } catch (e) {
        if (e.status !== 401) _showLogin(e.status === 0 ? '連不到伺服器' : (e.message || '載入失敗'));
        return;
    }
    if (me && me.token) localStorage.setItem(TOKEN_KEY, me.token);   // 權限改了，後端順手給的新 token
    if (_gate && !_gate(me)) { _showNoPerm(me); return; }
    _authRoot().hidden = true;
    _hideApp(false);
    _started = true;
    if (_resolveBoot) _resolveBoot(me);
}

/**
 * 進入點：`const me = await boot({ gate })`。
 * gate(me) 回 falsy → 顯示「沒有權限」；有 token 但過期 → 登入；通過才 resolve。
 * 登入視圖在 #m-auth（沒有就自己建），app 容器是 #m-app（通過前保持 hidden）。
 */
export function boot({ gate } = {}) {
    _gate = gate || null;
    return new Promise(resolve => { _resolveBoot = resolve; _tryStart(); });
}
