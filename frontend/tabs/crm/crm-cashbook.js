/**
 * crm-cashbook.js — 收支明細子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, projectOptionsHtml, today, autoFee, crmToast, searchableSelect } from './crm-utils.js';
// 兩本帳（公司實體）— docs/LEDGER_ENTITY_PLAN.md §5。帳本由頁面隱形 pin：
// 財務 tab＝'parent'（預設）、/my-ledger.html＝'mine'（該頁在載入財務模組前設
// window._finEntity）。無使用者可見的帳本選單（單一 tab 單一帳本）。query 一律帶
// pin 值；payload 只在 'mine' 才帶 entity:'mine'（後端 None 語意：建立落 parent、
// 更新維持既有值 —— 不洗欄位）。pin 與 /api/v1/finance 的 fetch 都用財務模組那份，
// 不在這裡複寫（finFetch 會自己附 entity）。
import { finEntity as _pinEntity, finFetch as _finFetch, finIsMine,
         ledgerHasInvoices, bankOnly as _bankOnly } from '../finance/fin-utils.js';
// 六日／國定假日標記（owner 2026-08-27）：看帳時「那天是不是假日」是判斷公私的
// 關鍵線索，日期字串本身看不出來。星期是算的、假日是清單 —— 見該模組檔頭。
import { dayMark as _dayMark } from '../../js/shared/tw-calendar.js';
// 專案連結改用可搜尋的挑選視窗（owner 2026-09-01「專案列表要可以勾選、搜尋」——
// 私帳 405 個專案塞原生下拉等於沒得選）。共用件，別在這裡再刻一份。
import { openInvoicePicker, openPaymentPicker, openProjectPicker,
         paymentHay, paymentLabel } from '../../js/shared/project-picker.js';
import { splitBadgeHtml, splitGross } from '../../js/shared/cash-split-editor.js';
import { indexTax as _indexTaxShared, taxKidsAt as _kidsAt, taxSelects }
    from '../../js/shared/cash-tax-picker.js';

let _entries = [];
let _invoiceList = [];
let _paymentList = [];   // 請款單（付款側的分配用）
let _projectList = [];
let _clientList = [];
let _bankAccounts = null;   // 財務模組銀行帳戶；null = 載入失敗/未啟用（優雅降級：不顯示帳戶欄）
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', bank_account_id: '', direction: '',
                 date_from: '', date_to: '', amount_min: '', amount_max: '',
                 node_id: '',            // 分類樹：選到哪一層就看那一支整支
                 status: '' };           // 'card'＝只看信用卡明細（卡片頁籤）
// 分類樹（正本＝後端 cash_taxonomy_nodes；深度不限）。_taxById 是 id → **從根到
// 它的節點物件鏈** —— 編輯與篩選都要「這一層的上層是誰」，每次現爬會爬很多次。
let _taxTree = [];
let _taxById = {};
// 第 i 層的值域＝上一層節點的子節點（第 0 層＝整棵樹的根）。
// 篩選器與格內編輯器共用 —— 這是「每一格能選什麼」的唯一規則。
const _taxKidsAt = (chain, i) => _kidsAt(_taxTree, chain, i);
// 批次分類（owner 2026-08-28）：信用卡明細一天好幾筆「街口電支－統一超商」，
// 一筆一筆點三格要按上千次。on＝批次模式（點列＝選取，不開編輯）、
// sel＝選到的 id、last＝上一次點的（Shift 連選的錨點）、chain＝要套的分類路徑。
let _batch = { on: false, sel: new Set(), last: null, chain: [] };
let _csvFile = null;

function _toggleAdvanceFields(isAdv) {
    // 每一格的可見性一次算完 —— 原本是「先照 !isAdv 全部顯示、再把發票欄蓋掉」，
    // 補丁蓋補丁：下一個看的人會以為發票欄真的跟著 isAdv 走。
    [['cash-advance-section', isAdv],
     ['cash-field-project', !isAdv],
     ['cash-field-invoice', !isAdv && ledgerHasInvoices()],
     ['cash-field-bankfee', !isAdv]].forEach(([id, on]) => {
        const el = document.getElementById(id);
        if (el) { el.style.display = on ? '' : 'none'; }
    });
}

// ── Data Loading ────────────────────────────────────────────

async function loadEntries({ render = true, cards = true } = {}) {
    const params = new URLSearchParams();
    if (_filters.q)               params.set('q', _filters.q);
    if (_filters.category)        params.set('category', _filters.category);
    if (_filters.bank_account_id) params.set('bank_account_id', _filters.bank_account_id);
    if (_filters.direction)       params.set('direction', _filters.direction);
    if (_filters.date_from)       params.set('date_from', _filters.date_from);
    if (_filters.date_to)         params.set('date_to', _filters.date_to);
    if (_filters.node_id)         params.set('node_id', _filters.node_id);
    if (_filters.status)          params.set('status', _filters.status);
    if (_filters.amount_min)      params.set('amount_min', _filters.amount_min);
    if (_filters.amount_max)      params.set('amount_max', _filters.amount_max);
    params.set('entity', _pinEntity());
    // 🔴 卡片摘要只跟 entity 有關，與 q/類別/帳戶/方向這些篩選無關 ——
    // loadEntries 綁在搜尋框、兩個下拉、每個帳戶頁籤與 8 條存檔後路徑上，
    // 每次篩選都重算一次卡片（三個聚合）而數字永遠一樣。只在真的可能變的
    // 時候拉：第一次載入與異動之後。
    const [entries] = await Promise.all([
        _fetch('/cash-entries?' + params).then(r => r.entries || []).catch(() => []),
        cards ? loadCardSummary() : Promise.resolve(),
    ]);
    _entries = entries;
    _syncFilterOptions();     // 資料換了才要重算選項（不放 renderList —— 那支連點一列都會跑）
    if (render) renderList();
}

/** 兩份關聯選單的來源（發票／請款單）。開 tab 與每次存檔後各載一次。 */
async function _loadLinkLists() {
    // 兩支互不相干 —— 串行 await 等於把外層的 Promise.all 自己拆掉。
    //
    // 🔴 請款單只抓**還沒付的**（後端 payment_status=應付款 會一起帶未付款）。
    //    實測生產 810 張裡 805 張已付款 —— 全抓回來只為了讓下拉列出 5 個候選，
    //    而且每存一筆收支就重抓一次。篩選條件端點本來就吃（finance.py:1027）。
    //    發票側的候選規則在 _invoiceCandidates（判準是 outstanding 不是狀態字串），
    //    請款單目前沒有對應的欄位可用，先用狀態篩 —— 見 _payCandidates。
    const ent = _pinEntity();
    const [inv, pay] = await Promise.all([
        _fetch('/invoices?entity=' + ent).then(r => r.invoices || []).catch(() => []),
        _fetch('/payments?entity=' + ent + '&payment_status=應付款')
            .then(r => r.payments || []).catch(() => []),
    ]);
    _invoiceList = inv;
    _paymentList = pay;
}

async function _loadProjectList() {
    // 🔴 要帶帳本：不帶＝兩本都回，私帳的 402 案會混進母公司的下拉、反之亦然。
    // 選到另一本帳的專案，那筆錢在**兩本帳的掛帳支出裡都不會出現**（rollup 按
    // entity 篩收支、再按 entity 迭代專案，跨帳本的組合兩邊都對不上）。
    try {
        _projectList = (await _fetch('/projects?entity=' + _pinEntity())).projects || [];
    } catch (_) { _projectList = []; }
}

async function _loadClientList() {
    try { _clientList = (await _fetch('/clients')).clients || []; } catch(_) { _clientList = []; }
}

async function _loadBankAccounts() {
    // 財務模組帳戶清單（prefix /api/v1/finance，非 crm）— 失敗優雅降級：隱藏帳戶欄、不擋原功能
    // 要帶餘額（with_balances 預設 1）—— 帳戶切換列每顆都標當下餘額
    // 帳戶屬於哪家公司（兩本帳）：finFetch 自動附頁面 pin 的 entity，選單只列該帳本的帳戶
    try {
        _bankAccounts = (await _finFetch('/bank-accounts')).items || [];
    } catch (_) { _bankAccounts = null; }
    _acctNameMap = new Map((_bankAccounts || [])
        .map((a) => [String(a.id), a.name || '']));
}

// ── List ────────────────────────────────────────────────────

const _sorter = createSortable({
    storageKey: 'crm_cashbook_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'cash-list-panel',
    onChange: () => renderList(),
    // 🔴 getter 不用各自 .toLowerCase()：共用排序器的 `_sortKey` 已經在
    // decorate 那一步統一正規化（每列一次），getter 再做一次是純冗餘。
    // ⚠ 這條目前只有本檔遵守 —— crm-invoices / crm-payments / crm-projects-core
    // / crm-quotes / crm-staff / crm / crm-payables / crm-receivables 還有 23 個
    // getter 各背一次（不影響結果，只是白做）。它們在這次清理的範圍外，
    // 動到的話是八個檔的機械性修改；要收的話一次收乾淨，別留一半。
    getters: {
        date:     e => e.entry_date || '',
        summary:  e => e.summary || '',
        deposit:  e => e.deposit || 0,
        expense:  e => _bankOut(e),
        card:     e => _cardAmt(e),
        book:     e => e.book || '',
        item:     e => e.item || '',
        sub_item: e => e.sub_item || '',
        bank_memo: e => e.bank_memo || '',
        project:  e => e.project_name || '',
        invoice:  e => e.invoice_title || '',
        payment:  e => e.payment_label || '',
        account:  e => _acctName(e.bank_account_id),
        note:     e => e.note || '',
    },
});

/** 多行備註壓成一行（列表格子只有一行高，換行會被吃掉看不出斷點）。 */
function _flat(text, sep) {
    return String(text || '').split('\n').filter(Boolean).join(sep);
}

/** 這一列的分類收不收得下專案（＝前端側的 cash_can_link；值域由後端
 *  /cash-entries/options 供）。三個呼叫點共用，不各寫一次 includes。 */
const _canLinkProj = (e) => _LINKABLE.includes(e.category || '');

/** 這次渲染用的帳本判定（`_rowHtml` 每列都問一次是白費）。 */
let _MINE_LEDGER = false;

/** 帳戶 id → 顯示名。帳戶清單載入失敗（_bankAccounts=null）時回空字串，不炸。
 *  🔴 走查表不走 find：排序時每次比較都會問一次，4,733 列就是上萬次線性搜尋。*/
let _acctNameMap = new Map();
function _acctName(id) {
    return id ? (_acctNameMap.get(String(id)) || '') : '';
}

/** 分類篩選：**一排會長的下拉** —— 選到哪一層就再長一格，跟列的編輯器同一套走法。
 *  選了就篩「那一支整支」（選「家用」＝家用底下全部，不是只有沒細分的那些）。
 *
 *  🔴 值域來自後端的樹（cash_taxonomy_nodes），不是「畫面上這批列用過的」——
 *  篩完之後選單會跟著縮水，就再也切不回去了。
 */
/** 一排會長的分類下拉 —— 篩選器／格內編輯／批次分類三處共用。
 *  正本已抽到 js/shared/cash-tax-picker（對帳單匯入預覽也要用同一套，
 *  owner 2026-08-30「比照私帳收支表的模式」）—— 這裡只補上這個 tab 的樹與
 *  可搜尋升級，其餘參數原樣往下傳。 */
function _taxSelects(box, o) {
    taxSelects(box, {
        ...o,
        tree: _taxTree,
        searchable: (sel, i) => searchableSelect(
            sel, { placeholder: i === 0 ? '搜尋類別…' : '搜尋…' }),
    });
}


function _syncTaxFilter() {
    const box = document.getElementById('cash-filter-tax');
    if (!box) return;
    const chain = _filters.node_id ? (_taxById[_filters.node_id] || []) : [];
    _taxSelects(box, {
        chain,
        cls: 'crm-select cash-taxf',
        blank: (i) => (i === 0 ? '全部類別' : '全部'),
        extraFirst: `<option value="__none__"${
            _filters.category === '__none__' ? ' selected' : ''}>（未分類）</option>`,
        onPick: (i, v) => {
            // 🔴 「（未分類）」是**類別欄為空**的快篩，不是一個叫 __none__ 的節點 ——
            // 當成 node_id 送出去一列都篩不到（舊版當成 book 送，實測 0 筆）
            _filters.category = v === '__none__' ? '__none__' : '';
            _filters.node_id = v === '__none__' ? '' : v
                || (i > 0 && chain[i - 1] ? chain[i - 1].id : '');   // 選「全部」＝退回上一層
            loadEntries({ cards: false });
        },
    });
}

/** 篩選器選項依**實際資料**生成 —— 寫死的清單會跟使用中的類別脫節，
 *  選了卻篩不到東西（原本的「請款/收支」兩個選項就是這樣，永遠 0 筆）。 */
function _syncFilterOptions() {
    _syncTaxFilter();
    _renderAcctTabs();
}

/** 帳戶切換列：總表 + 每個帳戶各一顆，附當下餘額。
 *  餘額由後端 /finance/bank-accounts 算（current_balance）—— 前端不自己加總，
 *  那樣只會算到「目前篩出來的列」，切到單一帳戶時總表數字就會跟著縮水。 */
