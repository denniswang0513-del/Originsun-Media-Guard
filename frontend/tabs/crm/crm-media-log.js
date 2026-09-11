/**
 * crm-media-log.js — 業務管理 › 影像紀錄（跨專案總覽）Tab
 *
 * 集中管理各專案的劇組影像收集牆：列出有內容（或全部/已停用）的專案 + 圖/影片數
 * + 最近上傳 + 連結狀態，點「開啟」在 overlay 複用專案詳情的完整面板
 * （crm-projects-media.loadMediaTab，含上傳/QR/設定/刪除）—— 這裡不重寫面板，
 * 只多一個總覽入口。後端：GET /api/v1/crm/media-log/overview?q=&scope=。
 */

import { crmFetch as _fetch, esc as _esc, projectOptionsHtml, searchableSelect, createSortable, sortableTh } from './crm-utils.js';
import { loadMediaTab } from './crm-projects-media.js';
import { copyText } from '../../js/shared/utils.js';

const ADMIN_API = '/api/v1/crm';   // 孤兒資料夾 thumb/file 直接當 <img>/<a> src（非 crmFetch JSON）

let _root = null;
let _items = [];
let _scope = 'content';        // content(預設) / all / orphan
let _q = '';
let _debounce = null;
let _projects = null;          // 連結 picker 用（懶載快取）
let _viewFiles = [];           // 目前檢視的孤兒資料夾檔案清單（lightbox 導覽用）
let _viewFolder = '';          // 目前檢視的資料夾名
let _lbIdx = -1;               // lightbox 索引
let _lbKey = null;             // lightbox 鍵盤 handler

// ── Sortable list header（共用 createSortable，預設不排序維持後端順序）──
const _sorter = createSortable({
    storageKey: 'crm_medialog_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'cml-list',
    onChange: () => _renderList(),
    getters: {
        name:   it => (it.linked ? it.project_name : it.folder_name) || '',
        client: it => it.client_name || '',
        images: it => Number(it.images) || 0,
        videos: it => Number(it.videos) || 0,
        last:   it => it.last_upload || '',
        status: it => (it.linked ? (it.enabled ? '啟用' : '已停用') : '未連結'),
    },
});

export async function initCrmMediaLogTab() {
    _root = document.getElementById('cml-root');
    if (!_root) return;
    _renderShell();
    await _refresh();
}

function _renderShell() {
    document.getElementById('cml-content').innerHTML = `
        <h2>影像紀錄</h2>
        <div class="cml-sub">各專案的劇組影像收集牆（劇照 / 花絮）。預設根目錄底下有照片的資料夾都會出現。已連結專案點「開啟」管理；未連結的舊側拍可直接「檢視照片」，或「連結專案」把整夾收進某個專案。</div>
        <div class="cml-bar">
            <input id="cml-q" class="cml-search" type="text" placeholder="搜尋專案 / 資料夾名稱…" value="${_esc(_q)}">
            <select id="cml-scope">
                <option value="content"${_scope === 'content' ? ' selected' : ''}>有內容</option>
                <option value="all"${_scope === 'all' ? ' selected' : ''}>全部</option>
                <option value="orphan"${_scope === 'orphan' ? ' selected' : ''}>未連結</option>
            </select>
            <button id="cml-refresh" class="cml-btn ghost">重新整理</button>
            <button id="cml-newfolder" class="cml-btn">新增資料夾</button>
        </div>
        <div id="cml-list"></div>
        <div id="cml-overlay"><div class="cml-panel">
            <div class="cml-panel-head">
                <div class="t" id="cml-panel-title"></div>
                <button class="cml-close" id="cml-close" title="關閉">&times;</button>
            </div>
            <div class="cml-panel-body" id="cml-panel-body"></div>
        </div></div>`;

    document.getElementById('cml-q').oninput = (e) => {
        _q = e.target.value;
        clearTimeout(_debounce);
        _debounce = setTimeout(_refresh, 300);
    };
    document.getElementById('cml-scope').onchange = (e) => { _scope = e.target.value; _refresh(); };
    document.getElementById('cml-refresh').onclick = _refresh;
    document.getElementById('cml-newfolder').onclick = _openNewFolder;
    document.getElementById('cml-close').onclick = _closeOverlay;
    document.getElementById('cml-overlay').onclick = (e) => {
        if (e.target.id === 'cml-overlay') _closeOverlay();   // 點背景關閉
    };
}

