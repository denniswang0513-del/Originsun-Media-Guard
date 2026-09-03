// ─── 分散式派發（多機轉檔）─── //
// 從 app.js 拆出（2026-09-03）：多機進度列、活機挑選、心跳監控、失聯接手（HostDispatch_Takeover_*）、
// 派發（dispatchRemoteTranscode）、合併輸出＋合併後任務＋補跑缺檔。八個頁籤都只透過 window.* 呼叫，
// 名字全部維持不變（tests/e2e/test_host_failover.py 釘著 startHeartbeatMonitor / _activeRemoteHosts）。
// 回頭用到 app.js 的 updateActionBarState / playDing 一律走 window.（模組作用域看不到彼此）。
import { resetProgress } from '../shared/utils.js';
import { loadReportHistory } from '../shared/report-history.js';

// 派工時定住的 Proxy Root／案名：合併發生在幾分鐘後，發起派工的分頁不一定還在（分頁點到才載）。
// 目的地根由這兩個推導、不另存；window._dispatchCtx 只給 e2e 灌假狀態用。
const _dispatch = { proxyRoot: '', projectName: '' };
window._dispatchCtx = _dispatch;
const _destRoot = () => _dispatch.proxyRoot ? _dispatch.proxyRoot + '/' + _dispatch.projectName : '';
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

// 失聯門檻：90 秒沒有任何一次成功回應（心跳每 5 秒一次 = 連續 18 次落空）
const _HOST_TIMEOUT_MS = 90000;

// 遠端主機狀態：running（還在跑）/ done（確認完成）/ timeout（失聯，結果不明）。
// 其他 tab 建立的紀錄沒有這個欄位 —— 沒填就是還在跑。
const _hostState = info => info.state || 'running';

/**
 * 挑出「現在真的能接活」的遠端主機：扣掉黑名單、ping 得通、看得到來源。
 *
 * 補轉與失聯重派共用同一份判定 —— 兩邊若各寫一套，遲早會有一邊把工作
 * 派給看不到來源路徑的機器，然後整批再空轉一輪。
 */
async function pickLiveDispatchHosts(opts) {
    const exclude = new Set((opts && opts.exclude) || []);
    const blacklist = new Set(window._retryFailedHosts || []);
    const candidates = (window._originalDispatchHosts || [])
        .filter(h => !blacklist.has(h.ip) && !exclude.has(h.ip));
    const srcDirs = window._originalSourceDirs || [];
    const live = [];
    await Promise.all(candidates.map(async h => {
        try {
            const c1 = new AbortController();
            const t1 = setTimeout(() => c1.abort(), 3000);
            const ping = await fetch('http://' + h.ip + '/api/v1/health', { signal: c1.signal });
            clearTimeout(t1);
            if (!ping.ok) return;
            if (srcDirs.length) {
                const v = await window.validateRemotePaths(h.ip, srcDirs, 4000);
                if (!v.ok) return;
            }
            live.push(h);
        } catch (_) { /* 連不上就是不可用 */ }
    }));
    return live;
}

// 來源檔名 → 比對用 stem（proxy 產出是 <stem>_proxy.mov）
function _proxyStem(pathOrName) {
    const name = String(pathOrName).split(/[\\/]/).pop() || '';
    let stem = name.replace(/\.[^.]+$/, '').toLowerCase();
    if (stem.endsWith('_proxy')) stem = stem.slice(0, -6);
    return stem;
}

/**
 * 某台的 HostDispatch 夾裡「真的產出了什麼」。
 * 夾子在共享的 proxy root 上，所以那台就算已經死透了也查得到。
 * `*.part.<ext>` 是還沒收工的暫存檔，不算數（會被列進重派清單）。
 */
async function listProducedStems(dir) {
    const stems = new Set();
    if (!dir) return stems;
    try {
        const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: dir })
        });
        const d = await r.json();
        (d.files || []).forEach(p => {
            const name = String(p).split(/[\\/]/).pop() || '';
            // 過渡期防護：list_dir 從 v2.4.66 起自己就會濾掉半成品，但
            // OTA 期間機隊會有舊版 agent。機隊全數 ≥ 2.4.66 後可整條移除。
            if (/\.part\.[^.]+$/i.test(name)) return;
            stems.add(_proxyStem(name));
        });
    } catch (_) { /* 查不到就當作什麼都沒產出，寧可重轉也不要漏 */ }
    return stems;
}

/**
 * 一台主機失聯 → 把它「分到但還沒產出」的檔案改派給其他活著的主機。
 *
 * 🔴 這是 2026-08-13 事故的正面修法：當時一台轉檔中途掛掉，前端把
 * 「逾時」當成「完成」，直接宣告全體完成並觸發合併 —— 那台手上還有
 * 51 支沒轉，沒有任何人接手。
 *
 * 回傳 true 表示已經有接手的主機在跑（heartbeat 要繼續等）。
 */
