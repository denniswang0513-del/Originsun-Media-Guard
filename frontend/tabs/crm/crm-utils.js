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

/* ── Shared data cache (cross-tab, TTL-based) ── */
const _cache = new Map();
const CACHE_TTL = 30_000; // 30 seconds

/**
 * Fetch with cache. Same-key concurrent calls share one request.
 * Call crmCacheInvalidate(key) after mutations to clear stale data.
 */
export async function crmCacheFetch(key, path) {
    const cached = _cache.get(key);
    if (cached && Date.now() - cached.ts < CACHE_TTL) return cached.data;
    const ts = Date.now();
    const data = await crmFetch(path);
    _cache.set(key, { data, ts });
    return data;
}

export function crmCacheInvalidate(...keys) {
    if (keys.length === 0) _cache.clear();
    else keys.forEach(k => _cache.delete(k));
}

async function _doFetch(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        const detail = Array.isArray(err.detail)
            ? err.detail.map(e => e.msg || e.message || JSON.stringify(e)).join('; ')
            : (err.detail || '請求失敗');
        throw new Error(detail);
    }
    return res.json();
}

/** Save partial settings (merge-on-save). Used by staff_roles, project_types, etc. */
export async function saveSettings(payload) {
    const token = localStorage.getItem('auth_token');
    return fetch('/api/settings/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}) },
        body: JSON.stringify(payload)
    });
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
 * Upgrade a native <select> to a searchable dropdown.
 * Hides the original select, inserts an input + dropdown panel.
 * Call AFTER the select is populated with options.
 */
export function searchableSelect(sel, opts = {}) {
    if (!sel || sel.dataset.searchable) return;
    sel.dataset.searchable = '1';
    const placeholder = opts.placeholder || '搜尋...';

    const wrap = document.createElement('div');
    wrap.className = 'ss-wrap';
    wrap.style.position = 'relative';
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.style.display = 'none';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'crm-input ss-input';
    input.placeholder = placeholder;
    // Show current selected label
    const curOpt = sel.options[sel.selectedIndex];
    if (curOpt && curOpt.value) input.value = curOpt.textContent;
    wrap.insertBefore(input, sel);

    const panel = document.createElement('div');
    panel.className = 'ss-panel';
    wrap.appendChild(panel);

    let items = [];
    let activeIdx = -1;

    function _buildItems() {
        items = [];
        for (const o of sel.options) {
            items.push({ value: o.value, label: o.textContent });
        }
    }

    function _render(filter) {
        const q = (filter || '').toLowerCase();
        const filtered = q ? items.filter(it => it.label.toLowerCase().includes(q)) : items;
        activeIdx = -1;
        panel.innerHTML = filtered.map((it, i) =>
            `<div class="ss-item${it.value === sel.value ? ' ss-selected' : ''}" data-idx="${i}" data-value="${esc(it.value)}">${esc(it.label)}</div>`
        ).join('') || '<div class="ss-empty">無結果</div>';
        panel.style.display = 'block';

        panel.querySelectorAll('.ss-item').forEach(el => {
            el.addEventListener('mousedown', e => {
                e.preventDefault();
                _pick(el.dataset.value, el.textContent);
            });
        });
    }

    function _pick(value, label) {
        sel.value = value;
        input.value = value ? label : '';
        panel.style.display = 'none';
        sel.dispatchEvent(new Event('change', { bubbles: true }));
    }

    input.addEventListener('focus', () => { _buildItems(); _render(input.value); });
    input.addEventListener('input', () => { _buildItems(); _render(input.value); });
    input.addEventListener('blur', () => { setTimeout(() => panel.style.display = 'none', 150); });
    input.addEventListener('keydown', e => {
        const visible = panel.querySelectorAll('.ss-item');
        if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, visible.length - 1); _highlight(visible); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); _highlight(visible); }
        else if (e.key === 'Enter') { e.preventDefault(); if (activeIdx >= 0 && visible[activeIdx]) { const el = visible[activeIdx]; _pick(el.dataset.value, el.textContent); } }
        else if (e.key === 'Escape') { panel.style.display = 'none'; input.blur(); }
    });

    function _highlight(nodes) {
        nodes.forEach((n, i) => n.classList.toggle('ss-active', i === activeIdx));
        if (nodes[activeIdx]) nodes[activeIdx].scrollIntoView({ block: 'nearest' });
    }

    // Re-sync when select is repopulated externally
    const observer = new MutationObserver(() => {
        const curOpt = sel.options[sel.selectedIndex];
        if (curOpt && curOpt.value) input.value = curOpt.textContent;
        else input.value = '';
    });
    observer.observe(sel, { childList: true });

    // Expose a sync handle on the element so callers that mutate `sel.value`
    // (e.g. _costCopyToActual) can repaint the visible input.
    sel._syncSsValue = () => {
        const o = sel.options[sel.selectedIndex];
        input.value = (o && o.value) ? o.textContent : '';
    };

    // Global auto-upgrade path: most call sites set `sel.value = x` programmatically
    // (loaded record → field value) WITHOUT calling _syncSsValue. Intercept the
    // value setter so the visible input repaints on its own. Explicit callers that
    // still call _syncSsValue keep working (idempotent). Best-effort.
    try {
        const _vd = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
        if (_vd && _vd.get && _vd.set) {
            Object.defineProperty(sel, 'value', {
                configurable: true,
                get() { return _vd.get.call(this); },
                set(v) { _vd.set.call(this, v); sel._syncSsValue(); },
            });
        }
    } catch (_) { /* non-fatal — _syncSsValue + childList observer still cover most cases */ }

    return {
        refresh: () => { _buildItems(); sel._syncSsValue(); },
        destroy: () => { observer.disconnect(); wrap.replaceWith(sel); sel.style.display = ''; delete sel.value; delete sel.dataset.searchable; delete sel._syncSsValue; }
    };
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

