/**
 * recon.js — 🔍 對帳系統：上傳對帳單 → 分類規則 → 對帳工作台 → 核對餘額。
 *
 * 2026-08-22 從 banking.js 整段搬出來（**純搬移**，一行邏輯沒改、端點一條沒變）。
 * owner 的原話：「對帳系統按了之後就直接在收支表這裡對帳，不要跳轉到銀行帳戶；
 * 銀行帳戶的這個功能直接移到收支表就可以了。」
 *
 * 所以它不再是「銀行帳戶子視圖的一段」，而是一個**掛得進任何容器**的模組
 * （export default render(container, ctx)）。目前的家是收支明細
 * （crm-cashbook.js 的對帳面板）—— 對帳本來就是在看收支表對銀行的帳，
 * 兩個畫面之間跳來跳去等於要人自己記住剛剛看到什麼。
 *
 * 為什麼是「搬」不是「複製一份」：分類規則、草稿、工作台狀態都只該有一份。
 * 兩個畫面各養一套規則，使用者在 A 改完到 B 看不到效果 —— 那是最容易靜默
 * 錯帳的一種重複（同 crm-cashbook 那顆按鈕原本的註解所述）。
 *
 * 命名空間 window._finRecon（banking.js 仍是 window._finBank，兩邊不重疊）。
 */
import { finFetch, finEntity, esc, fmtNum, finToast, bankOnly } from '../fin-utils.js';
import { createSortable, sortableTh, enumIndex, autoFee as _autoFee }
    from '../../crm/crm-utils.js';   // 匯費容差的正本在共用層（見 crm-utils）
import { bearerHeader } from '../../../js/shared/utils.js';   // 送 FormData 時不能自帶 Content-Type

let _c = null;
let _isCurrent = () => true;
let _accounts = [];       // 銀行帳戶（這一台自己抓，不跟 banking.js 共用狀態）
let _drafts = [];         // 對帳單匯入草稿（掛到一半的）
let _wb = null;           // 對帳工作台：{acct, month, data}；null=未開
let _wbImportRows = null; // 匯入流程暫存：貼上解析後的儲存格陣列
let _stmtPreview = null;  // 對帳單匯入：preview 回來的列（確認後才寫入）
let _reconItems = null;   // 最近一次月結對帳歷史 items（排序重繪用）

const _fr = (window._finRecon = window._finRecon || {});

/** 啟用中的**真銀行**帳戶（排除股東往來）。規則正本在 fin-utils.bankOnly。 */
function _bankOnly() {
    return bankOnly(_accounts);
}

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

/** 掛載：container 可以是子視圖容器，也可以是收支表裡的面板。 */
export default async function render(container, ctx = {}) {
    _c = container;
    _isCurrent = ctx.isCurrent || (() => true);
    container.innerHTML = '<div style="color:#888;padding:20px;">載入中…</div>';
    let bank, drafts;
    try {
        [bank, drafts] = await Promise.all([
            finFetch('/bank-accounts'),
            finFetch('/bank-statement/drafts').catch(() => ({ drafts: [] })),
        ]);
    } catch (e) {
        if (!_isCurrent()) return;
        container.innerHTML = `<div style="color:#f87171;padding:20px;">載入失敗：${esc(e.message)}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                    onclick="window._finRecon.reload()">重試</button></div>`;
        return;
    }
    if (!_isCurrent()) return;
    _accounts = bank.items || [];
    _drafts = drafts.drafts || [];
    _renderShell();
    const sel = _c.querySelector('#finbank-recon-acct');
    if (sel && sel.value) _loadReconHistory(sel.value);
}

_fr.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

