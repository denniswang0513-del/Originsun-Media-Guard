// ─── Role Management Modal (RBAC) (extracted from app.js) ─── //
import { _ensureModalStyles, _createFormModal } from '../shared/modal-styles.js';
import { _fetchRoles, ALL_MODULES, MODULE_LABELS } from './user-mgmt.js';

window._openRoleMgmt = async function() {
    _ensureModalStyles();
    document.getElementById('role-mgmt-modal')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'role-mgmt-modal';
    overlay.className = '_fm-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.addEventListener('keydown', function _esc(e) { if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', _esc); } });

    const modal = document.createElement('div');
    modal.className = '_fm-modal';
    modal.style.width = '820px'; modal.style.maxWidth = '90%';
    modal.innerHTML = `
        <div class="_fm-header">
            <h3>角色管理 (RBAC)</h3>
            <div style="display:flex;gap:10px;align-items:center;">
                <button id="rmgmt-add-btn" class="_fm-btn-submit" style="padding:5px 16px;font-size:12px;">+ 新增角色</button>
                <span class="_fm-close" onclick="document.getElementById('role-mgmt-modal')?.remove()">✕</span>
            </div>
        </div>
        <div class="_fm-body" style="padding:16px 24px;">
            <div id="rmgmt-list" style="font-size:12px;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    document.getElementById('rmgmt-add-btn').onclick = () => _addRolePrompt();
    await _loadRoleList();
};

async function _loadRoleList() {
    const container = document.getElementById('rmgmt-list');
    if (!container) return;
    try {
        const roles = await _fetchRoles();
        container.innerHTML = roles.map(r => {
            const moduleCheckboxes = ALL_MODULES.map(m =>
                `<label class="_fm-chk" style="min-width:auto;padding:3px 6px;">
                    <input type="checkbox" ${(r.modules||[]).includes(m)?'checked':''} data-role-id="${r.id}" data-module="${m}"> ${MODULE_LABELS[m]||m}
                </label>`
            ).join('');
            const isAdmin = r.name === 'admin';
            const lvColors = ['#666','#3b82f6','#d48a04','#a855f7'];
            const lvColor = lvColors[r.access_level] || '#666';
            return `
            <div style="padding:14px 16px;margin-bottom:6px;background:#1e1e1e;border:1px solid #2e2e2e;border-radius:8px;transition:border-color .15s;" onmouseenter="this.style.borderColor='#444'" onmouseleave="this.style.borderColor='#2e2e2e'">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
                    <input data-role-name="${r.id}" value="${r.name}" class="_fm-input" style="width:110px;padding:5px 10px;font-size:12px;font-weight:600;" ${isAdmin?'readonly style="width:110px;padding:5px 10px;font-size:12px;font-weight:600;opacity:0.6;cursor:not-allowed;"':''}>
                    <span style="display:inline-block;background:${lvColor}22;color:${lvColor};font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;letter-spacing:0.3px;">Lv${r.access_level}</span>
                    <select data-role-level="${r.id}" class="_fm-select" style="padding:4px 8px;font-size:11px;width:auto;">
                        <option value="0" ${r.access_level===0?'selected':''}>Lv0 唯讀</option>
                        <option value="1" ${r.access_level===1?'selected':''}>Lv1 操作</option>
                        <option value="2" ${r.access_level===2?'selected':''}>Lv2 管理</option>
                        <option value="3" ${r.access_level===3?'selected':''}>Lv3 超級管理</option>
                    </select>
                    <input data-role-desc="${r.id}" value="${r.description||''}" placeholder="描述..." class="_fm-input" style="flex:1;min-width:80px;padding:5px 10px;font-size:11px;color:#888;">
                    <div style="display:flex;gap:6px;margin-left:auto;">
                        <button onclick="window._saveRole(${r.id})" class="_fm-btn-submit" style="padding:3px 14px;font-size:11px;font-weight:500;">儲存</button>
                        ${!isAdmin ? `<button onclick="window._deleteRole(${r.id},'${r.name}')" style="background:transparent;border:1px solid rgba(239,68,68,0.3);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;transition:all .15s;" onmouseenter="this.style.borderColor='#ef4444';this.style.background='rgba(239,68,68,0.08)'" onmouseleave="this.style.borderColor='rgba(239,68,68,0.3)';this.style.background='transparent'">刪除</button>` : ''}
                    </div>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:2px;">${moduleCheckboxes}</div>
            </div>`;
        }).join('');
    } catch (_) {
        container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗</div>';
    }
}

async function _addRolePrompt() {
    const moduleOptions = ALL_MODULES.map(m => ({
        value: m, label: MODULE_LABELS[m] || m, checked: false,
    }));
    _createFormModal({
        id: 'add-role-modal',
        title: '新增角色',
        submitLabel: '建立角色',
        fields: [
            { type: 'section', label: '基本資訊' },
            { key: 'name', label: '角色名稱', type: 'text', required: true, autofocus: true, placeholder: '英文名稱，例如 intern' },
            { key: 'access_level', label: '權限等級', type: 'select', options: [
                { value: '0', label: 'Lv0 唯讀' },
                { value: '1', label: 'Lv1 操作' },
                { value: '2', label: 'Lv2 管理' },
                { value: '3', label: 'Lv3 超級管理' },
            ], defaultValue: '1' },
            { key: 'description', label: '描述', type: 'text', placeholder: '角色用途說明（選填）' },
            { type: 'divider' },
            { type: 'section', label: '功能權限' },
            { key: 'modules', label: '可用模組', type: 'checkboxes', options: moduleOptions },
        ],
        onSubmit: async (vals, setError, close) => {
            if (!vals.name) { setError('請輸入角色名稱'); return; }
            if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(vals.name)) { setError('角色名稱只能包含英文字母、數字和底線'); return; }
            try {
                const r = await fetch('/api/v1/roles', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: vals.name,
                        access_level: parseInt(vals.access_level),
                        modules: vals.modules || [],
                        description: vals.description || '',
                    }),
                });
                const d = await r.json();
                if (!r.ok) { setError(d.detail || '新增失敗'); return; }
                close();
                _loadRoleList();
            } catch (_) { setError('連線失敗，請稍後再試'); }
        },
    });
}

window._saveRole = async function(roleId) {
    const nameEl = document.querySelector(`input[data-role-name="${roleId}"]`);
    const levelEl = document.querySelector(`select[data-role-level="${roleId}"]`);
    const descEl = document.querySelector(`input[data-role-desc="${roleId}"]`);
    const moduleCbs = document.querySelectorAll(`input[data-role-id="${roleId}"][data-module]`);
    const modules = [];
    moduleCbs.forEach(cb => { if (cb.checked) modules.push(cb.dataset.module); });
    try {
        const r = await fetch('/api/v1/roles/' + roleId, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: nameEl?.value || '',
                access_level: parseInt(levelEl?.value || '1'),
                modules,
                description: descEl?.value || '',
            }),
        });
        if (r.ok) {
            const btn = document.querySelector(`button[onclick*="_saveRole(${roleId})"]`);
            if (btn) { btn.textContent = '✅ 已儲存'; btn.style.background = '#22c55e'; setTimeout(() => { btn.textContent = '💾 儲存'; btn.style.background = '#3b82f6'; }, 1500); }
        } else {
            const d = await r.json();
            alert(d.detail || '儲存失敗');
        }
    } catch (_) { alert('連線失敗'); }
};

window._deleteRole = async function(roleId, roleName) {
    if (!confirm(`確定要刪除角色 "${roleName}"？`)) return;
    try {
        const r = await fetch('/api/v1/roles/' + roleId, { method: 'DELETE' });
        if (r.ok) { _loadRoleList(); } else {
            const d = await r.json();
            alert(d.detail || '刪除失敗');
        }
    } catch (_) { alert('連線失敗'); }
};
