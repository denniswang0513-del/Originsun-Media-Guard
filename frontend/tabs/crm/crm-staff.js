/**
 * crm-staff.js — 人力資源 Tab
 */

import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

let _staff = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', role: '', status: '' };
let _csvFile = null;
const _DEFAULT_ROLES = ['攝影師','剪輯師','導演','製片','燈光','收音','空拍','動畫'];
let _roles = [..._DEFAULT_ROLES];

// ── Data ─────────────────────────────────────────────────────

async function loadStaff() {
    const params = new URLSearchParams();
    if (_filters.q)      params.set('q', _filters.q);
    if (_filters.role)   params.set('role', _filters.role);
    if (_filters.status) params.set('status', _filters.status);
    try {
        const data = await _fetch(`/staff?${params}`);
        _staff = data.staff || [];
    } catch (_) { _staff = []; }
    renderList();
}

// ── Rendering ────────────────────────────────────────────────

const _STATUS_CLS = { '在職': 'crm-staff-badge-在職', '兼職': 'crm-staff-badge-兼職', '專案': 'crm-staff-badge-專案', '單位': 'crm-staff-badge-單位' };

function _sBadge(status) {
    const s = status || '在職';
    const cls = _STATUS_CLS[s] || '';
    return `<span class="crm-badge ${cls}">${_esc(s)}</span>`;
}

const _STATUS_ORDER = { '在職': 0, '兼職': 1, '單位': 2, '專案': 3 };

