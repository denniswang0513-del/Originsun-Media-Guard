/**
 * clip_utils.js — Shared helpers for clip-based editors (drone_meta, concat).
 */

export const COLOR_FIELDS = ['brightness', 'contrast', 'saturation', 'gamma', 'color_temp', 'tint'];

export const COLOR_DEFAULTS = {
    brightness: 0,
    contrast: 1,
    saturation: 1,
    gamma: 1,
    color_temp: 0,
    tint: 0,  // -1..1, negative = magenta, positive = green
};

export function fmtDuration(sec) {
    if (!sec || sec <= 0) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

export function secToHMS(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = (sec % 60).toFixed(1);
    return h > 0
        ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(4, '0')}`
        : `${m}:${String(s).padStart(4, '0')}`;
}

export function fmtSize(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
}

export function hasColorAdjustment(clip) {
    return COLOR_FIELDS.some(f => clip[f] !== COLOR_DEFAULTS[f]);
}

// ── Per-clip live preview filter (CSS + shared SVG defs) ──
// Applies brightness/contrast/saturation via CSS filter, and
// gamma / color_temp / curves (shadows/mids/highs or user curve_points)
// via a per-clip SVG filter injected into a shared <svg> defs block.

const _SVG_DEFS_ID = 'clip-filter-defs';

function _ensureSvgDefs() {
    let defs = document.getElementById(_SVG_DEFS_ID);
    if (defs) return defs;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '0');
    svg.setAttribute('height', '0');
    svg.style.cssText = 'position:absolute;width:0;height:0;pointer-events:none';
    defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.id = _SVG_DEFS_ID;
    svg.appendChild(defs);
    document.body.appendChild(svg);
    return defs;
}

function _isIdentityCurve(pts) {
    if (!Array.isArray(pts) || pts.length !== 2) return false;
    const [a, b] = pts;
    return a[0] === 0 && a[1] === 0 && b[0] === 1 && b[1] === 1;
}

function _buildCurveTable(shadows, mids, highs, curvePoints) {
    const N = 17;
    const useCurve = Array.isArray(curvePoints) && curvePoints.length >= 2 && !_isIdentityCurve(curvePoints);
    const anchors = useCurve
        ? [...curvePoints].sort((a, b) => a[0] - b[0])
        : [
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
                y = x1 === x0 ? y0 : y0 + (y1 - y0) * (x - x0) / (x1 - x0);
                break;
            }
        }
        vals.push(Math.max(0, Math.min(1, y)).toFixed(4));
    }
    return vals.join(' ');
}

// Apply a live color preview to an <img> element based on clip's settings.
// filterId must be unique per displayed clip (caller supplies a stable string).
export function applyClipFilter(imgEl, clip, filterId) {
    if (!imgEl) return;
    const b = parseFloat(clip.brightness) || 0;
    const c = parseFloat(clip.contrast)   || 1;
    const s = parseFloat(clip.saturation) || 1;
    const g = parseFloat(clip.gamma)      || 1;
    const t = parseFloat(clip.color_temp) || 0;
    const tn = parseFloat(clip.tint)      || 0;
    const sh = parseFloat(clip.shadows)    || 0;
    const mi = parseFloat(clip.midtones)   || 0;
    const hi = parseFloat(clip.highlights) || 0;
    const curve = clip.curve_points;

    const defs = _ensureSvgDefs();
    let filter = document.getElementById(filterId);
    if (!filter) {
        filter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
        filter.id = filterId;
        filter.setAttribute('color-interpolation-filters', 'sRGB');
        filter.innerHTML = `
            <feComponentTransfer data-role="brightness">
                <feFuncR type="linear" slope="1" intercept="0"/>
                <feFuncG type="linear" slope="1" intercept="0"/>
                <feFuncB type="linear" slope="1" intercept="0"/>
            </feComponentTransfer>
            <feComponentTransfer data-role="gamma">
                <feFuncR type="gamma" amplitude="1" exponent="1" offset="0"/>
                <feFuncG type="gamma" amplitude="1" exponent="1" offset="0"/>
                <feFuncB type="gamma" amplitude="1" exponent="1" offset="0"/>
            </feComponentTransfer>
            <feColorMatrix data-role="temp" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0"/>
            <feComponentTransfer data-role="curve">
                <feFuncR type="table" tableValues="0 1"/>
                <feFuncG type="table" tableValues="0 1"/>
                <feFuncB type="table" tableValues="0 1"/>
            </feComponentTransfer>`;
        defs.appendChild(filter);
    }
    filter.querySelector('[data-role="brightness"]').querySelectorAll('feFuncR,feFuncG,feFuncB')
        .forEach(el => el.setAttribute('intercept', b.toFixed(4)));
    const exp = (1 / g).toFixed(4);
    filter.querySelector('[data-role="gamma"]').querySelectorAll('feFuncR,feFuncG,feFuncB')
        .forEach(el => el.setAttribute('exponent', exp));
    // Temp shifts R/B (blue↔yellow); tint shifts G channel (magenta↔green).
    const rScale = (1 + 0.3 * t).toFixed(4);
    const bScale = (1 - 0.3 * t).toFixed(4);
    const gShift = (0.15 * tn).toFixed(4);
    filter.querySelector('[data-role="temp"]')
        .setAttribute('values', `${rScale} 0 0 0 0  0 1 0 0 ${gShift}  0 0 ${bScale} 0 0  0 0 0 1 0`);
    const table = _buildCurveTable(sh, mi, hi, curve);
    filter.querySelector('[data-role="curve"]').querySelectorAll('feFuncR,feFuncG,feFuncB')
        .forEach(el => el.setAttribute('tableValues', table));

    // Brightness is done inside the SVG filter (additive, matches ffmpeg
    // `eq=brightness`); CSS brightness() is multiplicative and would diverge.
    // Chrome caches SVG url(#...) filter output aggressively when only the
    // filter's inner attributes change (gamma exponent, curve tableValues,
    // etc). Toggling style.filter + forced reflow EVERY call defeats the
    // cache — the `style.filter === next` check I had before was wrong
    // because style string stays identical when only filter internals changed.
    imgEl.style.filter = 'none';
    void imgEl.offsetHeight;
    imgEl.style.filter = `contrast(${c}) saturate(${s}) url(#${filterId})`;
}

export function hasTrim(clip) {
    const inOk = (clip.trim_in || 0) > 0;
    const outOk = (clip.trim_out || 0) > 0 && clip.duration && clip.trim_out < clip.duration;
    return inOk || outOk;
}

// Drone metadata creation_time is ISO 8601 UTC (e.g. "2026-03-21T10:22:21Z").
// Convert to local YYYY-MM-DD / HH:MM:SS suitable for <input type="date|time">.
export function parseCreationTimeLocal(creationTime) {
    if (!creationTime) return { date: '', time: '' };
    const d = new Date(creationTime);
    if (isNaN(d.getTime())) return { date: '', time: '' };
    const pad = n => String(n).padStart(2, '0');
    return {
        date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
        time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
    };
}
