/**
 * crm-projects.js — CRM 專案管理子視圖
 * 功能：列表 + 詳情面板 + 新增/編輯 Modal + 狀態快切
 */

import { crmFetch as _fetch, esc as _esc, renderAvatar, populateUserSelect, populateClientSelect, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

// ── State ────────────────────────────────────────────────────

let _projects = [];
let _clients = [];
let _users = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', status: '', client_id: '', am: '' };

// ── Data Loading ─────────────────────────────────────────────

export async function loadProjects() {
    const params = new URLSearchParams();
    if (_filters.q)         params.set('q', _filters.q);
    if (_filters.status)    params.set('status', _filters.status);
    if (_filters.client_id) params.set('client_id', _filters.client_id);
    if (_filters.am)        params.set('am', _filters.am);

    try {
        const data = await _fetch(`/projects?${params}`);
        _projects = data.projects || [];
    } catch (e) {
        _projects = [];
        _showListError(e.message);
    }
    renderList();
}

async function loadClients() {
    try {
        const data = await _fetch('/clients');
        _clients = data.clients || [];
        _populateClientFilter();
    } catch (_) {
        _clients = [];
    }
}

async function loadUsers() {
    try {
        const data = await _fetch('/users');
        _users = data.users || [];
        _populateSelect('proj-filter-am', '全部 AM');
    } catch (_) {
        _users = [];
    }
}

// ── Rendering ────────────────────────────────────────────────

function _badge(status) {
    const s = status || '洽談中';
    const known = ['洽談中', '報價中', '進行中', '已結案'];
    const cls = known.includes(s) ? `crm-badge crm-proj-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

function _avatar(username, size = 22) {
    return renderAvatar(username, _users, size);
}

function renderList() {
    const body = document.getElementById('proj-list-body');
    if (!body) return;

    if (_projects.length === 0) {
        body.innerHTML = `<div class="crm-empty">找不到專案${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }

    body.innerHTML = _projects.map(p => `
        <div class="crm-row${p.id === _selectedId ? ' selected' : ''}" data-id="${p.id}" onclick="window._projSelect('${p.id}')">
            <div class="crm-row-name">${_esc(p.name)}</div>
            <div class="crm-row-client">${_esc(p.client_short_name)}</div>
            <div class="crm-row-status">${_badge(p.status)}</div>
            <div class="crm-row-am">
                ${p.am_username ? _avatar(p.am_username) + _esc(p.am_username) : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-date">${p.start_date ? p.start_date.substring(0, 10) : '—'}</div>
            ${kebabMenuHtml(p.id, { onEdit: '_projEdit', onDuplicate: '_projDup', onDelete: '_projDelete' })}
        </div>
    `).join('');
}

const _PROJ_EDIT_FIELDS = [
    {name:'name', label:'專案名稱', type:'text'},
    {name:'project_type', label:'類型', type:'select', options:[
        {value:'',label:'—'},{value:'紀實影片',label:'紀實影片'},{value:'活動紀實',label:'活動紀實'},
        {value:'形象影片',label:'形象影片'},{value:'廣告',label:'廣告'},{value:'MV',label:'MV'},
    ]},
    {name:'status', label:'狀態', type:'select', options:[
        {value:'洽談中',label:'洽談中'},{value:'報價中',label:'報價中'},
        {value:'進行中',label:'進行中'},{value:'已結案',label:'已結案'},
    ]},
    {name:'start_date', label:'起始日', type:'date'},
    {name:'completion_date', label:'結案日', type:'date'},
    {name:'folder_path', label:'資料夾', type:'text'},
    {name:'description', label:'說明', type:'text'},
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
    {name:'notes', label:'備註', type:'textarea'},
];

function renderDetail(project) {
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(value)}</div>
        </div>`;
    };

    document.getElementById('proj-detail-title').textContent = project.name;

    const _pBadge = (status) => {
        const map = {'未到帳':'crm-badge crm-pay-未到帳','部分到帳':'crm-badge crm-pay-部分到帳','全額到帳':'crm-badge crm-pay-全額到帳'};
        return `<span class="${map[status] || 'crm-badge'}">${_esc(status || '未到帳')}</span>`;
    };

    // Tab 1: 專案資訊
    document.getElementById('proj-detail-info').innerHTML = `
        ${prop('客戶', project.client_short_name)}
        <div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value">${_badge(project.status)}</div></div>
        ${prop('類型', project.project_type)}
        ${prop('起始日', project.start_date ? project.start_date.substring(0, 10) : '')}
        ${prop('結案日', project.completion_date ? project.completion_date.substring(0, 10) : '')}
        ${prop('資料夾', project.folder_path)}
        ${prop('說明', project.description)}
        ${project.contract_amount ? `${prop('合約金額', '$' + project.contract_amount.toLocaleString('zh-TW'))}` : ''}
        ${project.contract_amount ? `<div class="crm-detail-prop"><div class="crm-prop-label">帳務</div><div class="crm-prop-value">${_pBadge(project.payment_status)}</div></div>` : ''}
        ${prop('備註', project.notes)}
    `;

    // Tab 4: 財務
    _loadFinancialSummary(project.id);

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
            <div class="crm-prop-value">${amHtml}</div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">PM</div>
            <div class="crm-prop-value">${pmHtml}</div>
        </div>
        <div style="margin-top:12px;border-top:1px solid #2e2e2e;padding-top:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:12px;font-weight:700;color:#6b7280;">派工人員</span>
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projAddStaff()">+ 新增派工</button>
            </div>
            <div id="proj-staff-list">載入中...</div>
        </div>
    `;
    _loadProjectStaff(project.id);

    const actions = document.getElementById('proj-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">✕</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('proj-bar-actions', () => {
        enableInlineEdit('proj-detail-info', 'proj-bar-actions', _PROJ_EDIT_FIELDS, project,
            async (payload) => {
                payload.client_id = project.client_id;
                await _fetch('/projects/' + project.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/projects/' + project.id);
                renderDetail(updated);
                await loadProjects();
            },
            () => renderDetail(project)
        );
    });
}

