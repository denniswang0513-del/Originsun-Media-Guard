/**
 * crm-projects-detail.js — 詳情面板渲染 (stage-aware) + cell-by-cell inline edit
 *
 * 每個欄位點擊即可編輯，blur → 寫 _projDirtyMap → 1s debounce auto-save
 * （由 cost.js 的 auto-save 機制統一處理）。沒有「✎ 編輯」模式。
 */

import { state, callbacks, STATUS_ORDER, PRESALE_STATUSES, CLOSED_STATUSES } from './crm-projects-state.js';
import { _badge, _avatar, getProjectTypes } from './crm-projects-core.js';
import { calcDashboard, remainColor, profitColor, barColor } from './crm-projects-calc.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, pickFolderPath, searchableSelect } from './crm-utils.js';

// ── Edit Fields ────────────────────────────────────────────

// AM (業務) and PM (專案經理) options come from 人力資源 (crm_staff),
// not the system users list. Stored value = staff name (the column is a
// generic text field, originally `username` but now used for any assigned
// person — legacy rows with system usernames continue to render as-is).
function _staffOptions(includePlaceholder = false) {
    const opts = (state.staffList || []).map(s => ({
        value: s.name,
        label: s.role ? `${s.name} (${s.role})` : s.name,
    }));
    return includePlaceholder ? [{value:'',label:'— 未指派 —'}, ...opts] : opts;
}

function _buildEditFields() {
    const clientOpts = [{value:'',label:'— 選擇客戶 —'}].concat(
        state.clients.map(c => ({value: c.id, label: c.short_name}))
    );
    const amOpts = _staffOptions(true);
    return [
        {name:'client_id', label:'客戶', type:'select', options: clientOpts},
        {name:'name', label:'專案名稱', type:'text'},
        {name:'status', label:'狀態', type:'select',
         options: STATUS_ORDER.map(s => ({value:s, label:s}))},
        {name:'project_type', label:'類型', type:'select', get options() {
            return [{value:'',label:'—'}, ...getProjectTypes().map(t => ({value:t,label:t}))];
        }},
        {name:'am_username', label:'AM', type:'select', options: amOpts},
        // PM is single-select but the DB column is a JSON array — wrap on
        // commit (`[value]` / `[]`) and unwrap on read (first element). Lets
        // us flip UX without a DB migration.
        {name:'pm_usernames', label:'PM', type:'select', options: amOpts, listWrap: true},
        {name:'start_date', label:'起始日', type:'date'},
        {name:'completion_date', label:'結案日', type:'date'},
        {name:'folder_path', label:'資料夾', type:'folder'},
        {name:'description', label:'說明', type:'textarea'},
        {name:'notes', label:'備註', type:'textarea'},
        {name:'contract_amount', label:'合約金額（含稅）', type:'number'},
        {name:'tax_rate', label:'稅率(%)', type:'number'},
        {name:'profit_target_pct', label:'目標毛利率(%)', type:'number'},
        {name:'misc_budget_pct', label:'雜支比例(%)', type:'number'},
        {name:'payment_status', label:'帳務狀況', type:'select', options:[
            {value:'未到帳',label:'未到帳'},{value:'部分到帳',label:'部分到帳'},{value:'全額到帳',label:'全額到帳'},
        ]},
        {name:'amount_receivable', label:'應收帳款', type:'number'},
        {name:'amount_received', label:'已收帳款', type:'number'},
        {name:'transfer_fee', label:'帳款匯費', type:'number'},
    ];
}

// ── Cell-by-cell inline edit (replaces the old enableInlineEdit modal) ──

// Fields whose new value flips a badge / stage card / budget chart — these
// require a full renderDetail to repaint dependent UI. Other fields (numbers,
// text, paths) only update one cell so we can mutate it in place and skip the
// expensive re-render + 4-endpoint refetch that loadFinancialSummary triggers.
const RENDER_DEFER_MS = 150;
const FIELDS_REQUIRING_FULL_RENDER = new Set([
    'status',           // badge color + stage card + budget visibility
    'start_date',       // stage card pre-project vs in-progress
    'completion_date',  // stage card closed banner
    'client_id',        // header chip + summary cell
    'project_type',     // summary cell + form
    'payment_status',   // payment badge color
    'pm_usernames',     // multi-avatar display
    'am_username',      // avatar display
    'contract_amount',  // budget chart depends on this
    'profit_target_pct',
    'misc_budget_pct',
    'tax_rate',
]);

