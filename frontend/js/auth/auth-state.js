// ─── Auth State (extracted from app.js) ─── //
import { TAB_MAP, shouldShowTab } from '../shared/tab-config.js';

const STORAGE_KEYS = { TOKEN: 'auth_token', USER: 'auth_user' };

// ─── Auth State Variables ─── //
window._isAdmin = false;
window._accessLevel = 0;     // RBAC: 0=readonly, 1=operator, 2=manager, 3=superadmin
window._modules = [];         // RBAC: visible module keys from role
window._authToken = localStorage.getItem(STORAGE_KEYS.TOKEN) || '';
window._authUser = null;

// Optimistic auth: hydrate window state synchronously from a cached user
// (written by _onLoginSuccess + each successful revalidate) so loadTabs()
// doesn't wait on /auth/me. Background revalidate reconciles by reloading
// the page only when the role's modules / access_level actually changed.
function _readCachedUser() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEYS.USER) || 'null'); }
    catch (_) { return null; }
}
function _writeCachedUser(d) {
    try { localStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(d)); } catch (_) {}
}
function _clearCachedUser() {
    try { localStorage.removeItem(STORAGE_KEYS.USER); } catch (_) {}
}

async function _fetchMe() {
    const r = await fetch('/api/v1/auth/me', { headers: { 'Authorization': 'Bearer ' + window._authToken } });
    if (!r.ok) { const e = new Error('auth/me failed'); e.status = r.status; throw e; }
    return r.json();
}

function _adoptUser(d) {
    window._authUser = d;
    window._accessLevel = d.access_level || 0;
    window._modules = d.modules || [];
    _writeCachedUser(d);
    _applyAuthState(window._accessLevel >= 3);
}

const _cached = window._authToken ? _readCachedUser() : null;
if (_cached) {
    window._authUser = _cached;
    window._accessLevel = _cached.access_level || 0;
    window._modules = _cached.modules || [];
    _applyAuthState(window._accessLevel >= 3);
}

window._authReady = _cached
    ? Promise.resolve()
    : (async function _initAuth() {
        if (!window._authToken) { _applyAuthState(false); return; }
        try {
            _adoptUser(await _fetchMe());
        } catch (e) {
            if (e.status) {
                localStorage.removeItem(STORAGE_KEYS.TOKEN);
                _clearCachedUser();
                window._authToken = '';
            }
            _applyAuthState(false);
        }
    })();

// Background revalidate — only when hydrated from cache. Reload if role
// shifted; trust cache on network failure (offline use case).
if (_cached && window._authToken) {
    setTimeout(() => {
        // Logout (or 401) may have cleared the token while we were waiting.
        if (!window._authToken) return;
        _fetchMe()
            .then(d => {
                const before = JSON.stringify((_cached.modules || []).slice().sort());
                const after = JSON.stringify((d.modules || []).slice().sort());
                _writeCachedUser(d);
                if (before !== after || (_cached.access_level || 0) !== (d.access_level || 0)) {
                    location.reload();
                }
            })
            .catch(e => {
                if (e.status === 401) {
                    localStorage.removeItem(STORAGE_KEYS.TOKEN);
                    _clearCachedUser();
                    location.reload();
                }
            });
    }, 100);
}

export function _applyAuthState(isAdmin) {
    window._isAdmin = isAdmin;
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = window._accessLevel >= 3 ? '' : 'none';
    });
    document.querySelectorAll('.manager-only').forEach(el => {
        el.style.display = window._accessLevel >= 2 ? '' : 'none';
    });
    const btn = document.getElementById('btn-auth');
    if (btn) {
        const avatarUrl = window._authUser?.avatar_url;
        if (window._authUser && avatarUrl) {
            // Google avatar
            btn.innerHTML = `<img src="${avatarUrl}" style="width:28px;height:28px;border-radius:50%;object-fit:cover;">`;
            btn.style.padding = '0';
            btn.style.overflow = 'hidden';
            btn.style.borderColor = window._accessLevel > 0 ? '#22c55e' : '#60a5fa';
            btn.title = window._authUser.username + ' (' + (window._accessLevel >= 3 ? '管理員' : '一般') + ')';
        } else if (window._authUser && window._accessLevel > 0) {
            btn.textContent = '👤';
            btn.style.color = '#22c55e';
            btn.style.borderColor = '#22c55e';
            btn.title = window._authUser.username + ' (' + (window._accessLevel >= 3 ? '管理員' : '一般') + ')';
        } else if (window._authUser) {
            btn.textContent = '👤';
            btn.style.color = '#60a5fa';
            btn.style.borderColor = '#60a5fa';
            btn.title = window._authUser.username + ' (' + (window._accessLevel >= 3 ? '管理員' : '一般') + ')';
        } else {
            btn.textContent = '👤';
            btn.style.color = '#888';
            btn.style.borderColor = '#555';
            btn.title = '登入';
        }
    }
}

