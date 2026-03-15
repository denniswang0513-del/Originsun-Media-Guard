import { appendLog, getComputeBaseUrl, getAgentBaseUrl, addStandaloneSource, setupDragAndDrop, setupInputDrop } from '../../js/shared/utils.js';

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
        const res = await fetch(getAgentBaseUrl() + '/api/v1/utils/pick?type=file');
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

export async function submitTranscribeJob() {
    const rows = document.getElementById('transcribe_file_list').children;
    const sources = Array.from(rows).map(row => row.querySelector('input')?.value.trim()).filter(v => v);
    
    if (!sources.length) {
        alert('請先加入影音檔案來源！');
        return;
    }
    const destDir = document.getElementById('transcribe_dest')?.value.trim();
    if (!destDir) {
        alert('請指定輸出目標資料夾！');
        return;
    }

    // Show progress area
    const progArea = document.getElementById('transcribe_progress_area');
    if (progArea) {
        progArea.classList.remove('hidden');
        document.getElementById('transcribe_prog_label').textContent = '佇列中等待執行...';
        document.getElementById('transcribe_prog_pct').textContent = '0%';
        document.getElementById('transcribe_prog_bar').style.width = '0%';
    }

    const modelSize = document.getElementById('transcribe_model')?.value || 'turbo';
    const outputSrt = document.getElementById('transcribe_srt')?.checked || false;
    const outputTxt = document.getElementById('transcribe_txt')?.checked || false;
    const outputWav = document.getElementById('transcribe_wav')?.checked || false;
    const generateProxy = document.getElementById('transcribe_proxy')?.checked || false;

    if (!outputSrt && !outputTxt) {
        alert('請至少勾選一種輸出格式 (.srt 或 .txt)！');
        return;
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

    const btn = document.querySelector('#tab_transcribe button[onclick="submitTranscribeJob()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = '🕒 送出中...';
    btn.disabled = true;
    btn.classList.add('opacity-70', 'cursor-not-allowed');

    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/jobs/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            appendLog('✅ 已成功將逐字稿任務送入佇列。', 'system');
            // Show progress area
            const progArea = document.getElementById('transcribe_progress_area');
            if (progArea) progArea.classList.remove('hidden');
            
            document.getElementById('transcribe_prog_label').textContent = '佇列中等待執行...';
            document.getElementById('transcribe_prog_pct').textContent = '0%';
            document.getElementById('transcribe_prog_bar').style.width = '0%';
        } else {
            const text = await res.text();
            alert(`提交失敗: ${text}`);
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
}

window.fetchModelStatus = fetchModelStatus;
window.pickMultiFiles = pickMultiFiles;
window.updateModelStatus = updateModelStatus;
window.downloadSelectedModel = downloadSelectedModel;
window.setTranscribeMode = setTranscribeMode;
window.submitTranscribeJob = submitTranscribeJob;
