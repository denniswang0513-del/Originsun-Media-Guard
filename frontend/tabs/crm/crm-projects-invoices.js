/**
 * crm-projects-invoices.js — 專案詳情的「發票」分頁
 *
 * 為什麼要有它（owner 2026-08-23）：「專案如果成案，其實就會有一筆發票需要開立，
 * 為何不在專案裡面就有開發票的入口？」
 *
 * 盤點證實了那個直覺：收款發票 209 張只有 74 張掛專案（35%），275 個專案裡只有
 * 30 個看得到自己的發票 —— 89% 的案子在系統裡是「沒有收入」的。發票表單**早就有**
 * 「關聯專案」欄位，缺的不是欄位，是入口方向：工作流從專案開始，工具卻只從發票本
 * 開始，於是那個選填欄位自然被跳過。
 *
 * 跟帳務→發票那個入口的分工（刻意的，不要「順手統一」）：
 *   發票本   全部的票、按號碼/抬頭/日期找、編輯、作廢、上傳 PDF、配收款、對帳
 *            —— 記帳與對帳的人用
 *   這一頁   只有這個案子、開票、看收款到哪 —— PM/AM 用
 *
 * 🔴 這裡**只做新增與檢視**。編輯、作廢、PDF、收款配對一律導去發票本 ——
 *    兩邊都能改同一張票 ＝ 兩套規則遲早分岔（收支明細、匯費、分類規則各踩過一次）。
 * 🔴 稅額走 crm-utils.invoiceAmounts，跟發票本同一支。複製一份的話，兩個入口
 *    開出來的發票尾差會不一樣，而且是對帳時才會發現的那種差。
 */

import { crmFetch as _fetch, crmCacheFetch, esc as _esc, fmtNum, invoiceAmounts,
         invoicePayBadge, crmToast, today } from './crm-utils.js';

let _cur = null;          // 目前這個專案（渲染與預填都要）
let _client = null;       // 這個案子的客戶 —— 抬頭與統編從這裡來
let _invoices = [];

const _P = (window._projInv = window._projInv || {});

/** 這個案子的收款發票 —— 代開（付款方向）不算，那是另一件事。 */
const _receipts = () => _invoices.filter(
    i => (i.payment_type || '收款') === '收款' && (i.issue_status || '') !== '作廢');

function _sum(list, key) {
    return list.reduce((n, x) => n + (Number(x[key]) || 0), 0);
}

/** 摘要列：合約 / 已開 / 已收 / 尚欠 / 還能開。
 *
 * 「還能開」是這一頁存在的第二個理由 —— 分期請款時最重要的數字，而發票本
 * 永遠算不出來（它不知道合約金額）。⚠ 合約沒填就不顯示那一格，不要拿 0 當
 * 上限硬算（238 個專案裡只有 21 個填了 contract_amount）。 */
/** 還能開＝合約 − 已開；合約沒填回 null（不是 0 —— 那會讓每個案子都顯示
 *  「還能開 -500,000」）。摘要列與開票視窗都要這個數，兩邊各推一次就會漂。 */
function _remaining() {
    const contract = Number(_cur?.contract_amount) || 0;
    return contract ? contract - _sum(_receipts(), 'amount_total') : null;
}

function _summaryHtml() {
    const rows = _receipts();
    const issued = _sum(rows, 'amount_total');
    const got = _sum(rows, 'collected');
    const contract = Number(_cur?.contract_amount) || 0;
    const left = _remaining();
    // val 收「已經格式化好的字串」—— 「還能開」在合約沒填時要顯示文字而不是金額，
    // 讓 cell 只管排版就不用為那個情況抄一份 markup。
    const cell = (label, val, color) => `
        <div style="min-width:110px;">
          <div style="color:#6b7280;font-size:11px;">${label}</div>
          <div style="color:${color || '#e0e0e0'};font-size:15px;font-weight:600;">${val}</div>
        </div>`;
    const money = (n) => '$' + fmtNum(n);
    return `<div style="display:flex;gap:22px;flex-wrap:wrap;padding:12px 14px;
                 background:#1b1b1b;border:1px solid #2e2e2e;border-radius:6px;margin-bottom:12px;">
        ${contract ? cell('合約金額', money(contract)) : ''}
        ${cell('已開發票', money(issued))}
        ${cell('已收', money(got), '#86efac')}
        ${cell('尚欠', money(issued - got), issued - got > 0 ? '#fbbf24' : '#6b7280')}
        ${left === null
            ? cell('還能開', '<span style="font-size:12px;">合約金額未填</span>', '#6b7280')
            : cell('還能開', money(left), left < 0 ? '#fca5a5' : '#93c5fd')}
    </div>`;
}