async function _loadFinancialSummary(projectId) {
    const container = document.getElementById('proj-detail-finance');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty" style="padding:8px;">載入中...</div>';
    try {
        const [f, expData] = await Promise.all([
            _fetch('/projects/' + projectId + '/financial-summary'),
            _fetch('/projects/' + projectId + '/expenses'),
        ]);
        const _n = (n) => (n || 0).toLocaleString('zh-TW');
        const profitColor = f.profit_rate >= 20 ? '#86efac' : f.profit_rate >= 0 ? '#fbbf24' : '#fca5a5';
        const expenses = expData.expenses || [];

        // Expense detail rows
        const expRows = expenses.map(e => `
            <div class="expense-row">
                <span class="expense-cat">${_esc(e.category)}</span>
                <span class="expense-num">$${_n(e.estimated)}</span>
                <span class="expense-num">$${_n(e.actual)}</span>
                <span class="expense-receipt">${e.receipt_url ? `<a href="${e.receipt_url}" target="_blank" style="color:#3b82f6;">📎</a>` : '—'}</span>
                <span class="expense-actions">
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projEditExpense('${e.id}','${_esc(e.category)}',${e.estimated},${e.actual},'${_esc(e.notes)}')">編輯</button>
                    <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._projDeleteExpense('${e.id}')">刪</button>
                </span>
            </div>
        `).join('');
        const expTotal = expenses.reduce((s, e) => [s[0] + e.estimated, s[1] + e.actual], [0, 0]);

        container.innerHTML = `
            <div class="crm-detail-prop"><div class="crm-prop-label">合約金額（含稅）</div><div class="crm-prop-value" style="font-weight:700;">$${_n(f.contract_amount)}</div></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">未稅金額</div><div class="crm-prop-value">$${_n(f.ex_tax)}</div></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">目標毛利 (${f.profit_target_pct}%)</div><div class="crm-prop-value">$${_n(f.profit_target)}</div></div>

            <div class="expense-section">
                <div class="expense-header">
                    <span style="font-weight:700;color:#6b7280;font-size:12px;">雜支明細</span>
                    <span>預算 $${_n(f.misc_budget)}</span>
                    <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projAddExpense()">+ 新增</button>
                </div>
                <div id="proj-expense-form" style="display:none;"></div>
                ${expenses.length > 0 ? `
                    <div class="expense-row expense-row-header">
                        <span class="expense-cat">類別</span>
                        <span class="expense-num">預估</span>
                        <span class="expense-num">實際</span>
                        <span class="expense-receipt">收據</span>
                        <span class="expense-actions"></span>
                    </div>
                    ${expRows}
                    <div class="expense-row" style="font-weight:700;border-top:1px solid #3a3a3a;">
                        <span class="expense-cat">合計</span>
                        <span class="expense-num">$${_n(expTotal[0])}</span>
                        <span class="expense-num">$${_n(expTotal[1])}</span>
                        <span class="expense-receipt"></span>
                        <span class="expense-actions"></span>
                    </div>
                ` : '<div class="crm-empty" style="padding:8px 0;">尚無雜支紀錄</div>'}
            </div>

            <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">外包預算</div><div class="crm-prop-value">$${_n(f.outsource_budget)}</div></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">外包實際（派工）</div><div class="crm-prop-value">$${_n(f.staff_actual)}</div></div>
            <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
            <div class="crm-detail-prop"><div class="crm-prop-label" style="font-weight:700;">實際毛利</div><div class="crm-prop-value" style="font-weight:700;color:${profitColor};">$${_n(f.actual_profit)} (${f.profit_rate}%)</div></div>
            <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">帳務狀況</div><div class="crm-prop-value">${f.payment_status || '未到帳'}</div></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">應收帳款</div><div class="crm-prop-value">$${_n(f.amount_receivable)}</div></div>
            <div class="crm-detail-prop"><div class="crm-prop-label">已收帳款</div><div class="crm-prop-value">$${_n(f.amount_received)}</div></div>
            ${f.transfer_fee ? `<div class="crm-detail-prop"><div class="crm-prop-label">帳款匯費</div><div class="crm-prop-value">$${_n(f.transfer_fee)}</div></div>` : ''}
        `;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

function _showExpenseForm(editId = null, cat = '', est = 0, act = 0, notes = '') {
    const form = document.getElementById('proj-expense-form');
    if (!form) return;
    form.style.display = 'block';
    form.innerHTML = `
        <div class="expense-row" style="gap:4px;flex-wrap:wrap;padding:8px;background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;margin-bottom:6px;">
            <select id="exp-f-cat" class="crm-input" style="width:80px;"><option value="交通">交通</option><option value="住宿">住宿</option><option value="飲食">飲食</option><option value="提案">提案</option><option value="器材">器材</option><option value="其他">其他</option></select>
            <input id="exp-f-est" type="number" class="crm-input" placeholder="預估" style="width:80px;text-align:right;" value="${est}">
            <input id="exp-f-act" type="number" class="crm-input" placeholder="實際" style="width:80px;text-align:right;" value="${act}">
            <input id="exp-f-notes" type="text" class="crm-input" placeholder="備註" style="flex:1;min-width:60px;" value="${_esc(notes)}">
            <input id="exp-f-receipt" type="file" accept="image/*,.pdf" style="display:none;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('exp-f-receipt').click()">📷</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projSaveExpense('${editId || ''}')">確定</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('proj-expense-form').style.display='none'">取消</button>
        </div>
    `;
    if (cat) document.getElementById('exp-f-cat').value = cat;
}

async function _loadProjectStaff(projectId) {
    const container = document.getElementById('proj-staff-list');
    if (!container) return;
    try {
        const data = await _fetch('/projects/' + projectId + '/staff');
        const staff = data.staff || [];
        if (staff.length === 0) {
            container.innerHTML = '<div class="crm-empty" style="padding:8px 0;">尚無派工</div>';
            return;
        }
        const totalCost = staff.reduce((s, r) => s + r.cost, 0);
        container.innerHTML = staff.map(r => `
            <div class="quote-item-row" style="padding:6px 0;">
                <span class="quote-item-desc">${_esc(r.staff_name)} <span class="crm-muted">${_esc(r.staff_role)}</span></span>
                <span class="quote-item-qty">${r.days}天</span>
                <span class="quote-item-price">$${(r.rate || 0).toLocaleString('zh-TW')}</span>
                <span class="quote-item-amount">$${r.cost.toLocaleString('zh-TW')}</span>
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:2px 6px;" onclick="window._projRemoveStaff('${r.id}','${projectId}')">&#x2715;</button>
            </div>
        `).join('') + `<div style="text-align:right;font-weight:700;padding:8px 0;color:#e0e0e0;">內部成本合計: $${totalCost.toLocaleString('zh-TW')}</div>`;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Helpers ──────────────────────────────────────────────────

function _populateSelect(elementId, placeholder) {
    populateUserSelect(elementId, _users, placeholder);
}

function _populateClientFilter() {
    populateClientSelect('proj-filter-client', _clients);
}

function _populateClientDropdown(elementId, selectedId) {
    const sel = document.getElementById(elementId);
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇客戶 —</option>` +
        _clients.map(c => `<option value="${c.id}"${c.id === selectedId ? ' selected' : ''}>${_esc(c.short_name)}</option>`).join('');
}

function _populatePmCheckboxes(selected = []) {
    const container = document.getElementById('proj-f-pm_usernames');
    if (!container) return;
    container.innerHTML = _users.map(u => `
        <label class="crm-checkbox-item">
            <input type="checkbox" value="${_esc(u.username)}" ${selected.includes(u.username) ? 'checked' : ''}>
            ${_avatar(u.username, 18)} ${_esc(u.username)}
        </label>
    `).join('');
}

function _getSelectedPms() {
    const container = document.getElementById('proj-f-pm_usernames');
    if (!container) return [];
    return Array.from(container.querySelectorAll('input:checked')).map(cb => cb.value);
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

// ── Detail Panel ─────────────────────────────────────────────

function selectProject(id) {
    _selectedId = id;
    renderList();

    const panel = document.getElementById('proj-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('proj-resize-handle');
    if (handle) handle.style.display = '';

    const project = _projects.find(p => p.id === id);
    if (!project) return;
    renderDetail(project);
    _loadProjectQuotations(id);
}

async function _loadProjectQuotations(projectId) {
    const container = document.getElementById('proj-detail-quotes');
    if (!container) return;
    try {
        const data = await _fetch(`/projects/${projectId}/quotations`);
        const quotes = data.quotations || [];
        let html = '';
        if (quotes.length > 0) {
            html = quotes.map(q => {
                const price = q.final_price !== null && q.final_price !== undefined ? q.final_price : q.total;
                const statusCls = ['草稿','已寄送','已簽核','已拒絕'].includes(q.status) ? `crm-badge crm-quote-badge-${q.status}` : 'crm-badge';
                return `<div class="quote-item-row" style="padding:8px 0;">
                    <span class="quote-item-desc">v${q.version}</span>
                    <span><span class="${statusCls}">${_esc(q.status)}</span></span>
                    <span class="quote-item-amount">$${(price || 0).toLocaleString('zh-TW')}</span>
                    <span class="crm-muted">${q.quote_date ? q.quote_date.substring(0, 10) : ''}</span>
                </div>`;
            }).join('');
        } else {
            html = '<div class="crm-empty" style="padding:16px 0;">尚無報價</div>';
        }
        html += `<div style="padding:8px 0;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projAddQuote()">+ 新增報價</button></div>`;
        container.innerHTML = html;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

function closeDetail() {
    _selectedId = null;
    const panel = document.getElementById('proj-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('proj-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Add / Edit Modal ─────────────────────────────────────────

const _FIELDS = ['name', 'client_id', 'status', 'project_type', 'start_date', 'shoot_date',
    'completion_date', 'folder_path', 'description', 'am_username', 'notes',
    'contract_amount', 'tax_rate', 'profit_target_pct', 'misc_budget_pct',
    'payment_status', 'amount_receivable', 'amount_received', 'transfer_fee'];

function openModal(project = null) {
    _editingId = project ? project.id : null;
    document.getElementById('proj-modal-title').textContent = project ? '編輯專案' : '新增專案';
    const errEl = document.getElementById('proj-modal-error');
    errEl.textContent = '';
    errEl.style.display = 'none';

    _populateClientDropdown('proj-f-client_id', project ? project.client_id : '');
    _populateSelect('proj-f-am_username', '— 未指派 —');
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

    document.getElementById('proj-modal').style.display = 'flex';
    document.getElementById('proj-f-name').focus();
}

async function saveProject() {
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
        const resp = _editingId
            ? await _fetch(`/projects/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/projects', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('proj-modal').style.display = 'none';

        if (resp.project) {
            const idx = _projects.findIndex(p => p.id === resp.project.id);
            if (idx >= 0) _projects[idx] = resp.project;
            else _projects.unshift(resp.project);
            renderList();
            if (_editingId) selectProject(_editingId);
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

// ── Delete ───────────────────────────────────────────────────

async function deleteProject(project) {
    if (!confirm(`確定刪除「${project.name}」？此操作無法復原。`)) return;
    try {
        await _fetch(`/projects/${project.id}`, { method: 'DELETE' });
        closeDetail();
        await loadProjects();
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

// ── CSV Import ───────────────────────────────────────────────

let _csvFile = null;

function openImportModal() {
    _csvFile = null;
    document.getElementById('proj-drop-filename').textContent = '';
    const result = document.getElementById('proj-import-result');
    result.style.display = 'none';
    result.className = 'crm-import-result';
    document.getElementById('proj-btn-do-import').disabled = true;
    document.getElementById('proj-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('proj-drop-filename').textContent = file ? file.name : '';
    document.getElementById('proj-btn-do-import').disabled = !file;
}

async function doImport() {
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

// ── Init ─────────────────────────────────────────────────────

export async function initCrmProjectsTab() {
    // Move modals to body
    for (const id of ['proj-modal', 'proj-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    // Global handlers for onclick
    window._projSelect = selectProject;
    window._projEdit = (id) => {
        const p = _projects.find(x => x.id === id);
        if (p) openModal(p);
    };
    window._projDelete = (id) => {
        const p = _projects.find(x => x.id === id);
        if (p) deleteProject(p);
    };
    window._projDup = (id) => {
        const p = _projects.find(x => x.id === id);
        if (p) { openModal(p); _editingId = null; document.getElementById('proj-modal-title').textContent = '複製專案'; }
    };
    window._projAddExpense = () => _showExpenseForm();
    window._projEditExpense = (id, cat, est, act, notes) => _showExpenseForm(id, cat, est, act, notes);
    window._projSaveExpense = async (editId) => {
        if (!_selectedId) return;
        const payload = {
            category: document.getElementById('exp-f-cat').value,
            estimated: parseInt(document.getElementById('exp-f-est').value) || 0,
            actual: parseInt(document.getElementById('exp-f-act').value) || 0,
            notes: document.getElementById('exp-f-notes').value,
        };
        try {
            let expenseId = editId;
            if (editId) {
                await _fetch('/project-expenses/' + editId, { method: 'PUT', body: JSON.stringify(payload) });
            } else {
                const r = await _fetch('/projects/' + _selectedId + '/expenses', { method: 'POST', body: JSON.stringify(payload) });
                expenseId = r.expense_id;
            }
            // Upload receipt if file selected
            const fileInput = document.getElementById('exp-f-receipt');
            if (fileInput?.files?.[0] && expenseId) {
                const form = new FormData();
                form.append('file', fileInput.files[0]);
                const token = localStorage.getItem('auth_token');
                await fetch('/api/v1/crm/project-expenses/' + expenseId + '/receipt', {
                    method: 'POST', headers: token ? { 'Authorization': 'Bearer ' + token } : {}, body: form
                });
            }
            _loadFinancialSummary(_selectedId);
        } catch (e) { alert('儲存失敗：' + e.message); }
    };
    window._projDeleteExpense = async (id) => {
        if (!confirm('確定刪除此雜支？')) return;
        try {
            await _fetch('/project-expenses/' + id, { method: 'DELETE' });
            _loadFinancialSummary(_selectedId);
        } catch (e) { alert(e.message); }
    };
    window._projRefreshQuotes = (projectId) => {
        if (_selectedId === projectId) _loadProjectQuotations(projectId);
    };
    window._projAddStaff = async () => {
        if (!_selectedId) return;
        let staffList = [];
        try { staffList = (await _fetch('/staff?status=在職')).staff || []; } catch(_) {}
        if (staffList.length === 0) { alert('請先在人力資源 Tab 新增人員'); return; }
        const container = document.getElementById('proj-staff-list');
        if (!container) return;
        // Show inline form at top
        const formId = 'proj-staff-add-form';
        if (document.getElementById(formId)) return; // already showing
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
            await _fetch('/projects/' + _selectedId + '/staff', {
                method: 'POST', body: JSON.stringify({ staff_id: staffId, role_in_project: role, days })
            });
            _loadProjectStaff(_selectedId);
        } catch (e) { alert('新增失敗：' + e.message); }
    };
    window._projRemoveStaff = async (psId, projectId) => {
        if (!confirm('確定移除此派工？')) return;
        try {
            await _fetch('/project-staff/' + psId, { method: 'DELETE' });
            _loadProjectStaff(projectId);
        } catch (e) { alert(e.message); }
    };
    window._projAddQuote = () => {
        if (!_selectedId) return;
        if (window._openQuoteModalForProject) {
            window._openQuoteModalForProject(_selectedId);
        }
    };

    // Search + filters
    let _searchTimer;
    document.getElementById('proj-search').addEventListener('input', e => {
        _filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadProjects, 300);
    });
    document.getElementById('proj-filter-status').addEventListener('change', e => {
        _filters.status = e.target.value;
        loadProjects();
    });
    document.getElementById('proj-filter-client').addEventListener('change', e => {
        _filters.client_id = e.target.value;
        loadProjects();
    });
    document.getElementById('proj-filter-am').addEventListener('change', e => {
        _filters.am = e.target.value;
        loadProjects();
    });

    // Buttons
    document.getElementById('proj-btn-add').addEventListener('click', () => openModal());
    document.getElementById('proj-btn-import').addEventListener('click', openImportModal);
    document.getElementById('proj-btn-save').addEventListener('click', saveProject);
    document.getElementById('proj-detail-close').addEventListener('click', closeDetail);
    document.getElementById('proj-btn-do-import').addEventListener('click', doImport);

    // CSV file input + drop zone
    document.getElementById('proj-csv-file').addEventListener('change', e => {
        _setCsvFile(e.target.files[0] || null);
    });
    const zone = document.getElementById('proj-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) _setCsvFile(file);
    });

    // Detail sub-tabs
    document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#proj-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('proj-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('proj-detail-team').classList.toggle('hidden', tab !== 'team');
            document.getElementById('proj-detail-quotes').classList.toggle('hidden', tab !== 'quotes');
            document.getElementById('proj-detail-finance').classList.toggle('hidden', tab !== 'finance');
        });
    });

    // Modal overlay click to close
    for (const id of ['proj-modal', 'proj-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('proj-resize-handle', 'proj-detail-panel');

    await Promise.all([loadClients(), loadUsers(), loadProjects()]);
}
