// utils.js
// Shared utilities across all tabs

// clip_utils 是 leaf（自己 0 個 import）→ 這條相依不會造成循環。
// fmtSize 其實是通用格式化，只是當年落在那支檔案裡；不為了它再寫第二份。
import { fmtSize } from './clip_utils.js';

export function getComputeBaseUrl() {
    const mode = document.getElementById('compute_mode')?.value;
    return (mode === 'local' && window.localAgentActive) ? 'http://127.0.0.1:8000' : '';
}

export function getAgentBaseUrl() {
    // Dynamically use the current origin so it works via IP or localhost
    return window.location.origin;
}

// 帶 Bearer token 的 JSON fetch（body 傳物件自動 stringify）。
// hr_leave（hfetch）/timesheets（tfetch）/my.html（mfetch）各有同形本地版——
// 新分頁一律 import 這份；舊檔待翻修時收斂，勿再複製第 N 份。
export function authFetch(path, opts = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    const tok = localStorage.getItem('auth_token');
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    return fetch(path, Object.assign({}, opts, { headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined }));
}

/**
 * 帶權限下載（**單一正本**）—— `<a href>` 送不了 Authorization header，
 * 所以 fetch 成 blob 再觸發。失敗自己 alert，呼叫端一行就夠。
 * 檔名取路徑尾段，正反斜線都吃（NAS 路徑是反斜線）。
 */
export async function authDownload(url, filename, label = '下載') {
    try {
        const tok = localStorage.getItem('auth_token');
        const r = await fetch(url, { headers: tok ? { Authorization: 'Bearer ' + tok } : {} });
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
 * `<input type=file>` 選到的檔 → 上傳項目 `[{file, path}]`。
 * `path` 是相對路徑：選資料夾時（input 帶 webkitdirectory）瀏覽器會給
 * `webkitRelativePath`＝「資料夾名/子層/檔名」，正好就是我們要送的形式。
 */
export function inputUploadItems(fileList) {
    return [...(fileList || [])].map(f => ({ file: f, path: f.webkitRelativePath || f.name }));
}

/**
 * 拖放的 DataTransfer → 上傳項目 `[{file, path}]`，**資料夾會遞迴展開**
 * 且路徑含被拖進來的那層資料夾名（owner 2026-08-08：拖一個夾進去要連夾一起）。
 *
 * 🔴 `webkitGetAsEntry()` 必須在**事件當下同步**取完 —— DataTransfer 在
 * handler 回傳後就被清空，先 await 再讀會拿到一堆 null。所以先同步蒐集
 * entries，之後才慢慢遞迴。
 * 舊瀏覽器沒有這個 API → 退回 `dt.files`（平放），不會壞掉只是沒有結構。
 */
export async function dropUploadItems(dt) {
    const entries = [...(dt.items || [])]
        .map(it => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
        .filter(Boolean);
    if (!entries.length) return inputUploadItems(dt.files);

    const out = [];
    const readDir = (reader) => new Promise((res, rej) => {
        // readEntries 一次最多回 100 筆，要一直讀到回空陣列為止 ——
        // 只讀一次的話大資料夾會安靜地只上傳前 100 個檔
        const all = [];
        const step = () => reader.readEntries(
            (batch) => (batch.length ? (all.push(...batch), step()) : res(all)), rej);
        step();
    });
    const walk = async (entry, prefix) => {
        if (entry.isFile) {
            const file = await new Promise((res, rej) => entry.file(res, rej));
            out.push({ file, path: prefix + entry.name });
            return;
        }
        for (const child of await readDir(entry.createReader())) {
            await walk(child, prefix + entry.name + '/');
        }
    };
    for (const e of entries) {
        try { await walk(e, ''); } catch (_) { /* 單一項失敗不拖垮整批 */ }
    }
    return out;
}

/**
 * 自帶樣式的元件用：同一個 id 只注入一次。
 *
 * 「查 id → createElement → 設 id → textContent → appendChild」這串樣板在專案裡
 * 被逐字複製了十幾份。新元件一律呼叫這支；舊的遇到就順手換過來
 * （`grep -rn "createElement('style')" frontend` 看還剩哪些）。
 *
 * ⚠️ **會反覆改寫內容的動態 stylesheet 不適用**（例如 website/subviews/works.js
 * 的欄位顯示切換：它每次都重寫 textContent）。這支第一行就 early-return，
 * 換過去只有第一次會生效，之後靜默失效。
 */
export function ensureStyle(id, css) {
    if (document.getElementById(id)) return;
    const st = document.createElement('style');
    st.id = id;
    st.textContent = css;
    document.head.appendChild(st);
}

/**
 * 非同步 checkbox 的一次切換：鎖住 → 等 → 失敗還原並說明 —— 畫面不說謊。
 *
 * 單獨開出來是因為列表很長時要走**事件委派**（不可能每列綁監聽），那條路
 * 用不到 wireAsyncToggle，只需要這段語意。
 */
export async function runToggle(input, fn, errPrefix) {
    const want = input.checked;
    input.disabled = true;
    try {
        await fn(want);
    } catch (err) {
        input.checked = !want;
        alert(errPrefix + '：' + ((err && err.message) || err));
    }
    // 成功時呼叫端通常已把整塊重畫，這個節點已不在畫面上 —— isConnected
    // 擋掉對已卸下節點的無謂寫入
    if (input.isConnected) input.disabled = false;
}

/** runToggle 綁在單一 checkbox 上（列數少、直接綁得起的場合）。 */
export function wireAsyncToggle(input, fn, errPrefix) {
    input.addEventListener('change', () => runToggle(input, fn, errPrefix));
}

/**
 * 拖放上傳區：進入時高亮、放開送檔。`onItems([{file, path}])` 決定怎麼送
 * （拖資料夾進來時 path 帶著目錄結構，見 dropUploadItems）。
 *
 * 高亮走 class + 自帶樣式，不動 inline style —— 直接寫 `zone.style.borderColor`
 * 會猜錯對方用邊框還是底色，還會把人家原本的 border-top 一起清掉。
 */
const _DROP_HOT = 'osun-drop-hot';
export function wireFileDrop(zone, onItems) {
    ensureStyle('osun-drop-style',
        `.${_DROP_HOT}{outline:2px dashed #3b82f6;outline-offset:-2px;`
        + 'background:rgba(59,130,246,.06);}');
    const on = (e) => { e.preventDefault(); zone.classList.add(_DROP_HOT); };
    const off = (e) => { e.preventDefault(); zone.classList.remove(_DROP_HOT); };
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, on));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, off));
    zone.addEventListener('drop', async (e) => {
        const items = await dropUploadItems(e.dataTransfer);   // 內部先同步取 entries
        if (items.length) onItems(items);
    });
}

