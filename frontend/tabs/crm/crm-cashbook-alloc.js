// ────────────────────────────────────────────────────────────────────────────
// crm-cashbook-alloc.js —— 收支明細的一段：關聯發票／請款（合併匯款、分期收款；一筆收支掛多張、各帶分配金額，金額檢查由後端算）
//
// 2026-09-12 從 crm-cashbook.js（2,844 行，超過單次讀取上限）原樣切出來。主檔的狀態（_entries／_invoiceList…）
// 用 ES module 的 live binding 讀，**這裡不賦值**（賦值只在主檔）；主檔再 import 這裡的函式回去 ——
// 循環 import 只在函式內用到，模組頂層不碰對方的東西。掃原始碼的測試用 _srcscan.cashbook_src()（主檔＋五段串起來）。
// ────────────────────────────────────────────────────────────────────────────
import { bankOnly as _bankOnly, finFetch as _finFetch } from '../finance/fin-utils.js';
import { esc as _esc, crmFetch as _fetch, fmtNum as _fmtNum, autoFee, today } from './crm-utils.js';
import { paymentHay } from '../../js/shared/project-picker.js';
import { _bankAccounts, _cardSummary, _invoiceList, _loadLinkLists, _paymentList, loadEntries, renderDetail } from './crm-cashbook.js';
import { _outstanding } from './crm-cashbook-fields.js';

// ── 關聯發票（合併匯款 / 分期收款）──────────────────────────
//
// 一筆收款可以掛多張發票、各自帶分配金額。金額檢查由後端算（唯一正本，
// 前端不重算一次規則 —— 兩邊各寫一套遲早會講出不同的話）。
//
// 只有收入列才有這區：支出列掛發票是代開付出去那側，語意不同，不在這裡管。

const _CASH_ALLOC = { entryId: null, items: [], check: null };

/** 狀態列。合計即時反映畫面上的數字；**判讀語**只在畫面與後端一致時才顯示 ——
 *  改了還沒存就把舊判讀掛在新數字旁邊，等於拿過期的話騙人。判讀規則的唯一
 *  正本在後端（_alloc_verdict），前端不重寫一份。 */
function _allocStatusLine(check, items) {
    if (!check) return '';
    // 分配＝**總額**加總（跟後端 check.allocated 同一把尺 —— 用現金比的話，
    // 有匯費時畫面永遠顯示「少 30」而後端說相符，兩邊各講各的）
    const live = items.reduce((n, x) => n + (Number(x.amount) || 0), 0);
    const feeSum = items.reduce((n, x) => n + (Number(x.fee) || 0), 0);
    const dirty = live !== check.allocated;
    const color = dirty ? '#fbbf24' : _allocColor(check.state);
    const tail = dirty ? '尚未儲存 —— 存檔後才會重新檢查' : _esc(check.message);
    // 「實收」還是「實付」由後端的 side 決定並隨判讀一起送過來 —— 前端不必
    // 也不該猜自己在哪一側（本來是 `check.received != null ? … : check.paid …`，
    // 那個三元式存在的唯一理由是後端兩支 verdict 的回傳形狀漂開了）。
    const actual = check.actual_label
        ? ` / ${check.actual_label} $${_fmtNum(check.actual)}` : '';
    // 有匯費就講清楚那幾十塊去哪了 —— 不然使用者只看到「分配 149,900 /
    // 實收 149,900」，會以為帳戶真的多收了 30
    const feeNote = feeSum
        ? `<span style="color:#fbbf24;">（其中 $${_fmtNum(feeSum)} 被匯出行扣走，`
          + `實際入帳 $${_fmtNum(live - feeSum)}）</span>` : '';
    return `<div style="margin-top:8px;font-size:12px;color:${color};">`
        + `分配 $${_fmtNum(live)}${feeNote}${actual} —— ${tail}</div>`;
}

function _allocColor(state) {
    return { ok: '#86efac', fee: '#fbbf24', over: '#fca5a5',
             under: '#fca5a5', empty: '#888' }[state] || '#888';
}

/** 這筆收款還沒分配掉的入帳金額 —— 純函式，殘額由呼叫端餵：面板餵自己的
 *  (check.actual, items)，列表的就地連結餵那一列的。掛上一張發票時拿它預帶匯費：
 *  「還沒收的部分」比「還剩多少錢可分」多幾十塊 → 那幾十塊就是被匯出行扣走的
 *  （check.actual 是後端送來的實收，收付兩側同一個欄位，見 _allocStatusLine）。 */
