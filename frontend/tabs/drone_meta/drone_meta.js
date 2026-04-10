import { appendLog, resetProgress, pickPath } from '../../js/shared/utils.js';
import { renderClipDetail } from './drone_meta_editor.js';

// ── State ──
let _dmFiles = [];       // scanned file objects from backend
let _dmSelected = [];    // boolean array, same length as _dmFiles
let _dmSubmitting = false;

// ── Arrange Panel State ──
let _arrangeClips = []; // [{id, fileIdx, in, out, brightness, contrast, saturation, gamma, colorTemp}]
let _clipIdCounter = 0;

const DRONE_MODELS = {
    autel_evo_lite_plus: { make: 'Autel Robotics', model: 'EVO Lite+', lensMake: 'Autel Robotics', lensModel: 'EVO Lite+ Camera' },
    dji_mavic3:          { make: 'DJI', model: 'Mavic 3', lensMake: 'DJI', lensModel: 'Mavic 3 Camera' },
    dji_mini4pro:        { make: 'DJI', model: 'Mini 4 Pro', lensMake: 'DJI', lensModel: 'Mini 4 Pro Camera' },
    dji_air3:            { make: 'DJI', model: 'Air 3', lensMake: 'DJI', lensModel: 'Air 3 Camera' },
};

// ── Helpers ──

