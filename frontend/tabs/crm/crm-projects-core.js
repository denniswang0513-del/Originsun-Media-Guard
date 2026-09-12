/**
 * crm-projects-core.js — 列表 + CRUD Modal + CSV 匯入
 */
import { crmFetch as _fetch, crmCacheFetch, crmCacheInvalidate, esc as _esc, fmtNum, renderAvatar, populateClientSelect, searchableSelect, saveSettings, kebabMenuHtml, createSortable, enumIndex, crmToast } from './crm-utils.js';
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
        type:   p => (p.project_type || ''),
        date:   p => p.start_date || '',
    },
});

// ── 案型清單：一份（GET /api/v1/crm/project-types；正本＝私帳毛利表的列，settings.project_types 是它的鏡射）──
// owner 2026-09-03「這裡的案型跟私帳同步」：專案表下拉、這裡的編輯器、私帳設定頁、工時 burn 表都吃同一份；
// 這裡的新增／改名／刪除直接改毛利表（POST /project-types），用 CRM 的人不用看得到那張表。
const _DEFAULT_TYPES = ['紀實影片', '活動紀實', '形象影片', '廣告', 'MV'];
let _projectTypes = [..._DEFAULT_TYPES];

function _applyTypes(d) {
    _projectTypes = (d.project_types && d.project_types.length) ? d.project_types : [..._DEFAULT_TYPES];
}

export async function loadProjectTypes() {
    try { _applyTypes(await _fetch('/project-types')); }
    catch (_) { _projectTypes = [..._DEFAULT_TYPES]; }
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

/** 一個動作（add／rename／remove）打後端改毛利表，回來的清單直接套；失敗丟 toast、清單不動。 */
async function _typeOp(op, name, newName = '') {
    try {
        _applyTypes(await _fetch('/project-types', { method: 'POST', body: JSON.stringify({ op, name, new_name: newName }) }));
        _populateTypeSelects();
        return true;
    } catch (e) { crmToast(e.message || '案型沒存', true); return false; }
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
            <div class="crm-modal-header"><h3>案型清單</h3>
              <button onclick="document.getElementById('proj-types-overlay').remove()" class="crm-detail-close">關閉</button>
            </div>
            <div class="crm-modal-body" style="max-height:400px;overflow-y:auto;">
              <div style="font-size:12px;color:#888;margin-bottom:6px;">跟私帳的預期毛利表同一份：這裡加的案型預期毛利先照「其他」那列，之後可在財務設定調。</div>
              ${_projectTypes.map((t, i) => `<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid #2a2a2a;">
                <span style="flex:1;font-size:14px;">${_esc(t)}</span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="rename" data-idx="${i}" style="padding:2px 6px;">改名</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" data-action="delete" data-idx="${i}" style="padding:2px 6px;">刪除</button>
              </div>`).join('')}
              <button class="crm-btn crm-btn-primary crm-btn-sm" data-action="add" style="margin-top:8px;width:100%;">新增案型</button>
            </div>
          </div>`;
        overlay.querySelectorAll('[data-action="add"]').forEach(b => b.addEventListener('click', async () => {
            const n = prompt('新案型名稱：'); if (!n?.trim()) return;
            if (await _typeOp('add', n.trim())) _render();
        }));
        overlay.querySelectorAll('[data-action="rename"]').forEach(b => b.addEventListener('click', async () => {
            const old = _projectTypes[parseInt(b.dataset.idx)];
            const n = prompt('修改名稱：', old); if (!n?.trim() || n.trim() === old) return;
            if (await _typeOp('rename', old, n.trim())) _render();
        }));
        overlay.querySelectorAll('[data-action="delete"]').forEach(b => b.addEventListener('click', async () => {
            const t = _projectTypes[parseInt(b.dataset.idx)];
            if (!confirm(`確定刪除「${t}」？還掛著這個案型的專案不會被改。`)) return;
            if (await _typeOp('remove', t)) _render();
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

/** 專案表單的「沒有符合的？新增客戶」（owner 2026-09-07，桌機與手機都要）：建一筆只有代稱的客戶，
 *  推進 state.clients、清掉 clients 快取、重畫下拉並選起來。同報價彈窗的 _createClientInline。 */
export async function createClientInline() {
    const name = (prompt('客戶名稱（代稱）：') || '').trim();
    if (!name) return;
    try {
        const r = await _fetch('/clients', { method: 'POST', body: JSON.stringify({ short_name: name }) });
        const c = r.client || r;
        state.clients.push({ id: c.id, short_name: c.short_name || name, full_name: c.full_name || '' });
        crmCacheInvalidate('clients');
        _populateClientDropdown('proj-f-client_id', c.id);
        _populateClientFilter();
        crmToast('客戶已建立：' + (c.short_name || name));
    } catch (e) { alert('建立客戶失敗：' + e.message); }
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
            <div class="crm-row-name">${_esc(p.name)}${p.mirrored
                ? ` <span style="font-size:10px;color:#86efac;border:1px solid #2f5d43;border-radius:3px;padding:0 4px;vertical-align:1px;" title="這一案在私帳有對應的案（「推送到私帳」）${p.mine_link_name ? '：' + _esc(p.mine_link_name) : ''}">已連結私帳${p.mine_link_name && p.mine_link_name !== p.name ? ' → ' + _esc(p.mine_link_name) : ''}</span>`
                : ''}${p.entity !== 'mine' ? ''
                : p.crm_pushed
                    ? ' <span style="font-size:10px;color:#7dd3fc;border:1px solid #2d5a78;border-radius:3px;padding:0 4px;vertical-align:1px;" title="從 owner 私帳推送進管線的後期案；錢流仍在私帳">後期專案</span>'
                    : ' <span style="font-size:10px;color:#c4b5fd;border:1px solid #4c3d78;border-radius:3px;padding:0 4px;vertical-align:1px;" title="錢流記在 owner 私帳（我的帳）；專案本身共用">私帳</span>'}</div>
            <div class="crm-row-client">${p.client_short_name
                ? _esc(p.client_short_name)
                : '<span class="crm-muted">待補客戶</span>'}</div>
            <div class="crm-row-status">${_badge(p.status)}${_propSubBadge(p)}</div>
            <div class="crm-row-type" onclick="event.stopPropagation()">${_typeSelectHtml(p)}</div>
            <div class="crm-row-am">
                ${p.am_username ? _avatar(p.am_username) + _esc(p.am_username) : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-date">${p.start_date ? p.start_date.substring(0, 10) : '—'}</div>
            ${kebabMenuHtml(p.id, { onEdit: '_projOpenForm', onDuplicate: '_projDup', onDelete: '_projDelete' })}
        </div>
    `).join('');
}

