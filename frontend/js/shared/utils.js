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

// Make accessible to global scope if needed during transition
window.appendLog = appendLog;
window.pickPath = pickPath;
window.getComputeBaseUrl = getComputeBaseUrl;
window.resetProgress = resetProgress;

export function addStandaloneSource(listId, defaultPath = '') {
    const container = document.getElementById(listId);
    if (!container) return;
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    row.innerHTML = `
        <input type="text" class="flex-1 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath}" placeholder="檔案絕對路徑...">
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded">X</button>
    `;
    container.appendChild(row);

    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        row.remove();
    });
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

