/**
 * crm-staff.js — 人力資源 Tab
 */

import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, saveSettings, createSortable } from './crm-utils.js';

let _staff = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', role: '', status: '' };
let _csvFile = null;
const _DEFAULT_ROLES = ['攝影師','剪輯師','導演','製片','燈光','收音','空拍','動畫'];
let _roles = [..._DEFAULT_ROLES];

// ── Data ─────────────────────────────────────────────────────

async function loadStaff() {
    const params = new URLSearchParams();
    if (_filters.q)      params.set('q', _filters.q);
    if (_filters.role)   params.set('role', _filters.role);
    if (_filters.status) params.set('status', _filters.status);
    try {
        const data = await _fetch(`/staff?${params}`);
        _staff = data.staff || [];
    } catch (_) { _staff = []; }
    renderList();
}

// ── Rendering ────────────────────────────────────────────────

const _STATUS_CLS = { '在職': 'crm-staff-badge-在職', '兼職': 'crm-staff-badge-兼職', '專案': 'crm-staff-badge-專案', '單位': 'crm-staff-badge-單位' };

function _sBadge(status) {
    const s = status || '在職';
    const cls = _STATUS_CLS[s] || '';
    return `<span class="crm-badge ${cls}">${_esc(s)}</span>`;
}

const _STATUS_ORDER = { '在職': 0, '兼職': 1, '單位': 2, '專案': 3 };

const _sorter = createSortable({
    storageKey: 'crm_staff_sort',
    defaultSort: { key: 'status', dir: 'asc' },
    panelId: 'staff-list-panel',
    onChange: () => renderList(),
    getters: {
        name:   s => (s.name || '').toLowerCase(),
        role:   s => (s.role || '').toLowerCase(),
        status: s => _STATUS_ORDER[s.status] ?? 9,
        phone:  s => (s.phone || ''),
    },
});

