/**
 * crm-projects-media.js — 專案詳情「影像紀錄」Tab
 *
 * 後台側的劇組影像收集牆：設定原檔資料夾（全域共用根目錄）與分類、
 * 產生/重置公開上傳連結（/media-log.html?token=…）、後台直接上傳（XHR 進度）、
 * 縮圖牆（分類篩選 + 下載/刪除 + lightbox）。
 *
 * 後端契約（另組 agent 實作）：
 *   GET    /api/v1/crm/projects/{id}/media-log            → token/share_url/root/root_set/project_folder/categories/files/enabled
 *   POST   /api/v1/crm/projects/{id}/media-log/token      → 重置連結
 *   POST   /api/v1/crm/projects/{id}/media-log/enabled    → {"enabled": bool} → {"ok","enabled"} 啟用/停用公開連結
 *   GET    /api/v1/crm/public/media-log/{token}/qr?base=… → QR code PNG（免認證，token 即授權，直接當 <img src>）
 *   POST   /api/v1/crm/projects/{id}/media-log/settings   → {root?, categories?}
 *   DELETE /api/v1/crm/media-log/files/{fileId}
 *   POST   /api/v1/crm/public/media-log/{token}/upload    → multipart file/category/uploader_name（後台也走此端點）
 *   GET    /api/v1/crm/public/media-log/{token}/file/{fileId} → 原檔下載
 */

import { crmFetch as _fetch, esc as _esc, crmToast as _toast } from './crm-utils.js';

const PUBLIC_API = '/api/v1/crm/public/media-log';

// ── 模組狀態 ─────────────────────────────────────────────────
let _host = null;
let _projectId = null;
let _data = null;        // GET media-log 回應
let _cats = [];          // 分類編輯中的本地副本（按「儲存分類」才 POST）
let _filter = '';        // 縮圖牆分類篩選（'' = 全部）
let _uploads = [];       // 上傳佇列 [{file, name, cat, pct, status, err}]
let _uploading = false;
let _lbIdx = -1;         // lightbox 目前索引（對 _filtered() 而言）
let _lbKeyHandler = null;

// ── 入口 ─────────────────────────────────────────────────────

export async function loadMediaTab(projectId, host) {
    _host = host;
    _projectId = projectId;
    _filter = '';
    _closeLightbox();
    host.innerHTML = '<div class="crm-empty">載入中...</div>';
    try {
        _data = await _fetch(`/projects/${projectId}/media-log`);
    } catch (e) {
        host.innerHTML = `<div class="crm-empty" style="color:#fca5a5;">影像紀錄載入失敗: ${_esc(e.message || String(e))}</div>`;
        return;
    }
    _cats = Array.isArray(_data.categories) ? [..._data.categories] : [];
    _uploads = [];
    _render();
}

// ── 主渲染 ───────────────────────────────────────────────────

