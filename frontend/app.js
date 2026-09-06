// ─── Originsun Media Guard Pro ─── //
// Entry point — imports all extracted modules, then defines core app logic.

// ── Module Imports (side-effect: each module registers its window.* globals) ──
import { TAB_MAP, TAB_LOADERS, shouldShowTab, TAB_GROUPS, groupKeys, groupForSection, isMediaSection } from './js/shared/tab-config.js';
import './js/shared/modal-styles.js';
import './js/auth/auth-state.js';
import './js/auth/login-modal.js';
import './js/auth/google-oauth.js';
import './js/admin/user-mgmt.js';
import './js/admin/publish-mgmt.js';
import './js/admin/api-keys.js';
import { pollLocalAgent } from './js/update/version-check.js';
import './js/update/update-modal.js';
import './js/settings/settings-modal.js';
import './js/shared/nas-browser.js';
import './js/shared/drive-map-modal.js';
import { initSelectAutoUpgrade } from './js/shared/select-upgrade.js';
import { initPasteImage } from './js/shared/paste-image.js';
// utils.js 以前靠各分頁模組在開機時順便載進來；分頁改成點到才載後，要靠這一行保住
// window.appendLog / resetProgress / bearerHeader … 在開機就存在（socket handler 會用）
import { tabLoadError, startVisiblePolling, resetProgress } from './js/shared/utils.js';
import { _LOADING_HTML } from './js/shared/subview-loader.js';
import { loadReportHistory } from './js/shared/report-history.js';
import { updateProgress, showCompletionSummary, _showErrorPanelIfNeeded, _hideErrorPanel } from './js/app/progress.js';
import './js/app/remote-dispatch.js';

