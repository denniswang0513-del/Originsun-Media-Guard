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
 * 帶權限下載（**單一正本**）—— `<a href>` 送不了 Authorization header，
 * 所以 fetch 成 blob 再觸發。失敗自己 alert（呼叫端一行就夠）。
 * 檔名從路徑尾段取，正反斜線都吃（NAS 路徑是反斜線）。
 */
export async function authDownload(url, filename, label = '下載') {
    try {
        const r = await fetch(url, { headers: _authHeaders() });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
        }
        const href = URL.createObjectURL(await r.blob());
        const a = document.createElement('a');
        a.href = href;
        a.download = String(filename || '').split(/[\\/]/).pop() || 'file';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (e) {
        alert(`${label}失敗：` + (e.message || e));
    }
}

/**
 * 開啟提案簡報。deck 有兩種落點（2026-08-06 資產夾化）：
 * - `/uploads/...` 開頭（舊）→ 在 web root 裡，直接開新分頁。
 * - 其他（NAS 資產夾的絕對路徑）→ 走帶權限的下載端點。
 */
export async function openDeck(pid, deckUrl) {
    if (!deckUrl) return;
    if (deckUrl.startsWith('/')) {
        window.open(deckUrl, '_blank', 'noopener');
        return;
    }
    await authDownload(`/api/v1/proposals/${encodeURIComponent(pid)}/deck/download`,
                       deckUrl, '簡報下載');
}

/** 檔案大小 → 人看的字串（GB 級也顧到 —— 企劃夾可能放參考影片）。 */
export function fmtBytes(n) {
    const v = Number(n) || 0;
    if (v >= 1073741824) return (v / 1073741824).toFixed(1) + ' GB';
    if (v >= 1048576) return (v / 1048576).toFixed(1) + ' MB';
    if (v >= 1024) return Math.round(v / 1024) + ' KB';
    return v + ' B';
}

/**
 * 拖放上傳區：進入高亮、放開送檔。`onFiles(FileList)` 由呼叫端決定怎麼送。
 * 回傳解除綁定的函式（overlay 關掉時用）。
 */
export function wireFileDrop(zone, onFiles, hot = '#3b82f6') {
    const base = zone.style.borderColor;
    const on = (e) => { e.preventDefault(); zone.style.borderColor = hot; };
    const off = (e) => { e.preventDefault(); zone.style.borderColor = base; };
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, on));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, off));
    const drop = (e) => onFiles(e.dataTransfer.files);
    zone.addEventListener('drop', drop);
    return () => {
        ['dragenter', 'dragover'].forEach(ev => zone.removeEventListener(ev, on));
        ['dragleave', 'drop'].forEach(ev => zone.removeEventListener(ev, off));
        zone.removeEventListener('drop', drop);
    };
}
