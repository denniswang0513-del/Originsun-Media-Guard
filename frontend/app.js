// ─── Originsun Media Guard Pro ─── //
// Extracted from index.html <script> blocks

// ─── Auth State ─── //
window._isAdmin = false;
window._authToken = localStorage.getItem('auth_token') || '';
window._authUser = null;

// Check saved token on load
(async function _initAuth() {
    if (!window._authToken) { _applyAuthState(false); return; }
    try {
        const r = await fetch('/api/v1/auth/me', { headers: { 'Authorization': 'Bearer ' + window._authToken } });
        if (r.ok) {
            const d = await r.json();
            window._authUser = d;
            _applyAuthState(d.role === 'admin');
            _applyVisibleTabs();
        } else {
            localStorage.removeItem('auth_token');
            window._authToken = '';
            _applyAuthState(false);
        }
    } catch (_) { _applyAuthState(false); }
})();

function _applyAuthState(isAdmin) {
    window._isAdmin = isAdmin;
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = isAdmin ? '' : 'none';
    });
    const btn = document.getElementById('btn-auth');
    if (btn) {
        if (isAdmin && window._authUser) {
            btn.textContent = '👤';
            btn.style.color = '#22c55e';
            btn.style.borderColor = '#22c55e';
            btn.title = window._authUser.username + ' (登出)';
        } else {
            btn.textContent = '👤';
            btn.style.color = '#888';
            btn.style.borderColor = '#555';
            btn.title = '登入';
        }
    }
}

