import { appendLog, resetProgress, pickPath, getComputeBaseUrl, validateRemotePaths, toUncPath, ensureDriveMap } from '../../js/shared/utils.js';
import { fmtDuration as _fmtDuration, fmtSize as _fmtSize, parseCreationTimeLocal } from '../../js/shared/clip_utils.js';
import { createClipCard } from '../../js/shared/clip_card.js';
// Ensure openConcatEditor is available on window from drone_meta tab too
import '../concat/concat_editor_modal.js';

let _dmFiles = [];  // each file carries `selected: boolean`
// Exposed for concat_editor_modal.js to read current scan results.
export function getDmFiles() { return _dmFiles; }
window.getDmFiles = getDmFiles;

// Scan state — polled by concat modal to show "scan in progress" prompt.
let _dmScanState = { running: false, phase: '', done: 0, total: 0 };
export function getDmScanState() { return { ..._dmScanState }; }
window.getDmScanState = getDmScanState;

// Notify listeners (e.g. concat modal) that a single clip's data changed.
function _emitFileUpdated(idx) {
    const f = _dmFiles[idx];
    if (!f) return;
    window.dispatchEvent(new CustomEvent('dmfile:updated', { detail: { idx, path: f.path } }));
}

let _dmSubmitting = false;

const DRONE_MODELS = {
    autel_evo_lite_plus: { make: 'Autel Robotics', model: 'EVO Lite+', lensMake: 'Autel Robotics', lensModel: 'EVO Lite+ Camera' },
    dji_mavic3:          { make: 'DJI', model: 'Mavic 3', lensMake: 'DJI', lensModel: 'Mavic 3 Camera' },
    dji_mini4pro:        { make: 'DJI', model: 'Mini 4 Pro', lensMake: 'DJI', lensModel: 'Mini 4 Pro Camera' },
    dji_air3:            { make: 'DJI', model: 'Air 3', lensMake: 'DJI', lensModel: 'Air 3 Camera' },
};

function _updateSelectCount() {
    const count = _dmFiles.filter(f => f.selected).length;
    const el = document.getElementById('dm_select_count');
    if (el) el.textContent = `已勾選 ${count} / ${_dmFiles.length} 個`;
    const allCb = document.getElementById('dm_select_all');
    if (allCb) allCb.checked = count === _dmFiles.length && count > 0;
}

// ── File Picking ──

async function dmPickFolder() {
    try {
        await pickPath('dm_source_path', 'folder');
        const path = document.getElementById('dm_source_path').value;
        if (path) dmScanFiles();
    } catch (e) { console.warn('pick folder failed:', e); }
}
window.dmPickFolder = dmPickFolder;

async function dmPickFiles() {
    try {
        const res = await fetch('/api/v1/utils/pick_file?title=選擇影片檔');
        const data = await res.json();
        if (data.path) {
            const cur = document.getElementById('dm_source_path').value.trim();
            document.getElementById('dm_source_path').value = cur ? cur + ', ' + data.path : data.path;
            dmScanFiles();
        }
    } catch (e) { console.warn('pick file failed:', e); }
}
window.dmPickFiles = dmPickFiles;

// ── Streaming Scan ──