// Defer renderDetail() after a commit so clicks on sibling buttons fire first.
// Otherwise: mousedown on button → blur on input → renderDetail destroys DOM →
// click never reaches the button. 150ms is below human reaction perception
// and long enough for the same-mousedown click to dispatch. If the user starts
// another edit in this window, _cancelPendingRender wipes it.
let _pendingRenderTimer = null;
function _cancelPendingRender() {
    if (_pendingRenderTimer) { clearTimeout(_pendingRenderTimer); _pendingRenderTimer = null; }
}
function _scheduleRender(project) {
    _cancelPendingRender();
    _pendingRenderTimer = setTimeout(() => {
        _pendingRenderTimer = null;
        renderDetail(project);
    }, RENDER_DEFER_MS);
}

window._projEdit = function(cell) {
    if (cell.querySelector('input, select, textarea')) return;
    _cancelPendingRender();
    const field = cell.dataset.field;
    if (!field) return;
    const fieldDef = _buildEditFields().find(f => f.name === field);
    if (!fieldDef) return;
    const project = state.projects.find(p => p.id === state.selectedId);
    if (!project) return;
    const rawOrig = project[field];
    // listWrap fields (pm_usernames) store as JSON array but render as
    // single-select — unwrap to the first element for input population.
    const orig = fieldDef.listWrap
        ? (Array.isArray(rawOrig) && rawOrig.length > 0 ? rawOrig[0] : '')
        : rawOrig;
    const t = fieldDef.type;

    let input;
    if (t === 'select') {
        input = document.createElement('select');
        const opts = fieldDef.options;  // getter resolves
        opts.forEach(o => {
            const opt = document.createElement('option');
            opt.value = o.value;
            opt.textContent = o.label;
            if (String(o.value) === String(orig ?? '')) opt.selected = true;
            input.appendChild(opt);
        });
    } else if (t === 'date') {
        input = document.createElement('input');
        input.type = 'date';
        input.value = orig ? String(orig).substring(0, 10) : '';
    } else if (t === 'number') {
        input = document.createElement('input');
        input.type = 'number';
        input.min = '0';
        input.value = orig ?? '';
    } else if (t === 'textarea') {
        input = document.createElement('textarea');
        input.rows = 3;
        input.value = orig ?? '';
    } else {
        input = document.createElement('input');
        input.type = 'text';
        input.value = orig ?? '';
    }
    input.className = 'crm-input';
    input.style.cssText += 'min-width:80px;padding:2px 6px;font-size:inherit;width:100%;box-sizing:border-box;';
    cell.innerHTML = '';
    cell.appendChild(input);

    let _committed = false;
    const commit = () => {
        if (_committed) return;
        _committed = true;
        let val = input.value;
        if (t === 'number') val = val === '' ? null : parseInt(val);
        if (t === 'date' || t === 'month') val = val || null;
        // listWrap: single-select UI but DB column is a JSON array. Wrap
        // before sending so backend gets `["王士源"]` or `[]` as expected.
        const stored = fieldDef.listWrap ? (val ? [val] : []) : val;
        const changed = fieldDef.listWrap
            ? JSON.stringify(stored) !== JSON.stringify(rawOrig || [])
            : (val ?? null) !== (orig ?? null);
        // 提案=專案合體：來自提案的專案轉「未成案」必附原因（組織學習欄），
        // 後端 hook 會 422 擋；這裡先問，取消就還原不送
        if (changed && field === 'status' && val === '未成案' && project.proposal_status) {
            const reason = prompt('此專案來自提案 — 未成案原因（必填，組織學習欄）', '');
            if (reason === null || !reason.trim()) {
                cell.innerHTML = _projDisplayValue(field, orig, fieldDef);
                return;
            }
            window._projDirtyMap['outcome_reason'] = reason.trim();
        }
        // 收付軟擋（owner 2026-09-04）：推到結案／歸檔時，收付沒結清就列出來要人確認；取消就還原不送
        if (changed && field === 'status' && CLOSED_STATUSES.includes(val) && !CLOSED_STATUSES.includes(orig) && window._projPay?.confirmClosing) {
            cell.innerHTML = _projDisplayValue(field, val, fieldDef);
            window._projPay.confirmClosing(project.id).then((ok) => {
                if (!ok) { cell.innerHTML = _projDisplayValue(field, orig, fieldDef); return; }
                window._projDirtyMap[field] = stored;
                project[field] = stored;
                window._costScheduleAutoSave?.();
            });
            return;
        }
        if (changed) {
            window._projDirtyMap[field] = stored;
            project[field] = stored;
            // 客戶是 id 欄、畫面顯示的是 client_short_name — 不同步的話補完
            // 客戶詳情仍顯示「—」，看起來像沒存到（合體後補客戶是常見動線）
            if (field === 'client_id') {
                project.client_short_name =
                    state.clients.find(c => c.id === val)?.short_name || '';
            }
            window._costScheduleAutoSave?.();
        }
        // Only re-render when the field affects badge / stage card / budget /
        // sibling cells. For plain-text/number/path fields we just paint the
        // cell locally — saves the loadFinancialSummary 4-endpoint cascade.
        if (FIELDS_REQUIRING_FULL_RENDER.has(field)) {
            _scheduleRender(project);
        } else {
            cell.innerHTML = _projDisplayValue(field, val, fieldDef);
        }
    };

    if (t === 'select') {
        // Type-to-search wrapper (same widget the modal client field uses).
        // Hides the native select, shows a text input + filtered panel.
        searchableSelect(input, { placeholder: '搜尋' + (fieldDef.label || '') + '...' });
        const searchInput = cell.querySelector('.ss-input');
        if (searchInput) {
            searchInput.focus();
            searchInput.select?.();
            // Click-outside without picking → still commit (no-op if unchanged).
            searchInput.addEventListener('blur', commit);
            searchInput.addEventListener('keydown', e => {
                if (e.key === 'Escape') {
                    _committed = true;  // suppress the upcoming blur-commit
                    cell.innerHTML = _projDisplayValue(field, orig, fieldDef);
                }
            });
        }
        // searchableSelect dispatches `change` on the hidden select on pick.
        input.addEventListener('change', commit);
    } else {
        input.focus();
        if (input.select) input.select();
        input.addEventListener('blur', commit);
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && t !== 'textarea') { e.preventDefault(); input.blur(); }
            if (e.key === 'Escape') {
                _committed = true;
                cell.innerHTML = _projDisplayValue(field, orig, fieldDef);
            }
        });
    }
};

