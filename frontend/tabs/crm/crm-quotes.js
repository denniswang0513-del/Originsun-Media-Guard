/**
 * crm-quotes.js — 報價管理 Tab
 */

import { quoteTotals, parsePaymentStages, paymentStagesToText } from '../../js/shared/quote-amounts.js';
import * as _QA from '../../js/shared/quote-amounts.js';
// 🔴 Cloudflare 給 .js 4 小時瀏覽器快取：新分頁 js 配舊 quote-amounts.js 時，named import 拿不到的 export 會讓整個模組
//    載入失敗（分頁變「連不到伺服器」，2026-09-07 踩到）。新 export 先用命名空間拿、缺就退回同款本地實作
//    （reference_cloudflare_js_cache：契約要相容一輪；快取過期後這兩條退路可以拿掉）
const groupQuoteItems = _QA.groupQuoteItems || ((items) => {
    const groups = [], byName = new Map();
    (items || []).forEach(it => {
        const name = String(it.group_name || '').trim();
        let g = byName.get(name);
        if (!g) { g = { name, items: [] }; byName.set(name, g); groups.push(g); }
        const row = { ...it }; delete row.group_name; g.items.push(row);
    });
    return groups;
});
const flattenQuoteGroups = _QA.flattenQuoteGroups || ((groups) => (groups || []).flatMap(g => g.items.map(it => ({ ...it, group_name: g.name }))));
import { crmFetch as _fetch, esc as _esc, populateClientSelect, fmtNum as _fmtNum, setupResizeHandle, kebabMenuHtml, createSortable, enumIndex, quotePdfFilename, initRootFolderCard, hasModule } from './crm-utils.js';
import * as _U from './crm-utils.js';   // permDeniedMsg 走命名空間（舊快取的 crm-utils 沒有它，named import 會炸整頁）

// 刪除報價仍是管理員限定（RBAC 稽核第二批）—— 不是管理員就別畫那顆鈕
const _isAdmin = () => (window._accessLevel || 0) >= 3;
import { authDownload, copyText } from '../../js/shared/utils.js';
import { applyQuotePatch } from '../../js/shared/quote-patch.js';
import { confirmQuoteDelete } from '../../js/shared/quote-delete.js';
import { waitingText } from '../../js/shared/quote-wait.js';
import { ensurePasteBase, renderRich, pasteThumbs } from '../../js/shared/paste-image.js';

// ── State ────────────────────────────────────────────────────

let _quotations = [];
let _projects = [];
let _clients = [];
let _users = [];
let _templates = [];
let _priceItems = [];
let _selectedId = null;
let _editingId = null;  // null=新增, string=編輯
let _editingQuote = null;   // 正在編的那筆（openModal 帶進來的物件；不從 _quotations 查——那份清單有狀態篩選，查不到就被當成新增）
let _editingProjectId = null;
let _filters = { q: '', status: '', client_id: '' };

// ── Data Loading ─────────────────────────────────────────────

export async function loadQuotations() {
    const params = new URLSearchParams();
    if (_filters.q)         params.set('q', _filters.q);
    if (_filters.status)    params.set('status', _filters.status);
    if (_filters.client_id) params.set('client_id', _filters.client_id);
    try {
        const data = await _fetch(`/quotations?${params}`);
        _quotations = data.quotations || [];
    } catch (e) {
        _quotations = [];
    }
    renderList();
}

async function loadStats() {
    try {
        const s = await _fetch('/quotations/stats');
        document.getElementById('quote-stat-month').textContent = '$' + _fmtNum(s.month_total);
        document.getElementById('quote-stat-pending').textContent = s.pending_count + ' 筆';
        document.getElementById('quote-stat-rate').textContent = s.sign_rate + '%';
    } catch (_) {}
}

async function loadProjects() {
    try {
        const data = await _fetch('/projects');
        _projects = data.projects || [];
    } catch (_) { _projects = []; }
}

async function loadClients() {
    try {
        const data = await _fetch('/clients');
        _clients = data.clients || [];
        _populateClientFilter();
    } catch (_) { _clients = []; }
}

async function loadUsers() {
    try {
        const data = await _fetch('/users');
        _users = data.users || [];
    } catch (_) { _users = []; }
}

async function loadTemplates() {
    try {
        const data = await _fetch('/quotation-templates');
        _templates = data.templates || [];
    } catch (_) { _templates = []; }
}

// ── Rendering ────────────────────────────────────────────────

// 正本在後端 core.finance_logic.QUOTE_STATUSES（手機版從 /crm/m/options 拿）；這裡是桌機鏡射，改要一起改
const _QUOTE_STATUSES = ['草稿', '已寄送', '已簽核', '已拒絕'];

function _qBadge(status) {
    const s = status || '草稿';
    const cls = _QUOTE_STATUSES.includes(s) ? `crm-badge crm-quote-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

// _QUOTE_STATUSES 已定義在上面 — 工作流順序:草稿→已寄送→已簽核→已拒絕
const _sorter = createSortable({
    storageKey: 'crm_quotes_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'quote-list-panel',
    onChange: () => renderList(),
    getters: {
        name:   q => `${q.project_name || ''}-v${q.version || 0}`.toLowerCase(),
        client: q => (q.client_short_name || q.project_name || '').toLowerCase(),
        status: q => enumIndex(_QUOTE_STATUSES, q.status, '草稿'),
        amount: q => q.final_price ?? q.total ?? 0,
        date:   q => q.quote_date || '',
    },
});

function renderList() {
    const body = document.getElementById('quote-list-body');
    if (!body) return;
    _sorter.attach();
    if (_quotations.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無報價單${_filters.q ? '，請調整搜尋條件' : ''}</div>`;
        return;
    }
    body.innerHTML = _sorter.sorted(_quotations).map(q => {
        const price = q.final_price !== null && q.final_price !== undefined ? q.final_price : q.total;
        return `
        <div class="crm-row${q.id === _selectedId ? ' selected' : ''}" onclick="window._quoteSelect('${q.id}')">
            <div class="crm-row-name">Q-${_esc(q.project_name)}-v${q.version}</div>
            <div class="crm-row-client">${_esc(q.project_name)}<br><span class="crm-muted">${_esc(q.client_short_name)}</span></div>
            <div class="crm-row-status">${_qBadge(q.status)}</div>
            <div class="crm-row-amount">$${_fmtNum(price)}</div>
            <div class="crm-row-date">${q.quote_date ? q.quote_date.substring(0, 10) : '—'}</div>
            ${kebabMenuHtml(q.id, { onEdit: '_quoteEdit', onDuplicate: '_quoteDup', onDelete: _isAdmin() ? '_quoteDelete' : undefined })}
        </div>`;
    }).join('');
}

