// ── Projects Overview Tab ──
// ES Module — loaded dynamically by app.js loadTabs()

// ── Constants ──
const TYPE_COLORS = {
    backup: '#3b82f6', transcode: '#d48a04', concat: '#228b22',
    verify: '#06b6d4', transcribe: '#a855f7', report: '#7c3aed',
    tts: '#ec4899', clone: '#f59e0b',
};
const TYPE_LABELS = {
    backup: '備份', transcode: '轉檔', concat: '串接',
    verify: '驗證', transcribe: '轉錄', report: '報表',
    tts: '語音生成', clone: '聲音複製',
};
const STATUS_LABELS = {
    queued: '排隊中', waiting: '等待坑位', running: '執行中',
    paused: '已暫停', done: '完成', error: '失敗', cancelled: '已取消',
};

// ── Module State ──
const _cards = {};          // job_id → { el, status, taskType, logExpanded }
let _todayDoneCount = 0;

// Queue state
let _queueItems = [];       // current queue data from API
let _isDragging = false;    // true during drag — defers socket updates
let _pendingQueueUpdate = null; // buffered socket update during drag

// Queue pagination state
const QUEUE_PAGE_SIZE = 5;
let _queuePage = 0;
let _loadQueueTimer = null; // debounce timer for _loadQueue

// History pagination state
const HISTORY_PAGE_SIZE = 5;
let _historyAllJobs = [];
let _historyPage = 0;
let _historyFilterTimer = null; // debounce for search input

// Machine polling state
let _agents = [];           // from settings.json
let _agentStatus = {};      // id → { online, slow, data, timerId }
let _pollingActive = true;  // paused when tab not visible

// ── XSS-safe text node helper ──
function _text(str) {
    return document.createTextNode(String(str || ''));
}

// ── Utility: today as YYYY-MM-DD ──
function _todayStr() {
    const d = new Date();
    return d.getFullYear() + '-' +
        String(d.getMonth() + 1).padStart(2, '0') + '-' +
        String(d.getDate()).padStart(2, '0');
}

// ── Utility: format seconds → "Xm Ys" or "Xh Ym" ──
function _fmtDuration(sec) {
    if (!sec || sec < 0) return '--';
    sec = Math.round(sec);
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m < 60) return m + 'm ' + s + 's';
    const h = Math.floor(m / 60);
    return h + 'h ' + (m % 60) + 'm';
}

// ── Utility: compute duration between two ISO timestamps ──
function _calcDuration(started, finished) {
    if (!started || !finished) return null;
    const ms = new Date(finished) - new Date(started);
    return ms > 0 ? Math.round(ms / 1000) : null;
}

// ══════════════════════════════════════════
//  initTab — entry point called by loadTabs
// ══════════════════════════════════════════
export function initTab() {
    _initHistoryDate();
    _loadSettings();
    _loadActiveJobs();
    _loadQueue();
    _loadHistory();
    _loadSchedules();
    _bindSocket();
    _loadAgents();

    // Listen for tab visibility changes
    document.addEventListener('tab-changed', (e) => {
        if (e.detail && e.detail.tab === 'tab-projects') resumePolling();
        else pausePolling();
    });

    // Expose functions for onclick handlers in HTML
    window.projectsTab = {
        toggleLog,
        closeCard,
        pauseJob,
        stopJob,
        toggleSettings,
        saveLimits,
        saveAgentsDir,
        clearHistory,
        refreshQueue,
        jumpToFront,
        markUrgent,
        unmarkUrgent,
        toggleAddAgent,
        addAgent,
        removeAgent,
        resumePolling,
        pausePolling,
        reloadAgents: _loadAgents,
        reloadAgentsNoPolling: _loadAgentsNoPolling,
        toggleSchedule,
        deleteSchedule,
    };
}

// ══════════════════════════════════════════
//  Socket Binding
// ══════════════════════════════════════════
function _bindSocket() {
    const sock = window._socket || window.socket;
    if (!sock) {
        setTimeout(_bindSocket, 300);
        return;
    }
    sock.on('job_queued', _onJobQueued);
    sock.on('task_status', _onTaskStatus);
    sock.on('progress', _onProgress);
    sock.on('log', _onLog);
    sock.on('queue_updated', _onQueueUpdated);
}

// ══════════════════════════════════════════
//  API Calls
// ══════════════════════════════════════════
async function _loadActiveJobs() {
    try {
        const res = await fetch('/api/v1/status');
        if (!res.ok) return;
        const data = await res.json();
        const jobs = data.active_jobs || {};
        for (const [jobId, job] of Object.entries(jobs)) {
            const status = job.status || 'queued';
            // Only show running/paused in "進行中"; queued items show in "等待"
            if (status === 'queued' || status === 'waiting') continue;
            if (!_cards[jobId]) {
                _insertCard(job, false);
            }
            if (job.progress) {
                _updateCardProgress(jobId, job.progress);
            }
            _updateCardStatus(jobId, job.status);
        }
        _showEmptyStateIfNeeded();
        _updateBadges();
    } catch (_) { /* silent */ }
}

async function _loadSettings() {
    try {
        const res = await fetch('/api/settings/load');
        if (!res.ok) return;
        const settings = await res.json();
        const c = settings.concurrency || {};
        for (const key of ['backup', 'transcode', 'transcribe', 'concat']) {
            const sel = document.getElementById('pj-limit-' + key);
            if (sel && c[key] != null) sel.value = String(c[key]);
        }
        // 載入 NAS agents_dir 路徑
        const agentsDir = (settings.nas_paths || {}).agents_dir || '';
        const dirInput = document.getElementById('pj-nas-agents-dir');
        if (dirInput) dirInput.value = agentsDir;
    } catch (_) { /* silent */ }
}

async function saveLimits(key, val) {
    const numVal = parseInt(val, 10);
    if (!numVal || numVal < 1) return;
    try {
        await fetch('/api/settings/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ concurrency: { [key]: numVal } }),
        });
    } catch (_) { /* silent */ }
}

async function saveAgentsDir() {
    const input = document.getElementById('pj-nas-agents-dir');
    if (!input) return;
    const dir = input.value.trim();
    try {
        await fetch('/api/settings/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nas_paths: { agents_dir: dir } }),
        });
        // 重新載入機器列表
        await _loadAgents();
    } catch (_) { /* silent */ }
}

async function _loadHistory() {
    const container = document.getElementById('pj-history-container');
    if (!container) return;

    const dateInput = document.getElementById('pj-history-date');
    const date = dateInput ? dateInput.value : _todayStr();

    container.textContent = '';
    const loading = document.createElement('div');
    loading.className = 'pj-history-empty';
    loading.textContent = '載入中…';
    container.appendChild(loading);

    try {
        let url = '/api/v1/job_history?date=' + encodeURIComponent(date) + '&limit=200';
        const searchVal = (document.getElementById('pj-history-search')?.value || '').trim();
        const typeVal = document.getElementById('pj-history-type-filter')?.value || '';
        const statusVal = document.getElementById('pj-history-status-filter')?.value || '';
        if (searchVal) url += '&q=' + encodeURIComponent(searchVal);
        if (typeVal) url += '&task_type=' + encodeURIComponent(typeVal);
        if (statusVal) url += '&status=' + encodeURIComponent(statusVal);
        const res = await fetch(url);
        if (!res.ok) { loading.textContent = '載入失敗'; return; }
        const data = await res.json();

        _historyAllJobs = data.jobs || [];
        _historyPage = 0;
        _renderHistoryPage();

        // Update today done count
        if (date === _todayStr()) {
            _todayDoneCount = _historyAllJobs.length;
            _updateBadges();
        }
    } catch (_) {
        loading.textContent = '載入失敗';
    }
}