// Format a project field value for the inline cell display (mirrors what
// renderDetail would produce for that cell). Used after non-rendering edits.
function _projDisplayValue(field, val, fieldDef) {
    const t = fieldDef.type;
    const empty = '<span class="pi-empty">—</span>';
    if (val === null || val === undefined || val === '') return empty;
    if (t === 'select') {
        const opt = (fieldDef.options || []).find(o => String(o.value) === String(val));
        return _esc(opt ? opt.label : String(val));
    }
    if (t === 'date') return _esc(String(val).substring(0, 10));
    if (t === 'number') {
        if (field === 'tax_rate' || field === 'profit_target_pct' || field === 'misc_budget_pct') {
            return val + '%';
        }
        return '$' + fmtNum(val);
    }
    return _esc(String(val));
}

// (removed _projEditPm — PM is now a single-select handled by _projEdit
// with listWrap=true; the multi-select popover is no longer needed.)

// Folder picker for inline-editable folder fields.
window._projEditFolder = async function(field) {
    const project = state.projects.find(p => p.id === state.selectedId);
    if (!project) return;
    const path = await pickFolderPath(project[field] || '');
    if (!path || path === project[field]) return;
    window._projDirtyMap[field] = path;
    project[field] = path;
    window._costScheduleAutoSave?.();
    _scheduleRender(project);
};

// HTML helper: wrap a value in a click-to-edit cell. Hover affordance + cursor
// come from the .pi-edit-cell CSS class.
function _editCell(field, displayHtml) {
    return `<span class="pi-edit-cell" data-field="${field}" onclick="window._projEdit(this)">${displayHtml}</span>`;
}

// ── Detail Panel Rendering (stage-aware) ──────────────────

