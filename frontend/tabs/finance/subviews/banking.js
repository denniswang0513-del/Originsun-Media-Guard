/**
 * banking.js — 🏦 銀行帳戶子視圖（財務管理 Tab 階段二）
 *
 * 區塊：帳戶卡片（目前餘額/預設★/停用）→ 未掛帳提示（整批掛到預設帳戶）
 *       → 銀行貸款（階段四：卡片＋攤還表＋一鍵記繳款，自動寫收支明細）
 *       → 月底對帳（POST /reconciliations + 歷史表）→ 進階摺疊：帳務調整。
 * 後端 API prefix /api/v1/finance（fin-utils.finFetch）。
 */
import { finFetch, finEntity, esc, fmtNum, finToast, finSubviewBoot, todayStr, metricCard, ACCT_KIND_OPTIONS, isShareholderAcct } from '../fin-utils.js';
import { createSortable, sortableTh, enumIndex } from '../../crm/crm-utils.js';   // 點欄頭排序（通用排序器）
import { bearerHeader } from '../../../js/shared/utils.js';   // 送 FormData 時不能自帶 Content-Type

const KIND_LABEL = Object.fromEntries(ACCT_KIND_OPTIONS.map(k => [k.v, k.label]));
const ADJ_TYPES = [
    { v: 'opening', label: '期初結轉' },
    { v: 'owner_in', label: '業主投入（老闆拿錢進公司）' },
    { v: 'owner_out', label: '業主提領（老闆從公司拿錢）' },
    { v: 'accountant', label: '會計師調整' },
    { v: 'other', label: '其他調整' },
];
// ⚠ 值域對齊後端 /loans 的 method — 下拉只給白話，不裸露「攤提/年金法」術語
const LOAN_METHODS = [
    { v: 'annuity', label: '每月固定金額（等額本息）' },
    { v: 'straight', label: '每月固定本金（等額本金）' },
    { v: 'interest_only', label: '每月只繳利息，到期還本金' },
];
const LOAN_METHOD_LABEL = Object.fromEntries(LOAN_METHODS.map(m => [m.v, m.label]));
const LOAN_GROUP_KEY = 'finance_loan_groups_open';   // 展開中的銀行（依 lender 名）

let _c = null;
let _isCurrent = () => true;
let _accounts = [];      // 銀行帳戶
let _drafts = [];        // 對帳單匯入草稿（掛到一半的）
let _unassigned = 0;
let _adjustments = [];
let _coa = [];           // 會計科目（調整表下拉用）
let _editingId = null;   // 帳戶 modal：null=新增
let _loans = [];         // 銀行貸款
let _loanErr = null;     // /loans 載入失敗訊息（不擋其他區塊）
let _editingLoanId = null; // 貸款 modal：null=新增
let _schedLoanId = null; // 攤還表 modal 目前開的貸款 id
let _wb = null;          // 對帳工作台：{acct, month, data}；null=未開
let _wbImportRows = null; // 匯入流程暫存：貼上解析後的儲存格陣列
let _stmtPreview = null; // 對帳單匯入：preview 回來的列（確認後才寫入）

let _schedItems = null;  // 最近一次攤還表 items（排序重繪用）
let _reconItems = null;  // 最近一次月結對帳歷史 items（排序重繪用）

const _fb = (window._finBank = window._finBank || {});

// ── 點欄頭排序（createSortable；預設 key '' = 不排序、維持後端順序，點了才生效）──
const _schedSorter = createSortable({
    storageKey: 'finance_loan_schedule_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'finbank-sched-main',
    onChange: () => { if (_schedItems) _renderSchedMain(_schedItems); },
    getters: {
        period: r => r.period_no ?? '',
        due: r => r.due_date ? String(r.due_date).substring(0, 10) : '',
        principal: r => r.principal_due ?? '',
        interest: r => r.interest_due ?? '',
        total: r => r.total ?? '',
        status: r => (r.status === 'paid' ? 2 : (r.overdue ? 0 : 1)),   // 逾期→未到期→已繳
    },
});

const _reconSorter = createSortable({
    storageKey: 'finance_recon_history_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'finbank-recon-history',
    onChange: () => _renderReconHistory(),
    getters: {
        month: r => r.month || '',
        stmt: r => r.statement_balance ?? '',
        sys: r => r.system_balance ?? '',
        diff: r => r.diff ?? 0,
        status: r => (((r.diff || 0) === 0 || r.status === 'balanced') ? 1 : 0),
    },
});

const _wbLinesSorter = createSortable({
    storageKey: 'finance_wb_lines_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'finbank-wb-lines',
    onChange: () => _wbRender(),
    getters: {
        date: l => l.line_date || '',
        desc: l => l.description || '',
        amount: l => l.amount ?? '',
        status: l => enumIndex(['unmatched', 'noted', 'matched'], l.status),
    },
});

const _wbEntriesSorter = createSortable({
    storageKey: 'finance_wb_entries_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'finbank-wb-entries',
    onChange: () => _wbRender(),
    getters: {
        date: e => e.entry_date || '',
        summary: e => e.summary || '',
        category: e => e.category || '',
        amount: e => e.amount ?? '',
        matched: e => (e.matched ? 1 : 0),
    },
});

const _adjSorter = createSortable({
    storageKey: 'finance_adjustments_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'finbank-adj-list',
    onChange: () => _renderAdjList(),
    getters: {
        date: a => a.adj_date ? String(a.adj_date).substring(0, 10) : '',
        acct: a => {
            const x = _coa.find(c => String(c.id) === String(a.account_id));
            return x ? x.name : (a.account_id != null ? '#' + a.account_id : '');
        },
        amount: a => a.amount ?? '',
        type: a => (ADJ_TYPES.find(t => t.v === a.adj_type)?.label) || a.adj_type || '',
        desc: a => a.description || '',
    },
});

function _renderAdjList() {
    const el = _c && _c.querySelector('#finbank-adj-list');
    if (!el) return;
    el.innerHTML = _adjListHtml();
    _adjSorter.attach();
}

export default async function render(container, ctx = {}) {
    _c = container;
    _isCurrent = ctx.isCurrent || (() => true);
    const results = await finSubviewBoot(container, {
        title: '🏦 銀行帳戶',
        isCurrent: _isCurrent,
        retry: 'window._finBank.reload()',
        fetchers: [
            () => finFetch('/bank-accounts'),
            () => finFetch('/adjustments').catch(() => ({ items: [] })),
            () => finFetch('/accounts').catch(() => ({ items: [] })),
            () => finFetch('/loans').catch(e => ({ items: [], _error: e.message })),
            () => finFetch('/bank-statement/drafts').catch(() => ({ drafts: [] })),
        ],
    });
    if (!results) return;
    const [bank, adj, coa, loans, drafts] = results;
    _accounts = bank.items || [];
    _unassigned = bank.unassigned_count || 0;
    _adjustments = adj.items || [];
    _coa = coa.items || [];
    _loans = loans.items || [];
    _loanErr = loans._error || null;
    _drafts = drafts.drafts || [];
    _renderShell();
    const sel = _c.querySelector('#finbank-recon-acct');
    if (sel && sel.value) _loadReconHistory(sel.value);
}

_fb.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

// ── Shell ───────────────────────────────────────────────────

function _maskNo(no) {
    if (!no) return '';
    const s = String(no).replace(/\s/g, '');
    return s.length > 5 ? '•••• ' + s.slice(-5) : s;
}

function _card(a) {
    const inactive = a.active === false;
    const bal = a.current_balance || 0;
    const balColor = bal < 0 ? '#fca5a5' : '#86efac';
    return `
    <div style="background:#222;border:1px solid #333;border-radius:8px;padding:14px 16px;min-width:230px;flex:0 1 280px;${inactive ? 'opacity:.55;' : ''}">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span style="font-weight:600;color:#eee;">${esc(a.name)}</span>
            ${a.is_default ? '<span title="預設帳戶" style="color:#fbbf24;">★</span>' : ''}
            <span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#1e3a5f;color:#93c5fd;">${esc(KIND_LABEL[a.acct_kind] || a.acct_kind || '')}</span>
            ${inactive ? '<span style="font-size:10px;color:#f87171;border:1px solid #7f1d1d;border-radius:8px;padding:1px 6px;">已停用</span>' : ''}
        </div>
        <div style="color:#888;font-size:12px;margin-top:3px;">${esc(a.bank_name || '')} ${esc(_maskNo(a.account_no))}</div>
        <div style="font-size:22px;font-weight:700;color:${balColor};margin-top:8px;">$${fmtNum(bal)}</div>
        <div style="color:#666;font-size:11px;margin-top:2px;">期初 $${fmtNum(a.opening_balance)}${a.opening_date ? '（' + esc(String(a.opening_date).substring(0, 10)) + '）' : ''}</div>
        ${a.note ? `<div style="color:#777;font-size:11px;margin-top:4px;">${esc(a.note)}</div>` : ''}
        <div style="display:flex;gap:6px;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.edit('${esc(a.id)}')">編輯</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.toggleActive('${esc(a.id)}')">${inactive ? '啟用' : '停用'}</button>
        </div>
    </div>`;
}

/** 股東往來卡：語意跟銀行帳戶相反 —— 數字是**公司欠這位股東多少**，不是公司有多少錢。
 *  正數＝還欠著、負數＝股東反而欠公司（多領了）。顏色刻意不用綠（綠會讀成「有錢」）。 */
function _shCard(a) {
    const inactive = a.active === false;
    const owed = a.current_balance || 0;
    return `
    <div style="background:#241f18;border:1px solid #4a3a20;border-radius:8px;padding:14px 16px;min-width:230px;flex:0 1 280px;${inactive ? 'opacity:.55;' : ''}">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span style="font-weight:600;color:#eee;">${esc(a.name)}</span>
            <span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#3a2d14;color:#fbbf24;">${esc(KIND_LABEL[a.acct_kind] || '')}</span>
            ${inactive ? '<span style="font-size:10px;color:#f87171;border:1px solid #7f1d1d;border-radius:8px;padding:1px 6px;">已停用</span>' : ''}
        </div>
        <div style="font-size:22px;font-weight:700;color:${owed < 0 ? '#93c5fd' : '#fbbf24'};margin-top:8px;">$${fmtNum(owed)}</div>
        <div style="color:#888;font-size:11px;margin-top:2px;">${owed < 0 ? '股東欠公司' : '公司欠股東'}</div>
        <div style="color:#666;font-size:11px;margin-top:2px;">期初 $${fmtNum(a.opening_balance)}${a.opening_date ? '（' + esc(String(a.opening_date).substring(0, 10)) + '）' : ''}</div>
        ${a.note ? `<div style="color:#777;font-size:11px;margin-top:4px;">${esc(a.note)}</div>` : ''}
        <div style="display:flex;gap:6px;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.edit('${esc(a.id)}')">編輯</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.toggleActive('${esc(a.id)}')">${inactive ? '啟用' : '停用'}</button>
        </div>
    </div>`;
}

function _shareholderSection() {
    const rows = _accounts.filter(a => isShareholderAcct(a.acct_kind));
    const owed = rows.reduce((n, a) => n + (a.current_balance || 0), 0);
    return `
    <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
        <h3 style="margin:0 0 4px;color:#eee;font-size:15px;">🤝 股東往來</h3>
        <p style="color:#888;font-size:12px;margin:0 0 12px;">
            公司跟股東之間的錢。<b>股東墊付或把錢放進公司 → 數字變大</b>（公司欠款增加）；
            <b>公司匯還股東 → 數字變小</b>。這裡的錢<b>不算公司現金</b> ——
            報表上借款進負債、投資款進權益。
        </p>
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;">
            ${rows.length ? rows.map(_shCard).join('')
              : '<div style="color:#666;font-size:12px;padding:10px 0;">還沒有股東往來帳戶 —— 用右邊那顆新增，類型選「股東往來」。</div>'}
            <button class="crm-btn crm-btn-secondary" style="min-width:150px;min-height:120px;border-style:dashed;"
                    onclick="window._finBank.openAdd('shareholder_loan')">+ 新增股東往來</button>
        </div>
        ${rows.length ? `<div style="color:#888;font-size:12px;margin-top:10px;">
            合計 公司欠股東 <b style="color:#fbbf24;">$${fmtNum(owed)}</b></div>` : ''}
    </div>`;
}

/** 啟用中的**真銀行**帳戶（排除股東往來）。
 *  對帳單匯入、分類規則、貸款扣款、對帳工作台都只該看到這些 ——
 *  股東往來沒有銀行對帳單、也不會拿來扣貸款。收支明細那邊的帳戶下拉不受此限
 *  （股東墊付的費用本來就要掛到股東帳戶上）。 */
function _bankOnly() {
    return _accounts.filter(a => a.active !== false && !isShareholderAcct(a.acct_kind));
}

/** 預設帳戶：is_default 且啟用中；沒有就取第一個啟用的銀行帳戶；都沒有回 null */
function _defaultAcct() {
    const banks = _bankOnly();
    return banks.find(a => a.is_default) || banks[0] || null;
}