/**
 * 上傳項目 → FormData（`files` 與 `paths` 一組一組成對 append，同序）。
 * 後端 save_uploads 靠 index 對應，所以**兩個欄位必須在同一個迴圈裡加**。
 */
export function uploadFormData(items) {
    const fd = new FormData();
    for (const it of items) {
        fd.append('files', it.file);
        fd.append('paths', it.path || it.file.name);
    }
    return fd;
}

/** 只有授權標頭（送 FormData 時不能有 Content-Type，boundary 會被蓋掉）。 */
export function bearerHeader() {
    const tok = localStorage.getItem('auth_token');
    return tok ? { Authorization: 'Bearer ' + tok } : {};
}

// HTTP 狀態 → 「為什麼會這樣 + 你現在該做什麼」。
// 🔴 錯誤訊息不是給工程師看的。「HTTP 413」對使用者等於沒說話 —— 他不知道
// 是自己做錯什麼、還是系統壞了、還是該換個方式再試一次。每一條都要能回答
// 這兩個問題，答不出來就別假裝知道（見最後的 fallback）。
const _HTTP_WHY = {
    401: () => '登入已經過期了。請重新整理這個頁面、登入之後再傳一次 —— '
             + '檔案沒有上傳，重來即可。',
    403: () => '你的帳號沒有這個資料夾的上傳權限。請找管理員把「專案管理」或'
             + '「提案庫」模組開給你。',
    404: () => '找不到要上傳的資料夾 —— 它可能剛被改名或刪除了。'
             + '請重新整理頁面，確認資料夾還在再傳一次。',
    413: () => {
        const cap = proxyBodyLimit();
        return cap
            ? `檔案太大，被連線中途的 CDN 擋掉了（單一請求上限 ${fmtSize(cap)}），`
            + '請求根本沒送到伺服器。\n\n你可以這樣做：\n'
            + '① 多個檔案 → 系統已經會自動分批，直接重試即可；\n'
            + '② 單一檔案就超過這個大小 → 分批切不開一個檔，'
            + '請到公司區網用 http://192.168.1.107:8000 上傳；\n'
            + '③ 影片類的大檔 → 先壓縮或改傳 Proxy 檔。'
            : '檔案太大，被伺服器前的連線層擋下了。請改用較小的檔案，'
            + '或分次上傳。';
    },
    500: () => '伺服器處理的時候出錯了（不是你的問題）。請重試一次；'
             + '再失敗的話把這個畫面截圖給管理員。',
    502: () => '伺服器沒有回應 —— 通常是正在重新啟動。等 1 分鐘後再傳一次。',
    503: () => '伺服器暫時無法服務 —— 通常是正在重新啟動或更新。'
             + '等 1 分鐘後再傳一次。',
    504: () => '伺服器回應逾時。大檔案走外網容易這樣 —— '
             + '請到公司區網上傳，或把檔案拆小。',
    507: () => '伺服器空間不足，寫不進去。請通知管理員清理 NAS 空間。',
};

/**
 * HTTP 錯誤 → 帶得動「原因 + 怎麼辦」的 Error（`.status` 保留原始狀態碼）。
 *
 * 優先用伺服器回的 `detail` —— 那是**最準**的，因為它知道當下的實際情況
 * （哪個檔、超過多少）。沒有 detail 就表示回應不是我們送的（多半是中途的
 * 代理層），這時才查表。
 */
export function httpError(status, data) {
    const d = data && data.detail;
    const detail = typeof d === 'string' ? d : (d && d.reason) || '';
    const why = _HTTP_WHY[status];
    const msg = detail || (why && why())
        || `伺服器回應 HTTP ${status}，而且這不是本系統送出的訊息 —— `
         + '通常是中途的連線層（VPN／防火牆／CDN）擋下的。'
         + '請改用公司區網再試一次，或把這個代碼告訴管理員。';
    return Object.assign(new Error(msg), { status, fromServer: !!detail });
}

/**
 * 帶進度的上傳 → Promise<回應 JSON>。
 *
 * 🔴 這裡**只能**用 XMLHttpRequest：`fetch()` 沒有上傳進度事件（它的
 * ReadableStream 上傳在瀏覽器支援度上還不能靠）。整份專案其他地方都走 fetch，
 * 這是唯一的例外，理由就是進度條 —— 提案檔動輒上百 MB，沒有進度就是盯著
 * 畫面猜還要多久。
 *
 * `signal`（AbortSignal）可中止；中止時 reject 的錯誤帶 `aborted: true`，
 * 呼叫端據此不要跳錯誤視窗。
 *
 * 🔴 **取消只在位元組送完之前有意義**。送完之後伺服器就會把檔案寫進去，
 * 這時候中斷連線只是自己不聽回應而已 —— 檔案還是在的。所以 `onUploaded`
 * （body 送完那一刻）一觸發，呼叫端就該把取消鈕收掉，不要讓 UI 說謊。
 * 真正在傳輸途中中斷 → multipart 解析失敗 → 處理函式根本不會執行，不留殘檔。
 *
 * onProgress 與 onUploaded 分開兩個訊號，是因為快速連線上第一個 progress
 * 事件就已經 loaded===total —— 用「loaded>=total 就當成傳完」會讓進度條
 * 一次都沒顯示過百分比。
 */
export function uploadWithProgress(url, formData,
                                   { headers = {}, onProgress, onUploaded, signal } = {}) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        // 不設 Content-Type —— multipart boundary 要讓瀏覽器自己產生
        for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
        xhr.upload.addEventListener('progress', (e) => {
            if (onProgress) onProgress(e.loaded, e.lengthComputable ? e.total : 0);
        });
        xhr.upload.addEventListener('load', () => { if (onUploaded) onUploaded(); });
        xhr.addEventListener('load', () => {
            let data = null;
            try { data = JSON.parse(xhr.responseText || ''); } catch (_) { /* 非 JSON */ }
            if (xhr.status >= 200 && xhr.status < 300) return resolve(data || {});
            reject(httpError(xhr.status, data));
        });
        xhr.addEventListener('error', () => reject(Object.assign(
            new Error('連線中斷了 —— 網路斷線或伺服器沒回應，這批檔案沒有上傳。'
                      + '確認網路後可以直接重試。'),
            { status: 0 })));
        xhr.addEventListener('abort', () =>
            reject(Object.assign(new Error('已取消上傳'), { aborted: true })));
        if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true });
        xhr.send(formData);
    });
}