function renderDetail(q) {
    document.getElementById('quote-detail-title').textContent = `Q-${q.project_name || ''}-v${q.version}`;

    const price = q.final_price !== null && q.final_price !== undefined ? q.final_price : q.total;
    const prop = (label, value, empty = '空') => {
        const isEmpty = !value;
        return `<div class="crm-detail-prop">
            <div class="crm-prop-label">${label}</div>
            <div class="crm-prop-value${isEmpty ? ' empty' : ''}">${isEmpty ? empty : _esc(String(value))}</div>
        </div>`;
    };

    document.getElementById('quote-detail-info').innerHTML = `
        ${prop('專案', q.project_name)}
        ${prop('客戶', q.client_short_name)}
        <div class="crm-detail-prop"><div class="crm-prop-label">狀態</div><div class="crm-prop-value">${_qBadge(q.status)}</div></div>
        ${prop('報價日期', q.quote_date ? q.quote_date.substring(0, 10) : '')}
        ${prop('有效期限', q.valid_until ? q.valid_until.substring(0, 10) : '')}
        ${prop('規格', q.spec)}
        ${prop('小計', '$' + _fmtNum(q.subtotal))}
        ${prop('折扣', q.discount ? '-$' + _fmtNum(q.discount) : '')}
        ${prop('稅額', '$' + _fmtNum(q.tax_amount) + ' (' + q.tax_rate + '%)')}
        ${prop('含稅總計', '$' + _fmtNum(q.total))}
        ${prop('最終報價', q.final_price !== null ? '$' + _fmtNum(q.final_price) : '')}
        ${prop('付款階段', (q.payment_stages || []).map(s => s.label + ' ' + s.pct + '%').join(' / '))}
        ${prop('備註', q.terms)}
        ${q.status === '已簽核' ? `<div style="padding:12px 0;"><button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._quoteActivateProject('${q.project_id}')">啟動專案 (切為製作)</button></div>` : ''}
    `;

    // Items tab
    const items = q.items || [];
    const groups = {};
    items.forEach(it => {
        const g = it.group_name || '其他';
        if (!groups[g]) groups[g] = [];
        groups[g].push(it);
    });
    let itemsHtml = '';
    for (const [gName, gItems] of Object.entries(groups)) {
        itemsHtml += `<div class="quote-group-title">${_esc(gName)}</div>`;
        itemsHtml += gItems.map(it => `
            <div class="quote-item-row">
                <span class="quote-item-desc">${_esc(it.description)}</span>
                <span class="quote-item-qty">${it.quantity} ${_esc(it.unit)}</span>
                <span class="quote-item-price">$${_fmtNum(it.unit_price)}</span>
                <span class="quote-item-amount">$${_fmtNum(it.amount)}</span>
            </div>
        `).join('');
    }
    document.getElementById('quote-detail-items').innerHTML = itemsHtml || '<div class="crm-empty">尚無項目</div>';

    const actions = document.getElementById('quote-bar-actions');
    if (actions) {
        // PDF 與分享連結要寄出之後才出現（owner 2026-09-07「送出再產生連結與 pdf 按鈕」）：草稿還在改，不該流出去
        const sent = q.status !== _QUOTE_STATUSES[0];
        // 鑄連結是管理員限定（POST /share）：非管理員只有已經有連結時才給「複製」（同手機版）
        const canShare = q.share_url || hasModule('crm_quotes');   // 第二批：分享開給 crm_quotes（後端 /share 同步放行）
        actions.innerHTML = `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="quote-btn-edit">編輯</button>`
            + (sent ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="quote-btn-pdf">下載 PDF</button>`
            + (canShare ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="quote-btn-share">${q.share_url ? '複製連結' : '分享連結'}</button>` : '') : '')
            + `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
        // 「編輯」開的就是新增／編輯報價那個彈窗（owner 2026-09-09）——就地編欄位那套已退場：
        // 它只編得到上半部的欄位、項目明細碰不到，跟彈窗是兩套規則
        actions.querySelector('#quote-btn-edit')?.addEventListener('click', () => quoteEdit(q.id));
        actions.querySelector('#quote-btn-pdf')?.addEventListener('click', () =>
            authDownload('/api/v1/crm/quotations/' + q.id + '/pdf', quotePdfFilename(q), '下載 PDF'));
        // 線上檢視連結（免登入、頁上可下載 PDF）：沒有就鑄一條（冪等），然後複製完整網址
        actions.querySelector('#quote-btn-share')?.addEventListener('click', async (ev) => {
            const btn = ev.currentTarget;      // await 之後 currentTarget 就是 null 了，先抓住
            try {
                if (!q.share_url) {
                    const r = await _fetch('/quotations/' + q.id + '/share', { method: 'POST' });
                    q.share_url = r.share_url;
                    btn.textContent = '複製連結';
                }
                const full = location.origin + q.share_url;
                // 共用的 copyText：非 https（LAN 直連）走 execCommand 退路，按鈕閃「已複製」；真的不行才 prompt
                if (!(await copyText(full, btn))) prompt('連結（請自行複製）：', full);
            } catch (e) { alert('建立連結失敗：' + e.message); }
        });
    }
}

// ── Detail Panel ─────────────────────────────────────────────

async function selectQuotation(id) {
    _selectedId = id;
    renderList();
    const panel = document.getElementById('quote-detail-panel');
    if (panel) panel.style.display = 'flex';
    const handle = document.getElementById('quote-resize-handle');
    if (handle) handle.style.display = '';

    try {
        const q = await _fetch(`/quotations/${id}`);
        renderDetail(q);
    } catch (_) {}
}

function closeDetail() {
    _selectedId = null;
    const panel = document.getElementById('quote-detail-panel');
    if (panel) panel.style.display = 'none';
    const handle = document.getElementById('quote-resize-handle');
    if (handle) handle.style.display = 'none';
    renderList();
}

// ── Modal: Items ─────────────────────────────────────────────

// 項目的編輯模型是「大項目 → 子項目」（owner 2026-09-07：一列一列填、同名大項目被隔開就印成兩段；同手機版）：
// 進來時 groupQuoteItems 收成組，存檔／範本／重算都走 _flatItems() 攤平，同一組的列自然連在一起
let _groups = [];      // [{ name, items[] }]
const _emptyRow = () => ({ description: '', unit: '式', quantity: 1, unit_price: 0, internal_cost: 0, note: '' });
const _flatItems = () => flattenQuoteGroups(_groups);
function _setItems(items, { blankIfEmpty = false } = {}) {
    _groups = groupQuoteItems(items);
    if (!_groups.length && blankIfEmpty) _groups = [{ name: '', items: [_emptyRow()] }];
    _renderItemRows();
}

// ── 拖曳排序（owner 2026-09-09：大項目、子項目、備註列都要能換順序）────────
// 三處共用同一套：只有那根 ↕ 是 draggable（整列 draggable 會搶掉輸入框的選字），
// 列本身當落點。_drag 記「拖的是誰」，落點依 kind 分流。
let _drag = null;      // {kind:'group'|'item'|'term', gi, i}

/** 從 from 搬到 to（to＝目標列在「原本」陣列的位置）：往下拖＝落在目標後面、往上＝落在它前面 */
function _moveInArray(arr, from, to) {
    if (from === to || from < 0 || to < 0) return false;
    const [x] = arr.splice(from, 1);
    arr.splice(to, 0, x);
    return true;
}

const _gripHtml = (title = '拖曳換順序') =>
    `<span class="qi-grip" draggable="true" title="${title}">↕</span>`;

/** 落點列的共用綁定：dragover 只在 kind 對得上時 preventDefault（不然放不下去） */
function _bindDropTarget(el, kind, onDrop) {
    el.addEventListener('dragover', e => {
        if (!_drag || _drag.kind !== kind) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    });
    el.addEventListener('drop', e => {
        if (!_drag || _drag.kind !== kind) return;
        e.preventDefault(); e.stopPropagation();
        const d = _drag; _drag = null;
        onDrop(d);
    });
}

function _bindGrip(el, payload) {
    if (!el) return;
    el.addEventListener('dragstart', e => {
        _drag = payload;
        e.stopPropagation();
        if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', payload.kind); }
    });
    el.addEventListener('dragend', () => { _drag = null; });
}

function addGroup() {
    _groups.push({ name: '', items: [_emptyRow()] });
    _renderItemRows();
    const inputs = document.querySelectorAll('#quote-items-list .qi-group');
    inputs[inputs.length - 1]?.focus();      // 新的大項目先取名
}

function _renderItemRows() {
    const container = document.getElementById('quote-items-list');
    if (!container) return;
    container.innerHTML = _groups.map((g, gi) => `
        <div class="quote-group-edit" data-g="${gi}">
            <div class="quote-group-edit-head">
                ${_gripHtml('拖曳換大項目順序')}
                <span class="quote-group-edit-label">大項目</span>
                <input type="text" class="crm-input qi-group" value="${_esc(g.name)}" placeholder="例：拍攝／後期製作／其他" style="flex:1;">
                <button type="button" class="crm-btn crm-btn-danger crm-btn-sm qi-remove-group" title="刪除大項目">&#x2715;</button>
            </div>
            ${g.items.map((it, i) => `
            <div class="quote-item-edit-row" data-g="${gi}" data-i="${i}">
                ${_gripHtml('拖曳換子項目順序（可拖到別的大項目）')}
                <input type="text" class="crm-input qi-desc" value="${_esc(it.description)}" placeholder="子項目描述" style="flex:2;">
                <input type="text" class="crm-input qi-unit" value="${_esc(it.unit)}" placeholder="單位" style="width:50px;">
                <input type="number" class="crm-input qi-qty" value="${it.quantity}" min="1" style="width:55px;text-align:right;">
                <input type="number" class="crm-input qi-price" value="${it.unit_price}" min="0" style="width:90px;text-align:right;">
                <span class="qi-amount">$${_fmtNum(it.quantity * it.unit_price)}</span>
                <button type="button" class="crm-btn crm-btn-danger crm-btn-sm qi-remove" title="刪除子項目">&#x2715;</button>
            </div>`).join('')}
            <button type="button" class="crm-btn crm-btn-secondary crm-btn-sm qi-add-sub">＋ 子項目</button>
        </div>
    `).join('');

    container.querySelectorAll('.quote-group-edit').forEach(gEl => {
        const gi = parseInt(gEl.dataset.g), g = _groups[gi];
        _bindGrip(gEl.querySelector('.quote-group-edit-head .qi-grip'), { kind: 'group', gi });
        // 大項目落點＝整個框；子項目拖到框裡的空白處＝掛到這一組的最後面
        _bindDropTarget(gEl, 'group', d => { if (_moveInArray(_groups, d.gi, gi)) { _renderItemRows(); _recalcTotals(); _autoTouch(); } });
        _bindDropTarget(gEl, 'item', d => {
            if (d.gi === gi) return;
            const [it] = _groups[d.gi].items.splice(d.i, 1);
            g.items.push(it);
            _renderItemRows(); _recalcTotals(); _autoTouch();
        });
        gEl.querySelector('.qi-group').addEventListener('input', e => { g.name = e.target.value; });
        gEl.querySelector('.qi-add-sub').addEventListener('click', () => { g.items.push(_emptyRow()); _renderItemRows(); });
        gEl.querySelector('.qi-remove-group').addEventListener('click', () => {
            if (g.items.some(it => (it.description || '').trim()) && !window.confirm('這個大項目底下還有子項目，一起刪掉？')) return;
            _groups.splice(gi, 1); _renderItemRows(); _recalcTotals();
        });
    });
    container.querySelectorAll('.quote-item-edit-row').forEach(row => {
        const gi = parseInt(row.dataset.g), g = _groups[gi], i = parseInt(row.dataset.i), it = g.items[i];
        _bindGrip(row.querySelector('.qi-grip'), { kind: 'item', gi, i });
        _bindDropTarget(row, 'item', d => {
            if (d.gi === gi) { if (!_moveInArray(g.items, d.i, i)) return; }
            else { const [moved] = _groups[d.gi].items.splice(d.i, 1); g.items.splice(i, 0, moved); }
            _renderItemRows(); _recalcTotals(); _autoTouch();
        });
        row.querySelector('.qi-desc').addEventListener('input', e => { it.description = e.target.value; });
        row.querySelector('.qi-unit').addEventListener('input', e => { it.unit = e.target.value; });
        row.querySelector('.qi-qty').addEventListener('input', e => { it.quantity = parseInt(e.target.value) || 0; _recalcTotals(); });
        row.querySelector('.qi-price').addEventListener('input', e => { it.unit_price = parseInt(e.target.value) || 0; _recalcTotals(); });
        row.querySelector('.qi-remove').addEventListener('click', () => { g.items.splice(i, 1); _renderItemRows(); _recalcTotals(); });
    });
}

