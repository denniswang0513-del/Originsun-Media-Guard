/**
 * proposal-folders.js — 提案資產資料夾瀏覽器（提案庫 Tab 的「📁 資產資料夾」）
 *
 * owner 2026-08-06：NAS 上手工整理的**舊提案資料夾**也要在系統裡看得到、
 * 載得到、還能繼續往裡面丟檔案。所以這裡列的是 root 底下的**所有**資料夾：
 * - 已連結：`crm_projects.proposal_folder_name` 指到它（帶專案名/客戶）
 * - 未連結：磁碟上有、系統沒登記（過去的那些）
 *
 * owner 2026-08-07 追加三件事：
 * - **逐層瀏覽**：舊資料夾常有 `@Data/03_製片檔案PPM/…` 好幾層，攤平成一長串
 *   路徑既看不出結構、又要整棵樹掃 NAS。改成一次列一層 + 麵包屑。
 * - **改名**：連結專案的一起改（deck_url 同步，後端 rename_and_remap 保證同進退）。
 * - **連結專案**：連上之後它就是該專案的提案資產夾（簡報落這裡、專案改名跟著改）。
 *
 * 檔案清單純掃磁碟即時列（後端不建索引表）。
 */

import { esc } from '../website/website-utils.js';
import { fmtSize } from '../../js/shared/clip_utils.js';
import { authDownload, bearerHeader, folderCrumbsHtml, inputUploadItems,
         uploadItems, uploadProgress, wireFileDrop } from '../../js/shared/utils.js';
import { projectOptionsHtml } from '../crm/crm-utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm/proposal-assets';

let _folders = [];       // 整份清單（overview 不分頁，搜尋就地過濾）
let _open = '';          // 目前展開的資料夾名
let _rel = '';           // 展開的資料夾內目前在看哪一層（'' = 最外層）
let _level = {};         // `folder|rel` → 已載入的該層內容（搜尋重畫時不必再打 NAS）
let _action = '';        // 展開的資料夾上開著哪個操作列：'' / 'rename' / 'link'
let _projects = null;    // 連結專案的下拉清單（懶載快取）

const _key = (folder, rel) => `${folder}|${rel}`;

export async function openFolderBrowser(mountOverlay) {
    // 模組層狀態 —— 不重設的話重開會停在上次的資料夾/層級（或「載入中…」）
    _folders = [];
    _level = {};
    _open = '';
    _rel = '';
    _action = '';
    const ov = mountOverlay(`
        <div class="prop-panel" style="width:min(900px,96vw);">
            <div class="prop-panel-head">
                <h3>📁 提案資產資料夾</h3>
                <button class="prop-close" title="關閉">✕</button>
            </div>
            <div class="prop-panel-body" style="display:block;">
                <div class="prop-toolbar" style="margin:0 0 10px;">
                    <input id="pf-q" type="text" placeholder="🔍 搜尋資料夾或專案名…" style="width:220px;">
                    <span id="pf-root" class="prop-note" style="margin-left:auto;"></span>
                </div>
                <div id="pf-list"><div class="prop-note">載入中…</div></div>
            </div>
        </div>`);
    _bind(ov);
    await _load(ov);
}

function _bind(ov) {
    // 清單一次全撈（overview 不分頁）→ 搜尋就地過濾，不必每個按鍵都打伺服器
    // （後端那趟還會重跑一次 CrmProject×Client 的 join）
    ov.querySelector('#pf-q').addEventListener('input', () => _render(ov));
    // 事件委派：列表每次重畫
    ov.querySelector('#pf-list').addEventListener('click', (e) => _onClick(ov, e));
}

function _onClick(ov, e) {
    // 操作鈕在資料夾標題列裡（標題列本身是展開/收合），所以先攔它們
    const act = e.target.closest('[data-act]');
    if (act) {
        const want = act.dataset.act;
        const name = act.closest('[data-folder]').dataset.folder;
        if (_open !== name) { _open = name; _rel = ''; }
        _action = _action === want ? '' : want;
        _render(ov);
        return;
    }
    const nav = e.target.closest('[data-dir],[data-crumb]');
    if (nav) {
        _rel = nav.dataset.dir ?? nav.dataset.crumb;
        _render(ov);
        return;
    }
    const file = e.target.closest('[data-file]');
    if (file) {
        const rel = file.dataset.file;
        authDownload(
            `${API}/folder/file?folder=${encodeURIComponent(_open)}&rel=${encodeURIComponent(rel)}`,
            rel);
        return;
    }
    const head = e.target.closest('[data-folder]');
    if (!head) return;
    const name = head.dataset.folder;
    _open = _open === name ? '' : name;      // 再點一次收合
    _rel = '';
    _action = '';
    _render(ov);                              // 展開後由 _render 負責載/填
}