function renderDetail(project) {
    const _title = document.getElementById('proj-detail-title');
    _title.textContent = project.name;
    // 「開啟專案頁 ↗」—— 製作期的工作面（進度五軌／創意發想／企劃書／報價單／
    // 會議記錄／資料夾／人員配置）在那一頁，而且它是白底獨立頁、網址可以直接
    // 給同事。掛在標題列而不是某個分頁裡：不管你正在看哪一格，出口都在同一個
    // 位置。開新分頁：CRM 詳情面板裡常有編到一半的成本列，原地導覽會把它帶走。
    //
    // owner 2026-08-15 選 B（docs/PROPOSAL_PLANNER.md §15.5）：CRM **保留**
    // 全部入口，只是共用的分頁一律同一份元件（人員配置／完稿結案／提案企劃
    // 都是），不各寫一份。所以這顆是「換個工作面」而不是「去看被搬走的東西」。
    const _go = document.createElement('a');
    _go.className = 'crm-btn crm-btn-secondary crm-btn-sm';
    _go.style.cssText = 'margin-left:10px;font-size:10px;padding:2px 8px;';
    _go.target = '_blank';
    _go.rel = 'noopener';
    _go.href = '/project.html?id=' + encodeURIComponent(project.id);
    _go.textContent = '開啟專案頁 ↗';
    _title.appendChild(_go);
    // 私帳案（owner 才看得到這顆）：跳到財務管理的逐案損益 —— 工項/費用/
    // 實收檢查在那邊編。交棒走 sessionStorage，finance.js 的 tab-changed
    // handler 接住後切子視圖並開同一案。
    if (_isMineProject(project) && (window._modules || []).includes('finance_mine')
            && typeof window.switchTab === 'function') {
        const _led = document.createElement('a');
        _led.className = 'crm-btn crm-btn-secondary crm-btn-sm';
        _led.style.cssText = 'margin-left:6px;font-size:10px;padding:2px 8px;cursor:pointer;';
        _led.textContent = '執行專案 ↗';
        _led.title = '在財務管理的「執行專案」開啟這一案（私帳的工項與費用在那邊編）';
        _led.onclick = () => {
            sessionStorage.setItem('omgJumpLedgerProject', project.id);
            window.switchTab('tab_crm_invoices');
        };
        _title.appendChild(_led);
    }

    const _pBadge = (status) => {
        const map = {'未到帳':'crm-badge crm-pay-未到帳','部分到帳':'crm-badge crm-pay-部分到帳','全額到帳':'crm-badge crm-pay-全額到帳'};
        return `<span class="${map[status] || 'crm-badge'}">${_esc(status || '未到帳')}</span>`;
    };

    // Tab 1: 專案資訊 — stage-aware layout
    const _$ = (n) => n ? '$' + fmtNum(n) : '—';
    const _d = (v) => v ? _esc(String(v)) : '<span class="pi-empty">—</span>';
    const _folderBtn = (path) => path
        ? ` <button class="crm-btn crm-btn-secondary crm-btn-sm _open-folder-btn" data-folder-path="${_esc(path)}" style="padding:1px 5px;font-size:10px;">&#128193;</button>`
        : '';

    // People info — AM/PM are now click-editable inside the contract line.
    const _placeholder = (txt) => `<span class="pi-empty">${txt || '—'}</span>`;
    const _amHtml = project.am_username
        ? `<span class="pi-person"><span class="pi-person-role">AM</span>${_avatar(project.am_username, 20)} ${_esc(project.am_username)}</span>`
        : `<span class="pi-person"><span class="pi-person-role">AM</span>${_placeholder('+ 指派')}</span>`;
    const _pmList = (project.pm_usernames || []);
    const _pmHtml = _pmList.length > 0
        ? _pmList.map(u => `<span class="pi-person"><span class="pi-person-role">PM</span>${_avatar(u, 20)} ${_esc(u)}</span>`).join('')
        : `<span class="pi-person"><span class="pi-person-role">PM</span>${_placeholder('+ 指派')}</span>`;

    // Determine stage
    const isPreProject = PRESALE_STATUSES.includes(project.status);
    const isClosed = CLOSED_STATUSES.includes(project.status);

    document.getElementById('proj-detail-info').innerHTML = `
      <div class="pi-wrap">

        <!-- Layer 1: 摘要橫條 (every cell is click-to-edit) -->
        <div class="pi-summary">
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_editCell('client_id', _d(project.client_short_name))}</div>
            <div class="pi-summary-label">客戶</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_editCell('project_type', _d(project.project_type))}</div>
            <div class="pi-summary-label">類型</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_editCell('status', _badge(project.status))}</div>
            <div class="pi-summary-label">狀態</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_editCell('start_date', _d(project.start_date ? project.start_date.substring(0, 10) : ''))}</div>
            <div class="pi-summary-label">起始日</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_editCell('completion_date', _d(project.completion_date ? project.completion_date.substring(0, 10) : ''))}</div>
            <div class="pi-summary-label">結案日</div>
          </div>
        </div>

        <!-- Layer 1b: 人員 + 合約帳務 (always rendered for inline edit) -->
        <div class="pi-contract-line">
          <span class="pi-edit-cell" data-field="am_username" onclick="window._projEdit(this)" style="cursor:pointer;">${_amHtml}</span>
          <span class="pi-edit-cell" data-field="pm_usernames" onclick="window._projEdit(this)" style="cursor:pointer;">${_pmHtml}</span>
          <span class="pi-dot"></span>
          ${_isMineProject(project) ? _MINE_MONEY_NOTICE : `
          <span>合約 <b style="color:#60a5fa;">${_editCell('contract_amount', _$(project.contract_amount))}</b></span>
          <span class="pi-dot"></span>
          <span>稅率 ${_editCell('tax_rate', (project.tax_rate != null ? project.tax_rate : 5) + '%')}</span>
          <span class="pi-dot"></span>
          <span>應收 <b style="color:#fbbf24;">${_editCell('amount_receivable', _$(project.amount_receivable))}</b></span>
          <span class="pi-dot"></span>
          <span>已收 <b style="color:${(project.amount_received || 0) >= (project.amount_receivable || 1) ? '#86efac' : '#d1d5db'};">${_editCell('amount_received', _$(project.amount_received))}</b></span>
          <span class="pi-dot"></span>
          <span>匯費 ${_editCell('transfer_fee', project.transfer_fee ? '$' + fmtNum(project.transfer_fee) : _placeholder('—'))}</span>
          <span class="pi-dot"></span>
          ${_editCell('payment_status', _pBadge(project.payment_status))}`}
          <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:auto;padding:2px 10px;font-size:11px;"
                  onclick="window._projOpenForm('${project.id}')" title="編輯所有專案資訊">✎ 編輯</button>
        </div>

        <!-- Layer 2: Stage card (conditional) -->
        <div id="pi-stage-card"></div>

        <!-- Layer 2.5: 提案來源（async；有成案回填的提案才出現） -->
        <div id="pi-proposal-src"></div>

        <!-- Layer 3: 預算儀表板（async，製作/結案/歸檔） -->
        <div id="pi-budget-row"></div>

        <!-- Layer 5: 補充資訊 (always rendered with placeholders) -->
        <div class="pi-section-title">補充資訊</div>
        <div class="pi-details-card">
          <div class="pi-det-text">${_editCell('description', project.description ? _esc(project.description) : _placeholder('+ 加說明'))}</div>
          <div class="pi-det-paths">
            <div class="pi-det-path">&#128193; ${_editCell('folder_path', project.folder_path ? _esc(project.folder_path) : _placeholder('+ 加專案資料夾'))} <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projEditFolder('folder_path')" style="padding:1px 5px;font-size:10px;" title="瀏覽選擇">📁</button>${project.folder_path ? `<button class="crm-btn crm-btn-secondary crm-btn-sm _open-folder-btn" data-folder-path="${_esc(project.folder_path)}" style="padding:1px 5px;font-size:10px;" title="開啟">↗</button>` : ''}</div>
          </div>
          <div class="pi-det-note">${_editCell('notes', project.notes ? _esc(project.notes) : _placeholder('+ 加備註'))}</div>
        </div>

      </div>
    `;

    // 提案來源（async best-effort；沒有就整塊不出現）
    import('./crm-projects-proposals.js')
        .then(m => m.renderProposalSource(project.id, document.getElementById('pi-proposal-src')))
        .catch(() => {});

    // Bind open folder buttons
    document.querySelectorAll('#proj-detail-info ._open-folder-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            var p = btn.dataset.folderPath;
            if (!p) return;
            if (window._isExternalAccess && typeof window.openNasBrowser === 'function') {
                await window.openNasBrowser({ title: p, initialPath: p, showFiles: true });
            } else {
                fetch('/api/v1/utils/open_folder', {
                    method: 'POST',
                    headers: Object.assign({ 'Content-Type': 'application/json' }, window.bearerHeader ? window.bearerHeader() : {}),
                    body: JSON.stringify({ path: p })
                });
            }
        });
    });

    // Stage-aware async loading
    if (isPreProject) {
        _loadQuoteSummary(project.id);
    } else {
        _loadBudgetOverview(project.id, isClosed);
    }

    // Tab 4: 財務
    callbacks.loadFinancialSummary?.(project.id);

    // Tab 2: 人員配置 — AM/PM cells now click-editable (no more ✎ mode)
    const _amHtmlT = project.am_username
        ? `<div class="crm-am-row">${_avatar(project.am_username, 28)}<span>${_esc(project.am_username)}</span></div>`
        : '<span class="crm-prop-value empty">未指派</span>';
    const _pmListT = (project.pm_usernames || []);
    const _pmHtmlT = _pmListT.length > 0
        ? _pmListT.map(u => `<div class="crm-am-row" style="margin-bottom:4px;">${_avatar(u, 24)}<span>${_esc(u)}</span></div>`).join('')
        : '<span class="crm-prop-value empty">未指派</span>';

    document.getElementById('proj-detail-team').innerHTML = `
        <div id="proj-pay-strip" class="ppay-strip">載入中...</div>
        <div class="ppay-sec">
            <div class="ppay-sh"><span class="ppay-h">收款</span><span class="ppay-sub">本案的發票，和它連到的匯款</span></div>
            <div id="proj-pay-invoices"></div>
        </div>
        <div class="ppay-sec">
            <div class="ppay-sh"><span class="ppay-h">付款</span><span class="ppay-sub">執行人員一列一人：未請款 → 已請款 → 已付款；動作沿用專案帳目那組</span></div>
            <div class="crm-detail-prop">
                <div class="crm-prop-label">AM</div>
                <div class="crm-prop-value" id="proj-am-display"><span class="pi-edit-cell" data-field="am_username" onclick="window._projEdit(this)" style="cursor:pointer;display:inline-block;">${_amHtmlT}</span></div>
            </div>
            <div class="crm-detail-prop">
                <div class="crm-prop-label">PM</div>
                <div class="crm-prop-value" id="proj-pm-display"><span class="pi-edit-cell" data-field="pm_usernames" onclick="window._projEdit(this)" style="cursor:pointer;display:inline-block;">${_pmHtmlT}</span></div>
            </div>
            <div style="margin-top:12px;border-top:1px solid #2e2e2e;padding-top:12px;">
                <div style="margin-bottom:8px;">
                    <span style="font-size:12px;font-weight:700;color:#6b7280;">執行人員</span>
                </div>
                <div id="proj-cost-staff">載入中...</div>
            </div>
            <div style="margin-top:12px;border-top:1px solid #2e2e2e;padding-top:12px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:12px;font-weight:700;color:#6b7280;">預支款</span>
                    <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._costCreateAdvance()">+ 新增預支</button>
                </div>
                <div id="proj-advance-list">載入中...</div>
            </div>
            <details style="margin-top:12px;border-top:1px solid #2e2e2e;padding-top:10px;" ontoggle="if(this.open) window._projPay?.staff?.()">
                <summary style="font-size:12px;font-weight:700;color:#6b7280;cursor:pointer;">派工設定（誰做什麼；錢在上面的執行人員）</summary>
                <div id="proj-staff-list" style="margin-top:8px;">載入中...</div>
            </details>
        </div>
        <div class="ppay-sec">
            <div class="ppay-sh"><span class="ppay-h">掛在本案的收支</span><span class="ppay-sub">收支明細裡掛到本案（或本案發票）的列</span></div>
            <div id="proj-pay-cash">載入中...</div>
        </div>
        <div class="ppay-sec">
            <div class="ppay-sh"><span class="ppay-h">結案檢查</span><span class="ppay-sub">四項都綠才算收付結清（目前只提醒，不擋結案）</span></div>
            <div id="proj-pay-check">載入中...</div>
        </div>
    `;
    callbacks.loadPayTab?.(project.id);
    callbacks.loadCostStaff?.(project.id);
    callbacks.loadAdvances?.(project.id);

    const actions = document.getElementById('proj-bar-actions');
    if (actions) {
        // 搬帳本（owner 2026-08-28）。🔴 只給帳號上**真的有** finance_mine 的人 ——
        // 直接看 _modules、不走 Lv3 bypass，跟 finance.js 的私帳子視圖同一個口徑
        // （後端 require_entity 才是真正的牆，這裡只是不給看到按不到的東西）。
        const _mine = (window._modules || []).includes('finance_mine');
        const _toMine = (project.entity || 'parent') !== 'mine';
        actions.innerHTML = (_mine
            ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="proj-move-ledger"
                       title="${_toMine ? '把這個專案的錢流歸屬改成私帳' : '把這個專案搬回母公司帳'}"
                       >${_toMine ? '推送至私帳' : '搬回公司帳'}</button>` : '')
            // 連結私帳＝在私帳開一案，收入＝公司要付給我的成本行（母公司這案不動）。
            // 只對還在母公司的案子顯示 —— 已經搬過去的案子沒有「公司付給我」這回事。
            + (_mine && _toMine
                ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="proj-mirror-mine"
                           title="公司發給你做的部分，在私帳開一案、收入同步過去（這案留在母公司）"
                           >連結私帳</button>` : '')
            + `<button class="crm-detail-close" title="關閉">✕</button>`;
        actions.querySelector('#proj-move-ledger')?.addEventListener('click',
            () => window._projMoveLedger(project.id));
        actions.querySelector('#proj-mirror-mine')?.addEventListener('click',
            () => window._projMirrorMine(project.id));
        actions.querySelector('.crm-detail-close').addEventListener('click', () => callbacks.closeDetail?.());
    }
    // Re-attach the [🟢 已自動儲存] indicator that _loadFinancialSummary injects —
    // renderDetail just wiped the actions area.
    window._costShowSaveBtn?.();
}