/**
 * 這台瀏覽器的固定隨機識別字串（localStorage），沒有就產一個。
 *
 * 用途是「認得同一台裝置」而不是認人：公開共編頁拿它當「只刪自己貼的」憑證，
 * 分塊上傳拿它區分「同一個人重傳同一個檔」（→ 續傳）與「兩個人剛好傳同名同
 * 大小的檔」（→ 不可以共用半成品）。
 *
 * `storageKey` 要傳 —— 各用途分開存，換一個用途不會影響到另一個既有的身分。
 * 無痕模式/停用儲存 → 回一個固定字串，功能降級成「不跨分頁記得」而不是壞掉。
 */
export function browserKey(storageKey) {
    try {
        let k = localStorage.getItem(storageKey);
        if (!k) {
            k = (crypto.randomUUID ? crypto.randomUUID()
                                   : Math.random().toString(36).slice(2) + Date.now().toString(36))
                .replace(/-/g, '');
            localStorage.setItem(storageKey, k);
        }
        return k;
    } catch {
        return 'no-storage';
    }
}

// 單一請求 body 的預留餘裕：multipart 的邊界字串與標頭讓實際 body 比檔案本身大
// 一點，貼著上限送會被擋。uploadItems（分批）與 chunked-upload（分塊）共用。
export const BODY_MARGIN = 4 * 1024 * 1024;

/**
 * 這條連線的**單一請求** body 上限（0 = 沒有代理層限制）。
 *
 * 🔴 實測（2026-08-08）：走 foundry 隧道時 **Cloudflare 在 100MB 就回 413**，
 * 而且是 text/html 不是我們的 JSON —— 請求根本沒到伺服器。區網直連 8000
 * 傳 110MB 完全沒事。這不是我們能在後端調的，是 CDN 方案的硬限制。
 * 判準用 hostname：私有網段 = 直連，其餘一律當成走隧道。
 */
export function proxyBodyLimit() {
    const h = location.hostname;
    const lan = h === 'localhost' || h === '127.0.0.1'
        || /^192\.168\./.test(h) || /^10\./.test(h)
        || /^172\.(1[6-9]|2\d|3[01])\./.test(h);
    return lan ? 0 : 100 * 1024 * 1024;
}

/**
 * 上傳一批項目 → `{saved, skipped, …最後一批的回應}`。
 *
 * **自動分批**：拖一整個資料夾是**一個**請求，20 個 6MB 的檔加起來就會撞到
 * 上面那道 100MB 的牆 —— 所以照累計大小切成多個請求依序送，進度條把它們
 * 當成一件事回報。
 *
 * **單一檔案本身就超過上限** → 分批切不開它，改走**分塊上傳**
 * （`chunked-upload.js`，端點是 `{url}/begin|{id}/chunk|{id}/finish`）。
 * 後端沒有那組端點就會 404 → 那個檔進 skipped 並講清楚該怎麼辦，行為與從前相同。
 */
export async function uploadItems(url, items, opts = {}) {
    const { headers, onProgress, onUploaded, signal, limit } = opts;
    const cap = limit ?? proxyBodyLimit();
    const budget = cap ? cap - BODY_MARGIN : 0;
    const sizeOf = (it) => (it.file && it.file.size) || 0;

    const batches = [];
    const skipped = [];
    const oversized = [];          // 分批切不開 → 走分塊
    let cur = [];
    let curSize = 0;
    for (const it of items) {
        const sz = sizeOf(it);
        if (budget && sz > budget) {
            oversized.push(it);
            continue;
        }
        if (budget && cur.length && curSize + sz > budget) {
            batches.push(cur); cur = []; curSize = 0;
        }
        cur.push(it); curSize += sz;
    }
    if (cur.length) batches.push(cur);

    const batchBytes = batches.map(b => b.reduce((s, it) => s + sizeOf(it), 0));
    const overBytes = oversized.reduce((s, it) => s + sizeOf(it), 0);
    const total = batchBytes.reduce((a, b) => a + b, 0) + overBytes;
    const saved = [];
    let done = 0;
    let last = null;

    // 超大單檔先走分塊（一個一個來）—— 它們的進度也算進同一條進度條裡
    if (oversized.length) {
        const { uploadChunked } = await import('./chunked-upload.js');
        for (const it of oversized) {
            const base = done;
            try {
                const d = await uploadChunked(url, it.file, {
                    signal,
                    fields: { path: it.path || it.file.name },
                    onProgress: (pct) => onProgress
                        && onProgress(base + (pct / 100) * sizeOf(it), total),
                });
                saved.push(...(d.saved || []));
                last = d;
            } catch (e) {
                skipped.push({
                    filename: it.path || it.file.name,
                    reason: `單檔 ${fmtSize(sizeOf(it))} 超過這條連線的 ${fmtSize(cap)} 上限，`
                        + `改用分塊上傳也失敗了（${(e && e.message) || e}）`,
                });
            }
            done += sizeOf(it);
        }
    }
    for (let i = 0; i < batches.length; i++) {
        const d = await uploadWithProgress(url, uploadFormData(batches[i]), {
            headers,
            signal,
            // 每批各自 0→100%，對外換算成整體進度（總量用檔案大小估，夠準）
            onProgress: (l, t) => onProgress && onProgress(
                Math.min(total, done + (t ? (l / t) * batchBytes[i] : 0)), total),
            // 只有最後一批傳完才算「等伺服器」—— 中間批之後還要繼續傳
            onUploaded: () => { if (i === batches.length - 1 && onUploaded) onUploaded(); },
        });
        done += batchBytes[i];
        saved.push(...(d.saved || []));
        skipped.push(...(d.skipped || []));
        last = d;
    }
    return { ...(last || {}), saved, skipped, batches: batches.length };
}

/** 上傳失敗要顯示的整段文字（三個入口共用，措辭不會各自漂）。 */
export function uploadFailText(e) {
    if (e && e.aborted) return '已取消上傳 —— 沒有東西被寫進資料夾。';
    return '上傳失敗 —— ' + ((e && e.message) || String(e));
}

