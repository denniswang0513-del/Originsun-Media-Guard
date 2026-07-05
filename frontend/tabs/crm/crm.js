/**
 * crm.js — 客戶管理 Tab
 * 功能：列表視圖 + 詳情面板 + 新增/編輯 Modal + CSV 匯入
 */

import { crmFetch as _fetch, crmCacheFetch, crmCacheInvalidate, esc as _esc, fmtNum as _fmtNum, renderAvatar, populateUserSelect, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, enumIndex } from './crm-utils.js';

// ── State ────────────────────────────────────────────────────

let _clients = [];
let _users = [];
let _selectedId = null;
let _editingId = null;  // null = 新增, string = 編輯
let _filters = { q: '', status: '', am: '' };
let _csvFile = null;

// ── API ──────────────────────────────────────────────────────

async function _fetchMultipart(path, formData) {
    const token = localStorage.getItem('auth_token');
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    const res = await fetch('/api/v1/crm' + path, { method: 'POST', headers, body: formData });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || '匯入失敗');
    }
    return res.json();
}

// ── Data Loading ─────────────────────────────────────────────

async function loadClients() {
    const params = new URLSearchParams();
    if (_filters.q)      params.set('q', _filters.q);
    if (_filters.status) params.set('status', _filters.status);
    if (_filters.am)     params.set('am', _filters.am);

    try {
        const data = await _fetch(`/clients?${params}`);
        _clients = data.clients || [];
    } catch (e) {
        _clients = [];
        _showListError(e.message);
    }
    renderList();
}

async function loadUsers() {
    try {
        const data = await crmCacheFetch('users', '/users');
        _users = data.users || [];
        _populateSelect('crm-filter-am', '全部 AM');
    } catch (_) {
        _users = [];
    }
}

// ── Rendering ────────────────────────────────────────────────

function _badge(status) {
    const s = status || '潛在客戶';
    const known = ['潛在客戶','新客戶','舊客戶','暫停合作'];
    const cls = known.includes(s) ? `crm-badge crm-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

function _avatar(username, size = 22) {
    return renderAvatar(username, _users, size);
}

// 客戶狀態工作流順序;_sorter 用這個 index 而非字串比較
const _CLIENT_STATUS_ORDER = ['潛在客戶', '新客戶', '舊客戶'];
const _sorter = createSortable({
    storageKey: 'crm_clients_sort',
    defaultSort: { key: 'updated', dir: 'desc' },
    panelId: 'crm-list-panel',
    onChange: () => renderList(),
    getters: {
        name:    c => (c.short_name || '').toLowerCase(),
        status:  c => enumIndex(_CLIENT_STATUS_ORDER, c.status, '潛在客戶'),
        am:      c => (c.am_username || '').toLowerCase(),
        proj:    c => c.project_count || 0,
        revenue: c => c.total_contract || 0,
        updated: c => c.updated_at || '',
    },
});

function renderList() {
    const body = document.getElementById('crm-list-body');
    if (!body) return;
    _sorter.attach();

    if (_clients.length === 0) {
        body.innerHTML = `<div class="crm-empty">找不到客戶資料${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }

    body.innerHTML = _sorter.sorted(_clients).map(c => `
        <div class="crm-row${c.id === _selectedId ? ' selected' : ''}" data-id="${c.id}" onclick="window._crmSelectClient('${c.id}')">
            <div class="crm-row-name">${_esc(c.short_name)}</div>
            <div class="crm-row-status">${_badge(c.status)}</div>
            <div class="crm-row-am" title="${_esc(c.am_username || '')}">
                ${c.am_username ? _avatar(c.am_username, 18) + '<span>' + _esc(c.am_username) + '</span>' : '<span class="crm-muted">—</span>'}
            </div>
            <div class="crm-row-proj">${c.project_count || 0}</div>
            <div class="crm-row-revenue">${c.total_contract ? '$' + _fmtNum(c.total_contract) : '<span class="crm-muted">—</span>'}</div>
            <div class="crm-row-contact">${c.updated_at ? c.updated_at.substring(0,10) : '—'}</div>
            ${kebabMenuHtml(c.id, { onEdit: '_crmEditClient', onDuplicate: '_crmDupClient', onDelete: '_crmDeleteClient' })}
        </div>
    `).join('');
}