function _fmtDuration(sec) {
    if (!sec || sec <= 0) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function _fmtSize(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
}

function _secToHMS(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = (sec % 60).toFixed(1);
    return h > 0 ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(4,'0')}` : `${m}:${String(s).padStart(4,'0')}`;
}

function _updateSelectCount() {
    const count = _dmSelected.filter(Boolean).length;
    const el = document.getElementById('dm_select_count');
    if (el) el.textContent = `已勾選 ${count} 個`;
    const allCb = document.getElementById('dm_select_all');
    if (allCb) allCb.checked = count === _dmFiles.length && count > 0;
}

// ── File Picking ──

async function dmPickFolder() {
    try {
        const res = await fetch('/api/v1/utils/pick_folder?title=選擇空拍影片資料夾');
        const data = await res.json();
        if (data.path) {
            document.getElementById('dm_source_path').value = data.path;
            dmScanFiles(); // auto scan after pick
        }
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
            dmScanFiles(); // auto scan after pick
        }
    } catch (e) { console.warn('pick file failed:', e); }
}
window.dmPickFiles = dmPickFiles;

// ── Scan ──

async function dmScanFiles() {
    const raw = document.getElementById('dm_source_path').value.trim();
    if (!raw) { alert('請先輸入或選擇來源路徑'); return; }

    const paths = raw.split(',').map(p => p.trim()).filter(Boolean);
    const status = document.getElementById('dm_scan_status');
    status.textContent = '掃描中...';

    try {
        const res = await fetch('/api/v1/drone_meta/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        _dmFiles = data.files || [];
        _dmSelected = _dmFiles.map(() => true);
        status.textContent = `找到 ${_dmFiles.length} 個影片`;
        _renderFileList();
    } catch (e) {
        status.textContent = '掃描失敗';
        console.error('scan error:', e);
    }
}
window.dmScanFiles = dmScanFiles;

// ── Render File List ──

function _renderFileList() {
    const container = document.getElementById('dm_file_list');
    const clearBtn = document.getElementById('dm_btn_clear');

    if (!_dmFiles.length) {
        container.innerHTML = '<div class="text-sm text-gray-500 text-center py-4">尚未匯入影片</div>';
        if (clearBtn) clearBtn.classList.add('hidden');
        return;
    }

    if (clearBtn) clearBtn.classList.remove('hidden');
    // Auto-select all on first render
    if (_dmSelected.length === 0 || _dmSelected.every(s => !s)) {
        _dmSelected = _dmFiles.map(() => true);
    }
    _updateSelectCount();

    container.innerHTML = _dmFiles.map((f, i) => `
        <div class="dm-file-card flex flex-col bg-[#252525] rounded border border-[#3a3a3a] overflow-hidden"
             draggable="true" data-idx="${i}"
             ondragstart="dmDragStart(event, ${i})" ondragover="dmDragOver(event)" ondrop="dmDrop(event, ${i})">
            <!-- Main row -->
            <div class="flex items-center gap-3 p-2">
                <span class="cursor-grab text-gray-600 select-none" title="拖拽排序">⠿</span>
                <input type="checkbox" ${_dmSelected[i] ? 'checked' : ''}
                    onchange="dmToggleFile(${i}, this.checked)"
                    data-idx="${i}"
                    class="dm-file-check form-checkbox text-blue-500 bg-[#1e1e1e] border-[#444] rounded flex-shrink-0">
                <img src="${f.thumbnail || ''}" alt="" class="w-24 h-14 object-cover rounded bg-[#1a1a1a] flex-shrink-0"
                     onerror="this.style.display='none'">
                <div class="flex-1 min-w-0">
                    <div class="text-sm text-gray-200 truncate">${f.filename}</div>
                    <div class="text-xs text-gray-500">${f.width}x${f.height} | ${f.codec} | ${_fmtDuration(f.duration)} | ${_fmtSize(f.size)}</div>
                    ${f.is_dji ? `<div class="text-xs text-blue-400 mt-1">\u{1F4CD} ${f.dji_gps.lat.toFixed(4)}\u00B0N, ${f.dji_gps.lon.toFixed(4)}\u00B0E | ${f.dji_gps.alt.toFixed(0)}m | ISO:${f.dji_camera.iso} | f/${f.dji_camera.fnum} | 1/${f.dji_camera.shutter}</div>` : ''}
                    <div class="flex items-center gap-2 mt-1">
                        <span class="text-xs text-gray-500">\u{1F4C5}</span>
                        <input type="date" class="dm-file-date bg-[#1e1e1e] border border-[#444] rounded px-2 py-0.5 text-xs"
                            data-idx="${i}" value="${f.creation_time ? f.creation_time.substring(0,10) : ''}">
                        <input type="time" class="dm-file-time bg-[#1e1e1e] border border-[#444] rounded px-2 py-0.5 text-xs"
                            data-idx="${i}" step="1" value="${f.creation_time ? f.creation_time.substring(11,19) : ''}">
                    </div>
                </div>
                <button onclick="dmToggleEdit(${i})"
                    class="text-xs bg-[#333] hover:bg-[#444] px-2 py-1 rounded border border-[#555] text-gray-300 flex-shrink-0">
                    🔧 修剪/色彩
                </button>
            </div>
            <!-- Edit panel (hidden by default) -->
            <div id="dm_edit_${i}" class="hidden border-t border-[#333] p-3 bg-[#1e1e1e]">
                ${_renderEditPanel(f, i)}
            </div>
        </div>
    `).join('');
}

function _renderEditPanel(file, idx) {
    const dur = file.duration || 0;
    const filmstripHtml = (file.filmstrip || []).map(src =>
        `<img src="${src}" class="h-10 rounded-sm flex-shrink-0" onerror="this.style.display='none'">`
    ).join('');

    return `
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <!-- 修剪 -->
        <div>
            <div class="text-xs text-gray-400 font-semibold mb-2">修剪 Trim</div>
            <!-- Filmstrip -->
            <div class="flex gap-0.5 mb-2 overflow-x-auto rounded">${filmstripHtml || '<span class="text-xs text-gray-600">無預覽幀</span>'}</div>
            <!-- Dual range slider -->
            <div class="dm-range-wrap relative h-6 mb-2">
                <input type="range" id="dm_trim_in_${idx}" min="0" max="${dur}" step="0.1" value="0"
                    class="dm-range dm-range-lo absolute w-full" oninput="dmUpdateTrim(${idx})">
                <input type="range" id="dm_trim_out_${idx}" min="0" max="${dur}" step="0.1" value="${dur}"
                    class="dm-range dm-range-hi absolute w-full" oninput="dmUpdateTrim(${idx})">
            </div>
            <div class="flex gap-2 items-center text-xs">
                <label class="text-gray-500">In:</label>
                <input type="text" id="dm_trim_in_txt_${idx}" value="0:00.0"
                    class="w-20 bg-[#2a2a2a] border border-[#444] rounded px-1 py-0.5 text-xs text-center"
                    onchange="dmTrimTextChanged(${idx}, 'in')">
                <label class="text-gray-500">Out:</label>
                <input type="text" id="dm_trim_out_txt_${idx}" value="${_secToHMS(dur)}"
                    class="w-20 bg-[#2a2a2a] border border-[#444] rounded px-1 py-0.5 text-xs text-center"
                    onchange="dmTrimTextChanged(${idx}, 'out')">
                <span id="dm_trim_dur_${idx}" class="text-gray-500 ml-auto">時長: ${_secToHMS(dur)}</span>
            </div>
        </div>
        <!-- 色彩 -->
        <div>
            <div class="flex items-center justify-between mb-2">
                <span class="text-xs text-gray-400 font-semibold">色彩調整 Color</span>
                <button onclick="dmResetColor(${idx})" class="text-xs text-blue-400 hover:text-blue-300">重置</button>
            </div>
            <div class="space-y-2">
                ${_colorSlider(idx, 'brightness', '亮度', -1, 1, 0, 0.05)}
                ${_colorSlider(idx, 'contrast', '對比度', 0, 2, 1, 0.05)}
                ${_colorSlider(idx, 'saturation', '飽和度', 0, 3, 1, 0.05)}
                ${_colorSlider(idx, 'gamma', 'Gamma', 0.1, 3, 1, 0.05)}
                ${_colorSlider(idx, 'color_temp', '色溫 (冷←→暖)', -1, 1, 0, 0.05)}
            </div>
        </div>
    </div>`;
}

function _colorSlider(idx, name, label, min, max, def, step) {
    return `
    <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500 w-24 flex-shrink-0">${label}</label>
        <input type="range" id="dm_color_${name}_${idx}" min="${min}" max="${max}" step="${step}" value="${def}"
            class="flex-1 h-1.5 accent-blue-500" oninput="dmColorChanged(${idx}, '${name}')">
        <span id="dm_color_${name}_val_${idx}" class="text-xs text-gray-400 w-10 text-right">${def}</span>
    </div>`;
}

// ── Edit Panel Toggle ──

function dmToggleEdit(idx) {
    const el = document.getElementById(`dm_edit_${idx}`);
    if (el) el.classList.toggle('hidden');
}
window.dmToggleEdit = dmToggleEdit;

// ── Trim Controls ──

function dmUpdateTrim(idx) {
    const loEl = document.getElementById(`dm_trim_in_${idx}`);
    const hiEl = document.getElementById(`dm_trim_out_${idx}`);
    let lo = parseFloat(loEl.value);
    let hi = parseFloat(hiEl.value);
    if (lo > hi) { lo = hi; loEl.value = lo; }
    document.getElementById(`dm_trim_in_txt_${idx}`).value = _secToHMS(lo);
    document.getElementById(`dm_trim_out_txt_${idx}`).value = _secToHMS(hi);
    document.getElementById(`dm_trim_dur_${idx}`).textContent = `時長: ${_secToHMS(hi - lo)}`;
}
window.dmUpdateTrim = dmUpdateTrim;

function dmTrimTextChanged(idx, which) {
    // Parse M:SS.s or H:MM:SS.s
    const txt = document.getElementById(`dm_trim_${which}_txt_${idx}`).value.trim();
    const parts = txt.split(':').map(Number);
    let sec = 0;
    if (parts.length === 3) sec = parts[0] * 3600 + parts[1] * 60 + parts[2];
    else if (parts.length === 2) sec = parts[0] * 60 + parts[1];
    else sec = parts[0] || 0;
    const slider = document.getElementById(`dm_trim_${which === 'in' ? 'in' : 'out'}_${idx}`);
    slider.value = sec;
    dmUpdateTrim(idx);
}
window.dmTrimTextChanged = dmTrimTextChanged;

// ── Color Controls ──

function dmColorChanged(idx, name) {
    const val = document.getElementById(`dm_color_${name}_${idx}`).value;
    document.getElementById(`dm_color_${name}_val_${idx}`).textContent = val;
}
window.dmColorChanged = dmColorChanged;

function dmResetColor(idx) {
    const defaults = { brightness: 0, contrast: 1, saturation: 1, gamma: 1, color_temp: 0 };
    for (const [name, def] of Object.entries(defaults)) {
        const el = document.getElementById(`dm_color_${name}_${idx}`);
        if (el) { el.value = def; dmColorChanged(idx, name); }
    }
}
window.dmResetColor = dmResetColor;

// ── Select / Drag ──

function dmToggleFile(idx, checked) {
    _dmSelected[idx] = checked;
    _updateSelectCount();
}
window.dmToggleFile = dmToggleFile;

function dmToggleSelectAll(checked) {
    _dmSelected = _dmSelected.map(() => checked);
    document.querySelectorAll('#dm_file_list input[type="checkbox"]').forEach(cb => cb.checked = checked);
    _updateSelectCount();
}
window.dmToggleSelectAll = dmToggleSelectAll;

function dmClearFiles() {
    _dmFiles = [];
    _dmSelected = [];
    _renderFileList();
}
window.dmClearFiles = dmClearFiles;

let _dmDragIdx = -1;
function dmDragStart(e, idx) {
    _dmDragIdx = idx;
    e.dataTransfer.setData('application/dm-source-idx', String(idx));
    e.dataTransfer.effectAllowed = 'copyMove';
}
window.dmDragStart = dmDragStart;
function dmDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
window.dmDragOver = dmDragOver;
function dmDrop(e, targetIdx) {
    e.preventDefault();
    if (_dmDragIdx < 0 || _dmDragIdx === targetIdx) return;
    // Swap in arrays
    const [file] = _dmFiles.splice(_dmDragIdx, 1);
    const [sel] = _dmSelected.splice(_dmDragIdx, 1);
    _dmFiles.splice(targetIdx, 0, file);
    _dmSelected.splice(targetIdx, 0, sel);
    _dmDragIdx = -1;
    _renderFileList();
}
window.dmDrop = dmDrop;

// ── Batch Time Functions ──

window.dmBatchApplyTime = function() {
    const date = document.getElementById('dm_batch_date').value;
    const time = document.getElementById('dm_batch_time').value;
    if (!date || !time) { alert('請先設定日期和時間'); return; }
    document.querySelectorAll('.dm-file-card').forEach(card => {
        const chk = card.querySelector('.dm-file-check');
        if (chk && chk.checked) {
            const dateInput = card.querySelector('.dm-file-date');
            const timeInput = card.querySelector('.dm-file-time');
            if (dateInput) dateInput.value = date;
            if (timeInput) timeInput.value = time;
        }
    });
};

window.dmBatchIncrementTime = function() {
    const date = document.getElementById('dm_batch_date').value;
    const time = document.getElementById('dm_batch_time').value;
    const increment = parseInt(document.getElementById('dm_batch_increment').value) || 1;
    if (!date || !time) { alert('請先設定起始日期和時間'); return; }

    let baseTime = new Date(`${date}T${time}`);
    let count = 0;
    document.querySelectorAll('.dm-file-card').forEach(card => {
        const chk = card.querySelector('.dm-file-check');
        if (chk && chk.checked) {
            const dt = new Date(baseTime.getTime() + count * increment * 60000);
            const dateInput = card.querySelector('.dm-file-date');
            const timeInput = card.querySelector('.dm-file-time');
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
    const trimIn = parseFloat(document.getElementById(`dm_trim_in_${idx}`)?.value || '0');
    const trimOutEl = document.getElementById(`dm_trim_out_${idx}`);
    const trimOut = trimOutEl ? parseFloat(trimOutEl.value) : -1;
    const dur = f.duration || 0;

    // Per-file time override
    const cards = document.querySelectorAll('.dm-file-card');
    let dateTimeOverride = '';
    if (cards[idx]) {
        const dateInput = cards[idx].querySelector('.dm-file-date');
        const timeInput = cards[idx].querySelector('.dm-file-time');
        const dateVal = dateInput?.value || '';
        const timeVal = timeInput?.value || '';
        if (dateVal && timeVal) dateTimeOverride = `${dateVal}T${timeVal}`;
    }

    return {
        path: f.path,
        trim_in: trimIn > 0 ? trimIn : 0,
        trim_out: (trimOut < dur && trimOut >= 0) ? trimOut : -1,
        brightness: parseFloat(document.getElementById(`dm_color_brightness_${idx}`)?.value || '0'),
        contrast: parseFloat(document.getElementById(`dm_color_contrast_${idx}`)?.value || '1'),
        saturation: parseFloat(document.getElementById(`dm_color_saturation_${idx}`)?.value || '1'),
        gamma: parseFloat(document.getElementById(`dm_color_gamma_${idx}`)?.value || '1'),
        color_temp: parseFloat(document.getElementById(`dm_color_color_temp_${idx}`)?.value || '0'),
        date_time_override: dateTimeOverride,
    };
}

function collectDroneMetaPayload() {
    const fileIndex = parseInt(document.getElementById('dm_file_index').value) || 1;
    const selectedFiles = [];
    for (let i = 0; i < _dmFiles.length; i++) {
        if (_dmSelected[i]) selectedFiles.push(_getFileSetting(i));
    }
    if (!selectedFiles.length) { alert('請至少勾選一個影片檔'); return { valid: false }; }

    // Use first file's time as global fallback
    const firstDate = selectedFiles[0]?.date_time_override || new Date().toISOString();
    const dateTime = firstDate;

    const model = _getDroneModelInfo();
    const doConcat = document.getElementById('dm_do_concat').checked;

    const payload = {
        file_index: fileIndex,
        files: selectedFiles,
        output_dir: document.getElementById('dm_output_dir').value.trim(),
        date_time: dateTime,
        drone_make: model.make,
        drone_model: model.model,
        lens_make: model.lensMake,
        lens_model: model.lensModel,
        do_concat: doConcat,
        concat_dest_dir: doConcat ? document.getElementById('dm_concat_dest').value.trim() : '',
        concat_resolution: document.getElementById('dm_concat_res').value,
        concat_codec: document.getElementById('dm_concat_codec').value,
        concat_burn_timecode: document.getElementById('dm_concat_tc').checked,
        concat_burn_filename: document.getElementById('dm_concat_fn').checked,
    };

    if (doConcat && !payload.concat_dest_dir) {
        alert('請填寫串帶輸出目錄');
        return { valid: false };
    }

    return { valid: true, payload, name: projectName };
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

        window._lastJob = { url: '/api/v1/jobs/drone_meta', payload: collected.payload };
        const res = await fetch(window._lastJob.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collected.payload),
        });
        const result = await res.json();
        if (result.status === 'queued') {
            appendLog(`空拍寫入任務已提交 (job: ${result.job_id})`, 'info');
            // Show progress bar
            const progEl = document.getElementById('dm-progress');
            if (progEl) progEl.classList.remove('hidden');
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

// ── Arrange Panel ──

function _addClipToArrange(fileIdx) {
    const file = _dmFiles[fileIdx];
    if (!file) return;
    _arrangeClips.push({
        id: 'clip_' + (++_clipIdCounter),
        fileIdx,
        in: 0,
        out: file.duration || 0,
        brightness: 0.0,
        contrast: 1.0,
        saturation: 1.0,
        gamma: 1.0,
        colorTemp: 0.0,
    });
    _renderArrangeClips();
}

function _renderArrangeClips() {
    const container = document.getElementById('dm_arrange_clips');
    if (!container) return;
    if (_arrangeClips.length === 0) {
        container.innerHTML = '<div class="text-sm text-gray-600 w-full text-center py-8">拖拽左側素材到此處排列</div>';
        return;
    }
    container.innerHTML = _arrangeClips.map((clip, i) => {
        const file = _dmFiles[clip.fileIdx];
        const thumb = file?.thumbnail || '';
        const fname = file?.filename || '?';
        const durText = _fmtDuration(clip.out - clip.in);
        return `<div class="dm-arrange-card" draggable="true" data-clip-idx="${i}"
                    ondragstart="dmClipDragStart(event, ${i})"
                    ondragover="dmClipDragOver(event, this)"
                    ondragleave="dmClipDragLeave(this)"
                    ondrop="dmClipDrop(event, ${i})"
                    onclick="dmOpenDetail('${clip.id}')">
            <img src="${thumb}" class="w-full h-20 object-cover rounded-t" onerror="this.style.display='none'">
            <div class="px-2 py-1">
                <div class="text-xs text-gray-300 truncate">${fname}</div>
                <div class="text-xs text-gray-500">${durText}</div>
            </div>
            <button onclick="event.stopPropagation(); dmRemoveClip(${i})"
                class="absolute top-1 right-1 w-5 h-5 bg-black/60 rounded-full text-white text-xs flex items-center justify-center hover:bg-red-600">\u2715</button>
        </div>`;
    }).join('');
}

// Clip reorder drag within arrange panel
window.dmClipDragStart = function(e, clipIdx) {
    e.dataTransfer.setData('application/dm-clip-idx', String(clipIdx));
    e.dataTransfer.effectAllowed = 'move';
    e.stopPropagation(); // don't trigger source drag
};

window.dmClipDragOver = function(e, el) {
    e.preventDefault();
    e.stopPropagation();
    el.classList.add('dm-arrange-card-drop-target');
};

window.dmClipDragLeave = function(el) {
    el.classList.remove('dm-arrange-card-drop-target');
};

window.dmClipDrop = function(e, toIdx) {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.classList.remove('dm-arrange-card-drop-target');

    // Check if it's a clip reorder
    const clipFromStr = e.dataTransfer.getData('application/dm-clip-idx');
    if (clipFromStr !== '') {
        const from = parseInt(clipFromStr);
        if (!isNaN(from) && from !== toIdx) {
            const [moved] = _arrangeClips.splice(from, 1);
            _arrangeClips.splice(toIdx, 0, moved);
            _renderArrangeClips();
        }
        return;
    }

    // Check if it's a source file drop
    const srcStr = e.dataTransfer.getData('application/dm-source-idx');
    if (srcStr !== '') {
        const fileIdx = parseInt(srcStr);
        if (!isNaN(fileIdx) && _dmFiles[fileIdx]) {
            _addClipToArrange(fileIdx);
        }
    }
};

// Open detail panel for a clip
window.dmOpenDetail = function(clipId) {
    const clip = _arrangeClips.find(c => c.id === clipId);
    if (!clip) return;
    const file = _dmFiles[clip.fileIdx];
    document.getElementById('dm_arrange_panel').classList.add('hidden');
    document.getElementById('dm_detail_panel').classList.remove('hidden');
    renderClipDetail(clip, file, _arrangeClips, _fmtDuration);
};

window.dmRemoveClip = function(idx) {
    _arrangeClips.splice(idx, 1);
    _renderArrangeClips();
};

window.dmBackToArrange = function() {
    document.getElementById('dm_detail_panel').classList.add('hidden');
    document.getElementById('dm_arrange_panel').classList.remove('hidden');
    _renderArrangeClips();
};

function _initArrangePanel() {
    const arrangeEl = document.getElementById('dm_arrange_clips');
    if (!arrangeEl) return;

    arrangeEl.addEventListener('dragover', e => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        arrangeEl.classList.add('dm-arrange-drop-active');
    });
    arrangeEl.addEventListener('dragleave', e => {
        // Only remove if actually leaving the container
        if (!arrangeEl.contains(e.relatedTarget)) {
            arrangeEl.classList.remove('dm-arrange-drop-active');
        }
    });
    arrangeEl.addEventListener('drop', e => {
        e.preventDefault();
        arrangeEl.classList.remove('dm-arrange-drop-active');

        // Check if it's from source file list
        const srcStr = e.dataTransfer.getData('application/dm-source-idx');
        if (srcStr !== '') {
            const fileIdx = parseInt(srcStr);
            if (!isNaN(fileIdx) && _dmFiles[fileIdx]) {
                _addClipToArrange(fileIdx);
            }
        }
    });
}

// ── Init ──

export async function initDroneMetaTab() {
    // Set default date/time to now
    const now = new Date();
    const dateStr = now.toISOString().split('T')[0];
    _setupProgressListener();
    _initArrangePanel();
}