export function _allocRemain(actual, items) {
    const used = (items || []).reduce((n, x) => n + (Number(x.amount) || 0), 0);
    return Math.max(0, (Number(actual) || 0) - used);
}

/** PUT 的 items 形狀：{idKey, amount[, fee]} —— 面板存檔與就地連結同一份投影。 */
export function _allocBody(c, items) {
    return items.map(x => ({
        [c.idKey]: x[c.idKey], amount: Number(x.amount) || 0,
        ...(c.perItemFee ? { fee: Math.max(0, Math.round(Number(x.fee) || 0)) } : {}),
    }));
}

// 兩側（收款掛發票／匯款掛請款單）是**同一個面板**，只是換掉九樣東西。
//
// 🔴 本來是兩份複本，~150 行對 ~150 行，差別只有端點、id key、標籤、預帶
//    規則和一顆額外的按鈕。它們在同一次 commit 裡就已經漂開三處：狀態列
//    的 msg vs message、預帶面額 vs 尚欠、候選有沒有濾掉結清的。兩份的
//    代價不是「多打一次字」，是**同一個詳情面板裡的兩塊長得不一樣**。
//
// 差異全部收在這張表裡；新增第三種分配（預支結算、零用金整批）＝ 加一筆。
export const _ALLOC_SIDES = {
    invoice: {
        state: () => _CASH_ALLOC,
        prefix: 'alloc', path: 'invoices', idKey: 'invoice_id', noun: '發票',
        // 🔴 收款側的匯費是**逐張**的：匯出行對每一張發票的匯款各扣一次
        //    （客戶匯三張的錢，可能只有其中一張被扣了 30）。付款側相反 ——
        //    跨行手續費是對「那一筆匯出」收一次，所以那邊是整筆一顆按鈕。
        perItemFee: true,
        placeholder: '輸入發票號碼／抬頭／專案名稱找發票…',
        emptyText: '還沒掛任何發票',
        missingText: '（發票已刪除）',
        itemTitle: it => it.title || '(無標題)',
        itemMeta: it => `${_esc(it.invoice_number || '無號碼')} · 發票 $${_fmtNum(it.amount_total)}`
            + (it.collected != null ? ` · 這張總共已收 $${_fmtNum(it.collected)}` : '')
            + ((it.outstanding || 0) > 0
                ? `<span style="color:#fbbf24;">，尚欠 $${_fmtNum(it.outstanding)}</span>` : ''),
        // 🔴 刻意**不**改成 _invoiceCandidates（那支會濾掉已收齊的）：兩側的
        //    候選規則本來就不一樣，那是產品決定不是重構決定。這裡統一的是
        //    結構，不是規則 —— 想收斂的話要先問 owner 收齊的發票還要不要能搜到。
        candidates: (q, picked) => _invoiceList.filter(
            i => !picked.has(i.id) && [i.invoice_number, i.title, i.company_name, i.project_name]
                .some(v => (v || '').toLowerCase().includes(q))),
        hitLine: i => `${_esc(i.invoice_number || '無號碼')} · ${_esc((i.title || '').substring(0, 26))}`
            + `<span style="color:#fbbf24;"> $${_fmtNum(i.amount_total || 0)}</span>`
            + ((i.collected || 0) > 0
                ? `<span style="color:#86efac;"> · 已收 $${_fmtNum(i.collected)}${
                    (i.outstanding || 0) > 0 ? `，尚欠 $${_fmtNum(i.outstanding)}` : '（收齊）'}</span>` : '')
            + (i.company_name ? `<span style="color:#666;"> · ${_esc(i.company_name)}</span>` : ''),
        // 預帶「還沒收的部分」而不是面額 —— 分期收款時面額是錯的（發票 100,000
        // 已收 60,000，第三期預帶 100,000 只會讓人重打一次）。沒收過時兩者相同。
        //
        // 匯費也預帶一次（只在掛上的當下算，之後不再自己動）：這筆收款還沒分配掉
        // 的入帳金額比「還沒收的部分」少幾十塊時，那就是被匯出行扣走的
        // （owner 2026-08-23：「自動幫我填寫匯費，格子我可以修改調整」）。
        // `remainCash`＝這筆收款還沒分配掉的入帳金額：面板用自己的殘額，列表的
        // 就地連結餵那一列自己的 —— 兩邊同一支，匯費規則只有這一份
        toItem: (i, remainCash) => ({
            invoice_id: i.id, amount: _outstanding(i),
            fee: autoFee(_outstanding(i), remainCash),
            invoice_number: i.invoice_number || '', title: i.title || '',
            amount_total: i.amount_total || 0,
            payment_status: i.payment_status || '', missing: false,
        }),
    },
    payment: {
        state: () => _CASH_PAY,
        prefix: 'pay', path: 'payments', idKey: 'payment_request_id', noun: '請款單',
        canBookFee: true,
        placeholder: '輸入收款人／摘要找請款單…',
        emptyText: '還沒掛任何請款單',
        missingText: '（請款單已刪除）',
        itemTitle: it => it.summary || '(無摘要)',
        itemMeta: it => `${_esc(it.payee_name || '無收款人')} · 單據 $${_fmtNum(it.request_total)}`
            + (it.request_paid ? ` · 這張總共已付 $${_fmtNum(it.request_paid)}` : '')
            + (!it.request_settled && (it.request_open || 0) > 0
                ? `<span style="color:#fbbf24;">，尚欠 $${_fmtNum(it.request_open)}</span>` : ''),
        // 已付款的在 _loadLinkLists 就被後端篩掉了（不是在這裡濾 800 筆）。
        // 搜尋欄位走共用那份（paymentHay）—— 挑選視窗與這裡打同一批
        // _paymentList，欄位集合各寫一份就會出現「A 找得到 B 找不到」
        candidates: (q, picked) => _paymentList.filter(
            p => !picked.has(p.id) && paymentHay(p).includes(q)),
        hitLine: p => `${_esc(p.payee_name || '無收款人')} · ${_esc((p.summary || '').substring(0, 26))}`
            + `<span style="color:#fbbf24;"> $${_fmtNum(p.amount || 0)}</span>`
            + `<span style="color:#666;"> · ${_esc(p.payment_status || '')}</span>`,
        // 預帶面額。收款側預帶的是「未收部分」，但 /payments 清單沒有回已付金額
        //（只有 payment_status），這裡算不出來 —— 與其憑空猜，不如帶面額讓人改；
        // 已付清的那些已經被候選篩掉了。
        toItem: p => ({
            payment_request_id: p.id, amount: Number(p.amount) || 0,
            summary: p.summary || '', payee_name: p.payee_name || '',
            request_total: Number(p.amount) || 0, request_paid: 0,
            request_open: Number(p.amount) || 0,
            payment_status: p.payment_status || '', missing: false,
        }),
        // 🔴 已經掛上的那幾張要補進候選：_paymentList 只裝「還沒付的」，而掛上去
        //    之後那張多半已經變成已付款。不補的話，使用者把它移掉就再也選不回來
        //    （收款側用 _invoiceCandidates(keepId) 解同一個問題）。
        absorbLinked: (items) => {
            const known = new Set(_paymentList.map(p => p.id));
            items.forEach((it) => {
                if (it.missing || known.has(it.payment_request_id)) return;
                _paymentList.push({
                    id: it.payment_request_id, amount: it.request_total,
                    summary: it.summary, payee_name: it.payee_name,
                    payment_status: '已付款', project_label: '', category: '',
                });
            });
        },
    },
};

