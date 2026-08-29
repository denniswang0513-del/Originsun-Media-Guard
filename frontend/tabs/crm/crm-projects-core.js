/**
 * crm-projects-core.js — 列表 + CRUD Modal + CSV 匯入
 */
import { crmFetch as _fetch, crmCacheFetch, crmCacheInvalidate, esc as _esc, renderAvatar, populateClientSelect, searchableSelect, saveSettings, kebabMenuHtml, createSortable, enumIndex, crmToast } from './crm-utils.js';
import { state, callbacks, STATUS_ORDER } from './crm-projects-state.js';

// 開場的帳本預設（因人而異）—— 快取守衛拿它判斷「有沒有套篩選」
const _initialEntity = state.filters.entity;

// 「還在談」的狀態子集 —— 從提案模組的常數正本拿，不要在這裡再列一份
// （加第七個狀態時，列在這裡的那份會靜默漏掉）
import { PRESALE_STATUSES as _PROP_SUB_STATUSES } from '../proposals/prop-const.js';

// ── Sortable list ──────────────────────────────────────────
// 狀態走 STATUS_ORDER index(工作流順序);其他欄轉小寫做中文 localeCompare;
// 空值由 createSortable 內部統一排尾。
const _sorter = createSortable({
    storageKey: 'crm_projects_sort',
    defaultSort: { key: 'status', dir: 'asc' },
    panelId: 'proj-list-panel',
    onChange: () => renderList(),
    getters: {
        status: p => enumIndex(STATUS_ORDER, p.status, '投標'),
        name:   p => (p.name || '').toLowerCase(),
        client: p => (p.client_short_name || '').toLowerCase(),
        am:     p => (p.am_username || '').toLowerCase(),
        date:   p => p.start_date || '',
    },
});

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
    const s = status || '投標';
    const cls = STATUS_ORDER.includes(s) ? `crm-badge crm-proj-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

export function _avatar(username, size = 22) {
    return renderAvatar(username, state.users, size);
}

function _populateSelect(elementId, placeholder, currentValue = '') {
    // AM dropdown — pulls from 人力資源 (crm_staff), not system users.
    // Wrapped with searchableSelect for type-to-filter (same UX as 客戶).
    const sel = document.getElementById(elementId);
    if (!sel) return;
    const opts = (state.staffList || []).map(s =>
        `<option value="${_esc(s.name)}"${s.name === currentValue ? ' selected' : ''}>${_esc(s.name)}${s.role ? ` (${_esc(s.role)})` : ''}</option>`
    ).join('');
    sel.innerHTML = `<option value="">${placeholder}</option>${opts}`;
    searchableSelect(sel, { placeholder: '搜尋人員...' });
    // Refresh the visible search-input on modal reopen (searchableSelect
    // no-ops on already-wrapped selects so the input is otherwise stale).
    sel._syncSsValue?.();
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
    sel._syncSsValue?.();
}

function _populatePmCheckboxes(selected = []) {
    // PM is a single-select — DB column is still a JSON array, so we
    // pre-select the first element if present and store back as `[name]`.
    // Wrapped with searchableSelect for type-to-filter (same as AM).
    const sel = document.getElementById('proj-f-pm_usernames');
    if (!sel) return;
    const cur = Array.isArray(selected) && selected.length > 0 ? selected[0] : '';
    sel.innerHTML = `<option value="">— 未指派 —</option>` +
        (state.staffList || []).map(s =>
            `<option value="${_esc(s.name)}"${s.name === cur ? ' selected' : ''}>${_esc(s.name)}${s.role ? ` (${_esc(s.role)})` : ''}</option>`
        ).join('');
    searchableSelect(sel, { placeholder: '搜尋人員...' });
    sel._syncSsValue?.();
}

function _getSelectedPms() {
    const sel = document.getElementById('proj-f-pm_usernames');
    if (!sel) return [];
    return sel.value ? [sel.value] : [];
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

// Stale-while-revalidate cache (localStorage). Lets the project list render
// instantly on tab open from last session's data, while a fresh fetch runs
// in the background and re-renders when it lands. Coworkers used to see an
// empty "找不到專案" until the first fetch returned; now they see real data
// immediately on cached visits.
// v2（2026-08-24）：v1 的快取是私帳 402 案匯入後、加帳本篩選前存的混合清單 ——
// 不換鍵的話下次開啟會先閃一次那份舊資料再被正確的取代。
const _SWR_KEY = 'crm_projects_swr_v2';

export function _hydrateProjectsFromCache() {
    try {
        const cached = JSON.parse(localStorage.getItem(_SWR_KEY) || 'null');
        if (cached && Array.isArray(cached.projects) && cached.projects.length > 0) {
            state.projects = cached.projects;
            renderList();
            return true;
        }
    } catch (_) { /* corrupt cache — ignore */ }
    return false;
}

function _writeProjectsCache() {
    // Only cache the unfiltered view — filtered results would mislead next
    // boot when filters are reset.
    // 只快取「沒套任何篩選」的預設視圖。entity 的預設因人而異（見
    // crm-projects-state._defaultEntity），所以跟 _initialEntity 比而不是寫死。
    if (state.filters.q || state.filters.status || state.filters.client_id
        || state.filters.am || state.filters.entity !== _initialEntity) return;
    try {
        localStorage.setItem(_SWR_KEY, JSON.stringify({
            projects: state.projects, ts: Date.now(),
        }));
    } catch (_) { /* quota / private mode — best-effort */ }
}

export function _clearProjectsCache() {
    try { localStorage.removeItem(_SWR_KEY); } catch (_) {}
}

export async function loadProjects() {
    const params = new URLSearchParams();
    if (state.filters.q)         params.set('q', state.filters.q);
    if (state.filters.status)    params.set('status', state.filters.status);
    if (state.filters.client_id) params.set('client_id', state.filters.client_id);
    if (state.filters.am)        params.set('am', state.filters.am);
    if (state.filters.entity)    params.set('entity', state.filters.entity);
    // 母公司管線把推送過來的私帳案（後期專案）也算進來 —— 明確參數，
    // 掛錢用的下拉不會拿到（見 routers/crm/projects.py 的說明）
    if (state.filters.entity === 'parent') params.set('include_pushed', '1');

    try {
        const data = await _fetch(`/projects?${params}`);
        state.projects = data.projects || [];
        state.projectsLoaded = true;
        _writeProjectsCache();
    } catch (e) {
        // Keep cached projects on fetch failure — better than wiping to empty.
        if (!state.projectsLoaded) state.projects = [];
        _showListError(e.message);
    }
    renderList();
    // 提案庫進程帶跟著同一份 filters 走 —— 這裡是咽喉（init/分頁/下拉/成案後
    // 重載全都經過 loadProjects），別在個別事件 handler 再各掛一次。
    // dynamic import 避免與 proposals 模組（它 import 本檔的 loadProjects）成環。
    import('./crm-projects-proposals.js').then(m => m.syncProposalStrip()).catch(() => {});
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

// 提案=專案合體：列上顯示衛星提案的前期子狀態（成案/未成案已反映在專案
// 階段本身，不重複掛）。proposal_status 由 GET /projects 附掛。
function _propSubBadge(p) {
    if (!_PROP_SUB_STATUSES.includes(p.proposal_status || '')) return '';
    return `<span class="crm-badge" style="margin-left:4px;opacity:.7;">${p.proposal_status}</span>`;
}

export function renderList() {
    const body = document.getElementById('proj-list-body');
    if (!body) return;

    _sorter.attach();  // idempotent;每次 render 後重新整 indicator + 確保 onclick 綁好

    if (state.projects.length === 0) {
        if (!state.projectsLoaded) {
            body.innerHTML = `<div class="crm-empty">載入中…</div>`;
            return;
        }
        body.innerHTML = `<div class="crm-empty">找不到專案${state.filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }

    body.innerHTML = _sorter.sorted(state.projects).map(p => `
        <div class="crm-row${p.id === state.selectedId ? ' selected' : ''}" data-id="${p.id}" onclick="window._projSelect('${p.id}')">
            <div class="crm-row-name">${_esc(p.name)}${p.entity !== 'mine' ? ''
                : p.crm_pushed
                    ? ' <span style="font-size:10px;color:#7dd3fc;border:1px solid #2d5a78;border-radius:3px;padding:0 4px;vertical-align:1px;" title="從 owner 私帳推送進管線的後期案；錢流仍在私帳">後期專案</span>'
                    : ' <span style="font-size:10px;color:#c4b5fd;border:1px solid #4c3d78;border-radius:3px;padding:0 4px;vertical-align:1px;" title="錢流記在 owner 私帳（我的帳）；專案本身共用">私帳</span>'}</div>
            <div class="crm-row-client">${p.client_short_name
                ? _esc(p.client_short_name)
                : '<span class="crm-muted">待補客戶</span>'}</div>
            <div class="crm-row-status">${_badge(p.status)}${_propSubBadge(p)}</div>
            <div class="crm-row-am">
                ${p.am_username ? _avatar(p.am_username) + _esc(p.am_username) : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-date">${p.start_date ? p.start_date.substring(0, 10) : '—'}</div>
            ${kebabMenuHtml(p.id, { onEdit: '_projOpenForm', onDuplicate: '_projDup', onDelete: '_projDelete' })}
        </div>
    `).join('');
}