function _renderHistoryPage() {
    const container = document.getElementById('pj-history-container');
    if (!container) return;
    container.textContent = '';

    if (_historyAllJobs.length === 0) {
        const dateInput = document.getElementById('pj-history-date');
        const date = dateInput ? dateInput.value : _todayStr();
        const empty = document.createElement('div');
        empty.className = 'pj-history-empty';
        empty.textContent = date + ' 尚無完成的任務';
        container.appendChild(empty);
        return;
    }

    const totalPages = Math.ceil(_historyAllJobs.length / HISTORY_PAGE_SIZE);
    if (_historyPage >= totalPages) _historyPage = totalPages - 1;
    const start = _historyPage * HISTORY_PAGE_SIZE;
    const end = Math.min(start + HISTORY_PAGE_SIZE, _historyAllJobs.length);

    for (let i = start; i < end; i++) {
        _renderHistoryItem(container, _historyAllJobs[i]);
    }

    if (totalPages > 1) {
        _renderPaginationWidget(container, totalPages, _historyPage, (p) => {
            _historyPage = p; _renderHistoryPage();
        });
    }
}

function _renderPaginationWidget(container, totalPages, currentPage, onPageChange) {
    const wrap = document.createElement('div');
    wrap.className = 'pj-pagination';

    const left = document.createElement('span');
    left.className = 'pj-page-arrow' + (currentPage === 0 ? ' disabled' : '');
    left.textContent = '\u2039';
    left.addEventListener('click', () => { if (currentPage > 0) onPageChange(currentPage - 1); });
    wrap.appendChild(left);

    for (let i = 0; i < totalPages; i++) {
        const dot = document.createElement('span');
        dot.className = 'pj-page-dot' + (i === currentPage ? ' active' : '');
        dot.addEventListener('click', () => onPageChange(i));
        wrap.appendChild(dot);
    }

    const right = document.createElement('span');
    right.className = 'pj-page-arrow' + (currentPage === totalPages - 1 ? ' disabled' : '');
    right.textContent = '\u203a';
    right.addEventListener('click', () => { if (currentPage < totalPages - 1) onPageChange(currentPage + 1); });
    wrap.appendChild(right);

    container.appendChild(wrap);
}

async function clearHistory() {
    const dateInput = document.getElementById('pj-history-date');
    const date = dateInput ? dateInput.value : _todayStr();
    if (!confirm('確定要清除 ' + date + ' 的歷史紀錄？')) return;
    try {
        await fetch('/api/v1/job_history?date=' + encodeURIComponent(date), { method: 'DELETE' });
    } catch (_) { /* silent */ }
    _loadHistory();
}

async function pauseJob(jobId) {
    try {
        await fetch('/api/v1/control/pause?job_id=' + encodeURIComponent(jobId), { method: 'POST' });
    } catch (_) { /* silent */ }
}

async function stopJob(jobId) {
    if (!confirm('確定要停止此任務？')) return;
    try {
        await fetch('/api/v1/control/stop?job_id=' + encodeURIComponent(jobId), { method: 'POST' });
    } catch (_) { /* silent */ }
}

// ══════════════════════════════════════════
//  Settings Panel Toggle
// ══════════════════════════════════════════
function toggleSettings() {
    const panel = document.getElementById('pj-settings-panel');
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' ? 'grid' : 'none';
}

// ══════════════════════════════════════════
//  History Date Init
// ══════════════════════════════════════════
function _initHistoryDate() {
    const input = document.getElementById('pj-history-date');
    if (!input) return;
    const today = _todayStr();
    input.value = today;
    input.max = today;
    input.addEventListener('change', () => _loadHistory());

    // Filter controls
    const searchInput = document.getElementById('pj-history-search');
    const typeFilter = document.getElementById('pj-history-type-filter');
    const statusFilter = document.getElementById('pj-history-status-filter');
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            clearTimeout(_historyFilterTimer);
            _historyFilterTimer = setTimeout(() => _loadHistory(), 300);
        });
    }
    if (typeFilter) typeFilter.addEventListener('change', () => {
        clearTimeout(_historyFilterTimer);
        _historyFilterTimer = setTimeout(() => _loadHistory(), 150);
    });
    if (statusFilter) statusFilter.addEventListener('change', () => {
        clearTimeout(_historyFilterTimer);
        _historyFilterTimer = setTimeout(() => _loadHistory(), 150);
    });
}

// ══════════════════════════════════════════
//  Card Creation (XSS-safe, createElement only)
// ══════════════════════════════════════════
function _createCard(job) {
    const jobId = job.job_id;
    const taskType = job.task_type || 'backup';
    const color = TYPE_COLORS[taskType] || '#666';

    const card = document.createElement('div');
    card.className = 'pj-card pj-entering';
    card.id = 'pj-card-' + jobId;

    // ── Single row ──
    const row = document.createElement('div');
    row.className = 'pj-card-row';
    row.style.borderLeft = '3px solid ' + color;

    // Type badge (leftmost)
    const typeBadge = document.createElement('span');
    typeBadge.className = 'pj-type-badge';
    typeBadge.style.backgroundColor = color;
    typeBadge.appendChild(_text(TYPE_LABELS[taskType] || taskType));
    row.appendChild(typeBadge);

    // Toggle arrow
    const logToggle = document.createElement('span');
    logToggle.className = 'pj-log-toggle';
    logToggle.textContent = '▸';
    logToggle.addEventListener('click', () => toggleLog(jobId));
    row.appendChild(logToggle);

    // Project name
    const projName = document.createElement('span');
    projName.className = 'pj-project-name';
    projName.appendChild(_text(job.project_name || '--'));
    row.appendChild(projName);

    // Progress (inline)
    const progressWrap = document.createElement('div');
    progressWrap.className = 'pj-progress-wrap';

    const progressBar = document.createElement('div');
    progressBar.className = 'pj-progress-bar';

    const progressFill = document.createElement('div');
    progressFill.className = 'pj-progress-fill';
    progressFill.id = 'pj-bar-' + jobId;
    progressFill.style.backgroundColor = color;
    progressBar.appendChild(progressFill);
    progressWrap.appendChild(progressBar);

    const progressInfo = document.createElement('div');
    progressInfo.className = 'pj-progress-info';
    progressInfo.id = 'pj-info-' + jobId;
    progressWrap.appendChild(progressInfo);

    row.appendChild(progressWrap);

    // Status badge
    const statusBadge = document.createElement('span');
    statusBadge.className = 'pj-status-badge';
    statusBadge.id = 'pj-status-' + jobId;
    statusBadge.appendChild(_text(STATUS_LABELS[job.status] || job.status));
    row.appendChild(statusBadge);

    // Actions (inline)
    const actions = document.createElement('div');
    actions.className = 'pj-card-actions';
    actions.id = 'pj-actions-' + jobId;

    const btnPause = document.createElement('button');
    btnPause.className = 'pj-btn pj-btn-pause';
    btnPause.textContent = '暫停';
    btnPause.addEventListener('click', () => pauseJob(jobId));

    const btnStop = document.createElement('button');
    btnStop.className = 'pj-btn pj-btn-stop';
    btnStop.textContent = '停止';
    btnStop.addEventListener('click', () => stopJob(jobId));

    const btnClose = document.createElement('button');
    btnClose.className = 'pj-btn pj-btn-close';
    btnClose.textContent = '關閉';
    btnClose.style.display = 'none';
    btnClose.addEventListener('click', () => closeCard(jobId));

    actions.appendChild(btnPause);
    actions.appendChild(btnStop);
    actions.appendChild(btnClose);
    row.appendChild(actions);

    card.appendChild(row);

    // ── Expandable detail area ──
    const detail = document.createElement('div');
    detail.className = 'pj-card-detail';
    detail.id = 'pj-detail-' + jobId;

    const curFile = document.createElement('div');
    curFile.className = 'pj-current-file';
    curFile.id = 'pj-file-' + jobId;
    detail.appendChild(curFile);

    const logArea = document.createElement('div');
    logArea.className = 'pj-log-area';
    logArea.id = 'pj-log-' + jobId;
    detail.appendChild(logArea);

    card.appendChild(detail);

    // Set initial indeterminate state for queued/waiting
    const status = job.status || 'queued';
    if (status === 'queued' || status === 'waiting') {
        progressFill.classList.add('pj-indeterminate');
    }
    _applyStatusClass(statusBadge, status);

    return card;
}

