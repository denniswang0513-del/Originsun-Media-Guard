/**
 * settings.js — ⚙️ 科目與設定子視圖（財務管理 Tab 階段二）
 *
 * 區塊：期初設定精靈（bank-accounts 為空 = 尚未設定，置頂大卡）
 *       → 待歸類佇列（category-map/unmapped 黃條）→ 類別對映表（可改科目/treatment）。
 * 科目代碼不出現在主 UI：下拉顯示 name（name_plain 進 option title tooltip），
 * treatment 全用白話標籤。後端 API prefix /api/v1/finance（fin-utils.finFetch）。
 */
import { finFetch, finEntity, esc, fmtNum, finToast, finSubviewBoot, TREATMENT_OPTIONS, ACCT_KIND_OPTIONS, finIsMine } from '../fin-utils.js';
import { createSortable, sortableTh, crmFetch } from '../../crm/crm-utils.js';   // 點欄頭排序（通用排序器）+ 分類樹 API（在 /crm 底下不是 /finance）

let _c = null;
let _isCurrent = () => true;
let _bankAccounts = [];
let _coa = [];        // 會計科目
let _map = [];        // 類別對映列
let _unmapped = [];   // 待歸類
let _fee = null;      // 記帳費費率（含歷史 + 未來幾期預覽）
let _margin = null;   // 預期毛利表＋人力日成本（工時預算的基礎；owner 2026-09-03）
let _tax = [];        // 收支分類樹（**只有私帳有**，母公司走上面那張平面對映表）

// 精靈帳戶列狀態（re-render 前先 _syncWizRows 保住已輸入值）
let _wizRows = [];
let _wizDefaultIdx = 0;

const _fs = (window._finSet = window._finSet || {});

// ── 類別對映表點欄頭排序（預設 key '' = 不排序、維持後端順序，點了才生效）──
const _mapSorter = createSortable({
    storageKey: 'finance_category_map_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'finset-map-card',
    onChange: () => _rerenderMapTable(),
    getters: {
        source: m => m.source || '',
        cat: m => m.category_text || '',
        acct: m => {
            const a = _coa.find(x => String(x.id) === String(m.account_id));
            return a ? a.name : (m.account_id != null && m.account_id !== '' ? '#' + m.account_id : '');
        },
        treat: m => (TREATMENT_OPTIONS.find(t => t.v === m.treatment)?.label) || m.treatment || '',
    },
});

/** 排序後重畫對映表 — 先把未儲存的下拉修改（value + dirty flag）保下來再重繪，
 *  排序只是重排顯示順序，不能洗掉使用者還沒按儲存的編輯。 */
function _rerenderMapTable() {
    const card = _c && _c.querySelector('#finset-map-card');
    if (!card) return;
    const saved = {};
    card.querySelectorAll('.finset-map-row').forEach(row => {
        saved[row.dataset.source + '\u0000' + row.dataset.cat] = {
            acct: row.querySelector('.finset-map-acct')?.value,
            treat: row.querySelector('.finset-map-treat')?.value,
            dirty: row.dataset.dirty,
        };
    });
    card.outerHTML = _mapHtml();
    _c.querySelectorAll('.finset-map-row').forEach(row => {
        const s = saved[row.dataset.source + '\u0000' + row.dataset.cat];
        if (!s) return;
        const a = row.querySelector('.finset-map-acct');
        const t = row.querySelector('.finset-map-treat');
        if (a && s.acct != null) a.value = s.acct;
        if (t && s.treat != null) t.value = s.treat;
        if (s.dirty) row.dataset.dirty = s.dirty;
    });
    _mapSorter.attach();
}

export default async function render(container, ctx = {}) {
    _c = container;
    _isCurrent = ctx.isCurrent || (() => true);
    const results = await finSubviewBoot(container, {
        title: '⚙️ 科目與設定',
        isCurrent: _isCurrent,
        retry: 'window._finSet.reload()',
        fetchers: [
            () => finFetch('/bank-accounts'),
            () => finFetch('/accounts'),
            () => finFetch('/category-map'),
            () => finFetch('/category-map/unmapped').catch(() => ({ items: [] })),
            () => finFetch('/bookkeeping-fee').catch(() => null),
            () => finFetch('/margin-model').catch(() => null),
            // 🔴 分類樹只有私帳有；母公司那本用的是平面的類別對映表（詞彙不同，
            // 混在一起私帳的類別下拉會從 5 個爆成 37 個 —— 見 core/cash_taxonomy.py）
            () => (finIsMine()
                ? crmFetch('/cash-taxonomy/nodes?entity=mine').catch(() => ({ tree: [] }))
                : Promise.resolve({ tree: [] })),
        ],
    });
    if (!results) return;
    const [bank, coa, map, unmapped, fee, margin, tax] = results;
    _tax = (tax && tax.tree) || [];
    _fee = fee;
    _margin = margin;
    _bankAccounts = bank.items || [];
    _coa = coa.items || [];
    _map = map.items || [];
    _unmapped = unmapped.items || [];
    if (_bankAccounts.length === 0 && _wizRows.length === 0) {
        _wizRows = [{ name: '', bank_name: '', account_no: '', acct_kind: 'bank', opening_balance: '' }];
        _wizDefaultIdx = 0;
    }
    _renderShell();
}

_fs.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

// ── 共用下拉 ────────────────────────────────────────────────

