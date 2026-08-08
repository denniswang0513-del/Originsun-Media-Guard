/**
 * chunked-upload.js — 上傳一個檔案，大到過不了中間那道牆就自動分塊。
 *
 * **為什麼**：對外流量走 Cloudflare，單一 HTTP 請求的 body 有 100MB 硬上限
 * （`proxyBodyLimit()` 的註解記著那次實測）。超過的檔在抵達伺服器**之前**就被
 * 擋掉，我們拿到的是一個沒有內容的失敗。同一個檔在辦公室傳得上去、在外面傳
 * 不上去 —— 這個模組就是為了那個差異存在的。後端落地層是 core/chunked_upload.py。
 *
 * **門檻不是寫死的常數，是 `proxyBodyLimit()`**：多大才需要分塊，取決於這條
 * 連線中間有沒有 CDN，只有瀏覽器知道自己連的是哪個網域。區網直連（回 0）就
 * 一律走單一請求 —— 分塊在那裡只是白白多幾十次來回。
 *
 * 續傳：`upload_id` 由後端從（token, 瀏覽器鍵, 檔名, 大小, 修改時間）推導，
 * 同一個檔重傳一定落在同一個半成品上。瀏覽器鍵存 localStorage，所以關掉分頁
 * 再回來按同一個檔，會從斷掉的地方接著傳。
 *
 * 兩個消費端：公開收照頁（frontend/media-log.html）與後台專案的影像紀錄分頁
 * （tabs/crm/crm-projects-media.js）。額外欄位走 `opts.fields`，不綁死在影像
 * 紀錄的欄位名 —— 下一個要用分塊的上傳面（提案資產夾）才不必再抄一份。
 */

import {
    authFetch, BODY_MARGIN, browserKey, httpError, proxyBodyLimit, uploadWithProgress,
} from './utils.js';

const CLIENT_KEY = 'media_log_client_key';
const RETRY_DELAYS = [1000, 2000, 4000];   // 每塊最多重試 3 次（弱網常態）

async function jsonOrThrow(res) {
    let payload = null;
    try { payload = await res.json(); } catch { /* 空 body / 非 JSON */ }
    if (!res.ok) throw httpError(res.status, payload);
    return payload;
}

/** 單一請求上傳（小檔 / 區網）。進度只有 XHR 給得出來，故走 uploadWithProgress。 */
function uploadWhole(base, file, opts) {
    const fd = new FormData();
    fd.append('file', file);
    for (const [k, v] of Object.entries(opts.fields || {})) fd.append(k, v);
    return uploadWithProgress(base + '/upload', fd, {
        signal: opts.signal,
        onProgress: (loaded, total) => opts.onProgress
            && opts.onProgress(total ? Math.round((loaded / total) * 100) : 0),
        onUploaded: () => opts.onProgress && opts.onProgress(100),
    });
}

/**
 * 傳一塊 → 回伺服器目前持有的 bytes。
 *
 * 409 不是失敗 —— 它是「你以為傳到 X、其實伺服器在 Y」（逾時重試時第一次
 * 其實寫進去了就會這樣）。把伺服器的真實值回給呼叫端重新對齊即可，重試次數
 * 也不該因此被消耗掉。
 */
async function putChunk(base, uploadId, offset, blob, signal) {
    const res = await fetch(`${base}/upload/${uploadId}/chunk?offset=${offset}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: blob,
        signal,
    });
    // body 只讀一次 —— 讀第二次會拿到已消耗的串流，錯誤訊息就這樣掉了
    const payload = await res.json().catch(() => null);
    const realigned = res.status === 409 && payload?.detail?.received;
    if (typeof realigned === 'number') return realigned;
    if (!res.ok) throw httpError(res.status, payload);
    return payload.received;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function uploadChunked(base, file, opts) {
    const begun = await jsonOrThrow(await authFetch(base + '/upload/begin', {
        method: 'POST',
        body: {
            filename: file.name,
            size: file.size,
            mtime: file.lastModified || 0,
            client_key: browserKey(CLIENT_KEY),
        },
    }));
    const { upload_id: uploadId, chunk_bytes: chunkBytes } = begun;
    let sent = begun.received || 0;

    // 續傳時一開始就顯示既有進度，不要從 0% 跳。99 封頂 —— 100% 留給 finish
    // （伺服器還要改名 + 抽縮圖，提早顯示 100% 會被當成卡住）。
    const report = () => opts.onProgress
        && opts.onProgress(Math.min(99, Math.round((sent / file.size) * 100)));
    report();

    while (sent < file.size) {
        const blob = file.slice(sent, Math.min(sent + chunkBytes, file.size));
        for (let attempt = 0; ; attempt++) {
            try {
                sent = await putChunk(base, uploadId, sent, blob, opts.signal);
                break;
            } catch (e) {
                // 4xx 是這個檔本身的問題（格式/太大/連結失效），重試幾次都一樣
                const fatal = e.status && e.status !== 429 && e.status < 500;
                if (fatal || e.name === 'AbortError' || attempt >= RETRY_DELAYS.length) throw e;
                await sleep(RETRY_DELAYS[attempt]);
            }
        }
        report();
    }

    const done = await jsonOrThrow(await authFetch(
        `${base}/upload/${uploadId}/finish`, {
            method: 'POST',
            body: { filename: file.name, size: file.size, ...(opts.fields || {}) },
        }));
    if (opts.onProgress) opts.onProgress(100);
    return done;
}

/**
 * 上傳一個檔 → 後端回的 FILE 物件。失敗 throw（Error 帶 `.status`，訊息已經是
 * 講人話的版本，呼叫端直接用 `uploadFailText(e)` 顯示即可）。
 *
 * @param {string} base   端點前綴，例 `/api/v1/crm/public/media-log/{token}`
 * @param {File}   file
 * @param {object} opts   { fields, onProgress(pct), signal, bodyLimit }
 *
 * `fields` 會同時進 multipart 表單（單一請求路徑）與 finish 的 JSON body
 * （分塊路徑），所以兩條路徑對後端長得一樣。
 *
 * `bodyLimit` 是伺服器量到的單一請求上限（GET 回應的 `request_body_limit`：
 * 它看 `cf-connecting-ip`，而那個回應與上傳走同一條連線）。沒給就退回
 * `proxyBodyLimit()` 用網域猜。0 = 中間沒東西擋 → 完全不分塊。
 *
 * 猜錯的代價不對稱：多分塊只是多幾十次來回，少分塊是根本傳不上去 —— 所以
 * 單一請求真的撞到**不是我們發出的** 413 時，自動改走分塊再試一次。
 */
export async function uploadFile(base, file, opts = {}) {
    const cap = opts.bodyLimit ?? proxyBodyLimit();
    if (cap && file.size > cap - BODY_MARGIN) return uploadChunked(base, file, opts);
    try {
        return await uploadWhole(base, file, opts);
    } catch (e) {
        // fromServer=false → 這個 413 是中間某層產生的（HTML 頁面，不是我們的
        // JSON），代表判斷失準、這條連線其實有上限。我們自己回的 413（超過
        // 500MB）帶 detail，重試幾次都一樣，不要浪費使用者的時間。
        if (e.status === 413 && !e.fromServer) return uploadChunked(base, file, opts);
        throw e;
    }
}
