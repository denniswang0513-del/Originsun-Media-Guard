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

    // 左欄「任務摘要」：只顯示關鍵訊息（system + error）
    if (terminal && (type === 'error' || type === 'system')) {
        const divLeft = document.createElement('div');
        divLeft.textContent = formattedText;
        if (type === 'error') divLeft.className = 'text-red-400 mt-1 mb-1';
        else divLeft.className = 'text-yellow-400 font-bold mt-1 mb-1';
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
    // ── Backup TAB (four-segment) ──
    // 根據勾選狀態決定哪些段要顯示
    const _chkTrans = document.getElementById('chk_transcode')?.checked ?? false;
    const _chkConcat = document.getElementById('chk_concat')?.checked ?? false;
    const _chkReport = (document.getElementById('chk_report')?.checked ?? false) || !!window._backupReportPending;

    ['bk-seg-backup', 'bk-seg-trans', 'bk-seg-concat', 'bk-seg-report'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.width = '0%';
        // 根據勾選狀態顯示/隱藏
        if (id === 'bk-seg-trans') el.classList.toggle('hidden', !_chkTrans);
        else if (id === 'bk-seg-concat') el.classList.toggle('hidden', !_chkConcat);
        else if (id === 'bk-seg-report') el.classList.toggle('hidden', !_chkReport);
    });
    ['bk-lbl-backup', 'bk-lbl-trans', 'bk-lbl-concat', 'bk-lbl-report'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0%';
    });
    // 圖例也根據勾選狀態
    const legendContainer = document.querySelector('#bk-progress .flex.gap-4');
    if (legendContainer) {
        const legends = legendContainer.children;
        if (legends[1]) legends[1].classList.toggle('hidden', !_chkTrans);
        if (legends[2]) legends[2].classList.toggle('hidden', !_chkConcat);
    }
    document.getElementById('bk-legend-report')?.classList.toggle('hidden', !_chkReport);
    const bkLabel = document.getElementById('bk-prog-label');
    if (bkLabel) bkLabel.textContent = '進度：尚未開始';
    const bkEta = document.getElementById('bk-prog-eta');
    if (bkEta) bkEta.textContent = '';
    document.getElementById('bk-progress')?.classList.add('hidden');

    // ── Standalone TABs (single bar) ──
    ['vf', 'tc', 'ct'].forEach(prefix => {
        const bar = document.getElementById(prefix + '-prog-bar');
        if (bar) bar.style.width = '0%';
        const lbl = document.getElementById(prefix + '-prog-label');
        if (lbl) lbl.textContent = '進度：尚未開始';
        const eta = document.getElementById(prefix + '-prog-eta');
        if (eta) eta.textContent = '';
        const detail = document.getElementById(prefix + '-prog-detail');
        if (detail) detail.textContent = '';
        document.getElementById(prefix + '-progress')?.classList.add('hidden');
    });

    // ── Report TAB (four-segment) ──
    ['rp-seg-scan', 'rp-seg-meta', 'rp-seg-strip', 'rp-seg-render'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.width = '0%';
    });
    ['rp-lbl-scan', 'rp-lbl-meta', 'rp-lbl-strip', 'rp-lbl-render'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0%';
    });
    const rpLabel = document.getElementById('rp-prog-label');
    if (rpLabel) rpLabel.textContent = '備用中...';
    document.getElementById('rp-progress')?.classList.add('hidden');

    // ── Shared controls ──
    const btnReport = document.getElementById('btn_open_report');
    if (btnReport) btnReport.style.display = 'none';

    // ── Remote host progress ──
    ['bk', 'tc', 'ct'].forEach(p => document.getElementById(p + '-remote-hosts-progress')?.classList.add('hidden'));
    if (window._heartbeatTimer) clearInterval(window._heartbeatTimer);
    window._remoteDispatching = false;

    // 不清除 _activeJobTab / _backupPipeline / _backupReportPending
    // — 備份流程中子任務（轉檔/串帶/報表）的 running 事件會觸發 resetProgress，
    //   但 pipeline 和 reportPending 在整個流程完成前都需要保留
    window._backupFinalShown = false;
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
// 格式化 IP：去掉 port（:8000）
function _displayIp(ip) {
    if (!ip || ip === 'local') return '';
    return ip.replace(/:\d+$/, '');
}