function _renderAcctTabs() {
    const box = document.getElementById('cash-acct-tabs');
    if (!box) return;
    if (!_bankAccounts || !_bankAccounts.length) { box.innerHTML = ''; return; }
    const money = (n) => (n == null ? '—'
        : `<span class="${n < 0 ? 'neg' : 'pos'}">$${_fmtNum(n)}</span>`);
    // 🔴 只列真銀行帳戶：信用卡帳戶是**卡別身分**不是錢包（2026-08-27），
    // 混進來會多出幾顆餘額是負數的假錢包
    const banks = _bankOnly(_bankAccounts);
    const total = banks.reduce((sum, a) => sum + (a.current_balance || 0), 0);
    const on = (id) => !_filters.status && String(id) === _filters.bank_account_id;
    const tab = (id, name, bal) => `
        <button type="button" class="cash-acct-tab${on(id) ? ' active' : ''}"
                data-acct="${_esc(id)}">${_esc(name)}<b>${money(bal)}</b></button>`;
    box.innerHTML = tab('', '總表', total)
        + banks.map(a => tab(a.id, a.name, a.current_balance)).join('')
        + _cardChipHtml();
    box.querySelectorAll('[data-acct]').forEach(btn => {
        btn.addEventListener('click', () => {
            _filters.bank_account_id = btn.dataset.acct;
            _filters.status = '';           // 回到銀行視角
            loadEntries({ cards: false });   // 換帳戶頁籤不影響卡片餘額
        });
    });
    // 卡片頁籤：切換成信用卡明細（owner 2026-08-27）——
    // data-card='' 是全部卡、有值就是那一張
    box.querySelectorAll('[data-card]').forEach(btn => {
        btn.addEventListener('click', () => {
            _filters.status = 'card';
            _filters.bank_account_id = btn.dataset.card;
            loadEntries({ cards: false });
        });
    });
}

// 卡片摘要（/card-summary：期初＋刷卡−還款＋逐卡）——「有沒有刷卡列」也靠它，
// 沒有就不畫信用卡欄與卡片頁籤（母公司帳 0 筆，永遠空的欄是雜訊）
let _cardSummary = null;

function _cardChipHtml() {
    const c = _cardSummary;
    if (!c || !c.charge_count) return '';
    const n = c.outstanding;
    const act = (id) => (_filters.status === 'card'
        && String(id) === _filters.bank_account_id) ? ' active' : '';
    const money = (v) => `<b class="${v < 0 ? 'pos' : 'neg'}">$${_fmtNum(v)}</b>`;
    // 主 chip：未繳總額（點了看全部卡的明細）
    let html = `<button type="button" class="cash-acct-tab${act('')}" data-card=""
            title="期初 ${_fmtNum(c.opening)} + 刷卡 ${_fmtNum(c.charges)} − 還款 ${_fmtNum(c.repayments)}
點一下看信用卡明細">💳 信用卡未繳${money(n)}</button>`;
    // 逐卡（>1 張才分，1 張時主 chip 就夠了）
    const cards = c.by_card || [];
    if (cards.length > 1) {
        html += cards.map((k) => `<button type="button" class="cash-acct-tab${act(k.id)}"
            data-card="${_esc(k.id)}" title="這張卡的刷卡合計（未繳是全部卡一起算 ——
還款沒記還的是哪張卡）">${_esc(k.name)}${money(k.charges)}</button>`).join('');
    }
    if (c.unassigned_charges) {
        html += `<button type="button" class="cash-acct-tab${act('__none__')}" data-card="__none__"
            title="還沒指定是哪張卡的刷卡列">未指定卡別${money(c.unassigned_charges)}</button>`;
    }
    return html + `<button type="button" class="cash-acct-tab" title="卡片設定／還款"
            onclick="window._cashCardPanel()" style="padding:6px 10px;">⚙</button>`;
}

export async function loadCardSummary() {
    try {
        _cardSummary = await _finFetch('/card-summary');   // finFetch 自帶 entity + token
    } catch (_) { _cardSummary = null; }
    // 有刷卡列才顯示信用卡欄（母公司帳 0 筆 → 永遠空的欄是雜訊）
    const panel = document.getElementById('cash-list-panel');
    if (panel) panel.classList.toggle('has-card', !!(_cardSummary && _cardSummary.charge_count));
}

/** 編輯視窗填「匯費」時，自動從「支出」扣掉同額（總流出不變）。
 *
 *  🔴 owner 2026-08-24 實際踩到：轉存那列銀行實扣 35,015（本金 35,000 + 手續費 15），
 *     他在匯費填 15，支出還是 35,015 → 系統認為流出 35,030，富邦餘額憑空少了 30。
 *     匯費不是「另外再扣一筆」，是**把已經扣掉的那筆錢從本金裡標示出來**。
 *
 *  這條規則在關聯面板那顆「認列成匯費」早就是對的（recognize_bank_fee，總流出
 *  不變）—— 手動編輯這條路沒有，於是同一件事兩個入口兩種結果。這裡補上。
 *
 *  只在**改匯費**時搬；改支出時不動匯費（那時使用者是在改本金）。
 */
function _wireBankFeeSplit() {
    const fee = document.getElementById('cash-f-bank_fee');
    const exp = document.getElementById('cash-f-expense');
    if (!fee || !exp) return;
    let prevFee = Number(fee.value) || 0;
    const hint = document.createElement('div');
    hint.id = 'cash-f-fee-hint';
    hint.style.cssText = 'color:#6b7280;font-size:11px;margin-top:3px;';
    fee.parentNode.appendChild(hint);

    const paint = () => {
        const e = Number(exp.value) || 0;
        const f = Number(fee.value) || 0;
        hint.innerHTML = f
            ? `銀行實扣 <b style="color:#ccc;">$${_fmtNum(e + f)}</b>`
              + ` ＝ 支出 $${_fmtNum(e)} ＋ 匯費 $${_fmtNum(f)}`
              + '<br>（填匯費會自動從支出扣掉 —— 那筆錢本來就包在銀行扣的金額裡）'
            : '';
    };
    fee.addEventListener('input', () => {
        const f = Math.max(0, Math.round(Number(fee.value) || 0));
        const e = Number(exp.value) || 0;
        // 總流出不變：搬多少過來，支出就減多少（跟 recognize_bank_fee 同一條規則）
        const moved = f - prevFee;
        if (moved && e - moved >= 0) exp.value = String(e - moved);
        prevFee = f;
        paint();
    });
    exp.addEventListener('input', paint);
    paint();
}

/** 沒填分類的那一列給一個小紅點（owner 2026-08-24）。
 *
 *  🔴 為什麼要標：沒分類的列在三表裡會落到「未歸類」，而清單上那一格只是**空白**
 *     —— 空白看起來像「這欄本來就沒東西」，不像「這裡要處理」。對帳單匯入一次
 *     進來幾十列，摘要沒中任何關鍵字的就是空的，很容易整批漏掉。
 *  用 title 講原因，不要只放一個沒人看得懂的點。 */
const _NO_VAL_DOT =
    '<span title="還沒填 —— 點一下這一格補上"'
    + ' style="display:inline-block;width:7px;height:7px;border-radius:50%;'
    + 'background:#ef4444;vertical-align:middle;"></span>';
// 舊名保留給類別那格的既有呼叫點（語意相同：沒填就是一顆紅點）

let _offOnly = false;   // 只看六日／假日（純前端篩，日期已在手上）

/** 子項目格顯示**第三層以後的全部**（`醫療保健 ▸ 乳癌治療 ▸ 台北馬偕`）。
 *  🔴 只印第三層的話，第四、五層在畫面上會整個消失 —— 資料在那裡卻看不到，
 *  比沒有還糟。格子本來就有 ellipsis，完整路徑放在 title。
 *  沒有節點的舊列（分類是匯入寫的、還沒回填）退回 sub_item 欄。 */
const _taxDeep = (e) => ((e.taxonomy_path || []).length > 2
    ? e.taxonomy_path.slice(2).join(' ▸ ') : (e.sub_item || ''));

/** 整條路徑（給 title）。沒有節點就退回複合鍵。 */
const _taxFull = (e) => ((e.taxonomy_path || []).length
    ? e.taxonomy_path.join(' ▸ ') : (e.category || ''));

/** 日期格：日期＋星期（六日／假日再上色）。沒有日期就一個破折號。 */
function _dayHtml(m) {
    if (!m.day) { return '—'; }
    return `${m.day}<span class="cash-wd">${_esc(m.holiday || m.wd)}</span>`;
}

function _dayCls(m) {
    return m.holiday ? ' is-holiday' : (m.weekend ? ' is-weekend' : '');
}

// 一次只畫這麼多列，捲到底再續 —— 4,733 列全畫是 105,912 個 DOM 元素、
// 每次重畫 ~680ms（2026-08-28 實測 0.14ms/列）。篩選、排序、改一格分類都會
// 重畫，於是每個動作都要等一秒以上。
const _PAGE = 200;
let _shown = [];      // 目前這批排序/篩選後的完整列表（畫多少由 _drawn 決定）
let _drawn = 0;

/** 單列 HTML。renderList 與 _patchRow 共用 —— 兩份會漂。 */
// ── 拆項（帳目一筆、內容拆裂；owner 2026-08-31）──────────────────
// 編輯器是 js/shared/cash-split-editor（對帳單匯入預覽共用同一份）。
// 有拆項的列：分類三格換成「已拆 N 項」badge（分類的正本在拆項），
// 金額/分類/專案改由「拆內容」進 —— 後端對這幾欄也是 409。

/** 拆項父列的「專案」格：本列的 project_id 已讓位給拆項，把拆項連的案名
 *  秀回來 —— 不然整列看起來像沒連結（owner 就是這樣以為帳沒連完）。 */
const _splitProjNames = (e) => [...new Set(
    (e.splits || []).map((s) => s.project_name).filter(Boolean))].join('、');

function _splitMenu(e) {
    const amt = (e.deposit || 0) || (e.expense || 0);
    if (!amt) { return []; }
    return [{ label: e.split_count ? '改拆項' : '拆內容', fn: '_cashSplitOpen' }];
}

window._cashSplitOpen = async (id) => {
    const e = _entries.find((x) => x.id === id);
    if (!e) { return; }
    const { openCashSplitEditor } = await import('../../js/shared/cash-split-editor.js');
    openCashSplitEditor({
        entity: _pinEntity(),
        amount: (e.deposit || 0) || (e.expense || 0),
        side: e.deposit ? 'deposit' : 'expense',
        taxOpts: { tree: _taxTree, byId: _taxById },
        initial: e.splits || [],
        title: `${e.entry_date || ''} ${e.summary || ''}`,
        onSave: async (items) => {
            try {
                const r = await _fetch(`/cash-entries/${id}/splits`, {
                    method: 'PUT',
                    body: JSON.stringify({ splits: items || [] }),
                });
                crmToast(items && items.length ? `已拆 ${items.length} 項` : '已解除拆項');
                // 只補這一列（整表重抓＋重畫是 4,733 列與 ~680ms，見 _patchRow 註解）。
                // 後端的讓位規則在這裡鏡射：分類與專案清空、拆項換新。
                e.splits = (r && r.splits) || [];
                e.split_count = e.splits.length;
                e.category = ''; e.item = ''; e.sub_item = ''; e.book = '';
                e.taxonomy_node_id = ''; e.taxonomy_path = [];
                e.project_id = ''; e.project_name = '';
                _patchRow(id);
            } catch (err) {
                crmToast(err.message || '拆項儲存失敗', true);
            }
        },
    });
};

/** 就地連結（列表格子點一下）的共用殼：挑 → 寫 → 只補那一列 → toast。
 *
 *  專案與請款單只差三件事：開哪個挑選視窗、寫哪支端點、選完那一列長什麼樣。
 *  三個 try/catch/toast 各寫一份的話，「連結失敗」的行為就會各自漂。
 *  `apply(pid)` 要回一個 promise；`patch(e, pid)` 就地把那一列改成新樣子
 *  （不重抓整表 —— 4,733 列與 ~680ms，見 _patchRow 的註解）。 */
function _inlineLink({ ev, id, noun, open, apply, patch }) {
    ev.stopPropagation();
    const e = _entries.find((x) => x.id === id);
    if (!e) { return; }
    open(e, async (pid) => {
        try {
            await apply(pid, e);
            patch(e, pid);
            _patchRow(id);
            if (id === _selectedId) { renderDetail(e); }   // 詳情開著就同步
            // 🔴 多選回的是陣列，而 `[]` 在 JS 是 truthy —— 用 `pid ?` 判斷的話
            // 「取消全部連結」會報成「已連結」。
            const has = Array.isArray(pid) ? pid.length > 0 : !!pid;
            crmToast(has ? `已連結${noun}` : '已取消連結');
        } catch (err) {
            crmToast('連結失敗：' + err.message, true);
        }
    });
}

/** 專案挑選視窗的參數 —— 列表格子與詳情面板的快速連結共用（各寫一份的話，
 *  「預設只列未收齊」這種規則改一邊就漂）。 */
const _projPickerOpts = (e, onPick) => ({
    projects: _projectList,
    currentId: e.project_id || '',
    title: '連結專案 — ' + (e.summary || ''),
    onPick,
});

// 列表的「專案」格就地連結（owner 2026-09-01「在紅框處就可以連結」——
// 不用開詳情面板）。
window._cashProjPick = (ev, id) => _inlineLink({
    ev, id, noun: '專案',
    open: (e, onPick) => openProjectPicker(_projPickerOpts(e, onPick)),
    apply: (pid) => _fetch('/cash-entries/' + id, {
        method: 'PUT', body: JSON.stringify({ project_id: pid }) }),
    patch: (e, pid) => {
        e.project_id = pid;
        e.project_name = pid
            ? ((_projectList.find((p) => p.id === pid) || {}).name || '') : '';
    },
});

