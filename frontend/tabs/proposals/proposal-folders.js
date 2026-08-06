/**
 * proposal-folders.js — 提案資產資料夾瀏覽器（提案庫 Tab 的「📁 資產資料夾」）
 *
 * owner 2026-08-06：NAS 上手工整理的**舊提案資料夾**也要在系統裡看得到、
 * 載得到、還能繼續往裡面丟檔案。所以這裡列的是 root 底下的**所有**資料夾：
 * - 已連結：`crm_projects.proposal_folder_name` 指到它（帶專案名/客戶）
 * - 未連結：磁碟上有、系統沒登記（過去的那些）
 *
 * 刻意**不做**「連結到專案」—— owner 選擇讓舊資料夾獨立存在。
 * 檔案清單純掃磁碟即時列（後端不建索引表）。
 */

import { esc } from '../website/website-utils.js';
import { fmtSize } from '../../js/shared/clip_utils.js';
import { authDownload, wireFileDrop } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm/proposal-assets';

let _folders = [];       // 整份清單（overview 不分頁，搜尋就地過濾）
let _open = '';          // 目前展開的資料夾名
let _filesOf = {};       // folder → 已載入的檔案清單（搜尋重畫時不必再打 NAS）

export async function openFolderBrowser(mountOverlay) {
    _folders = [];
    _filesOf = {};
    _open = '';          // 模組層狀態 —— 不重設的話重開會停在「載入檔案中…」
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
    ov.querySelector('#pf-list').addEventListener('click', async (e) => {
        const head = e.target.closest('[data-folder]');
        const fileRow = e.target.closest('[data-rel]');
        if (fileRow) {
            const { folder, rel } = fileRow.dataset;
            if (e.target.closest('[data-dl]')) {
                authDownload(
                    `${API}/folder/file?folder=${encodeURIComponent(folder)}&rel=${encodeURIComponent(rel)}`,
                    rel);
            }
            return;
        }
        if (!head) return;
        const name = head.dataset.folder;
        _open = _open === name ? '' : name;      // 再點一次收合
        _render(ov);                              // 展開後由 _render 負責載/填
    });
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
            </div>
            ${open ? `<div style="border-top:1px solid #242424;">
                <div style="padding:6px 12px;">
                    <button data-upload class="prop-btn ghost">＋ 上傳檔案到這個資料夾</button>
                    <span class="prop-note" style="margin-left:8px;">也可以把檔案拖進來</span>
                </div>
                <div id="pf-files" style="padding:0 0 4px;">
                    <div class="prop-note" style="padding:8px 12px;">載入檔案中…</div>
                </div>
            </div>` : ''}
        </div>`;
    }).join('');
    if (!_open) return;
    _wireDrop(ov, _open);
    // 重畫（例如打字搜尋）後把已載入的檔案填回去 —— 不然展開中的資料夾會被
    // 洗成「載入檔案中…」並卡在那，使用者得收合再展開、多打一次 NAS 全掃
    const cached = _filesOf[_open];
    if (cached) _paintFiles(ov, _open, cached.files, cached.truncated);
    else _loadFiles(ov, _open);
}

// 上傳（按鈕 + 拖放）—— 未連結專案的「過去資料夾」也能丟檔（owner 指定）
function _wireDrop(ov, folder) {
    const zone = ov.querySelector('#pf-files')?.parentElement;
    if (!zone) return;
    const send = async (fileList) => {
        if (!fileList || !fileList.length) return;
        const fd = new FormData();
        for (const f of fileList) fd.append('files', f);
        try {
            // 走 tfetch（不是手刻 fetch）—— 錯誤形狀與 warning 浮出都掛在它身上；
            // 它只帶 Accept + Authorization，FormData 的 boundary 不會被蓋掉
            const d = await tfetch(`${API}/folder/upload?folder=${encodeURIComponent(folder)}`,
                { method: 'POST', body: fd });
            const bad = (d.skipped || []).map(s => `${s.filename}（${s.reason}）`);
            if (bad.length) alert('部分檔案未上傳：\n' + bad.join('\n'));
            // 一個都沒存成時端點不回 files（省一次全掃）→ 畫面維持原樣
            if (d.files) _paintFiles(ov, folder, d.files, d.truncated);
        } catch (e) { alert('上傳失敗：' + (e.message || e)); }
    };
    zone.querySelector('[data-upload]')?.addEventListener('click', () => {
        const inp = document.createElement('input');
        inp.type = 'file';
        inp.multiple = true;
        inp.addEventListener('change', () => send(inp.files));
        inp.click();
    });
    wireFileDrop(zone, send);
}

async function _loadFiles(ov, folder) {
    const box = ov.querySelector('#pf-files');
    if (!box) return;
    try {
        const d = await tfetch(`${API}/folder/files?folder=${encodeURIComponent(folder)}`);
        _paintFiles(ov, folder, d.files || [], d.truncated);
    } catch (e) {
        if (box.isConnected) {
            box.innerHTML = `<div class="prop-note" style="padding:8px 12px;color:#f87171;">載入失敗：${esc(e.message || e)}</div>`;
        }
    }
}

function _paintFiles(ov, folder, files, truncated) {
    _filesOf[folder] = { files, truncated };
    const box = ov.querySelector('#pf-files');
    if (!box || !box.isConnected) return;
    box.innerHTML = files.length ? files.map(f => `
        <div data-folder="${esc(folder)}" data-rel="${esc(f.rel)}"
             style="display:flex;gap:8px;align-items:center;padding:5px 12px 5px 30px;font-size:12.5px;">
            <span data-dl style="flex:1;cursor:pointer;" title="下載">${esc(f.rel)}</span>
            <span style="color:#6b6b6b;">${fmtSize(f.size_bytes)}</span>
            <span style="color:#6b6b6b;">${esc(new Date(f.mtime * 1000).toISOString().slice(0, 10))}</span>
        </div>`).join('')
        + (truncated ? '<div class="prop-note" style="padding:6px 12px;color:#fbbf24;">檔案過多，只顯示前 1000 筆</div>' : '')
        : '<div class="prop-note" style="padding:8px 12px;">這個資料夾是空的</div>';
}
