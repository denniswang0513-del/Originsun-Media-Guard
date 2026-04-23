/**
 * crm-projects-cost-groups.js — 成本子表切換器 + 3 Modal
 *
 * 一個專案下可有多張成本子表（拍攝日 / 子專案），每張 = 完整財務表單
 * （預算 + 成本估算 + 雜支）。此模組負責：
 *   1. 載入子表列表（含每組 summary）
 *   2. 渲染切換卡片列 + + 新增按鈕
 *   3. 新增 / 編輯 / 刪除 / 複製 4 個 Modal
 *   4. 切換子表時通知 cost.js 重載範圍
 */
import { crmFetch as _fetch, esc as _esc, fmtNum } from './crm-utils.js';
import { state, callbacks } from './crm-projects-state.js';
import { barColor, remainColor } from './crm-projects-calc.js';

// ── Data ──────────────────────────────────────────────────────

export async function loadCostGroups(projectId) {
    try {
        const data = await _fetch('/projects/' + projectId + '/cost-groups');
        state.costGroups = data.cost_groups || [];
    } catch (_) {
        state.costGroups = [];
    }
    // selectedGroupId 維持既有選擇；不存在則切到第一張。渲染交給 caller
    // （避免在 host DOM 還沒建好時 render 一次、之後又 render 第二次）
    const stillValid = state.selectedGroupId
        && state.costGroups.some(g => g.id === state.selectedGroupId);
    if (!stillValid) {
        state.selectedGroupId = state.costGroups[0]?.id || null;
    }
    return state.costGroups;
}

// ── Render: 切換卡 ─────────────────────────────────────────────

export function renderGroupSwitcher() {
    const host = document.getElementById('cost-groups-switcher');
    if (!host) return;
    const groups = state.costGroups;
    const canDelete = groups.length > 1;

    const chips = groups.map(g => _renderChip(g, g.id === state.selectedGroupId, canDelete)).join('');
    host.innerHTML = `
        <div class="cg-switcher-title">成本子表</div>
        <div class="cg-switcher">
            ${chips}
            <button class="cg-chip cg-chip-add" onclick="window._cgAdd()">
                <span class="cg-chip-add-icon">+</span>
                <span>新增子表</span>
            </button>
        </div>
    `;
}

function _renderChip(g, active, canDelete) {
    const s = g.summary || {};
    const total = (g.budget_amount || 0) + (g.misc_budget_amount || 0);
    const used = s.total_actual || 0;
    const pct = _usagePct(used, total);
    const date = g.shoot_date ? `<div class="cg-chip-date">${_esc(g.shoot_date)}</div>` : '<div class="cg-chip-date">&nbsp;</div>';
    const actions = canDelete || active
        ? `<button class="cg-chip-menu" onclick="event.stopPropagation();window._cgMenu('${g.id}',event)" title="選項">⋯</button>`
        : '';
    return `
        <div class="cg-chip ${active ? 'active' : ''}" onclick="window._cgSelect('${g.id}')">
            ${actions}
            <div class="cg-chip-name">${_esc(g.name)}</div>
            ${date}
            <div class="cg-chip-budget">預算 ${total > 0 ? '$' + fmtNum(total) : '<span class="cg-muted">未設</span>'}</div>
            <div class="cg-chip-actual">結算 ${used > 0 ? '$' + fmtNum(used) : '<span class="cg-muted">—</span>'}</div>
            <div class="cg-chip-bar"><div class="cg-chip-bar-fill" style="width:${Math.min(pct ?? 0, 100)}%;background:${_pctColor(pct)};"></div></div>
            <div class="cg-chip-status">${_statusBadge(total, used, pct)}</div>
        </div>
    `;
}

function _usagePct(actual, budget) {
    return budget > 0 ? Math.round(actual / budget * 100) : null;
}

const _pctColor = (pct) => pct == null ? '#4b5563' : barColor(pct);

function _statusBadge(total, used, pct) {
    if (total === 0) return '<span class="cg-badge cg-badge-hint">💡 未設預算</span>';
    if (used === 0) return '<span class="cg-badge cg-badge-idle">未開始</span>';
    if (pct > 100) return `<span class="cg-badge cg-badge-danger">↑ 超支 $${fmtNum(used - total)}</span>`;
    if (pct > 80) return '<span class="cg-badge cg-badge-warn">⚠ 接近上限</span>';
    return '<span class="cg-badge cg-badge-ok">✓ 預算內</span>';
}

