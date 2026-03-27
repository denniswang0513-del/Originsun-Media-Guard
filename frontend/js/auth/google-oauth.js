// ─── Google OAuth Integration (extracted from app.js) ─── //
import { _onLoginSuccess } from './auth-state.js';

let _googleOAuthInited = false;

async function _initGoogleLogin() {
    if (_googleOAuthInited) return;
    try {
        const res = await fetch('/api/v1/auth/google/config');
        if (!res.ok) return;
        const cfg = await res.json();
        if (!cfg.enabled || !cfg.client_id) return;

        // Wait for GIS library
        if (typeof google === 'undefined' || !google.accounts) {
            await new Promise(resolve => {
                let tries = 0;
                const iv = setInterval(() => {
                    tries++;
                    if ((typeof google !== 'undefined' && google.accounts) || tries > 40) {
                        clearInterval(iv);
                        resolve();
                    }
                }, 150);
            });
        }
        if (typeof google === 'undefined' || !google.accounts) return;

        google.accounts.id.initialize({
            client_id: cfg.client_id,
            callback: _handleGoogleCredential,
            auto_select: false,
            cancel_on_tap_outside: true,
        });

        const container = document.getElementById('g_id_signin_container');
        if (container) {
            google.accounts.id.renderButton(container, {
                theme: 'filled_black', size: 'large', width: 268,
                text: 'signin_with', shape: 'rectangular', logo_alignment: 'left',
            });
            container.style.display = 'flex';
        }
        const divider = document.getElementById('google-login-divider');
        if (divider) divider.style.display = 'flex';

        _googleOAuthInited = true;
    } catch (e) {
        console.warn('[Google OAuth] Init failed:', e);
    }
}

async function _handleGoogleCredential(response) {
    const errEl = document.getElementById('auth-error');
    try {
        const res = await fetch('/api/v1/auth/google/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential: response.credential }),
        });
        const d = await res.json();
        if (!res.ok) {
            if (errEl) { errEl.textContent = d.detail || 'Google 登入失敗'; errEl.classList.remove('hidden'); }
            return;
        }
        _onLoginSuccess(d);
    } catch (e) {
        if (errEl) { errEl.textContent = '連線失敗'; errEl.classList.remove('hidden'); }
    }
}

// Expose for login-modal.js
window._initGoogleLogin = _initGoogleLogin;
