/**
 * crm.js — 客戶管理 Tab
 * 功能：列表視圖 + 詳情面板 + 新增/編輯 Modal + CSV 匯入
 */

import { crmFetch as _fetch, esc as _esc, renderAvatar, populateUserSelect, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

// ── State ────────────────────────────────────────────────────

let _clients = [];
let _users = [];
let _selectedId = null;
let _editingId = null;  // null = 新增, string = 編輯
let _filters = { q: '', status: '', am: '' };
let _csvFile = null;

// ── API ──────────────────────────────────────────────────────

async function _fetchMultipart(path, formData) {
    const token = localStorage.getItem('auth_token');
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    const res = await fetch('/api/v1/crm' + path, { method: 'POST', headers, body: formData });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || '匯入失敗');
    }
    return res.json();
}

// ── Data Loading ─────────────────────────────────────────────

async function loadClients() {
    const params = new URLSearchParams();
    if (_filters.q)      params.set('q', _filters.q);
    if (_filters.status) params.set('status', _filters.status);
    if (_filters.am)     params.set('am', _filters.am);

    try {
        const data = await _fetch(`/clients?${params}`);
        _clients = data.clients || [];
    } catch (e) {
        _clients = [];
        _showListError(e.message);
    }
    renderList();
}

async function loadUsers() {
    try {
        const data = await _fetch('/users');
        _users = data.users || [];
        _populateSelect('crm-filter-am', '全部 AM');
    } catch (_) {
        _users = [];
    }
}

// ── Rendering ────────────────────────────────────────────────

function _badge(status) {
    const s = status || '潛在客戶';
    const known = ['潛在客戶','新客戶','舊客戶','暫停合作'];
    const cls = known.includes(s) ? `crm-badge crm-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

function _avatar(username, size = 22) {
    return renderAvatar(username, _users, size);
}

function renderList() {
    const body = document.getElementById('crm-list-body');
    if (!body) return;

    if (_clients.length === 0) {
        body.innerHTML = `<div class="crm-empty">找不到客戶資料${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }

    body.innerHTML = _clients.map(c => `
        <div class="crm-row${c.id === _selectedId ? ' selected' : ''}" data-id="${c.id}" onclick="window._crmSelectClient('${c.id}')">
            <div class="crm-row-name">${_esc(c.short_name)}</div>
            <div class="crm-row-status">${_badge(c.status)}</div>
            <div class="crm-row-am">
                ${c.am_username ? _avatar(c.am_username) + _esc(c.am_username) : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-contact">${c.updated_at ? c.updated_at.substring(0,10) : '—'}</div>
            ${kebabMenuHtml(c.id, { onEdit: '_crmEditClient', onDuplicate: '_crmDupClient', onDelete: '_crmDeleteClient' })}
        </div>
    `).join('');
}

const _CLIENT_EDIT_FIELDS = [
    {name:'short_name', label:'客戶代稱', type:'text'},
    {name:'full_name', label:'抬頭', type:'text'},
    {name:'tax_id', label:'統一編號', type:'text'},
    {name:'status', label:'狀態', type:'select', options:[
        {value:'潛在客戶',label:'潛在客戶'},{value:'新客戶',label:'新客戶'},
        {value:'舊客戶',label:'舊客戶'},{value:'暫停合作',label:'暫停合作'},
    ]},
    {name:'am_username', label:'AM', type:'text'},
    {name:'source_channel', label:'來源管道', type:'text'},
    {name:'contact_person', label:'聯絡人', type:'text'},
    {name:'contact_method', label:'聯絡方式', type:'text'},
    {name:'cooperation_note', label:'合作契機', type:'text'},
    {name:'payment_info', label:'匯款資訊', type:'text'},
    {name:'payment_note', label:'匯款備註', type:'text'},
    {name:'notes', label:'備註', type:'textarea'},
];