function _acctOptions(selectedId, withPlaceholder) {
    const sel = selectedId == null ? '' : String(selectedId);
    let html = withPlaceholder ? '<option value="">— 選擇科目 —</option>' : '';
    let matched = !sel;
    for (const a of _coa) {
        if (a.active === false && String(a.id) !== sel) continue;
        const isSel = String(a.id) === sel;
        if (isSel) matched = true;
        html += `<option value="${esc(a.id)}" title="${esc(a.name_plain || '')}"${isSel ? ' selected' : ''}>${esc(a.name)}</option>`;
    }
    // 既有值不在清單（科目被停用/刪除）→ 保留原值避免儲存時被第一個選項蓋掉
    if (!matched && sel) html = `<option value="${esc(sel)}" selected>（原科目 #${esc(sel)}）</option>` + html;
    return html;
}

function _treatOptions(selected, withPlaceholder) {
    const sel = selected || '';
    let html = withPlaceholder ? '<option value="">— 這算什麼錢 —</option>' : '';
    const known = TREATMENT_OPTIONS.some(t => t.v === sel);
    if (sel && !known) html += `<option value="${esc(sel)}" selected>${esc(sel)}</option>`;
    html += TREATMENT_OPTIONS.map(t =>
        `<option value="${t.v}"${t.v === sel ? ' selected' : ''}>${esc(t.label)}</option>`).join('');
    return html;
}

// ── Shell ───────────────────────────────────────────────────

function _renderShell() {
    _c.innerHTML = `
        <h2 style="margin:0 0 4px;color:#eee;">⚙️ 科目與設定</h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">期初設定 + 收支類別 → 財務科目的對映，是三表與儀表板的地基。</p>
        ${_bankAccounts.length === 0 ? _wizardHtml() : ''}
        ${_unmappedHtml()}
        ${_taxHtml()}
        ${_feeHtml()}
        ${_marginHtml()}
        ${_mapHtml()}
    `;
    if (_bankAccounts.length === 0) _renderWizRows();
    _wireTaxNewRow();
    _mapSorter.attach();
}

// ── 期初設定精靈 ────────────────────────────────────────────

function _wizardHtml() {
    return `
    <div style="background:#1c2536;border:1px solid #3b82f6;border-radius:10px;padding:18px 20px;margin-bottom:20px;">
        <h3 style="color:#eee;margin:0 0 4px;font-size:15px;">🚀 期初設定精靈</h3>
        <p style="color:#9ca3af;font-size:12px;margin:0 0 16px;">還沒設定任何銀行帳戶 — 花 2 分鐘完成期初設定，之後對帳、三表、儀表板才有基準。</p>

        <div style="margin-bottom:16px;">
            <div style="color:#ddd;font-size:13px;font-weight:600;">① 基準月</div>
            <div style="color:#888;font-size:12px;margin:2px 0 6px;">從哪個月開始讓系統幫你記帳？</div>
            <input id="finset-wiz-month" type="month" class="crm-input">
        </div>

        <div style="margin-bottom:16px;">
            <div style="color:#ddd;font-size:13px;font-weight:600;">② 公司的錢包們</div>
            <div style="color:#888;font-size:12px;margin:2px 0 6px;">照存摺或網銀抄「基準月 1 號」的餘額，一個帳戶一列。</div>
            <div id="finset-wiz-accts"></div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-top:6px;" onclick="window._finSet.wizAddRow()">+ 加一個帳戶</button>
        </div>

        <div style="margin-bottom:16px;">
            <div style="color:#ddd;font-size:13px;font-weight:600;">③ 既有資料</div>
            <label style="display:flex;align-items:center;gap:6px;color:#ccc;font-size:13px;margin-top:6px;cursor:pointer;">
                <input id="finset-wiz-assign" type="checkbox" checked> 把既有收支明細整批掛到第一個帳戶（之後可逐筆改）
            </label>
        </div>

        <div style="margin-bottom:16px;">
            <div style="color:#ddd;font-size:13px;font-weight:600;">④ 期初淨值（選填）</div>
            <div style="color:#888;font-size:12px;margin:2px 0 6px;">公司到基準日為止的淨值，不確定可先跳過，之後在「帳務調整」補。</div>
            <input id="finset-wiz-equity" type="number" step="any" class="crm-input" placeholder="可先留空" style="width:180px;">
        </div>

        <div>
            <div style="color:#ddd;font-size:13px;font-weight:600;margin-bottom:6px;">⑤ 完成</div>
            <button class="crm-btn crm-btn-primary" onclick="window._finSet.wizardSubmit(this)">✅ 完成設定</button>
        </div>
    </div>`;
}

function _renderWizRows() {
    const box = _c.querySelector('#finset-wiz-accts');
    if (!box) return;
    box.innerHTML = _wizRows.map((r, i) => `
        <div class="finset-wiz-row" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">
            <label title="預設帳戶（新收支預先選它）" style="display:flex;align-items:center;gap:3px;color:#9ca3af;font-size:11px;cursor:pointer;">
                <input type="radio" name="finset-wiz-default"${i === _wizDefaultIdx ? ' checked' : ''}> 預設</label>
            <input data-k="name" type="text" class="crm-input" placeholder="名稱（例：玉山主帳戶）" value="${esc(r.name)}" style="width:170px;">
            <input data-k="bank_name" type="text" class="crm-input" placeholder="銀行" value="${esc(r.bank_name)}" style="width:110px;">
            <input data-k="account_no" type="text" class="crm-input" placeholder="帳號" value="${esc(r.account_no)}" style="width:140px;">
            <select data-k="acct_kind" class="crm-select">${ACCT_KIND_OPTIONS.map(k =>
                `<option value="${k.v}"${r.acct_kind === k.v ? ' selected' : ''}>${esc(k.label)}</option>`).join('')}</select>
            <input data-k="opening_balance" type="number" step="any" class="crm-input" placeholder="期初餘額" value="${esc(r.opening_balance)}" style="width:120px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" title="移除此列" onclick="window._finSet.wizDelRow(${i})">✕</button>
        </div>`).join('');
}