// 狀態燈 HTML
function _dotHtml(ip) {
    const dotId = 'host-dot-' + (ip || 'local').replace(/[.:]/g, '_');
    return `<span id="${dotId}" class="host-status-dot" style="width:8px;height:8px;border-radius:50%;display:inline-block;background:#555;flex-shrink:0;"></span>`;
}

// 偵測所有遠端主機連線狀態，更新狀態燈
let _hostHealthTimer = null;
async function _checkHostHealth() {
    const hosts = window._computeHosts || [];
    // 本機固定綠燈
    document.querySelectorAll('[id="host-dot-local"]').forEach(el => {
        el.style.background = '#22c55e';
        el.style.boxShadow = '0 0 4px #22c55e';
    });
    for (const h of hosts) {
        const dotId = 'host-dot-' + (h.ip || '').replace(/[.:]/g, '_');
        const dots = document.querySelectorAll(`[id="${dotId}"]`);
        try {
            const r = await fetch('http://' + h.ip + '/api/v1/health', { signal: AbortSignal.timeout(3000) });
            if (r.ok) {
                dots.forEach(el => { el.style.background = '#22c55e'; el.style.boxShadow = '0 0 4px #22c55e'; });
            } else {
                dots.forEach(el => { el.style.background = '#ef4444'; el.style.boxShadow = 'none'; });
            }
        } catch {
            dots.forEach(el => { el.style.background = '#ef4444'; el.style.boxShadow = 'none'; });
        }
    }
}

function _startHostHealthPolling() {
    if (_hostHealthTimer) return;
    _checkHostHealth(); // 立即偵測一次
    _hostHealthTimer = setInterval(_checkHostHealth, 30000); // 每 30 秒
}

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
        lbl.innerHTML = `<input type="checkbox" id="${prefix}_local" data-ip="local" data-name="本機" ${localChecked ? 'checked' : ''} class="form-checkbox rounded bg-[#1e1e1e] border-[#444]"> ${_dotHtml('local')} 本機`;
        container.appendChild(lbl);
    }
    hosts.forEach((h, i) => {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        lbl.innerHTML = `<input type="checkbox" id="${prefix}_${i}" data-ip="${h.ip}" data-name="${h.name}" class="form-checkbox rounded bg-[#1e1e1e] border-[#444]"> ${_dotHtml(h.ip)} ${h.name} <span class="text-gray-500">(${_displayIp(h.ip)})</span>`;
        container.appendChild(lbl);
    });
    container.dataset.built = '1';
    _startHostHealthPolling();
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

// 單選版本（radio）— 用於只在一台主機執行的 TAB
export function renderHostRadios(containerId, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const hosts = window._computeHosts || [];
    if (!hosts.length) {
        container.closest('.pj-sch-sub-panel, [id$="_host_panel"], [id$="host_selector_panel"]')
            ?.style.setProperty('display', 'none');
        return;
    }
    if (container.dataset.built) return;

    const prefix = opts.idPrefix || containerId;
    const groupName = prefix + '_radio';
    const includeLocal = opts.includeLocal !== false;
    const localChecked = opts.localChecked !== false;
    container.innerHTML = '';

    if (includeLocal) {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        lbl.innerHTML = `<input type="radio" name="${groupName}" id="${prefix}_local" data-ip="local" data-name="本機" ${localChecked ? 'checked' : ''} class="form-radio bg-[#1e1e1e] border-[#444]"> ${_dotHtml('local')} 本機`;
        container.appendChild(lbl);
    }
    hosts.forEach((h, i) => {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-1 text-xs text-gray-300 cursor-pointer';
        lbl.innerHTML = `<input type="radio" name="${groupName}" id="${prefix}_${i}" data-ip="${h.ip}" data-name="${h.name}" ${!localChecked && i === 0 ? 'checked' : ''} class="form-radio bg-[#1e1e1e] border-[#444]"> ${_dotHtml(h.ip)} ${h.name} <span class="text-gray-500">(${_displayIp(h.ip)})</span>`;
        container.appendChild(lbl);
    });
    container.dataset.built = '1';
    _startHostHealthPolling();
}

export function collectSelectedHost(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return { name: '本機', ip: 'local' };
    const checked = container.querySelector('input[type="radio"]:checked');
    if (checked) return { name: checked.dataset.name, ip: checked.dataset.ip };
    return { name: '本機', ip: 'local' };
}

window.renderHostCheckboxes = renderHostCheckboxes;
window.collectSelectedHosts = collectSelectedHosts;
window.renderHostRadios = renderHostRadios;
window.collectSelectedHost = collectSelectedHost;

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