function _renderShell() {
    const actives = _bankOnly();
    const def = _defaultAcct();

    const unassignedBar = (_unassigned > 0) ? `
        <div style="background:#3a2a12;border:1px solid #92600f;color:#fbbf24;border-radius:6px;padding:10px 12px;margin:0 0 14px;font-size:13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <span>⚠ 有 ${fmtNum(_unassigned)} 筆收支尚未指定帳戶</span>
            ${def ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.bulkAssign()">整批掛到預設帳戶（${esc(def.name)}）</button>`
                  : '<span style="color:#9ca3af;font-size:12px;">先新增一個帳戶才能整批掛上</span>'}
        </div>` : '';

    const reconOpts = actives.map((a, i) =>
        `<option value="${esc(a.id)}"${i === 0 ? ' selected' : ''}>${esc(a.name)}</option>`).join('');

    // 月底對帳預設 = 上個月
    const d = new Date(); d.setMonth(d.getMonth() - 1);
    const defMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

    _c.innerHTML = `
        <h2 style="margin:0 0 4px;color:#eee;">🏦 銀行帳戶</h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">公司每個錢包一張卡 — 收支明細掛上帳戶後，這裡的餘額就是各帳戶的即時水位。</p>

        ${unassignedBar}

        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;margin-bottom:20px;">
            ${_accounts.filter(a => !isShareholderAcct(a.acct_kind)).map(_card).join('')}
            <button class="crm-btn crm-btn-secondary" style="min-width:150px;min-height:120px;border-style:dashed;"
                    onclick="window._finBank.openAdd()">+ 新增帳戶</button>
        </div>

        <!-- 股東往來：跟銀行帳戶分開列，因為它**不是現金**而是公司欠股東的錢。
             混在上面那排的話，「公司有多少錢」這個問題會被答錯。 -->
        ${_shareholderSection()}

        <!-- 銀行貸款（階段四） -->
        <div id="finbank-loans-section" style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
            ${_loansSectionInner()}
        </div>

        <!-- 月底對帳（工作台：明細逐筆勾銷 → 最後核對餘額） -->
        <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
            <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">🔍 對帳系統</h3>
            <p style="color:#888;font-size:12px;margin:0 0 10px;">
                銀行的帳從這裡進系統：上傳對帳單 → 自動分類、自動配貸款期別 → 寫進收支明細。
                同一份重傳只會補新的，所以可以每個月固定丟一次。
                分類規則自己設，用久了幾乎不用手動改。</p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;">
                <button class="crm-btn crm-btn-primary" onclick="window._finBank.stmtOpen()">📄 上傳對帳單</button>
                <button class="crm-btn crm-btn-secondary" onclick="window._finBank.rulesOpen()">⚙️ 分類規則</button>
            </div>
            ${_draftsStrip()}
            ${actives.length === 0 ? '<div style="color:#888;font-size:13px;">先新增帳戶才能對帳。</div>' : `
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">帳戶</div>
                    <select id="finbank-recon-acct" class="crm-select">${reconOpts}</select></div>
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">月份</div>
                    <input id="finbank-recon-month" type="month" class="crm-input" value="${defMonth}" onchange="window._finBank.wbTargetChanged()"></div>
                <button class="crm-btn crm-btn-primary" onclick="window._finBank.wbOpen(this)">📋 開啟對帳工作台</button>
            </div>
            <div id="finbank-wb" style="margin-top:12px;"></div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:14px;padding-top:12px;border-top:1px solid #2a2a2a;">
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">核對：對帳單月底餘額</div>
                    <input id="finbank-recon-balance" type="number" class="crm-input" placeholder="照對帳單抄" style="width:150px;"></div>
                <button class="crm-btn crm-btn-secondary" onclick="window._finBank.reconcile(this)">核對餘額</button>
            </div>
            <div id="finbank-recon-result" style="margin-top:10px;font-size:13px;"></div>
            <div id="finbank-recon-history" style="margin-top:12px;"></div>`}
        </div>

        <!-- 對帳工作台共用 Modal（匯入/手動列/配對/補記/註記 動態換內容） -->
        <div id="finbank-wb-modal" class="crm-modal-overlay" style="display:none;">
            <div class="crm-modal" style="max-width:760px;">
                <div class="crm-modal-header">
                    <h3 id="finbank-wb-modal-title"></h3>
                    <button class="crm-detail-close" onclick="document.getElementById('finbank-wb-modal').style.display='none'">&#x2715;</button>
                </div>
                <div class="crm-modal-body" id="finbank-wb-modal-body"></div>
            </div>
        </div>

        <!-- 進階：帳務調整 -->
        <details style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:12px 16px;">
            <summary style="cursor:pointer;color:#9ca3af;font-size:13px;">進階：帳務調整（期初/業主投入提領/會計師調整）</summary>
            <div style="padding-top:12px;">
                <div id="finbank-adj-list">${_adjListHtml()}</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-top:12px;padding-top:12px;border-top:1px solid #2a2a2a;">
                    <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">日期</div>
                        <input id="finbank-adj-date" type="date" class="crm-input"></div>
                    <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">科目</div>
                        <select id="finbank-adj-acct" class="crm-select">${_adjAcctOptions()}</select></div>
                    <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">金額（可負）</div>
                        <input id="finbank-adj-amount" type="number" step="any" class="crm-input" style="width:130px;"></div>
                    <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">類型</div>
                        <select id="finbank-adj-type" class="crm-select">${ADJ_TYPES.map(t => `<option value="${t.v}">${esc(t.label)}</option>`).join('')}</select></div>
                    <div style="flex:1;min-width:160px;"><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">說明</div>
                        <input id="finbank-adj-desc" type="text" class="crm-input" style="width:100%;box-sizing:border-box;"></div>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.adjAdd(this)">+ 新增調整</button>
                </div>
            </div>
        </details>

        <!-- 新增/編輯帳戶 Modal -->
        <div id="finbank-modal" class="crm-modal-overlay" style="display:none;">
            <div class="crm-modal" style="max-width:440px;">
                <div class="crm-modal-header">
                    <h3 id="finbank-modal-title">新增帳戶</h3>
                    <button class="crm-detail-close" onclick="document.getElementById('finbank-modal').style.display='none'">&#x2715;</button>
                </div>
                <div class="crm-modal-body">
                    <div class="crm-form-grid">
                        <div class="crm-field"><label>名稱 <span class="crm-required">*</span></label>
                            <input id="finbank-f-name" type="text" class="crm-input" placeholder="例：玉山主帳戶"></div>
                        <div class="crm-field"><label>銀行</label>
                            <input id="finbank-f-bank_name" type="text" class="crm-input" placeholder="例：玉山銀行"></div>
                        <div class="crm-field"><label>帳號</label>
                            <input id="finbank-f-account_no" type="text" class="crm-input"></div>
                        <div class="crm-field"><label>種類</label>
                            <select id="finbank-f-acct_kind" class="crm-input">${ACCT_KIND_OPTIONS.map(k => `<option value="${k.v}">${esc(k.label)}</option>`).join('')}</select></div>
                        <div class="crm-field"><label>期初餘額</label>
                            <input id="finbank-f-opening_balance" type="number" step="any" class="crm-input" value="0"></div>
                        <div class="crm-field"><label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                            <input id="finbank-f-is_default" type="checkbox"> 設為預設帳戶</label></div>
                        <div class="crm-field crm-field-full"><label>備註</label>
                            <input id="finbank-f-note" type="text" class="crm-input"></div>
                    </div>
                    <div id="finbank-modal-error" class="crm-error" style="display:none;"></div>
                </div>
                <div class="crm-modal-footer">
                    <button class="crm-btn crm-btn-secondary" onclick="document.getElementById('finbank-modal').style.display='none'">取消</button>
                    <button id="finbank-btn-save" class="crm-btn crm-btn-primary" onclick="window._finBank.saveAcct(this)">儲存</button>
                </div>
            </div>
        </div>

        <!-- 新增/編輯貸款 Modal -->
        <div id="finbank-loan-modal" class="crm-modal-overlay" style="display:none;">
            <div class="crm-modal" style="max-width:520px;">
                <div class="crm-modal-header">
                    <h3 id="finbank-loan-title">新增貸款</h3>
                    <button class="crm-detail-close" onclick="document.getElementById('finbank-loan-modal').style.display='none'">&#x2715;</button>
                </div>
                <div class="crm-modal-body">
                    <div class="crm-form-grid">
                        <div class="crm-field"><label>名稱 <span class="crm-required">*</span></label>
                            <input id="finbank-lf-name" type="text" class="crm-input" placeholder="例：週轉金貸款"></div>
                        <div class="crm-field"><label>銀行</label>
                            <input id="finbank-lf-lender" type="text" class="crm-input" placeholder="例：玉山銀行"></div>
                        <div class="crm-field"><label>貸款金額 <span class="crm-required">*</span></label>
                            <input id="finbank-lf-principal" type="number" step="any" class="crm-input" placeholder="合約核貸總額"></div>
                        <div class="crm-field"><label>年利率 % <span class="crm-required">*</span></label>
                            <input id="finbank-lf-annual_rate" type="number" step="0.01" min="0" class="crm-input" placeholder="例：2.35"></div>
                        <div class="crm-field"><label>期數（月）<span class="crm-required">*</span></label>
                            <input id="finbank-lf-term_months" type="number" step="1" min="1" class="crm-input" placeholder="例：60"></div>
                        <div class="crm-field"><label>還款方式</label>
                            <select id="finbank-lf-method" class="crm-input">${LOAN_METHODS.map(m => `<option value="${m.v}">${esc(m.label)}</option>`).join('')}</select></div>
                        <div class="crm-field"><label>寬限期（月）</label>
                            <input id="finbank-lf-grace_months" type="number" step="1" min="0" class="crm-input" value="0">
                            <div style="color:#777;font-size:11px;margin-top:2px;">前幾個月只繳利息不還本金（銀行核貸常見），沒有就填 0</div></div>
                        <div class="crm-field"><label>起貸日 <span class="crm-required">*</span></label>
                            <input id="finbank-lf-start_date" type="date" class="crm-input"></div>
                        <div class="crm-field"><label>首次繳款日</label>
                            <input id="finbank-lf-first_payment_date" type="date" class="crm-input">
                            <div style="color:#777;font-size:11px;margin-top:2px;">留空＝起貸日下個月同一天</div></div>
                        <div class="crm-field"><label>扣款帳戶</label>
                            <select id="finbank-lf-bank_account_id" class="crm-input"></select></div>
                        <div class="crm-field crm-field-full"><label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                            <input id="finbank-lf-is_old" type="checkbox" onchange="window._finBank.loanToggleOld(this.checked)"> 這是已經在繳的舊貸款</label></div>
                        <div class="crm-field crm-field-full" id="finbank-lf-old-row" style="display:none;"><label>目前剩餘本金</label>
                            <input id="finbank-lf-opening_balance" type="number" step="any" class="crm-input" placeholder="現在還欠銀行多少">
                            <div style="color:#777;font-size:11px;margin-top:2px;">已在繳的貸款：這裡填現在還欠多少、「期數」填還剩幾期，之後的攤還表從這裡接著排</div></div>
                        <div class="crm-field crm-field-full"><label>備註</label>
                            <input id="finbank-lf-note" type="text" class="crm-input"></div>
                    </div>
                    <div id="finbank-lf-edit-hint" style="display:none;color:#777;font-size:11px;margin-top:8px;">修改後只會重算「還沒繳」的期別，已繳的紀錄不會動。</div>
                    <div id="finbank-loan-error" class="crm-error" style="display:none;"></div>
                </div>
                <div class="crm-modal-footer">
                    <button class="crm-btn crm-btn-secondary" onclick="document.getElementById('finbank-loan-modal').style.display='none'">取消</button>
                    <button id="finbank-loan-save" class="crm-btn crm-btn-primary" onclick="window._finBank.loanSave(this)">儲存</button>
                </div>
            </div>
        </div>

        <!-- 攤還表 Modal -->
        <div id="finbank-sched-modal" class="crm-modal-overlay" style="display:none;">
            <div class="crm-modal" style="max-width:760px;">
                <div class="crm-modal-header">
                    <h3 id="finbank-sched-title">📅 攤還表</h3>
                    <button class="crm-detail-close" onclick="document.getElementById('finbank-sched-modal').style.display='none'">&#x2715;</button>
                </div>
                <div class="crm-modal-body">
                    <div id="finbank-sched-paybar"></div>
                    <div id="finbank-sched-main"></div>
                </div>
                <div class="crm-modal-footer">
                    <button class="crm-btn crm-btn-secondary" onclick="document.getElementById('finbank-sched-modal').style.display='none'">關閉</button>
                </div>
            </div>
        </div>
    `;

    const reconSel = _c.querySelector('#finbank-recon-acct');
    if (reconSel) reconSel.addEventListener('change', () => {
        const resEl = _c.querySelector('#finbank-recon-result');
        if (resEl) resEl.innerHTML = '';
        _loadReconHistory(reconSel.value);
        _fb.wbTargetChanged();   // 工作台已開 → 跟著切帳戶
    });
    // 點 overlay 空白處關閉 modal（帳戶 / 貸款 / 攤還表 / 對帳工作台）
    for (const mid of ['finbank-modal', 'finbank-loan-modal', 'finbank-sched-modal', 'finbank-wb-modal']) {
        const m = _c.querySelector('#' + mid);
        if (m) m.addEventListener('click', (e) => { if (e.target === m) m.style.display = 'none'; });
    }
    _adjSorter.attach();   // 調整分錄表隨 shell 一起畫 → 這裡綁欄頭
}

// ── 帳戶 CRUD ───────────────────────────────────────────────

function _openModal(a, defaultKind) {
    _editingId = a ? a.id : null;
    const g = (id) => _c.querySelector('#finbank-f-' + id);
    _c.querySelector('#finbank-modal-title').textContent =
        a ? '編輯帳戶' : (defaultKind ? '新增股東往來帳戶' : '新增帳戶');
    g('name').value = a ? (a.name || '') : '';
    g('bank_name').value = a ? (a.bank_name || '') : '';
    g('account_no').value = a ? (a.account_no || '') : '';
    g('acct_kind').value = a ? (a.acct_kind || 'bank') : (defaultKind || 'bank');
    g('opening_balance').value = a ? (a.opening_balance ?? 0) : 0;
    g('is_default').checked = !!(a && a.is_default);
    g('note').value = a ? (a.note || '') : '';
    const err = _c.querySelector('#finbank-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _c.querySelector('#finbank-modal').style.display = 'flex';
}

// 從股東往來那一區按新增時預選「股東往來－借款」—— 少一次選錯的機會
_fb.openAdd = (defaultKind) => _openModal(null, defaultKind);

_fb.edit = (id) => {
    const a = _accounts.find(x => String(x.id) === String(id));
    if (a) _openModal(a);
};

_fb.saveAcct = async (btn) => {
    const g = (id) => _c.querySelector('#finbank-f-' + id);
    const err = _c.querySelector('#finbank-modal-error');
    const name = g('name').value.trim();
    if (!name) {
        err.textContent = '名稱為必填'; err.style.display = 'block';
        return;
    }
    const payload = {
        name,
        bank_name: g('bank_name').value.trim(),
        account_no: g('account_no').value.trim(),
        acct_kind: g('acct_kind').value,
        opening_balance: parseFloat(g('opening_balance').value) || 0,
        is_default: g('is_default').checked,
        note: g('note').value.trim(),
    };
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId != null) await finFetch('/bank-accounts/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
        else await finFetch('/bank-accounts', { method: 'POST', body: JSON.stringify(payload) });
        finToast(_editingId != null ? '帳戶已更新' : '帳戶已建立');
        _fb.reload();
    } catch (e) {
        err.textContent = e.message; err.style.display = 'block';
        btn.disabled = false; btn.textContent = '儲存';
    }
};

_fb.toggleActive = async (id) => {
    const a = _accounts.find(x => String(x.id) === String(id));
    if (!a) return;
    const disabling = a.active !== false;
    if (disabling && !confirm(`確定停用「${a.name}」？停用後不會出現在收支的帳戶下拉，歷史資料保留。`)) return;
    try {
        await finFetch('/bank-accounts/' + a.id, { method: 'PUT', body: JSON.stringify({ active: !disabling }) });
        finToast(disabling ? '帳戶已停用' : '帳戶已啟用');
        _fb.reload();
    } catch (e) { finToast(e.message, true); }
};

_fb.bulkAssign = async () => {
    const def = _defaultAcct();
    if (!def) { finToast('請先新增帳戶', true); return; }
    if (!confirm(`確定把 ${_unassigned} 筆尚未指定帳戶的收支，整批掛到「${def.name}」？（之後可在收支明細逐筆改）`)) return;
    try {
        const r = await finFetch('/cash-entries/bulk-assign-account', {
            method: 'POST',
            body: JSON.stringify({ bank_account_id: def.id, only_unassigned: true }),
        });
        finToast(`已更新 ${fmtNum(r.updated)} 筆收支`);
        _fb.reload();
    } catch (e) { finToast(e.message, true); }
};

// ── 銀行貸款（階段四） ──────────────────────────────────────

/** 依 id 找貸款（清單已載入 _loans） */
function _loanById(id) {
    return _loans.find(x => String(x.id) === String(id));
}

/** 啟用中帳戶下拉選項（貸款扣款帳戶 / 記繳款帳戶共用） */
function _loanAcctOptions(selectedId, emptyLabel) {
    const actives = _bankOnly();
    return `<option value="">${esc(emptyLabel)}</option>` + actives.map(a =>
        `<option value="${esc(a.id)}"${String(a.id) === String(selectedId) ? ' selected' : ''}>${esc(a.name)}</option>`).join('');
}

function _loanCard(l) {
    const paidOff = l.status === 'paid_off';
    const paid = l.paid_periods || 0;
    const total = l.total_periods || l.term_months || 0;
    const pct = total > 0 ? Math.min(100, Math.round(paid / total * 100)) : 0;
    const badge = paidOff
        ? '<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#14351f;color:#86efac;">已繳清</span>'
        : '<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#1e3a5f;color:#93c5fd;">繳款中</span>';
    let nextHtml = '';
    if (paidOff) {
        nextHtml = '<div style="color:#86efac;font-size:12px;margin-top:6px;">✓ 全部繳完了</div>';
    } else if (l.next_due) {
        const overdue = !!l.next_due.overdue;  // 後端即時推導，前端不重算
        nextHtml = `<div style="font-size:12px;margin-top:6px;color:${overdue ? '#fca5a5' : '#ccc'};">
            下期繳款 ${esc(String(l.next_due.due_date).substring(0, 10))} · $${fmtNum(l.next_due.total)}${overdue ? '<b>（已逾期）</b>' : ''}</div>`;
    }
    // 銀行名不重複寫進卡片 —— 分組標頭已經標明是哪一家
    const meta = [`年利率 ${esc(l.annual_rate)}%`, esc(LOAN_METHOD_LABEL[l.method] || l.method || '')]
        .filter(Boolean).join(' · ') + ((l.grace_months || 0) > 0 ? ` · 寬限 ${esc(l.grace_months)} 個月` : '');
    return `
    <div style="background:#222;border:1px solid #333;border-radius:8px;padding:14px 16px;min-width:250px;flex:0 1 300px;${paidOff ? 'opacity:.7;' : ''}">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span style="font-weight:600;color:#eee;">${esc(l.name)}</span>${badge}
        </div>
        <div style="color:#888;font-size:12px;margin-top:3px;">${meta}</div>
        <div style="color:#9ca3af;font-size:11px;margin-top:8px;">剩餘本金</div>
        <div style="font-size:22px;font-weight:700;color:${paidOff ? '#86efac' : '#fbbf24'};">$${fmtNum(l.outstanding)}</div>
        <div style="color:#9ca3af;font-size:11px;margin-top:6px;">已繳 ${fmtNum(paid)}/${fmtNum(total)} 期</div>
        <div style="background:#333;height:5px;border-radius:3px;margin-top:3px;overflow:hidden;">
            <div style="width:${pct}%;height:5px;background:${paidOff ? '#22c55e' : '#3b82f6'};"></div></div>
        ${nextHtml}
        <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanSchedule('${esc(l.id)}')">📅 攤還表</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanEdit('${esc(l.id)}')">✏️ 編輯</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanDel('${esc(l.id)}')">🗑 刪除</button>
        </div>
    </div>`;
}

/** 依銀行分組 → [{ lender, loans, outstanding, nextTotal, active, paidOff }]，
 *  未繳清餘額大的銀行排前面（同一家銀行常有多筆授信，合起來才看得出曝險）。 */
function _loanGroups() {
    const by = new Map();
    for (const l of _loans) {
        const key = (l.lender || '').trim() || '未指定銀行';
        if (!by.has(key)) by.set(key, []);
        by.get(key).push(l);
    }
    return [...by.entries()].map(([lender, loans]) => {
        const live = loans.filter(l => l.status !== 'paid_off');
        return {
            lender, loans,
            outstanding: loans.reduce((s, l) => s + (l.outstanding || 0), 0),
            // 下期應繳只加未繳清的（繳清的沒有 next_due，加了會誤導）
            nextTotal: live.reduce((s, l) => s + (l.next_due ? (l.next_due.total || 0) : 0), 0),
            overdue: live.some(l => l.next_due && l.next_due.overdue),
            active: live.length, paidOff: loans.length - live.length,
        };
    }).sort((a, b) => b.outstanding - a.outstanding);
}

/** 展開中的銀行（localStorage 記住；預設全展開 = 首次進來就看得到明細）。 */
function _loanOpenSet(groups) {
    try {
        const raw = localStorage.getItem(LOAN_GROUP_KEY);
        if (raw) return new Set(JSON.parse(raw));
    } catch (e) { /* 壞掉的舊值當沒存過 */ }
    return new Set(groups.map(g => g.lender));   // 沒存過＝全展開
}

function _loanTotalsBar(groups) {
    const outstanding = groups.reduce((s, g) => s + g.outstanding, 0);
    const nextTotal = groups.reduce((s, g) => s + g.nextTotal, 0);
    const active = groups.reduce((s, g) => s + g.active, 0);
    const overdue = groups.some(g => g.overdue);
    const money = (n, color) => `<span style="color:${color};">$${fmtNum(n)}</span>`;
    return `
    <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px;">
        ${metricCard('貸款總餘額', money(outstanding, '#fbbf24'))}
        ${metricCard('下期應繳合計', money(nextTotal, overdue ? '#fca5a5' : '#eee'))}
        ${metricCard('筆數', `${fmtNum(active)}`,
                     `${fmtNum(active)} 筆繳款中 / ${fmtNum(groups.length)} 家銀行`)}
    </div>`;
}

function _loanGroupBlock(g, open) {
    const arrow = open ? '▾' : '▸';
    const sub = [`${fmtNum(g.active)} 筆繳款中`, g.paidOff ? `${fmtNum(g.paidOff)} 筆已繳清` : '']
        .filter(Boolean).join(' · ');
    return `
    <div style="border:1px solid #333;border-radius:8px;margin-bottom:10px;overflow:hidden;">
        <div onclick="window._finBank.loanGroupToggle('${esc(encodeURIComponent(g.lender))}')"
             style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#1b1b1b;
                    padding:10px 14px;cursor:pointer;user-select:none;">
            <span style="color:#9ca3af;width:12px;">${arrow}</span>
            <span style="font-weight:600;color:#eee;">${esc(g.lender)}</span>
            <span style="color:#888;font-size:12px;">${esc(sub)}</span>
            <span style="margin-left:auto;display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;">
                <span style="color:#9ca3af;font-size:11px;">剩餘本金
                    <b style="color:#fbbf24;font-size:16px;margin-left:4px;">$${fmtNum(g.outstanding)}</b></span>
                ${g.nextTotal ? `<span style="color:#9ca3af;font-size:11px;">下期應繳
                    <b style="color:${g.overdue ? '#fca5a5' : '#ddd'};font-size:16px;margin-left:4px;">$${fmtNum(g.nextTotal)}</b></span>` : ''}
            </span>
        </div>
        ${open ? `<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;padding:12px 14px;background:#202020;">
            ${g.loans.map(_loanCard).join('')}</div>` : ''}
    </div>`;
}

/** 貸款區塊內容（記繳款/取消後只重畫這塊，modal 不動） */
function _loansSectionInner() {
    // 這裡刻意沒有「上傳對帳單」鈕：入口統一在上面的「對帳系統」——
    // 同一個動作兩個入口，使用者永遠在猜哪個才是對的（2026-08-20 搬走）。
    const head = `
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
            <h3 style="color:#eee;margin:0;font-size:14px;">🏦 銀行貸款</h3>
            <span style="display:flex;gap:6px;flex-wrap:wrap;">
                ${_loans.length ? '<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanOpenAdd()">+ 新增貸款</button>' : ''}
            </span>
        </div>
        <p style="color:#888;font-size:12px;margin:4px 0 12px;">依銀行分組 — 點銀行列展開該行每筆授信。攤還表照合約排好，每期一鍵記繳款、自動寫進收支明細。</p>`;
    if (_loanErr) {
        return head + `<div style="color:#fca5a5;font-size:13px;">貸款載入失敗：${esc(_loanErr)}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;" onclick="window._finBank.reload()">🔄 重試</button></div>`;
    }
    if (!_loans.length) {
        return head + `
        <div style="color:#888;font-size:13px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
            <span>尚未建立貸款 — 把銀行貸款建進來，三表會自動算利息費用與剩餘本金，並在到期前提醒你繳款</span>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanOpenAdd()">+ 新增貸款</button>
        </div>`;
    }
    const groups = _loanGroups();
    const open = _loanOpenSet(groups);
    return head + _loanTotalsBar(groups)
        + groups.map(g => _loanGroupBlock(g, open.has(g.lender))).join('');
}

