/**
 * crm-media-log.js — 業務管理 › 影像紀錄（跨專案總覽）Tab
 *
 * 集中管理各專案的劇組影像收集牆：列出有內容（或全部/已停用）的專案 + 圖/影片數
 * + 最近上傳 + 連結狀態，點「開啟」在 overlay 複用專案詳情的完整面板
 * （crm-projects-media.loadMediaTab，含上傳/QR/設定/刪除）—— 這裡不重寫面板，
 * 只多一個總覽入口。後端：GET /api/v1/crm/media-log/overview?q=&scope=。
 */

import { crmFetch as _fetch, esc as _esc, projectOptionsHtml } from './crm-utils.js';
import { loadMediaTab } from './crm-projects-media.js';

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
                <th>專案 / 資料夾</th><th>客戶</th>
                <th class="num">圖片</th><th class="num">影片</th>
                <th>最近上傳</th><th>狀態</th><th style="text-align:right;">動作</th>
            </tr></thead>
            <tbody>
            ${_items.map((it) => {
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
}

// ── 連結專案：孤兒資料夾 → 選一個 CRM 專案 ────────────────────────────────
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
        body.innerHTML = `<div class="cml-empty" style="color:#fca5a5;">專案載入失敗：${_esc(e.message || e)}</div>`;
        return;
    }
    body.innerHTML = `
        <div class="cml-link">
            <div class="cml-link-info">把資料夾 <b>${_esc(folderName)}</b> 的照片連結到專案 —— 連結後該資料夾裡的照片會匯入這個專案的影像紀錄（縮圖稍後自動產生）。</div>
            <select id="cml-link-proj" class="cml-search" style="width:100%;margin:12px 0;">
                ${projectOptionsHtml(projects)}
            </select>
            <div class="cml-link-actions">
                <button id="cml-link-cancel" class="cml-btn ghost">取消</button>
                <button id="cml-link-go" class="cml-btn">連結並匯入</button>
            </div>
            <div id="cml-link-msg" class="cml-link-msg"></div>
        </div>`;
    document.getElementById('cml-link-cancel').onclick = _closeOverlay;
    document.getElementById('cml-link-go').onclick = () => _doLink(folderName);
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
function _thumbUrl(rel) {
    return `${ADMIN_API}/media-log/folder/thumb?folder=${encodeURIComponent(_viewFolder)}&rel=${encodeURIComponent(rel)}`;
}
function _fileUrl(rel) {
    return `${ADMIN_API}/media-log/folder/file?folder=${encodeURIComponent(_viewFolder)}&rel=${encodeURIComponent(rel)}`;
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
