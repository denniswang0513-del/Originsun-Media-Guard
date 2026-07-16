/**
 * crm-projects.js — CRM 專案管理入口
 * 串接所有子模組 + 註冊回呼 + 初始化事件
 *
 * 子模組：
 *   state.js   — 共享狀態 + 回呼登記
 *   calc.js    — 純財務計算（可被未來報表模組共用）
 *   core.js    — 列表 + CRUD Modal + CSV
 *   detail.js  — 詳情面板 + 報價 + 人員
 *   cost.js    — 成本 render + edit + 範本 + 計算
 *   finance.js — 預支 + 請款 + 雜支
 */

import { crmFetch as _fetch, esc as _esc, setupResizeHandle } from './crm-utils.js';
import { state, callbacks, EXPENSE_CATEGORIES } from './crm-projects-state.js';
import {
    loadProjects, loadClients, loadUsers, loadStaffList,
    renderList, selectProject, closeDetail,
    openModal, saveProject, deleteProject,
    openImportModal, setCsvFile, doImport,
    _hydrateProjectsFromCache,
} from './crm-projects-core.js';
import { renderDetail, initDetailHandlers } from './crm-projects-detail.js';
import { _loadFinancialSummary, _showExpenseForm, initCostHandlers } from './crm-projects-cost.js';
import { _loadCostStaff, _loadAdvances, _loadProjectStaff, initFinanceHandlers } from './crm-projects-finance.js';
import { loadProjectQuotes, initQuoteHandlers } from './crm-projects-quotes.js';
import { loadDeliveryTab, initDeliveryHandlers } from './crm-projects-delivery.js';
import { loadProjectTypes } from './crm-projects-core.js';
import { loadCostGroups, renderGroupSwitcher, initCostGroupsHandlers } from './crm-projects-cost-groups.js';

// ── 回呼串接（解耦跨模組依賴） ──────────────────────────────

callbacks.renderDetail = (project) => {
    renderDetail(project);
};
callbacks.renderList = renderList;
callbacks.loadProjects = loadProjects;
callbacks.loadQuotations = loadProjectQuotes;
callbacks.loadFinancialSummary = _loadFinancialSummary;
callbacks.loadCostStaff = _loadCostStaff;
callbacks.loadAdvances = _loadAdvances;
callbacks.closeDetail = closeDetail;
callbacks.loadCostGroups = loadCostGroups;
callbacks.renderGroupSwitcher = renderGroupSwitcher;

// ── Init ─────────────────────────────────────────────────────

export { loadProjects };