async function dmScanFiles() {
    const raw = document.getElementById('dm_source_path').value.trim();
    if (!raw) { alert('請先輸入或選擇來源路徑'); return; }

    const paths = raw.split(',').map(p => p.trim()).filter(Boolean);
    const status = document.getElementById('dm_scan_status');
    const scanProgress = document.getElementById('dm-scan-progress');
    const scanBar = document.getElementById('dm-scan-bar');
    const scanLabel = document.getElementById('dm-scan-label');
    const scanCount = document.getElementById('dm-scan-count');

    _dmFiles = [];
    // Clear persisted advanced-editor state so modal re-opens fresh against
    // the new scan. Xfade prefs (enabled/type/duration) are kept intentionally
    // as user preferences that should persist across batches.
    window._concatAdvancedClips = null;
    const grid = document.getElementById('dm_file_grid') || document.getElementById('dm_file_list');
    if (grid) grid.innerHTML = '';
    const gridToolbar = document.getElementById('dm_grid_toolbar');
    if (gridToolbar) gridToolbar.classList.add('hidden');

    status.textContent = '掃描中...';
    if (scanProgress) { scanProgress.classList.remove('hidden'); }
    if (scanBar) { scanBar.style.width = '0%'; }
    if (scanLabel) { scanLabel.textContent = '掃描中...'; }
    if (scanCount) { scanCount.textContent = ''; }

    _dmScanState = { running: true, phase: 'init', done: 0, total: 0 };
    let streamed = false;
    // SSE three-phase: 1) filenames+size  2) thumbnails  3) ffprobe+DJI detail
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
            let total = 0;
            let thumbCount = 0;
            let detailCount = 0;

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
                    try { evt = JSON.parse(line.slice(6)); } catch (_e) { continue; }

                    if (evt.event === 'start') {
                        total = evt.total;
                        _dmScanState = { running: true, phase: 'files', done: 0, total };
                        if (scanCount) scanCount.textContent = `0 / ${total}`;
                        if (total === 0) { status.textContent = '找不到影片檔案'; if (scanProgress) scanProgress.classList.add('hidden'); _dmScanState.running = false; }

                    } else if (evt.event === 'file') {
                        streamed = true;
                        evt.data.selected = true;
                        _dmFiles.push(evt.data);
                        _dmScanState.done = evt.index + 1;
                        const pct = total > 0 ? ((evt.index + 1) / total * 100) : 0;
                        if (scanBar) { scanBar.style.width = pct + '%'; scanBar.style.backgroundColor = '#3b82f6'; }
                        if (scanCount) scanCount.textContent = `${evt.index + 1} / ${total}`;
                        if (scanLabel) scanLabel.textContent = `列出檔案... ${evt.data.filename}`;
                        _appendFileCard(evt.data, evt.index);
                        _emitFileUpdated(evt.index);

                    } else if (evt.event === 'files_done') {
                        _dmScanState.phase = 'thumbs';
                        _dmScanState.done = 0;
                        status.textContent = `找到 ${total} 個影片，載入縮圖中...`;
                        if (scanBar) { scanBar.style.width = '0%'; scanBar.style.backgroundColor = '#228b22'; }
                        if (scanLabel) scanLabel.textContent = '載入縮圖...';
                        if (scanCount) scanCount.textContent = `0 / ${total}`;
                        const toolbar = document.getElementById('dm_grid_toolbar');
                        const clearBtn = document.getElementById('dm_btn_clear');
                        if (toolbar) toolbar.classList.remove('hidden');
                        if (clearBtn) clearBtn.classList.remove('hidden');
                        _updateSelectCount();

                    } else if (evt.event === 'thumb') {
                        thumbCount++;
                        _dmScanState.done = thumbCount;
                        const idx = evt.index;
                        if (_dmFiles[idx]) _dmFiles[idx].thumbnail = evt.thumbnail;
                        const cards = document.querySelectorAll('.dm-file-card');
                        if (cards[idx]) {
                            const wrap = cards[idx].querySelector('.clip-thumb-wrap');
                            if (wrap) wrap.innerHTML = `<img src="${evt.thumbnail}" alt="" class="clip-thumb w-full h-full object-cover">`;
                        }
                        const pct = total > 0 ? (thumbCount / total * 100) : 0;
                        if (scanBar) scanBar.style.width = pct + '%';
                        if (scanCount) scanCount.textContent = `${thumbCount} / ${total}`;
                        _emitFileUpdated(idx);

                    } else if (evt.event === 'thumbs_done') {
                        _dmScanState.phase = 'details';
                        _dmScanState.done = 0;
                        status.textContent = `讀取影片資訊中...`;
                        if (scanBar) { scanBar.style.width = '0%'; scanBar.style.backgroundColor = '#d48a04'; }
                        if (scanLabel) scanLabel.textContent = '讀取影片資訊...';
                        if (scanCount) scanCount.textContent = `0 / ${total}`;

                    } else if (evt.event === 'detail') {
                        detailCount++;
                        _dmScanState.done = detailCount;
                        const idx = evt.index;
                        const d = evt.data;
                        if (_dmFiles[idx]) {
                            for (const [k, v] of Object.entries(d)) {
                                if (v !== '' && v !== null && !(Array.isArray(v) && v.length === 0)) {
                                    _dmFiles[idx][k] = v;
                                }
                            }
                        }
                        _updateCardDetail(idx, d);
                        const pct = total > 0 ? (detailCount / total * 100) : 0;
                        if (scanBar) scanBar.style.width = pct + '%';
                        if (scanCount) scanCount.textContent = `${detailCount} / ${total}`;
                        if (scanLabel) scanLabel.textContent = `讀取資訊... ${d.filename}`;
                        _emitFileUpdated(idx);

                    } else if (evt.event === 'done') {
                        streamed = true;
                        _dmScanState.running = false;
                    }
                }
            }
        }
    } catch (e) {
        console.warn('[DM] SSE stream failed, falling back to batch scan:', e);
    }

    // Fallback: batch scan (if SSE didn't produce results)
    if (!streamed) {
        if (scanLabel) scanLabel.textContent = '掃描中（批次模式）...';
        try {
            const res = await fetch('/api/v1/drone_meta/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths }),
            });
            const data = await res.json();
            _dmFiles = (data.files || []).map(f => ({ ...f, selected: true }));
            _dmFiles.forEach((f, i) => _appendFileCard(f, i));
        } catch (e) {
            status.textContent = '掃描失敗';
            if (scanProgress) scanProgress.classList.add('hidden');
            console.error('[DM] batch scan error:', e);
            _dmScanState.running = false;
            return;
        }
    }

    // Finalize
    if (_dmFiles.length > 0) {
        status.textContent = `找到 ${_dmFiles.length} 個影片`;
        const toolbar = document.getElementById('dm_grid_toolbar');
        const clearBtn = document.getElementById('dm_btn_clear');
        if (toolbar) toolbar.classList.remove('hidden');
        if (clearBtn) clearBtn.classList.remove('hidden');
        _updateSelectCount();
    }
    if (scanProgress) scanProgress.classList.add('hidden');
    _dmScanState.running = false;
}
window.dmScanFiles = dmScanFiles;