// ══════════════════════════════════════════
//  Card Insert / Remove
// ══════════════════════════════════════════
function _insertCard(job, animate) {
    const container = document.getElementById('pj-active-container');
    if (!container) return;

    // Remove empty state if present
    const empty = container.querySelector('.pj-empty-state');
    if (empty) empty.remove();

    const card = _createCard(job);
    if (!animate) card.classList.remove('pj-entering');

    container.insertBefore(card, container.firstChild);

    _cards[job.job_id] = {
        el: card,
        status: job.status || 'queued',
        taskType: job.task_type,
        logExpanded: false,
    };

    // Remove entering animation class after it plays
    if (animate) {
        setTimeout(() => card.classList.remove('pj-entering'), 350);
    }
}

function closeCard(jobId) {
    const info = _cards[jobId];
    if (!info) return;
    info.el.classList.add('pj-leaving');
    setTimeout(() => {
        info.el.remove();
        delete _cards[jobId];
        _showEmptyStateIfNeeded();
        _updateBadges();
    }, 280);
}

function _showEmptyStateIfNeeded() {
    const container = document.getElementById('pj-active-container');
    if (!container) return;
    const existing = container.querySelector('.pj-empty-state');

    if (Object.keys(_cards).length === 0) {
        if (!existing) {
            const empty = document.createElement('div');
            empty.className = 'pj-empty-state';
            const spinner = document.createElement('div');
            spinner.className = 'pj-empty-spinner';
            empty.appendChild(spinner);
            const text = document.createElement('div');
            text.textContent = '目前沒有進行中的任務';
            empty.appendChild(text);
            container.appendChild(empty);
        }
    } else {
        if (existing) existing.remove();
    }
}

// ══════════════════════════════════════════
//  Card Status / Progress Update
// ══════════════════════════════════════════
function _applyStatusClass(badge, status) {
    badge.className = 'pj-status-badge';
    if (status === 'running') badge.classList.add('pj-status-running');
    else if (status === 'done') badge.classList.add('pj-status-done');
    else if (status === 'error' || status === 'cancelled') badge.classList.add('pj-status-error');
    else if (status === 'queued' || status === 'waiting') badge.classList.add('pj-status-queued');
}

function _updateCardStatus(jobId, status) {
    const info = _cards[jobId];
    if (!info) return;
    info.status = status;

    const badge = document.getElementById('pj-status-' + jobId);
    if (badge) {
        badge.textContent = '';
        badge.appendChild(_text(STATUS_LABELS[status] || status));
        _applyStatusClass(badge, status);
    }

    const fill = document.getElementById('pj-bar-' + jobId);
    if (fill) {
        if (status === 'queued' || status === 'waiting') {
            fill.classList.add('pj-indeterminate');
        } else {
            fill.classList.remove('pj-indeterminate');
        }
    }

    // Card done/error styling
    const card = info.el;
    card.classList.remove('pj-card-done', 'pj-card-error');
    if (status === 'done') card.classList.add('pj-card-done');
    if (status === 'error' || status === 'cancelled') card.classList.add('pj-card-error');

    // Toggle action buttons
    const actions = document.getElementById('pj-actions-' + jobId);
    if (actions) {
        const btnPause = actions.querySelector('.pj-btn-pause');
        const btnStop = actions.querySelector('.pj-btn-stop');
        const btnClose = actions.querySelector('.pj-btn-close');
        const terminal = (status === 'done' || status === 'error' || status === 'cancelled');

        if (btnPause) btnPause.style.display = terminal ? 'none' : '';
        if (btnStop) btnStop.style.display = terminal ? 'none' : '';
        if (btnClose) btnClose.style.display = terminal ? '' : 'none';
    }

    // Done with 100% progress
    if (status === 'done' && fill) {
        fill.style.width = '100%';
    }

    _updateBadges();
}

function _updateCardProgress(jobId, data) {
    if (!data) return;
    const fill = document.getElementById('pj-bar-' + jobId);
    const infoEl = document.getElementById('pj-info-' + jobId);
    const fileEl = document.getElementById('pj-file-' + jobId);

    if (fill) {
        fill.classList.remove('pj-indeterminate');
        const pct = Math.min(100, Math.max(0, data.total_pct || 0));
        fill.style.width = pct.toFixed(1) + '%';
    }

    if (infoEl) {
        const parts = [];
        if (data.total_pct != null) parts.push(Math.round(data.total_pct) + '%');
        if (data.done_files != null && data.total_files != null) {
            parts.push(data.done_files + '/' + data.total_files + ' 個');
        }
        if (data.speed_mbps != null && data.speed_mbps > 0) {
            parts.push(data.speed_mbps.toFixed(1) + ' MB/s');
        }
        if (data.eta_sec != null && data.eta_sec > 0) {
            parts.push('ETA ' + _fmtDuration(data.eta_sec));
        }
        infoEl.textContent = parts.join(' / ');
    }

    if (fileEl && data.current_file) {
        fileEl.textContent = data.current_file;
    }
}

// ══════════════════════════════════════════
//  Card Log
// ══════════════════════════════════════════
function _appendCardLog(jobId, msg, type) {
    const logArea = document.getElementById('pj-log-' + jobId);
    if (!logArea) return;

    const line = document.createElement('div');
    const cls = 'pj-log-line-' + (type || 'info');
    line.className = cls;
    line.appendChild(_text(msg));
    logArea.appendChild(line);

    // Auto-scroll if near bottom
    if (logArea.scrollHeight - logArea.scrollTop - logArea.clientHeight < 40) {
        logArea.scrollTop = logArea.scrollHeight;
    }

    // Trim old lines (keep last 200)
    while (logArea.children.length > 200) {
        logArea.removeChild(logArea.firstChild);
    }
}

function toggleLog(jobId) {
    const info = _cards[jobId];
    if (!info) return;
    const detail = document.getElementById('pj-detail-' + jobId);
    const logArea = document.getElementById('pj-log-' + jobId);
    const card = info.el;
    const toggle = card.querySelector('.pj-log-toggle');
    if (!detail || !toggle) return;

    info.logExpanded = !info.logExpanded;
    if (info.logExpanded) {
        detail.classList.add('pj-detail-open');
        toggle.textContent = '▾';
        if (logArea) logArea.scrollTop = logArea.scrollHeight;
    } else {
        detail.classList.remove('pj-detail-open');
        toggle.textContent = '▸';
    }
}

// ══════════════════════════════════════════
//  Badge Counts
// ══════════════════════════════════════════
function _updateBadges() {
    let running = 0;
    for (const info of Object.values(_cards)) {
        if (info.status === 'running' || info.status === 'paused') running++;
    }
    // Count queued from queue data (excludes running items)
    const queued = _queueItems.filter(i => i.status !== 'running').length;
    // Count enabled schedules
    const scheduled = _schedules.filter(s => s.enabled).length;

    const elRunning = document.getElementById('pj-count-running');
    const elQueued = document.getElementById('pj-count-queued');
    const elScheduled = document.getElementById('pj-count-scheduled');
    const elDone = document.getElementById('pj-count-done');

    if (elRunning) elRunning.textContent = String(running);
    if (elQueued) elQueued.textContent = String(queued);
    if (elScheduled) elScheduled.textContent = String(scheduled);
    if (elDone) elDone.textContent = String(_todayDoneCount);
}

// ══════════════════════════════════════════
//  Socket Event Handlers
// ══════════════════════════════════════════
function _onJobQueued(data) {
    if (!data || !data.job_id) return;
    // Queued items show in "等待" section — refresh queue panel
    // (debounced: _loadQueue calls _updateBadges after fetch completes)
    _loadQueueDebounced();
}

