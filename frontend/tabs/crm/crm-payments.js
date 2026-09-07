/**
 * crm-payments.js — 請款管理子視圖
 */
import { crmFetch as _fetch, esc as _esc, fmtNum as _fmtNum, setupResizeHandle, enableInlineEdit, addEditButton, kebabMenuHtml, createSortable, enumIndex, today, crmToast, projectOptionsHtml } from './crm-utils.js';
// 兩本帳 pin（同 crm-cashbook）：清單/專案下拉/新增請款都跟著目前帳本走
import { finEntity as _pinEntity } from '../finance/fin-utils.js';

let _payments = [];
let _projects = [];
let _staffList = [];
let _selectedId = null;
let _editingId = null;
let _filters = { q: '', category: '', payment_status: '', project_id: '', unassigned: false };
/** 批次掛專案（owner 2026-08-23：「專案我可以手動掛，精準為主」）。
 *  `order` 是**畫面上的順序**，Shift 範圍選要靠它 —— 用 _payments 的原順序會在
 *  排序過之後選到完全不相干的一段。 */
let _batch = { on: false, sel: new Set(), order: [], last: null };
let _csvFile = null;

async function loadPayments() {
    const params = new URLSearchParams();
    if (_filters.q)              params.set('q', _filters.q);
    if (_filters.category)       params.set('category', _filters.category);
    if (_filters.payment_status) params.set('payment_status', _filters.payment_status);
    if (_filters.project_id)     params.set('project_id', _filters.project_id);
    if (_filters.unassigned)     params.set('unassigned', '1');
    // 建議只在批次模式要 —— 那是唯一會看它的地方，平常列清單不必付這個計算成本
    if (_batch.on)               params.set('suggest', '1');
    params.set('entity', _pinEntity());
    try { _payments = (await _fetch('/payments?' + params)).payments || []; }
    catch (_) { _payments = []; }
    renderList();
}

async function loadProjects() {
    // 🔴 帶帳本：跨帳本掛專案會被守衛 403，下拉從源頭就別給選（同 cashbook）
    try { _projects = (await _fetch('/projects?entity=' + _pinEntity())).projects || []; } catch(_) { _projects = []; }
    _populateProjectFilter();
}

function _populateProjectFilter() {
    _batchPopulateProjects();
    const sel = document.getElementById('pay-filter-project');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">全部專案</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === current ? ' selected' : ''}>${_esc(p.name)}</option>`).join('');
}

async function loadStaffList() {
    try { _staffList = (await _fetch('/staff')).staff || []; } catch(_) { _staffList = []; }
}