// ── 貸款 CRUD ──

function _openLoanModal(l) {
    _editingLoanId = l ? l.id : null;
    const v = l || {};
    const g = (id) => _c.querySelector('#finbank-lf-' + id);
    _c.querySelector('#finbank-loan-title').textContent = l ? '編輯貸款' : '新增貸款';
    g('name').value = v.name || '';
    g('lender').value = v.lender || '';
    g('principal').value = v.principal ?? '';
    g('annual_rate').value = v.annual_rate ?? '';
    g('term_months').value = v.term_months ?? '';
    g('method').value = v.method || 'annuity';
    g('grace_months').value = v.grace_months ?? 0;
    g('start_date').value = v.start_date ? String(v.start_date).substring(0, 10) : '';
    g('first_payment_date').value = v.first_payment_date ? String(v.first_payment_date).substring(0, 10) : '';
    g('bank_account_id').innerHTML = _loanAcctOptions(v.bank_account_id || '', '—（不指定）—');
    const isOld = (v.opening_balance || 0) > 0;
    g('is_old').checked = isOld;
    g('opening_balance').value = isOld ? v.opening_balance : '';
    _fb.loanToggleOld(isOld);
    g('note').value = v.note || '';
    _c.querySelector('#finbank-lf-edit-hint').style.display = l ? 'block' : 'none';
    const err = _c.querySelector('#finbank-loan-error');
    err.textContent = ''; err.style.display = 'none';
    _c.querySelector('#finbank-loan-modal').style.display = 'flex';
}

_fb.loanOpenAdd = () => _openLoanModal(null);

_fb.loanEdit = (id) => {
    const l = _loanById(id);
    if (l) _openLoanModal(l);
};

_fb.loanToggleOld = (on) => {
    const row = _c.querySelector('#finbank-lf-old-row');
    if (row) row.style.display = on ? '' : 'none';
};

_fb.loanSave = async (btn) => {
    const g = (id) => _c.querySelector('#finbank-lf-' + id);
    const err = _c.querySelector('#finbank-loan-error');
    const showErr = (m) => { err.textContent = m; err.style.display = 'block'; };
    const name = g('name').value.trim();
    const principal = parseFloat(g('principal').value);
    const annual_rate = parseFloat(g('annual_rate').value);
    const term_months = parseInt(g('term_months').value, 10);
    const start_date = g('start_date').value;
    const isOld = g('is_old').checked;
    const opening_balance = isOld ? (parseFloat(g('opening_balance').value) || 0) : 0;
    if (!name) return showErr('名稱為必填');
    if (!(principal > 0)) return showErr('貸款金額要大於 0');
    if (isNaN(annual_rate) || annual_rate < 0) return showErr('請填年利率（%，可小數）');
    if (!(term_months >= 1)) return showErr('期數（月）至少 1 期');
    if (!start_date) return showErr('請選起貸日');
    if (isOld && !(opening_balance > 0)) return showErr('勾了「舊貸款」就要填目前剩餘本金');
    const payload = {
        name,
        lender: g('lender').value.trim(),
        principal, annual_rate, term_months,
        method: g('method').value,
        grace_months: parseInt(g('grace_months').value, 10) || 0,
        start_date,
        opening_balance,
        note: g('note').value.trim(),
    };
    const fpd = g('first_payment_date').value;
    if (fpd) payload.first_payment_date = fpd;
    const acct = g('bank_account_id').value;
    if (acct) payload.bank_account_id = acct;
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingLoanId != null) await finFetch('/loans/' + _editingLoanId, { method: 'PUT', body: JSON.stringify(payload) });
        else await finFetch('/loans', { method: 'POST', body: JSON.stringify(payload) });
        finToast(_editingLoanId != null ? '貸款已更新（未繳期別已重算）' : '貸款已建立');
        _fb.reload();
    } catch (e) {
        showErr(e.message);
        btn.disabled = false; btn.textContent = '儲存';
    }
};

/** 展開/收合某家銀行 —— 只重畫貸款區塊，攤還表 modal 與其他區塊不動。 */
_fb.loanGroupToggle = (encoded) => {
    const lender = decodeURIComponent(encoded);
    const open = _loanOpenSet(_loanGroups());
    if (open.has(lender)) open.delete(lender);
    else open.add(lender);
    try {
        localStorage.setItem(LOAN_GROUP_KEY, JSON.stringify([...open]));
    } catch (e) { /* 無痕模式寫不了 —— 這輪照樣展開，只是不記住 */ }
    const sec = _c.querySelector('#finbank-loans-section');
    if (sec) sec.innerHTML = _loansSectionInner();
};

_fb.loanDel = async (id) => {
    const l = _loanById(id);
    if (!l) return;
    if (!confirm(`確定刪除貸款「${l.name}」？未繳的攤還表會一併刪除。`)) return;
    try {
        await finFetch('/loans/' + l.id, { method: 'DELETE' });
        finToast('貸款已刪除');
        _fb.reload();
    } catch (e) { finToast(e.message, true); }  // 409：有已繳期別 → 直接顯示後端 detail
};

// ── 攤還表 + 記繳款 ──

_fb.loanSchedule = async (id) => {
    const l = _loanById(id);
    if (!l) return;
    _schedLoanId = l.id;
    _c.querySelector('#finbank-sched-title').textContent = `📅 攤還表 — ${l.name}`;
    // 繳款設定列只在開啟時畫一次 — 之後記繳款刷新表格不會蓋掉使用者選的日期/帳戶
    _c.querySelector('#finbank-sched-paybar').innerHTML = `
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;padding:8px 10px;background:#1c2431;border:1px solid #2c3a52;border-radius:6px;margin-bottom:12px;">
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">繳款日</div>
                <input id="finbank-sched-paydate" type="date" class="crm-input" value="${todayStr()}"></div>
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">扣款帳戶</div>
                <select id="finbank-sched-payacct" class="crm-select">${_loanAcctOptions(l.bank_account_id, '—（用貸款設定的帳戶）—')}</select></div>
            <span style="color:#777;font-size:11px;padding-bottom:6px;">按各期「記繳款」會用這兩個設定，並自動寫進收支明細</span>
        </div>`;
    const main = _c.querySelector('#finbank-sched-main');
    main.innerHTML = '<div style="color:#888;padding:20px;">載入攤還表…</div>';
    _c.querySelector('#finbank-sched-modal').style.display = 'flex';
    try {
        const r = await finFetch(`/loans/${_schedLoanId}/schedule`);
        _renderSchedMain(r.items || []);
    } catch (e) {
        main.innerHTML = `<div style="color:#fca5a5;padding:20px;">攤還表載入失敗：${esc(e.message)}</div>`;
    }
};

function _renderSchedMain(items) {
    const main = _c.querySelector('#finbank-sched-main');
    if (!main) return;
    _schedItems = items;
    const loan = _loanById(_schedLoanId);
    const totalInterest = items.reduce((s, r) => s + (r.interest_due || 0), 0);
    const paidPrincipal = items.filter(r => r.status === 'paid')
        .reduce((s, r) => s + (r.principal_due || 0), 0);
    const row = (r) => {
        let statusHtml, opHtml;
        if (r.status === 'paid') {
            statusHtml = `<span style="color:#86efac;">✓ 已繳 ${esc(r.paid_at ? String(r.paid_at).substring(0, 10) : '')}</span>`;
            opHtml = `<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanUnpay(${r.period_no})">取消</button>`;
        } else {
            statusHtml = r.overdue ? '<span style="color:#fca5a5;">已逾期</span>' : '<span style="color:#888;">未到期</span>';
            opHtml = `<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finBank.loanPay(${r.period_no})">記繳款</button>`;
        }
        return `<tr style="border-top:1px solid #2a2a2a;${r.overdue ? 'background:#311a1a;' : ''}">
            <td style="padding:5px 8px;">${esc(r.period_no)}</td>
            <td style="padding:5px 8px;">${esc(String(r.due_date || '').substring(0, 10))}</td>
            <td style="padding:5px 8px;text-align:right;">$${fmtNum(r.principal_due)}</td>
            <td style="padding:5px 8px;text-align:right;">$${fmtNum(r.interest_due)}</td>
            <td style="padding:5px 8px;text-align:right;font-weight:600;">$${fmtNum(r.total)}</td>
            <td style="padding:5px 8px;white-space:nowrap;">${statusHtml}</td>
            <td style="padding:5px 8px;">${opHtml}</td>
        </tr>`;
    };
    main.innerHTML = `
        <div style="color:#ccc;font-size:12px;margin-bottom:8px;display:flex;gap:16px;flex-wrap:wrap;">
            <span>總利息 <b style="color:#fbbf24;">$${fmtNum(totalInterest)}</b></span>
            <span>已繳本金 <b style="color:#86efac;">$${fmtNum(paidPrincipal)}</b></span>
            <span>剩餘本金 <b style="color:#fbbf24;">$${fmtNum(loan ? loan.outstanding : 0)}</b></span>
        </div>
        <div style="max-height:420px;overflow-y:auto;border:1px solid #2a2a2a;border-radius:6px;">
            <table style="border-collapse:collapse;font-size:12px;color:#ccc;width:100%;">
                <thead><tr style="color:#888;text-align:left;position:sticky;top:0;background:#202020;">
                    ${sortableTh('period', '期別', 'style="padding:6px 8px;"')}
                    ${sortableTh('due', '繳款日', 'style="padding:6px 8px;"')}
                    ${sortableTh('principal', '本金', 'style="padding:6px 8px;text-align:right;"')}
                    ${sortableTh('interest', '利息', 'style="padding:6px 8px;text-align:right;"')}
                    ${sortableTh('total', '合計', 'style="padding:6px 8px;text-align:right;"')}
                    ${sortableTh('status', '狀態', 'style="padding:6px 8px;"')}
                    <th style="padding:6px 8px;"></th>
                </tr></thead>
                <tbody>${_schedSorter.sorted(items).map(row).join('')}</tbody>
            </table>
        </div>`;
    _schedSorter.attach();
}