function _onTaskStatus(data) {
    if (!data || !data.job_id) return;
    const jobId = data.job_id;
    const status = data.status;

    // Create card only for running/paused (queued items stay in "等待")
    if (!_cards[jobId] && (status === 'running' || status === 'paused')) {
        _insertCard({
            job_id: jobId,
            project_name: data.project_name || '',
            task_type: data.task_type || 'backup',
            status: status,
        }, true);
        // Refresh queue to remove this item from "等待"
        _loadQueueDebounced();
    }

    if (_cards[jobId]) {
        _updateCardStatus(jobId, status);
    }

    // On done/error, write to history, auto-close card, refresh queue
    if (status === 'done' || status === 'error' || status === 'cancelled') {
        _todayDoneCount++;
        _updateBadges();

        // Auto-close card after 3 seconds
        if (_cards[jobId]) {
            setTimeout(() => closeCard(jobId), 3000);
        }

        // Post to backend history (deduplication handled server-side)
        const entry = {
            job_id: jobId,
            task_type: data.task_type || (_cards[jobId] ? _cards[jobId].taskType : ''),
            project_name: data.project_name || '',
            status: status,
            finished_at: new Date().toISOString(),
            error_detail: data.detail || data.message || null,
        };
        fetch('/api/v1/job_history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(entry),
        }).catch(() => {});

        // If viewing today, prepend to paginated history
        const dateInput = document.getElementById('pj-history-date');
        if (dateInput && dateInput.value === _todayStr()) {
            _historyAllJobs.unshift(entry);
            _historyPage = 0;
            _renderHistoryPage();
        }

        // Refresh queue
        _loadQueueDebounced();
    }
}

function _onProgress(data) {
    if (!data || !data.job_id) return;
    if (_cards[data.job_id]) {
        _updateCardProgress(data.job_id, data);
        // Ensure status shows running
        if (_cards[data.job_id].status !== 'running') {
            _updateCardStatus(data.job_id, 'running');
        }
    }
}

function _onLog(data) {
    if (!data || !data.job_id) return;
    if (_cards[data.job_id]) {
        _appendCardLog(data.job_id, data.msg, data.type);
    }
}

// ══════════════════════════════════════════
//  Machine Status Polling
// ══════════════════════════════════════════

