// ── Agent Cards & Polling ──
// Extracted from projects.js — handles machine card rendering & health polling
// Uses window.* late binding to avoid circular imports with agent-update.js

import { _updatingAgents } from './agent-update.js';

// ── Module State ──
export let _agents = [];           // from settings.json
export let _agentStatus = {};      // id → { online, slow, data, timerId }
let _pollingActive = true;         // paused when tab not visible

// ── Constants (imported via window from projects.js) ──
// window.TYPE_COLORS, window.TYPE_LABELS — from projects.js

// ── Shared helpers (imported from projects.js to avoid duplication) ──
import { _text, _isNewer } from './projects.js';

function _syncComputeHosts() {
    window._computeHosts = _agents.map(a => ({
        name: a.name,
        ip: (a.url || '').replace(/^https?:\/\//, '')
    }));
    if (typeof window.renderStandaloneHostPanels === 'function') {
        window.renderStandaloneHostPanels();
    }
}

export async function _loadAgents() {
    await _loadAgentsNoPolling();
    // Start polling for each agent
    for (const agent of _agents) {
        _startPolling(agent);
    }
}

export async function _loadAgentsNoPolling() {
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

export function _renderMachines() {
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
    const TYPE_COLORS = window.TYPE_COLORS || {};
    const TYPE_LABELS = window.TYPE_LABELS || {};
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

    // Version row (separate line)
    const agentVersion = data.version || '';
    const updating = _updatingAgents[agent.id];
    const masterVersion = window._masterVersion || '';
    const versionRow = document.createElement('div');
    versionRow.className = 'pj-machine-version-row';
    if (updating) {
        const updBadge = document.createElement('span');
        updBadge.className = 'pj-version-badge pj-version-updating';
        const PHASE_TEXT = { downloading: '下載中', installing: '安裝套件', extracting: '解壓中',
            restarting: '重啟中', triggering: '觸發中', updating: '更新中' };
        updBadge.textContent = `⏳ ${PHASE_TEXT[updating.phase] || updating.phase} ${updating.pct || 0}%`;
        versionRow.appendChild(updBadge);
    } else if (agentVersion) {
        const vBadge = document.createElement('span');
        vBadge.className = 'pj-version-badge';
        vBadge.textContent = 'v' + agentVersion;
        if (masterVersion && _isNewer(masterVersion, agentVersion)) {
            vBadge.classList.add('pj-version-outdated');
            vBadge.textContent += ' ⬆';
            versionRow.appendChild(vBadge);
            if (window._isAdmin) {
                const updBtn = document.createElement('button');
                updBtn.className = 'pj-btn-inline-update';
                updBtn.textContent = '更新';
                updBtn.addEventListener('click', (e) => { e.stopPropagation(); window._triggerAgentUpdate(agent.id); });
                versionRow.appendChild(updBtn);
            }
        } else {
            versionRow.appendChild(vBadge);
        }
    }
    card.appendChild(versionRow);

    // Edit + Remove buttons (admin only)
    if (window._isAdmin) {
        const editBtn = document.createElement('button');
        editBtn.className = 'pj-machine-edit';
        editBtn.textContent = '\u270E'; // ✎
        editBtn.title = '編輯名稱 / IP';
        editBtn.addEventListener('click', (e) => { e.stopPropagation(); _showEditModal(agent); });
        card.appendChild(editBtn);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'pj-machine-remove';
        removeBtn.textContent = '\u2715'; // ✕
        removeBtn.addEventListener('click', () => removeAgent(agent.id));
        card.appendChild(removeBtn);
    }

    // Update progress bar (shown during update)
    if (updating && updating.pct > 0) {
        const updDiv = document.createElement('div');
        updDiv.className = 'pj-machine-update-bar';
        const updFill = document.createElement('div');
        updFill.className = 'pj-machine-update-fill';
        updFill.style.width = updating.pct + '%';
        updDiv.appendChild(updFill);
        card.appendChild(updDiv);
    }

    // Task info
    const taskDiv = document.createElement('div');
    taskDiv.className = 'pj-machine-task';

    if (updating) {
        const updText = document.createElement('span');
        updText.style.color = '#f59e0b';
        updText.textContent = updating.detail || '更新進行中...';
        taskDiv.appendChild(updText);
    } else if (!isOnline && status.online !== undefined) {
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

export function _updateMachineCard(agentId) {
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
    // Jitter 0-2s — without it, all N agents poll at once and the 6-per-origin
    // browser concurrency limit serializes them. When many agents are dead,
    // healthy ones (incl. master self-poll) wait behind dead timeouts and get
    // marked slow (>3s elapsed) → orange dot.
    const jitter = Math.floor(Math.random() * 2000);
    _agentStatus[agent.id].timerId = setTimeout(() => _pollAgent(agent), jitter);
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
    window._updateBatchButton();

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

export function pausePolling() { _pollingActive = false; }

export function resumePolling() {
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

export function toggleAddAgent() {
    const form = document.getElementById('pj-add-agent-form');
    if (!form) return;
    form.style.display = form.style.display === 'none' ? 'flex' : 'none';
}

export async function addAgent() {
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

export async function removeAgent(agentId) {
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

// ── Edit Agent Modal ──

function _showEditModal(agent) {
    // Parse IP and port from URL
    const urlMatch = (agent.url || '').match(/^https?:\/\/([^:\/]+)(?::(\d+))?/);
    const currentIp = urlMatch ? urlMatch[1] : '';
    const currentPort = urlMatch ? (urlMatch[2] || '8000') : '8000';

    const overlay = document.createElement('div');
    overlay.className = 'pj-edit-overlay';
    overlay.innerHTML = `
        <div class="pj-edit-modal">
            <h3>編輯機器 — ${agent.name}</h3>
            <label>名稱</label>
            <input id="pj-edit-name" value="${agent.name || ''}" />
            <label>IP 位址</label>
            <input id="pj-edit-ip" value="${currentIp}" />
            <label>Port</label>
            <input id="pj-edit-port" value="${currentPort}" />
            <div class="pj-edit-actions">
                <button class="pj-edit-cancel">取消</button>
                <button class="pj-edit-save">儲存</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.querySelector('.pj-edit-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    overlay.querySelector('.pj-edit-save').addEventListener('click', async () => {
        const newName = document.getElementById('pj-edit-name').value.trim();
        const newIp = document.getElementById('pj-edit-ip').value.trim();
        const newPort = document.getElementById('pj-edit-port').value.trim() || '8000';

        if (!newName || !newIp) { alert('名稱和 IP 不可為空'); return; }

        const newUrl = `http://${newIp}:${newPort}`;
        try {
            const res = await fetch(`/api/v1/agents/${encodeURIComponent(agent.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName, url: newUrl }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert(err.detail || '更新失敗');
                return;
            }
            const data = await res.json();
            const updated = data.agent;
            // Update local state
            const idx = _agents.findIndex(a => a.id === agent.id);
            if (idx !== -1) _agents[idx] = { ..._agents[idx], ...updated };
            overlay.remove();
            _renderMachines();
            _syncComputeHosts();
            // Restart polling with new URL
            _stopPolling(agent.id);
            _startPolling(_agents.find(a => a.id === agent.id));
        } catch (_) { alert('更新失敗'); }
    });
}

// ── Expose via window for late binding ──
window._updateMachineCard = _updateMachineCard;
window._agents = _agents;
window._agentStatus = _agentStatus;

// Keep window references in sync when module state changes
// (The module uses `let` for _agents, so we need a getter pattern)
Object.defineProperty(window, '_agents', {
    get() { return _agents; },
    set(v) { _agents = v; },
    configurable: true,
});
Object.defineProperty(window, '_agentStatus', {
    get() { return _agentStatus; },
    set(v) { _agentStatus = v; },
    configurable: true,
});
