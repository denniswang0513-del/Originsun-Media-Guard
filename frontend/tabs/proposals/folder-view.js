/**
 * folder-view.js — 資產資料夾的逐層瀏覽元件。
 *
 * 提案公開頁 `/proposal-plan.html` 的「資料夾」分頁用它，**登入與訪客兩種模式
 * 共用同一份渲染**，差別只在建構時傳進來的 `load` 打哪支端點：
 *
 *   登入（員工）→ /api/v1/crm/projects/{id}/proposal-assets   整個資產夾
 *   訪客（?t=）  → /api/v1/proposals/shared/{token}/folder     只有「對外分享」子夾
 *
 * 🔴 範圍是**端點**決定的，不是這個元件的參數決定的 —— 元件只會顯示 load
 * 回什麼。想改「客戶看得到什麼」要去改後端的 root，改這裡沒有用也不該有用。
 *
 * 寫入（建夾／上傳）是**可選的**：傳 `write` 才會長出工具列與拖放區。
 * 訪客那條不傳，所以它連按鈕都沒有 —— 唯讀不是靠隱藏元素，是靠沒建出來。
 *
 * 樣式走呼叫端的 CSS 變數（--line/--sub/--ink/--red），所以在官網白底的公開頁
 * 與深色 SPA 都不用改。
 */

import { esc, folderCrumbsHtml, inputUploadItems,
         wireFileDrop } from '../../js/shared/utils.js';
import { fmtSize } from '../../js/shared/clip_utils.js';

const _day = (mtime) => (mtime ? new Date(mtime * 1000).toISOString().slice(0, 10) : '');

/**
 * @param host    掛載容器（會被清空）
 * @param opts.load      async (rel) => {rel, dirs, files, truncated}
 * @param opts.onFile    (file) => void   點檔案要做什麼（各自決定怎麼下載）
 * @param opts.rootLabel 麵包屑最左邊顯示的名字
 * @param opts.emptyHint 整個資料夾空的時候要說什麼
 * @param opts.write     可選；有給才有工具列與拖放：
 *        { upload(items, rel) -> level, mkdir(name, rel) -> level,
 *          shareDir?: '對外分享' }  給 shareDir 時，最外層還沒有那個夾就多一顆
 *        「建立對外分享夾」（名字由程式帶，不讓人手打）
 * @returns { rel(), go(rel), apply(level) }
 */
