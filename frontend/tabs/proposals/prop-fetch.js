/**
 * prop-fetch.js — 提案模組共用 fetcher（proposals.js + plan-matrix.js 同用一份）。
 *
 * 比一般 tfetch 多帶 err.status / err.detail — plan-matrix 的 409 衝突 UI
 * 靠它們分流（曾因兩份 fetcher 分岔，SPA 路徑的衝突 UI 整個死路）。
 * 無 SPA 依賴：未來 /proposal-plan.html 獨立頁也 import 這份。
 */

export async function tfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Accept': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}) };
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