// 支出列的「請款單」格就地連結（owner 2026-09-01「支出的請款單勾記我希望
// 也可以在這裡直接勾，比照專案」）。私帳沒有發票，那一欄讓給請款單。
// 🔴 寫入走分配表的正本路徑（PUT /cash-entries/{id}/payments）不是直接寫
// payment_request_id —— 只寫一邊會讓列表與關聯面板各講各的（既有的坑）。
// 🔴 分配比的是 `_grossOut`（含匯費）而且**刻意不看 `status === 'card'`**：
// 刷卡列照樣可以掛請款單，那筆錢只是還沒離開帳戶。
window._cashPayPick = (ev, id) => _inlineLink({
    ev, id, noun: '請款單',
    open: (e, onPick) => openPaymentPicker({
        payments: _paymentList,
        currentIds: _payIds(e),
        // 已付掉、不在「還沒付完」清單裡、但正掛在這一列上的那幾張
        linkedRows: e.payment_request_id && !_paymentList.some(
            (p) => p.id === e.payment_request_id)
            ? [{ id: e.payment_request_id, payee_name: e.payment_label || '',
                 summary: '', amount: _grossOut(e) }] : [],
        rowAmount: _grossOut(e),
        title: '連結請款單 — ' + (e.summary || ''),
        onPick,
    }),
    apply: (ids, e) => {
        // 逐張給它自己的金額，累計不超過本列實際流出 —— 一筆匯出付多張
        // （張皓雲 17,200 + 3,000 = 20,200）剛好填滿；分次付（匯出比單小）
        // 則只分配得出去的那部分，差額留給詳情面板的分配面板。
        let left = _grossOut(e);
        const items = [];
        const over = [];
        (ids || []).forEach((pid) => {
            const p = _paymentList.find((x) => x.id === pid);
            const amt = Math.max(0, Math.min(p ? (p.amount || 0) : 0, left));
            left -= amt;
            // 🔴 金額 0 後端會擋（分配 ≤ 0 是一定錯）—— 濾掉，但要講出來，
            // 不然使用者勾了三張、存完只剩兩張，畫面上沒有任何跡象。
            if (amt > 0) { items.push({ payment_request_id: pid, amount: amt }); }
            else { over.push(paymentLabel(p) || pid); }
        });
        if (over.length) {
            crmToast(`本列金額只夠分配 ${items.length} 張；`
                + `${over.join('、')} 超出、沒有掛上去`, true);
        }
        return _fetch(`/cash-entries/${id}/payments`,
                      { method: 'PUT', body: JSON.stringify({ items }) });
    },
    patch: (e, ids) => {
        // 後端把 payment_request_id 同步成**金額最大**的那張 —— 這裡照做，
        // 不然重整前後主要單據會不一樣（列表那一欄讀的就是它）。
        const list = (ids || []).map((pid) => _paymentList.find((x) => x.id === pid))
            .filter(Boolean).sort((a, b) => (b.amount || 0) - (a.amount || 0));
        e.payment_ids = list.map((p) => p.id);
        e.payment_request_id = list.length ? list[0].id : '';
        // 顯示名走共用那份（＝後端 payment_label 的鏡射）
        e.payment_label = list.length ? paymentLabel(list[0]) : '';
    },
});

// 列表的「發票」格就地連結（owner 2026-09-02「這裡的發票要可以連結（多筆），
// 像是連結專案那樣」）。收款可以一次對到好幾張發票 —— 合併匯款是常態。
//
// 🔴 **這個視窗只決定「掛哪幾張」，不動逐張的分配金額與匯費。**
// 發票側跟請款單側形狀不同：這一側是 per-item fee（客戶匯三張的錢，可能只有
// 其中一張被扣了 30），而分期收款時分配金額也不等於面額。所以：
//   · 本來就掛著的 → 金額與匯費**原封保留**（不然就地勾一下就把人調好的匯費洗掉）
//   · 這次新掛上的 → 走既有的預帶規則（尚欠金額 ＋ autoFee），跟分配面板同一份
// 要細調就開詳情面板那個分配面板 —— 那才是它的家。
window._cashInvPick = (ev, id) => {
    let existing = null;   // 現有分配（金額＋匯費）：open 撈一次、apply 沿用
    return _inlineLink({
        ev, id, noun: '發票',
        open: async (e, onPick) => {
            // 已收齊、不在候選清單裡、但正掛在這一列上的那幾張：不補進視窗就會被存檔
            // 洗掉。列上只有「金額最大那張」的抬頭，第二、三張要撈現有分配才有真的
            // 號碼與金額（捏造成主發票的抬頭＋$0 會讓人以為掛錯張）。
            try {
                existing = (await _fetch(`/cash-entries/${e.id}/invoices`)).items || [];
            } catch (err) {
                // 看不到現有分配就不開視窗 —— 開了再存檔，等於把看不到的金額／匯費洗掉
                crmToast('撈不到現有分配：' + err.message, true);
                return;
            }
            const linked = existing.filter((it) => !it.missing
                    && !_invoiceList.some((x) => x.id === it.invoice_id))
                .map((it) => ({ id: it.invoice_id, invoice_number: it.invoice_number || '',
                                title: it.title || '', amount_total: it.amount_total || 0 }));
            openInvoicePicker({
                invoices: _invoiceList,
                currentIds: _invIds(e),
                linkedRows: linked,
                rowAmount: Number(e.deposit) || 0,
                title: '連結發票 — ' + (e.summary || ''),
                onPick,
            });
        },
        apply: async (ids, e) => {
            // 現有分配先撈回來 —— 留著的那幾張要原封帶走（金額＋匯費）
            const keep = Object.fromEntries((existing || []).map((it) => [it.invoice_id, it]));
            // 🔴 預帶走分配面板同一支 `toItem`，殘額餵**這一列自己的**（面板餵它自己的）——
            // 曾經是讀全域面板狀態的預設值，從列表叫會拿到別列的數字、靜靜寫一個錯的匯費進 DB。
            const picked = ids || [];
            let left = _allocRemain(e.deposit, picked.filter((iid) => keep[iid]).map((iid) => keep[iid]));
            const items = [];
            const over = [];
            picked.forEach((iid) => {
                if (keep[iid]) { items.push(keep[iid]); return; }   // 原封帶走（金額＋匯費）
                // 沒在 keep 裡的一定在候選清單（視窗只列這兩種）
                const inv = _invoiceList.find((x) => x.id === iid);
                const it = _ALLOC_SIDES.invoice.toItem(inv, Math.max(0, left));
                left -= it.amount;
                // 🔴 金額 0 後端會擋（分配 ≤ 0 是一定錯）—— 濾掉，但要講出來（同請款單
                // 那側），不然使用者勾了三張、存完只剩兩張，畫面上沒有任何跡象。
                if (it.amount > 0) { items.push(it); }
                else { over.push(inv.invoice_number || inv.title || iid); }
            });
            if (over.length) {
                crmToast(`${over.join('、')} 沒有尚欠金額，沒有掛上去`, true);
            }
            return _fetch(`/cash-entries/${id}/invoices`, {
                method: 'PUT',
                body: JSON.stringify({ items: _allocBody(_ALLOC_SIDES.invoice, items) }),
            });
        },
        patch: (e, ids) => {
            e.invoice_ids = (ids || []).slice();
            // 後端把 invoice_id 同步成**金額最大**的那張；這裡樂觀更新只認「有沒有」，
            // 顯示名等下次重載校正（分配金額在前端算不準 —— 那是後端寫入後才知道的）
            const first = (ids || [])[0];
            const inv = first ? _invoiceList.find((x) => x.id === first) : null;
            e.invoice_id = first || '';
            e.invoice_title = inv ? (inv.title || inv.invoice_number || '') : (first ? e.invoice_title : '');
        },
    });
};


/** 這一列的毛支出：匯費算進去（銀行實際扣掉的就是這個數）。
 *  🔴 下面三個「流出多少」全部長在它上面 —— 原本各寫一次 `expense + bank_fee`，
 *  三處相隔 430 行，改刷卡規則的人只會看到其中兩個。 */
const _grossOut = (e) => (e.expense || 0) + (e.bank_fee || 0);

/** 這一列掛著哪幾張請款單。後端 `_to_cash_dict` **無條件**輸出 `payment_ids`
 *  （一個人的好幾張單常併成一筆匯出），這裡不再留舊形狀的退路 —— 前端與 API
 *  由同一個行程吐出，「新 JS 遇到舊回應」這個組合出不來。 */
const _payIds = (e) => (e.payment_ids || []).slice();

/** 這一列掛著哪幾張發票（同 _payIds 的理由：只帶主要那張的話，挑選視窗
 *  只勾得回一張，存檔就把其餘的洗掉）。 */
const _invIds = (e) => (e.invoice_ids || []).slice();

/** 「專案」與「請款單／發票」兩格：可點時是一格 cash-ed（沒值就一顆紅點），
 *  不可點時是唯讀格。兩格的形狀一樣，差別只有 class／內容／要不要掛 onclick
 *  —— 各寫一份三元式的下場是同一段樣板在檔案裡有四份（都要記得帶 class）。*/
const _linkCell = (val, { cls = '', pick = '', hint = '' } = {}) =>
    `<div class="${cls}${pick ? ' cash-ed' : ''}"${
        pick ? ` onclick="${pick}"` : ''} title="${_esc(val || (pick ? hint : ''))}">${
        val ? _esc(val) : (pick ? _NO_VAL_DOT : '')}</div>`;

function _rowHtml(e) {
    const card = _cardAmt(e), out = _bankOut(e), deep = _taxDeep(e);
    // 一列算一次就好 —— 每個都被原本的樣板呼叫 2~3 次（4,733 列時很有感）
    const tf = _esc(_taxFull(e)), bm = _flat(e.bank_memo, ' '), nt = _flat(e.note, ' ');
    const dm = _dayMark(e.entry_date);   // 日期格與假日底色共用一次判定
    // 同上：這三個以前在樣板裡各算兩次（`_splitProjNames` 還是 map+Set+join）
    const projName = e.split_count ? _splitProjNames(e) : (e.project_name || '');
    const canPickProj = !e.split_count && _canLinkProj(e);
    // 帳本 pin 在一次渲染裡不會變 —— 200 列問 200 次沒有意義（同上）
    const mine = _MINE_LEDGER;
    return `
        <div class="crm-row${e.id === _selectedId && !_batch.on ? ' selected' : ''}${
            _batch.on && _batch.sel.has(e.id) ? ' batch-picked' : ''}" data-id="${e.id}"
             onclick="window._cashRowClick(event,'${e.id}')">
            <div class="crm-row-date cash-c-date${_dayCls(dm)}">${_dayHtml(dm)}</div>
            <div class="crm-row-name cash-c-summary">${_esc(e.summary)}${_pettyTag(e)}</div>
            <div class="cash-c-deposit" style="color:#86efac;">${e.deposit ? '$' + _fmtNum(e.deposit) : ''}</div>
            <div class="cash-col-card cash-c-card" style="color:#c4b5fd;">${card ? '$' + _fmtNum(card) : ''}</div>
            <div class="cash-c-expense" style="color:#fca5a5;">${out ? '$' + _fmtNum(out) : ''}</div>
            ${/* 🔴 拆項列一樣要吐**三個**格子（分類/項目/子項目各一）：這個列表是
                 flex，欄寬是 `nth-child(N)` 給的 —— `grid-column:span 3` 在 flex
                 底下完全無效，少吐兩個節點就讓後面每一欄整排前移（銀行資訊跑到
                 附註欄、專案名跑到銀行資訊欄，owner 2026-09-01 截圖）。
                 badge 放第一格、後兩格留白（badge 比 78px 寬一點，溢到空格上剛好）。*/ ''}
            ${e.split_count ? `
            <div class="cash-c-book" style="cursor:pointer;white-space:nowrap;" onclick="event.stopPropagation();window._cashSplitOpen('${e.id}')"
                 title="${_esc((e.splits || []).map((s) => `$${_fmtNum(s.amount)} ${(s.taxonomy_path || []).join(' ▸ ') || s.category || '未分類'}`).join('\n'))}">
                ${splitBadgeHtml(e.split_count)}
            </div><div class="cash-c-item"></div><div class="cash-c-sub_item"></div>` : `
            <div class="cash-ed cash-c-book" onclick="window._cashTaxEdit(event,'${e.id}',0)"
                 title="${tf}">${e.category ? _esc(e.book) : _NO_VAL_DOT}</div>
            <div class="cash-ed cash-c-item" onclick="window._cashTaxEdit(event,'${e.id}',1)"
                 style="color:#c9c9c9;" title="${tf}">${e.item ? _esc(e.item) : _NO_VAL_DOT}</div>
            <div class="cash-ed cash-c-sub_item" onclick="window._cashTaxEdit(event,'${e.id}',2)"
                 style="color:#9a9a9a;" title="${tf}">${deep ? _esc(deep) : _NO_VAL_DOT}</div>`}
            <div class="cash-ed cash-c-bank_memo" onclick="window._cashInline(event,'${e.id}','bank_memo')"
                 title="${_esc(bm)}">${_esc(_flat(e.bank_memo, ' · '))}</div>
            <div class="cash-ed cash-c-note" onclick="window._cashInline(event,'${e.id}','note')"
                 title="${_esc(nt)}">${_esc(_flat(e.note, ' · '))}</div>
            ${_linkCell(projName, {
                cls: 'cash-c-project',
                pick: canPickProj ? `window._cashProjPick(event,'${e.id}')` : '',
                hint: '連結專案（可搜尋）' })}
            ${/* 發票欄只裝發票（收入列可點；私帳沒有發票 → 預設藏、不可點）。
                 請款單自己一欄（owner 2026-09-03）：支出列可點、兩本帳都有。 */ ''}
            ${_linkCell(e.invoice_title || '', {
                cls: 'cash-col-inv cash-c-invoice',
                pick: (e.deposit && !mine) ? `window._cashInvPick(event,'${e.id}')` : '',
                hint: '連結發票（可搜尋、可多張）' })}
            ${_linkCell(e.expense ? (e.payment_label || '') : '', {
                cls: 'cash-c-payment',
                pick: e.expense ? `window._cashPayPick(event,'${e.id}')` : '',
                hint: '連結請款單（可搜尋、可多張）' })}
            <div class="cash-c-account">${_esc(_acctName(e.bank_account_id))}</div>
            ${kebabMenuHtml(e.id, { onEdit: '_cashSelect', onDuplicate: '_cashDup',
                                   onDelete: '_cashDelete',
                                   extra: [..._splitMenu(e), ..._pettyMenu(e)] })}
        </div>
    `;
}