function _render() {
    _injectStyle();
    const d = _data;
    const shareUrl = _absShareUrl(d.share_url);

    _host.innerHTML = `
    <div class="pm-card">
      ${d.root_set ? '' : '<div class="pm-warn">尚未設定資料夾，公開頁無法上傳</div>'}
      <div class="pm-card-title">原檔資料夾</div>
      <div class="pm-row">
        <input id="pm-root" type="text" class="crm-input" style="flex:1;" value="${_esc(d.root || '')}" placeholder="例：\\\\NAS\\media-log 或 D:\\MediaLog">
        <button id="pm-root-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存</button>
        <button id="pm-open-folder" class="crm-btn crm-btn-secondary crm-btn-sm">開啟資料夾</button>
      </div>
      <div class="pm-hint">所有專案共用根資料夾，各專案自動建立子資料夾</div>
      <div class="pm-card-title" style="margin-top:14px;">分類管理</div>
      <div id="pm-chips" class="pm-chips"></div>
      <div class="pm-row" style="margin-top:8px;">
        <input id="pm-cat-new" type="text" class="crm-input" style="flex:1;" placeholder="新增分類名稱，例：花絮">
        <button id="pm-cat-add" class="crm-btn crm-btn-secondary crm-btn-sm">新增</button>
        <button id="pm-cat-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存分類</button>
      </div>
    </div>

    <div class="pm-card">
      <div class="pm-card-title">公開上傳連結</div>
      <label class="pm-toggle"><input id="pm-enabled" type="checkbox"${d.enabled !== false ? ' checked' : ''}> 啟用公開連結</label>
      <div id="pm-off-hint" class="pm-off-hint" style="display:none;">已停用 — 公開頁顯示連結失效</div>
      <div id="pm-share-blk">
        <div class="pm-row">
          <input id="pm-share" type="text" class="crm-input" style="flex:1;" readonly value="${_esc(shareUrl)}">
          <button id="pm-copy" class="crm-btn crm-btn-primary crm-btn-sm">複製連結</button>
          <button id="pm-reset" class="crm-btn crm-btn-danger crm-btn-sm">重置連結</button>
        </div>
        ${d.token ? `<div class="pm-qr-row">
          <img id="pm-qr" class="pm-qr" src="${_esc(_qrUrl())}" alt="公開上傳連結 QR code" width="160" height="160">
          <span class="pm-hint" style="margin:0;">現場立牌或投影掃碼即傳</span>
        </div>` : ''}
      </div>
      <div class="pm-hint">丟到劇組群組即可收集現場照片與影片，開頁就能上傳、無需登入</div>
    </div>

    <div class="pm-card">
      <div class="pm-card-title">上傳</div>
      <div class="pm-row" style="margin-bottom:8px;">
        <label class="pm-hint" style="margin:0;">分類</label>
        <select id="pm-up-cat" class="crm-input" style="width:auto;min-width:120px;" data-no-search></select>
        <button id="pm-pick" class="crm-btn crm-btn-secondary crm-btn-sm">選擇檔案</button>
        <input id="pm-file" type="file" multiple style="display:none;">
      </div>
      <div id="pm-drop" class="pm-drop">拖放檔案到這裡上傳</div>
      <div id="pm-prog"></div>
    </div>

    <div class="pm-card">
      <div id="pm-pills" class="pm-pills"></div>
      <div id="pm-grid" class="pm-grid"></div>
    </div>`;

    // ── 設定卡 ──
    document.getElementById('pm-root-save').addEventListener('click', _saveRoot);
    document.getElementById('pm-open-folder').addEventListener('click', _openFolder);
    document.getElementById('pm-cat-add').addEventListener('click', _addCat);
    document.getElementById('pm-cat-new').addEventListener('keydown', e => { if (e.key === 'Enter') _addCat(); });
    document.getElementById('pm-cat-save').addEventListener('click', _saveCats);
    document.getElementById('pm-chips').addEventListener('click', e => {
        const btn = e.target.closest('button[data-ci]');
        if (!btn) return;
        _cats.splice(Number(btn.dataset.ci), 1);
        _renderChips();
    });

    // ── 公開連結卡 ──
    document.getElementById('pm-copy').addEventListener('click', _copyLink);
    document.getElementById('pm-reset').addEventListener('click', _resetToken);
    document.getElementById('pm-enabled').addEventListener('change', _toggleEnabled);
    _applyEnabledUI();

    // ── 上傳卡 ──
    document.getElementById('pm-pick').addEventListener('click', () => document.getElementById('pm-file').click());
    document.getElementById('pm-file').addEventListener('change', e => {
        _handleFiles(e.target.files);
        e.target.value = '';
    });
    const drop = document.getElementById('pm-drop');
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('drag-over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
    drop.addEventListener('drop', e => {
        e.preventDefault();
        drop.classList.remove('drag-over');
        _handleFiles(e.dataTransfer.files);
    });

    // ── 縮圖牆（事件委派，_renderGrid 只換 innerHTML） ──
    document.getElementById('pm-pills').addEventListener('click', e => {
        const pill = e.target.closest('button[data-cat]');
        if (!pill) return;
        _filter = pill.dataset.cat;
        _renderPills();
        _renderGrid();
    });
    document.getElementById('pm-grid').addEventListener('click', e => {
        const actBtn = e.target.closest('button[data-act]');
        if (actBtn) {
            const item = actBtn.closest('.pm-item');
            const f = (_data.files || []).find(x => String(x.id) === item.dataset.id);
            if (!f) return;
            if (actBtn.dataset.act === 'dl') _download(f);
            else if (actBtn.dataset.act === 'del') _deleteFile(f);
            return;
        }
        const item = e.target.closest('.pm-item');
        if (item) _openLightbox(Number(item.dataset.idx));
    });

    _renderChips();
    _renderUploadCatOptions();
    _renderPills();
    _renderGrid();
    _renderProgress();
}

// ── 設定：原檔資料夾 + 分類 ──────────────────────────────────

async function _saveRoot() {
    const root = document.getElementById('pm-root').value.trim();
    try {
        await _fetch(`/projects/${_projectId}/media-log/settings`, {
            method: 'POST', body: JSON.stringify({ root }),
        });
        _toast('已儲存資料夾設定');
        // root_set / project_folder 會跟著變 → 整個 tab 重載最省事
        await loadMediaTab(_projectId, _host);
    } catch (e) {
        alert('儲存失敗：' + (e.message || e));
    }
}

function _openFolder() {
    const path = _data.project_folder || _data.root || '';
    if (!path) { alert('尚未設定原檔資料夾'); return; }
    // payload 形狀照抄既有呼叫（app.js / crm-projects-finance.js）：{path}
    fetch('/api/v1/utils/open_folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
    }).catch(() => {});
}

