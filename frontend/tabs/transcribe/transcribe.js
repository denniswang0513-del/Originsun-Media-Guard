import { appendLog, getComputeBaseUrl, getAgentBaseUrl, addStandaloneSource, setupDragAndDrop, setupInputDrop, pickPath } from '../../js/shared/utils.js';

window.modelCacheStatus = {};
window.isDownloadingModel = false;

export async function fetchModelStatus() {
    try {
        const res = await fetch('/api/v1/models/status');
        const data = await res.json();
        if (data.status) {
            window.modelCacheStatus = data.status;
            window.updateModelStatus();
        }
    } catch (e) {
        console.error("無法取得模型狀態", e);
    }
}

export async function pickMultiFiles() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/utils/pick_file');
        const data = await res.json();
        if (data.path) {
            let paths = Array.isArray(data.path) ? data.path : [data.path];
            paths.forEach(p => {
                if (p) {
                    addStandaloneSource('transcribe_file_list', p);
                }
            });
        }
    } catch (err) {
        console.error("無法開啟選取視窗", err);
    }
}

export function updateModelStatus() {
    const selector = document.getElementById('transcribe_model');
    const badge = document.getElementById('model_status_badge');
    const btn = document.getElementById('btn_download_model');
    if (!selector || !badge || !btn) return;
    
    const size = selector.value;
    const isCached = window.modelCacheStatus[size] === true;
    
    if (window.isDownloadingModel) {
         return;
    }

    btn.disabled = false;
    btn.textContent = '⬇️ 下載模型';

    if (isCached) {
        badge.textContent = '✅ 模型 Ready';
        badge.className = 'px-2 py-0.5 rounded text-xs bg-emerald-900/50 text-emerald-400 border border-emerald-800';
        btn.classList.add('hidden');
    } else {
        badge.textContent = '☁️ 模型待下載';
        badge.className = 'px-2 py-0.5 rounded text-xs bg-gray-700 text-gray-300 border border-gray-600';
        btn.classList.remove('hidden');
    }
}

export async function downloadSelectedModel() {
    const size = document.getElementById('transcribe_model').value;
    if (!size) return;
    
    const btn = document.getElementById('btn_download_model');
    const badge = document.getElementById('model_status_badge');
    
    btn.disabled = true;
    btn.textContent = '⏳ 下載中...';
    badge.textContent = '🔄 模型下載中...';
    badge.className = 'px-2 py-0.5 rounded text-xs bg-blue-900/50 text-blue-400 border border-blue-800';
    window.isDownloadingModel = true;

    try {
        const res = await fetch('/api/v1/models/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_size: size })
        });
        if (!res.ok) throw new Error("API 回應錯誤");
    } catch (e) {
        console.error(e);
        alert("啟動下載失敗");
        window.isDownloadingModel = false;
        window.updateModelStatus();
    }
}

window.transcribeMode = 'merged'; // default
export function setTranscribeMode(mode) {
    window.transcribeMode = mode;
    const toggle = document.getElementById('transcribe_mode_toggle');
    const hint = document.getElementById('transcribe_mode_hint');
    if (!toggle) return;
    toggle.querySelectorAll('button').forEach(btn => {
        if (btn.dataset.mode === mode) {
            btn.className = 'flex-1 px-3 py-1.5 text-xs font-semibold bg-emerald-800 text-emerald-100 border-r border-[#444] transition-colors';
        } else {
            btn.className = 'flex-1 px-3 py-1.5 text-xs font-semibold bg-[#1e1e1e] text-gray-400 transition-colors';
        }
    });
    if (hint) {
        hint.textContent = mode === 'merged' ? '所有來源合併為一份逐字稿' : '每個來源檔案各自輸出同名的 .srt 與 .txt';
    }
}

