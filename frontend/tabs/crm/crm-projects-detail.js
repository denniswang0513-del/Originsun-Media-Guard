/**
 * crm-projects-detail.js — 詳情面板渲染 (stage-aware) + cell-by-cell inline edit
 *
 * 每個欄位點擊即可編輯，blur → 寫 _projDirtyMap → 1s debounce auto-save
 * （由 cost.js 的 auto-save 機制統一處理）。沒有「✎ 編輯」模式。
 */

import { state, callbacks } from './crm-projects-state.js';
import { _badge, _avatar, getProjectTypes } from './crm-projects-core.js';
import { calcDashboard, remainColor, profitColor, barColor } from './crm-projects-calc.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, pickFolderPath } from './crm-utils.js';

// ── Edit Fields ────────────────────────────────────────────

function _buildEditFields() {
    const clientOpts = [{value:'',label:'— 選擇客戶 —'}].concat(
        state.clients.map(c => ({value: c.id, label: c.short_name}))
    );
    const userOpts = [{value:'',label:'— 未指派 —'}].concat(
        state.users.map(u => ({value: u.username, label: u.username}))
    );
    const pmOpts = state.users.map(u => ({value: u.username, label: u.username}));
    return [
        {name:'client_id', label:'客戶', type:'select', options: clientOpts},
        {name:'name', label:'專案名稱', type:'text'},
        {name:'status', label:'狀態', type:'select', options:[
            {value:'洽談中',label:'洽談中'},{value:'報價中',label:'報價中'},
            {value:'進行中',label:'進行中'},{value:'已結案',label:'已結案'},
        ]},
        {name:'project_type', label:'類型', type:'select', get options() {
            return [{value:'',label:'—'}, ...getProjectTypes().map(t => ({value:t,label:t}))];
        }},
        {name:'am_username', label:'AM', type:'select', options: userOpts},
        {name:'pm_usernames', label:'PM', type:'checkboxes', options: pmOpts},
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
        {name:'receipt_path', label:'收據資料夾', type:'folder'},
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
    const orig = project[field];
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
    input.focus();
    if (input.select) input.select();

    const commit = () => {
        let val = input.value;
        if (t === 'number') val = val === '' ? null : parseInt(val);
        if (t === 'date' || t === 'month') val = val || null;
        const changed = (val ?? null) !== (orig ?? null);
        if (changed) {
            window._projDirtyMap[field] = val;
            project[field] = val;
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
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && t !== 'textarea') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') {
            cell.innerHTML = _projDisplayValue(field, orig, fieldDef);
        }
    });
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

