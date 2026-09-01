/**
 * crm-projects-finance.js — CRM 專案財務模組
 * 功能：cost staff loading, advances, payments, expenses, receipt browsing, share links
 */

import { state, callbacks, EXPENSE_CATEGORIES } from './crm-projects-state.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, moneyGate, today } from './crm-utils.js';
import { loadProjectStaff } from '../proposals/staff-view.js';

// ── Load Project Staff ──────────────────────────────────────────

/** CRM 這一側的人員配置入口 —— 畫的那份在 tabs/proposals/staff-view.js
 *  （專案頁用同一份，host / fetcher / 刪除動作由呼叫端注入，見該檔檔頭）。 */
async function _loadProjectStaff(projectId) {
    const host = document.getElementById('proj-staff-list');
    if (!host) return;
    return loadProjectStaff(projectId, {
        host, fetcher: _fetch,
        // 不注入 onRemove：刪除由元件自己做（它已經有確認與重畫）。
        // 注入的話會確認兩次 —— 元件問一次、被注入的那支再問一次。
    });
}

// ── Load Cost Staff ─────────────────────────────────────────────

async function _loadCostStaff(projectId) {
    var container = document.getElementById('proj-cost-staff');
    if (!container) return;
    // 「執行人員」畫的是 cost-lines（**錢的正本**：項目 × 金額 × 付款狀態），
    // 所以它跟著金額權走。沒授權時人員配置要看 tabs/proposals 那條路
    // （派工＝人的正本），不是把這張表閹掉。docs/MONEY_VISIBILITY.md §4
    if (moneyGate(container)) return;
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

            // 這張單是**誰的費用** —— 一般單就是收款人；代墊單的收款人是代墊人，
            // 費用歸屬在 advance_by。
            // 🔴 只比 payee_name 的話，代墊單永遠配不到費用歸屬人那一列：那一列
            // 會一直顯示三顆按鈕，同一筆費用可以再請一次款（而畫面上看不出來）。
            var _costOwner = function(p) { return p.advance_by || p.payee_name; };
            var matchedPayment = null;
            for (var pi = 0; pi < payments.length; pi++) {
                if (_costOwner(payments[pi]) === s.name && payments[pi].amount === subtotal) {
                    matchedPayment = payments[pi];
                    break;
                }
            }

            var statusHtml = '';
            if (matchedPayment) {
                // 代墊：錢是別人先掏的，這一列要標出來 —— 不標的話「已付款」
                // 看起來像公司付給這個人，而實際上公司欠的是代墊人。
                var advTag = matchedPayment.advance_by
                    ? '<span style="color:#fb923c;font-size:10px;margin-right:6px;" title="這筆費用由 '
                        + _esc(matchedPayment.payee_name || '') + ' 先代墊，公司要還的是他">'
                        + _esc(matchedPayment.payee_name || '') + ' 代墊</span>'
                    : '';
                if (matchedPayment.payment_status === '已付款') {
                    statusHtml = advTag + '<span style="color:#86efac;cursor:pointer;font-size:11px;" onclick="window._costViewPayment(\'' + matchedPayment.id + '\')">已付款 ✓</span>';
                } else {
                    statusHtml = advTag + '<span style="color:#fb923c;cursor:pointer;font-size:11px;" onclick="window._costViewPayment(\'' + matchedPayment.id + '\')">已請款</span>';
                }
            } else {
                var _eName = _esc(s.name).replace(/'/g, "\\'");
                var _eItems = _esc(itemNames.join('、')).replace(/'/g, "\\'");
                statusHtml = '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;" onclick="window._costCreatePayment(\'' + _eName + '\',' + subtotal + ',\'' + _eItems + '\',\'應付款\')">請款</button>' +
                    '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;margin-left:4px;" onclick="window._costCreatePayment(\'' + _eName + '\',' + subtotal + ',\'' + _eItems + '\',\'已付款\')">現金已付款</button>' +
                    // 費用已代墊（owner 2026-09-02）：這筆錢別人先掏了，公司要還的
                    // 是**代墊人**。開的是同一個請款視窗、預先勾好代墊 —— 那個機制
                    // 本來就在（modal 裡的勾選框），只是藏著沒人找得到（生產 0 筆）。
                    '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="font-size:10px;padding:1px 6px;margin-left:4px;" title="這筆費用由別人先代墊 —— 收款人改成代墊人，費用歸屬仍記在 ' + _eName + ' 身上" onclick="window._costCreatePayment(\'' + _eName + '\',' + subtotal + ',\'' + _eItems + '\',\'應付款\',true)">費用已代墊</button>';
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
    // 兩支端點都是錢（advances 與 expenses 都掛了 money_dep）。這裡漏掉閘門的
    // 話，畫面上半截（執行人員）寫「沒有權限」、下半截紅字「載入失敗」——
    // 它跟 _loadCostStaff 是同一個呼叫點一起叫的。
    if (moneyGate(container)) return;
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
        '<div class="crm-field crm-field-full"><label>日期 <span class="crm-required">*</span></label><input id="adv-modal-date" type="date" class="crm-input" value="' + today() + '" required></div>' +
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
                    request_date: document.getElementById('adv-modal-date').value || today(),
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
    // 收據資料夾下放到子表 — 用當前選中的子表 receipt_path
    var g = state.costGroups.find(function(x) { return x.id === state.selectedGroupId; });
    var receiptPath = g ? (g.receipt_path || '') : '';
    if (!receiptPath) {
        alert('此子表尚未設定收據資料夾。\n請點擊子表上方「⋯」→「✎ 編輯」→ 設定「收據資料夾」。');
        return;
    }
    if (window._isExternalAccess && typeof window.openNasBrowser === 'function') {
        await window.openNasBrowser({
            title: '收據資料夾 — ' + (g.name || ''),
            initialPath: receiptPath,
            destPath: receiptPath,
            showFiles: true,
        });
    } else {
        fetch('/api/v1/utils/open_folder', {
            method: 'POST',
            headers: Object.assign({ 'Content-Type': 'application/json' }, window.bearerHeader ? window.bearerHeader() : {}),
            body: JSON.stringify({ path: receiptPath }),
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
    // Fallback：沒子表狀態時退回專案層級連結。
    // 帶 token 不帶專案 id —— 理由同子表那顆（見 crm-projects-cost-groups._shareLink）：
    // 現場的人沒有帳號，而 `?project=` 那條要登入。
    _fetch('/expense-links', {
        method: 'POST',
        body: JSON.stringify({ kind: 'project', target_id: state.selectedId }),
    }).then(function(d) {
        var url = location.origin + '/expense.html?t=' + encodeURIComponent(d.token);
        navigator.clipboard.writeText(url).then(function() {
            alert('雜支登記連結已複製（免登入，可直接給現場人員）：\n' + url);
        }).catch(function() {
            prompt('請複製連結：', url);
        });
    }).catch(function(e) {
        alert('發連結失敗：' + (e.message || e));
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

/** 請款／現金已付款／費用已代墊 —— 同一個視窗。
 *
 *  `advanced`＝從「費用已代墊」那顆進來：代墊區預先展開、代墊人下拉先聚焦。
 *  🔴 不另建一套代墊流程 —— 這個機制本來就在（視窗裡的勾選框），只是藏著，
 *  生產庫 0 筆用過。再刻一份的話，「誰去領這筆錢」就會有兩條規則。
 */
window._costCreatePayment = function(payeeName, amount, summary, status, advanced) {
    if (!state.selectedId) return;
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var projName = proj ? proj.name : '';
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = '<div class="crm-modal" style="max-width:420px;">' +
        '<div class="crm-modal-header"><h3>' + (advanced ? '費用已代墊' : status === '已付款' ? '現金已付款' : '請款') + '</h3>' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
        '<div class="crm-modal-body">' +
        '<div class="crm-field" style="margin-bottom:8px;"><label>專案</label><input class="crm-input" value="' + _esc(projName) + '" disabled style="opacity:0.6;"></div>' +
        '<div class="crm-form-grid">' +
        '<div class="crm-field crm-field-full"><label>人員 <span class="crm-required">*</span></label><input id="pay-modal-payee" class="crm-input" value="' + _esc(payeeName) + '" required></div>' +
        '<div class="crm-field crm-field-full"><label>金額 <span class="crm-required">*</span></label><input id="pay-modal-amount" type="number" class="crm-input" value="' + amount + '" required></div>' +
        // 代墊：收款人換成代墊人，費用歸屬仍是原本那個人（送出時才對調 —— 見下方）
        '<div class="crm-field crm-field-full" style="display:flex;align-items:center;gap:8px;"><label style="display:flex;align-items:center;gap:4px;cursor:pointer;margin:0;flex-shrink:0;"><input type="checkbox" id="pay-modal-advance"' + (advanced ? ' checked' : '') + ' onchange="document.getElementById(\'pay-modal-advance-by\').style.display=this.checked?\'\':\'none\'"> 代墊</label><select id="pay-modal-advance-by" class="crm-input" style="' + (advanced ? '' : 'display:none;') + 'flex:1;"><option value="">— 代墊人（實際收款人）—</option>' +
        state.staffList.map(function(s) { return '<option value="' + _esc(s.name) + '">' + _esc(s.name) + '</option>'; }).join('') +
        '</select></div>' +
        (advanced ? '<div class="crm-field crm-field-full" style="color:#fb923c;font-size:11px;margin-top:-4px;">這筆錢由代墊人先掏 —— 公司要還的是<b>代墊人</b>，費用歸屬仍記在 ' + _esc(payeeName) + ' 身上（不影響連結私帳的收入鏡射）。</div>' : '') +
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
        var isAdvance = document.getElementById('pay-modal-advance').checked;
        var advanceBy = isAdvance ? document.getElementById('pay-modal-advance-by').value : '';
        var originalPayee = document.getElementById('pay-modal-payee').value;
        // 🔴 勾了代墊卻沒選人＝靜靜變成一張付給原本那個人的單（代墊人根本拿不到
        // 錢，而畫面上看起來一切正常）。當場擋下來。
        if (isAdvance && !advanceBy) {
            alert('請選代墊人 —— 公司要還的是先掏錢的那個人');
            btn.disabled = false; btn.textContent = '確定'; return;
        }
        if (isAdvance && advanceBy === originalPayee) {
            alert('代墊人跟費用歸屬是同一個人 —— 那就是一般請款，不必勾代墊');
            btn.disabled = false; btn.textContent = '確定'; return;
        }
        try {
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
                    request_date: today(),
                    payment_status: status,
                    payment_date: status === '已付款' ? today() : '',
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
            // 🔴 這一欄裝的是**費用歸屬人**（原本該收這筆的人），不是代墊人 ——
            // 代墊人是 payee_name（他才是實際去領錢的）。標籤原本寫反了：把
            // 費用歸屬人標成「代墊人（實際收款人）」，看的人會以為錢匯給他。
            (p.advance_by ? '<div class="crm-detail-prop"><div class="crm-prop-label">代墊</div><div class="crm-prop-value" style="color:#fb923c;">' + _esc(p.payee_name || '') + ' 代墊　·　費用歸屬 ' + _esc(p.advance_by) + '</div></div>' : '') +
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
        if (newStatus === '已付款') existing.payment_date = today();
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