function _syncWizRows() {
    const rows = _c.querySelectorAll('.finset-wiz-row');
    rows.forEach((row, i) => {
        if (!_wizRows[i]) return;
        row.querySelectorAll('[data-k]').forEach(inp => { _wizRows[i][inp.dataset.k] = inp.value; });
        const radio = row.querySelector('input[type="radio"]');
        if (radio && radio.checked) _wizDefaultIdx = i;
    });
}

_fs.wizAddRow = () => {
    _syncWizRows();
    _wizRows.push({ name: '', bank_name: '', account_no: '', acct_kind: 'bank', opening_balance: '' });
    _renderWizRows();
};

_fs.wizDelRow = (i) => {
    _syncWizRows();
    _wizRows.splice(i, 1);
    if (_wizRows.length === 0) _wizRows.push({ name: '', bank_name: '', account_no: '', acct_kind: 'bank', opening_balance: '' });
    if (_wizDefaultIdx >= _wizRows.length) _wizDefaultIdx = 0;
    _renderWizRows();
};

_fs.wizardSubmit = async (btn) => {
    const baseline_month = _c.querySelector('#finset-wiz-month')?.value;
    if (!baseline_month) { finToast('請選基準月', true); return; }
    _syncWizRows();
    const bank_accounts = [];
    let default_account_index = 0;
    _wizRows.forEach((r, i) => {
        if (!(r.name || '').trim()) return;   // 空列略過
        if (i === _wizDefaultIdx) default_account_index = bank_accounts.length;
        bank_accounts.push({
            name: r.name.trim(),
            bank_name: (r.bank_name || '').trim(),
            account_no: (r.account_no || '').trim(),
            acct_kind: r.acct_kind || 'bank',
            opening_balance: parseFloat(r.opening_balance) || 0,
        });
    });
    if (bank_accounts.length === 0) { finToast('至少要填一個帳戶名稱', true); return; }
    const equityRaw = _c.querySelector('#finset-wiz-equity')?.value;
    const assign_history = !!_c.querySelector('#finset-wiz-assign')?.checked;
    btn.disabled = true; btn.textContent = '設定中...';
    try {
        const r = await finFetch('/setup-wizard', {
            method: 'POST',
            body: JSON.stringify({
                baseline_month,
                bank_accounts,
                default_account_index,
                assign_history,
                equity_amount: (equityRaw === '' || equityRaw == null) ? null : (parseFloat(equityRaw) || 0),
            }),
        });
        finToast(`期初設定完成 — 建立 ${fmtNum(r.created_accounts ?? bank_accounts.length)} 個帳戶`
            + (assign_history ? `、掛上 ${fmtNum(r.assigned || 0)} 筆收支` : ''));
        _wizRows = [];
        _fs.reload();
    } catch (e) {
        finToast('設定失敗：' + e.message, true);
        btn.disabled = false; btn.textContent = '✅ 完成設定';
    }
};

// ── 待歸類佇列 ──────────────────────────────────────────────

function _srcBadge(source) {
    return `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:#3f3423;color:#fbbf24;">${esc(source || '')}</span>`;
}

function _unmappedHtml() {
    if (!_unmapped.length) return '';
    return `
    <div style="background:#3a2a12;border:1px solid #92600f;border-radius:8px;padding:12px 14px;margin-bottom:16px;">
        <div style="color:#fbbf24;font-size:13px;margin-bottom:8px;">⚠ 待歸類：這些類別還沒對到科目（報表會先歸到「未分類」）</div>
        ${_unmapped.map((u, i) => `
            <div class="finset-unmapped-row" data-source="${esc(u.source)}" data-cat="${esc(u.category_text)}"
                 style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:6px 0;${i ? 'border-top:1px solid #4a3a1a;' : ''}">
                ${_srcBadge(u.source)}
                <span style="color:#eee;font-size:13px;font-weight:600;">${esc(u.category_text)}</span>
                <span style="color:#9ca3af;font-size:11px;">（${fmtNum(u.usage_count)} 筆）</span>
                <select class="crm-select finset-unmapped-acct">${_acctOptions(null, true)}</select>
                <select class="crm-select finset-unmapped-treat">${_treatOptions('', true)}</select>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finSet.addMap(this)">加入對映</button>
            </div>`).join('')}
    </div>`;
}

_fs.addMap = async (btn) => {
    const row = btn.closest('.finset-unmapped-row');
    if (!row) return;
    const account_id = row.querySelector('.finset-unmapped-acct')?.value;
    const treatment = row.querySelector('.finset-unmapped-treat')?.value;
    if (!account_id || !treatment) { finToast('請先選科目與「這算什麼錢」', true); return; }
    btn.disabled = true;
    try {
        await finFetch('/category-map', {
            method: 'PUT',
            body: JSON.stringify({
                items: [{
                    source: row.dataset.source,
                    category_text: row.dataset.cat,
                    account_id,
                    treatment,
                }],
            }),
        });
        finToast(`「${row.dataset.cat}」已加入對映`);
        _fs.reload();
    } catch (e) {
        finToast(e.message, true);
        btn.disabled = false;
    }
};

// ── 記帳費 ──────────────────────────────────────────────────
//
// owner 2026-08-24：「寫一個按鈕設定會計費用來解決這件事，之後換會計調整這個
// 按鈕就好」。費率是會變的商業決定 —— 之前只活在人的記憶裡，被講錯兩次、
// 兩次都寫進了生產帳（2024/2025/2026 的 5 月期各被記成 8,000）。
//
// 🔴 舊費率**留著**：歷史期別要用當時的費率算。但使用者只填「從哪個月開始、
//    多少錢」，那張生效日期表由後端自己維護 —— 不要求人去管它。