export function collectTranscribePayload() {
    const rows = document.getElementById('transcribe_file_list').children;
    const sources = Array.from(rows).map(row => row.querySelector('input')?.value.trim()).filter(v => v);

    if (!sources.length) {
        alert('請先加入影音檔案來源！');
        return { valid: false };
    }
    const destDir = document.getElementById('transcribe_dest')?.value.trim();
    if (!destDir) {
        alert('請指定輸出目標資料夾！');
        return { valid: false };
    }

    const modelSize = document.getElementById('transcribe_model')?.value || 'turbo';
    const outputSrt = document.getElementById('transcribe_srt')?.checked || false;
    const outputTxt = document.getElementById('transcribe_txt')?.checked || false;
    const outputWav = document.getElementById('transcribe_wav')?.checked || false;
    const generateProxy = document.getElementById('transcribe_proxy')?.checked || false;

    if (!outputSrt && !outputTxt) {
        alert('請至少勾選一種輸出格式 (.srt 或 .txt)！');
        return { valid: false };
    }

    const payload = {
        task_type: "transcribe",
        sources: sources,
        dest_dir: destDir,
        model_size: modelSize,
        output_srt: outputSrt,
        output_txt: outputTxt,
        output_wav: outputWav,
        generate_proxy: generateProxy,
        individual_mode: window.transcribeMode === 'individual'
    };

    return { valid: true, payload, name: '逐字稿' };
}
window.collectTranscribePayload = collectTranscribePayload;

export async function submitTranscribeJob() {
    window._activeJobTab = 'transcribe';
    const collected = collectTranscribePayload();
    if (!collected.valid) return;
    const payload = collected.payload;

    // Show progress area
    const progArea = document.getElementById('transcribe_progress_area');
    if (progArea) {
        progArea.classList.remove('hidden');
        document.getElementById('transcribe_prog_label').textContent = '佇列中等待執行...';
        document.getElementById('transcribe_prog_pct').textContent = '0%';
        document.getElementById('transcribe_prog_bar').style.width = '0%';
    }

    const btn = document.querySelector('#tab_transcribe button[onclick="submitTranscribeJob()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = '🕒 送出中...';
    btn.disabled = true;
    btn.classList.add('opacity-70', 'cursor-not-allowed');

    // 讀取處理主機
    const trHostObj = window.collectSelectedHost ? window.collectSelectedHost('tr_host_checkboxes') : { name: '本機', ip: 'local' };
    const isLocal = trHostObj.ip === 'local';
    const trHostUrl = isLocal ? getAgentBaseUrl() : 'http://' + trHostObj.ip;

    if (!isLocal && window.initRemoteHostProgress) {
        window._remoteJobType = 'transcribe';
        window._activeRemoteHosts = {};
        if (window.showRemoteMainProgress) window.showRemoteMainProgress('遠端轉錄中...');
        window.initRemoteHostProgress([trHostObj]);
    }

    try {
        const res = await fetch(trHostUrl + '/api/v1/jobs/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await res.json();
        if (res.ok) {
            appendLog(`✅ 已成功將逐字稿任務送至 [${trHostObj.name}] 佇列。`, 'system');
            if (result.warning) appendLog(`⚠️ ${result.warning}`, 'system');
            if (!isLocal) {
                if (window.updateHostProgress) window.updateHostProgress(trHostObj.ip, 20, '已排程，轉錄中...', '#059669');
                window._activeRemoteHosts[trHostObj.ip] = { host: trHostObj, lastSeen: Date.now(), startTime: Date.now(), logOffset: 0 };
                if (window.startHeartbeatMonitor) window.startHeartbeatMonitor();
            }
            // Show progress area
            const progArea = document.getElementById('transcribe_progress_area');
            if (progArea) progArea.classList.remove('hidden');
            
            document.getElementById('transcribe_prog_label').textContent = '佇列中等待執行...';
            document.getElementById('transcribe_prog_pct').textContent = '0%';
            document.getElementById('transcribe_prog_bar').style.width = '0%';
        } else {
            alert(`提交失敗: ${result.detail || JSON.stringify(result)}`);
            btn.innerHTML = originalText;
            btn.disabled = false;
            btn.classList.remove('opacity-70', 'cursor-not-allowed');
        }
    } catch (err) {
        alert(`網路或連線錯誤: ${err.message}`);
        btn.innerHTML = originalText;
        btn.disabled = false;
        btn.classList.remove('opacity-70', 'cursor-not-allowed');
    }
}

export function initTranscribeTab() {
    fetchModelStatus();
    setupDragAndDrop('transcribe_file_list', () => addStandaloneSource('transcribe_file_list', ''));
    setupInputDrop('transcribe_dest');
    // Wire align mode UI (lives in the same tab)
    if (typeof _wireAlignTab === 'function') _wireAlignTab();
}

export async function pickTranscribeFolder() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/utils/pick_folder');
        const data = await res.json();
        if (data.path) {
            // List video files from the selected folder
            const listRes = await fetch(getAgentBaseUrl() + '/api/v1/list_dir', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: data.path })
            });
            const listData = await listRes.json();
            if (listData.files && listData.files.length > 0) {
                listData.files.forEach(f => {
                    addStandaloneSource('transcribe_file_list', f);
                });
                appendLog(`📂 已載入 ${listData.files.length} 個影音檔案`, 'system');
            } else {
                appendLog('⚠️ 資料夾中沒有找到影音檔案', 'info');
            }
        }
    } catch (err) {
        console.error("無法開啟資料夾選取視窗", err);
    }
}