window._authToggle = function() {
    if (window._isAdmin || window._authUser) {
        // Show dropdown
        let dd = document.getElementById('auth-dropdown');
        if (dd) { dd.remove(); return; }
        dd = document.createElement('div');
        dd.id = 'auth-dropdown';
        dd.style.cssText = 'position:fixed;top:50px;right:10px;background:#2d2d2d;border:1px solid #555;border-radius:8px;padding:8px 0;z-index:100;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';

        const TAB_NAMES = {backup:'備份並轉檔',verify:'檔案比對',transcode:'轉 Proxy',concat:'製作串帶',report:'檔案視覺報表',transcribe:'AI 逐字稿',tts:'語音生成'};
        const tabs = window._authUser?.visible_tabs;
        const ALL_TABS = Object.keys(TAB_NAMES);

        let html = `<div style="padding:4px 12px 8px;color:#aaa;font-size:11px;border-bottom:1px solid #444;">👤 ${window._authUser?.username || ''} (${window._authUser?.role || ''})</div>`;
        html += `<div style="padding:8px 12px;font-size:12px;color:#ccc;">📋 顯示頁籤</div>`;
        ALL_TABS.forEach(t => {
            const checked = !tabs || tabs.includes(t);
            html += `<label style="display:flex;align-items:center;gap:6px;padding:2px 12px;cursor:pointer;font-size:12px;color:#ddd;">
                <input type="checkbox" ${checked?'checked':''} data-tab="${t}" onchange="window._toggleTab(this)"> ${TAB_NAMES[t]}</label>`;
        });
        html += `<div style="border-top:1px solid #444;margin-top:8px;padding:8px 12px;">
            <button onclick="window._authLogout()" style="background:transparent;border:1px solid #ef4444;color:#ef4444;border-radius:4px;padding:3px 12px;cursor:pointer;font-size:12px;width:100%;">登出</button></div>`;
        dd.innerHTML = html;
        document.body.appendChild(dd);
        setTimeout(() => document.addEventListener('click', function _close(e) {
            if (!dd.contains(e.target) && e.target.id !== 'btn-auth') { dd.remove(); document.removeEventListener('click', _close); }
        }), 100);
        return;
    }
    // Show login modal
    let modal = document.getElementById('auth-login-modal');
    if (modal) { modal.classList.remove('hidden'); return; }
    modal = document.createElement('div');
    modal.id = 'auth-login-modal';
    modal.className = 'fixed inset-0 bg-black bg-opacity-70 z-50 flex items-center justify-center';
    modal.innerHTML = `
        <div class="bg-[#2d2d2d] border border-[#444] rounded-xl shadow-2xl p-6 w-80">
            <h3 class="text-lg font-bold text-white mb-4">登入</h3>
            <input id="auth-username" type="text" placeholder="帳號" class="w-full mb-3 px-3 py-2 bg-[#1e1e1e] border border-[#555] rounded text-white text-sm" autofocus>
            <input id="auth-password" type="password" placeholder="密碼" class="w-full mb-3 px-3 py-2 bg-[#1e1e1e] border border-[#555] rounded text-white text-sm">
            <div id="auth-error" class="text-red-400 text-xs mb-2 hidden"></div>
            <div class="flex justify-end gap-2">
                <button onclick="document.getElementById('auth-login-modal').classList.add('hidden')"
                    class="px-4 py-1.5 text-sm text-gray-400 hover:text-white">取消</button>
                <button onclick="window._doLogin()"
                    class="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded">登入</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });
    document.getElementById('auth-password').addEventListener('keydown', (e) => { if (e.key === 'Enter') window._doLogin(); });
    document.getElementById('auth-username').focus();
};

window._doLogin = async function() {
    const u = document.getElementById('auth-username')?.value.trim();
    const p = document.getElementById('auth-password')?.value;
    const errEl = document.getElementById('auth-error');
    if (!u || !p) { if (errEl) { errEl.textContent = '請輸入帳號和密碼'; errEl.classList.remove('hidden'); } return; }
    try {
        const r = await fetch('/api/v1/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p }),
        });
        const d = await r.json();
        if (!r.ok) { if (errEl) { errEl.textContent = d.detail || '登入失敗'; errEl.classList.remove('hidden'); } return; }
        window._authToken = d.token;
        window._authUser = d;
        localStorage.setItem('auth_token', d.token);
        _applyAuthState(d.role === 'admin');
        _applyVisibleTabs();
        document.getElementById('auth-login-modal')?.classList.add('hidden');
        if (d.first_login) {
            alert('首次登入，請到系統設定修改密碼。');
        }
    } catch (e) {
        if (errEl) { errEl.textContent = '連線失敗'; errEl.classList.remove('hidden'); }
    }
};

window._authLogout = function() {
    localStorage.removeItem('auth_token');
    window._authToken = '';
    window._authUser = null;
    _applyAuthState(false);
    document.getElementById('auth-dropdown')?.remove();
    // Restore all tabs
    document.querySelectorAll('.tab-content').forEach(el => el.style.removeProperty('display'));
    document.querySelectorAll('nav button').forEach(el => el.style.removeProperty('display'));
};

window._toggleTab = async function(checkbox) {
    const tab = checkbox.dataset.tab;
    const allCheckboxes = document.querySelectorAll('#auth-dropdown input[data-tab]');
    const selected = [];
    allCheckboxes.forEach(cb => { if (cb.checked) selected.push(cb.dataset.tab); });
    // Save to server
    try {
        await fetch('/api/v1/auth/me', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ visible_tabs: selected.length === 7 ? null : selected }),
        });
        if (window._authUser) window._authUser.visible_tabs = selected.length === 7 ? null : selected;
        _applyVisibleTabs();
    } catch (_) {}
};

function _applyVisibleTabs() {
    const tabs = window._authUser?.visible_tabs;
    if (!tabs) return; // null = show all
    const TAB_MAP = {backup:'tab_backup',verify:'tab_verify',transcode:'tab_transcode',concat:'tab_concat',report:'tab_report',transcribe:'tab_transcribe',tts:'tab_tts'};
    const NAV_MAP = {backup:'備份',verify:'比對',transcode:'Proxy',concat:'串帶',report:'報表',transcribe:'逐字',tts:'語音'};
    Object.entries(TAB_MAP).forEach(([key, id]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = tabs.includes(key) ? '' : 'none';
    });
    // Hide nav buttons for hidden tabs
    document.querySelectorAll('nav button').forEach(btn => {
        const text = btn.textContent;
        Object.entries(NAV_MAP).forEach(([key, label]) => {
            if (text.includes(label)) btn.style.display = tabs.includes(key) ? '' : 'none';
        });
    });
}

// Patch fetch to auto-add auth header for sensitive endpoints
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
    if (window._authToken && typeof url === 'string' &&
        (url.includes('/settings/') || url.includes('/control/update') ||
         url.includes('/admin/') || url.includes('/auth/') ||
         url.includes('/job_history') && opts.method === 'DELETE' ||
         url.includes('/reports/') && opts.method === 'DELETE' ||
         url.includes('/agents') && (opts.method === 'POST' || opts.method === 'DELETE'))) {
        opts.headers = { ...opts.headers, 'Authorization': 'Bearer ' + window._authToken };
    }
    return _origFetch.call(window, url, opts);
};

// ─── Global Error Handler ─── //
window.onerror = function(msg, url, lineNo, columnNo, error) {
    var errDiv = document.createElement('div');
    errDiv.style.position = 'fixed';
    errDiv.style.top = '0';
    errDiv.style.left = '0';
    errDiv.style.width = '100%';
    errDiv.style.background = 'red';
    errDiv.style.color = 'white';
    errDiv.style.zIndex = '999999';
    errDiv.style.padding = '20px';
    errDiv.style.fontSize = '24px';
    errDiv.style.fontWeight = 'bold';
    errDiv.innerHTML = 'FRONTEND ERROR:<br>' + msg + '<br>Line: ' + lineNo + '<br>Col: ' + columnNo;
    document.body.appendChild(errDiv);
    return false;
};

// ─── Fallback for appendLog function ─── //
// If utils.js hasn't loaded yet or appendLog is not available globally, define a fallback
if (typeof appendLog === 'undefined') {
    window.appendLog = function(msg, type = 'info') {
        const terminal = document.getElementById('terminal_verbose');
        if (terminal) {
            const line = document.createElement('div');
            line.className = type === 'system' ? 'text-yellow-300 font-bold' :
                            type === 'error' ? 'text-red-400' :
                            type === 'verbose' ? 'text-gray-500 text-xs' : 'text-gray-400';
            line.textContent = '[' + new Date().toLocaleTimeString('en-US', { hour12: false }) + '] ' + msg;
            terminal.appendChild(line);
            terminal.scrollTop = terminal.scrollHeight;
        }
    };
}

// ─── Main Application ─── //
        let currentSocketUrl = window.location.origin;
        let socket = null;

        // Dynamically load tabs
        async function loadTabs() {
            try {
                // Load Backup Tab
                const tabMain = document.getElementById('tab_main');
                const _cb = `?t=${Date.now()}`;

                // Load Projects Overview Tab (first, before backup)
                try {
                    const tabProjects = document.getElementById('tab-projects');
                    const projRes = await fetch(`./tabs/projects/projects.html${_cb}`);
                    if (projRes.ok) {
                        tabProjects.innerHTML = await projRes.text();
                        const projModule = await import(`./tabs/projects/projects.js${_cb}`);
                        projModule.initTab();
                    }
                } catch (projErr) {
                    console.warn('[Projects Tab] 載入失敗:', projErr);
                }
                const backupRes = await fetch(`./tabs/backup/backup.html${_cb}`);
                if (backupRes.ok) {
                    tabMain.innerHTML = await backupRes.text();
                    // dynamically import module to avoid breaking global app.js scope
                    const backupModule = await import(`./tabs/backup/backup.js${_cb}`);
                    backupModule.initBackupTab();
                } else {
                    console.error("Failed to load Backup tab HTML:", backupRes.statusText);
                }

                // Load Verify Tab
                const tabVerify = document.getElementById('tab_verify');
                const verifyRes = await fetch(`./tabs/verify/verify.html${_cb}`);
                if (verifyRes.ok) {
                    tabVerify.innerHTML = await verifyRes.text();
                    const verifyModule = await import(`./tabs/verify/verify.js${_cb}`);
                    verifyModule.initVerifyTab();
                } else {
                    console.error("Failed to load Verify tab HTML:", verifyRes.statusText);
                }

                // Load Transcode Tab
                const tabTranscode = document.getElementById('tab_transcode');
                const transcodeRes = await fetch(`./tabs/transcode/transcode.html${_cb}`);
                if (transcodeRes.ok) {
                    tabTranscode.innerHTML = await transcodeRes.text();
                    const tcModule = await import(`./tabs/transcode/transcode.js${_cb}`);
                    tcModule.initTranscodeTab();
                } else {
                    console.error("Failed to load Transcode tab HTML:", transcodeRes.statusText);
                }

                // Load Concat Tab
                const tabConcat = document.getElementById('tab_concat');
                const concatRes = await fetch(`./tabs/concat/concat.html${_cb}`);
                if (concatRes.ok) {
                    tabConcat.innerHTML = await concatRes.text();
                    const ccModule = await import(`./tabs/concat/concat.js${_cb}`);
                    ccModule.initConcatTab();
                } else {
                    console.error("Failed to load Concat tab HTML:", concatRes.statusText);
                }

                // Load Report Tab
                const tabReport = document.getElementById('tab_report');
                const reportRes = await fetch(`./tabs/report/report.html${_cb}`);
                if (reportRes.ok) {
                    tabReport.innerHTML = await reportRes.text();
                    const reportModule = await import(`./tabs/report/report.js${_cb}`);
                    reportModule.initReportTab();
                } else {
                    console.error("Failed to load Report tab HTML:", reportRes.statusText);
                }

                // Load Transcribe Tab
                const tabTranscribe = document.getElementById('tab_transcribe');
                const transcribeRes = await fetch(`./tabs/transcribe/transcribe.html${_cb}`);
                if (transcribeRes.ok) {
                    tabTranscribe.innerHTML = await transcribeRes.text();
                    const tsModule = await import(`./tabs/transcribe/transcribe.js${_cb}`);
                    tsModule.initTranscribeTab();
                } else {
                    console.error("Failed to load Transcribe tab HTML:", transcribeRes.statusText);
                }

                // Load TTS Tab (🚧 開發中 - 載入失敗不影響其他分頁)
                try {
                    const tabTts = document.getElementById('tab_tts');
                    const ttsRes = await fetch(`./tabs/tts/tts.html?t=${Date.now()}`);
                    if (ttsRes.ok) {
                        tabTts.innerHTML = await ttsRes.text();
                        const ttsModule = await import(`./tabs/tts/tts.js?t=${Date.now()}`);
                        ttsModule.initTtsTab();
                    }
                } catch (ttsErr) {
                    console.warn('[TTS Tab] 載入失敗（開發中）:', ttsErr);
                }
            } catch (err) {
                console.error("Error loading tabs:", err);
            }
        }

        // Initialize tabs immediately
        loadTabs().then(() => {
            // Initialization after dynamic tabs load
            const today = new Date();
            const yyyy = today.getFullYear();
            const mm = String(today.getMonth() + 1).padStart(2, '0');
            const dd = String(today.getDate()).padStart(2, '0');
            
            // Backup Tab project name
            const projNameEl = document.getElementById('proj_name');
            if (projNameEl) projNameEl.value = `${yyyy}${mm}${dd}`;

            // Report Tab report name
            const rptNameEl = document.getElementById('rpt_report_name');
            if (rptNameEl && !rptNameEl.value) {
                rptNameEl.value = `${yyyy}${mm}${dd}_Report`;
            }

            // Transcode checkbox listener
            const _chkTc = document.getElementById('chk_transcode');
            if (_chkTc) {
                _chkTc.addEventListener('change', () => {
                    const hp = document.getElementById('host_selector_panel');
                    if (hp && (window._computeHosts || []).length > 0)
                        hp.classList.toggle('hidden', !_chkTc.checked);
                });
            }

            if (typeof updateComputeModeStyle === 'function') {
                updateComputeModeStyle();
            }

            // Pre-load agents from NAS so host selector shows immediately
            fetch('/api/v1/agents')
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (data) {
                        window._computeHosts = (data.agents || []).map(a => ({
                            name: a.name,
                            ip: (a.url || '').replace(/^https?:\/\//, '')
                        }));
                        if (typeof renderHostSelector === 'function') renderHostSelector();
                        if (typeof renderStandaloneHostPanels === 'function') renderStandaloneHostPanels();
                    }
                }).catch(() => {});
        });


        function setupSocket(url) {
            if (socket) {
                socket.disconnect();
                socket.removeAllListeners();
            }
            socket = io(url, {
                transports: ['websocket'],
                autoConnect: true,
                reconnection: true
            });
            window._socket = socket;  // Expose for TTS tab and other modules
            window.socket = socket;

            socket.on('connect', () => {
                appendLog('已連線至伺服器 WebSocket', 'system');
            });

            // 後端定期檢查主控端版號，有新版時推播
            socket.on('update_available', (data) => {
                const btnBadge = document.getElementById('header_version_badge');
                if (!btnBadge) return;
                const stripV = (v) => v && v.startsWith('v') ? v.slice(1) : v;
                const latest = stripV(data.latest_version);
                const current = stripV(data.current_version);
                _localAgentVersion = data.current_version;
                btnBadge.style.display = 'inline-block';
                btnBadge.className = "cursor-pointer text-sm font-bold text-white bg-red-600 hover:bg-red-500 px-2 py-0.5 rounded shadow animate-pulse flex items-center gap-1";
                btnBadge.innerHTML = `🚀 <span class="underline">發現新版本 (v${latest})</span>`;
                btnBadge.title = `點擊以從伺服器安裝最新版 (目前: v${current})`;
            });

            socket.on('log', (data) => {
                if (typeof appendLog === 'function') {
                    appendLog(data.msg, data.type);
                }
            });

            socket.on('progress', (data) => {
                if (typeof updateProgress === 'function') {
                    updateProgress(data);
                }
            });

            socket.on('transcribe_error', (data) => {
                const retryBtn = document.getElementById('btn_retry');
                
                // --- Unlock Transcribe Button if locked ---
                const tBtn = document.querySelector('#tab_transcribe button[onclick="submitTranscribeJob()"]');
                if (tBtn && tBtn.disabled) {
                    tBtn.innerHTML = '🎙️ 開始生成逐字稿';
                    tBtn.disabled = false;
                    tBtn.classList.remove('opacity-70', 'cursor-not-allowed');
                }
                const tLbl = document.getElementById('transcribe_prog_label');
                if (tLbl) tLbl.textContent = '❌ 任務中止或失敗: ' + (data.msg || '');
                const tBar = document.getElementById('transcribe_prog_bar');
                if (tBar) {
                    tBar.style.width = '0%';
                    tBar.classList.add('bg-red-500');
                }
            });

            socket.on('task_status', (data) => {
                const retryBtn = document.getElementById('btn_retry');

                if (data.status === 'running') {
                    updateActionBarState('running');
                    // 多機模式：派發中/heartbeat 執行中，不清空進度
                    if (window._remoteDispatching || window._heartbeatTimer ||
                        (window._activeRemoteHosts && Object.keys(window._activeRemoteHosts).length > 0)) {
                        return;
                    }
                    // 新任務開始時才清除上一次的完成狀態
                    if (typeof resetProgress === 'function') resetProgress();
                }

                if (data.status === 'done') {
                    // If we are actively polling remote hosts in distributed mode, do NOT let a single local host's
                    // task completion broadcast prematurely reset the global UI and kill the heartbeat monitor.
                    if (window._activeRemoteHosts && Object.keys(window._activeRemoteHosts).length > 0 && window._heartbeatTimer) {
                        return;
                    }

                    // 分散式轉檔：備份 done 後立即派發，不顯示完成摘要
                    if (window._remoteDispatch) {
                        if (typeof appendLog === 'function') appendLog('系統：備份完成，開始派發分散式轉檔...', 'system');
                        dispatchRemoteTranscode(window._remoteDispatch);
                        window._remoteDispatch = null;
                        return;
                    }

                    // 多卡串帶：追蹤完成數
                    const mc = window._concatMultiCard;
                    if (mc && mc.total > 1 && data.summary?.task_type === 'concat') {
                        mc.done++;
                        if (mc.done < mc.total) {
                            if (typeof appendLog === 'function') appendLog(`🎞️ 串帶 ${mc.done}/${mc.total} 張卡完成`, 'system');
                            return;
                        }
                        if (typeof appendLog === 'function') appendLog(`✅ 串帶全部完成（${mc.total} 張卡）`, 'system');
                        window._concatMultiCard = null;
                    }

                    // Pipeline 追蹤：標記 concat 完成
                    const pl = window._backupPipeline;
                    if (pl && pl.pending && data.summary?.task_type === 'concat') {
                        pl.pending.delete('concat');
                    }

                    // 若 pipeline 還有待完成項目（concat 或 report），不顯示最終摘要
                    if (pl && pl.pending && pl.pending.size > 0) {
                        return;
                    }

                    if (typeof appendLog === 'function') appendLog('系統：所有排定任務執行完畢！', 'system');
                    showCompletionSummary(data.summary, window._activeJobTab);
                    updateActionBarState('idle');
                    if (retryBtn) retryBtn.style.display = 'none';
                    playDing();

                } else if (data.status === 'error') {
                    updateActionBarState('idle');
                    if (typeof appendLog === 'function') appendLog('系統提示：任務執行發生錯誤：' + data.detail, 'error');
                    if (retryBtn && window._lastJob) retryBtn.style.display = 'inline-block';

                } else if (data.status === 'cancelled') {
                    updateActionBarState('idle');
                    if (typeof resetProgress === 'function') resetProgress();
                    if (typeof appendLog === 'function') appendLog('❌ 任務已被中止', 'error');
                }
            });

            socket.on('file_conflict', (data) => {
                if (typeof showConflictModal === 'function') {
                    showConflictModal(data);
                }
            });

            socket.on('transcribe_progress', (data) => {
                const label = document.getElementById('transcribe_prog_label');
                const pctLabel = document.getElementById('transcribe_prog_pct');
                const bar = document.getElementById('transcribe_prog_bar');
                if (label) label.textContent = '🔊 ' + (data.msg || '處理中...');
                if (pctLabel) pctLabel.textContent = Math.floor(data.pct) + '%';
                if (bar) {
                    bar.style.width = Math.min(Math.max(data.pct, 0), 100) + '%';
                }
            });

            socket.on('model_download_done', (data) => {
                window.isDownloadingModel = false;
                if (typeof window.fetchModelStatus === 'function') {
                    window.fetchModelStatus();
                }
            });

            socket.on('model_download_error', (data) => {
                window.isDownloadingModel = false;
                const btn = document.getElementById('btn_download_model');
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = '⬇️ 下載模型';
                }
                const badge = document.getElementById('model_status_badge');
                if (badge) {
                    badge.textContent = '❌ 下載失敗';
                    badge.className = 'px-2 py-0.5 rounded text-xs bg-red-900/50 text-red-400 border border-red-800';
                }
            });

            socket.on('transcribe_done', (data) => {
                const btn = document.querySelector('#tab_transcribe button[onclick="submitTranscribeJob()"]');
                if (btn) {
                    btn.innerHTML = '🎙️ 開始生成逐字稿';
                    btn.disabled = false;
                    btn.classList.remove('opacity-70', 'cursor-not-allowed');
                }
                const label = document.getElementById('transcribe_prog_label');
                if (label) label.textContent = '✅ 轉錄完成';
                const pctLabel = document.getElementById('transcribe_prog_pct');
                if (pctLabel) pctLabel.textContent = '100%';
                const bar = document.getElementById('transcribe_prog_bar');
                if (bar) {
                    bar.style.width = '100%';
                    bar.style.background = 'linear-gradient(90deg, #22c55e, #4ade80)';
                }
                if (typeof appendLog === 'function') {
                    appendLog('✅ 逐字稿生成完畢！目的地：' + data.dest_dir, 'system');
                }
            });

            // Report progress updates (report tab + backup tab chained report)
            socket.on('report_progress', (data) => {
                const phase = data.phase || '';
                const pct = parseFloat(data.pct) || 0;
                const msg = data.msg || '';
                const pctStr = `${pct.toFixed(0)}%`;

                // __done__ = job ended
                if (phase === '__done__') return;

                // Update report tab progress bar (rp-*)
                const rpContainer = document.getElementById('rp-progress');
                if (rpContainer) rpContainer.classList.remove('hidden');
                const lblEl = document.getElementById('rp-prog-label');
                if (lblEl) lblEl.textContent = msg;

                const quarter = 25;
                const segs = {
                    scan: ['rp-seg-scan', 'rp-lbl-scan'],
                    meta: ['rp-seg-meta', 'rp-lbl-meta'],
                    strip: ['rp-seg-strip', 'rp-lbl-strip'],
                    render: ['rp-seg-render', 'rp-lbl-render'],
                };
                const order = ['scan', 'meta', 'strip', 'render'];
                const phaseIdx = order.indexOf(phase);
                order.forEach((p, i) => {
                    const [segId, lblId] = segs[p];
                    const segEl = document.getElementById(segId);
                    const lblEl2 = document.getElementById(lblId);
                    const width = i < phaseIdx ? quarter :
                        i === phaseIdx ? (pct / 100) * quarter : 0;
                    if (segEl) segEl.style.width = `${width}%`;
                    if (lblEl2) lblEl2.textContent = i < phaseIdx ? '100%' : i === phaseIdx ? pctStr : '0%';
                });

                // 同時更新備份 TAB 的報表進度段（bk-seg-report）
                if (window._activeJobTab === 'backup' && !window._backupFinalShown) {
                    // 計算整體報表進度：4 個 report phase 各佔 25%
                    const phaseWeight = { scan: 0, meta: 1, strip: 2, render: 3 };
                    const pw = phaseWeight[phase] ?? 0;
                    const overallPct = (pw * 25) + (pct / 100) * 25; // 0-100
                    // 動態段寬
                    const _doT = document.getElementById('chk_transcode')?.checked ?? false;
                    const _doC = document.getElementById('chk_concat')?.checked ?? false;
                    const _sc = 1 + (_doT ? 1 : 0) + (_doC ? 1 : 0) + 1; // +1 for report itself
                    const _sw = 100 / _sc;
                    const bkSegReport = document.getElementById('bk-seg-report');
                    const bkLblReport = document.getElementById('bk-lbl-report');
                    const bkLegendReport = document.getElementById('bk-legend-report');
                    const bkProgLabel = document.getElementById('bk-prog-label');
                    const bkProgEta = document.getElementById('bk-prog-eta');
                    if (bkSegReport) { bkSegReport.classList.remove('hidden'); bkSegReport.style.width = `${(overallPct / 100) * _sw}%`; }
                    if (bkLblReport) bkLblReport.textContent = `${Math.round(overallPct)}%`;
                    if (bkLegendReport) bkLegendReport.classList.remove('hidden');
                    if (bkProgLabel) bkProgLabel.textContent = `📊 ${msg}`;
                    if (bkProgEta) bkProgEta.textContent = '';
                    // 確保前面的段顯示 100%
                    const prevSegs = ['bk-seg-backup'];
                    const prevLbls = ['bk-lbl-backup'];
                    if (_doT) { prevSegs.push('bk-seg-trans'); prevLbls.push('bk-lbl-trans'); }
                    if (_doC) { prevSegs.push('bk-seg-concat'); prevLbls.push('bk-lbl-concat'); }
                    prevSegs.forEach(id => { const el = document.getElementById(id); if (el) el.style.width = `${_sw}%`; });
                    prevLbls.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '100%'; });
                }

                rptLog(msg, data.type || 'info');
            });

            // Report job finished
            socket.on('report_job_done', (data) => {
                appendLog(`✅ 報表完成：${data.report_name || ''}`, 'system');

                window._backupReportPending = false;

                // Pipeline 追蹤：標記 report 完成
                const pl = window._backupPipeline;
                if (pl && pl.pending) {
                    pl.pending.delete('report');
                }

                // 報表 TAB 完成摘要
                showCompletionSummary({ task_type: 'report', elapsed_sec: 0 }, 'report');

                // 備份 TAB：僅在所有 pipeline 階段都完成時才顯示最終摘要
                if (window._activeJobTab === 'backup') {
                    if (pl && pl.pending && pl.pending.size > 0) {
                        // 還有其他階段（如 concat）未完成，等它完成
                        return;
                    }
                    appendLog('系統：所有排定任務執行完畢！', 'system');
                    showCompletionSummary({ task_type: 'backup', elapsed_sec: 0 }, 'backup');
                    updateActionBarState('idle');
                    playDing();
                }
                updateActionBarState('idle');
                const retryBtn = document.getElementById('btn_retry');
                if (retryBtn) retryBtn.style.display = 'none';
                
                // Refresh the history dashboard on both tabs
                loadReportHistory();
                
                playDing();

                // Show "開啟本次報表" button
                const btn = document.getElementById('btn_open_report');
                if (btn && data.local_path) {
                    btn.dataset.localPath = data.local_path;
                    btn.dataset.driveUrl = data.drive_url || '';
                    btn.style.display = 'inline-flex';
                }
                // Auto-open: only if THIS tab initiated the report (job_id match).
                // Prevents duplicate windows even if Socket.IO broadcasts to all tabs.
                if (data.job_id && window._myReportJobIds?.has(data.job_id)) {
                    window._myReportJobIds.delete(data.job_id);
                    if (data.public_url) {
                        window.open(data.public_url, '_blank');
                    } else if (data.drive_url) {
                        window.open(data.drive_url, '_blank');
                    } else if (data.local_path) {
                        openReportFile(data.local_path, '');
                    }
                }
            });

            // Transcribe progress updates
            socket.on('transcribe_progress', (data) => {
                const pct = parseFloat(data.pct) || 0;
                const msg = data.msg || '';
                const pctStr = `${pct.toFixed(0)}%`;

                const lblEl = document.getElementById('transcribe_prog_label');
                const pctEl = document.getElementById('transcribe_prog_pct');
                const barEl = document.getElementById('transcribe_prog_bar');

                if (lblEl) lblEl.textContent = msg;
                if (pctEl) pctEl.textContent = pctStr;
                if (barEl) barEl.style.width = pctStr;

                if (msg && !msg.includes('正在寫入')) {
                    appendLog(`🎙️ [逐字稿] ${msg}`, data.type || 'info');
                }
            });

            // Transcribe job finished
            socket.on('transcribe_done', (data) => {
                appendLog(`✅ 逐字稿生成完成！輸出目錄：${data.dest_dir || ''}`, 'system');
                
                const lblEl = document.getElementById('transcribe_prog_label');
                const pctEl = document.getElementById('transcribe_prog_pct');
                const barEl = document.getElementById('transcribe_prog_bar');
                
                if (lblEl) lblEl.textContent = '完成！';
                if (pctEl) pctEl.textContent = '100%';
                if (barEl) barEl.style.width = '100%';

                if (typeof appendLog === 'function') appendLog('系統：所有排定任務執行完畢！', 'system');
                playDing();

                // Open output folder directly
                if (data.dest_dir) {
                    fetch(currentSocketUrl + '/api/v1/utils/open_folder', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({path: data.dest_dir})
                    }).catch(e => console.error(e));
                }
            });
        }

        setupSocket(currentSocketUrl);

        const terminal = document.getElementById('terminal');
        const terminalVerbose = document.getElementById('terminal_verbose');
        const statusBadge = document.getElementById('status-badge');
        window._activeJobTab = null; // 'backup' | 'verify' | 'transcode' | 'concat' | 'report'

        function playDing() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                if (!audioCtx) return;
                const oscillator = audioCtx.createOscillator();
                const gainNode = audioCtx.createGain();

                oscillator.type = 'sine';
                oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); // A5 note
                oscillator.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.5);

                gainNode.gain.setValueAtTime(0.5, audioCtx.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);

                oscillator.connect(gainNode);
                gainNode.connect(audioCtx.destination);

                oscillator.start();
                oscillator.stop(audioCtx.currentTime + 0.5);
            } catch (e) {
                console.warn("Audio API failed to play:", e);
            }
        }

        // --- Local Agent Polling ---
        let localAgentActive = false;
        let initialPollComplete = false;
        let isUpdating = false;
        let hasServerDiedDuringUpdate = false;
        let _updatePollTimer = null;
        let _updateStartTime = 0;    // timestamp when update started
        let _updateLastProgress = 0; // timestamp of last progress update

        async function pollLocalAgent() {
            try {
                const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
                const targetUrl = isLocal ? '/api/v1/status' : 'http://127.0.0.1:8000/api/v1/status';

                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 1000);

                const response = await fetch(targetUrl, { signal: controller.signal });
                clearTimeout(timeoutId);

                if (response.ok) {
                    localAgentActive = true;
                    updateAgentBadge(true);
                    const newUrl = isLocal ? window.location.origin : 'http://127.0.0.1:8000';
                    if (currentSocketUrl !== newUrl) {
                        currentSocketUrl = newUrl;
                        setupSocket(currentSocketUrl);
                    }
                } else {
                    localAgentActive = false;
                    if (isUpdating) hasServerDiedDuringUpdate = true;
                    updateAgentBadge(false);
                    if (currentSocketUrl !== window.location.origin) {
                        currentSocketUrl = window.location.origin;
                        setupSocket(currentSocketUrl);
                    }
                }
            } catch (err) {
                localAgentActive = false;
                if (isUpdating) hasServerDiedDuringUpdate = true;
                updateAgentBadge(false);
                if (currentSocketUrl !== window.location.origin) {
                    currentSocketUrl = window.location.origin;
                    setupSocket(currentSocketUrl);
                }
            } finally {
                initialPollComplete = true;
                checkForceInstallModal();
            }
        }

        function checkForceInstallModal() {
            const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
            const modal = document.getElementById('forceInstallModal');
            const updatingModal = document.getElementById('updatingModal');
            if (!modal) return;

            // 處理更新中的遮罩顯示
            if (isUpdating) {
                modal.classList.add('hidden');
                if (updatingModal) updatingModal.classList.remove('hidden');
            } else {
                if (updatingModal) updatingModal.classList.add('hidden');
            }

            if (isLocal || localAgentActive) {
                modal.classList.add('hidden');
                // 如果代理剛更新回來，且本機真的斷線過又恢復連線，才觸發網頁重整以套用新版 JS
                if (isUpdating && localAgentActive && hasServerDiedDuringUpdate) {
                    isUpdating = false;
                    setTimeout(() => window.location.reload(), 1500);
                }
            } else if (initialPollComplete && !isUpdating) {
                // 只有在非更新狀態，且啟動檢查已完成，又失去連線時，才顯示安裝視窗
                modal.classList.remove('hidden');
            }
        }

        function updateAgentBadge(isActive) {
            if (!statusBadge) return;
            const btnShortcut = document.getElementById('btn_create_shortcut');
            if (isActive) {
                statusBadge.textContent = "🟢 本機已連線";
                statusBadge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-green-900 text-green-100 border border-green-700";
                // 每次節點上線時，偷偷檢查版本
                checkAgentVersion();
                if (btnShortcut) btnShortcut.style.display = 'flex';
            } else {
                statusBadge.textContent = "🔴 僅支援伺服器運算";
                statusBadge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-red-900 text-red-100 border border-red-700";
                if (btnShortcut) btnShortcut.style.display = 'none';
            }
        }

        async function checkAgentVersion() {
            try {
                // 1. 取得本機 Agent 正在運行的版本（不論在 localhost 或遠端都固定問 localhost）
                const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
                const localVersionUrl = isLocal ? '/api/v1/version' : 'http://127.0.0.1:8000/api/v1/version';
                // 瀏覽主控端時，主控端本身就是版本來源，直接用 /api/v1/version
                const nasVersionUrl = isLocal ? '/api/v1/nas_version' : '/api/v1/version';

                const [localRes, nasRes] = await Promise.all([
                    fetch(localVersionUrl).catch(() => null),
                    fetch(nasVersionUrl).catch(() => null)
                ]);

                const btnBadge = document.getElementById('header_version_badge');
                if (!btnBadge) return;

                btnBadge.style.display = 'inline-block';

                if (!localRes || !localRes.ok || !nasRes || !nasRes.ok) {
                    console.warn("版號檢查失敗: 無法連線");
                    return;
                }

                const localData = await localRes.json();
                const nasData = await nasRes.json();
                const currentVersion = localData.version;
                let latestVersion = nasData.version;
                _localAgentVersion = currentVersion; // 記住本機版本供 updateAgent 使用

                // Fallback: 若 nas_version 回傳 unknown，嘗試從當前頁面 origin 取得
                if (latestVersion === 'unknown') {
                    try {
                        const fbRes = await fetch('/api/v1/version').catch(() => null);
                        if (fbRes?.ok) {
                            const fb = await fbRes.json();
                            if (fb.version && fb.version !== 'unknown') latestVersion = fb.version;
                        }
                    } catch(e) {}
                }

                // 移除 v 前綴後再比較版號
                const stripV = (v) => v && v.startsWith('v') ? v.slice(1) : v;
                const isNewerSemver = (latest, current) => {
                    if (!latest || latest === 'unknown' || !current) return false;
                    const l = stripV(latest), c = stripV(current);
                    if (l === c) return false;
                    const lParts = l.split('.').map(Number);
                    const cParts = c.split('.').map(Number);
                    for (let i = 0; i < Math.max(lParts.length, cParts.length); i++) {
                        const lp = lParts[i] || 0;
                        const cp = cParts[i] || 0;
                        if (lp > cp) return true;
                        if (lp < cp) return false;
                    }
                    return false;
                };

                // 3. 比較版號，嚴謹確認最新版本是否真的大於本機版本
                if (isNewerSemver(latestVersion, currentVersion)) {
                    const displayLatest = stripV(latestVersion);
                    const displayCurrent = stripV(currentVersion);
                    btnBadge.className = "cursor-pointer text-sm font-bold text-white bg-red-600 hover:bg-red-500 px-2 py-0.5 rounded shadow animate-pulse flex items-center gap-1";
                    btnBadge.innerHTML = `🚀 <span class="underline">發現新版本 (v${displayLatest})</span>`;
                    btnBadge.title = `點擊以從伺服器安裝最新版 (目前: v${displayCurrent})`;
                } else {
                    const displayCurrent = stripV(currentVersion);
                    btnBadge.className = "cursor-pointer text-sm font-normal text-blue-400 hover:text-blue-300 px-2 py-0.5 rounded transition-colors";
                    btnBadge.innerHTML = `v${displayCurrent || '?'}`;
                    btnBadge.title = "已是最新版 (點擊可強制重新套用更新)";
                }
            } catch (err) {
                console.warn("版號檢查失敗", err);
            }
        }

        function updateUpdateModal(d) {
            const pctBar = document.getElementById('update_pct_bar');
            const msgEl  = document.getElementById('upd_msg');
            if (pctBar) pctBar.style.width = (d.pct || 2) + '%';
            if (msgEl)  msgEl.textContent = d.msg || '';
            const step = d.step || 0;
            for (let i = 1; i <= 3; i++) {
                const icon = document.getElementById(`upd_icon_${i}`);
                const row  = document.getElementById(`upd_step_${i}`);
                if (!icon || !row) continue;
                if (i < step) {
                    icon.textContent = '✅';
                    row.className = row.className.replace(/text-gray-400|text-blue-300/g, '') + ' text-green-400';
                } else if (i === step) {
                    icon.textContent = '🔄';
                    row.className = row.className.replace(/text-gray-400|text-green-400/g, '') + ' text-blue-300';
                } else {
                    icon.textContent = '⏳';
                    row.className = row.className.replace(/text-blue-300|text-green-400/g, '') + ' text-gray-400';
                }
            }
        }

        function startUpdateProgressPolling() {
            if (_updatePollTimer) return;
            _updateStartTime = Date.now();
            _updateLastProgress = Date.now();
            _updatePollTimer = setInterval(async () => {
                if (!isUpdating) {
                    clearInterval(_updatePollTimer);
                    _updatePollTimer = null;
                    return;
                }

                // 1) 嘗試從 update_monitor (port 8001) 取得進度
                try {
                    const r = await fetch('http://127.0.0.1:8001/status',
                        { signal: AbortSignal.timeout(2000) });
                    if (r.ok) {
                        _updateLastProgress = Date.now();
                        updateUpdateModal(await r.json());
                    }
                } catch (e) { /* monitor 尚未就緒或 port 衝突 */ }

                // 2) Fallback：若超過 30 秒沒收到進度，直接偵測主服務是否已恢復
                const elapsed = Date.now() - _updateStartTime;
                const stale = Date.now() - _updateLastProgress;
                if (stale > 30000 && elapsed > 15000) {
                    try {
                        const health = await fetch('http://127.0.0.1:8000/api/v1/health',
                            { signal: AbortSignal.timeout(2000) });
                        if (health.ok) {
                            // 伺服器已恢復！自動完成更新
                            isUpdating = false;
                            clearInterval(_updatePollTimer);
                            _updatePollTimer = null;
                            window.location.reload();
                            return;
                        }
                    } catch (e) { /* 伺服器尚未恢復 */ }
                }

                // 3) 最大超時 5 分鐘：強制 reload
                if (elapsed > 300000) {
                    isUpdating = false;
                    clearInterval(_updatePollTimer);
                    _updatePollTimer = null;
                    window.location.reload();
                }
            }, 2000);
        }

        // 記住本機版本，供 updateAgent 判斷是否需要首次遷移
        let _localAgentVersion = null;

        async function updateAgent() {
            // 若本機 Agent 版本 < 1.8.0，舊版 OTA 機制 (NAS xcopy) 不可用，
            // 自動下載升級工具 + 顯示操作指引遮罩
            const needsMigration = _localAgentVersion && _isOlderThan(_localAgentVersion, '1.8.0');

            if (needsMigration) {
                // 1) 自動下載 Originsun_Updater.bat
                const a = document.createElement('a');
                a.href = '/download_updater';
                a.download = 'Originsun_Updater.bat';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);

                // 2) 顯示首次遷移指引遮罩
                _showMigrationOverlay();

                // 3) 同時複製 PowerShell 指令到剪貼簿（備用）
                const serverHost = window.location.host || '192.168.1.11:8000';
                const psCmd = `powershell -ExecutionPolicy Bypass -c "irm http://${serverHost}/bootstrap.ps1 | iex"`;
                try { await navigator.clipboard.writeText(psCmd); } catch(e) {}

                // 4) 開始輪詢：偵測版本升級完成後自動重整
                _pollForMigrationDone();
                appendLog('已下載升級工具，請在 Chrome 下載列執行它。', 'system');
                return;
            }

            if (!confirm('即將從伺服器下載最新版本並重新啟動本機代理。這將會中斷正在本機執行的任務。\n確認要執行嗎？')) return;

            isUpdating = true;
            hasServerDiedDuringUpdate = false;
            startUpdateProgressPolling();
            checkForceInstallModal(); // 立刻覆蓋藍色大遮罩

            try {
                // 不使用 await 等待 json，因為伺服器會在這瞬間自我了斷 (os._exit)，必然引發 Network Error
                fetch('http://127.0.0.1:8000/api/v1/control/update', { method: 'POST' }).catch(e => console.log('Expected disconnect:', e));
                appendLog('更新指令已送出，稍後連線指示燈將會變為紅色，數秒後將自動重新載入網頁。', 'system');
            } catch (err) {
                // 忽略錯誤，絕對不把 isUpdating 設為 false，讓畫面維持藍色等待直到 polling 醒來
                console.warn('Update trigger network drop:', err);
            }
        }

        function _showMigrationOverlay() {
            // 如果遮罩已存在就不重複建立
            if (document.getElementById('migrationOverlay')) return;
            const overlay = document.createElement('div');
            overlay.id = 'migrationOverlay';
            overlay.className = 'fixed inset-0 bg-black/90 z-[120] flex items-center justify-center backdrop-blur-md';
            overlay.innerHTML = `
                <div class="bg-[#1e1e1e] border-t-4 border-orange-500 rounded-lg shadow-2xl p-8 max-w-lg w-full">
                    <div class="flex items-center gap-4 mb-6">
                        <div class="text-5xl">🚀</div>
                        <div>
                            <h2 class="text-xl font-bold text-white">首次升級（僅需一次）</h2>
                            <p class="text-gray-400 text-sm">升級完成後，未來更新只需點一下即可</p>
                        </div>
                    </div>
                    <div class="bg-[#2a2a2a] rounded-lg p-5 mb-5 space-y-4">
                        <div class="flex items-start gap-3">
                            <span class="text-2xl">①</span>
                            <div>
                                <p class="text-white font-semibold">在 Chrome 下載列找到並執行升級工具</p>
                                <p class="text-gray-400 text-xs mt-1">若出現「已封鎖」，請點 ⋮ →「仍然保留」</p>
                            </div>
                        </div>
                        <div class="flex items-start gap-3">
                            <span class="text-2xl">②</span>
                            <div>
                                <p class="text-white font-semibold">等待自動完成（約 30 秒）</p>
                                <p class="text-gray-400 text-xs mt-1">完成後此頁面會自動重新整理</p>
                            </div>
                        </div>
                    </div>
                    <div class="bg-blue-900/30 border border-blue-600 rounded p-3 mb-5 text-xs text-blue-300">
                        💡 <strong>備用方法：</strong>升級指令已複製到剪貼簿，也可按
                        <kbd class="bg-[#333] px-1 rounded">Win+R</kbd> →
                        <kbd class="bg-[#333] px-1 rounded">Ctrl+V</kbd> →
                        <kbd class="bg-[#333] px-1 rounded">Enter</kbd> 執行
                    </div>
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2 text-gray-500 text-xs">
                            <div class="w-4 h-4 rounded-full border-2 border-t-transparent border-orange-500 animate-spin"></div>
                            等待升級完成中...
                        </div>
                        <button onclick="document.getElementById('migrationOverlay').remove()"
                                class="text-gray-500 hover:text-gray-300 text-xs underline">關閉</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);
        }

        function _pollForMigrationDone() {
            const timer = setInterval(async () => {
                try {
                    const r = await fetch('http://127.0.0.1:8000/api/v1/version', { signal: AbortSignal.timeout(2000) });
                    if (!r.ok) return;
                    const data = await r.json();
                    if (data.version && !_isOlderThan(data.version, '1.8.0')) {
                        clearInterval(timer);
                        const overlay = document.getElementById('migrationOverlay');
                        if (overlay) overlay.remove();
                        appendLog('升級完成！正在重新載入頁面...', 'system');
                        setTimeout(() => window.location.reload(), 1500);
                    }
                } catch (e) { /* agent 尚未重啟，繼續等 */ }
            }, 3000);
        }

        function _isOlderThan(ver, minVer) {
            const a = ver.split('.').map(Number);
            const b = minVer.split('.').map(Number);
            for (let i = 0; i < Math.max(a.length, b.length); i++) {
                if ((a[i] || 0) < (b[i] || 0)) return true;
                if ((a[i] || 0) > (b[i] || 0)) return false;
            }
            return false;
        }

        async function createShortcut() {
            if (!localAgentActive) {
                alert("此功能需要在「本機已連線」狀態下才能執行！");
                return;
            }
            try {
                const res = await fetch('http://127.0.0.1:8000/api/v1/utils/create_shortcut', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    alert(data.message);
                } else {
                    alert(data.message);
                }
            } catch (err) {
                alert("建立失敗，無法連線至本機代理程式。");
            }
        }

        // Start polling immediately and then every 3 seconds
        pollLocalAgent();
        setInterval(pollLocalAgent, 3000);
        // ---------------------------

        // Variables related to sources and setup were moved to backup.js

        // ===== SaaS 路由輔助函數 =====
        function getAgentBaseUrl() {
            // 本機代理伺服器，負責 UI 操作如選取資料夾與拖曳。如果沒開，回退給網頁原始伺服器。
            return localAgentActive ? 'http://127.0.0.1:8000' : '';
        }

        // ===== Multi-host: render host selector checkboxes =====
        window._computeHosts = [];

        function renderHostSelector() {
            const panel = document.getElementById('host_selector_panel');
            if (!panel) return;
            const hosts = window._computeHosts || [];
            const chkTc = document.getElementById('chk_transcode');
            const chkCc = document.getElementById('chk_concat');
            const shouldShow = (chkTc && chkTc.checked) || (chkCc && chkCc.checked);
            if (!hosts.length || !shouldShow) { panel.classList.add('hidden'); return; }
            window.renderHostCheckboxes('host_selector_checkboxes', { idPrefix: 'host_chk' });
            panel.classList.remove('hidden');
        }

        function getSelectedHosts() {
            const result = window.collectSelectedHosts('host_selector_checkboxes');
            if (!result.length) result.push({ name: '本機', ip: 'local' });
            return result;
        }




        // 將主機列表同步到各獨立 TAB 的主機選擇 UI
        // 多選（checkbox）：轉 Proxy TAB（支援分散式多機轉檔）
        const _MULTI_HOST_PANELS = [
            { checkboxes: 'tc_host_checkboxes', panel: 'tc_host_panel', prefix: 'tc_host_chk' },
        ];
        // 單選（radio）：其餘 TAB（只在一台主機執行）
        const _SINGLE_HOST_PANELS = [
            { checkboxes: 'cc_host_checkboxes',         panel: 'cc_host_panel',         prefix: 'cc_host_chk' },
            { checkboxes: 'vf_host_checkboxes',         panel: 'vf_host_panel',         prefix: 'vf_host_chk' },
            { checkboxes: 'tr_host_checkboxes',         panel: 'tr_host_panel',         prefix: 'tr_host_chk' },
            { checkboxes: 'rpt_host_checkboxes',        panel: 'rpt_host_panel',        prefix: 'rpt_host_chk' },
            { checkboxes: 'tts_host_checkboxes',        panel: 'tts_host_panel',        prefix: 'tts_host_chk' },
            { checkboxes: 'tts_clone_host_checkboxes',  panel: 'tts_clone_host_panel',  prefix: 'tts_clone_host_chk' },
        ];
        function renderStandaloneHostPanels() {
            const hosts = window._computeHosts || [];
            if (!hosts.length) return;
            for (const { checkboxes, panel, prefix } of _MULTI_HOST_PANELS) {
                window.renderHostCheckboxes(checkboxes, { idPrefix: prefix });
                const el = document.getElementById(panel);
                if (el) el.classList.remove('hidden');
            }
            for (const { checkboxes, panel, prefix } of _SINGLE_HOST_PANELS) {
                window.renderHostRadios(checkboxes, { idPrefix: prefix });
                const el = document.getElementById(panel);
                if (el) el.classList.remove('hidden');
            }
        }


        // ================= Conflict Modal =================
        function showConflictModal(data) {
            const modal = document.getElementById('conflict_modal');
            const pathEl = document.getElementById('conflict_path');
            const reasonEl = document.getElementById('conflict_reason');
            const actionsEl = document.getElementById('conflict_actions');
            const conflictJobId = data.job_id || '';  // Capture job_id for routing
            window._currentConflictJobId = conflictJobId;  // Store for setGlobalConflict

            pathEl.textContent = `檔案: ${data.rel_path} (${data.target === 'nas' ? 'NAS端' : '本機端'})`;
            reasonEl.textContent = data.reason;

            // 清空舊按鈕
            actionsEl.innerHTML = '';

            const createBtn = (text, action, colorClass) => {
                const btn = document.createElement('button');
                btn.className = `px-4 py-2 rounded text-sm font-semibold transition-colors ${colorClass}`;
                btn.textContent = text;
                btn.onclick = () => {
                    modal.classList.add('hidden');
                    if (socket) socket.emit('resolve_conflict', { action: action, job_id: conflictJobId });
                };
                return btn;
            };

            // 根據不同情境產生適合的按鈕
            if (data.conflict_type === 'size_mismatch') {
                actionsEl.appendChild(createBtn('強制覆蓋 (Overwrite)', 'overwrite', 'bg-red-600 hover:bg-red-700 text-white'));
                actionsEl.appendChild(createBtn('略過不處理 (Skip)', 'skip', 'bg-gray-600 hover:bg-gray-500 text-white'));
                actionsEl.appendChild(createBtn('自動更名保留 (Rename)', 'rename', 'bg-blue-600 hover:bg-blue-500 text-white'));
            } else if (data.conflict_type === 'time_mismatch') {
                actionsEl.appendChild(createBtn('強制覆蓋 (Overwrite)', 'overwrite', 'bg-red-600 hover:bg-red-700 text-white'));
                actionsEl.appendChild(createBtn('進階校驗 (XXH64)', 'verify', 'bg-purple-600 hover:bg-purple-500 text-white'));
                actionsEl.appendChild(createBtn('略過不處理 (Skip)', 'skip', 'bg-gray-600 hover:bg-gray-500 text-white'));
                actionsEl.appendChild(createBtn('自動更名保留 (Rename)', 'rename', 'bg-blue-600 hover:bg-blue-500 text-white'));
            } else if (data.conflict_type === 'hash_mismatch') {
                actionsEl.appendChild(createBtn('雜湊不同-強制覆蓋', 'overwrite', 'bg-red-600 hover:bg-red-700 text-white'));
                actionsEl.appendChild(createBtn('略過不處理 (Skip)', 'skip', 'bg-gray-600 hover:bg-gray-500 text-white'));
                actionsEl.appendChild(createBtn('自動更名保留 (Rename)', 'rename', 'bg-blue-600 hover:bg-blue-500 text-white'));
            } else {
                actionsEl.appendChild(createBtn('覆蓋', 'overwrite', 'bg-red-600 hover:bg-red-700 text-white'));
                actionsEl.appendChild(createBtn('略過', 'skip', 'bg-gray-600 hover:bg-gray-500 text-white'));
            }

            // 顯示 Modal
            modal.classList.remove('hidden');
            appendLog(`[!] 等待使用者解決檔案衝突: ${data.rel_path}`, 'system');
        }

        // 全部覆蓋 / 全部略過：通知 server 設定全域模式並關閉 modal
        function setGlobalConflict(action) {
            socket.emit('set_global_conflict', { action, job_id: window._currentConflictJobId || '' });
            document.getElementById('conflict_modal').classList.add('hidden');
            const label = action === 'overwrite' ? '全部覆蓋' : '全部略過';
            appendLog(`[!] 已設定「${label}」模式，後續衝突將自動套用。`, 'system');
        }

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
        }

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


        // ================= Tab 切換邏輯 =================
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById(tabId).classList.remove('hidden');

            // 重置按鈕樣式
            const btnCols = ['btn_tab-projects', 'btn_tab_main', 'btn_tab_verify', 'btn_tab_transcode', 'btn_tab_concat', 'btn_tab_report', 'btn_tab_transcribe', 'btn_tab_tts'];
            btnCols.forEach(btn => {
                const el = document.getElementById(btn);
                if (el) {
                    el.classList.remove('bg-[#2a2a2a]', 'text-blue-400', 'text-amber-300', 'border', 'border-b-0', 'border-[#3a3a3a]');
                    el.classList.add('bg-[#1e1e1e]', 'text-white', 'border-transparent');
                }
            });

            // 啟動當前
            const activeBtn = document.getElementById('btn_' + tabId);
            if (activeBtn) {
                activeBtn.classList.remove('bg-[#1e1e1e]', 'text-white', 'text-amber-400', 'border-transparent');
                activeBtn.classList.add('bg-[#2a2a2a]', 'border', 'border-b-0', 'border-[#3a3a3a]', 'text-blue-400');
            }

            // Notify projects tab of visibility change
            document.dispatchEvent(new CustomEvent('tab-changed', { detail: { tab: tabId } }));

            // Auto-fill output directory when entering report tab
            if (tabId === 'tab_report') {
                // Default output directory = local_root (專案素材區)
                const rptOut = document.getElementById('rpt_output');
                const localRoot = document.getElementById('local_root');
                if (rptOut && localRoot && !rptOut.value.trim() && localRoot.value.trim()) {
                    rptOut.value = localRoot.value.trim();
                }
                if (window.loadReportHistory) window.loadReportHistory();
            }
        }

        // ===== 全域：記錄上一次任務，供重試使用 =====
        window._lastJob = null; // { url, payload }

        // ===== Multi-host runtime (Steps 3-8) =====
        window._remoteDispatch = null;
        window._activeRemoteHosts = {};
        window._missingFiles = [];
        window._heartbeatTimer = null;
        window._remoteJobType = null;

        // 任務類型中文對照（全域常量，避免重複定義）
        const JOB_LABELS = {
            transcode: '轉檔', verify: '比對', concat: '串帶',
            report: '報表', transcribe: '轉錄', tts: 'TTS 合成', tts_clone: '聲音複製',
        };

        function initRemoteHostProgress(hosts) {
            const tab = window._activeJobTab || 'backup';
            const prefixMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts' };
            const prefix = prefixMap[tab];
            if (!prefix) return;
            const panel = document.getElementById(prefix + '-remote-hosts-progress');
            const rows = document.getElementById(prefix + '-remote-host-rows');
            if (!panel || !rows) return;
            rows.innerHTML = '';
            hosts.forEach(h => {
                const sid = h.ip.replace(/[^a-zA-Z0-9]/g, '_');
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;flex-direction:column;gap:4px;';
                row.innerHTML = (
                    '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                    '<span style="font-size:12px;color:#93c5fd;">🖥️ ' + h.name + ' <span style="color:#6b7280;font-size:10px;">(' + h.ip + ')</span></span>' +
                    '<span id="rh_status_' + sid + '" style="font-size:11px;color:#9ca3af;">等待...</span>' +
                    '</div>' +
                    '<div style="background:#1e1e1e;border-radius:6px;height:8px;overflow:hidden;border:1px solid #374151;">' +
                    '<div id="rh_bar_' + sid + '" style="height:8px;width:0%;background:#1f538d;transition:width .4s;border-radius:6px;"></div>' +
                    '</div>'
                );
                rows.appendChild(row);
            });
            panel.classList.remove('hidden');
        }

        function updateHostProgress(ip, pct, txt, color) {
            const sid = ip.replace(/[^a-zA-Z0-9]/g, '_');
            const bar = document.getElementById('rh_bar_' + sid);
            const lbl = document.getElementById('rh_status_' + sid);
            if (bar) { bar.style.width = pct + '%'; if (color) bar.style.background = color; }
            if (lbl) lbl.textContent = txt || (pct + '%');
        }

        // 預編譯遠端 log 分類 regex（避免 heartbeat 每次迭代重新編譯）
        const _RE_SYSTEM_LOG = /\[Engine\]|系統|✅|完成|開始/;
        const _RE_ERROR_LOG = /\[!\]|❌|失敗|錯誤|error|FAIL/i;

        function startHeartbeatMonitor() {
            if (window._heartbeatTimer) clearInterval(window._heartbeatTimer);
            window._heartbeatTimer = setInterval(async () => {
                const now = Date.now();
                for (const [ip, info] of Object.entries(window._activeRemoteHosts || {})) {
                    if (info.done) continue;
                    try {
                        const ctrl = new AbortController();
                        const t = setTimeout(() => ctrl.abort(), 3000);
                        const offset = info.logOffset || 0;
                        const r = await fetch('http://' + ip + '/api/v1/status?log_offset=' + offset, { signal: ctrl.signal });
                        clearTimeout(t);
                        if (r.ok) {
                            info.lastSeen = now;
                            const d = await r.json();
                            info.logOffset = d.new_log_offset || offset;

                            if (d.logs && d.logs.length > 0) {
                                d.logs.forEach(msg => {
                                    if (typeof appendLog === 'function') {
                                        const cleanMsg = msg.replace(/^\[.*?\]\s*/, '');
                                        let _lt = 'info';
                                        if (_RE_SYSTEM_LOG.test(cleanMsg)) _lt = 'system';
                                        if (_RE_ERROR_LOG.test(cleanMsg)) _lt = 'error';
                                        appendLog(`[${info.host.name}] ${cleanMsg}`, _lt);
                                    }
                                });
                            }

                            // 聚合此主機的所有 job 進度（多卡場景）
                            let hostPct = 0;
                            let hostTxt = '處理中...';
                            let totalJobs = info.expectedJobs || 1;
                            let doneJobs = 0;

                            if (d.active_jobs) {
                                const ajobs = Object.values(d.active_jobs);
                                if (ajobs.length > 0) {
                                    totalJobs = Math.max(totalJobs, ajobs.length);
                                    let sumPct = 0;
                                    for (const j of ajobs) {
                                        const jp = j.progress?.total_pct || 0;
                                        if (j.status === 'done' || j.status === 'completed') {
                                            sumPct += 100;
                                            doneJobs++;
                                        } else {
                                            sumPct += jp;
                                            if (j.status === 'running' && j.progress?.current_file) {
                                                hostTxt = j.progress.current_file;
                                            }
                                        }
                                    }
                                    hostPct = sumPct / totalJobs;
                                }
                            }
                            // Fallback to legacy single-progress
                            if (hostPct === 0 && d.progress) {
                                hostPct = d.progress.total_pct || 0;
                                hostTxt = d.progress.current_file || '處理中...';
                            }

                            info.pct = hostPct;
                            if (hostPct > 0) {
                                updateHostProgress(ip, Math.floor(hostPct), `[${Math.floor(hostPct)}%] ${hostTxt}`, '#3b82f6');
                            }

                            // If worker is idle, queue empty, and at least 15s have passed since submission
                            if (!d.busy && d.queue_length === 0 && (now - info.startTime > 15000)) {
                                info.done = true;
                                info.pct = 100;
                                const _jl = JOB_LABELS[window._remoteJobType] || '任務';
                                updateHostProgress(ip, 100, `✅ ${_jl}完成`, '#228b22');
                            }
                        }
                    } catch (_) { }
                    if (!info.done && now - info.lastSeen > 60000) {
                        updateHostProgress(ip, 0, '⚠️ 逾時', '#b45309');
                        if (typeof appendLog === 'function') appendLog('⚠️ ' + info.host.name + ' (' + ip + ') 逾時', 'error');
                        info.done = true;
                    }
                }

                // 更新主進度條（多機加總進度 — 用各主機實際進度的平均值）
                const _allHosts = Object.values(window._activeRemoteHosts || {});
                if (_allHosts.length > 0) {
                    const _doneCount = _allHosts.filter(h => h.done).length;
                    const _sumPct = _allHosts.reduce((s, h) => s + (h.pct || 0), 0);
                    const _aggPct = Math.round(_sumPct / _allHosts.length);
                    const _tab = window._activeJobTab || 'backup';

                    if (_tab === 'backup') {
                        // 備份 TAB：更新轉檔段的進度（聚合所有主機）
                        const _doTrans = document.getElementById('chk_transcode')?.checked ?? false;
                        const _doConcat = document.getElementById('chk_concat')?.checked ?? false;
                        const _doReport = !!window._backupReportPending || !document.getElementById('bk-seg-report')?.classList.contains('hidden');
                        const _sc = 1 + (_doTrans ? 1 : 0) + (_doConcat ? 1 : 0) + (_doReport ? 1 : 0);
                        const _segW = 100 / _sc;
                        const bkSegBackup = document.getElementById('bk-seg-backup');
                        const bkSegTrans = document.getElementById('bk-seg-trans');
                        const bkLblTrans = document.getElementById('bk-lbl-trans');
                        const bkProgLabel = document.getElementById('bk-prog-label');
                        if (bkSegBackup) { bkSegBackup.style.width = `${_segW}%`; bkSegBackup.style.backgroundColor = '#1f538d'; }
                        if (bkSegTrans) { bkSegTrans.style.width = `${(_aggPct / 100) * _segW}%`; bkSegTrans.style.backgroundColor = '#d48a04'; }
                        if (bkLblTrans) bkLblTrans.textContent = `${_aggPct}%`;
                        document.getElementById('bk-lbl-backup').textContent = '100%';
                        if (bkProgLabel) bkProgLabel.textContent = `遠端轉檔　${_doneCount}/${_allHosts.length} 台完成 (${_aggPct}%)`;
                    } else {
                        // 其他 TAB：單一進度條
                        const _pfxMap = { transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts' };
                        const _pfx = _pfxMap[_tab];
                        if (_pfx) {
                            const _bar = document.getElementById(_pfx + '-prog-bar');
                            const _lbl = document.getElementById(_pfx + '-prog-label');
                            if (_bar) _bar.style.width = Math.max(5, _aggPct) + '%';
                            const _jl2 = JOB_LABELS[window._remoteJobType] || '任務';
                            if (_lbl) _lbl.textContent = `遠端${_jl2}　${_doneCount}/${_allHosts.length} 台完成 (${_aggPct}%)`;
                        }
                    }
                }

                // Check if all hosts have completed their chunks
                const hosts = _allHosts;
                if (hosts.length > 0 && hosts.every(h => h.done)) {
                    stopHeartbeatMonitor();

                    // Small buffer to allow the UI to reflect 100% state before fetching
                    setTimeout(() => {
                        const _rjt = window._remoteJobType || 'transcode';
                        // 只有 transcode（多機備份流程）才需要合併
                        if (_rjt === 'transcode') {
                            const ms = document.getElementById('merge_status_text');
                            if (ms) ms.textContent = '所有遠端主機任務結束，自動觸發整合程序…';
                            if (typeof appendLog === 'function') appendLog('系統提示：所有遠端任務已完成，自動觸發合併與驗證程序...', 'system');
                            mergeHostOutputs();
                        } else {
                            // 其他 TAB（verify/concat/report/transcribe/tts）：直接顯示完成
                            const _jl = JOB_LABELS[_rjt] || '任務';
                            if (typeof appendLog === 'function') appendLog(`✅ 遠端${_jl}任務已完成。`, 'system');
                            // 更新主進度條為完成狀態（嘗試 dash 和 underscore 兩種命名）
                            const _tab = window._activeJobTab || 'backup';
                            const _pfxMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts' };
                            const _pfx = _pfxMap[_tab];
                            if (_pfx) {
                                const _bar = document.getElementById(_pfx + '-prog-bar') || document.getElementById(_pfx + '_prog_bar');
                                const _lbl = document.getElementById(_pfx + '-prog-label') || document.getElementById(_pfx + '_prog_label');
                                const _eta = document.getElementById(_pfx + '-prog-eta') || document.getElementById(_pfx + '_prog_eta');
                                const _pct = document.getElementById(_pfx + '-prog-pct') || document.getElementById(_pfx + '_prog_pct');
                                const _area = document.getElementById(_pfx + '-progress') || document.getElementById(_pfx + '_progress_area');
                                if (_area) _area.classList.remove('hidden');
                                if (_bar) { _bar.style.width = '100%'; _bar.style.background = 'linear-gradient(90deg, #22c55e, #4ade80)'; }
                                if (_lbl) _lbl.textContent = `✅ 遠端${_jl}完成`;
                                if (_eta) _eta.textContent = '';
                                if (_pct) _pct.textContent = '100%';
                            }
                            stopHeartbeatMonitor();
                            // 報表完成時刷新歷史列表
                            if (_rjt === 'report' && typeof loadReportHistory === 'function') {
                                loadReportHistory();
                            }
                            updateActionBarState('idle');
                            playDing();
                        }
                    }, 2000);
                }
            }, 5000);
        }

        function stopHeartbeatMonitor() {
            if (window._heartbeatTimer) { clearInterval(window._heartbeatTimer); window._heartbeatTimer = null; }
            window._remoteDispatching = false;
        }

        // 顯示對應 TAB 的主進度條（多機模式，不經過 progress Socket 事件）
        function showRemoteMainProgress(label) {
            const tab = window._activeJobTab || 'backup';
            const pfxMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts' };
            const pfx = pfxMap[tab];
            if (!pfx) return;
            const container = document.getElementById(pfx + '-progress');
            const bar = document.getElementById(pfx + '-prog-bar');
            const lbl = document.getElementById(pfx + '-prog-label');
            if (container) container.classList.remove('hidden');
            if (bar) { bar.style.width = '5%'; bar.style.backgroundColor = '#3b82f6'; }
            if (lbl) lbl.textContent = label || '遠端執行中...';
        }

        // Export remote host functions to window for tab JS access
        window.initRemoteHostProgress = initRemoteHostProgress;
        window.updateHostProgress = updateHostProgress;
        window.startHeartbeatMonitor = startHeartbeatMonitor;
        window.stopHeartbeatMonitor = stopHeartbeatMonitor;
        window.showRemoteMainProgress = showRemoteMainProgress;
        window.dispatchRemoteTranscode = dispatchRemoteTranscode;

        async function dispatchRemoteTranscode(ctx) {
            window._remoteJobType = 'transcode';
            window._remoteDispatching = true; // 防止 task_status:running 觸發 resetProgress
            if (!ctx || !ctx.hosts || !ctx.hosts.length) { window._remoteDispatching = false; return; }
            if (typeof appendLog === 'function') appendLog('🖥️ 分派轉檔任務給遠端主機...', 'system');
            showRemoteMainProgress('分散式轉檔：派發中...');
            initRemoteHostProgress(ctx.hosts);

            // Pre-flight ping
            const reachable = [];
            await Promise.all(ctx.hosts.map(async h => {
                updateHostProgress(h.ip, 2, 'Ping...', '#4b5563');
                try {
                    const ctrl = new AbortController();
                    const t = setTimeout(() => ctrl.abort(), 3000);
                    const r = await fetch('http://' + h.ip + '/api/v1/health', { signal: ctrl.signal });
                    clearTimeout(t);
                    if (r.ok) {
                        updateHostProgress(h.ip, 5, '✅ 連線正常', '#228b22');
                        if (typeof appendLog === 'function') appendLog('✅ ' + h.name + ' (' + h.ip + ') OK', 'system');
                        reachable.push(h);
                    } else {
                        updateHostProgress(h.ip, 0, '❌ HTTP ' + r.status, '#8b0000');
                    }
                } catch (_) {
                    updateHostProgress(h.ip, 0, '❌ 無法連線', '#8b0000');
                    if (typeof appendLog === 'function') appendLog('❌ ' + h.name + ' 無法連線', 'error');
                }
            }));

            if (!reachable.length) {
                if (typeof appendLog === 'function') appendLog('❌ 所有遠端主機均無法連線，分派取消。', 'error');
                return;
            }
            ctx = Object.assign({}, ctx, { hosts: reachable });

            // ── 掃描來源：按卡分別掃描，保留卡名 ──────────────────────────────
            // cards: [[cardName, srcPath], ...] 或 scanDir fallback
            const cardEntries = []; // [{ cardName, files: [] }]
            const cards = ctx.cards || [];

            if (cards.length > 0) {
                if (ctx.use_absolute_paths) {
                    // Bypass localRoot fallback — sources are already absolute
                    for (const card of cards) {
                        const cardName = card[0];
                        const absoluteSrcPath = card[2];
                        if (!absoluteSrcPath) continue;
                        try {
                            const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ path: absoluteSrcPath, exts: ['.mov', '.mp4', '.mkv', '.mxf', '.avi', '.mts', '.m2ts', '.r3d', '.braw'] })
                            });
                            if (r.ok) {
                                const d = await r.json();
                                if (d.status === 'error') {
                                    if (typeof appendLog === 'function') appendLog('⚠️ [' + cardName + '] 路徑無效或無法讀取: ' + d.message, 'error');
                                } else if (d.files && d.files.length > 0) {
                                    cardEntries.push({ cardName, files: d.files, cardDir: absoluteSrcPath });
                                    if (typeof appendLog === 'function') appendLog('📁 ' + cardName + ': ' + d.files.length + ' 個影片 (Standalone)', 'system');
                                } else {
                                    if (typeof appendLog === 'function') appendLog('⚠️ [' + cardName + '] 找不到任何符合的影片檔案！', 'error');
                                }
                            }
                        } catch (e) { if (typeof appendLog === 'function') appendLog('⚠️ 掃描 ' + cardName + ' 失敗: ' + e.message, 'error'); }
                    }
                } else {
                    // 有記憶卡資訊：按卡掃 (Main Flow - requires backup structure mapping)
                    const localRoot = ctx.local_root || (document.getElementById('local_root') || {}).value || '';
                    for (const [cardName] of cards) {
                        const cardDir = localRoot ? localRoot + '/' + ctx.project_name + '/' + cardName : '';
                        if (!cardDir) continue;
                        try {
                            const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ path: cardDir, exts: ['.mov', '.mp4', '.mkv', '.mxf', '.avi', '.mts', '.m2ts', '.r3d', '.braw'] })
                            });
                            if (r.ok) {
                                const d = await r.json();
                                if (d.status === 'error') {
                                    if (typeof appendLog === 'function') appendLog('⚠️ [' + cardName + '] 路徑無效或無法讀取: ' + d.message, 'error');
                                } else if (d.files && d.files.length > 0) {
                                    cardEntries.push({ cardName, files: d.files, cardDir });
                                    if (typeof appendLog === 'function') appendLog('📁 ' + cardName + ': ' + d.files.length + ' 個影片', 'system');
                                } else {
                                    if (typeof appendLog === 'function') appendLog('⚠️ [' + cardName + '] 找不到任何符合的影片檔案！', 'error');
                                }
                            }
                        } catch (e) { if (typeof appendLog === 'function') appendLog('⚠️ 掃描 ' + cardName + ' 失敗: ' + e.message, 'error'); }
                    }
                }
            } else {
                // Fallback：掃 project 目錄，card 名稱設為空
                const localRoot = ctx.local_root || (document.getElementById('local_root') || {}).value || '';
                const projDir = localRoot ? localRoot + '/' + ctx.project_name : '';
                if (projDir) {
                    try {
                        const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ path: projDir, exts: ['.mov', '.mp4', '.mkv', '.mxf', '.avi', '.mts', '.m2ts', '.r3d', '.braw'] })
                        });
                        if (r.ok) { const d = await r.json(); if (d.files && d.files.length) cardEntries.push({ cardName: '', files: d.files }); }
                    } catch (e) { if (typeof appendLog === 'function') appendLog('⚠️ 無法掃描來源: ' + e.message, 'error'); }
                }
            }

            const totalFiles = cardEntries.reduce((s, c) => s + c.files.length, 0);
            if (totalFiles === 0) {
                if (typeof appendLog === 'function') appendLog('⚠️ 找不到來源檔案，分派取消。', 'error');
                reachable.forEach(h => updateHostProgress(h.ip, 0, '找不到來源', '#8b0000'));
                return;
            }

            // 建立預期產出清單 (For Verification)
            // 檔名規則: basename + "_proxy.mov"，子目錄結構應予保留
            const expectedFiles = {};
            for (const entry of cardEntries) {
                expectedFiles[entry.cardName] = {};
                // If the standalone tab passes a specific 'cardDir' directly to map structural depths, use it as basePath.
                // Otherwise, fallback to 'projDir' mapping from the root backup.
                const basePath = entry.cardDir || projDir; 
                for (const fileAbs of entry.files) {
                    let relPath = fileAbs;
                    const normFileAbs = fileAbs.replace(/\\/g, '/');
                    const normBasePath = basePath ? basePath.replace(/\\/g, '/') : '';
                    if (normBasePath && normFileAbs.startsWith(normBasePath)) {
                        relPath = normFileAbs.substring(normBasePath.length).replace(/^[\\\/]+/, '');
                    }
                    let parentDir = '';
                    const parts = relPath.replace(/\\/g, '/').split('/');
                    if (parts.length > 1) {
                        // The main backup structure only preserves one level of internal directories, standardly.
                        parentDir = parts[parts.length - 2] + '/';
                    }
                    const basename = parts[parts.length - 1].replace(/\.[^/.]+$/, "");
                    const expectedProxyPath = parentDir + basename + "_proxy.mov";
                    // Store the mapping: expectedProxyPath -> original absolute source path
                    expectedFiles[entry.cardName][expectedProxyPath] = fileAbs;
                }
            }
            window._remoteDispatchExpected = expectedFiles;

            // ── 分派：將每張卡的檔案按輪轉 round-robin 分配給各遠端主機 ──────────
            // 先把所有卡的 [cardName, file] 攤平，再 round-robin 給主機
            const allCardFiles = []; // [{ cardName, file }]
            for (const { cardName, files } of cardEntries) {
                for (const file of files) allCardFiles.push({ cardName, file });
            }

            const n = reachable.length;
            if (typeof appendLog === 'function') appendLog('📋 共 ' + totalFiles + ' 個檔案（' + cardEntries.length + ' 張卡），分配給 ' + n + ' 台主機', 'system');

            // 每台主機建立 { cardName -> [files] } 的 map
            const hostCardMaps = reachable.map(() => ({})); // [{cardName: [files]}, ...]
            allCardFiles.forEach(({ cardName, file }, idx) => {
                const hostIdx = idx % n;
                if (!hostCardMaps[hostIdx][cardName]) hostCardMaps[hostIdx][cardName] = [];
                hostCardMaps[hostIdx][cardName].push(file);
            });

            window._activeRemoteHosts = {};
            for (let i = 0; i < reachable.length; i++) {
                const h = reachable[i];
                const cardMap = hostCardMaps[i];
                const cardNames = Object.keys(cardMap);
                if (!cardNames.length) { updateHostProgress(h.ip, 100, '無分配檔案', '#374151'); continue; }

                const totalForHost = cardNames.reduce((s, c) => s + cardMap[c].length, 0);
                updateHostProgress(h.ip, 10, '送出中... (' + totalForHost + ' 個)', '#1f538d');

                // 每張卡對此主機各送一個 transcode job，dest_dir 包含卡名
                let hostOk = false;
                for (const cardName of cardNames) {
                    const files = cardMap[cardName];
                    // dest_dir: proxy_root/project_name/HostDispatch_主機名/卡名
                    const cardSuffix = cardName ? '/' + cardName : '';
                    const dest = ctx.proxy_root
                        ? ctx.proxy_root + '/' + ctx.project_name + '/HostDispatch_' + h.name.replace(/\s+/g, '_') + cardSuffix
                        : '';
                    try {
                        if (typeof appendLog === 'function') appendLog('→ 送出 [' + (cardName || '(all)') + '] ' + files.length + ' 個給 ' + h.name, 'system');
                        const r = await fetch('http://' + h.ip + '/api/v1/jobs/transcode', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ sources: files, dest_dir: dest })
                        });
                        const res = await r.json();
                        if (typeof appendLog === 'function') appendLog('✅ ' + h.name + ' [' + (cardName || 'all') + '] 接收，任務 ID: ' + (res.job_id || '?'), 'system');
                        hostOk = true;
                    } catch (err) {
                        if (typeof appendLog === 'function') appendLog('❌ 無法連線到 ' + h.name + ': ' + err.message, 'error');
                    }
                }
                if (hostOk) {
                    updateHostProgress(h.ip, 20, '轉檔中...', '#d48a04');
                    window._activeRemoteHosts[h.ip] = { host: h, files: allCardFiles.map(cf => cf.file), lastSeen: Date.now(), startTime: Date.now(), expectedJobs: cardNames.length, pct: 0 };
                } else {
                    updateHostProgress(h.ip, 0, '連線失敗', '#8b0000');
                }
            }
            if (Object.keys(window._activeRemoteHosts).length) {
                startHeartbeatMonitor();
            }
        }

        // Step 6: Merge
        async function mergeHostOutputs() {
            const proxyRoot = window._isStandaloneTranscode
                ? (document.getElementById('tc_dest') || {}).value || ''
                : (document.getElementById('proxy_root') || {}).value || '';
            const projName = window._isStandaloneTranscode
                ? (document.getElementById('tc_proj_name') || {}).value || ''
                : (document.getElementById('proj_name') || {}).value || '';
            if (!proxyRoot || !projName) {
                if (typeof appendLog === 'function') appendLog('請先填寫 Proxy Root 與專案名稱。', 'error'); return;
            }
            if (typeof appendLog === 'function') appendLog('📁 合併遠端主機輸出...', 'system');
            const ms = document.getElementById('merge_status_text');
            if (ms) ms.textContent = '合併中…';
            try {
                const r = await fetch(getComputeBaseUrl() + '/api/v1/merge_host_outputs', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proxy_root: proxyRoot, project_name: projName })
                });
                const d = await r.json();
                if (d.status === 'ok') {
                    if (typeof appendLog === 'function') appendLog('✅ 合併完成！共 ' + d.merged + ' 個檔案。', 'system');
                    stopHeartbeatMonitor();

                    // 先定義驗證過關後執行的後續作業
                    window.executePostMergeJobs = function(flags) {
                        const ms = document.getElementById('merge_status_text');
                        if (ms) ms.textContent = '完成';
                        if (flags && (flags.do_concat || flags.do_report)) {
                            if (typeof appendLog === 'function') appendLog('🔄 自動觸發後續作業...', 'system');
                            setTimeout(async () => {
                                try {
                                    // ── 串帶 ──
                                    if (flags.do_concat && Array.isArray(flags.cards) && flags.cards.length) {
                                        const concatUrl = (flags.concat_host_url || getComputeBaseUrl()) + '/api/v1/jobs/concat';
                                        const concatHostName = flags.concat_host_name || '本機';
                                        if (typeof appendLog === 'function') appendLog('🏗️ 串帶將由 [' + concatHostName + '] 執行', 'system');
                                        // 追蹤多卡串帶進度
                                        window._concatMultiCard = { total: flags.cards.length, done: 0, jobIds: [] };
                                        for (let ci = 0; ci < flags.cards.length; ci++) {
                                            const cardEntry = flags.cards[ci];
                                            // Cards can be [cardName, srcPath] or [cardName, srcPath, absSrcPath]
                                            const cardName = Array.isArray(cardEntry) ? cardEntry[0] : cardEntry;
                                            if (!cardName) continue;
                                            const concatSrcDir = flags.proxy_root + '/' + flags.project_name + '/' + cardName;
                                            const concatDestDir = flags.proxy_root + '/' + flags.project_name + '/' + cardName;
                                            const concatPayload = {
                                                sources: [concatSrcDir],
                                                dest_dir: concatDestDir,
                                                custom_name: flags.project_name + '_' + cardName + '_reel',
                                                resolution: flags.concat_resolution || '720P',
                                                codec: flags.concat_codec || 'H.264 (NVENC)',
                                                burn_timecode: flags.concat_burn_tc ?? true,
                                                burn_filename: flags.concat_burn_fn ?? false
                                            };
                                            const r3 = await fetch(concatUrl, {
                                                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(concatPayload)
                                            });
                                            const j3 = await r3.json();
                                            if (typeof appendLog === 'function') appendLog('📌 串帶 [' + cardName + '] 排隊中，任務 ID: ' + (j3.job_id || '?'), 'system');
                                        }
                                    }

                                    // ── 報表 ──
                                    if (flags.do_report) {
                                        const localDir = flags.local_root + '/' + flags.project_name;
                                        const reportPayload = {
                                            source_dir: localDir,
                                            output_dir: flags.report_output || flags.local_root,
                                            nas_root: flags.nas_root || '',
                                            report_name: flags.report_name || flags.project_name,
                                            do_filmstrip: flags.report_filmstrip ?? true,
                                            do_techspec: flags.report_techspec ?? true,
                                            do_hash: flags.report_hash ?? false,
                                            do_gdrive: false, do_gchat: false, do_line: false,
                                            exclude_dirs: flags.proxy_root ? [flags.proxy_root + '/' + flags.project_name] : [],
                                            client_sid: window.socket?.id || ''
                                        };
                                        const r4 = await fetch(getComputeBaseUrl() + '/api/v1/report_jobs', {
                                            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(reportPayload)
                                        });
                                        const j4 = await r4.json();
                                        if (j4.job_id) {
                                            window._myReportJobIds = window._myReportJobIds || new Set();
                                            window._myReportJobIds.add(j4.job_id);
                                        }
                                        if (typeof appendLog === 'function') appendLog('📊 報表任務已提交: ' + j4.status, 'system');
                                    }
                                    window._postMergeFlags = null;
                                } catch (e2) {
                                    if (typeof appendLog === 'function') appendLog('❌ 後續作業提交失敗: ' + e2.message, 'error');
                                }
                            }, 1500);
                        }
                    };


                    // ── 驗證 Proxy 完整性（使用後端 compare_source，與轉 Proxy TAB 邏輯一致）─────
                    window.verifyAndRetryMissingProxies = async function(proxyRoot, projName, flags) {
                        const cards = (flags && Array.isArray(flags.cards)) ? flags.cards : [];
                        const localRoot = flags ? flags.local_root : '';

                        // 若無卡匣資訊，直接跳過驗證執行後續作業
                        if (!localRoot || !proxyRoot || !projName || cards.length === 0) {
                            if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                            return;
                        }

                        if (typeof appendLog === 'function') appendLog('🔍 正在驗證 Proxy 轉檔完整性（後端掃描比對）...', 'system');
                        const ms = document.getElementById('merge_status_text');
                        if (ms) ms.textContent = '驗證檔案中…';

                        try {
                            // ── 逐卡呼叫後端 compare_source（與轉 Proxy TAB 邏輯完全相同）
                            const allMissing = []; // [{ cardName, sourceFile }]

                            // All cards share the same proxy output dir (flat, no card subfolder)
                            const sharedProxyDir = proxyRoot.replace(/\\/g, '/') + '/' + projName;

                            for (let ci = 0; ci < cards.length; ci++) {
                                const cardEntry = cards[ci];
                                const cardName = Array.isArray(cardEntry) ? cardEntry[0] : cardEntry;
                                // cards[1] = original source path (e.g. Y:\原始素材\card_A)
                                const cardSrcPath = Array.isArray(cardEntry) && cardEntry[1] ? cardEntry[1] : null;
                                if (!cardName) continue;

                                // ── 智慧來源路徑：優先用「原始素材路徑」，找不到自動降級到「備份副本路徑」──
                                const backupCopyDir = (localRoot.replace(/\\/g, '/') + '/' + projName + '/' + cardName);
                                let sourceDir = cardSrcPath || backupCopyDir;
                                // 【優化】精準指定到個別卡匣的專屬資料夾，防止跨卡同名檔案互相干擾
                                const proxyDir  = sharedProxyDir + '/' + cardName;

                                if (typeof appendLog === 'function') appendLog(`🔍 [${cardName}] 比對來源: ${sourceDir} → ${proxyDir}`, 'system');

                                try {
                                    let r = await fetch(getComputeBaseUrl() + '/api/v1/compare_source', {
                                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ source_dir: sourceDir, output_dir: proxyDir, flat_proxy: true })
                                    });
                                    let d = await r.json();

                                    // ── 若原始路徑不可達（已拔卡/離線），自動降級用備份副本 ──
                                    if (d.status === 'error' && cardSrcPath && sourceDir === cardSrcPath) {
                                        if (typeof appendLog === 'function') appendLog(`⚠️ [${cardName}] 原始路徑不可達，改用備份副本: ${backupCopyDir}`, 'system');
                                        sourceDir = backupCopyDir;
                                        r = await fetch(getComputeBaseUrl() + '/api/v1/compare_source', {
                                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ source_dir: sourceDir, output_dir: proxyDir, flat_proxy: true })
                                        });
                                        d = await r.json();
                                    }

                                    if (d.status === 'ok') {
                                        const missing = Array.isArray(d.missing) ? d.missing : [];
                                        if (typeof appendLog === 'function') appendLog(`📋 [${cardName}] 來源 ${d.source_count} 個，Proxy ${d.proxy_count} 個，缺少 ${missing.length} 個`, 'system');
                                        missing.forEach(srcPath => allMissing.push({ cardName, sourceFile: srcPath }));
                                    } else {
                                        if (typeof appendLog === 'function') appendLog(`⚠️ [${cardName}] 驗證仍失敗: ${d.message || JSON.stringify(d)}`, 'error');
                                    }
                                } catch (cardErr) {
                                    if (typeof appendLog === 'function') appendLog(`⚠️ [${cardName}] 驗證失敗: ${cardErr.message}`, 'error');
                                }
                            }

                            if (allMissing.length === 0) {
                                if (typeof appendLog === 'function') appendLog('✅ 所有 Proxy 檔案皆已正常產出！', 'system');
                                if (ms) ms.textContent = '驗證完成';
                                if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                                return;
                            }

                            // ── 有缺漏：啟動補轉重試機制
                            window._remoteDispatchExpectedRetryCount = (window._remoteDispatchExpectedRetryCount || 0) + 1;
                            if (typeof appendLog === 'function') appendLog(`⚠️ 發現 ${allMissing.length} 個缺失的 Proxy 檔案，啟動補轉 (第 ${window._remoteDispatchExpectedRetryCount} 次)...`, 'error');

                            if (window._remoteDispatchExpectedRetryCount > 2) {
                                if (typeof appendLog === 'function') appendLog('❌ 補件重試已達上限 (2次)，放棄重試，強行啟動後續作業。', 'error');
                                if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                                return;
                            }

                            // ── 派發補轉：round-robin 分配給存活的遠端主機
                            const activeHostsObj = window._activeRemoteHosts || {};
                            const activeHostNames = Object.keys(activeHostsObj);
                            if (activeHostNames.length === 0) {
                                if (typeof appendLog === 'function') appendLog('❌ 無存活的遠端主機可補轉，強行啟動後續作業。', 'error');
                                if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                                return;
                            }

                            const reachable = activeHostNames.map(ip => ({
                                ip,
                                name: (activeHostsObj[ip].host && activeHostsObj[ip].host.name) || ip
                            }));
                            const distributions = reachable.map(h => ({ host: h, byCard: {} }));
                            allMissing.forEach(({ cardName, sourceFile }, i) => {
                                const dist = distributions[i % distributions.length];
                                if (!dist.byCard[cardName]) dist.byCard[cardName] = [];
                                dist.byCard[cardName].push(sourceFile);
                            });

                            let requestsStarted = 0;
                            for (const dist of distributions) {
                                for (const [cardName, srcFiles] of Object.entries(dist.byCard)) {
                                    const remoteUrl = `http://${dist.host.ip}/api/v1/jobs/transcode`;
                                    const destDir = proxyRoot + '/' + projName + '/' + cardName;
                                    try {
                                        fetch(remoteUrl, {
                                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ sources: srcFiles, dest_dir: destDir })
                                        }).then(res => res.json()).then(j => {
                                            if (typeof appendLog === 'function') appendLog(`📌 主機 ${dist.host.name} [${cardName}] 補轉排隊，任務 ID: ${j.job_id || '?'}`, 'system');
                                        }).catch(err => {
                                            if (typeof appendLog === 'function') appendLog(`⚠️ 主機 ${dist.host.name} [${cardName}] 補轉派發失敗: ${err.message}`, 'error');
                                        });
                                        requestsStarted++;
                                    } catch(e) {}
                                }
                            }

                            if (requestsStarted > 0 && typeof window.startHeartbeatMonitor === 'function') {
                                window.startHeartbeatMonitor();
                            } else {
                                if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                            }

                        } catch (e) {
                            if (typeof appendLog === 'function') appendLog('❌ 驗證時發生錯誤: ' + e.message, 'error');
                            if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                        }
                    };

                    // 啟動驗證 (加入 2.5 秒延遲，等待 NAS 檔案系統完全落盤，避免驗證與寫入的 Race Condition)
                    setTimeout(() => {
                        if (window._isStandaloneTranscode) {
                            if (window.verifyStandaloneProxies) window.verifyStandaloneProxies();
                        } else {
                            if (window.verifyAndRetryMissingProxies) {
                                window.verifyAndRetryMissingProxies(proxyRoot, projName, window._postMergeFlags);
                            }
                        }
                    }, 2500);

                } else { if (typeof appendLog === 'function') appendLog('❌ 合併失敗: ' + d.message, 'error'); }
            } catch (e) { if (typeof appendLog === 'function') appendLog('❌ 合併錯誤: ' + e.message, 'error'); }
        }


        // submitJob & retryLastJob migrated to backup.js

        // 輪詢遠端主機進度，直到任務完成
        async function pollRemoteHostProgress(hostUrl, hostName) {
            let offset = 0;
            const maxPolls = 300; // 最多輪詢 10 分鐘
            appendLog(`[${hostName}] 開始監控遠端進度...`, 'system');
            for (let i = 0; i < maxPolls; i++) {
                await new Promise(r => setTimeout(r, 2000));
                try {
                    const res = await fetch(hostUrl + '/api/v1/status?log_offset=' + offset, { signal: AbortSignal.timeout(5000) });
                    if (!res.ok) { appendLog(`[${hostName}] 狀態查詢失敗 (${res.status})`, 'error'); break; }
                    const data = await res.json();
                    // 顯示新日誌
                    (data.logs || []).forEach(line => appendLog(`[${hostName}] ${line}`, 'system'));
                    offset = data.new_log_offset || offset;
                    // 結束條件：不 busy 且 queue 空
                    if (!data.busy && data.queue_length === 0) {
                        appendLog(`[${hostName}] ✅ 任務完成`, 'system');
                        return;
                    }
                } catch (e) {
                    appendLog(`[${hostName}] ❌ 無法連線: ${e.message}`, 'error');
                    return;
                }
            }
            appendLog(`[${hostName}] ⏰ 監控逾時`, 'error');
        }




        // ── 統一按鈕列狀態切換（DOM refs 延遲快取）──
        function updateActionBarState(state) {
            document.querySelectorAll('.tab-control-btns').forEach(el => {
                el.classList.toggle('hidden', state === 'idle');
            });
            document.querySelectorAll('.tab-start-btn').forEach(btn => {
                const idle = btn.dataset.idleText || '開始';
                const busy = btn.dataset.busyText || '開始新佇列';
                btn.textContent = state === 'idle' ? idle : busy;
            });
        }
        window.updateActionBarState = updateActionBarState;

        async function apiControl(cmd) {
            const cmdLabel = cmd === 'pause' ? '暫停' : cmd === 'resume' ? '繼續' : '強制中止';

            // 1. 發送給本機
            try {
                await fetch(getComputeBaseUrl() + `/api/v1/control/${cmd}`, { method: 'POST' });
                appendLog(`[本機] ${cmdLabel} 成功`, 'system');
            } catch (err) {
                appendLog(`[本機] ${cmdLabel} 失敗: ${err.message}`, 'error');
            }

            // 2. 發送給所有活躍中的遠端主機
            const activeHosts = Object.keys(window._activeRemoteHosts || {});
            for (const ip of activeHosts) {
                try {
                    await fetch('http://' + ip + `/api/v1/control/${cmd}`, { method: 'POST' });
                    appendLog(`[${ip}] ${cmdLabel} 成功`, 'system');
                } catch (e) {
                    appendLog(`[${ip}] ${cmdLabel} 失敗: ${e.message}`, 'error');
                }
            }

            // 3. 強制中止：清理所有狀態
            if (cmd === 'stop') {
                window._remoteDispatch = null;
                window._postMergeFlags = null;
                window._backupPipeline = null;
                window._backupReportPending = false;
                window._backupFinalShown = false;
                window._concatMultiCard = null;
                window._activeRemoteHosts = {};
                if (window._heartbeatTimer) { clearInterval(window._heartbeatTimer); window._heartbeatTimer = null; }
                window._remoteDispatching = false;
                updateActionBarState('idle');
                if (typeof resetProgress === 'function') resetProgress();
                appendLog('❌ 已全部強制中止（本機 + 所有遠端主機）', 'error');
            }
        }



        function updateComputeModeStyle() {
            const selectEl = document.getElementById('compute_mode');
            if (selectEl.value === 'remote') {
                selectEl.className = "bg-[#4a0000] text-sm border border-[#ff4444] rounded px-2 py-1 focus:outline-none focus:border-red-500 shadow-[0_0_8px_rgba(255,0,0,0.3)]";
            } else {
                selectEl.className = "bg-[#333] text-sm border border-[#555] rounded px-2 py-1 focus:outline-none focus:border-blue-500";
            }
        }

        function showInstallModal() {
            document.getElementById('install-modal').classList.remove('hidden');
        }

        // ─── Settings Modal ───────────────────────────────────────────
        // Global tab-switch function (called from inline onclick on tab buttons)
        function switchSettingsTab(tabId, event) {
            document.querySelectorAll('#settingsModal .tab-content').forEach(el => el.style.display = 'none');
            document.querySelectorAll('#settingsModal .tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab_' + tabId).style.display = 'block';
            (event || window.event).currentTarget.classList.add('active');
            if (tabId === 'user_mgmt') _loadUserList();
        }

        // ── User Management ──
        async function _loadUserList() {
            const container = document.getElementById('user-mgmt-list');
            if (!container) return;
            try {
                const r = await fetch('/api/v1/auth/users');
                if (!r.ok) { container.innerHTML = '<span style="color:#f87171">載入失敗（需要管理員權限）</span>'; return; }
                const users = await r.json();
                container.innerHTML = users.map(u => `
                    <div style="display:flex; align-items:center; gap:8px; padding:8px 0; border-bottom:1px solid #333;">
                        <span style="color:#fff; min-width:80px;">👤 ${u.username}</span>
                        <span style="color:#888; min-width:60px;">${u.role}</span>
                        <span style="color:#555; flex:1; font-size:11px;">${u.visible_tabs ? u.visible_tabs.join(', ') : '全部頁籤'}</span>
                        ${u.username !== 'admin' ? `
                            <button onclick="window._changeUserRole('${u.username}','${u.role}')" style="background:transparent; border:1px solid #555; color:#aaa; border-radius:3px; padding:2px 8px; cursor:pointer; font-size:11px;">改角色</button>
                            <button onclick="window._changeUserPwd('${u.username}')" style="background:transparent; border:1px solid #555; color:#aaa; border-radius:3px; padding:2px 8px; cursor:pointer; font-size:11px;">改密碼</button>
                            <button onclick="window._deleteUser('${u.username}')" style="background:transparent; border:1px solid #ef4444; color:#ef4444; border-radius:3px; padding:2px 8px; cursor:pointer; font-size:11px;">刪除</button>
                        ` : '<span style="color:#22c55e; font-size:11px;">系統管理員</span>'}
                    </div>
                `).join('');
            } catch (_) {
                container.innerHTML = '<span style="color:#f87171">載入失敗</span>';
            }
        }

        window._addUserPrompt = async function() {
            const username = prompt('新使用者帳號：');
            if (!username) return;
            const password = prompt('密碼：');
            if (!password) return;
            const role = prompt('角色（admin / editor / viewer）：', 'editor');
            if (!role) return;
            try {
                const r = await fetch('/api/v1/auth/users', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password, role }),
                });
                const d = await r.json();
                if (!r.ok) { alert(d.detail || '新增失敗'); return; }
                _loadUserList();
            } catch (_) { alert('連線失敗'); }
        };

        window._changeUserRole = async function(username, currentRole) {
            const role = prompt(`修改 ${username} 的角色（目前：${currentRole}）：`, currentRole);
            if (!role || role === currentRole) return;
            const r = await fetch('/api/v1/auth/users/' + username, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role }),
            });
            if (r.ok) _loadUserList(); else alert('修改失敗');
        };

        window._changeUserPwd = async function(username) {
            const password = prompt(`設定 ${username} 的新密碼：`);
            if (!password) return;
            const r = await fetch('/api/v1/auth/users/' + username, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            if (r.ok) alert('密碼已更新'); else alert('修改失敗');
        };

        window._deleteUser = async function(username) {
            if (!confirm(`確定要刪除使用者 "${username}"？`)) return;
            const r = await fetch('/api/v1/auth/users/' + username, { method: 'DELETE' });
            if (r.ok) _loadUserList(); else alert('刪除失敗');
        };

        // Wrapped in DOMContentLoaded so modal HTML (placed after this script)
        // is fully parsed before we try to bind event listeners.
        document.addEventListener('DOMContentLoaded', () => {
            const modal = document.getElementById('settingsModal');

            // ── Load settings when modal opens ───────────────────────
            document.getElementById('btnOpenSettings').addEventListener('click', async () => {
                modal.style.display = 'flex';
                try {
                    const res = await fetch('/api/settings/load');
                    if (res.ok) {
                        const data = await res.json();
                        const n = data.notifications || {};
                        const t = data.message_templates || {};
                        document.getElementById('line_token').value = n.line_notify_token || '';
                        document.getElementById('gchat_webhook').value = n.google_chat_webhook || '';
                        document.getElementById('custom_webhook').value = n.custom_webhook_url || '';
                        document.getElementById('tpl_backup_success').value = t.backup_success || '';
                        document.getElementById('tpl_report_success').value = t.report_success || '';
                        document.getElementById('tpl_transcode_success').value = t.transcode_success || '';
                        document.getElementById('tpl_concat_success').value = t.concat_success || '';
                        document.getElementById('tpl_verify_success').value = t.verify_success || '';
                        document.getElementById('tpl_transcribe_success').value = t.transcribe_success || '';
                        // ── Load channel toggles ──────────────────────────────────────
                        const ch = data.notification_channels || {};
                        const tabs = ['backup', 'report', 'transcode', 'concat', 'verify', 'transcribe'];
                        tabs.forEach(tab => {
                            const cfg = ch[tab] || { gchat: true, line: false };
                            document.querySelectorAll(`.ch-toggle[data-tab="${tab}"]`).forEach(el => {
                                const channel = el.dataset.ch;
                                const isOn = cfg[channel] !== undefined ? cfg[channel] : (channel === 'gchat');
                                el.classList.toggle('on', isOn);
                                el.classList.toggle('off', !isOn);
                            });
                        });
                    }
                } catch (e) { /* silent fail */ }
            });

            document.getElementById('btnCloseSettings').onclick = () => modal.style.display = 'none';
            document.getElementById('btnCancelSettings').onclick = () => modal.style.display = 'none';

            // ── Save settings ────────────────────────────────────────
            document.getElementById('btnSaveSettings').addEventListener('click', async () => {
                const settingsData = {
                    notifications: {
                        line_notify_token: document.getElementById('line_token').value,
                        google_chat_webhook: document.getElementById('gchat_webhook').value,
                        custom_webhook_url: document.getElementById('custom_webhook').value,
                    },
                    message_templates: {
                        backup_success: document.getElementById('tpl_backup_success').value,
                        report_success: document.getElementById('tpl_report_success').value,
                        transcode_success: document.getElementById('tpl_transcode_success').value,
                        concat_success: document.getElementById('tpl_concat_success').value,
                        verify_success: document.getElementById('tpl_verify_success').value,
                        transcribe_success: document.getElementById('tpl_transcribe_success').value,
                    },
                    // ── Channel toggles ──────────────────────────────
                    notification_channels: Object.fromEntries(
                        ['backup', 'report', 'transcode', 'concat', 'verify', 'transcribe'].map(tab => [
                            tab,
                            {
                                gchat: document.querySelector(`.ch-toggle.gchat[data-tab="${tab}"]`)?.classList.contains('on') ?? true,
                                line: document.querySelector(`.ch-toggle.line[data-tab="${tab}"]`)?.classList.contains('on') ?? false,
                            }
                        ])
                    ),
                };
                try {
                    const response = await fetch('/api/settings/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(settingsData)
                    });
                    if (response.ok) {
                        alert('✅ 設定已成功儲存！');
                        modal.style.display = 'none';
                    } else {
                        alert('❌ 儲存失敗，請檢查伺服器連線。');
                    }
                } catch (error) {
                    console.error('儲存設定發生錯誤:', error);
                    alert('❌ 儲存發生例外錯誤！');
                }
            });
            // ── Restart Agent ──────────────────────────────────────
            document.getElementById('btnRestartAgent').addEventListener('click', async () => {
                if (!confirm('確定要重新啟動本機 Agent？\n伺服器將短暫離線約 10 秒，並自動同步 NAS 最新版本。')) return;
                try {
                    await fetch('/api/admin/restart', { method: 'POST' });
                } catch (_) { /* server going down is expected */ }
                alert('⏳ Agent 正在重新啟動中，請等待約 15 秒後重新整理頁面。');
                modal.style.display = 'none';
            });
            // ── Channel toggle pills ────────────────────────────────
            document.getElementById('settingsModal').addEventListener('click', e => {
                const t = e.target.closest('.ch-toggle');
                if (!t) return;
                t.classList.toggle('on');
                t.classList.toggle('off');
            });
        });


