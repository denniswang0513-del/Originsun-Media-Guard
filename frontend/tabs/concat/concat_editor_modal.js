/**
 * concat_editor_modal.js — Advanced concat editor modal.
 *
 * Data source: drone_meta tab's getDmFiles() (exposed on window).
 * State persisted in window._concatAdvancedClips between opens.
 */

import { COLOR_DEFAULTS, fmtDuration as _fmtDuration, secToHMS as _secToHMS } from '../../js/shared/clip_utils.js';
import { createClipCard } from '../../js/shared/clip_card.js';
import { renderInlineEditor, teardownInlineEditor } from '../drone_meta/drone_meta_editor.js';

let _clips = [];
let _inlineEditIdx = -1;

function _cloneClip(f) {
    // Preserve all drone_meta fields (codec, size, is_dji, dji_gps, dji_camera, creation_time, filmstrip, ...)
    return {
        ...f,
        selected: true,
        trim_in: 0,
        trim_out: f.duration || -1,
        ...COLOR_DEFAULTS,
    };
}

// ── Open/Close ──

// Display fields that should always reflect the latest drone_meta scan state.
const _REFRESH_FIELDS = ['thumbnail', 'duration', 'codec', 'size', 'width', 'height',
                          'creation_time', 'is_dji', 'dji_gps', 'dji_camera', 'filmstrip'];

function _mergeFresh(stored, freshByPath) {
    const fresh = freshByPath.get(stored.path);
    if (!fresh) return stored;
    const merged = { ...stored };
    for (const k of _REFRESH_FIELDS) {
        if (fresh[k]) merged[k] = fresh[k];
    }
    // If trim_out was clamped to an old duration, extend to new duration
    if (fresh.duration && stored.trim_out >= 0 && stored.trim_out === stored.duration) {
        merged.trim_out = fresh.duration;
    }
    return merged;
}

const _PHASE_LABEL = { init: '準備中', files: '列出檔案', thumbs: '載入縮圖', details: '讀取資訊' };

export function openConcatEditor() {
    const scanned = (typeof window.getDmFiles === 'function') ? window.getDmFiles() : [];
    const byPath = new Map(scanned.map(f => [f.path, f]));

    // If drone_meta scan is still running, warn user (data will auto-refresh live)
    const scan = (typeof window.getDmScanState === 'function') ? window.getDmScanState() : null;
    if (scan && scan.running) {
        const phaseName = _PHASE_LABEL[scan.phase] || scan.phase;
        const msg = `空拍掃描還在進行中（${phaseName} ${scan.done}/${scan.total}）。\n現在打開部分編輯功能受影響。\n\n建議現在打開嗎？`;
        if (!confirm(msg)) return;
    }

    if (window._concatAdvancedClips && window._concatAdvancedClips.length) {
        _clips = window._concatAdvancedClips.map(c => _mergeFresh({ ...c }, byPath));
    } else {
        if (!scanned.length) {
            alert('請先到「空拍寫入」Tab 掃描檔案後再打開進階編輯。');
            return;
        }
        _clips = scanned.map(_cloneClip);
    }
    _renderModal();
    _startLiveRefresh();
}
window.openConcatEditor = openConcatEditor;

// Live refresh: when drone_meta updates any file (thumb/detail), patch the matching clip
let _liveRefreshHandler = null;

function _startLiveRefresh() {
    _stopLiveRefresh();
    _liveRefreshHandler = (e) => {
        if (!document.getElementById('cc_editor_overlay')) { _stopLiveRefresh(); return; }
        const path = e.detail?.path;
        if (!path) return;
        const clipIdx = _clips.findIndex(c => c.path === path);
        if (clipIdx < 0) return;
        const fresh = (window.getDmFiles() || []).find(f => f.path === path);
        if (!fresh) return;
        for (const k of _REFRESH_FIELDS) {
            if (fresh[k]) _clips[clipIdx][k] = fresh[k];
        }
        _refreshOneCard(clipIdx);
    };
    window.addEventListener('dmfile:updated', _liveRefreshHandler);
}

function _stopLiveRefresh() {
    if (_liveRefreshHandler) {
        window.removeEventListener('dmfile:updated', _liveRefreshHandler);
        _liveRefreshHandler = null;
    }
}

function _refreshOneCard(idx) {
    // Find the card by dataset.idx in either grid
    const clip = _clips[idx];
    if (!clip) return;
    const oldCard = document.querySelector(`.cc-clip-card[data-idx="${idx}"]`);
    if (!oldCard?.parentElement) return;
    const newCard = createClipCard(clip, idx, { cardClass: _cardClass(clip.selected), ..._cardOpts() });
    oldCard.parentElement.replaceChild(newCard, oldCard);
}