window.fetchModelStatus = fetchModelStatus;
window.pickMultiFiles = pickMultiFiles;
window.pickTranscribeFolder = pickTranscribeFolder;
window.updateModelStatus = updateModelStatus;
window.downloadSelectedModel = downloadSelectedModel;
window.setTranscribeMode = setTranscribeMode;
window.submitTranscribeJob = submitTranscribeJob;


// ─────────────────────────────────────────────────────────────────────────
// 對齊已有文字稿（Forced Alignment）模式
// ─────────────────────────────────────────────────────────────────────────

window._aiMode = 'transcribe';
window._alignPairs = new Map();   // pairId → { source, transcript, format, segCount }
window._alignBindActiveId = null; // pairId currently being bound in modal
window._alignBindSrc = 'paste';

export function setAiMode(mode) {
    window._aiMode = mode;
    const tx = document.getElementById('transcribe_section');
    const al = document.getElementById('align_section');
    const toggle = document.getElementById('ai_mode_toggle');
    if (!tx || !al || !toggle) return;

    if (mode === 'align') {
        tx.classList.add('hidden');
        al.classList.remove('hidden');
    } else {
        tx.classList.remove('hidden');
        al.classList.add('hidden');
    }
    toggle.querySelectorAll('button').forEach(btn => {
        if (btn.dataset.aiMode === mode) {
            btn.className = 'flex-1 px-4 py-2 text-sm font-semibold bg-emerald-800 text-emerald-100 border-r border-[#444] transition-colors';
        } else {
            btn.className = 'flex-1 px-4 py-2 text-sm font-semibold bg-[#1e1e1e] text-gray-400 transition-colors';
        }
    });
    if (mode === 'align' && window._alignPairs.size === 0) {
        addAlignPair();
    }
    updateAlignSubmitBtn();
}

function _alignPairId() {
    return 'align_pair_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
}