const _INFO_EDIT_FIELDS = [
    {name:'short_name', label:'客戶代稱', type:'text'},
    {name:'full_name', label:'抬頭', type:'text'},
    {name:'tax_id', label:'統一編號', type:'text'},
    {name:'payment_info', label:'匯款資訊', type:'text'},
    {name:'payment_note', label:'匯款備註', type:'text'},
];

const _REL_EDIT_FIELDS = [
    {name:'am_username', label:'AM', type:'text'},
    {name:'source_channel', label:'來源管道', type:'text'},
    {name:'contact_person', label:'聯絡人', type:'text'},
    {name:'contact_method', label:'聯絡方式', type:'text'},
    {name:'cooperation_note', label:'合作契機', type:'text'},
    {name:'notes', label:'備註', type:'textarea'},
];

async function _loadClientPerformance(clientId) {
    const container = document.getElementById('crm-detail-perf');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty">載入中...</div>';
    try {
        const data = await _fetch('/projects?client_id=' + clientId);
        const projects = data.projects || [];
        if (projects.length === 0) {
            container.innerHTML = '<div class="crm-empty">尚無專案資料</div>';
            return;
        }

        const _n = (n) => (n || 0).toLocaleString('zh-TW');
        const excludeStatus = new Set(['洽談中', '報價中']);
        const activeProjects = projects.filter(p => !excludeStatus.has(p.status));
        const totalProjects = activeProjects.length;
        const totalRevenue = activeProjects.reduce((s, p) => s + (p.contract_amount || 0), 0);
        const avgProject = totalProjects > 0 ? Math.round(totalRevenue / totalProjects) : 0;
        const totalReceived = activeProjects.reduce((s, p) => s + (p.amount_received || 0), 0);
        const collectRate = totalRevenue > 0 ? Math.round(totalReceived / totalRevenue * 100) : 0;

        // Status breakdown
        const statusCount = {};
        for (const p of projects) {
            const s = p.status || '其他';
            statusCount[s] = (statusCount[s] || 0) + 1;
        }
        const statusColors = { '洽談中': '#3b82f6', '進行中': '#f59e0b', '報價中': '#8b5cf6', '已結案': '#22c55e', '結案作業': '#14b8a6', '已取消': '#6b7280' };

        // Yearly revenue (only active projects)
        const yearRevenue = {};
        for (const p of activeProjects) {
            const y = p.start_date ? p.start_date.substring(0, 4) : '未定';
            yearRevenue[y] = (yearRevenue[y] || 0) + (p.contract_amount || 0);
        }
        const yearKeys = Object.keys(yearRevenue).sort().reverse();

        const collectColor = collectRate >= 80 ? '#86efac' : collectRate >= 50 ? '#fbbf24' : '#fca5a5';

        container.innerHTML = `
            <div class="crm-perf-grid">
                <div class="crm-perf-card">
                    <div class="crm-perf-num">${totalProjects}</div>
                    <div class="crm-perf-label">專案總數</div>
                </div>
                <div class="crm-perf-card">
                    <div class="crm-perf-num">$${_n(totalRevenue)}</div>
                    <div class="crm-perf-label">合約總額</div>
                </div>
                <div class="crm-perf-card">
                    <div class="crm-perf-num">$${_n(avgProject)}</div>
                    <div class="crm-perf-label">平均單價</div>
                </div>
                <div class="crm-perf-card">
                    <div class="crm-perf-num" style="color:${collectColor};">${collectRate}%</div>
                    <div class="crm-perf-label">收款率</div>
                </div>
            </div>

            <div style="margin-top:16px;">
                <div style="font-size:12px;font-weight:700;color:#6b7280;margin-bottom:8px;">專案狀態分布</div>
                ${Object.entries(statusCount).map(([s, c]) => {
                    const color = statusColors[s] || '#9ca3af';
                    const pct = Math.round(c / totalProjects * 100);
                    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                        <span style="min-width:60px;font-size:12px;color:#9ca3af;">${_esc(s)}</span>
                        <div style="flex:1;height:8px;background:#1e1e1e;border-radius:4px;overflow:hidden;">
                            <div style="width:${pct}%;height:100%;background:${color};border-radius:4px;"></div>
                        </div>
                        <span style="font-size:11px;color:#e0e0e0;min-width:30px;text-align:right;">${c}</span>
                    </div>`;
                }).join('')}
            </div>

            <div style="margin-top:16px;">
                <div style="font-size:12px;font-weight:700;color:#6b7280;margin-bottom:8px;">年度營收</div>
                ${yearKeys.map(y => `
                    <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2e2e2e;">
                        <span style="font-size:13px;color:#e0e0e0;">${_esc(y)}</span>
                        <span style="font-size:13px;font-weight:700;color:#fbbf24;">$${_n(yearRevenue[y])}</span>
                    </div>
                `).join('')}
            </div>
        `;
    } catch (e) {
        container.innerHTML = '<div class="crm-empty" style="color:#fca5a5;">載入失敗</div>';
    }
}

async function _loadClientProjects(clientId) {
    const container = document.getElementById('crm-detail-projects');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty">載入中...</div>';
    try {
        const data = await _fetch('/projects?client_id=' + clientId);
        const projects = data.projects || [];
        if (projects.length === 0) {
            container.innerHTML = '<div class="crm-empty">尚無專案紀錄</div>';
            return;
        }
        const statusColors = { '洽談中': '#3b82f6', '執行中': '#f59e0b', '已完成': '#22c55e', '已取消': '#6b7280', '進行中': '#f59e0b', '報價中': '#8b5cf6', '已結案': '#22c55e', '結案作業': '#14b8a6' };
        let lastYear = '';
        container.innerHTML = projects.map(p => {
            const color = statusColors[p.status] || '#9ca3af';
            const startDate = p.start_date ? p.start_date.substring(0, 10) : '—';
            const amount = p.contract_amount ? '$' + _fmtNum(p.contract_amount) : '—';
            const payStatus = p.payment_status || '—';
            const year = p.start_date ? p.start_date.substring(0, 4) : '';
            let yearHeader = '';
            if (year && year !== lastYear) {
                lastYear = year;
                yearHeader = `<div class="crm-project-year-header">${year}</div>`;
            }
            return `${yearHeader}
            <div class="crm-project-card">
                <div class="crm-project-card-header">
                    <span class="crm-project-card-name">${_esc(p.name)}</span>
                    <span class="crm-badge" style="background:${color}22;color:${color};border:1px solid ${color}44;">${_esc(p.status)}</span>
                </div>
                <div class="crm-project-card-meta">
                    <span>起始日 ${startDate}</span>
                    <span>合約 ${amount}</span>
                    <span>收款 ${_esc(payStatus)}</span>
                </div>
                <div class="crm-project-card-actions">
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._crmShowProjectDetail('${_esc(p.id)}')">詳情</button>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        container.innerHTML = '<div class="crm-empty" style="color:#fca5a5;">載入失敗</div>';
    }
}