function _feeHtml() {
    if (!_fee) return '';
    const cur = _fee.current || {};
    const nextMonth = () => {
        const d = new Date();
        d.setMonth(d.getMonth() + 1);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    };
    const hist = (_fee.rates || []).slice(0, -1).reverse();
    return `
    <div id="finset-fee-card" style="background:#1b1b1b;border:1px solid #2e2e2e;border-radius:8px;padding:14px 16px;margin-bottom:16px;">
        <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">記帳費（外部會計）</h3>
        <p style="color:#888;font-size:12px;margin:0 0 12px;">
            會計代繳營業稅，記帳費跟稅款是<b>同一筆匯出</b> —— 對帳時要拆成兩列。
            雙月收一次；一年超過 12 個月的部分，併在 <b>${_fee.extra_on_month || 5} 月</b>那期收。
            換會計或調價就改這裡，<b>舊的會留著</b>（歷史期別要用當時的費率算）。
        </p>

        <div style="display:flex;gap:22px;flex-wrap:wrap;margin-bottom:14px;">
            <div><div style="color:#6b7280;font-size:11px;">目前月費</div>
                 <div style="color:#eee;font-size:16px;font-weight:600;">$${fmtNum(cur.monthly || 0)}</div></div>
            <div><div style="color:#6b7280;font-size:11px;">一年計</div>
                 <div style="color:#eee;font-size:16px;font-weight:600;">${cur.months_per_year || 12} 個月</div></div>
            <div><div style="color:#6b7280;font-size:11px;">自</div>
                 <div style="color:#9ca3af;font-size:16px;">${esc(cur.effective_from === '0000-00' ? '一開始' : (cur.effective_from || '—'))}</div></div>
        </div>

        <div style="color:#6b7280;font-size:11px;margin-bottom:5px;">接下來幾期會收</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">
            ${(_fee.upcoming || []).map(u => `
                <span style="background:#242424;border:1px solid #2e2e2e;border-radius:6px;
                             padding:4px 9px;font-size:12px;color:#ccc;">
                    ${esc(u.month)}　<b style="color:#eee;">$${fmtNum(u.fee)}</b></span>`).join('')}
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;
                    border-top:1px solid #2a2a2a;padding-top:12px;">
            <div><label style="display:block;color:#9ca3af;font-size:11px;margin-bottom:3px;">從哪一期開始</label>
                 <input id="finset-fee-from" class="crm-input" type="month"
                        value="${esc(nextMonth())}" style="width:150px;"></div>
            <div><label style="display:block;color:#9ca3af;font-size:11px;margin-bottom:3px;">月費</label>
                 <input id="finset-fee-monthly" class="crm-input" type="number" min="1"
                        value="${cur.monthly || ''}" style="width:110px;text-align:right;"></div>
            <div><label style="display:block;color:#9ca3af;font-size:11px;margin-bottom:3px;">一年計幾個月</label>
                 <input id="finset-fee-months" class="crm-input" type="number" min="12" max="24"
                        value="${cur.months_per_year || 12}" style="width:110px;text-align:right;"></div>
            <button class="crm-btn crm-btn-primary" onclick="window._finSet.saveFee(this)">儲存</button>
        </div>

        ${hist.length ? `<div style="margin-top:12px;border-top:1px solid #2a2a2a;padding-top:10px;">
            <div style="color:#6b7280;font-size:11px;margin-bottom:5px;">過去的費率（歷史期別會用它們算）</div>
            ${hist.map(r => `<div style="color:#9ca3af;font-size:12px;padding:2px 0;">
                ${esc(r.effective_from === '0000-00' ? '一開始' : r.effective_from)} 起
                月費 $${fmtNum(r.monthly)}　一年 ${r.months_per_year} 個月</div>`).join('')}
        </div>` : ''}
    </div>`;
}

_fs.saveFee = async (btn) => {
    const from = document.getElementById('finset-fee-from')?.value || '';
    const monthly = Number(document.getElementById('finset-fee-monthly')?.value || 0);
    const months = Number(document.getElementById('finset-fee-months')?.value || 0);
    if (!/^\d{4}-\d{2}$/.test(from)) { finToast('請選生效的年月', true); return; }
    if (!(monthly > 0)) { finToast('月費要大於 0', true); return; }
    if (!(months >= 12)) { finToast('一年至少 12 個月', true); return; }
    btn.disabled = true;
    try {
        await finFetch('/bookkeeping-fee', {
            method: 'PUT',
            body: JSON.stringify({ effective_from: from, monthly, months_per_year: months }),
        });
        finToast(`${from} 起改為月費 $${fmtNum(monthly)}、一年 ${months} 個月`);
        _fs.reload();
    } catch (e) {
        finToast(e.message, true);
        btn.disabled = false;
    }
};