async function _load(ov) {
    const list = ov.querySelector('#pf-list');
    try {
        const d = await tfetch(`${API}/overview`);
        _folders = d.folders || [];
        ov.querySelector('#pf-root').textContent =
            (d.root || '（未設定根目錄）') + (d.root_set ? '' : ' — 目前搆不到');
    } catch (e) {
        list.innerHTML = `<div class="prop-note" style="color:#f87171;">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(ov);
}

function _render(ov) {
    const list = ov.querySelector('#pf-list');
    const ql = (ov.querySelector('#pf-q').value || '').trim().toLowerCase();
    const rows = ql
        ? _folders.filter(f => f.folder_name.toLowerCase().includes(ql)
            || (f.project_name || '').toLowerCase().includes(ql))
        : _folders;
    if (!rows.length) {
        list.innerHTML = `<div class="prop-note">${
            ql ? '沒有符合的資料夾' : '這個根目錄底下還沒有資料夾'}</div>`;
        return;
    }
    list.innerHTML = rows.map(f => {
        const open = f.folder_name === _open;
        const tag = f.missing_on_disk
            ? '<span class="prop-pill" style="color:#fbbf24;border-color:#fbbf24;">磁碟上找不到</span>'
            : f.linked
                ? `<span class="prop-pill">已連結</span><span class="m">${esc(f.project_name || '')}${
                    f.client_name ? ' · ' + esc(f.client_name) : ''}</span>`
                : '<span class="prop-pill" style="opacity:.6;">未連結</span>';
        return `
        <div style="border:1px solid #2a2a2a;border-radius:4px;margin-bottom:6px;">
            <div data-folder="${esc(f.folder_name)}"
                 style="display:flex;gap:8px;align-items:center;padding:8px 10px;cursor:pointer;">
                <span style="color:#8b8b8b;">${open ? '▾' : '▸'}</span>
                <span style="font-weight:600;">${esc(f.folder_name)}</span>
                ${tag}
                <span style="flex:1;"></span>
                ${f.missing_on_disk ? '' : `
                    <button data-act="rename" class="prop-btn ghost">改名</button>
                    <button data-act="link" class="prop-btn ghost">${f.linked ? '改連結' : '連結專案'}</button>`}
            </div>
            ${open ? `<div style="border-top:1px solid #242424;">
                <div id="pf-action">${_actionHtml(f)}</div>
                <div id="pf-crumbs" style="padding:6px 12px 0;font-size:12px;">${
                    folderCrumbsHtml(_open, _rel)}</div>
                <div style="padding:6px 12px;">
                    <button data-upload class="prop-btn ghost">＋ 上傳檔案</button>
                    <button data-upload-dir class="prop-btn ghost">＋ 上傳資料夾</button>
                    <button data-mkdir class="prop-btn ghost">＋ 新增資料夾</button>
                    <span class="prop-note" style="margin-left:8px;">也可以把檔案或整個資料夾拖進來</span>
                </div>
                <div id="pf-files" style="padding:0 0 4px;">
                    <div class="prop-note" style="padding:8px 12px;">載入中…</div>
                </div>
            </div>` : ''}
        </div>`;
    }).join('');
    if (!_open) return;
    _wireAction(ov);
    _wireDrop(ov);
    // 重畫（例如打字搜尋）後把已載入的這一層填回去 —— 不然展開中的資料夾會被
    // 洗成「載入中…」並卡在那，使用者得收合再展開、多打一次 NAS 掃描
    const cached = _level[_key(_open, _rel)];
    if (cached) _paint(ov, cached, _open, _rel);
    else _loadLevel(ov, _open, _rel);
}

// ── 改名 / 連結專案的操作列（就地展開，不另開視窗）──────────────
function _actionHtml(f) {
    if (_action === 'rename') {
        return `<div style="display:flex;gap:6px;align-items:center;padding:8px 12px;background:#161616;">
            <span class="prop-note">新資料夾名稱</span>
            <input id="pf-newname" type="text" value="${esc(f.folder_name)}" style="flex:1;">
            <button id="pf-rename-go" class="prop-btn">改名</button>
        </div>`;
    }
    if (_action === 'link') {
        return `<div style="padding:8px 12px;background:#161616;">
            <div class="prop-note" style="margin-bottom:6px;">
                連結後這個資料夾就是該專案的提案資產夾 —— 之後上傳的簡報會落在這裡，
                專案改名時資料夾也會跟著改名。${f.linked
                    ? `目前連結：<b>${esc(f.project_name || '')}</b>` : ''}
            </div>
            <div style="display:flex;gap:6px;align-items:center;">
                <select id="pf-proj" style="flex:1;"><option value="">載入專案清單…</option></select>
                <button id="pf-link-go" class="prop-btn">連結</button>
                ${f.linked ? '<button id="pf-unlink" class="prop-btn ghost">解除連結</button>' : ''}
            </div>
        </div>`;
    }
    return '';
}

function _wireAction(ov) {
    const folder = _open;
    ov.querySelector('#pf-rename-go')?.addEventListener('click', async () => {
        const name = ov.querySelector('#pf-newname').value.trim();
        if (!name || name === folder) { _action = ''; _render(ov); return; }
        try {
            const d = await tfetch(`${API}/folder/rename`,
                { method: 'POST', json: { folder, new_name: name } });
            _level = {};                 // 快取的 key 帶舊資料夾名，整份作廢
            _open = d.folder_name || name;
            _rel = '';
            _action = '';
            await _load(ov);
        } catch (e) { alert('改名失敗：' + (e.message || e)); }
    });

    const sel = ov.querySelector('#pf-proj');
    if (sel) {
        _loadProjects()
            .then(list => {
                if (!sel.isConnected) return;
                // 選項灌進去後由全域 select-upgrade 自動升級成可搜尋下拉（≥8 項）
                sel.innerHTML = projectOptionsHtml(list);
            })
            .catch(e => { sel.innerHTML = `<option value="">載入失敗：${esc(e.message || e)}</option>`; });
    }
    ov.querySelector('#pf-link-go')?.addEventListener('click', () => {
        const pid = ov.querySelector('#pf-proj').value;
        if (!pid) { alert('請先選一個專案'); return; }
        _link(ov, folder, pid);
    });
    ov.querySelector('#pf-unlink')?.addEventListener('click', () => {
        if (confirm('解除連結？資料夾與裡面的檔案都會留著，只是不再屬於這個專案。')) {
            _link(ov, folder, '');
        }
    });
}

async function _link(ov, folder, projectId) {
    try {
        await tfetch(`${API}/folder/link`,
            { method: 'POST', json: { folder, project_id: projectId } });
        _action = '';
        await _load(ov);         // 連結狀態來自 DB，重撈一次總覽最準
    } catch (e) { alert('連結失敗：' + (e.message || e)); }
}

function _loadProjects() {
    // 快取 **promise** 不是結果 —— 搜尋框每個按鍵都會 _render()，操作列開著時
    // 就等於在第一趟還沒回來前又發一次同樣的 join 查詢
    if (!_projects) _projects = tfetch('/api/v1/crm/projects').then(d => d.projects || []);
    return _projects;
}

// 上傳（按鈕 + 拖放）—— 未連結專案的「過去資料夾」也能丟檔（owner 指定）
function _wireDrop(ov) {
    const zone = ov.querySelector('#pf-files')?.parentElement;
    if (!zone) return;
    const folder = _open;
    const rel = _rel;
    // items = [{file, path}]；path 帶目錄結構時後端照著重建（拖／選整個資料夾）
    // 這裡不走 tfetch —— fetch 沒有上傳進度事件，大檔要有進度條
    const send = async (items) => {
        if (!items || !items.length) return;
        const ctrl = new AbortController();
        const bar = uploadProgress(zone, () => ctrl.abort());
        try {
            const d = await uploadItems(
                `${API}/folder/upload?folder=${encodeURIComponent(folder)}`
                + `&rel=${encodeURIComponent(rel)}`,
                items,
                { headers: bearerHeader(), signal: ctrl.signal,
                  onProgress: (l, t) => bar.update(l, t),
                  onUploaded: () => bar.finishing() });
            bar.remove();
            const bad = (d.skipped || []).map(s => `${s.filename}（${s.reason}）`);
            if (bad.length) alert('部分項目未上傳：\n' + bad.join('\n'));
            // 一個都沒存成時端點不回這一層的內容（省一次掃描）→ 畫面維持原樣
            if (d.files) _paint(ov, d, folder, rel);
        } catch (e) {
            bar.fail(e.aborted ? '已取消上傳' : '上傳失敗：' + (e.message || e));
        }
    };
    const pick = (asDir) => {
        const inp = document.createElement('input');
        inp.type = 'file';
        inp.multiple = true;
        if (asDir) inp.webkitdirectory = true;   // webkitRelativePath 會帶夾名
        inp.addEventListener('change', () => send(inputUploadItems(inp.files)));
        inp.click();
    };
    zone.querySelector('[data-upload]')?.addEventListener('click', () => pick(false));
    zone.querySelector('[data-upload-dir]')?.addEventListener('click', () => pick(true));
    wireFileDrop(zone, send);      // 拖資料夾進來會遞迴展開（含資料夾本身）

    zone.querySelector('[data-mkdir]')?.addEventListener('click', async () => {
        const name = prompt('新資料夾名稱（會建在你目前看的這一層）：');
        if (!name || !name.trim()) return;
        try {
            const d = await tfetch(`${API}/folder/mkdir?folder=${encodeURIComponent(folder)}`
                + `&rel=${encodeURIComponent(rel)}`,
                { method: 'POST', json: { name: name.trim() } });
            _paint(ov, d, folder, rel);
        } catch (e) { alert('建立失敗：' + (e.message || e)); }
    });
}

async function _loadLevel(ov, folder, rel) {
    const box = ov.querySelector('#pf-files');
    if (!box) return;
    try {
        const d = await tfetch(`${API}/folder/files?folder=${encodeURIComponent(folder)}`
            + `&rel=${encodeURIComponent(rel)}`);
        _paint(ov, d, folder, rel);
    } catch (e) {
        if (box.isConnected) {
            box.innerHTML = `<div class="prop-note" style="padding:8px 12px;color:#f87171;">載入失敗：${esc(e.message || e)}</div>`;
        }
    }
}

function _paint(ov, d, folder, rel) {
    _level[_key(folder, rel)] = d;
    const box = ov.querySelector('#pf-files');
    // 載入期間使用者可能已經走到別層 —— 舊回應不覆蓋新畫面
    if (!box || !box.isConnected || folder !== _open || rel !== _rel) return;
    const dirs = d.dirs || [];
    const files = d.files || [];
    if (!dirs.length && !files.length) {
        box.innerHTML = '<div class="prop-note" style="padding:8px 12px;">這個資料夾是空的</div>';
        return;
    }
    box.innerHTML = dirs.map(x => `
        <div data-dir="${esc(x.rel)}"
             style="display:flex;gap:8px;align-items:center;padding:5px 12px 5px 24px;
                    font-size:12.5px;cursor:pointer;">
            <span style="color:#8b8b8b;">▸</span>
            <span style="font-weight:600;">${esc(x.name)}</span>
        </div>`).join('')
        + files.map(f => `
        <div data-file="${esc(f.rel)}"
             style="display:flex;gap:8px;align-items:center;padding:5px 12px 5px 30px;font-size:12.5px;">
            <span style="flex:1;cursor:pointer;" title="下載">${esc(f.filename)}</span>
            <span style="color:#6b6b6b;">${fmtSize(f.size_bytes)}</span>
            <span style="color:#6b6b6b;">${esc(new Date(f.mtime * 1000).toISOString().slice(0, 10))}</span>
        </div>`).join('')
        + (d.truncated ? '<div class="prop-note" style="padding:6px 12px;color:#fbbf24;">項目過多，只顯示前 1000 筆</div>' : '');
}
