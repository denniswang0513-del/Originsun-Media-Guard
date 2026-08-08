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

import { authFetch, httpError, proxyBodyLimit, uploadWithProgress } from './utils.js';

const CLIENT_KEY = 'media_log_client_key';
const RETRY_DELAYS = [1000, 2000, 4000];   // 每塊最多重試 3 次（弱網常態）
// 留餘裕給 multipart 的邊界字串與標頭（實際 body 會比檔案本身大一點）——
// 與 utils.js 的 uploadItems 分批預算同一個理由、同一個數字。
const BODY_MARGIN = 4 * 1024 * 1024;

/** 這台瀏覽器的固定隨機鍵 —— 讓「同一個人的同一個檔」才共用半成品。 */
function clientKey() {
    try {
        let k = localStorage.getItem(CLIENT_KEY);
        if (!k) {
            k = Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem(CLIENT_KEY, k);
        }
        return k;
    } catch {
        return 'no-storage';    // 無痕模式 → 退化成「不跨分頁續傳」，仍可上傳
    }
}

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
    if (res.status === 409) {
        const payload = await res.json().catch(() => null);
        const received = payload && payload.detail && payload.detail.received;
        if (typeof received === 'number') return received;
    }
    return (await jsonOrThrow(res)).received;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function uploadChunked(base, file, opts) {
    const begun = await jsonOrThrow(await authFetch(base + '/upload/begin', {
        method: 'POST',
        body: {
            filename: file.name,
            size: file.size,
            mtime: file.lastModified || 0,
            client_key: clientKey(),
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
 * @param {object} opts   { fields, onProgress(pct), signal, thresholdBytes }
 *
 * `fields` 會同時進 multipart 表單（單一請求路徑）與 finish 的 JSON body
 * （分塊路徑），所以兩條路徑對後端長得一樣。
 * `thresholdBytes` 只在需要覆寫連線判定時才給（測試用）。
 */
export function uploadFile(base, file, opts = {}) {
    const cap = opts.thresholdBytes ?? proxyBodyLimit();
    const threshold = cap ? cap - BODY_MARGIN : Infinity;   // 0 = 區網，不必分塊
    return file.size > threshold
        ? uploadChunked(base, file, opts)
        : uploadWhole(base, file, opts);
}
