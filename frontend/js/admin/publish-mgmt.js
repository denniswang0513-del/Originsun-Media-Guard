// ─── Version Publisher Modal (admin only) ─── //
import { _ensureModalStyles } from '../shared/modal-styles.js';

function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function _authHeaders() {
    const h = { 'Content-Type': 'application/json' };
    if (window._authToken) h['Authorization'] = 'Bearer ' + window._authToken;
    return h;
}

function _compareSemver(a, b) {
    const pa = a.split('.').map(Number), pb = b.split('.').map(Number);
    for (let i = 0; i < 3; i++) {
        if ((pa[i] || 0) > (pb[i] || 0)) return 1;
        if ((pa[i] || 0) < (pb[i] || 0)) return -1;
    }
    return 0;
}

let _curVer = '';

window._openPublishMgmt = async function () {
    _ensureModalStyles();
    document.getElementById('publish-mgmt-modal')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'publish-mgmt-modal';
    overlay.className = '_fm-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.addEventListener('keydown', function _escH(e) { if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', _escH); } });

    const modal = document.createElement('div');
    modal.className = '_fm-modal';
    modal.style.width = '680px';
    modal.style.maxWidth = '92%';
    modal.innerHTML = `
        <div class="_fm-header">
            <h3>🚀 版本發布</h3>
            <span class="_fm-close" onclick="document.getElementById('publish-mgmt-modal')?.remove()">✕</span>
        </div>
        <div class="_fm-body" style="padding:16px 24px;">
            <!-- Current Version -->
            <div id="pub-ver-badge" style="display:flex;align-items:center;gap:10px;background:#1a1a2e;border:1px solid #3b82f6;border-radius:8px;padding:10px 16px;margin-bottom:16px;">
                <div>
                    <div id="pub-cur-ver" style="font-size:18px;font-weight:700;color:#3b82f6;font-family:Consolas,monospace;">Loading...</div>
                    <div id="pub-cur-date" style="color:#888;font-size:12px;"></div>
                </div>
                <button onclick="window._pubLoadVersion()" style="margin-left:auto;background:none;border:1px solid #555;color:#aaa;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:11px;">刷新</button>
            </div>

            <!-- New Version -->
            <div class="_fm-field">
                <label class="_fm-label">新版本號</label>
                <input id="pub-new-ver" class="_fm-input" placeholder="e.g. 1.11.0" spellcheck="false" style="font-family:Consolas,monospace;">
            </div>
            <div style="display:flex;gap:6px;margin-bottom:14px;">
                <button class="_pub-bump" onclick="window._pubBump('patch')">+0.0.1 Patch</button>
                <button class="_pub-bump" onclick="window._pubBump('minor')">+0.1.0 Minor</button>
                <button class="_pub-bump" onclick="window._pubBump('major')">+1.0.0 Major</button>
            </div>
            <div class="_fm-field">
                <label class="_fm-label">Release Notes</label>
                <textarea id="pub-notes" class="_fm-input" placeholder="此版本的變更內容..." rows="2" style="resize:vertical;min-height:50px;"></textarea>
            </div>

            <!-- Publish Button -->
            <button id="pub-btn" onclick="window._pubPublish()" style="width:100%;padding:10px;background:linear-gradient(135deg,#6d28d9,#8b5cf6);color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;">
                🚀 發布
            </button>

            <!-- Deploy to prod 8000 (dev/8001 only — shown at runtime) -->
            <button id="pub-deploy-btn" onclick="window._pubDeployProd()" style="display:none;width:100%;padding:10px;margin-top:8px;background:linear-gradient(135deg,#0f766e,#14b8a6);color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;align-items:center;justify-content:center;gap:8px;">
                📦 部署到生產 8000
            </button>
            <div id="pub-deploy-hint" style="display:none;margin-top:6px;font-size:11px;color:#5eead4;">把這台 dev(8001) 的程式碼複製進 C:\\OriginsunAgent 並重啟 8000；不會動到機隊。settings/字典/帳號保留。</div>

            <div style="display:flex;gap:8px;margin-top:8px;">
                <button onclick="window._pubRollback()" style="padding:5px 12px;border-radius:6px;font-size:11px;border:1px solid rgba(239,68,68,0.4);background:transparent;color:#f87171;cursor:pointer;">回滾到上一版</button>
            </div>
            <div id="pub-status" style="margin-top:10px;font-size:12px;text-align:center;min-height:18px;"></div>

            <!-- Divider -->
            <div style="height:1px;background:#333;margin:16px 0;"></div>

            <!-- Agent Status -->
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                <span style="font-size:12px;font-weight:600;color:#999;">📡 Agent 狀態</span>
                <button onclick="window._pubLoadAgents()" style="background:none;border:1px solid #555;color:#aaa;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:10px;">刷新</button>
            </div>
            <div id="pub-agent-grid" style="display:flex;flex-direction:column;gap:4px;max-height:160px;overflow-y:auto;">Loading...</div>

            <!-- Divider -->
            <div style="height:1px;background:#333;margin:16px 0;"></div>

            <!-- Log -->
            <div style="font-size:12px;font-weight:600;color:#999;margin-bottom:8px;">📋 發布日誌</div>
            <div id="pub-log" style="background:#0a0a0a;border:1px solid #333;border-radius:6px;padding:10px;font-family:Consolas,monospace;font-size:11px;line-height:1.6;color:#888;max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;">Ready.</div>

            <!-- Divider -->
            <div style="height:1px;background:#333;margin:16px 0;"></div>

            <!-- History -->
            <div style="font-size:12px;font-weight:600;color:#999;margin-bottom:8px;">📅 發布紀錄</div>
            <div id="pub-history" style="max-height:140px;overflow-y:auto;font-size:12px;">Loading...</div>

            <!-- Divider -->
            <div style="height:1px;background:#333;margin:16px 0;"></div>

            <!-- Download -->
            <div style="font-size:12px;font-weight:600;color:#999;margin-bottom:8px;">📥 安裝檔下載</div>
            <button onclick="window.open('/download_installer','_blank')" style="display:inline-flex;align-items:center;gap:6px;background:#1f538d;color:#fff;border:none;border-radius:6px;padding:8px 14px;font-size:12px;cursor:pointer;">
                📥 下載 Install_or_Update.bat
            </button>
            <div style="margin-top:6px;font-size:11px;color:#666;">分享給同事，雙擊即可安裝或更新。</div>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Dev test server (port 8001) only: expose "deploy to prod 8000" button.
    if (window.location.port === '8001') {
        const db = document.getElementById('pub-deploy-btn');
        const dh = document.getElementById('pub-deploy-hint');
        if (db) db.style.display = 'flex';
        if (dh) dh.style.display = 'block';
    }

    // Inject bump button styles
    if (!document.getElementById('_pubBumpStyles')) {
        const s = document.createElement('style');
        s.id = '_pubBumpStyles';
        s.textContent = `
            ._pub-bump { background:#252525;border:1px solid #444;color:#ccc;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;font-family:Consolas,monospace;transition:all .15s; }
            ._pub-bump:hover { background:#333;border-color:#8b5cf6;color:#fff; }
        `;
        document.head.appendChild(s);
    }

    // Ctrl+Enter to publish
    document.getElementById('pub-notes')?.addEventListener('keydown', e => {
        if (e.key === 'Enter' && e.ctrlKey) window._pubPublish();
    });

    // Load data
    window._pubLoadVersion();
    window._pubLoadAgents();
    _loadHistory();
};

window._pubLoadVersion = async function () {
    try {
        const r = await fetch('/api/v1/version', { signal: AbortSignal.timeout(5000) });
        const d = await r.json();
        _curVer = d.version || '0.0.0';
        const el = document.getElementById('pub-cur-ver');
        if (el) el.textContent = 'v' + _curVer;
        const dateEl = document.getElementById('pub-cur-date');
        if (dateEl) dateEl.textContent = (d.build_date || '') + ' — ' + (d.notes || '');
        if (!document.getElementById('pub-new-ver')?.value) window._pubBump('patch');
    } catch {
        const el = document.getElementById('pub-cur-ver');
        if (el) el.textContent = 'Offline';
    }
};

window._pubBump = function (type) {
    const p = (_curVer || '0.0.0').split('.').map(Number);
    if (type === 'patch') p[2]++;
    if (type === 'minor') { p[1]++; p[2] = 0; }
    if (type === 'major') { p[0]++; p[1] = 0; p[2] = 0; }
    const el = document.getElementById('pub-new-ver');
    if (el) el.value = p.join('.');
};

window._pubLoadAgents = async function () {
    const grid = document.getElementById('pub-agent-grid');
    if (!grid) return;
    grid.innerHTML = '<span style="color:#666">Loading...</span>';
    try {
        const r = await fetch('/api/v1/agents', { headers: _authHeaders(), signal: AbortSignal.timeout(5000) });
        const d = await r.json();
        const agents = d.agents || [];
        if (!agents.length) { grid.innerHTML = '<span style="color:#666">尚無已註冊 Agent</span>'; return; }

        grid.innerHTML = '';
        for (const a of agents) {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 10px;background:#161616;border-radius:6px;font-size:12px;';
            row.innerHTML = '<span style="width:7px;height:7px;border-radius:50%;background:#555;flex-shrink:0;" class="pub-dot"></span>' +
                '<span style="flex:1;color:#ddd;">' + _esc(a.name) + '</span>' +
                '<span style="font-family:Consolas,monospace;color:#888;font-size:11px;" class="pub-ver">...</span>';
            grid.appendChild(row);
            _checkAgent(a, row);
        }
    } catch {
        grid.innerHTML = '<span style="color:#ef4444">載入失敗</span>';
    }
};

async function _checkAgent(agent, row) {
    const dot = row.querySelector('.pub-dot');
    const ver = row.querySelector('.pub-ver');
    try {
        const r = await fetch('/api/v1/agents/' + agent.id + '/health', {
            headers: _authHeaders(), signal: AbortSignal.timeout(5000)
        });
        const d = await r.json();
        if (d.status === 'ok') {
            dot.style.background = d.worker_busy ? '#fbbf24' : '#22c55e';
            ver.textContent = 'v' + (d.version || '?') + (d.worker_busy ? ' (忙碌)' : '');
        } else {
            dot.style.background = '#555';
            ver.textContent = '離線';
        }
    } catch {
        dot.style.background = '#555';
        ver.textContent = '離線';
    }
}

window._pubPublish = async function () {
    const version = document.getElementById('pub-new-ver')?.value.trim();
    const notes = document.getElementById('pub-notes')?.value.trim();
    const btn = document.getElementById('pub-btn');
    const statusEl = document.getElementById('pub-status');
    const logBox = document.getElementById('pub-log');

    if (!version) { _setStatus('err', '請輸入版本號'); return; }
    if (!/^\d+\.\d+\.\d+$/.test(version)) { _setStatus('err', '版本格式需為 X.Y.Z'); return; }
    if (!notes) { _setStatus('err', '請輸入 Release Notes'); return; }
    if (_curVer && _compareSemver(version, _curVer) < 0) {
        _setStatus('err', 'v' + version + ' 小於目前版本 v' + _curVer);
        return;
    }

    // Check busy agents
    _setStatus('loading', '檢查 Agent 狀態...');
    const busy = await _checkBusyAgents();
    if (busy.length > 0) {
        if (!confirm('⚠️ 以下 Agent 正在執行任務：\n' + busy.join(', ') + '\n\n確定要發布？')) return;
    }

    if (!confirm('確認發布 v' + version + '？\n\nNotes: ' + notes + '\n\n將重建 Agent ZIP（約 1-2 分鐘）')) return;

    btn.disabled = true;
    btn.innerHTML = '<span style="display:inline-block;width:14px;height:14px;border:2px solid #555;border-top-color:#8b5cf6;border-radius:50%;animation:spin .8s linear infinite;"></span> 發布中...';
    _setStatus('loading', '發布中... 請耐心等待 1-2 分鐘');
    logBox.textContent = '[' + new Date().toLocaleTimeString() + '] Starting publish v' + version + '...\n';

    try {
        const r = await fetch('/api/v1/publish', {
            method: 'POST', headers: _authHeaders(),
            body: JSON.stringify({ version, notes }),
        });

        if (r.status === 401) { _setStatus('err', '認證已過期，請重新登入'); btn.disabled = false; btn.innerHTML = '🚀 發布'; return; }
        if (r.status === 409) { _setStatus('err', '另一個發布正在進行中'); btn.disabled = false; btn.innerHTML = '🚀 發布'; return; }

        const d = await r.json().catch(() => null);
        if (!d || !d.job_id) {
            _setStatus('err', d?.message || '發布啟動失敗');
            btn.disabled = false; btn.innerHTML = '🚀 發布';
            return;
        }

        logBox.innerHTML += '[' + new Date().toLocaleTimeString() + '] 發布任務已啟動 (job: ' + d.job_id + ')，背景執行中...\n';

        // Poll for status
        const jobId = d.job_id;
        const poll = setInterval(async () => {
            try {
                const sr = await fetch('/api/v1/publish/status?job_id=' + jobId, { headers: _authHeaders() });
                const sd = await sr.json().catch(() => null);
                if (!sd) return;

                if (sd.status === 'running') return; // Still running

                clearInterval(poll);

                if (sd.log) {
                    const safe = _esc(sd.log)
                        .replace(/\[OK\]/g, '<span style="color:#22c55e">[OK]</span>')
                        .replace(/\[ERROR\]/g, '<span style="color:#ef4444">[ERROR]</span>')
                        .replace(/\[\*\]/g, '<span style="color:#3b82f6">[*]</span>')
                        .replace(/\[AUTO\]/g, '<span style="color:#3b82f6">[AUTO]</span>')
                        .replace(/\[WARN\]/g, '<span style="color:#ef4444">[WARN]</span>');
                    logBox.innerHTML += safe;
                    logBox.scrollTop = logBox.scrollHeight;
                }

                if (sd.status === 'done') {
                    _setStatus('ok', 'v' + version + ' 發布成功！');
                    logBox.innerHTML += '\n<span style="color:#22c55e">[DONE] v' + version + ' published at ' + new Date().toLocaleTimeString() + '</span>';
                    logBox.scrollTop = logBox.scrollHeight;
                    setTimeout(() => { window._pubLoadVersion(); window._pubLoadAgents(); _loadHistory(); }, 1500);
                } else {
                    _setStatus('err', sd.message || '發布失敗');
                }
                btn.disabled = false;
                btn.innerHTML = '🚀 發布';
            } catch (_) { /* polling error, retry next interval */ }
        }, 2000);

        // Safety timeout: stop polling after 10 minutes
        setTimeout(() => {
            clearInterval(poll);
            if (btn.disabled) {
                _setStatus('err', '發布逾時（10 分鐘）');
                btn.disabled = false; btn.innerHTML = '🚀 發布';
            }
        }, 600000);

    } catch (e) {
        _setStatus('err', 'Error: ' + e.message);
        logBox.innerHTML += '\n<span style="color:#ef4444">[ERROR] ' + _esc(e.message) + '</span>';
        btn.disabled = false;
        btn.innerHTML = '🚀 發布';
    }
};

// Deploy this dev (8001) checkout's code → production master (8000) + restart 8000.
// Fleet agents are NOT touched. Shown only when running on port 8001.
window._pubDeployProd = async function () {
    const version = document.getElementById('pub-new-ver')?.value.trim();
    const notes = document.getElementById('pub-notes')?.value.trim();
    const btn = document.getElementById('pub-deploy-btn');
    const logBox = document.getElementById('pub-log');

    if (!version || !/^\d+\.\d+\.\d+$/.test(version)) { _setStatus('err', '版本格式需為 X.Y.Z'); return; }
    if (!notes) { _setStatus('err', '請輸入 Release Notes'); return; }

    if (!confirm('📦 部署到生產 8000？\n\n會把這台 dev(8001) 的程式碼複製進 C:\\OriginsunAgent，' +
        '寫成 v' + version + ' 並重啟 8000。\n\n• 不會推送到機隊（120/107…）\n' +
        '• 保留生產的 settings.json / 正音字典 / 帳號\n\nNotes: ' + notes)) return;

    btn.disabled = true;
    const _orig = btn.innerHTML;
    btn.innerHTML = '<span style="display:inline-block;width:14px;height:14px;border:2px solid #555;border-top-color:#14b8a6;border-radius:50%;animation:spin .8s linear infinite;"></span> 部署中...';
    _setStatus('loading', '部署到生產 8000 中...');
    logBox.textContent = '[' + new Date().toLocaleTimeString() + '] Deploying v' + version + ' → 8000...\n';

    const _done = (ok, msg) => { btn.disabled = false; btn.innerHTML = _orig; _setStatus(ok ? 'ok' : 'err', msg); };

    try {
        const r = await fetch('/api/v1/deploy_to_prod', {
            method: 'POST', headers: _authHeaders(),
            body: JSON.stringify({ version, notes }),
        });
        if (r.status === 401) { _done(false, '認證已過期，請重新登入'); return; }
        if (r.status === 409) { _done(false, '另一個發布/部署正在進行中'); return; }
        const d = await r.json().catch(() => null);
        if (!d || !d.job_id) { _done(false, d?.message || '部署啟動失敗'); return; }

        logBox.innerHTML += '[' + new Date().toLocaleTimeString() + '] 部署任務已啟動 (job: ' + d.job_id + ')...\n';
        const jobId = d.job_id;
        const poll = setInterval(async () => {
            try {
                const sr = await fetch('/api/v1/publish/status?job_id=' + jobId, { headers: _authHeaders() });
                const sd = await sr.json().catch(() => null);
                if (!sd || sd.status === 'running') return;
                clearInterval(poll);
                if (sd.log) {
                    logBox.innerHTML += '\n' + _esc(sd.log)
                        .replace(/\[OK\]/g, '<span style="color:#22c55e">[OK]</span>')
                        .replace(/\[SKIP\]/g, '<span style="color:#fbbf24">[SKIP]</span>')
                        .replace(/Preflight 失敗/g, '<span style="color:#ef4444">Preflight 失敗</span>');
                    logBox.scrollTop = logBox.scrollHeight;
                }
                if (sd.status === 'done') {
                    _done(true, sd.message || ('v' + version + ' 已部署到 8000'));
                    setTimeout(() => { _loadHistory(); }, 1500);
                } else {
                    _done(false, sd.message || '部署失敗');
                }
            } catch (_) { /* retry next interval */ }
        }, 2000);
        setTimeout(() => {
            clearInterval(poll);
            if (btn.disabled) _done(false, '部署逾時（10 分鐘）');
        }, 600000);
    } catch (e) {
        _done(false, 'Error: ' + e.message);
        logBox.innerHTML += '\n<span style="color:#ef4444">[ERROR] ' + _esc(e.message) + '</span>';
    }
};

window._pubRollback = async function () {
    if (!confirm('確定要回滾到上一個版本？\n將還原 version.json 和 Agent ZIP。')) return;
    try {
        const r = await fetch('/api/v1/publish/rollback', {
            method: 'POST', headers: _authHeaders(), signal: AbortSignal.timeout(10000),
        });
        const d = await r.json();
        if (d.status === 'ok') {
            _setStatus('ok', d.message || '回滾成功');
            setTimeout(() => { window._pubLoadVersion(); _loadHistory(); }, 1000);
        } else {
            _setStatus('err', d.message || '回滾失敗');
        }
    } catch (e) {
        _setStatus('err', 'Error: ' + e.message);
    }
};

async function _checkBusyAgents() {
    try {
        const r = await fetch('/api/v1/agents', { headers: _authHeaders(), signal: AbortSignal.timeout(5000) });
        const d = await r.json();
        const agents = d.agents || [];
        const results = await Promise.allSettled(agents.map(async a => {
            const hr = await fetch('/api/v1/agents/' + a.id + '/health', {
                headers: _authHeaders(), signal: AbortSignal.timeout(3000),
            });
            const hd = await hr.json();
            return hd.worker_busy ? a.name : null;
        }));
        return results.filter(r => r.status === 'fulfilled' && r.value).map(r => r.value);
    } catch { return []; }
}

async function _loadHistory() {
    const el = document.getElementById('pub-history');
    if (!el) return;
    try {
        const r = await fetch('/api/v1/publish/history', {
            headers: _authHeaders(), signal: AbortSignal.timeout(5000),
        });
        const d = await r.json();
        const items = (d.history || []).slice(-10).reverse();
        if (!items.length) { el.innerHTML = '<span style="color:#666">尚無發布紀錄</span>'; return; }
        el.innerHTML = items.map(h =>
            '<div style="padding:4px 0;border-bottom:1px solid #222;font-size:11px;">' +
            '<span style="color:#666;margin-right:6px;">' + _esc((h.timestamp || '').replace('T', ' ').substring(0, 19)) + '</span>' +
            '<span style="color:#3b82f6;font-family:Consolas,monospace;">v' + _esc(h.version || '?') + '</span> ' +
            (h.success ? '<span style="color:#22c55e">OK</span>' : '<span style="color:#ef4444">FAIL</span>') +
            ' <span style="color:#666">by ' + _esc(h.published_by || '?') + '</span>' +
            (h.notes ? ' — <span style="color:#888">' + _esc(h.notes).substring(0, 50) + '</span>' : '') +
            '</div>'
        ).join('');
    } catch {
        el.innerHTML = '<span style="color:#666">無法載入紀錄</span>';
    }
}

function _setStatus(type, msg) {
    const el = document.getElementById('pub-status');
    if (!el) return;
    const colors = { ok: '#22c55e', err: '#ef4444', loading: '#fbbf24' };
    el.style.color = colors[type] || '#888';
    el.textContent = msg;
}