window._crmShowProjectDetail = async (projectId) => {
    let modal = document.getElementById('crm-project-modal');
    if (modal) modal.remove();

    const _n = (n) => (n || 0).toLocaleString('zh-TW');
    const prop = (label, value) => {
        const isEmpty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? '—' : _esc(String(value))}</div></div>`;
    };
    const statusColors = { '洽談中': '#3b82f6', '進行中': '#f59e0b', '報價中': '#8b5cf6', '已結案': '#22c55e', '結案作業': '#14b8a6', '已取消': '#6b7280' };

    try {
        const [p, fin, staffData, quoteData] = await Promise.all([
            _fetch('/projects/' + projectId),
            _fetch('/projects/' + projectId + '/financial-summary').catch(() => null),
            _fetch('/projects/' + projectId + '/staff').catch(() => ({ staff: [] })),
            _fetch('/projects/' + projectId + '/quotations').catch(() => ({ quotations: [] })),
        ]);

        const color = statusColors[p.status] || '#9ca3af';
        const staff = staffData.staff || [];
        const quotes = quoteData.quotations || [];

        // Tab 1: 專案資訊
        const infoHtml = `
            ${prop('客戶', p.client_short_name)}
            <div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value"><span class="crm-badge" style="background:${color}22;color:${color};border:1px solid ${color}44;">${_esc(p.status)}</span></div></div>
            ${prop('類型', p.project_type)}
            ${prop('起始日', p.start_date ? p.start_date.substring(0, 10) : '')}
            ${prop('結案日', p.completion_date ? p.completion_date.substring(0, 10) : '')}
            ${prop('資料夾', p.folder_path)}
            ${prop('說明', p.description)}
            ${prop('AM', p.am_username)}
            ${prop('備註', p.notes)}
        `;

        // Tab 2: 人員配置
        const staffHtml = staff.length === 0
            ? '<div class="crm-empty">尚無派工</div>'
            : staff.map(r => `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #2e2e2e;">
                    <span>${_esc(r.staff_name)} <span style="color:#9ca3af;font-size:11px;">${_esc(r.staff_role)}</span></span>
                    <span style="font-size:12px;">${r.days}天 × $${_n(r.rate)} = <b>$${_n(r.cost)}</b></span>
                </div>
            `).join('') + `<div style="text-align:right;font-weight:700;padding:8px 0;">合計: $${_n(staff.reduce((s, r) => s + r.cost, 0))}</div>`;

        // Tab 3: 報價
        const quoteHtml = quotes.length === 0
            ? '<div class="crm-empty">尚無報價</div>'
            : quotes.map(q => {
                const qColor = statusColors[q.status] || '#9ca3af';
                return `<div style="padding:8px 0;border-bottom:1px solid #2e2e2e;display:flex;justify-content:space-between;align-items:center;">
                    <span>v${q.version} <span class="crm-badge" style="background:${qColor}22;color:${qColor};border:1px solid ${qColor}44;">${_esc(q.status)}</span></span>
                    <span style="font-weight:700;">$${_n(q.final_price ?? q.total)}</span>
                </div>`;
            }).join('');

        // Tab 4: 財務
        let finHtml = '<div class="crm-empty">無財務資料</div>';
        if (fin) {
            const profitColor = fin.profit_rate >= 20 ? '#86efac' : fin.profit_rate >= 0 ? '#fbbf24' : '#fca5a5';
            finHtml = `
                ${prop('合約金額（含稅）', '$' + _n(fin.contract_amount))}
                ${prop('未稅金額', '$' + _n(fin.ex_tax))}
                ${prop('目標毛利 (' + fin.profit_target_pct + '%)', '$' + _n(fin.profit_target))}
                <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
                ${prop('外包預算', '$' + _n(fin.outsource_budget))}
                ${prop('外包實際（派工）', '$' + _n(fin.staff_actual))}
                <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
                <div class="crm-detail-prop"><div class="crm-prop-label" style="font-weight:700;">實際毛利</div><div class="crm-prop-value" style="font-weight:700;color:${profitColor};">$${_n(fin.actual_profit)} (${fin.profit_rate}%)</div></div>
                <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
                ${prop('帳務狀況', fin.payment_status || '未到帳')}
                ${prop('應收帳款', '$' + _n(fin.amount_receivable))}
                ${prop('已收帳款', '$' + _n(fin.amount_received))}
            `;
        }

        const html = `
        <div id="crm-project-modal" class="crm-modal-overlay" style="display:flex;">
            <div class="crm-modal" style="max-width:600px;">
                <div class="crm-modal-header" style="flex-wrap:wrap;gap:8px;">
                    <h3 style="flex:1;min-width:150px;">${_esc(p.name)}</h3>
                    <div class="crm-pm-tabs" style="display:flex;gap:2px;">
                        <button class="crm-tab active" data-pm-tab="info">專案資訊</button>
                        <button class="crm-tab" data-pm-tab="staff">人員配置</button>
                        <button class="crm-tab" data-pm-tab="quote">報價</button>
                        <button class="crm-tab" data-pm-tab="finance">財務</button>
                    </div>
                    <button class="crm-detail-close" onclick="document.getElementById('crm-project-modal').remove()">&#x2715;</button>
                </div>
                <div class="crm-modal-body" style="max-height:60vh;overflow-y:auto;">
                    <div id="pm-tab-info">${infoHtml}</div>
                    <div id="pm-tab-staff" class="hidden">${staffHtml}</div>
                    <div id="pm-tab-quote" class="hidden">${quoteHtml}</div>
                    <div id="pm-tab-finance" class="hidden">${finHtml}</div>
                </div>
            </div>
        </div>`;

        document.body.insertAdjacentHTML('beforeend', html);

        // Tab switching inside modal
        document.querySelectorAll('.crm-pm-tabs .crm-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.crm-pm-tabs .crm-tab').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const t = btn.dataset.pmTab;
                ['info', 'staff', 'quote', 'finance'].forEach(k => {
                    document.getElementById('pm-tab-' + k).classList.toggle('hidden', k !== t);
                });
            });
        });

        // Close on overlay click
        document.getElementById('crm-project-modal').addEventListener('click', e => {
            if (e.target.id === 'crm-project-modal') e.target.remove();
        });
    } catch (e) {
        alert('載入專案詳情失敗: ' + e.message);
    }
};

window._crmGoToProject = (projectId) => {
    const projTab = document.querySelector('[data-section="tab_crm_projects"]');
    if (projTab) projTab.click();
    setTimeout(() => {
        if (window._projSelect) window._projSelect(projectId);
    }, 300);
};

function renderDetail(client) {
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(value)}</div>
        </div>`;
    };

    const amHtml = client.am_username
        ? `<div class="crm-am-row">${_avatar(client.am_username, 28)}<span>${_esc(client.am_username)}</span></div>`
        : '<span class="crm-prop-value empty">未指派</span>';

    // Title (fixed above tabs)
    document.getElementById('crm-detail-title').textContent = client.short_name;

    // Tab 1: 客戶資訊
    document.getElementById('crm-detail-info').innerHTML = `
        ${prop('抬頭', client.full_name)}
        ${prop('統一編號', client.tax_id)}
        ${prop('匯款資訊', client.payment_info)}
        ${prop('匯款備註', client.payment_note)}
    `;

    // Tab 2: 客戶關係
    document.getElementById('crm-detail-rel').innerHTML = `
        <div class="crm-detail-prop">
            <div class="crm-prop-label">狀態</div>
            <div class="crm-prop-value">${_badge(client.status)}</div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">AM</div>
            <div class="crm-prop-value">${amHtml}</div>
        </div>
        ${prop('來源管道', client.source_channel)}
        ${prop('聯絡人', client.contact_person)}
        ${prop('聯絡方式', client.contact_method)}
        ${prop('合作契機', client.cooperation_note)}
        ${prop('備註', client.notes)}
        ${prop('修改日期', client.updated_at ? client.updated_at.substring(0,10) : '')}
    `;

    const actions = document.getElementById('crm-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">✕</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    // Tab 3: 專案紀錄
    _loadClientProjects(client.id);
    _loadClientPerformance(client.id);

    addEditButton('crm-bar-actions', () => {
        // Detect active tab
        const activeTab = document.querySelector('#crm-detail-tabs .crm-tab.active');
        const tab = activeTab?.dataset.tab || 'info';
        if (tab !== 'info' && tab !== 'rel') return; // Only info & rel are editable

        const containerId = tab === 'info' ? 'crm-detail-info' : 'crm-detail-rel';
        let fields = tab === 'info' ? _INFO_EDIT_FIELDS : _REL_EDIT_FIELDS;
        // Dynamically inject user options for AM select
        if (tab === 'rel') {
            const amOptions = [{value:'', label:'— 未指派 —'}, ..._users.map(u => ({value: u.username, label: u.username}))];
            fields = fields.map(f => f.name === 'am_username' ? {...f, type:'select', options: amOptions} : f);
        }

        enableInlineEdit(containerId, 'crm-bar-actions', fields, client,
            async (payload) => {
                // Only send fields that ClientPayload accepts
                const allowed = ['short_name','full_name','tax_id','am_username','source_channel',
                    'contact_person','contact_method','cooperation_note','payment_info','payment_note','notes'];
                const full = {};
                for (const k of allowed) full[k] = payload[k] ?? client[k] ?? '';
                await _fetch('/clients/' + client.id, { method: 'PUT', body: JSON.stringify(full) });
                const updated = await _fetch('/clients/' + client.id);
                renderDetail(updated);
                await loadClients();
            },
            () => renderDetail(client)
        );
    });
}

