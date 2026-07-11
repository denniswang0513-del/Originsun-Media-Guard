/**
 * banking.js — 🏦 銀行帳戶子視圖（財務管理 Tab 階段二）
 *
 * 區塊：帳戶卡片（目前餘額/預設★/停用）→ 未掛帳提示（整批掛到預設帳戶）
 *       → 月底對帳（POST /reconciliations + 歷史表）→ 進階摺疊：帳務調整。
 * 後端 API prefix /api/v1/finance（fin-utils.finFetch）。
 */
import { finFetch, esc, fmtNum, finToast, finSubviewBoot, ACCT_KIND_OPTIONS } from '../fin-utils.js';

const KIND_LABEL = Object.fromEntries(ACCT_KIND_OPTIONS.map(k => [k.v, k.label]));
const ADJ_TYPES = [
    { v: 'opening', label: '期初結轉' },
    { v: 'owner_in', label: '業主投入（老闆拿錢進公司）' },
    { v: 'owner_out', label: '業主提領（老闆從公司拿錢）' },
    { v: 'accountant', label: '會計師調整' },
    { v: 'other', label: '其他調整' },
];

let _c = null;
let _isCurrent = () => true;
let _accounts = [];      // 銀行帳戶
let _unassigned = 0;
let _adjustments = [];
let _coa = [];           // 會計科目（調整表下拉用）
let _editingId = null;   // 帳戶 modal：null=新增

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
        ],
    });
    if (!results) return;
    const [bank, adj, coa] = results;
    _accounts = bank.items || [];
    _unassigned = bank.unassigned_count || 0;
    _adjustments = adj.items || [];
    _coa = coa.items || [];
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
    `;

    const reconSel = _c.querySelector('#finbank-recon-acct');
    if (reconSel) reconSel.addEventListener('change', () => {
        const resEl = _c.querySelector('#finbank-recon-result');
        if (resEl) resEl.innerHTML = '';
        _loadReconHistory(reconSel.value);
    });
    // 點 overlay 空白處關閉 modal
    const modal = _c.querySelector('#finbank-modal');
    if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });
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