/** 這一案的錢記在私帳嗎（owner 2026-08-30「crm 專案的檢視要以母公司的帳為主體」）。
 *
 *  🔴 CRM ＝**公司的**帳本視角。私帳案的合約／應收／已收是他跟自己客戶的錢，
 *  公司帳上沒有這些數字 —— 照原樣畫在專案總覽，就等於把私帳的營收混進公司的
 *  管線裡（實測生產有 2 案這樣：86,500 與 60,000 都是他自己接的）。
 *
 *  只擋**收入那半邊**：專案帳目（成本行／雜支）照舊可編 —— 那些是這一案真的
 *  發生的成本，而且私帳的逐案損益正是從它們算過來的（CRM_BACKED）。
 */
const _isMineProject = (p) => (p && (p.entity || 'parent') === 'mine');

/** 私帳案在 CRM 的金額列：不畫數字、指路到它真正該看的地方。 */
const _MINE_MONEY_NOTICE = `<span style="color:#c4b5fd;font-size:12px;"
        title="CRM 是公司的帳本視角。這一案的合約與收款屬於私帳（我的帳），公司帳上沒有它的錢 —— 金額請到財務管理 › 執行專案看。專案帳目（成本、雜支）在這裡照常可以編。">
        合約與收款在私帳</span>
        <span class="pi-dot"></span>
        <span style="color:#6b7280;font-size:12px;">公司帳上沒有這一案的錢</span>`;