/** Shared helper: populate a <select> with user options */
function _populateSelect(elementId, placeholder) {
    populateUserSelect(elementId, _users, placeholder);
}

function _showListError(msg) {
    const body = document.getElementById('crm-list-body');
    if (body) body.innerHTML = `<div class="crm-empty" style="color:#fca5a5;">❌ ${_esc(msg)}</div>`;
}

// ── Detail Panel ─────────────────────────────────────────────

function selectClient(id) {
    _selectedId = id;
    renderList();

    const panel = document.getElementById('crm-detail-panel');
    if (!panel) return;
    panel.style.display = 'flex';
    const handle = document.getElementById('crm-resize-handle');
    if (handle) handle.style.display = '';

    // Use already-loaded data from _clients instead of re-fetching
    const client = _clients.find(c => c.id === id);
    if (!client) return;
    renderDetail(client);
}

function closeDetail() {
    _selectedId = null;
    const panel = document.getElementById('crm-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('crm-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Add / Edit Modal ─────────────────────────────────────────

const _FIELDS = ['short_name','full_name','tax_id','payment_info','payment_note',
    'am_username','source_channel','contact_person','contact_method',
    'cooperation_note','notes'];

function openModal(client = null) {
    _editingId = client ? client.id : null;
    document.getElementById('crm-modal-title').textContent = client ? '編輯客戶' : '新增客戶';
    const errEl = document.getElementById('crm-modal-error');
    errEl.textContent = '';
    errEl.style.display = 'none';

    _populateSelect('crm-f-am_username', '— 未指派 —');

    for (const f of _FIELDS) {
        const el = document.getElementById(`crm-f-${f}`);
        if (el) el.value = (client ? (client[f] ?? '') : '');
    }

    document.getElementById('crm-modal').style.display = 'flex';
    document.getElementById('crm-f-short_name').focus();
}

async function saveClient() {
    const short_name = document.getElementById('crm-f-short_name').value.trim();
    if (!short_name) {
        _showModalError('客戶代稱為必填欄位');
        return;
    }

    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById(`crm-f-${f}`);
        payload[f] = el ? el.value.trim() : '';
    }

    const btn = document.getElementById('crm-btn-save');
    btn.disabled = true;
    btn.textContent = '儲存中...';

    try {
        const resp = _editingId
            ? await _fetch(`/clients/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/clients', { method: 'POST', body: JSON.stringify(payload) });
        crmCacheInvalidate('clients');
        document.getElementById('crm-modal').style.display = 'none';

        // Use response data to update local state instead of re-fetching
        if (resp.client) {
            const idx = _clients.findIndex(c => c.id === resp.client.id);
            if (idx >= 0) _clients[idx] = resp.client;
            else _clients.unshift(resp.client);
            renderList();
            if (_editingId) selectClient(_editingId);
        } else {
            await loadClients();
        }
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '儲存';
    }
}

function _showModalError(msg) {
    const el = document.getElementById('crm-modal-error');
    el.textContent = msg;
    el.style.display = 'block';
}

// ── Delete ───────────────────────────────────────────────────

async function deleteClient(client) {
    if (!confirm(`確定刪除「${client.short_name}」？此操作無法復原。`)) return;
    try {
        await _fetch(`/clients/${client.id}`, { method: 'DELETE' });
        crmCacheInvalidate('clients');
        closeDetail();
        await loadClients();
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

// ── CSV Import ───────────────────────────────────────────────

function openImportModal() {
    _csvFile = null;
    document.getElementById('crm-drop-filename').textContent = '';
    const result = document.getElementById('crm-import-result');
    result.style.display = 'none';
    result.className = 'crm-import-result';
    document.getElementById('crm-btn-do-import').disabled = true;
    document.getElementById('crm-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('crm-drop-filename').textContent = file ? file.name : '';
    document.getElementById('crm-btn-do-import').disabled = !file;
}

async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('crm-btn-do-import');
    btn.disabled = true;
    btn.textContent = '匯入中...';

    try {
        const form = new FormData();
        form.append('file', _csvFile);
        const data = await _fetchMultipart('/clients/import_csv', form);
        const result = document.getElementById('crm-import-result');
        result.className = 'crm-import-result';
        let msg = `✅ 匯入完成<br>新增：<strong>${data.imported}</strong> 筆 ／ 更新：<strong>${data.updated}</strong> 筆 ／ 跳過：<strong>${data.skipped}</strong> 筆`;
        if (data.hint) msg += `<br><span style="color:#fbbf24;font-size:12px;">${_esc(data.hint)}</span>`;
        result.innerHTML = msg;
        result.style.display = 'block';
        await loadClients();
    } catch (e) {
        const result = document.getElementById('crm-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = `❌ ${_esc(e.message)}`;
        result.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '開始匯入';
    }
}

// ── Utility ──────────────────────────────────────────────────

// ── Init ─────────────────────────────────────────────────────

export async function initCrmTab() {
    // Move modals to document.body — parent section's `transform` class
    // creates a new containing block that breaks position:fixed
    for (const id of ['crm-modal', 'crm-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._crmSelectClient = selectClient;
    window._crmEditClient = (id) => {
        const client = _clients.find(c => c.id === id);
        if (client) openModal(client);
    };
    window._crmDeleteClient = (id) => {
        const client = _clients.find(c => c.id === id);
        if (client) deleteClient(client);
    };
    window._crmDupClient = (id) => {
        const client = _clients.find(c => c.id === id);
        if (client) { openModal(client); _editingId = null; document.getElementById('crm-modal-title').textContent = '複製客戶'; }
    };

    let _searchTimer;
    document.getElementById('crm-search').addEventListener('input', e => {
        _filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadClients, 300);
    });
    document.getElementById('crm-filter-status').addEventListener('change', e => {
        _filters.status = e.target.value;
        loadClients();
    });
    document.getElementById('crm-filter-am').addEventListener('change', e => {
        _filters.am = e.target.value;
        loadClients();
    });

    document.getElementById('crm-btn-add').addEventListener('click', () => openModal());
    document.getElementById('crm-btn-import').addEventListener('click', openImportModal);
    document.getElementById('crm-btn-save').addEventListener('click', saveClient);
    document.getElementById('crm-detail-close').addEventListener('click', closeDetail);
    document.getElementById('crm-btn-do-import').addEventListener('click', doImport);

    document.getElementById('crm-csv-file').addEventListener('change', e => {
        _setCsvFile(e.target.files[0] || null);
    });

    const zone = document.getElementById('crm-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) _setCsvFile(file);
    });

    // Detail sub-tab switching
    document.querySelectorAll('#crm-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#crm-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('crm-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('crm-detail-rel').classList.toggle('hidden', tab !== 'rel');
            document.getElementById('crm-detail-projects').classList.toggle('hidden', tab !== 'projects');
            document.getElementById('crm-detail-perf').classList.toggle('hidden', tab !== 'perf');
        });
    });

    // Close modals on overlay click
    for (const id of ['crm-modal', 'crm-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('crm-resize-handle', 'crm-detail-panel');

    await Promise.all([loadUsers(), loadClients()]);
}
