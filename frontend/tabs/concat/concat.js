import { appendLog, getComputeBaseUrl, addStandaloneSource, setupDragAndDrop, setupInputDrop, validateRemotePaths, toUncPath, ensureDriveMap, pickFiles } from '../../js/shared/utils.js';
import { refreshConcatEditorStatus } from './concat_editor_modal.js';
import { createClipCard } from '../../js/shared/clip_card.js';

// Scanned files shown as the thumbnail grid. When populated it takes
// precedence over the text source_list for the submitted payload.
let _ccFiles = [];

export function getCcFiles() { return _ccFiles; }
window.getCcFiles = getCcFiles;

export function collectConcatPayload() {
    const rows = document.getElementById('cc_source_list').children;
    let sources = Array.from(rows).map(row => row.querySelector('input').value.trim()).filter(v => v);

    // If scan grid is populated, prefer it (user has seen thumbnails +
    // may have unchecked unwanted clips or reordered them).
    if (_ccFiles.length) {
        sources = _ccFiles.filter(f => f.selected).map(f => f.path);
        if (!sources.length) {
            alert('⚠️ 掃描的檔案全部未勾選，請至少勾選一個');
            return { valid: false };
        }
    }

    // If advanced clips are set, derive sources from them (for compatibility with backend path validation)
    const advancedClips = window._concatAdvancedClips || null;
    const advancedSelected = advancedClips ? advancedClips.filter(c => c.selected) : null;

    if (advancedSelected && advancedSelected.length) {
        // Use clip paths as sources (ordered)
        sources = advancedSelected.map(c => c.path);
    } else if (!sources.length) {
        alert('需要提供來源！');
        return { valid: false };
    }

    const destDir = document.getElementById('cc_dest').value.trim();
    if (!destDir) {
        alert('請設定目標輸出資料夾！');
        return { valid: false };
    }
    const payload = {
        sources,
        dest_dir: destDir,
        resolution: document.getElementById('cc_res').value,
        custom_name: document.getElementById('cc_name').value.trim(),
        codec: document.getElementById('cc_codec')?.value || 'ProRes',
        burn_timecode: document.getElementById('cc_burn_timecode')?.checked === true,
        burn_filename: document.getElementById('cc_burn_filename')?.checked === true,
    };
    if (advancedSelected && advancedSelected.length) {
        payload.advanced_clips = advancedSelected.map(c => ({
            path: c.path,
            trim_in: c.trim_in || 0,
            trim_out: (c.trim_out ?? -1),
            brightness: c.brightness || 0,
            contrast: (c.contrast ?? 1),
            saturation: (c.saturation ?? 1),
            gamma: (c.gamma ?? 1),
            color_temp: c.color_temp || 0,
            tint: c.tint || 0,
            shadows: c.shadows || 0,
            midtones: c.midtones || 0,
            highlights: c.highlights || 0,
            curve_points: c.curve_points || null,
        }));
        payload.xfade_enabled = !!window._concatXfadeEnabled;
        payload.xfade_type = window._concatXfadeType || 'fade';
        payload.xfade_duration = parseFloat(window._concatXfadeDuration) || 1.0;
    }
    return { valid: true, payload, name: '串帶' };
}
window.collectConcatPayload = collectConcatPayload;

let _ccSubmitting = false;