async function _refresh() {
    const list = document.getElementById('cml-list');
    if (!list) return;
    list.innerHTML = '<div class="cml-empty">載入中…</div>';
    try {
        const params = new URLSearchParams({ scope: _scope });
        if (_q.trim()) params.set('q', _q.trim());
        _items = (await _fetch(`/media-log/overview?${params}`)).items || [];
    } catch (e) {
        list.innerHTML = `<div class="cml-empty" style="color:#fca5a5;">載入失敗：${_esc(e.message || String(e))}</div>`;
        return;
    }
    _renderList();
}

function _fmtDate(iso) {
    if (!iso) return '—';
    return iso.slice(0, 10).replace(/-/g, '/');
}

function _renderList() {
    const list = document.getElementById('cml-list');
    if (!_items.length) {
        const hint = _scope === 'orphan'
            ? '沒有未連結的資料夾。'
            : _scope === 'content'
                ? '目前沒有已上傳內容的影像紀錄。把照片丟進根目錄的子資料夾、或到專案詳情產生上傳連結。'
                : '沒有符合的影像紀錄。';
        list.innerHTML = `<div class="cml-empty">${hint}</div>`;
        return;
    }
    list.innerHTML = `
        <table>
            <thead><tr>
                ${sortableTh('name', '專案 / 資料夾')}${sortableTh('client', '客戶')}
                ${sortableTh('images', '圖片', 'class="num"')}${sortableTh('videos', '影片', 'class="num"')}
                ${sortableTh('last', '最近上傳')}${sortableTh('status', '狀態')}<th style="text-align:right;">動作</th>
            </tr></thead>
            <tbody>
            ${_sorter.sorted(_items).map((it) => {
                const zero = it.total === 0 ? ' zero' : '';
                // 已連結：顯示專案名 + 開啟；孤兒：顯示資料夾名 + 連結專案
                const nameCell = it.linked
                    ? `<span class="cml-proj">${_esc(it.project_name)}</span>`
                    : `<span class="cml-folder" title="根目錄底下的資料夾">${_esc(it.folder_name)}</span>`;
                const statusCell = it.linked
                    ? `<span class="cml-pill ${it.enabled ? 'on' : 'off'}">${it.enabled ? '啟用' : '已停用'}</span>`
                    : '<span class="cml-pill orphan">未連結</span>';
                const action = it.linked
                    ? `<button class="cml-btn sm" data-open="${_esc(it.project_id)}" data-name="${_esc(it.project_name)}">開啟</button>`
                    : `<button class="cml-btn sm" data-view="${_esc(it.folder_name)}">檢視照片</button>
                       <button class="cml-btn sm ghost" data-qr="${_esc(it.folder_name)}">${it.has_token ? '收集連結' : '產生 QR'}</button>
                       <button class="cml-btn sm ghost" data-link="${_esc(it.folder_name)}">連結專案</button>`;
                const plus = it.capped ? '+' : '';   // 大夾計數到上限 → 顯示 999+
                return `<tr>
                    <td>${nameCell}</td>
                    <td class="cml-client">${_esc(it.client_name) || '—'}</td>
                    <td class="num cml-count${zero}"><b>${it.images}${plus}</b></td>
                    <td class="num cml-count${zero}"><b>${it.videos}${plus}</b></td>
                    <td class="cml-date">${_fmtDate(it.last_upload)}</td>
                    <td>${statusCell}</td>
                    <td style="text-align:right;">${action}</td>
                </tr>`;
            }).join('')}
            </tbody>
        </table>`;
    list.querySelectorAll('button[data-open]').forEach((b) => {
        b.onclick = () => _openPanel(b.dataset.open, b.dataset.name);
    });
    list.querySelectorAll('button[data-link]').forEach((b) => {
        b.onclick = () => _openLinkPicker(b.dataset.link);
    });
    list.querySelectorAll('button[data-view]').forEach((b) => {
        b.onclick = () => _openFolderViewer(b.dataset.view);
    });
    list.querySelectorAll('button[data-qr]').forEach((b) => {
        b.onclick = () => _openQr(b.dataset.qr);
    });
    _sorter.attach();
}