function _addCat() {
    const inp = document.getElementById('pm-cat-new');
    const name = inp.value.trim();
    if (!name) return;
    if (_cats.includes(name)) { _toast('分類已存在'); return; }
    _cats.push(name);
    inp.value = '';
    _renderChips();
}

async function _saveCats() {
    try {
        await _fetch(`/projects/${_projectId}/media-log/settings`, {
            method: 'POST', body: JSON.stringify({ categories: _cats }),
        });
        _data.categories = [..._cats];
        _toast('已儲存分類');
        _renderUploadCatOptions();
        _renderPills();
        _renderGrid();
    } catch (e) {
        alert('儲存失敗：' + (e.message || e));
    }
}

function _renderUploadCatOptions() {
    const sel = document.getElementById('pm-up-cat');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = _cats.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join('')
        + '<option value="">未分類</option>';
    if (prev && [..._cats, ''].includes(prev)) sel.value = prev;
}

function _renderChips() {
    const box = document.getElementById('pm-chips');
    if (!box) return;
    box.innerHTML = _cats.length
        ? _cats.map((c, i) =>
            `<span class="pm-chip">${_esc(c)}<button data-ci="${i}" title="移除">✕</button></span>`).join('')
        : '<span class="pm-hint">尚無分類，於下方輸入後按「新增」，記得按「儲存分類」</span>';
}

// ── 公開連結 ─────────────────────────────────────────────────

/** QR 端點：免認證、token 即授權，直接當 <img src>；base 帶前端 origin 讓 QR 內容是完整網址 */
function _qrUrl() {
    return `${PUBLIC_API}/${_data.token}/qr?base=${encodeURIComponent(location.origin)}`;
}

async function _toggleEnabled(e) {
    const want = e.target.checked;
    try {
        const r = await _fetch(`/projects/${_projectId}/media-log/enabled`, {
            method: 'POST', body: JSON.stringify({ enabled: want }),
        });
        _data.enabled = !!r.enabled;
        _toast(_data.enabled ? '已啟用公開連結' : '已停用公開連結');
    } catch (err) {
        e.target.checked = !want;   // 失敗回滾 checkbox，不動 _data
        alert('切換失敗：' + (err.message || err));
        return;
    }
    _applyEnabledUI();
}

/** 依 _data.enabled 套用停用視覺（QR + 連結區塊淡化 + 琥珀提示） */
function _applyEnabledUI() {
    const on = _data.enabled !== false;
    const blk = document.getElementById('pm-share-blk');
    if (blk) blk.classList.toggle('pm-share-off', !on);
    const hint = document.getElementById('pm-off-hint');
    if (hint) hint.style.display = on ? 'none' : '';
    const cb = document.getElementById('pm-enabled');
    if (cb) cb.checked = on;
}

