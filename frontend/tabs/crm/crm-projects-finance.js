/**
 * crm-projects-finance.js — CRM 專案財務模組
 * 功能：payments, expenses, receipt browsing, share links（執行人員／預支款畫面在 crm-projects-pay.js）
 */

import { copyText } from '../../js/shared/utils.js';
import { state } from './crm-projects-state.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, today, groupCostStaff, hasModule, canSeeMoney } from './crm-utils.js';
import * as _U from './crm-utils.js';   // permDeniedMsg 走命名空間（舊快取的 crm-utils 沒有它，named import 會炸整頁）
import { loadProjectStaff } from '../proposals/staff-view.js';

// 錢流（請款單／預支款）的寫入要 crm_invoices＋money_view（RBAC 稽核第二批）。
// 這幾支 window.* 是全域入口（收付款分頁、預算結算的雜支列都會叫），所以入口本身也擋一次，
// 不只靠呼叫端不畫鈕 —— 舊快取的分頁 js 還是會畫出那顆鈕。
const _INVOICE_NEED = '財務管理＋金額檢視';
const _canInvoice = () => hasModule('crm_invoices') && canSeeMoney();
function _denyInvoice() {
    alert(_U.permDeniedMsg?.(_INVOICE_NEED) ?? '權限不足：需要「財務管理＋金額檢視」權限，請管理員在使用者管理開通');
}

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
        canAssign: hasModule('crm_projects'),   // 派工寫入是 crm_projects 的事，沒鑰匙不畫新增／移除
    });
}

// ── Window Handlers ─────────────────────────────────────────────

window._costCreateAdvance = function() {
    if (!state.selectedId) return;
    if (!_canInvoice()) { _denyInvoice(); return; }
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var projName = proj ? proj.name : '';
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.dataset.dynamic = '1';        // _costAfterPay 只收這種動態長出來的
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
            _refreshPayIfOpen();
        } catch (e) {
            alert(_U.permDeniedMsg?.(_INVOICE_NEED, e) ?? ('建立失敗：' + e.message));
            this.disabled = false; this.textContent = '確定';
        }
    });
};

window._advDeleteAdvance = async function(advanceId, payeeName) {
    if (!_canInvoice()) { _denyInvoice(); return; }
    if (!confirm('確定刪除「' + payeeName + '」的預支款？')) return;
    try {
        await _fetch('/payments/' + advanceId, { method: 'DELETE' });
        _refreshPayIfOpen();
    } catch (e) { alert(_U.permDeniedMsg?.(_INVOICE_NEED, e) ?? ('刪除失敗：' + e.message)); }
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
        // 🔴 走 copyText 不要裸用 navigator.clipboard：內網是 http://192.168.1.x，
        // 非安全來源上 `navigator.clipboard` **不存在**，`.writeText` 會同步丟
        // TypeError → 被外層那個 .catch 接走 → 使用者看到「發連結失敗」，
        // 而 token 其實已經建好了，他只是永遠拿不到連結（會再按一次、再建一個）。
        // 看 copyText 的回傳值：它**從不 reject**，退到 prompt 那條時已經讓使用者
        // 自己複製過了 —— 再 alert 一次「已複製」等於連吃兩個對話框。
        copyText(url).then(function(ok) {
            if (ok) alert('雜支登記連結已複製（免登入，可直接給現場人員）：\n' + url);
        });
    }).catch(function(e) {
        alert('發連結失敗：' + (e.message || e));
    });
};

window._advShareLink = function(advanceId) {
    var url = location.origin + '/advance-expense.html?id=' + advanceId;
    // 同上：內網非安全來源裸用 clipboard ＝ 這顆點了完全沒反應（連 alert 都沒有）。
    copyText(url).then(function(ok) {
        if (ok) alert('連結已複製：\n' + url);
    });
};




/** 請款／現金已付款／費用已代墊 —— 同一個視窗。
 *
 *  `advanced`＝從「費用已代墊」那顆進來：代墊區預先展開、代墊人下拉先聚焦。
 *  🔴 不另建一套代墊流程 —— 這個機制本來就在（視窗裡的勾選框），只是藏著，
 *  生產庫 0 筆用過。再刻一份的話，「誰去領這筆錢」就會有兩條規則。
 */