// ── 備註／條款：一列一條（owner 2026-09-09「用一列一列開，每列一樣可以更改排序，前面加個編號」）──
// 存回 DB 仍是同一個 terms 文字欄（一行一條）：報價單模板早就是照行切成 <ol>，
// 所以編輯器的順序＝報價單上的編號順序，舊資料原樣讀得回來。
let _terms = [];
const _termsText = () => _terms.map(t => t.trim()).filter(Boolean).join('\n');

function _setTerms(text) {
    _terms = String(text || '').split('\n').map(t => t.trim()).filter(Boolean);
    if (!_terms.length) _terms = [''];
    _renderTermRows();
}

function _renderTermRows() {
    const c = document.getElementById('quote-terms-list');
    if (!c) return;
    c.innerHTML = _terms.map((t, i) => `
        <div class="quote-term-row" data-i="${i}">
            ${_gripHtml('拖曳換備註順序')}
            <span class="quote-term-no">${i + 1}.</span>
            <input type="text" class="crm-input qt-text" value="${_esc(t)}" placeholder="例：本報價未含字幕翻譯" style="flex:1;min-width:0;">
            <button type="button" class="crm-btn crm-btn-danger crm-btn-sm qt-remove" title="刪除這列">&#x2715;</button>
        </div>`).join('');

    c.querySelectorAll('.quote-term-row').forEach(row => {
        const i = parseInt(row.dataset.i);
        _bindGrip(row.querySelector('.qi-grip'), { kind: 'term', i });
        _bindDropTarget(row, 'term', d => {
            if (_moveInArray(_terms, d.i, i)) { _renderTermRows(); _autoTouch(); }
        });
        const input = row.querySelector('.qt-text');
        input.addEventListener('input', e => { _terms[i] = e.target.value; });   // 不重繪，游標才不會被搶
        input.addEventListener('keydown', e => {
            if (e.key !== 'Enter') return;                 // Enter＝在下面開新的一列（一列一條打得順）
            e.preventDefault();
            _terms.splice(i + 1, 0, '');
            _renderTermRows();
            document.querySelector(`.quote-term-row[data-i="${i + 1}"] .qt-text`)?.focus();
        });
        row.querySelector('.qt-remove').addEventListener('click', () => {
            _terms.splice(i, 1);
            if (!_terms.length) _terms = [''];
            _renderTermRows(); _autoTouch();
        });
    });
}

function addTermRow() {
    _terms.push('');
    _renderTermRows();
    const rows = document.querySelectorAll('#quote-terms-list .qt-text');
    rows[rows.length - 1]?.focus();
}

// 報價表單的狀態（跟手機版 _form 同形）：客戶、要連結的案、優惠／最終報價哪一格是人打的
let _form = { client_id: '', project_id: '', anchor: null };

function _recalcTotals() {
    // 金額規則跟後端 _calc_quotation 同一份（js/shared/quote-amounts.js）。稅前折扣已退場（同手機版）：
    // 優惠一律走「最終報價」倒算，報價單上印成「專案優惠」。舊報價存著的稅前折扣仍要算進去，
    // 不然畫面的含稅總計跟後端存的對不上、優惠↔最終報價也會用錯底數
    const taxRate = parseInt(document.getElementById('quote-f-tax_rate')?.value) || 0;
    const { subtotal, tax, total } = quoteTotals({ items: _flatItems(), taxRate, discount: _editingQuote?.discount || 0 });

    document.getElementById('quote-calc-subtotal').textContent = '$' + _fmtNum(subtotal);
    document.getElementById('quote-calc-tax-pct').textContent = taxRate;
    document.getElementById('quote-calc-tax').textContent = '$' + _fmtNum(tax);
    document.getElementById('quote-calc-total').textContent = '$' + _fmtNum(total);

    // 優惠 ↔ 最終報價 互推：只寫「不是正在打的那一格」，免得游標被搶（同手機版 recalc）
    const promoEl = document.getElementById('quote-f-promo'), finalEl = document.getElementById('quote-f-final_price');
    if (_form.anchor === 'promo') {
        finalEl.value = promoEl.value === '' ? '' : Math.max(total - (parseInt(promoEl.value) || 0), 0);
    } else if (_form.anchor === 'final') {
        promoEl.value = finalEl.value === '' ? '' : Math.max(total - (parseInt(finalEl.value) || 0), 0);
    }
    // 0 是合法的最終報價（全免）：只有空字串才算「沒填」（同手機版 recalc、同存檔規則）
    const finalPrice = finalEl.value.trim() === '' ? null : (parseInt(finalEl.value) || 0);
    document.getElementById('quote-calc-final').textContent = '$' + _fmtNum(finalPrice != null ? finalPrice : total);

    document.querySelectorAll('.quote-item-edit-row').forEach(row => {
        const it = _groups[parseInt(row.dataset.g)]?.items[parseInt(row.dataset.i)];
        if (it) row.querySelector('.qi-amount').textContent = '$' + _fmtNum(it.quantity * it.unit_price);
    });
}

// ── Modal: Open / Save ───────────────────────────────────────