export function addAlignPair(defaultPath = '') {
    const list = document.getElementById('align_pair_list');
    if (!list) return;
    const id = _alignPairId();
    const inputId = id + '_src';
    window._alignPairs.set(id, { source: defaultPath, transcript: '', format: 'auto', segCount: 0 });

    const row = document.createElement('div');
    row.className = 'align-pair flex gap-1.5 items-center p-1 bg-[#0e0e0e] border border-[#3a3a3a] rounded hover:border-[#555] transition-colors';
    row.dataset.pairId = id;
    row.innerHTML = `
        <span class="text-[11px] text-gray-500 w-6 text-center align-pair-num shrink-0">#1</span>
        <input type="text" id="${inputId}" class="align-pair-source flex-1 min-w-0 bg-[#1a1a1a] border border-[#444] rounded px-2 py-1 text-sm focus:border-emerald-500"
            placeholder="影片路徑（拖放 / 選檔案）" value="${defaultPath}">
        <button type="button" class="btn-pick-video shrink-0 text-gray-400 hover:text-white bg-[#2a2a2a] hover:bg-[#444] border border-[#444] w-8 h-7 rounded text-sm flex items-center justify-center" title="選擇影片檔案">🎬</button>
        <button type="button" class="btn-bind-transcript shrink-0 flex items-center gap-1 px-2 h-7 rounded text-[11px] border transition-colors bg-yellow-900/30 hover:bg-yellow-900/50 text-yellow-400 border-yellow-800/60" title="綁定文字稿">
            <span class="align-pair-status whitespace-nowrap">📄 未綁定</span>
        </button>
        <button type="button" class="btn-remove-pair shrink-0 text-red-400 hover:text-red-300 hover:bg-red-900/20 w-7 h-7 rounded font-bold text-sm" title="刪除">×</button>
    `;
    list.appendChild(row);

    const sourceInput = row.querySelector('.align-pair-source');
    sourceInput.addEventListener('input', () => {
        const st = window._alignPairs.get(id);
        if (st) { st.source = sourceInput.value.trim(); updateAlignSubmitBtn(); }
    });
    row.querySelector('.btn-pick-video').addEventListener('click', async () => {
        const path = await _pickFileHybrid('選擇影片檔案');
        if (path) {
            sourceInput.value = path;
            sourceInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
    });
    setupInputDrop(inputId);

    row.querySelector('.btn-remove-pair').addEventListener('click', () => {
        window._alignPairs.delete(id);
        row.remove();
        renumberAlignPairs();
        updateAlignSubmitBtn();
    });
    row.querySelector('.btn-bind-transcript').addEventListener('click', () => openAlignBindModal(id));

    renumberAlignPairs();
    updateAlignSubmitBtn();
    return id;
}

function _refreshPairBindStatus(id) {
    const st = window._alignPairs.get(id);
    const row = document.querySelector(`.align-pair[data-pair-id="${id}"]`);
    if (!st || !row) return;
    const status = row.querySelector('.align-pair-status');
    const btn = row.querySelector('.btn-bind-transcript');
    if (st.transcript) {
        status.textContent = `📄 ${st.segCount} 段 ✓`;
        btn.className = 'btn-bind-transcript shrink-0 flex items-center gap-1 px-2 h-7 rounded text-[11px] border transition-colors bg-emerald-900/30 hover:bg-emerald-900/50 text-emerald-400 border-emerald-800/60';
        btn.title = '已綁定 — 點擊修改';
    } else {
        status.textContent = '📄 未綁定';
        btn.className = 'btn-bind-transcript shrink-0 flex items-center gap-1 px-2 h-7 rounded text-[11px] border transition-colors bg-yellow-900/30 hover:bg-yellow-900/50 text-yellow-400 border-yellow-800/60';
        btn.title = '綁定文字稿';
    }
}

function renumberAlignPairs() {
    const list = document.getElementById('align_pair_list');
    if (!list) return;
    Array.from(list.children).forEach((row, idx) => {
        const num = row.querySelector('.align-pair-num');
        if (num) num.textContent = '#' + (idx + 1);
    });
}

function updateAlignSubmitBtn() {
    const btn = document.getElementById('align_submit_btn');
    if (!btn) return;
    const dest = document.getElementById('align_dest')?.value.trim();
    let allBound = window._alignPairs.size > 0;
    let unboundCount = 0;
    window._alignPairs.forEach(st => {
        if (!st.source || !st.transcript) {
            allBound = false;
            unboundCount++;
        }
    });
    if (!dest || !allBound) {
        btn.disabled = true;
        btn.textContent = !dest
            ? '請先指定輸出資料夾'
            : `還有 ${unboundCount} 個配對未完整 (影片或文字稿)`;
    } else {
        btn.disabled = false;
        btn.textContent = `開始對齊 (${window._alignPairs.size} 支影片)`;
    }
}

// ─── Batch import ───────────────────────────────────────────────────────

const _VIDEO_EXTS_FOR_LIST = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".webm", ".ts"];
const _TRANSCRIPT_EXTS = [".txt", ".srt"];

// Native tkinter pickers open on the agent's machine. When the user is
// browsing a remote agent (192.168.x.x, cloudflared tunnel, etc.) they
// can't see those dialogs — use the in-browser NAS modal instead.
function _agentIsLocal() {
    const h = window.location.hostname;
    return h === 'localhost' || h === '127.0.0.1';
}

async function _pickFolderHybrid(title = '選擇資料夾') {
    if (!_agentIsLocal() && typeof window.openNasBrowser === 'function') {
        return (await window.openNasBrowser({ title, mode: 'folder', showFiles: false })) || '';
    }
    try {
        const r = await fetch(getAgentBaseUrl() + '/api/v1/utils/pick_folder');
        return (await r.json()).path || '';
    } catch (e) { console.error(e); return ''; }
}

async function _pickFileHybrid(title = '選擇影片') {
    if (!_agentIsLocal() && typeof window.openNasBrowser === 'function') {
        return (await window.openNasBrowser({ title, mode: 'file', showFiles: true })) || '';
    }
    try {
        const r = await fetch(getAgentBaseUrl() + '/api/v1/utils/pick_file');
        const d = await r.json();
        return Array.isArray(d.path) ? (d.path[0] || '') : (d.path || '');
    } catch (e) { console.error(e); return ''; }
}