// `opts`（選填，行政雜支那顆才會帶）：
//   expenseId    → 寫進請款單的 `expense_id` 硬連結（「這張單是 CRM 某一行的
//                  鏡射」）。重複請款由後端 409 擋 —— 前端把按鈕換掉擋不住
//                  雙擊／兩個分頁／重送。
//   plannedMonth → 預帶預計付款月（雜支先花了才請，通常就是消費月）
//   onDone       → 建立成功後要重畫哪一區（人員費用那三顆不帶＝重畫自己那區）
window._costCreatePayment = function(payeeName, amount, summary, status, advanced, opts) {
    opts = opts || {};
    if (!state.selectedId) return;
    if (!_canInvoice()) { _denyInvoice(); return; }
    var proj = state.projects.find(function(p) { return p.id === state.selectedId; });
    var projName = proj ? proj.name : '';
    var overlay = document.createElement('div');
    overlay.className = 'crm-modal-overlay';
    overlay.dataset.dynamic = '1';        // _costAfterPay 只收這種動態長出來的
    overlay.style.display = 'flex';
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = '<div class="crm-modal" style="max-width:420px;">' +
        '<div class="crm-modal-header"><h3>' + (advanced ? '費用已代墊' : status === '已付款' ? '現金已付款' : '請款') + '</h3>' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-detail-close">✕</button></div>' +
        '<div class="crm-modal-body">' +
        '<div class="crm-field" style="margin-bottom:8px;"><label>專案</label><input class="crm-input" value="' + _esc(projName) + '" disabled style="opacity:0.6;"></div>' +
        '<div class="crm-form-grid">' +
        // 🔴 收款人**用選的不要打字**（owner 2026-09-11）：打字的結果是同一個人被寫成
        // 三個收款人（「停車費 史丹」「停車 史丹」「早餐 史丹」）、對不到人員檔就沒有帳號。
        // 名字不在人員庫時仍保留原值（代開單的收款人常常是外面的人），不然一開就被清掉。
        '<div class="crm-field crm-field-full"><label>人員 <span class="crm-required">*</span></label>' +
        '<select id="pay-modal-payee" class="crm-input" required>' +
        (payeeName && !(state.staffList || []).some(function(s) { return s.name === payeeName; })
            ? '<option value="' + _esc(payeeName) + '" selected>' + _esc(payeeName) + '（不在人員庫）</option>' : '') +
        '<option value="">— 選擇人員 —</option>' +
        '<option value="__other__">＋ 其他（自己打）</option>' +
        (state.staffList || []).map(function(s) {
            return '<option value="' + _esc(s.name) + '"' + (s.name === payeeName ? ' selected' : '') + '>' +
                   _esc(s.name) + (s.alias ? '（' + _esc(s.alias) + '）' : '') + '</option>';
        }).join('') +
        '</select>' +
        // 選「其他」才露出來 —— 代開的錢是匯回外面的人，不該強迫挑員工（owner 2026-09-04）
        '<input id="pay-modal-payee-other" class="crm-input" style="display:none;margin-top:6px;" placeholder="自己打（外面的人）">' +
        '</div>' +
        '<div class="crm-field crm-field-full"><label>金額 <span class="crm-required">*</span></label><input id="pay-modal-amount" type="number" class="crm-input" value="' + amount + '" required></div>' +
        // 代墊：收款人換成代墊人，費用歸屬仍是原本那個人（送出時才對調 —— 見下方）
        '<div class="crm-field crm-field-full" style="display:flex;align-items:center;gap:8px;"><label style="display:flex;align-items:center;gap:4px;cursor:pointer;margin:0;flex-shrink:0;"><input type="checkbox" id="pay-modal-advance"' + (advanced ? ' checked' : '') + ' onchange="document.getElementById(\'pay-modal-advance-by\').style.display=this.checked?\'\':\'none\'"> 代墊</label><select id="pay-modal-advance-by" class="crm-input" style="' + (advanced ? '' : 'display:none;') + 'flex:1;"><option value="">— 代墊人（實際收款人）—</option>' +
        state.staffList.map(function(s) { return '<option value="' + _esc(s.name) + '">' + _esc(s.name) + '</option>'; }).join('') +
        '</select></div>' +
        (advanced ? '<div class="crm-field crm-field-full" style="color:#fb923c;font-size:11px;margin-top:-4px;">這筆錢由代墊人先掏 —— 公司要還的是<b>代墊人</b>，費用歸屬仍記在 ' + _esc(payeeName) + ' 身上（不影響連結私帳的收入鏡射）。</div>' : '') +
        '<div class="crm-field crm-field-full"><label>摘要 <span class="crm-required">*</span></label><input id="pay-modal-summary" class="crm-input" value="' + _esc(summary) + '" required></div>' +
        '<div class="crm-field crm-field-full"><label>報支項目 <span class="crm-required">*</span></label><select id="pay-modal-payee-type" class="crm-input" required><option value="">—</option><option value="內部人員">內部人員</option><option value="現金">現金</option><option value="勞報">勞報</option><option value="核銷">核銷</option></select></div>' +
        '<div class="crm-field crm-field-full"><label>預計付款月 <span class="crm-required">*</span></label><input id="pay-modal-month" type="month" class="crm-input" value="' + _esc(opts.plannedMonth || '') + '" required></div>' +
        '<div class="crm-field crm-field-full"><label>備註</label><input id="pay-modal-notes" class="crm-input" placeholder="選填"></div>' +
        '</div></div>' +
        '<div class="crm-modal-footer">' +
        '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">取消</button>' +
        '<button id="pay-modal-submit" class="crm-btn crm-btn-primary">確定</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    // 選「其他」才露出打字框
    document.getElementById('pay-modal-payee').addEventListener('change', function() {
        document.getElementById('pay-modal-payee-other').style.display =
            this.value === '__other__' ? '' : 'none';
        if (this.value === '__other__') document.getElementById('pay-modal-payee-other').focus();
    });
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
        // 收款人預設用選的；選「其他」時才讀旁邊那格（代開的錢是匯回外面的人，
        // 不該強迫挑員工 —— owner 2026-09-04）
        var _pSel = document.getElementById('pay-modal-payee');
        var originalPayee = _pSel.value === '__other__'
            ? (document.getElementById('pay-modal-payee-other').value || '').trim()
            : _pSel.value;
        if (!originalPayee) {
            alert('人員 為必填');
            btn.disabled = false; btn.textContent = '確定'; return;
        }
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
                    // 行政雜支那顆才有 —— 釘住這張單是哪一行的鏡射
                    expense_id: opts.expenseId || '',
                })
            });
            overlay.remove();
            if (opts.onDone) { opts.onDone(); }
            _refreshPayIfOpen();
        } catch (e) {
            alert(_U.permDeniedMsg?.(_INVOICE_NEED, e) ?? ('建立失敗：' + e.message));
            btn.disabled = false; btn.textContent = '確定';
        }
    });
};