/** 記繳款/取消後：貸款卡片 + 攤還表一起刷新（modal 開著、繳款設定列不重畫） */
async function _reloadLoansAndSched() {
    const [loansRes, schedRes] = await Promise.all([
        finFetch('/loans'),
        finFetch(`/loans/${_schedLoanId}/schedule`),
    ]);
    _loans = loansRes.items || [];
    _loanErr = null;
    const sec = _c.querySelector('#finbank-loans-section');
    if (sec) sec.innerHTML = _loansSectionInner();
    _renderSchedMain(schedRes.items || []);
}

_fb.loanPay = async (periodNo) => {
    const body = {};
    const d = _c.querySelector('#finbank-sched-paydate')?.value;
    if (d) body.paid_date = d;
    const acct = _c.querySelector('#finbank-sched-payacct')?.value;
    if (acct) body.bank_account_id = acct;
    try {
        await finFetch(`/loans/${_schedLoanId}/payments/${periodNo}/pay`, { method: 'POST', body: JSON.stringify(body) });
        finToast(`第 ${periodNo} 期已記繳款（已同步寫進收支明細）`);
        await _reloadLoansAndSched();
    } catch (e) { finToast(e.message, true); }  // 409：該月已鎖帳 → 直接顯示後端 detail
};

_fb.loanUnpay = async (periodNo) => {
    if (!confirm(`確定取消第 ${periodNo} 期的繳款紀錄？會同時刪除關聯的收支明細。`)) return;
    try {
        await finFetch(`/loans/${_schedLoanId}/payments/${periodNo}/unpay`, { method: 'POST' });
        finToast(`第 ${periodNo} 期繳款已取消`);
        await _reloadLoansAndSched();
    } catch (e) { finToast(e.message, true); }
};

// ── 對帳 ────────────────────────────────────────────────────

_fb.reconcile = async (btn) => {
    const acct = _c.querySelector('#finbank-recon-acct')?.value;
    const month = _c.querySelector('#finbank-recon-month')?.value;
    const balRaw = _c.querySelector('#finbank-recon-balance')?.value;
    const resEl = _c.querySelector('#finbank-recon-result');
    if (!acct || !month || balRaw === '' || balRaw == null) {
        finToast('請選帳戶、月份並填對帳單月底餘額', true);
        return;
    }
    btn.disabled = true; btn.textContent = '對帳中...';
    try {
        const r = await finFetch('/reconciliations', {
            method: 'POST',
            body: JSON.stringify({ bank_account_id: acct, month, statement_balance: parseFloat(balRaw) || 0 }),
        });
        const diff = r.diff || 0;
        if (diff === 0) {
            resEl.innerHTML = `<span style="color:#86efac;">對平了 ✓（系統餘額 $${fmtNum(r.system_balance)} = 對帳單餘額）</span>`;
        } else {
            resEl.innerHTML = `<span style="color:#fca5a5;">差 $${fmtNum(Math.abs(diff))} —
                可能有漏記或銀行手續費/利息未入帳，去收支明細補一筆。
                （系統 $${fmtNum(r.system_balance)} vs 對帳單 $${fmtNum(parseFloat(balRaw) || 0)}）</span>`;
        }
        _loadReconHistory(acct);
    } catch (e) {
        resEl.innerHTML = `<span style="color:#fca5a5;">對帳失敗：${esc(e.message)}</span>`;
    } finally {
        btn.disabled = false; btn.textContent = '核對餘額';
    }
};

async function _loadReconHistory(acctId) {
    const el = _c.querySelector('#finbank-recon-history');
    if (!el) return;
    el.innerHTML = '<div style="color:#666;font-size:12px;">載入對帳紀錄…</div>';
    try {
        _reconItems = (await finFetch('/reconciliations?bank_account_id=' + encodeURIComponent(acctId))).items || [];
    } catch (e) {
        el.innerHTML = `<div style="color:#fca5a5;font-size:12px;">對帳紀錄載入失敗：${esc(e.message)}</div>`;
        return;
    }
    _renderReconHistory();
}

function _renderReconHistory() {
    const el = _c && _c.querySelector('#finbank-recon-history');
    if (!el) return;
    const items = _reconItems || [];
    if (!items.length) { el.innerHTML = '<div style="color:#666;font-size:12px;">此帳戶尚無對帳紀錄</div>'; return; }
    const row = (r) => {
        const diff = r.diff || 0;
        const ok = diff === 0 || r.status === 'balanced';
        return `<tr style="border-top:1px solid #2a2a2a;">
            <td style="padding:5px 10px;">${esc(r.month)}</td>
            <td style="padding:5px 10px;text-align:right;">$${fmtNum(r.statement_balance)}</td>
            <td style="padding:5px 10px;text-align:right;">$${fmtNum(r.system_balance)}</td>
            <td style="padding:5px 10px;text-align:right;color:${ok ? '#86efac' : '#fca5a5'};">${diff === 0 ? '—' : '$' + fmtNum(diff)}</td>
            <td style="padding:5px 10px;color:${ok ? '#86efac' : '#fca5a5'};">${ok ? '✓ 平' : '✗ 不平'}</td>
        </tr>`;
    };
    el.innerHTML = `
        <table style="border-collapse:collapse;font-size:12px;color:#ccc;min-width:420px;">
            <thead><tr style="color:#888;text-align:left;">
                ${sortableTh('month', '月份', 'style="padding:5px 10px;"')}
                ${sortableTh('stmt', '對帳單餘額', 'style="padding:5px 10px;text-align:right;"')}
                ${sortableTh('sys', '系統餘額', 'style="padding:5px 10px;text-align:right;"')}
                ${sortableTh('diff', '差額', 'style="padding:5px 10px;text-align:right;"')}
                ${sortableTh('status', '狀態', 'style="padding:5px 10px;"')}
            </tr></thead>
            <tbody>${_reconSorter.sorted(items).map(row).join('')}</tbody>
        </table>`;
    _reconSorter.attach();
}

// ── 對帳工作台（對帳單明細逐筆勾銷）──────────────────────────

const _WB_CHIP = {
    matched: '<span style="font-size:10px;padding:1px 7px;border-radius:8px;background:#14351c;color:#86efac;white-space:nowrap;">✓ 已配對</span>',
    noted: '<span style="font-size:10px;padding:1px 7px;border-radius:8px;background:#3a2a12;color:#fbbf24;white-space:nowrap;">📝 已註記</span>',
    unmatched: '<span style="font-size:10px;padding:1px 7px;border-radius:8px;background:#3a1215;color:#fca5a5;white-space:nowrap;">未配對</span>',
};

/** 有號金額上色：正=綠(存入)、負=紅(支出) */
function _wbAmt(v) {
    const n = v || 0;
    return `<span style="color:${n >= 0 ? '#86efac' : '#fca5a5'};">${n > 0 ? '+' : (n < 0 ? '−' : '')}$${fmtNum(Math.abs(n))}</span>`;
}

/** 這組視窗共用同一個外框。width 給欄位多的內容用（預設 760 太窄，
 *  對帳單預覽有 9 欄含兩個下拉，全部擠在一起，owner 2026-08-21 反應）。 */
function _wbModal(title, bodyHtml, { width = 760 } = {}) {
    const overlay = document.getElementById('finbank-wb-modal');
    const box = overlay.querySelector('.crm-modal');
    // 寬版也要吃得下小螢幕 —— min() 讓它最多佔畫面 95%
    if (box) box.style.maxWidth = `min(${width}px, 95vw)`;
    overlay.querySelector('#finbank-wb-modal-title').textContent = title;
    overlay.querySelector('#finbank-wb-modal-body').innerHTML = bodyHtml;
    overlay.style.display = 'flex';
}

function _wbCloseModal() {
    const o = document.getElementById('finbank-wb-modal');
    if (o) o.style.display = 'none';
}
// 對帳單匯入的 modal 按鈕走 onclick="window._finBank.…" —— 要真的掛上去，
// 不然按了完全沒反應（inline onclick 看不到模組作用域裡的函式）。
_fb.wbCloseModal = _wbCloseModal;

/** 工作台變更請求共用骨架：打 API →（可選 toast）→（可選關 modal）→ 整台重載。
 *  失敗 toast 錯誤訊息（409 月結鎖帳等直接顯示後端 detail）。 */
async function _wbApi(path, opts, { btn, okMsg, close } = {}) {
    if (btn) btn.disabled = true;
    try {
        const r = await finFetch(path, opts);
        if (okMsg) finToast(typeof okMsg === 'function' ? okMsg(r) : okMsg);
        if (close) _wbCloseModal();
        await _fb.wbReload();
        return r;
    } catch (e) {
        finToast(e.message, true);
    } finally {
        if (btn) btn.disabled = false;
    }
}

const _wbLine = (id) => (_wb.data.lines || []).find(l => l.id === id);

/** modal 頂部「現在在操作哪一列」橫幅 */
function _wbBanner(ln, extra = '') {
    return `<div style="color:#ccc;font-size:13px;margin-bottom:10px;">${esc(ln.line_date || '—')}　${esc(ln.description || '')}　${_wbAmt(ln.amount)}${extra}</div>`;
}

/** 收支列共用四格（日期/摘要/類別/金額）— 工作台右表與配對候選表共用 */
function _wbEntryCells(e) {
    return `<td style="padding:4px 8px;white-space:nowrap;">${esc(e.entry_date || '—')}</td>
        <td style="padding:4px 8px;">${esc(e.summary || '')}</td>
        <td style="padding:4px 8px;color:#9ca3af;">${esc(e.category || '')}</td>
        <td style="padding:4px 8px;text-align:right;white-space:nowrap;">${_wbAmt(e.amount)}</td>`;
}

/** 可捲動表格容器。data-wb-scroll 給 _wbRender 記位置用（見下）。 */
function _wbTable(inner, maxH = 420) {
    return `<div data-wb-scroll style="max-height:${maxH}px;overflow:auto;border:1px solid #2a2a2a;border-radius:6px;">
        <table style="border-collapse:collapse;font-size:12px;color:#ccc;width:100%;">${inner}</table></div>`;
}

/** 月份切換（帳戶下拉的歷史重載走 _renderShell 的既有 listener）：工作台已開就跟著重載 */
_fb.wbTargetChanged = () => { if (_wb) _fb.wbOpen(); };

_fb.wbOpen = async (btn) => {
    const acct = _c.querySelector('#finbank-recon-acct')?.value;
    const month = _c.querySelector('#finbank-recon-month')?.value;
    if (!acct || !month) { finToast('請先選帳戶與月份', true); return; }
    if (btn) { btn.disabled = true; btn.textContent = '載入中...'; }
    try {
        const data = await finFetch(`/reconciliations/workbench?bank_account_id=${encodeURIComponent(acct)}&month=${encodeURIComponent(month)}`);
        _wb = { acct, month, data };
        _wbRender();
    } catch (e) {
        finToast(e.message, true);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '📋 開啟對帳工作台'; }
    }
};

_fb.wbReload = () => _fb.wbOpen();

function _wbLineRow(l) {
    let acts;
    if (l.status === 'matched') {
        const e = (_wb.data.entries || []).find(en => en.id === l.matched_entry_id);
        acts = `<button class="crm-btn crm-btn-secondary crm-btn-sm" title="配對到：${esc(e ? e.summary : '（其他月份的收支）')}" onclick="window._finBank.wbUnmatch('${l.id}')">取消配對</button>`;
    } else {
        acts = `<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.wbMatchOpen('${l.id}')">配對</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.wbCreateOpen('${l.id}')">補記入帳</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.wbNoteOpen('${l.id}')">${l.note ? '改註記' : '註記'}</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" title="刪除這列（只刪對帳單明細，不動帳）" onclick="window._finBank.wbDelLine('${l.id}')">✕</button>`;
    }
    return `<tr style="border-top:1px solid #2a2a2a;">
        <td style="padding:4px 8px;white-space:nowrap;">${esc(l.line_date || '—')}</td>
        <td style="padding:4px 8px;"${l.note ? ` title="註記：${esc(l.note)}"` : ''}>${esc(l.description || '')}</td>
        <td style="padding:4px 8px;text-align:right;white-space:nowrap;">${_wbAmt(l.amount)}</td>
        <td style="padding:4px 8px;">${_WB_CHIP[l.status] || ''}</td>
        <td style="padding:4px 8px;white-space:nowrap;">${acts}</td>
    </tr>`;
}

function _wbEntryRow(e) {
    return `<tr style="border-top:1px solid #2a2a2a;${e.matched ? 'opacity:.5;' : ''}">
        ${_wbEntryCells(e)}
        <td style="padding:4px 8px;color:#86efac;">${e.matched ? '✓' : ''}</td>
    </tr>`;
}

function _wbRender() {
    const el = _c.querySelector('#finbank-wb');
    if (!el) return;
    if (!_wb) { el.innerHTML = ''; return; }
    // 🔴 重畫前記住捲軸位置（owner 2026-08-21：「編輯後不要跳到最上面」）。
    // 每配對／註記／補記一列就整塊重畫（那些動作真的改了伺服器狀態，不能像
    // 匯入預覽那樣只換一列），對帳到第 30 列時每按一次就被彈回第 1 列。
    const keep = [...el.querySelectorAll('[data-wb-scroll]')].map(x => x.scrollTop);
    const { data, month } = _wb;
    const s = data.summary || {};
    const lines = data.lines || [];
    const entries = data.entries || [];
    const bankMiss = s.lines_bank_only || 0;   // 桶規則由後端 workbench_summary 單一定義
    const lineHead = `<thead><tr style="color:#888;text-align:left;">
        ${sortableTh('date', '日期', 'style="padding:4px 8px;"')}${sortableTh('desc', '摘要', 'style="padding:4px 8px;"')}
        ${sortableTh('amount', '金額', 'style="padding:4px 8px;text-align:right;"')}${sortableTh('status', '狀態', 'style="padding:4px 8px;"')}<th style="padding:4px 8px;"></th></tr></thead>`;
    const entryHead = `<thead><tr style="color:#888;text-align:left;">
        ${sortableTh('date', '日期', 'style="padding:4px 8px;"')}${sortableTh('summary', '摘要', 'style="padding:4px 8px;"')}${sortableTh('category', '類別', 'style="padding:4px 8px;"')}
        ${sortableTh('amount', '金額', 'style="padding:4px 8px;text-align:right;"')}${sortableTh('matched', '勾銷', 'style="padding:4px 8px;"')}</tr></thead>`;
    el.innerHTML = `
        <div style="border:1px solid #2e2e2e;border-radius:8px;padding:12px;background:#1c1c1c;">
            <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:12px;color:#ccc;margin-bottom:10px;">
                <span>已配對 <b style="color:#86efac;">${s.lines_matched || 0}</b> / ${s.lines_total || 0} 筆</span>
                <span>銀行有・系統沒有 <b style="color:${bankMiss ? '#fca5a5' : '#86efac'};">${bankMiss}</b> 筆
                    ${s.lines_noted ? `（含已註記 ${s.lines_noted}）` : ''}（${_wbAmt(s.lines_unmatched_sum)}）</span>
                <span>系統有・銀行沒有 <b style="color:${s.entries_unmatched ? '#fbbf24' : '#86efac'};">${s.entries_unmatched || 0}</b> 筆（${_wbAmt(s.entries_unmatched_sum)}）</span>
                <span style="color:#888;">系統月底餘額 $${fmtNum(data.system_balance)}</span>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.wbImportOpen()">📥 匯入對帳單明細</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.wbAddOpen()">＋ 手動新增一列</button>
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finBank.wbAutoMatch(this)">⚡ 自動配對</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.wbReload()">🔄 重新整理</button>
            </div>
            <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start;">
                <div id="finbank-wb-lines" style="flex:1 1 460px;min-width:380px;">
                    <div style="color:#9ca3af;font-size:12px;margin-bottom:4px;">🏦 銀行對帳單明細（${esc(month)}）</div>
                    ${lines.length ? _wbTable(`${lineHead}<tbody>${_wbLinesSorter.sorted(lines).map(_wbLineRow).join('')}</tbody>`)
                    : '<div style="color:#666;font-size:12px;border:1px dashed #333;border-radius:6px;padding:14px;">還沒有明細 — 從網銀/存摺把這個月的交易「📥 匯入」進來，或「＋ 手動新增」。</div>'}
                </div>
                <div id="finbank-wb-entries" style="flex:1 1 400px;min-width:360px;">
                    <div style="color:#9ca3af;font-size:12px;margin-bottom:4px;">📒 系統收支明細（${esc(month)}，掛此帳戶）</div>
                    ${entries.length ? _wbTable(`${entryHead}<tbody>${_wbEntriesSorter.sorted(entries).map(_wbEntryRow).join('')}</tbody>`)
                    : '<div style="color:#666;font-size:12px;border:1px dashed #333;border-radius:6px;padding:14px;">這個月此帳戶沒有掛帳的收支明細。</div>'}
                </div>
            </div>
        </div>`;
    _wbLinesSorter.attach();
    _wbEntriesSorter.attach();
    // 捲軸放回去（依序對回同一個容器；表格結構固定，兩個容器順序不會變）
    el.querySelectorAll('[data-wb-scroll]').forEach((x, i) => {
        if (keep[i]) x.scrollTop = keep[i];
    });
}

