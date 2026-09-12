/**
 * views/ledger-cash.js — 士源帳本「收支」分頁：記一筆（收入／支出）＋近期清單＋抽屜改／刪。
 *
 * 寫入**只走** /api/v1/crm/cash-entries（私帳已收是增量制：_sync_mine_project_received 在那支端點裡；
 * 這裡沒有、也不准有直接寫 amount_received 的路）。
 * 家用分頁（ledger-household.js）跟這裡共用表單／卡片／抽屜 —— 差別只在分類子樹與固定支出。
 */
import { mfetch, toast, esc, money, todayLocal, fmtDate } from '../shell.js';
import { state, openSheet, closeSheet, segHtml, mountSeg, pickerHtml, mountPicker, selectOpts, skeleton, emptyBox, errBox, withBusy, shouldLoad, markStale } from '../ui.js';

const API = '/api/v1/crm/cash-entries';
const DAYS = 90;            // 清單一次抓近 90 天，「載入更早」再往前推 90 天
const KIND = { deposit: '收入', expense: '支出' };

const opt = () => state.options || {};
const household = () => opt().household_top || '家用';
// 頂層先看樹的路徑，沒掛節點的舊列（匯入／節點停用）退到 category 鏡射出來的 book ——
// 後端 /home 的「其中家用」是按 category 前綴算的，兩頁要認同一批列
export const isHousehold = (e) => (((e.taxonomy_path || [])[0] || e.book || '') === household());

/** 分類 picker 的 items：`mode`＝'cash'（家用以外）／'household'（只家用子樹）。 */
export function taxonomyItems(mode) {
    return (opt().taxonomy || [])
        .filter(t => (mode === 'household') === (t.top === household()))
        .map(t => ({ value: t.id, label: t.label }));
}
export const projectItems = () => (opt().projects || []).map(p => ({ value: p.id, label: p.label || p.name }));

// ── 表單 ─────────────────────────────────────────────────────────
/** 記一筆／改一筆的表單 html。`pfx` 讓同一頁可以同時有「記一筆」卡與抽屜表單（id 不撞）。
 *  `mode`＝'household'：沒有收入／支出分段（固定支出）、沒有專案。 */
export function entryFormHtml(pfx, { mode = 'cash', entry = null } = {}) {
    const e = entry || {};
    const kind = e.deposit ? 'deposit' : 'expense';
    const amount = e.deposit || e.expense || '';
    const day = e.entry_date ? fmtDate(e.entry_date) : todayLocal();
    // 帳戶選單同桌機 crm-cashbook-fields：只列 active ＋ 這一筆目前掛的那個（停用的歷史帳戶要認得，
    // 不然選單選不到它、存個摘要就把帳戶洗成「不指定」）；新增時預選預設帳戶
    const cur = String(e.bank_account_id || '');
    const accounts = (opt().accounts || []).filter(a => a.active !== false || String(a.id) === cur);
    const acct = entry ? cur : String((accounts.find(a => a.is_default) || {}).id || '');
    return `<div class="m-form">
        ${mode === 'household' ? `<input type="hidden" id="${pfx}-kind" value="expense">`
            : `<label>收入或支出</label>${segHtml(pfx + '-kind', Object.values(KIND), KIND[kind])}`}
        <label class="req">金額</label>
        <input id="${pfx}-amount" type="number" inputmode="numeric" min="0" step="1" value="${esc(amount)}" placeholder="0">
        <label class="req">分類</label>${pickerHtml(pfx + '-node')}
        ${mode === 'household' ? '' : `<label>專案</label>${pickerHtml(pfx + '-project')}`}
        <label>銀行帳戶</label>
        <select id="${pfx}-account">${selectOpts(accounts.map(a => ({ value: a.id, label: a.name })), acct, '（不指定）')}</select>
        <label>日期</label><input id="${pfx}-date" type="date" value="${esc(day)}">
        <label class="req">摘要</label><input id="${pfx}-summary" value="${esc(e.summary || '')}" placeholder="這筆是什麼">
        <label>備註</label><input id="${pfx}-note" value="${esc(e.note || '')}">
    </div>`;
}

