/**
 * proposal-folders.js — 提案資產資料夾**總覽**（root 底下所有資料夾）。
 *
 * owner 2026-08-06：NAS 上手工整理的**舊提案資料夾**也要在系統裡看得到、
 * 載得到、還能繼續往裡面丟檔案。所以這裡列的是 root 底下的**所有**資料夾：
 * - 已連結：`crm_projects.proposal_folder_name` 指到它（帶專案名/客戶）
 * - 未連結：磁碟上有、系統沒登記（過去的那些）
 *
 * 每個資料夾展開後的**逐層瀏覽 / 上傳 / 新增子夾**一律走共用的 folder-view
 * （CRM 專案的檔案列、企劃頁的資料夾分頁用的是同一支）。這支只負責總覽層
 * 特有的三件事：搜尋、**改名**（連結專案的一起改，後端 rename_and_remap
 * 保證同進退）、**連結專案**（連上之後它就是該專案的提案資產夾）。
 *
 * 兩個介面共用：後台提案庫 Tab 與獨立企劃頁 —— 所以
 *   (a) 只 import js/shared 與同目錄（NAS 對外容器只 serve 這兩處）；
 *   (b) 版面自帶 --pf-* 變數（深色預設、白底靠 html.plan-theme-light 覆寫），
 *       不吃呼叫端的 class；
 *   (c) 外殼由呼叫端注入（`mount(html) -> element`），後台給 overlay、
 *       企劃頁給對話框。
 *
 * 檔案清單純掃磁碟即時列（後端不建索引表）。
 */

import { authDownload, bearerHeader, ensureStyle, esc, projectOptionsHtml,
         uploadItems } from '../../js/shared/utils.js';
import { renderFolderView } from './folder-view.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm/proposal-assets';

let _projects = null;    // 連結專案的下拉清單（懶載，快取 promise；跨實例共用）

// 🔴 狀態掛在**這一個實例的根節點**上，不是模組層：企劃頁的「資產資料夾」
// 是對話框，連按兩下就會疊出兩個 —— 模組層的話兩個實例共用同一組
// open/action，互相踩到對方的展開狀態。ov 一律是 `.pf` 那顆（外殼由呼叫端
// 給，可能是 overlay 也可能是對話框，所以進來時先正規化）。
const S = (ov) => ov.__pf;

/**
 * @param mount (html) => element   由呼叫端提供外殼（overlay / 對話框），
 *              回傳掛好的容器。內容用 `.pf-*` class，樣式這支自己帶。
 */
export async function openFolderBrowser(mount) {
    ensureStyle('pf-style', STYLE);
    const shell = mount(`
        <div class="pf">
            <div class="pf-head">
                <input id="pf-q" type="text" placeholder="搜尋資料夾或專案名…">
                <span class="pf-gap"></span>
                <span id="pf-root" class="pf-note"></span>
            </div>
            <div id="pf-list"><div class="pf-note">載入中…</div></div>
            <div class="pf-note" id="pf-empty" hidden>沒有符合的資料夾</div>
        </div>`);
    const ov = shell.querySelector('.pf');   // mount 回的是**包住**內容的外殼
    ov.__pf = { folders: [], open: '', action: '' };
    // 搜尋純前端過濾：整份清單一次撈回來了，而且**不重建 DOM** ——
    // 重建會把展開中那個資料夾的 folder-view 一起洗掉（連同它的層快取）
    ov.querySelector('#pf-q').addEventListener('input', () => _applyFilter(ov));
    ov.querySelector('#pf-list').addEventListener('click', (e) => _onClick(ov, e));
    await _load(ov);
}

