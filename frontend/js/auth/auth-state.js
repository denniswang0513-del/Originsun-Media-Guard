// ─── Auth State (extracted from app.js) ─── //
import { TAB_MAP, NAV_MAP, shouldShowTab } from '../shared/tab-config.js';

// ─── Auth State Variables ─── //
window._isAdmin = false;
window._accessLevel = 0;     // RBAC: 0=readonly, 1=operator, 2=manager, 3=superadmin
window._modules = [];         // RBAC: visible module keys from role
window._authToken = localStorage.getItem('auth_token') || '';
window._authUser = null;

// Check saved token on load（存 Promise 供 loadTabs 等待）
window._authReady = (async function _initAuth() {
    if (!window._authToken) { _applyAuthState(false); return; }
    try {
        const r = await fetch('/api/v1/auth/me', { headers: { 'Authorization': 'Bearer ' + window._authToken } });
        if (r.ok) {
            const d = await r.json();
            window._authUser = d;
            window._accessLevel = d.access_level || 0;
            window._modules = d.modules || [];
            _applyAuthState(window._accessLevel >= 3);
            // _applyModuleTabs 延後到 loadTabs 完成後執行
        } else {
            localStorage.removeItem('auth_token');
            window._authToken = '';
            _applyAuthState(false);
        }
    } catch (_) { _applyAuthState(false); }
})();

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
            btn.title = window._authUser.username + ' (' + (window._authUser.role_name || '') + ')';
        } else if (window._authUser && window._accessLevel > 0) {
            btn.textContent = '👤';
            btn.style.color = '#22c55e';
            btn.style.borderColor = '#22c55e';
            btn.title = window._authUser.username + ' (' + (window._authUser.role_name || '') + ')';
        } else if (window._authUser) {
            btn.textContent = '👤';
            btn.style.color = '#60a5fa';
            btn.style.borderColor = '#60a5fa';
            btn.title = window._authUser.username + ' (' + (window._authUser.role_name || '') + ')';
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
        // 已登入 — 顯示角色名稱
        const roleName = window._authUser.role_name || window._authUser.role || '';
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
            html += _item('🔰', '角色管理', "window._openRoleMgmt();document.getElementById('auth-dropdown')?.remove()");
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
    localStorage.removeItem('auth_token');
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
export function _onLoginSuccess(d) {
    window._authToken = d.token;
    window._authUser = d;
    window._accessLevel = d.access_level || 0;
    window._modules = d.modules || [];
    localStorage.setItem('auth_token', d.token);
    _applyAuthState(window._accessLevel >= 3);
    _applyModuleTabs();
    document.getElementById('auth-login-modal')?.classList.add('hidden');
    if (d.first_login) {
        alert('首次登入，請到系統設定修改密碼。');
    }
}

// ─── RBAC Module-based Tab Visibility ─── //
export function _applyModuleTabs() {
    const modules = window._modules;
    const _show = (key) => shouldShowTab(key, window._authUser, modules);
    Object.entries(TAB_MAP).forEach(([key, id]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = _show(key) ? '' : 'none';
    });
    document.querySelectorAll('nav button').forEach(btn => {
        const text = btn.textContent;
        Object.entries(NAV_MAP).forEach(([key, label]) => {
            if (text.includes(label)) btn.style.display = _show(key) ? '' : 'none';
        });
    });
}
// Legacy alias
window._applyVisibleTabs = _applyModuleTabs;
window._applyModuleTabs = _applyModuleTabs;
