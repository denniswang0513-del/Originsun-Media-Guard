/**
 * crm-projects-detail.js — 詳情面板渲染 (stage-aware) + 預算概覽 + 人員編輯
 */

import { state, callbacks } from './crm-projects-state.js';
import { _badge, _avatar, loadProjects, getProjectTypes } from './crm-projects-core.js';
import { calcDashboard, remainColor, profitColor, barColor } from './crm-projects-calc.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, enableInlineEdit, addEditButton } from './crm-utils.js';

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
        {name:'description', label:'說明 / 備註', type:'textarea'},
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

    // People info
    const _amPm = [];
    if (project.am_username) _amPm.push(`<span class="pi-person"><span class="pi-person-role">AM</span>${_avatar(project.am_username, 20)} ${_esc(project.am_username)}</span>`);
    (project.pm_usernames || []).forEach(u => _amPm.push(`<span class="pi-person"><span class="pi-person-role">PM</span>${_avatar(u, 20)} ${_esc(u)}</span>`));

    // Determine stage
    const isPreProject = project.status === '洽談中' || project.status === '報價中';
    const isClosed = project.status === '已結案';

    document.getElementById('proj-detail-info').innerHTML = `
      <div class="pi-wrap">

        <!-- Layer 1: 摘要橫條 -->
        <div class="pi-summary">
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_d(project.client_short_name)}</div>
            <div class="pi-summary-label">客戶</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_d(project.project_type)}</div>
            <div class="pi-summary-label">類型</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_badge(project.status)}</div>
            <div class="pi-summary-label">狀態</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_d(project.start_date ? project.start_date.substring(0, 10) : '')}</div>
            <div class="pi-summary-label">起始日</div>
          </div>
          <div class="pi-summary-cell">
            <div class="pi-summary-val">${_d(project.completion_date ? project.completion_date.substring(0, 10) : '')}</div>
            <div class="pi-summary-label">結案日</div>
          </div>
        </div>

        <!-- Layer 1b: 人員 + 合約帳務 -->
        ${_amPm.length > 0 || project.contract_amount ? `<div class="pi-contract-line">
          ${_amPm.join('')}
          ${_amPm.length > 0 && project.contract_amount ? '<span class="pi-dot"></span>' : ''}
          ${project.contract_amount ? `
            <span>合約 <b style="color:#60a5fa;">${_$(project.contract_amount)}</b></span>
            <span class="pi-dot"></span>
            <span>稅率 ${project.tax_rate != null ? project.tax_rate : 5}%</span>
            <span class="pi-dot"></span>
            <span>應收 <b style="color:#fbbf24;">${_$(project.amount_receivable)}</b></span>
            <span class="pi-dot"></span>
            <span>已收 <b style="color:${(project.amount_received || 0) >= (project.amount_receivable || 1) ? '#86efac' : '#d1d5db'};">${_$(project.amount_received)}</b></span>
            ${project.transfer_fee ? `<span class="pi-dot"></span><span>匯費 $${fmtNum(project.transfer_fee)}</span>` : ''}
            <span class="pi-dot"></span>
            ${_pBadge(project.payment_status)}
          ` : ''}
        </div>` : ''}

        <!-- Layer 2: Stage card (conditional) -->
        <div id="pi-stage-card"></div>

        <!-- Layer 3: 預算儀表板（async，進行中/已結案） -->
        <div id="pi-budget-row"></div>

        <!-- Layer 5: 補充資訊 -->
        ${project.description || project.folder_path || project.receipt_path || project.notes ? `
        <div class="pi-section-title">補充資訊</div>
        <div class="pi-details-card">
          ${project.description ? `<div class="pi-det-text">${_esc(project.description)}</div>` : ''}
          ${project.folder_path || project.receipt_path ? `<div class="pi-det-paths">
            ${project.folder_path ? `<div class="pi-det-path">&#128193; ${_esc(project.folder_path)}${_folderBtn(project.folder_path)}</div>` : ''}
            ${project.receipt_path ? `<div class="pi-det-path">&#128203; ${_esc(project.receipt_path)}${_folderBtn(project.receipt_path)}</div>` : ''}
          </div>` : ''}
          ${project.notes ? `<div class="pi-det-note">${_esc(project.notes)}</div>` : ''}
        </div>` : ''}

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

    // Tab 2: 人員配置
    const amHtml = project.am_username
        ? `<div class="crm-am-row">${_avatar(project.am_username, 28)}<span>${_esc(project.am_username)}</span></div>`
        : '<span class="crm-prop-value empty">未指派</span>';

    const pmList = (project.pm_usernames || []);
    const pmHtml = pmList.length > 0
        ? pmList.map(u => `<div class="crm-am-row" style="margin-bottom:4px;">${_avatar(u, 24)}<span>${_esc(u)}</span></div>`).join('')
        : '<span class="crm-prop-value empty">未指派</span>';

    document.getElementById('proj-detail-team').innerHTML = `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">AM</div>
            <div class="crm-prop-value" id="proj-am-display">${amHtml}</div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">PM</div>
            <div class="crm-prop-value" id="proj-pm-display">${pmHtml}</div>
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
    addEditButton('proj-bar-actions', () => {
        // Detect active tab
        const activeTab = document.querySelector('#proj-detail-tabs .crm-tab.active');
        const tab = activeTab ? activeTab.dataset.tab : 'info';
        if (tab === 'team') {
            _enableStaffEdit(project);
            return;
        }
        enableInlineEdit('proj-detail-info', 'proj-bar-actions', _buildEditFields(), project,
            async (payload) => {
                // Save project fields
                await _fetch('/projects/' + project.id, { method: 'PUT', body: JSON.stringify(payload) });
                // Also save any cost line edits
                var costEntries = Object.entries(window._costDirtyMap || {});
                for (var i = 0; i < costEntries.length; i++) {
                    await _fetch('/project-cost-lines/' + costEntries[i][0], {
                        method: 'PUT', body: JSON.stringify(costEntries[i][1])
                    });
                }
                window._costDirtyMap = {};
                const updated = await _fetch('/projects/' + project.id);
                renderDetail(updated);
                await loadProjects();
            },
            () => { window._costDirtyMap = {}; renderDetail(project); }
        );
    });
}

// ── Staff Edit ─────────────────────────────────────────────

async function _enableStaffEdit(project) {
    const content = document.getElementById('proj-detail-team');
    const actions = document.getElementById('proj-bar-actions');
    if (!content || !actions) return;

    // Fetch users for AM/PM selects
    var users = [];
    try {
        var token = localStorage.getItem('auth_token');
        var r = await fetch('/api/v1/auth/users', { headers: token ? { 'Authorization': 'Bearer ' + token } : {} });
        var d = await r.json();
        users = d.users || d || [];
    } catch (_) {}

    // Build AM select
    var amSelectHtml = '<select id="proj-am-select" class="crm-input" style="max-width:220px;">' +
        '<option value="">-- 未指派 --</option>' +
        users.map(u => '<option value="' + _esc(u.username) + '"' + (u.username === project.am_username ? ' selected' : '') + '>' + _esc(u.username) + '</option>').join('') +
        '</select>';

    // Build PM checkboxes
    var selectedPms = project.pm_usernames || [];
    var pmCheckHtml = users.map(u =>
        '<label style="display:flex;align-items:center;gap:6px;padding:3px 0;cursor:pointer;font-size:12px;color:#d1d5db;">' +
        '<input type="checkbox" class="_pm-check" value="' + _esc(u.username) + '"' + (selectedPms.indexOf(u.username) >= 0 ? ' checked' : '') + '>' +
        _esc(u.username) + '</label>'
    ).join('');

    // Replace AM/PM display with editable controls (keep 執行人員 and 預支款 sections intact)
    var amProp = content.querySelector('#proj-am-display');
    var pmProp = content.querySelector('#proj-pm-display');
    if (amProp) amProp.innerHTML = amSelectHtml;
    if (pmProp) pmProp.innerHTML = pmCheckHtml;

    // Replace action buttons with save/cancel
    var closeBtn = actions.querySelector('.crm-detail-close');
    var closeHtml = closeBtn ? closeBtn.outerHTML : '';
    actions.innerHTML = `
        <button class="crm-btn crm-btn-secondary crm-btn-sm" id="_inline-cancel">取消</button>
        <button class="crm-btn crm-btn-primary crm-btn-sm" id="_inline-save">儲存</button>
        ${closeHtml}
    `;
    actions.querySelector('.crm-detail-close').addEventListener('click', () => callbacks.closeDetail?.());

    document.getElementById('_inline-cancel').addEventListener('click', () => {
        renderDetail(project);
        document.querySelector('#proj-detail-tabs .crm-tab[data-tab="team"]')?.click();
    });
    document.getElementById('_inline-save').addEventListener('click', async () => {
        var btn = document.getElementById('_inline-save');
        btn.disabled = true; btn.textContent = '儲存中...';
        try {
            var newAm = document.getElementById('proj-am-select').value;
            var checks = content.querySelectorAll('._pm-check:checked');
            var newPms = [];
            checks.forEach(c => newPms.push(c.value));

            var full = await _fetch('/projects/' + project.id);
            full.am_username = newAm;
            full.pm_usernames = newPms;
            delete full.id; delete full.created_at; delete full.updated_at;
            delete full.client_name; delete full.client_short_name;
            delete full.am_avatar_url; delete full.quotation_count;
            await _fetch('/projects/' + project.id, { method: 'PUT', body: JSON.stringify(full) });

            var updated = await _fetch('/projects/' + project.id);
            renderDetail(updated);
            // Switch back to team tab
            document.querySelector('#proj-detail-tabs .crm-tab[data-tab="team"]')?.click();
            await loadProjects();
        } catch (e) {
            alert('儲存失敗: ' + e.message);
            btn.disabled = false; btn.textContent = '儲存';
        }
    });
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