async function _pickMultiFilesHybrid(title = '選擇影片') {
    // openNasBrowser is single-select; on remote we fall back to folder
    // selection and let the caller list videos in it.
    if (!_agentIsLocal() && typeof window.openNasBrowser === 'function') {
        const folder = await window.openNasBrowser({ title: '遠端模式：請選資料夾(會列出內含影片)', mode: 'folder', showFiles: false });
        if (!folder) return [];
        return await _listDir(folder, _VIDEO_EXTS_FOR_LIST);
    }
    try {
        const r = await fetch(getAgentBaseUrl() + '/api/v1/utils/pick_files');
        return (await r.json()).paths || [];
    } catch (e) { console.error(e); return []; }
}

async function _listDir(folder, exts) {
    const r = await fetch(getAgentBaseUrl() + '/api/v1/list_dir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: folder, exts }),
    });
    const data = await r.json();
    return data.files || [];
}

async function _readTextFile(path) {
    try {
        const r = await fetch(getAgentBaseUrl() + '/api/v1/utils/read_text?path=' + encodeURIComponent(path));
        const data = await r.json();
        return data.ok ? data.text : null;
    } catch (e) { return null; }
}

function _basename(p) {
    return (p || '').split(/[\\/]/).pop() || '';
}
function _stem(p) {
    const b = _basename(p);
    const i = b.lastIndexOf('.');
    return i > 0 ? b.slice(0, i) : b;
}

export async function alignBatchImport() {
    appendLog(_agentIsLocal()
        ? '📂 開啟資料夾選擇器…(若沒看到對話框,請檢查工作列或 Alt-Tab 切換)'
        : '📂 開啟伺服器目錄瀏覽器…(遠端模式)', 'system');
    const folder = await _pickFolderHybrid('選擇影片資料夾');
    if (!folder) { appendLog('(已取消)', 'info'); return; }

    appendLog(`📂 掃描資料夾: ${folder}`, 'system');
    let videos = [];
    let transcripts = [];
    try {
        [videos, transcripts] = await Promise.all([
            _listDir(folder, _VIDEO_EXTS_FOR_LIST),
            _listDir(folder, _TRANSCRIPT_EXTS),
        ]);
    } catch (e) {
        appendLog(`❌ 掃描失敗: ${e.message}`, 'error');
        return;
    }
    if (videos.length === 0) {
        appendLog('⚠️ 此資料夾沒有影片檔', 'info');
        return;
    }

    // Build stem → transcript path map; prefer .srt over .txt when both exist
    const tByStem = new Map();
    transcripts.forEach(t => {
        const stem = _stem(t);
        const ext = (t.match(/\.[^.]+$/) || [''])[0].toLowerCase();
        const cur = tByStem.get(stem);
        if (!cur || (ext === '.srt' && cur.ext === '.txt')) {
            tByStem.set(stem, { path: t, ext });
        }
    });

    let added = 0, autoBound = 0;
    for (const v of videos) {
        const stem = _stem(v);
        const id = addAlignPair(v);
        added++;
        const match = tByStem.get(stem);
        if (match) {
            const text = await _readTextFile(match.path);
            if (text && text.trim()) {
                const fmt = match.ext === '.srt' ? 'srt' : 'txt';
                const n = countSegments(text, fmt);
                const st = window._alignPairs.get(id);
                if (st) {
                    st.transcript = text;
                    st.format = fmt;
                    st.segCount = n;
                    _refreshPairBindStatus(id);
                    autoBound++;
                }
            }
        }
    }
    updateAlignSubmitBtn();
    appendLog(
        `✅ 批次匯入完成：${added} 支影片，自動綁定 ${autoBound} 份文字稿（${added - autoBound} 份待手動綁定）`,
        'system',
    );
}

export async function pickMultiVideosForAlign() {
    appendLog(_agentIsLocal()
        ? '📁 開啟檔案選擇器…(若沒看到對話框,請檢查工作列或 Alt-Tab 切換)'
        : '📁 開啟伺服器目錄瀏覽器…(遠端模式 — 將列出資料夾內所有影片)', 'system');
    const paths = await _pickMultiFilesHybrid('選擇影片');
    if (paths.length === 0) { appendLog('(已取消或無影片)', 'info'); return; }
    paths.forEach(p => addAlignPair(p));
    appendLog(`📁 已加入 ${paths.length} 支影片,請各自綁定文字稿`, 'system');
}

// ─── Bind modal ─────────────────────────────────────────────────────────

