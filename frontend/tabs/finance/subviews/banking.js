/**
 * banking.js — 🏦 銀行帳戶子視圖（財務管理 Tab 階段二）
 *
 * 區塊：帳戶卡片（目前餘額/預設★/停用）→ 未掛帳提示（整批掛到預設帳戶）
 *       → 銀行貸款（階段四：卡片＋攤還表＋一鍵記繳款，自動寫收支明細）
 *       → 月底對帳（POST /reconciliations + 歷史表）→ 進階摺疊：帳務調整。
 * 後端 API prefix /api/v1/finance（fin-utils.finFetch）。
 */
import { finFetch, esc, fmtNum, finToast, finSubviewBoot, todayStr, ACCT_KIND_OPTIONS } from '../fin-utils.js';

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

let _c = null;
let _isCurrent = () => true;
let _accounts = [];      // 銀行帳戶
let _unassigned = 0;
let _adjustments = [];
let _coa = [];           // 會計科目（調整表下拉用）
let _editingId = null;   // 帳戶 modal：null=新增
let _loans = [];         // 銀行貸款
let _loanErr = null;     // /loans 載入失敗訊息（不擋其他區塊）
let _editingLoanId = null; // 貸款 modal：null=新增
let _schedLoanId = null; // 攤還表 modal 目前開的貸款 id

const _fb = (window._finBank = window._finBank || {});

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
        ],
    });
    if (!results) return;
    const [bank, adj, coa, loans] = results;
    _accounts = bank.items || [];
    _unassigned = bank.unassigned_count || 0;
    _adjustments = adj.items || [];
    _coa = coa.items || [];
    _loans = loans.items || [];
    _loanErr = loans._error || null;
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

/** 預設帳戶：is_default 且啟用中；沒有就取第一個啟用帳戶；都沒有回 null */
function _defaultAcct() {
    const actives = _accounts.filter(a => a.active !== false);
    return _accounts.find(a => a.is_default && a.active !== false) || actives[0] || null;
}

function _renderShell() {
    const actives = _accounts.filter(a => a.active !== false);
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
            ${_accounts.map(_card).join('')}
            <button class="crm-btn crm-btn-secondary" style="min-width:150px;min-height:120px;border-style:dashed;"
                    onclick="window._finBank.openAdd()">+ 新增帳戶</button>
        </div>

        <!-- 銀行貸款（階段四） -->
        <div id="finbank-loans-section" style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
            ${_loansSectionInner()}
        </div>

        <!-- 月底對帳 -->
        <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
            <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">🔍 月底對帳</h3>
            <p style="color:#888;font-size:12px;margin:0 0 12px;">照銀行對帳單（或存摺）抄月底餘額，系統幫你核對有沒有漏記。</p>
            ${actives.length === 0 ? '<div style="color:#888;font-size:13px;">先新增帳戶才能對帳。</div>' : `
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">帳戶</div>
                    <select id="finbank-recon-acct" class="crm-select">${reconOpts}</select></div>
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">月份</div>
                    <input id="finbank-recon-month" type="month" class="crm-input" value="${defMonth}"></div>
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">對帳單月底餘額</div>
                    <input id="finbank-recon-balance" type="number" class="crm-input" placeholder="照對帳單抄" style="width:150px;"></div>
                <button class="crm-btn crm-btn-primary" onclick="window._finBank.reconcile(this)">對帳</button>
            </div>
            <div id="finbank-recon-result" style="margin-top:10px;font-size:13px;"></div>
            <div id="finbank-recon-history" style="margin-top:12px;"></div>`}
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
    });
    // 點 overlay 空白處關閉 modal（帳戶 / 貸款 / 攤還表）
    for (const mid of ['finbank-modal', 'finbank-loan-modal', 'finbank-sched-modal']) {
        const m = _c.querySelector('#' + mid);
        if (m) m.addEventListener('click', (e) => { if (e.target === m) m.style.display = 'none'; });
    }
}