const _CASH_PAY = { entryId: null, items: [], check: null };

function _sideOf(side) {
    return _ALLOC_SIDES[side];
}

async function _loadAllocs(side, entryId) {
    const c = _sideOf(side);
    const st = c.state();
    st.entryId = entryId;
    try {
        const r = await _fetch(`/cash-entries/${entryId}/${c.path}`);
        st.items = r.items || [];
        st.check = r.check || null;
        if (c.absorbLinked) c.absorbLinked(st.items);
    } catch (e) {
        st.items = [];
        st.check = { state: 'error', message: '讀取失敗：' + e.message };
    }
    _renderAllocs(side);
}

/** 沒掛過任何請款單的支出列 —— 空狀態不必跟後端要（見 renderDetail 的註解）。 */
export function _renderEmptyPayBox(entryId) {
    _CASH_PAY.entryId = entryId;
    _CASH_PAY.items = [];
    _CASH_PAY.check = null;
    _renderAllocs('payment');
}

export async function loadCashInvoiceAllocs(entryId) {
    return _loadAllocs('invoice', entryId);
}

export async function loadCashPaymentAllocs(entryId) {
    return _loadAllocs('payment', entryId);
}

function _renderAllocs(side) {
    const c = _sideOf(side);
    const box = document.getElementById(`cash-${c.prefix}-box`);
    if (!box) return;
    const { items, check } = c.state();
    const rows = items.map((it, i) => `
        <div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #2a2a2a;">
            <div style="flex:1;min-width:0;">
                <div style="color:#ddd;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                    ${_esc(c.itemTitle(it))}${it.missing ? ` <span style="color:#fca5a5;">${c.missingText}</span>` : ''}</div>
                <div style="color:#888;font-size:11px;">${c.itemMeta(it)}</div>
            </div>
            <input type="number" value="${it.amount}" data-alloc-i="${i}"
                   title="這張發票被認列收到多少（含被匯出行扣掉的那幾十塊）"
                   style="width:96px;text-align:right;background:#1a1a1a;border:1px solid #333;
                          color:#eee;border-radius:4px;padding:3px 6px;font-size:12px;">
            ${c.perItemFee ? `<input type="number" value="${it.fee || ''}" data-alloc-fee="${i}"
                   placeholder="匯費"
                   title="被匯出行扣掉、沒進到我們帳戶的那幾十塊。面額減分配還有餘額時會自動帶，可以改。"
                   style="width:74px;text-align:right;background:#1a1a1a;border:1px solid #333;
                          color:${it.fee ? '#fbbf24' : '#eee'};border-radius:4px;padding:3px 6px;font-size:12px;">` : ''}
            <button data-alloc-del="${i}" title="移除這張"
                    style="background:none;border:none;color:#888;cursor:pointer;font-size:14px;">✕</button>
        </div>`).join('');
    // 判為手續費時給一顆「認列成匯費」—— 那幾元本來只是對不起來的差額，寫進
    // bank_fee 之後就是管理費用（見 recognize_bank_fee 的「總流出不變」）。
    // 🔴 只有付款側有這顆：收款側改用**逐張一格**的匯費輸入（見 perItemFee）——
    //    匯出行是對每一張發票的匯款各扣一次，一顆整筆的按鈕表達不了。
    const feeBtn = (c.canBookFee && check && check.state === 'fee' && check.fee)
        ? `<button id="cash-${c.prefix}-fee" class="crm-btn crm-btn-sm"
                   style="margin-left:8px;">把 $${_fmtNum(check.fee)} 認列成匯費</button>`
        : '';
    box.innerHTML = `
        ${rows || `<div style="color:#666;font-size:12px;padding:4px 0;">${c.emptyText}</div>`}
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
            <input id="cash-${c.prefix}-search" placeholder="${c.placeholder}"
                   style="flex:1;background:#1a1a1a;border:1px solid #333;color:#eee;
                          border-radius:4px;padding:4px 8px;font-size:12px;">
        </div>
        <div id="cash-${c.prefix}-results" style="max-height:150px;overflow:auto;"></div>
        ${_allocStatusLine(check, items)}
        <div style="margin-top:8px;">
            <button id="cash-${c.prefix}-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存${c.noun}分配</button>
            ${feeBtn}
        </div>`;

    box.querySelectorAll('[data-alloc-i]').forEach((inp) => {
        inp.addEventListener('change', () => {
            const it = c.state().items[Number(inp.dataset.allocI)];
            it.amount = Number(inp.value) || 0;
            // 🔴 **不**從金額格反推匯費。試過「現金 ↔ 總額」互相換算，結果是
            //    改一格另一格跟著動、存一次就把總額疊成 149,930。匯費是使用者
            //    知道、系統猜不準的數字（掛上時預帶一次就夠，見 toItem）。
            _renderAllocs(side);
        });
    });
    box.querySelectorAll('[data-alloc-fee]').forEach((inp) => {
        inp.addEventListener('change', () => {
            c.state().items[Number(inp.dataset.allocFee)].fee =
                Math.max(0, Math.round(Number(inp.value) || 0));
            _renderAllocs(side);
        });
    });
    box.querySelectorAll('[data-alloc-del]').forEach((btn) => {
        btn.addEventListener('click', () => {
            c.state().items.splice(Number(btn.dataset.allocDel), 1);
            _renderAllocs(side);
        });
    });
    const search = box.querySelector(`#cash-${c.prefix}-search`);
    if (search) search.addEventListener('input', () => _allocSearch(side, search.value));
    // id 保持 per-side（cash-alloc-* / cash-pay-*）—— 兩塊可能同時在畫面上
    // （一列同時有收入與支出），共用 id 會撞。列內的 data-alloc-* 則是共用的，
    // 因為那些查詢都框在自己的 box 裡。
    const save = box.querySelector(`#cash-${c.prefix}-save`);
    if (save) save.addEventListener('click', () => _allocSave(side, save, null));
    const fee = box.querySelector(`#cash-${c.prefix}-fee`);
    if (fee) fee.addEventListener('click', () => _allocSave(side, fee, check.fee));
}

function _allocSearch(side, q) {
    const c = _sideOf(side);
    const out = document.getElementById(`cash-${c.prefix}-results`);
    if (!out) return;
    q = (q || '').trim().toLowerCase();
    if (q.length < 1) { out.innerHTML = ''; return; }
    const st = c.state();
    const picked = new Set(st.items.map(x => x[c.idKey]));
    const hits = c.candidates(q, picked).slice(0, 12);
    out.innerHTML = hits.length ? hits.map(x => `
        <div data-alloc-add="${_esc(x.id)}"
             style="padding:4px 6px;cursor:pointer;font-size:12px;color:#ccc;border-bottom:1px solid #262626;">
            ${c.hitLine(x)}
        </div>`).join('')
        : `<div style="color:#666;font-size:12px;padding:4px;">找不到符合的${c.noun}</div>`;
    out.querySelectorAll('[data-alloc-add]').forEach((el) => {
        el.addEventListener('click', () => {
            const hit = hits.find(x => x.id === el.dataset.allocAdd);
            if (!hit) return;
            // 殘額餵面板自己的（付款側的 toItem 不吃第二個參數，多給無妨）
            st.items.push(c.toItem(hit, _allocRemain(st.check && st.check.actual, st.items)));
            _renderAllocs(side);
        });
    });
}

async function _allocSave(side, btn, fee) {
    const c = _sideOf(side);
    const st = c.state();
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
        const body = {
            // 🔴 amount ＝**發票被認列收到多少**（客戶實際付的，含被扣的匯費），
            //    不是進到帳戶的現金。真正入帳的是 amount − fee，後端據此把
            //    deposit 補上去並寫 bank_fee（recognize_receipt_fee，淨流入不變）。
            //    2026-08-24 owner 踩到的就是這裡：只把 30 填進匯費、amount 還停在
            //    149,870 → deposit 被補成 149,900、分配卻沒跟上，那 30 元繞一圈
            //    變成「還有沒掛上的發票」，發票也還是尚欠 30。
            items: _allocBody(c, st.items),
        };
        if (fee != null) body.fee = fee;
        const r = await _fetch(`/cash-entries/${st.entryId}/${c.path}`, {
            method: 'PUT', body: JSON.stringify(body),
        });
        st.items = r.items || [];
        st.check = r.check || null;
        _renderAllocs(side);
        await loadEntries();      // 主要發票／請款單欄與匯費可能都變了
    } catch (e) {
        const box = document.getElementById(`cash-${c.prefix}-box`);
        if (box) {
            const err = document.createElement('div');
            err.style.cssText = 'color:#fca5a5;font-size:12px;margin-top:6px;';
            err.textContent = e.message;
            box.appendChild(err);
        }
    } finally {
        btn.disabled = false;
        btn.textContent = label;
    }
}