_fb.wbAutoMatch = (btn) => _wbApi('/statement-lines/auto-match', {
    method: 'POST', body: JSON.stringify({ bank_account_id: _wb.acct, month: _wb.month }),
}, { btn, okMsg: r => r.matched ? `自動配對成功 ${r.matched} 筆` : '沒有可自動配對的（金額相同且日期相近才會自動配）' });

_fb.wbUnmatch = (lineId) => _wbApi(`/statement-lines/${lineId}/unmatch`, { method: 'POST' });

_fb.wbDelLine = (lineId) => {
    if (!confirm('刪除這列對帳單明細？（只刪工作底稿，收支明細不動）')) return;
    _wbApi(`/statement-lines/${lineId}`, { method: 'DELETE' });
};

// ── 工作台：手動新增一列 ─────────────────────────────────────

_fb.wbAddOpen = () => {
    _wbModal('手動新增對帳單明細', `
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">交易日</div>
                <input id="finbank-wb-add-date" type="date" class="crm-input" value="${_wb.month}-01"></div>
            <div style="flex:1;min-width:160px;"><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">摘要</div>
                <input id="finbank-wb-add-desc" type="text" class="crm-input" style="width:100%;box-sizing:border-box;" placeholder="照對帳單抄"></div>
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">方向</div>
                <select id="finbank-wb-add-dir" class="crm-select"><option value="out">支出（提出）</option><option value="in">存入</option></select></div>
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">金額</div>
                <input id="finbank-wb-add-amt" type="number" min="1" class="crm-input" style="width:120px;"></div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px;">
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.wbAddSave(this)">新增</button>
        </div>`);
};

_fb.wbAddSave = (btn) => {
    const body = document.getElementById('finbank-wb-modal-body');
    const amt = Math.abs(parseInt(body.querySelector('#finbank-wb-add-amt')?.value, 10) || 0);
    if (!amt) { finToast('請填金額', true); return; }
    const dir = body.querySelector('#finbank-wb-add-dir')?.value;
    return _wbApi('/statement-lines', {
        method: 'POST',
        body: JSON.stringify({
            bank_account_id: _wb.acct, month: _wb.month,
            lines: [{
                line_date: body.querySelector('#finbank-wb-add-date')?.value || null,
                description: body.querySelector('#finbank-wb-add-desc')?.value || '',
                amount: dir === 'in' ? amt : -amt,
            }],
        }),
    }, { btn, close: true });
};

// ── 工作台：匯入對帳單明細 ──────────────────────────────────
//
// 解析走**後端**的 core/bank_statement.py（POST /bank-statement/preview）。
// 這裡本來有一整套前端解析器（_wbParsePaste/_wbParseDate/_wbParseAmt/_wbGuessRoles，
// 約 70 行），連同「請人逐欄指定角色」的步驟一起收掉了 —— 那份的註解自己寫著
// 「若日後有第二個消費者（如後端匯入路徑）再搬去鎖黃金測試」，而後端匯入路徑
// 就是第二個消費者。留兩份的代價是：民國年與會計括號的規則只存在其中一邊，
// 兩支對同一份對帳單會得到不同答案，而且錯的那支永遠不會被測到。
//
// 換過來之後不必再問「哪一欄是支出」：金額的正負由**餘額鏈**推（本列餘額 −
// 上列餘額），再跟銀行自己印的總計對帳。人要確認的是「這些列對不對」，
// 不是「這欄叫什麼」。

_fb.wbImportOpen = () => {
    _wbImportRows = null;
    _wbModal('匯入對帳單明細', `
        <p style="color:#888;font-size:12px;margin:0 0 8px;">
            從網銀交易明細整塊選取複製，直接貼進來。金額的正負由<b>餘額欄</b>推算，
            不必指定哪一欄是支出 —— 所以請把<b>餘額那一欄一起複製</b>。</p>
        <textarea id="finbank-wb-paste" class="crm-input" rows="10"
            style="width:100%;box-sizing:border-box;font-family:monospace;font-size:12px;"
            placeholder="例：&#10;2026/07/01\t跨行轉入\t\t50,000\t120,000&#10;2026/07/03\t轉帳手續費\t15\t\t119,985"></textarea>
        <div id="finbank-wb-imp-err" style="display:none;color:#fca5a5;font-size:12px;margin-top:8px;white-space:pre-wrap;"></div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.wbImportParse(this)">下一步：確認明細</button>
        </div>`);
};

// ── 對帳單預覽：兩個入口（工作台貼上、貸款區上傳檔案）共用 ────────────
//
// 兩邊打的是同一支 /bank-statement/preview，只有「送什麼欄位」和「拿到之後
// 畫成什麼」不同。錯誤處理、忙碌狀態、摘要列本來各寫一份 —— 尤其
// multipart 這段：finFetch 會硬塞 Content-Type: application/json，boundary
// 就被蓋掉，所以必須走原生 fetch 讓瀏覽器自己帶。這種一寫錯就整條壞掉的
// 細節只該存在一份。

/** POST 預覽。失敗/未通過檢查 → 呼叫 show(原因) 並回 null。 */
async function _stmtFetchPreview(fd, { btn, doneLabel, show, failMsg }) {
    btn.disabled = true;
    btn.textContent = '解析中…';
    try {
        const res = await fetch(`/api/v1/finance/bank-statement/preview?entity=${finEntity()}`,
            { method: 'POST', body: fd, headers: bearerHeader() });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || '解析失敗');
        if (!d.ok) { show(failMsg + '\n• ' + (d.errors || []).join('\n• ')); return null; }
        return d;
    } catch (e) {
        show(e.message);
        return null;
    } finally {
        btn.disabled = false;
        btn.textContent = doneLabel;
    }
}

/** 「共 N 筆／存入／支出」摘要列 + 警告區塊。extra = 追加的 <span>。 */
function _stmtSummaryBar(d, extra = '') {
    const s = d.summary || {};
    const warn = (d.warnings || []).length
        ? `<div style="color:#fbbf24;font-size:12px;margin:6px 0;white-space:pre-wrap;">⚠ ${(d.warnings || []).map(esc).join('\n⚠ ')}</div>`
        : '';
    return `<div style="display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#ccc;margin-bottom:6px;">
            <span>共 <b>${fmtNum(s.count)}</b> 筆</span>
            <span>存入 <b style="color:#86efac;">$${fmtNum(s.total_in)}</b></span>
            <span>支出 <b style="color:#fca5a5;">$${fmtNum(s.total_out)}</b></span>
            ${extra}
        </div>
        ${warn}`;
}

_fb.wbImportParse = async (btn) => {
    const text = (document.getElementById('finbank-wb-paste')?.value || '').trim();
    const err = document.getElementById('finbank-wb-imp-err');
    const show = (m) => { err.textContent = m; err.style.display = 'block'; };
    err.style.display = 'none';
    if (!text) return show('請先貼上交易明細');
    const fd = new FormData();
    fd.append('bank_account_id', _wb.acct);
    fd.append('text', text);
    const d = await _stmtFetchPreview(fd, {
        btn, doneLabel: '下一步：確認明細', show,
        failMsg: '這份明細沒通過檢查，所以不匯入：',
    });
    if (!d) return;
    _wbImportRows = d.rows || [];
    _wbRenderImportPreview(d);
};

function _wbRenderImportPreview(d) {
    const rows = d.rows || [];
    const body = rows.slice(0, 8).map(r => `
        <tr style="border-top:1px solid #2a2a2a;">
            <td style="padding:3px 6px;white-space:nowrap;">${esc(r.date)}</td>
            <td style="padding:3px 6px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.description || '')}</td>
            <td style="padding:3px 6px;text-align:right;white-space:nowrap;">${_wbAmt(r.amount)}</td>
        </tr>`).join('');
    _wbModal('匯入對帳單明細 — 確認', `
        ${_stmtSummaryBar(d)}
        <div style="overflow-x:auto;border:1px solid #2a2a2a;border-radius:6px;">
            <table style="border-collapse:collapse;font-size:12px;color:#ccc;width:100%;">
                <tbody>${body}</tbody>
            </table>
        </div>
        ${rows.length > 8 ? `<div style="color:#666;font-size:11px;margin-top:4px;">…（預覽前 8 列，實際匯入 ${fmtNum(rows.length)} 筆）</div>` : ''}
        <label style="display:block;color:#ccc;font-size:12px;margin-top:10px;">
            <input type="checkbox" id="finbank-wb-replace">
            取代這幾個月已匯入的明細（重新來過）
            <span style="color:#9ca3af;">—— 不勾的話只補新的，重複的自動跳過；
            勾了會連已經勾銷好的紀錄一起清掉。</span></label>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finBank.wbImportOpen()">← 重貼</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.wbImportSave(this)">匯入</button>
        </div>`);
}

_fb.wbImportSave = async (btn) => {
    const body = document.getElementById('finbank-wb-modal-body');
    const replace = body.querySelector('#finbank-wb-replace')?.checked || false;
    // 對帳工作底稿只要 (日期, 摘要, 帶號金額)；後端已經把三者算好了
    const lines = (_wbImportRows || [])
        .filter(r => r.amount)
        .map(r => ({ line_date: r.date, description: (r.description || '').slice(0, 255),
                     amount: r.amount }));
    if (!lines.length) { finToast('沒有可匯入的明細（每列要有非 0 金額）', true); return; }
    // 🔴 不傳 month —— 每一列自己的日期決定它屬於哪個月，跨月的對帳單一次傳完
    // 就好（月份只當「該列沒有日期」時的後備，這條路每列都有日期）。
    return _wbApi('/statement-lines', {
        method: 'POST',
        body: JSON.stringify({ bank_account_id: _wb.acct, lines, replace }),
    }, {
        btn, close: true,
        okMsg: (r) => {
            const ms = r.months || [];
            return `已匯入 ${fmtNum(r.added)} 筆`
                + (ms.length > 1 ? `（涵蓋 ${ms[0]} ～ ${ms[ms.length - 1]}，共 ${ms.length} 個月）` : '')
                + (r.skipped ? `；跳過 ${fmtNum(r.skipped)} 筆重複` : '')
                + (r.dropped_matched ? `；⚠ 覆蓋掉 ${fmtNum(r.dropped_matched)} 筆已勾銷的` : '');
        },
    });
};

// ── 工作台：手動配對 / 註記 / 補記入帳 ───────────────────────

_fb.wbMatchOpen = (lineId) => {
    const ln = _wbLine(lineId);
    if (!ln) return;
    const cands = (_wb.data.entries || []).filter(e => !e.matched && e.amount === ln.amount);
    const rows = cands.map(e => `<tr style="border-top:1px solid #2a2a2a;">
        ${_wbEntryCells(e)}
        <td style="padding:4px 8px;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finBank.wbMatchPick('${lineId}','${e.id}')">選這筆</button></td>
    </tr>`).join('');
    _wbModal('配對到系統收支', `
        ${_wbBanner(ln)}
        ${cands.length ? `${_wbTable(`<tbody>${rows}</tbody>`, 320)}
        <div style="color:#666;font-size:11px;margin-top:6px;">只列同金額且未勾銷的收支（金額不同不能配 — 漏記請用「補記入帳」）。</div>`
        : '<div style="color:#888;font-size:13px;border:1px dashed #333;border-radius:6px;padding:14px;">這個月沒有同金額的未勾銷收支。若系統確實漏記，關掉這個視窗改按「補記入帳」；若是跨月時間差，用「註記」寫明。</div>'}`);
};

_fb.wbMatchPick = (lineId, entryId) => _wbApi(`/statement-lines/${lineId}/match`, {
    method: 'POST', body: JSON.stringify({ entry_id: entryId }),
}, { close: true });

_fb.wbNoteOpen = (lineId) => {
    const ln = _wbLine(lineId);
    if (!ln) return;
    _wbModal('註記（不入帳的說明）', `
        ${_wbBanner(ln)}
        <p style="color:#888;font-size:12px;margin:0 0 8px;">這筆銀行有、但不需要（或不是這個月）入系統帳時，寫清楚原因 — 例如「上月已入帳，跨月入帳時間差」。</p>
        <textarea id="finbank-wb-note" class="crm-input" rows="3" style="width:100%;box-sizing:border-box;">${esc(ln.note || '')}</textarea>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.wbNoteSave(this,'${lineId}')">儲存</button>
        </div>`);
};

_fb.wbNoteSave = (btn, lineId) => _wbApi(`/statement-lines/${lineId}`, {
    method: 'PUT',
    body: JSON.stringify({ note: document.getElementById('finbank-wb-note')?.value || '' }),
}, { btn, close: true });

_fb.wbCreateOpen = async (lineId) => {
    const ln = _wbLine(lineId);
    if (!ln) return;
    // 🔴 類別清單只有一份：_ensureCashCats（後端的 cash_category_texts）。
    // 這裡本來自己抓 /category-map 再在前端篩一遍 —— 而且篩法跟後端不一樣
    // （後端 active.is_(True) 排除 NULL、這邊 active !== false 收進 NULL），
    // 於是這個 datalist 會給出後端不認得的類別，而這一欄會直接寫進真的收支列。
    const cats = await _ensureCashCats();
    _wbModal('補記入帳（系統漏記 → 建收支明細）', `
        ${_wbBanner(ln, `<span style="color:#888;font-size:11px;">（${ln.amount >= 0 ? '存入' : '支出'}，日期金額照對帳單帶入）</span>`)}
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
            ${ln.line_date ? '' : `<div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">交易日（這列匯入時沒日期，先補上）</div>
                <input id="finbank-wb-ce-date" type="date" class="crm-input" value="${_wb.month}-01"></div>`}
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">類別（報表靠它歸科目）</div>
                <input id="finbank-wb-ce-cat" class="crm-input" list="finbank-wb-cats" style="width:170px;" placeholder="選或輸入類別">
                <datalist id="finbank-wb-cats">${cats.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
            <div style="flex:1;min-width:160px;"><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">摘要</div>
                <input id="finbank-wb-ce-summary" class="crm-input" style="width:100%;box-sizing:border-box;" value="${esc(ln.description || '')}"></div>
            <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">收款/付款人（可空）</div>
                <input id="finbank-wb-ce-payee" class="crm-input" style="width:140px;"></div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px;">
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.wbCreateSave(this,'${lineId}')">補記並勾銷</button>
        </div>`);
};

_fb.wbCreateSave = async (btn, lineId) => {
    const body = document.getElementById('finbank-wb-modal-body');
    const category = body.querySelector('#finbank-wb-ce-cat')?.value?.trim() || '';
    if (!category) { finToast('請選擇類別（報表要靠它歸科目）', true); return; }
    const dateEl = body.querySelector('#finbank-wb-ce-date');   // 無日期匯入列才有這欄
    if (dateEl) {
        if (!dateEl.value) { finToast('這列沒有交易日，請先填日期', true); return; }
        btn.disabled = true;
        try {
            await finFetch(`/statement-lines/${lineId}`, {
                method: 'PUT', body: JSON.stringify({ line_date: dateEl.value }),
            });
        } catch (e) { finToast(e.message, true); btn.disabled = false; return; }
        btn.disabled = false;
    }
    return _wbApi(`/statement-lines/${lineId}/create-entry`, {
        method: 'POST',
        body: JSON.stringify({
            category,
            summary: body.querySelector('#finbank-wb-ce-summary')?.value || '',
            payee: body.querySelector('#finbank-wb-ce-payee')?.value || '',
        }),
    }, { btn, okMsg: '已補記收支並勾銷 ✓', close: true });  // 409：該月已鎖帳 → 顯示後端 detail
};