// ── 帳戶 CRUD ───────────────────────────────────────────────

function _openModal(a) {
    _editingId = a ? a.id : null;
    const g = (id) => _c.querySelector('#finbank-f-' + id);
    _c.querySelector('#finbank-modal-title').textContent = a ? '編輯帳戶' : '新增帳戶';
    g('name').value = a ? (a.name || '') : '';
    g('bank_name').value = a ? (a.bank_name || '') : '';
    g('account_no').value = a ? (a.account_no || '') : '';
    g('acct_kind').value = a ? (a.acct_kind || 'bank') : 'bank';
    g('opening_balance').value = a ? (a.opening_balance ?? 0) : 0;
    g('is_default').checked = !!(a && a.is_default);
    g('note').value = a ? (a.note || '') : '';
    const err = _c.querySelector('#finbank-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _c.querySelector('#finbank-modal').style.display = 'flex';
}

_fb.openAdd = () => _openModal(null);

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
    const actives = _accounts.filter(a => a.active !== false);
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
    const meta = [esc(l.lender || ''), `年利率 ${esc(l.annual_rate)}%`, esc(LOAN_METHOD_LABEL[l.method] || l.method || '')]
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

/** 貸款區塊內容（記繳款/取消後只重畫這塊，modal 不動） */
function _loansSectionInner() {
    const head = `
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
            <h3 style="color:#eee;margin:0;font-size:14px;">🏦 銀行貸款</h3>
            ${_loans.length ? '<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finBank.loanOpenAdd()">+ 新增貸款</button>' : ''}
        </div>
        <p style="color:#888;font-size:12px;margin:4px 0 12px;">公司借的每一筆錢一張卡 — 攤還表照銀行合約排好，每期一鍵記繳款、自動寫進收支明細。</p>`;
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
    return head + `<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;">${_loans.map(_loanCard).join('')}</div>`;
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
                    <th style="padding:6px 8px;">期別</th>
                    <th style="padding:6px 8px;">繳款日</th>
                    <th style="padding:6px 8px;text-align:right;">本金</th>
                    <th style="padding:6px 8px;text-align:right;">利息</th>
                    <th style="padding:6px 8px;text-align:right;">合計</th>
                    <th style="padding:6px 8px;">狀態</th>
                    <th style="padding:6px 8px;"></th>
                </tr></thead>
                <tbody>${items.map(row).join('')}</tbody>
            </table>
        </div>`;
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
        btn.disabled = false; btn.textContent = '對帳';
    }
};

async function _loadReconHistory(acctId) {
    const el = _c.querySelector('#finbank-recon-history');
    if (!el) return;
    el.innerHTML = '<div style="color:#666;font-size:12px;">載入對帳紀錄…</div>';
    let items = [];
    try {
        items = (await finFetch('/reconciliations?bank_account_id=' + encodeURIComponent(acctId))).items || [];
    } catch (e) {
        el.innerHTML = `<div style="color:#fca5a5;font-size:12px;">對帳紀錄載入失敗：${esc(e.message)}</div>`;
        return;
    }
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
                <th style="padding:5px 10px;">月份</th>
                <th style="padding:5px 10px;text-align:right;">對帳單餘額</th>
                <th style="padding:5px 10px;text-align:right;">系統餘額</th>
                <th style="padding:5px 10px;text-align:right;">差額</th>
                <th style="padding:5px 10px;">狀態</th>
            </tr></thead>
            <tbody>${items.map(row).join('')}</tbody>
        </table>`;
}

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
                <th style="padding:5px 8px;">日期</th>
                <th style="padding:5px 8px;">科目</th>
                <th style="padding:5px 8px;text-align:right;">金額</th>
                <th style="padding:5px 8px;">類型</th>
                <th style="padding:5px 8px;">說明</th>
                <th style="padding:5px 8px;"></th>
            </tr></thead>
            <tbody>${_adjustments.map(a => `
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