window._authToggle = function() {
    let dd = document.getElementById('auth-dropdown');
    if (dd) { dd.remove(); return; }
    dd = document.createElement('div');
    dd.id = 'auth-dropdown';
    dd.style.cssText = 'position:fixed;top:50px;right:10px;background:#2d2d2d;border:1px solid #555;border-radius:8px;padding:8px 0;z-index:100;min-width:180px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';

    const _item = (icon, text, onclick) => `<div onclick="${onclick}" style="padding:6px 14px;cursor:pointer;font-size:12px;color:#ddd;display:flex;align-items:center;gap:6px;" onmouseover="this.style.background='#3a3a3a'" onmouseout="this.style.background=''">${icon} ${text}</div>`;
    const _sep = '<div style="border-top:1px solid #444;margin:4px 0;"></div>';

    let html = '';

    if (window._authUser) {
        // 已登入 — 顯示管理身分（RBAC v2 無角色概念）
        const roleName = window._accessLevel >= 3 ? '管理員' : '一般';
        html += `<div style="padding:6px 14px 8px;color:#aaa;font-size:11px;">👤 ${window._authUser.username} <span style="color:#60a5fa;">(${roleName})</span></div>`;
        html += _sep;

        // 工具
        html += _item('✨', '建立桌面捷徑', "createShortcut();document.getElementById('auth-dropdown')?.remove()");
        html += _item('📥', '下載安裝檔', "showInstallModal();document.getElementById('auth-dropdown')?.remove()");
        html += _item('⚙️', '通知設定', "document.getElementById('btnOpenSettings')?.click();document.getElementById('auth-dropdown')?.remove()");
        html += _item('🔄', '重新啟動 Agent', "window._restartAgent();document.getElementById('auth-dropdown')?.remove()");

        if (window._accessLevel >= 3) {
            html += _sep;
            html += _item('👥', '使用者管理', "window._openUserMgmt();document.getElementById('auth-dropdown')?.remove()");
            html += _item('🚀', '版本發布', "window._openPublishMgmt();document.getElementById('auth-dropdown')?.remove()");
        }

        html += _sep;
        html += `<div style="padding:6px 14px;">
            <button onclick="window._authLogout()" style="background:transparent;border:1px solid #ef4444;color:#ef4444;border-radius:4px;padding:4px 0;cursor:pointer;font-size:12px;width:100%;">登出</button></div>`;
    } else {
        // 未登入
        html += _item('✨', '建立桌面捷徑', "createShortcut();document.getElementById('auth-dropdown')?.remove()");
        html += _item('📥', '下載安裝檔', "showInstallModal();document.getElementById('auth-dropdown')?.remove()");
        html += _item('⚙️', '通知設定', "document.getElementById('btnOpenSettings')?.click();document.getElementById('auth-dropdown')?.remove()");
        html += _item('🔄', '重新啟動 Agent', "window._restartAgent();document.getElementById('auth-dropdown')?.remove()");
        html += _sep;
        html += `<div style="padding:6px 14px;">
            <button onclick="document.getElementById('auth-dropdown')?.remove();window._showLoginModal()" style="background:#3b82f6;color:#fff;border:none;border-radius:4px;padding:4px 0;cursor:pointer;font-size:12px;width:100%;">登入</button></div>`;
    }

    dd.innerHTML = html;
    document.body.appendChild(dd);
    setTimeout(() => document.addEventListener('click', function _close(e) {
        if (!dd.contains(e.target) && e.target.id !== 'btn-auth') { dd.remove(); document.removeEventListener('click', _close); }
    }), 100);
};

window._restartAgent = async function() {
    if (!confirm('確定要重新啟動本機 Agent？\n伺服器將短暫離線約 10 秒。')) return;
    try {
        await fetch('/api/admin/restart', { method: 'POST' });
    } catch (_) { /* server going down is expected */ }
    alert('Agent 正在重新啟動中，請等待約 15 秒後重新整理頁面。');
};

window._authLogout = function() {
    localStorage.removeItem(STORAGE_KEYS.TOKEN);
    _clearCachedUser();
    // Wipe per-user SWR caches so the next person to log in on this
    // browser doesn't briefly see the previous user's data.
    try { localStorage.removeItem('crm_projects_swr_v1'); } catch (_) {}
    window._authToken = '';
    window._authUser = null;
    window._accessLevel = 0;
    window._modules = [];
    _applyAuthState(false);
    document.getElementById('auth-dropdown')?.remove();
    // Restore media tabs only (未登入 = 只顯示媒體)
    _applyModuleTabs();
};

// ─── Shared Login Success Handler ─── //
export async function _onLoginSuccess(d) {
    window._authToken = d.token;
    localStorage.setItem(STORAGE_KEYS.TOKEN, d.token);
    _adoptUser(d);
    _applyModuleTabs();
    document.getElementById('auth-login-modal')?.classList.add('hidden');
    // _applyModuleTabs only flips visibility — sections gated out of the
    // boot-time loadTabs() filter (no token → CRM/admin skipped) still
    // have empty innerHTML. Inject them now or first-login users hit a
    // blank 專案管理 forever.
    if (typeof window._ensureTabsLoaded === 'function') {
        await window._ensureTabsLoaded();
    }
    if (d.first_login) {
        alert('首次登入，請到系統設定修改密碼。');
    }
}

// ─── RBAC Module-based Tab Visibility ─── //
export function _applyModuleTabs() {
    const modules = window._modules;
    const _show = (key) => shouldShowTab(key, window._authUser, modules);
    // Section-level hiding — retained as the defense-in-depth fallback layer so a
    // section is never visible even if reached outside the grouped nav.
    Object.entries(TAB_MAP).forEach(([key, id]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = _show(key) ? '' : 'none';
    });
    // Grouped nav (top bar + left sidebar) is RBAC-rendered in app.js; re-render it
    // for the new auth state — hides unauthorized groups/items and redirects off the
    // current tab if it is no longer allowed.
    if (typeof window._refreshGroupNav === 'function') window._refreshGroupNav();
}
// Legacy alias
window._applyVisibleTabs = _applyModuleTabs;
window._applyModuleTabs = _applyModuleTabs;
window._applyAuthState = _applyAuthState;
