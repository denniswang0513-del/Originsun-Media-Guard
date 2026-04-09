/**
 * crm-projects-core.js — 列表 + CRUD Modal + CSV 匯入
 */
import { crmFetch as _fetch, crmCacheFetch, crmCacheInvalidate, esc as _esc, renderAvatar, populateUserSelect, populateClientSelect, searchableSelect, saveSettings, kebabMenuHtml } from './crm-utils.js';
import { state, callbacks } from './crm-projects-state.js';

// ── Project Types (dynamic from settings) ─────────────────
const _DEFAULT_TYPES = ['紀實影片', '活動紀實', '形象影片', '廣告', 'MV'];
let _projectTypes = [..._DEFAULT_TYPES];

export async function loadProjectTypes() {
    try {
        const s = await fetch('/api/settings/load').then(r => r.json());
        _projectTypes = s.project_types && s.project_types.length > 0 ? s.project_types : [..._DEFAULT_TYPES];
    } catch (_) { _projectTypes = [..._DEFAULT_TYPES]; }
    _populateTypeSelects();
}

export function getProjectTypes() { return _projectTypes; }

function _populateTypeSelects() {
    const sel = document.getElementById('proj-f-project_type');
    if (sel) {
        const val = sel.value;
        sel.innerHTML = '<option value="">—</option>' + _projectTypes.map(t => `<option value="${_esc(t)}">${_esc(t)}</option>`).join('');
        sel.value = val;
    }
}

async function _saveTypes() {
    await saveSettings({ project_types: _projectTypes });
}

