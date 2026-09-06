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
 * 🔴 這裡做**新增、刪除與檢視**。編輯、作廢、PDF、收款配對一律導去發票本 ——
 *    兩邊都能改同一張票 ＝ 兩套規則遲早分岔（收支明細、匯費、分類規則各踩過一次）。
 * 🔴 刪除是那條規則的例外（owner 2026-08-24：「這裡刪除發票，發票開立那邊也要
 *    可以刪除發票」），而它**刻意不自己實作**：直接打發票本同一支
 *    DELETE /invoices/{id}。那支會一併清掉收款分配（crm_cash_invoice_links）、
 *    改指或清空收支列的主要發票、並檢查鎖帳月 —— 在這裡另寫一套「簡單版刪除」
 *    就會留下孤兒分配列，而後果是靜默的：那筆收款仍被判成「分配與實收相符」，
 *    錢卻沒對到任何存在的發票。共用同一支端點，正是兩邊不會分岔的原因。
 * 🔴 稅額走 crm-utils.invoiceAmounts，跟發票本同一支。複製一份的話，兩個入口
 *    開出來的發票尾差會不一樣，而且是對帳時才會發現的那種差。
 */

import { crmFetch as _fetch, crmCacheFetch, esc as _esc, fmtNum, invoiceAmounts,
         invoicePayBadge, invoiceIssueBadge, crmToast, today } from './crm-utils.js';

let _cur = null;          // 目前這個專案（渲染與預填都要）
let _client = null;       // 這個案子的客戶 —— 抬頭與統編從這裡來
let _invoices = [];
// 申請人清單存在伺服器（settings.invoice_applicants），發票本的 ⚙️ 在管它。
// 🔴 這裡只讀不寫，也不另存一份 —— 兩個入口各有一份清單的話，這頁開的票會
//    掛到一個發票本下拉裡選不到的名字，篩選與排序就再也對不起來。
let _applicants = [];

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
    // 🔴 尚欠不能寫成 issued - got。「收齊了沒」的規則是後端的 `settled`
    //    （含 NT$50 匯費容差，394 張歷史發票裡有 42 張靠它）—— 自己相減的話，
    //    那 42 張被匯費短收 30 元的票會在這裡顯示琥珀色「尚欠 $30」，而發票本
    //    與應收帳款都說收齊了。同一個畫面兩個答案，正是 collection_fields 把
    //    布林算好送過來要消滅的東西（見 routers/crm/finance.py 的 docstring）。
    const owed = _sum(rows.filter(i => !i.settled), 'outstanding');
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
        ${cell('尚欠', money(owed), owed > 0 ? '#fbbf24' : '#6b7280')}
        ${left === null
            ? cell('還能開', '<span style="font-size:12px;">合約金額未填</span>', '#6b7280')
            : cell('還能開', money(left), left < 0 ? '#fca5a5' : '#93c5fd')}
    </div>`;
}

/** 申請人下拉。
 *
 * 🔴 票上的名字不在目前清單裡時（有人把它從發票本的 ⚙️ 移掉、或歷史資料），
 *    要把它補成一個選項並選起來。少了這一段，下拉會顯示「—」——畫面在說
 *    「這張沒有申請人」，而它其實有；接著只要有人動同一列的品項，整包寫回就會
 *    把那個名字真的清掉。畫面說謊在先，資料損毀在後。 */
function _applicantCell(inv) {
    const cur = inv.applicant || '';
    const names = _applicants.includes(cur) || !cur ? _applicants : [cur, ..._applicants];
    return `<select class="crm-input" data-inv-meta="applicant"
                style="font-size:11px;padding:2px 5px;min-width:76px;"
                onchange="window._projInv.setMeta('${_esc(inv.id)}','applicant',this)">
        <option value=""${cur ? '' : ' selected'}>—</option>
        ${names.map(n => `<option value="${_esc(n)}"${n === cur ? ' selected' : ''}>${_esc(n)}</option>`).join('')}
      </select>`;
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
          ${th('發票號')}${th('日期')}${th('品名')}${th('申請人')}${th('品項')}
          ${th('金額', 1)}${th('已收', 1)}${th('開立')}${th('收款')}${th('')}
        </tr></thead>
        <tbody>${rows.map(i => `
          <tr style="border-top:1px solid #2a2a2a;">
            <td style="padding:5px 8px;color:#ddd;">${_esc(i.invoice_number || '無號')}</td>
            <td style="padding:5px 8px;color:#9ca3af;">${_esc((i.invoice_date || '').substring(0, 10))}</td>
            <td style="padding:5px 8px;color:#eee;">${_esc(i.title || '')}</td>
            <td style="padding:3px 6px;">${_applicantCell(i)}</td>
            <td style="padding:3px 6px;">
              <input class="crm-input" data-inv-meta="item_type"
                     style="font-size:11px;padding:2px 5px;width:104px;"
                     value="${_esc(i.item_type || '')}" placeholder="影片製作…"
                     onchange="window._projInv.setMeta('${_esc(i.id)}','item_type',this)">
            </td>
            <td style="padding:5px 8px;text-align:right;color:#eee;">$${fmtNum(i.amount_total || 0)}</td>
            <td style="padding:5px 8px;text-align:right;color:#9ca3af;">$${fmtNum(i.collected || 0)}</td>
            <td style="padding:5px 8px;">${invoiceIssueBadge(i.issue_status)}</td>
            <td style="padding:5px 8px;">${invoicePayBadge(i.payment_status)}</td>
            <td style="padding:5px 8px;text-align:right;">
              <button class="crm-btn crm-btn-secondary crm-btn-sm" title="刪除這張發票"
                      onclick="window._projInv.del('${_esc(i.id)}')">刪除</button>
            </td>
          </tr>`).join('')}</tbody>
      </table>
    </div>
    <div style="color:#6b7280;font-size:11px;margin-top:8px;">
      要改內容、作廢、上傳電子發票或配收款 —— 到「帳務 → 發票」那一頁。
      這裡負責開票、刪票與看進度。兩邊是同一張票：那邊改了這裡就是改後的，
      哪一邊刪掉另一邊都會跟著不見。
    </div>`;
}