function _renderShell() {
    const actives = _bankOnly();
    const reconOpts = actives.map((a, i) =>
        `<option value="${esc(a.id)}"${i === 0 ? ' selected' : ''}>${esc(a.name)}</option>`).join('');
    // 月底對帳預設 = 上個月
    const d = new Date(); d.setMonth(d.getMonth() - 1);
    const defMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

    _c.innerHTML = `
        <!-- 月底對帳（工作台：明細逐筆勾銷 → 最後核對餘額） -->
        <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
            <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">🔍 對帳系統</h3>
            <p style="color:#888;font-size:12px;margin:0 0 10px;">
                銀行的帳從這裡進系統：上傳對帳單 → 自動分類、自動配貸款期別 → 寫進收支明細。
                同一份重傳只會補新的，所以可以每個月固定丟一次。
                分類規則自己設，用久了幾乎不用手動改。</p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;">
                <button class="crm-btn crm-btn-primary" onclick="window._finRecon.stmtOpen()">📄 上傳對帳單</button>
                <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.rulesOpen()">⚙️ 分類規則</button>
            </div>
            ${_draftsStrip()}
            ${actives.length === 0 ? '<div style="color:#888;font-size:13px;">先新增帳戶才能對帳。</div>' : `
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">帳戶</div>
                    <select id="finbank-recon-acct" class="crm-select">${reconOpts}</select></div>
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">月份</div>
                    <input id="finbank-recon-month" type="month" class="crm-input" value="${defMonth}" onchange="window._finRecon.wbTargetChanged()"></div>
                <button class="crm-btn crm-btn-primary" onclick="window._finRecon.wbOpen(this)">📋 開啟對帳工作台</button>
            </div>
            <div id="finbank-wb" style="margin-top:12px;"></div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:14px;padding-top:12px;border-top:1px solid #2a2a2a;">
                <div><div style="color:#9ca3af;font-size:11px;margin-bottom:3px;">核對：對帳單月底餘額</div>
                    <input id="finbank-recon-balance" type="number" class="crm-input" placeholder="照對帳單抄" style="width:150px;"></div>
                <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.reconcile(this)">核對餘額</button>
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
    `;

    const reconSel = _c.querySelector('#finbank-recon-acct');
    if (reconSel) reconSel.addEventListener('change', () => {
        const resEl = _c.querySelector('#finbank-recon-result');
        if (resEl) resEl.innerHTML = '';
        _loadReconHistory(reconSel.value);
        _fr.wbTargetChanged();   // 工作台已開 → 跟著切帳戶
    });
    const m = _c.querySelector('#finbank-wb-modal');
    if (m) m.addEventListener('click', (e) => { if (e.target === m) m.style.display = 'none'; });
}

// ── 對帳 ────────────────────────────────────────────────────

_fr.reconcile = async (btn) => {
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
// 對帳單匯入的 modal 按鈕走 onclick="window._finRecon.…" —— 要真的掛上去，
// 不然按了完全沒反應（inline onclick 看不到模組作用域裡的函式）。
_fr.wbCloseModal = _wbCloseModal;

/** 工作台變更請求共用骨架：打 API →（可選 toast）→（可選關 modal）→ 整台重載。
 *  失敗 toast 錯誤訊息（409 月結鎖帳等直接顯示後端 detail）。 */
async function _wbApi(path, opts, { btn, okMsg, close } = {}) {
    if (btn) btn.disabled = true;
    try {
        const r = await finFetch(path, opts);
        if (okMsg) finToast(typeof okMsg === 'function' ? okMsg(r) : okMsg);
        if (close) _wbCloseModal();
        await _fr.wbReload();
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
_fr.wbTargetChanged = () => { if (_wb) _fr.wbOpen(); };

_fr.wbOpen = async (btn) => {
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

_fr.wbReload = () => _fr.wbOpen();

function _wbLineRow(l) {
    let acts;
    if (l.status === 'matched') {
        const e = (_wb.data.entries || []).find(en => en.id === l.matched_entry_id);
        acts = `<button class="crm-btn crm-btn-secondary crm-btn-sm" title="配對到：${esc(e ? e.summary : '（其他月份的收支）')}" onclick="window._finRecon.wbUnmatch('${l.id}')">取消配對</button>`;
    } else {
        acts = `<button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finRecon.wbMatchOpen('${l.id}')">配對</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finRecon.wbCreateOpen('${l.id}')">補記入帳</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finRecon.wbNoteOpen('${l.id}')">${l.note ? '改註記' : '註記'}</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" title="刪除這列（只刪對帳單明細，不動帳）" onclick="window._finRecon.wbDelLine('${l.id}')">✕</button>`;
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
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finRecon.wbImportOpen()">📥 匯入對帳單明細</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finRecon.wbAddOpen()">＋ 手動新增一列</button>
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finRecon.wbAutoMatch(this)">⚡ 自動配對</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finRecon.wbReload()">🔄 重新整理</button>
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

_fr.wbAutoMatch = (btn) => _wbApi('/statement-lines/auto-match', {
    method: 'POST', body: JSON.stringify({ bank_account_id: _wb.acct, month: _wb.month }),
}, { btn, okMsg: r => r.matched ? `自動配對成功 ${r.matched} 筆` : '沒有可自動配對的（金額相同且日期相近才會自動配）' });

_fr.wbUnmatch = (lineId) => _wbApi(`/statement-lines/${lineId}/unmatch`, { method: 'POST' });

_fr.wbDelLine = (lineId) => {
    if (!confirm('刪除這列對帳單明細？（只刪工作底稿，收支明細不動）')) return;
    _wbApi(`/statement-lines/${lineId}`, { method: 'DELETE' });
};

// ── 工作台：手動新增一列 ─────────────────────────────────────

_fr.wbAddOpen = () => {
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
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.wbAddSave(this)">新增</button>
        </div>`);
};

