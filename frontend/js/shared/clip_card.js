/**
 * clip_card.js — Shared clip card rendering + drag-reorder.
 *
 * Used by both drone_meta tab (inline grid) and concat advanced-edit modal.
 * Consumers supply an options bag to customize accent color, action button,
 * and which metadata rows to render. Event handlers are attached via
 * addEventListener after innerHTML is set — consumers pass closures, not
 * window function name strings.
 */

import { fmtDuration, fmtSize, hasColorAdjustment, hasTrim, parseCreationTimeLocal, applyClipFilter } from './clip_utils.js';

/**
 * Build a clip card's inner HTML (without the outer wrapping div).
 * Handlers are NOT in the HTML; bind them via createClipCard or manually.
 */
export function buildClipCardHTML(clip, idx, opts = {}) {
    const {
        accentColor = 'blue',
        actionLabel = '\u2702 修剪/色彩',
        showMeta = true,
        showDji = true,
        showDateTime = true,
        showBadges = false,
        showAction = true,
        showRefresh = false,
    } = opts;

    const accent = accentColor === 'purple' ? 'text-purple-500' : 'text-blue-500';
    const hasMeta = clip.width && clip.height;
    const dur = clip.duration ? fmtDuration(clip.duration) : '';
    const metaLine = hasMeta
        ? `${clip.width}x${clip.height} | ${clip.codec || ''}${dur ? ' | ' + dur : ''} | ${fmtSize(clip.size)}`
        : (dur ? dur + ' | ' : '') + fmtSize(clip.size);
    const effDur = hasTrim(clip) ? fmtDuration((clip.trim_out > 0 ? clip.trim_out : clip.duration) - (clip.trim_in || 0)) : dur;

    const djiBlock = (showDji && clip.is_dji && clip.dji_gps && clip.dji_camera)
        ? `<div class="text-[10px] text-blue-400 mt-0.5">\u{1F4CD} ${clip.dji_gps.lat.toFixed(4)}°N, ${clip.dji_gps.lon.toFixed(4)}°E | ${clip.dji_gps.alt.toFixed(0)}m</div>
           <div class="text-[10px] text-blue-400">ISO:${clip.dji_camera.iso} | f/${clip.dji_camera.fnum} | 1/${clip.dji_camera.shutter}</div>`
        : '';

    const { date: dateVal, time: timeVal } = parseCreationTimeLocal(clip.creation_time);
    const dateTimeBlock = showDateTime
        ? `<div class="flex items-center gap-1 mt-1.5">
            <input type="date" class="clip-date bg-[#1e1e1e] border border-[#444] rounded px-1 py-0.5 text-[10px] flex-1" value="${dateVal}">
            <input type="time" class="clip-time bg-[#1e1e1e] border border-[#444] rounded px-1 py-0.5 text-[10px] flex-1" step="1" value="${timeVal}">
          </div>`
        : '';

    const badges = [];
    if (showBadges) {
        if (hasTrim(clip)) badges.push('<span class="text-[9px] bg-yellow-700/70 text-white px-1 rounded">\u2702</span>');
        if (hasColorAdjustment(clip)) badges.push('<span class="text-[9px] bg-purple-700/70 text-white px-1 rounded">\u{1F3A8}</span>');
    }
    const badgeBlock = badges.length ? `<div class="absolute bottom-1 right-1 flex gap-1">${badges.join('')}</div>` : '';

    return `
        <div class="relative">
            <div class="clip-thumb-wrap w-full aspect-video bg-[#1a1a1a] flex items-center justify-center overflow-hidden">
                ${clip.thumbnail
                    ? `<img src="${clip.thumbnail}" alt="" class="clip-thumb w-full h-full object-cover">`
                    : `<span class="text-gray-600 text-2xl">\u{1F3AC}</span>`}
            </div>
            <div class="absolute top-1 left-1 flex items-center gap-1">
                <input type="checkbox" ${clip.selected !== false ? 'checked' : ''}
                    class="clip-check form-checkbox ${accent} bg-[#1e1e1e] border-[#444] rounded">
                <span class="clip-idx-badge text-[10px] text-white bg-black/70 px-1 rounded">${idx + 1}</span>
            </div>
            ${effDur ? `<span class="absolute top-1 right-1 text-[10px] text-white bg-black/70 px-1 rounded clip-dur-badge">${effDur}</span>` : ''}
            ${badgeBlock}
        </div>
        <div class="p-2">
            <div class="flex items-center gap-1">
                <div class="text-xs text-gray-200 truncate font-medium clip-filename flex-1">${clip.filename || ''}</div>
                ${showRefresh ? `<button class="clip-refresh text-gray-500 hover:text-blue-400 flex-shrink-0 text-sm leading-none" title="重新讀取資料">\u21bb</button>` : ''}
            </div>
            ${showMeta ? `<div class="clip-meta text-[10px] text-gray-500 mt-0.5">${metaLine}</div>` : ''}
            <div class="clip-dji-info">${djiBlock}</div>
            ${dateTimeBlock}
            ${showAction ? `<button class="clip-action w-full mt-2 text-[11px] bg-[#333] hover:bg-[#444] px-2 py-1 rounded border border-[#555] text-gray-300 transition-colors">
                ${actionLabel}
            </button>` : ''}
        </div>`;
}

