/**
 * crm-projects-cost.js — 成本估算 / 實際結算
 * 財務摘要載入、成本表格渲染、inline 編輯、計算、存取消、新增/刪除、範本
 */

import { state, callbacks, EXPENSE_CATEGORIES } from './crm-projects-state.js';
import { calcDashboard, remainColor, profitColor, barColor, diffLabel } from './crm-projects-calc.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, searchableSelect } from './crm-utils.js';

// INT field names from _buildEditFields — inlined to avoid circular dep with detail module
const _INT_FIELD_NAMES = [
    'contract_amount', 'tax_rate', 'profit_target_pct', 'misc_budget_pct',
    'amount_receivable', 'amount_received', 'transfer_fee',
];

// ── Dirty map ──────────────────────────────────────────────────
window._costDirtyMap = {};

// ── Financial Summary ──────────────────────────────────────────
async function _loadFinancialSummary(projectId) {
    const container = document.getElementById('proj-detail-finance');
    if (!container) return;
    container.innerHTML = '<div class="crm-empty" style="padding:8px;">載入中...</div>';
    try {
        // 先載子表列表 + 決定當前選中（loadCostGroups 會保證 selectedGroupId 設定）
        await callbacks.loadCostGroups?.(projectId);
        const gid = state.selectedGroupId;
        const scopeQ = gid ? ('?group_id=' + encodeURIComponent(gid)) : '';

        const [f, costData, expData] = await Promise.all([
            _fetch('/projects/' + projectId + '/financial-summary'),
            _fetch('/projects/' + projectId + '/cost-lines' + scopeQ),
            _fetch('/projects/' + projectId + '/expenses' + scopeQ),
        ]);
        const d = calcDashboard(f);
        const rc = remainColor(d.remaining);
        const pc = profitColor(d.profitPct);
        const bc = barColor(d.usagePct);
        const alert = _renderAllocationAlert(f, d);

        container.innerHTML = `
            <div class="cost-dashboard" data-ex-tax="${f.ex_tax}" data-profit-target="${f.profit_target}">
              <div class="cost-card">
                <div class="cost-card-label">執行預算</div>
                <div class="cost-card-value" style="color:#60a5fa;">$${fmtNum(d.execBudget)}</div>
                <div class="cost-card-sub">&nbsp;</div>
              </div>
              <div class="cost-card">
                <div class="cost-card-label">剩餘預算</div>
                <div class="cost-card-value" style="color:${rc};">$${fmtNum(d.remaining)}</div>
                <div class="cost-card-sub">預算 - 預估成本 - 預估雜支</div>
              </div>
              <div class="cost-card">
                <div class="cost-card-label">專案結算</div>
                <div class="cost-card-value" style="color:#fb923c;">$${fmtNum(d.totalActual)}</div>
                <div class="cost-card-sub">成本 + 雜支</div>
              </div>
              <div class="cost-card">
                <div class="cost-card-label">毛利</div>
                <div class="cost-card-value" style="color:${pc};">$${fmtNum(d.actualProfit)}</div>
                <div class="cost-card-sub">${d.profitPct}%${d.profitPct >= 20 ? ' ↑' : d.profitPct < 0 ? ' ↓' : ''}</div>
              </div>
            </div>
            <div class="cost-progress-wrap">
              <div class="cost-progress-bar" style="width:${Math.min(d.usagePct, 100)}%;background:${bc};"></div>
            </div>
            <div class="cost-progress-label">預算已使用 ${d.usagePct}% ($${fmtNum(d.totalEstimated)} / $${fmtNum(d.execBudget)})</div>
            ${f.transfer_fee ? `<div style="font-size:11px;color:#6b7280;margin-bottom:4px;">帳款匯費 $${fmtNum(f.transfer_fee)}</div>` : ''}
            ${alert}
            <div id="cost-groups-switcher"></div>
            ${_renderCostLines(costData.grouped || [], expData.expenses || [], f)}
        `;
        window._costDirtyMap = {};
        callbacks.renderGroupSwitcher?.();
        container.querySelectorAll('.cost-staff-sel').forEach(sel => searchableSelect(sel, { placeholder: '搜尋人員...' }));
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

function _renderAllocationAlert(f, d) {
    const execBudget = d.execBudget || 0;
    const allocated = f.allocated_budget_sum || 0;
    const missing = f.groups_missing_budget_count || 0;
    const diff = allocated - execBudget;
    let msgs = [];
    if (execBudget > 0 && diff > 0) {
        msgs.push('<div class="cg-alert cg-alert-danger">⚠ 子表預算加總超出合約執行預算 $' + fmtNum(diff) + ' — 建議調整</div>');
    } else if (execBudget > 0 && diff < 0 && missing === 0) {
        msgs.push('<div class="cg-alert cg-alert-ok">✓ 尚可分配 $' + fmtNum(-diff) + '</div>');
    }
    if (missing > 0) {
        msgs.push('<div class="cg-alert cg-alert-hint">💡 還有 ' + missing + ' 張子表未設預算</div>');
    }
    return msgs.join('');
}

// ── Expense Form ───────────────────────────────────────────────
function _showExpenseForm(editId = null, cat = '', est = 0, act = 0, notes = '') {
    const form = document.getElementById('proj-expense-form');
    if (!form) return;
    form.style.display = 'block';
    form.innerHTML = `
        <div class="expense-row" style="gap:4px;flex-wrap:wrap;padding:8px;background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;margin-bottom:6px;">
            <select id="exp-f-cat" class="crm-input" style="width:80px;">${EXPENSE_CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join('')}</select>
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

// ── Cost Table Rendering ───────────────────────────────────────
const _UNIT_TYPES = ['式','日','班','時','支','套','件'];

function _renderCostLines(grouped, expenses, financialSummary) {
    const staffOpts = '<option value="">— 未指定 —</option>' +
        state.staffList.map(s => `<option value="${s.id}">${_esc(s.name)} (${_esc(s.role || '')})</option>`).join('');
    const _unitOpts = function(curVal) {
        return '<option value="">—</option>' +
            _UNIT_TYPES.map(function(u) { return '<option value="' + u + '"' + (curVal === u ? ' selected' : '') + '>' + u + '</option>'; }).join('');
    };

    let grandEst = 0, grandAct = 0;
    grouped.forEach(g => g.lines.forEach(ln => {
        grandEst += ln.estimated_amount || 0;
        grandAct += ln.actual_amount || 0;
    }));

    let html = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;margin-top:12px;border-top:1px solid #2e2e2e;padding-top:10px;">
        <span style="font-size:12px;font-weight:700;color:#6b7280;">成本估算 / 實際結算</span>
        <div style="display:flex;gap:6px;align-items:center;">
          <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projShowExpenseModal()">雜支登記</button>
          <div style="position:relative;display:inline-block;" id="cost-tpl-dropdown-wrap">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costToggleTplDropdown()">套用範本 ▾</button>
            <div id="cost-tpl-dropdown" style="display:none;position:absolute;right:0;top:100%;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:6px;min-width:160px;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,0.4);margin-top:4px;"></div>
          </div>
          <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costSaveAsTemplate()">儲存為範本</button>
        </div>
      </div>`;

    if (grouped.length === 0) {
        html += `<div class="crm-empty" style="padding:16px 0;text-align:center;">
            <div style="margin-bottom:10px;color:#9ca3af;">尚無項目</div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projInitCostLines()">初始化標準項目</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;" onclick="window._costImportFromQuote()">從報價匯入</button>
        </div>`;
    } else {
        html += `<div class="cost-table">
          <div class="cost-row cost-row-header">
            <span class="cost-col-item">項目</span>
            <span class="cost-col-price">單價</span>
            <span class="cost-col-qty">數量</span>
            <span class="cost-col-unit">單位</span>
            <span class="cost-col-amt">預估金額</span>
            <span class="cost-col-staff cost-divider">預估人員</span>
            <span class="cost-col-copy"></span>
            <span class="cost-col-price">單價</span>
            <span class="cost-col-qty">數量</span>
            <span class="cost-col-unit">單位</span>
            <span class="cost-col-amt">結算金額</span>
            <span class="cost-col-staff">執行人員</span>
            <span class="cost-col-diff">差異</span>
            <span class="cost-col-actions"></span>
          </div>`;

        for (const group of grouped) {
            let phaseEst = 0, phaseAct = 0;
            group.lines.forEach(ln => {
                phaseEst += ln.estimated_amount || 0;
                phaseAct += ln.actual_amount || 0;
            });
            const phaseDiff = phaseAct - phaseEst;
            const phaseDiffColor = phaseDiff < 0 ? '#86efac' : phaseDiff > 0 ? '#fca5a5' : '#9ca3af';

            html += `<div class="cost-row cost-phase-header">
              <span class="cost-col-item" style="font-weight:700;">${_esc(group.phase)}</span>
              <span class="cost-col-price"></span><span class="cost-col-qty"></span><span class="cost-col-unit"></span><span class="cost-col-amt"></span><span class="cost-col-staff cost-divider"></span><span class="cost-col-copy"></span><span class="cost-col-price"></span><span class="cost-col-qty"></span><span class="cost-col-unit"></span><span class="cost-col-amt"></span><span class="cost-col-staff"></span>
              <span class="cost-col-diff" style="display:flex;justify-content:flex-end;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:0 6px;font-size:11px;line-height:18px;" onclick="window._costAddItem('${_esc(group.phase)}')">+</button>
              </span>
              <span class="cost-col-actions">
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:1px 5px;" onclick="window._costDeletePhase('${_esc(group.phase)}')">✕</button>
              </span>
            </div>`;

            for (const ln of group.lines) {
                const diff = (ln.actual_amount || 0) - (ln.estimated_amount || 0);
                const bothZero = !ln.estimated_amount && !ln.actual_amount;
                const _d = diffLabel(diff, bothZero);
                const diffStr = _d.text;
                const diffColor = _d.color;

                const _selHtml = (curVal) => {
                    return staffOpts.replace(
                        new RegExp(`value="${curVal}"`),
                        `value="${curVal}" selected`
                    );
                };

                html += `
                  <div class="cost-row" data-line-id="${ln.id}">
                    <span class="cost-col-item cost-editable" onclick="window._costEditName(this,'${ln.id}','${_esc(ln.item_name)}')">${_esc(ln.item_name)}</span>
                    <span class="cost-col-price cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','estimated_unit_price',${ln.estimated_unit_price ?? "''"})">
                      ${ln.estimated_unit_price != null ? '$' + fmtNum(ln.estimated_unit_price) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-qty cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','estimated_quantity',${ln.estimated_quantity ?? "''"})">
                      ${ln.estimated_quantity != null ? ln.estimated_quantity : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-unit">
                      <select class="crm-input cost-unit-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','estimated_unit_type',this.value)">
                        ${_unitOpts(ln.estimated_unit_type)}
                      </select>
                    </span>
                    <span class="cost-col-amt">
                      ${ln.estimated_amount != null ? '$' + fmtNum(ln.estimated_amount) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-staff cost-divider">
                      <select class="crm-input cost-staff-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','estimated_staff_id',this.value)">
                        ${_selHtml(ln.estimated_staff_id)}
                      </select>
                    </span>
                    <span class="cost-col-copy" onclick="window._costCopyToActual('${ln.id}')">→</span>
                    <span class="cost-col-price cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','actual_unit_price',${ln.actual_unit_price ?? "''"})">
                      ${ln.actual_unit_price != null ? '$' + fmtNum(ln.actual_unit_price) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-qty cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','actual_quantity',${ln.actual_quantity ?? "''"})">
                      ${ln.actual_quantity != null ? ln.actual_quantity : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-unit">
                      <select class="crm-input cost-unit-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','actual_unit_type',this.value)">
                        ${_unitOpts(ln.actual_unit_type)}
                      </select>
                    </span>
                    <span class="cost-col-amt">
                      ${ln.actual_amount != null ? '$' + fmtNum(ln.actual_amount) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-staff">
                      <select class="crm-input cost-staff-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','actual_staff_id',this.value)">
                        ${_selHtml(ln.actual_staff_id)}
                      </select>
                    </span>
                    <span class="cost-col-diff" style="color:${diffColor};">${diffStr}</span>
                    <span class="cost-col-actions">
                      <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:1px 5px;"
                              onclick="window._projDeleteCostLine('${ln.id}')">✕</button>
                    </span>
                  </div>`;
            }

            html += `
              <div class="cost-row cost-row-subtotal">
                <span class="cost-col-item" style="color:#9ca3af;font-style:italic;">${_esc(group.phase)} 小計</span>
                <span class="cost-col-price"></span>
                <span class="cost-col-qty"></span>
                <span class="cost-col-unit"></span>
                <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(phaseEst)}</span>
                <span class="cost-col-staff cost-divider"></span>
                <span class="cost-col-copy"></span>
                <span class="cost-col-price"></span>
                <span class="cost-col-qty"></span>
                <span class="cost-col-unit"></span>
                <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(phaseAct)}</span>
                <span class="cost-col-staff"></span>
                <span class="cost-col-diff" style="color:${diffLabel(phaseDiff, !phaseEst && !phaseAct, true).color};font-weight:600;">
                  ${diffLabel(phaseDiff, !phaseEst && !phaseAct, true).text}
                </span>
                <span class="cost-col-actions"></span>
              </div>`;
        }

        // ── 行政雜支 section (from crm_project_expenses) ──
        const miscBudget = financialSummary ? (financialSummary.misc_budget || 0) : 0;
        const expActualTotal = (expenses || []).reduce((s, e) => s + (e.actual || 0), 0);
        const expDiff = expActualTotal - miscBudget;
        const expDiffColor = expDiff < 0 ? '#86efac' : expDiff > 0 ? '#fca5a5' : '#9ca3af';

        html += `<div class="cost-phase-header" style="display:flex;justify-content:space-between;align-items:center;">
          <span>行政雜支</span>
          <div style="display:flex;gap:6px;align-items:center;">
            <span style="font-weight:400;font-size:11px;color:#6b7280;">預估 $${fmtNum(miscBudget)}</span>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:0 6px;font-size:11px;line-height:18px;" onclick="window._projShowExpenseModal()">+</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:0 6px;font-size:11px;line-height:18px;" onclick="window._projBrowseReceipts()" title="瀏覽收據">&#128065;</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:0 6px;font-size:11px;line-height:18px;" onclick="window._projShareExpenseLink()" title="複製當前子表的雜支登記連結">🔗</button>
          </div>
        </div>`;

        if (expenses && expenses.length > 0) {
            for (const e of expenses) {
                const label = e.sub_item ? _esc(e.category) + ' · ' + _esc(e.sub_item) : _esc(e.category);
                const dateStr = e.created_at ? `<span style="color:#4b5563;font-size:10px;margin-left:6px;">${e.created_at}</span>` : '';
                html += `
                  <div class="cost-row">
                    <span class="cost-col-item">${label}${dateStr}</span>
                    <span class="cost-col-amt"></span>
                    <span class="cost-col-staff"></span>
                    <span class="cost-col-amt">$${fmtNum(e.actual)}</span>
                    <span class="cost-col-staff" style="font-size:11px;color:#9ca3af;">${_esc(e.payee || '')}</span>
                    <span class="cost-col-diff">${e.receipt_url ? '<a href="' + e.receipt_url + '" target="_blank" style="color:#3b82f6;">📎</a>' : '—'}</span>
                    <span class="cost-col-actions">
                      <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:1px 5px;"
                              onclick="window._projDeleteExpense('${e.id}')">✕</button>
                    </span>
                  </div>`;
            }
        } else {
            html += '<div class="cost-row"><span class="cost-col-item crm-muted" style="font-size:11px;">尚無雜支紀錄</span></div>';
        }

        html += `
          <div class="cost-row cost-row-subtotal">
            <span class="cost-col-item" style="color:#9ca3af;font-style:italic;">行政雜支 小計</span>
            <span class="cost-col-price"></span>
            <span class="cost-col-qty"></span>
            <span class="cost-col-unit"></span>
            <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(expActualTotal)}</span>
            <span class="cost-col-staff cost-divider"></span>
            <span class="cost-col-price"></span>
            <span class="cost-col-qty"></span>
            <span class="cost-col-unit"></span>
            <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(expActualTotal)}</span>
            <span class="cost-col-staff"></span>
            <span class="cost-col-diff" style="color:${diffLabel(expDiff, !miscBudget && !expActualTotal, true).color};font-weight:600;">
              ${diffLabel(expDiff, !miscBudget && !expActualTotal, true).text}
            </span>
            <span class="cost-col-actions"></span>
          </div>`;

        // ── Grand total (cost lines + expenses) ──
        const totalEst = grandEst + expActualTotal;
        const totalAct = grandAct + expActualTotal;
        const totalDiff = totalAct - totalEst;
        const totalDiffColor = totalDiff < 0 ? '#86efac' : totalDiff > 0 ? '#fca5a5' : '#9ca3af';
        html += `
          <div class="cost-row cost-row-total">
            <span class="cost-col-item">本子表合計</span>
            <span class="cost-col-price"></span>
            <span class="cost-col-qty"></span>
            <span class="cost-col-unit"></span>
            <span class="cost-col-amt">$${fmtNum(totalEst)}</span>
            <span class="cost-col-staff cost-divider"></span>
            <span class="cost-col-price"></span>
            <span class="cost-col-qty"></span>
            <span class="cost-col-unit"></span>
            <span class="cost-col-amt">$${fmtNum(totalAct)}</span>
            <span class="cost-col-staff"></span>
            <span class="cost-col-diff" style="color:${diffLabel(totalDiff, !totalEst && !totalAct, true).color};">
              ${diffLabel(totalDiff, !totalEst && !totalAct, true).text}
            </span>
            <span class="cost-col-actions"></span>
          </div>`;

        html += '</div>';
    }
    return html;
}

// ── Copy estimated → actual ────────────────────────────────────
window._costCopyToActual = function(lineId) {
    var row = document.querySelector('[data-line-id="' + lineId + '"]');
    if (!row) return;
    window._costShowSaveBtn(true);
    var dirty = window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});

    // Read estimated values from cells or dirty map
    var estPriceEl = row.querySelector('[onclick*="estimated_unit_price"]');
    var estQtyEl = row.querySelector('[onclick*="estimated_quantity"]');
    var estStaffEl = row.querySelectorAll('.cost-staff-sel')[0];
    var estUnitEl = row.querySelectorAll('.cost-unit-sel')[0];

    var price = dirty['estimated_unit_price'];
    if (price === undefined && estPriceEl) {
        var pt = estPriceEl.textContent.replace(/[$,]/g, '').trim();
        price = pt && pt !== '—' ? parseInt(pt) : null;
    }
    var qty = dirty['estimated_quantity'];
    if (qty === undefined && estQtyEl) {
        var qt = estQtyEl.textContent.trim();
        qty = qt && qt !== '—' ? parseInt(qt) : null;
    }
    var staffId = estStaffEl ? estStaffEl.value : '';
    var unitType = estUnitEl ? estUnitEl.value : '';

    // Write to dirty map
    if (price != null) dirty['actual_unit_price'] = price;
    if (qty != null) dirty['actual_quantity'] = qty;
    dirty['actual_staff_id'] = staffId || null;
    dirty['actual_unit_type'] = unitType || null;
    var amt = (price && qty) ? price * qty : null;
    if (amt != null) dirty['actual_amount'] = amt;

    // Update actual side cells visually
    var actPriceEl = row.querySelector('[onclick*="actual_unit_price"]');
    var actQtyEl = row.querySelector('[onclick*="actual_quantity"]');
    var actStaffEl = row.querySelectorAll('.cost-staff-sel')[1];
    var actUnitEl = row.querySelectorAll('.cost-unit-sel')[1];
    var amtCells = row.querySelectorAll('.cost-col-amt');

    if (actPriceEl && price != null) actPriceEl.innerHTML = '$' + fmtNum(price);
    if (actQtyEl && qty != null) actQtyEl.textContent = qty;
    if (actStaffEl) actStaffEl.value = staffId;
    if (actUnitEl) actUnitEl.value = unitType;
    if (amtCells[1] && amt != null) amtCells[1].innerHTML = '$' + fmtNum(amt);

    _costUpdateDiff(row);
    _costUpdateDashboard();
};