export function mountEntryForm(pfx, { mode = 'cash', entry = null, preset = null } = {}) {
    const e = entry || {};
    if (mode !== 'household') mountSeg(pfx + '-kind');
    mountPicker(pfx + '-node', { items: taxonomyItems(mode), placeholder: '打字找分類…', value: e.taxonomy_node_id || '' });
    if (mode !== 'household') mountPicker(pfx + '-project', { items: projectItems(), placeholder: '打字找專案（可空）…', value: e.project_id || '' });
    if (preset) {
        // 應收頁「收到錢」帶過來：收入＋這案＋未收金額＋摘要
        const kindHidden = document.getElementById(pfx + '-kind');
        if (kindHidden && preset.kind) {
            kindHidden.value = KIND[preset.kind] || KIND.deposit;
            document.querySelectorAll(`#${pfx}-kind-seg button`).forEach(b => b.classList.toggle('on', b.dataset.v === kindHidden.value));
        }
        const proj = document.getElementById(pfx + '-project');
        if (proj && proj._set && preset.project_id) proj._set(String(preset.project_id));
        if (preset.amount) document.getElementById(pfx + '-amount').value = preset.amount;
        if (preset.summary) document.getElementById(pfx + '-summary').value = preset.summary;
    }
}

/** 表單 → body（只放有值的鍵；改一筆時呼叫端再跟原值比、只送有動的）。 */
export function readEntryForm(pfx, { mode = 'cash' } = {}) {
    const v = (id) => { const el = document.getElementById(pfx + '-' + id); return el ? el.value : ''; };
    const kindLabel = v('kind');
    const kind = mode === 'household' ? 'expense' : (kindLabel === KIND.deposit ? 'deposit' : 'expense');
    const amount = parseInt(v('amount'), 10);
    if (!Number.isFinite(amount) || amount <= 0) throw new Error('金額要大於 0');
    if (!v('summary').trim()) throw new Error('摘要必填');
    if (!v('date')) throw new Error('日期必填');
    const body = { entity: 'mine', entry_date: v('date'), summary: v('summary').trim(), note: v('note') };
    body[kind] = amount;
    body[kind === 'deposit' ? 'expense' : 'deposit'] = null;     // 換方向時把另一邊清掉
    body.taxonomy_node_id = v('node') || '';
    body.project_id = mode === 'household' ? null : (v('project') || null);
    body.bank_account_id = v('account') || null;
    return body;
}

/** 新增：收入沒掛專案先問一次（私帳的收入多半是案子的錢）。 */
export async function createEntry(body) {
    if (body.deposit && !body.project_id && !window.confirm('這筆收入沒有掛專案，確定要記嗎？')) return null;
    return mfetch(API, { method: 'POST', body });
}

// ── 卡片與抽屜 ────────────────────────────────────────────────────
export function entryCard(e) {
    const inn = e.deposit ? `<span class="amt in">+${money(e.deposit)}</span>` : `<span class="amt out">−${money(e.expense)}</span>`;
    const path = (e.taxonomy_path || []).join('／');
    return `<div class="m-card tap" data-id="${esc(e.id)}">
        <div class="t"><div class="name">${esc(e.summary || '（無摘要）')}</div>${inn}</div>
        <div class="sub">${esc(fmtDate(e.entry_date))}${path ? '・' + esc(path) : ''}${e.project_name ? '・' + esc(e.project_name) : ''}</div>
    </div>`;
}

/** 點卡片開抽屜：改／刪。`mode` 決定分類子樹；`onDone` 成功後重抓。 */
export function openEntrySheet(e, { mode = 'cash', onDone } = {}) {
    const body = openSheet(`<div class="ttl">${esc(e.summary || '這一筆')}</div>
        ${entryFormHtml('es', { mode, entry: e })}
        <div class="m-actions">
            <button type="button" class="m-btn danger" id="es-del">刪除</button>
            <button type="button" class="m-btn pri" id="es-save">儲存</button>
        </div>`);
    mountEntryForm('es', { mode, entry: e });
    body.querySelector('#es-save').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        let next;
        try { next = readEntryForm('es', { mode }); } catch (err) { toast(err.message, 'err'); return; }
        // 只送有動的鍵（後端 PUT 是 exclude_unset：整包送會把沒動的欄洗掉）
        const was = { entry_date: fmtDate(e.entry_date), summary: e.summary || '', note: e.note || '',
                      deposit: e.deposit || null, expense: e.expense || null,
                      taxonomy_node_id: e.taxonomy_node_id || '', project_id: e.project_id || null,
                      bank_account_id: e.bank_account_id || null };
        const diff = { entity: 'mine' };
        for (const k of Object.keys(was)) if (String(next[k] ?? '') !== String(was[k] ?? '')) diff[k] = next[k];
        // 🔴 picker 找不到原值（分類節點停用／掛在頂層、專案不在私帳清單）時 mountPicker 會把它 set 成空；
        // 帳戶 <select> 沒有那個 option 時瀏覽器落到第一個（不指定）—— 那不是使用者「清掉」，只是這台
        // 畫不出來。送空等於清掉分類三欄／把專案解掉（私帳已收還會跟著減）／把帳戶洗成不指定。
        const pickerKnows = (id, v) => { const h = document.getElementById(id); return !!h && (h._items || []).some(i => String(i.value) === v); };
        const knows = {
            taxonomy_node_id: (v) => pickerKnows('es-node', v),
            project_id: (v) => pickerKnows('es-project', v),
            bank_account_id: (v) => { const s = document.getElementById('es-account'); return !!s && [...s.options].some(o => o.value === v); },
        };
        for (const [k, has] of Object.entries(knows))
            if (k in diff && !next[k] && was[k] && !has(String(was[k]))) delete diff[k];
        if (Object.keys(diff).length === 1) { toast('沒有改動'); closeSheet(); return; }
        try {
            await mfetch(`${API}/${encodeURIComponent(e.id)}`, { method: 'PUT', body: diff });
            markStale('cash', 'household', 'projects', 'receivable', 'overview', 'assets');
            toast('已更新'); closeSheet(); if (onDone) await onDone();
        } catch (err) { toast(err.message, 'err'); }
    }));
    body.querySelector('#es-del').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        if (!window.confirm(`刪除「${e.summary || '這一筆'}」？`)) return;
        try {
            await mfetch(`${API}/${encodeURIComponent(e.id)}?entity=mine`, { method: 'DELETE' });
            markStale('cash', 'household', 'projects', 'receivable', 'overview', 'assets');
            toast('已刪除'); closeSheet(); if (onDone) await onDone();
        } catch (err) { toast(err.message, 'err'); }
    }));
}