/** 只換一列（行內編輯用）。整表重畫要 ~680ms，換一列是 0。 */
function _patchRow(id) {
    const e = _entries.find((x) => x.id === id);
    const el = document.querySelector(`.crm-row[data-id="${CSS.escape(id)}"]`);
    if (!el) { return; }          // 不在目前畫出來的那批裡 —— 捲到它時自然是新的
    if (!e) { el.remove(); return; }
    el.outerHTML = _rowHtml(e);
}

/** 捲到接近底部就補下一批（哨兵 + IntersectionObserver）。 */
let _moreObserver = null;

/** IntersectionObserver 的 root。列表容器**通常**就是捲動的那個
 *  （`crm.css` 給 `[id$="-list-body"]` 的是 `flex:1; overflow-y:auto`），
 *  但那要它真的被限高才成立 —— 獨立掛載或版面改動時就不是了。
 *  🔴 所以用量的，不是寫死：量不出捲動就退回 viewport（null）。寫死成 body
 *  的話，容器沒被限高時哨兵一出現就立刻相交，首屏會一路多畫好幾批。 */
function _scrollRoot(body) {
    const ov = getComputedStyle(body).overflowY;
    return ((ov === 'auto' || ov === 'scroll') && body.scrollHeight > body.clientHeight)
        ? body : null;
}

function _drawMore(body) {
    const slice = _shown.slice(_drawn, _drawn + _PAGE);
    if (!slice.length) { return; }
    const html = slice.map(_rowHtml).join('');
    let s = document.getElementById('cash-more');
    // 插在哨兵**前面** —— 這樣不用把它搬回最後，observe 也只做一次
    if (s) { s.insertAdjacentHTML('beforebegin', html); }
    else { body.insertAdjacentHTML('beforeend', html); }
    _drawn += slice.length;
    if (_drawn >= _shown.length) { if (s) { s.remove(); } return; }
    if (!s) {
        body.insertAdjacentHTML('beforeend',
            '<div id="cash-more" class="crm-empty" style="padding:10px;">載入更多…</div>');
        _moreObserver = new IntersectionObserver((ents) => {
            if (ents.some((x) => x.isIntersecting)) { _drawMore(body); }
        }, { root: _scrollRoot(body), rootMargin: '600px' });
        _moreObserver.observe(document.getElementById('cash-more'));
    }
}

function renderList() {
    const body = document.getElementById('cash-list-body');
    if (!body) return;
    _MINE_LEDGER = !ledgerHasInvoices();   // 這次渲染問一次就好
    _sorter.attach();
    if (_moreObserver) { _moreObserver.disconnect(); _moreObserver = null; }
    if (_entries.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無收支紀錄${_filters.q ? '，請調整搜尋' : ''}</div>`;
        _shown = []; _drawn = 0;
        return;
    }
    // 六日／假日是純前端篩（日期已在手上，不必為了它多跑一趟後端）
    const _rows = _offOnly
        ? _entries.filter((e) => { const m = _dayMark(e.entry_date);
                                   return m.holiday || m.weekend; })
        : _entries;
    _shown = _sorter.sorted(_rows);
    _drawn = 0;
    body.innerHTML = '';
    _drawMore(body);
    _batchRefreshBar();        // 篩選變了，「目前篩出 N 筆」要跟著變
}

/** 列表格子行內編輯（owner 2026-08-26「直接點按就編輯、自動儲存」）：
 *  子項目／銀行資訊／附註三欄。點格→input→Enter/失焦即存（Esc 取消），
 *  PUT 部分更新只送那一欄（exclude_unset —— 不會洗掉其他欄）。
 *  stopPropagation：格子的點擊不能觸發整列的選取/開詳情。 */
window._cashInline = (ev, id, f) => {
    // 批次模式：點格子＝選這一列（不進行內編輯）—— 兩種點擊語意疊在同一個
    // 格子上，會變成「想選取卻開了編輯器」。stopPropagation 是必要的：不然
    // 這一下還會冒泡到列的 onclick，選了又取消＝按不動。
    if (_batch.on) { ev.stopPropagation(); window._cashRowClick(ev, id); return; }
    ev.stopPropagation();
    // 入口有兩個（✎ 與雙擊格子）—— 一律取所在的格子
    const cell = ev.currentTarget.closest('.cash-ed');
    if (!cell) return;
    if (cell.querySelector('input')) return;       // 已在編輯中
    const e = _entries.find((x) => x.id === id);
    if (!e) return;
    const old = e[f] || '';
    cell.innerHTML = `<input class="crm-input" style="width:100%;font-size:12px;padding:2px 6px;">`;
    const inp = cell.querySelector('input');
    inp.value = old;                                // 不走 innerHTML 插值（引號/尖括號安全）
    inp.focus();
    inp.select();
    inp.addEventListener('click', (k) => k.stopPropagation());
    let done = false;
    const finish = async (save) => {
        if (done) return;
        done = true;
        const v = inp.value.trim();
        if (!save || v === (old || '').trim()) { _patchRow(id); return; }
        try {
            await _fetch(`/cash-entries/${id}`, {
                method: 'PUT', body: JSON.stringify({ [f]: v }),
            });
            e[f] = v;
            crmToast('已儲存');
        } catch (err) {
            crmToast('儲存失敗：' + err.message);
        }
        _patchRow(id);
    };
    inp.addEventListener('keydown', (k) => {
        if (k.key === 'Enter') finish(true);
        else if (k.key === 'Escape') finish(false);
    });
    inp.addEventListener('blur', () => finish(true));
};

/** 分類欄點按填寫 —— **順著樹走**（owner 2026-08-27）。
 *
 *  類別／項目／子項目三個格子是同一棵樹的前三層；點哪一格就從那一層開始問，
 *  選到「還有下層」的節點就再長一格出來 —— 所以第四、五層
 *  （家用▸變動支出▸醫療保健▸乳癌治療▸台北馬偕）不必改程式就問得出來。
 *
 *  🔴 上層還沒選就從頭問：項目掛在類別底下，沒有類別形不成路徑。反過來，上層
 *  已經有值時點哪格就從哪格開始 —— 只想換子項目卻被要求重選類別很惱人。
 *
 *  🔴 存檔時機＝選到**沒有下層**的節點（或點到別處）。一選就存的話，「改類別」
 *  在還沒選到項目前就會先把下層清掉，畫面跳一次而且中途狀態會落地。
 *
 *  🔴 存完一律重載列表，不在前端自己算 category／item／sub_item —— 那三欄是
 *  後端從路徑推導的鏡射（core.cash_taxonomy.mirror_from_path），前端再算一份
 *  就是第二份規則，而且漂的是會計對映的鍵。
 */
window._cashTaxEdit = (ev, id, level) => {
    // 批次模式：點格子＝選這一列（不進行內編輯）—— 兩種點擊語意疊在同一個
    // 格子上，會變成「想選取卻開了編輯器」。stopPropagation 是必要的：不然
    // 這一下還會冒泡到列的 onclick，選了又取消＝按不動。
    if (_batch.on) { ev.stopPropagation(); window._cashRowClick(ev, id); return; }
    ev.stopPropagation();
    const cell = ev.currentTarget.closest('.cash-ed');
    if (!cell || cell.querySelector('select,input')) return;
    const e = _entries.find((x) => x.id === id);
    if (!e) return;
    const chain = (_taxById[e.taxonomy_node_id] || []).slice();
    const start = Math.min(level, chain.length);   // 上層沒值就從頭問
    // 🔴 `sel` 是**完整**的現況路徑，`start` 只決定從第幾格開始畫。截掉下層的話，
    // 點「類別」格會看到一個空白下拉（現在選的是哪一個看不出來），而且一改類別
    // 就把下層默默清掉。
    let sel = chain.slice();
    let saved = false, dirty = false;
    const CUSTOM = '__custom__';
    cell.closest('.crm-row')?.classList.add('cash-ed-open');

    const childrenAt = (i) => _taxKidsAt(sel, i);

    const save = async (close) => {
        if (saved) return;
        saved = true;
        if (!dirty) { if (close) _patchRow(id); return; }
        const node = sel[sel.length - 1];
        try {
            const r = await _fetch(`/cash-entries/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ taxonomy_node_id: node ? node.id : '' }),
            });
            // 三欄鏡射是後端從路徑推導的 —— 就地 patch 它回傳的結果，
            // 不為了拿三個欄位把 4,700 列整表重載
            if (r.entry) { Object.assign(e, r.entry); }
            crmToast('已儲存');
        } catch (err) {
            crmToast('儲存失敗：' + err.message);
        }
        _patchRow(id);
    };

    const focusLast = () => {
        const inputs = cell.querySelectorAll('.ss-input');
        const last = inputs[inputs.length - 1];
        if (!last) return;
        last.focus();
        last.addEventListener('blur', () => setTimeout(() => {
            if (!saved && !cell.contains(document.activeElement)) save(true);
        }, 250));
    };

    const addCustom = (i) => {
        cell.innerHTML = '<input class="crm-input" placeholder="新分類名稱，Enter 建立"'
            + ' style="width:100%;font-size:12px;padding:2px 6px;">';
        const inp = cell.querySelector('input');
        inp.focus();
        inp.addEventListener('click', (k) => k.stopPropagation());
        inp.addEventListener('keydown', async (k) => {
            if (k.key === 'Escape') { saved = true; _patchRow(id); return; }
            if (k.key !== 'Enter') return;
            const name = inp.value.trim();
            if (!name) { saved = true; _patchRow(id); return; }
            try {
                const r = await _fetch('/cash-taxonomy/nodes', {
                    method: 'POST',
                    body: JSON.stringify({ parent_id: sel[i - 1] ? sel[i - 1].id : '', name }),
                });
                await _loadCashOptions();          // 新節點要進樹才選得到
                // 重新解析整條鏈：重載之後舊的節點物件已經是別份了
                sel = (_taxById[r.node && r.node.id] || []).slice();
                dirty = true;
                saved = false;
                await save(true);
            } catch (err) {
                crmToast('建立失敗：' + err.message);
                _patchRow(id);
            }
        });
        inp.addEventListener('blur', () => setTimeout(() => {
            if (!saved && !cell.contains(document.activeElement)) { saved = true; _patchRow(id); }
        }, 250));
    };

    const onPick = (i, v) => {
        if (v === CUSTOM) { addCustom(i); return; }
        sel = sel.slice(0, i);
        dirty = true;
        const node = childrenAt(i).find((n) => n.id === v);
        if (node) sel.push(node);
        const last = sel[sel.length - 1];
        if (last && last.children.length) { draw(); focusLast(); }
        else save(true);
    };

    // 「至少留一格」＝ keepOne：`公司▸器材` 這種還沒有子項目的，那一格就是
    // 拿來按「＋ 自訂…」的
    const draw = () => _taxSelects(cell, {
        chain: sel, start, keepOne: true, wrap: true,
        cls: 'crm-input cash-tax-sel', style: 'min-width:92px;',
        blank: (i) => (i === 0 ? '類別…' : '（不再細分）'),
        custom: true,
        onPick,
    });

    draw();
    focusLast();
};

// ── 批次分類 ──────────────────────────────────────────────────

/** 批次模式時點列＝選取；平常點列不做事（詳情走最右邊的編輯鈕）。
 *  Shift＋點＝從上一次點的那列選到這列（照畫面順序）。 */
window._cashRowClick = (ev, id) => {
    if (!_batch.on) { return; }
    const order = _shown.map((e) => e.id);
    if (ev && ev.shiftKey && _batch.last && order.includes(_batch.last)) {
        const a = order.indexOf(_batch.last), b = order.indexOf(id);
        const [lo, hi] = a < b ? [a, b] : [b, a];
        // 範圍一律**加選**（不是 toggle）—— toggle 會把中間已選的取消掉，
        // 那是使用者最不想要的結果
        for (let i = lo; i <= hi; i++) { _batch.sel.add(order[i]); }
    } else if (_batch.sel.has(id)) {
        _batch.sel.delete(id);
    } else {
        _batch.sel.add(id);
    }
    _batch.last = id;
    _batchPaint();
};

/** 只把選取狀態刷到**已經畫出來**的列上 —— 不重建 DOM。
 *  （整表重畫要 ~680ms，而且分批繪製時重畫會把捲軸拉回頂端。） */
function _batchPaint() {
    document.querySelectorAll('#cash-list-body .crm-row[data-id]').forEach((el) => {
        el.classList.toggle('batch-picked', _batch.sel.has(el.dataset.id));
    });
    _batchRefreshBar();
}

