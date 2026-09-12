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

import { crmFetch as _fetch, esc as _esc, setupResizeHandle, hasModule, today } from './crm-utils.js';
import * as _U from './crm-utils.js';   // permDeniedMsg 走命名空間（舊快取的 crm-utils 沒有它，named import 會炸整頁）
import { state, callbacks, EXPENSE_CATEGORIES } from './crm-projects-state.js';
import {
    loadProjects, loadClients, loadUsers, loadStaffList, createClientInline,
    renderList, selectProject, closeDetail,
    openModal, saveProject, deleteProject,
    openImportModal, setCsvFile, doImport,
    _hydrateProjectsFromCache,
} from './crm-projects-core.js';
import { renderDetail, initDetailHandlers } from './crm-projects-detail.js';
import { _loadFinancialSummary, _showExpenseForm, initCostHandlers } from './crm-projects-cost.js';
import { _loadProjectStaff, initFinanceHandlers } from './crm-projects-finance.js';
import { loadProjectQuotes, initQuoteHandlers } from './crm-projects-quotes.js';
// 完稿結案：元件搬到 tabs/proposals/（專案頁也用同一份，見 docs §15.2）——
// host 與 fetcher 由呼叫端注入，所以這裡把 CRM 這邊的兩樣東西一次綁好。
import { loadDeliveryTab, initDeliveryHandlers } from '../proposals/delivery-view.js';

/** CRM 這一側的完稿結案入口 —— 注入的內容兩個呼叫點完全一樣，寫一次就好。
 *  host 每次現查：與這個檔案其他分頁的慣例一致（面板是靜態 markup，但不必
 *  為此在模組層建立一個載入順序的耦合）。 */
const _openDelivery = (pid) => {
    const host = document.getElementById('proj-detail-delivery');
    // 官網上架編輯器（作品／編輯連結）要 website_admin；歸檔卡的建資料夾／掃描動 NAS，仍是管理員限定。
    // 元件住在 tabs/proposals/（公開頁的 import 封閉範圍），讀不到 crm-utils，所以由這裡注入。
    const r = loadDeliveryTab(pid, { host, fetcher: _fetch,
                                     canEditShowcase: hasModule('website_admin'),
                                     canManageFolders: (window._accessLevel || 0) >= 3 });
    // 收付檢查提示條放最上面（loadDeliveryTab 一開始就重設 innerHTML，之後只動它自己的區塊）
    loadClosingBanner(pid, host);
    return r;
};
import { loadProjectTypes } from './crm-projects-core.js';
import { loadPayTab, loadClosingBanner } from './crm-projects-pay.js';
import { loadCostGroups, renderGroupSwitcher, initCostGroupsHandlers } from './crm-projects-cost-groups.js';

// ── 回呼串接（解耦跨模組依賴） ──────────────────────────────

// 換專案時，當前開著的 lazy 分頁要跟著換內容 —— renderDetail 只畫資訊層＋財務摘要，
// 影像紀錄/參考影片/完稿結案/人員配置的內容是分頁點擊才載，直接切專案會殘留上一案。
// 只在專案 id 真的變了才重載（同專案的 renderDetail 重繪不動分頁，免清掉上傳佇列等現場）。
let _lastDetailId = null;
function _reloadActiveDetailTab(projectId) {
    const tab = document.querySelector('#proj-detail-tabs .crm-tab.active')?.dataset.tab;
    if (tab === 'media') _loadMediaTab(projectId, { fast: true });
    else if (tab === 'plan') _loadPlanTab(projectId);
    else if (tab === 'refs') _loadRefsTab(projectId);
    else if (tab === 'delivery') _openDelivery(projectId);
    else if (tab === 'team') loadPayTab(projectId);        // 收付款（人員配置＋發票併在一頁）
    // info/finance 由 renderDetail 涵蓋、quotes 由 selectProject 的 loadQuotations 涵蓋
}

callbacks.renderDetail = (project) => {
    renderDetail(project);
    if (project.id !== _lastDetailId) {
        _lastDetailId = project.id;
        _reloadActiveDetailTab(project.id);
    }
};
callbacks.renderList = renderList;
callbacks.loadProjects = loadProjects;
callbacks.loadQuotations = loadProjectQuotes;
callbacks.loadFinancialSummary = _loadFinancialSummary;
callbacks.loadPayTab = loadPayTab;
callbacks.closeDetail = closeDetail;
callbacks.loadCostGroups = loadCostGroups;
callbacks.renderGroupSwitcher = renderGroupSwitcher;