// ── Shared collect-function map (used by schedule modal) ──
        const _collectMap = {
            'backup': 'collectBackupPayload',
            'transcode': 'collectTranscodePayload',
            'concat': 'collectConcatPayload',
            'verify': 'collectVerifyPayload',
            'transcribe': 'collectTranscribePayload',
            'tts': 'collectTtsPayload',
            'clone': 'collectClonePayload',
            'report': 'collectReportPayload',
        };

// ── Schedule Modal ──────────────────────────────
        let _scheduleModalData = null;

        function _initScheduleSelects() {
            const hourSel = document.getElementById('schedule-modal-hour');
            const minSel = document.getElementById('schedule-modal-min');
            if (!hourSel || hourSel.options.length) return;
            for (let h = 0; h < 24; h++) {
                const o = document.createElement('option');
                o.value = o.textContent = String(h).padStart(2, '0');
                hourSel.appendChild(o);
            }
            for (const m of ['00', '15', '30', '45']) {
                const o = document.createElement('option');
                o.value = o.textContent = m;
                minSel.appendChild(o);
            }
        }

        function scheduleJob(taskType) {
            const fnName = _collectMap[taskType];
            if (!fnName || typeof window[fnName] !== 'function') {
                alert('此任務類型暫不支援排程');
                return;
            }
            const result = window[fnName]();
            if (!result || !result.valid) return;

            _scheduleModalData = { taskType, payload: result.payload, name: result.name || '' };

            // Pre-fill modal
            _initScheduleSelects();
            const overlay = document.getElementById('schedule-modal-overlay');
            document.getElementById('schedule-modal-name').value = result.name || '';
            // Default date = tomorrow
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            document.getElementById('schedule-modal-date').value = tomorrow.toISOString().slice(0, 10);
            document.getElementById('schedule-modal-hour').value = '02';
            document.getElementById('schedule-modal-min').value = '00';
            overlay.style.display = 'flex';
        }

        function cancelScheduleModal() {
            document.getElementById('schedule-modal-overlay').style.display = 'none';
            _scheduleModalData = null;
        }

        async function confirmScheduleModal() {
            if (!_scheduleModalData) return;
            const name = document.getElementById('schedule-modal-name').value.trim();
            const date = document.getElementById('schedule-modal-date').value;
            const hh = document.getElementById('schedule-modal-hour').value;
            const mm = document.getElementById('schedule-modal-min').value;
            if (!name) { alert('請輸入排程名稱'); return; }
            if (!date) { alert('請選擇日期'); return; }

            const runAt = date + 'T' + hh + ':' + mm + ':00';
            const body = {
                name,
                run_at: runAt,
                task_type: _scheduleModalData.taskType,
                request: _scheduleModalData.payload,
            };

            try {
                const r = await fetch('/api/v1/schedules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    alert('排程建立失敗: ' + (err.detail || r.statusText));
                    return;
                }
                alert('排程已建立');
                cancelScheduleModal();
            } catch (e) {
                alert('排程建立失敗: ' + e.message);
            }
        }

        window.scheduleJob = scheduleJob;
        window.cancelScheduleModal = cancelScheduleModal;
        window.confirmScheduleModal = confirmScheduleModal;


// ─── Initialize on Page Load ─── //
        document.addEventListener('DOMContentLoaded', () => {
            // Load the NAS report history into the main Backup Tab dashboard right away
            if (typeof loadReportHistory === 'function') {
                loadReportHistory();
            }
            
            // Check model status immediately
            if (typeof fetchModelStatus === 'function') {
                fetchModelStatus();
            }
        });