// ── Card Grid Rendering ──

function _appendFileCard(f, idx) {
    const grid = document.getElementById('dm_file_grid') || document.getElementById('dm_file_list');
    if (!grid) return;
    const placeholder = grid.querySelector('.col-span-full') || grid.querySelector('.text-center');
    if (placeholder) placeholder.remove();

    const card = createClipCard(f, idx, {
        cardClass: 'dm-file-card clip-card bg-[#252525] rounded-lg border border-[#3a3a3a] overflow-hidden cursor-grab',
        accentColor: 'blue',
        showAction: false,
        showRefresh: true,
        onToggle: (i, checked) => { if (_dmFiles[i]) _dmFiles[i].selected = checked; _updateSelectCount(); },
        onRefresh: _refreshDmFile,
        onReorder: _reorderDmFiles,
    });
    grid.appendChild(card);
}

async function _refreshDmFile(idx) {
    const f = _dmFiles[idx];
    if (!f?.path) return;
    try {
        const res = await fetch(`/api/v1/drone_meta/rescan_file?path=${encodeURIComponent(f.path)}`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data?.path) {
            // Replace fields in-place (preserve user edits like selected)
            for (const [k, v] of Object.entries(data)) {
                if (v !== '' && v !== null && !(Array.isArray(v) && v.length === 0)) {
                    _dmFiles[idx][k] = v;
                }
            }
            _updateCardDetail(idx, data);
            // Also update thumbnail wrap
            const cards = document.querySelectorAll('.dm-file-card');
            if (cards[idx] && data.thumbnail) {
                const wrap = cards[idx].querySelector('.clip-thumb-wrap');
                if (wrap) wrap.innerHTML = `<img src="${data.thumbnail}" alt="" class="clip-thumb w-full h-full object-cover">`;
            }
            _emitFileUpdated(idx);
        }
    } catch (e) {
        console.warn('[DM] refresh failed:', e);
    }
}