export function renderFolderView(host, opts) {
    const { load, onFile, rootLabel = '資料夾', emptyHint = '這裡還沒有檔案。',
            write = null } = opts;
    // 走過的層留著：往回走是最常見的動作，重打一次是整趟 NAS 掃描
    const cache = new Map();
    let cur = '';
    _ensureStyle();

    async function go(rel) {
        cur = rel;
        const hit = cache.get(rel);
        if (hit) { paint(hit); return; }
        host.innerHTML = '<div class="fv-body">載入中…</div>';
        try {
            const d = await load(rel);
            cache.set(rel, d);
            if (cur === rel) paint(d);       // await 期間使用者可能已走到別層
        } catch (e) {
            host.innerHTML = `<div class="fv-body fv-err">載入失敗：${esc(e.message || e)}</div>`;
        }
    }

    function paint(d) {
        const dirs = d.dirs || [];
        const files = d.files || [];
        const crumbs = d.rel
            ? `<div class="fv-crumbs">${folderCrumbsHtml(rootLabel, d.rel)}</div>` : '';
        const rows = dirs.map(x => `
            <div class="fv-row fv-dir" data-dir="${esc(x.rel)}">
                <span class="fv-caret">▸</span><span class="fv-name">${esc(x.name)}</span>
            </div>`).join('')
            + files.map(f => `
            <div class="fv-row fv-file" data-file="${esc(f.rel)}" title="下載">
                <span class="fv-name">${esc(f.filename)}</span>
                <span class="fv-meta">${fmtSize(f.size_bytes)}</span>
                <span class="fv-meta">${esc(_day(f.mtime))}</span>
            </div>`).join('');
        host.innerHTML = _toolbarHtml(d, dirs) + crumbs + (rows
            ? `<div class="fv-list">${rows}</div>`
            : `<div class="fv-body">${esc(d.rel ? '這一層是空的。'
                : (write ? '把檔案或整個資料夾拖進來，或用上面的按鈕。' : emptyHint))}</div>`)
            + (d.truncated ? '<div class="fv-body fv-warn">項目過多，只顯示前 1000 筆。</div>' : '');
        host.querySelectorAll('[data-dir],[data-crumb]').forEach(el => {
            el.addEventListener('click', () => go(el.dataset.dir ?? el.dataset.crumb));
        });
        host.querySelectorAll('[data-file]').forEach(el => {
            el.addEventListener('click', () => onFile(files.find(f => f.rel === el.dataset.file)));
        });
        if (write) _wireToolbar(d);
    }

    // ── 寫入工具列（只有傳 write 才存在）──────────────────────
    function _toolbarHtml(d, dirs) {
        if (!write) return '';
        const needShare = write.shareDir && !d.rel
            && !dirs.some(x => x.name === write.shareDir);
        return `<div class="fv-tools">
            ${needShare ? `<button class="fv-btn primary" data-act="share"
                title="建立客戶看得到的資料夾">＋ ${esc(write.shareDir)}夾</button>` : ''}
            <button class="fv-btn" data-act="mkdir">＋ 新增資料夾</button>
            <button class="fv-btn" data-act="files">＋ 上傳檔案</button>
            <button class="fv-btn" data-act="dir">＋ 上傳資料夾</button>
            <span class="fv-hint">也可以把檔案或整個資料夾拖進來</span>
            <span class="fv-busy" hidden>上傳中…</span>
        </div>`;
    }

    async function run(fn) {
        const busy = host.querySelector('.fv-busy');
        if (busy) busy.hidden = false;
        try {
            apply(await fn());
        } catch (e) {
            alert((e && e.message) || e);
        } finally {
            if (busy && busy.isConnected) busy.hidden = true;
        }
    }

    function _wireToolbar(d) {
        const rel = d.rel || '';
        const pick = (asDir) => {
            const inp = document.createElement('input');
            inp.type = 'file';
            inp.multiple = true;
            if (asDir) inp.webkitdirectory = true;   // 選整個資料夾（含夾名）
            inp.addEventListener('change', () => {
                const items = inputUploadItems(inp.files);
                if (items.length) run(() => write.upload(items, rel));
            });
            inp.click();
        };
        const act = {
            share: () => run(() => write.mkdir(write.shareDir, '')),
            mkdir: () => {
                const name = (prompt('新資料夾名稱（會建在你目前看的這一層）：') || '').trim();
                if (name) run(() => write.mkdir(name, rel));
            },
            files: () => pick(false),
            dir: () => pick(true),
        };
        host.querySelectorAll('[data-act]').forEach(el => {
            el.addEventListener('click', () => act[el.dataset.act]());
        });
    }

    function apply(level) {
        if (!level) return;
        const key = level.rel || '';
        cache.set(key, level);
        if (key === cur) paint(level);
        else go(key);
    }

    // 拖放**只綁一次**在 host 上：paint() 換的是 host 的 children，host 本身
    // 不變 —— 每次重畫都綁一遍的話，導覽 5 層之後拖一次會送出 5 份。
    // 落點層在放開的當下才讀 cur，所以永遠是「使用者現在看的這一層」。
    if (write) wireFileDrop(host, (items) => run(() => write.upload(items, cur)));

    go('');
    return { rel: () => cur, go, apply };
}

// 自帶樣式（只注一次）—— 呼叫端不必為了掛這個元件去改自己的 CSS 檔
function _ensureStyle() {
    if (document.getElementById('fv-style')) return;
    const st = document.createElement('style');
    st.id = 'fv-style';
    st.textContent = `
.fv-body { padding: 18px 4px; font-size: 13px; color: var(--sub, #8b8b8b); }
.fv-err { color: var(--red, #d33); }
.fv-warn { color: #b8860b; }
.fv-crumbs { font-size: 12.5px; padding: 4px 2px 10px; }
.fv-list { border-top: 1px solid var(--line, #e5e5e5); }
.fv-row { display: flex; align-items: center; gap: 10px; padding: 9px 4px;
          border-bottom: 1px solid var(--line, #e5e5e5); cursor: pointer; font-size: 13.5px; }
.fv-row:hover { background: rgba(127,127,127,.07); }
.fv-caret { color: var(--sub, #8b8b8b); }
.fv-dir .fv-name { font-weight: 600; }
.fv-name { flex: 1; word-break: break-all; }
.fv-meta { color: var(--sub, #8b8b8b); font-size: 12px; white-space: nowrap; }
.fv-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 2px 0 12px; }
.fv-btn { border: 1px solid var(--line, #d5d5d5); background: none; cursor: pointer;
          color: var(--ink, #222); font: inherit; font-size: 12.5px; padding: 5px 10px; }
.fv-btn:hover { background: rgba(127,127,127,.08); }
.fv-btn.primary { border-color: var(--red, #c33); color: var(--red, #c33); }
.fv-hint, .fv-busy { font-size: 12px; color: var(--sub, #8b8b8b); }
.fv-busy[hidden] { display: none; }`;
    document.head.appendChild(st);
}
