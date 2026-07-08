// ─── User Management (extracted from app.js) ─── //
// RBAC v2: 權限直接綁帳號（角色層已移除）。每個帳號 = 一組可勾選模組 + 「管理員」開關。
import { _ensureModalStyles, _createFormModal } from '../shared/modal-styles.js';
import { groupModules, ALL_MODULES } from '../shared/tab-config.js';

// key 集合必須 == core/auth.py ALL_MODULES == tab-config.js PERMISSION_GROUPS
// （tests/unit/test_rbac_module_sync.py 三方同步測試把關，漏 key 會 fail）
const MODULE_LABELS = {bulletin:'公布欄',preprod_plan:'拍攝企劃',preprod_locations:'場景庫',preprod_proposals:'提案庫',intel:'產業情報',backup:'備份',verify:'比對',transcode:'轉檔',concat:'串帶',report:'報表',transcribe:'逐字稿',tts:'語音',drone_meta:'空拍寫入',projects:'專案',crm_clients:'客戶',crm_projects:'專案管理',crm_quotes:'報價',crm_staff:'人力',crm_invoices:'帳務',timesheets:'工時檢核',portal:'審批門戶',website_admin:'官網'};

// The 4-group structure is identical for every user (it's all modules grouped),
// so compute it once rather than per user row / per modal open.
const _PERM_GROUPS = groupModules(ALL_MODULES);