function _reorderDmFiles(from, to) {
    // DOM was already reordered live during dragover; sync _dmFiles to match.
    const [file] = _dmFiles.splice(from, 1);
    _dmFiles.splice(to, 0, file);
    // Re-number dataset.idx so the next drag reads fresh positions.
    const grid = document.getElementById('dm_file_grid') || document.getElementById('dm_file_list');
    if (grid) for (let i = 0; i < grid.children.length; i++) grid.children[i].dataset.idx = i;
}

function _updateCardDetail(idx, d) {
    const cards = document.querySelectorAll('.dm-file-card');
    const card = cards[idx];
    if (!card) return;

    const metaEl = card.querySelector('.clip-meta');
    if (metaEl && d.width && d.height) {
        metaEl.textContent = `${d.width}x${d.height} | ${d.codec} | ${_fmtSize(d.size)}`;
    }

    if (d.duration) {
        let badge = card.querySelector('.clip-dur-badge');
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'absolute top-1 right-1 text-[10px] text-white bg-black/60 px-1 rounded clip-dur-badge';
            const rel = card.querySelector('.relative');
            if (rel) rel.appendChild(badge);
        }
        badge.textContent = _fmtDuration(d.duration);
    }

    const djiEl = card.querySelector('.clip-dji-info');
    if (djiEl && d.is_dji && d.dji_gps && d.dji_camera) {
        djiEl.innerHTML = `<div class="text-[10px] text-blue-400 mt-0.5">\u{1F4CD} ${d.dji_gps.lat.toFixed(4)}°N, ${d.dji_gps.lon.toFixed(4)}°E | ${d.dji_gps.alt.toFixed(0)}m</div>
            <div class="text-[10px] text-blue-400">ISO:${d.dji_camera.iso} | f/${d.dji_camera.fnum} | 1/${d.dji_camera.shutter}</div>`;
    }

    if (d.creation_time) {
        const { date, time } = parseCreationTimeLocal(d.creation_time);
        const dateInput = card.querySelector('.clip-date');
        const timeInput = card.querySelector('.clip-time');
        if (dateInput && !dateInput.value && date) dateInput.value = date;
        if (timeInput && !timeInput.value && time) timeInput.value = time;
    }
}

function _renderFileGrid() {
    const grid = document.getElementById('dm_file_grid') || document.getElementById('dm_file_list');
    if (!grid) return;
    if (!_dmFiles.length) {
        grid.innerHTML = '<div class="text-sm text-gray-500 text-center py-8 col-span-full">尚未掃描影片，請先選擇來源資料夾</div>';
        const toolbar = document.getElementById('dm_grid_toolbar');
        const clearBtn = document.getElementById('dm_btn_clear');
        if (toolbar) toolbar.classList.add('hidden');
        if (clearBtn) clearBtn.classList.add('hidden');
        return;
    }
    const toolbar = document.getElementById('dm_grid_toolbar');
    const clearBtn = document.getElementById('dm_btn_clear');
    if (toolbar) toolbar.classList.remove('hidden');
    if (clearBtn) clearBtn.classList.remove('hidden');

    grid.innerHTML = '';
    _dmFiles.forEach((f, i) => _appendFileCard(f, i));
    _updateSelectCount();
}

// ── Select / Clear ──

function dmToggleFile(idx, checked) {
    if (_dmFiles[idx]) _dmFiles[idx].selected = checked;
    _updateSelectCount();
}
window.dmToggleFile = dmToggleFile;

function dmToggleSelectAll(checked) {
    _dmFiles.forEach(f => { f.selected = checked; });
    document.querySelectorAll('.clip-check').forEach(cb => cb.checked = checked);
    _updateSelectCount();
}
window.dmToggleSelectAll = dmToggleSelectAll;

function dmClearFiles() {
    _dmFiles = [];
    window._concatAdvancedClips = null;
    _renderFileGrid();
    document.getElementById('dm_scan_status').textContent = '';
}
window.dmClearFiles = dmClearFiles;

// ── Batch Time Functions ──