// ── Subtotal / Dashboard live update ───────────────────────────
function _costUpdateSubtotals() {
    // Recalculate all phase subtotals and grand total from individual rows
    var allRows = document.querySelectorAll('.cost-row[data-line-id]');
    var phases = {};
    allRows.forEach(function(row) {
        // Find which phase this row belongs to by scanning backwards for phase header
        var prev = row.previousElementSibling;
        var phase = '';
        while (prev) {
            if (prev.classList.contains('cost-phase-header')) {
                var itemEl = prev.querySelector('.cost-col-item');
                if (itemEl) phase = itemEl.textContent.trim();
                break;
            }
            prev = prev.previousElementSibling;
        }
        if (!phase) return;
        if (!phases[phase]) phases[phase] = { est: 0, act: 0 };
        var amts = row.querySelectorAll('.cost-col-amt');
        if (amts[0]) {
            var e = amts[0].textContent.replace(/[$,]/g, '').trim();
            if (e && e !== '—') phases[phase].est += parseInt(e);
        }
        if (amts[1]) {
            var a = amts[1].textContent.replace(/[$,]/g, '').trim();
            if (a && a !== '—') phases[phase].act += parseInt(a);
        }
    });

    // Update subtotal rows
    var subtotals = document.querySelectorAll('.cost-row-subtotal');
    var grandEst = 0, grandAct = 0;
    subtotals.forEach(function(sub) {
        var label = sub.querySelector('.cost-col-item');
        if (!label) return;
        var text = label.textContent.trim();
        // Match phase name from "XXX 小計"
        for (var p in phases) {
            if (text.indexOf(p) >= 0 && text.indexOf('小計') >= 0) {
                var amts = sub.querySelectorAll('.cost-col-amt');
                if (amts[0]) amts[0].textContent = '$' + fmtNum(phases[p].est);
                if (amts[1]) amts[1].textContent = '$' + fmtNum(phases[p].act);
                var diff = phases[p].act - phases[p].est;
                var diffCell = sub.querySelector('.cost-col-diff');
                if (diffCell) {
                    var dl = diffLabel(diff, !phases[p].est && !phases[p].act, true);
                    diffCell.textContent = dl.text;
                    diffCell.style.color = dl.color;
                }
                grandEst += phases[p].est;
                grandAct += phases[p].act;
                break;
            }
        }
        // 行政雜支 subtotal — add to grand total (don't recalc, expenses are static)
        if (text.indexOf('行政雜支') >= 0) {
            var eAmts = sub.querySelectorAll('.cost-col-amt');
            if (eAmts[0]) { var v = eAmts[0].textContent.replace(/[$,]/g, '').trim(); if (v && v !== '—') grandEst += parseInt(v); }
            if (eAmts[1]) { var v2 = eAmts[1].textContent.replace(/[$,]/g, '').trim(); if (v2 && v2 !== '—') grandAct += parseInt(v2); }
        }
    });

    // Update grand total row
    var totalRow = document.querySelector('.cost-row-total');
    if (totalRow) {
        var tAmts = totalRow.querySelectorAll('.cost-col-amt');
        if (tAmts[0]) tAmts[0].textContent = '$' + fmtNum(grandEst);
        if (tAmts[1]) tAmts[1].textContent = '$' + fmtNum(grandAct);
        var tDiff = grandAct - grandEst;
        var tDiffCell = totalRow.querySelector('.cost-col-diff');
        if (tDiffCell) {
            var tdl = diffLabel(tDiff, !grandEst && !grandAct, true);
            tDiffCell.textContent = tdl.text;
            tDiffCell.style.color = tdl.color;
        }
    }
    return { est: grandEst, act: grandAct };
}

