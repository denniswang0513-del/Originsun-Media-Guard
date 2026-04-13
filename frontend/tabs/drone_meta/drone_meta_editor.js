/**
 * drone_meta_editor.js -- Inline editor panel (trim + color grading).
 * Consumer passes in the target container (e.g. concat modal's #cc_inline_edit).
 * Filmstrip is lazy-loaded from /api/v1/drone_meta/filmstrip on open.
 * Exports a teardown helper to clear timers/caches on close.
 */

import { secToHMS as _secToHMS, COLOR_DEFAULTS, applyClipFilter } from '../../js/shared/clip_utils.js';

function _fmtHMS(sec) {
    if (!sec || sec <= 0) return '00:00:00';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function _colorSliderHTML(idx, name, label, min, max, def, step) {
    return `
    <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500 w-20 flex-shrink-0">${label}</label>
        <input type="range" id="dm_color_${name}_${idx}" min="${min}" max="${max}" step="${step}" value="${def}"
            class="flex-1 h-1.5 accent-blue-500">
        <span id="dm_color_${name}_val_${idx}" class="text-xs text-gray-400 w-10 text-right">${def}</span>
    </div>`;
}

// ── State ──
let _currentFile = null;
let _currentIdx = -1;
let _colorDebounceTimer = null;
let _filmstripCache = {};  // path → filmstrip array
let _currentFilmstrip = [];  // live ref used by timeline drag handler

// ── Render ──

export function renderInlineEditor(container, file, idx, fmtDuration) {
    _currentFile = file;
    _currentIdx = idx;

    const duration = file.duration || 0;
    const fname = file.filename || '?';
    const res = (file.width && file.height) ? `${file.width}x${file.height}` : '';
    const durText = fmtDuration ? fmtDuration(duration) : _fmtHMS(duration);
    const thumb = file.thumbnail || '';

    container.innerHTML = `
        <div class="p-4">
            <!-- Header -->
            <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-3">
                    <h3 class="text-sm font-semibold text-[#c0c0c0]">\u2702 修剪/色彩 — ${fname}</h3>
                    <span class="text-xs text-gray-500">${res}${res ? ' | ' : ''}${durText}</span>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Left: Preview + Timeline -->
                <div>
                    <!-- Preview -->
                    <div id="dm_preview_frame" class="mb-3 rounded overflow-hidden">
                        ${thumb
                            ? `<img id="dm_preview_img" src="${thumb}" class="w-full h-auto block rounded">`
                            : '<div class="w-full aspect-video bg-[#111] flex items-center justify-center text-gray-600 text-sm">No preview</div>'}
                    </div>

                    <!-- Filmstrip timeline -->
                    <div class="mb-3">
                        <div class="flex items-center gap-2 mb-1">
                            <span id="dm_current_time" class="text-xs text-gray-400 font-mono w-16">${_fmtHMS(0)}</span>
                            <span class="text-xs text-gray-600">/ ${_fmtHMS(duration)}</span>
                        </div>
                        <div id="dm_timeline_track" class="relative h-12 bg-[#1a1a1a] rounded cursor-pointer overflow-hidden border border-[#333]">
                            <div id="dm_filmstrip_bar" class="absolute inset-0 flex">
                                <div class="flex items-center justify-center w-full text-xs text-gray-600">載入時間軸中...</div>
                            </div>
                            <div id="dm_trim_region" class="absolute top-0 bottom-0 bg-blue-500/15 border-l-2 border-r-2 border-blue-500 pointer-events-none"
                                style="left:0%;right:0%;"></div>
                            <div id="dm_playhead" class="absolute top-0 bottom-0 w-0.5 bg-red-500 z-10 pointer-events-none" style="left:0%;"></div>
                        </div>
                    </div>

                    <!-- In/Out -->
                    <div class="grid grid-cols-3 gap-3 items-center">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">In</label>
                            <input type="text" id="dm_trim_in_txt_${idx}" value="${_secToHMS(0)}"
                                class="w-full bg-[#2a2a2a] border border-[#444] rounded px-2 py-1 text-xs font-mono text-center">
                            <input type="hidden" id="dm_trim_in_${idx}" value="0">
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Out</label>
                            <input type="text" id="dm_trim_out_txt_${idx}" value="${_secToHMS(duration)}"
                                class="w-full bg-[#2a2a2a] border border-[#444] rounded px-2 py-1 text-xs font-mono text-center">
                            <input type="hidden" id="dm_trim_out_${idx}" value="${duration}">
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">有效時長</label>
                            <div id="dm_trim_dur_${idx}" class="text-sm text-gray-300 font-mono text-center">${_secToHMS(duration)}</div>
                        </div>
                    </div>
                </div>

                <!-- Right: Color grading (sliders + curve side-by-side in one row) -->
                <div class="bg-[#1e1e1e] border border-[#333] rounded-lg p-3">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="text-xs font-semibold text-gray-400">色彩調整 · 曲線</h4>
                        <div class="flex gap-3">
                            <button id="dm_curve_reset_${idx}" class="text-xs text-blue-400 hover:text-blue-300">重置曲線</button>
                            <button id="dm_detail_reset_color" class="text-xs text-blue-400 hover:text-blue-300">重置全部</button>
                        </div>
                    </div>
                    <div class="flex gap-4 items-start">
                        <!-- 8 sliders in 2 cols -->
                        <div class="flex-1 grid grid-cols-2 gap-x-4 gap-y-2 min-w-0">
                            ${_colorSliderHTML(idx, 'brightness', '亮度', -1, 1, 0, 0.05)}
                            ${_colorSliderHTML(idx, 'shadows', '陰影', -1, 1, 0, 0.05)}
                            ${_colorSliderHTML(idx, 'contrast', '對比度', 0.5, 2, 1, 0.05)}
                            ${_colorSliderHTML(idx, 'midtones', '中間調', -1, 1, 0, 0.05)}
                            ${_colorSliderHTML(idx, 'saturation', '飽和度', 0, 3, 1, 0.05)}
                            ${_colorSliderHTML(idx, 'highlights', '高光', -1, 1, 0, 0.05)}
                            ${_colorSliderHTML(idx, 'gamma', 'Gamma', 0.1, 3, 1, 0.05)}
                            ${_colorSliderHTML(idx, 'color_temp', '色溫', -1, 1, 0, 0.05)}
                        </div>
                        <!-- Curve canvas -->
                        <div class="flex-shrink-0">
                            <canvas id="dm_curve_canvas_${idx}" width="160" height="160"
                                class="bg-[#1a1a1a] border border-[#333] rounded cursor-crosshair block"></canvas>
                            <div class="text-[10px] text-gray-600 mt-1 leading-tight">拖曳控制點<br>點擊新增·雙擊刪除</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    _bindTrimInputEvents(idx, duration);
    _bindColorEvents(idx, container);
    _renderCurveEditor(idx);
    // Bind timeline drag immediately (uses _currentFilmstrip that fills in later)
    _currentFilmstrip = [];
    _bindTimelineEvents(duration);

    _loadFilmstrip(file, duration);
}

// ── Lazy filmstrip loading ──

async function _loadFilmstrip(file, duration) {
    const barEl = document.getElementById('dm_filmstrip_bar');
    if (!barEl) return;

    // Check cache first
    if (_filmstripCache[file.path]) {
        _renderFilmstripBar(barEl, _filmstripCache[file.path], duration);
        return;
    }

    try {
        const params = new URLSearchParams({ path: file.path, frames: 10 });
        const res = await fetch(`/api/v1/drone_meta/filmstrip?${params}`);
        if (!res.ok) throw new Error('Filmstrip fetch failed');
        const data = await res.json();
        const filmstrip = data.filmstrip || [];
        _filmstripCache[file.path] = filmstrip;

        // Only render if this file is still the active one
        if (_currentFile && _currentFile.path === file.path) {
            _renderFilmstripBar(barEl, filmstrip, duration);
        }
    } catch (e) {
        if (barEl) barEl.innerHTML = '<div class="flex items-center justify-center w-full text-xs text-gray-600">時間軸載入失敗</div>';
        console.warn('filmstrip load error:', e);
    }
}

function _renderFilmstripBar(barEl, filmstrip, _duration) {
    _currentFilmstrip = filmstrip || [];
    if (!_currentFilmstrip.length) {
        barEl.innerHTML = '<div class="flex items-center justify-center w-full text-xs text-gray-600">無時間軸資料</div>';
        return;
    }
    barEl.innerHTML = _currentFilmstrip.map(src =>
        `<img src="${src}" class="h-full flex-1 object-cover" onerror="this.style.visibility='hidden'">`
    ).join('');

    // Ensure preview img exists (inline editor may have rendered a "No preview" fallback)
    const frame = document.getElementById('dm_preview_frame');
    if (frame && _currentFilmstrip[0]) {
        let img = document.getElementById('dm_preview_img');
        if (!img) {
            frame.innerHTML = `<img id="dm_preview_img" src="${_currentFilmstrip[0]}" class="w-full h-auto block rounded">`;
        } else {
            img.src = _currentFilmstrip[0];
        }
    }
}

// ── Timeline events ──

function _bindTimelineEvents(duration) {
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

        const strip = _currentFilmstrip;
        if (strip.length > 0) {
            const frameIdx = Math.min(Math.floor(pct * strip.length), strip.length - 1);
            const img = document.getElementById('dm_preview_img');
            if (img && strip[frameIdx]) img.src = strip[frameIdx];
        }
    };

    // Hover scrubs: red line and preview follow cursor directly
    track.style.cursor = 'crosshair';
    track.addEventListener('mousemove', updatePlayhead);
}

// ── Trim input events ──

function _bindTrimInputEvents(idx, duration) {
    const inTxt = document.getElementById(`dm_trim_in_txt_${idx}`);
    const outTxt = document.getElementById(`dm_trim_out_txt_${idx}`);
    const inHidden = document.getElementById(`dm_trim_in_${idx}`);
    const outHidden = document.getElementById(`dm_trim_out_${idx}`);
    const durEl = document.getElementById(`dm_trim_dur_${idx}`);
    const regionEl = document.getElementById('dm_trim_region');

    if (!inTxt || !outTxt) return;

    const update = () => {
        const parts = inTxt.value.trim().split(':').map(Number);
        let inVal = 0;
        if (parts.length === 3) inVal = parts[0] * 3600 + parts[1] * 60 + parts[2];
        else if (parts.length === 2) inVal = parts[0] * 60 + parts[1];
        else inVal = parts[0] || 0;

        const oParts = outTxt.value.trim().split(':').map(Number);
        let outVal = duration;
        if (oParts.length === 3) outVal = oParts[0] * 3600 + oParts[1] * 60 + oParts[2];
        else if (oParts.length === 2) outVal = oParts[0] * 60 + oParts[1];
        else outVal = oParts[0] || duration;

        inVal = Math.max(0, Math.min(inVal, duration));
        outVal = Math.max(inVal, Math.min(outVal, duration));

        if (inHidden) inHidden.value = inVal;
        if (outHidden) outHidden.value = outVal;
        if (durEl) durEl.textContent = _secToHMS(outVal - inVal);
        if (regionEl && duration > 0) {
            regionEl.style.left = (inVal / duration * 100) + '%';
            regionEl.style.right = (100 - outVal / duration * 100) + '%';
        }
    };

    inTxt.addEventListener('change', update);
    outTxt.addEventListener('change', update);
}

// ── Color events (instant preview via CSS + SVG filter, no server round-trip) ──

const _SVG_FILTER_ID = 'dm-color-filter';

function _ensureSvgFilter() {
    if (document.getElementById('dm-svg-defs')) return;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.id = 'dm-svg-defs';
    svg.setAttribute('width', '0');
    svg.setAttribute('height', '0');
    svg.style.cssText = 'position:absolute;width:0;height:0;pointer-events:none';
    svg.innerHTML = `<defs><filter id="${_SVG_FILTER_ID}" color-interpolation-filters="sRGB">
        <feComponentTransfer id="dm-svg-gamma">
            <feFuncR type="gamma" amplitude="1" exponent="1" offset="0"/>
            <feFuncG type="gamma" amplitude="1" exponent="1" offset="0"/>
            <feFuncB type="gamma" amplitude="1" exponent="1" offset="0"/>
        </feComponentTransfer>
        <feColorMatrix id="dm-svg-temp" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0"/>
        <feComponentTransfer id="dm-svg-curve">
            <feFuncR type="table" tableValues="0 1"/>
            <feFuncG type="table" tableValues="0 1"/>
            <feFuncB type="table" tableValues="0 1"/>
        </feComponentTransfer>
    </filter></defs>`;
    document.body.appendChild(svg);
}

// Build tableValues string from tonal zone sliders (shadows/midtones/highlights)
// and optional curve points. Returns a space-separated list of 17 values 0..1.
function _isIdentityCurve(pts) {
    if (!Array.isArray(pts) || pts.length !== 2) return false;
    const [a, b] = pts;
    return a[0] === 0 && a[1] === 0 && b[0] === 1 && b[1] === 1;
}

function _buildCurveTable(shadows, mids, highs, curvePoints) {
    const N = 17;
    // If user-drawn curve is present (and not identity), interpolate that
    if (Array.isArray(curvePoints) && curvePoints.length >= 2 && !_isIdentityCurve(curvePoints)) {
        const sorted = [...curvePoints].sort((a, b) => a[0] - b[0]);
        const vals = [];
        for (let i = 0; i < N; i++) {
            const x = i / (N - 1);
            let y = x;
            for (let j = 0; j < sorted.length - 1; j++) {
                const [x0, y0] = sorted[j], [x1, y1] = sorted[j + 1];
                if (x >= x0 && x <= x1) {
                    y = x1 === x0 ? y0 : y0 + (y1 - y0) * (x - x0) / (x1 - x0);
                    break;
                }
            }
            vals.push(Math.max(0, Math.min(1, y)).toFixed(4));
        }
        return vals.join(' ');
    }
    // Otherwise derive from shadows/mids/highs: each pushes anchor points
    // shadows: x=0.25 anchor, +s raises; mids: x=0.5; highlights: x=0.75, -h lowers
    const anchors = [
        [0, 0],
        [0.25, Math.max(0, Math.min(1, 0.25 + (shadows || 0) * 0.25))],
        [0.5,  Math.max(0, Math.min(1, 0.5  + (mids    || 0) * 0.25))],
        [0.75, Math.max(0, Math.min(1, 0.75 + (highs   || 0) * 0.25))],
        [1, 1],
    ];
    const vals = [];
    for (let i = 0; i < N; i++) {
        const x = i / (N - 1);
        let y = x;
        for (let j = 0; j < anchors.length - 1; j++) {
            const [x0, y0] = anchors[j], [x1, y1] = anchors[j + 1];
            if (x >= x0 && x <= x1) {
                y = y0 + (y1 - y0) * (x - x0) / (x1 - x0);
                break;
            }
        }
        vals.push(Math.max(0, Math.min(1, y)).toFixed(4));
    }
    return vals.join(' ');
}

function _applyLivePreview(idx) {
    const mainImg = document.getElementById('dm_preview_img');
    if (!mainImg) return;
    const get = (n, def) => parseFloat(document.getElementById(`dm_color_${n}_${idx}`)?.value ?? def) || def;
    const brightness = get('brightness', 0);      // -1..1 (ffmpeg eq brightness)
    const contrast = get('contrast', 1);          // 0.5..2
    const saturation = get('saturation', 1);      // 0..3
    const gamma = get('gamma', 1);                // 0.1..3
    const colorTemp = get('color_temp', 0);       // -1..1 (positive = warm)
    const shadows = get('shadows', 0);            // -1..1
    const mids = get('midtones', 0);
    const highs = get('highlights', 0);

    // Map ffmpeg eq brightness (-1..1, additive) to CSS brightness (multiplicative, 0..2)
    const cssBrightness = 1 + brightness;
    mainImg.style.filter = `brightness(${cssBrightness}) contrast(${contrast}) saturate(${saturation}) url(#${_SVG_FILTER_ID})`;

    // Update SVG filter: gamma, color_temp, curve
    const gammaEl = document.getElementById('dm-svg-gamma');
    if (gammaEl) {
        // CSS gamma correction: output = input^(1/gamma). SVG feFuncR type=gamma with exponent=1/gamma
        const exp = (1 / gamma).toFixed(4);
        gammaEl.querySelectorAll('feFuncR, feFuncG, feFuncB').forEach(el => el.setAttribute('exponent', exp));
    }
    const tempEl = document.getElementById('dm-svg-temp');
    if (tempEl) {
        const t = colorTemp;
        const rScale = (1 + 0.3 * t).toFixed(4);
        const bScale = (1 - 0.3 * t).toFixed(4);
        tempEl.setAttribute('values', `${rScale} 0 0 0 0  0 1 0 0 0  0 0 ${bScale} 0 0  0 0 0 1 0`);
    }
    const curveEl = document.getElementById('dm-svg-curve');
    if (curveEl) {
        const clip = _currentFile || {};
        const table = _buildCurveTable(shadows, mids, highs, clip.curve_points);
        curveEl.querySelectorAll('feFuncR, feFuncG, feFuncB').forEach(el => el.setAttribute('tableValues', table));
    }
}

// Map slider DOM id back to the field key on the clip object.
// "dm_color_brightness_3" -> "brightness"
function _sliderFieldKey(sliderId) {
    const m = sliderId.match(/^dm_color_(.+)_\d+$/);
    return m ? m[1] : null;
}

function _bindColorEvents(idx, container) {
    if (!container) container = document;
    _ensureSvgFilter();
    const sliders = container.querySelectorAll('input[type="range"]');
    sliders.forEach(slider => {
        slider.addEventListener('input', () => {
            const valId = slider.id.replace(/_(\d+)$/, '_val_$1');
            const valEl = document.getElementById(valId);
            if (valEl) valEl.textContent = slider.value;
            // Persist slider value into the clip object so "套用到全部" sees
            // current values without needing a separate DOM-read step, and
            // so the clip's own card thumbnail reflects live changes too.
            const key = _sliderFieldKey(slider.id);
            if (key && _currentFile) _currentFile[key] = parseFloat(slider.value);
            _applyLivePreview(idx);
            // Live-update every visible card of this clip via the shared filter utility.
            if (_currentFile) {
                const allCards = document.querySelectorAll(`[data-idx="${idx}"].clip-card, .dm-file-card[data-idx="${idx}"]`);
                allCards.forEach(card => {
                    const thumb = card.querySelector('.clip-thumb');
                    if (thumb) {
                        const fid = thumb.dataset._liveFilterId || `clip-filter-live-${idx}`;
                        thumb.dataset._liveFilterId = fid;
                        applyClipFilter(thumb, _currentFile, fid);
                    }
                });
            }
        });
    });

    const resetBtn = document.getElementById('dm_detail_reset_color');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            for (const [name, def] of Object.entries(COLOR_DEFAULTS)) {
                const el = document.getElementById(`dm_color_${name}_${idx}`);
                const valEl = document.getElementById(`dm_color_${name}_val_${idx}`);
                if (el) el.value = def;
                if (valEl) valEl.textContent = def;
            }
            for (const name of ['shadows', 'midtones', 'highlights']) {
                const el = document.getElementById(`dm_color_${name}_${idx}`);
                const valEl = document.getElementById(`dm_color_${name}_val_${idx}`);
                if (el) el.value = 0;
                if (valEl) valEl.textContent = 0;
            }
            if (_currentFile) _currentFile.curve_points = null;
            _renderCurveEditor(idx);
            _applyLivePreview(idx);
        });
    }

    // Initial apply so any non-default values on load render immediately
    _applyLivePreview(idx);
}

