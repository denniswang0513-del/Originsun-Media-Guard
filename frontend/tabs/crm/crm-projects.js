/**
 * crm-projects.js — CRM 專案管理子視圖
 * 功能：列表 + 詳情面板 + 新增/編輯 Modal + 狀態快切
 */

import { crmFetch as _fetch, esc as _esc, renderAvatar, populateUserSelect, populateClientSelect, setupResizeHandle } from './crm-utils.js';

// ── State ────────────────────────────────────────────────────

let _projects = [];
let _clients = [];
let _users = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', status: '', client_id: '', am: '' };

// ── Data Loading ─────────────────────────────────────────────

export async function loadProjects() {
    const params = new URLSearchParams();
    if (_filters.q)         params.set('q', _filters.q);
    if (_filters.status)    params.set('status', _filters.status);
    if (_filters.client_id) params.set('client_id', _filters.client_id);
    if (_filters.am)        params.set('am', _filters.am);

    try {
        const data = await _fetch(`/projects?${params}`);
        _projects = data.projects || [];
    } catch (e) {
        _projects = [];
        _showListError(e.message);
    }
    renderList();
}

async function loadClients() {
    try {
        const data = await _fetch('/clients');
        _clients = data.clients || [];
        _populateClientFilter();
    } catch (_) {
        _clients = [];
    }
}

async function loadUsers() {
    try {
        const data = await _fetch('/users');
        _users = data.users || [];
        _populateSelect('proj-filter-am', '全部 AM');
    } catch (_) {
        _users = [];
    }
}

// ── Rendering ────────────────────────────────────────────────

function _badge(status) {
    const s = status || '洽談中';
    const known = ['洽談中', '報價中', '進行中', '已結案'];
    const cls = known.includes(s) ? `crm-badge crm-proj-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

function _avatar(username, size = 22) {
    return renderAvatar(username, _users, size);
}

function renderList() {
    const body = document.getElementById('proj-list-body');
    if (!body) return;

    if (_projects.length === 0) {
        body.innerHTML = `<div class="crm-empty">找不到專案${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }

    body.innerHTML = _projects.map(p => `
        <div class="crm-row${p.id === _selectedId ? ' selected' : ''}" data-id="${p.id}" onclick="window._projSelect('${p.id}')">
            <div class="crm-row-name">${_esc(p.name)}</div>
            <div class="crm-row-client">${_esc(p.client_short_name)}</div>
            <div class="crm-row-status">${_badge(p.status)}</div>
            <div class="crm-row-am">
                ${p.am_username ? _avatar(p.am_username) + _esc(p.am_username) : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-date">${p.shoot_date ? p.shoot_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-actions" onclick="event.stopPropagation()">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projEdit('${p.id}')">編輯</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._projDelete('${p.id}')">刪除</button>
            </div>
        </div>
    `).join('');
}