// ── 請款單的狀態動作（收回／標記付款／改回應付）────────────────────
// owner 2026-09-02「這裡的請款要可收回可編輯（像私帳那樣）」：請款之後那一列
// 只剩「已請款」三個字，按錯了只能跑去別的 tab 找那張單。
//
// 🔴 **走跟私帳同一組端點**（batch-pay / batch-unpay / DELETE）——
// 不自己 PUT `payment_status`：那支端點一次處理付款日與代開發票的撥款狀態
// （sync_remit_status），繞過去就會出現「請款單說沒付、發票說已撥款」的兩份答案。
// 這也是本檔原本那支 `_costUpdatePaymentStatus`（整包 GET→PUT 寫回）被拿掉的
// 理由：那種寫法還會被 schema 的預設值洗掉前端沒送的欄位。
//
// 🔴 **「編輯」＝收回後重新請款**（同私帳）。收回會把單子刪掉，那一列就變回
// 三顆建立鈕，改好金額／收款人再按一次即可。已付款的單不給收回 —— 錢都出去了
// 還撤單，帳上會少一筆付款；那條路是先「改回應付」。
async function _costPayAction(id, paid) {
    try {
        await _fetch(paid ? '/payments/batch-pay' : '/payments/batch-unpay', {
            method: 'PATCH',
            body: JSON.stringify(paid ? { payment_ids: [id], payment_date: today() }
                                      : { payment_ids: [id] }),
        });
        return true;
    } catch (e) {
        alert(_U.permDeniedMsg?.(_INVOICE_NEED, e) ?? ((paid ? '標記付款失敗：' : '改回應付失敗：') + e.message));
        return false;
    }
}

/** 收付款分頁開著才重抓它（8＋2 支請求）；沒開著的話切過去時分頁點擊本來就會 loadPayTab。
 *  （舊的執行人員／預支款渲染器 _loadCostStaff／_loadAdvances 在收付款分頁上線後沒有容器可畫，2026-09-06 拿掉；活的畫面在 crm-projects-pay.js）*/
function _refreshPayIfOpen() {
    if (document.querySelector('#proj-detail-tabs .crm-tab.active')?.dataset.tab === 'team') window._projPay?.refresh?.();
}