// ── Init ─────────────────────────────────────────────────────

export { loadProjects };

// ── 兩邊互通（owner 2026-08-25：「推至專案管理後兩邊互相連通、編修即時同步」）
// 同一列資料（crm_projects），所以「同步」= 切回來時拿最新的。比照提案庫
// _bindTabHook 的理由：清單在開頁那一刻定格，逐案損益那邊改的營收/結案日/
// 推送狀態不會通知這裡。有未存編修就整段跳過 —— 不吃掉使用者手上的東西。
let _tabHookBound = false;
function _bindTabHook() {
    if (_tabHookBound) return;
    _tabHookBound = true;
    document.addEventListener('tab-changed', async (e) => {
        if (e.detail?.tab !== 'tab_crm_projects') return;
        // 兩條交棒路：同一個 SPA 內走 sessionStorage；從獨立頁（/my-ledger.html 的
        // 「母帳：案名 ↗」）開新分頁過來走 ?project=（sessionStorage 跨分頁帶不過去）
        const qs = new URLSearchParams(location.search);
        const jump = sessionStorage.getItem('omgJumpCrmProject') || qs.get('project');
        if (!jump && (window._allDirtyCount?.() > 0)) return;
        if (!e.detail.fresh) await loadProjects();   // 自帶 renderList；剛載入的分頁 init 抓過了
        if (jump) {
            sessionStorage.removeItem('omgJumpCrmProject');
            if (qs.has('project')) {
                qs.delete('project');
                history.replaceState(null, '', location.pathname + (qs.toString() ? '?' + qs : '') + location.hash);
            }
            selectProject(jump);                 // 自帶未存編修 confirm
            document.querySelector(`#proj-list-body .crm-row[data-id="${jump}"]`)
                ?.scrollIntoView({ block: 'center' });
        } else if (state.selectedId) {
            // 詳情面板跟清單同一份 state —— 重抓後面板也換成新資料
            const p = state.projects.find((x) => x.id === state.selectedId);
            if (p) renderDetail(p);
        }
    });
}


export async function initCrmProjectsTab() {
    _bindTabHook();
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
                  <label>消費日</label>
                  <input id="exp-modal-date" type="date" class="crm-input" value="${today()}">
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
            expense_date: document.getElementById('exp-modal-date')?.value || '',
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
        } catch (e) { alert(_U.permDeniedMsg?.('專案管理', e) ?? ('儲存失敗：' + e.message)); }
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
        } catch (e) { alert(_U.permDeniedMsg?.('專案管理', e) ?? ('儲存失敗：' + e.message)); }
    };
    window._projDeleteExpense = async (id) => {
        if (!confirm('確定刪除此雜支？')) return;
        try {
            await _fetch('/project-expenses/' + id, { method: 'DELETE' });
            _loadFinancialSummary(state.selectedId);
        } catch (e) { alert(_U.permDeniedMsg?.('管理員', e) ?? ('刪除失敗：' + e.message)); }
    };

    // ── Staff handlers ──
    // 新增／移除派工都在共用元件裡（tabs/proposals/staff-view.js）—— 這裡原本
    // 有一整套 window._projAddStaff / _projConfirmStaff / _projRemoveStaff，
    // 那是元件搬出去之前的東西。兩套並存的話 CRM 會冒出兩個「新增」入口，
    // 而且刪除會確認兩次（元件問一次、這裡再問一次）。

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
    const _entityEl = document.getElementById('proj-filter-entity');
    _entityEl.value = state.filters.entity;   // 預設由 state 決定（見 _defaultEntity）
    _entityEl.addEventListener('change', e => {
        state.filters.entity = e.target.value;
        loadProjects();
    });

    // ── Buttons ──
    document.getElementById('proj-btn-add').addEventListener('click', () => openModal());
    document.getElementById('proj-btn-new-client')?.addEventListener('click', createClientInline);
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
            if (tab === 'team' && state.selectedId) { loadPayTab(state.selectedId); }
            document.getElementById('proj-detail-finance').classList.toggle('hidden', tab !== 'finance');
            document.getElementById('proj-detail-delivery').classList.toggle('hidden', tab !== 'delivery');
            if (tab === 'delivery' && state.selectedId) { _openDelivery(state.selectedId); }
            document.getElementById('proj-detail-plan').classList.toggle('hidden', tab !== 'plan');
            if (tab === 'plan' && state.selectedId) { _loadPlanTab(state.selectedId); }
            document.getElementById('proj-detail-media').classList.toggle('hidden', tab !== 'media');
            if (tab === 'media' && state.selectedId) { _loadMediaTab(state.selectedId); }
            document.getElementById('proj-detail-refs').classList.toggle('hidden', tab !== 'refs');
            if (tab === 'refs' && state.selectedId) { _loadRefsTab(state.selectedId); }
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
    const _canManageWebsite = hasModule('website_admin');

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
                // 官網製作收件匣（website_admin 專屬）；提案帶要明確清掉 ——
                // 這條路不走 loadProjects（strip 的咽喉），上一分頁的卡會殘留。
                _projView.style.display = 'none';
                _closingView.style.display = 'flex';
                _updateProposalStrip('結案');
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
            loadProjects();   // 提案帶由 loadProjects 尾端的 syncProposalStrip 一併更新
        });
    });

    // SWR: paint cached project list immediately so the user sees their
    // last-known data while the fresh fetch is still in-flight. Coworkers
    // used to see "找不到專案" briefly and assume nothing was there.
    _hydrateProjectsFromCache();

    // ── Load initial data ──（提案帶由 loadProjects 尾端自動同步）
    await Promise.all([loadClients(), loadUsers(), loadProjects(), loadStaffList(), loadProjectTypes()]);
}

