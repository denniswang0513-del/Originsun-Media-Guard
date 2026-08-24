/**
 * crm-cashbook.js — 收支明細子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, projectOptionsHtml, today, autoFee } from './crm-utils.js';
// 兩本帳（公司實體）— docs/LEDGER_ENTITY_PLAN.md §5。帳本由頁面隱形 pin：
// 財務 tab＝'parent'（預設）、/my-ledger.html＝'mine'（該頁在載入財務模組前設
// window._finEntity）。無使用者可見的帳本選單（單一 tab 單一帳本）。query 一律帶
// pin 值；payload 只在 'mine' 才帶 entity:'mine'（後端 None 語意：建立落 parent、
// 更新維持既有值 —— 不洗欄位）。pin 與 /api/v1/finance 的 fetch 都用財務模組那份，
// 不在這裡複寫（finFetch 會自己附 entity）。
import { finEntity as _pinEntity, finFetch as _finFetch,
         bankOnly as _bankOnly } from '../finance/fin-utils.js';

let _entries = [];
let _invoiceList = [];
let _paymentList = [];   // 請款單（付款側的分配用）
let _projectList = [];
let _clientList = [];
let _bankAccounts = null;   // 財務模組銀行帳戶；null = 載入失敗/未啟用（優雅降級：不顯示帳戶欄）
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', bank_account_id: '', direction: '' };
let _csvFile = null;
const _catVocab = new Set();   // 類別篩選器的詞彙（只增不減，見 _syncFilterOptions）

function _toggleAdvanceFields(isAdv) {
    var ids = ['cash-advance-section', 'cash-field-project', 'cash-field-invoice', 'cash-field-bankfee'];
    for (var i = 0; i < ids.length; i++) {
        var el = document.getElementById(ids[i]);
        if (el) el.style.display = (i === 0 ? isAdv : !isAdv) ? '' : 'none';
    }
}

// ── Data Loading ────────────────────────────────────────────

async function loadEntries({ render = true, cards = true } = {}) {
    const params = new URLSearchParams();
    if (_filters.q)               params.set('q', _filters.q);
    if (_filters.category)        params.set('category', _filters.category);
    if (_filters.bank_account_id) params.set('bank_account_id', _filters.bank_account_id);
    if (_filters.direction)       params.set('direction', _filters.direction);
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
}

// ── List ────────────────────────────────────────────────────

const _sorter = createSortable({
    storageKey: 'crm_cashbook_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'cash-list-panel',
    onChange: () => renderList(),
    getters: {
        date:     e => e.entry_date || '',
        summary:  e => (e.summary || '').toLowerCase(),
        deposit:  e => e.deposit || 0,
        expense:  e => _bankOut(e),
        card:     e => _cardAmt(e),
        category: e => (e.category || '').toLowerCase(),
        project:  e => (e.project_name || '').toLowerCase(),
        invoice:  e => (e.invoice_title || '').toLowerCase(),
        account:  e => _acctName(e.bank_account_id).toLowerCase(),
        note:     e => (e.note || '').toLowerCase(),
    },
});

/** 多行備註壓成一行（列表格子只有一行高，換行會被吃掉看不出斷點）。 */
function _flat(text, sep) {
    return String(text || '').split('\n').filter(Boolean).join(sep);
}

/** 帳戶 id → 顯示名。帳戶清單載入失敗（_bankAccounts=null）時回空字串，不炸。 */
function _acctName(id) {
    if (!id || !_bankAccounts) return '';
    const a = _bankAccounts.find(x => String(x.id) === String(id));
    return a ? (a.name || '') : '';
}

/** 篩選器選項依**實際資料**生成 —— 寫死的清單會跟使用中的類別脫節，
 *  選了卻篩不到東西（原本的「請款/收支」兩個選項就是這樣，永遠 0 筆）。 */
function _syncFilterOptions() {
    const cat = document.getElementById('cash-filter-cat');
    if (cat) {
        // 🔴 詞彙要**累積**，不能每次拿當下篩出來的結果重建 —— 那樣選了「專案雜支」
        // 之後選單裡就只剩「專案雜支」一個選項，再也切不到別的類別（2026-08-20 實測：
        // 從專案雜支切轉存變成整個篩選被清空）。首次載入是未篩選的，詞彙那時就齊了；
        // 之後只增不減。
        _entries.forEach(e => { if (e.category) _catVocab.add(e.category); });
        const used = [..._catVocab].sort();
        cat.innerHTML = '<option value="">全部類別</option>'
            + used.map(v => `<option value="${_esc(v)}"${v === _filters.category ? ' selected' : ''}>${_esc(v)}</option>`).join('');
    }
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
    const total = _bankAccounts.reduce((sum, a) => sum + (a.current_balance || 0), 0);
    const tab = (id, name, bal) => `
        <button type="button" class="cash-acct-tab${String(id) === _filters.bank_account_id ? ' active' : ''}"
                data-acct="${_esc(id)}">${_esc(name)}<b>${money(bal)}</b></button>`;
    box.innerHTML = tab('', '總表', total)
        + _bankAccounts.map(a => tab(a.id, a.name, a.current_balance)).join('')
        + _cardChipHtml();
    box.querySelectorAll('[data-acct]').forEach(btn => {
        btn.addEventListener('click', () => {
            _filters.bank_account_id = btn.dataset.acct;
            loadEntries({ cards: false });   // 換帳戶頁籤不影響卡片餘額
        });
    });
}