export function openAlignBindModal(pairId) {
    window._alignBindActiveId = pairId;
    const st = window._alignPairs.get(pairId);
    if (!st) return;
    const modal = document.getElementById('align_bind_modal');
    const title = document.getElementById('align_bind_title');
    const ta = document.getElementById('align_bind_text');
    if (title) {
        const fname = (st.source.split(/[\\/]/).pop()) || '影片';
        title.textContent = fname;
    }
    if (ta) {
        ta.value = st.transcript || '';
        updateAlignBindSummary();
    }
    if (modal) modal.classList.remove('hidden');
    // Reset radio
    document.querySelectorAll('input[name="align_bind_src"]').forEach(r => {
        r.checked = (r.value === 'paste');
    });
    window._alignBindSrc = 'paste';
}

export function closeAlignBindModal() {
    const modal = document.getElementById('align_bind_modal');
    if (modal) modal.classList.add('hidden');
    window._alignBindActiveId = null;
}

function detectTranscriptFormat(text) {
    const head = text.slice(0, 500);
    // SRT signature: arrow + numeric index line
    if (head.includes('-->') && /^\s*\d+\s*$/m.test(head)) return 'srt';
    return 'txt';
}

function countSegments(text, fmt) {
    if (!text || !text.trim()) return 0;
    if (fmt === 'srt') {
        // Count "-->" lines
        return (text.match(/-->/g) || []).length;
    }
    // txt: blank lines if any, else single newlines
    const t = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
    if (!t) return 0;
    if (/\n\s*\n/.test(t)) {
        return t.split(/\n\s*\n+/).filter(s => s.trim()).length;
    }
    return t.split('\n').filter(s => s.trim()).length;
}

function updateAlignBindSummary() {
    const ta = document.getElementById('align_bind_text');
    const summary = document.getElementById('align_bind_summary');
    if (!ta || !summary) return;
    const text = ta.value;
    if (!text.trim()) {
        summary.textContent = '尚未輸入';
        summary.className = 'text-gray-500';
        return;
    }
    const fmt = detectTranscriptFormat(text);
    const n = countSegments(text, fmt);
    const fmtLabel = fmt === 'srt' ? '.srt 格式' : (
        /\n\s*\n/.test(text) ? '.txt（空行分段）' : '.txt（單行分段）'
    );
    summary.textContent = `偵測到 ${n} 段 ｜ ${fmtLabel}`;
    summary.className = n > 0 ? 'text-emerald-400' : 'text-yellow-500';
}

export function alignBindUploadClick() {
    window._alignBindSrc = 'upload';
    const input = document.getElementById('align_bind_file_input');
    if (input) input.click();
}

export function confirmAlignBind() {
    const pairId = window._alignBindActiveId;
    if (!pairId) return;
    const st = window._alignPairs.get(pairId);
    if (!st) return;
    const ta = document.getElementById('align_bind_text');
    const text = (ta?.value || '').trim();
    if (!text) {
        alert('文字稿不能為空');
        return;
    }
    const fmt = detectTranscriptFormat(text);
    const n = countSegments(text, fmt);

    st.transcript = text;
    st.format = fmt;
    st.segCount = n;

    _refreshPairBindStatus(pairId);
    closeAlignBindModal();
    updateAlignSubmitBtn();
}

// ─── Submit ─────────────────────────────────────────────────────────────

export async function submitAlignJob() {
    const dest = document.getElementById('align_dest')?.value.trim();
    if (!dest) { alert('請指定輸出資料夾'); return; }
    if (window._alignPairs.size === 0) { alert('請至少新增一個配對'); return; }

    const tasks = [];
    for (const [, st] of window._alignPairs) {
        if (!st.source || !st.transcript) {
            alert('還有配對未完整，無法送出');
            return;
        }
        tasks.push({
            source: st.source,
            transcript: st.transcript,
            transcript_format: st.format || 'auto',
        });
    }

    const payload = {
        task_type: 'align',
        tasks,
        dest_dir: dest,
        model_size: document.getElementById('align_model')?.value || 'turbo',
        language: document.getElementById('align_language')?.value || 'zh',
        subtitle_polish: document.getElementById('align_polish')?.checked !== false,
        anchor_threshold: 0.4,  // backend default; advanced users edit settings.json
    };

    window._activeJobTab = 'transcribe';

    const progArea = document.getElementById('align_progress_area');
    const sumPanel = document.getElementById('align_summary');
    if (sumPanel) sumPanel.classList.add('hidden');
    if (progArea) {
        progArea.classList.remove('hidden');
        document.getElementById('align_prog_label').textContent = '佇列中等待執行...';
        document.getElementById('align_prog_pct').textContent = '0%';
        document.getElementById('align_prog_bar').style.width = '0%';
    }

    const btn = document.getElementById('align_submit_btn');
    const orig = btn?.textContent || '開始對齊';
    if (btn) {
        btn.disabled = true;
        btn.textContent = '🕒 送出中...';
    }

    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/jobs/align', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const result = await res.json();
        if (res.ok) {
            appendLog(`✅ 對齊任務已送進佇列 (${tasks.length} 支影片)`, 'system');
            if (result.warning) appendLog(`⚠️ ${result.warning}`, 'system');
        } else {
            alert(`提交失敗: ${result.detail || JSON.stringify(result)}`);
            if (btn) { btn.disabled = false; btn.textContent = orig; }
        }
    } catch (e) {
        alert(`網路或連線錯誤: ${e.message}`);
        if (btn) { btn.disabled = false; btn.textContent = orig; }
    }
}