/** 信用卡面板：算式攤開 + 記還款 + 設期初。 */
window._cashCardPanel = function () {
    const c = _cardSummary;
    if (!c) return;
    // bankOnly：'哪些算真銀行帳戶' 的正本（fin-utils）—— 自己 filter 會漏掉
    // active 的判斷，停用帳戶就會出現在還款下拉裡
    const accts = _bankOnly(_bankAccounts || []);
    let ov = document.getElementById('cash-card-overlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'cash-card-overlay';
    ov.className = 'crm-modal-overlay';
    ov.style.display = 'flex';
    ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
    ov.innerHTML = `
      <div class="crm-modal" style="max-width:460px;">
        <div class="crm-modal-header"><h3>💳 信用卡</h3>
          <button onclick="document.getElementById('cash-card-overlay').remove()" class="crm-detail-close">✕</button>
        </div>
        <div class="crm-modal-body">
          <table class="crm-table" style="width:100%;font-size:12px;">
            <tr><td style="color:#bbb;">期初（資料起點前的卡債）</td><td style="text-align:right;">${_fmtNum(c.opening)}</td></tr>
            <tr><td style="color:#bbb;">＋ 刷卡（${_fmtNum(c.charge_count)} 筆）</td><td style="text-align:right;color:#c4b5fd;">${_fmtNum(c.charges)}</td></tr>
            <tr><td style="color:#bbb;">− 還款（類別：${_esc(c.repay_categories.join('、') || '未設定')}）</td><td style="text-align:right;color:#86efac;">${_fmtNum(c.repayments)}</td></tr>
            <tr><td style="color:#ddd;font-weight:600;">＝ 未繳</td><td style="text-align:right;font-weight:600;color:#eee;">$${_fmtNum(c.outstanding)}</td></tr>
          </table>

          <div class="crm-form-section" style="margin-top:14px;">記一筆還款</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
            <input type="date" id="cash-card-date" class="crm-input" value="${today()}" style="width:150px;">
            <input type="number" id="cash-card-amt" class="crm-input" placeholder="金額"
                   value="${c.outstanding > 0 ? c.outstanding : ''}" style="width:120px;text-align:right;">
            <select id="cash-card-acct" class="crm-input" style="flex:1;min-width:140px;">
              ${accts.map(a => `<option value="${_esc(a.id)}">${_esc(a.name)}</option>`).join('')}
            </select>
            <button class="crm-btn crm-btn-primary" onclick="window._cashCardRepay(this)">記還款</button>
          </div>
          <div style="color:#666;font-size:11px;margin-top:4px;">
            會建一筆「支出」掛在選定帳戶、類別「${_esc(c.repay_categories[0] || '信用卡')}」——
            那筆錢是這時候才真的離開銀行的。</div>

          <div class="crm-form-section" style="margin-top:16px;">校準期初</div>
          <div style="display:flex;gap:6px;align-items:center;">
            <input type="number" id="cash-card-actual" class="crm-input" placeholder="對帳單上現在實際欠多少" style="flex:1;">
            <button class="crm-btn crm-btn-secondary" onclick="window._cashCardCalib(this)">反推期初</button>
          </div>
          <div style="color:#666;font-size:11px;margin-top:4px;">
            資料從 2023/10 起，卡片在那之前已有餘額 —— 填對帳單上的未繳金額，
            系統回推期初，之後就會自己對得上。</div>
          <div id="cash-card-err" style="display:none;color:#fca5a5;font-size:12px;margin-top:8px;"></div>
        </div>
      </div>`;
    document.body.appendChild(ov);
};

window._cashCardRepay = async function (btn) {
    const err = document.getElementById('cash-card-err');
    const show = (m) => { err.textContent = m; err.style.display = 'block'; };
    const amt = Number(document.getElementById('cash-card-amt').value) || 0;
    const acct = document.getElementById('cash-card-acct').value;
    if (amt <= 0) return show('請填還款金額');
    if (!acct) return show('請選擇從哪個帳戶扣款');
    btn.disabled = true;
    try {
        await _fetch('/cash-entries', {
            method: 'POST',
            body: JSON.stringify({
                entry_date: document.getElementById('cash-card-date').value,
                summary: '信用卡還款', expense: amt,
                category: (_cardSummary.repay_categories[0] || '信用卡'),
                bank_account_id: acct,
            }),
        });
        document.getElementById('cash-card-overlay').remove();
        await loadEntries();
    } catch (e) { show(e.message); }
    finally { btn.disabled = false; }
};

window._cashCardCalib = async function (btn) {
    const err = document.getElementById('cash-card-err');
    const v = document.getElementById('cash-card-actual').value;
    if (v === '') { err.textContent = '請填現在實際欠多少'; err.style.display = 'block'; return; }
    btn.disabled = true;
    try {
        await _finFetch('/card-summary', {
            method: 'PUT', body: JSON.stringify({ derive_opening_from: Number(v) }),
        });
        document.getElementById('cash-card-overlay').remove();
        await loadEntries();
    } catch (e) { err.textContent = e.message; err.style.display = 'block'; }
    finally { btn.disabled = false; }
};