/* ── 信用卡：未繳餘額 + 還款記帳 ─────────────────────────────
 *
 * 刷卡不動銀行（列在「信用卡」欄），月底繳款才是銀行支出 —— 所以「現在欠多少」
 * 不是任何一個帳戶餘額看得出來的，要另外算：期初 + 刷卡 − 還款。
 * 🔴 期初不可省：owner 的資料從 2023/10 起，卡片在那之前就有餘額（實測不給
 * 期初會算出 −36,988，而當時實際是 +2,428）。設定裡可以直接填「現在實際欠
 * 多少」讓後端反推期初 —— 對帳單上的未繳金額是手上唯一可信的數字。
 */
let _cardSummary = null;

function _cardChipHtml() {
    if (!_cardSummary || !_cardSummary.charge_count) return '';
    const n = _cardSummary.outstanding;
    return `<button type="button" class="cash-acct-tab" title="期初 ${_fmtNum(_cardSummary.opening)}
 + 刷卡 ${_fmtNum(_cardSummary.charges)} − 還款 ${_fmtNum(_cardSummary.repayments)}"
            onclick="window._cashCardPanel()">💳 信用卡未繳<b class="${n < 0 ? 'pos' : 'neg'}">$${_fmtNum(n)}</b></button>`;
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
const _NO_CAT_DOT =
    '<span title="還沒填分類 —— 三表會把它歸到「未歸類」，點開這一列補上"'
    + ' style="display:inline-block;width:7px;height:7px;border-radius:50%;'
    + 'background:#ef4444;vertical-align:middle;"></span>';

function renderList() {
    const body = document.getElementById('cash-list-body');
    if (!body) return;
    _sorter.attach();
    if (_entries.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無收支紀錄${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    // 每列各算一次就好 —— 三元判斷與輸出各呼叫一次的話，4,704 列會多跑
    // 9,408 次同樣的計算
    body.innerHTML = _sorter.sorted(_entries).map((e) => {
        const card = _cardAmt(e), out = _bankOut(e);
        return `
        <div class="crm-row${e.id === _selectedId ? ' selected' : ''}" onclick="window._cashSelect('${e.id}')">
            <div class="crm-row-date">${e.entry_date ? e.entry_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-name">${_esc(e.summary)}</div>
            <div style="color:#86efac;">${e.deposit ? '$' + _fmtNum(e.deposit) : ''}</div>
            <div class="cash-col-card" style="color:#c4b5fd;">${card ? '$' + _fmtNum(card) : ''}</div>
            <div style="color:#fca5a5;">${out ? '$' + _fmtNum(out) : ''}</div>
            <div>${e.category ? _esc(e.category) : _NO_CAT_DOT}</div>
            <div title="${_esc(_flat(e.note, ' '))}">${_esc(_flat(e.note, ' · '))}</div>
            <div>${_esc(e.project_name || '')}</div>
            <div>${_esc(e.invoice_title || '')}</div>
            <div>${_esc(_acctName(e.bank_account_id))}</div>
            ${kebabMenuHtml(e.id, { onEdit: '_cashEdit', onDuplicate: '_cashDup', onDelete: '_cashDelete' })}
        </div>
    `;
    }).join('');
}

/** 刷卡金額：status='card' 的列（刷卡當下不動銀行，所以不算銀行支出）。 */
function _cardAmt(e) {
    return e.status === 'card' ? ((e.expense || 0) + (e.bank_fee || 0)) : 0;
}

/** 銀行支出：卡費列不算（那筆錢還在卡上，月底繳款才真的離開帳戶）。 */
function _bankOut(e) {
    return e.status === 'card' ? 0 : ((e.expense || 0) + (e.bank_fee || 0));
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
    } catch (_) { /* 用 fallback，不擋畫面 */ }
}

/** 專案／發票的即時連結下拉。直接打 PUT /cash-entries/{id} 只送要改的那一欄 ——
 *  該端點是 exclude_unset 部分更新，沒送的欄位不會被動到。 */
function _renderQuickLink(e) {
    const box = document.getElementById('cash-quicklink');
    if (!box) return;
    const row = (label, id, optsHtml) => `
        <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
            <span style="color:#9ca3af;font-size:12px;flex:0 0 48px;">${label}</span>
            <select id="${id}" style="flex:1;min-width:0;">${optsHtml}</select>
        </div>`;
    box.innerHTML = `
        ${row('專案', 'cash-ql-proj', _projOptsHtml(e.project_id, '— 未連結 —'))}
        ${/* 發票下拉只在支出列出現：收入列的發票走上面可掛多張的「關聯發票」區 */ ''}
        ${e.deposit ? '' : row('發票', 'cash-ql-inv', _invOptsHtml(e.invoice_id, '— 未連結 —'))}
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
    if (pj) pj.addEventListener('change', () => save({ project_id: pj.value }));
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
    if (e.category) html += prop('類別', e.category);
    if (e.note) html += prop('備註', e.note);

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
    const isProjectish = _LINKABLE.includes(e.category || '');
    const showInvoiceProp = !e.deposit;   // 收入列的發票由下面「關聯發票」區負責，不重複列
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

    // 關聯發票（合併匯款 / 分期收款）—— 只有收入列有，內容由 loadCashInvoiceAllocs
    // 非同步填。舊的「單張發票驗算」被這區塊取代：它只看得到一張發票，客戶合併
    // 匯款時必然報「不平衡」，等於在對的資料上顯示假警告。
    if (e.deposit) {
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
    if (_LINKABLE.includes(e.category || '')) _renderQuickLink(e);
    if (e.deposit) loadCashInvoiceAllocs(e.id);
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
    _selectedId = id; renderList();
    document.getElementById('cash-detail-panel').style.display = 'flex';
    document.getElementById('cash-resize-handle').style.display = '';
    const e = _entries.find(x => x.id === id);
    if (e) renderDetail(e);
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('cash-detail-panel').style.display = 'none';
    document.getElementById('cash-resize-handle').style.display = 'none';
    renderList();
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
    const invoiceOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _invoiceCandidates(currentInvoiceId).map(inv =>
            ({value:inv.id, label:_invoiceLabel(inv)})));
    const projectOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _projectList.map(p => ({value:p.id, label:p.name + (p.client_short_name ? ' (' + p.client_short_name + ')' : '')})));
    const catOpts = [''].concat(_CATEGORIES).map(v => ({ value: v, label: v || '—' }));
    const fields = [
        {name:'entry_date', label:'日期', type:'date'},
        {name:'deposit', label:'收入', type:'number'},
        {name:'expense', label:'支出', type:'number'},
        {name:'summary', label:'內容', type:'text'},
        {name:'category', label:'類別', type:'select', options:catOpts},
        {name:'note', label:'備註', type:'text'},
        {name:'project_id', label:'專案', type:'select', options:projectOpts},
        {name:'invoice_id', label:'發票', type:'select', options:invoiceOpts},
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

/** 新增/編輯 modal 的類別下拉（HTML 不再寫死選項）。 */
function _populateCategorySelect(selected) {
    const sel = document.getElementById('cash-f-category');
    if (!sel) return;
    sel.innerHTML = '<option value="">—</option>' + _CATEGORIES.map(v =>
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
    if (_pinEntity() === 'mine') payload.entity = 'mine';
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

// ── Init ────────────────────────────────────────────────────

export async function initCrmCashbookTab() {
    for (const id of ['cash-modal', 'cash-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
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
    // 兩個篩選下拉同理 —— 卡片餘額只跟 entity 有關
    document.getElementById('cash-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadEntries({ cards: false }); });
    document.getElementById('cash-filter-dir').addEventListener('change', e => { _filters.direction = e.target.value; loadEntries({ cards: false }); });

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

/** 這筆收款還沒被分配掉的入帳金額。掛上一張發票時拿它預帶匯費：
 *  「還沒收的部分」比「還剩多少錢可分」多幾十塊 → 那幾十塊就是被匯出行扣走的。
 *  check.actual 是後端送來的實收（收付兩側同一個欄位，見 _allocStatusLine）。 */
function _allocRemainCash() {
    const st = _CASH_ALLOC;
    const actual = (st && st.check && st.check.actual) || 0;
    const used = ((st && st.items) || []).reduce((n, x) => n + (Number(x.amount) || 0), 0);
    return Math.max(0, actual - used);
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
        toItem: i => ({
            invoice_id: i.id, amount: _outstanding(i),
            fee: autoFee(_outstanding(i), _allocRemainCash()),
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
        candidates: (q, picked) => _paymentList.filter(
            p => !picked.has(p.id) && [p.payee_name, p.summary, p.project_label, p.category]
                .some(v => (v || '').toLowerCase().includes(q))),
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
            st.items.push(c.toItem(hit));
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
            items: st.items.map(x => ({
                [c.idKey]: x[c.idKey], amount: Number(x.amount) || 0,
                ...(c.perItemFee ? { fee: Math.max(0, Math.round(Number(x.fee) || 0)) } : {}),
            })),
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