function _syncComputeHosts() {
    window._computeHosts = _agents.map(a => ({
        name: a.name,
        ip: (a.url || '').replace(/^https?:\/\//, '')
    }));
    if (typeof window.renderStandaloneHostPanels === 'function') {
        window.renderStandaloneHostPanels();
    }
}

async function _loadAgents() {
    await _loadAgentsNoPolling();
    // Start polling for each agent
    for (const agent of _agents) {
        _startPolling(agent);
    }
}

async function _loadAgentsNoPolling() {
    console.log('[Agents] _loadAgentsNoPolling start');
    try {
        const res = await fetch('/api/v1/agents');
        console.log('[Agents] fetch agents response:', res.status);
        if (!res.ok) return;
        const data = await res.json();
        _agents = data.agents || [];
        console.log('[Agents] agents loaded:', _agents.length);
    } catch (_) { /* silent */ }
    _renderMachines();
    try { _syncComputeHosts(); } catch (_) { /* host checkboxes may not be ready yet */ }
}

function _renderMachines() {
    const container = document.getElementById('pj-machines-container');
    if (!container) return;
    container.textContent = '';

    if (_agents.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'pj-machines-empty';
        empty.textContent = '尚未設定任何機器  ';
        const btn = document.createElement('button');
        btn.textContent = '+ 新增第一台';
        btn.addEventListener('click', () => toggleAddAgent());
        empty.appendChild(btn);
        container.appendChild(empty);
        return;
    }

    for (const agent of _agents) {
        const card = _createMachineCard(agent);
        container.appendChild(card);
    }
}

function _createMachineCard(agent) {
    const status = _agentStatus[agent.id] || {};
    const isOnline = status.online === true;
    const isSlow = status.slow === true;
    const data = status.data || {};

    const card = document.createElement('div');
    card.className = 'pj-machine-card';
    card.id = 'pj-agent-' + agent.id;
    if (!isOnline && status.online !== undefined) card.classList.add('pj-machine-offline');

    // Header: dot + name
    const header = document.createElement('div');
    header.className = 'pj-machine-header';

    const dot = document.createElement('div');
    dot.className = 'pj-machine-dot';
    if (status.online === undefined) dot.classList.add('pj-dot-unknown');
    else if (!isOnline) dot.classList.add('pj-dot-offline');
    else if (isSlow) dot.classList.add('pj-dot-slow');
    else dot.classList.add('pj-dot-online');
    header.appendChild(dot);

    const name = document.createElement('span');
    name.className = 'pj-machine-name';
    name.appendChild(_text(agent.name || agent.id));
    header.appendChild(name);

    card.appendChild(header);

    // Remove button
    const removeBtn = document.createElement('button');
    removeBtn.className = 'pj-machine-remove';
    removeBtn.textContent = '\u2715'; // ✕
    removeBtn.addEventListener('click', () => removeAgent(agent.id));
    card.appendChild(removeBtn);

    // Task info
    const taskDiv = document.createElement('div');
    taskDiv.className = 'pj-machine-task';

    if (!isOnline && status.online !== undefined) {
        const offText = document.createElement('span');
        offText.className = 'pj-machine-offline-text';
        offText.textContent = '無法連線';
        taskDiv.appendChild(offText);
    } else if (data.current_tasks && data.current_tasks.length > 0) {
        const t = data.current_tasks[0];
        const typeBadge = document.createElement('span');
        typeBadge.className = 'pj-machine-task-type';
        typeBadge.style.backgroundColor = TYPE_COLORS[t.task_type] || '#666';
        typeBadge.appendChild(_text(TYPE_LABELS[t.task_type] || t.task_type));
        taskDiv.appendChild(typeBadge);

        const taskName = document.createElement('span');
        taskName.className = 'pj-machine-task-name';
        taskName.appendChild(_text(t.project_name));
        taskDiv.appendChild(taskName);
    } else if (data.worker_busy) {
        taskDiv.appendChild(_text('執行中'));
    } else {
        const idle = document.createElement('span');
        idle.style.color = '#555';
        idle.textContent = '閒置';
        taskDiv.appendChild(idle);
    }
    card.appendChild(taskDiv);

    // CPU bar
    const cpuDiv = document.createElement('div');
    cpuDiv.className = 'pj-machine-cpu';

    if (!isOnline && status.online !== undefined) {
        // no CPU bar when offline
    } else {
        const cpuLabel = document.createElement('span');
        cpuLabel.className = 'pj-machine-cpu-label';
        cpuLabel.textContent = 'CPU';
        cpuDiv.appendChild(cpuLabel);

        const barWrap = document.createElement('div');
        barWrap.className = 'pj-machine-cpu-bar';

        const fill = document.createElement('div');
        fill.className = 'pj-machine-cpu-fill';
        const pct = data.cpu_percent || 0;
        fill.style.width = pct + '%';
        if (pct > 85) fill.style.backgroundColor = '#ef4444';
        else if (pct > 60) fill.style.backgroundColor = '#d48a04';
        else fill.style.backgroundColor = '#228b22';
        barWrap.appendChild(fill);
        cpuDiv.appendChild(barWrap);

        const cpuText = document.createElement('span');
        cpuText.className = 'pj-machine-cpu-text';
        cpuText.textContent = Math.round(pct) + '%';
        cpuDiv.appendChild(cpuText);
    }
    card.appendChild(cpuDiv);

    return card;
}

function _updateMachineCard(agentId) {
    const existing = document.getElementById('pj-agent-' + agentId);
    if (!existing) return;
    const agent = _agents.find(a => a.id === agentId);
    if (!agent) return;
    const newCard = _createMachineCard(agent);
    existing.replaceWith(newCard);
}

function _startPolling(agent) {
    if (_agentStatus[agent.id] && _agentStatus[agent.id].timerId) {
        clearTimeout(_agentStatus[agent.id].timerId);
    }
    if (!_agentStatus[agent.id]) {
        _agentStatus[agent.id] = { online: undefined, slow: false, data: {} };
    }
    _pollAgent(agent);
}

async function _pollAgent(agent) {
    if (!_pollingActive) {
        // Schedule retry when polling resumes
        const st = _agentStatus[agent.id];
        if (st) st.timerId = setTimeout(() => _pollAgent(agent), 5000);
        return;
    }

    const st = _agentStatus[agent.id];
    if (!st) return; // agent was removed

    // Use server-side proxy to poll agent health (avoids browser CORS / Private Network issues)
    const healthUrl = '/api/v1/agents/' + encodeURIComponent(agent.id) + '/health';

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    const startTime = Date.now();

    try {
        const res = await fetch(healthUrl, { signal: controller.signal });
        clearTimeout(timeout);
        const elapsed = Date.now() - startTime;

        if (res.ok) {
            const data = await res.json();
            if (data.status === 'offline') {
                st.online = false;
                st.slow = false;
                st.data = {};
            } else {
                st.online = true;
                st.slow = elapsed > 3000;
                st.data = data;
            }
        } else {
            st.online = false;
            st.slow = false;
            st.data = {};
        }
    } catch (err) {
        clearTimeout(timeout);
        st.online = false;
        st.slow = false;
        st.data = {};
    }

    _updateMachineCard(agent.id);

    // Schedule next poll (5s after completion)
    if (_agentStatus[agent.id]) {
        st.timerId = setTimeout(() => _pollAgent(agent), 5000);
    }
}

function _stopPolling(agentId) {
    const st = _agentStatus[agentId];
    if (st && st.timerId) {
        clearTimeout(st.timerId);
    }
    delete _agentStatus[agentId];
}

function pausePolling() { _pollingActive = false; }

function resumePolling() {
    if (_pollingActive) return;
    _pollingActive = true;
    // Re-trigger immediate poll for all agents
    for (const agent of _agents) {
        const st = _agentStatus[agent.id];
        if (st && st.timerId) clearTimeout(st.timerId);
        _pollAgent(agent);
    }
}

// ── Add / Remove Agent ──

function toggleAddAgent() {
    const form = document.getElementById('pj-add-agent-form');
    if (!form) return;
    form.style.display = form.style.display === 'none' ? 'flex' : 'none';
}

async function addAgent() {
    const nameInput = document.getElementById('pj-agent-name');
    const ipInput = document.getElementById('pj-agent-ip');
    const portInput = document.getElementById('pj-agent-port');

    const agentName = (nameInput ? nameInput.value.trim() : '');
    const agentIp = (ipInput ? ipInput.value.trim() : '');
    const agentPort = (portInput ? portInput.value.trim() : '8000') || '8000';

    if (!agentName || !agentIp) {
        alert('請填入名稱和 IP 位址');
        return;
    }

    const url = 'http://' + agentIp + ':' + agentPort;

    try {
        const res = await fetch('/api/v1/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: agentName, url }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.detail || '新增失敗');
            return;
        }
        const data = await res.json();
        const newAgent = data.agent;

        _agents.push(newAgent);
        _renderMachines();
        _syncComputeHosts();

        // Start polling for the new agent
        if (newAgent) _startPolling(newAgent);

        // Clear form and hide
        if (nameInput) nameInput.value = '';
        if (ipInput) ipInput.value = '';
        if (portInput) portInput.value = '8000';
        toggleAddAgent();
    } catch (_) { /* silent */ }
}

async function removeAgent(agentId) {
    const agent = _agents.find(a => a.id === agentId);
    const displayName = agent ? agent.name : agentId;
    if (!confirm('確定移除 ' + displayName + '？')) return;

    _stopPolling(agentId);

    try {
        const res = await fetch('/api/v1/agents/' + encodeURIComponent(agentId), {
            method: 'DELETE',
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.detail || '移除失敗');
            return;
        }
        _agents = _agents.filter(a => a.id !== agentId);
    } catch (_) { /* silent */ }

    _renderMachines();
    _syncComputeHosts();
}

// ══════════════════════════════════════════
//  Queue Panel
// ══════════════════════════════════════════

async function _loadQueue() {
    try {
        const res = await fetch('/api/v1/queue');
        if (!res.ok) return;
        _queueItems = await res.json();
        _queuePage = 0;
        _renderQueue();
        _updateBadges();
    } catch (_) { /* silent */ }
}

// Debounced version — coalesces rapid socket events into a single fetch
function _loadQueueDebounced() {
    if (_loadQueueTimer) clearTimeout(_loadQueueTimer);
    _loadQueueTimer = setTimeout(() => { _loadQueueTimer = null; _loadQueue(); }, 200);
}

function refreshQueue() { _loadQueue(); }

function _renderQueue() {
    const container = document.getElementById('pj-queue-container');
    if (!container) return;
    container.textContent = '';

    // Filter out running items (they show in "進行中")
    const waitingItems = _queueItems.filter(i => i.status !== 'running');

    if (waitingItems.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'pj-queue-empty';
        empty.textContent = '目前沒有等待中的任務';
        container.appendChild(empty);
        return;
    }

    const totalPages = Math.ceil(waitingItems.length / QUEUE_PAGE_SIZE);
    if (_queuePage >= totalPages) _queuePage = totalPages - 1;
    const start = _queuePage * QUEUE_PAGE_SIZE;
    const end = Math.min(start + QUEUE_PAGE_SIZE, waitingItems.length);

    for (let i = start; i < end; i++) {
        const row = _createQueueRow(waitingItems[i], i + 1);
        container.appendChild(row);
    }

    if (totalPages > 1) {
        _renderPaginationWidget(container, totalPages, _queuePage, (p) => {
            _queuePage = p; _renderQueue();
        });
    }
}

function _createQueueRow(item, num) {
    const isRunning = item.status === 'running';
    const row = document.createElement('div');
    row.className = 'pj-queue-row';
    row.id = 'pj-q-' + item.job_id;
    row.dataset.jobId = item.job_id;

    if (isRunning) {
        row.classList.add('pj-locked');
    } else {
        row.draggable = true;
        row.addEventListener('dragstart', _onDragStart);
        row.addEventListener('dragover', _onDragOver);
        row.addEventListener('dragleave', _onDragLeave);
        row.addEventListener('drop', _onDrop);
        row.addEventListener('dragend', _onDragEnd);
    }

    if (item.urgent) row.classList.add('pj-urgent');

    // Handle
    const handle = document.createElement('span');
    handle.className = 'pj-queue-handle';
    handle.textContent = '\u2807'; // ⠇
    row.appendChild(handle);

    // Number
    const numEl = document.createElement('span');
    numEl.className = 'pj-queue-number';
    numEl.textContent = String(num);
    row.appendChild(numEl);

    // Project name
    const proj = document.createElement('span');
    proj.className = 'pj-queue-project';
    proj.appendChild(_text(item.project_name));
    row.appendChild(proj);

    // Task type badge
    const typeBadge = document.createElement('span');
    typeBadge.className = 'pj-queue-type';
    const color = TYPE_COLORS[item.task_type] || '#666';
    typeBadge.style.backgroundColor = color;
    typeBadge.appendChild(_text(TYPE_LABELS[item.task_type] || item.task_type));
    row.appendChild(typeBadge);

    // Urgent tag
    if (item.urgent) {
        const urgTag = document.createElement('span');
        urgTag.className = 'pj-queue-urgent-tag';
        urgTag.textContent = '緊急';
        row.appendChild(urgTag);
    }

    // Status (for running)
    if (isRunning) {
        const stBadge = document.createElement('span');
        stBadge.className = 'pj-status-badge pj-status-running';
        stBadge.style.fontSize = '10px';
        stBadge.appendChild(_text('執行中'));
        row.appendChild(stBadge);
    }

    // Actions (only for queued/waiting)
    if (!isRunning) {
        const actions = document.createElement('div');
        actions.className = 'pj-queue-actions';

        const btnJump = document.createElement('button');
        btnJump.className = 'pj-btn pj-btn-pause';
        btnJump.textContent = '插隊';
        btnJump.addEventListener('click', (e) => { e.stopPropagation(); jumpToFront(item.job_id); });
        actions.appendChild(btnJump);

        const btnUrg = document.createElement('button');
        btnUrg.className = 'pj-btn';
        if (item.urgent) {
            btnUrg.className += ' pj-btn-stop';
            btnUrg.textContent = '取消緊急';
            btnUrg.addEventListener('click', (e) => { e.stopPropagation(); unmarkUrgent(item.job_id); });
        } else {
            btnUrg.className += ' pj-btn-close';
            btnUrg.textContent = '緊急';
            btnUrg.addEventListener('click', (e) => { e.stopPropagation(); markUrgent(item.job_id); });
        }
        actions.appendChild(btnUrg);
        row.appendChild(actions);
    }

    return row;
}

// ── Drag and Drop ──

let _dragJobId = null;

function _onDragStart(e) {
    _dragJobId = e.currentTarget.dataset.jobId;
    _isDragging = true;
    e.currentTarget.classList.add('pj-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _dragJobId);
}

function _onDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const row = e.currentTarget;
    if (row.classList.contains('pj-locked')) return;
    row.classList.add('pj-drag-over');
}

function _onDragLeave(e) {
    e.currentTarget.classList.remove('pj-drag-over');
}

function _onDrop(e) {
    e.preventDefault();
    const targetRow = e.currentTarget;
    targetRow.classList.remove('pj-drag-over');
    if (targetRow.classList.contains('pj-locked')) return;

    const container = document.getElementById('pj-queue-container');
    if (!container || !_dragJobId) return;

    const draggedRow = document.getElementById('pj-q-' + _dragJobId);
    if (!draggedRow || draggedRow === targetRow) return;

    // Insert dragged row before or after target based on position
    const targetRect = targetRow.getBoundingClientRect();
    const midY = targetRect.top + targetRect.height / 2;
    if (e.clientY < midY) {
        container.insertBefore(draggedRow, targetRow);
    } else {
        container.insertBefore(draggedRow, targetRow.nextSibling);
    }

    // Optimistic: sync order to backend
    _syncQueueOrder();
}

function _onDragEnd(e) {
    e.currentTarget.classList.remove('pj-dragging');
    _isDragging = false;
    _dragJobId = null;

    // Remove all drag-over indicators
    const container = document.getElementById('pj-queue-container');
    if (container) {
        container.querySelectorAll('.pj-drag-over').forEach(el => el.classList.remove('pj-drag-over'));
    }

    // Apply any pending queue update from socket
    if (_pendingQueueUpdate) {
        _queueItems = _pendingQueueUpdate.queue || [];
        _renderQueue();
        _pendingQueueUpdate = null;
    }
}

async function _syncQueueOrder() {
    const container = document.getElementById('pj-queue-container');
    if (!container) return;

    const rows = container.querySelectorAll('.pj-queue-row:not(.pj-locked)');
    const orderedIds = [];
    rows.forEach(row => orderedIds.push(row.dataset.jobId));

    // Renumber visible rows
    let n = 0;
    container.querySelectorAll('.pj-queue-row').forEach(row => {
        n++;
        const numEl = row.querySelector('.pj-queue-number');
        if (numEl) numEl.textContent = String(n);
    });

    try {
        const res = await fetch('/api/v1/queue/reorder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ordered_job_ids: orderedIds }),
        });
        if (res.ok) {
            _queueItems = await res.json();
        }
    } catch (_) { /* silent — optimistic update already applied */ }
}