export async function submitConcat() {
    if (_ccSubmitting) return;
    _ccSubmitting = true;
    window._activeJobTab = 'concat';

    const submitBtn = document.querySelector('#tab_concat button[onclick="submitConcat()"]');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add('opacity-70', 'cursor-not-allowed');
        submitBtn._origText = submitBtn.textContent;
        submitBtn.textContent = '提交中...';
    }

    try {

    const collected = collectConcatPayload();
    if (!collected.valid) return;
    const payload = collected.payload;

    const ccHostObj = window.collectSelectedHost ? window.collectSelectedHost('cc_host_checkboxes') : { name: '本機', ip: 'local' };
    const isLocal = ccHostObj.ip === 'local';
    const ccHostUrl = isLocal ? getComputeBaseUrl() : 'http://' + ccHostObj.ip;
    const ccHostName = ccHostObj.name || ccHostObj.ip;
    const ccIp = isLocal ? 'local' : ccHostObj.ip;

    if (!isLocal) {
        // 載入 UNC 映射並轉換路徑
        await ensureDriveMap();
        payload.sources = payload.sources.map(toUncPath);
        payload.dest_dir = toUncPath(payload.dest_dir);

        // 驗證遠端主機路徑（用轉換後的路徑）
        const pathsToCheck = [payload.dest_dir, ...payload.sources];
        try {
            const result = await validateRemotePaths(ccHostObj.ip, pathsToCheck);
            if (!result.ok) {
                alert(`⚠️ 遠端主機 [${ccHostObj.name}] 路徑檢查失敗：\n\n${result.errors.join('\n')}\n\n請確認該主機是否已映射對應磁碟機。`);
                return;
            }
        } catch (e) {
            alert(`⚠️ 無法連線至遠端主機 [${ccHostObj.name}] 進行路徑驗證：${e.message}`);
            return;
        }

        window._remoteJobType = 'concat';
        window._activeRemoteHosts = {};
        if (window.showRemoteMainProgress) window.showRemoteMainProgress('遠端串帶中...');
        if (window.initRemoteHostProgress) window.initRemoteHostProgress([ccHostObj]);
    }
    try {
        window._lastJob = { url: ccHostUrl + '/api/v1/jobs/concat', payload };
        const res = await fetch(window._lastJob.url, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        const retryBtn = document.getElementById('btn_retry');
        if(retryBtn) retryBtn.style.display = 'none';

        appendLog(`串帶請求已送出至 [${ccHostName}]，任務 ID: ${result.job_id || '?'}`, 'system');
        if (result.warning) appendLog(`⚠️ ${result.warning}`, 'system');

        if (!isLocal) {
            if(window.updateHostProgress) window.updateHostProgress(ccIp, 20, '已排程，串帶中...', '#228b22');
            window._activeRemoteHosts[ccIp] = {
                host: ccHostObj, files: payload.sources,
                lastSeen: Date.now(), startTime: Date.now(), logOffset: 0
            };
            if(window.startHeartbeatMonitor) window.startHeartbeatMonitor();
        }
    } catch (e) {
        appendLog('發送失敗: ' + e.message, 'error');
    }

    } finally {
        _ccSubmitting = false;
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('opacity-70', 'cursor-not-allowed');
            submitBtn.textContent = submitBtn._origText || '開始串帶';
        }
    }
}

export function initConcatTab() {
    addStandaloneSource('cc_source_list', '');
    setupDragAndDrop('cc_source_list', () => addStandaloneSource('cc_source_list', ''));
    setupInputDrop('cc_dest');
    refreshConcatEditorStatus();
}

// ── Scan sources → thumbnail grid (shared with drone_meta via createClipCard) ──

async function ccScanSources() {
    const rows = document.getElementById('cc_source_list').children;
    const paths = Array.from(rows).map(r => r.querySelector('input').value.trim()).filter(Boolean);
    if (!paths.length) { alert('請先新增來源路徑'); return; }

    const grid = document.getElementById('cc_file_grid');
    const toolbar = document.getElementById('cc_grid_toolbar');
    const scanProg = document.getElementById('cc-scan-progress');
    const scanBar = document.getElementById('cc-scan-bar');
    const scanLabel = document.getElementById('cc-scan-label');
    const scanCount = document.getElementById('cc-scan-count');

    _ccFiles = [];
    grid.innerHTML = '';
    grid.classList.remove('hidden');
    toolbar.classList.add('hidden');
    window._concatAdvancedClips = null;
    scanProg.classList.remove('hidden');
    scanBar.style.width = '0%';
    scanBar.style.backgroundColor = '#3b82f6';
    scanLabel.textContent = '掃描中...';
    scanCount.textContent = '';

    let streamed = false;
    let total = 0;
    let thumbCount = 0;
    try {
        const res = await fetch('/api/v1/drone_meta/scan_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths }),
        });
        if (res.ok && res.body) {
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split('\n\n');
                buffer = parts.pop();
                for (const part of parts) {
                    const line = part.trim();
                    if (!line.startsWith('data: ')) continue;
                    let evt;
                    try { evt = JSON.parse(line.slice(6)); } catch (_) { continue; }

                    if (evt.event === 'start') {
                        total = evt.total;
                        scanCount.textContent = `0 / ${total}`;
                        if (total === 0) { scanLabel.textContent = '找不到影片檔案'; scanProg.classList.add('hidden'); }
                    } else if (evt.event === 'file') {
                        streamed = true;
                        evt.data.selected = true;
                        _ccFiles.push(evt.data);
                        _ccAppendCard(evt.data, _ccFiles.length - 1);
                        const pct = total > 0 ? (_ccFiles.length / total * 100) : 0;
                        scanBar.style.width = pct + '%';
                        scanCount.textContent = `${_ccFiles.length} / ${total}`;
                        scanLabel.textContent = `列出檔案... ${evt.data.filename}`;
                    } else if (evt.event === 'files_done') {
                        scanBar.style.width = '0%';
                        scanBar.style.backgroundColor = '#228b22';
                        scanLabel.textContent = '載入縮圖...';
                        toolbar.classList.remove('hidden');
                        _ccUpdateSelectCount();
                    } else if (evt.event === 'thumb') {
                        thumbCount++;
                        const idx = evt.index;
                        if (_ccFiles[idx]) _ccFiles[idx].thumbnail = evt.thumbnail;
                        const cards = grid.querySelectorAll('.cc-file-card');
                        if (cards[idx]) {
                            const wrap = cards[idx].querySelector('.clip-thumb-wrap');
                            if (wrap) wrap.innerHTML = `<img src="${evt.thumbnail}" alt="" class="clip-thumb w-full h-full object-cover">`;
                        }
                        const pct = total > 0 ? (thumbCount / total * 100) : 0;
                        scanBar.style.width = pct + '%';
                        scanCount.textContent = `${thumbCount} / ${total}`;
                    } else if (evt.event === 'done') {
                        streamed = true;
                    }
                }
            }
        }
    } catch (e) {
        console.warn('[CC] SSE failed, falling back:', e);
    }

    if (!streamed) {
        try {
            const res = await fetch('/api/v1/drone_meta/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths }),
            });
            const data = await res.json();
            _ccFiles = (data.files || []).map(f => ({ ...f, selected: true }));
            _ccFiles.forEach((f, i) => _ccAppendCard(f, i));
            if (_ccFiles.length) toolbar.classList.remove('hidden');
            _ccUpdateSelectCount();
        } catch (e) {
            scanLabel.textContent = '掃描失敗';
            return;
        }
    }

    scanProg.classList.add('hidden');
    if (_ccFiles.length) refreshConcatEditorStatus();
}

