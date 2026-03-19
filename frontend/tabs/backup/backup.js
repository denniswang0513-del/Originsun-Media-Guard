import { getComputeBaseUrl, appendLog, resetProgress, resolveDropPath, pickPath, setupInputDrop, setupDragAndDrop } from '../../js/shared/utils.js';

let sourceIndex = 0;

export function addSourceRow(defaultName = '', defaultPath = '') {
    sourceIndex++;
    const name = defaultName || `Card_${String.fromCharCode(64 + sourceIndex)}`;
    const container = document.getElementById('source_list');
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    row.id = `src_row_${sourceIndex}`;
    row.innerHTML = `
        <input type="text" class="w-1/4 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${name}" placeholder="卡匣名稱">
        <input type="text" id="src_path_${sourceIndex}" class="flex-1 bg-[#1e1e1e] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath}" placeholder="伺服器端來源絕對路徑...">
        <button type="button" class="btn-pick-folder bg-[#333] hover:bg-[#444] px-2 rounded text-sm border border-[#555] text-gray-300 transition" data-target="src_path_${sourceIndex}">📁</button>
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded" data-target="src_row_${sourceIndex}">X</button>
    `;
    container.appendChild(row);

    // Bind events for the newly created row
    row.querySelector('.btn-pick-folder').addEventListener('click', function() {
        pickPath(this.dataset.target, 'folder');
    });
    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        document.getElementById(this.dataset.target).remove();
    });

    return `src_path_${sourceIndex}`;
}

export function setTodayName() {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    document.getElementById('proj_name').value = `${yyyy}${mm}${dd}`;
}

function getSelectedHosts() {
    // This function originally lived in app.js and relied on window._computeHosts.
    // For now, we will query the DOM to find selected hosts if we are in Backup tab.
    const result = [];
    const localChk = document.getElementById('host_chk_local');
    if (localChk && localChk.checked) result.push({ name: '本機', ip: 'local' });
    
    // In a real refactor, window._computeHosts should be managed by a central store,
    // but we can query checkboxes directly if they are rendered.
    const cbxDiv = document.getElementById('host_selector_checkboxes');
    if (cbxDiv) {
        const checkboxes = cbxDiv.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach((chk, i) => {
            if (chk.id !== 'host_chk_local' && chk.checked) {
                // Temporary hack based on the text content. We will need a better way to store host IPs.
                // Assuming we stored ip in dataset when rendering. (Requires updating renderHostSelector)
                const ip = chk.dataset.ip;
                const name = chk.dataset.name;
                if(ip && name) result.push({name, ip});
            }
        });
    }

    if (!result.length) result.push({ name: '本機', ip: 'local' });
    return result;
}


let _submitting = false;