// ── 預期毛利 × 人力日成本（工時預算的基礎）────────────────────
//
// owner 2026-09-03：「這是我的專案利潤率，我的員工成本一天 4000，專案的使用率是用這個基礎算出來的，
// 這一塊在我的私帳設定」。表上的「項目」＝專案的 project_type；工時預算＝合約未稅 ×（1−預期毛利）
// ÷ 日成本 × 每日工時。規則本體在 core.finance_logic（burn 表、專案檔案頁、財務摘要都吃它）。
function _marginHtml() {
    if (!_margin) return '';
    const inp = 'class="crm-input" style="width:100%;box-sizing:border-box;"';
    const rows = (_margin.rows || []).map((r, i) => `
        <tr data-mrow="${i}">
            <td><input ${inp} data-f="group" value="${esc(r.group || '')}" placeholder="規格品"></td>
            <td><input ${inp} data-f="type" value="${esc(r.type || '')}" placeholder="項目（＝專案的案型）"></td>
            <td><input ${inp} data-f="margin_pct" type="number" min="0" max="99" step="1" value="${r.margin_pct ?? ''}" style="width:90px;text-align:right;"></td>
            <td><input ${inp} data-f="note" value="${esc(r.note || '')}" placeholder="說明"></td>
            <td style="width:30px;text-align:center;"><span style="color:#777;cursor:pointer;" title="拿掉這列" onclick="window._finSet.marginDelRow(${i})">×</span></td>
        </tr>`).join('');
    return `
    <div id="finset-margin-card" style="background:#1b1b1b;border:1px solid #2e2e2e;border-radius:8px;padding:14px 16px;margin-bottom:16px;">
        <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">預期毛利與人力成本（工時預算的基礎）</h3>
        <p style="color:#888;font-size:12px;margin:0 0 12px;">
            每種案（＝專案的<b>案型</b>）一個預期毛利；員工成本以「一天」計。
            專案的<b>工時預算</b>＝合約未稅 ×（1 − 預期毛利）÷ 日成本 × 每日工時 ——
            專案工時的 burn 表會用它給還沒設預算的案一個「建議」，專案檔案頁「改預算」也預帶這個數。</p>
        <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px;">
            <div><label style="display:block;color:#9ca3af;font-size:11px;margin-bottom:3px;">員工成本／天</label>
                 <input id="finset-margin-daily" class="crm-input" type="number" min="1" value="${_margin.daily_cost || ''}" style="width:120px;text-align:right;"></div>
            <div><label style="display:block;color:#9ca3af;font-size:11px;margin-bottom:3px;">每日工時</label>
                 <input id="finset-margin-hpd" class="crm-input" type="number" min="1" max="24" step="0.5" value="${_margin.hours_per_day || 8}" style="width:90px;text-align:right;"></div>
            <div style="color:#6b7280;font-size:11px;">例：合約 105,000（未稅 100,000）、紀實影片 40% → 60,000 ÷ ${fmtNum(_margin.daily_cost || 4000)} × ${_margin.hours_per_day || 8} ＝ ${Math.round(60000 / (_margin.daily_cost || 4000) * (_margin.hours_per_day || 8) * 2) / 2} h</div>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
            <thead><tr style="color:#888;text-align:left;"><th style="padding:4px 6px;width:110px;">類別</th><th style="padding:4px 6px;">項目（案型）</th><th style="padding:4px 6px;width:100px;">預期毛利 %</th><th style="padding:4px 6px;">說明</th><th></th></tr></thead>
            <tbody id="finset-margin-rows">${rows}</tbody>
        </table>
        ${_unifyHtml()}
        <div style="display:flex;gap:8px;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finSet.marginAddRow()">＋ 一列</button>
            <span style="flex:1;"></span>
            <button class="crm-btn crm-btn-primary" onclick="window._finSet.saveMargin(this)">儲存</button>
        </div>
    </div>`;
}

/** 統一案型：CRM 與私帳有好幾套版本（owner 2026-09-03「以我的這個版本為主，不同的讓我設定連結」）。
 *  列出專案在用、但不在上面表裡的案型，每個對到你的版本，一鍵把專案改過去。 */
function _unifyHtml() {
    const canon = (_margin.rows || []).map(r => r.type).filter(Boolean);
    const have = new Set(canon);
    const outside = (_margin.in_use || []).filter(u => u.type && !have.has(u.type));
    if (!outside.length) return `<div style="color:#6ee7b7;font-size:11.5px;margin-top:8px;">專案在用的案型都在你的表裡（CRM 與私帳已統一）。</div>`;
    const opt = (u) => canon.map(t => `<option value="${esc(t)}"${(_margin.aliases || {})[u.type] === t ? ' selected' : ''}>${esc(t)}</option>`).join('');
    return `
    <div style="margin-top:12px;border-top:1px solid #2a2a2a;padding-top:10px;">
        <div style="color:#f59e0b;font-size:12.5px;margin-bottom:6px;"><b>統一案型</b> —— 這些案型在專案上用著，但不在你的表裡（CRM 與私帳各有一套舊版本）。對到你的版本，按套用就把專案改過去。</div>
        <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
            <thead><tr style="color:#888;text-align:left;"><th style="padding:4px 6px;">舊案型</th><th style="padding:4px 6px;width:120px;">幾個案</th><th style="padding:4px 6px;">對到你的版本</th></tr></thead>
            <tbody>${outside.map(u => `<tr>
                <td style="padding:4px 6px;color:#eee;">${esc(u.type)}</td>
                <td style="padding:4px 6px;color:#9ca3af;">${u.count}（${Object.entries(u.entities || {}).map(([e, n]) => `${e === 'mine' ? '私帳' : 'CRM'} ${n}`).join('、')}）</td>
                <td style="padding:4px 6px;"><select class="crm-input" data-unify="${esc(u.type)}" style="max-width:220px;">
                    <option value="">— 先不動 —</option>${opt(u)}</select></td></tr>`).join('')}</tbody>
        </table>
        <div style="display:flex;gap:8px;margin-top:8px;align-items:center;">
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finSet.unifyApply(this)">套用統一（改專案的案型）</button>
            <span style="color:#6b7280;font-size:11px;">沒選的先不動；之後拉進來的舊名也會照對應算毛利。</span>
        </div>
    </div>`;
}
_fs.unifyApply = async (btn) => {
    const aliases = {};
    document.querySelectorAll('#finset-margin-card select[data-unify]').forEach(sel => { if (sel.value) aliases[sel.dataset.unify] = sel.value; });
    if (!Object.keys(aliases).length) { finToast('先把舊案型對到你的版本', true); return; }
    if (!confirm(`把 ${Object.keys(aliases).length} 種舊案型的專案改成你的版本？（CRM 與私帳一起）`)) return;
    btn.disabled = true;
    try {
        const r = await finFetch('/margin-model/unify', { method: 'POST', body: JSON.stringify({ aliases }) });
        finToast(`已改 ${r.changed_total} 個案的案型` + (r.still_outside.length ? `；還有沒對應的：${r.still_outside.join('、')}` : ''));
        _fs.reload();
    } catch (e) { finToast(e.message, true); btn.disabled = false; }
};

