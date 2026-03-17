// verify.js
import { getComputeBaseUrl, appendLog, pickPath, setupDragAndDrop } from '../../js/shared/utils.js';

let vfIndex = 0;

export function addVerifySourceRow(defaultPath1 = '', defaultPath2 = '') {
    vfIndex++;
    const container = document.getElementById('vf_source_list');
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    row.id = `vf_row_${vfIndex}`;
    row.innerHTML = `
        <input type="text" id="vf_src_${vfIndex}" class="flex-1 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath1}" placeholder="被選擇的來源 (卡匣)">
        <button type="button" class="btn-pick-folder bg-[#333] hover:bg-[#444] px-2 rounded text-sm border border-[#555] text-gray-300 transition" data-target="vf_src_${vfIndex}">📁</button>
        <span class="text-gray-500 text-xs">➡</span>
        <input type="text" id="vf_dst_${vfIndex}" class="flex-1 bg-[#1e1e1e] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath2}" placeholder="NAS 或代理目標目錄...">
        <button type="button" class="btn-pick-folder bg-[#333] hover:bg-[#444] px-2 rounded text-sm border border-[#555] text-gray-300 transition" data-target="vf_dst_${vfIndex}">📁</button>
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded" data-target="vf_row_${vfIndex}">X</button>
    `;
    container.appendChild(row);

    // Bind events
    row.querySelectorAll('.btn-pick-folder').forEach(btn => {
        btn.addEventListener('click', function() {
            pickPath(this.dataset.target, 'folder');
        });
    });
    
    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        document.getElementById(this.dataset.target).remove();
    });

    return `vf_src_${vfIndex}`;
}

export async function submitVerify() {
    const rows = document.getElementById('vf_source_list').children;
    const pairs = [];
    for (let row of rows) {
        const inputs = row.querySelectorAll('input');
        if (inputs[0].value.trim() && inputs[1].value.trim()) {
            pairs.push([inputs[0].value.trim(), inputs[1].value.trim()]);
        }
    }
    if (pairs.length === 0) {
        alert('至少需要一組完整的比對路徑！');
        return;
    }

    const vfMode = document.querySelector('input[name="vf_mode"]:checked').value;
    const payload = {
        pairs: pairs,
        mode: vfMode
    };

    try {
        window._lastJob = { url: 'http://localhost:8000/api/v1/jobs/verify', payload };
        const res = await fetch(window._lastJob.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        const retryBtn = document.getElementById('btn_retry');
        if (retryBtn) retryBtn.style.display = 'none';
        appendLog(`比對請求已送出，模式：${vfMode === 'quick' ? '快速' : 'XXH64 進階'}，任務 ID: ${result.job_id || '?'}`, 'system');
    } catch (e) { 
        appendLog('發送失敗: ' + e, 'error'); 
    }
}

export function initVerifyTab() {
    addVerifySourceRow();
    setupDragAndDrop('vf_source_list', addVerifySourceRow);
}

window.addVerifySourceRow = addVerifySourceRow;
window.submitVerify = submitVerify;
