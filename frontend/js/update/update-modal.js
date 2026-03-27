// ─── Update Modal & OTA (extracted from app.js) ─── //

function updateUpdateModal(d) {
    const pctBar = document.getElementById('update_pct_bar');
    const msgEl  = document.getElementById('upd_msg');
    if (pctBar) {
        pctBar.style.width = (d.pct || 2) + '%';
        pctBar.className = d.step >= 4
            ? 'bg-green-500 h-2 rounded-full transition-all duration-500'
            : 'bg-blue-500 h-2 rounded-full transition-all duration-500';
    }
    if (msgEl) {
        msgEl.textContent = d.msg || '';
        msgEl.className = d.step >= 4
            ? 'text-xs text-green-400 font-mono bg-[#0a0a0a] rounded px-3 py-2'
            : 'text-xs text-blue-400 font-mono bg-[#0a0a0a] rounded px-3 py-2 animate-pulse';
    }
    const step = d.step || 0;
    for (let i = 1; i <= 3; i++) {
        const icon = document.getElementById(`upd_icon_${i}`);
        const row  = document.getElementById(`upd_step_${i}`);
        if (!icon || !row) continue;
        if (i < step) {
            icon.textContent = '✅';
            row.className = row.className.replace(/text-gray-400|text-blue-300/g, '') + ' text-green-400';
        } else if (i === step) {
            icon.textContent = '🔄';
            row.className = row.className.replace(/text-gray-400|text-green-400/g, '') + ' text-blue-300';
        } else {
            icon.textContent = '⏳';
            row.className = row.className.replace(/text-blue-300|text-green-400/g, '') + ' text-gray-400';
        }
    }
}

function startUpdateProgressPolling() {
    if (window._updatePollTimer) return;
    window._updateStartTime = Date.now();
    window._updatePollTimer = setInterval(async () => {
        if (!window._isUpdating) {
            clearInterval(window._updatePollTimer);
            window._updatePollTimer = null;
            return;
        }

        const elapsed = Date.now() - window._updateStartTime;
        const sec = elapsed / 1000;

        // Time-based step animation (server is offline, can't poll real progress)
        // Step 1: Download (~0-15s), Step 2: pip install (~15-60s), Step 3: restart (~60s+)
        if (sec < 8) {
            updateUpdateModal({ step: 1, pct: Math.min(30, 5 + sec * 3), msg: '正在下載最新版本...' });
        } else if (sec < 30) {
            updateUpdateModal({ step: 2, pct: Math.min(70, 30 + (sec - 8) * 1.8), msg: '正在安裝套件...' });
        } else {
            updateUpdateModal({ step: 3, pct: Math.min(90, 70 + (sec - 30) * 0.3), msg: '正在重新啟動伺服器...' });
        }

        // After 10 seconds, start checking if server is back
        if (sec > 10) {
            try {
                const health = await fetch('http://127.0.0.1:8000/api/v1/health',
                    { signal: AbortSignal.timeout(2000) });
                if (health.ok) {
                    // Server is back — show all done and reload
                    updateUpdateModal({ step: 4, pct: 100, msg: '更新完成！正在重新載入...' });
                    window._isUpdating = false;
                    clearInterval(window._updatePollTimer);
                    window._updatePollTimer = null;
                    setTimeout(() => window.location.reload(), 1000);
                    return;
                }
            } catch (e) { /* server still down */ }
        }

        // Max timeout 5 minutes: force reload
        if (elapsed > 300000) {
            window._isUpdating = false;
            clearInterval(window._updatePollTimer);
            window._updatePollTimer = null;
            window.location.reload();
        }
    }, 2000);
}

