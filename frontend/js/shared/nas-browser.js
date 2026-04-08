// ─── NAS Browser Modal (for external access) ─── //
// Replaces Windows folder picker when accessing from outside LAN.

let _browserModal = null;
let _browserResolve = null;

function _fmt(bytes) {
    if (!bytes) return '';
    if (bytes > 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
    if (bytes > 1e6) return (bytes / 1e6).toFixed(1) + ' MB';
    return (bytes / 1e3).toFixed(0) + ' KB';
}

function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Open NAS browser modal and return selected path.
 * @param {object} opts
 * @param {string} opts.title - Modal title (default: '選擇目錄')
 * @param {string} opts.initialPath - Starting path
 * @param {boolean} opts.showFiles - Show video files (for file picker mode)
 * @param {string} opts.mode - 'folder' or 'file'
 * @returns {Promise<string>} Selected path or '' if cancelled
 */
export function openNasBrowser(opts = {}) {
    return new Promise((resolve) => {
        _browserResolve = resolve;
        const title = opts.title || '選擇目錄';
        const showFiles = opts.showFiles !== false;
        _showModal(title, opts.initialPath || '', showFiles, opts.mode || 'folder', opts.destPath || '');
    });
}

function _showModal(title, initialPath, showFiles, mode, destPath) {
    // Remove existing
    if (_browserModal) _browserModal.remove();

    const overlay = document.createElement('div');
    overlay.id = 'nas-browser-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px);';

    overlay.innerHTML = `
        <div style="background:#1e1e1e;border:1px solid #444;border-radius:8px;width:680px;max-width:95vw;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.6);">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid #333;">
                <span style="font-size:14px;font-weight:600;color:#e0e0e0;">${title}</span>
                <button id="nb-close" style="background:none;border:none;color:#888;font-size:18px;cursor:pointer;padding:2px 6px;">X</button>
            </div>
            <div id="nb-breadcrumb" style="padding:8px 16px;background:#161616;border-bottom:1px solid #333;display:flex;align-items:center;gap:4px;flex-wrap:wrap;min-height:36px;">
            </div>
            <div id="nb-list" style="flex:1;overflow-y:auto;padding:4px 0;min-height:300px;max-height:50vh;">
                <div style="text-align:center;color:#666;padding:40px;">載入中...</div>
            </div>
            <div style="padding:10px 16px;border-top:1px solid #333;display:flex;align-items:center;gap:8px;">
                <div id="nb-mode-toggle" style="display:flex;border:1px solid #555;border-radius:4px;overflow:hidden;flex-shrink:0;">
                    <button id="nb-mode-server" style="padding:4px 10px;font-size:11px;cursor:pointer;border:none;background:#1f538d;color:#fff;">伺服器</button>
                    <button id="nb-mode-local" style="padding:4px 10px;font-size:11px;cursor:pointer;border:none;background:#333;color:#ccc;">本機上傳</button>
                </div>
                <input id="nb-path-input" type="text" value="" readonly
                    style="flex:1;background:#111;border:1px solid #444;border-radius:4px;padding:6px 10px;color:#ccc;font-size:12px;font-family:monospace;">
                <button id="nb-cancel" style="background:#333;border:1px solid #555;color:#ccc;padding:6px 16px;border-radius:4px;cursor:pointer;font-size:12px;">取消</button>
                <button id="nb-confirm" style="background:#1f538d;border:none;color:#fff;padding:6px 16px;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;">確定</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    _browserModal = overlay;

    // Store state
    overlay._showFiles = showFiles;
    overlay._mode = mode;
    overlay._selectedPath = destPath || '';

    // Events
    overlay.querySelector('#nb-close').onclick = () => _close('');
    overlay.querySelector('#nb-cancel').onclick = () => _close('');
    overlay.querySelector('#nb-confirm').onclick = () => {
        const input = overlay.querySelector('#nb-path-input');
        _close(input.value || overlay._selectedPath || '');
    };
    // Only close when clicking the dark overlay backdrop, not the modal content
    overlay.addEventListener('mousedown', (e) => {
        if (e.target === overlay) _close('');
    });
    // Stop clicks inside the modal box from reaching the overlay
    overlay.firstElementChild.addEventListener('mousedown', (e) => e.stopPropagation());

    // Mode toggle
    const btnServer = overlay.querySelector('#nb-mode-server');
    const btnLocal = overlay.querySelector('#nb-mode-local');
    const modeToggle = overlay.querySelector('#nb-mode-toggle');
    // Hide toggle if showDirectoryPicker not supported
    if (!window.showDirectoryPicker) modeToggle.style.display = 'none';

    btnServer.onclick = () => {
        btnServer.style.background = '#1f538d'; btnServer.style.color = '#fff';
        btnLocal.style.background = '#333'; btnLocal.style.color = '#ccc';
        overlay.querySelector('#nb-confirm').textContent = '確定';
        _loadDir(overlay._selectedPath || initialPath || '', showFiles);
    };
    btnLocal.onclick = () => {
        btnLocal.style.background = '#1f538d'; btnLocal.style.color = '#fff';
        btnServer.style.background = '#333'; btnServer.style.color = '#ccc';
        _showLocalBrowser(overlay);
    };

    // Load initial — use initialPath if provided, otherwise root
    _loadDir(initialPath || '', showFiles);
}

function _close(result) {
    if (_browserModal) { _browserModal.remove(); _browserModal = null; }
    if (_browserResolve) { _browserResolve(result); _browserResolve = null; }
}

async function _loadDir(path, showFiles) {
    const listEl = document.getElementById('nb-list');
    const inputEl = document.getElementById('nb-path-input');
    const breadcrumbEl = document.getElementById('nb-breadcrumb');
    if (!listEl) return;

    listEl.innerHTML = '<div style="text-align:center;color:#666;padding:40px;"><div style="display:inline-block;width:20px;height:20px;border:2px solid #444;border-top-color:#3b82f6;border-radius:50%;animation:spin 1s linear infinite;"></div></div>';

    try {
        const url = '/api/v1/browse?path=' + encodeURIComponent(path || '') + (showFiles ? '&show_files=true' : '');
        const res = await fetch(url);
        const data = await res.json();

        if (data.status === 'error') {
            if (path) { _loadDir('', showFiles); return; }
            listEl.innerHTML = `<div style="text-align:center;color:#ef4444;padding:40px;">${_esc(data.message)}</div>`;
            return;
        }

        // Update breadcrumb
        _renderBreadcrumb(breadcrumbEl, data.path || '', showFiles);

        // Update path input
        if (inputEl && data.path) inputEl.value = data.path;
        if (_browserModal) _browserModal._selectedPath = data.path || '';

        // Render entries
        if (!data.entries || data.entries.length === 0) {
            listEl.innerHTML = '<div style="text-align:center;color:#666;padding:40px;">此目錄為空</div>';
            return;
        }

        let html = '';
        for (const entry of data.entries) {
            const isDir = entry.type === 'dir' || entry.type === 'root';
            const icon = isDir ? '<span style="color:#d48a04;">&#128193;</span>' : '<span style="color:#888;">&#127909;</span>';
            const sizeStr = entry.size ? `<span style="color:#666;font-size:11px;margin-left:auto;padding-left:12px;white-space:nowrap;">${_fmt(entry.size)}</span>` : '';
            const bgHover = 'onmouseover="this.style.background=\'#2a2a2a\'" onmouseout="this.style.background=\'\'"';

            if (isDir) {
                html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 16px;cursor:pointer;font-size:13px;color:#e0e0e0;" ${bgHover}
                    data-path="${entry.path}" data-type="dir">
                    ${icon} <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(entry.name)}</span>${sizeStr}
                </div>`;
            } else {
                html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 16px;cursor:pointer;font-size:13px;color:#aaa;" ${bgHover}
                    data-path="${entry.path}" data-type="file">
                    ${icon} <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(entry.name)}</span>${sizeStr}
                </div>`;
            }
        }
        listEl.innerHTML = html;

        // Click handlers
        listEl.querySelectorAll('[data-path]').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const p = el.getAttribute('data-path');
                const t = el.getAttribute('data-type');
                if (t === 'dir' || t === 'root') {
                    _loadDir(p, showFiles);
                } else if (t === 'file') {
                    // Select file
                    if (inputEl) inputEl.value = p;
                    if (_browserModal) _browserModal._selectedPath = p;
                    // Highlight selected
                    listEl.querySelectorAll('[data-path]').forEach(x => x.style.background = '');
                    el.style.background = '#1f538d44';
                }
            });

            // Double-click dir = select it
            if (el.getAttribute('data-type') === 'dir') {
                el.addEventListener('dblclick', () => {
                    _close(el.getAttribute('data-path'));
                });
            }
        });

    } catch (err) {
        listEl.innerHTML = `<div style="text-align:center;color:#ef4444;padding:40px;">載入失敗: ${err.message}</div>`;
    }
}

function _renderBreadcrumb(el, currentPath, showFiles) {
    if (!el) return;
    let html = '<span style="color:#888;font-size:11px;cursor:pointer;" data-bc-path="">伺服器</span>';

    if (currentPath) {
        const normalized = currentPath.replace(/\\/g, '/');
        const parts = normalized.split('/').filter(Boolean);
        let accumulated = '';
        for (let i = 0; i < parts.length; i++) {
            accumulated += (i === 0 && parts[0].includes(':')) ? parts[i] + '/' : parts[i] + '/';
            const display = parts[i];
            const isLast = i === parts.length - 1;
            html += `<span style="color:#555;font-size:11px;"> / </span>`;
            if (isLast) {
                html += `<span style="color:#e0e0e0;font-size:11px;font-weight:600;">${display}</span>`;
            } else {
                html += `<span style="color:#3b82f6;font-size:11px;cursor:pointer;text-decoration:underline;" data-bc-path="${accumulated}">${display}</span>`;
            }
        }
    }

    el.innerHTML = html;

    // Breadcrumb click handlers
    el.querySelectorAll('[data-bc-path]').forEach(span => {
        span.addEventListener('click', () => {
            _loadDir(span.getAttribute('data-bc-path'), showFiles);
        });
    });
}

// CSS animation for spinner
if (!document.getElementById('nb-spin-style')) {
    const style = document.createElement('style');
    style.id = 'nb-spin-style';
    style.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
    document.head.appendChild(style);
}

// ── Local Upload Mode ──────────────────────────────────────

async function _showLocalBrowser(overlay) {
    const listEl = document.getElementById('nb-list');
    const breadcrumbEl = document.getElementById('nb-breadcrumb');
    const inputEl = document.getElementById('nb-path-input');
    const confirmBtn = document.getElementById('nb-confirm');
    if (!listEl) return;

    if (!window.showDirectoryPicker) {
        listEl.innerHTML = '<div style="text-align:center;color:#ef4444;padding:40px;">此瀏覽器不支援本機資料夾存取</div>';
        return;
    }

    breadcrumbEl.innerHTML = '<span style="color:#888;font-size:11px;">本機模式 - 選擇要上傳的資料夾</span>';
    listEl.innerHTML = '<div style="text-align:center;color:#666;padding:40px;">正在開啟資料夾選擇器...</div>';

    let dirHandle;
    try {
        dirHandle = await window.showDirectoryPicker({ mode: 'read' });
    } catch (e) {
        listEl.innerHTML = '<div style="text-align:center;color:#666;padding:40px;">已取消選擇</div>';
        return;
    }

    // List files in selected directory
    const files = [];
    for await (const [name, handle] of dirHandle) {
        if (handle.kind === 'file') {
            files.push({ name, handle });
        }
    }

    if (files.length === 0) {
        listEl.innerHTML = '<div style="text-align:center;color:#666;padding:40px;">此資料夾沒有檔案</div>';
        return;
    }

    breadcrumbEl.innerHTML = '<span style="color:#e0e0e0;font-size:11px;">' + dirHandle.name + '</span>' +
        '<span style="color:#666;font-size:11px;margin-left:8px;">(' + files.length + ' 個檔案)</span>';
    if (inputEl) inputEl.value = dirHandle.name;

    // Render file list with checkboxes
    let html = '<div style="padding:6px 16px;display:flex;align-items:center;gap:6px;">' +
        '<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:11px;color:#9ca3af;">' +
        '<input type="checkbox" id="nb-local-all" checked> 全選</label></div>';
    files.sort((a, b) => a.name.localeCompare(b.name));
    for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const isImg = /\.(jpg|jpeg|png|gif|webp|heic)$/i.test(f.name);
        const isPdf = /\.pdf$/i.test(f.name);
        const icon = isImg ? '&#128247;' : isPdf ? '&#128196;' : '&#128462;';
        html += '<div style="display:flex;align-items:center;gap:8px;padding:4px 16px;font-size:13px;color:#d1d5db;">' +
            '<input type="checkbox" class="nb-local-check" data-idx="' + i + '" checked>' +
            '<span style="color:#888;">' + icon + '</span>' +
            '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(f.name) + '</span></div>';
    }
    listEl.innerHTML = html;

    // Select all toggle
    const allCheck = listEl.querySelector('#nb-local-all');
    if (allCheck) {
        allCheck.addEventListener('change', () => {
            listEl.querySelectorAll('.nb-local-check').forEach(c => { c.checked = allCheck.checked; });
        });
    }

    // Override confirm button for upload
    confirmBtn.textContent = '上傳選取檔案';
    confirmBtn.onclick = async () => {
        let targetPath = overlay._selectedPath || inputEl.value;
        if (!targetPath) {
            targetPath = prompt('請輸入伺服器端目的資料夾路徑：');
            if (!targetPath) return;
        }
        const checks = listEl.querySelectorAll('.nb-local-check:checked');
        if (checks.length === 0) { alert('請勾選至少一個檔案'); return; }

        confirmBtn.disabled = true;
        confirmBtn.textContent = '上傳中 0/' + checks.length;
        let done = 0;
        let failed = 0;

        for (const chk of checks) {
            const idx = parseInt(chk.dataset.idx);
            const fileEntry = files[idx];
            try {
                const file = await fileEntry.handle.getFile();
                const form = new FormData();
                form.append('file', file);
                form.append('dest_path', targetPath);
                const token = localStorage.getItem('auth_token');
                const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
                await fetch('/api/v1/utils/upload_file', { method: 'POST', headers, body: form });
                done++;
            } catch (_) {
                failed++;
            }
            confirmBtn.textContent = '上傳中 ' + (done + failed) + '/' + checks.length;
        }

        confirmBtn.disabled = false;
        confirmBtn.textContent = '上傳完成';
        const msg = '上傳完成：成功 ' + done + ' 個' + (failed ? '，失敗 ' + failed + ' 個' : '');
        breadcrumbEl.innerHTML = '<span style="color:#86efac;font-size:11px;">' + msg + '</span>';
        setTimeout(() => { confirmBtn.textContent = '確定'; confirmBtn.onclick = () => _close(targetPath); }, 2000);
    };
}

// Expose
window.openNasBrowser = openNasBrowser;