function _batchRefreshBar() {
    const bar = document.getElementById('cash-batch-bar');
    if (!bar) { return; }
    bar.style.display = _batch.on ? 'flex' : 'none';
    const el = document.getElementById('cash-batch-count');
    if (!el) { return; }
    el.innerHTML = _batch.sel.size
        ? `已選 <b style="color:#eee;">${_batch.sel.size}</b> 筆`
        : `<span style="color:#6b7280;">點列選取，按住 Shift 選一整段（目前篩出 ${_shown.length} 筆）</span>`;
}

/** 批次列的分類下拉 —— 與篩選器、格內編輯同一個生成器。
 *  🔴 母公司那本**沒有分類樹**（種子只種私帳），類別是平的一層 ——
 *  那邊退回一顆 `_CATEGORIES` 的下拉。不退的話那本按下批次分類會看到一個
 *  空下拉，等於這顆按鈕在公司帳上是壞的。 */
function _batchTaxDraw() {
    const box = document.getElementById('cash-batch-tax');
    if (!box) { return; }
    if (!_taxTree.length) {
        box.innerHTML = '<select id="cash-batch-cat" class="crm-select" style="min-width:160px;">'
            + '<option value="">要套哪個類別…</option>'
            + _CATEGORIES.map((c) => `<option value="${_esc(c)}">${_esc(c)}</option>`).join('')
            + '</select>';
        searchableSelect(box.querySelector('select'), { placeholder: '搜尋類別…' });
        return;
    }
    _taxSelects(box, {
        chain: _batch.chain, keepOne: true,
        cls: 'crm-select cash-batch-sel', style: 'min-width:120px;',
        blank: (i) => (i === 0 ? '要套哪個分類…' : '（不再細分）'),
        onPick: (i, v) => {
            _batch.chain = _batch.chain.slice(0, i);
            const n = _taxKidsAt(_batch.chain, i).find((x) => x.id === v);
            if (n) { _batch.chain.push(n); }
            _batchTaxDraw();
        },
    });
}

function _batchSetMode(on) {
    _batch.on = on;
    _batch.sel.clear();
    _batch.last = null;
    const btn = document.getElementById('cash-btn-batch');
    if (btn) {
        btn.textContent = on ? '離開批次' : '批次分類';
        btn.classList.toggle('crm-btn-primary', on);
        btn.classList.toggle('crm-btn-secondary', !on);
    }
    if (on) { closeDetail(); _batchTaxDraw(); }
    renderList();                     // 底色與 selected 狀態都要跟著換
    _batchRefreshBar();
}

/** 目前挑到的分類：`{label, body}` —— body 直接就是要送的欄位
 *  （有樹送 taxonomy_node_id、平的那本送 category）。 */
function _batchPick() {
    if (!_taxTree.length) {
        const v = document.getElementById('cash-batch-cat')?.value || '';
        return { label: v, body: { category: v } };
    }
    const node = _batch.chain[_batch.chain.length - 1];
    return { label: node ? node.name : '', body: { taxonomy_node_id: node ? node.id : '' } };
}

/** 套用（label 為空＝把選取的這幾筆的分類清掉）。 */
async function _batchApply(pick) {
    const ids = [..._batch.sel];
    if (!ids.length) { crmToast('還沒選任何一筆'); return; }
    try {
        const r = await _fetch('/cash-entries/batch-taxonomy', {
            method: 'PATCH',
            body: JSON.stringify({ entry_ids: ids, ...pick.body }),
        });
        // 整批同一個分類 → 鏡射也只有一份，就地套到那幾列（不重載 4,700 列）
        _entries.forEach((e) => { if (_batch.sel.has(e.id)) { Object.assign(e, r.entry); } });
        crmToast(pick.label
            ? `${r.updated} 筆已分類到「${pick.label}」`
            : `${r.updated} 筆的分類已清掉`);
        _batch.sel.clear();
        _batch.last = null;
        renderList();
        _batchRefreshBar();
    } catch (e) {
        // 後端是**整批擋下**並說明原因（鎖月、掛了專案的類別不合）—— 原話顯示，
        // 吞成「操作失敗」的話使用者不知道要取消勾選哪幾筆
        crmToast(e.message || '批次分類失敗', 8000);
    }
}

/** 刷卡金額：status='card' 的列（刷卡當下不動銀行，所以不算銀行支出）。 */
function _cardAmt(e) {
    return e.status === 'card' ? _grossOut(e) : 0;
}

/** 銀行支出：卡費列不算（那筆錢還在卡上，月底繳款才真的離開帳戶）。 */
function _bankOut(e) {
    return e.status === 'card' ? 0 : _grossOut(e);
}

// ── Detail Panel ────────────────────────────────────────────

// 「哪些類別可以連結專案」由後端供應（/cash-entries/options，比照零用金的
// /petty/options）。這裡的值只是**斷線時的 fallback** —— 規則的正本在
// core/project_link.py，前端寫死一份就會跟後端漂（同一個問題本來散在三個檔案）。
let _LINKABLE = ['專案', '專案雜支', '專案外包'];
// 類別下拉的選項（正本是後端 finance_category_map）。這裡的值只是斷線 fallback ——
// 寫死一份就會跟種子脫節：貸款繳款／貸款補貼／銀行借款 就是這樣漏掉的，
// 結果對帳單匯入自己寫出來的列，使用者在編輯視窗選不到它的類別。
let _CATEGORIES = ['水電網路', '交際應酬', '行政', '其他', '其他收入', '房租', '建構',
    '專案', '專案外包', '專案雜支', '教育訓練', '設備耗材', '設備維護', '軟體網路服務',
    '勞健保', '發票代開', '會計', '業務推廣', '製作金', '銀行利息', '獎金', '請款單',
    '辦公室管理費', '營所稅', '營業稅', '薪資', '轉存'];

async function _loadCashOptions() {
    try {
        const o = await _fetch('/cash-entries/options?entity=' + _pinEntity());
        if (o.project_link_categories?.length) _LINKABLE = o.project_link_categories;
        if (o.categories?.length) _CATEGORIES = o.categories;
        if (o.tree) { _taxTree = o.tree; _taxById = _indexTaxShared(_taxTree); }
    } catch (_) { /* 用 fallback，不擋畫面 */ }
}

/** 專案／發票的即時連結下拉。直接打 PUT /cash-entries/{id} 只送要改的那一欄 ——
 *  該端點是 exclude_unset 部分更新，沒送的欄位不會被動到。 */
function _renderQuickLink(e) {
    const box = document.getElementById('cash-quicklink');
    if (!box) return;
    const row = (label, inner) => `
        <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
            <span style="color:#9ca3af;font-size:12px;flex:0 0 48px;">${label}</span>${inner}</div>`;
    const sel = (id, optsHtml) => `<select id="${id}" style="flex:1;min-width:0;">${optsHtml}</select>`;
    box.innerHTML = `
        ${row('專案', `<button id="cash-ql-proj" class="crm-btn crm-btn-secondary" title="挑一個專案（可搜尋）"
                style="flex:1;min-width:0;text-align:left;font-size:12px;padding:4px 8px;
                       overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${e.project_name ? '' : 'color:#6b7280;'}">
                ${e.project_name ? _esc(e.project_name) : '＋ 選專案'}</button>`)}
        ${/* 發票下拉只在支出列出現：收入列的發票走上面可掛多張的「關聯發票」區。
             私帳整個不出現 —— 私帳不開發票，連結一律走專案（owner 2026-09-01） */ ''}
        ${(e.deposit || !ledgerHasInvoices()) ? '' : row('發票', sel('cash-ql-inv', _invOptsHtml(e.invoice_id, '— 未連結 —')))}
        <div id="cash-ql-msg" style="font-size:11px;color:#666;margin-top:5px;">選了就直接存</div>`;

    const save = async (patch) => {
        const msg = document.getElementById('cash-ql-msg');
        msg.textContent = '儲存中…';
        msg.style.color = '#888';
        try {
            await _fetch('/cash-entries/' + e.id,
                { method: 'PUT', body: JSON.stringify(patch) });
            msg.textContent = '已儲存';
            msg.style.color = '#86efac';
            await loadEntries();
        } catch (err) {
            msg.textContent = err.message;
            msg.style.color = '#fca5a5';
        }
    };
    const pj = document.getElementById('cash-ql-proj');
    if (pj) pj.addEventListener('click', () => openProjectPicker(
        _projPickerOpts(e, (id) => save({ project_id: id }))));
    const iv = document.getElementById('cash-ql-inv');
    if (iv) iv.addEventListener('change', () => save({ invoice_id: iv.value }));
}

function renderDetail(e) {
    document.getElementById('cash-detail-title').textContent = e.summary;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    const section = (title) => `<div class="crm-detail-section">${title}</div>`;

    let html = '';
    // 收支
    html += prop('日期', e.entry_date ? e.entry_date.substring(0, 10) : '');
    if (e.deposit) html += prop('收入', '$' + _fmtNum(e.deposit));
    if (e.expense) html += prop('支出', '$' + _fmtNum(e.expense));
    html += prop('內容', e.summary);
    if (e.book) html += prop('類別', e.book);
    if (e.item) html += prop('項目', e.item);
    if (e.sub_item) html += prop('子項目', e.sub_item);
    if (e.bank_memo) html += prop('銀行資訊', e.bank_memo);
    if (e.note) html += prop('附註', e.note);

    html += prop('帳戶', _acctName(e.bank_account_id));
    if (e.bank_fee) html += prop('匯費', '$' + _fmtNum(e.bank_fee));

    // 關聯資訊：**有值才印**。收支明細絕大多數列的專案/發票/客戶都是空的，
    // 每列都掛三四行「空」只是把真正有內容的欄目往下擠。
    const matchedClient = _clientList.find(c => {
        const note = (c.payment_note || '').trim();
        return note && e.summary && (e.summary.includes(note) || note.includes(e.summary));
    });
    const clientLabel = matchedClient
        ? matchedClient.short_name + (matchedClient.payment_info ? ' (' + matchedClient.payment_info + ')' : '')
        : '';
    const isProjectish = _canLinkProj(e);
    // 收入列的發票由下面「關聯發票」區負責，不重複列。
    // 私帳沒有發票這回事（不開發票，收款＝連結專案），相關欄目整組不出現。
    const showInvoiceProp = !e.deposit && ledgerHasInvoices();
    const rel = [
        // 有快速連結下拉時就不印唯讀版 —— 下拉本身已經顯示目前選的是什麼，
        // 印兩份會變成「專案 X」下面又一個「專案」標籤
        ...(isProjectish ? [] : [['專案', e.project_name || '']]),
        ...(showInvoiceProp && !isProjectish ? [['發票', e.invoice_title || '']] : []),
        ['客戶', clientLabel],
        ...(e.advance_payment_id ? [['預支關聯', '已關聯']] : []),
    ].filter(([, v]) => v);
    if (rel.length || isProjectish) {
        html += section('關聯資訊');
        rel.forEach(([k, v]) => { html += prop(k, v); });
        // 專案／專案雜支：直接在詳情面板掛下拉連結，不用先按編輯
        if (isProjectish) html += '<div id="cash-quicklink"></div>';
    }

    // 拆項明細（owner 2026-09-02「已拆的明細要在這裡可以看到」）。
    // 🔴 一列被拆之後，它的分類／專案／發票**整組讓位給拆項** —— 上面那些欄位
    // 因此是空的，詳情面板等於只剩「350,436 從源日進來」，看不出這筆錢是誰的。
    // 資料早就跟著清單回來了（`splits`），只是沒畫。
    const _sp = e.splits || [];
    if (_sp.length) {
        html += section(`拆項明細（${_sp.length}）`);
        html += '<div style="font-size:12px;">' + _sp.map((s) => {
            const path = (s.taxonomy_path || []).join(' ▸ ')
                || [s.category, s.sub_item].filter(Boolean).join(' ▸ ');
            const tags = [];
            if (s.project_name) { tags.push(_esc(s.project_name)); }
            if ((s.advances || []).length) { tags.push(`沖 ${s.advances.length} 筆代墊`); }
            if (s.note) { tags.push(_esc(s.note)); }
            return `<div style="display:flex;gap:8px;align-items:baseline;padding:4px 0;border-bottom:1px solid #262626;">
                <span style="width:92px;text-align:right;flex-shrink:0;color:#e0e0e0;">$${_fmtNum(splitGross(s))}</span>
                <span style="flex:1;min-width:0;">
                    <span style="color:#ddd;">${_esc(path) || '（未分類）'}</span>
                    ${tags.length ? `<span style="display:block;color:#9ca3af;font-size:11px;">${tags.join('　·　')}</span>` : ''}
                    ${s.fee ? `<span style="display:block;color:#fb923c;font-size:11px;">實匯 $${_fmtNum(s.amount)} ＋ 代開費 $${_fmtNum(s.fee)}（專案按毛額結清）</span>` : ''}
                </span></div>`;
        }).join('')
        // 合計比的是 amount（＝帳目金額），代開費是外加的，不進這個 Σ
        + `<div style="display:flex;gap:8px;padding:5px 0;color:#9ca3af;">
             <span style="width:92px;text-align:right;flex-shrink:0;">$${
                 _fmtNum(_sp.reduce((n, s) => n + (s.amount || 0), 0))}</span>
             <span style="flex:1;">合計（＝本列金額）</span></div>`
        + `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-top:6px;"
                  onclick="window._cashSplitOpen('${e.id}')">改拆項</button>`
        + '</div>';
    }

    // 關聯發票（合併匯款 / 分期收款）—— 只有收入列有，內容由 loadCashInvoiceAllocs
    // 非同步填。舊的「單張發票驗算」被這區塊取代：它只看得到一張發票，客戶合併
    // 匯款時必然報「不平衡」，等於在對的資料上顯示假警告。
    if (e.deposit && ledgerHasInvoices()) {
        html += section('關聯發票');
        html += '<div id="cash-alloc-box" style="font-size:12px;color:#888;">載入中…</div>';
    }
    // 關聯請款單（合併匯款 / 分次支付）—— 只有支出列有。出納統一匯款時一個人的
    // 多張請款單常併成一筆匯出，payment_request_id 一對一掛不上去（實測生產
    // 322 筆結清請款單的收支，硬連結一筆都沒有）。
    if (e.expense) {
        html += section('關聯請款單');
        html += '<div id="cash-pay-box" style="font-size:12px;color:#888;">載入中…</div>';
    }

    document.getElementById('cash-detail-content').innerHTML = html;
    if (isProjectish) _renderQuickLink(e);
    if (e.deposit && ledgerHasInvoices()) loadCashInvoiceAllocs(e.id);
    // 🔴 沒掛過任何請款單就不用打那一趟：payment_request_id 的不變量由
    //    replace_payment_allocs 維持（有連結才非空、清空就設回 null），所以它
    //    等於「這列有沒有分配」。實測生產 907 筆支出列，有硬連結的是 0 筆 ——
    //    等於每點一列支出就白花一次往返 ＋ 兩個查詢（連線池只有 50）。
    if (e.expense) {
        if (e.payment_request_id) loadCashPaymentAllocs(e.id);
        else _renderEmptyPayBox(e.id);
    }

    const actions = document.getElementById('cash-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('cash-bar-actions', () => {
        const editData = { ...e, invoice_id: e.invoice_id || '', project_id: e.project_id || '', bank_fee: e.bank_fee || 0,
            bank_account_id: e.bank_account_id != null ? String(e.bank_account_id) : '' };
        enableInlineEdit('cash-detail-content', 'cash-bar-actions', _buildEditFields(e.bank_account_id, e.invoice_id), editData,
            async (payload) => {
                _cleanPayload(payload);
                await _fetch('/cash-entries/' + e.id, { method: 'PUT', body: JSON.stringify(payload) });
                await loadEntries();
                const updated = _entries.find(x => x.id === e.id);
                renderDetail(updated || e);
            },
            () => renderDetail(e)
        );
    });
}