async function _copyLink() {
    const url = _absShareUrl(_data.share_url);
    try {
        await navigator.clipboard.writeText(url);
        _toast('已複製公開連結');
    } catch {
        window.prompt('手動複製此連結：', url);
    }
}

async function _resetToken() {
    if (!confirm('重置公開連結？\n\n舊連結將立即失效，已分享到群組的連結都要重新發送。')) return;
    try {
        const r = await _fetch(`/projects/${_projectId}/media-log/token`, { method: 'POST' });
        _data.token = r.token;
        _data.share_url = r.share_url;
        const inp = document.getElementById('pm-share');
        if (inp) inp.value = _absShareUrl(r.share_url);
        const qr = document.getElementById('pm-qr');
        if (qr) qr.src = _qrUrl();   // token 變了 → QR 同步刷新
        _toast('已重置連結');
    } catch (e) {
        alert('重置失敗：' + (e.message || e));
    }
}

// ── 上傳（後台走公開端點 + token；XHR 才有進度） ─────────────

function _handleFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (!_data.token) { alert('沒有公開連結 token，無法上傳'); return; }
    if (!_data.root_set) { alert('尚未設定原檔資料夾，請先在上方設定並儲存'); return; }
    const cat = (document.getElementById('pm-up-cat') || {}).value || '';
    files.forEach(f => _uploads.push({ file: f, name: f.name, cat, pct: 0, status: 'wait', err: '' }));
    _renderProgress();
    _pumpUploads();
}

async function _pumpUploads() {
    if (_uploading) return;
    _uploading = true;
    let okCount = 0;
    // 逐檔序列上傳（現場影片檔大，並行只會互搶頻寬）
    for (const u of _uploads) {
        if (u.status !== 'wait') continue;
        u.status = 'up';
        _renderProgress();
        try {
            await _xhrUpload(u);
            u.status = 'done';
            u.pct = 100;
            okCount++;
        } catch (e) {
            u.status = 'fail';
            u.err = e.message || '上傳失敗';
        }
        _renderProgress();
    }
    _uploading = false;
    if (okCount > 0) await _reloadFiles();
    // 佇列裡若還有等待（上傳途中又丟新檔進來）→ 再跑一輪
    if (_uploads.some(u => u.status === 'wait')) _pumpUploads();
}

function _xhrUpload(u) {
    return new Promise((resolve, reject) => {
        const fd = new FormData();
        fd.append('file', u.file);
        fd.append('category', u.cat || '');
        fd.append('uploader_name', '後台');
        const xhr = new XMLHttpRequest();
        xhr.open('POST', `${PUBLIC_API}/${_data.token}/upload`);
        xhr.upload.addEventListener('progress', e => {
            if (e.lengthComputable) {
                u.pct = Math.round(e.loaded / e.total * 100);
                _tickProgressRow(u);   // 進度 tick 只改該列 bar/百分比 — 不整列表重繪
            }
        });
        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
            let msg = `HTTP ${xhr.status}`;
            try {
                const j = JSON.parse(xhr.responseText);
                if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
            } catch { /* 保留 HTTP 狀態碼訊息 */ }
            reject(new Error(msg));
        });
        xhr.addEventListener('error', () => reject(new Error('網路錯誤')));
        xhr.send(fd);
    });
}

async function _reloadFiles() {
    try {
        const d = await _fetch(`/projects/${_projectId}/media-log`);
        _data = { ..._data, ...d };
        _renderPills();
        _renderGrid();
        _applyEnabledUI();   // GET 也回 enabled → 同步停用視覺
    } catch { /* 靜默：下次切 tab 會重抓 */ }
}

