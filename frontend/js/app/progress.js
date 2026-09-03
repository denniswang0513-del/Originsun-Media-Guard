// ─── 進度條 / 完成摘要 / 錯誤面板 ─── //
// 從 app.js 拆出（2026-09-03）：備份三段＋報表一段的進度條、任務完成摘要卡、錯誤面板。
// 只讀寫 window._backupPipeline / _backupReportPending / _taskErrors / _concatMultiCard /
// _remoteDispatching 這些 app.js 派發時設的狀態；不 import utils.js（開機順序照舊）。
// ================= Progress Bar Update =================
// Helper: format bytes to human-readable
function _formatBytes(b) {
    if (b == null || b <= 0) return '';
    if (b >= 1e12) return (b / 1e12).toFixed(1) + ' TB';
    if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
    if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
    return (b / 1e3).toFixed(0) + ' KB';
}

// Helper: format elapsed seconds to HH:MM:SS
function _formatElapsed(sec) {
    if (!sec || sec <= 0) return '00:00';
    const s = Math.round(sec);
    const hh = Math.floor(s / 3600);
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
    const ss = String(s % 60).padStart(2, '0');
    return hh > 0 ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`;
}

// Helper: format speed + ETA string (with completion time point)
function _formatEta(data) {
    const parts = [];
    if (data.speed_mbps != null) parts.push(`${data.speed_mbps.toFixed(1)} MB/s`);
    if (data.eta_sec != null && data.eta_sec > 0) {
        const s = Math.round(data.eta_sec);
        parts.push(`剩餘 ${_formatElapsed(s)}`);
        const finish = new Date(Date.now() + s * 1000);
        parts.push(`預計 ${String(finish.getHours()).padStart(2,'0')}:${String(finish.getMinutes()).padStart(2,'0')} 完成`);
    }
    return parts.join('　');
}

// Helper: format phase label text (with GB display)
function _phaseLabel(phase, totalPct, data) {
    const PHASE_TEXT = {
        backup_local: "第一階段：寫入本機", backup_nas: "第二階段：寫入 NAS",
        rescan: "二次掃描/補齊", transcode: "Proxy轉檔", concat: "串帶作業",
        report: "📊 報表生成與同步", verify: "Hash 比對",
    };
    const phaseText = PHASE_TEXT[phase] || "進度";
    const done = data.done_files ?? 0;
    const total = data.total_files ?? 0;
    const fname = data.current_file || '';

    let sizeStr = '';
    if (data.total_bytes > 0) {
        sizeStr = `　${_formatBytes(data.done_bytes || 0)} / ${_formatBytes(data.total_bytes)}`;
    }

    if (fname) return `${phaseText}　${done}/${total} 檔${sizeStr} (${totalPct.toFixed(1)}%)　${fname}`;
    return `${phaseText}　${done}/${total} 檔${sizeStr} (${totalPct.toFixed(1)}%)`;
}

// ── Completion Summary ──
// Show final status after task completes (instead of resetting)
function showCompletionSummary(summary, tab) {
    if (!summary) return;
    // 備份 TAB 的最終摘要已顯示，不允許再覆蓋
    const _isBackup = tab === 'backup' || (tab == null && window._activeJobTab === 'backup');
    if (_isBackup && window._backupFinalShown) return;
    const type = summary.task_type || tab || '';
    const files = summary.total_files || 0;
    const bytes = summary.total_bytes || 0;
    const elapsed = summary.elapsed_sec || 0;
    const matched = summary.verify_matched || 0;
    const mismatched = summary.verify_mismatched || 0;

    let label = '✅ 完成';
    const parts = [];

    // 備份 TAB：顯示所有已完成的勾選項目
    const pipeline = window._backupPipeline;
    if ((type === 'backup' || tab === 'backup') && pipeline && !pipeline._shown) {
        label = '✅ 全部完成';
        parts.push(pipeline.phases.map(p => `${p} ✓`).join('　'));
        const totalElapsed = (Date.now() - pipeline.startTime) / 1000;
        if (totalElapsed > 0) parts.push(`總耗時 ${_formatElapsed(totalElapsed)}`);
        pipeline._shown = true;
        window._backupFinalShown = true; // 防止後續 progress 事件覆蓋
    } else if (type === 'backup') { label = '✅ 備份完成'; }
    else if (type === 'transcode') { label = '✅ 轉檔完成'; }
    else if (type === 'concat') { label = '✅ 串帶完成'; }
    else if (type === 'verify') {
        label = mismatched > 0 ? '⚠️ 驗證完成' : '✅ 驗證完成';
        if (mismatched > 0) parts.push(`${matched}/${matched + mismatched} 檔一致，${mismatched} 檔不符`);
        else if (files > 0) parts.push(`${files}/${files} 檔一致`);
    }
    else if (type === 'transcribe') { label = '✅ 轉錄完成'; }
    else if (type === 'report') { label = '✅ 報表完成'; }

    if (!pipeline && type !== 'verify' && files > 0) parts.push(`${files} 檔`);
    if (!pipeline && bytes > 0) parts.push(_formatBytes(bytes));
    if (!pipeline && elapsed > 0) parts.push(`耗時 ${_formatElapsed(elapsed)}`);

    const text = parts.length > 0 ? `${label} — ${parts.join(' / ')}` : label;

    // Update the active tab's progress bar
    const activeTab = tab || window._activeJobTab || 'backup';
    const prefixMap = { backup: 'bk', verify: 'vf', transcode: 'tc', concat: 'ct', report: 'rp' };
    const prefix = prefixMap[activeTab];

    // Helper: fill multi-segment bars to 100% green
    function _fillSegmentsGreen(segIds, lblIds, widthPct) {
        segIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.style.width = widthPct; el.style.backgroundColor = '#22c55e'; }
        });
        lblIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = '100%';
        });
    }

    if (activeTab === 'backup') {
        // 若報表尚在執行中，不顯示完成摘要
        if (window._backupReportPending) return;
        const progLabel = document.getElementById('bk-prog-label');
        const progEta = document.getElementById('bk-prog-eta');
        if (progLabel) progLabel.textContent = text;
        if (progEta) progEta.textContent = '';
        // 動態段寬
        const _doT = document.getElementById('chk_transcode')?.checked ?? false;
        const _doC = document.getElementById('chk_concat')?.checked ?? false;
        const _doR = !document.getElementById('bk-seg-report')?.classList.contains('hidden');
        const _n = 1 + (_doT ? 1 : 0) + (_doC ? 1 : 0) + (_doR ? 1 : 0);
        const _w = (100 / _n).toFixed(2) + '%';
        const segIds = ['bk-seg-backup'];
        const lblIds = ['bk-lbl-backup'];
        if (_doT) { segIds.push('bk-seg-trans'); lblIds.push('bk-lbl-trans'); }
        if (_doC) { segIds.push('bk-seg-concat'); lblIds.push('bk-lbl-concat'); }
        if (_doR) { segIds.push('bk-seg-report'); lblIds.push('bk-lbl-report'); }
        _fillSegmentsGreen(segIds, lblIds, _w);
    } else if (activeTab === 'report') {
        const rpLabel = document.getElementById('rp-prog-label');
        if (rpLabel) rpLabel.textContent = text;
        const rpEta = document.getElementById('rp-prog-eta');
        if (rpEta) rpEta.textContent = '';
        _fillSegmentsGreen(
            ['rp-seg-scan', 'rp-seg-meta', 'rp-seg-strip', 'rp-seg-render'],
            ['rp-lbl-scan', 'rp-lbl-meta', 'rp-lbl-strip', 'rp-lbl-render'], '25%');
    } else if (prefix) {
        const bar = document.getElementById(prefix + '-prog-bar');
        const lbl = document.getElementById(prefix + '-prog-label');
        const eta = document.getElementById(prefix + '-prog-eta');
        const detail = document.getElementById(prefix + '-prog-detail');
        if (bar) { bar.style.width = '100%'; bar.style.backgroundColor = mismatched > 0 ? '#f59e0b' : '#22c55e'; }
        if (lbl) lbl.textContent = text;
        if (eta) eta.textContent = '';
        if (detail) detail.textContent = '';
    }

    // 任務完成後顯示錯誤面板（如果有錯誤）
    _showErrorPanelIfNeeded();
}

// ── 錯誤摘要面板（通用，所有 TAB 共用）──
function _getErrorPanelParent() {
    const tab = window._activeJobTab || 'backup';
    // 每個 TAB 的進度條區域 ID
    const parentMap = {
        backup: 'bk-progress',
        verify: 'vf-progress',
        transcode: 'tc-progress',
        concat: 'ct-progress',
        report: 'rp-progress',
        transcribe: 'transcribe_progress_area',
        tts: 'tts_progress_area',
    };
    const parentId = parentMap[tab];
    return parentId ? document.getElementById(parentId) : null;
}

function _ensureErrorPanel(parent) {
    if (!parent) return null;
    let panel = parent.querySelector('.task-error-panel');
    if (panel) return panel;
    // 動態建立錯誤面板
    panel = document.createElement('div');
    panel.className = 'task-error-panel hidden mt-2 mb-2 rounded-lg border border-red-800/60 bg-red-950/40 overflow-hidden';
    panel.innerHTML = `
        <div class="flex items-center justify-between px-3 py-2 cursor-pointer select-none" onclick="window._toggleErrorPanel(this)">
            <span class="text-red-400 text-xs font-semibold"><span class="err-icon">&#9660;</span> <span class="err-count">0</span> -- 請檢查檔案狀態</span>
            <button class="text-xs text-red-400/70 hover:text-red-300 underline" onclick="event.stopPropagation();document.getElementById('terminal')?.scrollIntoView({behavior:'smooth'})">查看完整 Log</button>
        </div>
        <div class="err-list px-3 pb-2"></div>
    `;
    parent.after(panel);
    return panel;
}

function _showErrorPanelIfNeeded() {
    const errors = window._taskErrors || [];
    const parent = _getErrorPanelParent();
    if (!parent) return;
    const panel = _ensureErrorPanel(parent);
    if (!panel) return;

    if (errors.length === 0) {
        panel.classList.add('hidden');
        return;
    }

    // 確保進度區域可見（快速失敗的任務可能沒觸發進度條顯示）
    parent.classList.remove('hidden');

    const countEl = panel.querySelector('.err-count');
    const list = panel.querySelector('.err-list');
    if (countEl) countEl.textContent = errors.length + ' 個錯誤';
    if (list) list.innerHTML = errors.map(e =>
        `<div class="flex gap-2 text-xs py-1 border-t border-red-900/30">` +
        `<span class="text-red-600/70 shrink-0 font-mono">${e.ts}</span>` +
        (e.phase ? `<span class="text-red-500/60 shrink-0">[${e.phase}]</span>` : '') +
        `<span class="text-red-300/90 break-all">${e.msg.replace(/</g, '&lt;')}</span>` +
        `</div>`
    ).join('');

    panel.classList.remove('hidden');
    if (list) list.style.display = '';
    const icon = panel.querySelector('.err-icon');
    if (icon) icon.innerHTML = '&#9660;';
}

function _hideErrorPanel() {
    // 隱藏所有 TAB 的錯誤面板
    document.querySelectorAll('.task-error-panel').forEach(p => p.classList.add('hidden'));
}

window._toggleErrorPanel = function(header) {
    const panel = header?.closest('.task-error-panel');
    if (!panel) return;
    const list = panel.querySelector('.err-list');
    const icon = panel.querySelector('.err-icon');
    if (!list) return;
    const hidden = list.style.display === 'none';
    list.style.display = hidden ? '' : 'none';
    if (icon) icon.innerHTML = hidden ? '&#9660;' : '&#9654;';
};

// Helper: update a simple single-bar progress (verify/transcode/concat standalone)
function _updateSimpleProgress(prefix, totalPct, data) {
    const container = document.getElementById(prefix + '-progress');
    const bar = document.getElementById(prefix + '-prog-bar');
    const label = document.getElementById(prefix + '-prog-label');
    const eta = document.getElementById(prefix + '-prog-eta');
    const detail = document.getElementById(prefix + '-prog-detail');
    if (container) container.classList.remove('hidden');
    if (bar) bar.style.width = `${totalPct}%`;
    if (label) label.textContent = _phaseLabel(data.phase, totalPct, data);
    if (eta) eta.textContent = _formatEta(data);
    if (detail) detail.textContent = data.current_file ? `${data.done_files ?? 0}/${data.total_files ?? 0} ${data.current_file}` : '';
}

function updateProgress(data) {
    const phase = data.phase || 'backup';
    const filePct = data.file_pct ?? 0;
    const totalPct = data.total_pct ?? 0;
    const done = data.done_files ?? 0;
    const total = data.total_files ?? 0;
    const fname = data.current_file || '';
    const tab = window._activeJobTab || 'backup';

    // ── Standalone TAB progress bars ──
    if (tab === 'verify' && phase === 'verify') {
        _updateSimpleProgress('vf', totalPct, data);
        return;
    }
    if (tab === 'transcode' && phase === 'transcode') {
        _updateSimpleProgress('tc', totalPct, data);
        return;
    }
    if (tab === 'concat' && phase === 'concat') {
        _updateSimpleProgress('ct', totalPct, data);
        return;
    }

    // ── Backup TAB: dynamic multi-segment progress bar ──
    const segBackup = document.getElementById('bk-seg-backup');
    if (!segBackup) return; // tab not loaded yet

    // 完成摘要已顯示，不再接受進度更新
    if (window._backupFinalShown) return;

    // 多機模式下，忽略本機的 transcode/concat progress（由 heartbeat 聚合）
    if (window._remoteDispatching && (phase === 'transcode' || phase === 'concat')) {
        return;
    }

    const container = document.getElementById('bk-progress');
    if (container) container.classList.remove('hidden');
    const lblBackup = document.getElementById('bk-lbl-backup');
    const segTrans = document.getElementById('bk-seg-trans');
    const lblTrans = document.getElementById('bk-lbl-trans');
    const segConcat = document.getElementById('bk-seg-concat');
    const lblConcat = document.getElementById('bk-lbl-concat');
    const progLabel = document.getElementById('bk-prog-label');
    const progEta = document.getElementById('bk-prog-eta');

    // 動態計算每段寬度：根據勾選的執行項目
    const _doTrans = document.getElementById('chk_transcode')?.checked ?? false;
    const _doConcat = document.getElementById('chk_concat')?.checked ?? false;
    const _doReport = !!window._backupReportPending || !document.getElementById('bk-seg-report')?.classList.contains('hidden');
    const _segCount = 1 + (_doTrans ? 1 : 0) + (_doConcat ? 1 : 0) + (_doReport ? 1 : 0);
    const _sw = 100 / _segCount; // 每段寬度百分比

    if (phase === 'backup_local' || phase === 'backup_nas') {
        let combinedPct = 0;
        let barWidth = 0;
        if (phase === 'backup_local') {
            combinedPct = totalPct / 2;
            barWidth = (totalPct / 100) * (_sw / 2);
        } else {
            combinedPct = 50 + (totalPct / 2);
            barWidth = (_sw / 2) + ((totalPct / 100) * (_sw / 2));
        }
        segBackup.style.width = `${barWidth}%`;
        segBackup.style.backgroundColor = phase === 'backup_local' ? '#1f538d' : '#143c68';
        lblBackup.textContent = `${combinedPct.toFixed(0)}%`;
        if (segTrans) { segTrans.style.width = '0%'; lblTrans.textContent = '0%'; }
        if (segConcat) { segConcat.style.width = '0%'; lblConcat.textContent = '0%'; }
    } else if (phase === 'rescan') {
        segBackup.style.width = `${_sw}%`;
        segBackup.style.backgroundColor = '#0d6e6e';
        if (segTrans) { segTrans.style.width = '0%'; segTrans.style.backgroundColor = '#d48a04'; }
        if (segConcat) { segConcat.style.width = '0%'; segConcat.style.backgroundColor = '#228b22'; }
        const isRecopying = fname.startsWith('[補齊]');
        lblBackup.textContent = isRecopying ? `補${totalPct.toFixed(0)}%` : `掃${totalPct.toFixed(0)}%`;
        if (lblTrans) lblTrans.textContent = '0%';
        if (lblConcat) lblConcat.textContent = '0%';
    } else if (phase === 'transcode') {
        segBackup.style.width = `${_sw}%`; segBackup.style.backgroundColor = '#1f538d';
        if (segTrans) { segTrans.style.width = `${(totalPct / 100) * _sw}%`; segTrans.style.backgroundColor = '#d48a04'; }
        lblBackup.textContent = '100%';
        if (lblTrans) lblTrans.textContent = `${totalPct.toFixed(0)}%`;
        if (segConcat) { segConcat.style.width = '0%'; } if (lblConcat) lblConcat.textContent = '0%';
    } else if (phase === 'concat') {
        // 多卡串帶聚合：每張卡佔 1/total
        let aggConcatPct = totalPct;
        const mc = window._concatMultiCard;
        if (mc && mc.total > 1) {
            aggConcatPct = (mc.done / mc.total + totalPct / 100 / mc.total) * 100;
        }
        segBackup.style.width = `${_sw}%`; segBackup.style.backgroundColor = '#1f538d';
        if (segTrans) { segTrans.style.width = `${_sw}%`; segTrans.style.backgroundColor = '#d48a04'; }
        if (segConcat) { segConcat.style.width = `${(aggConcatPct / 100) * _sw}%`; segConcat.style.backgroundColor = '#228b22'; }
        lblBackup.textContent = '100%';
        if (lblTrans) lblTrans.textContent = '100%';
        if (lblConcat) lblConcat.textContent = `${aggConcatPct.toFixed(0)}%`;
    } else if (phase === 'report') {
        segBackup.style.width = `${_sw}%`; segBackup.style.backgroundColor = '#1f538d';
        if (segTrans) { segTrans.style.width = `${_sw}%`; segTrans.style.backgroundColor = '#d48a04'; }
        if (segConcat) { segConcat.style.width = `${_sw}%`; segConcat.style.backgroundColor = '#228b22'; }
        const segReportEl = document.getElementById('bk-seg-report');
        const lblReportEl = document.getElementById('bk-lbl-report');
        const legendReport = document.getElementById('bk-legend-report');
        if (segReportEl) { segReportEl.classList.remove('hidden'); segReportEl.style.width = `${(totalPct / 100) * _sw}%`; }
        if (lblReportEl) lblReportEl.textContent = `${totalPct.toFixed(0)}%`;
        if (legendReport) legendReport.classList.remove('hidden');
        lblBackup.textContent = '100%';
        if (lblTrans) lblTrans.textContent = '100%';
        if (lblConcat) lblConcat.textContent = '100%';
    } else if (phase === 'verify') {
        const teal = '#0d6e6e';
        segBackup.style.width = `${Math.min(totalPct, 100)}%`; segBackup.style.backgroundColor = teal;
        lblBackup.textContent = `${totalPct.toFixed(0)}%`;
    }

    // Labels
    if (progLabel) progLabel.textContent = _phaseLabel(phase, totalPct, data);
    if (progEta) progEta.textContent = _formatEta(data);
}

export { updateProgress, showCompletionSummary, _showErrorPanelIfNeeded, _hideErrorPanel };