// ── 連結專案：孤兒資料夾 → 選一個現有 CRM 專案（專案在 CRM 建，這裡只連結）──────
async function _loadProjects() {
    if (_projects) return _projects;
    const d = await _fetch('/projects');
    _projects = d.projects || [];
    return _projects;
}

async function _openLinkPicker(folderName) {
    const ov = document.getElementById('cml-overlay');
    document.getElementById('cml-panel-title').textContent = '連結專案';
    const body = document.getElementById('cml-panel-body');
    body.innerHTML = '<div class="cml-empty">載入專案清單…</div>';
    ov.classList.add('on');
    let projects;
    try {
        projects = await _loadProjects();
    } catch (e) {
        body.innerHTML = `<div class="cml-empty" style="color:#fca5a5;">載入失敗：${_esc(e.message || e)}</div>`;
        return;
    }
    body.innerHTML = `
        <div class="cml-link">
            <div class="cml-link-info">把資料夾 <b>${_esc(folderName)}</b> 的照片收進一個現有專案 —— 連結後資料夾裡的照片（含用 QR 收到的）會匯入該專案的影像紀錄。<br>（新專案請在 CRM「專案管理」建立，建好後回這裡連結。）</div>
            <select id="cml-link-proj" style="width:100%;margin:12px 0;">${projectOptionsHtml(projects)}</select>
            <div class="cml-link-actions">
                <button id="cml-link-cancel" class="cml-btn ghost">取消</button>
                <button id="cml-link-go" class="cml-btn">連結並匯入</button>
            </div>
            <div id="cml-link-msg" class="cml-link-msg"></div>
        </div>`;
    document.getElementById('cml-link-cancel').onclick = _closeOverlay;
    document.getElementById('cml-link-go').onclick = () => _doLink(folderName);
    // 專案下拉可打字搜尋（明確呼叫 → SPA 與內嵌頁都適用；樣式在 crm-media-log.html scoped）
    searchableSelect(document.getElementById('cml-link-proj'), { placeholder: '搜尋專案…' });
}

// ── 新增資料夾（工具列）：在根目錄開一個空夾現場收照（產 QR）；專案你之後另外建再連結 ──
async function _openNewFolder() {
    const ov = document.getElementById('cml-overlay');
    document.getElementById('cml-panel-title').textContent = '新增資料夾';
    const body = document.getElementById('cml-panel-body');
    ov.classList.add('on');
    body.innerHTML = `
        <div class="cml-link">
            <div class="cml-link-info">在影像紀錄根目錄底下開一個新資料夾，馬上就能產 QR 給劇組現場收照。<b>專案之後你再另外建、用「連結專案」把整夾收進去即可。</b></div>
            <div class="cml-link-sec">資料夾名稱</div>
            <input id="cml-nf-name" class="cml-search" style="width:100%;" placeholder="例：20260726_新案側拍">
            <div class="cml-link-actions">
                <button id="cml-nf-cancel" class="cml-btn ghost">取消</button>
                <button id="cml-nf-go" class="cml-btn">建立並產生 QR</button>
            </div>
            <div id="cml-nf-msg" class="cml-link-msg"></div>
        </div>`;
    document.getElementById('cml-nf-cancel').onclick = _closeOverlay;
    document.getElementById('cml-nf-go').onclick = _doCreateFolder;
    const inp = document.getElementById('cml-nf-name');
    inp.focus();
    inp.onkeydown = (e) => { if (e.key === 'Enter') _doCreateFolder(); };
}