function _renderProgress() {
    const box = document.getElementById('pm-prog');
    if (!box) return;
    if (!_uploads.length) { box.innerHTML = ''; return; }
    const rows = _uploads.map((u, i) => {
        let st;
        if (u.status === 'wait') st = '<span class="pm-prog-st">等待中</span>';
        else if (u.status === 'up') st = `<span class="pm-prog-st">${u.pct}%</span>`;
        else if (u.status === 'done') st = '<span class="pm-prog-st pm-ok">完成</span>';
        else st = `<span class="pm-prog-st pm-err" title="${_esc(u.err)}">失敗</span>`;
        return `<div class="pm-prog-row" data-i="${i}">
            <span class="pm-prog-name" title="${_esc(u.name)}">${_esc(u.name)}</span>
            <span class="pm-prog-bar"><span style="width:${u.pct}%;"></span></span>
            ${st}
        </div>`;
    });
    const allDone = _uploads.every(u => u.status === 'done' || u.status === 'fail');
    box.innerHTML = rows.join('') + (allDone
        ? '<div style="text-align:right;margin-top:6px;"><button id="pm-prog-clear" class="crm-btn crm-btn-secondary crm-btn-sm">清除上傳紀錄</button></div>'
        : '');
    const clr = document.getElementById('pm-prog-clear');
    if (clr) clr.addEventListener('click', () => { _uploads = []; _renderProgress(); });
}

/** XHR progress tick 專用：只更新該列的 bar 寬與百分比字（公開頁同款模式） */
function _absShareUrl(rel) {
    return location.origin + (rel || '');
}

function _tickProgressRow(u) {
    const row = document.querySelector(`#pm-prog .pm-prog-row[data-i="${_uploads.indexOf(u)}"]`);
    if (!row) return;
    const bar = row.querySelector('.pm-prog-bar > span');
    if (bar) bar.style.width = u.pct + '%';
    const st = row.querySelector('.pm-prog-st');
    if (st && u.status === 'up') st.textContent = u.pct + '%';
}

// ── 縮圖牆 ───────────────────────────────────────────────────

function _filtered() {
    const files = _data.files || [];
    return _filter ? files.filter(f => f.category === _filter) : files;
}

function _renderPills() {
    const box = document.getElementById('pm-pills');
    if (!box) return;
    const files = _data.files || [];
    // 分類清單 ∪ 檔案上實際掛的分類（設定裡刪掉的分類，舊檔可能還掛著）
    const cats = [...new Set([...(_data.categories || []), ...files.map(f => f.category).filter(Boolean)])];
    if (_filter && !cats.includes(_filter)) _filter = '';
    const pill = (cat, label, count) =>
        `<button class="pm-pill${_filter === cat ? ' active' : ''}" data-cat="${_esc(cat)}">${_esc(label)} <span>${count}</span></button>`;
    box.innerHTML = [
        pill('', '全部', files.length),
        ...cats.map(c => pill(c, c, files.filter(f => f.category === c).length)),
    ].join('');
}

function _renderGrid() {
    const grid = document.getElementById('pm-grid');
    if (!grid) return;
    const all = _data.files || [];
    if (!all.length) {
        grid.innerHTML = '<div class="pm-empty">還沒有影像紀錄 — 複製上方公開連結丟到劇組群組即可開始收集</div>';
        return;
    }
    const files = _filtered();
    if (!files.length) {
        grid.innerHTML = '<div class="pm-empty">此分類目前沒有檔案</div>';
        return;
    }
    // 依拍攝日（created_at 本地日期）分組。header 只是視覺插入（.pm-group-hd，非 .pm-item），
    // data-idx 仍= 篩選後扁平清單索引 → lightbox 導覽邏輯（_filtered()）完全不受影響。
    const groups = [];
    const byKey = new Map();
    files.forEach((f, idx) => {
        const key = _dateKey(f.created_at) || '未知日期';
        let g = byKey.get(key);
        if (!g) { g = { key, items: [] }; byKey.set(key, g); groups.push(g); }
        g.items.push({ f, idx });
    });
    grid.innerHTML = groups.map(g => {
        const label = g.key === '未知日期' ? g.key : g.key.slice(5);   // YYYY/MM/DD → MM/DD
        return `<div class="pm-group-hd">${_esc(label)} · ${g.items.length} 個檔</div>`
            + g.items.map(it => _itemHtml(it.f, it.idx)).join('');
    }).join('');
    // 縮圖載入失敗（thumb_url 404 等）→ 換成灰卡顯示副檔名
    grid.querySelectorAll('img.pm-thumb').forEach(img => {
        img.addEventListener('error', () => {
            const div = document.createElement('div');
            div.className = 'pm-thumb pm-thumb-fallback';
            div.textContent = img.dataset.ext || 'FILE';
            img.replaceWith(div);
        }, { once: true });
    });
}