function _closeModal() {
    _stopLiveRefresh();
    teardownInlineEditor();
    const overlay = document.getElementById('cc_editor_overlay');
    if (overlay) overlay.remove();
    _inlineEditIdx = -1;
}

function _applyAndClose() {
    window._concatAdvancedClips = _clips.map(c => ({ ...c }));
    _updateConcatStatus();
    // Broadcast new order + selection so drone_meta's outer grid can sync.
    window.dispatchEvent(new CustomEvent('dmfile:order-synced', {
        detail: {
            paths: _clips.map(c => c.path),
            selectedPaths: _clips.filter(c => c.selected).map(c => c.path),
        }
    }));
    _closeModal();
}

function _updateConcatStatus() {
    const status = document.getElementById('cc_editor_status');
    const clearBtn = document.getElementById('cc_clear_advanced');
    const clips = window._concatAdvancedClips || [];
    if (clips.length) {
        const sel = clips.filter(c => c.selected).length;
        if (status) status.textContent = `已套用：${clips.length} 段 (選中 ${sel})`;
        if (clearBtn) clearBtn.classList.remove('hidden');
    } else {
        if (status) status.textContent = '尚未套用進階編輯';
        if (clearBtn) clearBtn.classList.add('hidden');
    }
}

export function clearConcatAdvanced() {
    if (!confirm('確定清除進階編輯設定？')) return;
    window._concatAdvancedClips = null;
    _updateConcatStatus();
}
window.clearConcatAdvanced = clearConcatAdvanced;

// ── Render Modal ──

function _renderModal() {
    document.getElementById('cc_editor_overlay')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'cc_editor_overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    overlay.innerHTML = `
        <div style="width:95%;max-width:1400px;max-height:95vh;background:#1a1a1a;border:1px solid #3a3a3a;border-radius:8px;display:flex;flex-direction:column;overflow:hidden;">
            <div class="flex items-center justify-between px-5 py-3 border-b border-[#3a3a3a]">
                <h3 class="text-lg font-semibold text-gray-200">🎬 進階編輯串帶</h3>
                <button onclick="_ccmClose()" class="text-gray-400 hover:text-white text-xl">✕</button>
            </div>
            <div class="px-5 py-2 text-xs text-gray-400 border-b border-[#3a3a3a]" id="cc_editor_info">
                資料來源：空拍 Tab 掃描結果 · 共 ${_clips.length} 段
            </div>
            <div style="flex:1;overflow-y:auto;padding:16px;">
                <!-- Grid -->
                <div class="flex items-center gap-3 mb-3 text-xs text-gray-400">
                    <label class="flex items-center gap-1 cursor-pointer">
                        <input type="checkbox" id="cc_select_all" checked onchange="_ccmSelectAll(this.checked)"
                            class="form-checkbox text-blue-500 bg-[#1e1e1e] border-[#444] rounded"> 全選
                    </label>
                    <span class="text-gray-600">|</span>
                    <span>拖曳卡片調整順序</span>
                    <span id="cc_sel_count" class="ml-auto text-gray-500"></span>
                </div>
                <div class="mb-2 text-xs text-purple-300 font-semibold flex items-center gap-2">
                    <span>已選（<span id="cc_selected_count">0</span>）</span>
                    <span class="text-gray-600">|</span>
                    <span class="text-gray-500">拖曳調整排列順序</span>
                </div>
                <div id="cc_selected_grid" class="grid gap-2 mb-4 min-h-[60px]" style="grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));"></div>
                <div id="cc_unchecked_section" class="hidden">
                    <div class="mb-2 text-xs text-gray-500 font-semibold flex items-center gap-2 border-t border-[#333] pt-3">
                        <span>未勾選暫存（<span id="cc_unchecked_count">0</span>）</span>
                        <span class="text-gray-600">|</span>
                        <span class="text-gray-600">勾選即重新加入排列</span>
                    </div>
                    <div id="cc_unchecked_grid" class="grid gap-2 mb-4 opacity-60" style="grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));"></div>
                </div>

                <!-- Inline editor (for single clip trim+color) -->
                <div id="cc_inline_edit" class="hidden mb-4 bg-[#1e1e1e] border border-[#444] rounded-lg overflow-hidden"></div>
            </div>
            <div class="flex items-center justify-end gap-3 px-5 py-3 border-t border-[#3a3a3a] bg-[#1e1e1e]">
                <button onclick="_ccmClose()" class="text-sm bg-[#333] hover:bg-[#444] px-4 py-2 rounded border border-[#555] text-gray-300">取消</button>
                <button onclick="_ccmApply()" class="text-sm bg-[#228b22] hover:bg-[#2eaa2e] px-6 py-2 rounded text-white font-semibold">💾 套用並關閉</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    _renderGrid();
    _updateSelCount();
}