function renderDetail(client) {
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(value)}</div>
        </div>`;
    };

    const amHtml = client.am_username
        ? `<div class="crm-am-row">${_avatar(client.am_username, 28)}<span>${_esc(client.am_username)}</span></div>`
        : '<span class="crm-prop-value empty">未指派</span>';

    // Title (fixed above tabs)
    document.getElementById('crm-detail-title').textContent = client.short_name;

    // Tab 1: 客戶資訊
    document.getElementById('crm-detail-info').innerHTML = `
        ${prop('抬頭', client.full_name)}
        ${prop('統一編號', client.tax_id)}
        ${prop('匯款資訊', client.payment_info)}
        ${prop('匯款備註', client.payment_note)}
    `;

    // Tab 2: 客戶關係
    document.getElementById('crm-detail-rel').innerHTML = `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">狀態</div>
            <div class="crm-prop-value">${_badge(client.status)}</div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">AM</div>
            <div class="crm-prop-value">${amHtml}</div>
        </div>
        ${prop('來源管道', client.source_channel)}
        ${prop('聯絡人', client.contact_person ? `${client.contact_person}${client.contact_method ? ' / ' + client.contact_method : ''}` : '')}
        ${prop('合作契機', client.cooperation_note)}
        ${prop('備註', client.notes)}
        ${prop('修改日期', client.updated_at ? client.updated_at.substring(0,10) : '')}
    `;

    const actions = document.getElementById('crm-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">✕</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('crm-bar-actions', () => {
        enableInlineEdit('crm-detail-info', 'crm-bar-actions', _CLIENT_EDIT_FIELDS, client,
            async (payload) => {
                await _fetch('/clients/' + client.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/clients/' + client.id);
                renderDetail(updated);
                await loadClients();
            },
            () => renderDetail(client)
        );
    });
}

/** Shared helper: populate a <select> with user options */
function _populateSelect(elementId, placeholder) {
    populateUserSelect(elementId, _users, placeholder);
}

function _showListError(msg) {
    const body = document.getElementById('crm-list-body');
    if (body) body.innerHTML = `<div class="crm-empty" style="color:#fca5a5;">❌ ${_esc(msg)}</div>`;
}

// ── Detail Panel ─────────────────────────────────────────────

function selectClient(id) {
    _selectedId = id;
    renderList();

    const panel = document.getElementById('crm-detail-panel');
    if (!panel) return;
    panel.style.display = 'flex';
    const handle = document.getElementById('crm-resize-handle');
    if (handle) handle.style.display = '';

    // Use already-loaded data from _clients instead of re-fetching
    const client = _clients.find(c => c.id === id);
    if (!client) return;
    renderDetail(client);
}

function closeDetail() {
    _selectedId = null;
    const panel = document.getElementById('crm-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('crm-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Add / Edit Modal ─────────────────────────────────────────

const _FIELDS = ['short_name','full_name','tax_id','payment_info','payment_note',
    'am_username','source_channel','contact_person','contact_method','status',
    'cooperation_note','notes'];

function openModal(client = null) {
    _editingId = client ? client.id : null;
    document.getElementById('crm-modal-title').textContent = client ? '編輯客戶' : '新增客戶';
    const errEl = document.getElementById('crm-modal-error');
    errEl.textContent = '';
    errEl.style.display = 'none';

    _populateSelect('crm-f-am_username', '— 未指派 —');

    for (const f of _FIELDS) {
        const el = document.getElementById(`crm-f-${f}`);
        if (el) el.value = (client ? (client[f] ?? '') : '');
    }

    document.getElementById('crm-modal').style.display = 'flex';
    document.getElementById('crm-f-short_name').focus();
}

async function saveClient() {
    const short_name = document.getElementById('crm-f-short_name').value.trim();
    if (!short_name) {
        _showModalError('客戶代稱為必填欄位');
        return;
    }

    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById(`crm-f-${f}`);
        payload[f] = el ? el.value.trim() : '';
    }

    const btn = document.getElementById('crm-btn-save');
    btn.disabled = true;
    btn.textContent = '儲存中...';

    try {
        const resp = _editingId
            ? await _fetch(`/clients/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/clients', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('crm-modal').style.display = 'none';

        // Use response data to update local state instead of re-fetching
        if (resp.client) {
            const idx = _clients.findIndex(c => c.id === resp.client.id);
            if (idx >= 0) _clients[idx] = resp.client;
            else _clients.unshift(resp.client);
            renderList();
            if (_editingId) selectClient(_editingId);
        } else {
            await loadClients();
        }
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '儲存';
    }
}

