/**
 * crm-projects-finance.js — CRM 專案財務模組
 * 功能：cost staff loading, advances, payments, expenses, receipt browsing, share links
 */

import { state, callbacks, EXPENSE_CATEGORIES } from './crm-projects-state.js';
import { crmFetch as _fetch, esc as _esc, fmtNum } from './crm-utils.js';

// ── Load Project Staff ──────────────────────────────────────────

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
                <span class="quote-item-price">$${fmtNum(r.rate)}</span>
                <span class="quote-item-amount">$${fmtNum(r.cost)}</span>
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:2px 6px;" onclick="window._projRemoveStaff('${r.id}','${projectId}')">&#x2715;</button>
            </div>
        `).join('') + `<div style="text-align:right;font-weight:700;padding:8px 0;color:#e0e0e0;">內部成本合計: $${fmtNum(totalCost)}</div>`;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Load Cost Staff ─────────────────────────────────────────────

async function _loadCostStaff(projectId) {
    var container = document.getElementById('proj-cost-staff');
    if (!container) return;
    try {
        var data = await _fetch('/projects/' + projectId + '/cost-lines');
        var lines = data.cost_lines || [];
        // Group by actual_staff_id
        var staffMap = {};
        for (var i = 0; i < lines.length; i++) {
            var ln = lines[i];
            if (!ln.actual_staff_id) continue;
            var key = ln.actual_staff_id;
            if (!staffMap[key]) {
                staffMap[key] = { name: ln.actual_staff_name || '未知', items: [] };
            }
            staffMap[key].items.push({ item_name: ln.item_name, amount: ln.actual_amount || 0 });
        }
        var keys = Object.keys(staffMap);
        if (keys.length === 0) {
            container.innerHTML = '<div class="crm-empty" style="padding:8px 0;font-size:12px;">尚無執行人員</div>';
            return;
        }
        // Fetch payment requests for this project to check payment status
        var payments = [];
        try {
            var payData = await _fetch('/payments?project_id=' + projectId);
            payments = payData.payments || [];
        } catch(_) {}

        var proj = state.projects.find(function(p) { return p.id === projectId; });
        var projName = proj ? proj.name : '';
        var grandTotal = 0;
        var html = '<div style="font-size:12px;">';
        for (var k = 0; k < keys.length; k++) {
            var s = staffMap[keys[k]];
            var subtotal = 0;
            var itemNames = [];
            for (var j = 0; j < s.items.length; j++) {
                subtotal += s.items[j].amount;
                itemNames.push(s.items[j].item_name);
            }
            grandTotal += subtotal;

            // Check payment status for this person
            var matchedPayment = null;
            for (var pi = 0; pi < payments.length; pi++) {
                if (payments[pi].payee_name === s.name && payments[pi].amount === subtotal) {
                    matchedPayment = payments[pi];
                    break;
                }
            }

            var statusHtml = '';
            if (matchedPayment) {
                if (matchedPayment.payment_status === '已付款') {
                    statusHtml = '<span style="color:#86efac;cursor:pointer;font-size:11px;" onclick="window._costViewPayment(\'' + matchedPayment.id + '\')">已付款 ✓</span>';
                } else {
                    statusHtml = '<span style="color:#fb923c;cursor:pointer;font-size:11px;" onclick="window._costViewPayment(\'' + matchedPayment.id + '\')">已請款</span>';
                }
            } else {
                var _eName = _esc(s.name).replace(/'/g, "\\'");
                var _eItems = _esc(itemNames.join('、')).replace(/'/g, "\\'");
                statusHtml = '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;" onclick="window._costCreatePayment(\'' + _eName + '\',' + subtotal + ',\'' + _eItems + '\',\'應付款\')">請款</button>' +
                    '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;margin-left:4px;" onclick="window._costCreatePayment(\'' + _eName + '\',' + subtotal + ',\'' + _eItems + '\',\'已付款\')">現金已付款</button>';
            }

            html += '<div style="display:flex;align-items:center;padding:6px 0;border-bottom:1px solid #2e2e2e;gap:8px;">';
            html += '<span style="width:80px;font-weight:600;color:#d1d5db;flex-shrink:0;">' + _esc(s.name) + '</span>';
            html += '<span style="flex:1;color:#6b7280;font-size:11px;">' + _esc(itemNames.join('、')) + '</span>';
            html += '<span style="width:80px;text-align:right;font-weight:600;color:#e0e0e0;flex-shrink:0;">$' + fmtNum(subtotal) + '</span>';
            html += '<span style="flex-shrink:0;">' + statusHtml + '</span>';
            html += '</div>';
        }
        html += '<div style="display:flex;padding:6px 0;border-top:2px solid #3a3a3a;">';
        html += '<span style="flex:1;font-weight:700;color:#e0e0e0;">合計</span>';
        html += '<span style="width:80px;text-align:right;font-weight:700;color:#e0e0e0;">$' + fmtNum(grandTotal) + '</span>';
        html += '<span style="width:120px;"></span>';
        html += '</div></div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Load Advances ───────────────────────────────────────────────

async function _loadAdvances(projectId) {
    var container = document.getElementById('proj-advance-list');
    if (!container) return;
    try {
        var [advData, expData] = await Promise.all([
            _fetch('/payments/advances?returned=-1&project_id=' + projectId),
            _fetch('/projects/' + projectId + '/expenses'),
        ]);
        var advances = advData.advances || [];
        var allExpenses = expData.expenses || [];
        if (advances.length === 0) {
            container.innerHTML = '<div class="crm-empty" style="padding:8px 0;font-size:12px;">尚無預支款</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < advances.length; i++) {
            var a = advances[i];
            var balance = (a.balance != null) ? a.balance : a.amount - a.expense_total;
            var balanceColor = balance > 0 ? '#fb923c' : balance < 0 ? '#fca5a5' : '#86efac';

            // Payment status (發款)
            var isPaid = a.is_paid;
            var payStatusText = isPaid ? '已發款' : '未發款';
            var payStatusColor = isPaid ? '#86efac' : '#6b7280';
            // Return status (收款) — 標籤只顯示狀態文字，不帶金額
            var isReturned = a.is_returned;
            var returnStatusText = a.is_settled ? '已結清' : isReturned ? '已收款' : (a.expense_total > 0 ? '需還款' : '待收款');
            var returnStatusColor = a.is_settled ? '#86efac' : isReturned ? '#fb923c' : balanceColor;

            var canEdit = (a.cash_entries || []).length === 0 && a.expense_total === 0;
            html += '<div style="background:#1a1a1a;border:1px solid #2e2e2e;border-radius:8px;padding:10px;margin-bottom:8px;">';
            // Header row
            html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">';
            html += '<span style="font-weight:600;color:#d1d5db;font-size:13px;">' + _esc(a.payee_name) + '</span>';
            html += '<span style="font-size:12px;color:#6b7280;">預支 $' + fmtNum(a.amount) + '</span>';
            if (canEdit) {
                html += '<span style="font-size:11px;color:#6b7280;cursor:pointer;" onclick="window._advEditAmount(\'' + a.id + '\',' + a.amount + ')" title="修改金額">✏️</span>';
            }
            html += '<span style="flex:1;"></span>';
            if (canEdit) {
                html += '<span style="font-size:11px;color:#6b7280;cursor:pointer;margin-right:4px;" onclick="window._advDeleteAdvance(\'' + a.id + '\',\'' + _esc(a.payee_name).replace(/'/g, "\\'") + '\')" title="刪除預支">🗑</span>';
            }
            html += '<span style="font-size:10px;color:' + payStatusColor + ';border:1px solid ' + payStatusColor + ';border-radius:4px;padding:1px 6px;cursor:pointer;" onclick="window._costViewPayment(\'' + a.id + '\')">' + payStatusText + '</span>';
            html += '<span style="font-size:10px;color:' + returnStatusColor + ';border:1px solid ' + returnStatusColor + ';border-radius:4px;padding:1px 6px;cursor:pointer;" onclick="window._costViewPayment(\'' + a.id + '\')">' + returnStatusText + '</span>';
            html += '</div>';
            // Expense details — filter by advance_id
            var payeeExpenses = allExpenses.filter(function(e) { return e.advance_id === a.id; });
            if (payeeExpenses.length > 0) {
                html += '<div style="margin:6px 0;border-top:1px solid #2e2e2e;padding-top:6px;">';
                for (var ei = 0; ei < payeeExpenses.length; ei++) {
                    var ex = payeeExpenses[ei];
                    var exLabel = ex.sub_item ? _esc(ex.category) + ' · ' + _esc(ex.sub_item) : _esc(ex.category);
                    html += '<div style="display:flex;align-items:center;padding:2px 0;font-size:11px;color:#9ca3af;">';
                    html += '<span style="flex:1;">' + exLabel + '</span>';
                    html += '<span>$' + fmtNum(ex.actual) + '</span>';
                    html += '<span style="margin-left:8px;cursor:pointer;color:#6b7280;font-size:10px;" onclick="window._advUnlinkExpense(\'' + ex.id + '\')" title="解除關聯">✕</span>';
                    html += '</div>';
                }
                html += '</div>';
            }
            // 收支明細（發款/收款記錄）
            var cashEntries = a.cash_entries || [];
            if (cashEntries.length > 0) {
                html += '<div style="margin:6px 0;border-top:1px solid #2e2e2e;padding-top:6px;">';
                for (var ci = 0; ci < cashEntries.length; ci++) {
                    var ce = cashEntries[ci];
                    var ceColor = ce.type === '發款' ? '#fca5a5' : '#86efac';
                    var ceAmt = ce.type === '發款' ? ce.expense : ce.deposit;
                    html += '<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:11px;color:#9ca3af;">';
                    html += '<span><span style="color:' + ceColor + ';font-size:10px;margin-right:4px;">' + ce.type + '</span>' + _esc(ce.summary) + (ce.entry_date ? ' <span style="color:#4b5563;">' + ce.entry_date + '</span>' : '') + '</span>';
                    html += '<span style="color:' + ceColor + ';">$' + fmtNum(ceAmt) + '</span>';
                    html += '</div>';
                }
                html += '</div>';
            }
            // Summary row
            html += '<div style="display:flex;align-items:center;gap:12px;font-size:11px;color:#6b7280;border-top:1px solid #2e2e2e;padding-top:6px;margin-top:4px;">';
            html += '<span>支出 $' + fmtNum(a.expense_total) + '</span>';
            var balanceLabel = balance > 0 ? '餘額 $' + fmtNum(balance) : balance < 0 ? '超支 $' + fmtNum(Math.abs(balance)) : '已結清';
            html += '<span style="color:' + balanceColor + ';font-weight:600;">' + balanceLabel + '</span>';
            html += '<span style="flex:1;"></span>';
            if (!a.is_settled) {
                html += '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;" onclick="window._advAddExpense(\'' + _esc(a.payee_name).replace(/'/g, "\\'") + '\',\'' + a.id + '\')">+ 登記支出</button>';
                html += '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;margin-left:4px;" onclick="window._advLinkExpenses(\'' + a.id + '\')">關聯既有</button>';
                html += '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;margin-left:4px;" onclick="window._advShareLink(\'' + a.id + '\')">分享連結</button>';
            }
            html += '</div>';
            html += '</div>';
        }
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Window Handlers ─────────────────────────────────────────────

window._costCreateAdvance = function() {
    if (!state.selectedId) return;
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var projName = proj ? proj.name : '';
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = '<div class="crm-modal" style="max-width:420px;">' +
        '<div class="crm-modal-header"><h3>新增預支款</h3>' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
        '<div class="crm-modal-body">' +
        '<div class="crm-field" style="margin-bottom:8px;"><label>專案</label><input class="crm-input" value="' + _esc(projName) + '" disabled style="opacity:0.6;"></div>' +
        '<div class="crm-form-grid">' +
        '<div class="crm-field crm-field-full"><label>預支人 <span class="crm-required">*</span></label><select id="adv-modal-payee" class="crm-input" required><option value="">— 選擇人員 —</option>' +
        state.staffList.map(function(s) { return '<option value="' + _esc(s.name) + '">' + _esc(s.name) + ' (' + _esc(s.role || '') + ')</option>'; }).join('') +
        '</select></div>' +
        '<div class="crm-field crm-field-full"><label>預支金額 <span class="crm-required">*</span></label><input id="adv-modal-amount" type="number" class="crm-input" required></div>' +
        '<div class="crm-field crm-field-full"><label>日期 <span class="crm-required">*</span></label><input id="adv-modal-date" type="date" class="crm-input" value="' + new Date().toISOString().substring(0, 10) + '" required></div>' +
        '<div class="crm-field crm-field-full"><label>應付款月 <span class="crm-required">*</span></label><input id="adv-modal-month" type="month" class="crm-input" required></div>' +
        '<div class="crm-field crm-field-full"><label>備註</label><input id="adv-modal-notes" class="crm-input" placeholder="選填"></div>' +
        '</div></div>' +
        '<div class="crm-modal-footer">' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">取消</button>' +
        '<button id="adv-modal-submit" class="crm-btn crm-btn-primary">確定</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('adv-modal-submit').addEventListener('click', async function() {
        var payee = document.getElementById('adv-modal-payee').value.trim();
        var amount = parseInt(document.getElementById('adv-modal-amount').value) || 0;
        if (!payee) { alert('請填寫預支人'); return; }
        if (!amount) { alert('請填寫金額'); return; }
        this.disabled = true; this.textContent = '處理中...';
        try {
            await _fetch('/payments', {
                method: 'POST', body: JSON.stringify({
                    payee_name: payee,
                    amount: amount,
                    summary: '預支款 — ' + projName,
                    notes: document.getElementById('adv-modal-notes').value,
                    project_id: state.selectedId,
                    project_label: projName,
                    category: '專案雜支',
                    is_advance: 1,
                    payment_status: '應付款',
                    planned_month: document.getElementById('adv-modal-month').value || '',
                    request_date: document.getElementById('adv-modal-date').value || new Date().toISOString().substring(0, 10),
                })
            });
            overlay.remove();
            _loadAdvances(state.selectedId);
        } catch (e) {
            alert('建立失敗：' + e.message);
            this.disabled = false; this.textContent = '確定';
        }
    });
};

window._advDeleteAdvance = async function(advanceId, payeeName) {
    if (!confirm('確定刪除「' + payeeName + '」的預支款？')) return;
    try {
        await _fetch('/payments/' + advanceId, { method: 'DELETE' });
        if (state.selectedId) _loadAdvances(state.selectedId);
    } catch (e) { alert('刪除失敗：' + e.message); }
};

window._advEditAmount = async function(advanceId, currentAmount) {
    var input = prompt('修改預支金額：', currentAmount);
    if (input === null) return;
    var newAmount = parseInt(input);
    if (!newAmount || newAmount <= 0) { alert('請輸入有效金額'); return; }
    try {
        var adv = await _fetch('/payments/' + advanceId);
        adv.amount = newAmount;
        delete adv.id; delete adv.created_at; delete adv.updated_at; delete adv.project_name;
        await _fetch('/payments/' + advanceId, { method: 'PUT', body: JSON.stringify(adv) });
        if (state.selectedId) _loadAdvances(state.selectedId);
    } catch (e) { alert('修改失敗：' + e.message); }
};

window._projBrowseReceipts = async function() {
    if (!state.selectedId) return;
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var receiptPath = proj ? (proj.receipt_path || '') : '';
    if (typeof window.openNasBrowser === 'function') {
        await window.openNasBrowser({
            title: '收據資料夾',
            initialPath: receiptPath,
            destPath: receiptPath,
            showFiles: true,
        });
    }
};

window._projShareExpenseLink = function() {
    if (!state.selectedId) return;
    // 優先使用當前選中子表的專屬連結（與切換卡上的 🔗 按鈕同邏輯 + toast）
    if (state.selectedGroupId && typeof window._cgShareLink === 'function') {
        window._cgShareLink(state.selectedGroupId, null);
        return;
    }
    // Fallback：沒子表狀態時退回專案層級連結
    var url = location.origin + '/expense.html?project=' + state.selectedId;
    navigator.clipboard.writeText(url).then(function() {
        alert('公開雜支登記連結已複製：\n' + url);
    }).catch(function() {
        prompt('請複製連結：', url);
    });
};

window._advShareLink = function(advanceId) {
    var url = location.origin + '/advance-expense.html?id=' + advanceId;
    navigator.clipboard.writeText(url).then(function() {
        alert('連結已複製：\n' + url);
    }).catch(function() {
        prompt('請複製連結：', url);
    });
};

window._advUnlinkExpense = async function(expenseId) {
    if (!confirm('確定解除此支出的預支關聯？')) return;
    try {
        var exps = await _fetch('/projects/' + state.selectedId + '/expenses');
        var ex = (exps.expenses || []).find(function(e) { return e.id === expenseId; });
        if (!ex) { alert('找不到此支出'); return; }
        await _fetch('/project-expenses/' + expenseId, { method: 'PUT', body: JSON.stringify({
            category: ex.category, estimated: ex.estimated || 0, actual: ex.actual || 0,
            sub_item: ex.sub_item || '', payee: ex.payee || '', advance_id: '', notes: ex.notes || ''
        })});
        if (state.selectedId) { _loadAdvances(state.selectedId); callbacks.loadFinancialSummary?.(state.selectedId); }
    } catch (e) { alert('解除失敗：' + e.message); }
};

window._advLinkExpenses = async function(advanceId) {
    if (!state.selectedId) return;
    var expData;
    try { expData = await _fetch('/projects/' + state.selectedId + '/expenses'); } catch(_) { return; }
    var orphans = (expData.expenses || []).filter(function(e) { return !e.advance_id; });
    if (orphans.length === 0) { alert('沒有未綁定的支出'); return; }
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    var listHtml = '';
    for (var i = 0; i < orphans.length; i++) {
        var o = orphans[i];
        var label = o.sub_item ? _esc(o.category) + ' · ' + _esc(o.sub_item) : _esc(o.category);
        listHtml += '<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid #2e2e2e;border-radius:6px;margin-bottom:4px;cursor:pointer;background:#1a1a1a;">';
        listHtml += '<input type="checkbox" value="' + o.id + '">';
        listHtml += '<span style="flex:1;color:#d1d5db;font-size:12px;">' + label + (o.payee ? ' <span style="color:#6b7280;">(' + _esc(o.payee) + ')</span>' : '') + '</span>';
        listHtml += '<span style="color:#9ca3af;font-size:12px;">$' + fmtNum(o.actual) + '</span>';
        listHtml += '</label>';
    }
    overlay.innerHTML = '<div class="crm-modal" style="max-width:420px;">' +
        '<div class="crm-modal-header"><h3>關聯既有支出</h3>' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
        '<div class="crm-modal-body"><div style="font-size:12px;color:#6b7280;margin-bottom:8px;">勾選要歸入此預支的支出：</div>' +
        '<div id="adv-link-list">' + listHtml + '</div></div>' +
        '<div class="crm-modal-footer">' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">取消</button>' +
        '<button id="adv-link-submit" class="crm-btn crm-btn-primary">確定</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('adv-link-submit').addEventListener('click', async function() {
        var checks = overlay.querySelectorAll('#adv-link-list input[type=checkbox]:checked');
        var ids = [];
        for (var j = 0; j < checks.length; j++) ids.push(checks[j].value);
        if (ids.length === 0) { alert('請勾選至少一筆'); return; }
        this.disabled = true; this.textContent = '處理中...';
        try {
            await _fetch('/project-expenses/link-advance', {
                method: 'PATCH', body: JSON.stringify({ expense_ids: ids, advance_id: advanceId })
            });
            overlay.remove();
            _loadAdvances(state.selectedId);
            callbacks.loadFinancialSummary?.(state.selectedId);
        } catch (e) {
            alert('關聯失敗：' + e.message);
            this.disabled = false; this.textContent = '確定';
        }
    });
};

window._advAddExpense = function(payeeName, advanceId) {
    if (!state.selectedId) return;
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var projName = proj ? proj.name : '';
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = '<div class="crm-modal" style="max-width:420px;">' +
        '<div class="crm-modal-header"><h3>預支支出登記</h3>' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
        '<div class="crm-modal-body">' +
        '<div class="crm-field" style="margin-bottom:8px;"><label>專案</label><input class="crm-input" value="' + _esc(projName) + '" disabled style="opacity:0.6;"></div>' +
        '<div class="crm-field" style="margin-bottom:8px;"><label>預支人</label><input class="crm-input" value="' + _esc(payeeName) + '" disabled style="opacity:0.6;"></div>' +
        '<div class="crm-form-grid">' +
        '<div class="crm-field crm-field-full"><label>類別 <span class="crm-required">*</span></label><select id="adv-exp-cat" class="crm-input">' + EXPENSE_CATEGORIES.map(c => '<option value="' + c + '">' + c + '</option>').join('') + '</select></div>' +
        '<div class="crm-field crm-field-full"><label>細項</label><input id="adv-exp-sub" type="text" class="crm-input" placeholder="如：高鐵來回"></div>' +
        '<div class="crm-field crm-field-full"><label>金額 <span class="crm-required">*</span></label><input id="adv-exp-amt" type="number" class="crm-input" min="0"></div>' +
        '<div class="crm-field crm-field-full"><label>備註</label><input id="adv-exp-notes" type="text" class="crm-input" placeholder="選填"></div>' +
        '</div></div>' +
        '<div class="crm-modal-footer">' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">取消</button>' +
        '<button id="adv-exp-submit" class="crm-btn crm-btn-primary">確定</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('adv-exp-submit').addEventListener('click', async function() {
        var amt = parseInt(document.getElementById('adv-exp-amt').value) || 0;
        if (!amt) { alert('請填寫金額'); return; }
        this.disabled = true; this.textContent = '處理中...';
        try {
            await _fetch('/projects/' + state.selectedId + '/expenses', {
                method: 'POST', body: JSON.stringify({
                    category: document.getElementById('adv-exp-cat').value,
                    sub_item: document.getElementById('adv-exp-sub').value,
                    estimated: 0,
                    actual: amt,
                    payee: payeeName,
                    advance_id: advanceId || '',
                    notes: document.getElementById('adv-exp-notes').value,
                    cost_group_id: state.selectedGroupId,
                })
            });
            overlay.remove();
            _loadAdvances(state.selectedId);
            callbacks.loadFinancialSummary?.(state.selectedId);
        } catch (e) {
            alert('登記失敗：' + e.message);
            this.disabled = false; this.textContent = '確定';
        }
    });
};

window._costCreatePayment = function(payeeName, amount, summary, status) {
    if (!state.selectedId) return;
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var projName = proj ? proj.name : '';
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = '<div class="crm-modal" style="max-width:420px;">' +
        '<div class="crm-modal-header"><h3>' + (status === '已付款' ? '現金已付款' : '請款') + '</h3>' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
        '<div class="crm-modal-body">' +
        '<div class="crm-field" style="margin-bottom:8px;"><label>專案</label><input class="crm-input" value="' + _esc(projName) + '" disabled style="opacity:0.6;"></div>' +
        '<div class="crm-form-grid">' +
        '<div class="crm-field crm-field-full"><label>人員 <span class="crm-required">*</span></label><input id="pay-modal-payee" class="crm-input" value="' + _esc(payeeName) + '" required></div>' +
        '<div class="crm-field crm-field-full"><label>金額 <span class="crm-required">*</span></label><input id="pay-modal-amount" type="number" class="crm-input" value="' + amount + '" required></div>' +
        '<div class="crm-field crm-field-full" style="display:flex;align-items:center;gap:8px;"><label style="display:flex;align-items:center;gap:4px;cursor:pointer;margin:0;flex-shrink:0;"><input type="checkbox" id="pay-modal-advance" onchange="document.getElementById(\'pay-modal-advance-by\').style.display=this.checked?\'\':\' none\'"> 代墊</label><select id="pay-modal-advance-by" class="crm-input" style="display:none;flex:1;"><option value="">— 代墊人 —</option>' +
        state.staffList.map(function(s) { return '<option value="' + _esc(s.name) + '">' + _esc(s.name) + '</option>'; }).join('') +
        '</select></div>' +
        '<div class="crm-field crm-field-full"><label>摘要 <span class="crm-required">*</span></label><input id="pay-modal-summary" class="crm-input" value="' + _esc(summary) + '" required></div>' +
        '<div class="crm-field crm-field-full"><label>報支項目 <span class="crm-required">*</span></label><select id="pay-modal-payee-type" class="crm-input" required><option value="">—</option><option value="內部人員">內部人員</option><option value="現金">現金</option><option value="勞報">勞報</option><option value="核銷">核銷</option></select></div>' +
        '<div class="crm-field crm-field-full"><label>預計付款月 <span class="crm-required">*</span></label><input id="pay-modal-month" type="month" class="crm-input" required></div>' +
        '<div class="crm-field crm-field-full"><label>備註</label><input id="pay-modal-notes" class="crm-input" placeholder="選填"></div>' +
        '</div></div>' +
        '<div class="crm-modal-footer">' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">取消</button>' +
        '<button id="pay-modal-submit" class="crm-btn crm-btn-primary">確定</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('pay-modal-submit').addEventListener('click', async function() {
        var btn = this;
        btn.disabled = true; btn.textContent = '處理中...';
        var fields = ['pay-modal-payee','pay-modal-amount','pay-modal-summary','pay-modal-payee-type','pay-modal-month'];
        var labels = ['人員','金額','摘要','報支項目','預計付款月'];
        for (var fi = 0; fi < fields.length; fi++) {
            var el = document.getElementById(fields[fi]);
            if (!el || !el.value.trim()) { alert(labels[fi] + ' 為必填'); btn.disabled = false; btn.textContent = '確定'; return; }
        }
        try {
            var isAdvance = document.getElementById('pay-modal-advance').checked;
            var advanceBy = isAdvance ? document.getElementById('pay-modal-advance-by').value : '';
            var originalPayee = document.getElementById('pay-modal-payee').value;
            await _fetch('/payments', {
                method: 'POST', body: JSON.stringify({
                    payee_name: isAdvance && advanceBy ? advanceBy : originalPayee,
                    amount: parseInt(document.getElementById('pay-modal-amount').value) || 0,
                    summary: document.getElementById('pay-modal-summary').value,
                    notes: document.getElementById('pay-modal-notes').value + (isAdvance ? ' (代墊：費用歸屬 ' + originalPayee + ')' : ''),
                    project_id: state.selectedId,
                    project_label: projName,
                    category: document.getElementById('pay-modal-payee-type').value || '專案雜支',
                    payee_type: document.getElementById('pay-modal-payee-type').value || '',
                    planned_month: document.getElementById('pay-modal-month').value || '',
                    advance_by: isAdvance ? originalPayee : '',
                    request_date: new Date().toISOString().substring(0, 10),
                    payment_status: status,
                    payment_date: status === '已付款' ? new Date().toISOString().substring(0, 10) : '',
                })
            });
            overlay.remove();
            _loadCostStaff(state.selectedId);
        } catch (e) {
            alert('建立失敗：' + e.message);
            btn.disabled = false; btn.textContent = '確定';
        }
    });
};

window._costViewPayment = async function(paymentId) {
    try {
        var p = await _fetch('/payments/' + paymentId);
        var statusColor = p.payment_status === '已付款' ? '#86efac' : '#fb923c';
        var overlay = document.createElement('div');
        overlay.className = 'crm-modal-overlay';
        overlay.style.display = 'flex';
        overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
        overlay.innerHTML = '<div class="crm-modal" style="max-width:400px;">' +
            '<div class="crm-modal-header"><h3>請款單詳情</h3>' +
            '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
            '<div class="crm-modal-body" style="font-size:13px;">' +
            '<div class="crm-detail-prop"><div class="crm-prop-label">請款人</div><div class="crm-prop-value">' + _esc(p.payee_name) + '</div></div>' +
            '<div class="crm-detail-prop"><div class="crm-prop-label">金額</div><div class="crm-prop-value" style="font-weight:700;">$' + fmtNum(p.amount) + '</div></div>' +
            '<div class="crm-detail-prop"><div class="crm-prop-label">摘要</div><div class="crm-prop-value">' + _esc(p.summary) + '</div></div>' +
            (p.payee_type ? '<div class="crm-detail-prop"><div class="crm-prop-label">報支項目</div><div class="crm-prop-value">' + _esc(p.payee_type) + '</div></div>' : '') +
            (p.advance_by ? '<div class="crm-detail-prop"><div class="crm-prop-label">代墊人</div><div class="crm-prop-value" style="color:#fb923c;">' + _esc(p.advance_by) + '（實際收款人）</div></div>' : '') +
            (p.request_date ? '<div class="crm-detail-prop"><div class="crm-prop-label">請款日期</div><div class="crm-prop-value">' + p.request_date.substring(0, 10) + '</div></div>' : '') +
            '<div class="crm-detail-prop"><div class="crm-prop-label">預計付款月</div><div class="crm-prop-value">' + (p.planned_month || '未設定') + '</div></div>' +
            '<div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value" style="color:' + statusColor + ';">' + _esc(p.payment_status) + '</div></div>' +
            (p.payment_date ? '<div class="crm-detail-prop"><div class="crm-prop-label">付款日期</div><div class="crm-prop-value">' + p.payment_date.substring(0, 10) + '</div></div>' : '') +
            (p.notes ? '<div class="crm-detail-prop"><div class="crm-prop-label">備註</div><div class="crm-prop-value">' + _esc(p.notes) + '</div></div>' : '') +
            '</div>' +
            '<div class="crm-modal-footer">' +
            '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">關閉</button></div>' +
            '</div>';
        document.body.appendChild(overlay);
    } catch (e) { alert('載入失敗：' + e.message); }
};

window._costUpdatePaymentStatus = async function(paymentId, newStatus) {
    try {
        // GET existing data first, then PUT with updated status
        var existing = await _fetch('/payments/' + paymentId);
        existing.payment_status = newStatus;
        if (newStatus === '已付款') existing.payment_date = new Date().toISOString().substring(0, 10);
        delete existing.id;
        delete existing.created_at;
        delete existing.updated_at;
        await _fetch('/payments/' + paymentId, { method: 'PUT', body: JSON.stringify(existing) });
        var overlay = document.querySelector('.crm-modal-overlay');
        if (overlay) overlay.remove();
        if (state.selectedId) _loadCostStaff(state.selectedId);
    } catch (e) { alert('更新失敗：' + e.message); }
};

// ── Init ────────────────────────────────────────────────────────

function initFinanceHandlers() {
    // All window.* handlers are already assigned at module load time above.
    // This function serves as a hook for the main module to call after import,
    // ensuring the side effects (window.* assignments) have executed.
}

// ── Exports ─────────────────────────────────────────────────────

export { _loadCostStaff, _loadAdvances, _loadProjectStaff, initFinanceHandlers };
