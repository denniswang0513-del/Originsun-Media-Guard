// ─── Originsun Media Guard Pro ─── //
// Entry point — imports all extracted modules, then defines core app logic.

// ── Module Imports (side-effect: each module registers its window.* globals) ──
import { TAB_MAP, TAB_LOADERS, shouldShowTab, TAB_GROUPS, groupKeys, groupForSection, isMediaSection } from './js/shared/tab-config.js';
import './js/shared/modal-styles.js';
import './js/auth/auth-state.js';
import './js/auth/login-modal.js';
import './js/auth/google-oauth.js';
import './js/admin/user-mgmt.js';
import './js/admin/role-mgmt.js';
import './js/admin/publish-mgmt.js';
import './js/admin/api-keys.js';
import './js/update/version-check.js';
import './js/update/update-modal.js';
import './js/settings/settings-modal.js';
import './js/shared/nas-browser.js';

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
        window.currentSocketUrl = window.location.origin;
        let socket = null;

        // Idempotent — re-running loadTabs() after login skips already-loaded
        // sections instead of clobbering their state.
        const _loadedTabs = new Set();

        async function loadTabs({ autoSwitch = true } = {}) {
            try {
                const _cb = `?t=${Date.now()}`;

                // Wait for auth so we know which modules to load
                await window._authReady;
                const modules = window._modules;
                const hasModules = !!window._authUser && modules && modules.length > 0;

                // Hide nav buttons & sections for unauthorized tabs immediately
                Object.entries(TAB_MAP).forEach(([key, tabId]) => {
                    if (!_authed(key)) {
                        const btn = document.getElementById('btn_' + tabId);
                        if (btn) btn.style.display = 'none';
                        const sec = document.getElementById(tabId);
                        if (sec) sec.style.display = 'none';
                    }
                });

                // Helper: load a single tab. fetch(html) and import(js) run in
                // parallel — the module doesn't depend on the DOM until initFn
                // runs, so awaiting them sequentially wastes ~1 RTT per tab.
                const _loadTab = async (sectionId, htmlPath, jsPath, initFn) => {
                    if (_loadedTabs.has(sectionId)) return;
                    try {
                        const el = document.getElementById(sectionId);
                        if (!el) return;
                        const [res, mod] = await Promise.all([
                            fetch(`${htmlPath}${_cb}`),
                            import(`${jsPath}${_cb}`),
                        ]);
                        if (res.ok) {
                            el.innerHTML = await res.text();
                            if (typeof mod[initFn] === 'function') await mod[initFn]();
                            _loadedTabs.add(sectionId);
                        }
                    } catch (e) {
                        console.warn(`[${sectionId}] 載入失敗:`, e);
                    }
                };

                // All authorized tabs load in parallel. Section IDs come from
                // TAB_MAP so loadTabs and _applyModuleTabs share one truth.
                await Promise.all(
                    TAB_LOADERS
                        .filter(([key]) => _authed(key))
                        .map(([key, html, js, init]) => _loadTab(TAB_MAP[key], html, js, init))
                );

                // Auto-switch to first authorized tab. Skipped on the
                // post-login re-call so the user isn't yanked away from
                // whatever tab they were already on.
                if (autoSwitch) {
                    renderGroupNav(); // build top bar with resolved auth before first switch
                    // Deep-link: honor a #section in the URL if it exists and is allowed.
                    const hashTab = location.hash.slice(1);
                    const fromHash = _isNavigable(hashTab) ? hashTab : null;
                    // Else logged-out users get media tools only → land on 備份並轉檔
                    // (the historical default tab), derived from TAB_MAP not a literal.
                    const firstTab = fromHash || (hasModules ? TAB_MAP[modules[0]] : TAB_MAP.backup);
                    if (firstTab) switchTab(firstTab);
                }
            } catch (err) {
                console.error("Error loading tabs:", err);
            }
        }

        // Post-login hook: inject tabs that became authorized but weren't
        // loaded at boot (no token at boot → filter excluded CRM/admin tabs).
        window._ensureTabsLoaded = () => loadTabs({ autoSwitch: false });

        // Initialize tabs immediately (grouped nav is rendered inside loadTabs
        // once auth resolves, so it reflects the user's authorized tabs)
        loadTabs().then(async () => {
            // Auth already resolved inside loadTabs(); apply tab visibility as safety fallback
            if (typeof window._applyVisibleTabs === 'function') window._applyVisibleTabs();
            // Re-apply admin-only / manager-only visibility to newly injected tab DOM
            // (initial _applyAuthState runs before loadTabs completes, so querySelectorAll
            // misses elements inside dynamically loaded .html files like projects.html)
            if (typeof window._applyAuthState === 'function') {
                window._applyAuthState(window._accessLevel >= 3);
            }

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
                            id: a.id,
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
                window._localAgentVersion = data.current_version;
                btnBadge.style.display = 'inline-block';
                btnBadge.className = "cursor-pointer text-sm font-bold text-white bg-red-600 hover:bg-red-500 px-2 py-0.5 rounded shadow animate-pulse flex items-center gap-1";
                btnBadge.innerHTML = `🚀 <span class="underline">發現新版本 (v${latest})</span>`;
                btnBadge.title = `點擊以從伺服器安裝最新版 (目前: v${current})`;
            });

            socket.on('log', (data) => {
                if (typeof appendLog === 'function') {
                    appendLog(data.msg, data.type);
                }
                // 收集錯誤訊息到 _taskErrors（標記當前階段）
                if (data.type === 'error' && data.msg) {
                    if (!window._taskErrors) window._taskErrors = [];
                    const now = new Date();
                    const ts = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0') + ':' + String(now.getSeconds()).padStart(2,'0');
                    const phase = window._activeJobTab || 'system';
                    window._taskErrors.push({ ts, phase, msg: data.msg });
                }
            });

            socket.on('progress', (data) => {
                if (typeof updateProgress === 'function') {
                    updateProgress(data);
                }
            });

            socket.on('transcribe_error', (data) => {
                const retryBtn = document.getElementById('btn_retry');

                // 收集錯誤
                if (!window._taskErrors) window._taskErrors = [];
                const now = new Date();
                const ts = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0') + ':' + String(now.getSeconds()).padStart(2,'0');
                window._taskErrors.push({ ts, phase: 'transcribe', msg: data.msg || 'Whisper error' });
                _showErrorPanelIfNeeded();

                // --- Unlock Transcribe Button if locked ---
                const tBtn = document.querySelector('#tab_transcribe button[onclick="submitTranscribeJob()"]');
                if (tBtn && tBtn.disabled) {
                    tBtn.innerHTML = '🎙️ 開始生成逐字稿';
                    tBtn.disabled = false;
                    tBtn.classList.remove('opacity-70', 'cursor-not-allowed');
                }
                const tLbl = document.getElementById('transcribe_prog_label');
                if (tLbl) tLbl.textContent = '[X] 任務中止或失敗: ' + (data.msg || '');
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
                    // 新任務開始時清除上一次的錯誤 + 完成狀態
                    window._taskErrors = [];
                    _hideErrorPanel();
                    if (typeof resetProgress === 'function') resetProgress();
                }

                if (data.status === 'done') {
                    // If we are actively polling remote hosts in distributed mode, do NOT let a single local host's
                    // task completion broadcast prematurely reset the global UI and kill the heartbeat monitor.
                    if (window._activeRemoteHosts && Object.keys(window._activeRemoteHosts).length > 0 && window._heartbeatTimer) {
                        return;
                    }

                    // 本機補轉完成追蹤：遞減 pending 計數，到 0 時重新驗證
                    if (window._retryLocalPending && window._retryLocalPending > 0 && data.summary?.task_type === 'transcode') {
                        window._retryLocalPending--;
                        if (typeof appendLog === 'function') appendLog(`[OK] 本機補轉完成，剩餘 ${window._retryLocalPending} 個`, 'system');
                        if (window._retryLocalPending <= 0) {
                            window._retryLocalPending = 0;
                            if (typeof appendLog === 'function') appendLog('[>] 本機補轉全部完成，重新驗證...', 'system');
                            const _rlFlags = window._retryLocalFlags;
                            const _rlProxyRoot = window._retryProxyRoot;
                            const _rlProjName = window._retryProjName;
                            window._retryLocalFlags = null;
                            window._retryProxyRoot = null;
                            window._retryProjName = null;
                            // 延遲 3 秒等 NAS 落盤，再驗證
                            setTimeout(() => {
                                if (window.verifyAndRetryMissingProxies) {
                                    window.verifyAndRetryMissingProxies(_rlProxyRoot, _rlProjName, _rlFlags);
                                }
                            }, 3000);
                        }
                        return; // 不要觸發一般的完成摘要
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

                    // Pipeline 追蹤：標記階段完成
                    const pl = window._backupPipeline;
                    if (pl && pl.pending) {
                        const tt = data.summary?.task_type;
                        if (tt === 'concat') pl.pending.delete('concat');
                        // 備份 task_status:done 表示後端 chained 的 concat 也已完成（若有的話）
                        // 只有分散式模式的 concat 才需要等前端觸發
                        if (tt === 'backup' && !window._remoteDispatch) {
                            pl.pending.delete('concat');
                        }
                    }

                    // 若 pipeline 還有待完成項目（report 等），不顯示最終摘要
                    if (pl && pl.pending && pl.pending.size > 0) {
                        // 但如果報表已完成（_backupReportPending=false），也清除
                        if (pl.pending.has('report') && !window._backupReportPending) {
                            pl.pending.delete('report');
                        }
                        if (pl.pending.size > 0) return;
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
                    // 顯示錯誤面板
                    _showErrorPanelIfNeeded();

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
                if (typeof window.setAllModelErrorUI === 'function') {
                    window.setAllModelErrorUI();
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

                // __done__ = job ended (includes error cases where report_job_done is never emitted)
                if (phase === '__done__') {
                    // 確保 _backupReportPending 被清除，否則完成摘要永遠不會顯示
                    if (window._backupReportPending) {
                        window._backupReportPending = false;
                        const pl = window._backupPipeline;
                        if (pl && pl.pending) pl.pending.delete('report');
                        // 如果備份 TAB 且所有階段都完成，顯示最終摘要
                        if (window._activeJobTab === 'backup') {
                            if (!pl || !pl.pending || pl.pending.size === 0) {
                                showCompletionSummary({ task_type: 'backup', elapsed_sec: 0 }, 'backup');
                                updateActionBarState('idle');
                                playDing();
                            }
                        }
                    }
                    return;
                }

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
                    fetch(window.currentSocketUrl + '/api/v1/utils/open_folder', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({path: data.dest_dir})
                    }).catch(e => console.error(e));
                }
            });
        }
        window.setupSocket = setupSocket;

        setupSocket(window.currentSocketUrl);

        const terminal = document.getElementById('terminal');
        const terminalVerbose = document.getElementById('terminal_verbose');
        const statusBadge = document.getElementById('status-badge');
        window._activeJobTab = null; // 'backup' | 'verify' | 'transcode' | 'concat' | 'report'

        function playDing() {
            // Disabled — task completion sound removed per user request
        }

        // Start polling immediately and then every 3 seconds
        if (typeof window.pollLocalAgent === 'function') window.pollLocalAgent();
        setInterval(() => { if (typeof window.pollLocalAgent === 'function') window.pollLocalAgent(); }, 3000);
        // ---------------------------

        // Variables related to sources and setup were moved to backup.js

        // ===== SaaS 路由輔助函數 =====
        function getAgentBaseUrl() {
            // 本機代理伺服器，負責 UI 操作如選取資料夾與拖曳。如果沒開，回退給網頁原始伺服器。
            return window._localAgentActive ? 'http://127.0.0.1:8000' : '';
        }

        async function createShortcut() {
            if (!window._localAgentActive) {
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
            { checkboxes: 'al_host_checkboxes',         panel: 'al_host_panel',         prefix: 'al_host_chk' },
            { checkboxes: 'rpt_host_checkboxes',        panel: 'rpt_host_panel',        prefix: 'rpt_host_chk' },
            { checkboxes: 'tts_host_checkboxes',        panel: 'tts_host_panel',        prefix: 'tts_host_chk' },
            { checkboxes: 'tts_clone_host_checkboxes',  panel: 'tts_clone_host_panel',  prefix: 'tts_clone_host_chk' },
            { checkboxes: 'dm_host_checkboxes',         panel: 'dm_host_panel',         prefix: 'dm_host_chk' },
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


        // ============ Grouped nav (官網-style top groups + left sidebar) ============
        // Single source of truth = TAB_GROUPS (tab-config.js). Top bar = group
        // buttons; sidebar groups show a left list of their tabs. switchTab stays
        // the ONLY place that toggles section visibility — these helpers just keep
        // the surrounding chrome (top highlight + sidebar) in sync.
        const _groupLastTab = {};   // groupId -> last-active section id (restore on re-open)
        let _sidebarGroupId = null; // group currently rendered into #group-sidebar

        // RBAC: is this tab key visible for the current user? (single source = shouldShowTab)
        function _authed(key) { return shouldShowTab(key, window._authUser, window._modules); }

        // First section the current user may see — logout / redirect fallback.
        function _firstAuthorizedSection() {
            for (const g of TAB_GROUPS) {
                for (const key of groupKeys(g)) if (_authed(key)) return TAB_MAP[key];
            }
            return TAB_MAP.backup;
        }

        function _isTabAuthorized(sectionId) {
            const g = groupForSection(sectionId);
            if (!g) return true; // admin / non-group sections are gated elsewhere
            return groupKeys(g).some((k) => TAB_MAP[k] === sectionId && _authed(k));
        }

        // A section id we may navigate to: it exists in the DOM and is authorized.
        function _isNavigable(sectionId) {
            return !!(sectionId && document.getElementById(sectionId) && _isTabAuthorized(sectionId));
        }

        function _sectionForGroup(group) {
            if (_groupLastTab[group.id]) return _groupLastTab[group.id];
            if (group.single) return TAB_MAP[group.single];
            const first = (group.items || []).find((it) => TAB_MAP[it.key] && _authed(it.key));
            return first ? TAB_MAP[first.key] : null;
        }

        function renderGroupNav() {
            const top = document.getElementById('top-group-nav');
            if (!top) return;
            top.innerHTML = '';
            TAB_GROUPS.forEach((g) => {
                if (!groupKeys(g).some(_authed)) return; // hide groups with no authorized tab
                const b = document.createElement('button');
                b.id = 'gbtn_' + g.id;
                b.className = 'group-top-btn';
                b.textContent = g.label;
                b.onclick = () => selectGroup(g.id);
                top.appendChild(b);
            });
        }

        function renderGroupSidebar(group) {
            const side = document.getElementById('group-sidebar');
            if (!side) return;
            const gid = group ? group.id : null;
            if (gid === _sidebarGroupId) return; // unchanged — keep DOM + active state
            _sidebarGroupId = gid;
            if (!group) { side.classList.add('hidden'); side.innerHTML = ''; return; }
            side.innerHTML = '';
            group.items.forEach((it) => {
                const sec = TAB_MAP[it.key];
                if (!sec || !_authed(it.key)) return; // RBAC: skip unauthorized items
                const b = document.createElement('button');
                b.id = 'sbtn_' + sec;
                b.className = 'group-side-btn';
                b.textContent = it.label;
                b.onclick = () => switchTab(sec);
                side.appendChild(b);
            });
            side.classList.remove('hidden');
        }

        function selectGroup(groupId) {
            const g = TAB_GROUPS.find((x) => x.id === groupId);
            if (!g) return;
            const target = _sectionForGroup(g);
            if (target) switchTab(target);
        }

        // Re-render grouped nav for the current user (called on login/logout). Redirect
        // off the active tab if it is no longer authorized.
        function refreshGroupNav() {
            _sidebarGroupId = null;          // force sidebar rebuild to reflect new perms
            renderGroupNav();
            // The visible section IS the current tab (single source = the DOM).
            const cur = document.querySelector('.tab-content:not(.hidden)')?.id;
            if (cur && _isTabAuthorized(cur)) _syncGroupChrome(cur);
            else switchTab(_firstAuthorizedSection());
        }
        window._refreshGroupNav = refreshGroupNav;

        // Deep-link: react to manual hash changes (back/forward, pasted URL).
        window.addEventListener('hashchange', () => {
            const t = location.hash.slice(1);
            const cur = document.querySelector('.tab-content:not(.hidden)')?.id;
            if (t !== cur && _isNavigable(t)) switchTab(t);
        });

        // Keep top-bar + sidebar in sync with the section switchTab just showed.
        function _syncGroupChrome(tabId) {
            const g = groupForSection(tabId);
            document.querySelectorAll('#top-group-nav .group-top-btn')
                .forEach((b) => b.classList.toggle('active', !!g && b.id === 'gbtn_' + g.id));
            renderGroupSidebar(g && g.items ? g : null);
            if (g && g.items) {
                document.querySelectorAll('#group-sidebar .group-side-btn')
                    .forEach((b) => b.classList.toggle('active', b.id === 'sbtn_' + tabId));
            }
            if (g) _groupLastTab[g.id] = tabId;
        }

        // ================= Tab 切換邏輯 =================
        function switchTab(tabId) {
            if (typeof window._costCheckUnsaved === 'function' && Object.keys(window._costDirtyMap || {}).length > 0) {
                window._costCheckUnsaved(function() { window._costDirtyMap = {}; switchTab(tabId); });
                return;
            }
            const _section = document.getElementById(tabId);
            if (!_section) return; // unknown/orphan tabId (e.g. a granted-but-pageless module) — no-op
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            _section.classList.remove('hidden');

            // Sync grouped-nav chrome (top-bar highlight + left sidebar)
            _syncGroupChrome(tabId);

            // Hide the shared 執行控制與日誌 panel for non-media sections (CRM / 官網 / admin)
            const hideTaskLog = !isMediaSection(tabId);
            document.querySelectorAll('.media-task-section').forEach(el => el.style.display = hideTaskLog ? 'none' : '');

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

            // Reflect the active tab in the URL (shareable/bookmarkable). replaceState
            // fires no hashchange, so this can't loop with the hashchange listener.
            if (('#' + tabId) !== location.hash) history.replaceState(null, '', '#' + tabId);
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
            drone_meta: '空拍寫入',
        };

        function initRemoteHostProgress(hosts) {
            const tab = window._activeJobTab || 'backup';
            const prefixMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts', drone_meta: 'dm' };
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
            // 統一入口：heartbeat 啟動 = 有遠端任務執行中。
            // 若有 pending idle 切換（stopHeartbeat 留的 3 秒寬限），取消之 —
            // 避免 transcode 補轉流程 stop→merge→restart 中間閃 idle。
            if (window._idleSwitchTimer) {
                clearTimeout(window._idleSwitchTimer);
                window._idleSwitchTimer = null;
            }
            if (typeof updateActionBarState === 'function') updateActionBarState('running');
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

                            // If worker is idle, queue empty, and enough time has passed since submission
                            // (shorter wait for retries since files are smaller)
                            const _minWait = window._remoteDispatchExpectedRetryCount > 0 ? 8000 : 15000;
                            if (!d.busy && d.queue_length === 0 && (now - info.startTime > _minWait)) {
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
                            const _pfxMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts', drone_meta: 'dm' };
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
            // Debounced idle switch：給 3 秒寬限讓下一輪 heartbeat 有機會接手
            // （transcode 補轉/merge 流程）。如果真的完成，3 秒後自然切 idle。
            if (window._idleSwitchTimer) clearTimeout(window._idleSwitchTimer);
            window._idleSwitchTimer = setTimeout(() => {
                window._idleSwitchTimer = null;
                if (typeof updateActionBarState === 'function') updateActionBarState('idle');
            }, 3000);
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
            // 重置補轉相關狀態
            window._remoteDispatchExpectedRetryCount = 0;
            window._retryFailedHosts = [];
            window._retryLocalPending = 0;
            window._retryLocalFlags = null;
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

            // Path-access pre-flight: skip hosts that can't see the source
            // paths (e.g. user pointed at G:\ which only exists on one
            // machine). Keep the rest running — transcode normally uses a
            // NAS-shared path accessible to all, so a partial miss usually
            // means user also ticked a host that doesn't have the mount.
            const sourceDirsForCheck = (ctx.cards || []).map(c => c[2]).filter(Boolean);
            if (sourceDirsForCheck.length) {
                const accessible = [];
                await Promise.all(reachable.map(async h => {
                    try {
                        const ctrl = new AbortController();
                        const t = setTimeout(() => ctrl.abort(), 4000);
                        const r = await fetch('http://' + h.ip + '/api/v1/validate_paths', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ paths: sourceDirsForCheck }),
                            signal: ctrl.signal,
                        });
                        clearTimeout(t);
                        const d = await r.json();
                        const results = d.results || {};
                        const missing = Object.entries(results).filter(([_p, v]) => !v.path_exists).map(([p]) => p);
                        if (missing.length) {
                            updateHostProgress(h.ip, 0, '✗ 看不到來源', '#8b0000');
                            if (typeof appendLog === 'function') appendLog(`⚠️ ${h.name} 看不到來源 (${missing.join(', ')}) — 跳過此主機`, 'error');
                        } else {
                            accessible.push(h);
                        }
                    } catch (e) {
                        updateHostProgress(h.ip, 0, '✗ 驗證失敗', '#8b0000');
                        if (typeof appendLog === 'function') appendLog(`⚠️ ${h.name} 路徑驗證失敗: ${e.message} — 跳過此主機`, 'error');
                    }
                }));
                if (!accessible.length) {
                    if (typeof appendLog === 'function') appendLog('❌ 沒有任何主機能存取來源路徑，分派取消。請確認來源放在 NAS 共享路徑或只勾有掛到該路徑的主機。', 'error');
                    return;
                }
                if (accessible.length < reachable.length && typeof appendLog === 'function') {
                    appendLog(`📋 改派給 ${accessible.length} 台能存取來源的主機`, 'system');
                }
                reachable.length = 0;
                accessible.forEach(h => reachable.push(h));
            }

            ctx = Object.assign({}, ctx, { hosts: reachable });

            // Stash validated+reachable hosts so retry rounds can re-ping
            // them later without re-running the full selection UI flow.
            window._originalDispatchHosts = reachable.map(h => ({ ...h }));
            window._originalSourceDirs = sourceDirsForCheck.slice();

            // ── 取得磁碟代號 → UNC 映射表，讓遠端主機不受磁碟掛載差異影響 ──
            await window.ensureDriveMap();
            const _toUnc = window.toUncPath || (x => x);
            window._toUnc = _toUnc; // 保留給補轉邏輯的向後相容
            const mapCount = Object.keys(window._driveMap || {}).length;
            if (mapCount > 0 && typeof appendLog === 'function') {
                appendLog('[UNC] 已載入 ' + mapCount + ' 個磁碟映射，遠端路徑將自動轉換', 'system');
            }

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
            const expectedFiles = {};
            for (const entry of cardEntries) {
                expectedFiles[entry.cardName] = {};
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
                        parentDir = parts[parts.length - 2] + '/';
                    }
                    const basename = parts[parts.length - 1].replace(/\.[^/.]+$/, "");
                    const expectedProxyPath = parentDir + basename + "_proxy.mov";
                    expectedFiles[entry.cardName][expectedProxyPath] = fileAbs;
                }
            }
            window._remoteDispatchExpected = expectedFiles;

            // ── 分派：將每張卡的檔案按輪轉 round-robin 分配給各遠端主機 ──────────
            const allCardFiles = [];
            for (const { cardName, files } of cardEntries) {
                for (const file of files) allCardFiles.push({ cardName, file });
            }

            const n = reachable.length;
            if (typeof appendLog === 'function') appendLog('📋 共 ' + totalFiles + ' 個檔案（' + cardEntries.length + ' 張卡），分配給 ' + n + ' 台主機', 'system');

            const hostCardMaps = reachable.map(() => ({}));
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

                let hostOk = false;
                for (const cardName of cardNames) {
                    const files = cardMap[cardName].map(_toUnc);
                    const cardSuffix = cardName ? '/' + cardName : '';
                    const dest = _toUnc(ctx.proxy_root
                        ? ctx.proxy_root + '/' + ctx.project_name + '/HostDispatch_' + h.name.replace(/\s+/g, '_') + cardSuffix
                        : '');
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
                    // Blacklist this host so retry rounds skip it.
                    window._retryFailedHosts = [...new Set([...(window._retryFailedHosts || []), h.ip])];
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
                                    // ── 串帶：優先遠端 (扣黑名單) → 失敗退回本機 ──
                                    if (flags.do_concat && Array.isArray(flags.cards) && flags.cards.length) {
                                        const blacklist = new Set(window._retryFailedHosts || []);
                                        const candidates = (window._originalDispatchHosts || []).filter(h => !blacklist.has(h.ip));
                                        const localUrl = getComputeBaseUrl();
                                        // Parallel ping — first reachable candidate wins (avoids
                                        // O(N) worst-case when multiple hosts are unreachable).
                                        let concatHost = null;
                                        if (candidates.length) {
                                            try {
                                                concatHost = await Promise.any(candidates.map(async h => {
                                                    const ctrl = new AbortController();
                                                    const t = setTimeout(() => ctrl.abort(), 2500);
                                                    const ping = await fetch('http://' + h.ip + '/api/v1/health', { signal: ctrl.signal });
                                                    clearTimeout(t);
                                                    if (!ping.ok) throw new Error('not ok');
                                                    return h;
                                                }));
                                            } catch (_) { /* all failed — concatHost stays null */ }
                                        }
                                        const concatUrl = concatHost ? ('http://' + concatHost.ip + '/api/v1/jobs/concat') : (localUrl + '/api/v1/jobs/concat');
                                        const concatHostName = concatHost ? concatHost.name : '本機';
                                        if (typeof appendLog === 'function') appendLog('🏗️ 串帶將由 [' + concatHostName + '] 執行' + (concatHost ? '（遠端優先）' : '（遠端不可用，退回本機）'), 'system');
                                        window._concatMultiCard = { total: flags.cards.length, done: 0, jobIds: [] };
                                        for (let ci = 0; ci < flags.cards.length; ci++) {
                                            const cardEntry = flags.cards[ci];
                                            const cardName = Array.isArray(cardEntry) ? cardEntry[0] : cardEntry;
                                            if (!cardName) continue;
                                            const concatSrcDir = flags.local_root + '/' + flags.project_name + '/' + cardName;
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
                                            // Try remote first; on connection failure fall back to local.
                                            let submitted = false;
                                            try {
                                                const r3 = await fetch(concatUrl, {
                                                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(concatPayload)
                                                });
                                                const j3 = await r3.json();
                                                if (typeof appendLog === 'function') appendLog('📌 串帶 [' + cardName + '] 排隊中 @ ' + concatHostName + '，任務 ID: ' + (j3.job_id || '?'), 'system');
                                                submitted = true;
                                            } catch (err) {
                                                if (typeof appendLog === 'function') appendLog('⚠️ 遠端串帶失敗 (' + err.message + ') — 退回本機', 'error');
                                            }
                                            if (!submitted && concatHost) {
                                                // Remote died mid-dispatch → fall back to local for this card.
                                                try {
                                                    const r3b = await fetch(localUrl + '/api/v1/jobs/concat', {
                                                        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(concatPayload)
                                                    });
                                                    const j3b = await r3b.json();
                                                    if (typeof appendLog === 'function') appendLog('📌 串帶 [' + cardName + '] 改由本機排隊，任務 ID: ' + (j3b.job_id || '?'), 'system');
                                                } catch (err2) {
                                                    if (typeof appendLog === 'function') appendLog('❌ 串帶 [' + cardName + '] 本機也失敗: ' + err2.message, 'error');
                                                }
                                            }
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

                        if (!localRoot || !proxyRoot || !projName || cards.length === 0) {
                            if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                            return;
                        }

                        if (typeof appendLog === 'function') appendLog('🔍 正在驗證 Proxy 轉檔完整性（後端掃描比對）...', 'system');
                        const ms = document.getElementById('merge_status_text');
                        if (ms) ms.textContent = '驗證檔案中…';

                        try {
                            const allMissing = [];
                            const sharedProxyDir = proxyRoot.replace(/\\/g, '/') + '/' + projName;

                            for (let ci = 0; ci < cards.length; ci++) {
                                const cardEntry = cards[ci];
                                const cardName = Array.isArray(cardEntry) ? cardEntry[0] : cardEntry;
                                const cardSrcPath = Array.isArray(cardEntry) && cardEntry[1] ? cardEntry[1] : null;
                                if (!cardName) continue;

                                const backupCopyDir = (localRoot.replace(/\\/g, '/') + '/' + projName + '/' + cardName);
                                let sourceDir = cardSrcPath || backupCopyDir;
                                const proxyDir  = sharedProxyDir + '/' + cardName;

                                if (typeof appendLog === 'function') appendLog(`🔍 [${cardName}] 比對來源: ${sourceDir} → ${proxyDir}`, 'system');

                                try {
                                    let r = await fetch(getComputeBaseUrl() + '/api/v1/compare_source', {
                                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ source_dir: sourceDir, output_dir: proxyDir, flat_proxy: true })
                                    });
                                    let d = await r.json();

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

                            window._remoteDispatchExpectedRetryCount = (window._remoteDispatchExpectedRetryCount || 0) + 1;
                            if (typeof appendLog === 'function') appendLog(`[!] 發現 ${allMissing.length} 個缺失的 Proxy 檔案，啟動補轉 (第 ${window._remoteDispatchExpectedRetryCount} 次)...`, 'error');

                            if (window._remoteDispatchExpectedRetryCount > 3) {
                                if (typeof appendLog === 'function') appendLog('[X] 補件重試已達上限 (3次)，放棄重試，啟動後續作業。', 'error');
                                if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                                return;
                            }

                            // ── 補轉策略：前幾次都派給當下可執行的遠端主機
                            //    (re-ping + path validate)，最後一輪才本機補轉 ──
                            const retryCount = window._remoteDispatchExpectedRetryCount;
                            const LAST_LOCAL_ROUND = 3; // round 1-2 remote, round 3 local
                            let useLocal = retryCount >= LAST_LOCAL_ROUND;
                            let liveRemoteHosts = [];

                            if (!useLocal) {
                                const origHosts = window._originalDispatchHosts || [];
                                const origSrcDirs = window._originalSourceDirs || [];
                                const blacklist = new Set(window._retryFailedHosts || []);
                                const candidates = origHosts.filter(h => !blacklist.has(h.ip));
                                if (blacklist.size > 0 && typeof appendLog === 'function') {
                                    appendLog(`[i] 跳過黑名單主機 (${[...blacklist].join(', ')})`, 'system');
                                }
                                // Re-ping + re-validate surviving candidates.
                                await Promise.all(candidates.map(async h => {
                                    try {
                                        const c1 = new AbortController();
                                        const t1 = setTimeout(() => c1.abort(), 3000);
                                        const ping = await fetch('http://' + h.ip + '/api/v1/health', { signal: c1.signal });
                                        clearTimeout(t1);
                                        if (!ping.ok) return;
                                        if (origSrcDirs.length) {
                                            const c2 = new AbortController();
                                            const t2 = setTimeout(() => c2.abort(), 4000);
                                            const vr = await fetch('http://' + h.ip + '/api/v1/validate_paths', {
                                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ paths: origSrcDirs }),
                                                signal: c2.signal,
                                            });
                                            clearTimeout(t2);
                                            const vd = await vr.json();
                                            const ok = Object.values(vd.results || {}).every(v => v.path_exists);
                                            if (!ok) return;
                                        }
                                        liveRemoteHosts.push(h);
                                    } catch (_) { /* unreachable — skip */ }
                                }));
                                if (liveRemoteHosts.length === 0) {
                                    if (typeof appendLog === 'function') appendLog('[>] 目前無可執行的遠端主機（黑名單外都不可達或看不到來源），改用本機補轉', 'system');
                                    useLocal = true;
                                }
                            }

                            if (useLocal) {
                                // 本機補轉：直接送到 localhost，100% 路徑可達
                                if (typeof appendLog === 'function') appendLog(`[>] 第 ${retryCount} 次補轉：使用本機轉檔（保證路徑可達）`, 'system');
                                const localUrl = window.currentSocketUrl || window.location.origin;
                                let localStarted = 0;
                                const byCard = {};
                                allMissing.forEach(({ cardName, sourceFile }) => {
                                    if (!byCard[cardName]) byCard[cardName] = [];
                                    byCard[cardName].push(sourceFile);
                                });

                                for (const [cardName, srcFiles] of Object.entries(byCard)) {
                                    const destDir = proxyRoot + '/' + projName + '/' + cardName;
                                    try {
                                        const r = await fetch(localUrl + '/api/v1/jobs/transcode', {
                                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ sources: srcFiles, dest_dir: destDir })
                                        });
                                        const j = await r.json();
                                        if (typeof appendLog === 'function') appendLog(`[OK] 本機補轉 [${cardName}] ${srcFiles.length} 個檔案排隊，任務 ID: ${j.job_id || '?'}`, 'system');
                                        localStarted++;
                                    } catch (err) {
                                        if (typeof appendLog === 'function') appendLog(`[X] 本機補轉 [${cardName}] 失敗: ${err.message}`, 'error');
                                    }
                                }

                                if (localStarted > 0) {
                                    // 本機轉檔：用 Socket.IO task_status 事件偵測完成，再跑驗證
                                    if (typeof appendLog === 'function') appendLog(`[>] 本機補轉中，等待 ${localStarted} 個任務完成...`, 'system');
                                    window._retryLocalPending = localStarted;
                                    window._retryLocalFlags = flags;
                                    window._retryProxyRoot = proxyRoot;
                                    window._retryProjName = projName;
                                    // task_status:done 事件處理器會遞減 _retryLocalPending
                                    // 到 0 時自動觸發 verifyAndRetryMissingProxies
                                } else {
                                    if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                                }
                            } else {
                                // 遠端補轉：平均派給當下 ping+path 驗證都通過的主機
                                if (typeof appendLog === 'function') appendLog(`[>] 第 ${retryCount} 次補轉：平均派給 ${liveRemoteHosts.length} 台可執行的遠端主機`, 'system');

                                const distributions = liveRemoteHosts.map(h => ({ host: h, byCard: {} }));
                                allMissing.forEach(({ cardName, sourceFile }, i) => {
                                    const dist = distributions[i % distributions.length];
                                    if (!dist.byCard[cardName]) dist.byCard[cardName] = [];
                                    dist.byCard[cardName].push(sourceFile);
                                });

                                let requestsStarted = 0;
                                window._activeRemoteHosts = {}; // 重置，只追蹤補轉主機
                                const _unc = window.toUncPath || window._toUnc || (x => x);
                                for (const dist of distributions) {
                                    for (const [cardName, srcFiles] of Object.entries(dist.byCard)) {
                                        const destDir = _unc(proxyRoot + '/' + projName + '/HostDispatch_Retry_' + dist.host.name.replace(/\s+/g, '_') + '/' + cardName);
                                        const uncFiles = srcFiles.map(_unc);
                                        try {
                                            const r = await fetch('http://' + dist.host.ip + '/api/v1/jobs/transcode', {
                                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ sources: uncFiles, dest_dir: destDir })
                                            });
                                            const j = await r.json();
                                            if (typeof appendLog === 'function') appendLog(`[OK] ${dist.host.name} [${cardName}] 補轉排隊，任務 ID: ${j.job_id || '?'}`, 'system');
                                            requestsStarted++;
                                            window._activeRemoteHosts[dist.host.ip] = {
                                                host: dist.host, done: false, pct: 0,
                                                lastSeen: Date.now(), startTime: Date.now(),
                                                expectedJobs: Object.keys(dist.byCard).length
                                            };
                                        } catch (err) {
                                            if (typeof appendLog === 'function') appendLog(`[X] ${dist.host.name} [${cardName}] 補轉失敗: ${err.message}`, 'error');
                                            // Blacklist so next retry round skips this host.
                                            window._retryFailedHosts = [...new Set([...(window._retryFailedHosts || []), dist.host.ip])];
                                        }
                                    }
                                }

                                if (requestsStarted > 0) {
                                    // 存 flags 供 merge 後的驗證使用
                                    window._postMergeFlags = flags;
                                    startHeartbeatMonitor();
                                } else {
                                    // 遠端全部失敗 → 直接走本機
                                    if (typeof appendLog === 'function') appendLog('[>] 遠端補轉全部失敗，改用本機', 'system');
                                    window._remoteDispatchExpectedRetryCount = 2;
                                    window.verifyAndRetryMissingProxies(proxyRoot, projName, flags);
                                }
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
            const maxPolls = 300;
            appendLog(`[${hostName}] 開始監控遠端進度...`, 'system');
            for (let i = 0; i < maxPolls; i++) {
                await new Promise(r => setTimeout(r, 2000));
                try {
                    const res = await fetch(hostUrl + '/api/v1/status?log_offset=' + offset, { signal: AbortSignal.timeout(5000) });
                    if (!res.ok) { appendLog(`[${hostName}] 狀態查詢失敗 (${res.status})`, 'error'); break; }
                    const data = await res.json();
                    (data.logs || []).forEach(line => appendLog(`[${hostName}] ${line}`, 'system'));
                    offset = data.new_log_offset || offset;
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
            'drone_meta': 'collectDroneMetaPayload',
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


// ─── Expose remaining globals for HTML onclick and tab JS ─── //
        window.switchTab = switchTab;
        window.setGlobalConflict = setGlobalConflict;
        window.showConflictModal = showConflictModal;
        window.updateProgress = updateProgress;
        window.showCompletionSummary = showCompletionSummary;
        window.playDing = playDing;
        window.createShortcut = createShortcut;
        window.getAgentBaseUrl = getAgentBaseUrl;
        window.getSelectedHosts = getSelectedHosts;
        window.renderHostSelector = renderHostSelector;
        window.renderStandaloneHostPanels = renderStandaloneHostPanels;
        window.apiControl = apiControl;
        window.updateComputeModeStyle = updateComputeModeStyle;
        window.mergeHostOutputs = mergeHostOutputs;
        window.pollRemoteHostProgress = pollRemoteHostProgress;
        window.getComputeBaseUrl = typeof getComputeBaseUrl !== 'undefined' ? getComputeBaseUrl : getAgentBaseUrl;

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
