/**
 * crm-projects-core.js — 列表 + CRUD Modal + CSV 匯入
 *
 * 2026-09-13 拆檔：母帳 ↔ 私帳那一段（推送到私帳／換帳本／分身／彈窗；window._proj* 那幾支）
 * 住 crm-projects-ledger.js（由 crm-projects.js 以副作用 import 載入）。掃原始碼的測試用
 * `_srcscan.crm_projects_core_src()`（兩支串起來）。
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
                : ''}${p.entity !== 'mine' ? billingTagHtml(p)
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
    'contract_amount', 'tax_rate', 'profit_target_pct', 'misc_budget_pct', 'billing_mode',
    'payment_status', 'amount_receivable', 'amount_received', 'transfer_fee'];

// 收款方式（crm_projects.billing_mode；後端永不回 null）：源日自己怎麼收這一案的錢
export const BILLING_LABELS = { company: '源日專案', passthrough: '後期代開', cash: '現金收款' };
export const billingLabel = (p) => BILLING_LABELS[(p && p.billing_mode) || 'company'] || '源日專案';
/** 列表／詳情的收款方式小標：只畫後期代開／現金收款（源日專案是預設，每列都畫等於沒畫）。 */
export function billingTagHtml(p) {
    const m = (p && p.billing_mode) || 'company';
    if (m === 'company') return '';
    const color = m === 'passthrough' ? 'color:#f2c064;border-color:#8a5d10;' : 'color:#7dd3fc;border-color:#2d5a78;';
    const title = m === 'passthrough' ? '後期代開：你的後期案、客戶走源日開發票（私帳分身案源＝代開發票）' : '現金收款：源日收現金，沒有發票';
    return ` <span style="font-size:10px;border:1px solid;border-radius:3px;padding:0 4px;vertical-align:1px;${color}" title="${title}">${BILLING_LABELS[m]}</span>`;
}
/** 「後期連結」那一行（表單與詳情共用）：只畫後端 link_note 的 text ＋ 連結 ＋ 落後標籤，不自己拼句子。 */
export function linkNoteHtml(note) {
    if (!note || !note.text) return '<span style="color:#666;">—</span>';
    let html = _esc(note.text);
    if (note.linked && note.mine_id && note.mine_name) {
        // 鎖定「→ 案名」那一段換成連結：案名本身可能也出現在句子前段（案名就叫「後期」的話，
        // 裸 replace 會換到「已連結後期」裡的那兩個字）
        const name = _esc(note.mine_name);
        html = html.replace('→ ' + name, `→ <a href="/my-ledger.html?project=${encodeURIComponent(note.mine_id)}" target="_blank" rel="noopener" style="color:#8ab4f8;">${name} ↗</a>`);
    }
    if (note.linked && note.stale === true) {
        html += ' <span style="font-size:10px;color:#fca5a5;border:1px solid #7a2d2d;border-radius:3px;padding:0 4px;" title="母帳成本行改了之後還沒重新同步到私帳">私帳落後</span>';
    }
    return html;
}

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
            const defaults = { tax_rate: '5', profit_target_pct: '20', misc_budget_pct: '5', payment_status: '未到帳', billing_mode: 'company' };
            el.value = project ? (project[f] ?? '') : (defaults[f] ?? '');
        }
    }

    document.getElementById('proj-f-name').focus();

    // 後期連結（唯讀系統備註）：清單物件沒有 link_note，編既有案時打一次單筆補上（先開視窗再補）
    const noteEl = document.getElementById('proj-f-link-note');
    if (noteEl) {
        noteEl.innerHTML = project ? '<span style="color:#666;">載入中…</span>' : '儲存後依收款方式決定';
        if (project) {
            _fetch(`/projects/${encodeURIComponent(project.id)}`)
                .then((r) => { if (state.editingId === project.id) noteEl.innerHTML = linkNoteHtml(r && r.link_note); })
                .catch(() => { noteEl.innerHTML = '<span style="color:#666;">—</span>'; });
        }
    }
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