/**
 * 上傳進度條（自帶樣式，吃呼叫端的 CSS 變數 → 深色 SPA 與白底公開頁都能用）。
 * 插在 `host` 最前面，回傳操作把手。給 `onCancel` 才長出取消鈕。
 *
 * `finishing()` 是必要的一段：檔案傳完 100% 之後伺服器還在寫 NAS，
 * 進度條卡在 100% 不動會被當成當掉 —— 明講「伺服器處理中」。
 */
export function uploadProgress(host, onCancel) {
    ensureStyle('osun-prog-style', `
.osun-prog{display:flex;align-items:center;gap:10px;padding:8px 10px;font-size:12px;
  border:1px solid var(--line,#3a3a3a);border-radius:3px;margin-bottom:8px;}
.osun-prog-bar{flex:1;height:6px;border-radius:3px;background:rgba(127,127,127,.25);overflow:hidden;}
.osun-prog-bar>i{display:block;height:100%;width:0;background:#3b82f6;transition:width .15s;}
.osun-prog-txt{white-space:nowrap;color:var(--sub,#8b8b8b);}
/* 失敗：訊息有好幾行（原因＋怎麼辦）→ 攤成區塊、保留換行、進度條退居細線 */
.osun-prog.err{display:block;border-color:#f87171;}
.osun-prog.err .osun-prog-bar{height:3px;margin-bottom:8px;}
.osun-prog.err .osun-prog-bar>i{background:#f87171;}
.osun-prog.err .osun-prog-txt{display:block;white-space:pre-line;line-height:1.75;
  color:#f87171;margin-bottom:8px;}
.osun-prog-x{background:none;border:1px solid var(--line,#3a3a3a);color:var(--sub,#8b8b8b);
  cursor:pointer;font:inherit;font-size:11px;padding:2px 8px;border-radius:2px;}
.osun-prog-x:hover{color:#f87171;border-color:#f87171;}`);
    const wrap = document.createElement('div');
    wrap.className = 'osun-prog';
    wrap.innerHTML = '<div class="osun-prog-bar"><i></i></div>'
        + '<span class="osun-prog-txt">準備上傳…</span>'
        + (onCancel ? '<button class="osun-prog-x" type="button">取消</button>' : '');
    host.prepend(wrap);
    const fill = wrap.querySelector('i');
    const txt = wrap.querySelector('.osun-prog-txt');
    if (onCancel) wrap.querySelector('.osun-prog-x').addEventListener('click', onCancel);
    return {
        update(loaded, total) {
            if (!total) { txt.textContent = `上傳中… ${fmtSize(loaded)}`; return; }
            const pct = Math.min(100, Math.round(loaded / total * 100));
            fill.style.width = pct + '%';
            txt.textContent = `${pct}%（${fmtSize(loaded)} / ${fmtSize(total)}）`;
        },
        finishing(note) {
            fill.style.width = '100%';
            txt.textContent = note || '伺服器處理中…';
            const x = wrap.querySelector('.osun-prog-x');
            if (x) x.remove();          // 已經傳完，取消沒有意義了
        },
        // 失敗訊息會有好幾行（原因 + 該怎麼做）→ 換成可讀的區塊，
        // 而且**不自動消失**：看不完就沒了等於沒說。要使用者自己關掉。
        fail(msg) {
            wrap.classList.add('err');
            fill.style.width = '100%';
            txt.textContent = msg;
            const x = wrap.querySelector('.osun-prog-x');
            if (x) {
                x.textContent = '知道了';
                x.replaceWith(x.cloneNode(true));            // 清掉原本的取消 handler
                wrap.querySelector('.osun-prog-x')
                    .addEventListener('click', () => wrap.remove());
            }
        },
        remove() { wrap.remove(); },
    };
}

/**
 * HTML escape。這裡是**不依賴任何模組**的那一份 —— 公開頁（訪客、未登入）
 * 也 import 得起，不會像 tabs/crm/crm-utils.js 那樣把 CRM state 一起拖進來。
 */
export function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** textarea 隨內容長高（專案裡已有四份手抄，新程式一律用這支）。 */
export function autoGrow(el) {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
}

/**
 * 動態 import + 失敗重試。
 *
 * 🔴 ES module map 會**永久快取 rejected import** —— 一次網路抖動之後，用原路徑
 * 重 import 只會拿回同一個 rejected promise，那個分頁到重整為止都修不好。
 * 重試必須帶 cache-bust query（crm-projects-plan.js / subview-loader.js 同款教訓，
 * 那裡各自手寫了一份旗標；新程式一律用這支）。
 *
 * ⚠️ **path 必須是絕對路徑**（`/tabs/...`）：`import()` 以「呼叫它的那支模組」
 * 為基準解析，而這裡的呼叫者是 utils.js —— 傳 `./foo.js` 會去找
 * `/js/shared/foo.js`。
 */
const _importFailed = new Set();
export async function importRetry(path) {
    try {
        const mod = await import(_importFailed.has(path) ? `${path}?t=${Date.now()}` : path);
        _importFailed.delete(path);
        return mod;
    } catch (e) {
        _importFailed.add(path);
        throw e;
    }
}

/**
 * 直接用 `<a download>` 觸發下載（**不經 blob**）。
 *
 * 與 authDownload 的分工：這支給**免授權**的網址（token 端點、靜態檔）——
 * 瀏覽器邊下載邊寫檔，幾百 MB 的提案影片也不會把分頁記憶體吃爆；
 * authDownload 是給要帶 Authorization header 的，代價是整包進記憶體。
 */
export function plainDownload(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.download = String(filename || '').split(/[\\/]/).pop() || '';
    document.body.appendChild(a);
    a.click();
    a.remove();
}

/**
 * 複製文字到剪貼簿 → 真的複製成功才 true。傳 btn 會順手做「已複製」的回饋。
 *
 * 🔴 本系統多半是從 http://192.168.1.x 連進來的 —— **非安全內容**，
 * `navigator.clipboard` 根本不存在。少了 fallback，「複製」在同事的電腦上
 * 就是一顆按了沒反應的按鈕。三層：clipboard API → textarea + execCommand
 * （老招，非安全內容下**真的複製得到**）→ prompt 讓使用者自己 Ctrl+C。
 *
 * 新程式一律 import 這份。舊檔還有 5 份同形碼（api-keys / portal / social /
 * report / showcase-edit），待各自翻修時收斂 —— **勿再複製第 N 份**。
 */
