/**
 * prop-fetch.js — 提案模組共用 fetcher（proposals.js + plan-matrix.js 同用一份）。
 *
 * 比一般 tfetch 多帶 err.status / err.detail — plan-matrix 的 409 衝突 UI
 * 靠它們分流（曾因兩份 fetcher 分岔，SPA 路徑的衝突 UI 整個死路）。
 * 無 SPA 依賴：未來 /proposal-plan.html 獨立頁也 import 這份。
 */

function _authHeaders(extra = {}) {
    const token = localStorage.getItem('auth_token');
    return { ...extra, ...(token ? { 'Authorization': 'Bearer ' + token } : {}) };
}

export async function tfetch(path, opts = {}) {
    const headers = _authHeaders({ 'Accept': 'application/json' });
    if (opts.json !== undefined) {
        headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.json);
        delete opts.json;
    }
    const r = await fetch(path, { ...opts, headers });
    if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail;
        const err = new Error((detail && detail.reason) ? detail.reason
            : (typeof detail === 'string' && detail) || ('HTTP ' + r.status));
        err.status = r.status;
        err.detail = detail;
        throw err;
    }
    return r.json();
}

/**
 * 開啟提案簡報。deck 有兩種落點（2026-08-06 資產夾化）：
 * - `/uploads/...` 開頭（舊）→ 在 web root 裡，直接開新分頁。
 * - 其他（NAS 資產夾的絕對路徑）→ 走帶權限的下載端點。`<a href>` 送不了
 *   Authorization header，所以 fetch 成 blob 再觸發下載。
 *
 * 失敗自己 alert（三個呼叫端都只會做這件事）→ 呼叫端一行就夠。
 */
export async function openDeck(pid, deckUrl) {
    if (!deckUrl) return;
    if (deckUrl.startsWith('/')) {
        window.open(deckUrl, '_blank', 'noopener');
        return;
    }
    try {
        const r = await fetch(`/api/v1/proposals/${encodeURIComponent(pid)}/deck/download`,
            { headers: _authHeaders() });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
        }
        const href = URL.createObjectURL(await r.blob());
        const a = document.createElement('a');
        a.href = href;
        a.download = deckUrl.split(/[\\/]/).pop() || 'deck';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (e) {
        alert('簡報下載失敗：' + (e.message || e));
    }
}
