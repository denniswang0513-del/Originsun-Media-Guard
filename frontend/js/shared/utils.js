// utils.js
// Shared utilities across all tabs

export function getComputeBaseUrl() {
    const mode = document.getElementById('compute_mode')?.value;
    return (mode === 'local' && window.localAgentActive) ? 'http://localhost:8000' : '';
}

export function getAgentBaseUrl() {
    // Dynamically use the current origin so it works via IP or localhost
    return window.location.origin;
}

export async function resolveDropPath(e, file, index = 0) {
    // 方法1：text/uri-list（RFC 2483，CRLF 分隔）
    const uriList = e.dataTransfer.getData('text/uri-list');
    if (uriList) {
        const uris = uriList.split(/\r?\n/).map(u => u.trim()).filter(u => u && !u.startsWith('#'));
        const uri = uris[index] || uris[0];
        if (uri && uri.toLowerCase().startsWith('file:')) {
            return decodeURIComponent(uri)
                .replace(/^file:\/\/\/([A-Za-z]:)/i, '$1')
                .replace(/^file:\/\//i, '\\\\')
                .replace(/\//g, '\\');
        }
    }

    // 方法2：text/plain
    const textRaw = e.dataTransfer.getData('text');
    if (textRaw && (textRaw.match(/^[A-Za-z]:\\/) || textRaw.startsWith('\\\\'))) {
        const lines = textRaw.split(/\r?\n/).filter(l => l.trim());
        if (lines.length > index) return lines[index].trim();
        return textRaw.trim();
    }

    // 方法3：Electron file.path
    if (file && file.path) return file.path;

    // 方法4：後端智慧深度解析
    if (file) {
        try {
            const res = await fetch('/api/v1/utils/resolve_drop?name=' + encodeURIComponent(file.name));
            if (res.ok) { const d = await res.json(); return d.path || file.name; }
        } catch { }
        return file.name;
    }
    return '';
}

export function appendLog(msg, type = 'info') {
    const terminal = document.getElementById('terminal');
    const terminalVerbose = document.getElementById('terminal_verbose');
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    const formattedText = `[${time}] ${msg}`;

    if (terminalVerbose) {
        const divRight = document.createElement('div');
        divRight.textContent = formattedText;
        if (type === 'error') divRight.className = 'text-red-400 mt-1 mb-1';
        else if (type === 'system') divRight.className = 'text-yellow-400 font-bold mt-1 mb-1';
        else divRight.className = 'text-gray-300 mb-0.5';
        terminalVerbose.prepend(divRight);
        terminalVerbose.scrollTop = 0;
    }

    if (terminal && (type === 'error' || type === 'system' || type === 'info')) {
        const divLeft = document.createElement('div');
        divLeft.textContent = formattedText;
        if (type === 'error') divLeft.className = 'text-red-400 mt-1 mb-1';
        else if (type === 'system') divLeft.className = 'text-yellow-400 font-bold mt-1 mb-1';
        else divLeft.className = 'text-gray-300 mb-0.5';
        terminal.prepend(divLeft);
        terminal.scrollTop = 0;
    }
}

export async function pickPath(inputId, type = 'folder') {
    try {
        const endpoint = getAgentBaseUrl() + (type === 'folder' ? '/api/v1/utils/pick_folder' : '/api/v1/utils/pick_file');
        const el = document.getElementById(inputId);
        if (!el) return;
        
        el.classList.add('animate-pulse', 'bg-blue-900', 'text-white');
        const res = await fetch(endpoint);
        const data = await res.json();
        el.classList.remove('animate-pulse', 'bg-blue-900', 'text-white');

        if (data.path) {
            el.value = data.path;
            if (inputId.startsWith('src_path_')) {
                const row = el.closest('.flex');
                const nameInput = row.querySelectorAll('input')[0];
                if (!nameInput.value.trim() || nameInput.value.startsWith('Card_')) {
                    const parts = data.path.replace(/\\/g, '/').split('/');
                    nameInput.value = parts[parts.length - 1] || nameInput.value;
                }
            }
        }
    } catch (e) {
        console.error("Picker failed:", e);
        const el = document.getElementById(inputId);
        if(el) el.classList.remove('animate-pulse', 'bg-blue-900', 'text-white');
    }
}

export function resetProgress() {
    ['seg_backup', 'seg_trans', 'seg_concat', 'seg_report'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.width = '0%';
    });
    ['lbl_backup', 'lbl_trans', 'lbl_concat', 'lbl_report'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0%';
    });
    const progLabel = document.getElementById('prog_label');
    const progEta = document.getElementById('prog_eta');
    if (progLabel) progLabel.textContent = '等待開始...';
    if (progEta) progEta.textContent = '';
    
    const btnReport = document.getElementById('btn_open_report');
    if (btnReport) btnReport.style.display = 'none';
    
    document.getElementById('remote_hosts_progress')?.classList.add('hidden');
    if (window._heartbeatTimer) clearInterval(window._heartbeatTimer);
}

// ── 共用主機選擇模組 ──

/**
 * 將 compute_hosts 渲染為 checkbox 到指定容器。
 * @param {string} containerId - 容器 DOM id
 * @param {object} [opts] - 選項
 * @param {boolean} [opts.includeLocal=true] - 是否包含「本機」選項
 * @param {boolean} [opts.localChecked=true] - 「本機」預設勾選
 * @param {string}  [opts.idPrefix] - checkbox id 前綴（避免多個選擇器 id 衝突），預設為 containerId
 */