window._projEditTypes = function() {
    let overlay = document.getElementById('proj-types-overlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'proj-types-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    function _render() {
        overlay.innerHTML = `
          <div class="crm-modal" style="max-width:360px;">
            <div class="crm-modal-header"><h3>編輯案件類型</h3>
              <button onclick="document.getElementById('proj-types-overlay').remove()" class="crm-detail-close">✕</button>
            </div>
            <div class="crm-modal-body" style="max-height:400px;overflow-y:auto;">
              ${_projectTypes.map((t, i) => `<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid #2a2a2a;">
                <span style="flex:1;font-size:14px;">${_esc(t)}</span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="rename" data-idx="${i}" style="padding:2px 6px;">✎</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" data-action="delete" data-idx="${i}" style="padding:2px 6px;">✕</button>
              </div>`).join('')}
              <button class="crm-btn crm-btn-primary crm-btn-sm" data-action="add" style="margin-top:8px;width:100%;">+ 新增類型</button>
            </div>
          </div>`;
        overlay.querySelectorAll('[data-action="add"]').forEach(b => b.addEventListener('click', async () => {
            const n = prompt('輸入新案件類型：'); if (!n?.trim()) return;
            if (_projectTypes.includes(n.trim())) { alert('已存在'); return; }
            _projectTypes.push(n.trim()); await _saveTypes(); _populateTypeSelects(); _render();
        }));
        overlay.querySelectorAll('[data-action="rename"]').forEach(b => b.addEventListener('click', async () => {
            const i = parseInt(b.dataset.idx), old = _projectTypes[i];
            const n = prompt('修改名稱：', old); if (!n?.trim() || n.trim() === old) return;
            _projectTypes[i] = n.trim(); await _saveTypes(); _populateTypeSelects(); _render();
        }));
        overlay.querySelectorAll('[data-action="delete"]').forEach(b => b.addEventListener('click', async () => {
            const i = parseInt(b.dataset.idx);
            if (!confirm(`確定刪除「${_projectTypes[i]}」？`)) return;
            _projectTypes.splice(i, 1); await _saveTypes(); _populateTypeSelects(); _render();
        }));
    }
    _render();
    document.body.appendChild(overlay);
};

// ── Helpers ────────────────────────────────────────────────

export function _badge(status) {
    const s = status || '洽談中';
    const known = ['洽談中', '報價中', '進行中', '已結案'];
    const cls = known.includes(s) ? `crm-badge crm-proj-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

export function _avatar(username, size = 22) {
    return renderAvatar(username, state.users, size);
}

function _populateSelect(elementId, placeholder) {
    populateUserSelect(elementId, state.users, placeholder);
}

function _populateClientFilter() {
    populateClientSelect('proj-filter-client', state.clients);
}

function _populateClientDropdown(elementId, selectedId) {
    const sel = document.getElementById(elementId);
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇客戶 —</option>` +
        state.clients.map(c => `<option value="${c.id}"${c.id === selectedId ? ' selected' : ''}>${_esc(c.short_name)}</option>`).join('');
    searchableSelect(sel, { placeholder: '搜尋客戶...' });
}

function _populatePmCheckboxes(selected = []) {
    const container = document.getElementById('proj-f-pm_usernames');
    if (!container) return;
    container.innerHTML = state.users.map(u => `
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

// ── Data Loading ────────────────────────────────────────────

export async function loadProjects() {
    const params = new URLSearchParams();
    if (state.filters.q)         params.set('q', state.filters.q);
    if (state.filters.status)    params.set('status', state.filters.status);
    if (state.filters.client_id) params.set('client_id', state.filters.client_id);
    if (state.filters.am)        params.set('am', state.filters.am);

    try {
        const data = await _fetch(`/projects?${params}`);
        state.projects = data.projects || [];
    } catch (e) {
        state.projects = [];
        _showListError(e.message);
    }
    renderList();
}

export async function loadClients() {
    try {
        const data = await crmCacheFetch('clients', '/clients');
        state.clients = data.clients || [];
        _populateClientFilter();
    } catch (_) {
        state.clients = [];
    }
}

export async function loadUsers() {
    try {
        const data = await crmCacheFetch('users', '/users');
        state.users = data.users || [];
        _populateSelect('proj-filter-am', '全部 AM');
    } catch (_) {
        state.users = [];
    }
}

export async function loadStaffList() {
    try { state.staffList = ((await crmCacheFetch('staff', '/staff')).staff || []); } catch (_) { state.staffList = []; }
}

// ── List Rendering ──────────────────────────────────────────

export function renderList() {
    const body = document.getElementById('proj-list-body');
    if (!body) return;

    if (state.projects.length === 0) {
        body.innerHTML = `<div class="crm-empty">找不到專案${state.filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }

    body.innerHTML = state.projects.map(p => `
        <div class="crm-row${p.id === state.selectedId ? ' selected' : ''}" data-id="${p.id}" onclick="window._projSelect('${p.id}')">
            <div class="crm-row-name">${_esc(p.name)}</div>
            <div class="crm-row-client">${_esc(p.client_short_name)}</div>
            <div class="crm-row-status">${_badge(p.status)}</div>
            <div class="crm-row-am">
                ${p.am_username ? _avatar(p.am_username) + _esc(p.am_username) : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-date">${p.start_date ? p.start_date.substring(0, 10) : '—'}</div>
            ${kebabMenuHtml(p.id, { onEdit: '_projEdit', onDuplicate: '_projDup', onDelete: '_projDelete' })}
        </div>
    `).join('');
}

// ── Selection & Close ───────────────────────────────────────

export function selectProject(id) {
    if (id !== state.selectedId && Object.keys(window._costDirtyMap || {}).length > 0) {
        window._costCheckUnsaved(function() { window._costDirtyMap = {}; selectProject(id); });
        return;
    }
    state.selectedId = id;
    renderList();

    const panel = document.getElementById('proj-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('proj-resize-handle');
    if (handle) handle.style.display = '';

    const project = state.projects.find(p => p.id === id);
    if (!project) return;
    callbacks.renderDetail?.(project);
    callbacks.loadQuotations?.(id);
}

export function closeDetail() {
    if (Object.keys(window._costDirtyMap || {}).length > 0) {
        window._costCheckUnsaved(function() { window._costDirtyMap = {}; closeDetail(); });
        return;
    }
    state.selectedId = null;
    const panel = document.getElementById('proj-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('proj-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Add / Edit Modal ────────────────────────────────────────

const _FIELDS = ['name', 'client_id', 'status', 'project_type', 'start_date', 'shoot_date',
    'completion_date', 'folder_path', 'description', 'am_username', 'notes',
    'contract_amount', 'tax_rate', 'profit_target_pct', 'misc_budget_pct',
    'payment_status', 'amount_receivable', 'amount_received', 'transfer_fee'];

export function openModal(project = null) {
    state.editingId = project ? project.id : null;
    document.getElementById('proj-modal-title').textContent = project ? '編輯專案' : '新增專案';
    const errEl = document.getElementById('proj-modal-error');
    errEl.textContent = '';
    errEl.style.display = 'none';

    _populateClientDropdown('proj-f-client_id', project ? project.client_id : '');
    _populateSelect('proj-f-am_username', '— 未指派 —');
    _populatePmCheckboxes(project ? (project.pm_usernames || []) : []);

    const dateFields = ['shoot_date', 'start_date', 'completion_date'];
    for (const f of _FIELDS) {
        const el = document.getElementById(`proj-f-${f}`);
        if (!el) continue;
        if (dateFields.includes(f) && project?.[f]) {
            el.value = project[f].substring(0, 10);
        } else if (['contract_amount', 'amount_receivable', 'amount_received', 'transfer_fee'].includes(f)) {
            el.value = project?.[f] ?? '';
        } else {
            const defaults = { tax_rate: '5', profit_target_pct: '20', misc_budget_pct: '5', payment_status: '未到帳' };
            el.value = project ? (project[f] ?? '') : (defaults[f] ?? '');
        }
    }

    document.getElementById('proj-modal').style.display = 'flex';
    document.getElementById('proj-f-name').focus();
}

export async function saveProject() {
    const name = document.getElementById('proj-f-name').value.trim();
    const client_id = document.getElementById('proj-f-client_id').value;
    if (!name) { _showModalError('專案名稱為必填欄位'); return; }
    if (!client_id) { _showModalError('請選擇客戶'); return; }

    const payload = {};
    const intFields = ['contract_amount', 'tax_rate', 'profit_target_pct', 'misc_budget_pct',
                       'amount_receivable', 'amount_received', 'transfer_fee'];
    const dateFields = ['shoot_date', 'start_date', 'completion_date'];
    for (const f of _FIELDS) {
        const el = document.getElementById(`proj-f-${f}`);
        let val = el ? el.value.trim() : '';
        if (intFields.includes(f)) val = val ? parseInt(val) : null;
        if (dateFields.includes(f)) val = val || null;
        payload[f] = val;
    }
    payload.pm_usernames = _getSelectedPms();

    const btn = document.getElementById('proj-btn-save');
    btn.disabled = true;
    btn.textContent = '儲存中...';

    try {
        const resp = state.editingId
            ? await _fetch(`/projects/${state.editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/projects', { method: 'POST', body: JSON.stringify(payload) });
        crmCacheInvalidate('projects', 'clients');
        document.getElementById('proj-modal').style.display = 'none';

        if (resp.project) {
            const idx = state.projects.findIndex(p => p.id === resp.project.id);
            if (idx >= 0) state.projects[idx] = resp.project;
            else state.projects.unshift(resp.project);
            renderList();
            if (state.editingId) selectProject(state.editingId);
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

export async function deleteProject(project) {
    if (!confirm(`確定刪除「${project.name}」？此操作無法復原。`)) return;
    try {
        await _fetch(`/projects/${project.id}`, { method: 'DELETE' });
        crmCacheInvalidate('projects', 'clients');
        closeDetail();
        await loadProjects();
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

// ── CSV Import ──────────────────────────────────────────────

let _csvFile = null;

export function openImportModal() {
    _csvFile = null;
    document.getElementById('proj-drop-filename').textContent = '';
    const result = document.getElementById('proj-import-result');
    result.style.display = 'none';
    result.className = 'crm-import-result';
    document.getElementById('proj-btn-do-import').disabled = true;
    document.getElementById('proj-import-modal').style.display = 'flex';
}

export function setCsvFile(file) {
    _csvFile = file;
    document.getElementById('proj-drop-filename').textContent = file ? file.name : '';
    document.getElementById('proj-btn-do-import').disabled = !file;
}

export async function doImport() {
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