// ── Grid ──

function _cardClass(selected) {
    return 'cc-clip-card bg-[#252525] rounded-lg border-2 overflow-hidden cursor-grab '
        + (selected ? 'border-purple-500' : 'border-[#3a3a3a]');
}

function _cardOpts() {
    return {
        accentColor: 'purple',
        actionLabel: '\u2702 修剪/色彩',
        showMeta: true,
        showDji: false,
        showDateTime: false,
        showBadges: true,
        showRefresh: true,
        onToggle: _onToggleClip,
        onAction: _openInline,
        onRefresh: _refreshClip,
        onReorder: _reorderClips,
    };
}

function _renderGrid() {
    const selGrid = document.getElementById('cc_selected_grid');
    const unGrid = document.getElementById('cc_unchecked_grid');
    const unSection = document.getElementById('cc_unchecked_section');
    if (!selGrid || !unGrid) return;
    selGrid.innerHTML = '';
    unGrid.innerHTML = '';
    let selCount = 0, unCount = 0;
    _clips.forEach((c, i) => {
        const card = createClipCard(c, i, { cardClass: _cardClass(c.selected), ..._cardOpts() });
        if (c.selected) { selGrid.appendChild(card); selCount++; }
        else { unGrid.appendChild(card); unCount++; }
    });
    document.getElementById('cc_selected_count').textContent = selCount;
    document.getElementById('cc_unchecked_count').textContent = unCount;
    if (unSection) unSection.classList.toggle('hidden', unCount === 0);
}

function _reorderClips(_from, _to) {
    // DOM of selected grid was reordered live during dragover.
    // Rebuild _clips = [selected in new DOM order, ...unchecked keeping original order]
    const selGrid = document.getElementById('cc_selected_grid');
    if (!selGrid) return;
    const newSelectedIdxs = Array.from(selGrid.children).map(c => parseInt(c.dataset.idx));
    const selectedSet = new Set(newSelectedIdxs);
    const newClips = newSelectedIdxs.map(i => _clips[i]);
    for (let i = 0; i < _clips.length; i++) {
        if (!selectedSet.has(i)) newClips.push(_clips[i]);
    }
    _clips = newClips;
    _inlineEditIdx = -1;
    document.getElementById('cc_inline_edit')?.classList.add('hidden');
    _renderGrid();  // rebuild to resync dataset.idx
}

async function _refreshClip(idx) {
    const clip = _clips[idx];
    if (!clip?.path) return;
    try {
        const res = await fetch(`/api/v1/drone_meta/rescan_file?path=${encodeURIComponent(clip.path)}`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data?.path) {
            for (const [k, v] of Object.entries(data)) {
                if (v !== '' && v !== null && !(Array.isArray(v) && v.length === 0)) {
                    _clips[idx][k] = v;
                }
            }
            _refreshOneCard(idx);
        }
    } catch (e) {
        console.warn('[concat] refresh failed:', e);
    }
}

function _onToggleClip(idx, checked) {
    if (_clips[idx]) _clips[idx].selected = checked;
    // Re-render to move card between selected/unchecked grids
    _renderGrid();
    _updateSelCount();
    const all = document.getElementById('cc_select_all');
    if (all) all.checked = _clips.every(c => c.selected);
}

window._ccmSelectAll = function(checked) {
    _clips.forEach(c => c.selected = checked);
    _renderGrid();
    _updateSelCount();
};

function _updateSelCount() {
    const sel = _clips.filter(c => c.selected).length;
    const el = document.getElementById('cc_sel_count');
    if (el) el.textContent = `選中 ${sel} / ${_clips.length}`;
}

// Copy current clip's color/tonal/curve settings to all other clips.
// Called from the per-clip inline editor's "套用到全部" button.
window._ccmApplyColorToAll = function(srcIdx) {
    // Sync any uncommitted slider values from DOM back into _clips[srcIdx] first
    const src = _clips[srcIdx];
    if (!src) return;
    for (const name of Object.keys(COLOR_DEFAULTS)) {
        const el = document.getElementById(`dm_color_${name}_${srcIdx}`);
        if (el) src[name] = parseFloat(el.value);
    }
    for (const name of ['shadows', 'midtones', 'highlights']) {
        const el = document.getElementById(`dm_color_${name}_${srcIdx}`);
        if (el) src[name] = parseFloat(el.value) || 0;
    }
    const fields = [...Object.keys(COLOR_DEFAULTS), 'shadows', 'midtones', 'highlights'];
    _clips.forEach((c, i) => {
        if (i === srcIdx) return;
        fields.forEach(f => c[f] = src[f]);
        c.curve_points = Array.isArray(src.curve_points)
            ? src.curve_points.map(p => [p[0], p[1]])
            : null;
    });
    _renderGrid();
};