window.dmBatchApplyTime = function() {
    const date = document.getElementById('dm_batch_date')?.value;
    const time = document.getElementById('dm_batch_time')?.value;
    if (!date || !time) { alert('請先設定日期和時間'); return; }
    document.querySelectorAll('.dm-file-card').forEach(card => {
        const chk = card.querySelector('.clip-check');
        if (chk && chk.checked) {
            const dateInput = card.querySelector('.clip-date');
            const timeInput = card.querySelector('.clip-time');
            if (dateInput) dateInput.value = date;
            if (timeInput) timeInput.value = time;
        }
    });
};

window.dmBatchIncrementTime = function() {
    const date = document.getElementById('dm_batch_date')?.value;
    const time = document.getElementById('dm_batch_time')?.value;
    const increment = parseInt(document.getElementById('dm_batch_increment')?.value) || 1;
    if (!date || !time) { alert('請先設定起始日期和時間'); return; }

    let baseTime = new Date(`${date}T${time}`);
    let count = 0;
    document.querySelectorAll('.dm-file-card').forEach(card => {
        const chk = card.querySelector('.clip-check');
        if (chk && chk.checked) {
            const dt = new Date(baseTime.getTime() + count * increment * 60000);
            const dateInput = card.querySelector('.clip-date');
            const timeInput = card.querySelector('.clip-time');
            if (dateInput) dateInput.value = dt.toISOString().substring(0, 10);
            if (timeInput) timeInput.value = dt.toTimeString().substring(0, 8);
            count++;
        }
    });
};

// ── Model Change ──

function dmOnModelChange() {
    const sel = document.getElementById('dm_drone_model').value;
    document.getElementById('dm_custom_model_fields').classList.toggle('hidden', sel !== 'custom');
}
window.dmOnModelChange = dmOnModelChange;

// ── Concat Toggle ──

function dmToggleConcat() {
    const checked = document.getElementById('dm_do_concat').checked;
    document.getElementById('dm_concat_options').classList.toggle('hidden', !checked);
    document.getElementById('dm_concat_placeholder').classList.toggle('hidden', checked);
}
window.dmToggleConcat = dmToggleConcat;

// ── Collect Payload ──

function _getDroneModelInfo() {
    const sel = document.getElementById('dm_drone_model').value;
    if (sel === 'custom') {
        return {
            make: document.getElementById('dm_custom_make').value.trim() || 'Unknown',
            model: document.getElementById('dm_custom_model').value.trim() || 'Unknown',
            lensMake: document.getElementById('dm_custom_lens_make').value.trim() || '',
            lensModel: document.getElementById('dm_custom_lens_model').value.trim() || '',
        };
    }
    return DRONE_MODELS[sel] || DRONE_MODELS.autel_evo_lite_plus;
}

function _getFileSetting(idx) {
    const f = _dmFiles[idx];
    // Per-file time override (from card's date/time inputs)
    const cards = document.querySelectorAll('.dm-file-card');
    let dateTimeOverride = '';
    if (cards[idx]) {
        const dateInput = cards[idx].querySelector('.clip-date');
        const timeInput = cards[idx].querySelector('.clip-time');
        const dateVal = dateInput?.value || '';
        const timeVal = timeInput?.value || '';
        if (dateVal && timeVal) dateTimeOverride = `${dateVal}T${timeVal}`;
    }
    // Color/trim fields live on _dmFiles[idx] (written back by advanced
    // edit modal via dmfile:order-synced event). Read them through.
    const num = (v, def) => (typeof v === 'number' && !isNaN(v)) ? v : def;
    return {
        path: f.path,
        trim_in: num(f.trim_in, 0),
        trim_out: num(f.trim_out, -1),
        brightness: num(f.brightness, 0),
        contrast: num(f.contrast, 1),
        saturation: num(f.saturation, 1),
        gamma: num(f.gamma, 1),
        color_temp: num(f.color_temp, 0),
        tint: num(f.tint, 0),
        shadows: num(f.shadows, 0),
        midtones: num(f.midtones, 0),
        highlights: num(f.highlights, 0),
        curve_points: Array.isArray(f.curve_points) ? f.curve_points : null,
        date_time_override: dateTimeOverride,
    };
}