// ── 案型：列上直接改（owner 2026-09-03「在專案表裡頭可以調整設定案型」）──
// 案型是私帳「預期毛利」表的鍵：改了案型，工時預算的建議就跟著換。字彙＝GET /project-types 那一份
//（settings ∪ 私帳毛利表）；列上目前的值不在清單裡也照列，不會被洗掉。沒案型的列標橘框提醒。
function _typeSelectHtml(p) {
    // 平時只畫文字（433 列 × 11 個 option ＝ 5,000 個節點，畫一次 4 秒）；點到那一格才變成下拉
    const cur = p.project_type || '';
    return `<span class="crm-row-type-txt${cur ? '' : ' is-empty'}" title="點一下改案型（預期毛利／工時預算建議照這個算）"
                onclick="event.stopPropagation();window._projTypeEdit('${p.id}', this)">${cur ? _esc(cur) : '— 案型 —'} ▾</span>`;
}
window._projTypeEdit = (id, span) => {
    const p = state.projects.find(x => x.id === id);
    const cur = (p && p.project_type) || '';
    const opts = [...new Set([..._projectTypes, ...(cur ? [cur] : [])])];
    const sel = document.createElement('select');
    sel.className = cur ? '' : 'is-empty';
    sel.setAttribute('data-no-search', '');
    sel.innerHTML = `<option value="">— 案型 —</option>${opts.map(t => `<option value="${_esc(t)}"${t === cur ? ' selected' : ''}>${_esc(t)}</option>`).join('')}`;
    sel.addEventListener('click', (e) => e.stopPropagation());
    sel.addEventListener('change', () => window._projSetType(id, sel));
    sel.addEventListener('blur', () => { sel.replaceWith(_spanOf(sel.value)); });
    const _spanOf = (v) => { const d = document.createElement('div'); d.innerHTML = _typeSelectHtml({ id, project_type: v }); return d.firstElementChild; };
    span.replaceWith(sel);
    sel.focus();
};
window._projSetType = async (id, sel) => {
    const p = state.projects.find(x => x.id === id);
    const prev = p ? p.project_type : '';
    if (p) p.project_type = sel.value;
    sel.classList.toggle('is-empty', !sel.value);
    try {
        await _fetch(`/projects/${id}`, { method: 'PUT', body: JSON.stringify({ project_type: sel.value }) });
        crmCacheInvalidate();
        crmToast(sel.value ? `案型改為「${sel.value}」` : '案型已清空');
    } catch (e) {
        if (p) p.project_type = prev;
        sel.value = prev || '';
        crmToast(e.message || '案型沒存', true);
    }
};

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