/**
 * Create a fully-wired clip card (HTML + events). Reads dataset.idx dynamically
 * at event time so handlers remain correct after DOM reorder.
 *
 * @param {object} clip - clip data
 * @param {number} idx - initial index (stored on dataset.idx)
 * @param {object} opts - options passed to buildClipCardHTML + callbacks:
 *   opts.cardClass   - extra classes on outer div
 *   opts.onToggle(idx, checked)
 *   opts.onAction(idx)
 *   opts.onReorder(fromIdx, toIdx)
 *   opts.onDateTimeChange(idx)  // fires when date/time inputs change
 */
export function createClipCard(clip, idx, opts = {}) {
    const card = document.createElement('div');
    card.className = (opts.cardClass || 'clip-card bg-[#252525] rounded-lg border border-[#3a3a3a] overflow-hidden cursor-grab');
    card.draggable = true;
    card.dataset.idx = idx;
    card.innerHTML = buildClipCardHTML(clip, idx, opts);

    // Apply live color-preview filter to the card thumbnail so color edits
    // (and "套用到全部") reflect visually without needing to re-encode.
    // NOTE: applyClipFilter's internal reflow trick (void offsetHeight) only
    // works on attached elements. At this point the card is still detached,
    // so we also queue a post-attach re-apply via requestAnimationFrame to
    // guarantee Chrome picks up the latest filter attrs instead of cached.
    const thumbImg = card.querySelector('.clip-thumb');
    if (thumbImg) {
        const key = clip.path
            ? btoa(unescape(encodeURIComponent(clip.path))).replace(/[^a-z0-9]/gi, '').slice(0, 12)
            : String(idx);
        const filterId = `clip-filter-${opts.filterPrefix || 'c'}-${key}`;
        applyClipFilter(thumbImg, clip, filterId);
        requestAnimationFrame(() => {
            if (thumbImg.isConnected) applyClipFilter(thumbImg, clip, filterId);
        });
    }

    const getIdx = () => parseInt(card.dataset.idx);

    const cb = card.querySelector('.clip-check');
    if (cb) {
        cb.addEventListener('click', (e) => e.stopPropagation());
        if (opts.onToggle) cb.addEventListener('change', (e) => opts.onToggle(getIdx(), e.target.checked));
    }
    const btn = card.querySelector('.clip-action');
    if (btn && opts.onAction) {
        btn.addEventListener('click', (e) => { e.stopPropagation(); opts.onAction(getIdx()); });
    }
    const refreshBtn = card.querySelector('.clip-refresh');
    if (refreshBtn && opts.onRefresh) {
        refreshBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            refreshBtn.classList.add('opacity-50');
            refreshBtn.style.animation = 'spin 1s linear infinite';
            Promise.resolve(opts.onRefresh(getIdx())).finally(() => {
                refreshBtn.classList.remove('opacity-50');
                refreshBtn.style.animation = '';
            });
        });
    }
    card.querySelectorAll('.clip-date, .clip-time').forEach((inp) => {
        inp.addEventListener('click', (e) => e.stopPropagation());
        if (opts.onDateTimeChange) inp.addEventListener('change', () => opts.onDateTimeChange(getIdx()));
    });
    if (opts.onReorder) bindClipDrag(card, opts.onReorder);
    return card;
}