function collectDroneMetaPayload() {
    const fileIndex = parseInt(document.getElementById('dm_file_index').value) || 1;
    const selectedFiles = [];
    // Collect in _dmFiles order (which is the user's drag-sorted order)
    for (let i = 0; i < _dmFiles.length; i++) {
        if (_dmFiles[i].selected) selectedFiles.push(_getFileSetting(i));
    }
    if (!selectedFiles.length) { alert('請至少勾選一個影片檔'); return { valid: false }; }

    const firstDate = selectedFiles[0]?.date_time_override || new Date().toISOString();

    const model = _getDroneModelInfo();
    const doConcat = document.getElementById('dm_do_concat').checked;

    const payload = {
        file_index: fileIndex,
        files: selectedFiles,
        output_dir: document.getElementById('dm_output_dir').value.trim(),
        date_time: firstDate,
        drone_make: model.make,
        drone_model: model.model,
        lens_make: model.lensMake,
        lens_model: model.lensModel,
        do_concat: doConcat,
        concat_dest_dir: doConcat ? document.getElementById('dm_concat_dest').value.trim() : '',
        concat_custom_name: document.getElementById('dm_concat_name')?.value.trim() || '',
        concat_resolution: document.getElementById('dm_concat_res').value,
        concat_codec: document.getElementById('dm_concat_codec').value,
        concat_burn_timecode: document.getElementById('dm_concat_tc').checked,
        concat_burn_filename: document.getElementById('dm_concat_fn').checked,
        concat_xfade_enabled: !!window._concatXfadeEnabled,
        concat_xfade_type: window._concatXfadeType || 'dissolve',
        concat_xfade_duration: parseFloat(window._concatXfadeDuration) || 1.0,
    };

    if (doConcat && !payload.concat_dest_dir) {
        alert('請填寫串帶輸出目錄');
        return { valid: false };
    }

    return { valid: true, payload };
}
window.collectDroneMetaPayload = collectDroneMetaPayload;

// ── Submit ──

async function submitDroneMeta() {
    if (_dmSubmitting) return;
    _dmSubmitting = true;
    window._activeJobTab = 'drone_meta';

    const btn = document.querySelector('.dm-start-btn');
    if (btn) {
        btn.disabled = true;
        btn.classList.add('opacity-70', 'cursor-not-allowed');
        btn._origText = btn.textContent;
        btn.textContent = '提交中...';
    }

    try {
        resetProgress();
        const collected = collectDroneMetaPayload();
        if (!collected.valid) return;
        const payload = collected.payload;

        const host = window.collectSelectedHost ? window.collectSelectedHost('dm_host_checkboxes') : { name: '本機', ip: 'local' };
        const isLocal = host.ip === 'local';
        const hostUrl = isLocal ? getComputeBaseUrl() : 'http://' + host.ip;
        const hostName = host.name || host.ip;

        if (!isLocal) {
            // Convert paths to UNC so remote host can access via network share
            await ensureDriveMap();
            if (Array.isArray(payload.files)) {
                payload.files = payload.files.map(f => ({ ...f, path: toUncPath(f.path) }));
            }
            if (payload.output_dir) payload.output_dir = toUncPath(payload.output_dir);
            if (payload.concat_dest_dir) payload.concat_dest_dir = toUncPath(payload.concat_dest_dir);

            const pathsToCheck = [payload.output_dir, ...(payload.files || []).map(f => f.path)];
            if (payload.concat_dest_dir) pathsToCheck.push(payload.concat_dest_dir);
            try {
                const result = await validateRemotePaths(host.ip, pathsToCheck.filter(Boolean));
                if (!result.ok) {
                    alert(`\u26a0 遠端主機 [${hostName}] 路徑檢查失敗：\n\n${result.errors.join('\n')}\n\n請確認該主機是否已映射對應磁碟機。`);
                    return;
                }
            } catch (e) {
                alert(`\u26a0 無法連線至遠端主機 [${hostName}] 進行路徑驗證：${e.message}`);
                return;
            }
        }

        const url = hostUrl + '/api/v1/jobs/drone_meta';
        window._lastJob = { url, payload };
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const result = await res.json();
        if (result.status === 'queued') {
            appendLog(`空拍寫入任務已提交至 [${hostName}] (job: ${result.job_id})`, 'info');
            if (isLocal) {
                const progEl = document.getElementById('dm-progress');
                if (progEl) progEl.classList.remove('hidden');
                // 本機任務會由 local socket 的 task_status event 觸發按鈕切換，
                // 但避免時序空窗，這裡先 optimistic 切一次
                if (window.updateActionBarState) window.updateActionBarState('running');
            } else if (window.initRemoteHostProgress && window.startHeartbeatMonitor) {
                // Reuse the shared remote-host progress panel + heartbeat monitor.
                window._activeRemoteHosts = window._activeRemoteHosts || {};
                window._remoteJobType = 'drone_meta';
                window.initRemoteHostProgress([{ name: hostName, ip: host.ip }]);
                window.updateHostProgress && window.updateHostProgress(host.ip, 10, '任務已接收...', '#1f538d');
                window._activeRemoteHosts[host.ip] = {
                    host: { name: hostName, ip: host.ip },
                    lastSeen: Date.now(),
                    startTime: Date.now(),
                    expectedJobs: 1,
                    pct: 0,
                };
                window.startHeartbeatMonitor();
                // 遠端任務：local socket 收不到 remote 的 task_status，手動切
                // 按鈕為 running，heartbeat 偵測全部完成時會再切回 idle
                if (window.updateActionBarState) window.updateActionBarState('running');
            }
        } else {
            appendLog(`提交失敗: ${JSON.stringify(result)}`, 'error');
        }
    } catch (e) {
        appendLog(`提交失敗: ${e.message}`, 'error');
    } finally {
        _dmSubmitting = false;
        if (btn) {
            btn.disabled = false;
            btn.classList.remove('opacity-70', 'cursor-not-allowed');
            btn.textContent = btn._origText || '開始執行';
        }
    }
}
window.submitDroneMeta = submitDroneMeta;