function renderDetail(project) {
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(value)}</div>
        </div>`;
    };

    document.getElementById('proj-detail-title').textContent = project.name;

    // Tab 1: 專案資訊
    document.getElementById('proj-detail-info').innerHTML = `
        ${prop('客戶', project.client_short_name)}
        <div class="crm-detail-prop">
            <div class="crm-prop-label">狀態</div>
            <div class="crm-prop-value">${_badge(project.status)}</div>
        </div>
        ${prop('拍攝日期', project.shoot_date ? project.shoot_date.substring(0, 10) : '')}
        ${prop('資料夾', project.folder_path)}
        ${prop('說明', project.description)}
        ${prop('備註', project.notes)}
        ${prop('修改日期', project.updated_at ? project.updated_at.substring(0, 10) : '')}
    `;

    // Tab 2: 人員配置
    const amHtml = project.am_username
        ? `<div class="crm-am-row">${_avatar(project.am_username, 28)}<span>${_esc(project.am_username)}</span></div>`
        : '<span class="crm-prop-value empty">未指派</span>';

    const pmList = (project.pm_usernames || []);
    const pmHtml = pmList.length > 0
        ? pmList.map(u => `<div class="crm-am-row" style="margin-bottom:4px;">${_avatar(u, 24)}<span>${_esc(u)}</span></div>`).join('')
        : '<span class="crm-prop-value empty">未指派</span>';

    document.getElementById('proj-detail-team').innerHTML = `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">AM</div>
            <div class="crm-prop-value">${amHtml}</div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">PM</div>
            <div class="crm-prop-value">${pmHtml}</div>
        </div>
        <div style="margin-top:12px;border-top:1px solid #2e2e2e;padding-top:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:12px;font-weight:700;color:#6b7280;">派工人員</span>
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projAddStaff()">+ 新增派工</button>
            </div>
            <div id="proj-staff-list">載入中...</div>
        </div>
    `;
    _loadProjectStaff(project.id);
}

async function _loadProjectStaff(projectId) {
    const container = document.getElementById('proj-staff-list');
    if (!container) return;
    try {
        const data = await _fetch('/projects/' + projectId + '/staff');
        const staff = data.staff || [];
        if (staff.length === 0) {
            container.innerHTML = '<div class="crm-empty" style="padding:8px 0;">尚無派工</div>';
            return;
        }
        const totalCost = staff.reduce((s, r) => s + r.cost, 0);
        container.innerHTML = staff.map(r => `
            <div class="quote-item-row" style="padding:6px 0;">
                <span class="quote-item-desc">${_esc(r.staff_name)} <span class="crm-muted">${_esc(r.staff_role)}</span></span>
                <span class="quote-item-qty">${r.days}天</span>
                <span class="quote-item-price">$${(r.rate || 0).toLocaleString('zh-TW')}</span>
                <span class="quote-item-amount">$${r.cost.toLocaleString('zh-TW')}</span>
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:2px 6px;" onclick="window._projRemoveStaff('${r.id}','${projectId}')">&#x2715;</button>
            </div>
        `).join('') + `<div style="text-align:right;font-weight:700;padding:8px 0;color:#e0e0e0;">內部成本合計: $${totalCost.toLocaleString('zh-TW')}</div>`;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Helpers ──────────────────────────────────────────────────

function _populateSelect(elementId, placeholder) {
    populateUserSelect(elementId, _users, placeholder);
}

function _populateClientFilter() {
    populateClientSelect('proj-filter-client', _clients);
}

function _populateClientDropdown(elementId, selectedId) {
    const sel = document.getElementById(elementId);
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇客戶 —</option>` +
        _clients.map(c => `<option value="${c.id}"${c.id === selectedId ? ' selected' : ''}>${_esc(c.short_name)}</option>`).join('');
}

function _populatePmCheckboxes(selected = []) {
    const container = document.getElementById('proj-f-pm_usernames');
    if (!container) return;
    container.innerHTML = _users.map(u => `
        <label class="crm-checkbox-item">
            <input type="checkbox" value="${_esc(u.username)}" ${selected.includes(u.username) ? 'checked' : ''}>
            ${_avatar(u.username, 18)} ${_esc(u.username)}
        </label>
    `).join('');
}

function _getSelectedPms() {
    const container = document.getElementById('proj-f-pm_usernames');
    if (!container) return [];
    return Array.from(container.querySelectorAll('input:checked')).map(cb => cb.value);
}

function _showListError(msg) {
    const body = document.getElementById('proj-list-body');
    if (body) body.innerHTML = `<div class="crm-empty" style="color:#fca5a5;">${_esc(msg)}</div>`;
}

function _showModalError(msg) {
    const el = document.getElementById('proj-modal-error');
    el.textContent = msg;
    el.style.display = 'block';
}

// ── Detail Panel ─────────────────────────────────────────────

