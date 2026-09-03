/**
 * google-signin.js — Google GIS 登入按鈕的**唯一**一份 bootstrap。**零 import**（葉節點）。
 *
 * 流程：GET /api/v1/auth/google/config（沒開就靜靜不畫）→ 等 gsi/client 載好
 * （40 次 × 150ms）→ initialize + renderButton → 拿到 credential 打
 * POST /api/v1/auth/google/login → onSuccess(登入回應) / onError(訊息)。
 *
 * 兩個殼（SPA 的 js/auth/google-oauth.js、手機 CRM 的 m/shell.js）各抄一份
 * 同樣的 40 行已經漂過（按鈕寬度、divider 顯示方式各自不同）；這裡只管 GIS
 * 本身，「登入成功後做什麼」與「錯誤顯示在哪」由呼叫端傳進來。
 *
 * @param {object} o
 * @param {HTMLElement|null} o.container   按鈕要畫進去的容器（沒有就不畫）
 * @param {HTMLElement|null} [o.divider]   「或」分隔線，按鈕畫出來才顯示
 * @param {number} [o.width=268]           按鈕寬度（GIS 的 px）
 * @param {(data: object) => void} o.onSuccess   登入回應（含 token）
 * @param {(msg: string) => void} [o.onError]     伺服器 detail 或連線失敗文案
 * @returns {Promise<boolean>} 按鈕有沒有畫出來
 */
export async function initGoogleSignIn({ container, divider = null, width = 268, onSuccess, onError }) {
    const fail = (msg) => { if (onError) onError(msg); };
    try {
        const r = await fetch('/api/v1/auth/google/config');
        if (!r.ok) return false;
        const cfg = await r.json();
        if (!cfg.enabled || !cfg.client_id) return false;
        for (let i = 0; i < 40 && (typeof google === 'undefined' || !google.accounts); i++)
            await new Promise(res => setTimeout(res, 150));
        if (typeof google === 'undefined' || !google.accounts || !container) return false;
        google.accounts.id.initialize({
            client_id: cfg.client_id, auto_select: false, cancel_on_tap_outside: true,
            callback: async (resp) => {
                try {
                    const r2 = await fetch('/api/v1/auth/google/login', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ credential: resp.credential }),
                    });
                    const d = await r2.json().catch(() => ({}));
                    if (!r2.ok || !d.token) { fail(d.detail || 'Google 登入失敗'); return; }
                    onSuccess(d);
                } catch (_) { fail('連線失敗，請稍後再試'); }
            },
        });
        google.accounts.id.renderButton(container, {
            theme: 'filled_black', size: 'large', width, text: 'signin_with',
            shape: 'rectangular', logo_alignment: 'left',
        });
        // 兩個殼藏法不同（SPA 用 inline display:none、手機頁用 hidden 屬性）——兩種都解開
        container.hidden = false; container.style.display = 'flex';
        if (divider) { divider.hidden = false; divider.style.display = 'flex'; }
        return true;
    } catch (e) {
        console.warn('[GIS] init failed', e);
        return false;
    }
}