function _listHtml() {
    const rows = _receipts();
    if (!rows.length) {
        return `<div style="color:#6b7280;padding:22px;text-align:center;">
            這個案子還沒有發票 —— 成案了就可以開第一張</div>`;
    }
    const th = (t, r) => `<th style="padding:5px 8px;text-align:${r ? 'right' : 'left'};
        color:#9ca3af;font-weight:500;font-size:11px;">${t}</th>`;
    return `<div style="border:1px solid #2e2e2e;border-radius:6px;overflow:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead style="background:#242424;"><tr>
          ${th('發票號')}${th('日期')}${th('品名')}${th('金額', 1)}${th('已收', 1)}${th('狀態')}
        </tr></thead>
        <tbody>${rows.map(i => `
          <tr style="border-top:1px solid #2a2a2a;">
            <td style="padding:5px 8px;color:#ddd;">${_esc(i.invoice_number || '無號')}</td>
            <td style="padding:5px 8px;color:#9ca3af;">${_esc((i.invoice_date || '').substring(0, 10))}</td>
            <td style="padding:5px 8px;color:#eee;">${_esc(i.title || '')}</td>
            <td style="padding:5px 8px;text-align:right;color:#eee;">$${fmtNum(i.amount_total || 0)}</td>
            <td style="padding:5px 8px;text-align:right;color:#9ca3af;">$${fmtNum(i.collected || 0)}</td>
            <td style="padding:5px 8px;">${invoicePayBadge(i.payment_status)}</td>
          </tr>`).join('')}</tbody>
      </table>
    </div>
    <div style="color:#6b7280;font-size:11px;margin-top:8px;">
      要改內容、作廢、上傳電子發票或配收款 —— 到「帳務 → 發票」那一頁。
      這裡只負責開票與看進度。
    </div>`;
}

/** 開發票視窗：欄位刻意少。客戶、抬頭、統編、專案、類別全部從專案帶，
 *  PM 只要填品名和金額。那正是這個入口跟發票本的差別 —— 發票本每次都要重選一次，
 *  而且選錯了沒有人會知道。 */
_P.create = function _openCreate() {
    if (!_cur) return;
    const host = document.getElementById('proj-inv-modal');
    if (!host) return;
    const left = _remaining();
    host.className = 'crm-modal-overlay';   // 背景、置中、z-index 都交給 crm.css
    host.innerHTML = `
      <div class="crm-modal" style="max-width:560px;padding:18px;">
        <div style="font-size:15px;color:#eee;margin-bottom:4px;">開發票</div>
        <div style="color:#6b7280;font-size:11px;margin-bottom:14px;">
          ${_esc(_cur.name || '')}${_client?.full_name ? '　·　' + _esc(_client.full_name) : ''}
          ${left !== null ? `　·　還能開 $${fmtNum(left)}` : ''}
        </div>
        <div style="display:grid;grid-template-columns:88px 1fr;gap:9px 10px;align-items:center;">
          <label style="color:#9ca3af;font-size:12px;">開立日期</label>
          <input id="proj-inv-date" type="date" class="crm-input" value="${today()}">
          <label style="color:#9ca3af;font-size:12px;">品名</label>
          <input id="proj-inv-title" class="crm-input" placeholder="例：形象影片 第一期款"
                 value="${_esc(_cur.name || '')}">
          <label style="color:#9ca3af;font-size:12px;">金額</label>
          <div style="display:flex;gap:8px;align-items:center;">
            <input id="proj-inv-amount" type="number" class="crm-input" style="flex:1;"
                   placeholder="${left ? left : ''}">
            <select id="proj-inv-mode" class="crm-input" style="width:96px;">
              <option value="total">含稅</option>
              <option value="ex">未稅</option>
            </select>
          </div>
          <label style="color:#9ca3af;font-size:12px;">抬頭</label>
          <input id="proj-inv-company" class="crm-input" value="${_esc(_client?.full_name || '')}">
          <label style="color:#9ca3af;font-size:12px;">統一編號</label>
          <input id="proj-inv-taxid" class="crm-input" maxlength="8"
                 value="${_esc(_client?.tax_id || '')}">
          <label style="color:#9ca3af;font-size:12px;">發票號碼</label>
          <input id="proj-inv-number" class="crm-input" placeholder="還沒拿到號碼可以留空">
        </div>
        <div id="proj-inv-calc" style="color:#6b7280;font-size:11px;margin-top:9px;min-height:16px;"></div>
        <div id="proj-inv-err" style="display:none;color:#fca5a5;font-size:12px;margin-top:6px;"></div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px;">
          <button class="crm-btn crm-btn-secondary" onclick="window._projInv.close()">取消</button>
          <button class="crm-btn crm-btn-primary" onclick="window._projInv.save(this)">開立</button>
        </div>
      </div>`;
    host.style.display = '';
    const recalc = () => {
        const a = invoiceAmounts(document.getElementById('proj-inv-amount').value,
                                 document.getElementById('proj-inv-mode').value);
        const el = document.getElementById('proj-inv-calc');
        el.textContent = a.amount_total
            ? `未稅 $${fmtNum(a.amount_ex_tax)}　稅額 $${fmtNum(a.tax_amount)}　含稅 $${fmtNum(a.amount_total)}`
            : '';
    };
    document.getElementById('proj-inv-amount').addEventListener('input', recalc);
    document.getElementById('proj-inv-mode').addEventListener('change', recalc);
    document.getElementById('proj-inv-title').focus();
};