/** 開發票視窗：欄位刻意少。客戶、抬頭、統編、專案、類別全部從專案帶，
 *  PM 只要填品名和金額。那正是這個入口跟發票本的差別 —— 發票本每次都要重選一次，
 *  而且選錯了沒有人會知道。 */
_P.create = function _openCreate() {
    if (!_cur) return;
    let host = document.getElementById('proj-inv-modal');
    if (!host) return;
    // 發票模組現在住在收付款分頁的 hidden 宿主裡：浮層在 display:none 的祖先底下永遠畫不出來 → 搬到 body
    if (host.closest('[hidden]')) {
        document.querySelectorAll('body > #proj-inv-modal').forEach((x) => { if (x !== host) x.remove(); });
        document.body.appendChild(host);
    }
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
          <label style="color:#9ca3af;font-size:12px;">申請人</label>
          <select id="proj-inv-applicant" class="crm-input">
            <option value="">—</option>
            ${_applicants.map(n => `<option value="${_esc(n)}">${_esc(n)}</option>`).join('')}
          </select>
          <label style="color:#9ca3af;font-size:12px;">品項</label>
          <input id="proj-inv-item" class="crm-input" placeholder="影片製作/展場攝影...">
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
                applicant: document.getElementById('proj-inv-applicant').value,
                item_type: document.getElementById('proj-inv-item').value.trim(),
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

/** 就地補上申請人／品項 —— 這兩個是純標註欄位（沒有金額、方向、狀態），
 *  所以是「編輯一律去發票本」那條規則裡唯一開的口（owner 2026-08-24：
 *  「這裡要可以填申請人跟品項」。票通常就是在這一頁開的，開完卻要換一頁才能
 *  補這兩格，那正是這個入口存在要消滅的來回）。
 *
 * 🔴 PUT /invoices/{id} 是**整包寫回** —— model_dump 逐欄 setattr，沒送的欄位
 *    會被 schema 預設值覆蓋（端點自己的註解就寫著「空字串照樣會寫進去」）。
 *    只送要改的那一欄，品名、金額、抬頭、統編、專案連結會全部被洗掉。所以這裡
 *    先 GET 整張票、換掉一欄、再整包送回 —— 跟發票本同一條路（window._invEdit
 *    也是先 GET 再開表單）。已驗：GET /invoices/{id} 涵蓋 InvoicePayload 全部
 *    22 個欄位，所以這個來回是無損的，並由單元測試釘住。
 * ⚠ 不可以改成拿清單那一列當底稿省一趟。現在剛好夠，但清單序列化與寫入
 *    payload 是兩份定義 —— 哪天分岔，這裡就會靜默清掉欄位而沒有人會發現。
 */
_P.setMeta = async (id, field, el) => {
    const inv = _invoices.find(x => x.id === id);
    if (!inv) return;
    const value = (el.value || '').trim();
    if ((inv[field] || '') === value) return;      // 沒真的改就不要打後端
    el.disabled = true;
    try {
        const full = await _fetch('/invoices/' + id);
        await _fetch('/invoices/' + id,
                     { method: 'PUT', body: JSON.stringify({ ...full, [field]: value }) });
        inv[field] = value;
        crmToast('已更新');
    } catch (e) {
        el.value = inv[field] || '';               // 失敗要退回原值，別讓畫面說謊
        alert(e.message || '更新失敗');
    } finally {
        el.disabled = false;
    }
};

/** 刪除這張發票 —— 打的是發票本同一支端點，所以兩邊看到的結果一定一樣。
 *
 * 🔴 確認框要把後果講出來。已經配到收款的發票被刪掉時，後端會連著把
 *    crm_cash_invoice_links 的分配列一起清掉、並改指或清空那些收支列的主要發票
 *    —— 錢還在帳上，但它對到的發票不見了。PM 在專案頁按這顆按鈕時，畫面上只有
 *    「已收 $X」一個數字，不會自己想到那件事，所以由這裡明講。
 */
_P.del = async (id) => {
    const inv = _invoices.find(x => x.id === id);
    if (!inv) return;
    const got = Number(inv.collected) || 0;
    const warn = got
        ? `\n\n⚠ 這張已收 $${fmtNum(got)} —— 刪掉會一併解除那些收款的配對，`
          + `錢還在收支明細裡，但不再對到任何發票。`
        : '';
    if (!confirm(`確定刪除「${inv.title || ''}」$${fmtNum(inv.amount_total || 0)}？`
                 + `${warn}\n\n發票本那一頁也會跟著消失。`)) return;
    try {
        await _fetch('/invoices/' + id, { method: 'DELETE' });
        crmToast('發票已刪除');
        const r = await _fetch('/invoices?project_id=' + encodeURIComponent(_cur.id));
        _invoices = r.invoices || [];
        _renderTab();
    } catch (e) {
        // 鎖帳月會回 409 —— 那是規則不是故障，原文照顯示比「刪除失敗」有用
        alert(e.message || '刪除失敗');
    }
};

/** 分頁入口。專案換了就整頁重畫（跟其他 lazy 分頁同一個約定）。 */
let _hostId = 'proj-pay-invoices';   // 唯一的宿主：收付款分頁（舊的獨立發票分頁 2026-09-04 併掉了）
export async function loadInvoicesTab(projectId, hostId, preloaded = null) {
    if (hostId) _hostId = hostId;            // 收付款分頁把發票嵌進 #proj-pay-invoices
    const host = document.getElementById(_hostId);
    if (!host || !projectId) return;
    host.innerHTML = '<div style="color:#888;padding:20px;">載入中…</div>';
    try {
        // 🔴 專案回應只有 client_short_name（代稱），發票要的是**全名抬頭**與統編
        //    —— 那兩個只在客戶主檔裡。撈不到就留空讓人自己填，不要拿代稱當抬頭
        //    （代稱是「泛亞」，抬頭是「泛亞工程顧問股份有限公司」，開錯要作廢重開）。
        //    走 crmCacheFetch 的共用快取（列表本來就帶 full_name / tax_id），
        //    比逐次 GET /clients/{id} 少一趟序列往返，也不會跟別頁的客戶資料分岔。
        const [proj, inv, cli, app] = await Promise.all([
            preloaded?.proj ? Promise.resolve(preloaded.proj) : _fetch('/projects/' + projectId),      // 收付款分頁已抓過就直接用
            preloaded?.invoices ? Promise.resolve({ invoices: preloaded.invoices }) : _fetch('/invoices?project_id=' + encodeURIComponent(projectId)),
            crmCacheFetch('clients', '/clients').catch(() => ({ clients: [] })),
            // 申請人清單撈不到不該讓整個分頁掛掉 —— 那只會讓下拉變空
            crmCacheFetch('invoice_applicants', '/invoice-applicants')
                .catch(() => ({ applicants: [] })),
        ]);
        _cur = proj.project || proj;
        _invoices = inv.invoices || [];
        _client = (cli.clients || []).find(c => c.id === _cur?.client_id) || null;
        _applicants = app.applicants || [];
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
    const host = document.getElementById(_hostId);
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

_P.reload = (id) => { const pid = id || _cur?.id; if (pid) { loadInvoicesTab(pid); window._projPay?.refresh?.(); } };