function _costUpdateDashboard() {
    var totals = _costUpdateSubtotals();
    var totalEst = totals ? totals.est : 0;
    var totalAct = totals ? totals.act : 0;

    // Read financial summary from data attributes on dashboard
    var dash = document.querySelector('.cost-dashboard');
    if (!dash) return;
    var exTax = parseInt(dash.dataset.exTax) || 0;
    var profitTarget = parseInt(dash.dataset.profitTarget) || 0;

    var execBudget = exTax - profitTarget;
    var remaining = execBudget - totalEst;
    var actualProfit = exTax - totalAct;
    var profitPct = exTax > 0 ? Math.round(actualProfit / exTax * 100) : 0;
    var usagePct = execBudget > 0 ? Math.round(totalEst / execBudget * 100) : 0;

    // Update cards
    var cards = dash.querySelectorAll('.cost-card');
    if (cards[1]) {
        var rv = cards[1].querySelector('.cost-card-value');
        if (rv) { rv.textContent = '$' + fmtNum(remaining); rv.style.color = remaining >= 0 ? '#86efac' : '#fca5a5'; }
    }
    if (cards[2]) {
        var sv = cards[2].querySelector('.cost-card-value');
        if (sv) sv.textContent = '$' + fmtNum(totalAct);
    }
    if (cards[3]) {
        var pv = cards[3].querySelector('.cost-card-value');
        var ps = cards[3].querySelector('.cost-card-sub');
        if (pv) { pv.textContent = '$' + fmtNum(actualProfit); pv.style.color = profitPct >= 20 ? '#86efac' : profitPct >= 0 ? '#fbbf24' : '#fca5a5'; }
        if (ps) ps.textContent = profitPct + '%' + (profitPct >= 20 ? ' ↑' : profitPct < 0 ? ' ↓' : '');
    }

    // Update progress bar
    var bar = document.querySelector('.cost-progress-bar');
    if (bar) { bar.style.width = Math.min(usagePct, 100) + '%'; bar.style.background = usagePct > 100 ? '#ef4444' : usagePct > 80 ? '#f59e0b' : '#3b82f6'; }
    var label = document.querySelector('.cost-progress-label');
    if (label) label.textContent = '預算已使用 ' + usagePct + '% ($' + fmtNum(totalEst) + ' / $' + fmtNum(execBudget) + ')';
}

