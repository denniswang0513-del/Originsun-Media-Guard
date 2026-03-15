import { appendLog, getComputeBaseUrl, addStandaloneSource, setupDragAndDrop, setupInputDrop } from '../../js/shared/utils.js';

export async function submitConcat() {
    const rows = document.getElementById('cc_source_list').children;
    const sources = Array.from(rows).map(row => row.querySelector('input').value.trim()).filter(v => v);
    if (!sources.length) { alert('需要提供來源！'); return; }

    const ccHostSel = document.getElementById('cc_host_select');
    const selectedOpt = ccHostSel ? ccHostSel.options[ccHostSel.selectedIndex] : null;
    const isLocal = !selectedOpt || selectedOpt.value === 'local';
    const ccHostUrl = isLocal ? 'http://localhost:8000' : 'http://' + selectedOpt.value;
    const ccIp = selectedOpt ? selectedOpt.value : 'local';
    const ccHostName = selectedOpt ? selectedOpt.text : '本機';
    const ccHostObj = { ip: ccIp, name: ccHostName };

    const payload = {
        sources,
        dest_dir: document.getElementById('cc_dest').value.trim(),
        resolution: document.getElementById('cc_res').value,
        custom_name: document.getElementById('cc_name').value.trim(),
        codec: document.getElementById('cc_codec')?.value || 'ProRes',
        burn_timecode: document.getElementById('cc_burn_timecode')?.checked !== false,
        burn_filename: document.getElementById('cc_burn_filename')?.checked === true,
    };

    if (!isLocal) {
        window._remoteJobType = 'concat';
        window._activeRemoteHosts = {};
        if(window.initRemoteHostProgress) window.initRemoteHostProgress([ccHostObj]);
    }
    try {
        window._lastJob = { url: ccHostUrl + '/api/v1/jobs/concat', payload };
        const res = await fetch(window._lastJob.url, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        const retryBtn = document.getElementById('btn_retry');
        if(retryBtn) retryBtn.style.display = 'none';
        
        appendLog(`串帶請求已送出至 [${ccHostName}]，排序號: ${result.position}`, 'system');
        
        if (!isLocal) {
            if(window.updateHostProgress) window.updateHostProgress(ccIp, 20, '已排程，串帶中...', '#228b22');
            window._activeRemoteHosts[ccIp] = {
                host: ccHostObj, files: sources,
                lastSeen: Date.now(), startTime: Date.now(), logOffset: 0
            };
            if(window.startHeartbeatMonitor) window.startHeartbeatMonitor();
        }
    } catch (e) { 
        appendLog('發送失敗: ' + e.message, 'error'); 
    }
}

export function initConcatTab() {
    addStandaloneSource('cc_source_list', '');
    setupDragAndDrop('cc_source_list', () => addStandaloneSource('cc_source_list', ''));
    setupInputDrop('cc_dest');
}

window.submitConcat = submitConcat;