async function reassignFromDeadHost(deadIp, info) {
    const assigned = Array.isArray(info.assigned) ? info.assigned : [];
    const hostName = (info.host && info.host.name) || deadIp;

    // 就算它自己醒過來，也不再派工給它 —— 醒來的那台會繼續寫舊任務，
    // 與接手的主機同時寫同一個目的地。
    window._retryFailedHosts = [...new Set([...(window._retryFailedHosts || []), deadIp])];

    if (!assigned.length) {
        appendLog(`⚠️ ${hostName} 失聯，但沒有它的派工紀錄 —— 無法立即重派，留給合併後的補轉處理`, 'error');
        return false;
    }

    // 一台可能寫進不只一個夾（自己的份 + 接手別人的份）—— 全部一起掃
    const scans = await Promise.all((info.destDirs || []).map(listProducedStems));
    const produced = new Set(scans.flatMap(s => [...s]));
    const outstanding = assigned.filter(a => !produced.has(_proxyStem(a.file)));
    appendLog(`🔁 ${hostName} 失聯：分到 ${assigned.length} 個、已產出 ${assigned.length - outstanding.length} 個、`
              + `尚缺 ${outstanding.length} 個`, 'system');
    if (!outstanding.length) return false;

    const live = await pickLiveDispatchHosts({ exclude: [deadIp] });
    if (!live.length) {
        appendLog(`❌ 沒有其他可接手的主機（${outstanding.length} 個檔案待轉）—— 合併後會再嘗試補轉/本機轉`, 'error');
        return false;
    }

    const _unc = window.toUncPath || window._toUnc || (x => x);
    const destRoot = _destRoot();
    const slots = live.map(h => ({ host: h, byCard: {} }));
    outstanding.forEach((a, i) => {
        const slot = slots[i % slots.length];
        const card = a.cardName || '';
        (slot.byCard[card] = slot.byCard[card] || []).push(a.file);
    });

    // 各接手主機互相獨立 —— 一台卡住不該擋住其他台的派發
    const takenCounts = await Promise.all(slots.map(async slot => {
        const cards = Object.entries(slot.byCard);
        if (!cards.length) return 0;
        const base = destRoot
            ? destRoot + '/HostDispatch_Takeover_' + slot.host.name.replace(/\s+/g, '_')
            : '';
        const takenOver = [];
        for (const [cardName, files] of cards) {
            const destDir = _unc(base + (cardName ? '/' + cardName : ''));
            try {
                const ctrl = new AbortController();
                const t = setTimeout(() => ctrl.abort(), 8000);
                const r = await fetch('http://' + slot.host.ip + '/api/v1/jobs/transcode', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sources: files.map(_unc), dest_dir: destDir }),
                    signal: ctrl.signal,
                });
                clearTimeout(t);
                const j = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(j.detail || j.message || ('HTTP ' + r.status));
                appendLog(`↪️ ${slot.host.name} 接手 [${cardName || 'all'}] ${files.length} 個，任務 ID: ${j.job_id || '?'}`, 'system');
                files.forEach(f => takenOver.push({ cardName, file: f }));
            } catch (err) {
                appendLog(`❌ ${slot.host.name} 無法接手 [${cardName || 'all'}]: ${err.message}`, 'error');
                window._retryFailedHosts = [...new Set([...(window._retryFailedHosts || []), slot.host.ip])];
            }
        }
        if (!takenOver.length) return 0;
        // 這台可能本來就在跑自己的份 —— 併進去，不要覆蓋掉既有紀錄
        const prev = window._activeRemoteHosts[slot.host.ip] || {};
        window._activeRemoteHosts[slot.host.ip] = {
            ...prev,
            host: slot.host,
            assigned: [...(prev.assigned || []), ...takenOver],
            destDirs: [...new Set([...(prev.destDirs || []), base])],
            expectedJobs: (prev.expectedJobs || 0) + cards.length,
            state: 'running',
            lastSeen: Date.now(),
            // 重算起算點：不然「閒著就是做完了」的判定會對剛接手的機器誤判
            startTime: Date.now(),
        };
        updateHostProgress(slot.host.ip, 20, '接手轉檔中...', '#d48a04');
        return takenOver.length;
    }));
    const started = takenCounts.reduce((a, b) => a + b, 0);

    if (!started) {
        appendLog(`❌ ${outstanding.length} 個檔案沒有主機接手成功`, 'error');
        return false;
    }
    return true;
}