function selectProject(id) {
    _selectedId = id;
    renderList();

    const panel = document.getElementById('proj-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('proj-resize-handle');
    if (handle) handle.style.display = '';

    const project = _projects.find(p => p.id === id);
    if (!project) return;
    renderDetail(project);
    _loadProjectQuotations(id);
}

async function _loadProjectQuotations(projectId) {
    const container = document.getElementById('proj-detail-quotes');
    if (!container) return;
    try {
        const data = await _fetch(`/projects/${projectId}/quotations`);
        const quotes = data.quotations || [];
        let html = '';
        if (quotes.length > 0) {
            html = quotes.map(q => {
                const price = q.final_price !== null && q.final_price !== undefined ? q.final_price : q.total;
                const statusCls = ['草稿','已寄送','已簽核','已拒絕'].includes(q.status) ? `crm-badge crm-quote-badge-${q.status}` : 'crm-badge';
                return `<div class="quote-item-row" style="padding:8px 0;">
                    <span class="quote-item-desc">v${q.version}</span>
                    <span><span class="${statusCls}">${_esc(q.status)}</span></span>
                    <span class="quote-item-amount">$${(price || 0).toLocaleString('zh-TW')}</span>
                    <span class="crm-muted">${q.quote_date ? q.quote_date.substring(0, 10) : ''}</span>
                </div>`;
            }).join('');
        } else {
            html = '<div class="crm-empty" style="padding:16px 0;">尚無報價</div>';
        }
        html += `<div style="padding:8px 0;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projAddQuote()">+ 新增報價</button></div>`;
        container.innerHTML = html;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

function closeDetail() {
    _selectedId = null;
    const panel = document.getElementById('proj-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('proj-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Add / Edit Modal ─────────────────────────────────────────

const _FIELDS = ['name', 'client_id', 'status', 'shoot_date', 'folder_path',
    'description', 'am_username', 'notes'];

function openModal(project = null) {
    _editingId = project ? project.id : null;
    document.getElementById('proj-modal-title').textContent = project ? '編輯專案' : '新增專案';
    const errEl = document.getElementById('proj-modal-error');
    errEl.textContent = '';
    errEl.style.display = 'none';

    _populateClientDropdown('proj-f-client_id', project ? project.client_id : '');
    _populateSelect('proj-f-am_username', '— 未指派 —');
    _populatePmCheckboxes(project ? (project.pm_usernames || []) : []);

    for (const f of _FIELDS) {
        const el = document.getElementById(`proj-f-${f}`);
        if (!el) continue;
        if (f === 'shoot_date' && project?.shoot_date) {
            el.value = project.shoot_date.substring(0, 10);
        } else {
            el.value = project ? (project[f] ?? '') : '';
        }
    }

    document.getElementById('proj-modal').style.display = 'flex';
    document.getElementById('proj-f-name').focus();
}

async function saveProject() {
    const name = document.getElementById('proj-f-name').value.trim();
    const client_id = document.getElementById('proj-f-client_id').value;
    if (!name) { _showModalError('專案名稱為必填欄位'); return; }
    if (!client_id) { _showModalError('請選擇客戶'); return; }

    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById(`proj-f-${f}`);
        payload[f] = el ? el.value.trim() : '';
    }
    payload.pm_usernames = _getSelectedPms();
    payload.shoot_date = payload.shoot_date || null;

    const btn = document.getElementById('proj-btn-save');
    btn.disabled = true;
    btn.textContent = '儲存中...';

    try {
        const resp = _editingId
            ? await _fetch(`/projects/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/projects', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('proj-modal').style.display = 'none';

        if (resp.project) {
            const idx = _projects.findIndex(p => p.id === resp.project.id);
            if (idx >= 0) _projects[idx] = resp.project;
            else _projects.unshift(resp.project);
            renderList();
            if (_editingId) selectProject(_editingId);
        } else {
            await loadProjects();
        }
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '儲存';
    }
}

// ── Delete ───────────────────────────────────────────────────

async function deleteProject(project) {
    if (!confirm(`確定刪除「${project.name}」？此操作無法復原。`)) return;
    try {
        await _fetch(`/projects/${project.id}`, { method: 'DELETE' });
        closeDetail();
        await loadProjects();
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

// ── CSV Import ───────────────────────────────────────────────

let _csvFile = null;

function openImportModal() {
    _csvFile = null;
    document.getElementById('proj-drop-filename').textContent = '';
    const result = document.getElementById('proj-import-result');
    result.style.display = 'none';
    result.className = 'crm-import-result';
    document.getElementById('proj-btn-do-import').disabled = true;
    document.getElementById('proj-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('proj-drop-filename').textContent = file ? file.name : '';
    document.getElementById('proj-btn-do-import').disabled = !file;
}

async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('proj-btn-do-import');
    btn.disabled = true;
    btn.textContent = '匯入中...';

    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const form = new FormData();
        form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/projects/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || '匯入失敗');
        }
        const data = await res.json();
        const result = document.getElementById('proj-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> 筆 ／ 更新：<strong>${data.updated}</strong> 筆 ／ 跳過：<strong>${data.skipped}</strong> 筆`;
        result.style.display = 'block';
        await loadProjects();
    } catch (e) {
        const result = document.getElementById('proj-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '開始匯入';
    }
}

// ── Init ─────────────────────────────────────────────────────

export function initCrmProjectsTab() {
    // Move modals to body
    for (const id of ['proj-modal', 'proj-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    // Global handlers for onclick
    window._projSelect = selectProject;
    window._projEdit = (id) => {
        const p = _projects.find(x => x.id === id);
        if (p) openModal(p);
    };
    window._projDelete = (id) => {
        const p = _projects.find(x => x.id === id);
        if (p) deleteProject(p);
    };
    window._projRefreshQuotes = (projectId) => {
        if (_selectedId === projectId) _loadProjectQuotations(projectId);
    };
    window._projAddStaff = async () => {
        if (!_selectedId) return;
        let staffList = [];
        try { staffList = (await _fetch('/staff?status=在職')).staff || []; } catch(_) {}
        if (staffList.length === 0) { alert('請先在人力資源 Tab 新增人員'); return; }
        const container = document.getElementById('proj-staff-list');
        if (!container) return;
        // Show inline form at top
        const formId = 'proj-staff-add-form';
        if (document.getElementById(formId)) return; // already showing
        const formHtml = `<div id="${formId}" style="padding:8px;background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;margin-bottom:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
            <select id="proj-staff-sel" class="crm-input" style="flex:1;min-width:120px;">
                <option value="">— 選擇人員 —</option>
                ${staffList.map(s => `<option value="${s.id}" data-role="${_esc(s.role)}">${_esc(s.name)} (${_esc(s.role)} $${s.daily_rate}/天)</option>`).join('')}
            </select>
            <input id="proj-staff-days" type="number" class="crm-input" value="1" min="1" style="width:60px;text-align:right;" placeholder="天數">
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projConfirmStaff()">確定</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('${formId}').remove()">取消</button>
        </div>`;
        container.insertAdjacentHTML('afterbegin', formHtml);
    };
    window._projConfirmStaff = async () => {
        const sel = document.getElementById('proj-staff-sel');
        const staffId = sel?.value;
        if (!staffId) { alert('請選擇人員'); return; }
        const role = sel.selectedOptions[0]?.dataset.role || '';
        const days = parseInt(document.getElementById('proj-staff-days')?.value) || 1;
        try {
            await _fetch('/projects/' + _selectedId + '/staff', {
                method: 'POST', body: JSON.stringify({ staff_id: staffId, role_in_project: role, days })
            });
            _loadProjectStaff(_selectedId);
        } catch (e) { alert('新增失敗：' + e.message); }
    };
    window._projRemoveStaff = async (psId, projectId) => {
        if (!confirm('確定移除此派工？')) return;
        try {
            await _fetch('/project-staff/' + psId, { method: 'DELETE' });
            _loadProjectStaff(projectId);
        } catch (e) { alert(e.message); }
    };
    window._projAddQuote = () => {
        if (!_selectedId) return;
        if (window._openQuoteModalForProject) {
            window._openQuoteModalForProject(_selectedId);
        }
    };

    // Search + filters
    let _searchTimer;
    document.getElementById('proj-search').addEventListener('input', e => {
        _filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadProjects, 300);
    });
    document.getElementById('proj-filter-status').addEventListener('change', e => {
        _filters.status = e.target.value;
        loadProjects();
    });
    document.getElementById('proj-filter-client').addEventListener('change', e => {
        _filters.client_id = e.target.value;
        loadProjects();
    });
    document.getElementById('proj-filter-am').addEventListener('change', e => {
        _filters.am = e.target.value;
        loadProjects();
    });

    // Buttons
    document.getElementById('proj-btn-add').addEventListener('click', () => openModal());
    document.getElementById('proj-btn-import').addEventListener('click', openImportModal);
    document.getElementById('proj-btn-save').addEventListener('click', saveProject);
    document.getElementById('proj-detail-close').addEventListener('click', closeDetail);
    document.getElementById('proj-btn-do-import').addEventListener('click', doImport);

    // CSV file input + drop zone
    document.getElementById('proj-csv-file').addEventListener('change', e => {
        _setCsvFile(e.target.files[0] || null);
    });
    const zone = document.getElementById('proj-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) _setCsvFile(file);
    });

    // Detail sub-tabs
    document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('proj-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('proj-detail-team').classList.toggle('hidden', tab !== 'team');
            document.getElementById('proj-detail-quotes').classList.toggle('hidden', tab !== 'quotes');
        });
    });

    // Modal overlay click to close
    for (const id of ['proj-modal', 'proj-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('proj-resize-handle', 'proj-detail-panel');

    Promise.all([loadClients(), loadUsers(), loadProjects()]);
}