async function _doCreateFolder() {
    const name = document.getElementById('cml-nf-name').value.trim();
    const msg = document.getElementById('cml-nf-msg');
    if (!name) { msg.textContent = '請輸入資料夾名稱'; msg.className = 'cml-link-msg err'; return; }
    const go = document.getElementById('cml-nf-go');
    go.disabled = true;
    msg.textContent = '建立中…'; msg.className = 'cml-link-msg';
    try {
        const r = await _fetch('/media-log/folder', {
            method: 'POST', body: JSON.stringify({ name }),
        });
        // 建立端點已一併 mint token（空夾也會在「未連結」列表出現、之後連結得到）→
        // 直接用回應渲染 QR，不用再多打一次 folder/token。
        _renderQr(r.folder_name || name, r);
    } catch (e) {
        msg.textContent = e.message || String(e);
        msg.className = 'cml-link-msg err';
        go.disabled = false;
    }
}

// ── 孤兒資料夾：產生/開啟免專案收集連結（QR）────────────────────────────
async function _openQr(folderName) {
    const ov = document.getElementById('cml-overlay');
    document.getElementById('cml-panel-title').textContent = '收集連結 / QR';
    const body = document.getElementById('cml-panel-body');
    body.innerHTML = '<div class="cml-empty">產生連結…</div>';
    ov.classList.add('on');
    try {
        _renderQr(folderName, await _fetch('/media-log/folder/token', {
            method: 'POST', body: JSON.stringify({ folder_name: folderName }),
        }));
    } catch (e) {
        body.innerHTML = `<div class="cml-empty" style="color:#fca5a5;">產生失敗：${_esc(e.message || e)}</div>`;
    }
}

// 把 {token, share_url, linked} 渲染成 QR 面板。_openQr（既有孤兒）與新增資料夾共用，
// 後者用建立回應直接渲染、不再多打一次 folder/token。
function _renderQr(folderName, d) {
    document.getElementById('cml-panel-title').textContent = '收集連結 / QR';
    const body = document.getElementById('cml-panel-body');
    const share = /^https?:\/\//i.test(d.share_url || '') ? d.share_url : location.origin + (d.share_url || '');
    const qrSrc = `${ADMIN_API}/public/media-log/${encodeURIComponent(d.token)}/qr?base=${encodeURIComponent(location.origin)}`;
    const note = d.linked
        ? '（此資料夾已連結專案，用的是專案的收集連結）'
        : '之後可「連結專案」或「開新專案」把整夾（含這些照片）收進去。';
    body.innerHTML = `
        <div class="cml-link">
            <div class="cml-link-info">資料夾 <b>${_esc(folderName)}</b> 的現場收集連結：把 QR 或連結給劇組，<b>免登入</b>就能上傳劇照 / 花絮。${note}</div>
            <div class="cml-qr-box"><img class="cml-qr" src="${_esc(qrSrc)}" alt="收集連結 QR" width="180" height="180"></div>
            <input id="cml-qr-url" class="cml-search" readonly value="${_esc(share)}" style="width:100%;">
            <div class="cml-link-actions">
                <button id="cml-qr-copy" class="cml-btn">複製連結</button>
                <button id="cml-qr-close" class="cml-btn ghost">關閉</button>
            </div>
            <div id="cml-qr-msg" class="cml-link-msg"></div>
        </div>`;
    document.getElementById('cml-qr-close').onclick = _closeOverlay;
    document.getElementById('cml-qr-copy').onclick = async () => {
        const m = document.getElementById('cml-qr-msg');
        // copyText 自帶三層退路（內網 http 上 navigator.clipboard 不存在）
        if (await copyText(share)) {
            m.textContent = '已複製連結'; m.className = 'cml-link-msg ok';
        } else {
            const i = document.getElementById('cml-qr-url'); i.select();
            m.textContent = '請手動複製（Ctrl+C）'; m.className = 'cml-link-msg';
        }
    };
}