// Render the editable, 4-group permission cell for one user. `locked` disables
// everything (built-in admin: prevent self-lockout). When 管理員 is on, modules
// are implied (full access) so the grid is dimmed.
function _renderUserPermCell(username, userModules, isAdminUser, locked) {
    const groups = _PERM_GROUPS;
    const dis = locked ? 'disabled' : '';
    const adminRow = `
        <label class="_fm-chk" style="padding:3px 6px;font-weight:600;color:${isAdminUser ? '#a78bfa' : '#999'};">
            <input type="checkbox" data-uadmin-user="${username}" ${isAdminUser ? 'checked' : ''} ${dis}
                   onchange="window._onUserAdminToggle('${username}', this.checked)">
            👑 管理員（完整權限：使用者 / 設定 / 發版）
        </label>`;
    const groupsHtml = groups.map(g => {
        const total = g.modules.length;
        const checkedN = g.modules.filter(m => userModules.includes(m)).length;
        const allOn = checkedN === total && total > 0;
        const boxes = g.modules.map(m => `
            <label class="_fm-chk" style="min-width:auto;padding:2px 6px;">
                <input type="checkbox" data-umod-user="${username}" data-group="${g.id}" value="${m}"
                       ${userModules.includes(m) ? 'checked' : ''} ${dis}
                       onchange="window._syncUserGroupMaster('${username}','${g.id}')"> ${MODULE_LABELS[m] || m}
            </label>`).join('');
        return `
            <div style="margin-bottom:4px;">
                <label class="_fm-chk" style="font-weight:600;color:#bbb;padding:2px 6px;">
                    <input type="checkbox" data-umaster-user="${username}" data-group="${g.id}" ${allOn ? 'checked' : ''} ${dis}
                           onchange="window._toggleUserGroup('${username}','${g.id}',this.checked)"> ${g.label}
                    <span data-ucount-user="${username}" data-group="${g.id}" style="color:#666;font-size:10px;font-weight:400;margin-left:4px;">${checkedN}/${total}</span>
                </label>
                <div style="display:flex;flex-wrap:wrap;gap:1px;padding-left:18px;">${boxes}</div>
            </div>`;
    }).join('');
    return `${adminRow}
        <div data-uperm-user="${username}" style="margin-top:2px;opacity:${isAdminUser ? '0.45' : '1'};pointer-events:${isAdminUser ? 'none' : 'auto'};">${groupsHtml}</div>`;
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
        const r = await fetch('/api/v1/auth/users', { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') } });
        if (!r.ok) { container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗（需要管理員權限）</div>'; return; }
        const users = await r.json();

        // Table header
        let html = `<div style="display:grid;grid-template-columns:170px 1fr auto;gap:0;font-size:11px;color:#666;padding:0 16px 8px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;">
            <span>帳號</span><span>權限</span><span>操作</span>
        </div>`;
        html += users.map(u => {
            const modules = u.modules || [];
            const isAdminUser = (u.access_level || 0) >= 3;
            const locked = (u.username === 'admin');   // 內建超級帳號鎖定，避免把自己鎖在外
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
            <div style="display:grid;grid-template-columns:170px 1fr auto;gap:12px;align-items:start;padding:12px 16px;margin-bottom:1px;background:#1e1e1e;border:1px solid #2e2e2e;border-radius:8px;transition:border-color .15s;" onmouseenter="this.style.borderColor='#444'" onmouseleave="this.style.borderColor='#2e2e2e'">
                <div style="padding-top:4px;">
                    <div>${avatarImg}<span style="color:#f0f0f0;font-weight:600;font-size:13px;">${u.username}</span>${u.username === 'admin' ? '<span style="display:inline-block;background:#7c3aed22;color:#a78bfa;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">SUPER</span>' : ''}${authBadge}</div>
                    ${emailLine}
                </div>
                <div style="min-width:0;">${_renderUserPermCell(u.username, modules, isAdminUser, locked)}</div>
                <div style="display:flex;gap:6px;align-items:center;padding-top:4px;">
                    <button onclick="window._changeUserPwd('${u.username}')" class="_fm-btn-cancel" style="padding:3px 10px;font-size:11px;">改密碼</button>
                    ${locked ? '' : `<button onclick="window._saveUserSettings('${u.username}')" class="_fm-btn-submit" style="padding:3px 12px;font-size:11px;font-weight:500;">儲存</button>`}
                    ${u.username !== 'admin' ? `<button onclick="window._deleteUser('${u.username}')" style="background:transparent;border:1px solid rgba(239,68,68,0.3);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;transition:all .15s;" onmouseenter="this.style.borderColor='#ef4444';this.style.background='rgba(239,68,68,0.08)'" onmouseleave="this.style.borderColor='rgba(239,68,68,0.3)';this.style.background='transparent'">刪除</button>` : ''}
                </div>
            </div>`;
        }).join('');
        container.innerHTML = html;

        // Reflect partial-group state (indeterminate can't be set via HTML attr).
        container.querySelectorAll('input[data-umaster-user]').forEach(master => {
            const u = master.getAttribute('data-umaster-user');
            const g = master.getAttribute('data-group');
            const boxes = [...container.querySelectorAll(`input[data-umod-user="${u}"][data-group="${g}"]`)];
            const checked = boxes.filter(b => b.checked).length;
            master.indeterminate = checked > 0 && checked < boxes.length;
        });
    } catch (_) {
        container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗</div>';
    }
}

// ─── Add User (styled modal) ─── //
window._addUserPrompt = async function() {
    const moduleGroups = _PERM_GROUPS.map(g => ({
        label: g.label,
        options: g.modules.map(m => ({ value: m, label: MODULE_LABELS[m] || m, checked: false })),
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
            { type: 'section', label: '管理身分' },
            { key: 'is_admin', type: 'checkboxes', options: [{ value: 'admin', label: '👑 管理員（完整權限：使用者 / 設定 / 發版）' }] },
            { type: 'section', label: '可用模組（非管理員才需勾選）' },
            { key: 'modules', type: 'checkboxes', groups: moduleGroups },
        ],
        onSubmit: async (vals, setError, close) => {
            if (!vals.username) { setError('請輸入帳號'); return; }
            if (!vals.password) { setError('請輸入密碼'); return; }
            if (vals.password !== vals.password2) { setError('兩次密碼不一致'); return; }
            if (vals.password.length < 3) { setError('密碼至少需要 3 個字元'); return; }
            const isAdmin = (vals.is_admin || []).includes('admin');
            const modules = isAdmin ? ALL_MODULES.slice() : (vals.modules || []);
            const access_level = isAdmin ? 3 : 1;
            try {
                const r = await fetch('/api/v1/auth/users', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: vals.username, password: vals.password, modules, access_level }),
                });
                const d = await r.json();
                if (!r.ok) { setError(d.detail || '新增失敗'); return; }
                close();
                _loadUserList();
            } catch (_) { setError('連線失敗，請稍後再試'); }
        },
    });
};

// ─── Per-user permission editing (group master + admin toggle) ─── //
window._toggleUserGroup = function(username, groupId, checked) {
    document.querySelectorAll(`input[data-umod-user="${username}"][data-group="${groupId}"]`)
        .forEach(cb => { cb.checked = checked; });
    window._syncUserGroupMaster(username, groupId);
};

window._syncUserGroupMaster = function(username, groupId) {
    const boxes = [...document.querySelectorAll(`input[data-umod-user="${username}"][data-group="${groupId}"]`)];
    const checked = boxes.filter(b => b.checked).length;
    const master = document.querySelector(`input[data-umaster-user="${username}"][data-group="${groupId}"]`);
    if (master) {
        master.checked = checked === boxes.length && boxes.length > 0;
        master.indeterminate = checked > 0 && checked < boxes.length;
    }
    const count = document.querySelector(`span[data-ucount-user="${username}"][data-group="${groupId}"]`);
    if (count) count.textContent = `${checked}/${boxes.length}`;
};

// 管理員 on = full access (modules implied) → dim the module grid. off = re-enable.
window._onUserAdminToggle = function(username, checked) {
    const perm = document.querySelector(`[data-uperm-user="${username}"]`);
    if (!perm) return;
    perm.style.opacity = checked ? '0.45' : '1';
    perm.style.pointerEvents = checked ? 'none' : 'auto';
};

window._saveUserSettings = async function(username) {
    const adminEl = document.querySelector(`input[data-uadmin-user="${username}"]`);
    const isAdmin = !!(adminEl && adminEl.checked);
    const modules = isAdmin
        ? ALL_MODULES.slice()
        : [...document.querySelectorAll(`input[data-umod-user="${username}"]:checked`)].map(cb => cb.value);
    const access_level = isAdmin ? 3 : 1;
    try {
        const r = await fetch('/api/v1/auth/users/' + username, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ modules, access_level }),
        });
        if (r.ok) {
            const btns = document.querySelectorAll('#umgmt-list button');
            btns.forEach(b => {
                if (b.textContent.includes('儲存') && b.onclick?.toString().includes(username)) {
                    b.textContent = '✅ 已儲存'; b.style.background = '#22c55e';
                    setTimeout(() => { b.textContent = '儲存'; b.style.background = ''; }, 1500);
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
