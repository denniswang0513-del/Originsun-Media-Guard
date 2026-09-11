/**
 * crm-payables.js — 應付帳款子視圖（按月份分組）
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, createSortable, today } from './crm-utils.js';
// 兩本帳：Sheet 退役（owner 2026-08-25「我不會用 sheet 工作了」）→ 匯款清單
// 也要在私帳可用。pin 模式同 crm-cashbook。
import { finEntity as _pinEntity } from '../finance/fin-utils.js';
// 🔴 複製一律走共用那支：同事多半從 http://192.168.1.x 連進來＝**非安全內容**，
// `navigator.clipboard` 根本不存在，裸用它就是一顆按了沒反應的按鈕。
import { copyText } from '../../js/shared/utils.js';

let _payees = [];       // raw API data (grouped by payee)
let _monthGroups = [];  // restructured: grouped by month, then payee
let _selectedKey = null; // "payeeName|month"

/* ── 資料重組：payee-first → month-first ── */
function _buildMonthGroups() {
    const monthMap = {};
    const seen = new Set();
    for (const p of _payees) {
        for (const it of p.items) {
            if (seen.has(it.id)) continue;
            seen.add(it.id);
            // 已付款按實際付款月份歸類，應付款按預計月份
            const isPaid = it.payment_status === '已付款';
            const m = isPaid && it.payment_date
                ? it.payment_date.substring(0, 7)
                : (it.planned_month || '未指定月份');
            if (!monthMap[m]) monthMap[m] = {};
            if (!monthMap[m][p.payee_name]) {
                monthMap[m][p.payee_name] = {
                    payee_name: p.payee_name,
                    payee_id: p.payee_id,
                    bank_name: p.bank_name,
                    bank_account: p.bank_account,
                    bank_code: p.bank_code,
                    bank_display: p.bank_display,
                    month_amount: 0,
                    // 本次要匯＝這個月**還沒付**的那幾筆（後端 group_payables 也算同一套）。
                    // 跟 month_amount 是兩件事：照總額匯會把已付的再匯一次。
                    unpaid_amount: 0,
                    unpaid_count: 0,
                    items: [],
                };
            }
            monthMap[m][p.payee_name].month_amount += it.amount || 0;
            if (!isPaid) {
                monthMap[m][p.payee_name].unpaid_amount += it.amount || 0;
                monthMap[m][p.payee_name].unpaid_count += 1;
            }
            monthMap[m][p.payee_name].items.push(it);
        }
    }

    const sorted = Object.keys(monthMap).sort((a, b) => {
        if (a === '未指定月份') return 1;
        if (b === '未指定月份') return -1;
        return b.localeCompare(a);
    });

    _monthGroups = sorted.map(m => {
        const payees = Object.values(monthMap[m]).sort((a, b) => b.month_amount - a.month_amount);
        return {
            month: m,
            label: m === '未指定月份' ? '未指定月份' : m.replace(/^(\d{4})-(\d{2})$/, '$1年$2月'),
            payees,
            month_total: payees.reduce((s, p) => s + p.month_amount, 0),
            month_unpaid: payees.reduce((s, p) => s + p.unpaid_amount, 0),
            month_unpaid_count: payees.reduce((s, p) => s + p.unpaid_count, 0),
        };
    });
}

/* ── 載入 ── */
async function loadPayables() {
    const monthInput = document.getElementById('payable-month');
    const month = monthInput?.value || '';
    const statusSel = document.getElementById('payable-filter-status');
    const status = statusSel?.value || '';

    try {
        const params = new URLSearchParams();
        if (month) params.set('month', month);
        if (status) params.set('status', status);
        params.set('entity', _pinEntity());
        const data = await _fetch('/payables/summary?' + params);
        _payees = data.payees || [];
        document.getElementById('payable-total').textContent = '$' + _fmtNum(data.grand_total);
    } catch (_) {
        _payees = [];
    }
    _buildMonthGroups();
    renderList();

    if (_selectedKey) {
        const [name, m] = _selectedKey.split('|');
        const grp = _monthGroups.find(g => g.month === m);
        const p = grp?.payees.find(x => x.payee_name === name);
        // 🔴 找不到就要把面板收掉，不能什麼都不做：/payables/summary 只列未付，
        // 把某個收款人的最後一筆付掉之後他就整個不在清單裡了 —— 舊的 else 分支
        // （沒有）會讓付款前那份畫面留在螢幕上，連「應付款」「全部付款」兩顆
        // 按鈕都還在。使用者按了付款卻看到按鈕原封不動，會以為根本沒作用
        // （owner 2026-08-21 回報的正是這個畫面）；再按一次還會重付已付的單。
        if (p) renderDetail(p, m); else closeDetail();
    }
}