// ── 帳務調整 ────────────────────────────────────────────────

function _adjAcctOptions(selectedId) {
    // 排除 code 11 開頭的銀行類科目（銀行餘額走收支/對帳，不走調整）
    const opts = _coa.filter(a => !String(a.code || '').startsWith('11') && a.active !== false);
    return '<option value="">— 選擇科目 —</option>' + opts.map(a =>
        `<option value="${esc(a.id)}" title="${esc(a.name_plain || '')}"${String(a.id) === String(selectedId) ? ' selected' : ''}>${esc(a.name)}</option>`
    ).join('');
}

function _adjListHtml() {
    if (!_adjustments.length) return '<div style="color:#666;font-size:12px;">尚無調整分錄</div>';
    const acctName = (id) => {
        const a = _coa.find(x => String(x.id) === String(id));
        return a ? a.name : ('#' + id);
    };
    const typeLabel = (t) => (ADJ_TYPES.find(x => x.v === t)?.label) || t || '';
    return `
        <table style="border-collapse:collapse;font-size:12px;color:#ccc;width:100%;">
            <thead><tr style="color:#888;text-align:left;">
                ${sortableTh('date', '日期', 'style="padding:5px 8px;"')}
                ${sortableTh('acct', '科目', 'style="padding:5px 8px;"')}
                ${sortableTh('amount', '金額', 'style="padding:5px 8px;text-align:right;"')}
                ${sortableTh('type', '類型', 'style="padding:5px 8px;"')}
                ${sortableTh('desc', '說明', 'style="padding:5px 8px;"')}
                <th style="padding:5px 8px;"></th>
            </tr></thead>
            <tbody>${_adjSorter.sorted(_adjustments).map(a => `
                <tr style="border-top:1px solid #2a2a2a;">
                    <td style="padding:5px 8px;">${esc(a.adj_date ? String(a.adj_date).substring(0, 10) : '')}</td>
                    <td style="padding:5px 8px;">${esc(acctName(a.account_id))}</td>
                    <td style="padding:5px 8px;text-align:right;color:${(a.amount || 0) < 0 ? '#fca5a5' : '#86efac'};">$${fmtNum(a.amount)}</td>
                    <td style="padding:5px 8px;">${esc(typeLabel(a.adj_type))}</td>
                    <td style="padding:5px 8px;color:#999;">${esc(a.description || '')}</td>
                    <td style="padding:5px 8px;"><button class="crm-btn crm-btn-secondary crm-btn-sm"
                        onclick="window._finBank.adjDel('${esc(a.id)}')">刪除</button></td>
                </tr>`).join('')}
            </tbody>
        </table>`;
}

_fb.adjAdd = async (btn) => {
    const g = (id) => _c.querySelector('#finbank-adj-' + id);
    const adj_date = g('date').value;
    const account_id = g('acct').value;
    const amount = parseFloat(g('amount').value);
    if (!adj_date || !account_id || isNaN(amount)) {
        finToast('請填日期、科目與金額', true);
        return;
    }
    btn.disabled = true;
    try {
        await finFetch('/adjustments', {
            method: 'POST',
            body: JSON.stringify({
                adj_date, account_id, amount,
                adj_type: g('type').value,
                description: g('desc').value.trim(),
            }),
        });
        finToast('調整已新增');
        _fb.reload();
    } catch (e) {
        finToast(e.message, true);
        btn.disabled = false;
    }
};

_fb.adjDel = async (id) => {
    if (!confirm('確定刪除這筆調整？')) return;
    try {
        await finFetch('/adjustments/' + id, { method: 'DELETE' });
        finToast('已刪除');
        _fb.reload();
    } catch (e) { finToast(e.message, true); }
};

// ── 銀行對帳單匯入（上傳/貼上 → 逐列確認 → 寫帳）──────────────
//
// 為什麼要「預覽再確認」而不是解析完直接匯：對帳單解析得再穩，把錢寫進帳這件事
// 都該由人按下最後一步。預覽把每一列的判斷攤開（分類、配到哪筆貸款哪一期、是不是
// 已經匯過），錯的當場取消勾選 —— 不做「全自動但你不知道它做了什麼」。

_fb.stmtOpen = async () => {
    _stmtPreview = null;
    const opts = _bankOnly().map(a =>
        `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
    _wbModal('匯入銀行對帳單', `
        <p style="color:#888;font-size:12px;margin:0 0 10px;">
            上傳網銀下載的交易明細（PDF / CSV / TXT），或把網銀畫面的交易表格複製貼上。
            系統用<b>餘額欄</b>推每筆是支出還是存入，再跟對帳單自己印的總計核對 ——
            對不上會直接擋下來，不會猜。貸款扣款會自動配到對應的貸款期別。</p>
        <div class="crm-field"><label>這份對帳單是哪個帳戶</label>
            <select id="finbank-stmt-acct" class="crm-select">${opts}</select></div>
        <div class="crm-field"><label>上傳檔案</label>
            <input type="file" id="finbank-stmt-file" accept=".pdf,.csv,.txt" class="crm-file">
            <div style="color:#666;font-size:11px;margin-top:3px;">掃描成圖片的 PDF 沒有文字層，讀不到 —— 那種請改用下面的貼上。</div></div>
        <div class="crm-field"><label>或：貼上交易明細</label>
            <textarea id="finbank-stmt-text" rows="6" class="crm-input"
                placeholder="從網銀整塊選取複製，直接貼在這裡"
                style="font-family:ui-monospace,monospace;font-size:12px;"></textarea></div>
        <div id="finbank-stmt-err" style="display:none;color:#fca5a5;font-size:12px;margin:6px 0;white-space:pre-wrap;"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finBank.wbCloseModal()">取消</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.stmtParse(this)">解析看看</button>
        </div>`);
};

_fb.stmtParse = async (btn) => {
    const err = document.getElementById('finbank-stmt-err');
    const show = (m) => { err.textContent = m; err.style.display = 'block'; };
    err.style.display = 'none';
    const acctId = document.getElementById('finbank-stmt-acct').value;
    const file = document.getElementById('finbank-stmt-file').files[0];
    const text = document.getElementById('finbank-stmt-text').value.trim();
    if (!acctId) return show('請先選帳戶');
    if (!file && !text) return show('請上傳檔案或貼上交易明細');

    const fd = new FormData();
    fd.append('bank_account_id', acctId);
    if (file) fd.append('file', file);
    if (text) fd.append('text', text);
    const d = await _stmtFetchPreview(fd, {
        btn, doneLabel: '解析看看', show,
        failMsg: '這份對帳單沒通過檢查，所以不匯入：',
    });
    if (!d) return;
    // 帳戶跟著預覽回來（bank_account_id）—— 不用再自己記一份
    _stmtPreview = d;
    // 類別清單也一起回來了（mapped_categories）—— 這條路不用再跨 prefix 抓
    if (d.mapped_categories && d.mapped_categories.length) _cashCats = d.mapped_categories;
    _stmtRenderPreview();
};

/* ── 草稿：掛好專案與發票、確認無誤之後再匯入 ───────────────────────────
 *
 * 一份對帳單幾十列，每列要決定分類、掛哪個專案、對到哪張發票，中途常常要去查
 * 別的資料（owner 2026-08-21）。沒有草稿的話，人一離開就得從上傳重來。
 *
 * 🔴 草稿存的是**原始對帳單文字 + 人工決定**，不是畫面的快照。開啟時後端重新
 * 解析，才拿得到最新的重複判定 —— 草稿放兩天，中間可能有幾列已經從別的路進帳了。
 */
function _draftsStrip() {
    if (!_drafts.length) return '';
    return `<div id="finbank-drafts" style="margin:0 0 12px;padding:10px 12px;background:#1c2536;
                border:1px solid #2c3a52;border-radius:6px;">
        <div style="color:#93c5fd;font-size:12px;margin-bottom:6px;">
            未匯入的草稿（${_drafts.length}）—— 掛到一半的對帳單，接著做</div>
        ${_drafts.map(d => `<div style="display:flex;align-items:center;gap:8px;
                padding:4px 0;border-top:1px solid #2c3a52;">
            <div style="flex:1;min-width:0;color:#ddd;font-size:12px;overflow:hidden;
                        text-overflow:ellipsis;white-space:nowrap;">${esc(d.name)}</div>
            <div style="color:#6b7280;font-size:11px;white-space:nowrap;">${esc(d.updated_at)}</div>
            <button class="crm-btn crm-btn-primary crm-btn-sm"
                onclick="window._finBank.draftOpen('${esc(d.id)}', this)">接著做</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                onclick="window._finBank.draftDelete('${esc(d.id)}', this)">刪除</button>
        </div>`).join('')}
    </div>`;
}

_fb.draftOpen = async (id, btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '載入中…'; }
    try {
        const d = await finFetch('/bank-statement/drafts/' + id);
        if (!d.ok) {
            finToast('這份草稿的對帳單重新解析失敗：'
                + (d.errors || []).join('；'), true);
            return;
        }
        _stmtPreview = d;
        if (d.mapped_categories && d.mapped_categories.length) _cashCats = d.mapped_categories;
        _stmtRenderPreview();
        // 存草稿之後才被匯進去的列會變成「已匯過」並自動取消勾選 —— 要講出來，
        // 不然使用者以為自己上次沒勾到。
        const dup = (d.rows || []).filter(r => r.duplicate).length;
        if (dup) finToast(`這份草稿有 ${dup} 列在帳上已經有了，已自動取消勾選`);
        // 對帳單被重新下載過（摘要或金額改了）時，存過的決定會對不回去 ——
        // 不講的話人以為自己上次沒做完
        if (d.draft_missing) {
            finToast(`有 ${d.draft_missing} 列的決定對不回去（對帳單內容跟存檔時不一樣）`,
                     true);
        }
    } catch (e) {
        finToast(e.message, true);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '接著做'; }
    }
};

/** 只把草稿區換掉。
 *  _fb.reload() 是整個子頁重畫：5 支 API + 重建 shell，還會把使用者開著的
 *  對帳工作台一起收掉 —— 而存一份草稿只改了這一條。 */
async function _refreshDrafts() {
    try { _drafts = (await finFetch('/bank-statement/drafts')).drafts || []; }
    catch (_) { _drafts = []; }
    const el = _c && _c.querySelector('#finbank-drafts');
    if (el) el.outerHTML = _draftsStrip();
    else _fb.reload();          // 本來沒有草稿區（清單原本是空的）→ 只能整個重畫
}

_fb.draftDelete = async (id, btn) => {
    const d = _drafts.find(x => x.id === id);
    if (!confirm(`刪掉草稿「${d ? d.name : ''}」？裡面掛好的專案與發票會一起消失。`)) return;
    if (btn) btn.disabled = true;
    try {
        await finFetch('/bank-statement/drafts/' + id, { method: 'DELETE' });
        finToast('草稿已刪除');
        await _refreshDrafts();
    } catch (e) {
        finToast(e.message, true);
        if (btn) btn.disabled = false;
    }
};

_fb.stmtSaveDraft = async (btn) => {
    const d = _stmtPreview;
    if (!d) return;
    const err = document.getElementById('finbank-stmt-err');
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
        const r = await finFetch('/bank-statement/drafts', {
            method: 'POST',
            body: JSON.stringify({
                // 已經是草稿就更新同一份 —— 每按一次存一筆新的，一週後會有十幾份
                // 長得一樣的草稿，那比沒有還糟
                id: d.draft_id || null,
                bank_account_id: d.bank_account_id,
                source_text: d.source_text || '',
                rows: (d.rows || []).map(x => ({ ..._stmtRowPayload(x),
                                                 selected: !!x.selected })),
            }),
        });
        d.draft_id = r.id;          // 之後再按就是更新這一份
        _wbCloseModal();
        finToast(`已存成草稿「${r.name}」—— 在對帳系統可以接著做`);
        await _refreshDrafts();
    } catch (e) {
        if (err) { err.textContent = e.message; err.style.display = 'block'; }
    } finally {
        btn.disabled = false;
        btn.textContent = '存成草稿';
    }
};

/** 只重畫**一列**。
 *
 * 🔴 不要在編輯時整表重畫（owner 2026-08-21：「編輯後不要跳到最上面」）。
 * _stmtRenderPreview() 會重建整個 modal body → 捲軸回到頂端，一份三十列的
 * 對帳單改到第 20 列，每改一次就被彈回第 1 列。改分類、挑專案、挑發票影響到的
 * 都只有那一列（專案格能不能用跟著分類走，也在同一列裡），逐列換就夠。
 *
 * 另一個好處：整表重畫會把所有下拉丟給 select-upgrade 重新升級一次（閃一下）。
 */
function _stmtRefreshRow(i) {
    const tb = document.getElementById('finbank-stmt-tbody');
    const tr = tb && tb.querySelectorAll('tr')[i];
    if (!tr || !_stmtPreview || !_stmtPreview.rows[i]) return;
    tr.outerHTML = _stmtRow(_stmtPreview.rows[i], i);
}

function _stmtRow(r, i) {
    const isOut = r.amount < 0;
    const tag = (txt, bg, fg) =>
        `<span style="font-size:10px;padding:1px 5px;border-radius:7px;background:${bg};color:${fg};">${esc(txt)}</span>`;
    let status = '';
    if (r.duplicate) status = tag('已匯過', '#3a2d12', '#fbbf24');
    else if (r.inferred) status = tag('方向請確認', '#3a2d12', '#fbbf24');
    else if (r.unmapped_category) status = tag(r.category ? '科目未對映' : '無類別', '#3a2d12', '#fbbf24');
    let loanCell = '';
    if (r.is_loan) {
        loanCell = r.loan_id
            ? `${esc(r.loan_name)} 第${r.period_no}期 ` +
              tag(r.match_confidence === 'account' ? '帳號認出' : '金額吻合', '#14351f', '#86efac')
            : tag('配不到貸款 → 會當一般支出記', '#3a1f1f', '#fca5a5');
    }
    return `<tr style="border-bottom:1px solid #2a2a2a;${r.duplicate ? 'opacity:.55;' : ''}">
        <td style="padding:4px 6px;"><input type="checkbox" data-stmt-i="${i}"
            onchange="window._finBank.stmtPick(${i}, this.checked)" ${r.selected ? 'checked' : ''}></td>
        <td style="padding:4px 6px;color:#ccc;white-space:nowrap;">${esc(r.date)}</td>
        <td style="padding:4px 6px;color:#ddd;" title="${esc(r.description || '')}">${esc(r.description || '')}</td>
        <td style="padding:4px 6px;text-align:right;white-space:nowrap;color:${isOut ? '#fca5a5' : '#86efac'};">
            ${isOut ? '-' : '+'}$${fmtNum(Math.abs(r.amount))}</td>
        <td style="padding:4px 6px;white-space:nowrap;">
            <select class="crm-select crm-select-sm"
                    onchange="window._finBank.stmtCatChanged(${i}, this.value)">
                ${_stmtCatOptions(r.category)}
            </select>
            <button type="button" title="把「${esc((r.description || '').slice(0, 12))} → 這個類別」存成規則，以後自動套用"
                    onclick="window._finBank.stmtSaveRule(${i})"
                    style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:12px;padding:0 2px;">＋規則</button>
        </td>
        <td style="padding:4px 6px;">${_stmtProjCell(r, i)}</td>
        <td style="padding:4px 6px;">${_stmtInvCell(r, i)}</td>
        <td style="padding:4px 6px;">${loanCell}</td>
        <td style="padding:4px 6px;">${status}</td>
    </tr>`;
}

/** 類別下拉的選項。來源是後端的收支科目對映（規則只能填報表認得的類別）。 */
let _cashCats = null;

/** 類別清單由後端供（唯一正本）—— 前端寫死的清單會跟科目對映脫節。
 *  那支端點在 crm prefix 下，這裡直接打完整路徑（不為了一次呼叫把 crmFetch
 *  拉進財務模組，那條線的 base 與錯誤處理都不一樣）。抓一次就夠，兩個入口共用。 */
async function _ensureCashCats() {
    if (_cashCats) return _cashCats;
    try {
        const res = await fetch('/api/v1/crm/cash-entries/options',
                                { headers: bearerHeader() });
        _cashCats = res.ok ? ((await res.json()).categories || []) : [];
    } catch (_) { _cashCats = []; }
    return _cashCats;
}

function _stmtCatOptions(cur) {
    const list = _cashCats || (cur ? [cur] : []);
    return `<option value="">（未分類）</option>`
        + list.map(v => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(v)}</option>`).join('');
}

