import { appendLog, getComputeBaseUrl, setupInputDrop } from '../../js/shared/utils.js';

function rptLog(msg, type = 'info') {
    // Route all report logs into the shared '當前進度追蹤' log panels
    appendLog(msg, type);
}

export async function submitReportJob() {
    const src = document.getElementById('rpt_source')?.value.trim();
    const outDir = document.getElementById('rpt_output')?.value.trim();
    if (!src) { alert('請選擇辨源資料夾！'); return; }
    if (!outDir) { alert('請選擇報表輸出目錄！'); return; }

    // Switch progress area to report mode and scroll to tracker
    document.getElementById('progress_backup_mode')?.classList.add('hidden');
    document.getElementById('progress_report_mode')?.classList.remove('hidden');
    // Reset report progress bars
    ['rpt_seg_scan', 'rpt_seg_meta', 'rpt_seg_strip', 'rpt_seg_render'].forEach(id => { const el = document.getElementById(id); if (el) el.style.width = '0%'; });
    ['rpt_lbl_scan', 'rpt_lbl_meta', 'rpt_lbl_strip', 'rpt_lbl_render'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '0%'; });
    // Scroll to 當前進度追蹤 section
    document.getElementById('terminal')?.scrollIntoView({ behavior: 'smooth', block: 'start' });

    const today = new Date();
    const defaultName = today.getFullYear().toString() +
        String(today.getMonth() + 1).padStart(2, '0') +
        String(today.getDate()).padStart(2, '0') + '_Report';
    const reportName = document.getElementById('rpt_report_name')?.value.trim() || defaultName;

    const payload = {
        source_dir: src,
        output_dir: outDir,
        nas_root: document.getElementById('rpt_nas_root')?.value.trim() || '',
        report_name: reportName,
        do_filmstrip: document.getElementById('rpt_filmstrip')?.checked ?? true,
        do_techspec: document.getElementById('rpt_techspec')?.checked ?? true,
        do_hash: document.getElementById('rpt_hash')?.checked ?? false,
        do_gdrive: document.getElementById('rpt_gdrive')?.checked ?? false,
        do_gchat: false,
        do_line: false,
    };

    rptLog('正在送出報表工作請求...', 'system');
    try {
        const res = await fetch(getComputeBaseUrl() + '/api/v1/report_jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.status === 'queued') {
            rptLog(`工作已排隊—— Socket.IO 將持續回報進度`, 'ok');
        } else {
            rptLog(`錯誤： ${JSON.stringify(result)}`, 'error');
        }
    } catch (err) {
        rptLog(`連綫失敗: ${err.message}`, 'error');
    }
}

export async function loadReportHistory() {
    const listEls = [
        document.getElementById('rpt_history_list'),
        document.getElementById('main_history_list')
    ].filter(el => el !== null);
    
    if (listEls.length === 0) return;
    
    listEls.forEach(el => {
        el.innerHTML = '<div class="px-4 py-6 text-center text-xs text-gray-500">載入中...</div>';
    });
    
    // outDir is ignored by server in 1.0.143+, but keep it for API compatibility
    const outDir = document.getElementById('rpt_output')?.value.trim() || '';
    try {
        const url = getComputeBaseUrl() + '/api/v1/reports/history?output_dir=' + encodeURIComponent(outDir);
        const res = await fetch(url);
        const data = await res.json();
        const reports = data.reports || [];
        
        if (reports.length === 0) {
            const emptyMsg = '<div class="px-4 py-8 text-center text-xs text-gray-500 flex items-center justify-center h-full">NAS 尚無歷史報表紀錄</div>';
            listEls.forEach(el => el.innerHTML = emptyMsg);
            return;
        }
        
        const htmlStr = reports.map(r => `
            <div class="flex items-center justify-between px-4 py-2.5 hover:bg-[#2a2a2a] transition-colors border-b border-[#2a2a2a] last:border-0">
                <div class="flex-1 min-w-0 mr-3">
                    <div class="text-sm font-medium text-gray-200 truncate">${r.name}</div>
                    <div class="text-xs text-gray-500 mt-0.5">${r.created_at} &nbsp;·&nbsp; ${r.file_count} 個檔案 &nbsp;·&nbsp; ${r.total_size_str}</div>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <button onclick="openReportFolder('${r.local_path.replace(/\\/g, '\\\\')}')" class="text-xs border border-orange-700 bg-orange-900/40 hover:bg-orange-800 text-orange-300 px-2 py-1 rounded transition-colors whitespace-nowrap">📁 開啟資料夾</button>
                    ${r.public_url ? `<button onclick="copyPublicUrl('${r.public_url}', this)" class="text-xs border border-[#0d9488] bg-[#0f766e]/30 hover:bg-[#0f766e] text-teal-200 px-2 py-1 rounded transition-colors whitespace-nowrap">🌐 複製公開網址</button>` : ''}
                    <button onclick="openReportFile('${r.local_path.replace(/\\/g, '\\\\')}', '')"
                        class="text-xs bg-[#5b21b6] hover:bg-[#7c3aed] text-white px-2 py-1 rounded transition-colors">📄 HTML</button>
                    ${r.pdf_path ? `<button onclick="openReportPdf('${r.pdf_path.replace(/\\/g, '\\\\')}')" class="text-xs bg-[#9333ea] hover:bg-[#a855f7] text-white px-2 py-1 rounded transition-colors">📄 PDF</button>` : ''}
                    ${r.drive_url ? `<a href="${r.drive_url}" target="_blank" class="text-xs bg-[#1f538d] hover:bg-[#2a6cbf] text-white px-2 py-1 rounded transition-colors whitespace-nowrap">☁️ Drive</a>` : ''}
                    <button onclick="deleteReport('${r.id}')"
                        class="text-xs bg-[#500] hover:bg-[#800] text-red-300 px-2 py-1 rounded transition-colors">✕</button>
                </div>
            </div>
        `).join('');
        
        listEls.forEach(el => el.innerHTML = htmlStr);
    } catch (err) {
        const errMsg = `<div class="px-4 py-6 text-center text-xs text-red-400">載入失敗: ${err.message}</div>`;
        listEls.forEach(el => el.innerHTML = errMsg);
    }
}