/** 單一縮圖卡 HTML；i = 篩選後扁平索引（data-idx，lightbox 用） */
function _itemHtml(f, i) {
    let ext = String(f.filename || '').split('.').pop().toUpperCase();
    if (!ext || ext.length > 5 || ext === String(f.filename || '').toUpperCase()) ext = 'FILE';
    const thumb = f.thumb_url
        ? `<img class="pm-thumb" src="${_esc(f.thumb_url)}" loading="lazy" alt="" data-ext="${_esc(ext)}">`
        : `<div class="pm-thumb pm-thumb-fallback">${_esc(ext)}</div>`;
    const dur = f.media_type === 'video'
        ? `<span class="pm-dur">${f.duration_sec != null ? _esc(_fmtDur(f.duration_sec)) : '影片'}</span>`
        : '';
    return `<div class="pm-item" data-id="${_esc(String(f.id))}" data-idx="${i}" title="${_esc(f.filename || '')}">
      <div class="pm-thumbwrap">
        ${thumb}${dur}
        <div class="pm-acts">
          <button data-act="dl" title="下載原檔">下載</button>
          <button data-act="del" class="pm-act-del" title="刪除">刪除</button>
        </div>
      </div>
      <div class="pm-meta">${_esc(f.uploader_name || '—')} · ${_fmtTime(f.created_at)}</div>
    </div>`;
}

/** created_at → 本地日期組鍵 YYYY/MM/DD（無效日期回 ''） */
function _dateKey(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())}`;
}

function _download(f) {
    const a = document.createElement('a');
    a.href = `${PUBLIC_API}/${_data.token}/file/${f.id}`;
    a.download = f.filename || '';
    document.body.appendChild(a);
    a.click();
    a.remove();
}

async function _deleteFile(f) {
    if (!confirm(`刪除「${f.filename || '此檔案'}」？此操作無法復原。`)) return;
    try {
        await _fetch(`/media-log/files/${f.id}`, { method: 'DELETE' });
    } catch (e) {
        alert('刪除失敗：' + (e.message || e));
        return;
    }
    _data.files = (_data.files || []).filter(x => x.id !== f.id);
    _renderPills();
    _renderGrid();
    _toast('已刪除');
}

// ── Lightbox ─────────────────────────────────────────────────

function _openLightbox(idx) {
    _closeLightbox();
    const files = _filtered();
    if (!files.length) return;
    _lbIdx = Math.max(0, Math.min(idx, files.length - 1));
    const ov = document.createElement('div');
    ov.id = 'pm-lightbox';
    ov.className = 'pm-lightbox';
    ov.addEventListener('click', e => { if (e.target === ov) _closeLightbox(); });
    document.body.appendChild(ov);
    _renderLightbox();
    _lbKeyHandler = e => {
        if (e.key === 'Escape') _closeLightbox();
        else if (e.key === 'ArrowLeft') _lbStep(-1);
        else if (e.key === 'ArrowRight') _lbStep(1);
    };
    document.addEventListener('keydown', _lbKeyHandler);
}

function _renderLightbox() {
    const ov = document.getElementById('pm-lightbox');
    if (!ov) return;
    const files = _filtered();
    const f = files[_lbIdx];
    if (!f) { _closeLightbox(); return; }
    const src = `${PUBLIC_API}/${_data.token}/file/${f.id}`;
    const media = f.media_type === 'video'
        ? `<video src="${_esc(src)}" controls class="pm-lb-media"></video>`
        : `<img src="${_esc(src)}" alt="" class="pm-lb-media">`;
    ov.innerHTML = `
      <button class="pm-lb-close" data-lb="close" title="關閉">✕</button>
      ${files.length > 1 ? '<button class="pm-lb-nav pm-lb-prev" data-lb="prev" title="上一個">‹</button>' : ''}
      <div class="pm-lb-body">${media}
        <div class="pm-lb-cap">${_esc(f.filename || '')} · ${_esc(f.uploader_name || '—')} · ${_fmtTime(f.created_at)} (${_lbIdx + 1}/${files.length})</div>
      </div>
      ${files.length > 1 ? '<button class="pm-lb-nav pm-lb-next" data-lb="next" title="下一個">›</button>' : ''}`;
    ov.querySelectorAll('[data-lb]').forEach(btn => btn.addEventListener('click', () => {
        const act = btn.dataset.lb;
        if (act === 'close') _closeLightbox();
        else if (act === 'prev') _lbStep(-1);
        else if (act === 'next') _lbStep(1);
    }));
}

function _lbStep(delta) {
    const files = _filtered();
    if (!files.length) return;
    _lbIdx = (_lbIdx + delta + files.length) % files.length;
    _renderLightbox();
}

function _closeLightbox() {
    const ov = document.getElementById('pm-lightbox');
    if (ov) ov.remove();
    if (_lbKeyHandler) {
        document.removeEventListener('keydown', _lbKeyHandler);
        _lbKeyHandler = null;
    }
    _lbIdx = -1;
}

// ── 小工具 ───────────────────────────────────────────────────

function _fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const p = n => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function _fmtDur(sec) {
    sec = Math.max(0, Math.round(Number(sec) || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return h
        ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
        : `${m}:${String(s).padStart(2, '0')}`;
}

// toast → crm-utils.crmToast（import 時 alias 為 _toast）

// ── 樣式（模組自帶，注入一次） ───────────────────────────────

function _injectStyle() {
    if (document.getElementById('pm-media-style')) return;
    const st = document.createElement('style');
    st.id = 'pm-media-style';
    st.textContent = `
