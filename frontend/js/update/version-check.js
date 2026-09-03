// ─── Version Check & Local Agent Polling (extracted from app.js) ─── //

// --- Detect if accessing from external (non-LAN) network ---
function _isLanAccess() {
    const h = window.location.hostname;
    return h === 'localhost' || h === '127.0.0.1' ||
           h.startsWith('192.168.') || h.startsWith('10.') ||
           /^172\.(1[6-9]|2\d|3[01])\./.test(h);
}
window._isExternalAccess = !_isLanAccess();

// --- Local Agent Polling ---
window._localAgentActive = false;
window._initialPollComplete = false;
window._isUpdating = false;
window._hasServerDiedDuringUpdate = false;
window._updatePollTimer = null;
window._updateStartTime = 0;
window._localAgentVersion = null;

export async function pollLocalAgent() {
    if (document.hidden) return;   // 背景分頁不打（切回來下一輪就補）
    // 外網存取時不需要偵測本機代理 — 直接連伺服器
    if (window._isExternalAccess) {
        window._localAgentActive = true;
        window._initialPollComplete = true;
        updateAgentBadge(true);
        checkForceInstallModal();
        return;
    }
    try {
        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        // 只問「活著嗎」：health 幾十 bytes；status 會把整段 log 緩衝（上限 2000 行）帶回來
        const targetUrl = isLocal ? '/api/v1/health' : 'http://127.0.0.1:8000/api/v1/health';

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1000);

        const response = await fetch(targetUrl, { signal: controller.signal });
        clearTimeout(timeoutId);

        if (response.ok) {
            window._localAgentActive = true;
            updateAgentBadge(true);
            // 🔴 這裡原本會在「主控 IP 開頁 + 本機有 agent」時把 socket 切到
            // 127.0.0.1，但任務是送到 same-origin —— 進度事件在主控、瀏覽器
            // 卻聽自己的機器，畫面像卡死（2026-08-12）。socket 現在固定
            // same-origin（app.js 開場設一次），這裡不再切換。
            // 本機 agent 只用於：安裝提示、版本徽章、以及「該發生在使用者
            // 自己桌面」的動作（utils.js getLocalAgentBase）。
        } else {
            window._localAgentActive = false;
            if (window._isUpdating) window._hasServerDiedDuringUpdate = true;
            updateAgentBadge(false);
        }
    } catch (err) {
        window._localAgentActive = false;
        if (window._isUpdating) window._hasServerDiedDuringUpdate = true;
        updateAgentBadge(false);
    } finally {
        window._initialPollComplete = true;
        checkForceInstallModal();
    }
}

export function checkForceInstallModal() {
    const updatingModal = document.getElementById('updatingModal');

    // 處理更新中的遮罩顯示
    if (window._isUpdating) {
        if (updatingModal) updatingModal.classList.remove('hidden');
    } else {
        if (updatingModal) updatingModal.classList.add('hidden');
    }

    // 如果代理剛更新回來，且本機真的斷線過又恢復連線，才觸發網頁重整以套用新版 JS
    if (window._isUpdating && window._localAgentActive && window._hasServerDiedDuringUpdate) {
        window._isUpdating = false;
        setTimeout(() => window.location.reload(), 1500);
    }
}

export function updateAgentBadge(isActive) {
    const dot = document.getElementById('status-dot');
    const btnShortcut = document.getElementById('btn_create_shortcut');
    if (isActive) {
        if (dot) { dot.style.background = '#22c55e'; dot.style.boxShadow = '0 0 6px #22c55e'; dot.title = '本機已連線'; }
        checkAgentVersion();
        if (btnShortcut) btnShortcut.style.display = 'flex';
    } else {
        if (dot) { dot.style.background = '#ef4444'; dot.style.boxShadow = 'none'; dot.title = '本機離線'; }
        if (btnShortcut) btnShortcut.style.display = 'none';
    }
}