// ── 提案庫進程帶（提案×專案整合）────────────────────────────
// 常態同步在 loadProjects 尾端（咽喉）；這個 helper 只給不走 loadProjects 的
// 結案收件匣分支用（帶明確 override 清空）。載入失敗不擋主內容。
async function _updateProposalStrip(statusOverride) {
    try {
        const mod = await import('./crm-projects-proposals.js');
        await mod.syncProposalStrip(statusOverride);
    } catch (e) { console.error('提案帶載入失敗:', e); }
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

// ── 參考影片 lazy loader（同影像紀錄的模式）───────────────────
async function _loadRefsTab(projectId) {
    const container = document.getElementById('proj-detail-refs');
    if (!container) return;
    try {
        const mod = await import('./crm-projects-refs.js');
        await mod.loadRefsTab(projectId, container);
    } catch (e) {
        container.innerHTML = `<div class="crm-empty" style="padding:24px;color:#fca5a5;">參考影片載入失敗: ${e.message}</div>`;
        console.error('參考影片載入失敗:', e);
    }
}

// ── 提案企劃 lazy loader（提案=專案合體）──────────────────────
async function _loadPlanTab(projectId) {
    const container = document.getElementById('proj-detail-plan');
    if (!container) return;
    try {
        const mod = await import('./crm-projects-plan.js');
        await mod.loadPlanTab(projectId, container);
    } catch (e) {
        container.innerHTML = `<div class="crm-empty" style="padding:24px;color:#fca5a5;">提案企劃載入失敗: ${e.message}</div>`;
        console.error('提案企劃載入失敗:', e);
    }
}

// ── 影像紀錄 lazy loader ─────────────────────────────────────
// 照 closing 的動態 import 模式；模組只 import 一次（瀏覽器快取），
// 但每次點擊都重跑 loadMediaTab —— 內容依當前選中的專案重新抓取。
async function _loadMediaTab(projectId, opts) {
    const container = document.getElementById('proj-detail-media');
    if (!container) return;
    try {
        const mod = await import('./crm-projects-media.js');
        await mod.loadMediaTab(projectId, container, opts);
    } catch (e) {
        container.innerHTML = `<div class="crm-empty" style="padding:24px;color:#fca5a5;">影像紀錄載入失敗: ${e.message}</div>`;
        console.error('影像紀錄載入失敗:', e);
    }
}