function _costUpdateDiff(row) {
    var amtCells = row.querySelectorAll('.cost-col-amt');
    var diffCell = row.querySelector('.cost-col-diff');
    if (!diffCell || amtCells.length < 2) return;
    var estText = amtCells[0].textContent.replace(/[$,]/g, '').trim();
    var actText = amtCells[1].textContent.replace(/[$,]/g, '').trim();
    var est = estText && estText !== '—' ? parseInt(estText) : 0;
    var act = actText && actText !== '—' ? parseInt(actText) : 0;
    var bothZero = !est && !act;
    var diff = act - est;
    var d = diffLabel(diff, bothZero);
    diffCell.style.color = d.color;
    diffCell.textContent = d.text;
}

// ── Inline edit: numeric fields ────────────────────────────────
window._costStartEdit = function(cell, lineId, field, currentVal) {
    if (cell.querySelector('input')) return;
    window._costShowSaveBtn(true);
    var isQty = field.indexOf('quantity') >= 0;
    var isUnitOrQty = field.indexOf('unit_price') >= 0 || isQty;
    var input = document.createElement('input');
    input.type = 'number';
    input.className = 'crm-input';
    input.style.cssText = 'width:100%;max-width:100%;box-sizing:border-box;padding:2px 4px;font-size:11px;text-align:right;';
    input.value = currentVal !== '' ? currentVal : '';
    input.min = '0';
    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    input.select();

    input.addEventListener('blur', function() {
        try {
            var val = input.value.trim();
            var parsed = val === '' ? null : parseInt(val);
            window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});
            window._costDirtyMap[lineId][field] = parsed;
            // Display: no $ for quantity fields
            if (isQty) {
                cell.innerHTML = val === '' ? '<span class="crm-muted">—</span>' : val;
            } else {
                cell.innerHTML = val === '' ? '<span class="crm-muted">—</span>' : '$' + fmtNum(parseInt(val));
            }
            // Auto-calculate amount when unit_price or quantity changes
            if (isUnitOrQty) {
                var row = cell.closest('.cost-row');
                if (row) {
                    var side = field.indexOf('estimated') === 0 ? 'estimated' : 'actual';
                    var dirty = window._costDirtyMap[lineId] || {};
                    // Get current values from dirty map or from displayed cells
                    var priceCell = row.querySelector('[onclick*="' + side + '_unit_price"]');
                    var qtyCell = row.querySelector('[onclick*="' + side + '_quantity"]');
                    var price = dirty[side + '_unit_price'];
                    if (price === undefined && priceCell) {
                        var pt = priceCell.textContent.replace(/[$,]/g, '').trim();
                        price = pt && pt !== '—' ? parseInt(pt) : null;
                    }
                    var qty = dirty[side + '_quantity'];
                    if (qty === undefined && qtyCell) {
                        var qt = qtyCell.textContent.trim();
                        qty = qt && qt !== '—' ? parseInt(qt) : null;
                    }
                    var amt = (price && qty) ? price * qty : null;
                    var amtCells = row.querySelectorAll('.cost-col-amt');
                    var amtCell = side === 'estimated' ? amtCells[0] : amtCells[1];
                    if (amtCell) {
                        amtCell.innerHTML = amt != null ? '$' + fmtNum(amt) : '<span class="crm-muted">—</span>';
                    }
                    // Update diff cell
                    _costUpdateDiff(row);
                    _costUpdateDashboard();
                }
            } else {
                // Non unit/qty field edited (e.g. direct amount) — also update diff
                var row2 = cell.closest('.cost-row');
                if (row2) { _costUpdateDiff(row2); _costUpdateDashboard(); }
            }
        } catch(e) { alert('Error: ' + e.message); }
    });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { _loadFinancialSummary(state.selectedId); }
    });
};