async function _load(ov) {
    const list = ov.querySelector('#pf-list');
    try {
        const d = await tfetch(`${API}/overview`);
        S(ov).folders = d.folders || [];
        ov.querySelector('#pf-root').textContent =
            (d.root || '（未設定根目錄）') + (d.root_set ? '' : ' — 目前搆不到');
    } catch (e) {
        list.innerHTML = `<div class="pf-note pf-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(ov);
}

/** 整份重建。只在「資料變了」時呼叫（載入 / 改名 / 連結）—— 搜尋走 _applyFilter。 */
function _render(ov) {
    const s = S(ov);
    const list = ov.querySelector('#pf-list');
    if (!s.folders.length) {
        list.innerHTML = '<div class="pf-note">這個根目錄底下還沒有資料夾</div>';
        return;
    }
    // 展開中那格的檔案瀏覽器**整顆留著**：連結專案不動檔案內容，重掛等於把
    // 使用者從第三層丟回最外層，外加一趟 NAS 掃描。改名那條不會命中（名字變了
    // → 選不到舊的那格）—— 那正是它該重掛的場合。
    const keep = s.open ? ov.querySelector(_sel(s.open) + ' .pf-files') : null;
    list.innerHTML = s.folders.map(f => {
        const tag = f.missing_on_disk
            ? '<span class="pf-pill warn">磁碟上找不到</span>'
            : f.linked
                ? `<span class="pf-pill">已連結</span><span class="pf-note">${
                    esc(f.project_name || '')}${f.client_name ? ' · ' + esc(f.client_name) : ''}</span>`
                : '<span class="pf-pill dim">未連結</span>';
        return `
        <div class="pf-item" data-row="${esc(f.folder_name)}" data-q="${
            esc((f.folder_name + ' ' + (f.project_name || '')).toLowerCase())}">
            <div class="pf-row" data-folder="${esc(f.folder_name)}">
                <span class="pf-caret">▸</span>
                <span class="pf-name">${esc(f.folder_name)}</span>
                ${tag}
                <span class="pf-gap"></span>
                ${f.missing_on_disk ? '' : `
                    <button class="pf-btn" data-act="rename">改名</button>
                    <button class="pf-btn" data-act="link">${f.linked ? '改連結' : '連結專案'}</button>`}
            </div>
            <div class="pf-panel" hidden>
                <div class="pf-action"></div>
                <div class="pf-files"></div>
            </div>
        </div>`;
    }).join('');
    _applyFilter(ov);
    if (s.open) {                             // 重建後把原本展開的那個還原
        const slot = ov.querySelector(_sel(s.open) + ' .pf-files');
        if (keep && slot) slot.replaceWith(keep);
        _expand(ov, s.open, true);
    }
}

/** 搜尋：只切換顯示，不重建 DOM。可搜尋文字在 render 時就烤進 data-q。 */
function _applyFilter(ov) {
    const ql = (ov.querySelector('#pf-q').value || '').trim().toLowerCase();
    let shown = 0;
    for (const el of ov.querySelectorAll('.pf-item')) {
        el.hidden = !!ql && !el.dataset.q.includes(ql);
        if (!el.hidden) shown++;
    }
    ov.querySelector('#pf-empty').hidden = shown > 0;
}

// 屬性選擇器要跳脫：資料夾名是使用者取的，什麼字元都可能有 —— 用原生的
// CSS.escape，自己寫 regex 一定會漏
const _sel = (name) => `[data-row="${CSS.escape(name)}"]`;

function _onClick(ov, e) {
    const s = S(ov);
    // 操作鈕在標題列裡（標題列本身是展開/收合），所以先攔它們
    const act = e.target.closest('[data-act]');
    if (act) {
        const name = act.closest('[data-row]').dataset.row;
        // 先決定要開哪個操作列再展開 —— 反過來的話 _expand 會先畫一次空的
        s.action = (s.open === name && s.action === act.dataset.act)
            ? '' : act.dataset.act;                      // 同一顆再點一次＝收起來
        if (s.open !== name) _expand(ov, name, true);
        else _paintAction(ov);
        return;
    }
    const head = e.target.closest('[data-folder]');
    if (head) _expand(ov, head.dataset.folder);
}

/** 一列的展開狀態。`_render` 產生的 markup 本來就全是收合的，不必逐列清。 */
function _setRow(ov, name, on) {
    const el = ov.querySelector(_sel(name));
    if (!el) return;
    el.querySelector('.pf-panel').hidden = !on;
    el.querySelector('.pf-caret').textContent = on ? '▾' : '▸';
    if (!on) el.querySelector('.pf-action').innerHTML = '';
}

/** 展開/收合一個資料夾。`keep` = 重建後還原，不做 toggle。 */
function _expand(ov, name, keep = false) {
    const s = S(ov);
    const close = !keep && s.open === name;
    if (s.open) _setRow(ov, s.open, false);
    if (close) { s.open = ''; s.action = ''; return; }
    if (!keep && s.open !== name) s.action = '';
    s.open = name;
    _setRow(ov, name, true);
    _paintAction(ov);
    const files = ov.querySelector(_sel(name) + " .pf-files");
    if (files && !files.dataset.mounted) {
        files.dataset.mounted = '1';        // 每個資料夾掛一次，收合再展開保留瀏覽位置
        _mountFiles(files, name);
    }
}

/** 資料夾內容：整個交給共用的 folder-view（逐層 / 上傳 / 新增子夾 / 拖放）。 */
function _mountFiles(host, folder) {
    const q = (rel) => `?folder=${encodeURIComponent(folder)}&rel=${encodeURIComponent(rel || '')}`;
    renderFolderView(host, {
        rootLabel: folder,
        load: (rel) => tfetch(`${API}/folder/files${q(rel)}`),
        onFile: (f) => authDownload(`${API}/folder/file${q(f.rel)}`, f.filename || f.rel),
        // 未連結專案的「過去資料夾」也能丟檔（owner 指定）
        write: {
            mkdir: (name, rel) => tfetch(`${API}/folder/mkdir${q(rel)}`,
                { method: 'POST', json: { name } }),
            upload: (items, rel, opts) => uploadItems(`${API}/folder/upload${q(rel)}`, items,
                { headers: bearerHeader(), ...opts }),
        },
    });
}

// ── 改名 / 連結專案：總覽層特有的兩個動作（就地展開，不另開視窗）──────
function _paintAction(ov) {
    const s = S(ov);
    const box = ov.querySelector(_sel(s.open) + " .pf-action");
    if (!box) return;
    const f = s.folders.find(x => x.folder_name === s.open) || {};
    if (s.action === 'rename') {
        box.innerHTML = `<div class="pf-bar">
            <span class="pf-note">新資料夾名稱</span>
            <input class="pf-newname" type="text" value="${esc(f.folder_name || '')}">
            <button class="pf-btn primary pf-rename-go">改名</button>
        </div>`;
    } else if (s.action === 'link') {
        box.innerHTML = `<div class="pf-bar col">
            <div class="pf-note">連結後這個資料夾就是該專案的提案資產夾 —— 之後上傳的簡報會落在
                這裡，專案改名時資料夾也會跟著改名。${f.linked
                    ? `目前連結：<b>${esc(f.project_name || '')}</b>` : ''}</div>
            <div class="pf-bar">
                <select class="pf-proj"><option value="">載入專案清單…</option></select>
                <button class="pf-btn primary pf-link-go">連結</button>
                ${f.linked ? '<button class="pf-btn pf-unlink">解除連結</button>' : ''}
            </div>
        </div>`;
    } else {
        box.innerHTML = '';
        return;
    }
    _wireAction(box, s.open);
}

// box = 這一格的 .pf-action。查詢一律 scope 到它：操作列的欄位用 class 不用
// id（重複模板裡的 id 一定會撞），所以沒有 scope 就一定抓錯那一格
function _wireAction(box, folder) {
    const ov = box.closest('.pf');
    box.querySelector('.pf-rename-go')?.addEventListener('click', async () => {
        const name = box.querySelector('.pf-newname').value.trim();
        if (!name || name === folder) { S(ov).action = ''; _paintAction(ov); return; }
        try {
            const d = await tfetch(`${API}/folder/rename`,
                { method: 'POST', json: { folder, new_name: name } });
            S(ov).open = d.folder_name || name;
            S(ov).action = '';
            await _load(ov);      // 名字變了 → 整份重來（folder-view 也要跟著換 base）
        } catch (e) { alert('改名失敗：' + (e.message || e)); }
    });

    const sel = box.querySelector('.pf-proj');
    if (sel) {
        _loadProjects()
            .then(list => {
                if (!sel.isConnected) return;
                // 灌完選項後由全域 select-upgrade 自動升級成可搜尋下拉（≥8 項）——
                // 那支只在 SPA 有掛，企劃頁就是原生下拉，都可用
                sel.innerHTML = projectOptionsHtml(list);
            })
            .catch(e => { sel.innerHTML = `<option value="">載入失敗：${esc(e.message || e)}</option>`; });
    }
    box.querySelector('.pf-link-go')?.addEventListener('click', () => {
        const pid = box.querySelector('.pf-proj').value;
        if (!pid) { alert('請先選一個專案'); return; }
        _link(ov, folder, pid);
    });
    box.querySelector('.pf-unlink')?.addEventListener('click', () => {
        if (confirm('解除連結？資料夾與裡面的檔案都會留著，只是不再屬於這個專案。')) {
            _link(ov, folder, '');
        }
    });
}

async function _link(ov, folder, projectId) {
    try {
        await tfetch(`${API}/folder/link`,
            { method: 'POST', json: { folder, project_id: projectId } });
        S(ov).action = '';
        await _load(ov);         // 連結狀態來自 DB，重撈一次總覽最準
    } catch (e) { alert('連結失敗：' + (e.message || e)); }
}

function _loadProjects() {
    // 快取 **promise** 不是結果 —— 兩個資料夾先後開「連結專案」時，第一趟還沒
    // 回來就又發一次同樣的 join 查詢
    if (!_projects) _projects = tfetch('/api/v1/crm/projects').then(d => d.projects || []);
    return _projects;
}

const STYLE = `
.pf { --pf-ink: #ddd; --pf-sub: #8b8b8b; --pf-line: #2a2a2a; --pf-card: #161616;
      --pf-accent: #c9372c; --pf-warn: #fbbf24; --pf-err: #f87171; color: var(--pf-ink); }
html.plan-theme-light .pf { --pf-ink: #262626; --pf-sub: #737373; --pf-line: #e5e5e5;
      --pf-card: #fafafa; --pf-accent: #c9372c; --pf-warn: #b8860b; --pf-err: #d33; }
.pf * { box-sizing: border-box; }
.pf-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.pf-gap { flex: 1; }
.pf input, .pf select { background: none; color: var(--pf-ink); border: 1px solid var(--pf-line);
      border-radius: 3px; padding: 6px 9px; font: inherit; font-size: 12.5px; outline: none; }
.pf input:focus, .pf select:focus { border-color: var(--pf-accent); }
.pf-head input { width: 220px; }
.pf-note { font-size: 11.5px; color: var(--pf-sub); }
.pf-err { color: var(--pf-err); }
.pf-item { border: 1px solid var(--pf-line); border-radius: 4px; margin-bottom: 6px; }
.pf-item[hidden] { display: none; }
.pf-row { display: flex; gap: 8px; align-items: center; padding: 8px 10px; cursor: pointer; }
.pf-row:hover { background: rgba(127,127,127,.07); }
.pf-caret { color: var(--pf-sub); }
.pf-name { font-weight: 600; font-size: 13px; }
.pf-pill { font-size: 11px; padding: 1px 8px; border: 1px solid var(--pf-line);
      border-radius: 2px; color: var(--pf-sub); white-space: nowrap; }
.pf-pill.warn { color: var(--pf-warn); border-color: var(--pf-warn); }
.pf-pill.dim { opacity: .6; }
.pf-btn { border: 1px solid var(--pf-line); background: none; cursor: pointer;
      color: var(--pf-ink); font: inherit; font-size: 12px; padding: 4px 9px; border-radius: 3px; }
.pf-btn:hover { border-color: var(--pf-accent); color: var(--pf-accent); }
.pf-btn.primary { border-color: var(--pf-accent); color: var(--pf-accent); }
.pf-panel { border-top: 1px solid var(--pf-line); }
.pf-panel[hidden] { display: none; }
.pf-bar { display: flex; gap: 6px; align-items: center; }
.pf-bar.col { flex-direction: column; align-items: stretch; }
.pf-bar input, .pf-bar select { flex: 1; min-width: 0; }
.pf-action:not(:empty) { padding: 8px 12px; background: var(--pf-card); }
.pf-files { padding: 4px 12px 8px; }`;