// 排序在 month group 內,group 自己保持月份序列(資料已 buildMonthGroups 排好)
/** 清單上的銀行那一行。🔴 沒有帳號要**直接說出來**，不是留白 ——
 *  空白看起來像「還沒載入」，出納會以為再等一下就有；寫出來他才知道要去人員檔補。 */
function _bankLine(p) {
    // 欄位窄，寫短一點；完整那句掛 title（截斷的字反而看不出是什麼）
    if (!p.bank_account) return '<span style="color:#fbbf24;" title="這位收款人在人員檔裡沒有銀行帳號 —— 補在人員檔，這裡就會帶出來">沒有帳號</span>';
    const bank = [p.bank_display || p.bank_name, p.bank_code].filter(Boolean).join(' ');
    return _esc(bank ? `${bank} · ${p.bank_account}` : p.bank_account);
}

const _allPaid = (p) => p.items.every(it => it.payment_status === '已付款');
const _sorter = createSortable({
    storageKey: 'crm_payables_sort',
    defaultSort: { key: 'amount', dir: 'desc' },
    panelId: 'payable-list-panel',
    onChange: () => renderList(),
    getters: {
        payee:  p => (p.payee_name || '').toLowerCase(),
        amount: p => p.month_amount || 0,
        bank:   p => (p.bank_name || '') + ' ' + (p.bank_account || ''),
        // 應付款 < 已付款:asc 把待處理排前
        status: p => _allPaid(p) ? 1 : 0,
    },
});

/* ── 列表渲染（月份標題 + 收款人行;sort 在 group 內,group 順序維持月份）── */
function renderList() {
    const body = document.getElementById('payable-list-body');
    if (!body) return;
    _sorter.attach();
    if (_monthGroups.length === 0) {
        body.innerHTML = '<div class="crm-empty">無應付帳款</div>';
        return;
    }
    let html = '';
    for (const g of _monthGroups) {
        html += `<div class="payable-month-header">
            <span>${_esc(g.label)}</span>
            <span>要匯 $${_fmtNum(g.month_unpaid)}　·　${g.month_unpaid_count} 筆${
                g.month_unpaid !== g.month_total ? `（總額 $${_fmtNum(g.month_total)}）` : ''}</span>
        </div>`;
        for (const p of _sorter.sorted(g.payees)) {
            const key = p.payee_name + '|' + g.month;
            const allPaid = _allPaid(p);
            const statusCls = allPaid ? 'crm-badge crm-pay-全額到帳' : 'crm-badge crm-pay-未到帳';
            const statusText = allPaid ? '已付款' : '應付款';
            html += `
            <div class="crm-row${key === _selectedKey ? ' selected' : ''}" onclick="window._payableSelect('${_esc(p.payee_name)}','${_esc(g.month)}')">
                <div class="crm-row-name">${_esc(p.payee_name)}</div>
                <div class="crm-row-amount">$${_fmtNum(allPaid ? p.month_amount : p.unpaid_amount)}</div>
                <div class="crm-row-client" style="font-size:11px;">${_bankLine(p)}</div>
                <div class="crm-row-status"><span class="${statusCls}">${statusText}</span></div>
            </div>`;
        }
    }
    body.innerHTML = html;
}

/** 本次要匯的那幾筆＝**還沒付的**。月份分組裡可能混著已付的（已付按實際付款月份歸類），
 *  所以「應付總額」和「這次要匯多少」不是同一個數字 —— 匯款時要用的是這個。 */
const _unpaidItems = (p) => p.items.filter(it => it.payment_status !== '已付款');
const _unpaidTotal = (p) => _unpaidItems(p).reduce((s, it) => s + (it.amount || 0), 0);
/** 一筆項目寫成一行人看得懂的字（複製出去給收款人核對用）。 */
const _itemLine = (it) => `${it.summary}${it.project_label ? `（${it.project_label}）` : ''} $${_fmtNum(it.amount)}`;

