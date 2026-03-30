/**
 * crm-cashbook.js — 收支明細子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton } from './crm-utils.js';

let _entries = [];
let _staffList = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', item: '' };
let _csvFile = null;

async function loadEntries() {
    const params = new URLSearchParams();
    if (_filters.q)        params.set('q', _filters.q);
    if (_filters.category) params.set('category', _filters.category);
    if (_filters.item)     params.set('item', _filters.item);
    try { _entries = (await _fetch('/cash-entries?' + params)).entries || []; }
    catch (_) { _entries = []; }
    renderList();
}

function renderList() {
    const body = document.getElementById('cash-list-body');
    if (!body) return;
    if (_entries.length === 0) {
        body.innerHTML = `<div class="crm-empty">尚無收支紀錄${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    body.innerHTML = _entries.map(e => `
        <div class="crm-row${e.id === _selectedId ? ' selected' : ''}" onclick="window._cashSelect('${e.id}')">
            <div class="crm-row-date">${e.entry_date ? e.entry_date.substring(0, 10) : '—'}</div>
            <div class="crm-row-name">${_esc(e.summary)}</div>
            <div class="crm-row-amt-out" style="color:#fca5a5;">${e.expense ? '$' + _fmtNum(e.expense) : ''}</div>
            <div class="crm-row-amt-in" style="color:#86efac;">${e.deposit ? '$' + _fmtNum(e.deposit) : ''}</div>
            <div class="crm-row-client">${_esc(e.payee ? e.payee.split('_')[0] : '')}</div>
            <div class="crm-row-status"><span class="crm-badge">${_esc(e.category || e.item)}</span></div>
            <div class="crm-row-actions" onclick="event.stopPropagation()">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._cashEdit('${e.id}')">編輯</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._cashDelete('${e.id}')">刪</button>
            </div>
        </div>
    `).join('');
}

const _CASH_EDIT_FIELDS = [
    {name:'summary', label:'摘要', type:'text'},
    {name:'entry_date', label:'日期', type:'date'},
    {name:'expense', label:'支出', type:'number'},
    {name:'claim', label:'請款', type:'number'},
    {name:'deposit', label:'存入', type:'number'},
    {name:'category', label:'類別', type:'select', options:[{value:'',label:'—'},{value:'請款',label:'請款'},{value:'收支',label:'收支'},{value:'轉存',label:'轉存'}]},
    {name:'item', label:'項目', type:'select', options:[{value:'',label:'—'},{value:'專案',label:'專案'},{value:'設備耗材',label:'設備耗材'},{value:'行政',label:'行政'},{value:'轉存',label:'轉存'}]},
    {name:'payee', label:'收款人', type:'text'},
    {name:'project_label', label:'專案標籤', type:'text'},
    {name:'payment_status', label:'付款狀態', type:'select', options:[{value:'',label:'—'},{value:'已付款',label:'已付款'},{value:'未付款',label:'未付款'}]},
    {name:'note', label:'附註', type:'text'},
];

function renderDetail(e) {
    document.getElementById('cash-detail-title').textContent = e.summary;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    document.getElementById('cash-detail-content').innerHTML = `
        ${prop('日期', e.entry_date ? e.entry_date.substring(0, 10) : '')}
        ${e.expense ? prop('支出', '$' + _fmtNum(e.expense)) : ''}
        ${e.claim ? prop('請款', '$' + _fmtNum(e.claim)) : ''}
        ${e.deposit ? prop('存入', '$' + _fmtNum(e.deposit)) : ''}
        ${prop('類別', e.category)}
        ${prop('項目', e.item)}
        ${e.sub_item ? prop('子項目', e.sub_item) : ''}
        ${prop('收款人', e.payee)}
        ${e.status ? prop('狀態', e.status) : ''}
        ${e.invoice_number ? prop('發票號碼', e.invoice_number) : ''}
        ${prop('專案', e.project_label || e.project_name)}
        ${e.payment_status ? prop('付款狀態', e.payment_status) : ''}
        ${e.note ? prop('附註', e.note) : ''}
    `;
    const actions = document.getElementById('cash-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('cash-bar-actions', () => {
        enableInlineEdit('cash-detail-content', 'cash-bar-actions', _CASH_EDIT_FIELDS, e,
            async (payload) => {
                await _fetch('/cash-entries/' + e.id, { method: 'PUT', body: JSON.stringify(payload) });
                await loadEntries();
                const updated = _entries.find(x => x.id === e.id);
                if (updated) renderDetail(updated);
                else renderDetail(e);
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

const _FIELDS = ['summary', 'entry_date', 'expense', 'claim', 'deposit', 'category', 'item',
    'payee', 'project_label', 'payment_status', 'note'];
const _INT_FIELDS = ['expense', 'claim', 'deposit'];
const _DATE_FIELDS = ['entry_date'];

async function loadStaffList() {
    try { _staffList = (await _fetch('/staff')).staff || []; } catch(_) { _staffList = []; }
}

function _populatePayeeSelect(selectedPayee) {
    const sel = document.getElementById('cash-f-payee');
    if (!sel) return;
    const name = selectedPayee ? selectedPayee.split('_')[0] : '';
    sel.innerHTML = `<option value="">— 選擇人員 —</option>` +
        _staffList.map(s => {
            const val = s.name + (s.id_number ? '_' + s.id_number : '');
            return `<option value="${_esc(val)}"${s.name === name ? ' selected' : ''}>${_esc(s.name)} (${_esc(s.role)})</option>`;
        }).join('');
}

function openModal(e = null) {
    _editingId = e ? e.id : null;
    document.getElementById('cash-modal-title').textContent = e ? '編輯收支' : '新增收支';
    const err = document.getElementById('cash-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populatePayeeSelect(e?.payee || '');
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        if (!el) continue;
        if (f === 'payee') continue;
        if (_DATE_FIELDS.includes(f) && e?.[f]) el.value = e[f].substring(0, 10);
        else el.value = e ? (e[f] ?? '') : '';
    }
    if (!e) document.getElementById('cash-f-entry_date').value = new Date().toISOString().substring(0, 10);
    document.getElementById('cash-modal').style.display = 'flex';
}

async function saveEntry() {
    const summary = document.getElementById('cash-f-summary').value.trim();
    if (!summary) { _showErr('摘要為必填'); return; }
    const payload = {};
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        let val = el ? el.value.trim() : '';
        if (_INT_FIELDS.includes(f)) val = val ? parseInt(val) : null;
        if (_DATE_FIELDS.includes(f)) val = val || null;
        payload[f] = val;
    }
    const btn = document.getElementById('cash-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId) await _fetch('/cash-entries/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
        else await _fetch('/cash-entries', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('cash-modal').style.display = 'none';
        await loadEntries();
    } catch (e) { _showErr(e.message); }
    finally { btn.disabled = false; btn.textContent = '儲存'; }
}

async function deleteEntry(e) {
    if (!confirm(`確定刪除「${e.summary}」？`)) return;
    try { await _fetch('/cash-entries/' + e.id, { method: 'DELETE' }); closeDetail(); await loadEntries(); }
    catch (err) { alert(err.message); }
}

function _showErr(msg) { const el = document.getElementById('cash-modal-error'); el.textContent = msg; el.style.display = 'block'; }

// CSV Import
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

export function initCrmCashbookTab() {
    for (const id of ['cash-modal', 'cash-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
    window._cashSelect = selectEntry;
    window._cashRefresh = loadEntries;
    window._cashEdit = (id) => { const e = _entries.find(x => x.id === id); if (e) openModal(e); };
    window._cashDelete = (id) => { const e = _entries.find(x => x.id === id); if (e) deleteEntry(e); };

    let _t;
    document.getElementById('cash-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadEntries, 300);
    });
    document.getElementById('cash-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadEntries(); });
    document.getElementById('cash-filter-item').addEventListener('change', e => { _filters.item = e.target.value; loadEntries(); });

    document.getElementById('cash-btn-add').addEventListener('click', () => openModal());
    document.getElementById('cash-btn-import').addEventListener('click', openImportModal);
    document.getElementById('cash-btn-save').addEventListener('click', saveEntry);
    document.getElementById('cash-detail-close').addEventListener('click', closeDetail);
    document.getElementById('cash-btn-do-import').addEventListener('click', doImport);

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
    Promise.all([loadEntries(), loadStaffList()]);
}