function _showModalError(msg) {
    const el = document.getElementById('crm-modal-error');
    el.textContent = msg;
    el.style.display = 'block';
}

// ── Delete ───────────────────────────────────────────────────

async function deleteClient(client) {
    if (!confirm(`確定刪除「${client.short_name}」？此操作無法復原。`)) return;
    try {
        await _fetch(`/clients/${client.id}`, { method: 'DELETE' });
        closeDetail();
        await loadClients();
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

// ── CSV Import ───────────────────────────────────────────────

function openImportModal() {
    _csvFile = null;
    document.getElementById('crm-drop-filename').textContent = '';
    const result = document.getElementById('crm-import-result');
    result.style.display = 'none';
    result.className = 'crm-import-result';
    document.getElementById('crm-btn-do-import').disabled = true;
    document.getElementById('crm-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('crm-drop-filename').textContent = file ? file.name : '';
    document.getElementById('crm-btn-do-import').disabled = !file;
}

async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('crm-btn-do-import');
    btn.disabled = true;
    btn.textContent = '匯入中...';

    try {
        const form = new FormData();
        form.append('file', _csvFile);
        const data = await _fetchMultipart('/clients/import_csv', form);
        const result = document.getElementById('crm-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `
            ✅ 匯入完成<br>
            新增：<strong>${data.imported}</strong> 筆 ／
            更新：<strong>${data.updated}</strong> 筆 ／
            跳過：<strong>${data.skipped}</strong> 筆
        `;
        result.style.display = 'block';
        await loadClients();
    } catch (e) {
        const result = document.getElementById('crm-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = `❌ ${_esc(e.message)}`;
        result.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '開始匯入';
    }
}

// ── Utility ──────────────────────────────────────────────────

// ── Init ─────────────────────────────────────────────────────

export async function initCrmTab() {
    // Move modals to document.body — parent section's `transform` class
    // creates a new containing block that breaks position:fixed
    for (const id of ['crm-modal', 'crm-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._crmSelectClient = selectClient;
    window._crmEditClient = (id) => {
        const client = _clients.find(c => c.id === id);
        if (client) openModal(client);
    };
    window._crmDeleteClient = (id) => {
        const client = _clients.find(c => c.id === id);
        if (client) deleteClient(client);
    };
    window._crmDupClient = (id) => {
        const client = _clients.find(c => c.id === id);
        if (client) { openModal(client); _editingId = null; document.getElementById('crm-modal-title').textContent = '複製客戶'; }
    };

    let _searchTimer;
    document.getElementById('crm-search').addEventListener('input', e => {
        _filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadClients, 300);
    });
    document.getElementById('crm-filter-status').addEventListener('change', e => {
        _filters.status = e.target.value;
        loadClients();
    });
    document.getElementById('crm-filter-am').addEventListener('change', e => {
        _filters.am = e.target.value;
        loadClients();
    });

    document.getElementById('crm-btn-add').addEventListener('click', () => openModal());
    document.getElementById('crm-btn-import').addEventListener('click', openImportModal);
    document.getElementById('crm-btn-save').addEventListener('click', saveClient);
    document.getElementById('crm-detail-close').addEventListener('click', closeDetail);
    document.getElementById('crm-btn-do-import').addEventListener('click', doImport);

    document.getElementById('crm-csv-file').addEventListener('change', e => {
        _setCsvFile(e.target.files[0] || null);
    });

    const zone = document.getElementById('crm-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) _setCsvFile(file);
    });

    // Detail sub-tab switching
    document.querySelectorAll('#crm-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#crm-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('crm-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('crm-detail-rel').classList.toggle('hidden', tab !== 'rel');
        });
    });

    // Close modals on overlay click
    for (const id of ['crm-modal', 'crm-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('crm-resize-handle', 'crm-detail-panel');

    await Promise.all([loadUsers(), loadClients()]);
}