_P.close = () => {
    const host = document.getElementById('proj-inv-modal');
    if (host) { host.style.display = 'none'; host.innerHTML = ''; }
};

_P.save = async (btn) => {
    const err = document.getElementById('proj-inv-err');
    const show = (m) => { err.textContent = m; err.style.display = 'block'; };
    const amounts = invoiceAmounts(document.getElementById('proj-inv-amount').value,
                                   document.getElementById('proj-inv-mode').value);
    if (!amounts.amount_total) { show('金額要填'); return; }
    const title = document.getElementById('proj-inv-title').value.trim();
    if (!title) { show('品名要填'); return; }
    btn.disabled = true;
    btn.textContent = '開立中…';
    try {
        // 🔴 payment_status / payment_type 刻意**不送** —— 後端在入口定案
        //    （create_invoice：沒送就依方向推）。前端自己決定過一次，結果漏掉
        //    「待撥款」那條路，代開發票被當成收款跑進應收帳款。
        await _fetch('/invoices', {
            method: 'POST',
            body: JSON.stringify({
                invoice_date: document.getElementById('proj-inv-date').value || today(),
                title,
                invoice_number: document.getElementById('proj-inv-number').value.trim(),
                company_name: document.getElementById('proj-inv-company').value.trim(),
                tax_id: document.getElementById('proj-inv-taxid').value.trim(),
                category: '專案',
                project_id: _cur.id,
                ...amounts,
            }),
        });
        _P.close();
        crmToast('發票已開立');
        // 只有發票變了 —— 專案與客戶沒動，不必重跑整個分頁的三個請求
        const inv = await _fetch('/invoices?project_id=' + encodeURIComponent(_cur.id));
        _invoices = inv.invoices || [];
        _renderTab();
    } catch (e) {
        show(e.message || '開立失敗');
    } finally {
        btn.disabled = false;
        btn.textContent = '開立';
    }
};

/** 分頁入口。專案換了就整頁重畫（跟其他 lazy 分頁同一個約定）。 */
export async function loadInvoicesTab(projectId) {
    const host = document.getElementById('proj-detail-invoices');
    if (!host || !projectId) return;
    host.innerHTML = '<div style="color:#888;padding:20px;">載入中…</div>';
    try {
        // 🔴 專案回應只有 client_short_name（代稱），發票要的是**全名抬頭**與統編
        //    —— 那兩個只在客戶主檔裡。撈不到就留空讓人自己填，不要拿代稱當抬頭
        //    （代稱是「泛亞」，抬頭是「泛亞工程顧問股份有限公司」，開錯要作廢重開）。
        //    走 crmCacheFetch 的共用快取（列表本來就帶 full_name / tax_id），
        //    比逐次 GET /clients/{id} 少一趟序列往返，也不會跟別頁的客戶資料分岔。
        const [proj, inv, cli] = await Promise.all([
            _fetch('/projects/' + projectId),
            _fetch('/invoices?project_id=' + encodeURIComponent(projectId)),
            crmCacheFetch('clients', '/clients').catch(() => ({ clients: [] })),
        ]);
        _cur = proj.project || proj;
        _invoices = inv.invoices || [];
        _client = (cli.clients || []).find(c => c.id === _cur?.client_id) || null;
    } catch (e) {
        // 🔴 重試要帶**這次要載的 id**，不能靠 _cur —— 第一次就失敗時 _cur 還是
        //    null（或上一個專案），按下去不是沒反應就是載到別的案子。
        host.innerHTML = `<div style="color:#fca5a5;padding:20px;">載入失敗：${_esc(e.message || '')}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                onclick="window._projInv.reload('${_esc(projectId)}')">重試</button></div>`;
        return;
    }
    _renderTab();
}

/** 只重畫，不重抓 —— 開完票之後用得到。 */
function _renderTab() {
    const host = document.getElementById('proj-detail-invoices');
    if (!host) return;
    host.innerHTML = `
      <div style="padding:14px;">
        <div style="display:flex;align-items:center;margin-bottom:12px;">
          <div style="color:#9ca3af;font-size:12px;">這個案子的收款發票</div>
          <span style="flex:1;"></span>
          <button class="crm-btn crm-btn-primary crm-btn-sm"
                  onclick="window._projInv.create()">＋ 開發票</button>
        </div>
        ${_summaryHtml()}
        ${_listHtml()}
      </div>
      <div id="proj-inv-modal" class="crm-modal-overlay" style="display:none;"></div>`;
}

_P.reload = (id) => { const pid = id || _cur?.id; if (pid) loadInvoicesTab(pid); };