async function selectEntry(id) {
    const prev = _selectedId;
    _selectedId = id;
    if (prev) { _patchRow(prev); }
    _patchRow(id);
    document.getElementById('cash-detail-panel').style.display = 'flex';
    document.getElementById('cash-resize-handle').style.display = '';
    const e = _entries.find(x => x.id === id);
    if (e) renderDetail(e);
}

function closeDetail() {
    const prev = _selectedId;
    _selectedId = null;
    document.getElementById('cash-detail-panel').style.display = 'none';
    document.getElementById('cash-resize-handle').style.display = 'none';
    if (prev) { _patchRow(prev); }
}

// ── Edit Fields (for inline edit) ───────────────────────────

/** 下拉 option 標記的單一正本 —— 這三個下拉（詳情快速連結、編輯表單、modal）
 *  本來各自把同一串 <option> 寫一遍，改標籤要改三處。 */
function _invOptsHtml(selectedId, placeholder = '— 不關聯 —') {
    return `<option value="">${placeholder}</option>` + _invoiceCandidates(selectedId).map(inv =>
        `<option value="${_esc(inv.id)}"${inv.id === selectedId ? ' selected' : ''}>${_esc(_invoiceLabel(inv))}</option>`).join('');
}

const _projOptsHtml = (selectedId, placeholder = '— 不關聯 —') =>
    projectOptionsHtml(_projectList, placeholder, selectedId);

/** 這張發票還差多少錢沒收。outstanding 由後端從收支分配算（GET /invoices），
 *  沒有這欄的舊回應退回面額。 */
function _outstanding(inv) {
    return inv.outstanding != null ? inv.outstanding : (inv.amount_total || 0);
}

/** 收齊了沒。
 *  🔴 讀後端的 settled，不要自己用 `outstanding <= 0` 判 —— 真正的規則是
 *  invoice_is_settled（含 NT$50 匯費容差，394 張歷史發票裡有 42 張靠它）。
 *  自己判的話，被匯費短收 30 元的那張會在應收帳款「收齊」、在這裡「尚欠 $30」。 */
function _settled(inv) {
    return inv.settled != null ? !!inv.settled : _outstanding(inv) <= 0;
}

/** 發票下拉的候選清單：**還沒收齊的**才列（394 張裡多數早就結案，全倒進下拉
 *  等於在已結案名單裡大海撈針）。
 *
 *  🔴 判準用 outstanding（面額 − 實收）而**不是** payment_status 字串：
 *  「已收款」只要收到第一筆就會被標上（sync_invoice_paid 與 _mark_invoice_received
 *  都只看有沒有收款、不看金額），拿它當濾網會讓「10 萬收了 4 萬」的發票從下拉裡
 *  消失 —— 而那正是要用這個下拉記第二期的時候。作廢仍排除（終態，不是欠款）。
 *
 *  🔴 `keepId` 一定要留：編輯既有收支時它掛的那張多半已經收齊，濾掉的話下拉
 *  選不到目前值，一存檔就把關聯洗掉。
 */
function _invoiceCandidates(keepId) {
    return _invoiceList.filter(inv =>
        inv.issue_status === '已開立'
        && (inv.payment_status || '') !== '作廢'
        && (!_settled(inv) || inv.id === keepId));
}

/** 下拉顯示：內容 · 金額 · 客戶 · 發票號碼 —— 四項一起才分得出同名同額的兩張
 *  （實測「台灣歐姆龍 第五、六品 $166,950」與「第7品 $166,950」只差品名）。
 *  收過一部分的另外標出尚欠多少，分期時才知道這次該填多少。 */
function _invoiceLabel(inv) {
    const parts = [inv.title || '(無標題)', '$' + (inv.amount_total || 0).toLocaleString('zh-TW')];
    if (inv.company_name) parts.push(inv.company_name);
    if (inv.invoice_number) parts.push(inv.invoice_number);
    const left = _outstanding(inv);
    if (_settled(inv)) parts.push('已結清');
    else if (inv.collected) parts.push(`已收 $${_fmtNum(inv.collected)}，尚欠 $${_fmtNum(left)}`);
    return parts.join(' · ');
}

function _buildEditFields(currentBankAccountId, currentInvoiceId) {
    const projectOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _projectList.map(p => ({value:p.id, label:p.name + (p.client_short_name ? ' (' + p.client_short_name + ')' : '')})));
    const catOpts = [''].concat(_CATEGORIES).map(v => ({ value: v, label: v || '—' }));
    const fields = [
        {name:'entry_date', label:'日期', type:'date'},
        {name:'deposit', label:'收入', type:'number'},
        {name:'expense', label:'支出', type:'number'},
        {name:'summary', label:'內容', type:'text'},
        {name:'category', label:'類別', type:'select', options:catOpts},
        {name:'sub_item', label:'子項目', type:'text'},
        {name:'bank_memo', label:'銀行資訊', type:'text'},
        {name:'note', label:'附註', type:'text'},
        {name:'project_id', label:'專案', type:'select', options:projectOpts},
        // 私帳不開發票（owner 2026-09-01）：發票欄整個不出現，連結一律走專案。
        // 不出現＝payload 不含此鍵（exclude_unset 部分更新），不會洗掉既有值。
        // 私帳不開發票：連候選清單都不用建（走一遍全部發票只為了丟掉）
        ...(!ledgerHasInvoices() ? [] : [{name: 'invoice_id', label: '發票', type: 'select',
            options: [{ value: '', label: '— 不關聯 —' }].concat(
                _invoiceCandidates(currentInvoiceId).map(
                    (inv) => ({ value: inv.id, label: _invoiceLabel(inv) })))}]),
        {name:'bank_fee', label:'匯費', type:'number'},
    ];
    // 帳戶（財務模組）— 清單載入成功才提供（降級時不出現，PUT payload 不含此鍵、不洗掉既有值）
    if (_bankAccounts && _bankAccounts.length) {
        fields.push({name:'bank_account_id', label:'帳戶', type:'select',
            options: _bankAcctOpts(String(currentBankAccountId ?? ''))});
    }
    return fields;
}

/** 帳戶下拉選項（含「未指定」）：停用帳戶只在「就是現值」時保留，避免洗掉既有值 */
function _bankAcctOpts(cur) {
    return [{ value: '', label: '— 未指定 —' }].concat(
        _bankAccounts.filter(a => a.active !== false || String(a.id) === cur)
            .map(a => ({ value: String(a.id), label: a.name })));
}

// ── Modal ───────────────────────────────────────────────────

const _FIELDS = ['summary', 'entry_date', 'expense', 'deposit', 'note', 'invoice_id', 'bank_fee', 'project_id', 'category', 'advance_payment_id'];
const _INT_FIELDS = ['expense', 'deposit', 'bank_fee'];
const _DATE_FIELDS = ['entry_date'];

function _populateInvoiceSelect(selectedId) {
    const sel = document.getElementById('cash-f-invoice_id');
    if (!sel) return;
    sel.innerHTML = _invOptsHtml(selectedId);
}

/** 新增/編輯 modal 的類別下拉（HTML 不再寫死選項）。
 *
 *  🔴 這一列現在的值一定要在選項裡。不在的話下拉會掉到「—」，看起來像沒分類過
 *  —— 而使用者只是來改別的欄位，一存就真的把分類清掉了。值域是會變的（2026-08-30
 *  起私帳吃的是分類樹鏡射出來的鍵，不是母公司那份平面科目）。 */
function _populateCategorySelect(selected) {
    const sel = document.getElementById('cash-f-category');
    if (!sel) return;
    const list = _CATEGORIES.slice();
    if (selected && !list.includes(selected)) { list.unshift(selected); }
    sel.innerHTML = '<option value="">—</option>' + list.map(v =>
        `<option value="${_esc(v)}"${v === selected ? ' selected' : ''}>${_esc(v)}</option>`).join('');
}

function _populateProjectSelect(selectedId) {
    const sel = document.getElementById('cash-f-project_id');
    if (!sel) return;
    sel.innerHTML = _projOptsHtml(selectedId);
}

function _populateBankAccountSelect(selectedId) {
    // selectedId：null = 新增（預設選 is_default 帳戶）；'' / id = 編輯帶入現值
    const field = document.getElementById('cash-field-bankaccount');
    const sel = document.getElementById('cash-f-bank_account_id');
    if (!field || !sel) return;
    if (!_bankAccounts || _bankAccounts.length === 0) { field.style.display = 'none'; return; }
    field.style.display = '';
    const def = _bankAccounts.find(a => a.is_default && a.active !== false);
    const cur = (selectedId == null) ? (def ? String(def.id) : '') : String(selectedId);
    sel.innerHTML = _bankAcctOpts(cur)
        .map(o => `<option value="${o.value}"${o.value === cur ? ' selected' : ''}>${_esc(o.label)}</option>`).join('');
}

// 這裡本來有 _updateVerifyRow()：把「發票面額 = 收入 + 匯費」畫成一行綠/紅字。
// 已刪 —— 它只看得到**一張**發票，客戶合併匯款（一筆錢對三張發票）時必然報
// 「✗ 差額 $…」，在完全正確的資料上顯示假警告。詳情面板早就改用「關聯發票」
// 分配區（後端 _alloc_verdict 是唯一正本，看得到全部分配），編輯視窗這份是漏改的。

function _updateClientMatch() {
    const el = document.getElementById('cash-client-match');
    if (!el) return;
    if (_clientList.length === 0) { el.textContent = '—'; return; }
    const summary = (document.getElementById('cash-f-summary')?.value || '').trim();
    const note = (document.getElementById('cash-f-note')?.value || '').trim();
    const text = summary + ' ' + note;
    if (!text.trim()) { el.textContent = '—'; return; }
    // 用「內容」和「備註」比對客戶的「匯款備註」（payment_note）
    const matched = _clientList.find(c => {
        const pn = (c.payment_note || '').trim();
        if (!pn) return false;
        return text.includes(pn) || pn.includes(summary) || (note && pn.includes(note));
    });
    if (matched) {
        el.innerHTML = `<span style="color:#86efac;">${_esc(matched.short_name)}</span>` +
            (matched.payment_info ? ` <span style="color:#6b7280;font-size:11px;">(${_esc(matched.payment_info)})</span>` : '');
    } else {
        el.textContent = '—';
    }
}

function openModal(e = null) {
    _editingId = e ? e.id : null;
    document.getElementById('cash-modal-title').textContent = e ? '編輯收支' : '新增收支';
    const err = document.getElementById('cash-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populateInvoiceSelect(e?.invoice_id || '');
    _populateProjectSelect(e?.project_id || '');
    _populateCategorySelect(e?.category || '');
    _populateBankAccountSelect(e ? (e.bank_account_id ?? '') : null);
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        if (!el) continue;
        if (f === 'invoice_id' || f === 'project_id') continue; // already populated
        if (_DATE_FIELDS.includes(f) && e?.[f]) el.value = e[f].substring(0, 10);
        else el.value = e ? (e[f] ?? '') : '';
    }
    if (!e) {
        document.getElementById('cash-f-entry_date').value = today();
        document.getElementById('cash-f-bank_fee').value = '0';
    }
    _updateClientMatch();
    // Reset advance fields + toggle project/invoice visibility
    var cat = e ? (e.category || '') : '';
    _toggleAdvanceFields(cat === '專案雜支');
    var advCheck = document.getElementById('cash-f-advance-check');
    if (advCheck) advCheck.checked = !!(e && e.advance_payment_id);
    var advList = document.getElementById('cash-advance-list');
    if (advList) advList.style.display = (e && e.advance_payment_id) ? 'block' : 'none';
    var advHidden = document.getElementById('cash-f-advance_payment_id');
    if (advHidden) advHidden.value = (e && e.advance_payment_id) || '';
    document.getElementById('cash-modal').style.display = 'flex';
}