function _populateClientSelect(selectedId = '') {
    const sel = document.getElementById('quote-f-client_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 打字找客戶 —</option>` +
        _clients.map(c => `<option value="${c.id}"${c.id === selectedId ? ' selected' : ''}>${_esc(c.short_name || c.full_name || c.id)}</option>`).join('');
    sel._ssSync?.();
}

/** 連結既有專案：拿這個客戶的全部案子（免得打同名再建一個重複的案）——同手機版 loadClientProjects */
async function _loadClientProjects(clientId) {
    const sel = document.getElementById('quote-f-link_project');
    if (!sel) return;
    let rows = [];
    if (clientId) {
        try { rows = (await _fetch('/projects?client_id=' + encodeURIComponent(clientId))).projects || []; }
        catch (_) { rows = []; }
    }
    sel.innerHTML = `<option value="">${rows.length ? '（不連結，照上面的案名建新案）' : (clientId ? '這個客戶還沒有案子' : '先選客戶')}</option>` +
        rows.map(p => `<option value="${p.id}">${_esc(p.name)}</option>`).join('');
    sel._rows = rows;
}

async function _createClientInline() {
    const name = (prompt('客戶名稱（代稱）：') || '').trim();
    if (!name) return;
    try {
        const r = await _fetch('/clients', { method: 'POST', body: JSON.stringify({ short_name: name }) });
        const c = r.client || r;
        _clients.push({ id: c.id, short_name: c.short_name || name, full_name: c.full_name || '' });
        _populateClientSelect(c.id);
        _form.client_id = c.id;
        await _loadClientProjects(c.id);
        _populateClientFilter();
    } catch (e) { alert('建立客戶失敗：' + e.message); }
}

/** 編輯／從專案頁開＝案子固定；新增＝客戶／案名／連結案三格（同手機版 formHtml 的分流） */
function _setProjectMode(fixedLabel) {
    const fixed = !!fixedLabel;
    document.getElementById('quote-f-project-fixed').style.display = fixed ? '' : 'none';
    document.getElementById('quote-f-project-label').textContent = fixedLabel || '';
    for (const id of ['quote-f-client-field', 'quote-f-name-field', 'quote-f-link-field']) {
        document.getElementById(id).style.display = fixed ? 'none' : '';
    }
}

function _populateTemplateSelect() {
    const sel = document.getElementById('quote-f-template');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 不使用範本 —</option>` +
        _templates.map(t => `<option value="${t.id}">${_esc(t.name)}</option>`).join('');
}

function _populateClientFilter() {
    populateClientSelect('quote-filter-client', _clients);
}

// ── 「和 AI 一起完成」分頁 ─────────────────────────────────
// owner 2026-09-09：「客戶請我報價，他有很多訊息，我需要整理一個報價單給他」。
// 規劃正本 docs/QUOTE_ASSISTANT_PLAN.md。骨架同公布欄「問 Claude」：送出 → 背景跑
// claude → 前端輪詢。差別是它回的是 **patch**，由這邊套進草稿再走自動存寫回去。
//
// 🔴 **寫入者只有前端**：後端只算 patch、不改項目。送出前一定要把自動存 flush 掉 ——
//    AI 讀的是 DB 那一份，順序不一樣的話 patch 的編號會改到別人。
// 🔴 **同一則 patch 只套一次**：_chatApplied 記已處理到第幾則，重繪不會重套。
let _chat = [];
let _chatApplied = 0;      // 已套用過 patch 的訊息數
let _chatBusy = false;     // 等 AI 回覆中
let _chatGen = 0;          // 開窗世代：關窗／換一張報價後，舊的輪詢自己收工
let _chatPartial = '';     // 串流到目前為止的 reply（後端 GET /chat 的 partial）
let _chatStage = '';       // 'queued'（卡在閘門）／'running'（claude 真的在跑）
let _chatSince = 0;        // 這一輪按下送出的時間（等待文字要顯示秒數）
const _CHAT_MODEL_KEY = 'crm_quote_chat_model';   // 這台瀏覽器選的模型（後端仍會過白名單）

function _chatBubble(m) {
    const who = m.role === 'user' ? '你' : 'AI';
    // at 是後端寫的 UTC（_now()）—— 直接切字串會差八小時，要轉本地
    const d = new Date(m.at || '');
    const at = isNaN(d) ? '' : d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false });
    const extras = [];
    if ((m.questions || []).length)
        extras.push(`<ol class="quote-chat-q">${m.questions.map(q => `<li>${_esc(q)}</li>`).join('')}</ol>`);
    if ((m.needs_price || []).length)
        extras.push(`<div class="quote-chat-np">待定價：${m.needs_price.map(_esc).join('、')}</div>`);
    if (m.applied)     // 只在畫面上標，不寫回後端
        extras.push(`<div class="quote-chat-ap">已套進草稿：新增 ${m.applied.added}`
            + `、修改 ${m.applied.updated}、刪除 ${m.applied.removed}、備註 +${m.applied.terms}</div>`);
    // renderRich＝跳脫＋把貼圖 token 換成縮圖（paste-image.js 同一份契約）
    return `<div class="quote-chat-msg ${m.role === 'user' ? 'user' : 'ai'}">
        <div class="quote-chat-who">${who}<span class="quote-chat-at">${_esc(at)}</span></div>
        <div class="quote-chat-body">${renderRich(m.text || '')}</div>${extras.join('')}</div>`;
}

function _renderChat() {
    const log = document.getElementById('quote-chat-log');
    if (!log) return;
    if (!_editingId) {
        log.innerHTML = `<div class="crm-empty">先在「報價內容」填好客戶與專案 ——
            草稿建起來之後才能開始對話（AI 改的就是那張草稿）。</div>`;
    } else if (!_chat.length && !_chatBusy) {
        log.innerHTML = `<div class="crm-empty">把客戶的訊息整段貼進下面，或直接貼截圖。<br>
            AI 會整理成項目，不確定的地方會問你。</div>`;
    } else {
        // 串流：有字就直接顯示（邊產邊看）；還沒有就給會動的等待文字 ——
        // 前 35 秒左右畫面本來什麼都不會動，靜止太久看起來像當掉
        const streaming = _chatPartial
            ? `<div class="quote-chat-body"><span id="quote-chat-stream">${
                _esc(_chatPartial)}</span><span class="quote-chat-caret"></span></div>`
            : `<div class="quote-chat-body thinking"><span id="quote-chat-tick">${
                _esc(waitingText(Date.now() - _chatSince, _chatStage))}</span></div>`;
        log.innerHTML = _chat.map(_chatBubble).join('') + (_chatBusy
            ? `<div class="quote-chat-msg ai"><div class="quote-chat-who">AI</div>${streaming}</div>` : '');
    }
    log.scrollTop = log.scrollHeight;
    const off = !_editingId || _chatBusy;
    const send = document.getElementById('quote-chat-send');
    const input = document.getElementById('quote-chat-input');
    if (send) send.disabled = off;
    if (input) input.disabled = off;
}

/** 右欄的即時摘要。編號＝攤平後的順序，跟 patch 的 n 同一套（別自己重排） */
function _renderChatSummary() {
    const el = document.getElementById('quote-chat-summary');
    if (!el) return;
    const rows = _flatItems().filter(it => it.description);
    let html = '', last = null, n = 0, noPrice = 0;
    rows.forEach(it => {
        const g = (it.group_name || '').trim() || '未分類';
        if (g !== last) { html += `<div class="quote-group-title">${_esc(g)}</div>`; last = g; }
        n++;
        const p = it.unit_price || 0;
        if (!p) noPrice++;
        html += `<div class="quote-sum-row"><span>${n}. ${_esc(it.description)}</span>
            <span class="${p ? '' : 'noprice'}">${it.quantity} ${_esc(it.unit)} ×
            ${p ? '$' + _fmtNum(p) : '待定價'}</span></div>`;
    });
    const terms = (_terms || []).filter(t => String(t).trim());
    if (terms.length) {
        html += `<div class="quote-group-title">備註</div>`;
        html += terms.map((t, i) => `<div class="quote-sum-row"><span>${i + 1}. ${_esc(t)}</span></div>`).join('');
    }
    el.innerHTML = (html || '<div class="crm-empty">還沒有項目</div>')
        + `<div class="quote-sum-foot">${n} 項${noPrice ? ` · <b>${noPrice} 項待定價</b>` : (n ? ' · 都有價了' : '')}</div>`
        + (noPrice ? `<button type="button" id="quote-fill-prices"
             class="crm-btn crm-btn-secondary crm-btn-sm quote-fill-prices">用價目補上待定價</button>` : '');
    el.querySelector('#quote-fill-prices')?.addEventListener('click', _fillPricesFromBook);
}

/** 立刻把畫面上的內容寫進 DB（AI 動過草稿、或送出前要讓 AI 讀到最新的順序）。
 *
 * 🔴 `_autoOn` 只管「打字要不要觸發 debounce」——編輯既有報價時它是關的
 *    （怕靜默改到舊資料，見 _autoReset）。但 AI 的 patch、「用價目補上」、送出前的
 *    同步都是使用者按出來的、畫面也已經變了 —— 不寫回去才是丟資料。
 *    所有「動過草稿就要立刻落地」的地方都走這一支，別再各自判斷 _autoOn／_autoDirty。
 */
async function _persistNow(note) {
    if (!_editingId) return;
    clearTimeout(_autoTimer); _autoTimer = null;   // debounce 排到一半的那發不用了
    _autoNote('儲存中…');
    try {
        await _fetch(`/quotations/${_editingId}`, { method: 'PUT', body: JSON.stringify(_buildPayload()) });
        _autoDirty = false;
        _autoNote(note || '已儲存', 'ok');
    } catch (e) {
        _autoDirty = true;                         // 沒送成功，留給關窗前那一發補
        _autoNote('儲存失敗：' + e.message + '（請按儲存）', 'err');
    }
}

/** 把還沒套過的 AI patch 套進草稿（純函式在 js/shared/quote-patch.js） */
function _applyNewChatPatches() {
    let touched = false;
    for (let i = _chatApplied; i < _chat.length; i++) {
        const m = _chat[i];
        if (m.role !== 'ai' || !m.patch) continue;
        const r = applyQuotePatch(_groups, _terms, { ...m.patch, terms_add: m.terms_add || [] });
        _groups = r.groups; _terms = r.terms; m.applied = r.applied;
        touched = true;
    }
    _chatApplied = _chat.length;
    if (touched) {
        _renderItemRows(); _renderTermRows(); _recalcTotals();
        // 套完就存回去（單一寫入者是這邊）。排進 _autoChain，關窗時會被 await 到
        _autoQueue(() => _persistNow('AI 的修改已存進這張報價'));
    }
    _renderChatSummary();
}

async function _loadChat() {
    const id = _editingId, gen = _chatGen;
    if (!id) return;
    try {
        const d = await _fetch(`/quotations/${id}/chat`);
        if (gen !== _chatGen) return;     // 這中間換了一張報價
        _chat = d.chat || [];
        _chatApplied = _chat.length;      // 歷史的 patch 早就在資料裡，不再套一次
        _renderChat();
    } catch (_) {}
}

async function _pollChat(expect, gen) {
    const started = Date.now();
    // 後端一輪最久 _CLAUDE_TIMEOUT_SEC=180 秒，前面還可能排隊（閘門只有 2）——
    // 跟它一樣長的話一排隊就先在前端喊逾時，那一則的 patch 之後也不會再被套用
    while (_chatBusy && gen === _chatGen && Date.now() - started < 400000) {
        await new Promise(r => setTimeout(r, 1000));   // 串流要看得出來在動，1 秒一問
        if (gen !== _chatGen || !_editingId) return;
        let d;
        try { d = await _fetch(`/quotations/${_editingId}/chat`); } catch (_) { continue; }
        if ((d.chat || []).length >= expect) {
            _chat = d.chat || [];
            _chatBusy = false; _chatPartial = '';
            _applyNewChatPatches();
            _renderChat();
            return;
        }
        if (typeof d.stage === 'string') _chatStage = d.stage;
        if (typeof d.partial === 'string' && d.partial !== _chatPartial) {
            const grow = document.getElementById('quote-chat-stream');
            _chatPartial = d.partial;                  // 邊產邊長出來
            // 已經在串了就只換那顆泡泡的字 —— 整包重繪會把歷史裡所有截圖的 <img>
            // 丟掉重建（一輪十幾次），也會把捲軸拉回底
            if (grow) grow.textContent = _chatPartial;
            else _renderChat();
        } else {
            // 只換那一小段字，不整包重繪（重繪會把捲軸拉回底、也會閃）
            const tick = document.getElementById('quote-chat-tick');
            if (tick) tick.textContent = waitingText(Date.now() - _chatSince, _chatStage);
        }
    }
    if (_chatBusy && gen === _chatGen) {
        _chatBusy = false; _chatPartial = ''; _renderChat();
        _showModalError('AI 沒有在時間內回覆（可能 claude 在排隊或沒登入）');
    }
}

async function _sendChat() {
    const ta = document.getElementById('quote-chat-input');
    const text = (ta?.value || '').trim();
    if (!text || !_editingId || _chatBusy) return;
    // 🔴 先把畫面上的內容送進 DB：AI 讀的是 DB 那份，順序與內容要跟畫面一致
    //    （patch 的編號才對得上）。三種情況（有待存的／編輯既有報價／已經是最新）
    //    都收在 _persistNow 裡，呼叫端不用再自己挑。
    await _autoQueue(() => _persistNow('已存下目前的內容'));

    const gen = _chatGen;
    ta.value = '';
    document.getElementById('quote-chat-thumbs').innerHTML = '';
    _chatBusy = true; _chatPartial = ''; _chatStage = ''; _chatSince = Date.now();
    _renderChat();
    try {
        const model = document.getElementById('quote-chat-model')?.value || '';
        const r = await _fetch(`/quotations/${_editingId}/chat`,
                              { method: 'POST', body: JSON.stringify({ text, model }) });
        if (gen !== _chatGen) return;
        _chat = r.chat || _chat;
        _chatApplied = _chat.length;      // 使用者那則沒有 patch
        _renderChat();
        await _pollChat(_chat.length + 1, gen);
    } catch (e) {
        _chatBusy = false; _chatPartial = ''; _renderChat();
        _showModalError('送出失敗：' + e.message);
    }
}

/** 用價目補上目前是 0 的項目。**不覆蓋你手打的價**，只填空的那些。 */
async function _fillPricesFromBook() {
    const rows = _flatItems().filter(it => it.description);
    if (!rows.length) return;
    const btn = document.getElementById('quote-fill-prices');
    if (btn) { btn.disabled = true; btn.textContent = '查價目中…'; }
    try {
        // 比對規則（norm_key）住在後端，跟收價時同一支 —— 前端不再寫一份會漂掉的版本
        const r = await _fetch('/price-items/match', {
            method: 'POST',
            body: JSON.stringify({ items: rows.map(it => ({ description: it.description, unit: it.unit || '式' })) }),
        });
        // n 是攤平後的位置，跟 AI patch 的編號同一套；只補現在是 0 的
        const update = (r.matches || [])
            .filter(m => !(rows[m.n - 1] || {}).unit_price)
            .map(m => ({ n: m.n, unit_price: m.unit_price }));
        if (!update.length) { _autoNote('價目裡沒有對得上、而且還缺價的品項', 'err'); return; }
        const out = applyQuotePatch(_groups, _terms, { add: [], update, remove: [] });
        _groups = out.groups; _terms = out.terms;
        _renderItemRows(); _recalcTotals(); _renderChatSummary();
        await _autoQueue(() => _persistNow(`已用價目補上 ${out.applied.updated} 項`));
    } catch (e) {
        _showModalError('查價目失敗：' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '用價目補上待定價'; }
    }
}

/** 彈窗的兩個分頁：報價內容／和 AI 一起完成 */
function _setPane(name) {
    document.querySelectorAll('#quote-modal-tabs .crm-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.pane === name));
    const form = document.getElementById('quote-pane-form');
    const ai = document.getElementById('quote-pane-ai');
    if (form) form.style.display = name === 'form' ? '' : 'none';
    if (ai) ai.style.display = name === 'ai' ? 'flex' : 'none';
    // AI 分頁要並排「對話｜摘要」，彈窗撐寬一點
    document.querySelector('#quote-modal .crm-modal')?.classList.toggle('quote-modal-wide', name === 'ai');
    if (name === 'ai') { _renderChat(); _renderChatSummary(); }
}

// ── 自動存草稿 ───────────────────────────────────────────────
// owner 2026-09-09：「打了客戶與專案之後就可以自動儲存了，要不不小心點掉就消失了」。
// 客戶＋案名（或已經固定的案）一齊，第一次動到表單就先 POST 出一張草稿；之後每次改欄位
// debounce 一發 PUT，關窗前把還沒送的補送出去。只有「新增」這條路自動存 —— 編輯既有報價
// 維持「按儲存才寫」，不然舊資料會在使用者還沒決定前就被改掉。
let _autoOn = false;              // 這次彈窗是不是自動存模式
let _autoTimer = null;
let _autoDirty = false;           // 有改過、還沒送出去
let _autoChain = Promise.resolve();  // 建草稿／PUT 排成一條龍（關窗前 await 它）
let _autoShellProject = null;     // {id, name}：這次自動建出來的殼案（案名還能改）

function _autoNote(msg, kind = '') {
    const el = document.getElementById('quote-auto-note');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'quote-auto-note' + (kind ? ' ' + kind : '');
}

function _autoReset(on) {
    clearTimeout(_autoTimer); _autoTimer = null;
    _autoOn = !!on; _autoDirty = false; _autoShellProject = null;
    _autoNote('');
    // 三格解鎖（上一輪自動存草稿後是鎖著的）
    for (const id of ['quote-f-client_id', 'quote-btn-new-client', 'quote-f-link_project', 'quote-f-project_name']) {
        const el = document.getElementById(id);
        if (el) el.disabled = false;
    }
}

/** 案子從哪來：從專案頁／複製開的已經固定；新增則是「連結的案」或「客戶＋案名」 */
function _autoTarget() {
    if (_editingProjectId) return { id: _editingProjectId };
    if (_form.project_id)  return { id: _form.project_id };
    const name = (document.getElementById('quote-f-project_name')?.value || '').trim();
    if (_form.client_id && name) return { id: '', name };
    return null;
}

const _autoQueue = (fn) => { _autoChain = _autoChain.then(fn).catch(() => {}); return _autoChain; };

/** 表單被動到：草稿還沒建就先建，建好了就排一發 debounce PUT */
function _autoTouch() {
    if (!_autoOn) return;
    if (!_editingId) { _autoQueue(_autoCreateDraft); return; }
    _autoDirty = true;
    _autoNote('有還沒存的變更…');
    clearTimeout(_autoTimer);
    _autoTimer = setTimeout(() => _autoQueue(_autoFlush), 1200);
}

/** 草稿建起來之後，客戶／連結案就定了（PUT 報價改不動案子）——鎖起來免得改了以為有效。
 *  案名只有在「殼案是我們剛建的」時候還能改（改的是那個案，不是報價） */
function _lockProjectFields() {
    for (const id of ['quote-f-client_id', 'quote-btn-new-client', 'quote-f-link_project']) {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
    }
    const nameEl = document.getElementById('quote-f-project_name');
    if (nameEl) nameEl.disabled = !_autoShellProject;
}

async function _autoCreateDraft() {
    if (!_autoOn || _editingId) return;
    const t = _autoTarget();
    if (!t) return;
    _autoNote('自動儲存中…');
    try {
        let projectId = t.id;
        if (!projectId) {          // 案子還沒成立：照案名開一個殼案（同按儲存那條路；階段＝提案）
            const np = await _fetch('/projects', { method: 'POST', body: JSON.stringify({ name: t.name, client_id: _form.client_id, status: '提案' }) });
            const proj = np.project || np;
            projectId = proj.id;
            _projects.push(proj);
            _autoShellProject = { id: projectId, name: t.name };
        }
        const r = await _fetch(`/projects/${projectId}/quotations`, { method: 'POST', body: JSON.stringify(_buildPayload()) });
        const q = r.quotation || r;
        _editingId = q.id; _editingQuote = q; _editingProjectId = projectId; _form.project_id = projectId;
        _autoDirty = false;
        document.getElementById('quote-modal-title').textContent = '編輯報價';
        _lockProjectFields();
        _autoNote('已自動存成草稿 v' + (q.version || 1) + '（關掉也還在，在清單找得到）', 'ok');
        _renderChat();             // 有 id 了，AI 分頁的輸入框跟著解鎖
        loadQuotations();          // 清單先冒出來，不擋著使用者繼續填
    } catch (e) {
        _autoOn = false;           // 存不進去（權限／DB）就別再一直試，讓他按儲存看到真正的錯誤
        _autoNote('自動儲存失敗：' + e.message + '（請按儲存）', 'err');
    }
}

async function _autoFlush() {
    if (!_autoOn || !_editingId || !_autoDirty) return;
    _autoDirty = false;
    _autoNote('自動儲存中…');
    try {
        await _fetch(`/quotations/${_editingId}`, { method: 'PUT', body: JSON.stringify(_buildPayload()) });
        _autoNote('已自動儲存 ' + new Date().toLocaleTimeString('zh-TW', { hour12: false }), 'ok');
    } catch (e) {
        _autoDirty = true;
        _autoNote('自動儲存失敗：' + e.message + '（請按儲存）', 'err');
    }
}

/** 殼案改名：草稿存了之後案名還打錯的話，改的是那個案（報價的 PUT 碰不到案名） */
async function _autoRenameShell() {
    const shell = _autoShellProject;
    const name = (document.getElementById('quote-f-project_name')?.value || '').trim();
    if (!shell || !name || name === shell.name) return;
    try {
        await _fetch(`/projects/${shell.id}`, { method: 'PUT', body: JSON.stringify({ name }) });
        shell.name = name;
        const p = _projects.find(x => x.id === shell.id);
        if (p) p.name = name;
        _autoNote('案名已更新', 'ok');
        loadQuotations();
    } catch (e) { _autoNote('案名更新失敗：' + e.message, 'err'); }
}

/** 關窗（X／取消／點外面）：把還沒送出去的補送完再收工。已經自動存的草稿留著，不刪 */
async function closeModal() {
    clearTimeout(_autoTimer); _autoTimer = null;
    _chatGen++; _chatBusy = false; _chatPartial = '';   // 還在等 AI 的輪詢看到世代變了就收工
    // _autoFlush 開頭就有 `!_autoOn || !_editingId || !_autoDirty` 的守衛，這裡不用再判一次；
    // _autoQueue 自己 .catch 掉，chain 永遠 resolve，所以也不需要 try/catch
    const pending = _autoQueue(_autoFlush);
    const hadDraft = _autoOn && !!_editingId;
    document.getElementById('quote-modal').style.display = 'none';
    await pending;
    const savedId = _editingId;
    _autoReset(false);
    if (hadDraft) {
        await Promise.all([loadQuotations(), loadStats()]);
        if (_selectedId === savedId) await selectQuotation(savedId);
    }
}

async function openModal(quotation = null, projectId = null) {
    _autoReset(!quotation);        // 新增才自動存草稿；編輯既有報價維持「按儲存才寫」
    _editingId = quotation ? quotation.id : null;
    _editingQuote = quotation;
    _editingProjectId = projectId;
    document.getElementById('quote-modal-title').textContent = quotation ? '編輯報價' : '新增報價';
    const errEl = document.getElementById('quote-modal-error');
    errEl.textContent = ''; errEl.style.display = 'none';

    _populateTemplateSelect();
    _form = { client_id: '', project_id: '', anchor: quotation && quotation.final_price != null ? 'final' : null };
    if (quotation) {
        _setProjectMode([quotation.client_short_name, quotation.project_name].filter(Boolean).join('｜') || '（未連專案）');
    } else if (projectId) {
        const p = _projects.find(x => x.id === projectId);
        _setProjectMode(p ? [p.client_short_name, p.name].filter(Boolean).join('｜') : projectId);
    } else {
        _setProjectMode('');
        _populateClientSelect('');
        document.getElementById('quote-f-project_name').value = '';
        await _loadClientProjects('');
    }

    // Fill fields
    const q = quotation || {};
    document.getElementById('quote-f-status').value = q.status || '草稿';
    document.getElementById('quote-f-quote_date').value = q.quote_date ? q.quote_date.substring(0, 10) : '';
    document.getElementById('quote-f-valid_until').value = q.valid_until ? q.valid_until.substring(0, 10) : '';
    document.getElementById('quote-f-tax_rate').value = q.tax_rate ?? 5;
    document.getElementById('quote-f-promo').value = '';
    document.getElementById('quote-f-final_price').value = q.final_price ?? '';
    document.getElementById('quote-f-payment_stages').value = paymentStagesToText(q.payment_stages);
    _setTerms(q.terms);
    document.getElementById('quote-f-spec').value = q.spec || '';
    document.getElementById('quote-f-template').value = '';

    _setItems(q.items || [], { blankIfEmpty: !quotation });   // 新增：先給一個空的大項目
    _recalcTotals();

    _chat = []; _chatApplied = 0; _chatBusy = false; _chatPartial = ''; _chatGen++;   // 舊的輪詢看到世代變了就收工
    _setPane('form');
    if (_editingId) _loadChat();

    document.getElementById('quote-modal').style.display = 'flex';
}

/** 表單目前的樣子 → 送出去的 payload。按「儲存」與自動存草稿共用一份，兩條路不會各算一次 */
function _buildPayload() {
    const rows = _flatItems();      // 大項目一組接一組攤平，報價單上不會被拆開
    return {
        status: document.getElementById('quote-f-status').value,
        quote_date: document.getElementById('quote-f-quote_date').value || null,
        valid_until: document.getElementById('quote-f-valid_until').value || null,
        tax_rate: parseInt(document.getElementById('quote-f-tax_rate').value) || 5,
        discount: _editingQuote ? (_editingQuote.discount || 0) : 0,   // 稅前折扣已退場：舊值原樣帶回，不洗掉
        final_price: document.getElementById('quote-f-final_price').value ? parseInt(document.getElementById('quote-f-final_price').value) : null,
        payment_stages: parsePaymentStages(document.getElementById('quote-f-payment_stages').value),
        terms: _termsText(),
        spec: document.getElementById('quote-f-spec').value.trim(),
        items: rows.filter(it => it.description).map(it => ({
            group_name: it.group_name || '', description: it.description,
            unit: it.unit || '式', quantity: it.quantity || 1,
            unit_price: it.unit_price || 0, internal_cost: it.internal_cost || 0, note: it.note || '',
        })),
    };
}

async function saveQuotation() {
    const editing = _editingQuote;
    let projectId = editing ? editing.project_id : (_editingProjectId || _form.project_id || '');
    const projectName = document.getElementById('quote-f-project_name').value.trim();
    if (!editing && !_editingProjectId) {
        if (!_form.client_id) { _showModalError('請先選客戶'); return; }
        if (!projectId && !projectName) { _showModalError('請填專案名稱，或連結一個既有專案'); return; }
    }
    if (!_flatItems().some(it => it.description)) { _showModalError('請至少新增一個子項目'); return; }
    const payload = _buildPayload();

    const btn = document.getElementById('quote-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';

    try {
        if (_editingId) {
            await _fetch(`/quotations/${_editingId}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
            if (!projectId) {              // 案子還沒成立：先照案名開一個殼案（同手機版；階段＝提案）
                const np = await _fetch('/projects', { method: 'POST', body: JSON.stringify({ name: projectName, client_id: _form.client_id, status: '提案' }) });
                projectId = (np.project || np).id;
                _projects.push(np.project || np);
            }
            await _fetch(`/projects/${projectId}/quotations`, { method: 'POST', body: JSON.stringify(payload) });
        }
        _autoReset(false);              // 手動存完＝這一輪結束，別再補一發自動存
        document.getElementById('quote-modal').style.display = 'none';
        await Promise.all([loadQuotations(), loadStats()]);
        if (_selectedId) await selectQuotation(_selectedId);   // 右邊詳情開著就跟著換新的
        // Refresh project detail quotation sub-tab if open
        if (window._projRefreshQuotes) window._projRefreshQuotes(projectId);
    } catch (e) {
        _showModalError(e.message);
    } finally {
        btn.disabled = false; btn.textContent = '儲存';
    }
}