// ── Render: Dashboard ─────────────────────────────────────────

function _renderHeader(g, over, hasBudget) {
    const meta = [g.shoot_date, g.notes].filter(Boolean).map(_esc).join(' · ');
    const editLabel = hasBudget ? '編輯' : '設定預算';
    return `
        <div class="cg-dash-header ${over ? 'cg-dash-header-over' : ''}">
            <div class="cg-dash-title">
                <strong>${_esc(g.name)}</strong>${meta ? ' · ' + meta : ''}
            </div>
            <button class="crm-btn ${hasBudget ? 'crm-btn-secondary' : 'crm-btn-primary'} crm-btn-sm"
                    onclick="window._cgEdit('${g.id}')">✎ ${editLabel}</button>
        </div>
    `;
}

export function renderGroupDashboard() {
    const host = document.getElementById('cost-group-dashboard');
    if (!host) return;
    const g = state.costGroups.find(x => x.id === state.selectedGroupId);
    if (!g) { host.innerHTML = ''; return; }

    const s = g.summary || {};
    const totalBudget = (g.budget_amount || 0) + (g.misc_budget_amount || 0);
    const totalActual = s.total_actual || 0;
    const remain = totalBudget - totalActual;
    const pct = _usagePct(totalActual, totalBudget);
    const over = totalBudget > 0 && remain < 0;

    if (totalBudget === 0) {
        host.innerHTML = `
            ${_renderHeader(g, false, false)}
            <div class="cg-dash-empty">💡 此子表尚未設定預算 · 已用 $${fmtNum(totalActual)}</div>
        `;
        return;
    }

    const remainText = over
        ? `<span class="cg-dash-over">超支 $${fmtNum(-remain)} ⚠</span>`
        : `<span class="cg-dash-ok">剩餘 $${fmtNum(remain)} ✓</span>`;

    host.innerHTML = `
        ${_renderHeader(g, over, true)}
        <div class="cg-dash-strip ${over ? 'cg-dash-strip-over' : ''}">
            預算 $${fmtNum(totalBudget)}
            <span class="cg-dash-sep">·</span>
            已用 $${fmtNum(totalActual)}
            <span class="cg-dash-sep">·</span>
            ${remainText}
        </div>
        <div class="cg-dashboard-bar">
            <div class="cg-dashboard-bar-fill" style="width:${Math.min(pct ?? 0, 100)}%;background:${_pctColor(pct)};"></div>
        </div>
        <div class="cg-dashboard-bar-label">本子表預算使用 ${pct ?? 0}%${over ? ' ↑' : ''}</div>
    `;
}

// ── Select ─────────────────────────────────────────────────────

export async function selectGroup(gid) {
    if (gid === state.selectedGroupId) return;
    const dirty = Object.keys(window._costDirtyMap || {}).length > 0;
    if (dirty && typeof window._costCheckUnsaved === 'function') {
        window._costCheckUnsaved(function() {
            window._costDirtyMap = {};
            _doSelect(gid);
        });
        return;
    }
    _doSelect(gid);
}

function _doSelect(gid) {
    state.selectedGroupId = gid;
    renderGroupSwitcher();
    callbacks.loadFinancialSummary?.(state.selectedId);
}

// ── Add / Edit Modal ───────────────────────────────────────────