async function _doLink(folderName) {
    const sel = document.getElementById('cml-link-proj');
    const msg = document.getElementById('cml-link-msg');
    const projectId = sel.value;
    if (!projectId) { msg.textContent = '請先選擇專案'; msg.className = 'cml-link-msg err'; return; }
    const go = document.getElementById('cml-link-go');
    go.disabled = true;
    msg.textContent = '連結中…'; msg.className = 'cml-link-msg';
    try {
        const r = await _fetch('/media-log/link', {
            method: 'POST',
            body: JSON.stringify({ folder_name: folderName, project_id: projectId }),
        });
        msg.textContent = `已連結，匯入 ${r.imported || 0} 個檔案`;
        msg.className = 'cml-link-msg ok';
        setTimeout(_closeOverlay, 900);   // 關閉後 _refresh 會重抓總覽
    } catch (e) {
        msg.textContent = e.message || String(e);
        msg.className = 'cml-link-msg err';
        go.disabled = false;
    }
}

// ── 孤兒資料夾唯讀檢視（不連結專案也能看照片）──────────────────────────
// ⚠ 個人工作台 frontend/my.html 有一份平行實作（官網白底 + 傳統 script）；主題與
//   模組系統不同故各自維護，改這裡的檢視/燈箱行為時記得同步那邊（mlRenderGrid 等）。
// <img>/<video> src 無法帶 Authorization header → 端點也吃 ?token= query（同 JWT）。
function _authTok() { return encodeURIComponent(localStorage.getItem('auth_token') || ''); }
function _thumbUrl(rel) {
    return `${ADMIN_API}/media-log/folder/thumb?folder=${encodeURIComponent(_viewFolder)}&rel=${encodeURIComponent(rel)}&token=${_authTok()}`;
}
function _fileUrl(rel) {
    return `${ADMIN_API}/media-log/folder/file?folder=${encodeURIComponent(_viewFolder)}&rel=${encodeURIComponent(rel)}&token=${_authTok()}`;
}

async function _openFolderViewer(folderName) {
    const ov = document.getElementById('cml-overlay');
    document.getElementById('cml-panel-title').innerHTML =
        `影像紀錄 <small>${_esc(folderName)}（未連結資料夾）</small>`;
    const body = document.getElementById('cml-panel-body');
    body.innerHTML = '<div class="cml-empty">載入資料夾照片…</div>';
    ov.classList.add('on');
    let data;
    try {
        data = await _fetch(`/media-log/folder/files?folder=${encodeURIComponent(folderName)}`);
    } catch (e) {
        body.innerHTML = `<div class="cml-empty" style="color:#fca5a5;">載入失敗：${_esc(e.message || String(e))}</div>`;
        return;
    }
    _viewFolder = folderName;
    _viewFiles = data.files || [];
    _renderViewer(body, data);
}

function _renderViewer(body, data) {
    if (!_viewFiles.length) {
        body.innerHTML = '<div class="cml-empty">這個資料夾裡沒有照片或影片。</div>';
        return;
    }
    const info = `共 ${data.count} 個檔案${data.truncated ? `（只顯示前 ${_viewFiles.length} 個）` : ''}`
        + '　·　點縮圖看原圖，或「連結專案」把整夾收進某個專案。';
    body.innerHTML = `<div class="cml-view-info">${_esc(info)}</div>
        <div class="cml-view-grid">
        ${_viewFiles.map((f, i) => {
            let ext = String(f.filename || '').split('.').pop().toUpperCase();
            if (!ext || ext.length > 5) ext = 'FILE';
            const badge = f.media_type === 'video' ? '<span class="cml-vbadge">影片</span>' : '';
            return `<div class="cml-vitem" data-idx="${i}" title="${_esc(f.filename || '')}">
                <img class="cml-vthumb" loading="lazy" alt="" data-ext="${_esc(ext)}" src="${_esc(_thumbUrl(f.rel))}">
                ${badge}
                <div class="cml-vname">${_esc(f.filename || '')}</div>
            </div>`;
        }).join('')}
        </div>`;
    body.querySelectorAll('img.cml-vthumb').forEach((img) => {
        img.addEventListener('error', () => {
            const div = document.createElement('div');
            div.className = 'cml-vthumb-fallback';
            div.textContent = img.dataset.ext || 'FILE';
            img.replaceWith(div);
        }, { once: true });
    });
    body.querySelector('.cml-view-grid').addEventListener('click', (e) => {
        const item = e.target.closest('.cml-vitem');
        if (item) _openLightbox(Number(item.dataset.idx));
    });
}

