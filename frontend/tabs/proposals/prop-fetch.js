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
    const data = await r.json();
    // 後端的 warning（如「封存資料夾改名失敗」）在這條咽喉也要浮出來 ——
    // 片庫/提案頁走的是 tfetch 不是 crmFetch，只收 crmFetch 等於漏了 1/3。
    // 動態 import：公開 token 頁（guest）也走這份 fetcher，不該為了 toast
    // 在載入時就把 CRM 模組拖進來。
    if (data && data.warning) {
        import('../crm/crm-utils.js')
            .then(m => m.surfaceWarning(data))
            .catch(() => console.warn(data.warning));
    }
    return data;
}

/**
 * 開啟提案簡報。deck 有兩種落點（2026-08-06 資產夾化）：
 * - `/uploads/...` 開頭（舊）→ 在 web root 裡，直接開新分頁。
 * - 其他（NAS 資產夾的絕對路徑）→ 走帶權限的下載端點。
 *
 * 通用的 authDownload / wireFileDrop 住在 js/shared/utils.js —— 它們與提案
 * 無關，放這裡的話別的分頁搜不到、只會再複製一份。
 */
export async function openDeck(pid, deckUrl) {
    if (!deckUrl) return;
    if (deckUrl.startsWith('/')) {
        window.open(deckUrl, '_blank', 'noopener');
        return;
    }
    const { authDownload } = await import('../../js/shared/utils.js');
    await authDownload(`/api/v1/proposals/${encodeURIComponent(pid)}/deck/download`,
                       deckUrl, '簡報下載');
}