async function deleteQuotation(q) {
    // 確認規則跟手機版同一份（js/shared/quote-delete.js）：草稿按 OK 就好，
    // 已寄送／已簽核要打字確認案名 —— 那些刪掉會讓客戶手上的 /q/{code} 變 404
    if (!confirmQuoteDelete(q)) return;
    try {
        await _fetch(`/quotations/${q.id}`, { method: 'DELETE' });
        closeDetail();
        await Promise.all([loadQuotations(), loadStats()]);
    } catch (e) {
        alert(_U.permDeniedMsg?.('管理員', e) ?? ('刪除失敗：' + e.message));
    }
}

function _showModalError(msg) {
    const el = document.getElementById('quote-modal-error');
    el.textContent = msg; el.style.display = 'block';
}

// ── Template: Apply ──────────────────────────────────────────

function _applyTemplate(templateId) {
    const t = _templates.find(x => x.id === templateId);
    if (!t) return;
    _setItems(t.items || []);
    document.getElementById('quote-f-tax_rate').value = t.tax_rate ?? 5;
    _setTerms(t.terms);
    document.getElementById('quote-f-payment_stages').value = paymentStagesToText(t.payment_stages);
    _recalcTotals();
}

function _renderTemplateList() {
    const container = document.getElementById('quote-template-list');
    if (!container) return;
    if (_templates.length === 0) {
        container.innerHTML = '<div class="crm-empty">尚無範本</div>';
        return;
    }
    container.innerHTML = _templates.map(t => {
        const total = (t.items || []).reduce((s, it) => s + (it.quantity || 1) * (it.unit_price || 0), 0);
        return `
        <div class="quote-template-row">
            <div><strong>${_esc(t.name)}</strong> <span class="crm-muted">${(t.items || []).length} 項 $${_fmtNum(total)}</span></div>
            <div class="crm-muted">${_esc(t.description || '')}</div>
            <div style="margin-top:4px;">
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._quoteDeleteTemplate('${t.id}')">刪除</button>
            </div>
        </div>`;
    }).join('');
}