/** 動作做完要重畫哪一區 —— 人員費用與雜支各自不同，所以由呼叫端帶。 */
function _costAfterPay(onDone) {
    // 只收動態長出來的那層（data-dynamic）；靜態的 #proj-modal 刪掉之後「新增專案」就死了
    var ov = document.querySelector('.crm-modal-overlay[data-dynamic]');
    if (ov) ov.remove();
    if (onDone) { onDone(); }
    _refreshPayIfOpen();          // 收付款分頁的狀態列／提示／結案檢查跟著變
}

window._costPayMark = async function(id, paid, onDone) {
    if (!_canInvoice()) { _denyInvoice(); return; }
    if (!paid && !confirm('把這張請款單改回應付款？（單子留著，只是取消付款）')) return;
    if (await _costPayAction(id, paid)) { _costAfterPay(onDone); }
};

window._costPayWithdraw = async function(id, summary, onDone) {
    if (!_canInvoice()) { _denyInvoice(); return; }
    var msg = '收回這張請款單？' + '\n\n' + (summary || '')
        + '\n\n單子會被刪掉，那一列變回可以重新請款。\n'
        + '（已付的錢請改用「改回應付」）';
    if (!confirm(msg)) { return; }
    try {
        await _fetch('/payments/' + id, { method: 'DELETE' });
        _costAfterPay(onDone);
    } catch (e) { alert(_U.permDeniedMsg?.(_INVOICE_NEED, e) ?? ('收回失敗：' + e.message)); }
};

/** 財務區的小按鈕（請款三顆／付款動作／預支三顆）只有這一份模板 ——
 *  onclick 字串由呼叫端組，這裡只管樣式：gap＝左邊留 4px、css 追加、title 提示。 */
function _smallBtn(call, label, opt) {
    opt = opt || {};
    return '<button class="crm-btn crm-btn-secondary crm-btn-sm"'
        + ' style="font-size:10px;padding:1px 6px;' + (opt.gap ? 'margin-left:4px;' : '') + (opt.css || '') + '"'
        + (opt.title ? ' title="' + opt.title + '"' : '')
        + ' onclick="' + call + '">' + label + '</button>';
}

/** 這張單在畫面上該給哪幾顆動作鈕。列上與詳情視窗共用同一份。 */
window._costPayBtns = function(p, onDoneName) {
    if (!_canInvoice()) return '';       // 沒有錢流寫入權就一顆都不畫（看單仍可）
    var d = onDoneName ? (',' + onDoneName) : '';
    var sm = _esc(p.summary || '').replace(/'/g, "\\'");
    if (p.payment_status === '已付款') {
        return _smallBtn('window._costPayMark(\'' + p.id + '\',false' + d + ')', '改回應付',
                         { gap: true, title: '改回應付款（單子留著）' });
    }
    return _smallBtn('window._costPayMark(\'' + p.id + '\',true' + d + ')', '標記付款',
                     { gap: true, css: 'color:#86efac;', title: '標記為已付款' })
        + _smallBtn('window._costPayWithdraw(\'' + p.id + '\',\'' + sm + '\'' + d + ')', '收回請款',
                    { gap: true, css: 'color:#fca5a5;', title: '撤掉這張請款單（改好再請一次）' });
};

window._costViewPayment = async function(paymentId, onDoneName) {
    try {
        var p = await _fetch('/payments/' + paymentId);
        var statusColor = p.payment_status === '已付款' ? '#86efac' : '#fb923c';
        var overlay = document.createElement('div');
        overlay.className = 'crm-modal-overlay';
    overlay.dataset.dynamic = '1';        // _costAfterPay 只收這種動態長出來的
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
            '<button onclick="this.closest(\'.crm-modal-overlay\').remove()" class="crm-btn crm-btn-secondary">關閉</button>' +
            // 收回／改狀態就在這裡 —— 行政雜支那一欄只有 88px 塞不下鈕，
            // 兩個入口共用同一份動作（window._costPayBtns）
            window._costPayBtns(p, onDoneName) +
            '</div>' +
            '</div>';
        document.body.appendChild(overlay);
    } catch (e) { alert('載入失敗：' + e.message); }
};


// ── Init ────────────────────────────────────────────────────────

function initFinanceHandlers() {
    // All window.* handlers are already assigned at module load time above.
    // This function serves as a hook for the main module to call after import,
    // ensuring the side effects (window.* assignments) have executed.
}

// ── Exports ─────────────────────────────────────────────────────

export { _loadProjectStaff, initFinanceHandlers };