/** 近 `days` 天的私帳收支（清單端點；含頭含尾）。 */
export async function fetchEntries(days) {
    const from = new Date(); from.setDate(from.getDate() - days);
    const iso = `${from.getFullYear()}-${String(from.getMonth() + 1).padStart(2, '0')}-${String(from.getDate()).padStart(2, '0')}`;
    const r = await mfetch(`${API}?entity=mine&date_from=${iso}`);
    return { entries: r.entries || [], from: iso };
}

// ── 分頁 ─────────────────────────────────────────────────────────
let _days = DAYS;
let _rows = [];

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
            <div class="m-h">記一筆</div>
            <div class="m-card">${entryFormHtml('nc')}<button type="button" class="m-btn-primary" id="nc-go">記下</button></div>
            <div class="m-h">近 <span id="nc-days">${DAYS}</span> 天</div>
            <div id="nc-list">${skeleton(3)}</div>
            <button type="button" class="m-more" id="nc-more">載入更早 90 天</button>`;
        mountEntryForm('nc');
        host.querySelector('#nc-go').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
            let body;
            try { body = readEntryForm('nc'); } catch (err) { toast(err.message, 'err'); return; }
            // 分類是必填（標籤就是紅星），只在新增這條路擋：改一筆時 readEntryForm 不能擋 —— 節點停用的舊列
            // picker 畫成空、擋了就連摘要都改不了（上面的 known 守衛靠的正是「空著也能存」）。
            // 不擋的話掛了專案的收入會吃到後端 409「未分類不能掛專案」、沒掛的就靜默存成一筆沒分類的
            if (!body.taxonomy_node_id) { toast('請選一個分類', 'err'); return; }
            try {
                const r = await createEntry(body);
                if (!r) return;
                toast('已記下');
                markStale('household', 'projects', 'receivable', 'overview', 'assets');
                for (const id of ['nc-amount', 'nc-summary', 'nc-note']) document.getElementById(id).value = '';
                await load(host);
            } catch (err) { toast(err.message, 'err'); }
        }));
        host.querySelector('#nc-list').addEventListener('click', (ev) => {
            const c = ev.target.closest('.m-card[data-id]'); if (!c) return;
            const e = _rows.find(x => x.id === c.dataset.id);
            if (e) openEntrySheet(e, { onDone: () => load(host) });
        });
        host.querySelector('#nc-more').addEventListener('click', async () => { _days += DAYS; await load(host); });
    }
    // 應收頁「收到錢」帶過來的 preset：套進「記一筆」再清掉
    if (state.cashPreset) {
        mountEntryForm('nc', { preset: state.cashPreset });
        state.cashPreset = null;
        window.scrollTo(0, 0);
    }
    // 60 秒內切回來不重抓、寫過的分頁由 markStale 標髒（同 CRM 手機版七個 view 的做法）
    if (shouldLoad('cash', { first })) await load(host);
}

async function load(host) {
    const list = host.querySelector('#nc-list');
    try {
        const { entries } = await fetchEntries(_days);
        _rows = entries.filter(e => !isHousehold(e));      // 家用在自己那頁
        host.querySelector('#nc-days').textContent = _days;
        list.innerHTML = _rows.map(entryCard).join('') || emptyBox('這段時間沒有收支');
    } catch (e) {
        list.innerHTML = errBox(e);
    }
}
