// ─── User Management (extracted from app.js) ─── //
import { _ensureModalStyles, _createFormModal } from '../shared/modal-styles.js';

const TAB_NAMES = {backup:'備份並轉檔',verify:'檔案比對',transcode:'轉 Proxy',concat:'製作串帶',report:'檔案視覺報表',transcribe:'AI 逐字稿',tts:'語音生成'};

const ALL_MODULES = ['backup','verify','transcode','concat','report','transcribe','tts','projects','crm_clients','crm_projects','crm_quotes'];
const MODULE_LABELS = {backup:'備份',verify:'比對',transcode:'轉檔',concat:'串帶',report:'報表',transcribe:'逐字稿',tts:'語音',projects:'專案',crm_clients:'客戶',crm_projects:'專案管理',crm_quotes:'報價'};

// ─── RBAC: cached roles list for user mgmt ─── //
let _cachedRoles = [];
async function _fetchRoles() {
    try {
        const r = await fetch('/api/v1/roles');
        if (r.ok) _cachedRoles = await r.json();
    } catch (_) {}
    return _cachedRoles;
}

// Export for role-mgmt.js
export { _cachedRoles, _fetchRoles, ALL_MODULES, MODULE_LABELS };

function _renderModuleTags(modules) {
    return (modules && modules.length)
        ? modules.map(m => `<span style="display:inline-block;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:4px;padding:1px 7px;font-size:10px;color:#999;">${MODULE_LABELS[m]||m}</span>`).join(' ')
        : '<span style="color:#555;font-size:11px;">未設定</span>';
}