async function updateAgent() {
    // 若本機 Agent 版本 < 1.8.0，舊版 OTA 機制 (NAS xcopy) 不可用，
    // 自動下載升級工具 + 顯示操作指引遮罩
    const needsMigration = window._localAgentVersion && _isOlderThan(window._localAgentVersion, '1.8.0');

    if (needsMigration) {
        // 1) 自動下載 Originsun_Updater.bat
        const a = document.createElement('a');
        a.href = '/download_updater';
        a.download = 'Originsun_Updater.bat';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        // 2) 顯示首次遷移指引遮罩
        _showMigrationOverlay();

        // 3) 同時複製 PowerShell 指令到剪貼簿（備用）
        const serverHost = window.location.host || '192.168.1.11:8000';
        const psCmd = `powershell -ExecutionPolicy Bypass -c "irm http://${serverHost}/bootstrap.ps1 | iex"`;
        try { await navigator.clipboard.writeText(psCmd); } catch(e) {}

        // 4) 開始輪詢：偵測版本升級完成後自動重整
        _pollForMigrationDone();
        if (typeof appendLog === 'function') appendLog('已下載升級工具，請在 Chrome 下載列執行它。', 'system');
        return;
    }

    if (!confirm('即將從伺服器下載最新版本並重新啟動本機代理。這將會中斷正在本機執行的任務。\n確認要執行嗎？')) return;

    window._isUpdating = true;
    window._hasServerDiedDuringUpdate = false;
    startUpdateProgressPolling();
    if (typeof window.checkForceInstallModal === 'function') window.checkForceInstallModal(); // 立刻覆蓋藍色大遮罩

    try {
        // 不使用 await 等待 json，因為伺服器會在這瞬間自我了斷 (os._exit)，必然引發 Network Error
        const headers = {};
        if (window._authToken) headers['Authorization'] = 'Bearer ' + window._authToken;
        fetch('http://127.0.0.1:8000/api/v1/control/update', { method: 'POST', headers }).catch(e => console.log('Expected disconnect:', e));
        if (typeof appendLog === 'function') appendLog('更新指令已送出，稍後連線指示燈將會變為紅色，數秒後將自動重新載入網頁。', 'system');
    } catch (err) {
        // 忽略錯誤，絕對不把 isUpdating 設為 false，讓畫面維持藍色等待直到 polling 醒來
        console.warn('Update trigger network drop:', err);
    }
}

function _showMigrationOverlay() {
    // 如果遮罩已存在就不重複建立
    if (document.getElementById('migrationOverlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'migrationOverlay';
    overlay.className = 'fixed inset-0 bg-black/90 z-[120] flex items-center justify-center backdrop-blur-md';
    overlay.innerHTML = `
        <div class="bg-[#1e1e1e] border-t-4 border-orange-500 rounded-lg shadow-2xl p-8 max-w-lg w-full">
            <div class="flex items-center gap-4 mb-6">
                <div class="text-5xl">🚀</div>
                <div>
                    <h2 class="text-xl font-bold text-white">首次升級（僅需一次）</h2>
                    <p class="text-gray-400 text-sm">升級完成後，未來更新只需點一下即可</p>
                </div>
            </div>
            <div class="bg-[#2a2a2a] rounded-lg p-5 mb-5 space-y-4">
                <div class="flex items-start gap-3">
                    <span class="text-2xl">①</span>
                    <div>
                        <p class="text-white font-semibold">在 Chrome 下載列找到並執行升級工具</p>
                        <p class="text-gray-400 text-xs mt-1">若出現「已封鎖」，請點 ⋮ →「仍然保留」</p>
                    </div>
                </div>
                <div class="flex items-start gap-3">
                    <span class="text-2xl">②</span>
                    <div>
                        <p class="text-white font-semibold">等待自動完成（約 30 秒）</p>
                        <p class="text-gray-400 text-xs mt-1">完成後此頁面會自動重新整理</p>
                    </div>
                </div>
            </div>
            <div class="bg-blue-900/30 border border-blue-600 rounded p-3 mb-5 text-xs text-blue-300">
                💡 <strong>備用方法：</strong>升級指令已複製到剪貼簿，也可按
                <kbd class="bg-[#333] px-1 rounded">Win+R</kbd> →
                <kbd class="bg-[#333] px-1 rounded">Ctrl+V</kbd> →
                <kbd class="bg-[#333] px-1 rounded">Enter</kbd> 執行
            </div>
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-2 text-gray-500 text-xs">
                    <div class="w-4 h-4 rounded-full border-2 border-t-transparent border-orange-500 animate-spin"></div>
                    等待升級完成中...
                </div>
                <button onclick="document.getElementById('migrationOverlay').remove()"
                        class="text-gray-500 hover:text-gray-300 text-xs underline">關閉</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}

function _pollForMigrationDone() {
    const timer = setInterval(async () => {
        try {
            const r = await fetch('http://127.0.0.1:8000/api/v1/version', { signal: AbortSignal.timeout(2000) });
            if (!r.ok) return;
            const data = await r.json();
            if (data.version && !_isOlderThan(data.version, '1.8.0')) {
                clearInterval(timer);
                const overlay = document.getElementById('migrationOverlay');
                if (overlay) overlay.remove();
                if (typeof appendLog === 'function') appendLog('升級完成！正在重新載入頁面...', 'system');
                setTimeout(() => window.location.reload(), 1500);
            }
        } catch (e) { /* agent 尚未重啟，繼續等 */ }
    }, 3000);
}

function _isOlderThan(ver, minVer) {
    const a = ver.split('.').map(Number);
    const b = minVer.split('.').map(Number);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
        if ((a[i] || 0) < (b[i] || 0)) return true;
        if ((a[i] || 0) > (b[i] || 0)) return false;
    }
    return false;
}

// Expose on window
window.updateAgent = updateAgent;
window.updateUpdateModal = updateUpdateModal;
window.startUpdateProgressPolling = startUpdateProgressPolling;
