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
        const showFiles = opts.showFiles || opts.mode === 'file';
        _showModal(title, opts.initialPath || '', showFiles, opts.mode || 'folder');
    });
}

function _showModal(title, initialPath, showFiles, mode) {
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
    overlay._selectedPath = '';

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

    // Load initial
    _loadDir(initialPath, showFiles);
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
            listEl.innerHTML = `<div style="text-align:center;color:#ef4444;padding:40px;">${data.message}</div>`;
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
                    ${icon} <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${entry.name}</span>${sizeStr}
                </div>`;
            } else {
                html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 16px;cursor:pointer;font-size:13px;color:#aaa;" ${bgHover}
                    data-path="${entry.path}" data-type="file">
                    ${icon} <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${entry.name}</span>${sizeStr}
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
    let html = '<span style="color:#888;font-size:11px;cursor:pointer;" data-bc-path="">Roots</span>';

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

// Expose
window.openNasBrowser = openNasBrowser;
