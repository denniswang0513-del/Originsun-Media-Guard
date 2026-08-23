/**
 * crm-utils.js — CRM 共用工具函式
 * 供 crm.js 和 crm-projects.js 共用
 */

const API = '/api/v1/crm';

/* ── Request deduplication for GET requests ── */
const _inflight = new Map();

export async function crmFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    // FormData 不可以自己設 Content-Type —— 那會蓋掉瀏覽器要帶的 multipart
    // boundary，後端收到的是一包解不開的 body（檔案上傳全滅）。
    const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;
    const headers = {
        ...(isForm ? {} : { 'Content-Type': 'application/json' }),
        ...(opts.headers || {}),
    };
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
    return surfaceWarning(await res.json());
}

/**
 * 「成功但有話要說」的統一出口：後端回 warning = 主要動作成功、附帶副作用沒
 * 做成（例：專案改名成功，但資產資料夾正被開著改不動）。收在 fetch 咽喉，
 * 呼叫端就不必各自記得檢查 —— 沒接住的話那些提示等於不存在。
 *
 * 給所有帶 UI 的 fetch 咽喉共用（crmFetch 與提案/片庫的 tfetch）；回傳原
 * data 以便 `return surfaceWarning(await res.json())` 直接串接。
 */
export function surfaceWarning(data) {
    if (data && data.warning) crmToast(String(data.warning), 6000);
    return data;
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

// 今天（本地時區）的 YYYY-MM-DD —— 別用 toISOString().slice(0,10)：
// 那是 UTC 面值，台北早上八點前會差一天。
export function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function populateClientSelect(elementId, clients, placeholder = '全部客戶') {
    const sel = document.getElementById(elementId);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">${placeholder}</option>` +
        clients.map(c => `<option value="${c.id}"${c.id === current ? ' selected' : ''}>${esc(c.short_name)}</option>`).join('');
}

/** 專案下拉的 <option> 字串 —— 正本搬到 js/shared/utils.js（提案資產夾瀏覽器
 *  也要用，而它要能在 NAS 對外容器載入、拉不到 tabs/crm）。這裡 re-export。 */
export { projectOptionsHtml } from '../../js/shared/utils.js';

/**
 * Upgrade a native <select> to a searchable dropdown.
 * Hides the original select, inserts an input + dropdown panel.
 * Call AFTER the select is populated with options.
 */
export function searchableSelect(sel, opts = {}) {
    if (!sel || sel.dataset.searchable) return;
    sel.dataset.searchable = '1';
    // 篩選器的預設選項是「全部XX」而且 value 是空字串 —— 下面「顯示目前選中標籤」
    // 那段只認有 value 的選項，所以升級後整個框變空白，使用者看不出這是什麼篩選器
    // （實測收支明細的類別篩選器：選項從 4 個變 28 個跨過自動升級門檻後就這樣）。
    // 沒有明確指定 placeholder 時，就拿那個空值選項的字當提示 —— 等同原生 select
    // 未選時的顯示，語意一致。
    const _blank = [...sel.options].find(o => !o.value);
    const placeholder = opts.placeholder
        || (_blank && _blank.textContent.trim())
        || '搜尋...';

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

    // 點開就看到**全部**選項，不要拿已選值的文字去過濾 —— 那樣一個已經有值的
    // 下拉點開只會看到它自己那一項，使用者得先手動清空才換得掉（實測專案下拉
    // 32 個選項，選過之後再點開只剩 1 個）。開始打字才過濾。
    input.addEventListener('focus', () => { _buildItems(); _render(''); });
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
        // pick_* 端點要登入（2026-08-08 起）—— window.bearerHeader 由 shared/utils.js 掛
        const r = await fetch('/api/v1/utils/pick_folder',
                              { headers: window.bearerHeader ? window.bearerHeader() : {} });
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


/* enumIndex / 比較規則的正本搬到 js/shared/sortable.js —— tabs/proposals 底下的
 * 檔案不能靜態 import 這個檔（NAS 對外容器不 serve tabs/crm），而提案清單要用
 * 同一套規則。這裡 re-export，既有呼叫端一行都不用改。
 */
import { enumIndex, sortRows } from '../../js/shared/sortable.js';
export { enumIndex };


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
 *
 * 容器:`panelId`(元素 id)或 `panelSelector`(CSS selector,給沒有固定 id 的
 *   subview 容器;attach 時才 querySelector,重繪後照樣找得到)。
 * 表頭:panel 內任何帶 `data-sort-key` 的元素(th / span / div 都行 —
 *   全 repo 這個屬性只用在欄頭,不需要更窄的選擇器)。
 * defaultSort 可用 { key: '', dir: 'asc' } = 預設不排序:全部視為空值、
 *   stable sort 維持輸入順序,點了欄頭才生效。
 */
export function createSortable({ storageKey, defaultSort, panelId, panelSelector, getters, getValue, onChange }) {
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

    // 比較規則在 js/shared/sortable.js（空值排尾、大小寫正規化）——
    // 提案清單也走同一份，不然兩邊的空值會排到相反的方向
    const sorted = (items) => sortRows(items, _getValue, _sort);

    const attach = () => {
        const panel = panelId ? document.getElementById(panelId)
            : (panelSelector ? document.querySelector(panelSelector) : null);
        if (!panel) return;
        panel.querySelectorAll('[data-sort-key]').forEach(el => {
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


/** 可排序 <th> 標記 — 表格接排序都用它,不各自手寫指示器。
 *  sortableTh('year', '年份') / sortableTh('seo', 'AI SEO', 'data-col="seo" title="…"') */
export const sortableTh = (key, label, attrs = '') =>
    `<th data-sort-key="${key}"${attrs ? ' ' + attrs : ''}>${label} <span class="crm-sort-ind">↕</span></th>`;


/** span 版 — div/grid 欄頭用（如 .crm-list-header、user-mgmt 的 grid 表頭）。 */
export const sortableSpan = (key, label, attrs = '') =>
    `<span data-sort-key="${key}"${attrs ? ' ' + attrs : ''}>${label} <span class="crm-sort-ind">↕</span></span>`;


/** work_completeness 四項計分（影片/圖/說明/credits — 鏡射後端契約；
 *  works 列表與結案清單共用,欄位增減只改這裡）。無資料回 '' 排尾。 */
export const completenessScore = (c) =>
    c ? ['video', 'images', 'description', 'credits'].filter(k => c[k]).length : '';


/** 可編輯表格重繪前保住未儲存的 inline 編輯（帶 data-id + data-field 的
 *  input/select/textarea）,重繪後按同 key 還原值/勾選 — 點欄頭排序不再
 *  無聲洗掉還沒按儲存的修改。container 必須是重繪後仍存活的穩定祖先節點。 */
export function withInputsPreserved(container, rerender) {
    const saved = container ? [...container.querySelectorAll(
        'input[data-id][data-field], select[data-id][data-field], textarea[data-id][data-field]')]
        .map(el => ({ id: el.dataset.id, field: el.dataset.field, value: el.value, checked: el.checked })) : [];
    rerender();
    for (const s of saved) {
        const el = container.querySelector(
            `[data-id="${CSS.escape(s.id)}"][data-field="${CSS.escape(s.field)}"]`);
        if (!el) continue;
        if (el.type === 'checkbox' || el.type === 'radio') el.checked = s.checked;
        else el.value = s.value;
    }
}


/** 輕量 toast — 沿用 crm.css 既有 .cg-toast 樣式（cost-groups / media-log 等子視圖共用）。 */
export const INV_PENDING_REMIT = '待撥款';
export const INV_REMITTED = '已撥款';

/** 發票款項狀態 → badge。
 *
 * 🔴 從 crm-invoices.js 提上來（2026-08-23）—— 專案頁的發票分頁本來自己寫了一份
 * `_statusPill`，而且一出生就漂了：它把「待撥款」「已撥款」「未收款」全部收進同一個
 * 黃色，空值畫成「—」。同一張發票在兩頁看到不同顏色，比沒有顏色更糟。
 *
 * 三段各一個顏色 —— 共用綠色的話一整欄看起來都一樣，分不出哪些還沒撥款。
 * 待撥款＝紫（還有一筆錢要出去）、已撥款＝藍（收尾了）、已收款＝綠。
 * 🔴 class 用語意 token 不用中文狀態字：拿中文當 class 名的話，改一次用詞就得
 * 連 CSS 一起改（已轉撥→已撥款那次就是），而顯示的字只該住在這一行。 */
export function invoicePayBadge(status) {
    const s = (status || '').trim();
    if (!s) return '<span class="crm-badge crm-pay-badge-unset">未設定</span>';
    const cls = s === INV_REMITTED ? 'remitted'
        : s === INV_PENDING_REMIT ? 'pending-remit'
        : s === '已收款' ? 'collected' : s === '作廢' ? 'void' : 'unpaid';
    return `<span class="crm-badge crm-pay-badge-${cls}">${esc(s)}</span>`;
}

const TAX_RATE = 1.05;

/** 一個金額 + 它是未稅還是含稅 → 推出三個金額欄。
 *
 * 兩個方向都收在這裡：來源有時記未稅、有時記含稅，若讓兩條輸入路徑各自進位，
 * 同一筆錢會產生尾差。含稅→未稅用 round(total / 1.05)（166,950 → 159,000，
 * 回推 159,000×1.05 = 166,950 ✓），稅額一律取兩者之差，保證三欄自洽。
 *
 * 🔴 從 crm-invoices.js 搬上來（2026-08-23）—— 專案頁的「開發票」也要用它。
 * 稅率不是永恆的 5%，複製一份的話兩個入口開出來的發票尾差會不一樣，而且是
 * 那種對帳時才會發現的差。 */
export function invoiceAmounts(value, mode) {
    const n = parseInt(value) || 0;
    if (!n) return { amount_ex_tax: null, amount_total: null, tax_amount: null };
    const total = mode === 'total' ? n : Math.round(n * TAX_RATE);
    const ex = mode === 'total' ? Math.round(n / TAX_RATE) : n;
    return { amount_ex_tax: ex, amount_total: total, tax_amount: total - ex };
}

export function crmToast(msg, ms = 2000) {
    let el = document.getElementById('cg-toast');
    if (el) el.remove();
    el = document.createElement('div');
    el.id = 'cg-toast';
    el.className = 'cg-toast';
    el.textContent = msg;
    document.body.appendChild(el);
    // force reflow to trigger transition
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 250);
    }, ms);
}


// ── 金額檢視授權（前端鏡射）─────────────────────────────────
// owner 2026-08-15：**預設看不到金額，除非我授權**。
//
// 🔴 **執行的是後端**（core/money.py：整支 403 + 欄位從回應裡刪掉）。這裡不是
// 守衛，是為了「不要畫出謊話」：沒授權時金額欄位根本不在 payload 裡，而畫面上
// 一堆 `p.contract_amount || 0` 會把「看不到」畫成「這個客戶總營收 0 元」。
//
// 🔴 **住在 crm-utils 而不是 js/shared/**：它讀的是 SPA 的
// `window._accessLevel/_modules`，那兩個全域只有後台 SPA 會設。放在
// `js/shared/`（＝公開頁的 import 白名單）等於在獨立頁那側擺一個看起來能用、
// 實際上會把管理員判成「沒授權」的陷阱。放這裡，公開頁 import 不到，陷阱消失。
// 元件層（tabs/proposals/*）要判斷金額請看**後端回了那個鍵沒有**——
// 那是兩個掛載點都成立的唯一判準（見 staff-view.js）。
export const canSeeMoney = () => hasModule('money_view');

/** 「管理員 OR 有這個模組」—— 後端 `core.auth.payload_grants` 的前端鏡射。
 *
 *  寫一次的理由：這條規則本來就在前端被抄了兩份（這支與 crm-projects.js 的
 *  `_canManageWebsite`），RBAC 語義一動（例如 Lv2 也放行）就得兩處都找到。
 *
 *  ⚠️ Lv3 那一半今天其實是冗餘的 —— 登入與 `/auth/me` 都走 `_enrich_user`，
 *  管理員拿到的 `modules` 已經是 `ALL_MODULES`。留著是因為它的失效方向安全
 *  （少畫金額，不是多畫），而且不必去賭每一條發 token 的路徑都記得 enrich。
 */
export function hasModule(key) {
    return (window._accessLevel || 0) >= 3
        || (window._modules || []).includes(key);
}

// 整塊「這裡本來是錢」的替代畫面。多個呼叫端說的是同一句話 —— 各寫一份的話
// 改字時只會改到一邊。
export const NO_MONEY_HTML =
    '<div class="crm-empty" style="padding:8px 0;font-size:12px;">'
    + '此帳號沒有金額檢視權限</div>';

/** 「這一整塊都是錢」的區塊在**發請求前**先擋下來：擋住回 true，呼叫端 return。
 *
 *  為什麼要擋而不是讓它 403：那些端點整支是錢（`Depends(money_dep)`），
 *  fetch 失敗時畫面上是紅色「載入失敗」—— 那會被當成故障來報修。
 *
 *  收成一個名字而不是在每個區塊各貼三行：貼第三次時就已經漏掉一個
 *  （`_loadAdvances`，於是同一畫面上半截寫「沒有權限」、下半截紅字「載入失敗」）。
 */
export const moneyGate = (el) =>
    canSeeMoney() ? false : (el.innerHTML = NO_MONEY_HTML, true);
