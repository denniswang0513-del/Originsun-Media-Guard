/**
 * crm-utils.js — CRM 共用工具函式
 * 供 crm.js 和 crm-projects.js 共用
 */

const API = '/api/v1/crm';

/* ── Request deduplication for GET requests ── */
const _inflight = new Map();

export async function crmFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const method = (opts.method || 'GET').toUpperCase();
    const url = API + path;

    // Deduplicate concurrent GET requests to the same URL
    if (method === 'GET') {
        if (_inflight.has(url)) return _inflight.get(url);
        const p = _doFetch(url, { ...opts, headers }).finally(() => _inflight.delete(url));
        _inflight.set(url, p);
        return p;
    }
    return _doFetch(url, { ...opts, headers });
}

async function _doFetch(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || '請求失敗');
    }
    return res.json();
}

export function esc(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function renderAvatar(username, users, size = 22) {
    const user = users.find(u => u.username === username);
    const initials = esc((username || '?').substring(0, 1).toUpperCase());
    if (user?.avatar_url) {
        return `<div class="crm-avatar" style="width:${size}px;height:${size}px;">
            <img src="${esc(user.avatar_url)}" alt="${esc(username)}">
        </div>`;
    }
    const colors = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981'];
    const code = (username || '').charCodeAt(0);
    const color = colors[Number.isFinite(code) ? code % colors.length : 0];
    return `<div class="crm-avatar" style="width:${size}px;height:${size}px;background:${color};">${initials}</div>`;
}

export function populateUserSelect(elementId, users, placeholder) {
    const sel = document.getElementById(elementId);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">${placeholder}</option>` +
        users.map(u => `<option value="${esc(u.username)}"${u.username === current ? ' selected' : ''}>${esc(u.username)}</option>`).join('');
}

export function fmtNum(n) {
    return (n || 0).toLocaleString('zh-TW');
}

export function populateClientSelect(elementId, clients, placeholder = '全部客戶') {
    const sel = document.getElementById(elementId);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">${placeholder}</option>` +
        clients.map(c => `<option value="${c.id}"${c.id === current ? ' selected' : ''}>${esc(c.short_name)}</option>`).join('');
}

/**
 * Inline edit for detail panels.
 * @param {string} contentElId - detail content element ID
 * @param {string} actionsElId - detail bar actions element ID
 * @param {Array} fields - [{name, label, type, options?, value}]
 *   type: text|number|date|month|select|textarea|readonly
 *   options: [{value, label}] for select
 * @param {object} data - current record data
 * @param {function} onSave - async (payload) => void
 * @param {function} onCancel - () => void (re-render detail)
 */
export function enableInlineEdit(contentElId, actionsElId, fields, data, onSave, onCancel) {
    const content = document.getElementById(contentElId);
    const actions = document.getElementById(actionsElId);
    if (!content || !actions) return;

    // Build editable fields
    content.innerHTML = fields.map(f => {
        const val = data[f.name] ?? '';
        let input = '';
        if (f.type === 'readonly') {
            input = `<span class="crm-prop-value">${esc(String(val))}</span>`;
        } else if (f.type === 'select' && f.options) {
            input = `<select class="crm-input crm-inline-input" data-field="${f.name}">` +
                f.options.map(o => `<option value="${esc(o.value)}"${String(val) === String(o.value) ? ' selected' : ''}>${esc(o.label)}</option>`).join('') +
                `</select>`;
        } else if (f.type === 'textarea') {
            input = `<textarea class="crm-input crm-inline-input crm-textarea" data-field="${f.name}" rows="2">${esc(String(val))}</textarea>`;
        } else if (f.type === 'date') {
            const dateVal = val ? String(val).substring(0, 10) : '';
            input = `<input type="date" class="crm-input crm-inline-input" data-field="${f.name}" value="${dateVal}">`;
        } else if (f.type === 'month') {
            input = `<input type="month" class="crm-input crm-inline-input" data-field="${f.name}" value="${esc(String(val))}">`;
        } else if (f.type === 'number') {
            input = `<input type="number" class="crm-input crm-inline-input" data-field="${f.name}" value="${val || ''}" min="0">`;
        } else {
            input = `<input type="text" class="crm-input crm-inline-input" data-field="${f.name}" value="${esc(String(val))}">`;
        }
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${f.label}</div><div class="crm-prop-value-edit">${input}</div></div>`;
    }).join('');

    // Replace action buttons
    const closeBtn = actions.querySelector('.crm-detail-close');
    const closeHtml = closeBtn ? closeBtn.outerHTML : '';
    actions.innerHTML = `
        <button class="crm-btn crm-btn-secondary crm-btn-sm" id="_inline-cancel">取消</button>
        <button class="crm-btn crm-btn-primary crm-btn-sm" id="_inline-save">儲存</button>
        ${closeHtml}
    `;

    document.getElementById('_inline-cancel').addEventListener('click', onCancel);
    document.getElementById('_inline-save').addEventListener('click', async () => {
        const btn = document.getElementById('_inline-save');
        btn.disabled = true; btn.textContent = '儲存中...';
        const payload = {};
        const intFields = fields.filter(f => f.type === 'number').map(f => f.name);
        content.querySelectorAll('[data-field]').forEach(el => {
            const name = el.dataset.field;
            let val = el.value;
            if (intFields.includes(name)) val = val ? parseInt(val) : null;
            if (el.type === 'date' || el.type === 'month') val = val || null;
            payload[name] = val;
        });
        try {
            await onSave(payload);
        } catch (e) {
            alert('儲存失敗: ' + e.message);
            btn.disabled = false; btn.textContent = '儲存';
        }
    });
}

/**
 * Add edit button to detail bar actions. Call from renderDetail().
 */
export function addEditButton(actionsElId, onEdit) {
    const actions = document.getElementById(actionsElId);
    if (!actions) return;
    // Remove old edit button if exists
    const old = actions.querySelector('#_inline-edit-btn');
    if (old) old.remove();
    const btn = document.createElement('button');
    btn.id = '_inline-edit-btn';
    btn.className = 'crm-btn crm-btn-secondary crm-btn-sm';
    btn.textContent = '編輯';
    btn.addEventListener('click', onEdit);
    actions.insertBefore(btn, actions.firstChild);
}

/**
 * Render a kebab menu (⋮) button for list rows.
 * @param {string} id - record id
 * @param {object} callbacks - { onEdit, onDuplicate, onDelete } window function names
 * @returns {string} HTML string
 */
export function kebabMenuHtml(id, callbacks) {
    return `<div class="crm-kebab-wrap" onclick="event.stopPropagation()">` +
        `<button class="crm-kebab-btn" onclick="window._crmToggleKebab(this,'${esc(id)}')">&#x22EE;</button>` +
        `<div class="crm-kebab-menu" data-kebab-id="${esc(id)}">` +
        (callbacks.onEdit ? `<div class="crm-kebab-item" onclick="this.parentElement.classList.remove('open');window.${callbacks.onEdit}('${esc(id)}')">編輯</div>` : '') +
        (callbacks.onDuplicate ? `<div class="crm-kebab-item" onclick="this.parentElement.classList.remove('open');window.${callbacks.onDuplicate}('${esc(id)}')">複製</div>` : '') +
        (callbacks.onDelete ? `<div class="crm-kebab-item crm-kebab-danger" onclick="this.parentElement.classList.remove('open');window.${callbacks.onDelete}('${esc(id)}')">刪除</div>` : '') +
        `</div></div>`;
}

/* Global kebab toggle — only one open at a time, position:fixed to escape overflow */
window._crmToggleKebab = function (btn, id) {
    const menu = btn.nextElementSibling;
    const wasOpen = menu.classList.contains('open');
    // close all menus first
    document.querySelectorAll('.crm-kebab-menu.open').forEach(m => m.classList.remove('open'));
    if (wasOpen) return;
    // position fixed relative to the button
    const rect = btn.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.top = rect.bottom + 2 + 'px';
    menu.style.right = (window.innerWidth - rect.right) + 'px';
    menu.style.left = 'auto';
    menu.classList.add('open');
};

/* Close kebab on click outside */
document.addEventListener('click', (e) => {
    if (e.target.closest('.crm-kebab-wrap')) return;
    document.querySelectorAll('.crm-kebab-menu.open').forEach(m => m.classList.remove('open'));
});

export function setupResizeHandle(handleId, panelId) {
    const resizeHandle = document.getElementById(handleId);
    const detailPanel = document.getElementById(panelId);
    if (!resizeHandle || !detailPanel) return;

    let startX, startW;
    resizeHandle.addEventListener('mousedown', e => {
        e.preventDefault();
        startX = e.clientX;
        startW = detailPanel.offsetWidth;
        resizeHandle.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';

        const onMove = ev => {
            const w = startW - (ev.clientX - startX);
            detailPanel.style.width = Math.max(320, Math.min(800, w)) + 'px';
        };
        const onUp = () => {
            resizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}
