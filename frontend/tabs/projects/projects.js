// ── Projects Overview Tab ──
// ES Module — loaded dynamically by app.js loadTabs()

// ── Constants ──
const TYPE_COLORS = {
    backup: '#3b82f6', transcode: '#d48a04', concat: '#228b22',
    verify: '#06b6d4', transcribe: '#a855f7', report: '#7c3aed',
};
const TYPE_LABELS = {
    backup: '備份', transcode: '轉檔', concat: '串接',
    verify: '驗證', transcribe: '轉錄', report: '報表',
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

// History pagination state
const HISTORY_PAGE_SIZE = 5;
let _historyAllJobs = [];
let _historyPage = 0;

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
    } catch (_) { /* silent */ }
}

async function saveLimits(key, val) {
    const numVal = parseInt(val, 10);
    if (!numVal || numVal < 1) return;
    try {
        const r = await fetch('/api/settings/load');
        if (!r.ok) return;
        const settings = await r.json();
        if (!settings.concurrency) settings.concurrency = {};
        settings.concurrency[key] = numVal;
        await fetch('/api/settings/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });
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
        const res = await fetch('/api/v1/job_history?date=' + encodeURIComponent(date) + '&limit=100');
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
        _renderPagination(container, totalPages);
    }
}

function _renderPagination(container, totalPages) {
    const wrap = document.createElement('div');
    wrap.className = 'pj-pagination';

    // Left arrow
    const left = document.createElement('span');
    left.className = 'pj-page-arrow' + (_historyPage === 0 ? ' disabled' : '');
    left.textContent = '\u2039';
    left.addEventListener('click', () => {
        if (_historyPage > 0) { _historyPage--; _renderHistoryPage(); }
    });
    wrap.appendChild(left);

    // Dots
    for (let i = 0; i < totalPages; i++) {
        const dot = document.createElement('span');
        dot.className = 'pj-page-dot' + (i === _historyPage ? ' active' : '');
        dot.addEventListener('click', () => { _historyPage = i; _renderHistoryPage(); });
        wrap.appendChild(dot);
    }

    // Right arrow
    const right = document.createElement('span');
    right.className = 'pj-page-arrow' + (_historyPage === totalPages - 1 ? ' disabled' : '');
    right.textContent = '\u203a';
    right.addEventListener('click', () => {
        if (_historyPage < totalPages - 1) { _historyPage++; _renderHistoryPage(); }
    });
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
    let running = 0, queued = 0;
    for (const info of Object.values(_cards)) {
        if (info.status === 'running' || info.status === 'paused') running++;
        else if (info.status === 'queued' || info.status === 'waiting') queued++;
    }

    const elRunning = document.getElementById('pj-count-running');
    const elQueued = document.getElementById('pj-count-queued');
    const elDone = document.getElementById('pj-count-done');

    if (elRunning) elRunning.textContent = String(running);
    if (elQueued) elQueued.textContent = String(queued);
    if (elDone) elDone.textContent = String(_todayDoneCount);
}

// ══════════════════════════════════════════
//  Socket Event Handlers
// ══════════════════════════════════════════
function _onJobQueued(data) {
    if (!data || !data.job_id) return;
    if (_cards[data.job_id]) return; // already exists
    _insertCard({
        job_id: data.job_id,
        project_name: data.project_name || '',
        task_type: data.task_type || 'backup',
        status: 'queued',
    }, true);
    _updateBadges();
}

function _onTaskStatus(data) {
    if (!data || !data.job_id) return;
    const jobId = data.job_id;
    const status = data.status;

    // Create card if we don't have it (e.g., page opened after job was queued)
    if (!_cards[jobId] && status !== 'done' && status !== 'error' && status !== 'cancelled') {
        _insertCard({
            job_id: jobId,
            project_name: data.project_name || '',
            task_type: data.task_type || 'backup',
            status: status,
        }, true);
    }

    if (_cards[jobId]) {
        _updateCardStatus(jobId, status);
    }

    // On done/error, write to history and update history list if viewing today
    if (status === 'done' || status === 'error' || status === 'cancelled') {
        _todayDoneCount++;
        _updateBadges();

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

async function _loadAgents() {
    await _loadAgentsNoPolling();
    // Start polling for each agent
    for (const agent of _agents) {
        _startPolling(agent);
    }
}

async function _loadAgentsNoPolling() {
    try {
        const res = await fetch('/api/settings/load');
        if (!res.ok) return;
        const settings = await res.json();
        _agents = settings.agents || [];
    } catch (_) { /* silent */ }
    _renderMachines();
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4000);
    const startTime = Date.now();

    try {
        const res = await fetch(agent.url + '/api/v1/health', { signal: controller.signal });
        clearTimeout(timeout);
        const elapsed = Date.now() - startTime;

        if (res.ok) {
            const data = await res.json();
            st.online = true;
            st.slow = elapsed > 3000;
            st.data = data;
        } else {
            st.online = false;
            st.slow = false;
            st.data = {};
        }
    } catch (_) {
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

    const id = agentName.toLowerCase().replace(/[^a-z0-9]/g, '-');
    const url = 'http://' + agentIp + ':' + agentPort;

    try {
        const res = await fetch('/api/settings/load');
        if (!res.ok) return;
        const settings = await res.json();
        if (!settings.agents) settings.agents = [];

        // Check for duplicate
        if (settings.agents.some(a => a.id === id)) {
            alert('已存在相同名稱的機器');
            return;
        }

        settings.agents.push({ id, name: agentName, url });
        await fetch('/api/settings/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });

        _agents = settings.agents;
        _renderMachines();

        // Start polling for the new agent
        const newAgent = _agents.find(a => a.id === id);
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
        const res = await fetch('/api/settings/load');
        if (!res.ok) return;
        const settings = await res.json();
        settings.agents = (settings.agents || []).filter(a => a.id !== agentId);
        await fetch('/api/settings/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });
        _agents = settings.agents;
    } catch (_) { /* silent */ }

    _renderMachines();
}

// ══════════════════════════════════════════
//  Queue Panel
// ══════════════════════════════════════════

async function _loadQueue() {
    try {
        const res = await fetch('/api/v1/queue');
        if (!res.ok) return;
        _queueItems = await res.json();
        _renderQueue();
    } catch (_) { /* silent */ }
}

function refreshQueue() { _loadQueue(); }

function _renderQueue() {
    const container = document.getElementById('pj-queue-container');
    if (!container) return;
    container.textContent = '';

    if (_queueItems.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'pj-queue-empty';
        empty.textContent = '目前沒有排隊中的任務';
        container.appendChild(empty);
        return;
    }

    let idx = 0;
    for (const item of _queueItems) {
        idx++;
        const row = _createQueueRow(item, idx);
        container.appendChild(row);
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