/** 逐列改分類 —— 規則沒中的列本來只能整批落到「未歸類」，那比沒分類更難發現
 *  （畫面上看起來「已經分好了」）。改在這裡當場修掉，最省事。 */
_fb.stmtCatChanged = (i, v) => {
    if (!_stmtPreview || !_stmtPreview.rows[i]) return;
    const r = _stmtPreview.rows[i];
    const cats = _stmtPreview.project_categories || [];
    r.category = v;
    // 「會落到未歸類嗎」的規則跟後端同一條：沒類別**或**類別沒有科目對映。
    // 只判 !v 的話，改成一個沒對映的類別之後那個提醒就消失了。
    r.unmapped_category = !v || !(_stmtPreview.mapped_categories || []).includes(v);
    // 分類換成非專案類 → 原本掛的專案要跟著清掉，否則寫入時會被守衛擋下
    // （行政/薪資掛專案會讓專案毛利多算一筆不屬於它的錢）
    if (!cats.includes(v)) r.project_id = null;
    _stmtRefreshRow(i);        // 專案格的可用與否跟著分類走（只有這一列會變）
};

/** 把「這列的摘要 → 這個類別」存成規則。關鍵字由使用者自己打。
 *
 * 🔴 刻意**不**預填猜測值（owner 2026-08-20）。原本是 `description.slice(0, 8)`，
 * 對「2026/08/20 電信費 150725950595分行作業管理部」這種摘要會猜出 `2026/08/` ——
 * 存下去就是「2026 年 8 月的交易全歸這類」，而且它**會生效**，錯得很安靜。
 * （摘要裡會留著第二個日期欄，因為解析器只吃掉第一個日期。）
 * 改成把完整摘要放在提示裡讓人照著挑，輸入框留空 —— 猜錯的預設值比沒有更糟。
 */
_fb.stmtSaveRule = async (i) => {
    const r = _stmtPreview && _stmtPreview.rows[i];
    if (!r) return;
    if (!r.category) return finToast('先選一個類別再存規則');
    const kw = prompt(
        '摘要：' + (r.description || '')
        + '\n\n從上面挑一段當關鍵字（摘要包含它就自動歸到「'
        + r.category + '」）：', '');
    if (!kw || !kw.trim()) return;
    if (!(r.description || '').includes(kw.trim())) {
        // 打錯字的話這條規則永遠不會中，而且沒有任何跡象 —— 當場擋下來
        return alert('「' + kw.trim() + '」不在這列的摘要裡，這條規則不會生效。\n\n'
            + '摘要：' + (r.description || ''));
    }
    try {
        await finFetch('/import-rules', { method: 'POST', body: JSON.stringify({
            keyword: kw.trim(), category: r.category,
            bank_account_id: _stmtPreview.bank_account_id,   // 綁這個帳戶：各行摘要用語不同
            sort_order: 50, active: true,
            note: '從對帳單預覽建立' }) });
        finToast('已存成規則：' + kw.trim() + ' → ' + r.category);
    } catch (e) {
        alert('存不起來：' + e.message);
    }
};

// ── 分類規則（bank_import_rules）──────────────────────────
//
// 銀行摘要文字 →〔規則〕→ 類別 →〔科目對映〕→ 會計科目。
// 右半邊本來就是後台可編的，左半邊原本寫死 14 條在 core/bank_statement.py。

let _rules = [];

/** 帳戶 id → 名稱（找不到就顯示 id 前 8 碼，不要靜靜變空白）。 */
function _acctName(id) {
    const a = _accounts.find(x => String(x.id) === String(id));
    return a ? (a.name || '') : String(id).slice(0, 8);
}

_fb.rulesOpen = async () => {
    await _ensureCashCats();
    try { _rules = (await finFetch('/import-rules')).items || []; }
    catch (_) { _rules = []; }
    _rulesRender();
};