function _statusBadge(s) {
    const cls = s === '已付款' ? 'crm-badge crm-pay-全額到帳' : 'crm-badge crm-pay-未到帳';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

// 應付款 → 已付款:asc 把待處理(應付款)排前面
const _PAY_STATUS_ORDER = ['應付款', '已付款'];
const _sorter = createSortable({
    storageKey: 'crm_payments_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'pay-list-panel',
    onChange: () => renderList(),
    getters: {
        date:     p => p.request_date || '',
        summary:  p => (p.summary || '').toLowerCase(),
        amount:   p => p.amount || 0,
        category: p => (p.category || '').toLowerCase(),
        payee:    p => (p.payee_name || '').toLowerCase(),
        invoice:  p => (p.invoice_number || '').toLowerCase(),
        // 沒掛專案時用**建議**排序 —— 同一個建議的列會聚在一起，Shift 選一段
        // 就掛完一個案子。這是這顆建議真正的用處（不是自動套用）。
        project:  p => (p.project_name || p.suggested?.project_name
                        || p.project_label || '').toLowerCase(),
        status:   p => enumIndex(_PAY_STATUS_ORDER, p.payment_status, '應付款'),
    },
});

function renderList() {
    const body = document.getElementById('pay-list-body');
    if (!body) return;
    _sorter.attach();
    if (_payments.length === 0) {
        body.innerHTML = _quickAddRow()
            + `<div class="crm-empty">尚無請款${_filters.q ? '，請調整搜尋' : ''}</div>`;
        return;
    }
    const rows = _sorter.sorted(_payments);
    _batch.order = rows.map(p => p.id);
    body.innerHTML = (_batch.on ? '' : _quickAddRow()) + rows.map(p => `
        <div class="crm-row pay-row${p.id === _selectedId && !_batch.on ? ' selected' : ''}${
            _batch.on && _batch.sel.has(p.id) ? ' batch-picked' : ''}"
             data-pay-id="${p.id}"
             onclick="window._payRowClick(event, '${p.id}')">
            <span>${p.request_date ? p.request_date.substring(0, 10) : '—'}</span>
            <span style="font-weight:600;color:#e0e0e0;">${_esc(p.summary)}</span>
            <span style="font-weight:600;color:#e0e0e0;">$${_fmtNum(p.amount)}</span>
            <span>${_esc(p.category || '')}</span>
            <span>${_esc(p.payee_name)}</span>
            <span title="${_esc(p.invoice_number || '')}">${_esc(p.invoice_title || p.invoice_number || '')}</span>
            <span>${p.project_name
                ? _esc(p.project_name)
                : (p.suggested
                    ? `<span style="color:${p.suggested.strong ? '#93c5fd' : '#6b7280'};"
                             title="機器建議（相似度 ${p.suggested.score}）—— 要你確認，不會自動掛">
                         建議：${_esc(p.suggested.project_name)}</span>`
                    : _esc(p.project_label || ''))}</span>
            <span>${_statusBadge(p.payment_status)}</span>
            ${kebabMenuHtml(p.id, { onEdit: '_payEdit', onDuplicate: '_payDup', onDelete: '_payDelete' })}
        </div>
    `).join('');
    _batchRefreshBar();
}

// ── 批次掛專案 ────────────────────────────────────────────────

/** 批次模式時點列＝選取，否則照舊開詳情。
 *  Shift＋點＝從上一次點的那列選到這列（照畫面順序）。 */
window._payRowClick = (ev, id) => {
    if (!_batch.on) { window._paySelect(id); return; }
    const order = _batch.order;
    if (ev && ev.shiftKey && _batch.last && order.includes(_batch.last)) {
        const a = order.indexOf(_batch.last), b = order.indexOf(id);
        const [lo, hi] = a < b ? [a, b] : [b, a];
        // 範圍一律**加選**（不是 toggle）—— toggle 會讓中間已選的被取消，
        // 那是使用者最不想要的結果
        for (let i = lo; i <= hi; i++) _batch.sel.add(order[i]);
    } else if (_batch.sel.has(id)) {
        _batch.sel.delete(id);
    } else {
        _batch.sel.add(id);
    }
    _batch.last = id;
    _batchPaint();
};

/** 只把選取狀態刷到既有的列上 —— 不重建 DOM。
 *
 * 🔴 renderList() 會重排 810 列、重建約一萬兩千個節點；選一列就跑一次，
 *    Shift 選一段更是每次都跑。真正變的只有 class。 */
function _batchPaint() {
    document.querySelectorAll('#pay-list-body .pay-row[data-pay-id]').forEach(el => {
        el.classList.toggle('batch-picked', _batch.sel.has(el.dataset.payId));
    });
    _batchRefreshBar();
}

function _batchRefreshBar() {
    const bar = document.getElementById('pay-batch-bar');
    if (!bar) return;
    bar.style.display = _batch.on ? 'flex' : 'none';
    const el = document.getElementById('pay-batch-count');
    if (!el) return;
    const picked = _payments.filter(p => _batch.sel.has(p.id));
    const sum = picked.reduce((n, p) => n + (Number(p.amount) || 0), 0);
    el.innerHTML = picked.length
        ? `已選 <b style="color:#eee;">${picked.length}</b> 張 ／ 合計 <b style="color:#eee;">$${_fmtNum(sum)}</b>`
        : '<span style="color:#6b7280;">點列選取，按住 Shift 可以選一整段</span>';
}

function _batchPopulateProjects() {
    const sel = document.getElementById('pay-batch-project');
    if (!sel) return;
    sel.innerHTML = projectOptionsHtml(_projects, '— 掛到哪個專案 —', sel.value);
}

/** 清掉選取。要不要重畫由呼叫端決定 —— 有的接著 _batchPaint()、有的接著
 *  renderList()、進批次模式那條接著 loadPayments()，用布林參數表達不了。 */
function _batchClearSel() {
    _batch.sel.clear();
    _batch.last = null;
}

function _batchSetMode(on) {
    _batch.on = on;
    _batchClearSel();
    const btn = document.getElementById('pay-btn-batch');
    if (btn) {
        btn.textContent = on ? '離開批次模式' : '批次掛專案';
        btn.classList.toggle('crm-btn-primary', on);
        btn.classList.toggle('crm-btn-secondary', !on);
    }
    if (on) {
        closeDetail();
        _batchPopulateProjects();
        // 🔴 要重抓 —— loadPayments 的 suggest=1 只有在批次模式下才會帶，而剛剛
        //    才把 _batch.on 打開。只 renderList() 的話手上這份沒有 suggested，
        //    「建議：」要等使用者改一次篩選才出現，看起來像功能壞了。
        loadPayments();
    } else {
        renderList();
    }
}

async function _batchApply(projectId) {
    const ids = [..._batch.sel];
    if (!ids.length) { crmToast('還沒選任何一張'); return; }
    try {
        const r = await _fetch('/payments/batch-project', {
            method: 'PATCH',
            body: JSON.stringify({ payment_ids: ids, project_id: projectId || null }),
        });
        crmToast(projectId
            ? `${r.updated} 張已掛到「${r.project_name}」`
            : `${r.updated} 張已解除專案連結`);
        // 🔴 就地更新，不要 loadPayments() —— 批次模式下那會再跑一次 suggest
        //    （生產實測：帶 suggest 272ms vs 不帶 43ms，前端再重畫 810 列約
        //    126ms），而剛掛好的這幾張本來就不該再有建議。補歷史時一個下午要按
        //    幾十次。⚠ 先前這裡寫「2.4 秒」是誤植 —— 那是量到 CRM 分頁初始化的
        //    wall-clock，不是這支端點。
        const done = new Set(ids);
        _payments.forEach(p => {
            if (!done.has(p.id)) return;
            p.project_id = projectId || null;
            p.project_name = projectId ? r.project_name : '';
            if (projectId) delete p.suggested;
        });
        if (_filters.unassigned) _payments = _payments.filter(p => !done.has(p.id) || !projectId);
        _batchClearSel();
        renderList();
    } catch (e) {
        // 後端是整批擋下並說明原因（例如類別不能連結專案）—— 原話顯示，
        // 不要吞成「操作失敗」，那會讓人不知道要取消勾選哪幾張
        // 後端的 409 訊息很長而且是行動指示（要取消勾選哪幾張）—— 給久一點
        crmToast(e.message || '批次掛專案失敗', 8000);
    }
}

function _buildEditFields() {
    // 🔴 清單缺「專案外包」曾讓最大宗的類別（歷史匯入 371/806 筆，46%）在
    // 編輯視窗選不到自己 —— 跟收支明細寫死 27 項少 5 項同一種病。
    const catOpts = [''].concat(_CATEGORIES).map(v => ({value:v, label:v || '—'}));
    const payeeTypeOpts = ['','內部人員','現金','勞報','核銷'].map(v => ({value:v, label:v || '—'}));
    const payeeOpts = [{value:'', label:'— 選擇人員 —'}].concat(
        _staffList.map(s => ({value:s.name, label:s.name + ' (' + s.role + ')'})));
    const statusOpts = [{value:'應付款',label:'應付款'},{value:'已付款',label:'已付款'}];
    const projectOpts = [{value:'',label:'— 選擇專案 —'}].concat(
        _projects.map(pr => ({value:pr.id, label:pr.name + ' (' + (pr.client_short_name || '') + ')'})));
    // 代開的排前面（這個欄位就是為它存在的），其餘照日期新到舊。
    // 標籤帶發票號碼 —— 找發票最常用的就是號碼，不放進去就搜不到。
    const invoiceOpts = [{value:'',label:'— 選擇發票 —'}].concat(
        _invoiceList
            .filter(inv => inv.issue_status !== '作廢' && inv.payment_status !== '作廢')
            .sort((a, b) => (_isKaiInvoice(b) - _isKaiInvoice(a))
                || String(b.invoice_date || '').localeCompare(String(a.invoice_date || '')))
            .map(inv => ({
                value: inv.id,
                label: (inv.invoice_number || '無號') + ' ' + (inv.title || '')
                    + ' $' + (inv.amount_total || 0).toLocaleString('zh-TW')
                    + ' (' + (inv.company_name || '') + ')',
            })));

    return [
        {name:'summary', label:'摘要', type:'text'},
        {name:'amount', label:'金額', type:'number'},
        {name:'category', label:'項目', type:'select', options:catOpts},
        {name:'payee_type', label:'報支項目', type:'select', options:payeeTypeOpts, _group:'payee-type'},
        {name:'request_date', label:'日期', type:'date'},
        // 付款資訊
        {name:'payee_name', label:'收款人', type:'select', options:payeeOpts},
        {name:'_payee_id_display', label:'身分證', type:'readonly'},
        {name:'planned_month', label:'預計付款月', type:'month'},
        // 補充資訊
        {name:'_invoice_sel', label:'代開發票', type:'select', options:invoiceOpts, _group:'invoice'},
        {name:'invoice_number', label:'發票號碼', type:'text', _group:'invoice'},
        {name:'_project_sel', label:'專案', type:'select', options:projectOpts, _group:'project'},
        {name:'notes', label:'附註', type:'text'},
    ];
}

function _wireEditDynamics(p) {
    const content = document.getElementById('pay-detail-content');
    if (!content) return;
    const _fields = _buildEditFields();

    function _findRow(fieldName) {
        const f = _fields.find(x => x.name === fieldName);
        for (const row of content.querySelectorAll('.crm-detail-prop')) {
            const el = row.querySelector(`[data-field="${fieldName}"]`);
            if (el) return row;
            if (f) {
                const label = row.querySelector('.crm-prop-label');
                if (label && label.textContent.trim() === f.label) return row;
            }
        }
        return null;
    }

    const catSel = content.querySelector('[data-field="category"]');
    const payeeSel = content.querySelector('[data-field="payee_name"]');
    const invSel = content.querySelector('[data-field="_invoice_sel"]');

    const payeeTypeRow = _findRow('payee_type');
    const invoiceSelRow = _findRow('_invoice_sel');
    const invoiceNumRow = _findRow('invoice_number');
    const projectRow = _findRow('_project_sel');
    function _toggle() {
        const cat = catSel?.value || '';
        if (payeeTypeRow) payeeTypeRow.style.display = cat === '專案外包' ? '' : 'none';
        if (invoiceSelRow) invoiceSelRow.style.display = cat === '發票代開' ? '' : 'none';
        if (invoiceNumRow) invoiceNumRow.style.display = cat === '發票代開' ? '' : 'none';
        if (projectRow) projectRow.style.display = (_PROJECT_CATEGORIES.includes(cat) || cat === '發票代開') ? '' : 'none';
    }
    _toggle();

    if (catSel) catSel.addEventListener('change', _toggle);

    // 收款人 → 身分證 auto-fill
    if (payeeSel) {
        payeeSel.addEventListener('change', () => {
            const staff = _staffList.find(s => s.name === payeeSel.value);
            const idRow = _findRow('_payee_id_display');
            if (idRow) {
                const val = idRow.querySelector('.crm-prop-value');
                if (val) val.textContent = staff?.id_number || '';
            }
        });
    }

    // 發票選擇 → 自動填發票號碼
    if (invSel) {
        invSel.addEventListener('change', () => {
            const inv = _invoiceList.find(i => i.id === invSel.value);
            const numEl = content.querySelector('[data-field="invoice_number"]');
            if (inv && numEl) numEl.value = inv.invoice_number || '';
        });
    }

    // Set initial value for _invoice_sel and _project_sel
    if (invSel && p.category === '發票代開') {
        const matchInv = _invoiceOf(p);
        if (matchInv) invSel.value = matchInv.id;
    }
    const projSel = content.querySelector('[data-field="_project_sel"]');
    if (projSel && p.project_id) projSel.value = p.project_id;
}

function renderDetail(p) {
    document.getElementById('pay-detail-title').textContent = p.summary;
    const prop = (label, value) => {
        const empty = !value;
        return `<div class="crm-detail-prop"><div class="crm-prop-label">${label}</div><div class="crm-prop-value${empty ? ' empty' : ''}">${empty ? '空' : _esc(String(value))}</div></div>`;
    };
    document.getElementById('pay-detail-content').innerHTML = `
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">請款內容</div>
        ${prop('摘要', p.summary)}
        ${prop('金額', '$' + _fmtNum(p.amount))}
        ${prop('項目', p.category)}
        ${p.category === '專案外包' && p.payee_type ? prop('報支項目', p.payee_type) : ''}
        ${prop('日期', p.request_date ? p.request_date.substring(0, 10) : '')}
        <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">付款資訊</div>
        ${prop('收款人', p.payee_name + (p.payee_id ? ' (' + p.payee_id + ')' : ''))}
        ${prop('預計付款月', p.planned_month)}
        <div class="crm-detail-prop"><div class="crm-prop-label">付款狀態</div><div class="crm-prop-value">${_statusBadge(p.payment_status)}</div></div>
        ${p.payment_status === '已付款' && p.payment_date ? prop('付款日', p.payment_date.substring(0, 10)) : ''}
        <div style="border-top:1px solid #2e2e2e;margin:8px 0;"></div>
        <div style="font-size:12px;font-weight:700;color:#6b7280;padding:4px 0;">補充資訊</div>
        ${p.category === '發票代開' && (p.source_invoice_id || p.invoice_number) ? (() => {
            const inv = _invoiceOf(p);
            return prop('代開發票', inv ? inv.title + ' $' + (inv.amount_total||0).toLocaleString('zh-TW') : p.invoice_number);
        })() : ''}
        ${p.invoice_number ? prop('發票號碼', p.invoice_number) : ''}
        ${p.project_name ? prop('專案', p.project_name) : ''}
        ${prop('附註', p.notes)}
    `;
    // Restore action buttons to default (edit + close)
    const actions = document.getElementById('pay-bar-actions');
    if (actions) {
        actions.innerHTML = `<button id="payable-detail-close" class="crm-detail-close" title="關閉">&#x2715;</button>`;
        actions.querySelector('.crm-detail-close').addEventListener('click', closeDetail);
    }
    addEditButton('pay-bar-actions', () => {
        const editData = { ...p,
            _payee_id_display: p.payee_id || '',
            _invoice_sel: '',
            _project_sel: p.project_id || '',
        };
        enableInlineEdit('pay-detail-content', 'pay-bar-actions', _buildEditFields(), editData,
            async (payload) => {
                payload.amount = parseInt(payload.amount) || 0;
                payload.request_date = payload.request_date || null;
                // Preserve existing payment_status and payment_date (managed by 應付帳款)
                payload.payment_status = p.payment_status || '應付款';
                payload.payment_date = p.payment_date || null;
                // Resolve project_id from the right field depending on category
                if (payload.category === '發票代開') {
                    // 🔴 挑到的發票寫進 source_invoice_id，**不是** project_id。
                    // 舊寫法 `project_id = _invoice_sel` 是把發票 id 塞進專案欄：
                    // 專案欄會指向一個不存在的專案（畫面上空白），同時把使用者
                    // 在「專案」下拉挑的值整個丟掉。
                    payload.source_invoice_id = payload._invoice_sel || null;
                    payload.project_id = payload._project_sel || null;
                    const inv = _invoiceList.find(i => i.id === payload._invoice_sel);
                    if (inv) payload.invoice_number = inv.invoice_number || '';
                } else if (_PROJECT_CATEGORIES.includes(payload.category)) {
                    payload.project_id = payload._project_sel || null;
                } else {
                    payload.project_id = null;
                }
                if (payload.category !== '專案外包') payload.payee_type = '';
                // Resolve payee_id from staff list
                const staff = _staffList.find(s => s.name === payload.payee_name);
                payload.payee_id = staff?.id_number || p.payee_id || '';
                // Clean up internal fields
                delete payload._invoice_sel;
                delete payload._project_sel;
                delete payload._payee_id_display;
                await _fetch('/payments/' + p.id, { method: 'PUT', body: JSON.stringify(payload) });
                const updated = await _fetch('/payments/' + p.id);
                renderDetail(updated);
                await loadPayments();
            },
            () => renderDetail(p)
        );
        _wireEditDynamics(p);
    });
}

async function selectPayment(id) {
    _selectedId = id; renderList();
    document.getElementById('pay-detail-panel').style.display = 'flex';
    document.getElementById('pay-resize-handle').style.display = '';
    if (_invoiceList.length === 0) await _loadInvoiceList();
    try { renderDetail(await _fetch('/payments/' + id)); } catch(_) {}
}

function closeDetail() {
    _selectedId = null;
    document.getElementById('pay-detail-panel').style.display = 'none';
    document.getElementById('pay-resize-handle').style.display = 'none';
    renderList();
}

const _FIELDS = ['summary', 'amount', 'request_date', 'category', 'payee_name', 'payee_id',
    'payee_type', 'invoice_number', 'project_id',
    'payment_date', 'payment_status', 'planned_month', 'notes'];
const _DATE_FIELDS = ['request_date', 'payment_date'];
const _INT_FIELDS = ['amount'];

// 哪些類別可以連結專案 —— 由後端供（core/project_link.PAYMENT_CATEGORIES）。
// 這份只是斷線時的 fallback：寫死在前端的話，改規則要發版，而且零用金／請款／
// 收支三者刻意的差異從程式碼裡看不出來。
let _PROJECT_CATEGORIES = ['專案外包', '專案雜支'];

async function _loadPayOptions() {
    try {
        const o = await _fetch('/payments/options');
        if (o.project_link_categories?.length) _PROJECT_CATEGORIES = o.project_link_categories;
        if (o.categories?.length) _CATEGORIES = o.categories;
    } catch (_) { /* 用 fallback，不擋畫面 */ }
}
// 項目清單**由後端供**（/payments/options 的 categories ＝ 有會計對映的 ∪ 帳上在用的）。
// 🔴 寫死在前端會往兩個方向漂：選得到卻沒有會計對映（那筆錢會變成三表裡的「未歸類
// 科目」），或帳上已經在用卻選不到自己（一打開編輯就被迫改成別的項目）。後者在
// 「專案外包」身上咬過一次 —— 歷史匯入 371/806 筆、46% 的最大宗類別當時不在清單裡。
// 下面這份只是**斷線時的 fallback**，不是正本；快速列與編輯表單共用同一份。
let _CATEGORIES = ['專案外包', '發票代開', '建構', '零用金', '專案雜支', '專案',
    '薪資', '行政', '軟體網路服務', '業務推廣', '設備耗材', '設備維護',
    '獎金', '轉存', '其他'];

/** 列表頂端的快速新增列（比照發票 Tab：打字 → Enter → 直接進一筆）。 */
function _quickAddRow() {
    // 🔴 刻意**不**綁 Enter 送出 —— owner 2026-08-20：這排格子就在列表最上面，
    // 打字時很容易誤按，一按就直接寫進一筆。要新增就按右邊那顆 ＋。
    const opt = (v, sel) => `<option value="${_esc(v)}"${v === sel ? ' selected' : ''}>${_esc(v || '—')}</option>`;
    const cats = _CATEGORIES.map(v => opt(v, '專案外包')).join('');
    const payees = ['<option value="">—</option>'].concat(
        _staffList.map(st => `<option value="${_esc(st.name)}">${_esc(st.name)}</option>`)).join('');
    const projs = ['<option value="">—</option>'].concat(
        _projects.map(p => `<option value="${_esc(p.id)}">${_esc(p.name)}</option>`)).join('');
    return `
      <div class="inv-qa-wrap">
      <div class="crm-row inv-qa">
        <div><input id="pay-qa-date" type="date" value="${today()}"></div>
        <div><input id="pay-qa-summary" class="qa-left" placeholder="＋ 摘要（填完按右側 ＋）"
             title="填好摘要後按這一列最右邊的 ＋ 新增；其餘欄位可留白，之後點該列補齊"></div>
        <div><input id="pay-qa-amount" type="number" min="0" placeholder="金額"></div>
        <div><select id="pay-qa-category" onchange="window._payQuickCat()">${cats}</select></div>
        <div><select id="pay-qa-payee">${payees}</select></div>
        <div><input id="pay-qa-invoice" placeholder="發票號碼"
             title="項目選「發票代開」時才用得到"></div>
        <div><select id="pay-qa-project">${projs}</select></div>
        <div><select id="pay-qa-status">${
            ['應付款', '已付款'].map(v => opt(v, '應付款')).join('')}</select></div>
        <span class="crm-kebab-wrap">
          <button class="crm-btn crm-btn-primary crm-btn-sm inv-qa-btn"
                  title="新增這筆請款" onclick="window._payQuickAdd()">＋</button>
        </span>
      </div>
      </div>`;
}

/** 只有「發票代開」才用得到發票號碼欄 —— 其餘類別把它變灰，避免亂填。 */
window._payQuickCat = function () {
    const inv = document.getElementById('pay-qa-invoice');
    if (!inv) return;
    const on = document.getElementById('pay-qa-category')?.value === '發票代開';
    inv.disabled = !on;
    inv.style.opacity = on ? '' : '.35';
    if (!on) inv.value = '';
};

window._payQuickAdd = async function () {
    const val = (id) => (document.getElementById(id)?.value || '').trim();
    const summary = val('pay-qa-summary');
    if (!summary) { document.getElementById('pay-qa-summary')?.focus(); return; }  // 空列不送

    const category = val('pay-qa-category') || '專案外包';
    const status = val('pay-qa-status') || '應付款';
    const reqDate = val('pay-qa-date') || today();
    const payload = {
        summary,
        request_date: reqDate,
        amount: parseInt(val('pay-qa-amount'), 10) || 0,
        category,
        payee_name: val('pay-qa-payee'),
        payment_status: status,
        // 已付款要有付款日，否則現金側查不到這筆什麼時候出去的
        payment_date: status === '已付款' ? reqDate : null,
        // 應付款要有預計付款月，否則不會出現在應付帳款的月份分組裡
        planned_month: status === '應付款' ? reqDate.substring(0, 7) : '',
    };
    if (category === '發票代開' && val('pay-qa-invoice')) {
        payload.invoice_number = val('pay-qa-invoice');
        payload.needs_invoice = 1;
    }
    if (_PROJECT_CATEGORIES.includes(category) || category === '發票代開') {
        const pid = val('pay-qa-project');
        if (pid) payload.project_id = pid;
    }
    // 收款人若對得上人員庫，順帶帶入身分證與身分別（編輯視窗的既有規則）
    const staff = _staffList.find(st => st.name === payload.payee_name);
    if (staff) {
        if (staff.id_number) payload.payee_id = staff.id_number;
        if (staff.payee_type) payload.payee_type = staff.payee_type;
    }

    const btn = document.querySelector('.inv-qa-btn');
    if (btn) btn.disabled = true;
    try {
        payload.entity = _pinEntity();   // 新增落在目前帳本（後端 _entity_for_write 驗 scope）
        await _fetch('/payments', { method: 'POST', body: JSON.stringify(payload) });
        await loadPayments();
        // 重載後焦點回摘要欄 —— 連續登記（打字、Enter、打字、Enter）不用重新點
        document.getElementById('pay-qa-summary')?.focus();
    } catch (e) {
        alert('新增失敗：' + e.message);
    } finally {
        const b2 = document.querySelector('.inv-qa-btn');
        if (b2) b2.disabled = false;
    }
};
let _invoiceList = [];

/** 「代開發票」選單的來源。
 *
 * 🔴 不能用 payment_type=收款 過濾（owner 2026-08-21：找不到我要的發票）。
 * 資料裡的 payment_type 其實是「款項狀態」那一欄拆出來的方向，代開發票一旦
 * 收了錢／轉撥出去就變成 **付款** —— 於是這個專門用來挑代開發票的選單，
 * 反而看不到 199 張代開發票（生產實測：183 張 payment_type=付款 全部消失，
 * 想找的「cooltech VJ 補開」就是其中一張）。改成全部撈回來，作廢的不列。
 */
async function _loadInvoiceList() {
    try { _invoiceList = (await _fetch('/invoices')).invoices || []; } catch(_) { _invoiceList = []; }
}

const _isKaiInvoice = (inv) => ('' + (inv.category || '')).includes('代開');

/** 這張請款單對應的發票。
 *  後端已經把「舊資料用發票號碼補」那條規則解好了（_invoice_link_for），
 *  所以這裡只認 id —— 前端不該再抄一份號碼比對，那條規則的空號坑要修就得修每一份。 */
const _invoiceOf = (p) => _invoiceList.find(i => i.id === p.source_invoice_id) || null;

function _updateExtraFields(category, invoiceId = '') {
    const invoiceField = document.getElementById('pay-invoice-field');
    const invoiceNumField = document.getElementById('pay-invoice-number-field');
    const projectField = document.getElementById('pay-project-field');
    const payeeTypeField = document.getElementById('pay-payee-type-field');

    // Hide all first
    if (invoiceField) invoiceField.style.display = 'none';
    if (invoiceNumField) invoiceNumField.style.display = 'none';
    if (projectField) projectField.style.display = 'none';
    if (payeeTypeField) {
        payeeTypeField.style.display = category === '專案外包' ? 'flex' : 'none';
    }

    if (category === '發票代開') {
        if (invoiceField) invoiceField.style.display = '';
        if (invoiceNumField) invoiceNumField.style.display = '';
        if (projectField) {
            projectField.style.display = '';
            const lbl = projectField.querySelector('label');
            if (lbl) lbl.innerHTML = '專案';
        }
        const sel = document.getElementById('pay-f-project_id');
        if (sel) {
            const current = invoiceId || sel.value;
            sel.innerHTML = `<option value="">— 選擇發票 —</option>` +
                // 目前掛著的那張一定要在（還沒開號的票也要能對到，不然編輯一存就把發票連結洗掉）
                _invoiceList.filter(inv => inv.issue_status === '已開立' || inv.id === current).map(inv =>
                    `<option value="${inv.id}" data-num="${_esc(inv.invoice_number)}" data-amt="${inv.amount_total || 0}"${inv.id === current ? ' selected' : ''}>${_esc(inv.title)} $${(inv.amount_total||0).toLocaleString('zh-TW')} (${_esc(inv.company_name)})</option>`
                ).join('');
        }
    } else if (_PROJECT_CATEGORIES.includes(category)) {
        if (projectField) {
            projectField.style.display = '';
            const lbl = projectField.querySelector('label');
            if (lbl) lbl.innerHTML = '專案 <span class="crm-required">*</span>';
        }
        // 留住已選的案（openModal 先選好再呼叫這支；重建成空的會把編輯中的專案洗掉、一存就「請選擇專案」）
        _populateProject2Select(document.getElementById('pay-f-project_id2')?.value || '');
    }
}

function _populateProject2Select(selectedId) {
    const sel = document.getElementById('pay-f-project_id2');
    if (!sel) return;
    sel.innerHTML = `<option value="">— 選擇專案 —</option>` +
        _projects.map(p => `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${_esc(p.name)} (${_esc(p.client_short_name || '')})</option>`).join('');
}

function _populatePayeeSelect(selectedName) {
    const sel = document.getElementById('pay-f-payee_name');
    if (!sel) return;
    // 收款人不一定是員工（代開單的收款人＝代開人，多半是外面的人）：目前這個名字不在人員庫也要留著，
    // 不然一開編輯就被清成空白、一存就把收款人洗掉
    const extra = selectedName && !_staffList.some(s => s.name === selectedName)
        ? `<option value="${_esc(selectedName)}" selected>${_esc(selectedName)}</option>` : '';
    sel.innerHTML = `<option value="">— 選擇人員 —</option>` + extra +
        _staffList.map(s => `<option value="${_esc(s.name)}" data-id="${_esc(s.id_number)}"${s.name === selectedName ? ' selected' : ''}>${_esc(s.name)} (${_esc(s.role)})</option>`).join('');
}

function openModal(p = null) {
    _editingId = p ? p.id : null;
    document.getElementById('pay-modal-title').textContent = p ? '編輯請款' : '新增請款';
    const err = document.getElementById('pay-modal-error');
    err.textContent = ''; err.style.display = 'none';
    _populatePayeeSelect(p?.payee_name || '');
    _populateProject2Select(p?.project_id || '');
    for (const f of _FIELDS) {
        const el = document.getElementById('pay-f-' + f);
        if (!el) continue;
        if (f === 'payee_name') continue;
        if (_DATE_FIELDS.includes(f) && p?.[f]) el.value = p[f].substring(0, 10);
        else el.value = p ? (p[f] ?? '') : '';
    }
    if (!p) document.getElementById('pay-f-request_date').value = today();
    // 發票代開：pay-f-project_id 那顆裝的是**發票**，回填要用 source_invoice_id（不是 project_id——
    // 對不到就逼使用者重選發票，存檔時再把發票 id 當專案寫進去；2026-09-07 思沙龍兩張就是這樣壞的）
    _updateExtraFields(p?.category || '', p?.source_invoice_id || '');
    document.getElementById('pay-modal').style.display = 'flex';
}


async function savePayment() {
    const summary = document.getElementById('pay-f-summary').value.trim();
    if (!summary) { _showErr('摘要為必填'); return; }
    const payload = {};
    const cat = document.getElementById('pay-f-category')?.value || '';
    for (const f of _FIELDS) {
        const el = document.getElementById('pay-f-' + f);
        // 表單沒有的欄位（payment_status／payment_date 由應付帳款管）不送：送空字串會把狀態洗成 ''，
        // 應付帳款只認「應付款」就看不到那張單（2026-09-07 思沙龍兩張）。後端 PUT 是 exclude_unset，不送＝保留。
        if (!el && f !== 'project_id') continue;
        let val = el ? el.value.trim() : '';
        if (_INT_FIELDS.includes(f)) val = val ? parseInt(val) : 0;
        if (_DATE_FIELDS.includes(f)) val = val || null;
        if (f === 'project_id') {
            // 專案一律讀 pay-f-project_id2；發票代開那顆 pay-f-project_id 裝的是發票 → 寫進 source_invoice_id，
            // 不是 project_id（寫進 project_id 就是「專案欄指到一張發票」：畫面空白、應付帳款也連不回案）
            val = document.getElementById('pay-f-project_id2')?.value || null;
            if (cat === '發票代開') {
                const invSel = document.getElementById('pay-f-project_id');
                payload.source_invoice_id = invSel?.value || null;
                const opt = invSel?.selectedOptions?.[0];
                if (opt?.dataset?.num && !document.getElementById('pay-f-invoice_number')?.value.trim()) payload.invoice_number = opt.dataset.num;
            }
        }
        payload[f] = val;
    }
    // Auto-set payment_status for new payments
    if (!_editingId) payload.payment_status = '應付款';
    // Validation
    if (_PROJECT_CATEGORIES.includes(cat) && !document.getElementById('pay-f-project_id2')?.value) {
        _showErr('請選擇專案'); return;
    }
    if (cat === '發票代開' && !document.getElementById('pay-f-project_id')?.value) {
        _showErr('請選擇代開發票'); return;
    }
    const btn = document.getElementById('pay-btn-save');
    btn.disabled = true; btn.textContent = '儲存中...';
    try {
        if (_editingId) {
            await _fetch('/payments/' + _editingId, { method: 'PUT', body: JSON.stringify(payload) });
            // Refresh detail panel if this payment is currently selected
            if (_selectedId === _editingId) {
                try { renderDetail(await _fetch('/payments/' + _editingId)); } catch(_) {}
            }
        }
        else await _fetch('/payments', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('pay-modal').style.display = 'none';
        await loadPayments();
    } catch (e) { _showErr(e.message); }
    finally { btn.disabled = false; btn.textContent = '儲存'; }
}

async function deletePayment(p) {
    if (!confirm(`確定刪除「${p.summary}」？`)) return;
    try { await _fetch('/payments/' + p.id, { method: 'DELETE' }); closeDetail(); await loadPayments(); }
    catch (e) { alert(e.message); }
}

function _showErr(msg) { const el = document.getElementById('pay-modal-error'); el.textContent = msg; el.style.display = 'block'; }

// CSV Import
function openImportModal() {
    _csvFile = null;
    document.getElementById('pay-drop-filename').textContent = '';
    const r = document.getElementById('pay-import-result');
    r.style.display = 'none'; r.className = 'crm-import-result';
    document.getElementById('pay-btn-do-import').disabled = true;
    document.getElementById('pay-import-modal').style.display = 'flex';
}
function _setCsvFile(file) {
    _csvFile = file;
    document.getElementById('pay-drop-filename').textContent = file ? file.name : '';
    document.getElementById('pay-btn-do-import').disabled = !file;
}
async function doImport() {
    if (!_csvFile) return;
    const btn = document.getElementById('pay-btn-do-import');
    btn.disabled = true; btn.textContent = '匯入中...';
    try {
        const token = localStorage.getItem('auth_token');
        const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
        const form = new FormData(); form.append('file', _csvFile);
        const res = await fetch('/api/v1/crm/payments/import_csv', { method: 'POST', headers, body: form });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '匯入失敗');
        const data = await res.json();
        const result = document.getElementById('pay-import-result');
        result.className = 'crm-import-result';
        result.innerHTML = `匯入完成<br>新增：<strong>${data.imported}</strong> ／ 跳過：<strong>${data.skipped}</strong>`;
        result.style.display = 'block';
        await loadPayments();
    } catch (e) {
        const result = document.getElementById('pay-import-result');
        result.className = 'crm-import-result crm-import-result-error';
        result.innerHTML = _esc(e.message);
        result.style.display = 'block';
    } finally { btn.disabled = false; btn.textContent = '開始匯入'; }
}

export async function initCrmPaymentsTab() {
    for (const id of ['pay-modal', 'pay-import-modal']) {
        const el = document.getElementById(id);
        if (el) document.body.appendChild(el);
    }
    window._paySelect = selectPayment;
    window._payRefresh = loadPayments;
    window._payEdit = async (id) => { try { openModal(await _fetch('/payments/' + id)); } catch(_) {} };
    window._payDelete = (id) => { const p = _payments.find(x => x.id === id); if (p) deletePayment(p); };
    window._payDup = async (id) => {
        try {
            const p = await _fetch('/payments/' + id);
            openModal(p); _editingId = null;
            document.getElementById('pay-modal-title').textContent = '複製請款';
        } catch (_) {}
    };

    let _t;
    document.getElementById('pay-search').addEventListener('input', e => {
        _filters.q = e.target.value; clearTimeout(_t); _t = setTimeout(loadPayments, 300);
    });
    document.getElementById('pay-filter-cat').addEventListener('change', e => { _filters.category = e.target.value; loadPayments(); });
    document.getElementById('pay-filter-status').addEventListener('change', e => { _filters.payment_status = e.target.value; loadPayments(); });
    document.getElementById('pay-filter-project').addEventListener('change', e => { _filters.project_id = e.target.value; loadPayments(); });
    document.getElementById('pay-filter-unassigned').addEventListener('change', e => {
        _filters.unassigned = e.target.checked; loadPayments();
    });
    document.getElementById('pay-btn-batch').addEventListener('click', () => _batchSetMode(!_batch.on));
    document.getElementById('pay-batch-exit').addEventListener('click', () => _batchSetMode(false));
    document.getElementById('pay-batch-all').addEventListener('click', () => {
        _batch.order.forEach(id => _batch.sel.add(id)); _batchPaint();
    });
    document.getElementById('pay-batch-none').addEventListener('click',
        () => { _batchClearSel(); _batchPaint(); });
    document.getElementById('pay-batch-samesugg').addEventListener('click', () => {
        // 選起「跟剛才點的那張同一個建議」的所有列，並把那個專案帶進下拉。
        // 使用者仍然要自己看過清單再按「掛上去」—— 這裡只省找的功夫。
        const last = _payments.find(p => p.id === _batch.last);
        const name = last?.suggested?.project_name;
        if (!name) { crmToast('先點一張有建議的列'); return; }
        _payments.forEach(p => {
            if (!p.project_name && p.suggested?.project_name === name) _batch.sel.add(p.id);
        });
        const sel = document.getElementById('pay-batch-project');
        if (sel) sel.value = last.suggested.project_id;
        _batchPaint();
    });
    document.getElementById('pay-batch-apply').addEventListener('click', () => {
        const pid = document.getElementById('pay-batch-project').value;
        if (!pid) { crmToast('先選一個專案'); return; }
        _batchApply(pid);
    });
    document.getElementById('pay-batch-clear').addEventListener('click', () => _batchApply(''));

    document.getElementById('pay-btn-add').addEventListener('click', () => openModal());

    // Auto-fill payee_id when payee_name changes
    document.getElementById('pay-f-payee_name').addEventListener('change', e => {
        const opt = e.target.selectedOptions[0];
        document.getElementById('pay-f-payee_id').value = opt?.dataset.id || '';
    });

    // Category → toggle extra fields
    document.getElementById('pay-f-category').addEventListener('change', e => {
        _updateExtraFields(e.target.value);
    });

    // Invoice select → auto-fill invoice number
    document.getElementById('pay-f-project_id').addEventListener('change', e => {
        const cat = document.getElementById('pay-f-category').value;
        if (cat === '發票代開') {
            const inv = _invoiceList.find(i => i.id === e.target.value);
            if (inv) {
                document.getElementById('pay-f-invoice_number').value = inv.invoice_number || '';
                if (!document.getElementById('pay-f-amount').value) {
                    document.getElementById('pay-f-amount').value = inv.amount_total || '';
                }
            }
        }
    });
    document.getElementById('pay-btn-import').addEventListener('click', openImportModal);
    document.getElementById('pay-btn-save').addEventListener('click', savePayment);
    document.getElementById('pay-detail-close').addEventListener('click', closeDetail);
    document.getElementById('pay-btn-do-import').addEventListener('click', doImport);

    document.getElementById('pay-csv-file').addEventListener('change', e => _setCsvFile(e.target.files[0] || null));
    const zone = document.getElementById('pay-drop-zone');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over');
        const f = e.dataTransfer.files[0]; if (f && f.name.endsWith('.csv')) _setCsvFile(f); });

    for (const id of ['pay-modal', 'pay-import-modal']) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
    }

    setupResizeHandle('pay-resize-handle', 'pay-detail-panel');
    // 🔴 選項要**先**拿到再渲染：renderList() 會畫快速新增列，那一列的項目下拉
    //    直接讀 _CATEGORIES。跟 loadPayments() 平行跑的話，先到的通常是清單，
    //    快速新增列就用 fallback 那份畫出來了（少 6 個有對映的項目），
    //    而且不會再重畫 —— 使用者看到的是一份過期的選單。
    await _loadPayOptions();
    await Promise.all([loadPayments(), loadProjects(), loadStaffList(),
                       _loadInvoiceList()]);
}