async function _createTemplate(body) {
    await _fetch('/quotation-templates', { method: 'POST', body: JSON.stringify(body) });
    await loadTemplates();
    _renderTemplateList();
    _populateTemplateSelect();
}

// 報價彈窗目前的表單（稅率／條款／付款階段／項目）→ 一個範本
function _saveCurrentAsTemplate(name) {
    return _createTemplate({
        name, items: _flatItems().filter(it => it.description),
        tax_rate: parseInt(document.getElementById('quote-f-tax_rate').value) || 5,
        terms: _termsText(),
        payment_stages: parsePaymentStages(document.getElementById('quote-f-payment_stages').value),
    });
}

// 兩個入口都會問名字：範本管理彈窗的「+ 新增範本」建空範本（之後套用它再調整）、
// 報價彈窗的「存成範本」把目前表單存起來。兩顆都曾經沒綁事件、_saveCurrentAsTemplate 沒人呼叫。
// 🔴 別把「表單開著就存表單」塞進 _addTemplate：範本彈窗從工具列開，那時報價彈窗必定是關的。
async function _promptTemplate(save) {
    const name = (prompt('範本名稱：') || '').trim();
    if (!name) return;
    try { await save(name); } catch (e) { alert('儲存失敗：' + e.message); }
}

const _addTemplate = () => _promptTemplate(
    name => _createTemplate({ name, items: [], tax_rate: 5, terms: '', payment_stages: [] }));