export async function copyText(text, btn) {
    const flash = () => {
        if (!btn) return;
        const t = btn.textContent;
        btn.textContent = '已複製';
        setTimeout(() => { btn.textContent = t; }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            flash();
            return true;
        } catch (_) { /* 權限被擋 → 往下退 */ }
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0;';
    document.body.appendChild(ta);
    try {
        ta.select();
        if (document.execCommand('copy')) {
            flash();
            return true;
        }
    } catch (_) { /* 也不行就讓使用者自己來 */ } finally {
        ta.remove();
    }
    prompt('Ctrl+C 複製：', text);
    return false;
}

/**
 * 資料夾麵包屑：`"a/b/c"` → `[{label:'a',rel:'a'},{label:'b',rel:'a/b'},…]`。
 * 後端回的 rel 一律是 `/` 分隔、相對資料夾根。（消費端要的都是 HTML，
 * 走底下的 folderCrumbsHtml；這支是它的內部拆解。）
 */
function folderCrumbs(rel) {
    const parts = String(rel || '').split('/').filter(Boolean);
    return parts.map((label, i) => ({ label, rel: parts.slice(0, i + 1).join('/') }));
}

/**
 * 麵包屑的 HTML（**單一正本**）—— 兩個消費端的點擊都委派在 `[data-crumb]` 上，
 * 屬性契約與配色分兩份寫必定會漂。外層容器與「最外層要不要顯示」由呼叫端決定。
 * `rootLabel` = 資料夾根的顯示名（點它回最外層）。
 */
export function folderCrumbsHtml(rootLabel, rel) {
    const e = esc;
    const crumbs = folderCrumbs(rel);
    const span = (r, label, link) => `<span data-crumb="${e(r)}" style="cursor:pointer;color:${
        link ? '#60a5fa' : '#8b8b8b'};">${e(label)}</span>`;
    return span('', rootLabel, crumbs.length)
        + crumbs.map((c, i) => ' <span style="color:#4b4b4b;">/</span> '
            + span(c.rel, c.label, i < crumbs.length - 1)).join('');
}

export async function resolveDropPath(e, file, index = 0) {
    // 方法1：text/uri-list（RFC 2483，CRLF 分隔）
    const uriList = e.dataTransfer.getData('text/uri-list');
    if (uriList) {
        const uris = uriList.split(/\r?\n/).map(u => u.trim()).filter(u => u && !u.startsWith('#'));
        const uri = uris[index] || uris[0];
        if (uri && uri.toLowerCase().startsWith('file:')) {
            return decodeURIComponent(uri)
                .replace(/^file:\/\/\/([A-Za-z]:)/i, '$1')
                .replace(/^file:\/\//i, '\\\\')
                .replace(/\//g, '\\');
        }
    }

    // 方法2：text/plain
    const textRaw = e.dataTransfer.getData('text');
    if (textRaw && (textRaw.match(/^[A-Za-z]:\\/) || textRaw.startsWith('\\\\'))) {
        const lines = textRaw.split(/\r?\n/).filter(l => l.trim());
        if (lines.length > index) return lines[index].trim();
        return textRaw.trim();
    }

    // 方法3：Electron file.path
    if (file && file.path) return file.path;

    // 方法4：後端智慧深度解析
    if (file) {
        try {
            const res = await fetch('/api/v1/utils/resolve_drop?name=' + encodeURIComponent(file.name));
            if (res.ok) { const d = await res.json(); return d.path || file.name; }
        } catch { }
        return file.name;
    }
    return '';
}

export function appendLog(msg, type = 'info') {
    const terminal = document.getElementById('terminal');
    const terminalVerbose = document.getElementById('terminal_verbose');
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    const formattedText = `[${time}] ${msg}`;

    if (terminalVerbose) {
        const divRight = document.createElement('div');
        divRight.textContent = formattedText;
        if (type === 'error') divRight.className = 'text-red-400 mt-1 mb-1';
        else if (type === 'system') divRight.className = 'text-yellow-400 font-bold mt-1 mb-1';
        else divRight.className = 'text-gray-300 mb-0.5';
        terminalVerbose.prepend(divRight);
        terminalVerbose.scrollTop = 0;
    }

    // 左欄「任務摘要」：只顯示關鍵訊息（system + error）
    if (terminal && (type === 'error' || type === 'system')) {
        const divLeft = document.createElement('div');
        divLeft.textContent = formattedText;
        if (type === 'error') divLeft.className = 'text-red-400 mt-1 mb-1';
        else divLeft.className = 'text-yellow-400 font-bold mt-1 mb-1';
        terminal.prepend(divLeft);
        terminal.scrollTop = 0;
    }
}

export async function pickPath(inputId, type = 'folder') {
    const el = document.getElementById(inputId);
    if (!el) return;

    // External access (no LAN reach to Agent) → NAS browser modal
    if (window._isExternalAccess && typeof window.openNasBrowser === 'function') {
        const path = await window.openNasBrowser({
            title: type === 'folder' ? '選擇目錄' : '選擇檔案',
            initialPath: '',
            mode: type,
            showFiles: type === 'file'
        });
        if (path) {
            el.value = path;
            _autoFillCardName(el, inputId, path);
        }
        return;
    }

    // LAN access → native Windows picker via backend
    try {
        const endpoint = getAgentBaseUrl() + (type === 'folder' ? '/api/v1/utils/pick_folder' : '/api/v1/utils/pick_file');
        el.classList.add('animate-pulse', 'bg-blue-900', 'text-white');
        const res = await fetch(endpoint);
        const data = await res.json();
        el.classList.remove('animate-pulse', 'bg-blue-900', 'text-white');

        if (data.error === 'session_0') {
            alert(data.message || 'Master 跑在 Session 0,picker 無法顯示。');
            return;
        }

        if (data.path) {
            el.value = data.path;
            _autoFillCardName(el, inputId, data.path);
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }
    } catch (e) {
        console.error("Picker failed:", e);
        el.classList.remove('animate-pulse', 'bg-blue-900', 'text-white');
    }
}

function _autoFillCardName(el, inputId, path) {
    if (inputId.startsWith('src_path_')) {
        const row = el.closest('.flex');
        if (!row) return;
        const nameInput = row.querySelectorAll('input')[0];
        if (nameInput && (!nameInput.value.trim() || nameInput.value.startsWith('Card_'))) {
            const parts = path.replace(/\\/g, '/').split('/');
            nameInput.value = parts[parts.length - 1] || nameInput.value;
        }
    }
}

export function resetProgress() {
    // ── Backup TAB (four-segment) ──
    // 根據勾選狀態決定哪些段要顯示
    const _chkTrans = document.getElementById('chk_transcode')?.checked ?? false;
    const _chkConcat = document.getElementById('chk_concat')?.checked ?? false;
    const _chkReport = (document.getElementById('chk_report')?.checked ?? false) || !!window._backupReportPending;

    ['bk-seg-backup', 'bk-seg-trans', 'bk-seg-concat', 'bk-seg-report'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.width = '0%';
        // 根據勾選狀態顯示/隱藏
        if (id === 'bk-seg-trans') el.classList.toggle('hidden', !_chkTrans);
        else if (id === 'bk-seg-concat') el.classList.toggle('hidden', !_chkConcat);
        else if (id === 'bk-seg-report') el.classList.toggle('hidden', !_chkReport);
    });
    ['bk-lbl-backup', 'bk-lbl-trans', 'bk-lbl-concat', 'bk-lbl-report'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0%';
    });
    // 圖例也根據勾選狀態
    const legendContainer = document.querySelector('#bk-progress .flex.gap-4');
    if (legendContainer) {
        const legends = legendContainer.children;
        if (legends[1]) legends[1].classList.toggle('hidden', !_chkTrans);
        if (legends[2]) legends[2].classList.toggle('hidden', !_chkConcat);
    }
    document.getElementById('bk-legend-report')?.classList.toggle('hidden', !_chkReport);
    const bkLabel = document.getElementById('bk-prog-label');
    if (bkLabel) bkLabel.textContent = '進度：尚未開始';
    const bkEta = document.getElementById('bk-prog-eta');
    if (bkEta) bkEta.textContent = '';
    document.getElementById('bk-progress')?.classList.add('hidden');

    // ── Standalone TABs (single bar) ──
    ['vf', 'tc', 'ct'].forEach(prefix => {
        const bar = document.getElementById(prefix + '-prog-bar');
        if (bar) bar.style.width = '0%';
        const lbl = document.getElementById(prefix + '-prog-label');
        if (lbl) lbl.textContent = '進度：尚未開始';
        const eta = document.getElementById(prefix + '-prog-eta');
        if (eta) eta.textContent = '';
        const detail = document.getElementById(prefix + '-prog-detail');
        if (detail) detail.textContent = '';
        document.getElementById(prefix + '-progress')?.classList.add('hidden');
    });

    // ── Report TAB (four-segment) ──
    ['rp-seg-scan', 'rp-seg-meta', 'rp-seg-strip', 'rp-seg-render'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.width = '0%';
    });
    ['rp-lbl-scan', 'rp-lbl-meta', 'rp-lbl-strip', 'rp-lbl-render'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0%';
    });
    const rpLabel = document.getElementById('rp-prog-label');
    if (rpLabel) rpLabel.textContent = '備用中...';
    document.getElementById('rp-progress')?.classList.add('hidden');

    // ── Transcribe TAB ──
    const trBar = document.getElementById('transcribe_prog_bar');
    if (trBar) { trBar.style.width = '0%'; trBar.style.background = ''; }
    const trLabel = document.getElementById('transcribe_prog_label');
    if (trLabel) trLabel.textContent = '';
    const trPct = document.getElementById('transcribe_prog_pct');
    if (trPct) trPct.textContent = '0%';

    // ── TTS TABs ──
    ['tts_progress_area', 'tts_clone_progress_area'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
    ['tts_prog_bar', 'tts_clone_prog_bar'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.style.width = '0%'; el.style.background = ''; el.classList.remove('animate-pulse'); }
    });
    ['tts_prog_label', 'tts_clone_prog_label'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '';
    });
    ['tts_prog_pct', 'tts_clone_prog_pct'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0%';
    });

    // ── Re-enable all disabled submit buttons ──
    document.querySelectorAll('button.opacity-50.cursor-not-allowed').forEach(btn => {
        btn.disabled = false;
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
    });

    // ── Shared controls ──
    const btnReport = document.getElementById('btn_open_report');
    if (btnReport) btnReport.style.display = 'none';

    // ── Remote host progress ──
    ['bk', 'tc', 'ct'].forEach(p => document.getElementById(p + '-remote-hosts-progress')?.classList.add('hidden'));
    // 也隱藏所有「遠端主機進度」面板（各 TAB 共用 class）
    document.querySelectorAll('[id$="-remote-hosts-progress"]').forEach(el => el.classList.add('hidden'));
    if (window._heartbeatTimer) clearInterval(window._heartbeatTimer);
    window._remoteDispatching = false;

    // 不清除 _activeJobTab / _backupPipeline / _backupReportPending
    // — 備份流程中子任務（轉檔/串帶/報表）的 running 事件會觸發 resetProgress，
    //   但 pipeline 和 reportPending 在整個流程完成前都需要保留
    window._backupFinalShown = false;
}