// ── Selection & Close ───────────────────────────────────────

export function selectProject(id) {
    if (id !== state.selectedId && window._allDirtyCount?.() > 0) {
        window._costCheckUnsaved(function() {
            window._clearAllDirty();
            selectProject(id);
        });
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
    if (window._allDirtyCount?.() > 0) {
        window._costCheckUnsaved(function() {
            window._clearAllDirty();
            closeDetail();
        });
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

export async function openModal(project = null) {
    state.editingId = project ? project.id : null;
    document.getElementById('proj-modal-title').textContent = project ? '編輯專案' : '新增專案';
    const errEl = document.getElementById('proj-modal-error');
    errEl.textContent = '';
    errEl.style.display = 'none';

    // 先開 modal 再補資料 — 按鈕要即時有反應（fetch 慢時不至於像沒按到）
    document.getElementById('proj-modal').style.display = 'flex';

    // 客戶/人員可能剛在別的分頁新增 — 開 modal 時刷新（客戶管理存檔會
    // invalidate 共用快取，這裡重抓才看得到；LAN 一趟 <100ms 無感）
    await Promise.all([loadClients(), loadStaffList()]);

    _populateClientDropdown('proj-f-client_id', project ? project.client_id : '');
    _populateSelect('proj-f-am_username', '— 未指派 —', project?.am_username || '');
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


// ── 搬帳本（專案管理 ↔ 私帳）─────────────────────────────────────
// owner 2026-08-28：「可以有一個按鈕把專案推送至私帳（只有擁有私帳權限的人能用）」。
// 按鈕在詳情面板的動作列（crm-projects-detail.js），只對有 finance_mine 的帳號畫出來。
//
// 🔴 換帳本是全 repo「更新一律不得換帳本」的唯一例外，所以：
//   1) 先問後端「能不能搬」，把會擋住的東西講出來 —— 不要讓人按了才吃 409；
//   2) 動手前一定 confirm，並把「錢會跟著算到哪本帳」寫清楚。
window._projMoveLedger = async function (id) {
    let chk;
    try {
        chk = await _fetch(`/projects/${encodeURIComponent(id)}/ledger-move-check`);
    } catch (e) {
        crmToast('查不到搬帳本的狀態：' + e.message);
        return;
    }
    const toMine = chk.target === 'mine';
    if (!chk.can_move) {
        // 這句話的正本在後端（_blocked_reason）—— 前端再拼一份就會跟 409 的
        // 訊息漂成兩種說法
        crmToast(chk.reason || '這個專案不能換帳本', 6000);
        return;
    }
    const msg = toMine
        ? `把「${chk.name}」推送至私帳？\n\n`
          + '這個專案的錢流歸屬會改成私帳：之後掛在它身上的發票／收支／請款都算私帳的，\n'
          + '母公司的三表不再計入它。專案管理仍看得到（標「後期專案」）。\n\n'
          + '目前它身上沒有任何單據，所以搬過去不會動到任何一筆已記的帳。'
        : `把「${chk.name}」搬回母公司帳？\n\n`
          + '錢流歸屬改回母公司，之後掛在它身上的錢都算公司的。';
    if (!window.confirm(msg)) { return; }
    try {
        await _fetch(`/projects/${encodeURIComponent(id)}/move-ledger`, {
            method: 'POST', body: JSON.stringify({ entity: chk.target }),
        });
        crmToast(toMine ? '已推送至私帳' : '已搬回公司帳');
        crmCacheInvalidate('/projects');
        await loadProjects();
        // 詳情面板要重畫（按鈕文字與帳本標記都變了）
        const p = state.projects.find(x => x.id === id);
        if (p) { callbacks.renderDetail?.(p); }
    } catch (e) {
        crmToast('搬帳本失敗：' + e.message);
    }
};


// ── 連結私帳（母公司專案 → 私帳的收入分身）──────────────────────
// owner 2026-08-29：「費用要給王士源的，直接在私帳建立專案、同步收入」。
//
// 跟「推送至私帳」（搬家）是兩件事：這裡母公司那案原封不動，只是在私帳多開
// 一案，收入＝人員配置裡掛給我的那幾行成本。金額判定全在後端
// （core.ledger_project.mirror_lines），前端只顯示它算出來的明細 —— 前端自己
// 再加一次總和，兩個數字遲早會不一樣。
window._projMirrorMine = async function (id) {
    let chk;
    try {
        chk = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-check`);
    } catch (e) {
        crmToast('查不到連結私帳的狀態：' + e.message, 6000);
        return;
    }
    if (!chk.can_mirror) {
        // 這句話的正本在後端（_mirror_blocked_reason）——前端再拼一份就會跟
        // 409 的訊息漂成兩種說法
        crmToast(chk.reason || '這一案不能連結私帳', 6000);
        return;
    }
    const rows = (chk.lines || []).map(l => `<tr>
        <td style="color:#888;">${_esc(l.phase)}</td>
        <td>${_esc(l.item)}</td>
        <td style="text-align:right;">${l.amount.toLocaleString()}</td></tr>`).join('');
    const opts = (chk.options || []).map(o =>
        `<option value="${o.id}">${_esc(o.name)}${o.amount ? ` — ${o.amount.toLocaleString()}` : ''}</option>`).join('');
    _mirrorModal(`連結私帳 — ${chk.name}`, `
        <div style="color:#bbb;font-size:12px;margin-bottom:8px;">
            公司要付給你的（來自人員配置的成本行）</div>
        <table class="crm-table" style="width:100%;font-size:12px;">${rows}
            <tr><td colspan="2" style="font-weight:600;">私帳收入合計</td>
                <td style="text-align:right;font-weight:600;color:#86efac;">
                    ${chk.total.toLocaleString()}</td></tr></table>
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:8px;font-size:13px;">
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="pmm-mode" value="new" checked>
                在私帳建立新專案（客戶：${_esc(chk.client || '未指定')}／案源：源日）</label>
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="pmm-mode" value="link"> 連結到既有私帳專案</label>
            <select class="crm-input" id="pmm-target" disabled style="margin-left:22px;">
                <option value="">— 選一個 —</option>${opts}</select>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="pmm-go"
                    data-id="${id}">建立並連結</button>
        </div>`);
    const sel = document.getElementById('pmm-target');
    document.querySelectorAll('input[name="pmm-mode"]').forEach(r =>
        r.addEventListener('change', () => {
            const link = r.value === 'link' && r.checked;
            sel.disabled = !link;
            document.getElementById('pmm-go').textContent = link ? '連結' : '建立並連結';
        }));
    document.getElementById('pmm-go').addEventListener('click', _projMirrorSubmit);
};

async function _projMirrorSubmit(ev) {
    const btn = ev.currentTarget;
    const id = btn.dataset.id;
    const link = document.querySelector('input[name="pmm-mode"]:checked')?.value === 'link';
    const target = link ? (document.getElementById('pmm-target').value || '') : '';
    if (link && !target) { crmToast('請先選一個要連結的私帳專案'); return; }
    btn.disabled = true;
    try {
        const r = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-to-mine`, {
            method: 'POST', body: JSON.stringify({ target_id: target }),
        });
        window._projMirrorClose();
        crmToast(`已在私帳同步收入 ${(r.amount || 0).toLocaleString()}`);
        crmCacheInvalidate('/projects');
    } catch (e) {
        btn.disabled = false;
        crmToast('連結私帳失敗：' + e.message, 6000);
    }
}

/** 疊在詳情面板之上的預覽視窗。重複使用同一個 overlay（每次重建會在 body
 *  裡疊出一堆孤兒節點，radio 的 name 也會互相搶）。 */
function _mirrorModal(title, bodyHtml) {
    let o = document.getElementById('proj-mirror-modal');
    if (!o) {
        o = document.createElement('div');
        o.id = 'proj-mirror-modal';
        o.className = 'crm-modal-overlay';
        o.style.zIndex = '1100';
        o.innerHTML = `<div class="crm-modal" style="max-width:min(560px,94vw);">
            <div class="crm-modal-header">
                <h3 id="proj-mirror-title"></h3>
                <button class="crm-detail-close"
                        onclick="window._projMirrorClose()">&#x2715;</button>
            </div>
            <div class="crm-modal-body" id="proj-mirror-body"></div>
        </div>`;
        document.body.appendChild(o);
    }
    o.querySelector('#proj-mirror-title').textContent = title;
    o.querySelector('#proj-mirror-body').innerHTML = bodyHtml;
    o.style.display = 'flex';
}

window._projMirrorClose = function () {
    const o = document.getElementById('proj-mirror-modal');
    if (o) { o.style.display = 'none'; o.querySelector('#proj-mirror-body').innerHTML = ''; }
};