// 🔴 每一欄都要有對應的 #proj-f-<name> 元素（沒有的會被 openModal/saveProject
//    跳過，等於靜默不存）。備份三根（backup_*_root）是備份頁的正本 —— 備份頁
//    只顯示不編輯，只有這裡改得動。跟 folder_path 是不同的東西：folder_path 是
//    這個案的工作資料夾完整路徑，backup_* 是「根」（底下再用專案名開子資料夾）。
const _FIELDS = ['name', 'client_id', 'status', 'project_type', 'start_date', 'shoot_date',
    'completion_date', 'folder_path', 'description', 'am_username', 'notes',
    'backup_local_root', 'backup_nas_root', 'backup_proxy_root',
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
        // 🔴 視窗裡沒有這個欄位 → **不要送**（跟 openModal 的 `if (!el) continue` 對稱）。
        // 原本是 `el ? el.value.trim() : ''`，送空字串出去：後端 PUT 走
        // model_dump(exclude_unset=True)，有送就會寫 —— `shoot_date` 在 _FIELDS 裡
        // 但 crm-projects.html 沒有 #proj-f-shoot_date（拍攝日期已改由拍攝行事曆管），
        // 於是**按一次儲存就把拍攝日期洗成空的**，畫面上完全看不出來。
        // 使用者手動清空的情況不受影響：那時元素存在，照樣送 ''／null。
        if (!el) continue;
        let val = el.value.trim();
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


// ── 推送到私帳（一顆入口，三種情況分流）──────────────────────────
// owner 2026-09-12：「我希望私帳母帳可以連結 … 我在母帳建立專案可以推到私帳」
// ＋「有時候我會把母帳當私帳記」＋「有時候我只是拿專案費用，有時候走發票代開」。
// 三種情況是兩種病（docs/LEDGER_UNIFY_PLAN.md §8.7–8.8）：
//   1) 公司的案、公司付我一部分   → 分身（母帳留著，私帳多開一案，收入＝掛給我的成本行）
//   2) 我的案、客戶走公司代開發票 → 換帳本（案源＝代開發票；內部代開發票留在母帳掛過來）
//   3) 我的案、沒經過公司         → 換帳本（問案源）
// 使用者不必知道要按哪一顆：先問是哪一種，再分流到 _projMirrorMine／_projMoveLedger。
// 已連結的案直接進「重新同步」（同 _projMirrorMine 的 relink 分支）。
window._projPushMine = async function (id, linked) {
    if (linked) { return window._projMirrorMine(id); }
    const p = state.projects.find(x => x.id === id);
    const name = p ? p.name : '';
    const opt = (v, title, desc, checked) => `
        <label style="display:flex;gap:8px;align-items:flex-start;padding:8px 10px;border:1px solid #333;border-radius:6px;cursor:pointer;">
            <input type="radio" name="ppm-kind" value="${v}" ${checked ? 'checked' : ''} style="margin-top:3px;">
            <span><b style="color:#eee;">${title}</b><br>
                  <span style="color:#999;font-size:12px;">${desc}</span></span></label>`;
    _mirrorModal(`推送到私帳 — ${name}`, `
        <div style="color:#bbb;font-size:12px;margin-bottom:10px;">這一案是哪一種？</div>
        <div style="display:flex;flex-direction:column;gap:6px;font-size:13px;">
            ${opt('share', '公司的案，公司付我一部分',
                  '母帳留著跟客戶的合約；在私帳開一個對應的案，收入＝人員配置裡掛給你的成本行（沒有就先開 0）', true)}
            ${opt('passthrough', '我的案，客戶走公司代開發票',
                  '案源＝代開發票，代辦費自動算；公司開的「內部代開」發票掛在這一案上')}
            <div id="ppm-pt-sub" style="margin-left:26px;display:none;flex-direction:column;gap:4px;font-size:12px;color:#bbb;">
                <label style="display:flex;gap:6px;align-items:center;cursor:pointer;">
                    <input type="radio" name="ppm-pt" value="copy" checked>
                    公司也留一份帳 —— 母帳這案留著，在私帳開對應的案（收入＝母帳合約額）</label>
                <label style="display:flex;gap:6px;align-items:center;cursor:pointer;">
                    <input type="radio" name="ppm-pt" value="move">
                    整案搬到私帳 —— 公司帳上不留這個案（換帳本）</label>
            </div>
            ${opt('own', '我的案，沒有經過公司（記錯帳本）',
                  '整案搬到私帳，會問案源。公司帳上不會留下這個案')}
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="ppm-next">下一步</button>
        </div>`);
    // owner 2026-09-12「雖然是代開發票，但是專案公司也留一份帳」：代開那一項底下再分
    // 「留一份（分身，案源＝代開發票）」／「整案搬」。子選項只在選到代開時展開。
    const kindOf = () => (document.querySelector('input[name="ppm-kind"]:checked') || {}).value || 'share';
    const syncSub = () => {
        const sub = document.getElementById('ppm-pt-sub');
        if (sub) { sub.style.display = kindOf() === 'passthrough' ? 'flex' : 'none'; }
    };
    document.getElementById('proj-mirror-body').addEventListener('change', syncSub);
    syncSub();
    document.getElementById('ppm-next').addEventListener('click', () => {
        const kind = kindOf();
        const ptCopy = (document.querySelector('input[name="ppm-pt"]:checked') || {}).value !== 'move';
        window._projMirrorClose();
        if (kind === 'share') { return window._projMirrorMine(id); }
        if (kind === 'passthrough' && ptCopy) { return window._projMirrorMine(id, { source: '代開發票' }); }
        return window._projMoveLedger(id, { source: kind === 'passthrough' ? '代開發票' : '' });
    });
};

/** 已連結的案：問後端「私帳落後了沒」，把「重新同步」那顆改成講實話的字。
 *  判定正本在後端（core.ledger_project.mirror_stale）：True＝母帳成本行改了、
 *  False＝一致、null＝舊連結沒記過（同步一次就會記）—— 前端不自己比 Σsplit。 */
window._projMirrorStaleHint = async function (id, btn) {
    if (!btn) { return; }
    let chk;
    try { chk = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-check`); }
    catch (e) { return; }                      // 只是提示，拿不到就維持原字
    if (!btn.isConnected) { return; }          // 使用者已經切到別案
    if (chk.stale === true) {
        const d = chk.delta || 0;
        btn.textContent = `私帳落後 ${d > 0 ? '+' : ''}${fmtNum(d)} · 重新同步`;
        btn.style.color = '#fbbf24';
        btn.style.borderColor = '#7c5a12';
        btn.title = `母帳掛給你的成本行現在合計 ${fmtNum(chk.total)}，私帳上次同步時是 ${fmtNum(chk.total - d)}`;
    } else if (chk.stale === false) {
        btn.textContent = '已同步 · 重新同步';
        btn.title = `私帳跟母帳一致（掛給你的成本行合計 ${fmtNum(chk.total)}）`;
    }
};


// ── 換帳本（專案管理 ↔ 私帳）─────────────────────────────────────
// owner 2026-08-28：「可以有一個按鈕把專案推送至私帳（只有擁有私帳權限的人能用）」。
// 2026-09-12 起母帳側從 _projPushMine 的彈窗分流進來（「我的案、記錯帳本」那兩種）；
// 私帳側（已在私帳的案）仍是動作列上獨立的「搬回公司帳」。只對有 finance_mine 的帳號畫。
//
// 🔴 換帳本是全 repo「更新一律不得換帳本」的唯一例外，所以：
//   1) 先問後端「能不能搬」，把會擋住的東西講出來 —— 不要讓人按了才吃 409；
//   2) 動手前一定 confirm，並把「錢會跟著算到哪本帳」寫清楚；
//   3) 搬到私帳要問**案源**（代開發票→代辦費自動算）—— 後端拿它補齊私帳需要的欄位。
window._projMoveLedger = async function (id, opts = {}) {
    let chk;
    try {
        chk = await _fetch(`/projects/${encodeURIComponent(id)}/ledger-move-check`);
    } catch (e) {
        crmToast('查不到換帳本的狀態：' + e.message);
        return;
    }
    const toMine = chk.target === 'mine';
    if (!chk.can_move) {
        // 這句話的正本在後端（_blocked_reason）—— 前端再拼一份就會跟 409 的
        // 訊息漂成兩種說法。擋住的東西逐項列出來，人才知道要去哪裡處理。
        const rows = (chk.blockers || []).map(b => `<li>${_esc(b.what)} ${b.count} 筆</li>`).join('');
        _mirrorModal(`不能換帳本 — ${chk.name}`, `
            <div style="color:#fca5a5;font-size:13px;line-height:1.6;">${_esc(chk.reason || '這個專案不能換帳本')}</div>
            ${rows ? `<ul style="color:#ddd;font-size:12px;margin:10px 0 0 18px;">${rows}</ul>` : ''}
            <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
                ${toMine ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="pml-share"
                                    title="公司的案、公司付你一部分：在私帳開分身（母帳這案不動）">改用推送（分身）</button>` : ''}
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projMirrorClose()">知道了</button>
            </div>`);
        document.getElementById('pml-share')?.addEventListener('click', () => {
            window._projMirrorClose();
            window._projMirrorMine(id);
        });
        return;
    }
    if (!toMine) {
        const msg = `把「${chk.name}」搬回母公司帳？\n\n`
            + '錢流歸屬改回母公司，之後掛在它身上的錢都算公司的。';
        if (!window.confirm(msg)) { return; }
        return _projMoveSubmit(id, 'parent', '', null);
    }
    // 搬到私帳：問案源（confirm() 塞不下一個下拉）
    const src = opts.source || chk.source_default || '源日';
    const sources = (chk.source_options || ['源日', '代開發票', '執行業務所得']).map(o =>
        `<option value="${_esc(o)}"${o === src ? ' selected' : ''}>${_esc(o)}</option>`).join('');
    _mirrorModal(`搬到私帳 — ${chk.name}`, `
        <div style="color:#bbb;font-size:12.5px;line-height:1.6;">
            這個專案的錢流歸屬會改成私帳：之後掛在它身上的收支／發票／請款都算私帳的，
            母公司的三表不再計入它。專案管理仍看得到（標「後期專案」）。<br>
            ${chk.has_passthrough_invoice
                ? '<span style="color:#c4b5fd;">身上有「內部代開」發票 —— 會跟著掛在這一案上，案源預設「代開發票」。</span>'
                : '目前它身上沒有任何單據，搬過去不會動到任何一筆已記的帳。'}
        </div>
        <div style="margin-top:12px;display:flex;align-items:center;gap:8px;font-size:13px;">
            <span style="color:#ddd;">案源</span>
            <select class="crm-input" id="pml-source" style="width:180px;">${sources}</select>
            <span style="color:#777;font-size:11px;">代開發票＝代辦費自動算；源日＝現金收款</span>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="pml-go">搬到私帳</button>
        </div>`);
    document.getElementById('pml-go').addEventListener('click', (ev) =>
        _projMoveSubmit(id, 'mine', document.getElementById('pml-source').value, ev.currentTarget));
};

async function _projMoveSubmit(id, entity, source, btn) {
    if (btn) { btn.disabled = true; }
    try {
        await _fetch(`/projects/${encodeURIComponent(id)}/move-ledger`, {
            method: 'POST', body: JSON.stringify({ entity, source: source || null }),
        });
        window._projMirrorClose();
        crmToast(entity === 'mine' ? '已搬到私帳' : '已搬回公司帳');
        crmCacheInvalidate('/projects');
        await loadProjects();
        // 詳情面板要重畫（按鈕文字與帳本標記都變了）
        const p = state.projects.find(x => x.id === id);
        if (p) { callbacks.renderDetail?.(p); }
    } catch (e) {
        if (btn) { btn.disabled = false; }
        crmToast('換帳本失敗：' + e.message, 6000);
    }
}


// ── 分身（母公司專案 → 私帳的收入分身）──────────────────────────
// owner 2026-08-29：「費用要給王士源的，直接在私帳建立專案、同步收入」。
//
// 跟換帳本（搬家）是兩件事：這裡母公司那案原封不動，只是在私帳多開
// 一案，收入＝人員配置裡掛給我的那幾行成本。金額判定全在後端
// （core.ledger_project.mirror_lines），前端只顯示它算出來的明細 —— 前端自己
// 再加一次總和，兩個數字遲早會不一樣。
// 2026-09-12 起沒有掛給我的成本行**不擋**（先開 0，warning 講清楚）。
window._projMirrorMine = async function (id, mirrorOpts = {}) {
    let chk;
    try {
        chk = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-check`);
    } catch (e) {
        crmToast('查不到私帳連結的狀態：' + e.message, 6000);
        return;
    }
    if (chk.can_mirror === false) {
        // 舊後端（發版空窗）才會擋；這句話的正本在後端
        crmToast(chk.reason || '這一案不能推送到私帳', 6000);
        return;
    }
    const rows = (chk.lines || []).map(l => `<tr>
        <td style="color:#888;">${_esc(l.phase)}</td>
        <td>${_esc(l.item)}</td>
        <td style="text-align:right;">${fmtNum(l.amount)}</td></tr>`).join('');
    // 已經承接過別的 CRM 案的，在名稱後標出來 —— 一個私帳案可以承接多筆
    // （owner 2026-09-01），但覆蓋會洗掉別案的錢，要先看得見
    const opts = (chk.options || []).map(o =>
        `<option value="${o.id}">${_esc(o.name)}${o.amount ? ` — ${fmtNum(o.amount)}` : ''}${
            o.linked_count ? `（已連 ${o.linked_count} 案）` : ''}</option>`).join('');
    // 已經連結過 → 這次是**重新同步**（owner 2026-09-01「我 crm 有更新費用，
    // 但是私帳沒有連結過去」）。連結是連結當下的一次性複製，CRM 後來新增的
    // 成本行不會自己流過去。這時不給「建立新專案」—— 那會多一個分身，同一筆
    // 錢在私帳算兩次；目標鎖定原本那一案，怎麼合併由下面的模式鈕決定。
    const relink = !!chk.linked;
    // 案源＝代開發票的分身：收入是那張發票的面額（母帳合約額），不是掛給你的成本行
    const src = mirrorOpts.source || '';
    const ptProj = src === '代開發票' ? state.projects.find(x => x.id === id) : null;
    const ptNote = ptProj
        ? `<div style="color:#c4b5fd;font-size:12px;margin-bottom:8px;line-height:1.5;">案源＝代開發票：私帳這案的收入＝母帳合約額 ${
            fmtNum(ptProj.contract_amount || 0)}${ptProj.contract_amount ? '' : '（母帳還沒填合約額，先用下面的成本行合計）'}，代辦費照費率自動算。</div>`
        : '';
    const warn = chk.warning && !ptProj
        ? `<div style="color:#fbbf24;font-size:12px;margin-bottom:8px;line-height:1.5;">${_esc(chk.warning)}${
            chk.staff_bound === false ? '（這個帳號還沒綁人員檔案，認不出哪幾行是你的）' : ''}</div>`
        : '';
    _mirrorModal(`${relink ? '重新同步私帳' : '推送到私帳'} — ${chk.name}`, `
        ${ptNote}${warn}
        <div style="color:#bbb;font-size:12px;margin-bottom:8px;">
            公司要付給你的（來自人員配置的成本行）</div>
        <table class="crm-table" style="width:100%;font-size:12px;">${rows
            || '<tr><td colspan="3" style="color:#666;">（沒有掛給你的成本行）</td></tr>'}
            <tr><td colspan="2" style="font-weight:600;">私帳收入合計</td>
                <td style="text-align:right;font-weight:600;color:#86efac;">
                    ${fmtNum(chk.total)}</td></tr></table>
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:8px;font-size:13px;">
            ${relink ? `
            <div style="color:#c4b5fd;">已連結到私帳的「${_esc(chk.linked.name)}」</div>
            <input type="hidden" id="pmm-target" value="${_esc(chk.linked.id)}">` : `
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="pmm-mode" value="new" checked>
                在私帳建立新專案（客戶：${_esc(chk.client || '未指定')}／案源：${_esc(src || '源日')}）</label>
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="pmm-mode" value="link"> 連結到既有私帳專案</label>
            <div id="pmm-target-wrap" style="margin-left:22px;">
            <select class="crm-input" id="pmm-target" disabled>
                <option value="">— 搜尋私帳案 —</option>${opts}</select></div>`}
            <div id="pmm-conflict" style="${relink ? '' : 'margin-left:22px;'}"></div>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="pmm-go"
                    >${relink ? '用 CRM 更新' : '建立並連結'}</button>
        </div>`);
    // 模式只有一個真相：目前勾中的那顆 radio。`sel.disabled` 由它推導，
    // 不另外記一份（按鈕文字曾是第三份，改一處漏一處就會自相矛盾）。
    const sel = document.getElementById('pmm-target');
    const box = document.getElementById('proj-mirror-body');
    box.dataset.source = src;          // 衝突區那幾顆模式鈕送出時也要帶同一個案源
    if (sel.tagName === 'SELECT') {
        // 400 筆私帳案塞原生 select 找不到東西 —— 升級成可搜尋（同專案對應那頁）
        searchableSelect(sel, { placeholder: '搜尋私帳案…' });
    }
    const sync = () => {
        if (sel.tagName === 'SELECT') {
            const wrap = document.getElementById('pmm-target-wrap');
            const on = _pmmLink();
            sel.disabled = !on;
            if (wrap) { wrap.style.opacity = on ? '' : '0.45'; wrap.style.pointerEvents = on ? '' : 'none'; }
        }
        _pmmDrawConflict(chk, id);
        const go = document.getElementById('pmm-go');
        const conflict = document.getElementById('pmm-conflict');
        if (go) { go.style.display = conflict && conflict.innerHTML ? 'none' : ''; }
    };
    box.addEventListener('change', sync);
    // 開啟當下就畫一次：重新同步沒有 radio 不會有 change 事件（對照與模式鈕要先出來）；
    // 新連結則要把下拉先灰掉
    sync();
    document.getElementById('pmm-go').addEventListener('click',
        (ev) => _projMirrorSubmit(ev.currentTarget, id, '', src));
};

/** 這次要連到**既有**私帳案嗎。重新同步沒有 radio（目標鎖定原本那一案），
 *  所以沒有 radio 時看有沒有目標 —— 有就是連既有，不是「建立新專案」。 */
const _pmmLink = () => {
    const r = document.querySelector('input[name="pmm-mode"]:checked');
    return r ? r.value === 'link' : !!document.getElementById('pmm-target')?.value;
};

async function _projMirrorSubmit(btn, id, mode, source) {
    source = source || document.getElementById('proj-mirror-body')?.dataset.source || '';
    const target = _pmmLink() ? (document.getElementById('pmm-target').value || '') : '';
    if (_pmmLink() && !target) { crmToast('請先選一個要連結的私帳專案'); return; }
    btn.disabled = true;
    try {
        const r = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-to-mine`, {
            method: 'POST',
            body: JSON.stringify({ target_id: target, mode: mode || 'overwrite', source: source || null }),
        });
        window._projMirrorClose();
        crmToast(r.mode === 'keep' ? '已連結（私帳金額未變動）'
            : r.mode === 'import' ? `已從私帳匯入 ${r.imported} 個工項到 CRM 成本行`
                : r.mode === 'add' ? `已加進私帳：+${fmtNum(r.amount)}`
                    : `已在私帳同步收入 ${fmtNum(r.amount)}`);
        crmCacheInvalidate('/projects');
        await loadProjects();
        // 詳情面板要重畫（動作列從「推送到私帳」變成「已連結私帳 → 案名」）
        const p = state.projects.find(x => x.id === id);
        if (p) { callbacks.renderDetail?.(p); }
    } catch (e) {
        btn.disabled = false;
        crmToast('連結私帳失敗：' + e.message, 6000);
    }
}