/** 表格目前的輸入 → rows（加列／刪列前先收，才不會把打到一半的洗掉）。 */
function _marginReadRows() {
    return [...document.querySelectorAll('#finset-margin-rows tr')].map(tr => {
        const v = f => tr.querySelector(`[data-f="${f}"]`).value;
        return { group: v('group').trim(), type: v('type').trim(), margin_pct: Number(v('margin_pct') || 0), note: v('note').trim() };
    });
}
function _marginRedraw() {
    const card = document.getElementById('finset-margin-card');
    if (card) card.outerHTML = _marginHtml();
}
_fs.marginAddRow = () => {
    _margin.rows = [..._marginReadRows(), { group: '', type: '', margin_pct: 30, note: '' }];
    _margin.daily_cost = Number(document.getElementById('finset-margin-daily')?.value || _margin.daily_cost);
    _margin.hours_per_day = Number(document.getElementById('finset-margin-hpd')?.value || _margin.hours_per_day);
    _marginRedraw();
};
_fs.marginDelRow = (i) => {
    const rows = _marginReadRows(); rows.splice(i, 1); _margin.rows = rows;
    _margin.daily_cost = Number(document.getElementById('finset-margin-daily')?.value || _margin.daily_cost);
    _margin.hours_per_day = Number(document.getElementById('finset-margin-hpd')?.value || _margin.hours_per_day);
    _marginRedraw();
};
_fs.saveMargin = async (btn) => {
    const daily_cost = Number(document.getElementById('finset-margin-daily')?.value || 0);
    const hours_per_day = Number(document.getElementById('finset-margin-hpd')?.value || 8);
    const rows = _marginReadRows().filter(r => r.type);
    if (!(daily_cost > 0)) { finToast('員工成本／天要大於 0', true); return; }
    btn.disabled = true;
    try {
        _margin = await finFetch('/margin-model', { method: 'PUT', body: JSON.stringify({ daily_cost, hours_per_day, rows, aliases: _margin.aliases || {} }) });   // 回應含 in_use
        finToast(`已存：日成本 $${fmtNum(daily_cost)}、${rows.length} 種案型`);
        _marginRedraw();
    } catch (e) {
        finToast(e.message, true);
    } finally { btn.disabled = false; }
};

// ── 類別對映表 ──────────────────────────────────────────────

function _mapHtml() {
    const body = _map.length ? _mapSorter.sorted(_map).map(m => `
        <tr class="finset-map-row" data-source="${esc(m.source)}" data-cat="${esc(m.category_text)}"
            style="border-top:1px solid #2a2a2a;${m.active === false ? 'opacity:.5;' : ''}">
            <td style="padding:6px 10px;">${_srcBadge(m.source)}</td>
            <td style="padding:6px 10px;color:#eee;">${esc(m.category_text)}</td>
            <td style="padding:6px 10px;"><select class="crm-select finset-map-acct" data-orig="${esc(m.account_id ?? '')}"
                onchange="this.closest('tr').dataset.dirty='1'">${_acctOptions(m.account_id, true)}</select></td>
            <td style="padding:6px 10px;"><select class="crm-select finset-map-treat" data-orig="${esc(m.treatment ?? '')}"
                onchange="this.closest('tr').dataset.dirty='1'">${_treatOptions(m.treatment, true)}</select></td>
        </tr>`).join('')
        : '<tr><td colspan="4" style="padding:16px;color:#666;text-align:center;">尚無對映（新的收支類別出現後會進上方待歸類）</td></tr>';

    return `
    <div id="finset-map-card" style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;">
        <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">🧭 類別對映表</h3>
        <p style="color:#888;font-size:12px;margin:0 0 12px;">收支明細的「類別」透過這張表對到財務科目 — 改了下拉記得按儲存。</p>
        <div style="overflow-x:auto;">
            <table style="border-collapse:collapse;font-size:13px;width:100%;min-width:560px;">
                <thead><tr style="color:#888;font-size:12px;text-align:left;">
                    ${sortableTh('source', '來源', 'style="padding:6px 10px;"')}
                    ${sortableTh('cat', '類別文字', 'style="padding:6px 10px;"')}
                    ${sortableTh('acct', '對到的科目', 'style="padding:6px 10px;"')}
                    ${sortableTh('treat', '這算什麼錢', 'style="padding:6px 10px;"')}
                </tr></thead>
                <tbody>${body}</tbody>
            </table>
        </div>
        ${_map.length ? `<div style="margin-top:12px;">
            <button class="crm-btn crm-btn-primary" onclick="window._finSet.saveMap(this)">💾 儲存變更</button>
        </div>` : ''}
    </div>`;
}

