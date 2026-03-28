/**
 * crm-projects.js — CRM 專案管理子視圖
 * 功能：列表 + 詳情面板 + 新增/編輯 Modal + 狀態快切
 */

import { crmFetch as _fetch, esc as _esc, renderAvatar, populateUserSelect, setupResizeHandle } from './crm-utils.js';

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
    const known = ['洽談中', '進行中', '已結案'];
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
    `;
}

// ── Helpers ──────────────────────────────────────────────────

function _populateSelect(elementId, placeholder) {
    populateUserSelect(elementId, _users, placeholder);
}

function _populateClientFilter() {
    const sel = document.getElementById('proj-filter-client');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">全部客戶</option>` +
        _clients.map(c => `<option value="${c.id}"${c.id === current ? ' selected' : ''}>${_esc(c.short_name)}</option>`).join('');
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

// ── Init ─────────────────────────────────────────────────────

export function initCrmProjectsTab() {
    // Move modal to body
    const modal = document.getElementById('proj-modal');
    if (modal) document.body.appendChild(modal);

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
    document.getElementById('proj-btn-save').addEventListener('click', saveProject);
    document.getElementById('proj-detail-close').addEventListener('click', closeDetail);

    // Detail sub-tabs
    document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('proj-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('proj-detail-team').classList.toggle('hidden', tab !== 'team');
        });
    });

    // Modal overlay click to close
    const modalEl = document.getElementById('proj-modal');
    if (modalEl) modalEl.addEventListener('click', e => { if (e.target === modalEl) modalEl.style.display = 'none'; });

    setupResizeHandle('proj-resize-handle', 'proj-detail-panel');

    Promise.all([loadClients(), loadUsers(), loadProjects()]);
}
