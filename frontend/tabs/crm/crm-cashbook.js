/**
 * crm-cashbook.js — 收支明細子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml } from './crm-utils.js';

let _entries = [];
let _invoiceList = [];
let _projectList = [];
let _clientList = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', item: '' };
let _csvFile = null;

function _toggleAdvanceFields(isAdv) {
    var ids = ['cash-advance-section', 'cash-field-project', 'cash-field-invoice', 'cash-field-bankfee'];
    for (var i = 0; i < ids.length; i++) {
        var el = document.getElementById(ids[i]);
        if (el) el.style.display = (i === 0 ? isAdv : !isAdv) ? '' : 'none';
    }
}

// ── Data Loading ────────────────────────────────────────────

async function loadEntries() {
    const params = new URLSearchParams();
    if (_filters.q)        params.set('q', _filters.q);
    if (_filters.category) params.set('category', _filters.category);
    if (_filters.item)     params.set('item', _filters.item);
    try { _entries = (await _fetch('/cash-entries?' + params)).entries || []; }
    catch (_) { _entries = []; }
    renderList();
}

async function _loadInvoiceList() {
    try { _invoiceList = (await _fetch('/invoices')).invoices || []; } catch(_) { _invoiceList = []; }
}

async function _loadProjectList() {
    try { _projectList = (await _fetch('/projects')).projects || []; } catch(_) { _projectList = []; }
}

async function _loadClientList() {
    try { _clientList = (await _fetch('/clients')).clients || []; } catch(_) { _clientList = []; }
}

// ── List ────────────────────────────────────────────────────

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
            <div style="color:#86efac;">${e.deposit ? '$' + _fmtNum(e.deposit) : ''}</div>
            <div style="color:#fca5a5;">${((e.expense || 0) + (e.bank_fee || 0)) ? '$' + _fmtNum((e.expense || 0) + (e.bank_fee || 0)) : ''}</div>
            <div>${_esc(e.category || '')}</div>
            <div>${_esc(e.project_name || '')}</div>
            <div>${_esc(e.invoice_title || '')}</div>
            ${kebabMenuHtml(e.id, { onEdit: '_cashEdit', onDuplicate: '_cashDup', onDelete: '_cashDelete' })}
        </div>
    `).join('');
}

// ── Detail Panel ────────────────────────────────────────────

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

    // 關聯資訊
    html += section('關聯資訊');
    html += prop('專案', e.project_name || '');
    html += prop('發票', e.invoice_title || '');
    html += prop('匯費', e.bank_fee ? '$' + _fmtNum(e.bank_fee) : '');
    // 驗算
    if (e.invoice_id && e.deposit) {
        const inv = _invoiceList.find(i => i.id === e.invoice_id);
        if (inv) {
            const fee = e.bank_fee || 0;
            const expected = (e.deposit || 0) + fee;
            const invAmt = inv.amount_total || 0;
            const ok = expected === invAmt;
            html += `<div style="padding:6px 0;font-size:12px;color:${ok ? '#86efac' : '#fca5a5'};">` +
                `發票 $${_fmtNum(invAmt)} = 收入 $${_fmtNum(e.deposit)} + 匯費 $${_fmtNum(fee)} ${ok ? '✓' : '✗ 不平衡'}` +
                `</div>`;
        }
    }

    // 補充資訊
    html += section('補充資訊');
    const matchedClient = _clientList.find(c => {
        const note = (c.payment_note || '').trim();
        return note && e.summary && (e.summary.includes(note) || note.includes(e.summary));
    });
    html += prop('客戶', matchedClient ? matchedClient.short_name + (matchedClient.payment_info ? ' (' + matchedClient.payment_info + ')' : '') : '');
    if (e.advance_payment_id) {
        html += prop('預支關聯', e.advance_payment_id ? '已關聯' : '');
    }

    document.getElementById('cash-detail-content').innerHTML = html;

    const actions = document.getElementById('cash-bar-actions');
    if (actions) {
        actions.innerHTML = `<button class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('cash-bar-actions', () => {
        const editData = { ...e, invoice_id: e.invoice_id || '', project_id: e.project_id || '', bank_fee: e.bank_fee || 0 };
        enableInlineEdit('cash-detail-content', 'cash-bar-actions', _buildEditFields(), editData,
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

function _buildEditFields() {
    const invoiceOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _invoiceList.filter(inv => inv.issue_status === '已開立').map(inv =>
            ({value:inv.id, label:inv.title + ' $' + (inv.amount_total||0).toLocaleString('zh-TW') + (inv.payment_status === '已收款' ? ' (已收款)' : '')})));
    const projectOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _projectList.map(p => ({value:p.id, label:p.name + (p.client_short_name ? ' (' + p.client_short_name + ')' : '')})));
    const catOpts = ['','水電網路','交際應酬','行政','其他','其他收入','房租','建構','專案','專案外包','專案雜支','教育訓練','設備耗材','設備維護','軟體網路服務','勞健保','發票代開','會計','業務推廣','製作金','銀行利息','獎金','請款單','辦公室管理費','營所稅','營業稅','薪資','轉存']
        .map(v => ({value:v, label:v || '—'}));
    return [
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
}

// ── Modal ───────────────────────────────────────────────────

const _FIELDS = ['summary', 'entry_date', 'expense', 'deposit', 'note', 'invoice_id', 'bank_fee', 'project_id', 'category', 'advance_payment_id'];
const _INT_FIELDS = ['expense', 'deposit', 'bank_fee'];
const _DATE_FIELDS = ['entry_date'];

function _populateInvoiceSelect(selectedId) {
    const sel = document.getElementById('cash-f-invoice_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 不關聯 —</option>` +
        _invoiceList.filter(inv => inv.issue_status === '已開立').map(inv => {
            const tag = inv.payment_status === '已收款' ? ' (已收款)' : '';
            return `<option value="${inv.id}"${inv.id === selectedId ? ' selected' : ''}>${_esc(inv.title)} $${(inv.amount_total||0).toLocaleString('zh-TW')}${tag}</option>`;
        }).join('');
}

function _populateProjectSelect(selectedId) {
    const sel = document.getElementById('cash-f-project_id');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 不關聯 —</option>` +
        _projectList.map(p =>
            `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)}${p.client_short_name ? ' (' + _esc(p.client_short_name) + ')' : ''}</option>`
        ).join('');
}

function _updateVerifyRow() {
    const row = document.getElementById('cash-verify-row');
    if (!row) return;
    const invId = document.getElementById('cash-f-invoice_id')?.value;
    const deposit = parseInt(document.getElementById('cash-f-deposit')?.value) || 0;
    const fee = parseInt(document.getElementById('cash-f-bank_fee')?.value) || 0;
    if (!invId || !deposit) { row.style.display = 'none'; return; }
    const inv = _invoiceList.find(i => i.id === invId);
    if (!inv) { row.style.display = 'none'; return; }
    const invAmt = inv.amount_total || 0;
    const expected = deposit + fee;
    const ok = expected === invAmt;
    row.style.display = '';
    row.innerHTML = `<span style="color:${ok ? '#86efac' : '#fca5a5'};">` +
        `發票 $${_fmtNum(invAmt)} = 收入 $${_fmtNum(deposit)} + 匯費 $${_fmtNum(fee)} ${ok ? '✓' : '✗ 差額 $' + _fmtNum(Math.abs(invAmt - expected))}` +
        `</span>`;
}

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
    for (const f of _FIELDS) {
        const el = document.getElementById('cash-f-' + f);
        if (!el) continue;
        if (f === 'invoice_id' || f === 'project_id') continue; // already populated
        if (_DATE_FIELDS.includes(f) && e?.[f]) el.value = e[f].substring(0, 10);
        else el.value = e ? (e[f] ?? '') : '';
    }
    if (!e) {
        document.getElementById('cash-f-entry_date').value = new Date().toISOString().substring(0, 10);
        document.getElementById('cash-f-bank_fee').value = '0';
    }
    _updateVerifyRow();
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
        await Promise.all([loadEntries(), _loadInvoiceList()]);
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
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadEntries, 300);
    });
    document.getElementById('cash-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadEntries(); });
    document.getElementById('cash-filter-item').addEventListener('change', e => { _filters.item = e.target.value; loadEntries(); });

    document.getElementById('cash-btn-add').addEventListener('click', () => openModal());
    document.getElementById('cash-btn-import').addEventListener('click', openImportModal);
    document.getElementById('cash-btn-save').addEventListener('click', saveEntry);
    document.getElementById('cash-detail-close').addEventListener('click', closeDetail);
    document.getElementById('cash-btn-do-import').addEventListener('click', doImport);

    // Show/hide advance section + project/invoice based on category
    var catEl = document.getElementById('cash-f-category');
    if (catEl) catEl.addEventListener('change', function() { _toggleAdvanceFields(this.value === '專案雜支'); });

    // Modal dynamic: verify row + client match
    for (const id of ['cash-f-invoice_id', 'cash-f-deposit', 'cash-f-bank_fee', 'cash-f-summary', 'cash-f-note']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
            _updateVerifyRow();
            _updateClientMatch();
        });
    }

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
    await Promise.all([loadEntries(), _loadInvoiceList(), _loadProjectList(), _loadClientList()]);
}
