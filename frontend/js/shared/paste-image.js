/**
 * paste-image.js — 全站 textarea「貼上圖片」層。
 *
 * document 層 paste 攔截（capture）：現在與未來動態渲染的所有 textarea 一體
 * 適用（比照 select-upgrade 的全域哲學；paste 會冒泡，連 MutationObserver
 * 都不用）。單行 <input> 不啟用——姓名/金額欄貼圖無意義；個別 textarea 要
 * 退出加 data-no-paste-image。
 *
 * 流程：剪貼簿有圖 → 游標處插「（圖片上傳中…）」占位 → POST /api/v1/paste_upload
 * （後端轉 WebP 寫 NAS 圖床，見 routers/api_paste.py）→ 占位替換成 markdown
 * token `![圖片](paste:<hex>.webp)`。內容欄只存 token 不含網域；顯示面用本模組的
 * renderRich() 轉 <img>（32-hex 白名單，手打別的網址不會變圖）。
 * 尚未接 renderRich 的顯示面看到 token 純文字——可讀、不壞版面，逐面再接。
 *
 * 主 SPA 由 app.js boot 呼叫 initPasteImage()；獨立頁（journal.html 等）自行
 * import + 呼叫。渲染端（renderRich/ensurePasteBase）也住這裡——上傳與渲染是
 * 同一個 token 契約的兩半，未來公布欄/CRM 接顯示面直接 import 本模組
 * （journal-core 有 re-export，週誌消費端不用跨檔）。
 */
import { esc as _esc } from '../../tabs/website/website-utils.js';

// ── token → <img> 渲染 ─────────────────────────────────────────────────
// 白名單只認 `paste:<32hex>.webp` — 與後端 routers/api_paste.py 的產出契約一致
// （uuid4().hex + .webp；tests/integration/test_paste_upload.py 有跨語言守衛：
// 用真實後端 token 對本檔抽出的 PASTE_RE 驗 match，後端改命名這裡沒跟上會紅）。
// 基底網址來自 GET /paste_config（伺服端設定）：DB 只存 token，圖床搬家改 settings 即可。
const PASTE_RE = /!\[([^\]]*)\]\(paste:([0-9a-f]{32}\.webp)\)/g;
let _basePromise = null;        // fetch 一次快取（含失敗＝''，token 退純文字顯示）
let _pasteBase = '';

export function ensurePasteBase() {
    if (!_basePromise) {
        _basePromise = fetch('/api/v1/paste_config')
            .then(r => (r.ok ? r.json() : {}))
            .then(d => { _pasteBase = d.base_url || ''; return _pasteBase; })
            .catch(() => '');
    }
    return _basePromise;
}

// esc 後把貼圖 token 轉縮圖（點開新分頁看原圖）。頁面首次渲染前 await 一次
// ensurePasteBase()（可併入資料抓取的 Promise.all）；沒取到基底時 token 以
// 純文字顯示（不會壞版面）。.paste-img 樣式由各頁自帶。
export function renderRich(text) {
    const s = _esc(text);
    if (!_pasteBase) return s;
    return s.replace(PASTE_RE, (_, alt, file) => {
        const src = `${_pasteBase}/${file}`;
        return `<a href="${src}" target="_blank" rel="noopener"><img class="paste-img" src="${src}" alt="${alt || '圖片'}" loading="lazy"></a>`;
    });
}

// ── 貼上 → 上傳 ────────────────────────────────────────────────────────
let _seq = 0;

function _insertAt(ta, text) {
    const s = ta.selectionStart, e = ta.selectionEnd;
    ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
    ta.selectionStart = ta.selectionEnd = s + text.length;
    // 讓頁面的 debounce 自存/dirty 邏輯知道值變了
    ta.dispatchEvent(new Event('input', { bubbles: true }));
}

function _replacePlaceholder(ta, from, to) {
    const i = ta.value.indexOf(from);
    if (i < 0) return;                       // 使用者已手動刪掉占位 → 不硬插
    const caret = ta.selectionStart;         // 上傳中使用者可能繼續打字 — 補償游標位移
    ta.value = ta.value.slice(0, i) + to + ta.value.slice(i + from.length);
    if (caret > i) ta.selectionStart = ta.selectionEnd = Math.max(i, caret + to.length - from.length);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
}

async function _uploadOne(ta, file) {
    const ph = `（圖片上傳中…#${++_seq}）`;
    _insertAt(ta, ph);
    try {
        // multipart 不能走 utils.authFetch（它會把 body JSON.stringify）
        const fd = new FormData();
        fd.append('file', file, file.name || 'paste.png');
        const tok = localStorage.getItem('auth_token') || '';
        const r = await fetch('/api/v1/paste_upload', {
            method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd,
        });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            throw new Error(d.detail || `HTTP ${r.status}`);
        }
        const d = await r.json();
        _replacePlaceholder(ta, ph, `![圖片](${d.token})`);
    } catch (err) {
        _replacePlaceholder(ta, ph, `（圖片上傳失敗：${err?.message || '連線失敗'}）`);
    }
}

function _onPaste(e) {
    const ta = e.target;
    if (!(ta instanceof HTMLTextAreaElement) || ta.disabled || ta.readOnly) return;
    if (ta.dataset.noPasteImage !== undefined) return;
    const files = [...(e.clipboardData?.items || [])]
        .filter(it => it.kind === 'file' && it.type.startsWith('image/'))
        .map(it => it.getAsFile()).filter(Boolean);
    if (!files.length) return;               // 純文字貼上 → 完全不干涉
    e.preventDefault();                      // 有圖就接手（截圖貼上幾乎都是純圖）
    files.forEach(f => _uploadOne(ta, f));
}

export function initPasteImage() {
    if (window._pasteImageInit) return;      // 冪等（SPA 與內嵌頁都可能呼叫）
    window._pasteImageInit = true;
    document.addEventListener('paste', _onPaste, true);
}