_fr.wbAddSave = (btn) => {
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

_fr.wbImportOpen = () => {
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
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.wbImportParse(this)">下一步：確認明細</button>
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

_fr.wbImportParse = async (btn) => {
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
            <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.wbImportOpen()">← 重貼</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.wbImportSave(this)">匯入</button>
        </div>`);
}

_fr.wbImportSave = async (btn) => {
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

_fr.wbMatchOpen = (lineId) => {
    const ln = _wbLine(lineId);
    if (!ln) return;
    const cands = (_wb.data.entries || []).filter(e => !e.matched && e.amount === ln.amount);
    const rows = cands.map(e => `<tr style="border-top:1px solid #2a2a2a;">
        ${_wbEntryCells(e)}
        <td style="padding:4px 8px;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finRecon.wbMatchPick('${lineId}','${e.id}')">選這筆</button></td>
    </tr>`).join('');
    _wbModal('配對到系統收支', `
        ${_wbBanner(ln)}
        ${cands.length ? `${_wbTable(`<tbody>${rows}</tbody>`, 320)}
        <div style="color:#666;font-size:11px;margin-top:6px;">只列同金額且未勾銷的收支（金額不同不能配 — 漏記請用「補記入帳」）。</div>`
        : '<div style="color:#888;font-size:13px;border:1px dashed #333;border-radius:6px;padding:14px;">這個月沒有同金額的未勾銷收支。若系統確實漏記，關掉這個視窗改按「補記入帳」；若是跨月時間差，用「註記」寫明。</div>'}`);
};

_fr.wbMatchPick = (lineId, entryId) => _wbApi(`/statement-lines/${lineId}/match`, {
    method: 'POST', body: JSON.stringify({ entry_id: entryId }),
}, { close: true });

_fr.wbNoteOpen = (lineId) => {
    const ln = _wbLine(lineId);
    if (!ln) return;
    _wbModal('註記（不入帳的說明）', `
        ${_wbBanner(ln)}
        <p style="color:#888;font-size:12px;margin:0 0 8px;">這筆銀行有、但不需要（或不是這個月）入系統帳時，寫清楚原因 — 例如「上月已入帳，跨月入帳時間差」。</p>
        <textarea id="finbank-wb-note" class="crm-input" rows="3" style="width:100%;box-sizing:border-box;">${esc(ln.note || '')}</textarea>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.wbNoteSave(this,'${lineId}')">儲存</button>
        </div>`);
};

_fr.wbNoteSave = (btn, lineId) => _wbApi(`/statement-lines/${lineId}`, {
    method: 'PUT',
    body: JSON.stringify({ note: document.getElementById('finbank-wb-note')?.value || '' }),
}, { btn, close: true });

_fr.wbCreateOpen = async (lineId) => {
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
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.wbCreateSave(this,'${lineId}')">補記並勾銷</button>
        </div>`);
};

_fr.wbCreateSave = async (btn, lineId) => {
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
// ── 銀行對帳單匯入（上傳/貼上 → 逐列確認 → 寫帳）──────────────
//
// 為什麼要「預覽再確認」而不是解析完直接匯：對帳單解析得再穩，把錢寫進帳這件事
// 都該由人按下最後一步。預覽把每一列的判斷攤開（分類、配到哪筆貸款哪一期、是不是
// 已經匯過），錯的當場取消勾選 —— 不做「全自動但你不知道它做了什麼」。

_fr.stmtOpen = async () => {
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
            <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.wbCloseModal()">取消</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.stmtParse(this)">解析看看</button>
        </div>`);
};

_fr.stmtParse = async (btn) => {
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
                onclick="window._finRecon.draftOpen('${esc(d.id)}', this)">接著做</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                onclick="window._finRecon.draftDelete('${esc(d.id)}', this)">刪除</button>
        </div>`).join('')}
    </div>`;
}

_fr.draftOpen = async (id, btn) => {
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
 *  _fr.reload() 是整個子頁重畫：5 支 API + 重建 shell，還會把使用者開著的
 *  對帳工作台一起收掉 —— 而存一份草稿只改了這一條。 */
async function _refreshDrafts() {
    try { _drafts = (await finFetch('/bank-statement/drafts')).drafts || []; }
    catch (_) { _drafts = []; }
    const el = _c && _c.querySelector('#finbank-drafts');
    if (el) el.outerHTML = _draftsStrip();
    else _fr.reload();          // 本來沒有草稿區（清單原本是空的）→ 只能整個重畫
}

_fr.draftDelete = async (id, btn) => {
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

_fr.stmtSaveDraft = async (btn) => {
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
            onchange="window._finRecon.stmtPick(${i}, this.checked)" ${r.selected ? 'checked' : ''}></td>
        <td style="padding:4px 6px;color:#ccc;white-space:nowrap;">${esc(r.date)}</td>
        <td style="padding:4px 6px;color:#ddd;" title="${esc(r.description || '')}">${esc(r.description || '')}</td>
        <td style="padding:4px 6px;text-align:right;white-space:nowrap;color:${isOut ? '#fca5a5' : '#86efac'};">
            ${isOut ? '-' : '+'}$${fmtNum(Math.abs(r.amount))}</td>
        <td style="padding:4px 6px;white-space:nowrap;">
            <select class="crm-select crm-select-sm"
                    onchange="window._finRecon.stmtCatChanged(${i}, this.value)">
                ${_stmtCatOptions(r.category)}
            </select>
            <button type="button" title="把「${esc((r.description || '').slice(0, 12))} → 這個類別」存成規則，以後自動套用"
                    onclick="window._finRecon.stmtSaveRule(${i})"
                    style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:12px;padding:0 2px;">＋規則</button>
        </td>
        <td style="padding:4px 6px;">${_stmtProjCell(r, i)}</td>
        <td style="padding:4px 6px;">${_stmtAllocCell(r, i)}</td>
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
_fr.stmtCatChanged = (i, v) => {
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
_fr.stmtSaveRule = async (i) => {
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
// 方向條件的詞彙只有這一份 —— 下拉與表格欄位都從它生。
// （CLAUDE.md 規則 D 點名的那個陷阱：選項寫在 .js、標籤寫在模板，各一份，
//  加一個值時漏改哪一邊都不會有任何失敗訊號。）
const _DIR_OPTS = [[0, '不限'], [1, '只存入'], [-1, '只支出']];

function _dirLabel(v) {
    const hit = _DIR_OPTS.find(([x]) => x === (parseInt(v, 10) || 0));
    return hit ? hit[1] : '不限';
}

function _acctName(id) {
    const a = _accounts.find(x => String(x.id) === String(id));
    return a ? (a.name || '') : String(id).slice(0, 8);
}

_fr.rulesOpen = async () => {
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
            <td style="padding:4px 6px;color:#9ca3af;font-size:11px;">${
                _dirLabel(r.only_direction)}</td>
            <td style="padding:4px 6px;text-align:right;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm"
                        onclick="window._finRecon.ruleToggle('${r.id}', ${r.active ? 'false' : 'true'})">${
                    r.active ? '停用' : '啟用'}</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm"
                        onclick="window._finRecon.ruleDelete('${r.id}')">刪除</button>
            </td>
        </tr>`).join('');
    _wbModal('分類規則', `
        <p style="color:#888;font-size:12px;margin:0 0 10px;">
            對帳單的摘要包含「關鍵字」就自動歸到那個類別。
            <b>綁定帳戶的規則優先於「所有帳戶」</b> —— 合庫寫「攤還本息」、一銀寫
            「中小７月」，同一件事兩種寫法，綁帳戶才不會互相誤觸。
            由上而下比對，先命中的先贏。<br>
            <b>方向</b>：同一個關鍵字兩個方向是不同類別時用它 —— 例如「薪資」，
            股東匯進來是<b>代收薪資</b>、公司發給員工是<b>代發薪資</b>。
            不限的規則排在前面會蓋掉方向規則，所以方向規則的順序要排前面。</p>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:flex-end;margin-bottom:10px;">
            <div><div style="color:#9ca3af;font-size:11px;">關鍵字</div>
                <input id="rule-kw" class="crm-input" style="width:130px;" placeholder="摘要含這串"></div>
            <div><div style="color:#9ca3af;font-size:11px;">歸到類別</div>
                <select id="rule-cat" class="crm-select">${catOpts}</select></div>
            <div><div style="color:#9ca3af;font-size:11px;">適用帳戶</div>
                <select id="rule-acct" class="crm-select">${acctOpts}</select></div>
            <div><div style="color:#9ca3af;font-size:11px;">方向</div>
                <select id="rule-dir" class="crm-select">${_DIR_OPTS.map(
                    ([v, t]) => `<option value="${v}">${t}</option>`).join('')}</select></div>
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.ruleAdd(this)">新增</button>
        </div>
        <div id="rule-err" style="display:none;color:#fca5a5;font-size:12px;margin-bottom:6px;"></div>
        ${_wbTable(`<thead><tr>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">關鍵字</th>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">類別</th>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">適用帳戶</th>
            <th style="padding:4px 6px;text-align:left;color:#9ca3af;font-weight:500;font-size:11px;">方向</th>
            <th></th></tr></thead><tbody>${rows}</tbody>`, 300)}
        <div style="display:flex;gap:8px;justify-content:space-between;align-items:center;margin-top:12px;padding-top:10px;border-top:1px solid #2a2a2a;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.rulesApplyUnclassified(this)"
                    title="只碰 category 是空的列 —— 已經有類別的（不管規則分的還是人手改的）一律不動">
                套用到未歸類的歷史列</button>
            <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.wbCloseModal()">關閉</button>
        </div>`);
}

