// ─── Login Modal (extracted from app.js) ─── //
import { _onLoginSuccess } from './auth-state.js';

window._showLoginModal = function() {
    // Show login modal
    let modal = document.getElementById('auth-login-modal');
    if (modal) { modal.classList.remove('hidden'); return; }
    modal = document.createElement('div');
    modal.id = 'auth-login-modal';
    modal.className = 'fixed inset-0 bg-black bg-opacity-70 z-50 flex items-center justify-center';
    modal.innerHTML = `
        <div class="bg-[#2d2d2d] border border-[#444] rounded-xl shadow-2xl p-6 w-80">
            <h3 class="text-lg font-bold text-white mb-4">登入</h3>
            <input id="auth-username" type="text" placeholder="帳號" class="w-full mb-3 px-3 py-2 bg-[#1e1e1e] border border-[#555] rounded text-white text-sm" autofocus>
            <input id="auth-password" type="password" placeholder="密碼" class="w-full mb-3 px-3 py-2 bg-[#1e1e1e] border border-[#555] rounded text-white text-sm">
            <div id="auth-error" class="text-red-400 text-xs mb-2 hidden"></div>
            <div class="flex justify-end gap-2">
                <button onclick="document.getElementById('auth-login-modal').classList.add('hidden')"
                    class="px-4 py-1.5 text-sm text-gray-400 hover:text-white">取消</button>
                <button onclick="window._doLogin()"
                    class="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded">登入</button>
            </div>
            <div id="google-login-divider" class="flex items-center gap-3 my-4" style="display:none;">
                <hr class="flex-1 border-[#444]"><span class="text-xs text-gray-500">or</span><hr class="flex-1 border-[#444]">
            </div>
            <div id="g_id_signin_container" style="display:none;justify-content:center;"></div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });
    // Use named handler to avoid stacking on repeated open
    const pwdEl = document.getElementById('auth-password');
    if (pwdEl && !pwdEl._loginHandler) {
        pwdEl._loginHandler = (e) => { if (e.key === 'Enter') window._doLogin(); };
        pwdEl.addEventListener('keydown', pwdEl._loginHandler);
    }
    document.getElementById('auth-username').focus();
    if (typeof window._initGoogleLogin === 'function') window._initGoogleLogin();
};

window._doLogin = async function() {
    const u = document.getElementById('auth-username')?.value.trim();
    const p = document.getElementById('auth-password')?.value;
    const errEl = document.getElementById('auth-error');
    if (!u || !p) { if (errEl) { errEl.textContent = '請輸入帳號和密碼'; errEl.classList.remove('hidden'); } return; }
    try {
        const r = await fetch('/api/v1/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p }),
        });
        const d = await r.json();
        if (!r.ok) { if (errEl) { errEl.textContent = d.detail || '登入失敗'; errEl.classList.remove('hidden'); } return; }
        _onLoginSuccess(d);
    } catch (e) {
        if (errEl) { errEl.textContent = '連線失敗'; errEl.classList.remove('hidden'); }
    }
};

// Patch fetch to auto-add auth header for sensitive endpoints
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
    if (window._authToken && typeof url === 'string' &&
        (url.includes('/settings/') || url.includes('/control/update') ||
         url.includes('/admin/') || url.includes('/auth/') || url.includes('/roles') ||
         url.includes('/job_history') && opts.method === 'DELETE' ||
         url.includes('/reports/') && opts.method === 'DELETE' ||
         url.includes('/agents') && (opts.method === 'POST' || opts.method === 'DELETE'))) {
        opts.headers = { ...opts.headers, 'Authorization': 'Bearer ' + window._authToken };
    }
    return _origFetch.call(window, url, opts);
};

// ─── Global Error Handler ─── //
window.onerror = function(msg, url, lineNo, columnNo, error) {
    var errDiv = document.createElement('div');
    errDiv.style.position = 'fixed';
    errDiv.style.top = '0';
    errDiv.style.left = '0';
    errDiv.style.width = '100%';
    errDiv.style.background = 'red';
    errDiv.style.color = 'white';
    errDiv.style.zIndex = '999999';
    errDiv.style.padding = '20px';
    errDiv.style.fontSize = '24px';
    errDiv.style.fontWeight = 'bold';
    errDiv.innerHTML = 'FRONTEND ERROR:<br>' + msg + '<br>Line: ' + lineNo + '<br>Col: ' + columnNo;
    document.body.appendChild(errDiv);
    return false;
};