// PM 多選用 popover，因為概覽 layout 不適合 inline checkbox 列表。
window._projEditPm = function(cell) {
    const project = state.projects.find(p => p.id === state.selectedId);
    if (!project) return;
    const selected = new Set(project.pm_usernames || []);

    document.querySelectorAll('.pi-pm-popover').forEach(el => el.remove());

    const pop = document.createElement('div');
    pop.className = 'pi-pm-popover';
    pop.style.cssText = 'position:absolute;background:#1e1e1e;border:1px solid #555;border-radius:6px;padding:8px;z-index:1000;max-height:300px;overflow:auto;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
    pop.innerHTML = state.users.map(u => `
        <label style="display:block;padding:4px 8px;cursor:pointer;font-size:12px;color:#d1d5db;border-radius:3px;">
            <input type="checkbox" value="${_esc(u.username)}"${selected.has(u.username) ? ' checked' : ''} style="margin-right:6px;">
            ${_esc(u.username)}
        </label>
    `).join('') + `
        <div style="margin-top:8px;display:flex;gap:6px;justify-content:flex-end;border-top:1px solid #333;padding-top:6px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="_proj-pm-cancel">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="_proj-pm-confirm">確定</button>
        </div>
    `;

    const rect = cell.getBoundingClientRect();
    pop.style.left = rect.left + 'px';
    pop.style.top = (rect.bottom + 4) + 'px';
    document.body.appendChild(pop);

    // Single close path so confirm / cancel / outside-click all detach the
    // outside-click listener (previously cancel/confirm leaked it).
    const dismiss = () => {
        document.removeEventListener('click', _outside);
        pop.remove();
    };
    function _outside(e) {
        if (!pop.contains(e.target) && !cell.contains(e.target)) dismiss();
    }

    document.getElementById('_proj-pm-confirm').addEventListener('click', () => {
        const newPms = Array.from(pop.querySelectorAll('input[type="checkbox"]:checked')).map(c => c.value);
        const oldPms = project.pm_usernames || [];
        if (JSON.stringify(newPms.slice().sort()) !== JSON.stringify([...oldPms].sort())) {
            window._projDirtyMap['pm_usernames'] = newPms;
            project.pm_usernames = newPms;
            window._costScheduleAutoSave?.();
        }
        dismiss();
        _scheduleRender(project);
    });
    document.getElementById('_proj-pm-cancel').addEventListener('click', dismiss);
    setTimeout(() => document.addEventListener('click', _outside), 50);
};

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
    document.getElementById('proj-detail-title').textContent = project.name;

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
    const isPreProject = project.status === '洽談中' || project.status === '報價中';
    const isClosed = project.status === '已結案';

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
          <span class="pi-edit-cell" onclick="window._projEditPm(this)" style="cursor:pointer;">${_pmHtml}</span>
          <span class="pi-dot"></span>
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
          ${_editCell('payment_status', _pBadge(project.payment_status))}
          <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:auto;padding:2px 10px;font-size:11px;"
                  onclick="window._projOpenForm('${project.id}')" title="編輯所有專案資訊">✎ 編輯</button>
        </div>

        <!-- Layer 2: Stage card (conditional) -->
        <div id="pi-stage-card"></div>

        <!-- Layer 3: 預算儀表板（async，進行中/已結案） -->
        <div id="pi-budget-row"></div>

        <!-- Layer 5: 補充資訊 (always rendered with placeholders) -->
        <div class="pi-section-title">補充資訊</div>
        <div class="pi-details-card">
          <div class="pi-det-text">${_editCell('description', project.description ? _esc(project.description) : _placeholder('+ 加說明'))}</div>
          <div class="pi-det-paths">
            <div class="pi-det-path">&#128193; ${_editCell('folder_path', project.folder_path ? _esc(project.folder_path) : _placeholder('+ 加專案資料夾'))} <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projEditFolder('folder_path')" style="padding:1px 5px;font-size:10px;" title="瀏覽選擇">📁</button>${project.folder_path ? `<button class="crm-btn crm-btn-secondary crm-btn-sm _open-folder-btn" data-folder-path="${_esc(project.folder_path)}" style="padding:1px 5px;font-size:10px;" title="開啟">↗</button>` : ''}</div>
            <div class="pi-det-path">&#128203; ${_editCell('receipt_path', project.receipt_path ? _esc(project.receipt_path) : _placeholder('+ 加收據資料夾'))} <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projEditFolder('receipt_path')" style="padding:1px 5px;font-size:10px;" title="瀏覽選擇">📁</button>${project.receipt_path ? `<button class="crm-btn crm-btn-secondary crm-btn-sm _open-folder-btn" data-folder-path="${_esc(project.receipt_path)}" style="padding:1px 5px;font-size:10px;" title="開啟">↗</button>` : ''}</div>
          </div>
          <div class="pi-det-note">${_editCell('notes', project.notes ? _esc(project.notes) : _placeholder('+ 加備註'))}</div>
        </div>

      </div>
    `;

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
                    headers: { 'Content-Type': 'application/json' },
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
        <div class="crm-detail-prop">
            <div class="crm-prop-label">AM</div>
            <div class="crm-prop-value" id="proj-am-display"><span class="pi-edit-cell" data-field="am_username" onclick="window._projEdit(this)" style="cursor:pointer;display:inline-block;">${_amHtmlT}</span></div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">PM</div>
            <div class="crm-prop-value" id="proj-pm-display"><span class="pi-edit-cell" onclick="window._projEditPm(this)" style="cursor:pointer;display:inline-block;">${_pmHtmlT}</span></div>
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
    `;
    callbacks.loadCostStaff?.(project.id);
    callbacks.loadAdvances?.(project.id);

    const actions = document.getElementById('proj-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">✕</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', () => callbacks.closeDetail?.());
    }
    // Re-attach the [🟢 已自動儲存] indicator that _loadFinancialSummary injects —
    // renderDetail just wiped the actions area.
    window._costShowSaveBtn?.();
}

// ── Budget Overview (stage-aware) ─────────────────────────

async function _loadBudgetOverview(projectId, isClosed) {
    const el = document.getElementById('pi-budget-row');
    if (!el) return;
    try {
        const f = await _fetch('/projects/' + projectId + '/financial-summary');
        const d = calcDashboard(f);
        const rc = remainColor(d.remaining);
        const pc = profitColor(d.profitPct);
        const bc = barColor(d.usagePct);

        if (isClosed) {
            // 已結案: 3 cards (專案結算 + 毛利 + 帳款狀況), no progress bar
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
            // 進行中: full budget dashboard with progress bar
            el.innerHTML = `
              <div class="pi-finance">
                <div class="pi-fin-card">
                  <div class="pi-fin-label">執行預算</div>
                  <div class="pi-fin-value" style="color:#60a5fa;">$${fmtNum(d.execBudget)}</div>
                  <div class="pi-fin-sub">&nbsp;</div>
                </div>
                <div class="pi-fin-card">
                  <div class="pi-fin-label">剩餘預算</div>
                  <div class="pi-fin-value" style="color:${rc};">$${fmtNum(d.remaining)}</div>
                  <div class="pi-fin-sub">&nbsp;</div>
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
                預算已使用 ${d.usagePct}% ($${fmtNum(d.totalEstimated)} / $${fmtNum(d.execBudget)})
                &nbsp;·&nbsp;
                預估毛利率 ${f.profit_target_pct != null ? f.profit_target_pct : 20}% ($${fmtNum(d.actualProfit)} / $${fmtNum(f.profit_target)})
                &nbsp;·&nbsp;
                預估雜支 ${f.misc_budget_pct != null ? f.misc_budget_pct : 5}% ($${fmtNum(f.expense_actual)} / $${fmtNum(f.misc_budget)})
              </div>
            `;
        }
    } catch (_) {}
}

// ── Quote Summary (洽談中 / 報價中) ──────────────────────

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
        const canActivate = proj && proj.status !== '進行中' && proj.status !== '已結案' && quotes.length > 0;
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
