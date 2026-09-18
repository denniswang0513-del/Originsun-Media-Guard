/**
 * ctx.js — 知識庫獨立頁（/knowledge.html）的狀態、常數與小工具；四支視圖檔共用。
 *
 * 這支只 import js/shared（葉節點），不 import 同目錄的 shelf／book／chat ——
 * 三支之間要互相叫（上傳完開書、刪書回書架、討論回來重畫分頁）一律走 `nav`，
 * 由 index.js 在 mount 時填好，模組頂層不互相引用（同 crm-cashbook-*.js 的規矩：循環只在函式內用）。
 *
 * API：`/api/v1/knowledge`（只掛 master —— 檔案住 D:\、編譯與討論要 claude CLI）。
 * 走 NAS 那條路、或 master 關機時打到 404／503 → 畫面寫「書架需要主控主機在線」。
 * 宿主（knowledge.html）給 `fetch(path, {method, body})`：JSON 進 JSON 出、FormData 原樣送、
 * !ok 丟 Error 帶 `.status`／`.detail`；這裡再包一層 `api()` 補上 API 前綴與離線判定。
 */
import { esc } from '../shared/utils.js';

export { esc };

export const API = '/api/v1/knowledge';
export const OFFLINE_MSG = '書架需要主控主機在線';
export const CHAT_POLL_MS = 1000;
export const CHAT_GIVE_UP_MS = 10 * 60 * 1000;
export const CONCLUDE_POLL_MS = 2000;
export const CONCLUDE_MAX_TRIES = 20;
export const COMPILE_POLL_MS = 3000;
export const CHAT_TYPICAL = [20, 60];
//: AI 收尾那一句（同後端 core/knowledge_logic.py 的 CONCLUSION_MARK）——「存成結論」預設帶它後面那幾行
export const CONCLUSION_MARK = '可存成結論：';

export const STATUS_LABEL = { pending: '待補', uploaded: '未編譯', compiling: '編譯中', compiled: '已編譯', failed: '失敗' };
export const STATUS_CLS = { pending: 'warn', uploaded: 'na', compiling: 'warn', compiled: 'ok', failed: 'bad' };
//: 一次同時傳幾個檔。每支上傳都把整包讀進記憶體（上限 300MB），五本厚書同時傳就是 1GB。
export const UPLOAD_PARALLEL = 2;
export const PANES = [['chat', '討論'], ['conclusion', '結論'], ['notes', '筆記'], ['skeleton', '骨架'], ['chapters', '章節'], ['extend', '延伸'], ['gallery', '圖輯'], ['info', '資訊']];

/** 宿主給的東西（mount 時填） */
export const hooks = { host: null, fetch: null, toast: null };

/** 跨檔可見的畫面狀態（各檔只改自己負責的欄位；重畫一律從這裡讀） */
export const S = {
    root: null,        // 這次 mount 長出的 .kb 節點（監聽掛它身上）
    books: [],         // 書架清單（目前篩選下的）
    allTags: [],       // 篩選列用：沒篩時抓到的全部標籤
    uploads: [],       // 這一批在傳的檔 [{ name, state, cls, pct }]；傳完重抓書架也要留著，
                       // 不然失敗的那本原因閃一下就沒了
    uploadNote: '',    // 那一批的總結（完成幾本、失敗幾本）
    tag: '',           // 書架目前的標籤篩選（空＝全部）
    book: null,        // 打開的書（GET /{id} 整包）
    pane: 'chat',
    chat: [],
    wait: null,        // 等 AI 回覆：{ since, stage, partial, expect }
    chapter: null,     // 章節分頁打開的那一章 { n, title, md }
    extend: [],        // 「延伸」分頁：研究助理找到的清單（舊到新，同 `延伸.md` 的順序）
    extendStage: '',   // 正在找資料時的進度字串；空＝沒在找
    extendNote: '',    // 上一輪沒收到東西時的那句人話（失敗的理由／沒找到）
    watchConf: null,   // 研究助理的全域開關 { enabled, weekday, hour, can_edit }；null＝還沒讀到
    focusEdit: false,  // 「研究方向」那一格在編輯中
    infoEdit: false,   // 「資訊」分頁在編輯中
    assets: [],        // 「圖輯」分頁：整本書抽出來的圖
    assetsLoading: false,
    share: null,       // 公開分享 { on, id, url, at }；null＝還沒讀到
    assetUrls: [],     // 這一輪借出去的圖片 blob 網址（重畫前要 revoke）
    reports: [],       // 研究週報／月報的清單（§9.5）
    extendTimer: null,
    editing: false,    // 結論／筆記分頁：在編輯（textarea）還是在看（md 渲染）
    editingTags: false,
    concEdit: null,    // 「存成結論」開著的那一格 { idx, text }；null＝沒開
    chatTimer: null,
    concludeTimer: null,
    compileTimer: null,
};