const _addTemplateFromForm = () => _promptTemplate(_saveCurrentAsTemplate);

async function _deleteTemplate(id) {
    if (!confirm('確定刪除此範本？')) return;
    try {
        await _fetch(`/quotation-templates/${id}`, { method: 'DELETE' });
        await loadTemplates();
        _renderTemplateList();
        _populateTemplateSelect();
    } catch (e) { alert(e.message); }
}

// ── 價目表（docs/QUOTE_ASSISTANT_PLAN.md P3）────────────────
// 「答過的價順手存成價目，下一張 AI 就自己帶」。收價的時機是**寄出**（後端做），
// 這裡只負責看／改／刪，外加一顆把歷史報價一次補進來的鈕。

async function _loadPrices() {
    try { _priceItems = (await _fetch('/price-items')).items || []; }
    catch (_) { _priceItems = []; }
    _renderPriceList();
}

function _renderPriceList() {
    const el = document.getElementById('quote-price-list');
    if (!el) return;
    if (!_priceItems.length) {
        el.innerHTML = `<div class="crm-empty">還沒有價目 —— 寄出一張報價就會自動收進來，
            或按上面「從歷史報價匯入」把舊的補進來。</div>`;
        return;
    }
    el.innerHTML = _priceItems.map(p => `
        <div class="quote-price-row" data-id="${p.id}">
            <span class="quote-price-desc" title="${_esc(p.description)}">${_esc(p.description)}</span>
            <span class="quote-price-unit">${_esc(p.unit || '式')}</span>
            <input type="number" class="crm-input qp-price" value="${p.unit_price}" min="0">
            <span class="quote-price-hits" title="報過幾次">×${p.hits || 1}</span>
            <button type="button" class="crm-btn crm-btn-danger crm-btn-sm qp-del" title="刪掉這筆">&#x2715;</button>
        </div>`).join('');

    el.querySelectorAll('.quote-price-row').forEach(row => {
        const id = row.dataset.id;
        const input = row.querySelector('.qp-price');
        input.addEventListener('change', async () => {     // 離開欄位才送（打字不送）
            const v = Math.max(parseInt(input.value) || 0, 0);
            try {
                await _fetch(`/price-items/${id}`, { method: 'PUT', body: JSON.stringify({ unit_price: v }) });
                const hit = _priceItems.find(x => x.id === id);
                if (hit) hit.unit_price = v;
            } catch (e) { alert('改價失敗：' + e.message); _loadPrices(); }
        });
        row.querySelector('.qp-del').addEventListener('click', async () => {
            const p = _priceItems.find(x => x.id === id);
            if (!confirm(`刪掉「${p ? p.description : ''}」這筆價目？`)) return;
            try { await _fetch(`/price-items/${id}`, { method: 'DELETE' }); await _loadPrices(); }
            catch (e) { alert('刪除失敗：' + e.message); }
        });
    });
}

async function _importPricesFromHistory() {
    const btn = document.getElementById('quote-price-import');
    btn.disabled = true; btn.textContent = '匯入中…';
    try {
        const r = await _fetch('/price-items/import-history', { method: 'POST' });
        await _loadPrices();
        alert(`掃了 ${r.quotations} 張寄出過的報價：新增 ${r.added} 筆、更新 ${r.updated} 筆`);
    } catch (e) {
        alert('匯入失敗：' + e.message);
    } finally {
        btn.disabled = false; btn.textContent = '從歷史報價匯入';
    }
}

// ── Init ─────────────────────────────────────────────────────

// 三個彈窗入口也給專案頁的報價子頁 import（crm-projects-quotes.js）——它們在這裡是 window.* 給
// inline onclick 用，在那邊是模組匯出，名字錯了會在載入時就炸，不會變成按了沒反應的死按鈕
export async function quoteEdit(id) {
    try {
        const q = await _fetch(`/quotations/${id}`);
        openModal(q);
    } catch (_) {}
}
export async function quoteDup(id) {
    try {
        const q = await _fetch('/quotations/' + id);
        openModal(q, q.project_id);
        _editingId = null; _editingQuote = null;   // 複製＝新增：不帶舊的稅前折扣（表單沒有那一欄，帶了看不到也拿不掉）
        _autoReset(true);                         // openModal 帶了 quotation 會關掉自動存，複製其實是新增 —— 補開回來
        _recalcTotals();
        document.getElementById('quote-modal-title').textContent = '複製報價';
    } catch (_) {}
}
export const openQuoteForProject = (projectId) => openModal(null, projectId);