// ── Jump to Front / Urgent ──

async function jumpToFront(jobId) {
    // Move jobId to front of queued items, then reorder
    const queuedIds = _queueItems
        .filter(i => i.status !== 'running')
        .map(i => i.job_id);
    const idx = queuedIds.indexOf(jobId);
    if (idx > 0) {
        queuedIds.splice(idx, 1);
        queuedIds.unshift(jobId);
    }
    try {
        const res = await fetch('/api/v1/queue/reorder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ordered_job_ids: queuedIds }),
        });
        if (res.ok) {
            _queueItems = await res.json();
            _renderQueue();
        }
    } catch (_) { /* silent */ }
}

async function markUrgent(jobId) {
    try {
        const res = await fetch('/api/v1/queue/' + encodeURIComponent(jobId) + '/urgent', {
            method: 'POST',
        });
        if (res.ok) {
            _queueItems = await res.json();
            _renderQueue();
        }
    } catch (_) { /* silent */ }
}

async function unmarkUrgent(jobId) {
    try {
        const res = await fetch('/api/v1/queue/' + encodeURIComponent(jobId) + '/urgent', {
            method: 'DELETE',
        });
        if (res.ok) {
            _queueItems = await res.json();
            _renderQueue();
        }
    } catch (_) { /* silent */ }
}

// ── Queue Socket Handler ──

function _onQueueUpdated(data) {
    if (_isDragging) {
        _pendingQueueUpdate = data;
        return;
    }
    _queueItems = (data && data.queue) || [];
    _queuePage = 0;
    _renderQueue();
    _updateBadges();
}

// ══════════════════════════════════════════
//  History Rendering
// ══════════════════════════════════════════
function _renderHistoryItem(container, entry, prepend) {
    const row = document.createElement('div');
    row.className = 'pj-history-row';

    // Icon
    const icon = document.createElement('span');
    icon.className = 'pj-history-icon';
    const isOk = entry.status === 'done';
    icon.textContent = isOk ? '✓' : '✕';
    icon.style.color = isOk ? '#22c55e' : '#ef4444';
    row.appendChild(icon);

    // Time (HH:MM)
    const time = document.createElement('span');
    time.className = 'pj-history-time';
    const ts = entry.finished_at || entry.created_at || '';
    if (ts) {
        const d = new Date(ts);
        time.textContent = String(d.getHours()).padStart(2, '0') + ':' +
            String(d.getMinutes()).padStart(2, '0');
    }
    row.appendChild(time);

    // Task type
    const type = document.createElement('span');
    type.className = 'pj-history-type';
    type.appendChild(_text(TYPE_LABELS[entry.task_type] || entry.task_type || ''));
    row.appendChild(type);

    // Project name
    const proj = document.createElement('span');
    proj.className = 'pj-history-project';
    proj.appendChild(_text(entry.project_name || '--'));
    row.appendChild(proj);

    // Duration
    const dur = document.createElement('span');
    dur.className = 'pj-history-duration';
    const secs = _calcDuration(entry.started_at, entry.finished_at);
    dur.textContent = secs != null ? _fmtDuration(secs) : '--';
    row.appendChild(dur);

    // Log button (only if log_file exists)
    if (entry.log_file) {
        const logBtn = document.createElement('button');
        logBtn.className = 'pj-log-btn';
        logBtn.textContent = 'Log';
        logBtn.addEventListener('click', () => _showLogModal(entry));
        row.appendChild(logBtn);
    }

    if (prepend) {
        container.insertBefore(row, container.firstChild);
    } else {
        container.appendChild(row);
    }

    // Error detail on next line
    if (!isOk && entry.error_detail) {
        const errRow = document.createElement('div');
        errRow.className = 'pj-history-error';
        errRow.appendChild(_text(entry.error_detail));
        if (prepend) {
            // Insert error after the row (which is now the first child)
            row.after(errRow);
        } else {
            container.appendChild(errRow);
        }
    }
}


// ══════════════════════════════════════════
//  Log Viewer Modal
// ══════════════════════════════════════════