export async function initCrmProjectsTab() {
    // Move modals to body
    for (const id of ['proj-modal', 'proj-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    // ── Core handlers ──
    window._projSelect = selectProject;
    // _projEdit (cell-by-cell inline edit) is registered at module load by
    // crm-projects-detail.js. The kebab "✎ 編輯" path uses _projOpenForm
    // to open the full modal. Don't reuse the _projEdit name here — both
    // handlers were claiming the same global and the last-loaded one
    // (modal) silently no-op'd every cell click.
    window._projOpenForm = (id) => {
        const p = state.projects.find(x => x.id === id);
        if (p) openModal(p);
    };
    window._projDelete = (id) => {
        const p = state.projects.find(x => x.id === id);
        if (p) deleteProject(p);
    };
    window._projDup = async (id) => {
        const p = state.projects.find(x => x.id === id);
        if (!p) return;
        if (!confirm(`複製專案「${p.name}」？\n\n會連同預算、成本估算、雜支與派工名單一起複製；\n實際收付款、實際結算等數字會重置為新專案狀態。`)) return;
        try {
            const resp = await _fetch(`/projects/${id}/duplicate`, { method: 'POST' });
            await loadProjects();               // 直抓最新（loadProjects 不走快取）
            if (resp.project) selectProject(resp.project.id);
        } catch (e) {
            alert('複製失敗：' + (e.message || e));
        }
    };

    // ── Expense handlers (cross-module: use cost + state) ──
    window._projAddExpense = () => _showExpenseForm();
    window._projEditExpense = (id, cat, est, act, notes) => _showExpenseForm(id, cat, est, act, notes);
    window._projOpenExpensePage = () => {
        const p = new URLSearchParams();
        if (state.selectedId) p.set('preset_project', state.selectedId);
        if (state.selectedGroupId) p.set('preset_group', state.selectedGroupId);
        const qs = p.toString();
        window.open('/expense.html' + (qs ? '?' + qs : ''), '_blank', 'width=480,height=800');
    };
    window._projShowExpenseModal = () => {
        if (!state.selectedId) return;
        const proj = state.projects.find(p => p.id === state.selectedId);
        const projName = proj ? proj.name : '';
        let overlay = document.getElementById('expense-modal-overlay');
        if (overlay) overlay.remove();
        overlay = document.createElement('div');
        overlay.id = 'expense-modal-overlay';
        overlay.className = 'crm-modal-overlay';
        overlay.style.display = 'flex';
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
        overlay.innerHTML = `
          <div class="crm-modal" style="max-width:420px;">
            <div class="crm-modal-header">
              <h3>雜支登記</h3>
              <button onclick="document.getElementById('expense-modal-overlay').remove()" class="crm-detail-close">✕</button>
            </div>
            <div class="crm-modal-body">
              <div class="crm-field" style="margin-bottom:10px;">
                <label>專案</label>
                <input type="text" class="crm-input" value="${_esc(projName)}" disabled style="opacity:0.6;">
              </div>
              <div class="crm-form-grid">
                <div class="crm-field">
                  <label>類別</label>
                  <select id="exp-modal-cat" class="crm-input">
                    ${EXPENSE_CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join('')}
                  </select>
                </div>
                <div class="crm-field">
                  <label>細項</label>
                  <input id="exp-modal-sub" type="text" class="crm-input" placeholder="如：高鐵來回">
                </div>
                <div class="crm-field">
                  <label>金額</label>
                  <input id="exp-modal-act" type="number" class="crm-input" min="0" placeholder="0">
                </div>
                <div class="crm-field">
                  <label>請款人</label>
                  <select id="exp-modal-payee" class="crm-input">
                    <option value="">— 選擇人員 —</option>
                    ${state.staffList.map(s => `<option value="${_esc(s.name)}">${_esc(s.name)} (${_esc(s.role || '')})</option>`).join('')}
                  </select>
                </div>
                <div class="crm-field crm-field-full">
                  <label>備註</label>
                  <input id="exp-modal-notes" type="text" class="crm-input" placeholder="">
                </div>
              </div>
              <div class="crm-field" style="margin-top:8px;">
                <label>收據</label>
                <input id="exp-modal-receipt" type="file" accept="image/*,.pdf" class="crm-input" style="padding:4px;">
              </div>
            </div>
            <div class="crm-modal-footer">
              <button onclick="document.getElementById('expense-modal-overlay').remove()" class="crm-btn crm-btn-secondary">取消</button>
              <button onclick="window._projSaveExpenseModal()" class="crm-btn crm-btn-primary">儲存</button>
            </div>
          </div>`;
        document.body.appendChild(overlay);
    };
    window._projSaveExpenseModal = async () => {
        if (!state.selectedId) return;
        const payload = {
            category: document.getElementById('exp-modal-cat').value,
            sub_item: document.getElementById('exp-modal-sub').value,
            estimated: 0,
            actual: parseInt(document.getElementById('exp-modal-act').value) || 0,
            payee: document.getElementById('exp-modal-payee').value,
            notes: document.getElementById('exp-modal-notes').value,
            cost_group_id: state.selectedGroupId,
        };
        try {
            const r = await _fetch('/projects/' + state.selectedId + '/expenses', { method: 'POST', body: JSON.stringify(payload) });
            const expenseId = r.expense_id;
            const fileInput = document.getElementById('exp-modal-receipt');
            if (fileInput?.files?.[0] && expenseId) {
                const form = new FormData();
                form.append('file', fileInput.files[0]);
                const token = localStorage.getItem('auth_token');
                await fetch('/api/v1/crm/projects/' + state.selectedId + '/receipts/' + expenseId, {
                    method: 'POST', headers: token ? { 'Authorization': 'Bearer ' + token } : {}, body: form
                });
            }
            document.getElementById('expense-modal-overlay').remove();
            _loadFinancialSummary(state.selectedId);
        } catch (e) { alert('儲存失敗：' + e.message); }
    };
    window._projSaveExpense = async (editId) => {
        if (!state.selectedId) return;
        const payload = {
            category: document.getElementById('exp-f-cat').value,
            estimated: parseInt(document.getElementById('exp-f-est').value) || 0,
            actual: parseInt(document.getElementById('exp-f-act').value) || 0,
            notes: document.getElementById('exp-f-notes').value,
            cost_group_id: state.selectedGroupId,
        };
        try {
            let expenseId = editId;
            if (editId) {
                await _fetch('/project-expenses/' + editId, { method: 'PUT', body: JSON.stringify(payload) });
            } else {
                const r = await _fetch('/projects/' + state.selectedId + '/expenses', { method: 'POST', body: JSON.stringify(payload) });
                expenseId = r.expense_id;
            }
            const fileInput = document.getElementById('exp-f-receipt');
            if (fileInput?.files?.[0] && expenseId) {
                const form = new FormData();
                form.append('file', fileInput.files[0]);
                const token = localStorage.getItem('auth_token');
                await fetch('/api/v1/crm/projects/' + state.selectedId + '/receipts/' + expenseId, {
                    method: 'POST', headers: token ? { 'Authorization': 'Bearer ' + token } : {}, body: form
                });
            }
            _loadFinancialSummary(state.selectedId);
        } catch (e) { alert('儲存失敗：' + e.message); }
    };
    window._projDeleteExpense = async (id) => {
        if (!confirm('確定刪除此雜支？')) return;
        try {
            await _fetch('/project-expenses/' + id, { method: 'DELETE' });
            _loadFinancialSummary(state.selectedId);
        } catch (e) { alert(e.message); }
    };

    // ── Staff handlers (cross-module: use finance + state) ──
    window._projAddStaff = async () => {
        if (!state.selectedId) return;
        let staffList = [];
        try { staffList = (await _fetch('/staff?status=在職')).staff || []; } catch(_) {}
        if (staffList.length === 0) { alert('請先在人力資源 Tab 新增人員'); return; }
        const container = document.getElementById('proj-staff-list');
        if (!container) return;
        const formId = 'proj-staff-add-form';
        if (document.getElementById(formId)) return;
        const formHtml = `<div id="${formId}" style="padding:8px;background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;margin-bottom:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
            <select id="proj-staff-sel" class="crm-input" style="flex:1;min-width:120px;">
                <option value="">— 選擇人員 —</option>
                ${staffList.map(s => `<option value="${s.id}" data-role="${_esc(s.role)}">${_esc(s.name)} (${_esc(s.role)} $${s.daily_rate}/天)</option>`).join('')}
            </select>
            <input id="proj-staff-days" type="number" class="crm-input" value="1" min="1" style="width:60px;text-align:right;" placeholder="天數">
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projConfirmStaff()">確定</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('${formId}').remove()">取消</button>
        </div>`;
        container.insertAdjacentHTML('afterbegin', formHtml);
    };
    window._projConfirmStaff = async () => {
        const sel = document.getElementById('proj-staff-sel');
        const staffId = sel?.value;
        if (!staffId) { alert('請選擇人員'); return; }
        const role = sel.selectedOptions[0]?.dataset.role || '';
        const days = parseInt(document.getElementById('proj-staff-days')?.value) || 1;
        try {
            await _fetch('/projects/' + state.selectedId + '/staff', {
                method: 'POST', body: JSON.stringify({ staff_id: staffId, role_in_project: role, days })
            });
            _loadProjectStaff(state.selectedId);
        } catch (e) { alert('新增失敗：' + e.message); }
    };
    window._projRemoveStaff = async (psId, projectId) => {
        if (!confirm('確定移除此派工？')) return;
        try {
            await _fetch('/project-staff/' + psId, { method: 'DELETE' });
            _loadProjectStaff(projectId);
        } catch (e) { alert(e.message); }
    };

    // ── Sub-module handlers ──
    initDetailHandlers();
    initCostHandlers();
    initFinanceHandlers();
    initQuoteHandlers();
    initDeliveryHandlers();
    initCostGroupsHandlers();

    // ── Search + filters ──
    let _searchTimer;
    document.getElementById('proj-search').addEventListener('input', e => {
        state.filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadProjects, 300);
    });
    document.getElementById('proj-filter-status').addEventListener('change', e => {
        state.filters.status = e.target.value;
        loadProjects();
    });
    document.getElementById('proj-filter-client').addEventListener('change', e => {
        state.filters.client_id = e.target.value;
        loadProjects();
    });
    document.getElementById('proj-filter-am').addEventListener('change', e => {
        state.filters.am = e.target.value;
        loadProjects();
    });

    // ── Buttons ──
    document.getElementById('proj-btn-add').addEventListener('click', () => openModal());
    document.getElementById('proj-btn-import').addEventListener('click', openImportModal);
    document.getElementById('proj-btn-save').addEventListener('click', saveProject);
    document.getElementById('proj-detail-close').addEventListener('click', closeDetail);
    document.getElementById('proj-btn-do-import').addEventListener('click', doImport);

    // ── CSV file input + drop zone ──
    document.getElementById('proj-csv-file').addEventListener('change', e => {
        setCsvFile(e.target.files[0] || null);
    });
    const zone = document.getElementById('proj-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) setCsvFile(file);
    });

    // ── Detail sub-tabs ──
    document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('proj-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('proj-detail-quotes').classList.toggle('hidden', tab !== 'quotes');
            document.getElementById('proj-detail-team').classList.toggle('hidden', tab !== 'team');
            if (tab === 'quotes' && state.selectedId) { loadProjectQuotes(state.selectedId); }
            if (tab === 'team' && state.selectedId) { _loadCostStaff(state.selectedId); _loadAdvances(state.selectedId); }
            document.getElementById('proj-detail-finance').classList.toggle('hidden', tab !== 'finance');
            document.getElementById('proj-detail-delivery').classList.toggle('hidden', tab !== 'delivery');
            if (tab === 'delivery' && state.selectedId) { loadDeliveryTab(state.selectedId); }
        });
    });

    // ── Modal overlay click to close ──
    for (const id of ['proj-modal', 'proj-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('proj-resize-handle', 'proj-detail-panel');

    // ── RBAC: 管理員或擁有 website_admin 模組者，「結案」分頁顯示官網製作收件匣；
    //   其他人「結案」分頁＝一般「結案」狀態清單。權限旗標由 auth-state.js 掛在
    //   window（_accessLevel / _modules）。後端仍會再閘一次。「結案」分頁對所有人可見。
    const _canManageWebsite = (window._accessLevel >= 3) ||
        (Array.isArray(window._modules) && window._modules.includes('website_admin'));

    // ── Sub-tab switching (總表 + 8 階段管線；報價總覽已獨立成左側欄 tab） ──
    //   總表：不篩狀態、顯示工具列狀態下拉。
    //   單一階段：分頁本身即篩選（server ?status= 精確比對），隱藏工具列狀態下拉。
    //   結案：website_admin → 官網製作收件匣；其他人 → 一般「結案」狀態清單。
    const _statusFilterEl = document.getElementById('proj-filter-status');
    const _projView = document.getElementById('proj-view-projects');
    const _closingView = document.getElementById('proj-view-closing');
    const _subTabs = document.querySelectorAll('#proj-sub-tabs .crm-sub-tab');
    _subTabs.forEach(btn => {
        btn.addEventListener('click', () => {
            _subTabs.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const view = btn.dataset.view;
            const st = btn.dataset.status || '';

            if (view === 'closing' && _canManageWebsite) {
                // 官網製作收件匣（website_admin 專屬）
                _projView.style.display = 'none';
                _closingView.style.display = 'flex';
                _initClosingProduction();
                return;
            }

            // 一般清單視圖：總表 / 單一階段 / 非 website_admin 的「結案」。
            // 總表顯示狀態下拉;單一階段分頁本身即篩選,隱藏下拉。
            _projView.style.display = 'flex';
            _closingView.style.display = 'none';
            state.filters.status = (view === 'all') ? '' : st;
            if (_statusFilterEl) {
                _statusFilterEl.style.display = (view === 'all') ? '' : 'none';
                if (view === 'all') _statusFilterEl.value = '';
            }
            loadProjects();
        });
    });

    // SWR: paint cached project list immediately so the user sees their
    // last-known data while the fresh fetch is still in-flight. Coworkers
    // used to see "找不到專案" briefly and assume nothing was there.
    _hydrateProjectsFromCache();

    // ── Load initial data ──
    await Promise.all([loadClients(), loadUsers(), loadProjects(), loadStaffList(), loadProjectTypes()]);
}

// ── 結案（官網製作收件匣）lazy loader ─────────────────────
// closing 自繪整個 DOM，故 loader 只負責動態 import 模組 + 呼叫
// init(container)，用 flag 保證只初始化一次。
let _closingLoaded = false;
async function _initClosingProduction() {
    const container = document.getElementById('proj-view-closing');
    if (!container) return;
    if (_closingLoaded) return;
    try {
        const mod = await import('./crm-projects-closing.js');
        await mod.init(container);
        _closingLoaded = true;
    } catch (e) {
        container.innerHTML = `<div class="crm-empty" style="padding:24px;color:#fca5a5;">結案收件匣載入失敗: ${e.message}</div>`;
        console.error('結案收件匣載入失敗:', e);
    }
}