// ── Inline editor (per-clip trim + color) ──

function _openInline(idx) {
    const container = document.getElementById('cc_inline_edit');
    if (!container) return;
    if (_inlineEditIdx === idx) {
        teardownInlineEditor();
        _inlineEditIdx = -1;
        container.classList.add('hidden');
        return;
    }
    // Clean up previous clip's editor (if switching) before rendering new one
    if (_inlineEditIdx !== -1) teardownInlineEditor();
    _inlineEditIdx = idx;
    const clip = _clips[idx];
    container.classList.remove('hidden');
    container.style.position = 'relative';

    renderInlineEditor(container, clip, idx, _fmtDuration);

    // Sync current clip values into editor DOM (synchronous: renderInlineEditor uses innerHTML which parses immediately)
    for (const name of Object.keys(COLOR_DEFAULTS)) {
        const el = document.getElementById(`dm_color_${name}_${idx}`);
        const valEl = document.getElementById(`dm_color_${name}_val_${idx}`);
        if (el) el.value = clip[name];
        if (valEl) valEl.textContent = clip[name];
    }
    for (const name of ['shadows', 'midtones', 'highlights']) {
        const v = clip[name] ?? 0;
        const el = document.getElementById(`dm_color_${name}_${idx}`);
        const valEl = document.getElementById(`dm_color_${name}_val_${idx}`);
        if (el) el.value = v;
        if (valEl) valEl.textContent = v;
    }
    const inTxt = document.getElementById(`dm_trim_in_txt_${idx}`);
    const outTxt = document.getElementById(`dm_trim_out_txt_${idx}`);
    if (inTxt && clip.trim_in) inTxt.value = _secToHMS(clip.trim_in);
    if (outTxt && clip.trim_out > 0) outTxt.value = _secToHMS(clip.trim_out);

    const applyAllBtn = document.createElement('button');
    applyAllBtn.textContent = '↪ 套用到全部';
    applyAllBtn.className = 'absolute top-2 right-28 text-xs bg-[#6b3fa0] hover:bg-[#8254c0] px-2 py-1 rounded border border-[#8254c0] text-white z-10';
    applyAllBtn.title = '將此片段的色彩/影調/曲線設定複製到所有片段';
    applyAllBtn.onclick = () => {
        if (confirm('將此片段的色彩設定套用到所有片段？')) {
            window._ccmApplyColorToAll(idx);
        }
    };
    container.appendChild(applyAllBtn);

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '✕ 收合並套用';
    closeBtn.className = 'absolute top-2 right-2 text-xs bg-[#333] hover:bg-[#444] px-2 py-1 rounded border border-[#555] text-gray-300 z-10';
    closeBtn.onclick = () => _ccmCloseInline(idx);
    container.appendChild(closeBtn);

    container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

function _ccmCloseInline(idx) {
    // Read values from editor and save back to clip
    const clip = _clips[idx];
    if (!clip) return;
    for (const name of Object.keys(COLOR_DEFAULTS)) {
        const el = document.getElementById(`dm_color_${name}_${idx}`);
        if (el) clip[name] = parseFloat(el.value);
    }
    for (const name of ['shadows', 'midtones', 'highlights']) {
        const el = document.getElementById(`dm_color_${name}_${idx}`);
        if (el) clip[name] = parseFloat(el.value) || 0;
    }
    // curve_points is updated live on the clip object by the editor; nothing to copy
    const inHidden = document.getElementById(`dm_trim_in_${idx}`);
    const outHidden = document.getElementById(`dm_trim_out_${idx}`);
    if (inHidden) clip.trim_in = parseFloat(inHidden.value) || 0;
    if (outHidden) clip.trim_out = parseFloat(outHidden.value) || clip.duration;

    _inlineEditIdx = -1;
    document.getElementById('cc_inline_edit')?.classList.add('hidden');
    _renderGrid();
}

window._ccmClose = _closeModal;
window._ccmApply = _applyAndClose;

export function refreshConcatEditorStatus() {
    _updateConcatStatus();
}