// ─── Socket event handlers ──────────────────────────────────────────────

export function onAlignProgress(data) {
    if (!data || data.mode !== 'align') return;
    const area = document.getElementById('align_progress_area');
    if (area) area.classList.remove('hidden');
    const pct = Math.max(0, Math.min(100, Math.round(data.pct || 0)));
    const lbl = document.getElementById('align_prog_label');
    const num = document.getElementById('align_prog_pct');
    const bar = document.getElementById('align_prog_bar');
    if (lbl) lbl.textContent = data.msg || '進行中...';
    if (num) num.textContent = pct + '%';
    if (bar) bar.style.width = pct + '%';
}

function _appendDiv(parent, className, text) {
    const d = document.createElement('div');
    if (className) d.className = className;
    d.textContent = text;
    parent.appendChild(d);
    return d;
}

export function onAlignDone(data) {
    if (!data) return;
    const sumPanel = document.getElementById('align_summary');
    const rows = document.getElementById('align_summary_rows');
    if (!sumPanel || !rows) return;
    rows.replaceChildren();

    const qs = data.qualities || [];
    if (qs.length === 0) {
        _appendDiv(rows, 'text-xs text-gray-500', '完成，但無品質資訊回傳');
    }
    qs.forEach(q => {
        const a = q.alignment || {};
        const ancPct = Math.round((a.anchor_ratio || 0) * 100);
        const intpPct = Math.round((a.interpolated_ratio || 0) * 100);
        const edgPct = Math.round((a.edge_fill_ratio || 0) * 100);
        const polish = q.subtitle_polish || {};
        const lowConf = q.low_confidence_cues || [];
        const fname = (q.video || '').split(/[\\/]/).pop() || '(unknown)';
        const srtName = (q.srt || '').split(/[\\/]/).pop() || '';
        const qualityClass = ancPct >= 60 ? 'text-emerald-400' : (ancPct >= 35 ? 'text-yellow-400' : 'text-red-400');

        const row = document.createElement('div');
        row.className = 'p-2 bg-[#0e0e0e] border border-[#3a3a3a] rounded text-xs';

        // File paths via textContent — user-supplied source paths could contain
        // " or & that would otherwise break out of attribute / element context.
        const head = document.createElement('div');
        head.className = 'flex justify-between items-baseline';
        const titleDiv = document.createElement('div');
        titleDiv.className = 'font-semibold text-gray-200 truncate flex-1';
        titleDiv.textContent = srtName || fname;
        titleDiv.title = fname;
        const metaDiv = document.createElement('div');
        metaDiv.className = 'text-[10px] text-gray-500 ml-2';
        metaDiv.textContent = `${q.subtitle_count || 0} 段 / ${(q.audio_duration || 0).toFixed(1)}s / ${q.fps || 25}fps`;
        head.append(titleDiv, metaDiv);
        row.appendChild(head);

        const stats = document.createElement('div');
        stats.className = `mt-1 flex gap-2 ${qualityClass}`;
        _appendDiv(stats, '', `錨點 ${ancPct}%`);
        _appendDiv(stats, 'text-gray-500', `內插 ${intpPct}%`);
        _appendDiv(stats, 'text-gray-600', `邊緣補 ${edgPct}%`);
        row.appendChild(stats);

        if (polish.gap_inserted || polish.min_duration_extended || polish.frame_snapped) {
            _appendDiv(row, 'mt-1 text-[10px] text-gray-500',
                `時間優化：snap ${polish.frame_snapped || 0} / 延長 ${polish.min_duration_extended || 0} / 補間隔 ${polish.gap_inserted || 0}`);
        }
        if ((polish.reading_speed_warnings || []).length > 0) {
            _appendDiv(row, 'mt-1 text-[10px] text-yellow-500',
                `⚠ ${polish.reading_speed_warnings.length} 段閱讀速度過快`);
        }
        if (lowConf.length > 0) {
            _appendDiv(row, 'mt-1 text-[10px] text-yellow-500',
                `⚠ ${lowConf.length} 段低信心，建議人工複查`);
        }

        rows.appendChild(row);
    });
    sumPanel.classList.remove('hidden');

    const btn = document.getElementById('align_submit_btn');
    if (btn) {
        btn.disabled = false;
        updateAlignSubmitBtn();
    }
    appendLog(`🎯 對齊全部完成：${qs.length} 個 SRT 已輸出`, 'system');
}