/**
 * Open a folder picker — NAS browser when accessing via tunnel, else native
 * tkinter dialog through the local agent. Returns selected path or '' on cancel.
 */
export async function pickFolderPath(initialPath) {
    if (window._isExternalAccess && typeof window.openNasBrowser === 'function') {
        return (await window.openNasBrowser({ title: '選擇資料夾', initialPath: initialPath || '' })) || '';
    }
    try {
        const r = await fetch('/api/v1/utils/pick_folder');
        const d = await r.json();
        return d.path || '';
    } catch (_) {
        return '';
    }
}

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
        } else if (f.type === 'checkboxes' && f.options) {
            const selected = Array.isArray(val) ? val : [];
            input = `<div data-field="${f.name}" class="crm-checkbox-list crm-inline-input">` +
                f.options.map(o => `<label class="crm-checkbox-item"><input type="checkbox" value="${esc(o.value)}"${selected.includes(o.value) ? ' checked' : ''}> ${esc(o.label)}</label>`).join('') +
                `</div>`;
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
        if (f.type === 'folder') {
            input += `<button class="crm-btn crm-btn-secondary crm-btn-sm _folder-pick" data-for="${f.name}" style="margin-left:4px;padding:2px 8px;flex-shrink:0;">📁</button>`;
            return `<div class="crm-detail-prop"><div class="crm-prop-label">${f.label}</div><div class="crm-prop-value-edit" style="display:flex;align-items:center;">${input}</div></div>`;
        }
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${f.label}</div><div class="crm-prop-value-edit">${input}</div></div>`;
    }).join('');

    // Bind folder picker buttons → use global pickPath mechanism
    content.querySelectorAll('._folder-pick').forEach(btn => {
        btn.addEventListener('click', async () => {
            const fieldName = btn.dataset.for;
            const inputEl = content.querySelector(`[data-field="${fieldName}"]`);
            if (!inputEl) return;
            const path = await pickFolderPath(inputEl.value || '');
            if (path) inputEl.value = path;
        });
    });

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
            if (el.classList.contains('crm-checkbox-list')) {
                payload[name] = Array.from(el.querySelectorAll('input:checked')).map(cb => cb.value);
                return;
            }
            let val = el.value;
            if (intFields.includes(name)) val = val ? parseInt(val) : 0;
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
            detailPanel.style.width = Math.max(320, Math.min(1200, w)) + 'px';
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


/* enumIndex — 工作流順序排序的小工具:`['草稿','已送','已簽']` 找出 val 的 index,
 * 找不到回 arr.length(排尾)。給 createSortable 的 getters 用,避免每個 list 各自寫
 * 「const i = arr.indexOf(val||default); return i === -1 ? arr.length : i;」三行。
 */
export const enumIndex = (arr, val, fallback) => {
    const i = arr.indexOf(val ?? fallback);
    return i === -1 ? arr.length : i;
};


/* ── Sortable list headers ──────────────────────────────────
 *
 * 通用列表排序器:點欄頭 toggle asc/desc、localStorage 持久化、render 後重綁。
 *
 * 列表 panel 內的 <span data-sort-key="X">標題 <span class="crm-sort-ind">↕</span></span>
 * 會被 attach() 綁 onclick + 持續維護 indicator(▴/▾/↕)+ active 樣式。
 *
 * Usage:
 *   const sorter = createSortable({
 *       storageKey: 'crm_projects_sort',
 *       defaultSort: { key: 'status', dir: 'asc' },
 *       panelId:    'proj-list-panel',
 *       onChange:   () => renderList(),
 *       getters:    { status: p => ..., name: p => ..., ... },
 *   });
 *   body.innerHTML = sorter.sorted(state.projects).map(...).join('');
 *   sorter.attach();  // idempotent — 每次 render 後呼叫安全
 *
 * getters 約定:fn 回 number 走數值比較;否則 String + zh-Hant localeCompare;
 *   '' / null / undefined 視為空值,asc/desc 都排尾(避免空值蓋掉資料)。
 *   未匹配 sort key 的 getter 自動回空字串(同空值處理)。
 *
 * 進階:傳 `getValue: (item, key) => ...` 取代 `getters`,給需要 dynamic key 行為的場景。
 */
export function createSortable({ storageKey, defaultSort, panelId, getters, getValue, onChange }) {
    const _getValue = getValue || ((item, k) => getters?.[k]?.(item) ?? '');
    let _sort = (() => {
        try {
            const v = JSON.parse(localStorage.getItem(storageKey) || 'null');
            if (v && typeof v.key === 'string' && (v.dir === 'asc' || v.dir === 'desc')) return v;
        } catch (_) {}
        return { ...defaultSort };
    })();

    const _save = () => {
        try { localStorage.setItem(storageKey, JSON.stringify(_sort)); } catch (_) {}
    };

    const _setSort = (key) => {
        _sort = (_sort.key === key)
            ? { key, dir: _sort.dir === 'asc' ? 'desc' : 'asc' }
            : { key, dir: 'asc' };
        _save();
        onChange?.();
    };

    const _isEmpty = (v) => v === '' || v == null;
    const sorted = (items) => {
        const sign = _sort.dir === 'desc' ? -1 : 1;
        // copy first — caller 的原陣列保留輸入順序(下游可能也要用)
        return [...items].sort((a, b) => {
            const va = _getValue(a, _sort.key);
            const vb = _getValue(b, _sort.key);
            const ae = _isEmpty(va), be = _isEmpty(vb);
            // 空值永遠排尾(populated 先,跟方向無關 — 避免 desc 時空值跑到頂遮資料)
            if (ae !== be) return ae ? 1 : -1;
            if (ae) return 0;
            if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign;
            return String(va).localeCompare(String(vb), 'zh-Hant') * sign;
        });
    };

    const attach = () => {
        const panel = document.getElementById(panelId);
        if (!panel) return;
        panel.querySelectorAll('.crm-list-header [data-sort-key]').forEach(el => {
            const k = el.dataset.sortKey;
            // 用 dataset flag 避免重複 bind(每次 render 後 caller 都呼叫 attach,header 元素不變但 onclick 不能重疊)
            if (!el.dataset.sortBound) {
                el.addEventListener('click', () => _setSort(k));
                el.dataset.sortBound = '1';
            }
            const ind = el.querySelector('.crm-sort-ind');
            if (ind) {
                if (k === _sort.key) {
                    ind.textContent = _sort.dir === 'asc' ? '▴' : '▾';
                    el.classList.add('crm-sort-active');
                } else {
                    ind.textContent = '↕';
                    el.classList.remove('crm-sort-active');
                }
            }
        });
    };

    return { sorted, attach, setSort: _setSort };
}