_fr.ruleAdd = async (btn) => {
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
            only_direction: parseInt(document.getElementById('rule-dir').value, 10),
            sort_order: 50, active: true, note: '手動新增' }) });
        await _fr.rulesOpen();
    } catch (e) {
        show(e.message);
        btn.disabled = false;
    }
};

_fr.ruleToggle = async (id, active) => {
    const r = _rules.find(x => x.id === id);
    if (!r) return;
    await finFetch('/import-rules/' + id, { method: 'PUT', body: JSON.stringify({
        keyword: r.keyword, category: r.category,
        bank_account_id: r.bank_account_id || null,
        sort_order: r.sort_order, active, note: r.note }) });
    await _fr.rulesOpen();
};

_fr.ruleDelete = async (id) => {
    const r = _rules.find(x => x.id === id);
    if (!confirm(`刪除規則「${r ? r.keyword : id}」？`)) return;
    await finFetch('/import-rules/' + id, { method: 'DELETE' });
    await _fr.rulesOpen();
};

_fr.rulesApplyUnclassified = async (btn) => {
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
                        onclick="window._finRecon.pickClose()">&#x2715;</button>
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

_fr.pickClose = () => {
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

/** 挑選清單的欄寬 —— 表頭與資料列**共用同一組**，各寫一份一定會歪。
 *  原本一列是兩行、欄位用「·」串起來，數字沒有對齊，一頁十幾張很難掃。 */
const _GRID = (cols) => 'display:grid;grid-template-columns:'
    + cols + ';align-items:center;gap:7px;';
const _INV_GRID = _GRID('20px 112px minmax(0,1fr) 84px 140px 92px 88px 88px 100px 88px');
const _PAY_GRID = _GRID('20px minmax(0,1fr) 110px 84px 96px 96px 92px 92px 100px');

/* ── 兩側：收入列掛發票、支出列掛請款單 ────────────────────────────────
 *
 * 一列的方向決定它能掛什麼（發票是收入側、請款單是支出側，互斥），所以兩者
 * 共用同一格、同一個挑選視窗 —— 差異全部收在這張表裡。後端 _ALLOC_KINDS 與
 * 收支明細的關聯面板都是同一個做法。
 *
 * 🔴 `perItemFee` 是兩側**唯一真正的結構差異**，不是還沒做完：
 *    · 收款：匯出行對「每一張發票的匯款」各扣一次 → 逐列一格匯費
 *    · 付款：跨行手續費對「這一筆匯出」收一次，涵蓋幾張請款單都一樣
 *      → 整列一個（欄位在 r.payment_fee，同 CashPaymentLinksPayload.fee）
 */
const _SIDES = {
    inv: {
        noun: '發票', dir: 1, field: 'invoices', idKey: 'invoice_id',
        src: 'invoices', idx: 'inv', grid: _INV_GRID, perItemFee: true,
        empty: '＋ 選發票', hint: '挑發票（可複選：一筆匯款拆給多張）',
        heads: [['發票號'], ['名稱'], ['日期'], ['公司'],
            ['面額', 1], ['已收', 1], ['尚欠', 1]],
        hay: v => [v.invoice_number, v.title, v.company_name],
        code: v => v.invoice_number || '無號',
        name: v => v.title || '',
        cells: (v, hit) => [
            [esc(v.invoice_number || '無號'), 'color:#ddd;'],
            [esc(v.title || '') + hit, 'color:#eee;'],
            [esc(v.date || ''), 'color:#9ca3af;'],
            [esc(v.company_name || ''), 'color:#9ca3af;'],
            ['$' + fmtNum(v.amount_total || 0), 'color:#ccc;text-align:right;'],
            ['$' + fmtNum(v.collected || 0), 'color:#9ca3af;text-align:right;'],
            ['$' + fmtNum(v.outstanding || 0), 'color:#fbbf24;text-align:right;'],
        ],
        searchHint: '搜尋發票號／抬頭／客戶…',
        blurb: '一筆匯款可以拆給多張發票（合併匯款）。金額接近這筆入帳的排在前面。<br>'
            + '面額減掉分配金額還有幾十塊時會自動填進「匯費」（被匯出行扣走的），'
            + '發票照樣算收齊 —— 那格可以自己改。',
    },
    pay: {
        noun: '請款單', dir: -1, field: 'payments', idKey: 'payment_request_id',
        src: 'payment_requests', idx: 'pay', grid: _PAY_GRID, perItemFee: false,
        empty: '＋ 選請款單', hint: '挑請款單（可複選：出納把一個人的多張併成一筆匯出）',
        heads: [['摘要'], ['收款人'], ['日期'], ['類別'],
            ['金額', 1], ['已付', 1], ['未付', 1]],
        hay: v => [v.summary, v.payee_name, v.category],
        code: v => v.summary || '無摘要',
        name: v => v.payee_name || '',
        cells: (v, hit) => [
            [esc(v.summary || '') + hit, 'color:#eee;'],
            [esc(v.payee_name || ''), 'color:#ddd;'],
            [esc(v.date || ''), 'color:#9ca3af;'],
            [esc(v.category || '') + (v.is_advance
                ? ' <span style="color:#c084fc;font-size:10px;">預支</span>' : ''),
                'color:#9ca3af;'],
            ['$' + fmtNum(v.amount_total || 0), 'color:#ccc;text-align:right;'],
            ['$' + fmtNum(v.paid || 0), 'color:#9ca3af;text-align:right;'],
            ['$' + fmtNum(v.outstanding || 0), 'color:#fbbf24;text-align:right;'],
        ],
        searchHint: '搜尋摘要／收款人／類別…',
        blurb: '一筆匯出可以拆給多張請款單（出納統一匯款）。金額接近這筆支出的排在前面。<br>'
            + '跨行手續費是對<b>整筆匯出</b>收一次（不是每張請款單各一筆），'
            + '所以匯費那格在下面只有一個。',
    },
};

/** 這一列該用哪一側？金額的方向決定，沒有第二個判準。 */
const _sideOf = (r) => (r.amount > 0 ? _SIDES.inv : r.amount < 0 ? _SIDES.pay : null);

/** 搜尋框輸入 → 只重畫清單（不重畫整個視窗，不然游標會跳掉）。 */
_fr.pickSearch = (q) => {
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
            onclick="window._finRecon.stmtPickProj(${i})"
            title="${p ? esc(p.name) : '挑一個專案'}"
            style="width:100%;text-align:left;font-size:11px;padding:3px 6px;
                   ${p ? '' : 'color:#6b7280;'}overflow:hidden;text-overflow:ellipsis;
                   white-space:nowrap;">${label}</button>`;
}

_fr.stmtPickProj = (i) => {
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
                return `<div onclick="window._finRecon.pickProjTake('${esc(p.id)}')"
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
            _fr.pickClose();
            _stmtRefreshRow(i);
        },
    };
    _pickModal(`挑專案　—　${r.date}　${(r.description || '').slice(0, 24)}`, `
        <input id="finbank-pick-search" class="crm-input" placeholder="搜尋專案／客戶…"
               oninput="window._finRecon.pickSearch(this.value)"
               style="width:100%;margin-bottom:8px;">
        <div style="max-height:50vh;overflow:auto;border:1px solid #2e2e2e;border-radius:6px;"
             id="finbank-pick-list">${_pick.render()}</div>
        <div style="display:flex;justify-content:space-between;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary"
                    onclick="window._finRecon.pickProjTake('')">不掛專案</button>
            <button class="crm-btn crm-btn-secondary"
                    onclick="window._finRecon.pickClose()">取消</button>
        </div>`);
};

_fr.pickProjTake = (id) => { if (_pick) _pick.take(id); };

/* ── 發票 ─────────────────────────────────────────────────────────── */

/** 預覽的發票／專案查表。整張表共用一份 —— 逐格重建的話，60 列 × 400 張發票
 *  就是兩萬多次無謂的迴圈（而且每次逐列重畫又來一遍）。
 *  key 綁在 _stmtPreview 物件本身，換一份預覽自然失效。 */
let _stmtIdx = null;
function _stmtIndex() {
    if (_stmtIdx && _stmtIdx.src === _stmtPreview) return _stmtIdx;
    const inv = {}, proj = {}, pay = {};
    ((_stmtPreview && _stmtPreview.invoices) || []).forEach(v => { inv[v.id] = v; });
    ((_stmtPreview && _stmtPreview.projects) || []).forEach(x => { proj[x.id] = x; });
    ((_stmtPreview && _stmtPreview.payment_requests) || []).forEach(x => { pay[x.id] = x; });
    _stmtIdx = { src: _stmtPreview, inv, proj, pay };
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
    // 支出列的鏡像。同樣用 `|| []` —— 後端那兩欄是 List 不收 null（見上面那段
    // 已經發生過一次的事故）。payment_fee 則相反：null ＝不認列，不能寫成 0
    // （0 在關聯面板那條路是「把匯費清掉」的意思）。
    payments: x.payments || [],
    payment_fee: (x.payment_fee === 0 || x.payment_fee) ? x.payment_fee : null,
});

/** 分配格：收入列掛發票、支出列掛請款單、零元列兩者皆非。
 *  兩側共用同一格 —— 方向互斥，永遠不會兩個都要顯示。 */
function _stmtAllocCell(r, i) {
    const S = _sideOf(r);
    if (!S) return '<span style="color:#4b5563;font-size:11px;">—</span>';
    const allocs = r[S.field] || [];
    const byId = _stmtIndex()[S.idx];
    let label = S.empty;
    let title = S.hint;
    if (allocs.length === 1) {
        const v = byId[allocs[0][S.idKey]];
        label = esc((v && (S.name(v) || S.code(v))) || `已選 1 張`);
        title = v ? `${S.code(v)} ${S.name(v)}` : '';
    } else if (allocs.length > 1) {
        label = `${allocs.length} 張　$${fmtNum(allocs.reduce((t, a) => t + a.amount, 0))}`;
        title = allocs.map(a => {
            const v = byId[a[S.idKey]];
            return `${v ? S.code(v) : '？'} $${fmtNum(a.amount)}`;
        }).join('\n');
    }
    return `<button type="button" class="crm-btn crm-btn-secondary"
            onclick="window._finRecon.stmtPickAlloc(${i})" title="${esc(title)}"
            style="width:100%;text-align:left;font-size:11px;padding:3px 6px;
                   ${allocs.length ? '' : 'color:#6b7280;'}overflow:hidden;
                   text-overflow:ellipsis;white-space:nowrap;">${label}</button>`;
}

_fr.stmtPickAlloc = (i) => {
    const r = _stmtPreview && _stmtPreview.rows[i];
    if (!r) return;
    const S = _sideOf(r);
    if (!S) return;
    const target = Math.abs(r.amount);
    const list = (_stmtPreview[S.src] || []).slice();
    // 金額接近的排前面 —— 對一筆入帳來說，「尚欠剛好等於這個數」的那張幾乎
    // 一定就是答案，讓它不用搜尋就在第一行。同差距時日期近的優先。
    list.sort((a, b) => (Math.abs((a.outstanding || 0) - target)
                       - Math.abs((b.outstanding || 0) - target))
                     || String(b.date || '').localeCompare(String(a.date || '')));
    // sel: id -> { amt, fee }
    //   amt = 這張分到多少**現金**（加總後要對上這列的金額）
    //   fee = 被銀行扣掉、沒有真的進出帳戶的那幾十塊（只有收款側逐張有）
    // 存回去時發票拿到的是 amt + fee（那才是客戶實際付的），見 take()。
    const sel = new Map((r[S.field] || []).map(
        a => [a[S.idKey], { amt: (a.amount || 0) - (a.fee || 0), fee: a.fee || 0 }]));

    _pick = {
        S,
        q: '',
        sel,
        // 付款側的匯費是整列一個（跨行手續費對這筆匯出收一次）—— 收款側不用這個欄位
        fee: S.perItemFee ? 0 : Math.max(0, Math.round(Number(r.payment_fee) || 0)),
        /** k='amt' ＝分到的現金（要對上這列金額）、k='fee' ＝被銀行扣掉的。 */
        total(k = 'amt') { let t = 0; this.sel.forEach(a => { t += (a[k] || 0); }); return t; },
        /** 這一列總共認列多少匯費 —— 兩側的形狀不同，只有這裡要知道差別。 */
        feeTotal() { return this.S.perItemFee ? this.total('fee') : this.fee; },
        remain() { return Math.max(0, target - this.total()); },
        render() {
            const S2 = this.S;
            const all = list.filter(v => !this.q
                || _pickHay(...S2.hay(v)).includes(this.q));
            if (!all.length) {
                return `<div style="color:#6b7280;font-size:12px;padding:14px;">找不到符合的${S2.noun}</div>`;
            }
            // 只畫前 100 張。清單已經照「跟這筆金額的差距」排好，第 100 名之後
            // 不可能是答案；而每列是多格 grid，四百張要拼幾百 KB 的字串、生幾千個
            // 節點 —— 打一個字就重來一次。
            const rows = all.slice(0, _PICK_MAX);
            const more = all.length - rows.length;
            const tail = more > 0
                ? `<div style="color:#6b7280;font-size:11px;padding:8px 10px;">
                     還有 ${fmtNum(more)} 張沒顯示 —— 繼續輸入縮小範圍</div>`
                : '';
            return rows.map(v => {
                const on = this.sel.has(v.id);
                const hit = (v.outstanding || 0) === target
                    ? ' <span style="color:#86efac;font-size:10px;">◀ 金額吻合</span>' : '';
                const cell = ([html, extra]) =>
                    `<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${extra || ''}">${html}</div>`;
                const feeCell = S2.perItemFee ? `
                    <input class="crm-input" type="number" ${on ? '' : 'disabled'}
                        value="${on ? (this.sel.get(v.id).fee || '') : ''}"
                        onclick="event.stopPropagation();"
                        onchange="window._finRecon.pickFee('${esc(v.id)}', this.value)"
                        title="被匯出行扣掉、沒進帳戶的部分。面額減分配金額有餘額時自動帶入，可以改。"
                        placeholder="0"
                        style="text-align:right;font-size:12px;padding:3px 6px;min-width:0;
                               ${on && this.sel.get(v.id).fee ? 'color:#fbbf24;' : ''}">` : '';
                return `<div style="${S2.grid}padding:5px 10px;border-bottom:1px solid #2a2a2a;
                        cursor:pointer;${on ? 'background:#1e3a2a;' : ''}"
                        onclick="window._finRecon.pickToggle('${esc(v.id)}', ${on ? 'false' : 'true'})">
                    <input type="checkbox" ${on ? 'checked' : ''} style="pointer-events:none;">
                    ${S2.cells(v, hit).map(cell).join('')}
                    <input class="crm-input" type="number" ${on ? '' : 'disabled'}
                        value="${on ? this.sel.get(v.id).amt : ''}"
                        onclick="event.stopPropagation();"
                        onchange="window._finRecon.pickAmt('${esc(v.id)}', this.value)"
                        title="這張分到多少現金（加總要對上這列的金額）"
                        style="text-align:right;font-size:12px;padding:3px 6px;min-width:0;">
                    ${feeCell}
                </div>`;
            }).join('') + tail;
        },
        foot() {
            const S2 = this.S;
            const t = this.total();
            const diff = target - t;
            const fee = this.feeTotal();
            // 付款側的匯費是一格輸入（整筆匯出收一次），收款側是逐列、這裡只顯示合計
            const feeBox = S2.perItemFee ? '' : `
                <span style="margin-left:10px;">匯費
                <input class="crm-input" type="number" id="finbank-pick-fee"
                       value="${this.fee || ''}" placeholder="0"
                       onchange="window._finRecon.pickRowFee(this.value)"
                       title="這筆匯出被扣的跨行手續費。總流出不變 —— 從支出搬進手續費欄。"
                       style="width:82px;text-align:right;font-size:12px;padding:2px 6px;
                              ${this.fee ? 'color:#fbbf24;' : ''}"></span>`;
            return `已分配 <b style="color:#eee;">$${fmtNum(t)}</b>
                ／ 這列${S2.dir > 0 ? '入帳' : '支出'} $${fmtNum(target)}
                ${diff === 0
                    ? '<span style="color:#86efac;">　剛好對上</span>'
                    : `<span style="color:#fbbf24;">　${diff > 0 ? '還差' : '超出'} $${fmtNum(Math.abs(diff))}</span>`}
                ${fee && S2.perItemFee
                    ? `<span style="color:#fbbf24;">　＋匯費 $${fmtNum(fee)}（${S2.noun}認列 $${fmtNum(t + fee)}）</span>`
                    : ''}${feeBox}`;
        },
        redraw() {
            const el = document.getElementById('finbank-pick-list');
            if (el) el.innerHTML = this.render();
            const f = document.getElementById('finbank-pick-foot');
            if (f) f.innerHTML = this.foot();
        },
        // 🔴 允許不等於這列金額就關掉 —— 少匯、多匯、手續費都會讓它對不齊，
        // 擋下來只會逼人亂填。差額用顏色提醒，寫進去的是使用者填的數。
        take() {
            const S2 = this.S;
            const allocs = [];
            // 🔴 收款側送出去的 amount 是 amt + fee —— 那是**客戶實際付的**，也是
            //    發票該被認列收到的金額（被扣掉的匯費不該讓發票變成沒收齊）。
            //    付款側沒有逐張的 fee，amt 就是分配額。
            // 這裡的 a 是 sel 的值（{amt, fee}），不是後端送的物件 —— 名字刻意
            // 不叫 v：這一段裡 v 一律指候選物件，混用會讓「前端讀了後端沒送的
            // 欄位」那條測試看不出差別（tests/unit/test_stmt_picker.py）。
            this.sel.forEach((a, id) => {
                const amount = Math.round((a.amt || 0) + (S2.perItemFee ? (a.fee || 0) : 0));
                if (amount > 0) {
                    const one = { amount };
                    one[S2.idKey] = id;
                    if (S2.perItemFee) one.fee = Math.round(a.fee || 0);
                    allocs.push(one);
                }
            });
            // 只有分配表一種表示法 —— 主要那張（金額最大）由後端從分配表推，
            // 前端不留一份推導值
            r[S2.field] = allocs;
            if (!S2.perItemFee) {
                // null ＝不認列（不動 bank_fee）。0 在關聯面板那條路是「清掉」，
                // 這裡是新建的列、沒有舊值要清，所以沒掛任何單就送 null。
                r.payment_fee = allocs.length && this.fee ? this.fee : null;
            }
            _fr.pickClose();
            _stmtRefreshRow(i);
        },
    };
    const th = ([t, right]) =>
        `<div style="color:#9ca3af;font-size:11px;${right ? 'text-align:right;' : ''}">${t}</div>`;
    _pickModal(`挑${S.noun}　—　${r.date}　${S.dir > 0 ? '入帳' : '支出'} $${fmtNum(target)}`, `
        <input id="finbank-pick-search" class="crm-input" placeholder="${S.searchHint}"
               oninput="window._finRecon.pickSearch(this.value)"
               style="width:100%;margin-bottom:8px;">
        <div style="color:#6b7280;font-size:11px;margin-bottom:6px;">${S.blurb}</div>
        <div style="border:1px solid #2e2e2e;border-radius:6px;overflow:hidden;">
            <div style="${S.grid}padding:6px 10px;background:#242424;
                        border-bottom:1px solid #2e2e2e;">
                <div></div>${S.heads.map(th).join('')}${th(['分配金額', 1])}${
                    S.perItemFee ? th(['匯費', 1]) : ''}
            </div>
            <div style="max-height:46vh;overflow:auto;"
                 id="finbank-pick-list">${_pick.render()}</div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:10px;">
            <div id="finbank-pick-foot" style="color:#9ca3af;font-size:12px;">${_pick.foot()}</div>
            <div style="display:flex;gap:8px;">
                <button class="crm-btn crm-btn-secondary"
                        onclick="window._finRecon.pickClose()">取消</button>
                <button class="crm-btn crm-btn-primary"
                        onclick="window._finRecon.pickTake()">確定</button>
            </div>
        </div>`, { width: S.perItemFee ? 1180 : 1080 });
};

/** 目前這一側的候選物件（挑選視窗開著時才有意義）。 */
const _pickCand = (id) => {
    const S = _pick && _pick.S;
    return (S && (_stmtPreview[S.src] || []).find(x => x.id === id)) || {};
};

_fr.pickToggle = (id, on) => {
    if (!_pick || !_pick.sel) return;
    if (on) {
        const v = _pickCand(id);
        // 預設帶「這張還欠多少」與「這列還沒分配掉多少」的較小者 ——
        // 兩個都是使用者本來就要算的數，先算好比留空白省事。
        const amt = Math.min(v.outstanding || 0, _pick.remain()) || (v.outstanding || 0);
        _pick.sel.set(id, {
            amt,
            fee: _pick.S.perItemFee ? _autoFee(v.outstanding, amt) : 0,
        });
    } else {
        _pick.sel.delete(id);
    }
    _pick.redraw();
};

/** 改一列的 {amt, fee}。三個 handler 本來各自 guard 一次 _pick 又各自重建整個 pair。 */
const _setSel = (id, patch) => {
    if (!_pick || !_pick.sel) return;
    const cur = _pick.sel.get(id);
    if (!cur) return;
    _pick.sel.set(id, { ...cur, ...patch });
    _pick.redraw();
};

_fr.pickAmt = (id, v) => {
    const amt = Math.max(0, Math.round(Number(v) || 0));
    // 改分配金額 -> 匯費重算。手動填的匯費要留住的話別再動這格
    //（owner 2026-08-23：「自動幫我填寫匯費，格子我可以修改調整」）。
    // 付款側沒有逐張匯費，只更新金額。
    _setSel(id, _pick && _pick.S.perItemFee
        ? { amt, fee: _autoFee(_pickCand(id).outstanding, amt) }
        : { amt });
};

_fr.pickFee = (id, v) =>
    _setSel(id, { fee: Math.max(0, Math.round(Number(v) || 0)) });

/** 付款側：整列一個的匯費（跨行手續費）。 */
_fr.pickRowFee = (v) => {
    if (!_pick) return;
    _pick.fee = Math.max(0, Math.round(Number(v) || 0));
    const f = document.getElementById('finbank-pick-foot');
    // 只重畫 foot —— 重畫整個視窗會讓正在打字的那格失焦
    if (f) f.innerHTML = _pick.foot();
};

_fr.pickTake = () => { if (_pick) _pick.take(); };

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
                    ${th('金額', 'right')}${th('分類')}${th('專案')}${th('發票／請款單')}${th('貸款期別')}${th('')}
                </tr></thead>
                <tbody id="finbank-stmt-tbody">${(d.rows || []).map(_stmtRow).join('')}</tbody>
            </table>
        </div>
        <div id="finbank-stmt-err" style="display:none;color:#fca5a5;font-size:12px;margin:6px 0;white-space:pre-wrap;"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.wbCloseModal()">取消</button>
            <button class="crm-btn crm-btn-secondary" onclick="window._finRecon.stmtSaveDraft(this)"
                    title="掛到一半先收起來，之後在對帳系統接著做">存成草稿</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finRecon.stmtApply(this)">匯入勾選的列</button>
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
_fr.stmtPick = (i, checked) => {
    if (_stmtPreview && _stmtPreview.rows[i]) _stmtPreview.rows[i].selected = !!checked;
};

_fr.stmtApply = async (btn) => {
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
        _fr.reload();
    } catch (e) {
        err.textContent = e.message;
        err.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '匯入勾選的列';
    }
};