window._openUserMgmt = async function() {
    _ensureModalStyles();
    document.getElementById('user-mgmt-modal')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'user-mgmt-modal';
    overlay.className = '_fm-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.addEventListener('keydown', function _esc(e) { if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', _esc); } });

    const modal = document.createElement('div');
    modal.className = '_fm-modal';
    modal.style.width = '780px'; modal.style.maxWidth = '92%';
    modal.innerHTML = `
        <div class="_fm-header" style="padding:14px 24px;border-bottom:none;">
            <div style="display:flex;align-items:center;gap:0;">
                <button id="umgmt-tab-users" class="_umgmt-tab _umgmt-tab-active" onclick="window._switchMgmtTab('users')">使用者</button>
                <button id="umgmt-tab-keys" class="_umgmt-tab" onclick="window._switchMgmtTab('keys')">API Keys</button>
            </div>
            <div style="display:flex;gap:8px;align-items:center;">
                <button id="umgmt-action-btn" class="_fm-btn-submit" style="padding:5px 16px;font-size:12px;">+ 新增使用者</button>
                <span class="_fm-close" onclick="document.getElementById('user-mgmt-modal')?.remove()">&#x2715;</span>
            </div>
        </div>
        <div style="height:1px;background:#333;margin:0;"></div>
        <div class="_fm-body" style="padding:16px 24px;min-height:280px;">
            <div id="umgmt-panel-users" style="font-size:12px;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
            <div id="umgmt-panel-keys" style="font-size:12px;display:none;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Inject tab styles (once)
    if (!document.getElementById('_umgmtTabStyles')) {
        const s = document.createElement('style');
        s.id = '_umgmtTabStyles';
        s.textContent = `
            ._umgmt-tab { background:transparent;border:none;color:#666;font-size:13px;font-weight:500;padding:10px 20px;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;letter-spacing:0.3px; }
            ._umgmt-tab:hover { color:#bbb; }
            ._umgmt-tab-active { color:#f0f0f0;border-bottom-color:#7c3aed; }
        `;
        document.head.appendChild(s);
    }

    // Wire up action button and tabs
    document.getElementById('umgmt-action-btn').onclick = () => window._addUserPrompt();

    // Remap panel IDs to match what _loadUserList / _loadApiKeyList expect
    document.getElementById('umgmt-panel-users').id = 'umgmt-list';
    document.getElementById('umgmt-panel-keys').id = 'apikey-list';

    window._switchMgmtTab = function(tab) {
        const usersPanel = document.getElementById('umgmt-list');
        const keysPanel = document.getElementById('apikey-list');
        const usersTab = document.getElementById('umgmt-tab-users');
        const keysTab = document.getElementById('umgmt-tab-keys');
        const actionBtn = document.getElementById('umgmt-action-btn');
        if (tab === 'users') {
            usersPanel.style.display = ''; keysPanel.style.display = 'none';
            usersTab.classList.add('_umgmt-tab-active'); keysTab.classList.remove('_umgmt-tab-active');
            actionBtn.textContent = '+ 新增使用者'; actionBtn.onclick = () => window._addUserPrompt();
        } else {
            usersPanel.style.display = 'none'; keysPanel.style.display = '';
            usersTab.classList.remove('_umgmt-tab-active'); keysTab.classList.add('_umgmt-tab-active');
            actionBtn.textContent = '+ 產生新 Key'; actionBtn.onclick = () => window._createApiKey();
            if (typeof window._loadApiKeyList === 'function') window._loadApiKeyList();
        }
    };

    await _loadUserList();
};

async function _loadUserList() {
    const container = document.getElementById('umgmt-list');
    if (!container) return;
    try {
        const [rolesResult, r] = await Promise.all([
            _fetchRoles(),
            fetch('/api/v1/auth/users', { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') } }),
        ]);
        const roles = _cachedRoles;
        if (!r.ok) { container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗（需要管理員權限）</div>'; return; }
        const users = await r.json();
        const roleOptions = roles.map(rl =>
            `<option value="${rl.name}">${rl.name} (Lv${rl.access_level})</option>`
        ).join('');

        // Table header
        let html = `<div style="display:grid;grid-template-columns:140px 150px 1fr auto;gap:0;font-size:11px;color:#666;padding:0 16px 8px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;">
            <span>帳號</span><span>角色</span><span>可用模組</span><span>操作</span>
        </div>`;
        html += users.map(u => {
            const userRole = u.role_name || u.role || 'editor';
            const modules = u.modules || [];
            const moduleTags = _renderModuleTags(modules);
            const am = u.auth_method || 'password';
            const authBadge = am === 'google'
                ? '<span style="display:inline-block;background:#4285f422;color:#8ab4f8;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">G</span>'
                : am === 'both'
                ? '<span style="display:inline-block;background:#4285f422;color:#8ab4f8;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">G+</span>'
                : '';
            const avatarImg = u.avatar_url
                ? `<img src="${u.avatar_url}" style="width:20px;height:20px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:4px;">`
                : '';
            const emailLine = u.email
                ? `<div style="font-size:10px;color:#666;margin-top:1px;">${u.email}</div>`
                : '';
            return `
            <div style="display:grid;grid-template-columns:140px 150px 1fr auto;gap:12px;align-items:center;padding:12px 16px;margin-bottom:1px;background:#1e1e1e;border:1px solid #2e2e2e;border-radius:8px;transition:border-color .15s;" onmouseenter="this.style.borderColor='#444'" onmouseleave="this.style.borderColor='#2e2e2e'">
                <div>
                    <div>${avatarImg}<span style="color:#f0f0f0;font-weight:600;font-size:13px;">${u.username}</span>${u.username === 'admin' ? '<span style="display:inline-block;background:#7c3aed22;color:#a78bfa;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">SUPER</span>' : ''}${authBadge}</div>
                    ${emailLine}
                </div>
                <div>
                    <select data-role-user="${u.username}" class="_fm-select" style="padding:4px 8px;font-size:11px;border-radius:5px;" onchange="window._onUserRoleChange(this)">
                        ${roleOptions.replace(`value="${userRole}"`, `value="${userRole}" selected`)}
                    </select>
                </div>
                <div data-modules-user="${u.username}" style="display:flex;flex-wrap:wrap;gap:4px;line-height:1.6;">${moduleTags}</div>
                <div style="display:flex;gap:6px;align-items:center;">
                    <button onclick="window._changeUserPwd('${u.username}')" class="_fm-btn-cancel" style="padding:3px 10px;font-size:11px;">改密碼</button>
                    <button onclick="window._saveUserSettings('${u.username}')" class="_fm-btn-submit" style="padding:3px 12px;font-size:11px;font-weight:500;">儲存</button>
                    ${u.username !== 'admin' ? `<button onclick="window._deleteUser('${u.username}')" style="background:transparent;border:1px solid rgba(239,68,68,0.3);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;transition:all .15s;" onmouseenter="this.style.borderColor='#ef4444';this.style.background='rgba(239,68,68,0.08)'" onmouseleave="this.style.borderColor='rgba(239,68,68,0.3)';this.style.background='transparent'">刪除</button>` : ''}
                </div>
            </div>`;
        }).join('');
        container.innerHTML = html;
    } catch (_) {
        container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗</div>';
    }
}

// ─── Add User (styled modal) ─── //
window._addUserPrompt = async function() {
    const roles = _cachedRoles.length ? _cachedRoles : await _fetchRoles();
    const roleOptions = roles.map(r => ({
        value: r.name,
        label: `${r.name} (Lv${r.access_level})`,
    }));
    _createFormModal({
        id: 'add-user-modal',
        title: '新增使用者',
        submitLabel: '建立使用者',
        fields: [
            { type: 'section', label: '帳號資訊' },
            { key: 'username', label: '帳號', type: 'text', required: true, autofocus: true, placeholder: '輸入英文帳號名稱' },
            { key: 'password', label: '密碼', type: 'password', required: true, placeholder: '設定密碼' },
            { key: 'password2', label: '確認密碼', type: 'password', required: true, placeholder: '再次輸入密碼' },
            { type: 'divider' },
            { type: 'section', label: '權限指派' },
            { key: 'role_name', label: '角色', type: 'select', options: roleOptions, defaultValue: 'editor' },
        ],
        onSubmit: async (vals, setError, close) => {
            if (!vals.username) { setError('請輸入帳號'); return; }
            if (!vals.password) { setError('請輸入密碼'); return; }
            if (vals.password !== vals.password2) { setError('兩次密碼不一致'); return; }
            if (vals.password.length < 3) { setError('密碼至少需要 3 個字元'); return; }
            try {
                const r = await fetch('/api/v1/auth/users', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: vals.username, password: vals.password, role_name: vals.role_name }),
                });
                const d = await r.json();
                if (!r.ok) { setError(d.detail || '新增失敗'); return; }
                close();
                _loadUserList();
            } catch (_) { setError('連線失敗，請稍後再試'); }
        },
    });
};

window._onUserRoleChange = function(selectEl) {
    const username = selectEl.getAttribute('data-role-user');
    const roleName = selectEl.value;
    const role = _cachedRoles.find(r => r.name === roleName);
    const modulesDiv = document.querySelector(`[data-modules-user="${username}"]`);
    if (!modulesDiv || !role) return;
    const modules = role.modules || [];
    modulesDiv.innerHTML = _renderModuleTags(modules);
};

window._saveUserSettings = async function(username) {
    const roleSelect = document.querySelector(`select[data-role-user="${username}"]`);
    const role_name = roleSelect ? roleSelect.value : 'editor';
    try {
        const r = await fetch('/api/v1/auth/users/' + username, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role_name }),
        });
        if (r.ok) {
            const btns = document.querySelectorAll('#umgmt-list button');
            btns.forEach(b => {
                if (b.textContent.includes('儲存') && b.onclick?.toString().includes(username)) {
                    b.textContent = '✅ 已儲存'; b.style.background = '#22c55e';
                    setTimeout(() => { b.textContent = '💾 儲存'; b.style.background = '#3b82f6'; }, 1500);
                }
            });
        } else { alert('儲存失敗'); }
    } catch (_) { alert('連線失敗'); }
};

window._changeUserPwd = async function(username) {
    const password = prompt(`設定 ${username} 的新密碼：`);
    if (!password) return;
    const r = await fetch('/api/v1/auth/users/' + username, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
    });
    if (r.ok) alert('密碼已更新'); else alert('修改失敗');
};

window._deleteUser = async function(username) {
    if (!confirm(`確定要刪除使用者 "${username}"？`)) return;
    const r = await fetch('/api/v1/auth/users/' + username, { method: 'DELETE' });
    if (r.ok) _loadUserList(); else alert('刪除失敗');
};

// Expose _loadUserList for cross-module calls
window._loadUserList = _loadUserList;
