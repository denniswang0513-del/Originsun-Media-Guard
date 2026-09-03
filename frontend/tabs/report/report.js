import { appendLog, getComputeBaseUrl, setupInputDrop, resetProgress, todayStamp } from '../../js/shared/utils.js';
import { loadReportHistory } from '../../js/shared/report-history.js';

function rptLog(msg, type = 'info') {
    // Route all report logs into the shared '當前進度追蹤' log panels
    appendLog(msg, type);
}

export async function submitReportJob() {
    window._activeJobTab = 'report';
    const src = document.getElementById('rpt_source')?.value.trim();
    const outDir = document.getElementById('rpt_output')?.value.trim();
    if (!src) { alert('請選擇辨源資料夾！'); return; }
    if (!outDir) { alert('請選擇報表輸出目錄！'); return; }

    // Reset all progress bars then show report bar
    resetProgress();
    document.getElementById('rp-progress')?.classList.remove('hidden');

    const reportName = document.getElementById('rpt_report_name')?.value.trim() || todayStamp('_Report');

    const payload = {
        source_dir: src,
        output_dir: outDir,
        nas_root: document.getElementById('rpt_nas_root')?.value.trim() || '',
        report_name: reportName,
        do_filmstrip: document.getElementById('rpt_filmstrip')?.checked ?? true,
        do_techspec: document.getElementById('rpt_techspec')?.checked ?? true,
        do_hash: document.getElementById('rpt_hash')?.checked ?? false,
        do_gdrive: false,   // UI 從未有過這個選項（#rpt_gdrive 不存在），明寫關閉
        do_gchat: false,
        client_sid: window.socket?.id || '',
    };

    // 讀取處理主機
    const rptHostObj = window.collectSelectedHost ? window.collectSelectedHost('rpt_host_checkboxes') : { name: '本機', ip: 'local' };
    const isLocal = rptHostObj.ip === 'local';
    const rptHostUrl = isLocal ? getComputeBaseUrl() : 'http://' + rptHostObj.ip;

    if (!isLocal && window.initRemoteHostProgress) {
        window._remoteJobType = 'report';
        window._activeRemoteHosts = {};
        if (window.showRemoteMainProgress) window.showRemoteMainProgress('遠端報表生成中...');
        window.initRemoteHostProgress([rptHostObj]);
    }

    rptLog('正在送出報表工作請求...', 'system');
    try {
        const res = await fetch(rptHostUrl + '/api/v1/report_jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.status === 'queued') {
            if (result.job_id) {
                window._myReportJobIds = window._myReportJobIds || new Set();
                window._myReportJobIds.add(result.job_id);
            }
            rptLog(`工作已排隊至 [${rptHostObj.name}]—— Socket.IO 將持續回報進度`, 'ok');
            if (!isLocal) {
                if (window.updateHostProgress) window.updateHostProgress(rptHostObj.ip, 20, '已排程，報表生成中...', '#7c3aed');
                window._activeRemoteHosts[rptHostObj.ip] = { host: rptHostObj, lastSeen: Date.now(), startTime: Date.now(), logOffset: 0 };
                if (window.startHeartbeatMonitor) window.startHeartbeatMonitor();
            }
        } else {
            rptLog(`錯誤： ${JSON.stringify(result)}`, 'error');
        }
    } catch (err) {
        rptLog(`連綫失敗: ${err.message}`, 'error');
    }
}

export function initReportTab() {
    setupInputDrop('rpt_source');
    setupInputDrop('rpt_output');
    setupInputDrop('rpt_nas_root');
    // 報表名稱預設今天（原本在 app.js 開機段——分頁點到才載後，這頁的 DOM 開機時不存在）
    const nameEl = document.getElementById('rpt_report_name');
    if (nameEl && !nameEl.value) nameEl.value = todayStamp('_Report');
    loadReportHistory();
    // 切到這頁：輸出目錄空著就帶備份頁的專案素材區；切回來重抓歷史（剛載入的 init 抓過了）
    document.addEventListener('tab-changed', (e) => {
        if (e.detail?.tab !== 'tab_report') return;
        const rptOut = document.getElementById('rpt_output');
        const localRoot = document.getElementById('local_root');
        if (rptOut && localRoot && !rptOut.value.trim() && localRoot.value.trim()) rptOut.value = localRoot.value.trim();
        if (!e.detail.fresh) loadReportHistory();
    });
}

// Make accessible to global scope
function collectReportPayload() {
    const src = document.getElementById('rpt_source')?.value.trim();
    const outDir = document.getElementById('rpt_output')?.value.trim();
    if (!src) { alert('請選擇來源資料夾！'); return { valid: false }; }
    if (!outDir) { alert('請選擇報表輸出目錄！'); return { valid: false }; }
    const reportName = document.getElementById('rpt_report_name')?.value.trim() || todayStamp('_Report');
    return {
        valid: true,
        name: reportName,
        payload: {
            source_dir: src,
            output_dir: outDir,
            nas_root: document.getElementById('rpt_nas_root')?.value.trim() || '',
            report_name: reportName,
            do_filmstrip: document.getElementById('rpt_filmstrip')?.checked ?? true,
            do_techspec: document.getElementById('rpt_techspec')?.checked ?? true,
            do_hash: document.getElementById('rpt_hash')?.checked ?? false,
        },
    };
}

window.collectReportPayload = collectReportPayload;
window.submitReportJob = submitReportJob;
window.rptLog = rptLog;