/* ── 詳情面板 ── */
function renderDetail(p, month) {
    const monthLabel = month === '未指定月份' ? '未指定月份' : month.replace(/^(\d{4})-(\d{2})$/, '$1年$2月');
    document.getElementById('payable-detail-title').textContent = p.payee_name + ' — ' + monthLabel;

    const actionsArea = document.getElementById('payable-bar-actions');
    if (actionsArea) {
        const hasUnpaid = p.items.some(it => it.payment_status !== '已付款');
        const key = p.payee_name + '|' + month;
        actionsArea.innerHTML = `
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._payableCopyInfo('${_esc(p.payee_name)}','${_esc(month)}',this)">複製匯款資訊</button>
            ${hasUnpaid ? `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._payablePayAll('${_esc(p.payee_name)}','${_esc(month)}')">全部付款</button>` : ''}
            <button id="payable-detail-close" class="crm-detail-close" title="關閉" onclick="window._payableClose()">&#x2715;</button>
        `;
    }

    const bankHtml = p.bank_name
        ? `<div class="crm-detail-prop"><div class="crm-prop-label">銀行</div><div class="crm-prop-value">${_esc(p.bank_name)} ${_esc(p.bank_account)}</div></div>`
        : '';

    let itemsHtml = '';
    for (const it of p.items) {
        const isPaid = it.payment_status === '已付款';
        itemsHtml += `
        <div class="payable-item" id="payable-row-${it.id}">
            <span class="payable-item-summary">${_esc(it.summary)}${it.project_id ? `<span class="payable-item-proj" title="到這個案子的收付款分頁" onclick="window._crmPayableGo('${_esc(it.project_id)}')">${_esc(it.project_label || '案子')} →</span>` : ''}</span>
            <span class="payable-item-cat">${_esc(it.category)}</span>
            <span class="payable-item-amt">$${_fmtNum(it.amount)}</span>
            <span style="min-width:260px;display:flex;align-items:center;justify-content:flex-end;gap:6px;font-size:11px;">
            ${isPaid
                ? `<input type="date" id="payable-di-${it.id}" value="${it.payment_date || ''}" data-orig="${it.payment_date || ''}" style="background:#1a1a1a;border:1px solid #3a3a3a;color:#86efac;font-size:11px;padding:2px 6px;border-radius:4px;width:130px;" onchange="window._payableDateChanged('${it.id}')">
                    <button id="payable-ds-${it.id}" class="crm-btn crm-btn-primary crm-btn-sm" style="min-width:50px;display:none;" onclick="window._payableSaveDate('${it.id}')">儲存</button>
                    <span id="payable-dl-${it.id}" style="color:#86efac;min-width:42px;text-align:center;">已付款</span>
                    <button id="payable-du-${it.id}" class="crm-btn crm-btn-sm" style="font-size:10px;padding:1px 6px;color:#9ca3af;border:1px solid #3a3a3a;background:transparent;" onclick="window._payableUnpay('${it.id}')" title="改回應付款">↩</button>`
                : `<input type="month" id="payable-mi-${it.id}" value="${it.planned_month || ''}" data-orig="${it.planned_month || ''}" style="background:#1a1a1a;border:1px solid #3a3a3a;color:#e0e0e0;font-size:11px;padding:2px 6px;border-radius:4px;width:130px;" onchange="window._payableMonthChanged('${it.id}')" title="調整月份">
                    <button id="payable-ms-${it.id}" class="crm-btn crm-btn-primary crm-btn-sm" style="min-width:50px;display:none;" onclick="window._payableSaveMonth('${it.id}')">儲存</button>
                    <button id="payable-mb-${it.id}" class="crm-btn crm-btn-danger crm-btn-sm" style="min-width:60px;" onclick="window._payableSinglePay(this,'${it.id}')">應付款</button>`
            }
            </span>
        </div>`;
    }

    // 匯款要用的是「還沒付的那幾筆」，不是這個月的總額（見 _unpaidTotal）
    const unpaidTotal = _unpaidTotal(p);
    const unpaidLines = _unpaidItems(p).map(_itemLine);

    document.getElementById('payable-detail-content').innerHTML = `
        ${_cpRow('bankcode', '銀行代碼', p.bank_code
            ? `<span class="pay-mono">${_esc(p.bank_code)}</span><span class="pay-sub">${_esc(p.bank_display || p.bank_name)}</span>`
            : '<span class="pay-warn">沒有代碼，去人員檔補</span>', !!p.bank_code)}
        ${_cpRow('account', '帳號', p.bank_account
            ? `<span class="pay-mono">${_esc(p.bank_account)}</span>`
            : '<span class="pay-warn">沒有帳號，去人員檔補</span>', !!p.bank_account)}
        ${_cpRow('amount', '本次要匯',
            `<span class="pay-amt">$${_fmtNum(unpaidTotal)}</span>`
            + `<span class="pay-sub">${unpaidLines.length} 筆${
                unpaidTotal !== p.month_amount ? `　·　應付總額 $${_fmtNum(p.month_amount)}` : ''}</span>`,
            unpaidTotal > 0)}
        ${_cpRow('memo', '轉入備註', _esc(_bkCompany()), !!_bkCompany())}
        ${_cpRow('payee', '收款人', `<span style="font-weight:700;">${_esc(p.payee_name)}</span>`, true)}
        ${unpaidLines.length ? _cpRow('items', '本次項目',
            `<div style="display:flex;flex-direction:column;gap:2px;font-size:12px;">${
                unpaidLines.map(l => `<span>${_esc(l)}</span>`).join('')}</div>`, true) : ''}
        <div style="border-top:1px solid #2e2e2e;margin:10px 0;"></div>
        ${itemsHtml}
        <div style="display:flex;flex-wrap:wrap;gap:6px 16px;color:#9ca3af;font-size:11.5px;padding-top:10px;">
            ${p.payee_id ? `<span>身分證 ${_esc(p.payee_id)}</span>` : ''}
            <span>應付總額 $${_fmtNum(p.month_amount)}</span>
        </div>
    `;
}

/** 面板的一列：**複製鈕在最左邊**，每一列都是同一顆「複製」（owner 2026-09-11）。
 *  統一寫「複製」而不是「複製金額」「複製 012」—— 出納照著列往下走，
 *  按鈕在同一個 X 座標，眼睛不用找。列的順序＝網銀的填表順序。 */
function _cpRow(key, label, valueHtml, canCopy) {
    return `<div class="pay-row">
        ${canCopy
            ? `<button class="pay-cp" onclick="window._payableCopyField('${key}',this)">複製</button>`
            : '<span class="pay-cp-off">—</span>'}
        <span class="pay-k">${_esc(label)}</span>
        <span class="pay-v">${valueHtml}</span>
    </div>`;
}

/** 轉入帳號備註＝我們的公司名稱（後台設定裡已經有；出納現在每次手打）。 */
function _bkCompany() {
    return (window.__settingsCache?.company?.name) || _companyName || '';
}
let _companyName = '';
(async () => {
    try {
        const r = await fetch(location.origin + '/api/settings/load');
        if (r.ok) _companyName = (await r.json())?.company?.name || '';
    } catch (_) { /* 沒有就空著，那一列會顯示「—」不給複製 */ }
})();

/* ── 選取 / 關閉 ── *//* ── 選取 / 關閉 ── */
function selectPayee(name, month) {
    _selectedKey = name + '|' + month;
    renderList();
    document.getElementById('payable-detail-panel').style.display = 'flex';
    document.getElementById('payable-resize-handle').style.display = '';
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
    if (p) renderDetail(p, month);
}

function closeDetail() {
    _selectedKey = null;
    document.getElementById('payable-detail-panel').style.display = 'none';
    document.getElementById('payable-resize-handle').style.display = 'none';
    renderList();
}

/* ── 全域綁定 ── */
window._payableSelect = selectPayee;
window._payableClose = closeDetail;
window._payableRefresh = () => loadPayables();

window._payableMonthChanged = (paymentId) => {
    const input = document.getElementById('payable-mi-' + paymentId);
    const saveBtn = document.getElementById('payable-ms-' + paymentId);
    const payBtn = document.getElementById('payable-mb-' + paymentId);
    if (!input || !saveBtn) return;
    const changed = input.value !== input.dataset.orig;
    saveBtn.style.display = changed ? '' : 'none';
    if (payBtn) payBtn.style.display = changed ? 'none' : '';
};

window._payableSaveMonth = async (paymentId) => {
    const input = document.getElementById('payable-mi-' + paymentId);
    if (!input) return;
    const newMonth = input.value;
    try {
        await _fetch('/payments/batch-month', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], planned_month: newMonth })
        });
        // 更新 selectedKey 到新月份，讓詳情面板跟著刷新
        if (_selectedKey) {
            const [name] = _selectedKey.split('|');
            _selectedKey = name + '|' + (newMonth || '未指定月份');
        }
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableSinglePay = async (btn, paymentId) => {
    if (!confirm('確定標記此筆為已付款？')) return;
    const payDay = today();
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], payment_date: payDay })
        });
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableUnpay = async (paymentId) => {
    if (!confirm('確定將此筆改回應付款？')) return;
    try {
        await _fetch('/payments/batch-unpay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId] })
        });
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableDateChanged = (paymentId) => {
    const input = document.getElementById('payable-di-' + paymentId);
    const saveBtn = document.getElementById('payable-ds-' + paymentId);
    const label = document.getElementById('payable-dl-' + paymentId);
    const unpayBtn = document.getElementById('payable-du-' + paymentId);
    if (!input || !saveBtn) return;
    const changed = input.value !== input.dataset.orig;
    saveBtn.style.display = changed ? '' : 'none';
    if (label) label.style.display = changed ? 'none' : '';
    if (unpayBtn) unpayBtn.style.display = changed ? 'none' : '';
};

window._payableSaveDate = async (paymentId) => {
    const input = document.getElementById('payable-di-' + paymentId);
    if (!input || !input.value) return;
    const newDate = input.value;
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: [paymentId], payment_date: newDate })
        });
        // 已付款按付款日月份歸類，更新 selectedKey
        if (_selectedKey) {
            const [name] = _selectedKey.split('|');
            _selectedKey = name + '|' + newDate.substring(0, 7);
        }
        await loadPayables();
    } catch (e) { alert(e.message); }
};

window._payableCopyInfo = (name, month, btn) => {
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
    if (!p) return;
    const unpaid = _unpaidItems(p);
    // 金額**不帶錢字號與逗號**：網銀的金額欄只收數字，帶符號就得手動改，改就會錯
    // （owner 2026-09-11 轉述同事：「不要有標點符號，比較不會手動修改錯誤」）
    // 🔴 沒填的欄位要整行丟掉（filter(Boolean)），但分隔用的空行要留 —— 所以先濾完
    // 抬頭那幾行、再接項目清單。混在同一個陣列裡濾，不是多一行空白就是少一個分隔。
    const head = [
        '收款人: ' + p.payee_name,
        p.payee_id ? '身分證: ' + p.payee_id : '',
        p.bank_name ? '銀行: ' + p.bank_name : '',
        p.bank_account ? '帳號: ' + p.bank_account : '',
        '金額: ' + _plainAmount(_unpaidTotal(p)),
    ].filter(Boolean);
    // 通知收款人時對得起來：這次匯的是哪幾筆（同事要的「本次匯款項目包含哪一些」）
    const tail = unpaid.length ? ['', '本次匯款項目：', ...unpaid.map(it => '・' + _itemLine(it))] : [];
    const text = [...head, ...tail].join('\n');
    copyText(text, btn);
};

/** 貼進網銀的金額：純數字，沒有錢字號也沒有逗號。 */
const _plainAmount = (n) => String(Math.round(Number(n) || 0));

/** 面板逐格複製。值一律取「現在選著的那個收款人」，不從 DOM 讀字（畫面上的
 *  $1,495 帶著錢字號與逗號，網銀的金額欄只吃數字）。 */
window._payableCopyField = (key, btn) => {
    const [name, month] = (_selectedKey || '').split('|');
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
    if (!p) return;
    const text = {
        bankcode: () => p.bank_code || '',
        account: () => p.bank_account || '',
        amount: () => _plainAmount(_unpaidTotal(p)),
        memo: () => _bkCompany(),
        payee: () => p.payee_name || '',
        items: () => _unpaidItems(p).map(_itemLine).join(String.fromCharCode(10)),
    }[key]?.() || '';
    if (text) copyText(text, btn);
};

window._payablePayAll = async (name, month) => {
    const grp = _monthGroups.find(g => g.month === month);
    const p = grp?.payees.find(x => x.payee_name === name);
    if (!p) return;
    const unpaidIds = p.items.filter(it => it.payment_status !== '已付款').map(it => it.id);
    if (!unpaidIds.length) return;
    if (!confirm(`確定將 ${name} 的 ${unpaidIds.length} 筆全部標記為已付款？`)) return;
    const payDay = today();
    try {
        await _fetch('/payments/batch-pay', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: unpaidIds, payment_date: payDay })
        });
        await loadPayables();
    } catch (e) { alert(e.message); }
};

export function initCrmPayablesTab() {
    const monthInput = document.getElementById('payable-month');
    monthInput.value = '';
    monthInput.addEventListener('change', loadPayables);
    document.getElementById('payable-filter-status').addEventListener('change', loadPayables);
    setupResizeHandle('payable-resize-handle', 'payable-detail-panel');
    loadPayables();
}

// 「案子 →」：_crmGoToProjectPay 住在 crm-projects-pay.js（專案分頁點到才載）；沒載就先載再跳
window._crmPayableGo = async function (pid) {
    if (typeof window._crmGoToProjectPay !== 'function' && typeof window._ensureTabLoaded === 'function') {
        try { await window._ensureTabLoaded('tab_crm_projects', { embed: true }); } catch (_) { /* 下面會判 */ }
    }
    if (typeof window._crmGoToProjectPay === 'function') window._crmGoToProjectPay(pid);
    else alert('專案分頁還沒載入，請先開「專案管理」再試');
};