_fs.saveMap = async (btn) => {
    const items = [];
    _c.querySelectorAll('.finset-map-row').forEach(row => {
        const acctSel = row.querySelector('.finset-map-acct');
        const treatSel = row.querySelector('.finset-map-treat');
        const changed = row.dataset.dirty === '1'
            && (acctSel.value !== acctSel.dataset.orig || treatSel.value !== treatSel.dataset.orig);
        if (!changed) return;
        if (!acctSel.value || !treatSel.value) return;   // 不送空值
        items.push({
            source: row.dataset.source,
            category_text: row.dataset.cat,
            account_id: acctSel.value,
            treatment: treatSel.value,
        });
    });
    if (!items.length) { finToast('沒有變更', true); return; }
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        const r = await finFetch('/category-map', { method: 'PUT', body: JSON.stringify({ items }) });
        finToast(`已更新 ${fmtNum(r.count ?? items.length)} 筆對映`);
        _fs.reload();
    } catch (e) {
        finToast(e.message, true);
        btn.disabled = false; btn.textContent = '💾 儲存變更';
    }
};

// ── 收支分類樹（私帳專屬）─────────────────────────────────────────
// 正本＝後端 cash_taxonomy_nodes，深度不限。這張卡只在**我的帳**出現：母公司那
// 本用的是上面那張平面的類別對映表，兩套詞彙不同（見 core/cash_taxonomy.py）。
//
// 🔴 改名／搬家在後端是**資料遷移**（收支的三欄鏡射 ＋ finance_category_map 的
// 鍵一起改）。所以動到有資料的節點一律先講清楚影響筆數，做完也把後端實際改了
// 什麼回報出來 —— 靜靜改掉 N 筆帳是最難查的那種變更。

const _taxOpen = new Set();      // 展開的節點（預設只展開第一層）
let _taxAdding = null;           // 正在新增子分類的父節點 id（'' = 第一層），null = 沒有
let _taxFlatAll = [];            // 整棵攤平（每次 _taxHtml 算一次；每列各攤一次是 O(n²)）

function _taxFlat(nodes, out = []) {
    for (const n of nodes) { out.push(n); _taxFlat(n.children, out); }
    return out;
}

function _taxFind(id) { return _taxFlatAll.find(n => n.id === id); }

/** 可以搬去的上層：**同深度**的其他節點（後端也擋，這裡是不給他選錯的）。 */
function _taxMoveOptions(node, parentId) {
    if (node.depth < 2) return '';        // 第一層上面沒東西了，不能搬
    const banned = new Set(_taxFlat([node]).map(n => n.id));
    const opts = _taxFlatAll
        .filter(n => n.depth === node.depth - 1 && !banned.has(n.id))
        .map(n => `<option value="${esc(n.id)}"${n.id === parentId ? ' selected' : ''}>${esc(n.path.join(' ▸ '))}</option>`);
    return opts.length > 1 ? opts.join('') : '';
}

function _taxNewRow(parentId, depth) {
    return `<div style="padding:3px 0 3px ${depth * 20 + 20}px;">
        <input class="crm-input fin-tax-new" data-parent="${esc(parentId)}"
               placeholder="新分類名稱，Enter 建立（Esc 取消）"
               style="font-size:12px;padding:2px 6px;width:230px;"></div>`;
}

/** 筆數：直接掛在它上面的 vs 整支。0/0 就什麼都不印（大部分節點是這樣）。 */
function _taxCountText(n) {
    if (n.count && n.subtree_count > n.count) {
        return `${fmtNum(n.count)} 筆（整支 ${fmtNum(n.subtree_count)}）`;
    }
    if (n.count) return `${fmtNum(n.count)} 筆`;
    return n.subtree_count ? `整支 ${fmtNum(n.subtree_count)} 筆` : '';
}

function _taxRowHtml(n, parentId) {
    const kids = n.children.length;
    const open = _taxOpen.has(n.id);
    const move = _taxMoveOptions(n, parentId);
    const btn = (label, call) =>
        `<button class="crm-btn crm-btn-sm crm-btn-secondary" onclick="window._finSet.${call}">${label}</button>`;
    return `
    <div class="fin-tax-row" data-tax="${esc(n.id)}"
         style="display:flex;align-items:center;gap:6px;padding:2px 0;border-top:1px solid #262626;
                padding-left:${(n.depth - 1) * 20}px;${n.active ? '' : 'opacity:.45;'}">
        <span style="width:14px;color:#666;${kids ? 'cursor:pointer;' : ''}"
              ${kids ? `onclick="window._finSet.taxToggleOpen('${esc(n.id)}')"` : ''}>${kids ? (open ? '▾' : '▸') : ''}</span>
        <span class="tax-name" style="color:#eee;min-width:130px;">${esc(n.name)}</span>
        <span style="color:#666;font-size:11px;min-width:110px;">${_taxCountText(n)}</span>
        ${btn('改名', `taxRename('${esc(n.id)}')`)}
        ${btn('＋子分類', `taxAdd('${esc(n.id)}')`)}
        ${btn(n.active ? '停用' : '啟用', `taxToggle('${esc(n.id)}',${n.active ? 0 : 1})`)}
        ${!kids && !n.subtree_count ? btn('刪除', `taxDelete('${esc(n.id)}')`) : ''}
        ${move ? `<select class="crm-select" style="font-size:11px;padding:1px 4px;max-width:200px;"
                    title="搬到別的上層分類底下"
                    onchange="window._finSet.taxMove('${esc(n.id)}', this.value)">${move}</select>` : ''}
    </div>
    ${_taxAdding === n.id ? _taxNewRow(n.id, n.depth) : ''}
    ${open ? n.children.map(c => _taxRowHtml(c, n.id)).join('') : ''}`;
}