function _cleanPayload(payload) {
    for (const f of _INT_FIELDS) {
        const v = payload[f];
        payload[f] = (v !== '' && v != null) ? parseInt(v) || 0 : null;
    }
    for (const f of _DATE_FIELDS) payload[f] = payload[f] || null;
    payload.invoice_id = payload.invoice_id || null;
    payload.project_id = payload.project_id || null;
    if ('bank_account_id' in payload) payload.bank_account_id = payload.bank_account_id || null;
    payload.summary = payload.summary || '';
    payload.note = payload.note || '';
    payload.category = payload.category || '';
}

async function saveEntry() {
    const summary = document.getElementById('cash-f-summary').value.trim();
    if (!summary) { _showErr('內容為必填'); return; }
    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        payload[f] = el ? el.value.trim() : '';
    }
    // 帳戶（財務模組）— 清單載入成功才送；降級時不含此鍵，避免把既有值洗掉
    if (_bankAccounts && _bankAccounts.length) {
        const bel = document.getElementById('cash-f-bank_account_id');
        if (bel) payload.bank_account_id = bel.value;
    }
    // 帳本（兩本帳）— pin 是 'mine' 才帶；parent 不送（後端 None→parent，PUT 不洗欄位）
    if (finIsMine()) payload.entity = 'mine';
    _cleanPayload(payload);
    // 專案雜支 + 預支關聯 → 自動帶入預支款的專案
    if (payload.advance_payment_id && !payload.project_id) {
        var selRadio = document.querySelector('input[name="advance-select"]:checked');
        if (selRadio && selRadio.dataset.projectId) {
            payload.project_id = selRadio.dataset.projectId;
        }
    }
    const btn = document.getElementById('cash-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId) await _fetch('/cash-entries/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
        else await _fetch('/cash-entries', { method: 'POST', body: JSON.stringify(payload) });
        // 發款/收款狀態由後端自動計算，不需手動更新
        document.getElementById('cash-modal').style.display = 'none';
        await Promise.all([loadEntries(), _loadLinkLists()]);
    } catch (e) { _showErr(e.message); }
    finally { btn.disabled = false; btn.textContent = '儲存'; }
}

async function deleteEntry(e) {
    if (!confirm(`確定刪除「${e.summary}」？`)) return;
    try { await _fetch('/cash-entries/' + e.id, { method: 'DELETE' }); closeDetail(); await loadEntries(); }
    catch (err) { alert(err.message); }
}

window._cashToggleAdvance = async function(checked) {
    var list = document.getElementById('cash-advance-list');
    var hiddenInput = document.getElementById('cash-f-advance_payment_id');
    if (!list) return;
    if (!checked) {
        list.style.display = 'none';
        if (hiddenInput) hiddenInput.value = '';
        return;
    }
    list.style.display = 'block';
    list.innerHTML = '<div style="padding:8px;color:#9ca3af;font-size:11px;">載入中...</div>';
    try {
        var data = await _fetch('/payments/advances?returned=0');
        var advances = data.advances || [];
        if (advances.length === 0) {
            list.innerHTML = '<div style="padding:8px;color:#6b7280;font-size:11px;">無預支款</div>';
            return;
        }
        var html = '<div style="font-size:12px;color:#6b7280;margin-bottom:6px;">選擇預支款：</div>';
        for (var i = 0; i < advances.length; i++) {
            var a = advances[i];
            var balance = (a.balance != null) ? a.balance : a.amount - a.expense_total;
            var balanceText = balance > 0 ? '需還款 $' + _fmtNum(balance) : balance < 0 ? '需補款 $' + _fmtNum(Math.abs(balance)) : '已結清';
            var balanceColor = balance > 0 ? '#fb923c' : balance < 0 ? '#fca5a5' : '#86efac';
            var payTag = a.is_paid ? '<span style="color:#86efac;font-size:10px;">已發款</span>' : '<span style="color:#6b7280;font-size:10px;">未發款</span>';
            var retTag = a.is_settled ? '<span style="color:#86efac;font-size:10px;">已結清</span>' : a.is_returned ? '<span style="color:#fb923c;font-size:10px;">已收款</span>' : '';
            html += '<label style="display:flex;align-items:flex-start;gap:8px;padding:8px;border:1px solid #2e2e2e;border-radius:6px;margin-bottom:4px;cursor:pointer;background:#1a1a1a;">';
            html += '<input type="radio" name="advance-select" value="' + a.id + '" data-project-id="' + _esc(a.project_id) + '" style="margin-top:3px;" onchange="document.getElementById(\'cash-f-advance_payment_id\').value=this.value;">';
            html += '<div style="flex:1;">';
            html += '<div style="font-weight:600;color:#d1d5db;">' + _esc(a.project_name) + ' — ' + _esc(a.payee_name) + ' ' + payTag + ' ' + retTag + '</div>';
            html += '<div style="font-size:11px;color:#6b7280;">預支 $' + _fmtNum(a.amount) + '　支出 $' + _fmtNum(a.expense_total) + '　<span style="color:' + balanceColor + ';">' + balanceText + '</span></div>';
            html += '</div></label>';
        }
        list.innerHTML = html;
    } catch (e) {
        list.innerHTML = '<div style="padding:8px;color:#fca5a5;">載入失敗</div>';
    }
};

function _showErr(msg) { const el = document.getElementById('cash-modal-error'); el.textContent = msg; el.style.display = 'block'; }

// ── CSV Import ──────────────────────────────────────────────

// ── 對帳／匯入面板 ───────────────────────────────────────────
// recon.js 有 1,500 行 —— 第一次點才載，不放在開 tab 的關鍵路徑上。
let _reconMod = null;

async function openRecon() {
    const panel = document.getElementById('cash-recon-panel');
    const mount = document.getElementById('cash-recon-mount');
    if (!panel || !mount) return;
    closeDetail();
    document.getElementById('cash-list-panel').style.display = 'none';
    panel.style.display = 'flex';
    mount.innerHTML = '<div style="color:#888;padding:20px;">載入中…</div>';
    try {
        if (!_reconMod) _reconMod = await import('../finance/subviews/recon.js');
        // isCurrent：面板關掉之後才回來的 fetch 不要再動 DOM
        await _reconMod.default(mount, { isCurrent: () => panel.style.display !== 'none' });
    } catch (e) {
        mount.innerHTML = '<div style="color:#f87171;padding:20px;">對帳系統載入失敗：'
            + _esc(e.message) + '</div>';
    }
}

function closeRecon() {
    const panel = document.getElementById('cash-recon-panel');
    if (panel) panel.style.display = 'none';
    const list = document.getElementById('cash-list-panel');
    if (list) list.style.display = '';
    // 對帳會寫帳（工作台「補記入帳」、對帳單匯入）→ 回來要看得到新的那幾筆
    loadEntries();
}

function openImportModal() {
    _csvFile = null;
    document.getElementById('cash-drop-filename').textContent = '';
    const r = document.getElementById('cash-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('cash-btn-do-import').disabled = true;
    document.getElementById('cash-import-modal').style.display = 'flex';
}
function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('cash-drop-filename').textContent = file ? file.name : '';
    document.getElementById('cash-btn-do-import').disabled = !file;
}
async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('cash-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        const form = new FormData(); form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/cash-entries/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '匯入失敗');
        const data = await res.json();
        const result = document.getElementById('cash-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        result.style.display = 'block';
        await loadEntries();
    } catch (e) {
        const result = document.getElementById('cash-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally { btn.disabled = false; btn.textContent = '開始匯入'; }
}

// ── 欄位選擇（owner 2026-09-03：「一個編輯按鈕，讓我選擇哪一些欄要出現」）──
//
// 表頭與列的每一格都帶 cash-c-<key>；藏＝display:none（不抽節點：欄寬是 nth-child），
// 規則寫進 #cash-col-style。選擇記在瀏覽器（localStorage），跟排序一樣是個人偏好。
// 日期／內容固定不給藏；私帳沒有發票，發票欄預設藏（還是可以自己打開，只是空的）。
const _COLS = [
    ['deposit', '收入'], ['card', '信用卡'], ['expense', '支出'], ['book', '類別'], ['item', '項目'],
    ['sub_item', '子項目'], ['bank_memo', '銀行資訊'], ['note', '附註'], ['project', '專案'],
    ['invoice', '發票'], ['payment', '請款單'], ['account', '帳戶'],
];
const _COLS_KEY = 'cash_hidden_cols';
function _hiddenCols() {
    try {
        const v = JSON.parse(localStorage.getItem(_COLS_KEY) || 'null');
        if (Array.isArray(v)) return new Set(v);
    } catch (_) { /* 壞值當沒存 */ }
    return new Set(ledgerHasInvoices() ? [] : ['invoice']);
}
function _applyCols(hidden) {
    let st = document.getElementById('cash-col-style');
    if (!st) { st = document.createElement('style'); st.id = 'cash-col-style'; document.head.appendChild(st); }
    st.textContent = [...hidden].map((k) => `#cash-list-panel .cash-c-${k}{display:none;}`).join('');
}
function _initColumnChooser() {
    const btn = document.getElementById('cash-btn-cols'), pop = document.getElementById('cash-cols-pop');
    if (!btn || !pop) return;
    let hidden = _hiddenCols();
    _applyCols(hidden);
    const draw = () => {
        pop.innerHTML = `<div style="color:#888;font-size:11px;margin-bottom:6px;">要顯示的欄（日期、內容固定）</div>`
            + _COLS.map(([k, l]) => `<label><input type="checkbox" data-col="${k}" ${hidden.has(k) ? '' : 'checked'}> ${l}</label>`).join('')
            + `<div style="margin-top:8px;display:flex;gap:6px;"><button type="button" class="crm-btn crm-btn-secondary crm-btn-sm" data-cols-all>全部顯示</button>
               <button type="button" class="crm-btn crm-btn-secondary crm-btn-sm" data-cols-close>關閉</button></div>`;
    };
    btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (pop.style.display === 'none') { draw(); pop.style.display = 'block'; } else { pop.style.display = 'none'; }
    });
    pop.addEventListener('click', (ev) => ev.stopPropagation());
    pop.addEventListener('change', (ev) => {
        const cb = ev.target.closest('input[data-col]');
        if (!cb) return;
        if (cb.checked) hidden.delete(cb.dataset.col); else hidden.add(cb.dataset.col);
        try { localStorage.setItem(_COLS_KEY, JSON.stringify([...hidden])); } catch (_) { /* 私密視窗 */ }
        _applyCols(hidden);
    });
    pop.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-cols-all]')) {
            hidden = new Set();
            try { localStorage.setItem(_COLS_KEY, '[]'); } catch (_) { /* 同上 */ }
            _applyCols(hidden); draw();
        }
        if (ev.target.closest('[data-cols-close]')) pop.style.display = 'none';
    });
    document.addEventListener('click', () => { pop.style.display = 'none'; });
}

// ── Init ────────────────────────────────────────────────────