.pm-card { background:#242424; border:1px solid #3a3a3a; border-radius:8px; padding:14px; margin-bottom:12px; }
.pm-card-title { font-size:13px; font-weight:600; color:#e0e0e0; margin-bottom:8px; }
.pm-row { display:flex; gap:8px; align-items:center; }
.pm-hint { font-size:12px; color:#8a8a8a; margin-top:6px; }
.pm-warn { background:#3a2c14; border:1px solid #b45309; color:#fbbf24; border-radius:6px; padding:8px 12px; font-size:12px; margin-bottom:10px; }
.pm-toggle { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:#c0c0c0; cursor:pointer; margin-bottom:8px; user-select:none; }
.pm-toggle input { accent-color:#3b82f6; cursor:pointer; margin:0; }
.pm-off-hint { background:#3a2c14; border:1px solid #b45309; color:#fbbf24; border-radius:6px; padding:6px 10px; font-size:12px; margin-bottom:8px; }
#pm-share-blk.pm-share-off { opacity:.4; }
.pm-qr-row { display:flex; align-items:center; gap:12px; margin-top:10px; }
.pm-qr { width:160px; height:160px; background:#fff; padding:8px; border-radius:8px; display:block; }
.pm-group-hd { grid-column:1 / -1; font-size:12px; color:#8a8a8a; margin-top:10px; letter-spacing:.05em; }
.pm-group-hd:first-child { margin-top:0; }
.pm-chips { display:flex; flex-wrap:wrap; gap:6px; min-height:26px; align-items:center; }
.pm-chip { display:inline-flex; align-items:center; gap:6px; background:#333; border:1px solid #4a4a4a; color:#d0d0d0; border-radius:999px; padding:3px 6px 3px 10px; font-size:12px; }
.pm-chip button { background:none; border:0; color:#9ca3af; cursor:pointer; font-size:11px; line-height:1; padding:2px 4px; border-radius:50%; }
.pm-chip button:hover { color:#fca5a5; background:#4a2525; }
.pm-drop { border:2px dashed #4a4a4a; border-radius:8px; padding:18px; text-align:center; color:#8a8a8a; font-size:12px; transition:border-color .15s, background .15s; }
.pm-drop.drag-over { border-color:#3b82f6; background:#1e2a3f; color:#93c5fd; }
.pm-prog-row { display:flex; align-items:center; gap:8px; margin-top:8px; font-size:12px; }
.pm-prog-name { flex:0 1 220px; color:#c0c0c0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pm-prog-bar { flex:1; height:6px; background:#333; border-radius:3px; overflow:hidden; }
.pm-prog-bar span { display:block; height:100%; background:#3b82f6; border-radius:3px; transition:width .15s; }
.pm-prog-st { flex:0 0 44px; text-align:right; color:#9ca3af; }
.pm-prog-st.pm-ok { color:#86efac; }
.pm-prog-st.pm-err { color:#fca5a5; cursor:help; }
.pm-pills { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; }
.pm-pill { background:#2e2e2e; border:1px solid #4a4a4a; color:#c0c0c0; border-radius:999px; padding:4px 12px; font-size:12px; cursor:pointer; }
.pm-pill span { color:#8a8a8a; font-size:11px; }
.pm-pill:hover { border-color:#5a5a5a; color:#e0e0e0; }
.pm-pill.active { background:#1e3a5f; border-color:#3b82f6; color:#93c5fd; }
.pm-pill.active span { color:#93c5fd; }
.pm-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; }
.pm-empty { grid-column:1 / -1; text-align:center; color:#777; font-size:13px; padding:48px 12px; }
.pm-item { border-radius:6px; overflow:hidden; background:#1c1c1c; border:1px solid #333; cursor:pointer; }
.pm-item:hover { border-color:#4a4a4a; }
.pm-thumbwrap { position:relative; }
.pm-thumb { width:100%; aspect-ratio:16/10; object-fit:cover; display:block; background:#2a2a2a; }
.pm-thumb-fallback { display:flex; align-items:center; justify-content:center; color:#6b7280; font-size:14px; font-weight:600; letter-spacing:1px; }
.pm-dur { position:absolute; right:4px; bottom:4px; background:rgba(0,0,0,.75); color:#fff; font-size:10px; padding:1px 5px; border-radius:3px; line-height:1.5; }
.pm-acts { position:absolute; top:0; left:0; right:0; display:flex; justify-content:flex-end; gap:4px; padding:4px; opacity:0; transition:opacity .12s; background:linear-gradient(rgba(0,0,0,.55), transparent); }
.pm-item:hover .pm-acts { opacity:1; }
.pm-acts button { background:rgba(0,0,0,.6); border:1px solid #555; color:#e0e0e0; font-size:11px; padding:2px 8px; border-radius:4px; cursor:pointer; }
.pm-acts button:hover { background:rgba(59,130,246,.8); border-color:#3b82f6; }
.pm-acts button.pm-act-del:hover { background:rgba(153,27,27,.9); border-color:#991b1b; }
.pm-meta { font-size:11px; color:#8a8a8a; padding:4px 6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pm-lightbox { position:fixed; inset:0; background:rgba(0,0,0,.88); z-index:9999; display:flex; align-items:center; justify-content:center; }
.pm-lb-body { display:flex; flex-direction:column; align-items:center; gap:8px; max-width:92vw; }
.pm-lb-media { max-width:90vw; max-height:82vh; border-radius:6px; background:#000; }
.pm-lb-cap { color:#c0c0c0; font-size:12px; text-align:center; }
.pm-lb-close { position:absolute; top:14px; right:18px; background:none; border:0; color:#c0c0c0; font-size:20px; cursor:pointer; }
.pm-lb-close:hover { color:#fff; }
.pm-lb-nav { position:absolute; top:50%; transform:translateY(-50%); background:rgba(0,0,0,.5); border:1px solid #4a4a4a; color:#e0e0e0; font-size:26px; line-height:1; padding:6px 12px 10px; border-radius:8px; cursor:pointer; }
.pm-lb-nav:hover { background:rgba(59,130,246,.6); }
.pm-lb-prev { left:18px; }
.pm-lb-next { right:18px; }
`;
    document.head.appendChild(st);
}