function renderList() {
    const body = document.getElementById('staff-list-body');
    if (!body) return;
    _sorter.attach();
    if (_staff.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無人員${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    body.innerHTML = _sorter.sorted(_staff).map(s => `
        <div class="crm-row${s.id === _selectedId ? ' selected' : ''}" onclick="window._staffSelect('${s.id}')">
            <div class="crm-row-name">${s.show_on_website ? '<span title="顯示於官網團隊頁" style="margin-right:4px;">🌐</span>' : ''}${_esc(s.name)}</div>
            <div class="crm-row-role">${_esc(s.role)}</div>
            <div class="crm-row-status">${_sBadge(s.status)}</div>
            <div class="crm-row-phone">${_esc(s.phone)}</div>
            ${kebabMenuHtml(s.id, { onEdit: '_staffEdit', onDuplicate: '_staffDup', onDelete: '_staffDelete' })}
        </div>
    `).join('');
}

const _STAFF_EDIT_FIELDS = [
    {name:'name', label:'姓名', type:'text'},
    {name:'role', label:'職能', type:'select', get options() { return [{value:'',label:'—'}, ..._roles.map(r => ({value:r,label:r}))]; }},
    {name:'status', label:'狀態', type:'select', options:[{value:'在職',label:'在職'},{value:'兼職',label:'兼職'},{value:'專案',label:'專案'},{value:'單位',label:'單位'}]},
    {name:'phone', label:'電話', type:'text'},
    {name:'email', label:'Email', type:'text'},
    {name:'id_number', label:'身分證 / 統編', type:'text'},
    {name:'address', label:'住址', type:'text'},
    {name:'bank_name', label:'銀行', type:'text'},
    {name:'bank_account', label:'帳號', type:'text'},
    {name:'portfolio_url', label:'作品集', type:'text'},
    {name:'notes', label:'備註', type:'text'},
];

function renderDetail(s) {
    document.getElementById('staff-detail-title').textContent = s.name;
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `<div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(String(value))}</div>
        </div>`;
    };

    document.getElementById('staff-detail-info').innerHTML = `
        ${prop('職能', s.role)}
        <div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value">${_sBadge(s.status)}</div></div>
        ${prop('電話', s.phone)}
        ${prop('Email', s.email)}
        ${prop('身分證 / 統編', s.id_number)}
        ${prop('住址', s.address)}
        ${prop('銀行', s.bank_name ? s.bank_name + ' ' + (s.bank_account || '') : '')}
        ${s.portfolio_url ? `<div class="crm-detail-prop"><div class="crm-prop-label">作品集</div><div class="crm-prop-value"><a href="${_esc(s.portfolio_url)}" target="_blank" style="color:#3b82f6;">${_esc(s.portfolio_url)}</a></div></div>` : ''}
        ${prop('備註', s.notes)}
        ${(s.created_via === 'showcase_edit' && !s.phone && !s.email) ? `
            <div class="crm-info-box">
                📥 由作品編輯時快速建立
                ${s.created_for_project_id ? '<span class="crm-info-box-sub" style="margin-top:0;">(專案 #' + _esc(s.created_for_project_id) + ')</span>' : ''}
                <div class="crm-info-box-sub">請補完聯絡方式、勞報資料以利日後派工</div>
            </div>
        ` : ''}
        ${_websiteSectionHtml(s)}
    `;

    _wireWebsiteSection(s);

    const actions = document.getElementById('staff-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('staff-bar-actions', () => {
        enableInlineEdit('staff-detail-info', 'staff-bar-actions', _STAFF_EDIT_FIELDS, s,
            async (payload) => {
                await _fetch('/staff/' + s.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/staff/' + s.id);
                renderDetail(updated);
                await loadStaff();
            },
            () => renderDetail(s)
        );
        // Inject ✎ button next to role select in inline edit (same row)
        const roleSelect = document.querySelector('#staff-detail-info [data-field="role"]');
        if (roleSelect) {
            const wrapper = roleSelect.parentNode;
            wrapper.style.cssText = 'display:flex;align-items:center;gap:6px;';
            roleSelect.style.flex = '1';
            const btn = document.createElement('button');
            btn.className = 'crm-btn crm-btn-secondary crm-btn-sm';
            btn.style.cssText = 'padding:4px 8px;flex-shrink:0;';
            btn.textContent = '✎';
            btn.title = '編輯職能選項';
            btn.onclick = (e) => { e.stopPropagation(); window._staffEditRoles(); };
            wrapper.appendChild(btn);
        }
    });

    _loadStaffProjects(s.id);
}

// ── 官網呈現（與「官網管理 › 關於我們」團隊卡同步） ─────────────────
// 寫入同一批 crm_staff 欄位（show_on_website + website_*）。獨立 section-save 按鈕，
// 只送這 5 個欄位 → 配合後端 model_dump(exclude_unset=True)，絕不動到正本欄位。

function _websiteSectionHtml(s) {
    const on = !!s.show_on_website;
    return `
        <div class="staff-section-title" style="margin-top:18px;display:flex;align-items:center;gap:8px;">
            🌐 官網呈現
            <span id="staff-web-badge" class="crm-badge ${on ? 'crm-staff-badge-在職' : ''}" style="${on ? '' : 'background:#3a3a3a;color:#aaa;'}">
                ${on ? '顯示於官網 ✓' : '未顯示 ✗'}
            </span>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">顯示於官網</div>
            <div class="crm-prop-value-edit">
                <label class="crm-checkbox-item" style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" id="staff-web-show"${on ? ' checked' : ''}>
                    <span>出現在官網「關於我們」團隊頁</span>
                </label>
            </div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">官網職稱</div>
            <div class="crm-prop-value-edit">
                <input type="text" class="crm-input" id="staff-web-title"
                    value="${_esc(s.website_title || '')}" placeholder="${_esc(s.role || '（沿用職能）')}">
            </div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">官網頭像 URL</div>
            <div class="crm-prop-value-edit">
                <input type="text" class="crm-input" id="staff-web-photo"
                    value="${_esc(s.website_photo_url || '')}" placeholder="${_esc(s.photo_url || '（沿用簡歷照片）')}">
            </div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">官網簡介</div>
            <div class="crm-prop-value-edit">
                <textarea class="crm-input crm-textarea" id="staff-web-bio" rows="3"
                    placeholder="${_esc(s.bio || '（沿用自我介紹）')}">${_esc(s.website_bio || '')}</textarea>
            </div>
        </div>
        <div class="crm-detail-prop">
            <div class="crm-prop-label">官網排序</div>
            <div class="crm-prop-value-edit">
                <input type="number" class="crm-input" id="staff-web-sort"
                    value="${s.website_sort_order ?? 0}" min="0" style="max-width:120px;">
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:6px;">
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="staff-web-save">儲存官網呈現</button>
            <a href="#" id="staff-web-jump" class="crm-muted" style="font-size:12px;color:#3b82f6;text-decoration:none;">
                前往「官網管理 › 關於我們」團隊編輯 →
            </a>
        </div>
        <div class="crm-info-box-sub" style="margin-top:6px;color:#888;font-size:11px;">
            與「官網管理 › 關於我們」團隊卡同步；空欄位＝沿用正本（職能 / 照片 / 自我介紹）。儲存後官網會自動重新發布。
        </div>
    `;
}

function _wireWebsiteSection(s) {
    const showEl = document.getElementById('staff-web-show');
    const badge = document.getElementById('staff-web-badge');
    if (showEl && badge) {
        showEl.addEventListener('change', () => {
            const on = showEl.checked;
            badge.textContent = on ? '顯示於官網 ✓' : '未顯示 ✗';
            badge.className = 'crm-badge' + (on ? ' crm-staff-badge-在職' : '');
            badge.style.cssText = on ? '' : 'background:#3a3a3a;color:#aaa;';
        });
    }

    const saveBtn = document.getElementById('staff-web-save');
    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            const payload = {
                show_on_website: document.getElementById('staff-web-show').checked,
                website_title: document.getElementById('staff-web-title').value.trim(),
                website_photo_url: document.getElementById('staff-web-photo').value.trim(),
                website_bio: document.getElementById('staff-web-bio').value.trim(),
                website_sort_order: parseInt(document.getElementById('staff-web-sort').value, 10) || 0,
            };
            saveBtn.disabled = true; saveBtn.textContent = '儲存中...';
            try {
                // PUT 已回傳更新後的完整 staff（= GET /staff/{id} 同一份），免再 GET 一次。
                const resp = await _fetch('/staff/' + s.id, { method: 'PUT', body: JSON.stringify(payload) });
                renderDetail(resp.staff);   // 重繪詳情（badge / placeholder 同步）
                await loadStaff();          // 重繪列表（🌐 indicator 同步）
            } catch (e) {
                alert('儲存失敗: ' + e.message);
                saveBtn.disabled = false; saveBtn.textContent = '儲存官網呈現';
            }
        });
    }

    const jump = document.getElementById('staff-web-jump');
    if (jump) {
        jump.addEventListener('click', (e) => {
            e.preventDefault();
            try {
                if (typeof window.switchTab === 'function') window.switchTab('tab_website');
                // 切到官網 Tab 後再切「關於我們」子視圖；給 initWebsiteTab 一點時間掛載
                const go = () => {
                    if (typeof window.websiteSwitchSubview === 'function') {
                        window.websiteSwitchSubview('about');
                    }
                };
                setTimeout(go, 150);
            } catch (_) { /* 跳轉失敗不致命，section 本身已可直接編輯 */ }
        });
    }
}