export async function deleteReport(reportId) {
    if (!confirm('確定要刪除這筆報表記錄嗎？（將從索引移除，本機檔案不复刪除）')) return;
    try {
        await fetch(getComputeBaseUrl() + '/api/v1/reports/' + reportId, { method: 'DELETE' });
        loadReportHistory();
    } catch (err) {
        alert('刪除失敗: ' + err.message);
    }
}

export async function openReportFile(localPath, driveUrl) {
    if (driveUrl) { window.open(driveUrl, '_blank'); return; }
    if (localPath) {
        try {
            await fetch(getComputeBaseUrl() + '/api/v1/utils/open_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: localPath })
            });
        } catch (e) { console.warn(e); }
    }
}

export async function openReportFolder(localPath) {
    if (!localPath) return;
    try {
        await fetch(getComputeBaseUrl() + '/api/v1/utils/open_folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: localPath })
        });
    } catch (e) { console.warn(e); }
}

export async function openReportPdf(localPdfPath) {
    if (!localPdfPath) return;
    try {
        await fetch(getComputeBaseUrl() + '/api/v1/utils/open_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: localPdfPath })
        });
    } catch (e) { console.warn(e); }
}

// Opens the local report file in the OS browser via the server
export async function openReport() {
    const btn = document.getElementById('btn_open_report');
    const localPath = btn ? btn.dataset.localPath : '';
    const driveUrl = btn ? btn.dataset.driveUrl : '';
    if (driveUrl) {
        window.open(driveUrl, '_blank');
        return;
    }
    if (localPath) {
        try {
            await fetch(getComputeBaseUrl() + '/api/v1/utils/open_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: localPath })
            });
        } catch (e) {
            console.error("Failed to open report file:", e);
        }
    }
}

export function copyPublicUrl(url, btnElement) {
    if (!url) return;
    navigator.clipboard.writeText(url).then(() => {
        const oldHTML = btnElement.innerHTML;
        const oldClass = btnElement.className;
        
        btnElement.innerHTML = '✅ 已複製！';
        btnElement.className = 'text-xs border border-green-500 bg-green-600/50 text-white px-2 py-1 rounded transition-colors whitespace-nowrap';
        
        setTimeout(() => {
            btnElement.innerHTML = oldHTML;
            btnElement.className = oldClass;
        }, 1500);
    }).catch(err => {
        console.error("複製失敗", err);
        alert("複製失敗，請手動複製: " + url);
    });
}

export function initReportTab() {
    setupInputDrop('rpt_source');
    setupInputDrop('rpt_output');
    setupInputDrop('rpt_nas_root');
}

// Make accessible to global scope
window.submitReportJob = submitReportJob;
window.loadReportHistory = loadReportHistory;
window.deleteReport = deleteReport;
window.openReportFile = openReportFile;
window.openReportFolder = openReportFolder;
window.openReportPdf = openReportPdf;
window.copyPublicUrl = copyPublicUrl;
window.openReport = openReport;
window.rptLog = rptLog;