function _ccAppendCard(f, idx) {
    const grid = document.getElementById('cc_file_grid');
    const card = createClipCard(f, idx, {
        cardClass: 'cc-file-card bg-[#252525] rounded-lg border border-[#3a3a3a] overflow-hidden cursor-grab',
        filterPrefix: 'cc',
        // Concat doesn't write metadata, so drone-specific badges and the
        // date/time override are irrelevant here. Badges for trim/colour are
        // still useful so users can see at a glance what the modal changed.
        showDji: false,
        showDateTime: false,
        showAction: false,
        showBadges: true,
        onToggle: (i, checked) => { if (_ccFiles[i]) _ccFiles[i].selected = checked; _ccUpdateSelectCount(); },
        onReorder: _ccReorder,
    });
    grid.appendChild(card);
}

function _ccReorder(from, to) {
    // DOM was already reordered by bindClipDrag; sync _ccFiles to match, then
    // re-number dataset.idx so the next drag reads fresh positions.
    const [f] = _ccFiles.splice(from, 1);
    _ccFiles.splice(to, 0, f);
    const grid = document.getElementById('cc_file_grid');
    if (grid) for (let i = 0; i < grid.children.length; i++) grid.children[i].dataset.idx = i;
}

function _ccUpdateSelectCount() {
    const el = document.getElementById('cc_select_count');
    const allCb = document.getElementById('cc_select_all');
    const count = _ccFiles.filter(f => f.selected).length;
    if (el) el.textContent = `已勾選 ${count} / ${_ccFiles.length} 個`;
    if (allCb) allCb.checked = count === _ccFiles.length && count > 0;
}

function ccToggleSelectAll(checked) {
    _ccFiles.forEach(f => f.selected = !!checked);
    document.querySelectorAll('#cc_file_grid .cc-file-card input[type="checkbox"]').forEach(cb => {
        cb.checked = !!checked;
    });
    _ccUpdateSelectCount();
}

function ccClearScan() {
    _ccFiles = [];
    window._concatAdvancedClips = null;
    document.getElementById('cc_file_grid').innerHTML = '';
    document.getElementById('cc_file_grid').classList.add('hidden');
    document.getElementById('cc_grid_toolbar').classList.add('hidden');
    refreshConcatEditorStatus();
}

async function ccAddFiles() {
    const paths = await pickFiles('選擇影片（可多選）');
    paths.forEach(p => addStandaloneSource('cc_source_list', p));
}

async function ccAddFolder() {
    try {
        const res = await fetch('/api/v1/utils/pick_folder?title=' + encodeURIComponent('選擇資料夾'));
        const data = await res.json();
        if (data.path) addStandaloneSource('cc_source_list', data.path);
    } catch (_) { /* silent */ }
}

window.submitConcat = submitConcat;
window.ccScanSources = ccScanSources;
window.ccToggleSelectAll = ccToggleSelectAll;
window.ccClearScan = ccClearScan;
window.ccAddFiles = ccAddFiles;
window.ccAddFolder = ccAddFolder;
