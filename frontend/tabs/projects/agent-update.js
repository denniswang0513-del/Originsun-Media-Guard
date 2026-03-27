// ── Agent Update Logic ──
// Extracted from projects.js — handles remote agent OTA updates
// Uses window.* late binding to avoid circular imports with agent-cards.js

// ── Module State ──
export const _updatingAgents = {}; // agent_id → { phase, pct, detail, since }

// ── Shared helpers ──
import { _isNewer } from './projects.js';

// ── Imports via window (late-bound to avoid circular deps) ──
// window._updateMachineCard(agentId) — from agent-cards.js
// window._agentStatus — from agent-cards.js
// window._agents — from agent-cards.js
// window._masterVersion — from projects.js

export async function _triggerAgentUpdate(agentId) {
    if (_updatingAgents[agentId]) return;
    _updatingAgents[agentId] = { phase: 'triggering' };
    window._updateMachineCard(agentId);

    try {
        const r = await fetch('/api/v1/agents/' + encodeURIComponent(agentId) + '/update', { method: 'POST' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
    } catch (e) {
        delete _updatingAgents[agentId];
        window._updateMachineCard(agentId);
        if (typeof appendLog === 'function') appendLog(`❌ 更新 ${agentId} 失敗：${e.message}`, 'error');
        return;
    }

    // Start polling update progress
    _updatingAgents[agentId] = { phase: 'downloading', pct: 0, since: Date.now() / 1000 };
    window._updateMachineCard(agentId);
    _pollUpdateStatus(agentId);
}

export async function _pollUpdateStatus(agentId, _retries = 0) {
    if (!_updatingAgents[agentId]) return;
    if (_retries > 90) { // 90 × 2s = 3 分鐘 timeout
        delete _updatingAgents[agentId];
        window._updateMachineCard(agentId);
        if (typeof appendLog === 'function') appendLog(`⚠️ ${agentId} 更新超時（3 分鐘未完成）`, 'error');
        return;
    }
    try {
        const since = _updatingAgents[agentId]?.since || 0;
        const r = await fetch('/api/v1/agents/' + encodeURIComponent(agentId) + '/update_status?since=' + since);
        const d = await r.json();
        const u = _updatingAgents[agentId];
        if (!u) return;
        u.phase = d.phase || 'updating';
        u.pct = d.pct || 0;
        u.detail = d.detail || '';

        if (d.phase === 'done') {
            // Update complete — refresh agent status
            delete _updatingAgents[agentId];
            const st = window._agentStatus[agentId];
            if (st && d.version) {
                st.data = st.data || {};
                st.data.version = d.version;
            }
            window._updateMachineCard(agentId);
            _updateBatchButton();
            if (typeof appendLog === 'function') appendLog(`✅ ${agentId} 更新完成（${d.version || ''})`, 'system');
            // If batch update is running, trigger next
            if (window._batchUpdateQueue && window._batchUpdateQueue.length > 0) {
                const next = window._batchUpdateQueue.shift();
                _triggerAgentUpdate(next);
            } else if (window._batchUpdateQueue) {
                window._batchUpdateQueue = null;
                const btn = document.getElementById('pj-batch-update-btn');
                if (btn) { btn.textContent = '✅ 全部已是最新版'; btn.disabled = true; }
            }
            return;
        }
        if (d.phase === 'failed') {
            // Update failed — stop polling, show error
            delete _updatingAgents[agentId];
            window._updateMachineCard(agentId);
            if (typeof appendLog === 'function') appendLog(`❌ ${agentId} 更新失敗：${d.detail || 'Agent 無回應'}。請手動執行 Install_or_Update.bat`, 'error');
            // Continue batch if running
            if (window._batchUpdateQueue && window._batchUpdateQueue.length > 0) {
                const next = window._batchUpdateQueue.shift();
                _triggerAgentUpdate(next);
            }
            return;
        }
    } catch (_) {}

    window._updateMachineCard(agentId);
    // Poll again in 2 seconds
    setTimeout(() => _pollUpdateStatus(agentId, _retries + 1), 2000);
}

export function _updateBatchButton() {
    const btn = document.getElementById('pj-batch-update-btn');
    if (!btn) return;
    const agents = window._agents || [];
    const agentStatus = window._agentStatus || {};
    const masterVersion = window._masterVersion || '';
    const outdated = agents.filter(a => {
        const st = agentStatus[a.id] || {};
        const v = st.data?.version;
        return v && _isNewer(masterVersion, v) && !_updatingAgents[a.id];
    });
    if (outdated.length > 0) {
        btn.style.display = '';
        btn.textContent = `全部更新 ${outdated.length} 台過舊`;
        btn.disabled = false;
    } else if (Object.keys(_updatingAgents).length > 0) {
        btn.style.display = '';
        btn.disabled = true;
    } else {
        btn.style.display = 'none';
    }
}

export async function _batchUpdate() {
    const agents = window._agents || [];
    const agentStatus = window._agentStatus || {};
    const masterVersion = window._masterVersion || '';
    const outdated = agents.filter(a => {
        const st = agentStatus[a.id] || {};
        const v = st.data?.version;
        return v && _isNewer(masterVersion, v) && !_updatingAgents[a.id];
    });
    if (outdated.length === 0) return;

    const btn = document.getElementById('pj-batch-update-btn');
    if (btn) { btn.textContent = `更新中 0/${outdated.length}...`; btn.disabled = true; }

    // Rolling update: one at a time
    window._batchUpdateQueue = outdated.slice(1).map(a => a.id);
    _triggerAgentUpdate(outdated[0].id);
}

// ── Expose via window for late binding ──
window._triggerAgentUpdate = _triggerAgentUpdate;
window._updateBatchButton = _updateBatchButton;