/**
 * Bind drag-reorder handlers on a card. During dragover, the dragged card is
 * physically moved in the DOM for live preview (other cards shift aside). On
 * drop, the data model is synced via onReorder(origIdx, newIdx). If the drag
 * is cancelled (no drop), the DOM is restored to its original position.
 */
let _dragCard = null;
let _dragOrigIdx = -1;
let _dragOrigNext = null;  // original nextSibling for restore on cancel
let _dropAccepted = false;

// Only updates visible position badges. dataset.idx stays immutable as the
// consumer's identity reference (e.g. position in _clips / _dmFiles). The
// consumer resyncs its data model via onReorder using DOM order of dataset.idx.
function _renumberSiblings(grid) {
    for (let i = 0; i < grid.children.length; i++) {
        const badge = grid.children[i].querySelector('.clip-idx-badge');
        if (badge) badge.textContent = String(i + 1);
    }
}

export function bindClipDrag(card, onReorder) {
    card.addEventListener('dragstart', (e) => {
        _dragCard = card;
        _dragOrigIdx = parseInt(card.dataset.idx);
        _dragOrigNext = card.nextSibling;
        _dropAccepted = false;
        e.dataTransfer.effectAllowed = 'move';
        card.classList.add('opacity-40');
    });
    card.addEventListener('dragend', () => {
        card.classList.remove('opacity-40');
        const grid = card.parentElement;
        if (!_dropAccepted && grid && _dragCard === card) {
            grid.insertBefore(card, _dragOrigNext);
            _renumberSiblings(grid);
        } else if (_dropAccepted && grid && onReorder) {
            const newIdx = Array.prototype.indexOf.call(grid.children, card);
            if (newIdx !== _dragOrigIdx && newIdx >= 0) {
                onReorder(_dragOrigIdx, newIdx);
            }
        }
        // Clear any lingering FLIP transitions
        if (grid) {
            for (const s of grid.children) {
                s.style.transition = '';
                s.style.transform = '';
            }
        }
        _dragCard = null;
        _dragOrigIdx = -1;
        _dragOrigNext = null;
        _dropAccepted = false;
    });
    card.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (!_dragCard || _dragCard === card) return;
        const grid = card.parentElement;
        if (!grid || grid !== _dragCard.parentElement) return;
        const rect = card.getBoundingClientRect();
        const after = (e.clientX - rect.left) > rect.width / 2;
        const ref = after ? card.nextSibling : card;
        if (_dragCard === ref || _dragCard.nextSibling === ref) return;

        // FLIP animation: record positions, reorder, then slide from old→new
        const siblings = Array.from(grid.children).filter(c => c !== _dragCard);
        const firstRects = new Map();
        for (const s of siblings) firstRects.set(s, s.getBoundingClientRect());

        grid.insertBefore(_dragCard, ref);
        _renumberSiblings(grid);

        for (const s of siblings) {
            const first = firstRects.get(s);
            const last = s.getBoundingClientRect();
            const dx = first.left - last.left;
            const dy = first.top - last.top;
            if (dx === 0 && dy === 0) continue;
            s.style.transition = 'none';
            s.style.transform = `translate(${dx}px, ${dy}px)`;
            // Force reflow so the browser commits the pre-animation position
            void s.offsetWidth;
            s.style.transition = 'transform 180ms cubic-bezier(0.2, 0, 0, 1)';
            s.style.transform = '';
        }
    });
    card.addEventListener('drop', (e) => {
        e.preventDefault();
        _dropAccepted = true;
    });
}