// ── Curve editor (draggable Canvas) ──

function _renderCurveEditor(idx) {
    const canvas = document.getElementById(`dm_curve_canvas_${idx}`);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const PAD = 8;

    // Points: [[x, y], ...] in 0..1 coords (y inverted when drawing: 0=bottom)
    if (!_currentFile.curve_points || !Array.isArray(_currentFile.curve_points) || _currentFile.curve_points.length < 2) {
        _currentFile.curve_points = [[0, 0], [1, 1]];
    }

    const toCanvas = (p) => [PAD + p[0] * (W - 2 * PAD), H - PAD - p[1] * (H - 2 * PAD)];
    const toNorm = (cx, cy) => [
        Math.max(0, Math.min(1, (cx - PAD) / (W - 2 * PAD))),
        Math.max(0, Math.min(1, 1 - (cy - PAD) / (H - 2 * PAD))),
    ];

    const draw = () => {
        ctx.clearRect(0, 0, W, H);
        // Grid
        ctx.strokeStyle = '#2a2a2a';
        ctx.lineWidth = 1;
        for (let i = 1; i < 4; i++) {
            const x = PAD + i * (W - 2 * PAD) / 4;
            const y = PAD + i * (H - 2 * PAD) / 4;
            ctx.beginPath(); ctx.moveTo(x, PAD); ctx.lineTo(x, H - PAD); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke();
        }
        // Diagonal reference
        ctx.strokeStyle = '#333';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(PAD, H - PAD); ctx.lineTo(W - PAD, PAD);
        ctx.stroke();
        ctx.setLineDash([]);

        // Curve line (linear between points)
        const pts = [..._currentFile.curve_points].sort((a, b) => a[0] - b[0]);
        ctx.strokeStyle = '#60a5fa';
        ctx.lineWidth = 2;
        ctx.beginPath();
        pts.forEach((p, i) => {
            const [cx, cy] = toCanvas(p);
            if (i === 0) ctx.moveTo(cx, cy); else ctx.lineTo(cx, cy);
        });
        ctx.stroke();

        // Control points
        pts.forEach(p => {
            const [cx, cy] = toCanvas(p);
            ctx.fillStyle = '#60a5fa';
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.arc(cx, cy, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        });
    };

    const hitTest = (cx, cy) => {
        const pts = _currentFile.curve_points;
        for (let i = 0; i < pts.length; i++) {
            const [pcx, pcy] = toCanvas(pts[i]);
            if (Math.hypot(pcx - cx, pcy - cy) <= 8) return i;
        }
        return -1;
    };

    let dragIdx = -1;
    const getXY = (e) => {
        const rect = canvas.getBoundingClientRect();
        return [
            (e.clientX - rect.left) * (W / rect.width),
            (e.clientY - rect.top) * (H / rect.height),
        ];
    };

    canvas.onmousedown = (e) => {
        const [cx, cy] = getXY(e);
        const hit = hitTest(cx, cy);
        if (hit >= 0) {
            dragIdx = hit;
        } else {
            // Add new point
            const [nx, ny] = toNorm(cx, cy);
            _currentFile.curve_points.push([nx, ny]);
            _currentFile.curve_points.sort((a, b) => a[0] - b[0]);
            dragIdx = _currentFile.curve_points.findIndex(p => p[0] === nx && p[1] === ny);
            draw();
            _applyLivePreview(idx);
        }
    };
    canvas.onmousemove = (e) => {
        if (dragIdx < 0) return;
        const [cx, cy] = getXY(e);
        const [nx, ny] = toNorm(cx, cy);
        const pts = _currentFile.curve_points;
        // Lock endpoints' x axis to 0 / 1
        if (dragIdx === 0) pts[0] = [0, ny];
        else if (dragIdx === pts.length - 1) pts[pts.length - 1] = [1, ny];
        else pts[dragIdx] = [nx, ny];
        draw();
        _applyLivePreview(idx);
    };
    const endDrag = () => { dragIdx = -1; };
    canvas.onmouseup = endDrag;
    canvas.onmouseleave = endDrag;
    canvas.ondblclick = (e) => {
        const [cx, cy] = getXY(e);
        const hit = hitTest(cx, cy);
        const pts = _currentFile.curve_points;
        if (hit > 0 && hit < pts.length - 1) {
            pts.splice(hit, 1);
            draw();
            _applyLivePreview(idx);
        }
    };

    const resetBtn = document.getElementById(`dm_curve_reset_${idx}`);
    if (resetBtn) {
        resetBtn.onclick = () => {
            _currentFile.curve_points = [[0, 0], [1, 1]];
            draw();
            _applyLivePreview(idx);
        };
    }

    draw();
}

// Clear pending color-preview debounce timer + blob URL so closing the editor
// doesn't fire stale fetches or leak object URLs.
export function teardownInlineEditor() {
    if (_colorDebounceTimer) { clearTimeout(_colorDebounceTimer); _colorDebounceTimer = null; }
    const img = document.getElementById('dm_preview_img');
    if (img && img._prevUrl) { URL.revokeObjectURL(img._prevUrl); img._prevUrl = null; }
    _currentFile = null;
    _currentIdx = -1;
}
