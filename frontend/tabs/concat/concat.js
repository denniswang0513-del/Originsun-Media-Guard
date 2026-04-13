import { appendLog, getComputeBaseUrl, addStandaloneSource, setupDragAndDrop, setupInputDrop, validateRemotePaths, toUncPath, ensureDriveMap } from '../../js/shared/utils.js';
import { refreshConcatEditorStatus } from './concat_editor_modal.js';

export function collectConcatPayload() {
    const rows = document.getElementById('cc_source_list').children;
    let sources = Array.from(rows).map(row => row.querySelector('input').value.trim()).filter(v => v);

    // If advanced clips are set, derive sources from them (for compatibility with backend path validation)
    const advancedClips = window._concatAdvancedClips || null;
    const advancedSelected = advancedClips ? advancedClips.filter(c => c.selected) : null;

    if (advancedSelected && advancedSelected.length) {
        // Use clip paths as sources (ordered)
        sources = advancedSelected.map(c => c.path);
    } else if (!sources.length) {
        alert('需要提供來源！');
        return { valid: false };
    }

    const destDir = document.getElementById('cc_dest').value.trim();
    if (!destDir) {
        alert('請設定目標輸出資料夾！');
        return { valid: false };
    }
    const payload = {
        sources,
        dest_dir: destDir,
        resolution: document.getElementById('cc_res').value,
        custom_name: document.getElementById('cc_name').value.trim(),
        codec: document.getElementById('cc_codec')?.value || 'ProRes',
        burn_timecode: document.getElementById('cc_burn_timecode')?.checked === true,
        burn_filename: document.getElementById('cc_burn_filename')?.checked === true,
    };
    if (advancedSelected && advancedSelected.length) {
        payload.advanced_clips = advancedSelected.map(c => ({
            path: c.path,
            trim_in: c.trim_in || 0,
            trim_out: c.trim_out || -1,
            brightness: c.brightness || 0,
            contrast: c.contrast || 1,
            saturation: c.saturation || 1,
            gamma: c.gamma || 1,
            color_temp: c.color_temp || 0,
        }));
    }
    return { valid: true, payload, name: '串帶' };
}
window.collectConcatPayload = collectConcatPayload;

let _ccSubmitting = false;

export async function submitConcat() {
    if (_ccSubmitting) return;
    _ccSubmitting = true;
    window._activeJobTab = 'concat';

    const submitBtn = document.querySelector('#tab_concat button[onclick="submitConcat()"]');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add('opacity-70', 'cursor-not-allowed');
        submitBtn._origText = submitBtn.textContent;
        submitBtn.textContent = '提交中...';
    }

    try {

    const collected = collectConcatPayload();
    if (!collected.valid) return;
    const payload = collected.payload;

    const ccHostObj = window.collectSelectedHost ? window.collectSelectedHost('cc_host_checkboxes') : { name: '本機', ip: 'local' };
    const isLocal = ccHostObj.ip === 'local';
    const ccHostUrl = isLocal ? getComputeBaseUrl() : 'http://' + ccHostObj.ip;
    const ccHostName = ccHostObj.name || ccHostObj.ip;
    const ccIp = isLocal ? 'local' : ccHostObj.ip;

    if (!isLocal) {
        // 載入 UNC 映射並轉換路徑
        await ensureDriveMap();
        payload.sources = payload.sources.map(toUncPath);
        payload.dest_dir = toUncPath(payload.dest_dir);

        // 驗證遠端主機路徑（用轉換後的路徑）
        const pathsToCheck = [payload.dest_dir, ...payload.sources];
        try {
            const result = await validateRemotePaths(ccHostObj.ip, pathsToCheck);
            if (!result.ok) {
                alert(`⚠️ 遠端主機 [${ccHostObj.name}] 路徑檢查失敗：\n\n${result.errors.join('\n')}\n\n請確認該主機是否已映射對應磁碟機。`);
                return;
            }
        } catch (e) {
            alert(`⚠️ 無法連線至遠端主機 [${ccHostObj.name}] 進行路徑驗證：${e.message}`);
            return;
        }

        window._remoteJobType = 'concat';
        window._activeRemoteHosts = {};
        if (window.showRemoteMainProgress) window.showRemoteMainProgress('遠端串帶中...');
        if (window.initRemoteHostProgress) window.initRemoteHostProgress([ccHostObj]);
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

        appendLog(`串帶請求已送出至 [${ccHostName}]，任務 ID: ${result.job_id || '?'}`, 'system');
        if (result.warning) appendLog(`⚠️ ${result.warning}`, 'system');

        if (!isLocal) {
            if(window.updateHostProgress) window.updateHostProgress(ccIp, 20, '已排程，串帶中...', '#228b22');
            window._activeRemoteHosts[ccIp] = {
                host: ccHostObj, files: payload.sources,
                lastSeen: Date.now(), startTime: Date.now(), logOffset: 0
            };
            if(window.startHeartbeatMonitor) window.startHeartbeatMonitor();
        }
    } catch (e) {
        appendLog('發送失敗: ' + e.message, 'error');
    }

    } finally {
        _ccSubmitting = false;
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('opacity-70', 'cursor-not-allowed');
            submitBtn.textContent = submitBtn._origText || '開始串帶';
        }
    }
}

export function initConcatTab() {
    addStandaloneSource('cc_source_list', '');
    setupDragAndDrop('cc_source_list', () => addStandaloneSource('cc_source_list', ''));
    setupInputDrop('cc_dest');
    refreshConcatEditorStatus();
}

window.submitConcat = submitConcat;