function _rulesRender() {
    const acctOpts = '<option value="">所有帳戶</option>' + _bankOnly()
        .map(a => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
    const catOpts = '<option value="">— 選類別 —</option>'
        + (_cashCats || []).map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    const rows = _rules.map(r => `
        <tr style="border-bottom:1px solid #2a2a2a;${r.active ? '' : 'opacity:.45;'}">
            <td style="padding:4px 6px;color:#ddd;">${esc(r.keyword)}</td>
            <td style="padding:4px 6px;color:#bbb;">${esc(r.category)}</td>
            <td style="padding:4px 6px;color:#9ca3af;font-size:11px;">${
                r.bank_account_id ? esc(_acctName(r.bank_account_id)) : '所有帳戶'}</td>
            <td style="padding:4px 6px;text-align:right;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm"
                        onclick="window._finBank.ruleToggle('${r.id}', ${r.active ? 'false' : 'true'})">${
                    r.active ? '停用' : '啟用'}</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm"
                        onclick="window._finBank.ruleDelete('${r.id}')">刪除</button>
            </td>
        </tr>`).join('');
    _wbModal('分類規則', `
        <p style="color:#888;font-size:12px;margin:0 0 10px;">
            對帳單的摘要包含「關鍵字」就自動歸到那個類別。
            <b>綁定帳戶的規則優先於「所有帳戶」</b> —— 合庫寫「攤還本息」、一銀寫
            「中小７月」，同一件事兩種寫法，綁帳戶才不會互相誤觸。
            由上而下比對，先命中的先贏。</p>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:flex-end;margin-bottom:10px;">
            <div><div style="color:#9ca3af;font-size:11px;">關鍵字</div>
                <input id="rule-kw" class="crm-input" style="width:130px;" placeholder="摘要含這串"></div>
            <div><div style="color:#9ca3af;font-size:11px;">歸到類別</div>
                <select id="rule-cat" class="crm-select">${catOpts}</select></div>
            <div><div style="color:#9ca3af;font-size:11px;">適用帳戶</div>
                <select id="rule-acct" class="crm-select">${acctOpts}</select></div>
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.ruleAdd(this)">新增</button>
        </div>
        <div id="rule-err" style="display:none;color:#fca5a5;font-size:12px;margin-bottom:6px;"></div>
        ${_wbTable(`<thead><tr>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">關鍵字</th>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">類別</th>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">適用帳戶</th>
            <th></th></tr></thead><tbody>${rows}</tbody>`, 300)}
        <div style="display:flex;gap:8px;justify-content:space-between;align-items:center;margin-top:12px;padding-top:10px;border-top:1px solid #2a2a2a;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finBank.rulesApplyUnclassified(this)"
                    title="只碰 category 是空的列 —— 已經有類別的（不管規則分的還是人手改的）一律不動">
                套用到未歸類的歷史列</button>
            <button class="crm-btn crm-btn-secondary" onclick="window._finBank.wbCloseModal()">關閉</button>
        </div>`);
}

_fb.ruleAdd = async (btn) => {
    const err = document.getElementById('rule-err');
    const show = (m) => { err.textContent = m; err.style.display = 'block'; };
    err.style.display = 'none';
    const kw = document.getElementById('rule-kw').value.trim();
    const cat = document.getElementById('rule-cat').value;
    if (!kw) return show('請填關鍵字');
    if (!cat) return show('請選類別');
    btn.disabled = true;
    try {
        await finFetch('/import-rules', { method: 'POST', body: JSON.stringify({
            keyword: kw, category: cat,
            bank_account_id: document.getElementById('rule-acct').value || null,
            sort_order: 50, active: true, note: '手動新增' }) });
        await _fb.rulesOpen();
    } catch (e) {
        show(e.message);
        btn.disabled = false;
    }
};

_fb.ruleToggle = async (id, active) => {
    const r = _rules.find(x => x.id === id);
    if (!r) return;
    await finFetch('/import-rules/' + id, { method: 'PUT', body: JSON.stringify({
        keyword: r.keyword, category: r.category,
        bank_account_id: r.bank_account_id || null,
        sort_order: r.sort_order, active, note: r.note }) });
    await _fb.rulesOpen();
};

_fb.ruleDelete = async (id) => {
    const r = _rules.find(x => x.id === id);
    if (!confirm(`刪除規則「${r ? r.keyword : id}」？`)) return;
    await finFetch('/import-rules/' + id, { method: 'DELETE' });
    await _fb.rulesOpen();
};

_fb.rulesApplyUnclassified = async (btn) => {
    if (!confirm('把現行規則套用到還沒分類的歷史收支列？\n\n'
        + '只會碰「類別是空的」那些 —— 已經有類別的不動。')) return;
    btn.disabled = true;
    btn.textContent = '套用中…';
    try {
        const r = await finFetch(`/import-rules/apply-unclassified?entity=${finEntity()}`,
                                 { method: 'POST' });
        const detail = Object.entries(r.by_category || {})
            .map(([k, v]) => `${k} ${v}`).join('、');
        finToast(`掃了 ${fmtNum(r.scanned)} 筆未歸類，分好 ${fmtNum(r.changed)} 筆`
            + (detail ? `（${detail}）` : ''));
    } catch (e) {
        alert('套用失敗：' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '套用到未歸類的歷史列';
    }
};

/* ── 挑專案／挑發票：第二層視窗 ────────────────────────────────────────
 *
 * 原本這兩格是 <select>。下拉在這裡有三個過不去的問題（owner 2026-08-21）：
 *   1. 一格塞不下判斷所需的資訊 —— 專案要看客戶與狀態才知道是不是同名的另一案，
 *      發票要看日期與尚欠才知道是不是這筆匯款。一個 <option> 只有一行字。
 *   2. 一筆匯款常常是**合併好幾張發票**的錢，還要逐張填金額；下拉天生只能挑一張。
 * （下拉本身**有**搜尋 —— select-upgrade.js 會把選項 ≥8 的原生 select 自動換成
 *  可搜尋元件。所以「沒搜尋」不是理由，「一行字放不下、又不能複選」才是。）
 * 改成按鈕 → 第二層視窗（可搜尋、資訊完整、發票可複選並分配金額）。
 * 挑完回到預覽（第一層原封不動留在後面，不重畫、不重打 API）。
 */

/** 第二層視窗自己一個 overlay —— 不能借用 _wbModal 的那個：
 *  那支是換掉同一個容器的內容，一開就把底下的預覽表洗掉了。 */
function _pickModal(title, bodyHtml, { width = 720 } = {}) {
    let o = document.getElementById('finbank-pick-modal');
    if (!o) {
        o = document.createElement('div');
        o.id = 'finbank-pick-modal';
        o.className = 'crm-modal-overlay';
        o.style.zIndex = '1100';          // 疊在第一層（1000）之上
        o.innerHTML = `<div class="crm-modal">
            <div class="crm-modal-header">
                <h3 id="finbank-pick-title"></h3>
                <button class="crm-detail-close"
                        onclick="window._finBank.pickClose()">&#x2715;</button>
            </div>
            <div class="crm-modal-body" id="finbank-pick-body"></div>
        </div>`;
        document.body.appendChild(o);
    }
    o.querySelector('.crm-modal').style.maxWidth = `min(${width}px, 94vw)`;
    o.querySelector('#finbank-pick-title').textContent = title;
    o.querySelector('#finbank-pick-body').innerHTML = bodyHtml;
    o.style.display = 'flex';
    const box = o.querySelector('#finbank-pick-search');
    if (box) box.focus();
}

_fb.pickClose = () => {
    const o = document.getElementById('finbank-pick-modal');
    if (o) {
        o.style.display = 'none';
        // 內容一起清掉 —— overlay 是重複使用的，不清的話最後一次挑發票的
        // 幾千個節點會一直留在文件裡
        o.querySelector('#finbank-pick-body').innerHTML = '';
    }
    _pick = null;
};

/** 第二層視窗的暫存狀態（挑到一半的東西；按確定才寫回 _stmtPreview.rows）。 */
let _pick = null;

const _pickHay = (...parts) => parts.filter(Boolean).join(' ').toLowerCase();

// 一次最多畫幾列。清單都是「最可能的排前面」，看不到的那些靠搜尋。
const _PICK_MAX = 100;

/** 發票清單的欄寬 —— 表頭與資料列**共用同一組**，各寫一份一定會歪。
 *  原本一列是兩行、欄位用「·」串起來，數字沒有對齊，一頁十幾張很難掃。 */
const _INV_GRID = 'display:grid;grid-template-columns:'
    + '20px 116px minmax(0,1fr) 88px 168px 92px 92px 96px 104px;'
    + 'align-items:center;gap:8px;';

/** 搜尋框輸入 → 只重畫清單（不重畫整個視窗，不然游標會跳掉）。 */
_fb.pickSearch = (q) => {
    if (!_pick) return;
    _pick.q = (q || '').trim().toLowerCase();
    const el = document.getElementById('finbank-pick-list');
    if (el) el.innerHTML = _pick.render();
};

/* ── 專案 ─────────────────────────────────────────────────────────── */

/** 專案格：只有專案類的分類收得下（規則同收支明細，由後端回的清單決定）。 */
function _stmtProjCell(r, i) {
    const cats = (_stmtPreview && _stmtPreview.project_categories) || [];
    if (!cats.includes(r.category)) {
        return '<span style="color:#4b5563;font-size:11px;">—</span>';
    }
    const p = _stmtIndex().proj[r.project_id];
    const label = p ? esc(p.name) : '＋ 選專案';
    return `<button type="button" class="crm-btn crm-btn-secondary"
            onclick="window._finBank.stmtPickProj(${i})"
            title="${p ? esc(p.name) : '挑一個專案'}"
            style="width:100%;text-align:left;font-size:11px;padding:3px 6px;
                   ${p ? '' : 'color:#6b7280;'}overflow:hidden;text-overflow:ellipsis;
                   white-space:nowrap;">${label}</button>`;
}

_fb.stmtPickProj = (i) => {
    const r = _stmtPreview && _stmtPreview.rows[i];
    if (!r) return;
    const list = (_stmtPreview.projects || []);
    _pick = {
        q: '',
        render() {
            const all = list.filter(p => !this.q
                || _pickHay(p.name, p.client, p.status).includes(this.q));
            if (!all.length) {
                return '<div style="color:#6b7280;font-size:12px;padding:14px;">找不到符合的專案</div>';
            }
            const rows = all.slice(0, _PICK_MAX);      // 理由同發票清單
            const more = all.length - rows.length;
            const tail = more > 0
                ? `<div style="color:#6b7280;font-size:11px;padding:8px 10px;">
                     還有 ${fmtNum(more)} 個沒顯示 —— 繼續輸入縮小範圍</div>`
                : '';
            return rows.map(p => {
                const on = p.id === r.project_id;
                return `<div onclick="window._finBank.pickProjTake('${esc(p.id)}')"
                    style="padding:7px 10px;border-bottom:1px solid #2a2a2a;cursor:pointer;
                           ${on ? 'background:#1e3a2a;' : ''}">
                    <div style="color:#eee;font-size:13px;">${esc(p.name)}</div>
                    <div style="color:#9ca3af;font-size:11px;margin-top:2px;">
                        ${esc(p.client || '（無客戶）')}
                        ${p.status ? '　·　' + esc(p.status) : ''}
                        ${p.start ? '　·　' + esc(p.start) : ''}
                    </div></div>`;
            }).join('') + tail;
        },
        take(id) {
            r.project_id = id || null;
            _fb.pickClose();
            _stmtRefreshRow(i);
        },
    };
    _pickModal(`挑專案　—　${r.date}　${(r.description || '').slice(0, 24)}`, `
        <input id="finbank-pick-search" class="crm-input" placeholder="搜尋專案／客戶…"
               oninput="window._finBank.pickSearch(this.value)"
               style="width:100%;margin-bottom:8px;">
        <div style="max-height:50vh;overflow:auto;border:1px solid #2e2e2e;border-radius:6px;"
             id="finbank-pick-list">${_pick.render()}</div>
        <div style="display:flex;justify-content:space-between;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary"
                    onclick="window._finBank.pickProjTake('')">不掛專案</button>
            <button class="crm-btn crm-btn-secondary"
                    onclick="window._finBank.pickClose()">取消</button>
        </div>`);
};

_fb.pickProjTake = (id) => { if (_pick) _pick.take(id); };

/* ── 發票 ─────────────────────────────────────────────────────────── */

/** 預覽的發票／專案查表。整張表共用一份 —— 逐格重建的話，60 列 × 400 張發票
 *  就是兩萬多次無謂的迴圈（而且每次逐列重畫又來一遍）。
 *  key 綁在 _stmtPreview 物件本身，換一份預覽自然失效。 */
let _stmtIdx = null;
function _stmtIndex() {
    if (_stmtIdx && _stmtIdx.src === _stmtPreview) return _stmtIdx;
    const inv = {}, proj = {};
    ((_stmtPreview && _stmtPreview.invoices) || []).forEach(v => { inv[v.id] = v; });
    ((_stmtPreview && _stmtPreview.projects) || []).forEach(x => { proj[x.id] = x; });
    _stmtIdx = { src: _stmtPreview, inv, proj };
    return _stmtIdx;
}

/** 送回後端的一列：只送**決定**，不送 preview 的其他欄位。
 *
 * 🔴 存草稿與匯入共用這一支。各寫一份的下場已經發生過：匯入那份把「沒掛發票」
 * 寫成 `invoices: null`，而後端那欄是 List 不收 null —— 只要對帳單裡有一列沒掛
 * 發票（也就是幾乎每一份），整批匯入就 422。存草稿那份寫的是 `|| []`，所以存
 * 得起來、匯不進去，兩條路各講各的。
 */
const _stmtRowPayload = (x) => ({
    date: x.date, amount: x.amount, description: x.description,
    category: x.category, loan_id: x.loan_id, period_no: x.period_no,
    project_id: x.project_id || null,
    invoices: x.invoices || [],
});

/** 發票格：只有收入列有意義（支出掛發票是代開付出去那側，語意不同）。 */
function _stmtInvCell(r, i) {
    if (!(r.amount > 0)) {
        return '<span style="color:#4b5563;font-size:11px;">—</span>';
    }
    const allocs = r.invoices || [];
    const byId = _stmtIndex().inv;
    let label = '＋ 選發票';
    let title = '挑發票（可複選：一筆匯款拆給多張）';
    if (allocs.length === 1) {
        const v = byId[allocs[0].invoice_id];
        label = esc((v && (v.title || v.invoice_number)) || '已選 1 張');
        title = v ? `${v.invoice_number || '無號'} ${v.title || ''}` : '';
    } else if (allocs.length > 1) {
        label = `${allocs.length} 張　$${fmtNum(allocs.reduce((t, a) => t + a.amount, 0))}`;
        title = allocs.map(a => {
            const v = byId[a.invoice_id];
            return `${(v && v.invoice_number) || '無號'} $${fmtNum(a.amount)}`;
        }).join('\n');
    }
    return `<button type="button" class="crm-btn crm-btn-secondary"
            onclick="window._finBank.stmtPickInv(${i})" title="${esc(title)}"
            style="width:100%;text-align:left;font-size:11px;padding:3px 6px;
                   ${allocs.length ? '' : 'color:#6b7280;'}overflow:hidden;
                   text-overflow:ellipsis;white-space:nowrap;">${label}</button>`;
}

_fb.stmtPickInv = (i) => {
    const r = _stmtPreview && _stmtPreview.rows[i];
    if (!r) return;
    const target = Math.abs(r.amount);
    const list = (_stmtPreview.invoices || []).slice();
    // 金額接近的排前面 —— 對一筆入帳來說，「尚欠剛好等於這個數」的那張幾乎
    // 一定就是答案，讓它不用搜尋就在第一行。同差距時日期近的優先。
    list.sort((a, b) => (Math.abs((a.outstanding || 0) - target)
                       - Math.abs((b.outstanding || 0) - target))
                     || String(b.date || '').localeCompare(String(a.date || '')));
    const sel = new Map((r.invoices || []).map(a => [a.invoice_id, a.amount]));

    _pick = {
        q: '',
        sel,
        sum() { let t = 0; this.sel.forEach(v => { t += (v || 0); }); return t; },
        remain() { return Math.max(0, target - this.sum()); },
        render() {
            const all = list.filter(v => !this.q
                || _pickHay(v.invoice_number, v.title, v.company_name).includes(this.q));
            if (!all.length) {
                return '<div style="color:#6b7280;font-size:12px;padding:14px;">找不到符合的發票</div>';
            }
            // 只畫前 100 張。清單已經照「跟這筆入帳的金額差距」排好，第 100 名
            // 之後不可能是答案；而每列是九格 grid，四百張要拼 ~280KB 的字串、
            // 生幾千個節點 —— 打一個字就重來一次。
            const rows = all.slice(0, _PICK_MAX);
            const more = all.length - rows.length;
            const tail = more > 0
                ? `<div style="color:#6b7280;font-size:11px;padding:8px 10px;">
                     還有 ${fmtNum(more)} 張沒顯示 —— 繼續輸入縮小範圍</div>`
                : '';
            return rows.map(v => {
                const on = this.sel.has(v.id);
                const hit = (v.outstanding || 0) === target;
                const cell = (html, extra = '') =>
                    `<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${extra}">${html}</div>`;
                return `<div style="${_INV_GRID}padding:5px 10px;border-bottom:1px solid #2a2a2a;
                        cursor:pointer;${on ? 'background:#1e3a2a;' : ''}"
                        onclick="window._finBank.pickInvToggle('${esc(v.id)}', ${on ? 'false' : 'true'})">
                    <input type="checkbox" ${on ? 'checked' : ''} style="pointer-events:none;">
                    ${cell(esc(v.invoice_number || '無號'), 'color:#ddd;')}
                    ${cell(esc(v.title || '') + (hit
                        ? ' <span style="color:#86efac;font-size:10px;">◀ 金額吻合</span>' : ''),
                        'color:#eee;')}
                    ${cell(esc(v.date || ''), 'color:#9ca3af;')}
                    ${cell(esc(v.company_name || ''), 'color:#9ca3af;')}
                    ${cell('$' + fmtNum(v.amount_total || 0), 'color:#ccc;text-align:right;')}
                    ${cell('$' + fmtNum(v.collected || 0), 'color:#9ca3af;text-align:right;')}
                    ${cell('$' + fmtNum(v.outstanding || 0), 'color:#fbbf24;text-align:right;')}
                    <input class="crm-input" type="number" ${on ? '' : 'disabled'}
                        value="${on ? this.sel.get(v.id) : ''}"
                        onclick="event.stopPropagation();"
                        onchange="window._finBank.pickInvAmt('${esc(v.id)}', this.value)"
                        title="分配給這張的金額"
                        style="text-align:right;font-size:12px;padding:3px 6px;min-width:0;">
                </div>`;
            }).join('') + tail;
        },
        foot() {
            const t = this.sum();
            const diff = target - t;
            return `已分配 <b style="color:#eee;">$${fmtNum(t)}</b>
                ／ 這列入帳 $${fmtNum(target)}
                ${diff === 0
                    ? '<span style="color:#86efac;">　剛好對上</span>'
                    : `<span style="color:#fbbf24;">　${diff > 0 ? '還差' : '超出'} $${fmtNum(Math.abs(diff))}</span>`}`;
        },
        redraw() {
            const el = document.getElementById('finbank-pick-list');
            if (el) el.innerHTML = this.render();
            const f = document.getElementById('finbank-pick-foot');
            if (f) f.innerHTML = this.foot();
        },
        // 🔴 允許不等於入帳金額就關掉 —— 客戶少匯、多匯、匯款手續費都會讓它對不齊，
        // 擋下來只會逼人亂填。差額用顏色提醒，寫進去的是使用者填的數。
        take() {
            const allocs = [];
            this.sel.forEach((amt, id) => {
                if ((amt || 0) > 0) allocs.push({ invoice_id: id, amount: Math.round(amt) });
            });
            // 只有 invoices 一種表示法 —— 主要發票（金額最大那張）由後端從
            // 分配表推，前端不留一份推導值
            r.invoices = allocs;
            _fb.pickClose();
            _stmtRefreshRow(i);
        },
    };
    const th = (t, right) =>
        `<div style="color:#9ca3af;font-size:11px;${right ? 'text-align:right;' : ''}">${t}</div>`;
    _pickModal(`挑發票　—　${r.date}　入帳 $${fmtNum(target)}`, `
        <input id="finbank-pick-search" class="crm-input" placeholder="搜尋發票號／抬頭／客戶…"
               oninput="window._finBank.pickSearch(this.value)"
               style="width:100%;margin-bottom:8px;">
        <div style="color:#6b7280;font-size:11px;margin-bottom:6px;">
            一筆匯款可以拆給多張發票（合併匯款）。金額接近這筆入帳的排在前面。
        </div>
        <div style="border:1px solid #2e2e2e;border-radius:6px;overflow:hidden;">
            <div style="${_INV_GRID}padding:6px 10px;background:#242424;
                        border-bottom:1px solid #2e2e2e;">
                <div></div>${th('發票號')}${th('名稱')}${th('日期')}${th('公司')}
                ${th('面額', 1)}${th('已收', 1)}${th('尚欠', 1)}${th('分配金額', 1)}
            </div>
            <div style="max-height:46vh;overflow:auto;"
                 id="finbank-pick-list">${_pick.render()}</div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:10px;">
            <div id="finbank-pick-foot" style="color:#9ca3af;font-size:12px;">${_pick.foot()}</div>
            <div style="display:flex;gap:8px;">
                <button class="crm-btn crm-btn-secondary"
                        onclick="window._finBank.pickClose()">取消</button>
                <button class="crm-btn crm-btn-primary"
                        onclick="window._finBank.pickInvTake()">確定</button>
            </div>
        </div>`, { width: 1080 });
};

_fb.pickInvToggle = (id, on) => {
    if (!_pick || !_pick.sel) return;
    if (on) {
        const v = (_stmtPreview.invoices || []).find(x => x.id === id) || {};
        // 預設帶「這張還欠多少」與「這列還沒分配掉多少」的較小者 ——
        // 兩個都是使用者本來就要算的數，先算好比留空白省事。
        _pick.sel.set(id, Math.min(v.outstanding || 0, _pick.remain()) || (v.outstanding || 0));
    } else {
        _pick.sel.delete(id);
    }
    _pick.redraw();
};

_fb.pickInvAmt = (id, v) => {
    if (!_pick || !_pick.sel) return;
    _pick.sel.set(id, Math.max(0, Math.round(Number(v) || 0)));
    _pick.redraw();
};

_fb.pickInvTake = () => { if (_pick) _pick.take(); };

/** 整張預覽表。改一列請用 _stmtRefreshRow，不要整表重畫（捲軸會跳回頂端）。
 *  `#finbank-stmt-scroll` 那個 id 是捲軸測試用來量位置的。 */
function _stmtRenderPreview() {
    const d = _stmtPreview;
    const s = d.summary || {};
    const th = (t, align) => `<th style="padding:4px 6px;text-align:${align || 'left'};color:#9ca3af;font-weight:500;font-size:11px;">${t}</th>`;
    _wbModal('確認要匯入哪些列', `
        ${_stmtSummaryBar(d, `<span>貸款扣款 <b>${fmtNum(s.loan_rows)}</b> 筆</span>
            ${s.duplicates ? `<span style="color:#fbbf24;">已匯過 ${fmtNum(s.duplicates)} 筆（預設不勾）</span>` : ''}`)}
        <div id="finbank-stmt-scroll" style="max-height:46vh;overflow:auto;border:1px solid #2e2e2e;border-radius:6px;">
            <table style="width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed;">
                <colgroup>
                    <col style="width:30px;"><col style="width:88px;">
                    <col><!-- 摘要：吃剩下的 -->
                    <col style="width:96px;"><col style="width:118px;">
                    <col style="width:150px;"><col style="width:230px;">
                    <col style="width:150px;"><col style="width:62px;">
                </colgroup>
                <thead style="position:sticky;top:0;background:#1b1b1b;"><tr>
                    ${th('<input type="checkbox" id="finbank-stmt-all">')}${th('日期')}${th('摘要')}
                    ${th('金額', 'right')}${th('分類')}${th('專案')}${th('發票')}${th('貸款期別')}${th('')}
                </tr></thead>
                <tbody id="finbank-stmt-tbody">${(d.rows || []).map(_stmtRow).join('')}</tbody>
            </table>
        </div>
        <div id="finbank-stmt-err" style="display:none;color:#fca5a5;font-size:12px;margin:6px 0;white-space:pre-wrap;"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finBank.wbCloseModal()">取消</button>
            <button class="crm-btn crm-btn-secondary" onclick="window._finBank.stmtSaveDraft(this)"
                    title="掛到一半先收起來，之後在對帳系統接著做">存成草稿</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finBank.stmtApply(this)">匯入勾選的列</button>
        </div>`, { width: 1240 });
    const all = document.getElementById('finbank-stmt-all');
    if (all) {
        all.onchange = () => {
            document.querySelectorAll('#finbank-stmt-tbody input[data-stmt-i]')
                .forEach(cb => { cb.checked = all.checked; });
            (_stmtPreview.rows || []).forEach(r => { r.selected = all.checked; });
        };
    }
}

/** 勾選狀態要存進**資料**，不能只留在畫面上。
 *
 * 🔴 這張表會重畫（改分類、挑專案、挑發票都會），而重畫是從 r.selected 重建
 * checked。狀態只在 DOM 的話，使用者手動取消的那列會被還原成打勾 —— 然後那筆
 * 就被匯進去了，畫面上完全看不出來（v2.4.110 加專案欄之後就有這個洞，
 * 2026-08-21 實測確認：取消第 2 列 → 改第 3 列的分類 → 第 2 列自己勾回來）。
 */
_fb.stmtPick = (i, checked) => {
    if (_stmtPreview && _stmtPreview.rows[i]) _stmtPreview.rows[i].selected = !!checked;
};

_fb.stmtApply = async (btn) => {
    const d = _stmtPreview;
    if (!d) return;
    // 讀**資料**不是讀畫面 —— 畫面會被重畫洗掉（見 stmtPick 的說明）
    const picked = (d.rows || []).filter(r => r.selected);
    const err = document.getElementById('finbank-stmt-err');
    if (!picked.length) {
        err.textContent = '一列都沒勾 —— 沒有東西要匯入。';
        err.style.display = 'block';
        return;
    }
    btn.disabled = true;
    btn.textContent = '匯入中…';
    try {
        const r = await finFetch('/bank-statement/apply', {
            method: 'POST',
            body: JSON.stringify({
                bank_account_id: d.bank_account_id,
                rows: picked.map(_stmtRowPayload),
            }),
        });
        _wbCloseModal();
        // 後端會擋掉帳上已有的同日同額列（全選會把「已匯過」一起勾起來，
        // 逾時重按也是）—— 跳過幾筆一定要講，否則使用者以為全部匯進去了。
        const dup = (r.skipped_duplicates || []).length;
        finToast(`已匯入 ${r.entries} 筆收支、${r.loan_payments} 期貸款繳款`
            + (r.linked_invoices ? `；掛上 ${r.linked_invoices} 張發票` : '')
            + (r.statement_lines ? `；對帳工作台同步 ${r.statement_lines} 列（已自動配對）` : '')
            + (dup ? `；跳過 ${dup} 筆重複（帳上已有）` : '')
            // 從草稿匯入時草稿**留著** —— 常常是分幾次匯（先匯確定的、剩下的再查）。
            // 匯過的列下次開啟會自動標「已匯過」且不勾，所以留著不會重複匯。
            + (d.draft_id ? '；草稿仍保留（剩下的列可以之後再匯，做完記得刪掉）' : ''));
        _fb.reload();
    } catch (e) {
        err.textContent = e.message;
        err.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '匯入勾選的列';
    }
};