// ── 共用主機選擇模組 ──

/**
 * 將 compute_hosts 渲染為 checkbox 到指定容器。
 * @param {string} containerId - 容器 DOM id
 * @param {object} [opts] - 選項
 * @param {boolean} [opts.includeLocal=true] - 是否包含「本機」選項
 * @param {boolean} [opts.localChecked=true] - 「本機」預設勾選
 * @param {string}  [opts.idPrefix] - checkbox id 前綴（避免多個選擇器 id 衝突），預設為 containerId
 */
// 格式化 IP：去掉 port（:8000）
function _displayIp(ip) {
    if (!ip || ip === 'local') return '';
    return ip.replace(/:\d+$/, '');
}

// 狀態燈 HTML
function _dotHtml(ip) {
    const dotId = 'host-dot-' + (ip || 'local').replace(/[.:]/g, '_');
    return `<span id="${dotId}" class="host-status-dot" style="width:8px;height:8px;border-radius:50%;display:inline-block;background:#555;flex-shrink:0;"></span>`;
}

// 偵測本機 IP（從 window.location 或 /api/v1/health）
let _localIp = null;
async function _detectLocalIp() {
    if (_localIp) return _localIp;
    const hostname = window.location.hostname;
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1') {
        _localIp = hostname;
        return _localIp;
    }
    try {
        const r = await fetch('/api/v1/health', { signal: AbortSignal.timeout(3000) });
        const d = await r.json();
        // 從 agents 中找匹配 hostname 的
        const hosts = window._computeHosts || [];
        for (const h of hosts) {
            try {
                const hr = await fetch('http://' + h.ip + '/api/v1/health', { signal: AbortSignal.timeout(2000) });
                const hd = await hr.json();
                if (hd.hostname === d.hostname) { _localIp = h.ip; return _localIp; }
            } catch {}
        }
    } catch {}
    _localIp = 'localhost';
    return _localIp;
}

