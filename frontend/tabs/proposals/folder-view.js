/**
 * folder-view.js — 資產資料夾的逐層瀏覽元件（唯讀）。
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
 * 樣式走呼叫端的 CSS 變數（--line/--sub/--ink/--red），所以在官網白底的公開頁
 * 與深色 SPA 都不用改。
 */

import { folderCrumbsHtml } from '../../js/shared/utils.js';
import { fmtSize } from '../../js/shared/clip_utils.js';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const _day = (mtime) => (mtime ? new Date(mtime * 1000).toISOString().slice(0, 10) : '');

/**
 * @param host    掛載容器（會被清空）
 * @param opts.load      async (rel) => {rel, dirs, files, truncated}
 * @param opts.onFile    (file) => void   點檔案要做什麼（各自決定怎麼下載）
 * @param opts.rootLabel 麵包屑最左邊顯示的名字
 * @param opts.emptyHint 整個資料夾空的時候要說什麼
 */
export function renderFolderView(host, opts) {
    const { load, onFile, rootLabel = '資料夾', emptyHint = '這裡還沒有檔案。' } = opts;
    // 走過的層留著：往回走是最常見的動作，重打一次是整趟 NAS 掃描
    const cache = new Map();
    let cur = '';

    host.innerHTML = '<div class="fv-body">載入中…</div>';
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
        host.innerHTML = crumbs + (rows
            ? `<div class="fv-list">${rows}</div>`
            : `<div class="fv-body">${esc(d.rel ? '這一層是空的。' : emptyHint)}</div>`)
            + (d.truncated ? '<div class="fv-body fv-warn">項目過多，只顯示前 1000 筆。</div>' : '');
        host.querySelectorAll('[data-dir],[data-crumb]').forEach(el => {
            el.addEventListener('click', () => go(el.dataset.dir ?? el.dataset.crumb));
        });
        host.querySelectorAll('[data-file]').forEach(el => {
            el.addEventListener('click', () => {
                onFile((d.files || []).find(f => f.rel === el.dataset.file));
            });
        });
    }

    go('');
    return { reload: () => { cache.delete(cur); go(cur); } };
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
.fv-meta { color: var(--sub, #8b8b8b); font-size: 12px; white-space: nowrap; }`;
    document.head.appendChild(st);
}
