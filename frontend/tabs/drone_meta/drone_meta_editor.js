/**
 * drone_meta_editor.js -- Clip detail editor (timeline + in/out + color grading)
 */

// ── Helpers ──

function _fmtHMS(sec) {
    if (!sec || sec <= 0) return '00:00:00';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function _parseHMS(txt) {
    if (!txt) return 0;
    const parts = txt.trim().split(':').map(Number);
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return parts[0] || 0;
}

function _colorSlider(name, label, value, min, max, step) {
    return `
    <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500 w-20 flex-shrink-0">${label}</label>
        <input type="range" id="dm_detail_color_${name}" min="${min}" max="${max}" step="${step}" value="${value}"
            class="flex-1 h-1.5 accent-blue-500" data-color-name="${name}">
        <span id="dm_detail_color_${name}_val" class="text-xs text-gray-400 w-10 text-right">${value}</span>
    </div>`;
}

// ── State for current detail ──
let _currentClip = null;
let _currentFile = null;
let _colorDebounceTimer = null;

// ── Render ──

export function renderClipDetail(clip, file, allClips, fmtDuration) {
    const container = document.getElementById('dm_detail_panel');
    if (!container) return;

    _currentClip = clip;
    _currentFile = file;

    const duration = file.duration || 0;
    const filmstrip = file.filmstrip || [];
    const fname = file.filename || '?';
    const res = (file.width && file.height) ? `${file.width}x${file.height}` : '';
    const durText = fmtDuration ? fmtDuration(duration) : _fmtHMS(duration);

    container.innerHTML = `
        <div class="flex items-center gap-3 mb-3">
            <button onclick="dmBackToArrange()" class="text-xs bg-[#333] hover:bg-[#444] px-3 py-1.5 rounded text-white border border-[#555]">\u2190 返回排列</button>
            <h3 class="text-sm font-semibold text-[#c0c0c0]">${fname}</h3>
            <span class="text-xs text-gray-500">${res}${res ? ' | ' : ''}${durText}</span>
        </div>

        <!-- Preview area -->
        <div class="mb-4 flex justify-center">
            <div id="dm_preview_frame" class="rounded bg-[#111] flex items-center justify-center" style="max-height:300px;min-height:180px;min-width:320px;">
                ${filmstrip.length > 0
                    ? `<img id="dm_preview_img" src="${filmstrip[0]}" class="max-w-full rounded" style="max-height:300px;">`
                    : '<span class="text-gray-600 text-sm">No preview available</span>'}
            </div>
        </div>

        <!-- Filmstrip timeline -->
        <div class="mb-4">
            <div class="flex items-center gap-2 mb-1">
                <span id="dm_current_time" class="text-xs text-gray-400 font-mono w-16">${_fmtHMS(clip.in)}</span>
                <span class="text-xs text-gray-600">/ ${_fmtHMS(duration)}</span>
            </div>
            <div id="dm_timeline_track" class="relative h-12 bg-[#1a1a1a] rounded cursor-pointer overflow-hidden border border-[#333]">
                <div id="dm_filmstrip_bar" class="absolute inset-0 flex">${_buildFilmstripBar(filmstrip)}</div>
                <!-- In/Out region overlay -->
                <div id="dm_trim_region" class="absolute top-0 bottom-0 bg-blue-500/15 border-l-2 border-r-2 border-blue-500 pointer-events-none"
                    style="left:${(clip.in / duration * 100) || 0}%;right:${100 - (clip.out / duration * 100) || 0}%;"></div>
                <!-- Playhead -->
                <div id="dm_playhead" class="absolute top-0 bottom-0 w-0.5 bg-red-500 z-10 pointer-events-none" style="left:${(clip.in / duration * 100) || 0}%;"></div>
            </div>
        </div>

        <!-- In/Out range -->
        <div class="mb-4 grid grid-cols-3 gap-3 items-center">
            <div>
                <label class="block text-xs text-gray-500 mb-1">In</label>
                <input type="text" id="dm_detail_trim_in" value="${_fmtHMS(clip.in)}"
                    class="w-full bg-[#1e1e1e] border border-[#444] rounded px-2 py-1 text-xs font-mono text-center">
            </div>
            <div>
                <label class="block text-xs text-gray-500 mb-1">Out</label>
                <input type="text" id="dm_detail_trim_out" value="${_fmtHMS(clip.out)}"
                    class="w-full bg-[#1e1e1e] border border-[#444] rounded px-2 py-1 text-xs font-mono text-center">
            </div>
            <div>
                <label class="block text-xs text-gray-500 mb-1">有效時長</label>
                <div id="dm_detail_effective_dur" class="text-sm text-gray-300 font-mono text-center">${_fmtHMS(clip.out - clip.in)}</div>
            </div>
        </div>

        <!-- Color grading -->
        <div class="mb-4">
            <div class="flex items-center justify-between mb-2">
                <h4 class="text-xs font-semibold text-gray-400">色彩調整</h4>
                <button id="dm_detail_reset_color" class="text-xs text-blue-400 hover:text-blue-300">重置色彩</button>
            </div>
            <div class="space-y-2">
                ${_colorSlider('brightness', '亮度', clip.brightness, -1, 1, 0.05)}
                ${_colorSlider('contrast', '對比', clip.contrast, 0.5, 2, 0.05)}
                ${_colorSlider('saturation', '飽和', clip.saturation, 0, 3, 0.05)}
                ${_colorSlider('gamma', 'Gamma', clip.gamma, 0.1, 3, 0.05)}
                ${_colorSlider('colorTemp', '色溫', clip.colorTemp, -1, 1, 0.05)}
            </div>
        </div>

        <div class="flex justify-between">
            <button onclick="dmBackToArrange()" class="text-xs bg-[#333] hover:bg-[#444] px-3 py-1.5 rounded text-white border border-[#555]">\u2190 返回</button>
            <button id="dm_detail_delete_clip" class="text-xs bg-red-900/50 hover:bg-red-800 px-3 py-1.5 rounded text-red-300 border border-red-800">刪除此素材</button>
        </div>
    `;

    _bindTimelineEvents(clip, file, filmstrip, duration);
    _bindTrimInputEvents(clip, duration);
    _bindColorEvents(clip);
    _bindActionButtons(clip, allClips);
}

// ── Filmstrip bar ──

function _buildFilmstripBar(filmstrip) {
    if (!filmstrip || filmstrip.length === 0) return '';
    return filmstrip.map(src =>
        `<img src="${src}" class="h-full flex-1 object-cover" onerror="this.style.visibility='hidden'">`
    ).join('');
}

// ── Timeline events ──

function _bindTimelineEvents(clip, file, filmstrip, duration) {
    const track = document.getElementById('dm_timeline_track');
    if (!track || duration <= 0) return;

    const updatePlayhead = (e) => {
        const rect = track.getBoundingClientRect();
        const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
        const pct = x / rect.width;
        const time = pct * duration;

        const playhead = document.getElementById('dm_playhead');
        if (playhead) playhead.style.left = (pct * 100) + '%';

        const timeLabel = document.getElementById('dm_current_time');
        if (timeLabel) timeLabel.textContent = _fmtHMS(time);

        // Show nearest filmstrip frame
        if (filmstrip.length > 0) {
            const frameIdx = Math.min(Math.floor(pct * filmstrip.length), filmstrip.length - 1);
            const img = document.getElementById('dm_preview_img');
            if (img && filmstrip[frameIdx]) img.src = filmstrip[frameIdx];
        }
    };

    let dragging = false;
    track.addEventListener('mousedown', (e) => {
        dragging = true;
        updatePlayhead(e);
    });
    document.addEventListener('mousemove', (e) => {
        if (dragging) updatePlayhead(e);
    });
    document.addEventListener('mouseup', () => { dragging = false; });

    track.addEventListener('click', updatePlayhead);
}

// ── Trim input events ──

function _bindTrimInputEvents(clip, duration) {
    const inEl = document.getElementById('dm_detail_trim_in');
    const outEl = document.getElementById('dm_detail_trim_out');
    const durEl = document.getElementById('dm_detail_effective_dur');
    const regionEl = document.getElementById('dm_trim_region');

    if (!inEl || !outEl) return;

    const updateTrim = () => {
        let inVal = _parseHMS(inEl.value);
        let outVal = _parseHMS(outEl.value);
        inVal = Math.max(0, Math.min(inVal, duration));
        outVal = Math.max(inVal, Math.min(outVal, duration));

        clip.in = inVal;
        clip.out = outVal;

        if (durEl) durEl.textContent = _fmtHMS(outVal - inVal);
        if (regionEl && duration > 0) {
            regionEl.style.left = (inVal / duration * 100) + '%';
            regionEl.style.right = (100 - outVal / duration * 100) + '%';
        }
    };

    inEl.addEventListener('change', updateTrim);
    outEl.addEventListener('change', updateTrim);
}

// ── Color events ──

function _bindColorEvents(clip) {
    const sliders = document.querySelectorAll('#dm_detail_panel input[data-color-name]');
    sliders.forEach(slider => {
        slider.addEventListener('input', () => {
            const name = slider.dataset.colorName;
            const val = parseFloat(slider.value);
            const valEl = document.getElementById(`dm_detail_color_${name}_val`);
            if (valEl) valEl.textContent = val;

            // Update clip object
            if (name === 'colorTemp') clip.colorTemp = val;
            else clip[name] = val;
        });
    });

    const resetBtn = document.getElementById('dm_detail_reset_color');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            const defaults = { brightness: 0, contrast: 1, saturation: 1, gamma: 1, colorTemp: 0 };
            for (const [name, def] of Object.entries(defaults)) {
                const el = document.getElementById(`dm_detail_color_${name}`);
                const valEl = document.getElementById(`dm_detail_color_${name}_val`);
                if (el) { el.value = def; }
                if (valEl) { valEl.textContent = def; }
                if (name === 'colorTemp') clip.colorTemp = def;
                else clip[name] = def;
            }
        });
    }
}

// ── Action buttons ──

function _bindActionButtons(clip, allClips) {
    const deleteBtn = document.getElementById('dm_detail_delete_clip');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', () => {
            const idx = allClips.findIndex(c => c.id === clip.id);
            if (idx >= 0) allClips.splice(idx, 1);
            window.dmBackToArrange();
        });
    }
}