// 判斷 agent IP 是否為本機
function _isLocalHost(agentIp) {
    if (!agentIp || agentIp === 'local') return true;
    const ip = agentIp.replace(/:\d+$/, '');
    const local = (_localIp || '').replace(/:\d+$/, '');
    const hostname = window.location.hostname;
    return ip === local
        || ip === hostname
        || (hostname === 'localhost' && ip === '127.0.0.1')
        || (hostname === '127.0.0.1' && ip === 'localhost');
}

// 頁面載入時立即偵測本機 IP
_detectLocalIp();

// 偵測所有主機連線狀態，更新狀態燈。
//
// 走 server proxy（/api/v1/agents/{id}/health）讓主控端代為 ping agent，而不是
// 瀏覽器直連 http://<agent-LAN-IP>/...。直連的舊作法在「從遠端 https 網域
// （foundry.originsun-studio.com / cloudflared）開後台」時會全滅：
//   1. mixed content — https 頁面不准 fetch http 資源，瀏覽器直接擋
//   2. 網段不通 — 遠端瀏覽器不在 192.168.1.x 內網，連不到 LAN IP
// 這也讓本面板的燈號與「專案總覽」的機器卡片（agent-cards.js 早就用 proxy）一致。
let _hostHealthTimer = null;
async function _checkHostHealth() {
    const hosts = window._computeHosts || [];
    for (const h of hosts) {
        const dotId = 'host-dot-' + (h.ip || '').replace(/[.:]/g, '_');
        const dots = document.querySelectorAll(`[id="${dotId}"]`);
        if (!h.id) continue;  // 沒 agent id 無法走 proxy — 留灰，不誤判紅
        const setRed = () => dots.forEach(el => { el.style.background = '#ef4444'; el.style.boxShadow = 'none'; });
        try {
            const r = await fetch('/api/v1/agents/' + encodeURIComponent(h.id) + '/health',
                                  { signal: AbortSignal.timeout(6000) });
            let online = r.ok;
            if (online) {
                const d = await r.json().catch(() => null);
                if (d && d.status === 'offline') online = false;
            }
            if (online) {
                dots.forEach(el => { el.style.background = '#22c55e'; el.style.boxShadow = '0 0 4px #22c55e'; });
            } else {
                setRed();
            }
        } catch {
            setRed();
        }
    }
}

function _startHostHealthPolling() {
    if (_hostHealthTimer) return;
    _detectLocalIp().then(() => _checkHostHealth()); // 先偵測本機 IP 再檢查健康
    _hostHealthTimer = setInterval(_checkHostHealth, 30000); // 每 30 秒
}

export function renderHostCheckboxes(containerId, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const hosts = window._computeHosts || [];
    if (!hosts.length) {
        container.closest('.pj-sch-sub-panel, [id$="_host_panel"], [id$="host_selector_panel"]')
            ?.style.setProperty('display', 'none');
        return;
    }
    if (container.dataset.built) return; // 避免重複渲染清除勾選

    const prefix = opts.idPrefix || containerId;
    container.innerHTML = '';

    hosts.forEach((h, i) => {
        const isLocal = _isLocalHost(h.ip);
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        const localTag = isLocal ? '<span class="text-green-400 text-[10px]">(本機)</span>' : '';
        lbl.innerHTML = `<input type="checkbox" id="${prefix}_${i}" data-ip="${h.ip}" data-name="${h.name}" ${isLocal ? 'checked' : ''} class="form-checkbox rounded bg-[#1e1e1e] border-[#444]"> ${_dotHtml(h.ip)} ${h.name} <span class="text-gray-500">(${_displayIp(h.ip)})</span>${localTag}`;
        container.appendChild(lbl);
    });
    container.dataset.built = '1';
    _startHostHealthPolling();
}

/**
 * 收集容器內勾選的主機。
 * @param {string} containerId - 容器 DOM id
 * @returns {Array<{name: string, ip: string}>}
 */
export function collectSelectedHosts(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    const result = [];
    container.querySelectorAll('input[type="checkbox"]:checked').forEach(chk => {
        const ip = chk.dataset.ip;
        const name = chk.dataset.name;
        if (ip && name) {
            // 本機 agent 用 'local' 標記（和原本邏輯相容）
            const isLocal = _isLocalHost(ip);
            result.push({ name, ip: isLocal ? 'local' : ip });
        }
    });
    return result;
}

// 單選版本（radio）— 用於只在一台主機執行的 TAB
export function renderHostRadios(containerId, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const hosts = window._computeHosts || [];
    if (!hosts.length) {
        container.closest('.pj-sch-sub-panel, [id$="_host_panel"], [id$="host_selector_panel"]')
            ?.style.setProperty('display', 'none');
        return;
    }
    if (container.dataset.built) return;

    const prefix = opts.idPrefix || containerId;
    const groupName = prefix + '_radio';
    container.innerHTML = '';
    let hasChecked = false;

    hosts.forEach((h, i) => {
        const isLocal = _isLocalHost(h.ip);
        const shouldCheck = isLocal && !hasChecked;
        if (shouldCheck) hasChecked = true;
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        const localTag = isLocal ? '<span class="text-green-400 text-[10px]">(本機)</span>' : '';
        lbl.innerHTML = `<input type="radio" name="${groupName}" id="${prefix}_${i}" data-ip="${h.ip}" data-name="${h.name}" ${shouldCheck ? 'checked' : ''} class="form-radio bg-[#1e1e1e] border-[#444]"> ${_dotHtml(h.ip)} ${h.name} <span class="text-gray-500">(${_displayIp(h.ip)})</span>${localTag}`;
        container.appendChild(lbl);
    });
    // 如果沒有匹配的本機，預設勾第一個
    if (!hasChecked) {
        const first = container.querySelector('input[type="radio"]');
        if (first) first.checked = true;
    }
    container.dataset.built = '1';
    _startHostHealthPolling();
}