export function renderHostCheckboxes(containerId, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const hosts = window._computeHosts || [];
    if (!hosts.length) {
        container.closest('.pj-sch-sub-panel, [id$="_host_panel"], [id$="host_selector_panel"]')
            ?.style.setProperty('display', 'none');
        return;
    }
    if (container.dataset.built) return; // 避免重複渲染清除勾選

    const prefix = opts.idPrefix || containerId;
    const includeLocal = opts.includeLocal !== false;
    const localChecked = opts.localChecked !== false;
    container.innerHTML = '';

    if (includeLocal) {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        lbl.innerHTML = `<input type="checkbox" id="${prefix}_local" data-ip="local" data-name="本機" ${localChecked ? 'checked' : ''} class="form-checkbox rounded bg-[#1e1e1e] border-[#444]"> 本機`;
        container.appendChild(lbl);
    }
    hosts.forEach((h, i) => {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        lbl.innerHTML = `<input type="checkbox" id="${prefix}_${i}" data-ip="${h.ip}" data-name="${h.name}" class="form-checkbox rounded bg-[#1e1e1e] border-[#444]"> ${h.name} <span class="text-gray-500">(${h.ip})</span>`;
        container.appendChild(lbl);
    });
    container.dataset.built = '1';
}

/**
 * 收集容器內勾選的主機。
 * @param {string} containerId - 容器 DOM id
 * @returns {Array<{name: string, ip: string}>}
 */
export function collectSelectedHosts(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    const result = [];
    container.querySelectorAll('input[type="checkbox"]:checked').forEach(chk => {
        const ip = chk.dataset.ip;
        const name = chk.dataset.name;
        if (ip && name) result.push({ name, ip });
    });
    return result;
}

window.renderHostCheckboxes = renderHostCheckboxes;
window.collectSelectedHosts = collectSelectedHosts;

// Make accessible to global scope if needed during transition
window.resolveDropPath = resolveDropPath;
window.appendLog = appendLog;
window.pickPath = pickPath;
window.getComputeBaseUrl = getComputeBaseUrl;
window.resetProgress = resetProgress;

export function addStandaloneSource(listId, defaultPath = '') {
    const container = document.getElementById(listId);
    if (!container) return;
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    const inputId = 'standalone_src_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    row.innerHTML = `
        <input type="text" id="${inputId}" class="flex-1 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath}" placeholder="檔案絕對路徑...">
        <button type="button" class="btn-pick-file text-gray-400 hover:text-white bg-[#333] hover:bg-[#444] border border-[#555] px-2 py-1 rounded text-sm" title="選擇檔案">📁</button>
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded">X</button>
    `;
    container.appendChild(row);

    row.querySelector('.btn-pick-file').addEventListener('click', function() {
        pickPath(inputId, 'file');
    });
    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        row.remove();
    });
    return inputId;
}
window.addStandaloneSource = addStandaloneSource;

export function setupInputDrop(inputId) {
    const el = document.getElementById(inputId);
    if (!el) return;
    el.addEventListener('dragover', (e) => {
        e.preventDefault();
        el.classList.add('border-blue-400', 'bg-blue-900/20');
    });
    el.addEventListener('dragleave', () => {
        el.classList.remove('border-blue-400', 'bg-blue-900/20');
    });
    el.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.classList.remove('border-blue-400', 'bg-blue-900/20');

        const file = e.dataTransfer.files[0];
        let path = await resolveDropPath(e, file);

        if (!path || (file && path === file.name)) {
            const textData = e.dataTransfer.getData('text');
            if (textData) path = textData.trim();
        }

        if (path) el.value = path;
    });
}

export async function validateRemotePaths(hostIp, paths) {
    const url = 'http://' + hostIp + '/api/v1/validate_paths';
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths })
    });
    const data = await res.json();
    const errors = [];
    for (const [path, info] of Object.entries(data.results)) {
        if (!info.drive_exists) {
            errors.push(`磁碟機 ${info.drive} 不存在`);
        } else if (!info.path_exists) {
            errors.push(`路徑不存在: ${path}`);
        }
    }
    return errors.length ? { ok: false, errors } : { ok: true };
}
window.validateRemotePaths = validateRemotePaths;

export function setupDragAndDrop(containerId, addRowFunc) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.addEventListener('dragover', (e) => {
        e.preventDefault();
        container.classList.add('bg-[#2a2a2a]', 'border-blue-500');
    });

    container.addEventListener('dragleave', (e) => {
        e.preventDefault();
        container.classList.remove('bg-[#2a2a2a]', 'border-blue-500');
    });

    container.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        container.classList.remove('bg-[#2a2a2a]', 'border-blue-500');

        const files = e.dataTransfer.files;
        if (!files || files.length === 0) return;

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const absPath = await resolveDropPath(e, file, i);

            const newInputId = addRowFunc();
            if (newInputId) {
                const el = document.getElementById(newInputId);
                if (el) {
                    el.value = absPath;
                    if (containerId === 'source_list' || containerId === 'tc_source_list') {
                        const row = el.closest('.flex');
                        const nameEl = row && row.querySelectorAll('input')[0];
                        if (nameEl && (!nameEl.value || nameEl.value.startsWith('Card_'))) {
                            const parts = absPath.replace(/\\/g, '/').split('/');
                            nameEl.value = parts[parts.length - 1] || nameEl.value;
                        }
                    }
                }
            }
        }
    });
}