// ── Budget Overview (stage-aware) ─────────────────────────

async function _loadBudgetOverview(projectId, isClosed) {
    const el = document.getElementById('pi-budget-row');
    if (!el) return;
    try {
        const f = await _fetch('/projects/' + projectId + '/financial-summary');
        const d = calcDashboard(f);
        const rc = remainColor(d.remainingActual);
        const pc = profitColor(d.profitPct);
        const bc = barColor(d.usagePct);

        if (isClosed) {
            // 結案/歸檔: 3 cards (專案結算 + 毛利 + 帳款狀況), no progress bar
            const proj = state.projects.find(p => p.id === projectId);
            const payStatus = proj ? (proj.payment_status || '未到帳') : '未到帳';
            const payMap = {'未到帳':'#ef4444','部分到帳':'#fbbf24','全額到帳':'#86efac'};
            el.innerHTML = `
              <div class="pi-finance">
                <div class="pi-fin-card">
                  <div class="pi-fin-label">最終結算</div>
                  <div class="pi-fin-value" style="color:#fb923c;">$${fmtNum(d.totalActual)}</div>
                  <div class="pi-fin-sub">&nbsp;</div>
                </div>
                <div class="pi-fin-card">
                  <div class="pi-fin-label">毛利</div>
                  <div class="pi-fin-value" style="color:${pc};">$${fmtNum(d.actualProfit)}</div>
                  <div class="pi-fin-sub">${d.profitPct}%${d.profitPct >= 20 ? ' ↑' : d.profitPct < 0 ? ' ↓' : ''}</div>
                </div>
                <div class="pi-fin-card">
                  <div class="pi-fin-label">帳款狀況</div>
                  <div class="pi-fin-value" style="color:${payMap[payStatus] || '#d1d5db'};font-size:16px;">${_esc(payStatus)}</div>
                  <div class="pi-fin-sub">&nbsp;</div>
                </div>
              </div>
            `;
        } else {
            // 製作: full budget dashboard with progress bar
            el.innerHTML = `
              <div class="pi-finance">
                <div class="pi-fin-card">
                  <div class="pi-fin-label">執行預算</div>
                  <div class="pi-fin-value" style="color:#60a5fa;">$${fmtNum(d.execBudget)}</div>
                  <div class="pi-fin-sub">&nbsp;</div>
                </div>
                <div class="pi-fin-card">
                  <div class="pi-fin-label">剩餘預算</div>
                  <div class="pi-fin-value" style="color:${rc};">$${fmtNum(d.remainingActual)}</div>
                  <div class="pi-fin-sub">預估剩 $${fmtNum(d.remaining)}</div>
                </div>
                <div class="pi-fin-card">
                  <div class="pi-fin-label">專案結算</div>
                  <div class="pi-fin-value" style="color:#fb923c;">$${fmtNum(d.totalActual)}</div>
                  <div class="pi-fin-sub">&nbsp;</div>
                </div>
                <div class="pi-fin-card">
                  <div class="pi-fin-label">毛利</div>
                  <div class="pi-fin-value" style="color:${pc};">$${fmtNum(d.actualProfit)}</div>
                  <div class="pi-fin-sub">${d.profitPct}%${d.profitPct >= 20 ? ' ↑' : d.profitPct < 0 ? ' ↓' : ''}</div>
                </div>
              </div>
              <div class="cost-progress-wrap"><div class="cost-progress-bar" style="width:${Math.min(d.usagePct, 100)}%;background:${bc};"></div></div>
              <div class="pi-budget-meta">
                預算已使用 ${d.usagePct}%（實際 $${fmtNum(d.totalActual)} / $${fmtNum(d.execBudget)}）
                &nbsp;·&nbsp;
                預估排定 ${d.estPct}%（$${fmtNum(d.totalEstimated)}）
                &nbsp;·&nbsp;
                雜支 $${fmtNum(d.miscActual)} / $${fmtNum(d.miscEstimated)}${d.miscAuto ? '（自動）' : ''}
              </div>
            `;
        }
    } catch (_) {}
}