// Hook into existing transcribe_progress / transcribe_error / align_done events.
// app.js's main socket.on handlers will dispatch these via window.* if they
// detect mode === 'align'.
// We attach our listeners directly here in case the global socket exists.
function _attachAlignSocketHandlers() {
    if (window.socket && !window._alignSocketBound) {
        window.socket.on('transcribe_progress', (d) => {
            if (d && d.mode === 'align') onAlignProgress(d);
        });
        window.socket.on('align_done', onAlignDone);
        window.socket.on('transcribe_error', (d) => {
            if (d && d.mode === 'align') {
                appendLog(`❌ 對齊失敗: ${d.msg || ''}`, 'error');
                const btn = document.getElementById('align_submit_btn');
                if (btn) { btn.disabled = false; updateAlignSubmitBtn(); }
            }
        });
        window._alignSocketBound = true;
    }
}

window.setAiMode = setAiMode;
window.addAlignPair = addAlignPair;
window.alignBatchImport = alignBatchImport;
window.pickMultiVideosForAlign = pickMultiVideosForAlign;
window.openAlignBindModal = openAlignBindModal;
window.closeAlignBindModal = closeAlignBindModal;
window.alignBindUploadClick = alignBindUploadClick;
window.confirmAlignBind = confirmAlignBind;
window.submitAlignJob = submitAlignJob;
window.onAlignProgress = onAlignProgress;
window.onAlignDone = onAlignDone;

// Wire up textarea live summary + file upload + dest input on init
function _wireAlignTab() {
    const ta = document.getElementById('align_bind_text');
    if (ta) ta.addEventListener('input', updateAlignBindSummary);

    const fileIn = document.getElementById('align_bind_file_input');
    if (fileIn) {
        fileIn.addEventListener('change', async () => {
            const f = fileIn.files?.[0];
            if (!f) return;
            const text = await f.text();
            if (ta) {
                ta.value = text;
                updateAlignBindSummary();
            }
            fileIn.value = '';  // reset so same file can be re-selected
        });
    }

    const dest = document.getElementById('align_dest');
    if (dest) {
        dest.addEventListener('input', updateAlignSubmitBtn);
        setupInputDrop('align_dest');
    }

    // Toolbar buttons — programmatic binding (avoids inline-onclick
    // 'window.X is undefined' silent-fail when module loads after HTML).
    const _bind = (id, fn) => {
        const el = document.getElementById(id);
        if (el && !el.dataset.alignBound) {
            el.addEventListener('click', fn);
            el.dataset.alignBound = '1';
        }
        return !!el;
    };
    const wired = {
        batch:    _bind('btn_align_batch_import', alignBatchImport),
        multi:    _bind('btn_align_multi_pick',    pickMultiVideosForAlign),
        add:      _bind('btn_align_add',           () => addAlignPair()),
        destPick: _bind('btn_align_dest_pick',     async () => {
            const p = await _pickFolderHybrid('選擇輸出資料夾');
            if (p) {
                const el = document.getElementById('align_dest');
                if (el) {
                    el.value = p;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }
        }),
    };
    console.info('[align] tab wired:', wired);

    // Socket may not be ready when init runs; retry up to 10s then give up.
    _attachAlignSocketHandlers();
    if (!window._alignSocketBound) {
        let retries = 0;
        const t = setInterval(() => {
            _attachAlignSocketHandlers();
            if (window._alignSocketBound || ++retries > 20) clearInterval(t);
        }, 500);
    }
}