// ── Mark field dirty (for select changes) ──────────────────────
window._costMarkDirtyField = function(lineId, field, value) {
    window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});
    window._costDirtyMap[lineId][field] = value || null;
    window._costShowSaveBtn(true);
};

// ── Save all dirty edits ───────────────────────────────────────
window._costSaveAll = async function() {
    var saveBtn = document.getElementById('_inline-save');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = '儲存中...'; }
    var costEntries = Object.entries(window._costDirtyMap || {});

    // Also check if enableInlineEdit fields are present (edit button was clicked)
    var infoPayload = null;
    var infoFields = document.querySelectorAll('#proj-detail-info [data-field]');
    if (infoFields.length > 0) {
        infoPayload = {};
        infoFields.forEach(function(el) {
            var name = el.dataset.field;
            var val = el.value;
            if (_INT_FIELD_NAMES.indexOf(name) >= 0) val = val ? parseInt(val) : null;
            if (el.type === 'date' || el.type === 'month') val = val || null;
            infoPayload[name] = val;
        });
    }

    if (costEntries.length === 0 && !infoPayload) {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '儲存'; }
        return;
    }
    try {
        // Save project info if edited
        if (infoPayload && state.selectedId) {
            await _fetch('/projects/' + state.selectedId, { method: 'PUT', body: JSON.stringify(infoPayload) });
        }
        // Save cost line edits
        for (var i = 0; i < costEntries.length; i++) {
            await _fetch('/project-cost-lines/' + costEntries[i][0], {
                method: 'PUT', body: JSON.stringify(costEntries[i][1])
            });
        }
        window._costDirtyMap = {};
        // Restore and reload
        var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
        if (infoPayload) await callbacks.loadProjects?.();
        var updated = proj;
        if (infoPayload) {
            updated = await _fetch('/projects/' + state.selectedId);
        }
        callbacks.renderDetail?.(updated || proj);
        _loadFinancialSummary(state.selectedId);
    } catch (e) {
        alert('儲存失敗: ' + e.message);
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '儲存'; }
    }
};