function _taxHtml() {
    if (!_tax.length) return '';
    _taxFlatAll = _taxFlat(_tax);
    return `
    <div id="finset-tax-card" style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
        <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">🌳 收支分類樹</h3>
        <p style="color:#888;font-size:12px;margin:0 0 4px;">
            收支明細那三個分類欄的值域。<b>深度不限</b> —— 需要再細分就往下長一層
            （家用 ▸ 變動支出 ▸ 醫療保健 ▸ 乳癌治療 ▸ 就醫地點）。第四層以後不進
            收支的欄位，只活在路徑裡，所以加層數不會動到科目對映。</p>
        <p style="color:#c9a08a;font-size:12px;margin:0 0 12px;">
            改第一、二層的名字＝改到 <b>category</b> 複合鍵，收支的欄位與科目對映會一起更新。
            有資料的節點請優先用「停用」（歷史照樣看得到，只是新的挑不到）。</p>
        <div>${_tax.map(n => _taxRowHtml(n, '')).join('')}</div>
        ${_taxAdding === '' ? _taxNewRow('', 0) : ''}
        <div style="margin-top:10px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    onclick="window._finSet.taxAdd('')">＋ 新增第一層分類</button>
        </div>
    </div>`;
}

/** 新增列的鍵盤處理 + 自動 focus。_renderShell 每次重畫都要重掛。 */
function _wireTaxNewRow() {
    const inp = _c && _c.querySelector('.fin-tax-new');
    if (!inp) return;
    inp.focus();
    inp.addEventListener('keydown', (k) => {
        if (k.key === 'Escape') { _taxAdding = null; _renderShell(); return; }
        if (k.key !== 'Enter') return;
        const name = inp.value.trim();
        const parent = inp.dataset.parent;
        _taxAdding = null;
        if (!name) { _renderShell(); return; }
        _taxCreate(parent, name);
    });
    inp.addEventListener('blur', () => setTimeout(() => {
        if (_taxAdding !== null && !inp.value.trim()) { _taxAdding = null; _renderShell(); }
    }, 200));
}

_fs.taxToggleOpen = (id) => {
    if (_taxOpen.has(id)) _taxOpen.delete(id); else _taxOpen.add(id);
    _renderShell();
};

_fs.taxAdd = (parentId) => {
    _taxAdding = parentId;
    if (parentId) _taxOpen.add(parentId);
    _renderShell();
};

/** 改名：格內換成 input（Enter 存、Esc 取消）。有資料就先講清楚會動到什麼。 */
_fs.taxRename = (id) => {
    const n = _taxFind(id);
    const cell = _c.querySelector(`.fin-tax-row[data-tax="${id}"] .tax-name`);
    if (!n || !cell || cell.querySelector('input')) return;
    cell.innerHTML = '<input class="crm-input" style="font-size:12px;padding:1px 6px;width:150px;">';
    const inp = cell.querySelector('input');
    inp.value = n.name;
    inp.focus();
    inp.select();
    inp.addEventListener('keydown', (k) => {
        if (k.key === 'Escape') { _renderShell(); return; }
        if (k.key !== 'Enter') return;
        const name = inp.value.trim();
        if (!name || name === n.name) { _renderShell(); return; }
        if (n.subtree_count && n.depth <= 2
            && !confirm(`「${n.name}」改成「${name}」會一併更新 ${fmtNum(n.subtree_count)} 筆收支的`
                        + `類別欄，以及它在科目對映表裡的那一筆。確定？`)) {
            _renderShell(); return;
        }
        _taxSave(id, { name });
    });
};

_fs.taxToggle = (id, active) => {
    const n = _taxFind(id);
    if (!active && n && n.subtree_count
        && !confirm(`停用「${n.name}」？已經記在上面的 ${fmtNum(n.subtree_count)} 筆照樣看得到，`
                    + '只是新的收支挑不到它。')) return;
    _taxSave(id, { active });
};

_fs.taxMove = (id, parentId) => {
    const n = _taxFind(id);
    const tgt = _taxFind(parentId);
    if (!n || !tgt) { _renderShell(); return; }
    if (!confirm(`把「${n.name}」搬到「${tgt.path.join(' ▸ ')}」底下？`
                 + (n.subtree_count ? `會一併更新 ${fmtNum(n.subtree_count)} 筆收支的類別欄。` : ''))) {
        _renderShell(); return;
    }
    _taxSave(id, { parent_id: parentId });
};

_fs.taxDelete = async (id) => {
    const n = _taxFind(id);
    if (!n || !confirm(`刪除分類「${n.name}」？（它底下沒有子分類，也沒有任何收支）`)) return;
    try {
        await crmFetch(`/cash-taxonomy/nodes/${encodeURIComponent(id)}?entity=${finEntity()}`,
                       { method: 'DELETE' });
        finToast('已刪除');
        _fs.reload();
    } catch (e) { finToast(e.message, true); }
};

async function _taxSave(id, body) {
    try {
        const r = await crmFetch(`/cash-taxonomy/nodes/${encodeURIComponent(id)}`, {
            method: 'PUT', body: JSON.stringify({ ...body, entity: finEntity() }),
        });
        const bits = [];
        if (r.rows_remirrored) bits.push(`更新 ${fmtNum(r.rows_remirrored)} 筆收支的分類欄`);
        if (r.category_map_renamed && r.category_map_renamed.length) {
            bits.push('科目對映 ' + r.category_map_renamed.map(x => x[0] + '→' + x[1]).join('、'));
        }
        finToast(bits.length ? '已儲存：' + bits.join('；') : '已儲存');
        _fs.reload();
    } catch (e) {
        finToast(e.message, true);
        _renderShell();
    }
}

async function _taxCreate(parentId, name) {
    try {
        const r = await crmFetch('/cash-taxonomy/nodes', {
            method: 'POST',
            body: JSON.stringify({ parent_id: parentId, name, entity: finEntity() }),
        });
        finToast(r.existed ? `「${name}」本來就有了` : '已新增');
        _fs.reload();
    } catch (e) { finToast(e.message, true); }
}