export async function initCrmQuotesTab() {
    // 只有 app.js 的載入器會呼叫（它自己去重：載過不再載、進行中只載一次）；以前這裡另有一個
    // init 守衛擋第二條載入路，那條路 2026-09-03 已收進載入器——守衛留著反而會在 init
    // 中途炸掉時把重試也擋掉
    for (const id of ['quote-modal', 'quote-template-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }

    window._quoteSelect = selectQuotation;
    window._quoteEdit = quoteEdit;
    window._quoteDelete = (id) => {
        const q = _quotations.find(x => x.id === id);
        if (q) deleteQuotation(q);
    };
    window._quoteDup = quoteDup;
    window._quoteActivateProject = async (projectId) => {
        if (!confirm('確定將此專案狀態切為「製作」？')) return;
        try {
            await _fetch(`/projects/${projectId}/status`, {
                method: 'PATCH', body: JSON.stringify({ status: '製作' })
            });
            alert('專案已切為製作');
        } catch (e) {
            alert('操作失敗：' + e.message);
        }
    };
    window._quoteDeleteTemplate = _deleteTemplate;

    // Search + filters
    let _searchTimer;
    document.getElementById('quote-search').addEventListener('input', e => {
        _filters.q = e.target.value;
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(loadQuotations, 300);
    });
    document.getElementById('quote-filter-status').addEventListener('change', e => {
        _filters.status = e.target.value; loadQuotations();
    });
    document.getElementById('quote-filter-client').addEventListener('change', e => {
        _filters.client_id = e.target.value; loadQuotations();
    });

    // Buttons
    document.getElementById('quote-btn-add').addEventListener('click', () => openModal());
    document.getElementById('quote-btn-save').addEventListener('click', saveQuotation);
    document.getElementById('quote-btn-add-item').addEventListener('click', () => { addGroup(); _recalcTotals(); });
    document.getElementById('quote-btn-add-term').addEventListener('click', addTermRow);

    // 「和 AI 一起完成」分頁
    ensurePasteBase();                  // 貼圖縮圖要的圖床網址（拿不到就只顯示文字）
    document.querySelectorAll('#quote-modal-tabs .crm-tab').forEach(btn =>
        btn.addEventListener('click', () => _setPane(btn.dataset.pane)));
    document.getElementById('quote-chat-send').addEventListener('click', _sendChat);
    const modelSel = document.getElementById('quote-chat-model');
    try { modelSel.value = localStorage.getItem(_CHAT_MODEL_KEY) || 'sonnet'; } catch (_) {}
    if (!modelSel.value) modelSel.value = 'sonnet';
    modelSel.addEventListener('change', () => {
        try { localStorage.setItem(_CHAT_MODEL_KEY, modelSel.value); } catch (_) {}
    });
    const chatInput = document.getElementById('quote-chat-input');
    chatInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); _sendChat(); }
    });
    // 貼上的圖在 textarea 裡只是一串 token，下面補一排縮圖讓人看得到自己貼了什麼
    chatInput.addEventListener('input', () => {
        document.getElementById('quote-chat-thumbs').innerHTML = pasteThumbs(chatInput.value);
    });
    document.getElementById('quote-detail-close').addEventListener('click', closeDetail);
    // 公司資訊（報價單抬頭／匯款／Logo／章）就近開：系統設定 → 公司資訊分頁
    // （owner 2026-09-07「公司資訊的按鈕要放在報價管理的範本欄」）。設定只有管理員讀得到，別人不顯示這顆。
    // 報價單資料夾（owner 2026-09-07「跟發票一樣有個地方指定儲存位置」）：每次產 PDF 都存一份到那裡。管理員限定，不 await
    initRootFolderCard({
        linkId: 'quote-root-toggle', panelId: 'quote-root-panel', endpoint: '/quotations-root', key: 'quotes_root',
        intro: `報價單 PDF 的存放根目錄：每次下載／線上檢視產出的 PDF 都會存一份到這裡（同名覆蓋）。
        要集中到 NAS 就填那個路徑；底下會自動分 <code>{年}/{年-月}/</code>，檔名跟下載的 PDF 一樣（日期_客戶_專案）。`,
        placeholder: '例：\\\\192.168.1.132\\Archive\\Quotations 或 T:\\報價單',
        savedMsg: '已儲存（之後產出的報價單存到新位置；舊檔不搬）',
    });
    const companyBtn = document.getElementById('quote-btn-company');
    // 公司資訊存進 /api/settings/save 的 company 鍵：後端分流給 crm_quotes／crm_invoices，所以入口不只管理員
    const canEditCompany = hasModule('crm_quotes') || hasModule('crm_invoices');
    if (companyBtn && canEditCompany) {
        companyBtn.style.display = '';
        companyBtn.addEventListener('click', () => {
            document.getElementById('btnOpenSettings')?.click();
            [...document.querySelectorAll('#settingsModal .tab-btn')].find((b) => b.textContent.trim() === '公司資訊')?.click();
            // 只要公司資訊（owner 2026-09-07「請拉出公司資訊就好，其他 tab 不用」）：藏分頁列、標題改成公司資訊
            const modal = document.getElementById('settingsModal');
            if (modal) {
                modal.classList.add('company-only');
                const h3 = modal.querySelector('.modal-header h3');
                if (h3) { h3.dataset.full = h3.dataset.full || h3.textContent; h3.textContent = '公司資訊'; }
            }
        });
    }
    document.getElementById('quote-btn-templates').addEventListener('click', () => {
        _renderTemplateList();
        _loadPrices();                 // 價目跟範本同一個彈窗
        document.getElementById('quote-template-modal').style.display = 'flex';
    });
    document.getElementById('quote-price-import').addEventListener('click', _importPricesFromHistory);
    document.getElementById('quote-tpl-btn-add').addEventListener('click', _addTemplate);
    document.getElementById('quote-btn-as-template').addEventListener('click', _addTemplateFromForm);

    // Template apply
    document.getElementById('quote-f-template').addEventListener('change', e => {
        if (e.target.value) _applyTemplate(e.target.value);
    });

    // 自動存草稿的觸發點：彈窗裡任何一格被動到就算。項目／備註列是重繪出來的，所以掛在彈窗上委派。
    // 案名的 input 不算（不然打第一個字就開一個案）—— 它靠 change（離開欄位才發）進來。
    // 🔴 AI 分頁不算「動到表單」：對話框每打一個字都會走這裡，1.2 秒後就是一發
    //    整張報價的 PUT（後端 _save_items 是砍光重插），而那些字根本不是報價內容。
    const quoteModal = document.getElementById('quote-modal');
    const _fromForm = (el) => el && !el.closest('#quote-pane-ai');
    quoteModal.addEventListener('input', e => {
        if (e.target.id !== 'quote-f-project_name' && _fromForm(e.target)) _autoTouch();
    });
    quoteModal.addEventListener('change', e => {
        if (e.target.id === 'quote-f-project_name' && _editingId) { _autoQueue(_autoRenameShell); return; }
        if (_fromForm(e.target)) _autoTouch();
    });
    quoteModal.addEventListener('click', e => { if (e.target.closest('#quote-items-list, #quote-terms-list')) _autoTouch(); });

    // 客戶 → 撈他的案子給「連結既有專案」；打案名＝要建新案（剛連的就不算）；連結案＝案名帶進來
    document.getElementById('quote-f-client_id').addEventListener('change', e => { _form.client_id = e.target.value; _loadClientProjects(e.target.value); });
    document.getElementById('quote-btn-new-client').addEventListener('click', _createClientInline);
    document.getElementById('quote-f-project_name').addEventListener('input', () => {
        _form.project_id = '';
        const link = document.getElementById('quote-f-link_project'); if (link) link.value = '';
    });
    document.getElementById('quote-f-link_project').addEventListener('change', e => {
        _form.project_id = e.target.value;
        const hit = (e.target._rows || []).find(r => r.id === e.target.value);
        if (hit) document.getElementById('quote-f-project_name').value = hit.name;
    });

    // Recalc on tax / promo / final change（優惠與最終報價互推）
    document.getElementById('quote-f-tax_rate').addEventListener('input', _recalcTotals);
    document.getElementById('quote-f-promo').addEventListener('input', () => { _form.anchor = 'promo'; _recalcTotals(); });
    document.getElementById('quote-f-final_price').addEventListener('input', () => { _form.anchor = 'final'; _recalcTotals(); });

    // Modal overlay close（報價彈窗要先把自動存的草稿收尾，別直接把 display 關掉）
    document.getElementById('quote-modal').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeModal();
    });
    document.getElementById('quote-modal-close').addEventListener('click', closeModal);
    document.getElementById('quote-btn-cancel').addEventListener('click', closeModal);
    const tplModal = document.getElementById('quote-template-modal');
    tplModal.addEventListener('click', e => { if (e.target === tplModal) tplModal.style.display = 'none'; });

    setupResizeHandle('quote-resize-handle', 'quote-detail-panel');

    await Promise.all([loadQuotations(), loadStats(), loadProjects(), loadClients(), loadUsers(), loadTemplates()]);
}