// ── Progress listener ──

function _setupProgressListener() {
    if (!window._socket) return;
    window._socket.on('progress', (data) => {
        if (data.phase !== 'drone_meta' && data.phase !== 'concat') return;
        const pct = data.total_pct || 0;
        const bar = document.getElementById('dm-prog-bar');
        const label = document.getElementById('dm-prog-label');
        const detail = document.getElementById('dm-prog-detail');
        if (bar) bar.style.width = pct + '%';
        if (label) {
            if (data.phase === 'concat') {
                label.textContent = `串帶中... ${Math.round(pct)}%`;
            } else {
                label.textContent = `進度：${data.done_files || 0}/${data.total_files || 0} 檔案 (${Math.round(pct)}%)`;
            }
        }
        if (detail && data.current_file) {
            detail.textContent = `${data.current_file} → ${data.target_file || ''}`;
        }
    });
}

// ── Init ──

// Apply order + selection from advanced edit modal to outer grid.
function _syncOrderFromAdvanced(e) {
    const paths = e.detail?.paths;
    if (!Array.isArray(paths) || !paths.length) return;
    // `in` check: empty selectedPaths (all unchecked) is valid, missing key is not.
    const hasSelection = e.detail && ('selectedPaths' in e.detail);
    const selectedSet = new Set(hasSelection ? (e.detail.selectedPaths || []) : []);

    // Color/trim edits from modal (keyed by path). Missing = no edits on that clip.
    const edits = (e.detail && e.detail.edits) || {};

    const byPath = new Map(_dmFiles.map(f => [f.path, f]));
    const newOrder = [];
    for (const p of paths) {
        const f = byPath.get(p);
        if (f) {
            if (hasSelection) f.selected = selectedSet.has(p);
            const e = edits[p];
            if (e) Object.assign(f, e);
            newOrder.push(f);
            byPath.delete(p);
        }
    }
    // Append any _dmFiles not covered by the event (safety)
    for (const f of byPath.values()) newOrder.push(f);
    _dmFiles = newOrder;
    _renderFileGrid();
    _updateSelectCount();
}

export async function initDroneMetaTab() {
    _setupProgressListener();
    window.addEventListener('dmfile:order-synced', _syncOrderFromAdvanced);
}