export function collectSelectedHost(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return { name: '本機', ip: 'local' };
    const checked = container.querySelector('input[type="radio"]:checked');
    if (checked) {
        const ip = checked.dataset.ip;
        const name = checked.dataset.name;
        // 本機 agent 回傳 ip='local' 供後續判斷
        const isLocal = _isLocalHost(ip);
        return { name, ip: isLocal ? 'local' : ip };
    }
    return { name: '本機', ip: 'local' };
}

window.renderHostCheckboxes = renderHostCheckboxes;
window.collectSelectedHosts = collectSelectedHosts;
window.renderHostRadios = renderHostRadios;
window.collectSelectedHost = collectSelectedHost;

// Make accessible to global scope if needed during transition
window.resolveDropPath = resolveDropPath;
window.authFetch = authFetch;
window.appendLog = appendLog;
window.pickPath = pickPath;
window.getComputeBaseUrl = getComputeBaseUrl;
window.resetProgress = resetProgress;

export async function pickFiles(title = '選擇影片（可多選）') {
    try {
        const res = await fetch('/api/v1/utils/pick_files?title=' + encodeURIComponent(title));
        if (!res.ok) return [];
        const data = await res.json();
        if (data.error === 'session_0') {
            alert(data.message || 'Master 跑在 Session 0,picker 無法顯示。');
            return [];
        }
        return data.paths || [];
    } catch (_) {
        return [];
    }
}
window.pickFiles = pickFiles;

export function addStandaloneSource(listId, defaultPath = '') {
    const container = document.getElementById(listId);
    if (!container) return;
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    const inputId = 'standalone_src_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    row.innerHTML = `
        <input type="text" id="${inputId}" class="flex-1 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath}" placeholder="檔案絕對路徑...">
        <button type="button" class="btn-pick-file text-gray-400 hover:text-white bg-[#333] hover:bg-[#444] border border-[#555] px-2 py-1 rounded text-sm" title="選擇檔案">📁</button>
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded">X</button>
    `;
    container.appendChild(row);

    row.querySelector('.btn-pick-file').addEventListener('click', function() {
        pickPath(inputId, 'file');
    });
    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        row.remove();
    });
    return inputId;
}
window.addStandaloneSource = addStandaloneSource;

export function setupInputDrop(inputId) {
    const el = document.getElementById(inputId);
    if (!el) return;
    el.addEventListener('dragover', (e) => {
        e.preventDefault();
        el.classList.add('border-blue-400', 'bg-blue-900/20');
    });
    el.addEventListener('dragleave', () => {
        el.classList.remove('border-blue-400', 'bg-blue-900/20');
    });
    el.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.classList.remove('border-blue-400', 'bg-blue-900/20');

        const file = e.dataTransfer.files[0];
        let path = await resolveDropPath(e, file);

        if (!path || (file && path === file.name)) {
            const textData = e.dataTransfer.getData('text');
            if (textData) path = textData.trim();
        }

        if (path) {
            el.value = path;
            // Notify state-tracking listeners (e.g. align-pair Map sync) since
            // assigning `.value` doesn't fire `input` natively.
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }
    });
}

/**
 * 將本機磁碟代號路徑轉為 UNC 路徑（如 T:\foo → \\NAS\share\foo）。
 * 依賴 window._driveMap（由 /api/v1/utils/drive_map 取得）。
 * 若尚未載入映射表或無對應映射，原樣回傳。
 */
export function toUncPath(filePath) {
    const map = window._driveMap;
    if (!filePath || !map) return filePath;
    const drive = filePath.substring(0, 2).toUpperCase(); // "T:"
    const unc = map[drive];
    if (unc) {
        return unc.replace(/\\/g, '/') + filePath.substring(2).replace(/\\/g, '/');
    }
    return filePath;
}
window.toUncPath = toUncPath;

/**
 * 確保 window._driveMap 已載入（best-effort）。
 * 回傳映射表，或空物件。
 */
export async function ensureDriveMap() {
    if (window._driveMap && Object.keys(window._driveMap).length > 0) {
        return window._driveMap;
    }
    try {
        const res = await fetch('/api/v1/utils/drive_map');
        const data = await res.json();
        if (data.status === 'ok' && data.mappings) {
            window._driveMap = data.mappings;
            return data.mappings;
        }
    } catch (_) { /* best-effort */ }
    return window._driveMap || {};
}
window.ensureDriveMap = ensureDriveMap;

export async function validateRemotePaths(hostIp, paths) {
    const url = 'http://' + hostIp + '/api/v1/validate_paths';
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths })
    });
    const data = await res.json();
    const errors = [];
    for (const [path, info] of Object.entries(data.results)) {
        if (!info.drive_exists) {
            errors.push(`磁碟機 ${info.drive} 不存在`);
        } else if (!info.path_exists) {
            errors.push(`路徑不存在: ${path}`);
        }
    }
    return errors.length ? { ok: false, errors } : { ok: true };
}
window.validateRemotePaths = validateRemotePaths;

export function setupDragAndDrop(containerId, addRowFunc) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.addEventListener('dragover', (e) => {
        e.preventDefault();
        container.classList.add('bg-[#2a2a2a]', 'border-blue-500');
    });

    container.addEventListener('dragleave', (e) => {
        e.preventDefault();
        container.classList.remove('bg-[#2a2a2a]', 'border-blue-500');
    });

    container.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        container.classList.remove('bg-[#2a2a2a]', 'border-blue-500');

        const files = e.dataTransfer.files;
        if (!files || files.length === 0) return;

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const absPath = await resolveDropPath(e, file, i);

            const newInputId = addRowFunc();
            if (newInputId) {
                const el = document.getElementById(newInputId);
                if (el) {
                    el.value = absPath;
                    if (containerId === 'source_list' || containerId === 'tc_source_list') {
                        const row = el.closest('.flex');
                        const nameEl = row && row.querySelectorAll('input')[0];
                        if (nameEl && (!nameEl.value || nameEl.value.startsWith('Card_'))) {
                            const parts = absPath.replace(/\\/g, '/').split('/');
                            nameEl.value = parts[parts.length - 1] || nameEl.value;
                        }
                    }
                }
            }
        }
    });
}