async function _openEditModal(gid = null) {
    const isEdit = !!gid;
    const g = isEdit ? state.costGroups.find(x => x.id === gid) : null;
    const title = isEdit ? '編輯子表' : '新增子表';

    const projectId = state.selectedId;
    if (!projectId) return;
    const allocatedOther = state.costGroups
        .filter(x => !isEdit || x.id !== gid)
        .reduce((sum, x) => sum + (x.budget_amount || 0) + (x.misc_budget_amount || 0), 0);

    // 執行預算在 modal 開啟期間不會變，只抓一次
    let execBudget = 0;
    try {
        const fs = await _fetch('/projects/' + projectId + '/financial-summary');
        execBudget = (fs.ex_tax || 0) - (fs.profit_target || 0);
    } catch (_) {}

    _closeOverlay('cg-edit-overlay');
    const overlay = document.createElement('div');
    overlay.id = 'cg-edit-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
        <div class="crm-modal" style="max-width:480px;">
          <div class="crm-modal-header">
            <h3>${title}</h3>
            <button onclick="document.getElementById('cg-edit-overlay').remove()" class="crm-detail-close">✕</button>
          </div>
          <div class="crm-modal-body">
            <div class="crm-form-section">基本資訊</div>
            <div class="crm-form-grid">
              <div class="crm-field crm-field-full">
                <label>名稱 <span class="crm-required">*</span></label>
                <input id="cg-f-name" type="text" class="crm-input" placeholder="例：主表 / 5-15 外景 / 棚拍" value="${_esc(g?.name || '')}">
              </div>
              <div class="crm-field">
                <label>拍攝日（選填）</label>
                <input id="cg-f-shoot_date" type="date" class="crm-input" value="${g?.shoot_date || ''}">
              </div>
              <div class="crm-field">
                <label>排序</label>
                <input id="cg-f-sort_order" type="number" class="crm-input" value="${g?.sort_order ?? 0}" min="0">
              </div>
              <div class="crm-field crm-field-full">
                <label>備註（選填）</label>
                <textarea id="cg-f-notes" class="crm-input crm-textarea" rows="2" placeholder="例：台北信義區外景">${_esc(g?.notes || '')}</textarea>
              </div>
            </div>

            <div class="crm-form-section">預算設定（可留空，之後再設）</div>
            <div class="crm-form-grid">
              <div class="crm-field">
                <label>成本預算（未稅）</label>
                <input id="cg-f-budget_amount" type="number" class="crm-input" placeholder="0" value="${g?.budget_amount ?? ''}" min="0">
              </div>
              <div class="crm-field">
                <label>雜支預算</label>
                <input id="cg-f-misc_budget_amount" type="number" class="crm-input" placeholder="0" value="${g?.misc_budget_amount ?? ''}" min="0">
              </div>
              <div class="crm-field">
                <label>自訂目標毛利率（%）</label>
                <input id="cg-f-profit_target_pct" type="number" class="crm-input" placeholder="沿用專案預設" value="${g?.profit_target_pct ?? ''}" min="0" max="100">
              </div>
            </div>

            <div class="cg-alloc-hint" id="cg-alloc-hint"></div>
            <div id="cg-modal-error" class="crm-error" style="display:none;"></div>
          </div>
          <div class="crm-modal-footer">
            <button onclick="document.getElementById('cg-edit-overlay').remove()" class="crm-btn crm-btn-secondary">取消</button>
            <button id="cg-modal-save" class="crm-btn crm-btn-primary">${isEdit ? '儲存' : '建立'}</button>
          </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const renderHint = () => _renderAllocHint(execBudget, allocatedOther);
    renderHint();
    ['cg-f-budget_amount', 'cg-f-misc_budget_amount'].forEach(id => {
        document.getElementById(id).addEventListener('input', renderHint);
    });

    document.getElementById('cg-f-name').focus();
    document.getElementById('cg-modal-save').addEventListener('click', () => _saveEditModal(projectId, gid));
}

function _renderAllocHint(execBudget, allocatedOther) {
    const el = document.getElementById('cg-alloc-hint');
    if (!el) return;
    const b = parseInt(document.getElementById('cg-f-budget_amount').value) || 0;
    const mb = parseInt(document.getElementById('cg-f-misc_budget_amount').value) || 0;
    const totalAllocated = allocatedOther + b + mb;
    const remain = execBudget - totalAllocated;
    const over = execBudget > 0 && totalAllocated > execBudget;
    el.innerHTML = `
        <div class="${over ? 'cg-alloc-danger' : 'cg-alloc-ok'}">
            ${over ? '⚠' : '💡'} 合約執行預算：$${fmtNum(execBudget)}<br>
            已分配（含本筆）：$${fmtNum(totalAllocated)}<br>
            ${remain >= 0 ? `尚可分配：$${fmtNum(remain)}` : `已超出：$${fmtNum(-remain)}`}
        </div>
    `;
}