// ── Quote Summary (投標 / 開發 / 洽詢 / 提案) ──────────────────────

async function _loadQuoteSummary(projectId) {
    const el = document.getElementById('pi-stage-card');
    if (!el) return;
    try {
        const data = await _fetch(`/projects/${projectId}/quotations`);
        const quotes = data.quotations || [];
        if (quotes.length === 0) {
            el.innerHTML = '<div class="crm-empty" style="padding:16px 0;">尚無報價，請到「報價管理」Tab 新增</div>';
            return;
        }
        const latest = quotes[0]; // already sorted DESC by version
        const price = latest.final_price != null ? latest.final_price : latest.total;
        const statusCls = ['草稿','已寄送','已簽核','已拒絕'].includes(latest.status) ? 'crm-badge crm-quote-badge-' + latest.status : 'crm-badge';
        const proj = state.projects.find(p => p.id === projectId);
        const canActivate = proj && PRESALE_STATUSES.includes(proj.status) && quotes.length > 0;
        el.innerHTML = `
          <div class="pi-finance">
            <div class="pi-fin-card">
              <div class="pi-fin-label">最新報價</div>
              <div class="pi-fin-value" style="color:#60a5fa;">$${fmtNum(price)}</div>
              <div class="pi-fin-sub">v${latest.version} <span class="${statusCls}">${latest.status}</span></div>
            </div>
            <div class="pi-fin-card">
              <div class="pi-fin-label">報價版本</div>
              <div class="pi-fin-value" style="color:#d1d5db;">${quotes.length}</div>
              <div class="pi-fin-sub">版</div>
            </div>
          </div>
          ${canActivate ? '<div style="padding:8px 0;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projActivate()">啟動專案</button></div>' : ''}
        `;
    } catch (_) {}
}

// ── Window Handlers Registration ───────────────────────────

function initDetailHandlers() {
    // _projActivate and _projDoActivate moved to crm-projects-quotes.js
}

// ── Exports ────────────────────────────────────────────────

export { renderDetail, initDetailHandlers };