function _openLightbox(idx) {
    _closeViewerLightbox();
    if (!_viewFiles.length) return;
    _lbIdx = Math.max(0, Math.min(idx, _viewFiles.length - 1));
    const lb = document.createElement('div');
    lb.id = 'cml-lightbox';
    lb.className = 'cml-lb';
    lb.addEventListener('click', (e) => { if (e.target === lb) _closeViewerLightbox(); });
    document.body.appendChild(lb);
    _renderLightbox();
    _lbKey = (e) => {
        if (e.key === 'Escape') _closeViewerLightbox();
        else if (e.key === 'ArrowLeft') _lbStep(-1);
        else if (e.key === 'ArrowRight') _lbStep(1);
    };
    document.addEventListener('keydown', _lbKey);
}

function _renderLightbox() {
    const lb = document.getElementById('cml-lightbox');
    if (!lb) return;
    const f = _viewFiles[_lbIdx];
    if (!f) { _closeViewerLightbox(); return; }
    const src = _fileUrl(f.rel);
    const media = f.media_type === 'video'
        ? `<video src="${_esc(src)}" controls class="cml-lb-media"></video>`
        : `<img src="${_esc(src)}" alt="" class="cml-lb-media">`;
    const multi = _viewFiles.length > 1;
    lb.innerHTML = `
        <button class="cml-lb-close" data-lb="close" title="關閉">&times;</button>
        ${multi ? '<button class="cml-lb-nav cml-lb-prev" data-lb="prev" title="上一個">‹</button>' : ''}
        <div class="cml-lb-body">${media}
            <div class="cml-lb-cap">${_esc(f.filename || '')} · ${_lbIdx + 1}/${_viewFiles.length}
                · <a href="${_esc(src)}" download="${_esc(f.filename || '')}">下載原檔</a></div>
        </div>
        ${multi ? '<button class="cml-lb-nav cml-lb-next" data-lb="next" title="下一個">›</button>' : ''}`;
    lb.querySelectorAll('[data-lb]').forEach((btn) => btn.addEventListener('click', () => {
        const act = btn.dataset.lb;
        if (act === 'close') _closeViewerLightbox();
        else if (act === 'prev') _lbStep(-1);
        else if (act === 'next') _lbStep(1);
    }));
}

function _lbStep(delta) {
    if (!_viewFiles.length) return;
    _lbIdx = (_lbIdx + delta + _viewFiles.length) % _viewFiles.length;
    _renderLightbox();
}

function _closeViewerLightbox() {
    const lb = document.getElementById('cml-lightbox');
    if (lb) lb.remove();
    if (_lbKey) { document.removeEventListener('keydown', _lbKey); _lbKey = null; }
    _lbIdx = -1;
}

// ── overlay：複用專案詳情的影像紀錄面板 ──────────────────────────────────
async function _openPanel(projectId, projectName) {
    const ov = document.getElementById('cml-overlay');
    document.getElementById('cml-panel-title').innerHTML =
        `影像紀錄 <small>${_esc(projectName)}</small>`;
    const body = document.getElementById('cml-panel-body');
    ov.classList.add('on');
    await loadMediaTab(projectId, body);   // 面板自帶載入+渲染
}

function _closeOverlay() {
    _closeViewerLightbox();   // 檢視器 lightbox 是 body 層獨立元素，一併收掉
    const ov = document.getElementById('cml-overlay');
    ov.classList.remove('on');
    document.getElementById('cml-panel-body').innerHTML = '';   // 卸載面板（停掉其輪詢/狀態）
    _refresh();   // 關閉後重抓總覽（面板內可能改了內容/停用狀態）
}