async function _showLogModal(entry) {
    // Remove existing modal if any
    document.querySelector('.pj-log-overlay')?.remove();

    // Create overlay
    const overlay = document.createElement('div');
    overlay.className = 'pj-log-overlay';
    const _closeModal = () => { overlay.remove(); document.removeEventListener('keydown', _onEsc); };
    overlay.addEventListener('click', (e) => { if (e.target === overlay) _closeModal(); });
    const _onEsc = (e) => { if (e.key === 'Escape') _closeModal(); };
    document.addEventListener('keydown', _onEsc);

    const modal = document.createElement('div');
    modal.className = 'pj-log-modal';

    // Header
    const header = document.createElement('div');
    header.className = 'pj-log-modal-header';
    const title = document.createElement('span');
    title.appendChild(_text((TYPE_LABELS[entry.task_type] || entry.task_type) + ' — ' + (entry.project_name || entry.job_id)));
    header.appendChild(title);
    const closeBtn = document.createElement('button');
    closeBtn.className = 'pj-log-modal-close';
    closeBtn.textContent = '\u2715';
    closeBtn.addEventListener('click', () => _closeModal());
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // Body
    const body = document.createElement('div');
    body.className = 'pj-log-modal-body';
    body.textContent = '載入中…';
    modal.appendChild(body);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Fetch log
    try {
        const res = await fetch('/api/v1/job_history/' + encodeURIComponent(entry.job_id) + '/log');
        if (!res.ok) { body.textContent = '載入失敗（HTTP ' + res.status + '）'; return; }
        const data = await res.json();
        body.textContent = data.log || data.error || '（空白）';
    } catch (_) {
        body.textContent = '載入失敗';
    }
}

// ══════════════════════════════════════════
//  Scheduled Tasks
// ══════════════════════════════════════════

let _schedules = [];

async function _loadSchedules() {
    try {
        const res = await fetch('/api/v1/schedules');
        if (res.ok) {
            _schedules = await res.json();
        } else {
            _schedules = [];
        }
    } catch {
        _schedules = [];
    }
    _renderSchedules();
}

function _renderSchedules() {
    const container = document.getElementById('pj-schedules-container');
    if (!container) return;
    container.textContent = '';

    if (_schedules.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'pj-schedules-empty';
        empty.textContent = '尚未設定任何排程';
        container.appendChild(empty);
        _updateBadges();
        return;
    }

    for (const sch of _schedules) {
        container.appendChild(_createScheduleRow(sch));
    }
    _updateBadges();
}

function _createScheduleRow(sch) {
    const wrapper = document.createElement('div');
    wrapper.className = 'pj-schedule-wrapper';

    const row = document.createElement('div');
    row.className = 'pj-schedule-row';
    if (!sch.enabled) row.classList.add('pj-schedule-disabled');

    // Expand indicator
    const arrow = document.createElement('span');
    arrow.className = 'pj-schedule-arrow';
    arrow.textContent = '▶';
    row.appendChild(arrow);

    // Task type badge
    const typeBadge = document.createElement('span');
    typeBadge.className = 'pj-queue-type';
    const color = TYPE_COLORS[sch.task_type] || '#666';
    typeBadge.style.backgroundColor = color;
    typeBadge.appendChild(_text(TYPE_LABELS[sch.task_type] || sch.task_type));
    row.appendChild(typeBadge);

    // Name
    const name = document.createElement('span');
    name.className = 'pj-schedule-name';
    name.appendChild(_text(sch.name));
    row.appendChild(name);

    // Schedule time display (clickable to edit)
    const timeEl = document.createElement('span');
    timeEl.className = 'pj-schedule-time pj-schedule-time-editable';
    timeEl.title = '點擊修改時間';
    if (sch.run_at) {
        const d = new Date(sch.run_at);
        timeEl.appendChild(_text(_fmtShortDateTime(d)));
    } else if (sch.cron) {
        timeEl.appendChild(_text(_cronToHuman(sch.cron)));
    }
    timeEl.addEventListener('click', (e) => {
        e.stopPropagation();
        _editScheduleTime(sch, timeEl);
    });
    row.appendChild(timeEl);

    // Status info
    const statusEl = document.createElement('span');
    statusEl.className = 'pj-schedule-next';
    if (!sch.enabled && sch.last_run) {
        const d = new Date(sch.last_run);
        statusEl.appendChild(_text('已執行: ' + _fmtShortDateTime(d)));
    } else if (sch.next_run && sch.enabled) {
        const nd = new Date(sch.next_run);
        if (nd > new Date()) {
            statusEl.appendChild(_text('等待中'));
        }
    }
    row.appendChild(statusEl);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'pj-schedule-actions';

    // Toggle switch
    const toggle = document.createElement('button');
    toggle.className = 'pj-schedule-toggle';
    if (sch.enabled) toggle.classList.add('pj-toggle-on');
    toggle.title = sch.enabled ? '停用' : '啟用';
    toggle.addEventListener('click', (e) => { e.stopPropagation(); toggleSchedule(sch.schedule_id, !sch.enabled); });
    actions.appendChild(toggle);

    // Delete button
    const del = document.createElement('button');
    del.className = 'pj-schedule-delete';
    del.textContent = '\u2715'; // ✕
    del.title = '刪除排程';
    del.addEventListener('click', (e) => { e.stopPropagation(); deleteSchedule(sch.schedule_id); });
    actions.appendChild(del);

    row.appendChild(actions);

    // Detail panel (hidden by default)
    const detail = document.createElement('div');
    detail.className = 'pj-schedule-detail';
    detail.style.display = 'none';
    detail.appendChild(_buildScheduleDetail(sch));

    // Toggle detail on row click
    row.addEventListener('click', () => {
        const isOpen = detail.style.display !== 'none';
        detail.style.display = isOpen ? 'none' : '';
        arrow.textContent = isOpen ? '▶' : '▼';
        row.classList.toggle('pj-schedule-row-expanded', !isOpen);
    });
    row.style.cursor = 'pointer';

    wrapper.appendChild(row);
    wrapper.appendChild(detail);
    return wrapper;
}

function _buildScheduleDetail(sch) {
    const container = document.createElement('div');
    const r = sch.request || {};

    // Render fields as key-value pairs
    const fields = _getDetailFields(sch.task_type, r);
    for (const [label, value] of fields) {
        const line = document.createElement('div');
        line.className = 'pj-schedule-detail-line';

        const labelEl = document.createElement('span');
        labelEl.className = 'pj-schedule-detail-label';
        labelEl.appendChild(_text(label));
        line.appendChild(labelEl);

        const valEl = document.createElement('span');
        valEl.className = 'pj-schedule-detail-value';
        valEl.appendChild(_text(value));
        line.appendChild(valEl);

        container.appendChild(line);
    }

    if (fields.length === 0) {
        const empty = document.createElement('span');
        empty.className = 'pj-schedule-detail-value';
        empty.appendChild(_text('無設定資料'));
        container.appendChild(empty);
    }

    return container;
}

