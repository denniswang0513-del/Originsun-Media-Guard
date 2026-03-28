/**
 * crm-utils.js — CRM 共用工具函式
 * 供 crm.js 和 crm-projects.js 共用
 */

const API = '/api/v1/crm';

export async function crmFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(API + path, { ...opts, headers });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || '請求失敗');
    }
    return res.json();
}

export function esc(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