export async function submitJob() {
    if (_submitting) return;
    _submitting = true;

    const submitBtn = document.querySelector('#tab_backup button[onclick="submitJob()"]');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add('opacity-70', 'cursor-not-allowed');
        submitBtn._origText = submitBtn.textContent;
        submitBtn.textContent = '提交中...';
    }

    try {
    // Reset global progress
    resetProgress();
    window._isStandaloneTranscode = false;
    window._remoteDispatchExpectedRetryCount = 0;

    const srcRows = document.getElementById('source_list').children;
    const cards = [];
    for (let row of srcRows) {
        const inputs = row.querySelectorAll('input');
        if (inputs[0].value.trim() && inputs[1].value.trim()) {
            cards.push([inputs[0].value.trim(), inputs[1].value.trim()]);
        }
    }
    if (cards.length === 0) {
        alert('至少需要一個有效的來源路徑！');
        return;
    }

    const chkReport = document.getElementById('chk_report');
    const doReport = chkReport ? chkReport.checked : false;

    // Show / hide report progress segment based on selection
    const segReport = document.getElementById('seg_report');
    const legendReport = document.getElementById('legend_report');
    if (segReport) segReport.classList.toggle('hidden', !doReport);
    if (legendReport) legendReport.classList.toggle('hidden', !doReport);

    const payload = {
        project_name: document.getElementById('proj_name').value.trim(),
        local_root: document.getElementById('local_root').value.trim(),
        nas_root: document.getElementById('nas_root').value.trim(),
        proxy_root: document.getElementById('proxy_root').value.trim(),
        cards: cards,
        do_hash: document.getElementById('chk_hash').checked,
        do_transcode: document.getElementById('chk_transcode').checked,
        do_concat: document.getElementById('chk_concat').checked,
        do_report: doReport,
        // Concat settings
        concat_resolution: document.getElementById('bk_cc_res')?.value || '720P',
        concat_codec: document.getElementById('bk_cc_codec')?.value || 'H.264 (NVENC)',
        concat_burn_tc: document.getElementById('bk_cc_burn_tc')?.checked ?? true,
        concat_burn_fn: document.getElementById('bk_cc_burn_fn')?.checked ?? false,
        // Report settings
        report_name: document.getElementById('bk_rpt_name')?.value.trim() || '',
        report_output: document.getElementById('bk_rpt_output')?.value.trim() || '',
        report_filmstrip: document.getElementById('bk_rpt_filmstrip')?.checked ?? true,
        report_techspec: document.getElementById('bk_rpt_techspec')?.checked ?? true,
        report_hash: document.getElementById('bk_rpt_hash')?.checked ?? false,
    };

    const _selH = getSelectedHosts();
    const _hasRemote = _selH.some(h => h.ip !== 'local');
    if (_hasRemote) {
        const dispatchHosts = _selH.map(h => (h.ip === 'local') ? { name: h.name, ip: window.location.host } : h);
        window._remoteDispatch = { hosts: dispatchHosts, proxy_root: payload.proxy_root, project_name: payload.project_name, local_root: payload.local_root, cards: payload.cards };
        window._postMergeFlags = {
            do_concat: payload.do_concat,
            do_report: payload.do_report,
            project_name: payload.project_name,
            local_root: payload.local_root,
            nas_root: payload.nas_root,
            proxy_root: payload.proxy_root,
            cards: payload.cards,
            do_hash: false,
            do_transcode: false,
            concat_host_url: (() => {
                const localHost = _selH.find(h => h.ip === 'local');
                if (localHost) return getComputeBaseUrl();
                const firstRemote = _selH.find(h => h.ip !== 'local');
                return firstRemote ? 'http://' + firstRemote.ip : getComputeBaseUrl();
            })(),
            concat_host_name: (() => {
                const localHost = _selH.find(h => h.ip === 'local');
                if (localHost) return localHost.name || '本機';
                const firstRemote = _selH.find(h => h.ip !== 'local');
                return firstRemote ? firstRemote.name : '本機';
            })(),
            // 串帶進階設定
            concat_resolution: document.getElementById('bk_cc_res')?.value || '720P',
            concat_codec: document.getElementById('bk_cc_codec')?.value || 'H.264 (NVENC)',
            concat_burn_tc: document.getElementById('bk_cc_burn_tc')?.checked ?? true,
            concat_burn_fn: document.getElementById('bk_cc_burn_fn')?.checked ?? false,
            // 報表進階設定
            report_name: document.getElementById('bk_rpt_name')?.value.trim() || '',
            report_output: document.getElementById('bk_rpt_output')?.value.trim() || '',
            report_filmstrip: document.getElementById('bk_rpt_filmstrip')?.checked ?? true,
            report_techspec: document.getElementById('bk_rpt_techspec')?.checked ?? true,
            report_hash: document.getElementById('bk_rpt_hash')?.checked ?? false,
        };
        payload.do_transcode = false;
        payload.do_concat = false;
        payload.do_report = false;
    } else {
        window._remoteDispatch = null;
        window._postMergeFlags = null;
    }

    try {
        window._lastJob = { url: getComputeBaseUrl() + '/api/v1/jobs', payload };
        const res = await fetch(window._lastJob.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (!res.ok) {
            appendLog(`任務提交失敗: ${result.detail || result.message || JSON.stringify(result)}`, 'error');
            alert(result.detail || '任務提交失敗');
            return;
        }
        const btnRetry = document.getElementById('btn_retry');
        if (btnRetry) btnRetry.style.display = 'none';
        appendLog(`請求已送出，伺服器排序狀態: ${result.status}, 任務 ID: ${result.job_id || '?'}`, 'system');
        if (result.warning) {
            appendLog(`⚠️ ${result.warning}`, 'system');
        }
    } catch (err) {
        appendLog(`請求發送失敗: ${err.message}`, 'error');
    }

    } finally {
        _submitting = false;
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('opacity-70', 'cursor-not-allowed');
            submitBtn.textContent = submitBtn._origText || '開始備份';
        }
    }
}

function toggleConcatOptions() {
    const panel = document.getElementById('concat_options_panel');
    const checked = document.getElementById('chk_concat')?.checked;
    if (panel) panel.classList.toggle('hidden', !checked);
}

function toggleReportOptions() {
    const panel = document.getElementById('report_options_panel');
    const checked = document.getElementById('chk_report')?.checked;
    if (panel) panel.classList.toggle('hidden', !checked);
}

// Ensure functions are added to window object so inline event handlers in index.html still work during refactor,
// or we attach them dynamically. Since we want to decouple, we attach them.
export function initBackupTab() {
    // Adding initial card
    addSourceRow('Card_A', 'C:/A_TEST_SRC');

    // Bind drag and drop for source list
    setupDragAndDrop('source_list', addSourceRow);
    setupInputDrop('local_root');
    setupInputDrop('nas_root');
    setupInputDrop('proxy_root');
    setupInputDrop('bk_rpt_output');

    // Sync initial visibility of concat/report options panels
    toggleConcatOptions();
    toggleReportOptions();
}


// Bind to window for inline onclick execution (temporary during migration)
window.addSourceRow = addSourceRow;
window.setTodayName = setTodayName;
window.submitJob = submitJob;
window.toggleConcatOptions = toggleConcatOptions;
window.toggleReportOptions = toggleReportOptions;