// ─── Main Application ─── //
        let socket = null;

        // Idempotent — re-running loadTabs() after login skips already-loaded
        // sections instead of clobbering their state.
        const _loadedTabs = new Set();

        // 分頁「點到才載入」（owner 2026-09-03「存取都有點慢」：原本開頁就把 30 幾個分頁全載、
        // 打 80 幾支 API；遠端走 Cloudflare 每支再加幾十到上百 ms）。開頁只載入目標分頁，其餘
        // 在 switchTab 第一次切過去時才 fetch html + import js + init。
        // 🔴 不帶 ?t= 時間戳：主機對靜態檔回 no-cache + ETag，瀏覽器每次只問一句 304，
        // 檔案本體不重傳；發版換了檔 ETag 就變，不會拿到舊的。
        const _loaderBySection = new Map(TAB_LOADERS.map(([key, html, js, init]) => [TAB_MAP[key], { key, html, js, init }]));
        const _loading = new Map();   // sectionId → 進行中的載入（同時兩處要同一頁只載一次、init 一次）

        // 載一個分頁：html 與 js 並行抓、填進 section、init。回傳「這次真的載進來了嗎」。
        // embed：沒有這頁權限、但要用它模組的人（專案頁嵌報價子頁——報價 API 守的是 money_view，
        // 不是分頁權限）跳過權限閘門；section 本來就 hidden，不會多露出什麼。
        const _loadTab = (sectionId, { embed = false } = {}) => {
            if (_loadedTabs.has(sectionId)) return Promise.resolve(false);
            if (!_loading.has(sectionId)) {
                _loading.set(sectionId, _doLoadTab(sectionId, embed).finally(() => _loading.delete(sectionId)));
            }
            return _loading.get(sectionId);
        };
        async function _doLoadTab(sectionId, embed) {
            const ld = _loaderBySection.get(sectionId);
            const el = document.getElementById(sectionId);
            if (!ld || !el || (!embed && !_authed(ld.key))) return false;
            const fail = (status) => { el.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">${tabLoadError(status)}</div>`; return false; };
            try {
                // 帶版號：Cloudflare 對 .js 給 4 小時瀏覽器快取而 html 即時，發版後會拿到「新 html＋舊 js」；
                // 版號變了網址就變，兩邊永遠成對（app.js 自己在 index.html 也是 ?v=）
                const v = await _verTag;
                const [res, mod] = await Promise.all([fetch(`${ld.html}?v=${v}`), import(`${ld.js}?v=${v}`)]);
                if (!res.ok) return fail(res.status);
                el.innerHTML = await res.text();
                if (typeof mod[ld.init] === 'function') await mod[ld.init]();
            } catch (e) {
                console.warn(`[${sectionId}] 載入失敗:`, e);
                // init 跑到一半炸掉：它已經掛上的 document 監聽／輪詢收不回來，再跑一次會掛兩份 → 標成載過、請人重新整理
                _loadedTabs.add(sectionId);
                el.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">${tabLoadError(0)}<br><span style="color:#9ca3af;font-size:12px;">請重新整理頁面再試</span></div>`;
                return false;
            }
            _loadedTabs.add(sectionId);
            // 開機時套的權限（管理員限定元素）與機隊勾選面板都發生在這頁長出來之前 → 對新 DOM 補一次
            window._applyAuthState(window._accessLevel >= 3);
            _refreshHostPanels();
            return true;
        }
        window._ensureTabLoaded = _loadTab;
        // 分頁檔案的版號（同 /api/v1/version）；拿不到就用開頁時間戳（退回舊行為：每次開頁重抓）
        const _verTag = fetch('/api/v1/version').then((r) => r.json()).then((j) => encodeURIComponent(j.version || Date.now())).catch(() => String(Date.now()));

        async function loadTabs() {
            try {
                // Wait for auth so we know which modules to load
                await window._authReady;
                const modules = window._modules;
                const hasModules = !!window._authUser && modules && modules.length > 0;

                // Hide sections for unauthorized tabs immediately
                Object.entries(TAB_MAP).forEach(([key, tabId]) => {
                    const sec = document.getElementById(tabId);
                    if (sec && !_authed(key)) sec.style.display = 'none';
                });

                // 只載入要落地的那一頁；其餘分頁 switchTab 時才載
                renderGroupNav(); // build top bar with resolved auth before first switch
                // Deep-link: honor a #section in the URL if it exists and is allowed.
                const hashTab = location.hash.slice(1);
                const fromHash = _isNavigable(hashTab) ? hashTab : null;
                // Else logged-out users get media tools only → land on 備份並轉檔
                // (the historical default tab), derived from TAB_MAP not a literal.
                // modules[0] 可能是非 tab 的橫切 key（如 finance_partner —— 合夥人
                // 帳號只有這一把）→ TAB_MAP 查無 → 退到第一個看得到的 tab。
                const firstTab = fromHash || (hasModules ? (TAB_MAP[modules[0]] || _firstAuthorizedSection()) : TAB_MAP.backup);
                if (firstTab) await switchTab(firstTab);
            } catch (err) {
                console.error("Error loading tabs:", err);
            }
        }

        // 全域下拉統一：即刻啟動（初掃現有 DOM + MutationObserver 監看之後動態
        // 渲染的下拉）。不綁 loadTabs/登入，確保任何時序都會補升級長清單 select。
        initSelectAutoUpgrade();

        // 全站 textarea 貼上圖片（document 層 paste 攔截 → 上傳圖床 → 插 token）。
        // 顯示面逐個接 journal-core renderRich；未接的面 token 以純文字顯示。
        initPasteImage();

        // Initialize tabs immediately (grouped nav is rendered inside loadTabs
        // once auth resolves, so it reflects the user's authorized tabs)
        loadTabs().then(() => {
            updateComputeModeStyle();
            // 機隊清單只填 window._computeHosts；各分頁的勾選面板在它載入時（_doLoadTab）長出來，
            // 已載入的分頁靠這裡回頭補一次
            fetch('/api/v1/agents')
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (!data) return;
                    window._computeHosts = (data.agents || []).map(a => ({
                        id: a.id,
                        name: a.name,
                        ip: (a.url || '').replace(/^https?:\/\//, '')
                    }));
                    _refreshHostPanels();
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
                appendLog(data.msg, data.type);
                // 收集錯誤訊息到 _taskErrors（標記當前階段）
                if (data.type === 'error' && data.msg) {
                    if (!window._taskErrors) window._taskErrors = [];
                    const now = new Date();
                    const ts = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0') + ':' + String(now.getSeconds()).padStart(2,'0');
                    const phase = window._activeJobTab || 'system';
                    window._taskErrors.push({ ts, phase, msg: data.msg });
                }
            });

            socket.on('progress', (data) => updateProgress(data));

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
                    resetProgress();
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
                        appendLog(`[OK] 本機補轉完成，剩餘 ${window._retryLocalPending} 個`, 'system');
                        if (window._retryLocalPending <= 0) {
                            window._retryLocalPending = 0;
                            appendLog('[>] 本機補轉全部完成，重新驗證...', 'system');
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
                        appendLog('系統：備份完成，開始派發分散式轉檔...', 'system');
                        window.dispatchRemoteTranscode(window._remoteDispatch);
                        window._remoteDispatch = null;
                        return;
                    }

                    // 多卡串帶：追蹤完成數
                    const mc = window._concatMultiCard;
                    if (mc && mc.total > 1 && data.summary?.task_type === 'concat') {
                        mc.done++;
                        if (mc.done < mc.total) {
                            appendLog(`🎞️ 串帶 ${mc.done}/${mc.total} 張卡完成`, 'system');
                            return;
                        }
                        appendLog(`✅ 串帶全部完成（${mc.total} 張卡）`, 'system');
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

                    appendLog('系統：所有排定任務執行完畢！', 'system');
                    showCompletionSummary(data.summary, window._activeJobTab);
                    updateActionBarState('idle');
                    if (retryBtn) retryBtn.style.display = 'none';
                    playDing();

                } else if (data.status === 'error') {
                    updateActionBarState('idle');
                    appendLog('系統提示：任務執行發生錯誤：' + data.detail, 'error');
                    if (retryBtn && window._lastJob) retryBtn.style.display = 'inline-block';
                    // 顯示錯誤面板
                    _showErrorPanelIfNeeded();

                } else if (data.status === 'cancelled') {
                    updateActionBarState('idle');
                    resetProgress();
                    appendLog('❌ 任務已被中止', 'error');
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
                appendLog('✅ 逐字稿生成完畢！目的地：' + data.dest_dir, 'system');
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

                // 報表分頁點到才載：rptLog 只有它載了才在，沒載就落到共用的 appendLog（別讓 handler 炸掉）
                if (typeof window.rptLog === 'function') window.rptLog(msg, data.type || 'info');
                else if (typeof window.appendLog === 'function') window.appendLog(msg, data.type || 'info');
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
                        // openReportFile 早就不存在（2026-03 拿掉）：直接請本機代理開檔
                        fetch('/api/v1/utils/open_file', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                               body: JSON.stringify({ path: data.local_path }) }).catch(() => {});
                    }
                }
            });

        }
        window.setupSocket = setupSocket;

        setupSocket(window.location.origin);   // 固定 same-origin，見 version-check 註解

        window._activeJobTab = null; // 'backup' | 'verify' | 'transcode' | 'concat' | 'report'

        function playDing() {
            // Disabled — task completion sound removed per user request
        }

        startVisiblePolling(pollLocalAgent, 3000);   // 本機代理燈：立刻問一次，之後每 3 秒（分頁在背景不打）
        // ---------------------------

        // Variables related to sources and setup were moved to backup.js

        async function createShortcut() {
            if (!window._localAgentActive) {
                alert("此功能需要在「本機已連線」狀態下才能執行！");
                return;
            }
            try {
                const res = await fetch(window.getLocalAgentBase() + '/api/v1/utils/create_shortcut',
                                        { method: 'POST', headers: window.bearerHeader ? window.bearerHeader() : {} });
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

        // 分頁長出來／機隊清單到手時：備份頁的多機面板＋各獨立分頁的勾選面板都補一次
        function _refreshHostPanels() { renderHostSelector(); renderStandaloneHostPanels(); }

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
        let _switchGen = 0;      // 併發切換：慢的那次載入回來不能蓋掉現在畫面上那頁的 chrome／hash
        async function switchTab(tabId) {
            if (typeof window._costCheckUnsaved === 'function' && Object.keys(window._costDirtyMap || {}).length > 0) {
                window._costCheckUnsaved(function() { window._costDirtyMap = {}; switchTab(tabId); });
                return;
            }
            const _section = document.getElementById(tabId);
            if (!_section) return; // unknown/orphan tabId (e.g. a granted-but-pageless module) — no-op
            if (!_isNavigable(tabId)) {     // 沒權限的分頁（別頁程式直接叫 switchTab）：不要留一片「載入中…」的空白
                _section.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">${tabLoadError(403)}</div>`;
                _section.classList.remove('hidden');
                return;
            }
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            _section.classList.remove('hidden');
            // 第一次切到這頁：現在才載（html + js + init）。fresh＝這次真的載進來了（載過的回 false）
            if (!_section.children.length) _section.innerHTML = _LOADING_HTML;
            const gen = ++_switchGen;
            const fresh = await _loadTab(tabId);
            if (gen !== _switchGen) return;      // 等載入時使用者又切走了：那一次會做完它自己的收尾

            // Sync grouped-nav chrome (top-bar highlight + left sidebar)
            _syncGroupChrome(tabId);

            // Hide the shared 執行控制與日誌 panel for non-media sections (CRM / 官網 / admin)
            const hideTaskLog = !isMediaSection(tabId);
            document.querySelectorAll('.media-task-section').forEach(el => el.style.display = hideTaskLog ? 'none' : '');

            // 通知各分頁「你被切到了」。fresh＝這次切換才把它載進來，init 剛抓過資料，
            // 「切回來要重抓」的鉤子看到 fresh 就別再抓一次（提案庫、CRM 專案、財務）
            document.dispatchEvent(new CustomEvent('tab-changed', { detail: { tab: tabId, fresh } }));

            // Reflect the active tab in the URL (shareable/bookmarkable). replaceState
            // fires no hashchange, so this can't loop with the hashchange listener.
            if (('#' + tabId) !== location.hash) history.replaceState(null, '', '#' + tabId);
        }

        // ===== 全域：記錄上一次任務，供重試使用 =====
        window._lastJob = null; // { url, payload }


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
                resetProgress();
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
        window.playDing = playDing;
        window.createShortcut = createShortcut;
        window.getSelectedHosts = getSelectedHosts;
        window.renderHostSelector = renderHostSelector;
        window.renderStandaloneHostPanels = renderStandaloneHostPanels;
        window.apiControl = apiControl;
        window.updateComputeModeStyle = updateComputeModeStyle;

// ─── Initialize on Page Load ─── //
        document.addEventListener('DOMContentLoaded', () => {


            // Check model status immediately
            if (typeof fetchModelStatus === 'function') {
                fetchModelStatus();
            }
        });