/** 選到的那個私帳案已經填過工項 → 把兩邊並排列出來，讓人有依據可判斷，
 *  再給三個處理方式（owner 2026-08-30「跳出幾個選擇讓我決定要怎麼做」）。
 *
 *  🔴 沒有預設哪一個是對的：私帳那份可能是他照實際請款填的（比 CRM 準），
 *  也可能是舊的估算。只給三顆按鈕不給數字，等於要他憑印象賭一把。 */
function _pmmDrawConflict(chk, projectId) {
    const box = document.getElementById('pmm-conflict');
    if (!box) { return; }
    const id = document.getElementById('pmm-target').value || '';
    // 重新同步時目標就是已連結那一案（它自己帶著現有工項回來，不必去 options 找）
    const opt = chk.linked || (_pmmLink()
        ? (chk.options || []).find(o => o.id === id) : null);
    const mineSplit = (opt && opt.split) || {};
    // 🔴 「已經承接過別的 CRM 案」也要跳選擇 —— 它可能沒有工項明細，但它的
    // 合約金額裡已經有別案鏡射進來的錢，直接覆蓋就是把那筆洗掉。
    const shared = !!(opt && opt.linked_count);
    // 🔴 重新同步一定要畫：那正是「要怎麼合併」的決定點。私帳那案剛好沒工項
    // 時直接收掉選擇，就只剩一顆預設覆蓋的按鈕，等於幫他決定了。
    if (!chk.linked && !Object.keys(mineSplit).length && !shared) {
        box.innerHTML = ''; return;
    }

    // CRM 這側的工項合計用後端算好的 `crm_split`（mirror_lines 一次算出 lines
    // 與 split 兩份）—— 使用者就是拿這個數字跟私帳現有的並排做決定。
    const crmSplit = chk.crm_split || {};
    const keys = [...new Set([...Object.keys(mineSplit),
                              ...Object.keys(crmSplit)])];
    const sum = (o) => Object.values(o).reduce((n, v) => n + (v || 0), 0);
    const cell = (v) => (v ? fmtNum(v) : '<span style="color:#3f3f46;">—</span>');
    const rows = keys.map(k => `<tr>
        <td style="color:#ddd;">${_esc(k)}</td>
        <td style="text-align:right;">${cell(mineSplit[k])}</td>
        <td style="text-align:right;">${cell(crmSplit[k])}</td></tr>`).join('');
    const btn = (mode, label, title) =>
        `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-mode="${mode}"
                 title="${_esc(title)}">${label}</button>`;
    box.innerHTML = `
        <div style="margin-top:10px;border:1px solid #4c3d78;border-radius:6px;padding:10px;">
          <div style="color:#c4b5fd;font-size:12px;margin-bottom:6px;">
            「${_esc(opt.name)}」${chk.linked
                ? '是這一案的私帳分身 —— CRM 這邊改過之後要怎麼同步'
                : shared
                    ? `已經承接 ${opt.linked_count} 個 CRM 案的收入 —— 要怎麼處理`
                    : '已經填過工項 —— 要怎麼處理'}？</div>
          <table class="crm-table" style="width:100%;font-size:12px;">
            <tr><th style="text-align:left;">工項</th>
                <th style="text-align:right;">私帳現有</th>
                <th style="text-align:right;">CRM 成本行</th></tr>
            ${rows}
            <tr style="font-weight:600;"><td>合計</td>
                <td style="text-align:right;">${fmtNum(sum(mineSplit))}</td>
                <td style="text-align:right;">${fmtNum(sum(crmSplit))}</td></tr>
          </table>
          <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;">
            ${chk.linked ? '' : btn('add', '加進去',
                  '這個 CRM 案的錢**加**到私帳案上（同名工項相加、'
                  + '合約金額累加）—— 一個私帳案承接多筆時用這個')}
            ${btn('overwrite', chk.linked ? '用 CRM 更新' : '用 CRM 覆蓋', shared
                  ? '⚠ 私帳的金額換成這個 CRM 案算出來的 —— 已經承接的別案收入會被洗掉'
                  : chk.linked
                      ? '私帳的工項換成 CRM 現在算出來的（這就是「同步過去」）'
                      : '私帳的工項換成 CRM 成本行算出來的')}
            ${!chk.linked ? '' : btn('add', '再加一次',
                  '⚠ 很少用：把 CRM 這邊的金額**再加**到私帳現有的上面。'
                  + '同一案重新同步時通常是要「用 CRM 更新」—— 加會讓同一筆錢算兩次')}
            ${btn('keep', '保留私帳', '只建立連結，私帳的金額一毛不動')}
            ${btn('import', '從私帳匯入 CRM',
                  '反過來：把私帳的工項寫成 CRM 的成本行（掛給你、階段後期製作），'
                  + '私帳不動。匯入後那些成本行在 CRM 照常可以編。')}
          </div>
        </div>`;
    box.querySelectorAll('button[data-mode]').forEach((b) => {
        b.addEventListener('click', () =>
            _projMirrorSubmit(b, projectId, b.dataset.mode));
    });
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