/** 跨檔呼叫表（index.js 填）：shelf → book（上傳完開書）、chat → book（重畫分頁／重抓書）。
 *  import 方向是 ctx ← shelf ← book ← index、ctx ← chat ← book；反方向的呼叫走這張表。 */
export const nav = { openBook: null, renderPane: null, refetchBook: null, renderShelf: null };

/** 主控主機不在（cloudflared 打到 NAS、或 master 關機）：路由不存在的 404／代理層的 503。
 *  自家 router 丟的 404 帶自己的 detail（如「找不到這本書」），不會被當成離線。 */
export function isOffline(status, detail) {
    if (status === 404) return !detail || detail === 'Not Found';
    if (status === 503) return !detail || /^service unavailable$/i.test(detail);
    return false;
}

/** 宿主 fetch 外面再包一層：補 API 前綴、把離線標到錯誤上（`.offline`）。`opts.body` 傳物件或 FormData。 */
export async function api(path, opts = {}) {
    try {
        return await hooks.fetch(API + path, opts);
    } catch (e) {
        const status = e && e.status;
        const detail = (e && typeof e.detail === 'string') ? e.detail : '';
        const off = isOffline(status, detail);
        const err = new Error(off ? OFFLINE_MSG : ((e && e.message) || String(e)));
        err.status = status; err.offline = off; err.data = e && e.data;
        throw err;
    }
}

export const errText = (e) => (e && e.offline ? OFFLINE_MSG : (e && e.message) || String(e));
export const stageText = (s) => (typeof s === 'string' ? s : (s && (s.text || s.msg || s.label)) || '');
export const fmtWhen = (iso) => (iso ? String(iso).replace('T', ' ').slice(0, 16) : '');
export const alive = () => !!S.root && S.root.isConnected;
export const toast = (msg, err) => { if (hooks.toast) hooks.toast(msg, err ? 'err' : 'ok'); };

export function stopTimers() {
    clearInterval(S.chatTimer); S.chatTimer = null;
    clearInterval(S.concludeTimer); S.concludeTimer = null;
    clearInterval(S.compileTimer); S.compileTimer = null;
    clearInterval(S.extendTimer); S.extendTimer = null;
    S.wait = null;
}

/** 結論／筆記沒有檔時後端可能回 null：畫面與「變長了沒」的比較都要字串；tags 一律陣列 */
export function normBook(b) {
    if (b && typeof b === 'object') {
        if (typeof b.conclusion !== 'string') b.conclusion = '';
        if (typeof b.notes !== 'string') b.notes = '';
        if (!Array.isArray(b.tags)) b.tags = [];
    }
    return b;
}

/** GET /{id} 的 `chapters` 是**數量**（書架卡片那個），清單在 `chapter_list` */
export const chapterList = (b) => (Array.isArray(b && b.chapter_list) ? b.chapter_list : []);

export const bookTags = (b) => (Array.isArray(b && b.tags) ? b.tags : []);

/** 「財務, 投資 ,,財務」→ ['財務', '投資']（去空白、去重、保順序） */
export function parseTags(text) {
    const out = [];
    for (const raw of String(text || '').split(/[,，]/)) {
        const t = raw.trim();
        if (t && !out.includes(t)) out.push(t);
    }
    return out;
}

/** 一張圖的出處：`出自《書名》陳玉箴，第 76 頁`（owner 2026-09-19：「要標明出處」）。
 *  圖是從原書抽出來的，出處就是那本書 —— 公開分享時更需要，沒有出處的圖等於來路不明。 */
export function figureSource(book, page) {
    const b = book || {};
    const title = b.title || b.source_name || '';
    const who = b.author ? `　${b.author}` : '';
    const p = page ? `，第 ${page} 頁` : '';
    return title ? `出自《${title}》${who}${p}` : (page ? `第 ${page} 頁` : '');
}

export function statusPill(b) {
    const st = b.status || 'uploaded';
    let label = STATUS_LABEL[st] || st;
    const stage = stageText(b.stage);
    if (st === 'compiling' && stage) label += `（${stage}）`;
    return `<span class="pill ${STATUS_CLS[st] || 'na'}">${esc(label)}</span>`;
}

export const tagsHtml = (tags) => tags.map((t) => `<span class="kb-tag">${esc(t)}</span>`).join('');