export async function initCrmCashbookTab() {
    for (const id of ['cash-modal', 'cash-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
    _initColumnChooser();
    window._cashSelect = selectEntry;
    window._cashRefresh = loadEntries;
    window._cashEdit = (id) => { const e = _entries.find(x => x.id === id); if (e) openModal(e); };
    window._cashDelete = (id) => { const e = _entries.find(x => x.id === id); if (e) deleteEntry(e); };
    window._cashDup = (id) => {
        const e = _entries.find(x => x.id === id);
        if (e) { openModal(e); _editingId = null; document.getElementById('cash-modal-title').textContent = '複製收支'; }
    };

    let _t;
    document.getElementById('cash-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t);
        _t = setTimeout(() => loadEntries({ cards: false }), 300);   // 搜尋不影響卡片餘額
    });
    // 篩選控件同理 —— 卡片餘額只跟 entity 有關
    const _offBtn = document.getElementById('cash-filter-off');
    if (_offBtn) {
        _offBtn.addEventListener('click', () => {
            _offOnly = !_offOnly;
            _offBtn.setAttribute('aria-pressed', String(_offOnly));
            _offBtn.classList.toggle('crm-btn-primary', _offOnly);
            _offBtn.classList.toggle('crm-btn-secondary', !_offOnly);
            renderList();
        });
    }
    // 批次分類（分類下拉的監聽在 _batchTaxDraw 裡 —— 動態長出來的，這裡綁不到）
    document.getElementById('cash-btn-batch')?.addEventListener('click',
        () => _batchSetMode(!_batch.on));
    document.getElementById('cash-batch-exit')?.addEventListener('click',
        () => _batchSetMode(false));
    document.getElementById('cash-batch-all')?.addEventListener('click', () => {
        // 全選＝目前**篩選出來的全部**，不是畫面上那批（分批繪製一次只畫 200 列，
        // 只選看得到的會讓「全選」在 800 筆的清單上默默漏掉 600 筆）
        _shown.forEach((e) => _batch.sel.add(e.id));
        _batchPaint();
    });
    document.getElementById('cash-batch-same')?.addEventListener('click', () => {
        const last = _entries.find((e) => e.id === _batch.last);
        if (!last) { crmToast('先點一列，再按「選相同內容」'); return; }
        const key = (last.summary || '').trim();
        _shown.forEach((e) => { if ((e.summary || '').trim() === key) { _batch.sel.add(e.id); } });
        _batchPaint();
    });
    document.getElementById('cash-batch-none')?.addEventListener('click', () => {
        _batch.sel.clear(); _batch.last = null; _batchPaint();
    });
    document.getElementById('cash-batch-apply')?.addEventListener('click', () => {
        const pick = _batchPick();
        if (!pick.label) { crmToast('先選要套的分類'); return; }
        _batchApply(pick);
    });
    document.getElementById('cash-batch-clear')?.addEventListener('click',
        () => _batchApply({ label: '', body: _taxTree.length
            ? { taxonomy_node_id: '' } : { category: '' } }));
    // 分類篩選的監聽在 _syncTaxFilter 裡（下拉是動態長出來的，這裡綁不到）
    document.getElementById('cash-filter-dir').addEventListener('change', e => { _filters.direction = e.target.value; loadEntries({ cards: false }); });
    document.getElementById('cash-filter-from')?.addEventListener('change', e => { _filters.date_from = e.target.value; loadEntries({ cards: false }); });
    document.getElementById('cash-filter-to')?.addEventListener('change', e => { _filters.date_to = e.target.value; loadEntries({ cards: false }); });
    // 金額打字用同一個 debounce（連打數字別每鍵打一次 API）
    for (const [id, key] of [['cash-filter-amin', 'amount_min'], ['cash-filter-amax', 'amount_max']]) {
        document.getElementById(id)?.addEventListener('input', e => {
            _filters[key] = e.target.value.trim();
            clearTimeout(_t);
            _t = setTimeout(() => loadEntries({ cards: false }), 350);
        });
    }

    document.getElementById('cash-btn-add').addEventListener('click', () => openModal());
    document.getElementById('cash-btn-import').addEventListener('click', openImportModal);
    // 對帳／匯入：**就地展開**，不跳頁（owner 2026-08-22：「對帳系統按了之後
    // 就直接在收支表這裡對帳」）。整套對帳系統住在 finance/subviews/recon.js，
    // 這裡只負責讓位與掛載 —— 一行對帳邏輯都不複製（分類規則有兩份，使用者
    // 在 A 改完到 B 看不到效果，是最容易靜默錯帳的一種重複）。
    document.getElementById('cash-btn-recon').addEventListener('click', openRecon);
    document.getElementById('cash-recon-back').addEventListener('click', closeRecon);
    document.getElementById('cash-btn-save').addEventListener('click', saveEntry);
    document.getElementById('cash-detail-close').addEventListener('click', closeDetail);
    document.getElementById('cash-btn-do-import').addEventListener('click', doImport);

    // Show/hide advance section + project/invoice based on category
    var catEl = document.getElementById('cash-f-category');
    if (catEl) catEl.addEventListener('change', function() { _toggleAdvanceFields(this.value === '專案雜支'); });

    // Modal dynamic: client match
    for (const id of ['cash-f-invoice_id', 'cash-f-deposit', 'cash-f-bank_fee', 'cash-f-summary', 'cash-f-note']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input',
            _updateClientMatch);
    }
    _wireBankFeeSplit();

    document.getElementById('cash-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('cash-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over');
        const f = e.dataTransfer.files[0]; if (f && f.name.endsWith('.csv')) _setCsvFile(f); });

    for (const id of ['cash-modal', 'cash-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('cash-resize-handle', 'cash-detail-panel');
    // 🔴 loadEntries 這裡**不畫**：這幾支是並行的，它內部的 renderList 幾乎一定跑在
    // _loadBankAccounts 回來之前 —— 那時 _bankAccounts 還是 null，帳戶切換列畫不出來、
    // 列表的「帳戶」欄也是空的。等全部到齊再畫一次就好（畫兩次是 1,600 列 ×2）。
    await Promise.all([loadEntries({ render: false }), _loadLinkLists(), _loadProjectList(),
                       _loadClientList(), _loadBankAccounts(), _loadCashOptions()]);
    // 🔴 這裡要連 _syncFilterOptions 一起補畫，不是只有 renderList。
    // 帳戶切換列由 _syncFilterOptions → _renderAcctTabs 畫，而 loadEntries 內部那次
    // 幾乎一定跑在 _loadBankAccounts 回來之前（六支並行）—— 那時 _bankAccounts 還是
    // null，_renderAcctTabs 會把容器清成空字串，然後就再也沒有人重畫它。
    // 2026-08-20 實測：三顆帳戶鈕與總表全部消失，而且時好時壞（看誰先回來）。
    _syncFilterOptions();
    renderList();
}

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
function _allocRemain(actual, items) {
    const used = (items || []).reduce((n, x) => n + (Number(x.amount) || 0), 0);
    return Math.max(0, (Number(actual) || 0) - used);
}

/** PUT 的 items 形狀：{idKey, amount[, fee]} —— 面板存檔與就地連結同一份投影。 */
function _allocBody(c, items) {
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
const _ALLOC_SIDES = {
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
function _renderEmptyPayBox(entryId) {
    _CASH_PAY.entryId = entryId;
    _CASH_PAY.items = [];
    _CASH_PAY.check = null;
    _renderAllocs('payment');
}

async function loadCashInvoiceAllocs(entryId) {
    return _loadAllocs('invoice', entryId);
}

async function loadCashPaymentAllocs(entryId) {
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


// ── 源日請款（＝推送到母公司零用金；owner 2026-08-27 命名）──────────
//
// 「我收支表想要有個按鈕，可以推送到應收款，然後進到 crm 請款」＋「我希望接進去
// 的是零用金系統」。這裡不另造流程：把收支的那一列變成一張**零用金草稿單據**
// （crm_project_expenses），之後走既有的送出→核准→應付款→匯款。
//
// 🔴 付款方式不影響（owner：「不管是匯款或信用卡都可以直接接到 crm 的零用金
// 請款」）—— 卡片列與銀行列一視同仁，判準只有「這一列有沒有流出金額」。
// 🔴 只建草稿。送出＝即核准即產應付款，那一步要人在零用金那邊按。
// 🔴 命名：使用者看到的是**「源日請款」**（他站在私帳這一側，這動作就是「跟源日
// 請這筆錢」）；程式/資料/另一個 Tab 仍叫零用金（petty）—— 那是母公司那側的
// 系統名，別為了對齊而去改 petty_status／端點／Tab 名。

/** 這一列流出多少（卡片列與銀行列同一個式子；後端 `_cash_claim_amount` 的鏡像）。 */
const _pettyAmt = (e) => _grossOut(e) + (e.claim || 0);

/** 已推送的列在摘要後面帶一個狀態標 —— 不標的話這一列跟沒推過長得一樣。 */
function _pettyTag(e) {
    if (!e.petty_status) { return ''; }
    return `<span class="cash-petty" title="已送出源日請款">源日·${_esc(e.petty_status)}</span>`;
}

function _pettyMenu(e) {
    if (e.petty_status || e.expense_id) {   // expense_id 而無狀態＝單據被刪的空殼連結
        return [{ label: e.petty_status ? '撤銷源日請款' : '清除源日請款連結',
                  fn: '_cashPettyUndo' }];
    }
    return _pettyAmt(e) > 0 ? [{ label: '源日請款', fn: '_cashPettyPush' }] : [];
}

// 請款人：正本是「呼叫者綁定的人員檔案」（後端從 token 解，前端不傳）。
// 帳號還沒綁的時候才落到代管路徑 —— 那時挑一次人，之後這個 session 沿用。
// owner 2026-08-27：「登記人都是王士源」，所以代管的預設就挑他，但選單留著。
const _PETTY_DEFAULT_NAME = '王士源';
let _pettyStaffId = '';
let _pettyStaffOpts = [];

const _pettyPath = (suffix = '') => (_pettyStaffId
    ? `/petty/staff/${encodeURIComponent(_pettyStaffId)}/from-cash${suffix}`
    : `/petty/from-cash${suffix}`);

/** 先走本人路徑；帳號沒綁人員檔案（409）才挑一個人走代管。 */
async function _pettyPost(body) {
    try {
        return await _fetch(_pettyPath(), {
            method: 'POST', body: JSON.stringify(body) });
    } catch (e) {
        if (_pettyStaffId || !/綁定/.test(e.message || '')) { throw e; }
        const o = await _fetch('/petty/staff-options');
        _pettyStaffOpts = o.staff || [];
        const pick = _pettyStaffOpts.find(x => x.name === _PETTY_DEFAULT_NAME)
                     || _pettyStaffOpts[0];
        if (!pick) { throw e; }
        _pettyStaffId = pick.id;
        return await _fetch(_pettyPath(), {
            method: 'POST', body: JSON.stringify(body) });
    }
}

const _pettyClose = () => {
    const ov = document.getElementById('cash-petty-overlay');
    if (ov) { ov.remove(); }
};

/** 推送前先試算給人看 —— 推完那筆錢就進了公司的請款流程，不該是一鍵無聲的。 */
window._cashPettyPush = async function (id) {
    let pv;
    try {
        pv = await _pettyPost({ entry_id: id, preview: true });
    } catch (err) { crmToast('推送失敗：' + err.message); return; }
    const row = pv.row || {};
    _pettyClose();
    const ov = document.createElement('div');
    ov.id = 'cash-petty-overlay';
    ov.className = 'crm-modal-overlay';
    ov.style.display = 'flex';
    ov.addEventListener('click', ev => { if (ev.target === ov) { _pettyClose(); } });
    // 值域由後端供（_petty_item_domain）：下拉選的跟寫入時驗的是同一份清單，
    // 前端不自己濾 —— 濾法一漂，選得到的跟存得進的就不是同一批。
    const items = (pv.items || []).map(c =>
        `<option value="${_esc(c)}"${c === row.item ? ' selected' : ''}>${_esc(c)}</option>`).join('');
    ov.innerHTML = `
      <div class="crm-modal" style="max-width:460px;">
        <div class="crm-modal-header"><h3>源日請款</h3>
          <button onclick="window._cashPettyClose()" class="crm-detail-close">✕</button></div>
        <div class="crm-modal-body">
          <table class="crm-table" style="width:100%;font-size:12px;">
            <tr><td style="color:#bbb;width:80px;">日期</td><td>${_esc(row.date || '')}</td></tr>
            <tr><td style="color:#bbb;">摘要</td><td>${_esc(row.summary || '')}</td></tr>
            <tr><td style="color:#bbb;">收支類別</td><td>${_esc(row.category || '（未分類）')}</td></tr>
            <tr><td style="color:#bbb;">請款人</td><td>${_pettyStaffOpts.length
                ? `<select id="cash-petty-staff" class="crm-input" style="width:100%;">`
                  + _pettyStaffOpts.map(x => `<option value="${_esc(x.id)}"${
                        x.id === _pettyStaffId ? ' selected' : ''}>${_esc(x.name)}</option>`).join('')
                  + `</select>`
                : _esc((pv.staff || {}).name || '')}</td></tr>
            <tr><td style="color:#ddd;font-weight:600;">請款金額</td>
                <td style="font-weight:600;color:#eee;">$${_fmtNum(row.amount || 0)}</td></tr>
          </table>
          <div class="crm-form-section" style="margin-top:14px;">會計項目</div>
          <select id="cash-petty-item" class="crm-input" style="width:100%;">
            <option value="">— 請選擇 —</option>${items}</select>
          <div style="color:#666;font-size:11px;margin-top:4px;">
            ${row.item ? '由收支類別對映帶出，可以改。' :
                '這個收支類別沒有對應的會計項目，請自己挑一個。'}</div>
          <div id="cash-petty-err" style="display:none;color:#fca5a5;font-size:12px;margin-top:8px;"></div>
          <div style="color:#666;font-size:11px;margin-top:12px;">
            會建一張<b>草稿</b>單據掛在請款人名下。要真的請款，到
            「財務管理 → 零用金 → 我的請款」按送出（送出即核准並產生應付款）。</div>
        </div>
        <div class="crm-modal-footer">
          <button class="crm-btn crm-btn-secondary" onclick="window._cashPettyClose()">取消</button>
          <button class="crm-btn crm-btn-primary" onclick="window._cashPettyConfirm(this,'${_esc(id)}')"
                  ${row.blocked ? 'disabled title="' + _esc(row.blocked) + '"' : ''}>
            ${row.blocked ? _esc(row.blocked) : '建立草稿單據'}</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
};

window._cashPettyClose = _pettyClose;

window._cashPettyConfirm = async function (btn, id) {
    const err = document.getElementById('cash-petty-err');
    const item = document.getElementById('cash-petty-item').value;
    if (!item) {
        err.textContent = '請先選會計項目 —— 空著會落到「其他」，之後很難翻出來。';
        err.style.display = 'block';
        return;
    }
    const sel = document.getElementById('cash-petty-staff');
    if (sel) { _pettyStaffId = sel.value; }
    btn.disabled = true;
    try {
        const r = await _pettyPost({ entry_id: id, item });
        _pettyClose();
        crmToast(`已建立草稿單據 $${_fmtNum(r.amount || 0)}`);
        // 卡片摘要不會被推送改到（只動 expense_id/petty_status，不動金額）
        await loadEntries({ cards: false });
    } catch (e) {
        err.textContent = e.message; err.style.display = 'block';
        btn.disabled = false;
    }
};

window._cashPettyUndo = async function (id) {
    if (!confirm('撤銷推送？會刪掉那張草稿單據並解開連結。')) { return; }
    try {
        await _fetch('/petty/from-cash/' + encodeURIComponent(id), { method: 'DELETE' });
        crmToast('已撤銷');
        await loadEntries({ cards: false });
    } catch (e) { crmToast('撤銷失敗：' + e.message); }
};
