// ─── Google OAuth Integration (extracted from app.js) ─── //
// GIS 本身（config、等 gsi/client、renderButton、POST google/login）住在
// js/shared/google-signin.js —— 手機 CRM 殼（m/shell.js）用同一份；這裡只剩
// SPA 的 DOM id 與登入成功後的 _onLoginSuccess。
import { _onLoginSuccess } from './auth-state.js';
import { initGoogleSignIn } from '../shared/google-signin.js';

let _googleOAuthInited = false;

async function _initGoogleLogin() {
    if (_googleOAuthInited) return;
    const errEl = document.getElementById('auth-error');
    const rendered = await initGoogleSignIn({
        container: document.getElementById('g_id_signin_container'),
        divider: document.getElementById('google-login-divider'),
        width: 268,
        onSuccess: _onLoginSuccess,
        onError: (msg) => { if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); } },
    });
    if (rendered) _googleOAuthInited = true;
}

// Expose for login-modal.js
window._initGoogleLogin = _initGoogleLogin;