function startHeartbeatMonitor() {
    if (window._heartbeatTimer) clearInterval(window._heartbeatTimer);
    // 統一入口：heartbeat 啟動 = 有遠端任務執行中。
    // 若有 pending idle 切換（stopHeartbeat 留的 3 秒寬限），取消之 —
    // 避免 transcode 補轉流程 stop→merge→restart 中間閃 idle。
    if (window._idleSwitchTimer) {
        clearTimeout(window._idleSwitchTimer);
        window._idleSwitchTimer = null;
    }
    if (typeof window.updateActionBarState === 'function') window.updateActionBarState('running');
    window._hbTickInFlight = false;   // 上一輪若被 stop 打斷，別讓旗標鎖住新的監控
    window._heartbeatTimer = setInterval(async () => {
        // 🔴 上一輪還沒跑完就不要疊上去：一台 wedge 住的機器會讓這輪
        // 卡滿 8 秒，5 秒的 interval 不等人，堆疊起來就是對同一批 agent
        // 重複發同樣的請求。
        if (window._hbTickInFlight) return;
        window._hbTickInFlight = true;
        try {
        const now = Date.now();
        const timedOut = [];
        // 各主機互相獨立 —— 逐台 await 的話，一台不回應就把其他台的
        // 進度更新一起拖住（逾時放寬到 8 秒後更明顯）。
        await Promise.all(Object.entries(window._activeRemoteHosts || {}).map(async ([ip, info]) => {
            if (_hostState(info) !== 'running') return;
            try {
                const ctrl = new AbortController();
                // 8 秒不是 3 秒：一台被 ffmpeg 榨滿的機器偶爾三秒回不了狀態
                // 很正常，太短會把忙碌誤判成死亡（2026-08-13）
                const t = setTimeout(() => ctrl.abort(), 8000);
                const offset = info.logOffset || 0;
                const r = await fetch('http://' + ip + '/api/v1/status?log_offset=' + offset, { signal: ctrl.signal });
                clearTimeout(t);
                if (r.ok) {
                    info.lastSeen = now;
                    const d = await r.json();
                    info.logOffset = d.new_log_offset || offset;

                    if (d.logs && d.logs.length > 0) {
                        d.logs.forEach(msg => {
                            const cleanMsg = msg.replace(/^\[.*?\]\s*/, '');
                            let _lt = 'info';
                            if (_RE_SYSTEM_LOG.test(cleanMsg)) _lt = 'system';
                            if (_RE_ERROR_LOG.test(cleanMsg)) _lt = 'error';
                            appendLog(`[${info.host.name}] ${cleanMsg}`, _lt);
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
                    if (!d.busy && !d.paused && d.queue_length === 0 && (now - info.startTime > _minWait)) {
                        info.state = 'done';
                        info.pct = 100;
                        const _jl = JOB_LABELS[window._remoteJobType] || '任務';
                        updateHostProgress(ip, 100, `✅ ${_jl}完成`, '#228b22');
                    }
                }
            } catch (_) { }
            // 🔴 逾時 ≠ 完成（2026-08-13）。這裡標的是「失去聯絡、結果不明」，
            // 標成 done 會讓下面的「全部完成」成立、直接觸發合併 —— 那台手上
            // 沒轉完的檔案就這樣人間蒸發。分散式轉檔另外把它的份額改派出去。
            if (_hostState(info) === 'running' && now - info.lastSeen > _HOST_TIMEOUT_MS) {
                info.state = 'timeout';
                updateHostProgress(ip, info.pct || 0, '⚠️ 失聯', '#b45309');
                appendLog(`⚠️ ${info.host.name} (${ip}) 失去聯絡超過 ${Math.round(_HOST_TIMEOUT_MS / 1000)} 秒 — 任務結果不明`, 'error');
                timedOut.push([ip, info]);
            }
        }));

        // 同一輪可能不只一台失聯（交換器/NAS 抖一下就會這樣）——
        // 收齊了一起處理，存活主機只挑一次。狀態已在上面標成 timeout，
        // 下一輪不會重複進來。
        if (timedOut.length && window._remoteJobType === 'transcode') {
            for (const [ip, info] of timedOut) {
                try {
                    await reassignFromDeadHost(ip, info);
                } catch (err) {
                    appendLog('重派失敗: ' + err.message, 'error');
                }
            }
        }

        // 更新主進度條（多機加總進度 — 用各主機實際進度的平均值）
        const _allHosts = Object.values(window._activeRemoteHosts || {});
        const _doneCount = _allHosts.filter(h => _hostState(h) === 'done').length;
        const _lostCount = _allHosts.filter(h => _hostState(h) === 'timeout').length;
        if (_allHosts.length > 0) {
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
                if (bkProgLabel) bkProgLabel.textContent = `遠端轉檔　${_doneCount}/${_allHosts.length} 台完成 (${_aggPct}%)`
                    + (_lostCount ? `，${_lostCount} 台失聯` : '');
            } else {
                // 其他 TAB：單一進度條
                const _pfxMap = { transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts' };
                const _pfx = _pfxMap[_tab];
                if (_pfx) {
                    const _bar = _progEl(_pfx, _tab, 'bar');
                    const _lbl = _progEl(_pfx, _tab, 'label');
                    if (_bar) _bar.style.width = Math.max(5, _aggPct) + '%';
                    const _jl2 = JOB_LABELS[window._remoteJobType] || '任務';
                    if (_lbl) _lbl.textContent = `遠端${_jl2}　${_doneCount}/${_allHosts.length} 台完成 (${_aggPct}%)`
                        + (_lostCount ? `，${_lostCount} 台失聯` : '');
                }
            }
        }

        // Check if all hosts have completed their chunks
        const hosts = _allHosts;
        // 「全部有結果」不等於「全部成功」—— 失聯的那台若已改派出去，
        // 接手的主機會是新的 running 紀錄，這裡自然會繼續等它。
        if (hosts.length > 0 && hosts.every(h => _hostState(h) !== 'running')) {
            stopHeartbeatMonitor();
            if (_lostCount) {
                appendLog(`⚠️ 有 ${_lostCount} 台失聯且沒有主機接手 —— 以下流程是在「結果不完整」的前提下進行，`
                          + `合併後的驗證會列出缺件並嘗試補轉`, 'error');
            }

            // Small buffer to allow the UI to reflect 100% state before fetching
            setTimeout(() => {
                const _rjt = window._remoteJobType || 'transcode';
                // 只有 transcode（多機備份流程）才需要合併
                if (_rjt === 'transcode') {
                    appendLog('系統提示：所有遠端任務已完成，自動觸發合併與驗證程序...', 'system');
                    mergeHostOutputs();
                } else {
                    // 其他 TAB（verify/concat/report/transcribe/tts）：直接顯示完成
                    const _jl = JOB_LABELS[_rjt] || '任務';
                    appendLog(`✅ 遠端${_jl}任務已完成。`, 'system');
                    // 更新主進度條為完成狀態（嘗試 dash 和 underscore 兩種命名）
                    const _tab = window._activeJobTab || 'backup';
                    const _pfxMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts', drone_meta: 'dm' };
                    const _pfx = _pfxMap[_tab];
                    if (_pfx) {
                        const _bar = _progEl(_pfx, _tab, 'bar');
                        const _lbl = _progEl(_pfx, _tab, 'label');
                        const _eta = _progEl(_pfx, _tab, 'eta');
                        const _pct = _progEl(_pfx, _tab, 'pct');
                        const _area = _progArea(_pfx, _tab);
                        if (_area) _area.classList.remove('hidden');
                        if (_bar) { _bar.style.width = '100%'; _bar.style.background = 'linear-gradient(90deg, #22c55e, #4ade80)'; }
                        if (_lbl) _lbl.textContent = `✅ 遠端${_jl}完成`;
                        if (_eta) _eta.textContent = '';
                        if (_pct) _pct.textContent = '100%';
                    }
                    stopHeartbeatMonitor();
                    // 報表完成時刷新歷史列表
                    if (_rjt === 'report') {
                        loadReportHistory();
                    }
                    window.updateActionBarState('idle');
                    window.playDing();
                }
            }, 2000);
        }
        } finally {
            window._hbTickInFlight = false;
        }
    }, 5000);
}

/**
 * 收掉「遠端任務」的畫面狀態。
 *
 * 🔴 verify / concat / report 都在 **POST 之前**就把遠端進度面板點亮
 * （showRemoteMainProgress + initRemoteHostProgress）。提交失敗時只
 * `return` 的話，畫面會留一條停在 5% 的「遠端比對中…」，而 heartbeat
 * 根本還沒啟動、沒有人會來清它 —— 使用者拿到正確的錯誤訊息，卻同時
 * 看到一個假裝在跑的進度條（2026-08-12 /simplify）。
 */
function resetRemoteJobUi() {
    const tab = window._activeJobTab || 'backup';
    const prefixMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf',
                        report: 'rp', transcribe: 'tr', tts: 'tts', drone_meta: 'dm' };
    const prefix = prefixMap[tab];
    const panel = prefix && document.getElementById(prefix + '-remote-hosts-progress');
    if (panel) panel.classList.add('hidden');
    const area = prefix && _progArea(prefix, tab);
    if (area) area.classList.add('hidden');
    window._activeRemoteHosts = {};
    window._remoteJobType = null;
    resetProgress();
    window.updateActionBarState('idle');
}
window.resetRemoteJobUi = resetRemoteJobUi;

function stopHeartbeatMonitor() {
    if (window._heartbeatTimer) { clearInterval(window._heartbeatTimer); window._heartbeatTimer = null; }
    window._remoteDispatching = false;
    // Debounced idle switch：給 3 秒寬限讓下一輪 heartbeat 有機會接手
    // （transcode 補轉/merge 流程）。如果真的完成，3 秒後自然切 idle。
    if (window._idleSwitchTimer) clearTimeout(window._idleSwitchTimer);
    window._idleSwitchTimer = setTimeout(() => {
        window._idleSwitchTimer = null;
        if (typeof window.updateActionBarState === 'function') window.updateActionBarState('idle');
    }, 3000);
}

// 顯示對應 TAB 的主進度條（多機模式，不經過 progress Socket 事件）
// 進度元素解析器：多數 tab 是 `<pfx>-prog-bar`，transcribe/tts 是
// `<tab>_prog_bar` —— 三處各自組 id 時 transcribe 全部找不到。
function _progEl(pfx, tab, suffix) {
    return document.getElementById(pfx + '-prog-' + suffix)
        || document.getElementById(tab + '_prog_' + suffix);
}
function _progArea(pfx, tab) {
    return document.getElementById(pfx + '-progress')
        || document.getElementById(tab + '_progress_area');
}

function showRemoteMainProgress(label) {
    const tab = window._activeJobTab || 'backup';
    const pfxMap = { backup: 'bk', transcode: 'tc', concat: 'ct', verify: 'vf', report: 'rp', transcribe: 'tr', tts: 'tts' };
    const pfx = pfxMap[tab];
    if (!pfx) return;
    const container = _progArea(pfx, tab);
    const bar = _progEl(pfx, tab, 'bar');
    const lbl = _progEl(pfx, tab, 'label');
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
    appendLog('🖥️ 分派轉檔任務給遠端主機...', 'system');
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
                appendLog('✅ ' + h.name + ' (' + h.ip + ') OK', 'system');
                reachable.push(h);
            } else {
                updateHostProgress(h.ip, 0, '❌ HTTP ' + r.status, '#8b0000');
            }
        } catch (_) {
            updateHostProgress(h.ip, 0, '❌ 無法連線', '#8b0000');
            appendLog('❌ ' + h.name + ' 無法連線', 'error');
        }
    }));

    if (!reachable.length) {
        appendLog('❌ 所有遠端主機均無法連線，分派取消。', 'error');
        return;
    }

    // ── 磁碟代號 → UNC 映射表（提前載入：主機驗證與來源掃描都要用）──
    // 2026-07-21 煥民新村教訓：掃描來源用 T:\ 原樣丟給本機 agent，
    // 該機沒掛 T: → 0 個檔 → 分派整包取消。所有派發相關路徑一律先轉 UNC。
    // ⚠ 後端 enqueue/list_dir/validate_paths 現已同樣翻譯（core/drive_map）—
    // 前端這層是機隊 rollout 過渡的 belt-and-braces，機隊全數 ≥ 2.4.10 後
    // 可整批退場改以後端為唯一權威（勿只刪一半）。
    await window.ensureDriveMap();
    const _toUnc = window.toUncPath || (x => x);
    window._toUnc = _toUnc; // 保留給補轉邏輯的向後相容
    const mapCount = Object.keys(window._driveMap || {}).length;
    if (mapCount > 0) {
        appendLog('[UNC] 已載入 ' + mapCount + ' 個磁碟映射，來源與遠端路徑將自動轉換', 'system');
    }

    // Path-access pre-flight: skip hosts that can't see the source
    // paths (e.g. user pointed at G:\ which only exists on one
    // machine). Keep the rest running — transcode normally uses a
    // NAS-shared path accessible to all, so a partial miss usually
    // means user also ticked a host that doesn't have the mount.
    const sourceDirsForCheck = (ctx.cards || []).map(c => c[2]).filter(Boolean).map(p => _toUnc(p));
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
                    appendLog(`⚠️ ${h.name} 看不到來源 (${missing.join(', ')}) — 跳過此主機`, 'error');
                } else {
                    accessible.push(h);
                }
            } catch (e) {
                updateHostProgress(h.ip, 0, '✗ 驗證失敗', '#8b0000');
                appendLog(`⚠️ ${h.name} 路徑驗證失敗: ${e.message} — 跳過此主機`, 'error');
            }
        }));
        if (!accessible.length) {
            appendLog('❌ 沒有任何主機能存取來源路徑，分派取消。請確認來源放在 NAS 共享路徑或只勾有掛到該路徑的主機。', 'error');
            return;
        }
        if (accessible.length < reachable.length) {
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

    // ── 掃描來源：按卡分別掃描，保留卡名 ──────────────────────────────
    // cards: [[cardName, srcPath], ...] 或 scanDir fallback
    const cardEntries = []; // [{ cardName, files: [] }]
    const cards = ctx.cards || [];
    // 提升到函式層：fallback 分支在 else 內宣告，但下面建 expectedFiles
    // 時外層要用 —— 走到那條路會 ReferenceError 讓整個派發無聲死掉
    let projDir = '';
    // localRoot 先轉 UNC — 表單常填 T:\ 等網路磁碟代號，本機 agent 不一定有掛
    const localRoot = _toUnc(ctx.local_root || (document.getElementById('local_root') || {}).value || '');

    if (cards.length > 0) {
        if (ctx.use_absolute_paths) {
            // Bypass localRoot fallback — sources are already absolute
            for (const card of cards) {
                const cardName = card[0];
                const absoluteSrcPath = _toUnc(card[2]);   // 磁碟代號→UNC（本機碟原樣通過）
                if (!absoluteSrcPath) continue;
                try {
                    const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: absoluteSrcPath })
                    });
                    if (r.ok) {
                        const d = await r.json();
                        // 後端回的是 d.error（如「目錄不存在: <路徑>」），不是 d.status ——
                        // 之前檢查錯欄位，「路徑不存在」被誤報成「資料夾裡沒影片」，
                        // 使用者看不到掃的是哪條路徑（2026-08-12 赤兔派發除錯的教訓）
                        if (d.error) {
                            appendLog('⚠️ [' + cardName + '] ' + d.error, 'error');
                        } else if (d.files && d.files.length > 0) {
                            cardEntries.push({ cardName, files: d.files, cardDir: absoluteSrcPath });
                            appendLog('📁 ' + cardName + ': ' + d.files.length + ' 個影片 (Standalone)', 'system');
                        } else {
                            appendLog('⚠️ [' + cardName + '] 資料夾存在但沒有任何符合的影片檔案（' + absoluteSrcPath + '）', 'error');
                        }
                    }
                } catch (e) { appendLog('⚠️ 掃描 ' + cardName + ' 失敗: ' + e.message, 'error'); }
            }
        } else {
            // 有記憶卡資訊：按卡掃 (Main Flow - requires backup structure mapping)
            for (const [cardName] of cards) {
                const cardDir = localRoot ? localRoot + '/' + ctx.project_name + '/' + cardName : '';
                if (!cardDir) continue;
                try {
                    const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: cardDir })
                    });
                    if (r.ok) {
                        const d = await r.json();
                        // 同上：認 d.error、訊息帶掃描路徑，別把「路徑不存在」講成「沒影片」
                        if (d.error) {
                            appendLog('⚠️ [' + cardName + '] ' + d.error, 'error');
                        } else if (d.files && d.files.length > 0) {
                            cardEntries.push({ cardName, files: d.files, cardDir });
                            appendLog('📁 ' + cardName + ': ' + d.files.length + ' 個影片', 'system');
                        } else {
                            appendLog('⚠️ [' + cardName + '] 資料夾存在但沒有任何符合的影片檔案（' + cardDir + '）', 'error');
                        }
                    }
                } catch (e) { appendLog('⚠️ 掃描 ' + cardName + ' 失敗: ' + e.message, 'error'); }
            }
        }
    } else {
        // Fallback：掃 project 目錄，card 名稱設為空
        projDir = localRoot ? localRoot + '/' + ctx.project_name : '';
        if (projDir) {
            try {
                const r = await fetch(getComputeBaseUrl() + '/api/v1/list_dir', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: projDir })
                });
                if (r.ok) { const d = await r.json(); if (d.files && d.files.length) cardEntries.push({ cardName: '', files: d.files }); }
            } catch (e) { appendLog('⚠️ 無法掃描來源: ' + e.message, 'error'); }
        }
    }

    const totalFiles = cardEntries.reduce((s, c) => s + c.files.length, 0);
    if (totalFiles === 0) {
        appendLog('⚠️ 找不到來源檔案，分派取消。', 'error');
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
    appendLog('📋 共 ' + totalFiles + ' 個檔案（' + cardEntries.length + ' 張卡），分配給 ' + n + ' 台主機', 'system');

    const hostCardMaps = reachable.map(() => ({}));
    allCardFiles.forEach(({ cardName, file }, idx) => {
        const hostIdx = idx % n;
        if (!hostCardMaps[hostIdx][cardName]) hostCardMaps[hostIdx][cardName] = [];
        hostCardMaps[hostIdx][cardName].push(file);
    });

    _dispatch.proxyRoot = ctx.proxy_root || '';       // 失聯重派／合併都從這裡拿（見檔頭）
    _dispatch.projectName = ctx.project_name || '';

    window._activeRemoteHosts = {};
    for (let i = 0; i < reachable.length; i++) {
        const h = reachable[i];
        const cardMap = hostCardMaps[i];
        const cardNames = Object.keys(cardMap);
        if (!cardNames.length) { updateHostProgress(h.ip, 100, '無分配檔案', '#374151'); continue; }

        const totalForHost = cardNames.reduce((s, c) => s + cardMap[c].length, 0);
        updateHostProgress(h.ip, 10, '送出中... (' + totalForHost + ' 個)', '#1f538d');

        const hostDestBase = _destRoot() ? _destRoot() + '/HostDispatch_' + h.name.replace(/\s+/g, '_') : '';
        let hostOk = false;
        // 🔴 記下這台「真的接下了哪些檔案」—— 它中途掛掉時，重派要靠這份
        // 清單才知道該補什麼。原本這裡存的是全部檔案（每台都一樣），
        // 等於沒有派工紀錄（2026-08-13）。
        const acceptedFiles = [];
        for (const cardName of cardNames) {
            const files = cardMap[cardName].map(_toUnc);
            const cardSuffix = cardName ? '/' + cardName : '';
            const dest = _toUnc(hostDestBase ? hostDestBase + cardSuffix : '');
            try {
                appendLog('→ 送出 [' + (cardName || '(all)') + '] ' + files.length + ' 個給 ' + h.name, 'system');
                const r = await fetch('http://' + h.ip + '/api/v1/jobs/transcode', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sources: files, dest_dir: dest })
                });
                const res = await r.json().catch(() => ({}));
                // 🔴 不看 r.ok 的話，遠端回 422/500 也會被記成「已接收」，
                // 接著 heartbeat 看它閒著就報「轉檔完成」—— 什麼都沒轉（2026-08-12）
                if (!r.ok) throw new Error(res.detail || res.message || ('HTTP ' + r.status));
                appendLog('✅ ' + h.name + ' [' + (cardName || 'all') + '] 接收，任務 ID: ' + (res.job_id || '?'), 'system');
                files.forEach(f => acceptedFiles.push({ cardName, file: f }));
                hostOk = true;
            } catch (err) {
                appendLog('❌ ' + h.name + ' 拒收 [' + (cardName || 'all') + ']: ' + err.message, 'error');
            }
        }
        if (hostOk) {
            updateHostProgress(h.ip, 20, '轉檔中...', '#d48a04');
            window._activeRemoteHosts[h.ip] = {
                host: h, assigned: acceptedFiles, destDirs: [hostDestBase],
                state: 'running', lastSeen: Date.now(), startTime: Date.now(),
                expectedJobs: cardNames.length, pct: 0,
            };
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
    // 派工時定住的值（心跳只會在 dispatchRemoteTranscode 之後才走到這裡）
    const proxyRoot = _dispatch.proxyRoot;
    const projName = _dispatch.projectName;
    if (!proxyRoot || !projName) {
        appendLog('請先填寫 Proxy Root 與專案名稱。', 'error'); return;
    }
    appendLog('📁 合併遠端主機輸出...', 'system');
    try {
        const r = await fetch(getComputeBaseUrl() + '/api/v1/merge_host_outputs', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy_root: proxyRoot, project_name: projName })
        });
        const d = await r.json();
        if (d.status === 'ok') {
            appendLog('✅ 合併完成！共 ' + d.merged + ' 個檔案。', 'system');
            // 個別檔案搬移失敗要講出來（後端此時也不會刪來源目錄）
            if (Array.isArray(d.errors) && d.errors.length) {
                appendLog('⚠️ 有 ' + d.errors.length + ' 個檔案沒搬成（來源目錄保留）：', 'error');
                d.errors.slice(0, 10).forEach(e => appendLog('   • ' + e, 'error'));
            }
            stopHeartbeatMonitor();

            // 先定義驗證過關後執行的後續作業
            window.executePostMergeJobs = function(flags) {
                if (flags && (flags.do_concat || flags.do_report)) {
                    appendLog('🔄 自動觸發後續作業...', 'system');
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
                                appendLog('🏗️ 串帶將由 [' + concatHostName + '] 執行' + (concatHost ? '（遠端優先）' : '（遠端不可用，退回本機）'), 'system');
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
                                        const j3 = await r3.json().catch(() => ({}));
                                        // 非 2xx 也要退回本機，不能當成已排隊（2026-08-12）
                                        if (!r3.ok) throw new Error(j3.detail || j3.message || ('HTTP ' + r3.status));
                                        appendLog('📌 串帶 [' + cardName + '] 排隊中 @ ' + concatHostName + '，任務 ID: ' + (j3.job_id || '?'), 'system');
                                        submitted = true;
                                    } catch (err) {
                                        appendLog('⚠️ 遠端串帶失敗 (' + err.message + ') — 退回本機', 'error');
                                    }
                                    if (!submitted && concatHost) {
                                        // Remote died mid-dispatch → fall back to local for this card.
                                        try {
                                            const r3b = await fetch(localUrl + '/api/v1/jobs/concat', {
                                                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(concatPayload)
                                            });
                                            const j3b = await r3b.json().catch(() => ({}));
                                            if (!r3b.ok) throw new Error(j3b.detail || j3b.message || ('HTTP ' + r3b.status));
                                            appendLog('📌 串帶 [' + cardName + '] 改由本機排隊，任務 ID: ' + (j3b.job_id || '?'), 'system');
                                        } catch (err2) {
                                            appendLog('❌ 串帶 [' + cardName + '] 本機也失敗: ' + err2.message, 'error');
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
                                    do_gdrive: false, do_gchat: false,
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
                                appendLog('📊 報表任務已提交: ' + j4.status, 'system');
                            }
                            window._postMergeFlags = null;
                        } catch (e2) {
                            appendLog('❌ 後續作業提交失敗: ' + e2.message, 'error');
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

                appendLog('🔍 正在驗證 Proxy 轉檔完整性（後端掃描比對）...', 'system');
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

                        appendLog(`🔍 [${cardName}] 比對來源: ${sourceDir} → ${proxyDir}`, 'system');

                        try {
                            let r = await fetch(getComputeBaseUrl() + '/api/v1/compare_source', {
                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ source_dir: sourceDir, output_dir: proxyDir, flat_proxy: true })
                            });
                            let d = await r.json();

                            if (d.status === 'error' && cardSrcPath && sourceDir === cardSrcPath) {
                                appendLog(`⚠️ [${cardName}] 原始路徑不可達，改用備份副本: ${backupCopyDir}`, 'system');
                                sourceDir = backupCopyDir;
                                r = await fetch(getComputeBaseUrl() + '/api/v1/compare_source', {
                                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ source_dir: sourceDir, output_dir: proxyDir, flat_proxy: true })
                                });
                                d = await r.json();
                            }

                            if (d.status === 'ok') {
                                const missing = Array.isArray(d.missing) ? d.missing : [];
                                appendLog(`📋 [${cardName}] 來源 ${d.source_count} 個，Proxy ${d.proxy_count} 個，缺少 ${missing.length} 個`, 'system');
                                missing.forEach(srcPath => allMissing.push({ cardName, sourceFile: srcPath }));
                            } else {
                                appendLog(`⚠️ [${cardName}] 驗證仍失敗: ${d.message || JSON.stringify(d)}`, 'error');
                            }
                        } catch (cardErr) {
                            appendLog(`⚠️ [${cardName}] 驗證失敗: ${cardErr.message}`, 'error');
                        }
                    }

                    if (allMissing.length === 0) {
                        appendLog('✅ 所有 Proxy 檔案皆已正常產出！', 'system');
                        if (ms) ms.textContent = '驗證完成';
                        if (window.executePostMergeJobs) window.executePostMergeJobs(flags);
                        return;
                    }

                    window._remoteDispatchExpectedRetryCount = (window._remoteDispatchExpectedRetryCount || 0) + 1;
                    appendLog(`[!] 發現 ${allMissing.length} 個缺失的 Proxy 檔案，啟動補轉 (第 ${window._remoteDispatchExpectedRetryCount} 次)...`, 'error');

                    if (window._remoteDispatchExpectedRetryCount > 3) {
                        appendLog('[X] 補件重試已達上限 (3次)，放棄重試，啟動後續作業。', 'error');
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
                        const blacklist = new Set(window._retryFailedHosts || []);
                        if (blacklist.size > 0) {
                            appendLog(`[i] 跳過黑名單主機 (${[...blacklist].join(', ')})`, 'system');
                        }
                        // 與失聯重派共用同一套「誰能接活」判定（ping + 看得到來源）
                        liveRemoteHosts = await pickLiveDispatchHosts();
                        if (liveRemoteHosts.length === 0) {
                            appendLog('[>] 目前無可執行的遠端主機（黑名單外都不可達或看不到來源），改用本機補轉', 'system');
                            useLocal = true;
                        }
                    }

                    if (useLocal) {
                        // 本機補轉：直接送到 localhost，100% 路徑可達
                        appendLog(`[>] 第 ${retryCount} 次補轉：使用本機轉檔（保證路徑可達）`, 'system');
                        // 補轉送到主任務跑的同一台 = serve 這頁的那台
                        const localUrl = window.location.origin;
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
                                const j = await r.json().catch(() => ({}));
                                if (!r.ok) throw new Error(j.detail || j.message || ('HTTP ' + r.status));
                                appendLog(`[OK] 本機補轉 [${cardName}] ${srcFiles.length} 個檔案排隊，任務 ID: ${j.job_id || '?'}`, 'system');
                                localStarted++;
                            } catch (err) {
                                appendLog(`[X] 本機補轉 [${cardName}] 失敗: ${err.message}`, 'error');
                            }
                        }

                        if (localStarted > 0) {
                            // 本機轉檔：用 Socket.IO task_status 事件偵測完成，再跑驗證
                            appendLog(`[>] 本機補轉中，等待 ${localStarted} 個任務完成...`, 'system');
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
                        appendLog(`[>] 第 ${retryCount} 次補轉：平均派給 ${liveRemoteHosts.length} 台可執行的遠端主機`, 'system');

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
                            const retryBase = _unc(_destRoot() + '/HostDispatch_Retry_'
                                                   + dist.host.name.replace(/\s+/g, '_'));
                            for (const [cardName, srcFiles] of Object.entries(dist.byCard)) {
                                const destDir = retryBase + '/' + cardName;
                                const uncFiles = srcFiles.map(_unc);
                                try {
                                    const r = await fetch('http://' + dist.host.ip + '/api/v1/jobs/transcode', {
                                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ sources: uncFiles, dest_dir: destDir })
                                    });
                                    const j = await r.json().catch(() => ({}));
                                    // 拒收要走 catch 的黑名單邏輯，別把該機標成補轉中
                                    if (!r.ok) throw new Error(j.detail || j.message || ('HTTP ' + r.status));
                                    appendLog(`[OK] ${dist.host.name} [${cardName}] 補轉排隊，任務 ID: ${j.job_id || '?'}`, 'system');
                                    requestsStarted++;
                                    // 補轉中的機器也可能掛掉 —— 一樣要留派工紀錄供重派
                                    const prevR = window._activeRemoteHosts[dist.host.ip] || {};
                                    window._activeRemoteHosts[dist.host.ip] = {
                                        ...prevR,
                                        host: dist.host, state: 'running', pct: prevR.pct || 0,
                                        assigned: [...(prevR.assigned || []),
                                                   ...srcFiles.map(f => ({ cardName, file: f }))],
                                        destDirs: [...new Set([...(prevR.destDirs || []), retryBase])],
                                        lastSeen: Date.now(), startTime: Date.now(),
                                        expectedJobs: Object.keys(dist.byCard).length
                                    };
                                } catch (err) {
                                    appendLog(`[X] ${dist.host.name} [${cardName}] 補轉失敗: ${err.message}`, 'error');
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
                            appendLog('[>] 遠端補轉全部失敗，改用本機', 'system');
                            window._remoteDispatchExpectedRetryCount = 2;
                            window.verifyAndRetryMissingProxies(proxyRoot, projName, flags);
                        }
                    }

                } catch (e) {
                    appendLog('❌ 驗證時發生錯誤: ' + e.message, 'error');
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

        } else { appendLog('❌ 合併失敗: ' + (d.message || d.detail || ('HTTP ' + r.status)), 'error'); }
    } catch (e) { appendLog('❌ 合併錯誤: ' + e.message, 'error'); }
}