async function _saveEditModal(projectId, gid) {
    const name = document.getElementById('cg-f-name').value.trim();
    if (!name) {
        _showError('名稱為必填');
        return;
    }
    const payload = {
        name,
        shoot_date: document.getElementById('cg-f-shoot_date').value || null,
        sort_order: parseInt(document.getElementById('cg-f-sort_order').value) || 0,
        notes: document.getElementById('cg-f-notes').value || null,
        budget_amount: _intOrNull('cg-f-budget_amount'),
        misc_budget_amount: _intOrNull('cg-f-misc_budget_amount'),
        profit_target_pct: _intOrNull('cg-f-profit_target_pct'),
    };
    const btn = document.getElementById('cg-modal-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        let newGid = gid;
        if (gid) {
            await _fetch('/cost-groups/' + gid, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            const r = await _fetch('/projects/' + projectId + '/cost-groups', {
                method: 'POST', body: JSON.stringify(payload)
            });
            newGid = r.cost_group?.id;
        }
        _closeOverlay('cg-edit-overlay');
        await loadCostGroups(projectId);
        if (!gid && newGid) {
            state.selectedGroupId = newGid;
        }
        callbacks.loadFinancialSummary?.(projectId);
    } catch (e) {
        _showError('儲存失敗：' + e.message);
        btn.disabled = false; btn.textContent = gid ? '儲存' : '建立';
    }
}

function _intOrNull(id) {
    const v = document.getElementById(id).value;
    return v === '' ? null : (parseInt(v) || 0);
}

function _showError(msg) {
    const el = document.getElementById('cg-modal-error');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
}

function _closeOverlay(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// ── Delete Modal ───────────────────────────────────────────────

async function _openDeleteModal(gid) {
    const g = state.costGroups.find(x => x.id === gid);
    if (!g) return;
    const isLast = state.costGroups.length <= 1;
    if (isLast) {
        alert('至少需保留一張子表，無法刪除');
        return;
    }
    const s = g.summary || {};
    _closeOverlay('cg-del-overlay');
    const overlay = document.createElement('div');
    overlay.id = 'cg-del-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
        <div class="crm-modal" style="max-width:420px;">
          <div class="crm-modal-header">
            <h3>⚠ 刪除子表</h3>
            <button onclick="document.getElementById('cg-del-overlay').remove()" class="crm-detail-close">✕</button>
          </div>
          <div class="crm-modal-body">
            <p style="font-size:14px;margin-bottom:12px;">確定刪除「<strong>${_esc(g.name)}</strong>」？</p>
            <div style="background:#1a1a1a;border-radius:6px;padding:10px;font-size:12px;color:#9ca3af;">
              此子表包含：
              <ul style="margin:6px 0 0;padding-left:20px;">
                <li>${s.cost_lines_count || 0} 筆成本項目</li>
                <li>${s.expenses_count || 0} 筆雜支</li>
                <li>結算金額 $${fmtNum(s.total_actual || 0)}</li>
              </ul>
            </div>
            <p style="font-size:12px;color:#fca5a5;margin-top:10px;">刪除後成本項目與雜支將一併消失，無法復原。</p>
          </div>
          <div class="crm-modal-footer">
            <button onclick="document.getElementById('cg-del-overlay').remove()" class="crm-btn crm-btn-secondary">取消</button>
            <button id="cg-del-confirm" class="crm-btn crm-btn-danger">確認刪除</button>
          </div>
        </div>
    `;
    document.body.appendChild(overlay);
    document.getElementById('cg-del-confirm').addEventListener('click', async () => {
        const btn = document.getElementById('cg-del-confirm');
        btn.disabled = true; btn.textContent = '刪除中...';
        try {
            await _fetch('/cost-groups/' + gid, { method: 'DELETE' });
            _closeOverlay('cg-del-overlay');
            if (state.selectedGroupId === gid) state.selectedGroupId = null;
            await loadCostGroups(state.selectedId);
            callbacks.loadFinancialSummary?.(state.selectedId);
        } catch (e) {
            alert('刪除失敗：' + e.message);
            btn.disabled = false; btn.textContent = '確認刪除';
        }
    });
}

// ── Duplicate Modal ────────────────────────────────────────────

function _openDuplicateModal(gid) {
    const g = state.costGroups.find(x => x.id === gid);
    if (!g) return;
    _closeOverlay('cg-dup-overlay');
    const overlay = document.createElement('div');
    overlay.id = 'cg-dup-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    const defaultName = g.name + ' (副本)';
    overlay.innerHTML = `
        <div class="crm-modal" style="max-width:400px;">
          <div class="crm-modal-header">
            <h3>複製子表</h3>
            <button onclick="document.getElementById('cg-dup-overlay').remove()" class="crm-detail-close">✕</button>
          </div>
          <div class="crm-modal-body">
            <div style="font-size:13px;margin-bottom:10px;">來源：<strong>${_esc(g.name)}</strong></div>
            <div class="crm-field">
              <label>新子表名稱 <span class="crm-required">*</span></label>
              <input id="cg-dup-name" type="text" class="crm-input" value="${_esc(defaultName)}">
            </div>
            <div class="crm-field">
              <label>拍攝日（選填）</label>
              <input id="cg-dup-date" type="date" class="crm-input">
            </div>
            <p style="font-size:12px;color:#9ca3af;margin-top:10px;">
              將一併複製 ${g.summary?.cost_lines_count || 0} 筆成本項目（結算值清空）；雜支不複製。
            </p>
          </div>
          <div class="crm-modal-footer">
            <button onclick="document.getElementById('cg-dup-overlay').remove()" class="crm-btn crm-btn-secondary">取消</button>
            <button id="cg-dup-confirm" class="crm-btn crm-btn-primary">複製</button>
          </div>
        </div>
    `;
    document.body.appendChild(overlay);
    document.getElementById('cg-dup-name').focus();
    document.getElementById('cg-dup-confirm').addEventListener('click', async () => {
        const nm = document.getElementById('cg-dup-name').value.trim();
        if (!nm) { alert('請輸入名稱'); return; }
        const sd = document.getElementById('cg-dup-date').value || null;
        const btn = document.getElementById('cg-dup-confirm');
        btn.disabled = true; btn.textContent = '複製中...';
        try {
            const r = await _fetch('/cost-groups/' + gid + '/duplicate', {
                method: 'POST', body: JSON.stringify({ name: nm, shoot_date: sd })
            });
            _closeOverlay('cg-dup-overlay');
            const newGid = r.cost_group?.id;
            await loadCostGroups(state.selectedId);
            if (newGid) state.selectedGroupId = newGid;
            callbacks.loadFinancialSummary?.(state.selectedId);
        } catch (e) {
            alert('複製失敗：' + e.message);
            btn.disabled = false; btn.textContent = '複製';
        }
    });
}

// ── Menu (⋯) — edit / duplicate / delete ──────────────────────

let _menuCloseHandler = null;

function _closeMenu() {
    _closeOverlay('cg-menu-pop');
    if (_menuCloseHandler) {
        document.removeEventListener('click', _menuCloseHandler);
        _menuCloseHandler = null;
    }
}

function _openMenu(gid, ev) {
    _closeMenu();
    const pop = document.createElement('div');
    pop.id = 'cg-menu-pop';
    pop.className = 'cg-menu-pop';
    pop.innerHTML = `
        <button class="cg-menu-item" onclick="window._cgEdit('${gid}')">✎ 編輯</button>
        <button class="cg-menu-item" onclick="window._cgDuplicate('${gid}')">📋 複製</button>
        <button class="cg-menu-item cg-menu-danger" onclick="window._cgDelete('${gid}')">🗑 刪除</button>
    `;
    const rect = ev.target.getBoundingClientRect();
    pop.style.position = 'fixed';
    pop.style.top = (rect.bottom + 4) + 'px';
    pop.style.left = Math.max(rect.left - 80, 8) + 'px';
    pop.style.zIndex = '1000';
    document.body.appendChild(pop);
    setTimeout(() => {
        _menuCloseHandler = (e) => { if (!pop.contains(e.target)) _closeMenu(); };
        document.addEventListener('click', _menuCloseHandler);
    }, 0);
}

// ── Init ───────────────────────────────────────────────────────

export function initCostGroupsHandlers() {
    window._cgSelect = (gid) => selectGroup(gid);
    window._cgAdd = () => _openEditModal(null);
    window._cgEdit = (gid) => { _closeMenu(); _openEditModal(gid); };
    window._cgDelete = (gid) => { _closeMenu(); _openDeleteModal(gid); };
    window._cgDuplicate = (gid) => { _closeMenu(); _openDuplicateModal(gid); };
    window._cgMenu = (gid, ev) => _openMenu(gid, ev);
}