// ── Init cost lines (standard template) ────────────────────────
window._projInitCostLines = async function() {
    if (!state.selectedId) return;
    try {
        const r = await _fetch('/projects/' + state.selectedId + '/cost-lines/init', {
            method: 'POST',
            body: JSON.stringify({ cost_group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
        if (r.added === 0) alert('所有標準項目已存在，無需初始化');
    } catch (e) { alert('初始化失敗：' + e.message); }
};

// ── Import cost lines from quotation ──────────────────────────
window._costImportFromQuote = async function() {
    if (!state.selectedId) return;
    document.getElementById('cost-tpl-dropdown')?.style.setProperty('display', 'none');
    try {
        const data = await _fetch('/projects/' + state.selectedId + '/quotations');
        const quots = data.quotations || [];
        if (quots.length === 0) { alert('此專案尚無報價單'); return; }

        if (quots.length === 1) {
            await _doImportFromQuote(quots[0].id);
            return;
        }
        // Multiple versions — show modal picker (same pattern as _projActivate)
        let overlay = document.getElementById('cost-import-quote-overlay');
        if (overlay) overlay.remove();
        overlay = document.createElement('div');
        overlay.id = 'cost-import-quote-overlay';
        overlay.className = 'crm-modal-overlay';
        overlay.style.display = 'flex';
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
        overlay.innerHTML = `
          <div class="crm-modal" style="max-width:400px;">
            <div class="crm-modal-header">
              <h3>從報價匯入</h3>
              <button onclick="document.getElementById('cost-import-quote-overlay').remove()" class="crm-detail-close">✕</button>
            </div>
            <div class="crm-modal-body">
              <p style="font-size:13px;color:#9ca3af;margin-bottom:12px;">選擇報價版本匯入為成本項目（現有項目將被取代）</p>
              <div style="display:flex;flex-direction:column;gap:6px;">
                ${quots.map(q => {
                    const price = q.final_price != null ? q.final_price : q.total;
                    return `<button class="pi-activate-option" onclick="window._doImportFromQuote('${q.id}')">
                      <span>v${q.version}</span>
                      <span style="font-size:11px;color:#9ca3af;">${q.status}</span>
                      <span style="font-weight:600;color:#e0e0e0;">$${(price ?? 0).toLocaleString()}</span>
                    </button>`;
                }).join('')}
              </div>
            </div>
          </div>`;
        document.body.appendChild(overlay);
    } catch (e) { alert('匯入失敗：' + e.message); }
};

window._doImportFromQuote = async function(quotationId) {
    const overlay = document.getElementById('cost-import-quote-overlay');
    if (overlay) overlay.remove();
    const groupName = state.costGroups.find(g => g.id === state.selectedGroupId)?.name || '主表';
    if (!confirm('將從報價匯入成本項目到「' + groupName + '」，該子表現有項目將被取代。確定？')) return;
    try {
        const r = await _fetch('/projects/' + state.selectedId + '/cost-lines/import-from-quotation', {
            method: 'POST',
            body: JSON.stringify({ quotation_id: quotationId, cost_group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
        if (r.added === 0) alert('報價中沒有項目可匯入');
    } catch (e) { alert('匯入失敗：' + e.message); }
};

// ── Delete entire phase ────────────────────────────────────────
window._costDeletePhase = async function(phase) {
    if (!state.selectedId) return;
    if (!confirm('確定刪除「' + phase + '」所有項目？')) return;
    try {
        await _fetch('/projects/' + state.selectedId + '/cost-lines/phase', {
            method: 'DELETE',
            body: JSON.stringify({ phase: phase, group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert('刪除失敗：' + e.message); }
};

// ── Delete single cost line ────────────────────────────────────
window._projDeleteCostLine = async function(lineId) {
    if (!confirm('確定刪除此項目？')) return;
    try {
        await _fetch('/project-cost-lines/' + lineId, { method: 'DELETE' });
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert(e.message); }
};

// ── Edit item name ─────────────────────────────────────────────
window._costEditName = function(cell, lineId, currentName) {
    if (cell.querySelector('input')) return;
    window._costShowSaveBtn(true);
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'crm-input';
    input.style.cssText = 'width:100%;padding:2px 6px;font-size:12px;';
    input.value = currentName;
    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    input.select();

    input.addEventListener('blur', function() {
        try {
            var val = input.value.trim();
            if (!val || val === currentName) {
                cell.textContent = currentName;
                return;
            }
            window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});
            window._costDirtyMap[lineId]['item_name'] = val;
            cell.textContent = val;
        } catch(e) { alert('Error: ' + e.message); }
    });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { cell.textContent = currentName; }
    });
};

// ── Add custom item to phase ───────────────────────────────────
window._costAddItem = function(phase) {
    if (!state.selectedId) return;
    var rows = document.querySelectorAll('.cost-row-subtotal');
    var target = null;
    for (var i = 0; i < rows.length; i++) {
        var item = rows[i].querySelector('.cost-col-item');
        if (item && item.textContent.indexOf(phase) >= 0) { target = rows[i]; break; }
    }
    if (!target) return;
    if (document.getElementById('cost-add-form')) document.getElementById('cost-add-form').remove();
    var unitOpts = '<option value="">—</option>' + _UNIT_TYPES.map(function(u) { return '<option value="' + u + '">' + u + '</option>'; }).join('');
    var staffOpts = '<option value="">— 未指定 —</option>' + state.staffList.map(function(s) { return '<option value="' + s.id + '">' + _esc(s.name) + ' (' + _esc(s.role || '') + ')</option>'; }).join('');
    var form = document.createElement('div');
    form.id = 'cost-add-form';
    form.className = 'cost-row';
    form.style.cssText = 'background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;padding:6px;margin:4px 0;';
    form.innerHTML = '' +
        '<span class="cost-col-item"><input id="cost-add-name" type="text" class="crm-input" placeholder="項目名稱" style="width:100%;font-size:11px;padding:3px 6px;box-sizing:border-box;"></span>' +
        '<span class="cost-col-price"><input id="cost-add-price" type="number" class="crm-input" placeholder="單價" style="width:100%;font-size:11px;padding:2px 4px;text-align:right;box-sizing:border-box;-moz-appearance:textfield;"></span>' +
        '<span class="cost-col-qty"><input id="cost-add-qty" type="number" class="crm-input" placeholder="數量" style="width:100%;font-size:11px;padding:2px 4px;text-align:center;box-sizing:border-box;-moz-appearance:textfield;"></span>' +
        '<span class="cost-col-unit"><select id="cost-add-unit" class="crm-input cost-unit-sel">' + unitOpts + '</select></span>' +
        '<span class="cost-col-amt" id="cost-add-amt" style="color:#9ca3af;">—</span>' +
        '<span class="cost-col-staff cost-divider"><select id="cost-add-staff" class="crm-input cost-staff-sel">' + staffOpts + '</select></span>' +
        '<span class="cost-col-copy"></span>' +
        '<span class="cost-col-price"></span><span class="cost-col-qty"></span><span class="cost-col-unit"></span><span class="cost-col-amt"></span><span class="cost-col-staff"></span>' +
        '<span class="cost-col-diff" style="display:flex;gap:4px;">' +
            '<button class="crm-btn crm-btn-primary crm-btn-sm" style="padding:2px 6px;" onclick="window._costDoAddItem(\'' + phase + '\')">確定</button>' +
            '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:2px 6px;" onclick="document.getElementById(\'cost-add-form\').remove()">取消</button>' +
        '</span>' +
        '<span class="cost-col-actions"></span>';
    target.parentNode.insertBefore(form, target);
    document.getElementById('cost-add-name').focus();
    // Auto-calc amount preview
    var _calcPreview = function() {
        var p = parseInt(document.getElementById('cost-add-price').value) || 0;
        var q = parseInt(document.getElementById('cost-add-qty').value) || 0;
        var el = document.getElementById('cost-add-amt');
        if (el) el.textContent = (p && q) ? '$' + fmtNum(p * q) : '—';
    };
    document.getElementById('cost-add-price').addEventListener('input', _calcPreview);
    document.getElementById('cost-add-qty').addEventListener('input', _calcPreview);
    document.getElementById('cost-add-name').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') window._costDoAddItem(phase);
        if (e.key === 'Escape') form.remove();
    });
};

window._costDoAddItem = async function(phase) {
    var name = (document.getElementById('cost-add-name') || {}).value || '';
    name = name.trim();
    if (!name) { alert('請輸入項目名稱'); return; }
    var price = parseInt((document.getElementById('cost-add-price') || {}).value) || null;
    var qty = parseInt((document.getElementById('cost-add-qty') || {}).value) || null;
    var unitType = (document.getElementById('cost-add-unit') || {}).value || null;
    var staffId = (document.getElementById('cost-add-staff') || {}).value || null;
    var amt = (price && qty) ? price * qty : null;
    try {
        await _fetch('/projects/' + state.selectedId + '/cost-lines', {
            method: 'POST', body: JSON.stringify({
                phase: phase, item_name: name, sort_order: 99,
                cost_group_id: state.selectedGroupId,
                estimated_unit_price: price, estimated_quantity: qty,
                estimated_unit_type: unitType, estimated_amount: amt,
                estimated_staff_id: staffId
            })
        });
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert('新增失敗：' + e.message); }
};

// ── Template dropdown ──────────────────────────────────────────
window._costToggleTplDropdown = async function() {
    const dd = document.getElementById('cost-tpl-dropdown');
    if (!dd) return;
    if (dd.style.display !== 'none') { dd.style.display = 'none'; return; }
    dd.innerHTML = '<div style="padding:8px;color:#9ca3af;font-size:11px;">載入中...</div>';
    dd.style.display = 'block';
    try {
        const data = await _fetch('/cost-line-templates');
        const tpls = data.templates || [];
        let items = `<div class="cost-tpl-item" onclick="window._costApplyTemplate('__default__')">
            <span>標準項目</span><span style="color:#6b7280;font-size:10px;">${_COST_LINE_DEFAULT_COUNT} 項</span>
        </div>`;
        for (const t of tpls) {
            items += `<div class="cost-tpl-item" style="display:flex;justify-content:space-between;align-items:center;gap:4px;">
                <span onclick="window._costApplyTemplate('${t.id}')" style="flex:1;cursor:pointer;">${_esc(t.name)}<span style="color:#6b7280;font-size:10px;margin-left:4px;">${t.item_count} 項</span></span>
                <button style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:11px;padding:0 2px;" onclick="event.stopPropagation();window._costRenameTemplate('${t.id}','${_esc(t.name)}')">✎</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:0 4px;font-size:10px;line-height:16px;" onclick="event.stopPropagation();window._costDeleteTemplate('${t.id}')">✕</button>
            </div>`;
        }
        if (tpls.length === 0) items += '<div style="padding:4px 12px;color:#6b7280;font-size:11px;">尚無自訂範本</div>';
        items += '<div style="border-top:1px solid #3a3a3a;margin:4px 0;"></div>';
        items += `<div class="cost-tpl-item" onclick="window._costImportFromQuote()">📄 從報價匯入...</div>`;
        dd.innerHTML = items;
    } catch (e) { dd.innerHTML = '<div style="padding:8px;color:#fca5a5;">載入失敗</div>'; }
    // Close on click outside
    const _close = (e) => { if (!document.getElementById('cost-tpl-dropdown-wrap')?.contains(e.target)) { dd.style.display = 'none'; document.removeEventListener('click', _close); } };
    setTimeout(() => document.addEventListener('click', _close), 0);
};

const _COST_LINE_DEFAULT_COUNT = 25;

window._costApplyTemplate = async function(templateId) {
    if (!state.selectedId) return;
    document.getElementById('cost-tpl-dropdown').style.display = 'none';
    const groupName = state.costGroups.find(g => g.id === state.selectedGroupId)?.name || '主表';
    if (!confirm('將範本套用到「' + groupName + '」，該子表現有項目將被取代。確定？')) return;
    try {
        const r = await _fetch('/projects/' + state.selectedId + '/cost-lines/apply-template', {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId, cost_group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
        if (r.added === 0) alert('所有項目已存在，無需新增');
    } catch (e) { alert('套用失敗：' + e.message); }
};

window._costSaveAsTemplate = async function() {
    if (!state.selectedId) return;
    const name = prompt('請輸入範本名稱：');
    if (!name?.trim()) return;
    try {
        const r = await _fetch('/cost-line-templates', {
            method: 'POST', body: JSON.stringify({ name: name.trim(), project_id: state.selectedId })
        });
        alert('範本已建立（' + r.item_count + ' 個項目）');
    } catch (e) { alert('建立失敗：' + e.message); }
};

window._costRenameTemplate = async function(templateId, currentName) {
    var newName = prompt('修改範本名稱：', currentName);
    if (!newName || !newName.trim() || newName.trim() === currentName) return;
    try {
        await _fetch('/cost-line-templates/' + templateId, {
            method: 'PUT', body: JSON.stringify({ name: newName.trim() })
        });
        window._costToggleTplDropdown();
    } catch (e) { alert('修改失敗：' + e.message); }
};

window._costDeleteTemplate = async function(templateId) {
    if (!confirm('確定刪除此範本？')) return;
    try {
        await _fetch('/cost-line-templates/' + templateId, { method: 'DELETE' });
        window._costToggleTplDropdown();
    } catch (e) { alert(e.message); }
};

// ── Unsaved changes guard ──────────────────────────────────────
window._costCheckUnsaved = function(callback) {
    if (Object.keys(window._costDirtyMap || {}).length === 0) {
        if (callback) callback();
        return true;
    }
    // If no callback, use sync confirm (for beforeunload etc)
    if (!callback) {
        return confirm('有未儲存的修改，確定要離開嗎？');
    }
    // Show custom modal
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.innerHTML = '<div class="crm-modal" style="max-width:360px;">' +
        '<div class="crm-modal-header"><h3>未儲存的修改</h3></div>' +
        '<div class="crm-modal-body" style="padding:16px;color:#d1d5db;">你有尚未儲存的成本估算修改，要如何處理？</div>' +
        '<div class="crm-modal-footer" style="display:flex;gap:8px;justify-content:flex-end;">' +
            '<button id="_unsaved-cancel" class="crm-btn crm-btn-secondary crm-btn-sm">取消</button>' +
            '<button id="_unsaved-discard" class="crm-btn crm-btn-secondary crm-btn-sm" style="color:#fca5a5;">不儲存</button>' +
            '<button id="_unsaved-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('_unsaved-cancel').addEventListener('click', function() { overlay.remove(); });
    document.getElementById('_unsaved-discard').addEventListener('click', function() {
        overlay.remove();
        window._costDirtyMap = {};
        callback();
    });
    document.getElementById('_unsaved-save').addEventListener('click', async function() {
        overlay.remove();
        await window._costSaveAll();
        callback();
    });
    return false;
};

window._costShowSaveBtn = function(show) {
    var actions = document.getElementById('proj-bar-actions');
    if (!actions) return;
    // Already showing save buttons? skip
    if (show && document.getElementById('_inline-save')) return;
    if (!show) return; // hide is handled by renderDetail on reload

    // Copy the exact pattern from enableInlineEdit (line 116-123 of crm-utils.js)
    var closeBtn = actions.querySelector('.crm-detail-close');
    var closeHtml = closeBtn ? closeBtn.outerHTML : '';
    actions.innerHTML =
        '<button class="crm-btn crm-btn-secondary crm-btn-sm" id="_inline-cancel">取消</button>' +
        '<button class="crm-btn crm-btn-primary crm-btn-sm" id="_inline-save">儲存</button>' +
        closeHtml;

    document.getElementById('_inline-cancel').addEventListener('click', function() {
        window._costCancelAll();
    });
    document.getElementById('_inline-save').addEventListener('click', function() {
        window._costSaveAll();
    });
    // Re-attach close handler
    var newClose = actions.querySelector('.crm-detail-close');
    if (newClose) {
        newClose.addEventListener('click', function() {
            document.getElementById('proj-detail-panel').style.display = 'none';
            document.getElementById('proj-resize-handle').style.display = 'none';
            state.selectedId = null;
            callbacks.renderList?.();
        });
    }
};

window._costCancelAll = function() {
    window._costDirtyMap = {};
    var project = state.projects.find(function(p) { return p.id === state.selectedId; });
    if (project) callbacks.renderDetail?.(project);
    if (state.selectedId) _loadFinancialSummary(state.selectedId);
};

// ── Init: register all window handlers + beforeunload ──────────
function initCostHandlers() {
    window.addEventListener('beforeunload', function(e) {
        if (Object.keys(window._costDirtyMap || {}).length > 0) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
    // All window.* handlers are already assigned above at module level.
    // This function exists as a hook for the parent module to call during init.
}

// ── Exports ────────────────────────────────────────────────────
export { _loadFinancialSummary, _showExpenseForm, initCostHandlers };