async function _loadStaffProjects(staffId) {
    const container = document.getElementById('staff-detail-projects');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty" style="padding:8px;">載入中...</div>';
    try {
        const data = await _fetch('/staff/' + staffId + '/projects');
        const projects = data.projects || [];
        const creditOnly = data.credit_only_projects || [];

        if (!projects.length && !creditOnly.length) {
            container.innerHTML = '<div class="crm-empty" style="padding:12px 0;">尚無專案紀錄</div>';
            return;
        }

        const totalEarned = projects.reduce((s, p) => s + (p.cost || 0), 0);

        const orphanAssigned = projects.filter(p => !(p.credits_in_project || []).length).length;

        const _statChip = (label, value, variant = '') =>
            `<div class="crm-stat-chip${variant ? ' ' + variant : ''}">
                <div class="crm-stat-chip-label">${label}</div>
                <div class="crm-stat-chip-value">${value}</div>
            </div>`;

        const chipsHtml = `
            <div class="crm-stat-chips">
                ${_statChip('派工', `${projects.length} 件`)}
                ${_statChip('累計費用', `$${_fmtNum(totalEarned)}`)}
                ${orphanAssigned > 0 ? _statChip('⚠ 派工未掛 credit', `${orphanAssigned} 件`, 'warn') : ''}
                ${creditOnly.length > 0 ? _statChip('外部演員（無派工）', `${creditOnly.length} 件`, 'info') : ''}
            </div>
        `;

        const _renderProjectRow = (p, opts = {}) => {
            const { isCreditOnly = false } = opts;
            const credits = (p.credits_in_project || [])
                .map(c => `${_esc(c.role_zh)}${c.duty ? ' / ' + _esc(c.duty) : ''}`)
                .join('、');
            return `
                <div class="crm-project-row">
                    <div class="crm-project-row-title">
                        ${_esc(p.project_name)}
                        <span class="crm-muted" style="font-weight:400;margin-left:8px;font-size:12px;">
                            ${_esc(p.client_name) || '（無客戶）'}
                        </span>
                    </div>
                    ${isCreditOnly
                        ? `<div class="crm-project-row-meta assigned">📋 派工：（無）</div>`
                        : `<div class="crm-project-row-meta assigned">📋 派工：${_esc(p.role_in_project) || '—'} · ${p.days || 0}天 · $${_fmtNum(p.cost || 0)}</div>`}
                    ${credits
                        ? `<div class="crm-project-row-meta credit">🎬 演職員：${credits}</div>`
                        : (!isCreditOnly
                              ? `<div class="crm-project-row-meta warn">⚠ 演職員：未掛 credit</div>`
                              : '')}
                </div>
            `;
        };

        const projectsHtml = projects.map(p => _renderProjectRow(p)).join('');
        const creditOnlyHtml = creditOnly.map(p => _renderProjectRow(p, {isCreditOnly: true})).join('');

        container.innerHTML = chipsHtml + projectsHtml + creditOnlyHtml;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Detail ───────────────────────────────────────────────────

function selectStaff(id) {
    _selectedId = id;
    _resumeLoaded = {};  // reset so resume tab reloads for new selection
    renderList();
    const panel = document.getElementById('staff-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('staff-resize-handle');
    if (handle) handle.style.display = '';
    // Reset to info tab
    document.querySelectorAll('#staff-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
    const infoTab = document.querySelector('#staff-detail-tabs .crm-tab[data-tab="info"]');
    if (infoTab) infoTab.classList.add('active');
    document.getElementById('staff-detail-info').classList.remove('hidden');
    document.getElementById('staff-detail-projects').classList.add('hidden');
    document.getElementById('staff-detail-resume').classList.add('hidden');
    const s = _staff.find(x => x.id === id);
    if (s) renderDetail(s);
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('staff-detail-panel').style.display = 'none';
    document.getElementById('staff-resize-handle').style.display = 'none';
    renderList();
}

// ── Modal ────────────────────────────────────────────────────

const _FIELDS = ['name', 'role', 'phone', 'email',
    'status', 'portfolio_url', 'id_number', 'address', 'bank_name', 'bank_account', 'notes'];

function openModal(staff = null) {
    _editingId = staff ? staff.id : null;
    document.getElementById('staff-modal-title').textContent = staff ? '編輯人員' : '新增人員';
    const errEl = document.getElementById('staff-modal-error');
    errEl.textContent = ''; errEl.style.display = 'none';

    for (const f of _FIELDS) {
        const el = document.getElementById(`staff-f-${f}`);
        if (el) el.value = staff ? (staff[f] ?? '') : (f === 'status' ? '在職' : '');
    }
    document.getElementById('staff-modal').style.display = 'flex';
    document.getElementById('staff-f-name').focus();
}

async function saveStaff() {
    const name = document.getElementById('staff-f-name').value.trim();
    if (!name) { _showModalError('姓名為必填'); return; }

    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById(`staff-f-${f}`);
        let val = el ? el.value.trim() : '';
        payload[f] = val;
    }

    const btn = document.getElementById('staff-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        const resp = _editingId
            ? await _fetch(`/staff/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
            : await _fetch('/staff', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('staff-modal').style.display = 'none';
        if (resp.staff) {
            const idx = _staff.findIndex(s => s.id === resp.staff.id);
            if (idx >= 0) _staff[idx] = resp.staff;
            else _staff.unshift(resp.staff);
            renderList();
            if (_editingId) selectStaff(_editingId);
        } else {
            await loadStaff();
        }
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false; btn.textContent = '儲存';
    }
}

async function deleteStaff(s) {
    if (!confirm(`確定刪除「${s.name}」？`)) return;
    try {
        await _fetch(`/staff/${s.id}`, { method: 'DELETE' });
        closeDetail();
        await loadStaff();
    } catch (e) { alert('刪除失敗：' + e.message); }
}

function _showModalError(msg) {
    const el = document.getElementById('staff-modal-error');
    el.textContent = msg; el.style.display = 'block';
}

// ── CSV Import ───────────────────────────────────────────────

function openImportModal() {
    _csvFile = null;
    document.getElementById('staff-drop-filename').textContent = '';
    const r = document.getElementById('staff-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('staff-btn-do-import').disabled = true;
    document.getElementById('staff-import-modal').style.display = 'flex';
}

function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('staff-drop-filename').textContent = file ? file.name : '';
    document.getElementById('staff-btn-do-import').disabled = !file;
}

async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('staff-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const form = new FormData();
        form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/staff/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '匯入失敗'); }
        const data = await res.json();
        const result = document.getElementById('staff-import-result');
        result.className = 'crm-import-result';
        let msg = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 更新：<strong>${data.updated}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        if (data.hint) msg += `<br><span style="color:#fbbf24;font-size:12px;">${_esc(data.hint)}</span>`;
        result.innerHTML = msg;
        result.style.display = 'block';
        await loadStaff();
    } catch (e) {
        const result = document.getElementById('staff-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally {
        btn.disabled = false; btn.textContent = '開始匯入';
    }
}

// ── Role Management ─────────────────────────────────────────

async function _loadRoles() {
    try {
        const s = await fetch('/api/settings/load').then(r => r.json());
        _roles = s.staff_roles && s.staff_roles.length > 0 ? s.staff_roles : [..._DEFAULT_ROLES];
    } catch (_) { _roles = [..._DEFAULT_ROLES]; }
    _populateRoleSelects();
}

function _populateRoleSelects() {
    const filter = document.getElementById('staff-filter-role');
    if (filter) {
        const val = filter.value;
        filter.innerHTML = '<option value="">全部職能</option>' + _roles.map(r => `<option value="${_esc(r)}">${_esc(r)}</option>`).join('');
        filter.value = val;
    }
    const modal = document.getElementById('staff-f-role');
    if (modal) {
        const val = modal.value;
        modal.innerHTML = '<option value="">—</option>' + _roles.map(r => `<option value="${_esc(r)}">${_esc(r)}</option>`).join('');
        modal.value = val;
    }
    // Also refresh inline edit select if open
    const inline = document.querySelector('#staff-detail-info [data-field="role"]');
    if (inline) {
        const val = inline.value;
        inline.innerHTML = '<option value="">—</option>' + _roles.map(r => `<option value="${_esc(r)}">${_esc(r)}</option>`).join('');
        inline.value = val;
    }
}

async function _saveRoles() {
    await saveSettings({ staff_roles: _roles });
}

window._staffEditRoles = function() {
    let overlay = document.getElementById('staff-roles-overlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'staff-roles-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) _closeRolesModal(); });

    function _render() {
        overlay.innerHTML = `
          <div class="crm-modal" style="max-width:360px;">
            <div class="crm-modal-header">
              <h3>編輯職能選項</h3>
              <button onclick="document.getElementById('staff-roles-overlay').remove()" class="crm-detail-close">✕</button>
            </div>
            <div class="crm-modal-body" style="max-height:400px;overflow-y:auto;">
              ${_roles.map((r, i) => `<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid #2a2a2a;">
                <span style="flex:1;font-size:14px;">${_esc(r)}</span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._staffRenameRole(${i})" style="padding:2px 6px;">✎</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._staffRemoveRole(${i})" style="padding:2px 6px;">✕</button>
              </div>`).join('')}
              <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._staffAddRole()" style="margin-top:8px;width:100%;">+ 新增職能</button>
            </div>
          </div>`;
    }

    window._staffAddRole = async () => {
        const name = prompt('輸入新職能名稱：');
        if (!name || !name.trim()) return;
        const n = name.trim();
        if (_roles.includes(n)) { alert('已存在'); return; }
        _roles.push(n);
        await _saveRoles();
        _populateRoleSelects();
        _render();
    };
    window._staffRenameRole = async (i) => {
        const old = _roles[i];
        const name = prompt('修改職能名稱：', old);
        if (!name || !name.trim() || name.trim() === old) return;
        _roles[i] = name.trim();
        await _saveRoles();
        _populateRoleSelects();
        _render();
    };
    window._staffRemoveRole = async (i) => {
        if (!confirm(`確定刪除「${_roles[i]}」？已有此職能的人員不受影響。`)) return;
        _roles.splice(i, 1);
        await _saveRoles();
        _populateRoleSelects();
        _render();
    };

    function _closeRolesModal() { overlay.remove(); }
    _render();
    document.body.appendChild(overlay);
};

// ── Resume Tab ──────────────────────────────────────────────

let _resumeLoaded = {};  // staffId → true if already loaded

async function _renderResumeTab(staffId) {
    const container = document.getElementById('staff-detail-resume');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty" style="padding:12px;">載入中...</div>';

    let staff, projects, portfolio;
    try {
        [staff, projects, portfolio] = await Promise.all([
            _fetch('/staff/' + staffId),
            _fetch('/staff/' + staffId + '/projects').then(d => d.projects || []).catch(() => []),
            _fetch('/staff/' + staffId + '/portfolio').catch(() => ({ items: [] }))
        ]);
    } catch (e) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
        return;
    }

    const bio = staff.bio || '';
    const skills = staff.skills || [];
    const education = staff.education || [];
    const experience = staff.experience || [];
    const awards = staff.awards || [];
    const photoUrl = staff.photo_url || '';
    const resumeVisible = !!staff.resume_visible;
    const portfolioItems = portfolio.items || [];

    container.innerHTML = `
        <div class="staff-resume-wrap">
            <!-- Header: Photo + Name -->
            <div class="staff-resume-header">
                <div class="staff-photo-circle" id="staff-resume-photo" title="點擊上傳照片">
                    ${photoUrl
                        ? `<img src="${_esc(photoUrl)}" alt="photo">`
                        : `<span class="staff-photo-placeholder">📷</span>`}
                </div>
                <div class="staff-resume-name-block">
                    <div class="staff-resume-name">${_esc(staff.name)}</div>
                    <div class="staff-resume-role">${_esc(staff.role || '')}</div>
                </div>
            </div>

            <!-- Bio -->
            <div class="staff-section-title">自我介紹</div>
            <textarea id="staff-resume-bio" class="staff-resume-textarea" rows="3"
                placeholder="撰寫自我介紹...">${_esc(bio)}</textarea>
            <button class="crm-btn crm-btn-secondary crm-btn-sm staff-resume-save-bio"
                id="staff-resume-save-bio">儲存介紹</button>

            <!-- Skills -->
            <div class="staff-section-title">專業技能</div>
            <div class="staff-skills-wrap" id="staff-resume-skills">
                ${skills.map((sk, i) => `<span class="staff-skill-tag">${_esc(sk)}<button class="staff-skill-remove" data-idx="${i}">&times;</button></span>`).join('')}
                <div class="staff-skill-add-wrap">
                    <input type="text" class="staff-skill-input" id="staff-resume-skill-input" placeholder="新增技能 (Enter)">
                </div>
            </div>

            <!-- Company projects -->
            <div class="staff-section-title">公司專案作品</div>
            <div class="staff-resume-projects" id="staff-resume-projects">
                ${projects.length === 0
                    ? '<div class="crm-empty" style="padding:8px 0;font-size:12px;">尚無專案紀錄</div>'
                    : projects.map(p => `
                        <div class="staff-resume-project-row">
                            <span class="staff-resume-project-name">${_esc(p.project_name)}</span>
                            <span class="crm-muted">${_esc(p.role_in_project)}</span>
                            ${p.client_name ? `<span class="crm-muted">- ${_esc(p.client_name)}</span>` : ''}
                        </div>`).join('')}
            </div>

            <!-- Portfolio -->
            <div class="staff-section-title">個人作品集</div>
            <div class="staff-portfolio-list" id="staff-resume-portfolio">
                ${portfolioItems.map((item, i) => _portfolioCard(item, i)).join('')}
            </div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="staff-resume-add-portfolio">+ 新增作品</button>

            <!-- Experience -->
            <div class="staff-section-title">工作經歷</div>
            <div id="staff-resume-experience">
                ${experience.map((ex, i) => _expRow(ex, i)).join('')}
            </div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="staff-resume-add-exp">+ 新增</button>

            <!-- Education -->
            <div class="staff-section-title">學歷</div>
            <div id="staff-resume-education">
                ${education.map((ed, i) => _eduRow(ed, i)).join('')}
            </div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="staff-resume-add-edu">+ 新增</button>

            <!-- Awards -->
            <div class="staff-section-title">獲獎紀錄</div>
            <div id="staff-resume-awards">
                ${awards.map((aw, i) => _awardRow(aw, i)).join('')}
            </div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="staff-resume-add-award">+ 新增</button>

            <!-- Footer actions -->
            <div class="staff-resume-actions">
                <button class="crm-btn crm-btn-secondary" onclick="window.open('/resume.html?id=${staffId}','_blank')">
                    預覽簡歷
                </button>
                <button class="crm-btn crm-btn-secondary" onclick="window.open('/api/v1/crm/staff/${staffId}/resume-pdf','_blank')">
                    下載 PDF
                </button>
                <button class="crm-btn crm-btn-secondary" id="staff-resume-share-link">
                    🔗 分享編輯連結
                </button>
                <button class="crm-btn ${staff.resume_editable !== false ? 'crm-btn-primary' : 'crm-btn-secondary'}"
                    id="staff-resume-toggle-editable">
                    ${staff.resume_editable !== false ? '✎ 開放編輯' : '🔒 關閉編輯'}
                </button>
                <button class="crm-btn ${resumeVisible ? 'crm-btn-primary' : 'crm-btn-secondary'}"
                    id="staff-resume-toggle-visible">
                    ${resumeVisible ? '公開中' : '私密'}
                </button>
            </div>
        </div>
    `;

    // -- Wire up events --

    // Photo upload
    document.getElementById('staff-resume-photo').addEventListener('click', () => {
        const input = document.createElement('input');
        input.type = 'file'; input.accept = 'image/*';
        input.onchange = async () => {
            if (!input.files[0]) return;
            const form = new FormData();
            form.append('file', input.files[0]);
            const token = localStorage.getItem('auth_token');
            try {
                await fetch(`/api/v1/crm/staff/${staffId}/photo`, {
                    method: 'POST',
                    headers: token ? { 'Authorization': 'Bearer ' + token } : {},
                    body: form
                });
                _renderResumeTab(staffId);
            } catch (e) { alert('上傳失敗: ' + e.message); }
        };
        input.click();
    });

    // Save bio
    document.getElementById('staff-resume-save-bio').addEventListener('click', async () => {
        const newBio = document.getElementById('staff-resume-bio').value;
        try {
            await _fetch('/staff/' + staffId + '/resume', { method: 'PUT', body: JSON.stringify({ bio: newBio }) });
        } catch (e) { alert('儲存失敗: ' + e.message); }
    });

    // Skills: add on Enter
    document.getElementById('staff-resume-skill-input').addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        const val = e.target.value.trim();
        if (!val) return;
        e.target.value = '';
        const newSkills = [...skills, val];
        try {
            await _fetch('/staff/' + staffId + '/resume', { method: 'PUT', body: JSON.stringify({ skills: newSkills }) });
            _renderResumeTab(staffId);
        } catch (err) { alert('儲存失敗: ' + err.message); }
    });

    // Skills: remove
    container.querySelectorAll('.staff-skill-remove').forEach(btn => {
        btn.addEventListener('click', async () => {
            const idx = parseInt(btn.dataset.idx);
            const newSkills = skills.filter((_, i) => i !== idx);
            try {
                await _fetch('/staff/' + staffId + '/resume', { method: 'PUT', body: JSON.stringify({ skills: newSkills }) });
                _renderResumeTab(staffId);
            } catch (err) { alert('儲存失敗: ' + err.message); }
        });
    });

    // Portfolio: add
    document.getElementById('staff-resume-add-portfolio').addEventListener('click', () => {
        _openPortfolioForm(staffId, null, portfolioItems);
    });

    // Portfolio: edit / delete
    container.querySelectorAll('.staff-portfolio-edit').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.idx);
            _openPortfolioForm(staffId, portfolioItems[idx], portfolioItems, btn.dataset.id);
        });
    });
    container.querySelectorAll('.staff-portfolio-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('確定刪除此作品？')) return;
            const itemId = btn.dataset.id;
            try {
                await _fetch('/staff-portfolio/' + itemId, { method: 'DELETE' });
                _renderResumeTab(staffId);
            } catch (e) { alert('刪除失敗: ' + e.message); }
        });
    });

    // Experience: add / edit / delete
    _wireListSection(container, 'exp', experience, staffId, 'experience',
        [{ key: 'company', label: '公司' }, { key: 'title', label: '職稱' }, { key: 'period', label: '期間' }]);

    // Education: add / edit / delete
    _wireListSection(container, 'edu', education, staffId, 'education',
        [{ key: 'school', label: '學校' }, { key: 'degree', label: '學位' }, { key: 'year', label: '年份' }]);

    // Awards: add / edit / delete
    _wireListSection(container, 'award', awards, staffId, 'awards',
        [{ key: 'name', label: '獎項' }, { key: 'year', label: '年份' }]);

    // Toggle visibility
    document.getElementById('staff-resume-toggle-visible').addEventListener('click', async () => {
        try {
            await _fetch('/staff/' + staffId + '/resume', {
                method: 'PUT', body: JSON.stringify({ resume_visible: !resumeVisible })
            });
            _renderResumeTab(staffId);
        } catch (e) { alert('儲存失敗: ' + e.message); }
    });

    // Toggle editable
    document.getElementById('staff-resume-toggle-editable').addEventListener('click', async () => {
        try {
            await _fetch('/staff/' + staffId + '/resume', {
                method: 'PUT', body: JSON.stringify({ resume_editable: !(staff.resume_editable !== false) })
            });
            _renderResumeTab(staffId);
        } catch (e) { alert('儲存失敗: ' + e.message); }
    });

    // Generate share link
    document.getElementById('staff-resume-share-link').addEventListener('click', async () => {
        try {
            const r = await _fetch('/staff/' + staffId + '/generate-edit-token', { method: 'POST', body: '{}' });
            const fullUrl = location.origin + r.url;
            await navigator.clipboard.writeText(fullUrl).catch(() => {});
            alert('已複製分享連結：\n' + fullUrl);
        } catch (e) { alert('產生連結失敗: ' + e.message); }
    });

    _resumeLoaded[staffId] = true;
}

function _portfolioCard(item, idx) {
    return `<div class="staff-portfolio-card" data-item-id="${item.id || ''}">
        ${item.thumbnail_url
            ? `<img src="${_esc(item.thumbnail_url)}" class="staff-portfolio-thumb" alt="">`
            : `<div class="staff-portfolio-thumb staff-portfolio-thumb-empty"></div>`}
        <div class="staff-portfolio-info">
            <div class="staff-portfolio-title">${_esc(item.title || '未命名')}</div>
            ${item.role_desc ? `<div class="crm-muted" style="font-size:11px;">${_esc(item.role_desc)}</div>` : ''}
            ${item.url ? `<a href="${_esc(item.url)}" target="_blank" style="color:#3b82f6;font-size:11px;">連結</a>` : ''}
        </div>
        <div class="staff-portfolio-actions">
            <button class="crm-btn crm-btn-secondary crm-btn-sm staff-portfolio-edit" data-id="${item.id || ''}" data-idx="${idx}">✎</button>
            <button class="crm-btn crm-btn-danger crm-btn-sm staff-portfolio-delete" data-id="${item.id || ''}">&times;</button>
        </div>
    </div>`;
}

function _expRow(ex, i) {
    return `<div class="staff-resume-list-row" data-section="exp" data-idx="${i}">
        <span class="staff-resume-list-main">${_esc(ex.company || '')}</span>
        <span class="crm-muted">${_esc(ex.title || '')}</span>
        <span class="crm-muted">${_esc(ex.period || '')}</span>
        <button class="crm-btn crm-btn-secondary crm-btn-sm staff-list-edit" data-section="exp" data-idx="${i}">✎</button>
        <button class="crm-btn crm-btn-danger crm-btn-sm staff-list-delete" data-section="exp" data-idx="${i}">&times;</button>
    </div>`;
}

function _eduRow(ed, i) {
    return `<div class="staff-resume-list-row" data-section="edu" data-idx="${i}">
        <span class="staff-resume-list-main">${_esc(ed.school || '')}</span>
        <span class="crm-muted">${_esc(ed.degree || '')}</span>
        <span class="crm-muted">${_esc(ed.year || '')}</span>
        <button class="crm-btn crm-btn-secondary crm-btn-sm staff-list-edit" data-section="edu" data-idx="${i}">✎</button>
        <button class="crm-btn crm-btn-danger crm-btn-sm staff-list-delete" data-section="edu" data-idx="${i}">&times;</button>
    </div>`;
}

function _awardRow(aw, i) {
    return `<div class="staff-resume-list-row" data-section="award" data-idx="${i}">
        <span class="staff-resume-list-main">${_esc(aw.name || '')}</span>
        <span class="crm-muted">${_esc(aw.year || '')}</span>
        <button class="crm-btn crm-btn-secondary crm-btn-sm staff-list-edit" data-section="award" data-idx="${i}">✎</button>
        <button class="crm-btn crm-btn-danger crm-btn-sm staff-list-delete" data-section="award" data-idx="${i}">&times;</button>
    </div>`;
}

function _wireListSection(container, sectionName, items, staffId, fieldKey, fields) {
    // Add button
    const addBtn = document.getElementById(`staff-resume-add-${sectionName}`);
    if (addBtn) {
        addBtn.addEventListener('click', () => {
            _openListItemForm(sectionName, fields, null, async (newItem) => {
                const updated = [...items, newItem];
                await _fetch('/staff/' + staffId + '/resume', { method: 'PUT', body: JSON.stringify({ [fieldKey]: updated }) });
                _renderResumeTab(staffId);
            });
        });
    }

    // Edit buttons
    container.querySelectorAll(`.staff-list-edit[data-section="${sectionName}"]`).forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.idx);
            _openListItemForm(sectionName, fields, items[idx], async (edited) => {
                const updated = [...items];
                updated[idx] = edited;
                await _fetch('/staff/' + staffId + '/resume', { method: 'PUT', body: JSON.stringify({ [fieldKey]: updated }) });
                _renderResumeTab(staffId);
            });
        });
    });

    // Delete buttons
    container.querySelectorAll(`.staff-list-delete[data-section="${sectionName}"]`).forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('確定刪除？')) return;
            const idx = parseInt(btn.dataset.idx);
            const updated = items.filter((_, i) => i !== idx);
            try {
                await _fetch('/staff/' + staffId + '/resume', { method: 'PUT', body: JSON.stringify({ [fieldKey]: updated }) });
                _renderResumeTab(staffId);
            } catch (e) { alert('刪除失敗: ' + e.message); }
        });
    });
}

function _openListItemForm(sectionName, fields, existing, onSave) {
    // Create inline form overlay
    let overlay = document.getElementById('staff-resume-form-overlay');
    if (overlay) overlay.remove();

    overlay = document.createElement('div');
    overlay.id = 'staff-resume-form-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    const fieldsHtml = fields.map(f =>
        `<div class="crm-field" style="margin-bottom:8px;">
            <label style="font-size:12px;color:#9ca3af;">${f.label}</label>
            <input type="text" class="crm-input" data-key="${f.key}" value="${_esc((existing && existing[f.key]) || '')}">
        </div>`
    ).join('');

    overlay.innerHTML = `
        <div class="crm-modal" style="max-width:360px;">
            <div class="crm-modal-header">
                <h3>${existing ? '編輯' : '新增'}</h3>
                <button class="crm-detail-close" id="staff-form-close">&times;</button>
            </div>
            <div class="crm-modal-body">${fieldsHtml}</div>
            <div class="crm-modal-footer">
                <button class="crm-btn crm-btn-secondary" id="staff-form-cancel">取消</button>
                <button class="crm-btn crm-btn-primary" id="staff-form-save">儲存</button>
            </div>
        </div>`;

    document.body.appendChild(overlay);

    overlay.querySelector('#staff-form-close').addEventListener('click', () => overlay.remove());
    overlay.querySelector('#staff-form-cancel').addEventListener('click', () => overlay.remove());
    overlay.querySelector('#staff-form-save').addEventListener('click', async () => {
        const result = {};
        overlay.querySelectorAll('[data-key]').forEach(inp => {
            result[inp.dataset.key] = inp.value.trim();
        });
        try {
            await onSave(result);
            overlay.remove();
        } catch (e) { alert('儲存失敗: ' + e.message); }
    });

    // Focus first input
    const firstInput = overlay.querySelector('input');
    if (firstInput) firstInput.focus();
}

function _openPortfolioForm(staffId, existing, items, editItemId) {
    let overlay = document.getElementById('staff-portfolio-form-overlay');
    if (overlay) overlay.remove();

    overlay = document.createElement('div');
    overlay.id = 'staff-portfolio-form-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    overlay.innerHTML = `
        <div class="crm-modal" style="max-width:420px;">
            <div class="crm-modal-header">
                <h3>${existing ? '編輯作品' : '新增作品'}</h3>
                <button class="crm-detail-close" id="staff-pf-close">&times;</button>
            </div>
            <div class="crm-modal-body">
                <div class="crm-field" style="margin-bottom:8px;">
                    <label style="font-size:12px;color:#9ca3af;">作品名稱</label>
                    <input type="text" class="crm-input" id="staff-pf-title" value="${_esc((existing && existing.title) || '')}">
                </div>
                <div class="crm-field" style="margin-bottom:8px;">
                    <label style="font-size:12px;color:#9ca3af;">角色</label>
                    <input type="text" class="crm-input" id="staff-pf-role" value="${_esc((existing && existing.role) || '')}" placeholder="例：攝影師">
                </div>
                <div class="crm-field" style="margin-bottom:8px;">
                    <label style="font-size:12px;color:#9ca3af;">連結 URL</label>
                    <input type="url" class="crm-input" id="staff-pf-url" value="${_esc((existing && existing.url) || '')}" placeholder="https://...">
                </div>
                <div class="crm-field" style="margin-bottom:8px;">
                    <label style="font-size:12px;color:#9ca3af;">縮圖</label>
                    <input type="file" accept="image/*" id="staff-pf-thumb" class="crm-input" style="padding:4px;">
                    ${existing && existing.thumbnail_url ? `<img src="${_esc(existing.thumbnail_url)}" style="width:60px;height:40px;object-fit:cover;border-radius:4px;margin-top:4px;">` : ''}
                </div>
                <div class="crm-field">
                    <label style="font-size:12px;color:#9ca3af;">說明</label>
                    <textarea class="crm-input crm-textarea" id="staff-pf-desc" rows="2">${_esc((existing && existing.description) || '')}</textarea>
                </div>
            </div>
            <div class="crm-modal-footer">
                <button class="crm-btn crm-btn-secondary" id="staff-pf-cancel">取消</button>
                <button class="crm-btn crm-btn-primary" id="staff-pf-save">儲存</button>
            </div>
        </div>`;

    document.body.appendChild(overlay);

    overlay.querySelector('#staff-pf-close').addEventListener('click', () => overlay.remove());
    overlay.querySelector('#staff-pf-cancel').addEventListener('click', () => overlay.remove());
    overlay.querySelector('#staff-pf-save').addEventListener('click', async () => {
        const title = document.getElementById('staff-pf-title').value.trim();
        const url = document.getElementById('staff-pf-url').value.trim();
        const role_desc = document.getElementById('staff-pf-role').value.trim();
        if (!title) { alert('請輸入作品標題'); return; }

        const params = new URLSearchParams({ title, url, role_desc, sort_order: 0 });
        const form = new FormData();
        const thumbFile = document.getElementById('staff-pf-thumb').files[0];
        if (thumbFile) form.append('thumbnail', thumbFile);

        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        try {
            if (editItemId) {
                await fetch(`/api/v1/crm/staff-portfolio/${editItemId}?${params}`, {
                    method: 'PUT', headers, body: thumbFile ? form : undefined
                });
            } else {
                await fetch(`/api/v1/crm/staff/${staffId}/portfolio?${params}`, {
                    method: 'POST', headers, body: thumbFile ? form : undefined
                });
            }
            overlay.remove();
            _renderResumeTab(staffId);
        } catch (e) { alert('儲存失敗: ' + e.message); }
    });

    document.getElementById('staff-pf-title').focus();
}

// ── Init ─────────────────────────────────────────────────────

export async function initCrmStaffTab() {
    for (const id of ['staff-modal', 'staff-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._staffSelect = selectStaff;
    window._staffEdit = (id) => { const s = _staff.find(x => x.id === id); if (s) openModal(s); };
    window._staffDelete = (id) => { const s = _staff.find(x => x.id === id); if (s) deleteStaff(s); };
    window._staffDup = (id) => {
        const s = _staff.find(x => x.id === id);
        if (s) { openModal(s); _editingId = null; document.getElementById('staff-modal-title').textContent = '複製人員'; }
    };

    let _t;
    document.getElementById('staff-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadStaff, 300);
    });
    document.getElementById('staff-filter-role').addEventListener('change', e => { _filters.role = e.target.value; loadStaff(); });
    document.getElementById('staff-filter-status').addEventListener('change', e => { _filters.status = e.target.value; loadStaff(); });

    document.getElementById('staff-btn-add').addEventListener('click', () => openModal());
    document.getElementById('staff-btn-import').addEventListener('click', openImportModal);
    document.getElementById('staff-btn-save').addEventListener('click', saveStaff);
    document.getElementById('staff-detail-close').addEventListener('click', closeDetail);
    document.getElementById('staff-btn-do-import').addEventListener('click', doImport);

    document.getElementById('staff-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('staff-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.csv')) _setCsvFile(file);
    });

    document.querySelectorAll('#staff-detail-tabs .crm-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#staff-detail-tabs .crm-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('staff-detail-info').classList.toggle('hidden', tab !== 'info');
            document.getElementById('staff-detail-projects').classList.toggle('hidden', tab !== 'projects');
            document.getElementById('staff-detail-resume').classList.toggle('hidden', tab !== 'resume');
            if (tab === 'resume' && _selectedId && !_resumeLoaded[_selectedId]) {
                _renderResumeTab(_selectedId);
            }
        });
    });

    for (const id of ['staff-modal', 'staff-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('staff-resize-handle', 'staff-detail-panel');
    await _loadRoles();
    loadStaff();
}