function renderList() {
    const body = document.getElementById('staff-list-body');
    if (!body) return;
    if (_staff.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無人員${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    const sorted = [..._staff].sort((a, b) => {
        const sa = _STATUS_ORDER[a.status] ?? 9;
        const sb = _STATUS_ORDER[b.status] ?? 9;
        return sa !== sb ? sa - sb : (a.name || '').localeCompare(b.name || '', 'zh-TW');
    });
    body.innerHTML = sorted.map(s => `
        <div class="crm-row${s.id === _selectedId ? ' selected' : ''}" onclick="window._staffSelect('${s.id}')">
            <div class="crm-row-name">${_esc(s.name)}</div>
            <div class="crm-row-role">${_esc(s.role)}</div>
            <div class="crm-row-status">${_sBadge(s.status)}</div>
            <div class="crm-row-phone">${_esc(s.phone)}</div>
            ${kebabMenuHtml(s.id, { onEdit: '_staffEdit', onDuplicate: '_staffDup', onDelete: '_staffDelete' })}
        </div>
    `).join('');
}

const _STAFF_EDIT_FIELDS = [
    {name:'name', label:'姓名', type:'text'},
    {name:'role', label:'職能', type:'select', get options() { return [{value:'',label:'—'}, ..._roles.map(r => ({value:r,label:r}))]; }},
    {name:'status', label:'狀態', type:'select', options:[{value:'在職',label:'在職'},{value:'兼職',label:'兼職'},{value:'專案',label:'專案'},{value:'單位',label:'單位'}]},
    {name:'phone', label:'電話', type:'text'},
    {name:'email', label:'Email', type:'text'},
    {name:'id_number', label:'身分證 / 統編', type:'text'},
    {name:'address', label:'住址', type:'text'},
    {name:'bank_name', label:'銀行', type:'text'},
    {name:'bank_account', label:'帳號', type:'text'},
    {name:'portfolio_url', label:'作品集', type:'text'},
    {name:'notes', label:'備註', type:'text'},
];

function renderDetail(s) {
    document.getElementById('staff-detail-title').textContent = s.name;
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `<div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(String(value))}</div>
        </div>`;
    };

    document.getElementById('staff-detail-info').innerHTML = `
        ${prop('職能', s.role)}
        <div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value">${_sBadge(s.status)}</div></div>
        ${prop('電話', s.phone)}
        ${prop('Email', s.email)}
        ${prop('身分證 / 統編', s.id_number)}
        ${prop('住址', s.address)}
        ${prop('銀行', s.bank_name ? s.bank_name + ' ' + (s.bank_account || '') : '')}
        ${s.portfolio_url ? `<div class="crm-detail-prop"><div class="crm-prop-label">作品集</div><div class="crm-prop-value"><a href="${_esc(s.portfolio_url)}" target="_blank" style="color:#3b82f6;">${_esc(s.portfolio_url)}</a></div></div>` : ''}
        ${prop('備註', s.notes)}
    `;

    const actions = document.getElementById('staff-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('staff-bar-actions', () => {
        enableInlineEdit('staff-detail-info', 'staff-bar-actions', _STAFF_EDIT_FIELDS, s,
            async (payload) => {
                await _fetch('/staff/' + s.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/staff/' + s.id);
                renderDetail(updated);
                await loadStaff();
            },
            () => renderDetail(s)
        );
        // Inject ✎ button next to role select in inline edit (same row)
        const roleSelect = document.querySelector('#staff-detail-info [data-field="role"]');
        if (roleSelect) {
            const wrapper = roleSelect.parentNode;
            wrapper.style.cssText = 'display:flex;align-items:center;gap:6px;';
            roleSelect.style.flex = '1';
            const btn = document.createElement('button');
            btn.className = 'crm-btn crm-btn-secondary crm-btn-sm';
            btn.style.cssText = 'padding:4px 8px;flex-shrink:0;';
            btn.textContent = '✎';
            btn.title = '編輯職能選項';
            btn.onclick = (e) => { e.stopPropagation(); window._staffEditRoles(); };
            wrapper.appendChild(btn);
        }
    });

    _loadStaffProjects(s.id);
}

async function _loadStaffProjects(staffId) {
    const container = document.getElementById('staff-detail-projects');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty" style="padding:8px;">載入中...</div>';
    try {
        const data = await _fetch('/staff/' + staffId + '/projects');
        const projects = data.projects || [];
        if (projects.length === 0) {
            container.innerHTML = '<div class="crm-empty" style="padding:12px 0;">尚無專案紀錄</div>';
            return;
        }
        const totalEarned = projects.reduce((s, p) => s + p.cost, 0);
        container.innerHTML = projects.map(p => `
            <div class="quote-item-row" style="padding:8px 0;">
                <span class="quote-item-desc">${_esc(p.project_name)} <span class="crm-muted">${_esc(p.client_name)}</span></span>
                <span class="quote-item-qty">${_esc(p.role_in_project)}</span>
                <span class="quote-item-price">${p.days}天</span>
                <span class="quote-item-amount">$${_fmtNum(p.cost)}</span>
            </div>
        `).join('') + `<div style="text-align:right;font-weight:700;padding:8px 0;color:#e0e0e0;">累計費用: $${_fmtNum(totalEarned)}</div>`;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Detail ───────────────────────────────────────────────────

function selectStaff(id) {
    _selectedId = id;
    renderList();
    const panel = document.getElementById('staff-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('staff-resize-handle');
    if (handle) handle.style.display = '';
    const s = _staff.find(x => x.id === id);
    if (s) renderDetail(s);
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('staff-detail-panel').style.display = 'none';
    document.getElementById('staff-resize-handle').style.display = 'none';
    renderList();
}

// ── Modal ────────────────────────────────────────────────────

const _FIELDS = ['name', 'role', 'phone', 'email',
    'status', 'portfolio_url', 'id_number', 'address', 'bank_name', 'bank_account', 'notes'];

function openModal(staff = null) {
    _editingId = staff ? staff.id : null;
    document.getElementById('staff-modal-title').textContent = staff ? '編輯人員' : '新增人員';
    const errEl = document.getElementById('staff-modal-error');
    errEl.textContent = ''; errEl.style.display = 'none';

    for (const f of _FIELDS) {
        const el = document.getElementById(`staff-f-${f}`);
        if (el) el.value = staff ? (staff[f] ?? '') : (f === 'status' ? '在職' : '');
    }
    document.getElementById('staff-modal').style.display = 'flex';
    document.getElementById('staff-f-name').focus();
}

async function saveStaff() {
    const name = document.getElementById('staff-f-name').value.trim();
    if (!name) { _showModalError('姓名為必填'); return; }

    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById(`staff-f-${f}`);
        let val = el ? el.value.trim() : '';
        payload[f] = val;
    }

    const btn = document.getElementById('staff-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        const resp = _editingId
            ? await _fetch(`/staff/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/staff', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('staff-modal').style.display = 'none';
        if (resp.staff) {
            const idx = _staff.findIndex(s => s.id === resp.staff.id);
            if (idx >= 0) _staff[idx] = resp.staff;
            else _staff.unshift(resp.staff);
            renderList();
            if (_editingId) selectStaff(_editingId);
        } else {
            await loadStaff();
        }
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false; btn.textContent = '儲存';
    }
}

async function deleteStaff(s) {
    if (!confirm(`確定刪除「${s.name}」？`)) return;
    try {
        await _fetch(`/staff/${s.id}`, { method: 'DELETE' });
        closeDetail();
        await loadStaff();
    } catch (e) { alert('刪除失敗：' + e.message); }
}

function _showModalError(msg) {
    const el = document.getElementById('staff-modal-error');
    el.textContent = msg; el.style.display = 'block';
}

// ── CSV Import ───────────────────────────────────────────────

function openImportModal() {
    _csvFile = null;
    document.getElementById('staff-drop-filename').textContent = '';
    const r = document.getElementById('staff-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('staff-btn-do-import').disabled = true;
    document.getElementById('staff-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('staff-drop-filename').textContent = file ? file.name : '';
    document.getElementById('staff-btn-do-import').disabled = !file;
}

async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('staff-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const form = new FormData();
        form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/staff/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '匯入失敗'); }
        const data = await res.json();
        const result = document.getElementById('staff-import-result');
        result.className = 'crm-import-result';
        let msg = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 更新：<strong>${data.updated}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        if (data.hint) msg += `<br><span style="color:#fbbf24;font-size:12px;">${_esc(data.hint)}</span>`;
        result.innerHTML = msg;
        result.style.display = 'block';
        await loadStaff();
    } catch (e) {
        const result = document.getElementById('staff-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally {
        btn.disabled = false; btn.textContent = '開始匯入';
    }
}

// ── Role Management ─────────────────────────────────────────

async function _loadRoles() {
    try {
        const s = await fetch('/api/settings/load').then(r => r.json());
        _roles = s.staff_roles && s.staff_roles.length > 0 ? s.staff_roles : [..._DEFAULT_ROLES];
    } catch (_) { _roles = [..._DEFAULT_ROLES]; }
    _populateRoleSelects();
}

function _populateRoleSelects() {
    const filter = document.getElementById('staff-filter-role');
    if (filter) {
        const val = filter.value;
        filter.innerHTML = '<option value="">全部職能</option>' + _roles.map(r => `<option value="${_esc(r)}">${_esc(r)}</option>`).join('');
        filter.value = val;
    }
    const modal = document.getElementById('staff-f-role');
    if (modal) {
        const val = modal.value;
        modal.innerHTML = '<option value="">—</option>' + _roles.map(r => `<option value="${_esc(r)}">${_esc(r)}</option>`).join('');
        modal.value = val;
    }
    // Also refresh inline edit select if open
    const inline = document.querySelector('#staff-detail-info [data-field="role"]');
    if (inline) {
        const val = inline.value;
        inline.innerHTML = '<option value="">—</option>' + _roles.map(r => `<option value="${_esc(r)}">${_esc(r)}</option>`).join('');
        inline.value = val;
    }
}

async function _saveRoles() {
    const token = localStorage.getItem('auth_token');
    await fetch('/api/settings/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}) },
        body: JSON.stringify({ staff_roles: _roles })
    });
}

window._staffEditRoles = function() {
    let overlay = document.getElementById('staff-roles-overlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'staff-roles-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) _closeRolesModal(); });

    function _render() {
        overlay.innerHTML = `
          <div class="crm-modal" style="max-width:360px;">
            <div class="crm-modal-header">
              <h3>編輯職能選項</h3>
              <button onclick="document.getElementById('staff-roles-overlay').remove()" class="crm-detail-close">✕</button>
            </div>
            <div class="crm-modal-body" style="max-height:400px;overflow-y:auto;">
              ${_roles.map((r, i) => `<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid #2a2a2a;">
                <span style="flex:1;font-size:14px;">${_esc(r)}</span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._staffRenameRole(${i})" style="padding:2px 6px;">✎</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._staffRemoveRole(${i})" style="padding:2px 6px;">✕</button>
              </div>`).join('')}
              <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._staffAddRole()" style="margin-top:8px;width:100%;">+ 新增職能</button>
            </div>
          </div>`;
    }

    window._staffAddRole = async () => {
        const name = prompt('輸入新職能名稱：');
        if (!name || !name.trim()) return;
        const n = name.trim();
        if (_roles.includes(n)) { alert('已存在'); return; }
        _roles.push(n);
        await _saveRoles();
        _populateRoleSelects();
        _render();
    };
    window._staffRenameRole = async (i) => {
        const old = _roles[i];
        const name = prompt('修改職能名稱：', old);
        if (!name || !name.trim() || name.trim() === old) return;
        _roles[i] = name.trim();
        await _saveRoles();
        _populateRoleSelects();
        _render();
    };
    window._staffRemoveRole = async (i) => {
        if (!confirm(`確定刪除「${_roles[i]}」？已有此職能的人員不受影響。`)) return;
        _roles.splice(i, 1);
        await _saveRoles();
        _populateRoleSelects();
        _render();
    };

    function _closeRolesModal() { overlay.remove(); }
    _render();
    document.body.appendChild(overlay);
};

// ── Init ─────────────────────────────────────────────────────

export async function initCrmStaffTab() {
    for (const id of ['staff-modal', 'staff-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._staffSelect = selectStaff;
    window._staffEdit = (id) => { const s = _staff.find(x => x.id === id); if (s) openModal(s); };
    window._staffDelete = (id) => { const s = _staff.find(x => x.id === id); if (s) deleteStaff(s); };
    window._staffDup = (id) => {
        const s = _staff.find(x => x.id === id);
        if (s) { openModal(s); _editingId = null; document.getElementById('staff-modal-title').textContent = '複製人員'; }
    };

    let _t;
    document.getElementById('staff-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadStaff, 300);
    });
    document.getElementById('staff-filter-role').addEventListener('change', e => { _filters.role = e.target.value; loadStaff(); });
    document.getElementById('staff-filter-status').addEventListener('change', e => { _filters.status = e.target.value; loadStaff(); });

    document.getElementById('staff-btn-add').addEventListener('click', () => openModal());
    document.getElementById('staff-btn-import').addEventListener('click', openImportModal);
    document.getElementById('staff-btn-save').addEventListener('click', saveStaff);
    document.getElementById('staff-detail-close').addEventListener('click', closeDetail);
    document.getElementById('staff-btn-do-import').addEventListener('click', doImport);

    document.getElementById('staff-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('staff-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) _setCsvFile(file);
    });

    document.querySelectorAll('#staff-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#staff-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('staff-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('staff-detail-projects').classList.toggle('hidden', tab !== 'projects');
        });
    });

    for (const id of ['staff-modal', 'staff-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('staff-resize-handle', 'staff-detail-panel');
    await _loadRoles();
    loadStaff();
}