// 版本比對每 60 秒一次就夠（後端另有每 10 分鐘的 update_available 推播）；
// pollLocalAgent 每 3 秒呼叫 updateAgentBadge(true)，沒節流會 3 秒打兩支版本 API
let _versionCheckedAt = 0;
export async function checkAgentVersion() {
    if (Date.now() - _versionCheckedAt < 60000) return;
    _versionCheckedAt = Date.now();
    try {
        // 外網存取：只顯示伺服器版本，不顯示更新按鈕
        if (window._isExternalAccess) {
            const res = await fetch('/api/v1/version').catch(() => null);
            const btnBadge = document.getElementById('header_version_badge');
            if (res?.ok && btnBadge) {
                const d = await res.json();
                const v = d.version?.startsWith('v') ? d.version.slice(1) : d.version;
                btnBadge.style.display = 'inline-block';
                btnBadge.className = "text-sm font-normal text-gray-500 px-2 py-0.5";
                btnBadge.innerHTML = `v${v || '?'}`;
                btnBadge.title = "伺服器版本";
                btnBadge.onclick = null; // 外網不能觸發更新
            }
            return;
        }
        // 1. 取得本機 Agent 正在運行的版本（不論在 localhost 或遠端都固定問 localhost）
        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        const localVersionUrl = isLocal ? '/api/v1/version' : 'http://127.0.0.1:8000/api/v1/version';
        // 瀏覽主控端時，主控端本身就是版本來源，直接用 /api/v1/version
        const nasVersionUrl = isLocal ? '/api/v1/nas_version' : '/api/v1/version';

        const [localRes, nasRes] = await Promise.all([
            fetch(localVersionUrl).catch(() => null),
            fetch(nasVersionUrl).catch(() => null)
        ]);

        const btnBadge = document.getElementById('header_version_badge');
        if (!btnBadge) return;

        btnBadge.style.display = 'inline-block';

        if (!localRes || !localRes.ok || !nasRes || !nasRes.ok) {
            console.warn("版號檢查失敗: 無法連線");
            return;
        }

        const localData = await localRes.json();
        const nasData = await nasRes.json();
        const currentVersion = localData.version;
        let latestVersion = nasData.version;
        window._localAgentVersion = currentVersion; // 記住本機版本供 updateAgent 使用

        // Fallback: 若 nas_version 回傳 unknown，嘗試從當前頁面 origin 取得
        if (latestVersion === 'unknown') {
            try {
                const fbRes = await fetch('/api/v1/version').catch(() => null);
                if (fbRes?.ok) {
                    const fb = await fbRes.json();
                    if (fb.version && fb.version !== 'unknown') latestVersion = fb.version;
                }
            } catch(e) {}
        }

        // 移除 v 前綴後再比較版號
        const stripV = (v) => v && v.startsWith('v') ? v.slice(1) : v;
        const isNewerSemver = (latest, current) => {
            if (!latest || latest === 'unknown' || !current) return false;
            const l = stripV(latest), c = stripV(current);
            if (l === c) return false;
            const lParts = l.split('.').map(Number);
            const cParts = c.split('.').map(Number);
            for (let i = 0; i < Math.max(lParts.length, cParts.length); i++) {
                const lp = lParts[i] || 0;
                const cp = cParts[i] || 0;
                if (lp > cp) return true;
                if (lp < cp) return false;
            }
            return false;
        };

        // 3. 比較版號，嚴謹確認最新版本是否真的大於本機版本
        if (isNewerSemver(latestVersion, currentVersion)) {
            const displayLatest = stripV(latestVersion);
            const displayCurrent = stripV(currentVersion);
            btnBadge.className = "cursor-pointer text-sm font-bold text-white bg-red-600 hover:bg-red-500 px-2 py-0.5 rounded shadow animate-pulse flex items-center gap-1";
            btnBadge.innerHTML = `🚀 <span class="underline">發現新版本 (v${displayLatest})</span>`;
            btnBadge.title = `點擊以從伺服器安裝最新版 (目前: v${displayCurrent})`;
        } else {
            const displayCurrent = stripV(currentVersion);
            btnBadge.className = "cursor-pointer text-sm font-normal text-blue-400 hover:text-blue-300 px-2 py-0.5 rounded transition-colors";
            btnBadge.innerHTML = `v${displayCurrent || '?'}`;
            btnBadge.title = "已是最新版 (點擊可強制重新套用更新)";
        }
    } catch (err) {
        console.warn("版號檢查失敗", err);
    }
}

// Expose on window for cross-module access
window.pollLocalAgent = pollLocalAgent;
window.checkForceInstallModal = checkForceInstallModal;
window.updateAgentBadge = updateAgentBadge;
window.checkAgentVersion = checkAgentVersion;
