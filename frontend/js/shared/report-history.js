// ─── 報表歷史清單（備份頁「最新備份報表」＋報表頁「歷史報表」共用） ─── //
// 2026-09-03 從 tabs/report/report.js 搬來：它本來就同時填 rpt_history_list（報表頁）與
// main_history_list（備份頁）兩個容器；住在報表頁裡會逼備份頁為了一支函式把整個報表分頁載進來，
// 而且沒有 report 權限的人清單永遠停在「NAS 尚無歷史報表紀錄」（/api/v1/reports/history 本身不擋）。
// 清單裡的按鈕是 inline onclick → 三支都要掛在 window 上。
import { getComputeBaseUrl } from './utils.js';

export async function loadReportHistory() {
    const listEls = [
        document.getElementById('rpt_history_list'),
        document.getElementById('main_history_list')
    ].filter(el => el !== null);

    if (listEls.length === 0) return;

    listEls.forEach(el => {
        el.innerHTML = '<div class="px-4 py-6 text-center text-xs text-gray-500">載入中...</div>';
    });

    try {
        const res = await fetch(getComputeBaseUrl() + '/api/v1/reports/history');
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
                    ${r.public_url ? `<button onclick="copyPublicUrl('${r.public_url}', this)" class="text-xs border border-[#0d9488] bg-[#0f766e]/30 hover:bg-[#0f766e] text-teal-200 px-2 py-1 rounded transition-colors whitespace-nowrap">🌐 複製公開網址</button>` : ''}
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
        // 這支端點掛 admin 守衛：未登入 401、非管理員 403。原本完全不看回應，
        // 直接重載清單 → 項目還在、零訊息（正是「點了沒反應」）（2026-08-12）
        const r = await fetch(getComputeBaseUrl() + '/api/v1/reports/' + reportId,
                              { method: 'DELETE', headers: window.bearerHeader ? window.bearerHeader() : {} });
        const d = await r.json().catch(() => ({}));
        if (r.status === 401 || r.status === 403) {
            alert('刪除報表需要管理員登入 —— 請先用右上角 👤 登入管理員帳號。');
            return;
        }
        if (!r.ok || d.status === 'error') {
            alert('刪除失敗：' + (d.message || d.detail || ('HTTP ' + r.status)));
            return;
        }
        loadReportHistory();
    } catch (err) {
        alert('刪除失敗: ' + err.message);
    }
}

/**
 * Copy URL to clipboard with fallback for HTTP (non-secure) contexts.
 * navigator.clipboard.writeText() only works on HTTPS or localhost.
 * For HTTP (e.g. 192.168.x.x:8000), use the legacy execCommand('copy') fallback.
 */
export function copyPublicUrl(url, btnElement) {
    if (!url) return;

    function onSuccess() {
        const oldHTML = btnElement.innerHTML;
        const oldClass = btnElement.className;
        btnElement.innerHTML = '✅ 已複製！';
        btnElement.className = 'text-xs border border-green-500 bg-green-600/50 text-white px-2 py-1 rounded transition-colors whitespace-nowrap';
        setTimeout(() => {
            btnElement.innerHTML = oldHTML;
            btnElement.className = oldClass;
        }, 1500);
    }

    function fallbackCopy(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        try {
            const ok = document.execCommand('copy');
            if (ok) { onSuccess(); }
            else { alert('複製失敗，請手動複製: ' + text); }
        } catch (e) {
            alert('複製失敗，請手動複製: ' + text);
        }
        document.body.removeChild(ta);
    }

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(onSuccess).catch(() => fallbackCopy(url));
    } else {
        fallbackCopy(url);
    }
}

window.loadReportHistory = loadReportHistory;
window.deleteReport = deleteReport;
window.copyPublicUrl = copyPublicUrl;
