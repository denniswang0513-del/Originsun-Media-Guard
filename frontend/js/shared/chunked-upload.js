/**
 * chunked-upload.js — 影像紀錄的上傳客戶端（小檔單一請求、大檔自動分塊）。
 *
 * **為什麼要分塊**：對外流量走 Cloudflare，單一 HTTP 請求的 body 有 100MB 硬
 * 上限。超過的檔在抵達伺服器**之前**就被擋掉，所以我們拿到的是一個沒有內容的
 * 失敗（不是後端回的錯誤訊息）。公司內網直連不經 CF，同一個檔在辦公室傳得上
 * 去、在外面傳不上去 —— 這個模組就是為了那個差異存在的。後端對應的落地層是
 * `core/chunked_upload.py`。
 *
 * 兩個消費端：公開收照頁（frontend/media-log.html，NAS 容器 serve）與後台專案
 * 的影像紀錄分頁（tabs/crm/crm-projects-media.js）。兩邊打的是同一組端點，所以
 * 上傳這件事只該有一份實作。
 *
 * 續傳：`upload_id` 由後端從（token, 瀏覽器鍵, 檔名, 大小, 修改時間）推導，
 * 同一個檔重傳一定落在同一個半成品上。瀏覽器鍵存 localStorage，所以關掉分頁
 * 再回來按同一個檔，會從斷掉的地方接著傳。
 */

const CLIENT_KEY = "media_log_client_key";
const RETRY_DELAYS = [1000, 2000, 4000];   // 每塊最多重試 3 次（弱網常態）

/** 這台瀏覽器的固定隨機鍵 —— 讓「同一個人的同一個檔」才共用半成品。 */
function clientKey() {
    let k = "";
    try {
        k = localStorage.getItem(CLIENT_KEY) || "";
        if (!k) {
            k = Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem(CLIENT_KEY, k);
        }
    } catch {
        k = "no-storage";       // 無痕模式等 —— 退化成「不跨頁續傳」，仍可上傳
    }
    return k;
}

/** 後端錯誤 → 人看得懂的 Error（帶 status / detail 供呼叫端分流）。 */
function httpError(status, payload) {
    const d = payload && payload.detail;
    const msg = (d && typeof d === "object" ? d.message : d) || `HTTP ${status}`;
    const e = new Error(msg);
    e.status = status;
    e.detail = d;
    return e;
}

async function jsonOrThrow(res) {
    let payload = null;
    try { payload = await res.json(); } catch { /* 空 body / 非 JSON */ }
    if (!res.ok) throw httpError(res.status, payload);
    return payload;
}

function postJson(url, body) {
    return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/** 單一請求上傳（小檔）。用 XHR 而非 fetch —— 只有 XHR 給得出上傳進度。 */
function uploadWhole(base, file, opts) {
    return new Promise((resolve, reject) => {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("category", opts.category || "");
        fd.append("uploader_name", opts.uploaderName || "");
        const xhr = new XMLHttpRequest();
        xhr.open("POST", base + "/upload");
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && opts.onProgress) {
                opts.onProgress(Math.round((e.loaded / e.total) * 100));
            }
        };
        xhr.onload = () => {
            let payload = null;
            try { payload = JSON.parse(xhr.responseText); } catch { /* 非 JSON */ }
            if (xhr.status >= 200 && xhr.status < 300) {
                if (opts.onProgress) opts.onProgress(100);
                resolve(payload);
            } else {
                reject(httpError(xhr.status, payload));
            }
        };
        xhr.onerror = () => reject(new Error("網路錯誤"));
        xhr.send(fd);
    });
}

/**
 * 傳一塊 → 回伺服器目前持有的 bytes。
 *
 * 409 不是失敗 —— 它是「你以為傳到 X、其實伺服器在 Y」（逾時重試時第一次
 * 其實寫進去了就會這樣）。把伺服器的真實值回給呼叫端重新對齊即可，重試次數
 * 也不該因此被消耗掉。
 */
async function putChunk(base, uploadId, offset, blob) {
    const url = `${base}/upload/${uploadId}/chunk?offset=${offset}`;
    const res = await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/octet-stream" },
        body: blob,
    });
    if (res.status === 409) {
        const payload = await res.json().catch(() => null);
        const received = payload && payload.detail && payload.detail.received;
        if (typeof received === "number") return received;
    }
    const out = await jsonOrThrow(res);
    return out.received;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function uploadChunked(base, file, opts) {
    const begun = await jsonOrThrow(await postJson(base + "/upload/begin", {
        filename: file.name,
        size: file.size,
        mtime: file.lastModified || 0,
        client_key: clientKey(),
    }));
    const uploadId = begun.upload_id;
    const chunkBytes = begun.chunk_bytes || opts.chunkBytes || 8 * 1024 * 1024;
    let sent = begun.received || 0;

    const report = () => {
        if (opts.onProgress) {
            opts.onProgress(Math.min(99, Math.round((sent / file.size) * 100)));
        }
    };
    report();       // 續傳時一開始就顯示既有進度，不要從 0% 跳

    while (sent < file.size) {
        const blob = file.slice(sent, Math.min(sent + chunkBytes, file.size));
        let lastErr = null;
        for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt++) {
            try {
                sent = await putChunk(base, uploadId, sent, blob);
                lastErr = null;
                break;
            } catch (e) {
                // 400/413 是這個檔本身的問題，重試幾次都一樣 → 直接放棄
                if (e.status && e.status !== 429 && e.status < 500) throw e;
                lastErr = e;
                if (attempt < RETRY_DELAYS.length) await sleep(RETRY_DELAYS[attempt]);
            }
        }
        if (lastErr) throw lastErr;
        report();
    }

    const done = await jsonOrThrow(await postJson(
        `${base}/upload/${uploadId}/finish`, {
            filename: file.name,
            size: file.size,
            category: opts.category || "",
            uploader_name: opts.uploaderName || "",
        }));
    if (opts.onProgress) opts.onProgress(100);
    return done;
}

/**
 * 上傳一個檔 → 後端回的 FILE 物件。失敗 throw（Error 帶 `.status`）。
 *
 * @param {string} base   `/api/v1/crm/public/media-log/{token}`
 * @param {File}   file
 * @param {object} opts   { category, uploaderName, onProgress(pct),
 *                          thresholdBytes, chunkBytes }
 *
 * 門檻與塊大小由後端 GET 下發（`chunk_threshold_bytes` / `chunk_bytes`）；
 * 沒帶就用這裡的預設，兩邊對不上時**寧可分塊**（分塊在小檔上只是多幾次來回，
 * 而不分塊在大檔上是直接失敗）。
 */
export function uploadFile(base, file, opts = {}) {
    const threshold = opts.thresholdBytes || 64 * 1024 * 1024;
    return file.size > threshold
        ? uploadChunked(base, file, opts)
        : uploadWhole(base, file, opts);
}