function _getDetailFields(taskType, r) {
    const fields = [];
    const _bool = (v) => v ? '✓' : '✕';

    switch (taskType) {
        case 'backup':
            if (r.project_name) fields.push(['專案名稱', r.project_name]);
            if (r.local_root) fields.push(['本機路徑', r.local_root]);
            if (r.nas_root) fields.push(['NAS 路徑', r.nas_root]);
            if (r.proxy_root) fields.push(['Proxy 路徑', r.proxy_root]);
            if (r.cards && r.cards.length) {
                fields.push(['記憶卡', r.cards.map(c => (c[0] || '') + ' → ' + (c[1] || '')).join('、')]);
            }
            fields.push(['Hash 校驗', _bool(r.do_hash)]);
            fields.push(['轉檔', _bool(r.do_transcode)]);
            fields.push(['串接', _bool(r.do_concat)]);
            fields.push(['報表', _bool(r.do_report)]);
            if (r.do_concat) {
                fields.push(['串接解析度', r.concat_resolution || '720P']);
                fields.push(['串接編碼', r.concat_codec || 'H.264']);
            }
            break;
        case 'transcode':
            if (r.sources && r.sources.length) fields.push(['來源', r.sources.join('、')]);
            if (r.dest_dir) fields.push(['輸出目錄', r.dest_dir]);
            break;
        case 'concat':
            if (r.sources && r.sources.length) fields.push(['來源', r.sources.join('、')]);
            if (r.dest_dir) fields.push(['輸出目錄', r.dest_dir]);
            fields.push(['解析度', r.resolution || '1080P']);
            fields.push(['編碼', r.codec || 'ProRes']);
            fields.push(['時間碼', _bool(r.burn_timecode)]);
            fields.push(['檔名', _bool(r.burn_filename)]);
            if (r.custom_name) fields.push(['自訂檔名', r.custom_name]);
            break;
        case 'verify':
            if (r.pairs && r.pairs.length) {
                fields.push(['比對組', r.pairs.map(p => (p[0] || '') + ' ↔ ' + (p[1] || '')).join('、')]);
            }
            fields.push(['模式', r.mode || 'quick']);
            break;
        case 'transcribe':
            if (r.sources && r.sources.length) fields.push(['來源', r.sources.join('、')]);
            if (r.dest_dir) fields.push(['輸出目錄', r.dest_dir]);
            fields.push(['模型', r.model_size || 'turbo']);
            fields.push(['SRT', _bool(r.output_srt)]);
            fields.push(['TXT', _bool(r.output_txt)]);
            fields.push(['WAV', _bool(r.output_wav)]);
            fields.push(['Proxy', _bool(r.generate_proxy)]);
            break;
        case 'report':
            if (r.source_dir) fields.push(['來源目錄', r.source_dir]);
            if (r.output_dir) fields.push(['輸出目錄', r.output_dir]);
            fields.push(['縮圖條', _bool(r.do_filmstrip)]);
            fields.push(['技術規格', _bool(r.do_techspec)]);
            fields.push(['Hash', _bool(r.do_hash)]);
            break;
        case 'tts':
            if (r.voice) fields.push(['聲音', r.voice]);
            if (r.text) fields.push(['文字', r.text.length > 50 ? r.text.substring(0, 50) + '...' : r.text]);
            if (r.output_dir) fields.push(['輸出目錄', r.output_dir]);
            if (r.output_name) fields.push(['輸出檔名', r.output_name]);
            break;
        case 'clone':
            if (r.reference_audio) fields.push(['參考音訊', r.reference_audio]);
            if (r.text) fields.push(['文字', r.text.length > 50 ? r.text.substring(0, 50) + '...' : r.text]);
            if (r.output_dir) fields.push(['輸出目錄', r.output_dir]);
            if (r.output_name) fields.push(['輸出檔名', r.output_name]);
            break;
        default:
            // Generic fallback: show all keys
            for (const [k, v] of Object.entries(r)) {
                if (k === 'task_type' || k === 'job_id') continue;
                fields.push([k, typeof v === 'object' ? JSON.stringify(v) : String(v)]);
            }
    }

    // Show compute_hosts if present
    if (r.compute_hosts && r.compute_hosts.length) {
        fields.push(['處理主機', r.compute_hosts.map(h => h.name || h.ip || h).join('、')]);
    }

    return fields;
}

function _fmtShortDateTime(d) {
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return mm + '/' + dd + ' ' + hh + ':' + mi;
}

function _cronToHuman(cron) {
    if (!cron) return cron;
    const parts = cron.trim().split(/\s+/);
    if (parts.length < 5) return cron;
    const [min, hour, dom, mon, dow] = parts;

    const time = hour.padStart(2, '0') + ':' + min.padStart(2, '0');

    // Daily
    if (dom === '*' && mon === '*' && dow === '*') {
        return '每天 ' + time;
    }
    // Weekdays
    if (dom === '*' && mon === '*' && dow === '1-5') {
        return '週一至週五 ' + time;
    }
    // Specific weekday
    const dayNames = { '0': '日', '1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六', '7': '日' };
    if (dom === '*' && mon === '*' && dayNames[dow]) {
        return '每週' + dayNames[dow] + ' ' + time;
    }
    // Specific day of month
    if (mon === '*' && dow === '*' && /^\d+$/.test(dom)) {
        return '每月 ' + dom + ' 日 ' + time;
    }
    // Fallback — show raw cron with prefix so users know it's technical
    return 'Cron: ' + cron;
}

function _editScheduleTime(sch, timeEl) {
    // Already editing?
    if (timeEl.querySelector('select')) return;

    const origHtml = timeEl.innerHTML;
    timeEl.textContent = '';
    timeEl.classList.add('pj-schedule-time-editing');

    // Date input
    const dateIn = document.createElement('input');
    dateIn.type = 'date';
    dateIn.className = 'pj-sch-edit-date';
    if (sch.run_at) {
        dateIn.value = sch.run_at.slice(0, 10);
    } else {
        dateIn.value = _todayStr();
    }

    // Hour select (24h)
    const hourSel = document.createElement('select');
    hourSel.className = 'pj-sch-edit-select';
    for (let h = 0; h < 24; h++) {
        const o = document.createElement('option');
        o.value = o.textContent = String(h).padStart(2, '0');
        hourSel.appendChild(o);
    }
    const sep = document.createElement('span');
    sep.textContent = ':';
    sep.style.color = '#888';

    // Minute select
    const minSel = document.createElement('select');
    minSel.className = 'pj-sch-edit-select';
    for (const m of ['00', '15', '30', '45']) {
        const o = document.createElement('option');
        o.value = o.textContent = m;
        minSel.appendChild(o);
    }

    if (sch.run_at) {
        hourSel.value = sch.run_at.slice(11, 13);
        minSel.value = sch.run_at.slice(14, 16);
        // If minute not in 15-min options, add it
        if (!minSel.value) {
            const extra = document.createElement('option');
            extra.value = extra.textContent = sch.run_at.slice(14, 16);
            minSel.insertBefore(extra, minSel.firstChild);
            minSel.value = extra.value;
        }
    } else {
        hourSel.value = '02';
        minSel.value = '00';
    }

    // Confirm button
    const okBtn = document.createElement('button');
    okBtn.className = 'pj-sch-edit-ok';
    okBtn.textContent = '✓';
    okBtn.title = '確認';
    okBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const newRunAt = dateIn.value + 'T' + hourSel.value + ':' + minSel.value + ':00';
        const dt = new Date(newRunAt);
        if (dt <= new Date()) {
            alert('排程時間必須在未來');
            return;
        }
        try {
            const res = await fetch('/api/v1/schedules/' + sch.schedule_id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ run_at: newRunAt }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert('更新失敗: ' + (err.detail || res.statusText));
                return;
            }
            await _loadSchedules();
        } catch (ex) {
            alert('更新失敗: ' + ex.message);
        }
    });

    // Cancel button
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'pj-sch-edit-cancel';
    cancelBtn.textContent = '✕';
    cancelBtn.title = '取消';
    cancelBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        timeEl.classList.remove('pj-schedule-time-editing');
        timeEl.innerHTML = origHtml;
    });

    timeEl.appendChild(dateIn);
    timeEl.appendChild(hourSel);
    timeEl.appendChild(sep);
    timeEl.appendChild(minSel);
    timeEl.appendChild(okBtn);
    timeEl.appendChild(cancelBtn);

    dateIn.focus();
}

async function toggleSchedule(scheduleId, enabled) {
    try {
        const res = await fetch('/api/v1/schedules/' + scheduleId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        if (res.ok) {
            await _loadSchedules();
        }
    } catch {
        // silent
    }
}

async function deleteSchedule(scheduleId) {
    if (!confirm('確定要刪除此排程？')) return;
    try {
        const res = await fetch('/api/v1/schedules/' + scheduleId, { method: 'DELETE' });
        if (res.ok) {
            await _loadSchedules();
        }
    } catch {
        // silent
    }
}
