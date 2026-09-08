// hr_leave.js — 人事管理 › 請補修（docs/LEAVE_PLAN.md §7.5／§7.6：時數帳＋申請單；2026-09-07 一期重整）
// 四塊：待核佇列（卡片＋餘額／同期／撞場次）/ 請假紀錄（篩選＋代登）/ 時數帳（全員一列一人＋credit 明細＋手開）
// / 假日表（清單＋貼 CSV 匯入）。UI 無 emoji（owner 鐵則）。
// API: /api/v1/hr/leave*、/hr/balances、/hr/credits、/hr/holidays（管理端）；員工自助在 /my.html 走 /api/v1/me/leave/*。
// 🔴 改狀態一律走 approve／reject／cancel_decide 三支，PUT /hr/leave/{id} 不再接受 status。

import { createSortable, sortableTh, enumIndex, today } from '../crm/crm-utils.js';   // today()＝本地今天（別用 toISOString，台北早上八點前會差一天）
import { esc } from '../../js/shared/dom.js';
import { authFetch, tabLoadError } from '../../js/shared/utils.js';

// 字彙 fallback（正本 core/leave_logic.py；執行期以後端回的 vocab 為準，這裡只是還沒拿到時的預設）
const LEAVE_TYPES = ['特休', '補休', '病假', '事假', '公假', '婚假', '喪假', '其他'];
const STATUS_PILL = { '待審': 'pending', '已核准': 'approved', '已退回': 'rejected', '已撤回': 'cancelled', '消假待審': 'pending' };
const PART_LABEL = { all: '整天', am: '上午', pm: '下午', range: '時段' };
const HOLIDAY_KINDS = ['國定假日', '補班日', '颱風假'];
const CREDIT_KINDS = ['特休', '補休', '其他'];
const API = '/api/v1/hr';

let _vocab = { leave_types: LEAVE_TYPES, ledger_types: ['特休', '補休'], request_statuses: Object.keys(STATUS_PILL),
               parts: Object.keys(PART_LABEL), hours_per_day: 8, notice_days: 7 };
let _view = 'queue';
let _staff = [];                 // [{staff_id, name}]（來自 /hr/balances，一列一人）
let _balances = [];              // /hr/balances 的列
let _items = [];                 // 請假紀錄（目前篩選）
let _queue = [];                 // 待審＋消假待審
let _filters = { status: '', staff_id: '', year: new Date().getFullYear() };
let _hyear = new Date().getFullYear();
let _holidays = [];
let _openCredits = new Set();    // 時數帳展開中的 staff_id
let _credits = {};               // staff_id → credit 明細
let _importResult = '';

const el = (id) => document.getElementById(id);
// 這個檔一律用雙斜線註解，不要用 JSDoc 區塊註解：檔頭第 4 行的路徑寫法帶了一個「斜線星號」，
// 只要檔案裡再出現一個「星號斜線」，tests/unit/_srcscan.js_code_only 就會把中間整段當成區塊註解
// 剝掉——真的程式碼會跟著消失，而測試只會說某個常數不見了。
const hget = (path) => authFetch(API + path);
const hpost = (path, body) => authFetch(API + path, { method: 'POST', body: body ?? {} });
const hdel = (path) => authFetch(API + path, { method: 'DELETE' });

async function _fail(r, fallback) {
    const d = await r.json().catch(() => ({}));
    const det = d.detail;
    if (Array.isArray(det)) return det.map(x => x.msg || x.message || JSON.stringify(x)).join('\n');
    if (Array.isArray(d.errors)) return d.errors.map(x => x.msg || x.code).join('\n');
    return (typeof det === 'string' && det) || fallback;
}

const fmtH = (h) => { const n = Number(h || 0); return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, ''); };
const days = (h) => fmtH(Number(h || 0) / (_vocab.hours_per_day || 8));
const hoursText = (h) => `${fmtH(h)} 小時（${days(h)} 天）`;

function _pill(status) {
    return `<span class="hl-pill ${STATUS_PILL[status] || ''}">${esc(status)}</span>`;
}

function _periodText(it) {
    const part = PART_LABEL[it.part] || (it.part ? esc(it.part) : '');
    const tm = it.part === 'range' && it.start_time ? ` ${esc(it.start_time)}–${esc(it.end_time || '')}` : '';
    const range = it.start_date === it.end_date ? esc(it.start_date) : `${esc(it.start_date)} ~ ${esc(it.end_date)}`;
    return `${range}${part ? `（${part}${tm}）` : ''}`;
}

function _staffOptions(selected, placeholder) {
    return (placeholder ? [`<option value="">${placeholder}</option>`] : [])
        .concat(_staff.map(s => `<option value="${esc(s.staff_id)}" ${s.staff_id === selected ? 'selected' : ''}>${esc(s.name)}</option>`))
        .join('');
}

function _typeOptions(selected) {
    return (_vocab.leave_types || LEAVE_TYPES).map(t => `<option ${t === selected ? 'selected' : ''}>${esc(t)}</option>`).join('');
}

function _partOptions(idPrefix) {
    const parts = _vocab.parts || Object.keys(PART_LABEL);
    return `<select id="${idPrefix}-part">${parts.map(p => `<option value="${p}">${PART_LABEL[p] || p}</option>`).join('')}</select>
        <span id="${idPrefix}-range" style="display:none;gap:6px;align-items:center;">
            <input type="time" id="${idPrefix}-st" step="1800"><span style="color:#777;">–</span><input type="time" id="${idPrefix}-et" step="1800">
        </span>`;
}

// ── 時數帳（全員一列一人；也是人員清單與 vocab 的來源）──
async function _loadBalances() {
    const r = await hget('/balances?year=' + _filters.year);
    if (!r.ok) return r.status;
    const d = await r.json();
    if (d.vocab) _vocab = Object.assign({}, _vocab, d.vocab);
    _balances = d.staff || d.items || d.rows || [];
    _staff = _balances.map(s => ({ staff_id: s.staff_id, name: s.staff_name || s.name || '' }));
    return 0;
}

// ── 載入／切換視圖 ──
async function _load() {
    const status = await _loadBalances();
    if (status) {
        el('hl-content').innerHTML = `<div class="hl-empty">${tabLoadError(status, '請補修')}</div>`;
        return;
    }
    await _show(_view);
}

async function _show(view) {
    _view = view;
    document.querySelectorAll('#hl-nav [data-hl-view]').forEach(b => b.classList.toggle('active', b.dataset.hlView === view));
    const host = el('hl-content');
    host.style.cssText = '';
    if (view === 'queue') return _loadQueue();
    if (view === 'records') return _loadRecords();
    if (view === 'balances') {
        // 時數帳每次切進來都重抓：核准／退回／撤回都會動餘額，開分頁時抓的那份早就舊了（探針抓到「可用 24 保留 8」）
        const status = await _loadBalances();
        if (status) { host.innerHTML = `<div class="hl-empty">${tabLoadError(status, '請補修')}</div>`; return; }
        return _renderBalances();
    }
    if (view === 'holidays') return _loadHolidays();
}

// ══════════════════════════ 1. 待核佇列 ══════════════════════════
async function _loadQueue() {
    const [a, b] = await Promise.all([hget('/leave?status=待審'), hget('/leave?status=消假待審')]);
    if (!a.ok) { el('hl-content').innerHTML = `<div class="hl-empty">${tabLoadError(a.status, '請補修')}</div>`; return; }
    const pend = (await a.json()).items || [];
    const canc = b.ok ? ((await b.json()).items || []) : [];
    _queue = pend.concat(canc.filter(x => !pend.some(p => p.id === x.id)));
    _renderQueue();
}

function _queueCard(it) {
    const isCancel = it.status === '消假待審';
    const acts = isCancel
        ? `<button class="hl-btn ok" data-cancel-decide="${esc(it.id)}" data-approve="1">同意消假</button>
           <button class="hl-btn warn" data-cancel-decide="${esc(it.id)}" data-approve="0">不同意</button>`
        : `<button class="hl-btn ok" data-approve-req="${esc(it.id)}">核准</button>
           <button class="hl-btn warn" data-reject-req="${esc(it.id)}">退回</button>`;
    return `<div class="hl-qcard" data-req="${esc(it.id)}">
        <div class="hl-qhead"><b>${esc(it.staff_name)}</b><span>${esc(it.leave_type)}</span>${_pill(it.status)}</div>
        <div class="hl-qline"><span class="k">期間</span>${_periodText(it)}</div>
        <div class="hl-qline"><span class="k">時數</span>${hoursText(it.hours)}</div>
        <div class="hl-qline"><span class="k">事由</span>${esc(it.reason) || '—'}</div>
        ${isCancel ? `<div class="hl-qline"><span class="k">消假理由</span>${esc(it.cancel_note) || '—'}</div>` : ''}
        <div class="hl-qline"><span class="k">送出</span>${esc((it.created_at || '').slice(0, 16).replace('T', ' ')) || '—'}</div>
        <div class="hl-ctx" data-ctx="${esc(it.id)}"><span class="dim">查餘額與同期中…</span></div>
        <div class="hl-qacts">${acts}</div>
    </div>`;
}

function _renderQueue() {
    const cnt = el('hl-nav-queue-cnt');
    if (cnt) cnt.textContent = _queue.length ? String(_queue.length) : '';
    el('hl-content').innerHTML = `<div class="hl-card">
        <h3>待核佇列（${_queue.length}）</h3>
        ${_queue.length ? `<div class="hl-grid">${_queue.map(_queueCard).join('')}</div>` : '<div class="hl-empty">沒有待核的請假單</div>'}
        <div class="hl-note">核准走時數帳的假別會先扣 credit（先到期先扣），不足會擋下；退回與不同意消假都要寫理由，會通知申請人。</div>
    </div>`;
    _bindQueue();
    _queue.forEach(it => _loadCtx(it));
}

// 每張卡各自 lazy 抓 context：餘額夠不夠／同期誰休／撞場次／提前幾天
async function _loadCtx(it) {
    const host = document.querySelector(`#hl-content [data-ctx="${CSS.escape(String(it.id))}"]`);
    if (!host) return;
    let c;
    try {
        const r = await hget(`/leave/${it.id}/context`);
        if (!r.ok) { host.innerHTML = `<span class="dim">餘額／同期查不到（${r.status}）</span>`; return; }
        c = await r.json();
    } catch (_) { host.innerHTML = '<span class="dim">餘額／同期查不到</span>'; return; }
    const lines = [];
    const ledger = (_vocab.ledger_types || ['特休', '補休']).includes(it.leave_type);
    const bal = c.balance;
    if (ledger && bal && typeof bal === 'object') {
        const avail = Number(bal.available ?? 0);
        const short = Number(it.hours || 0) - avail;
        lines.push(short > 0
            ? `<div class="short">餘額不足：可用 ${fmtH(avail)} 小時，短 ${fmtH(short)} 小時</div>`
            : `<div class="ok">餘額足夠：可用 ${fmtH(avail)} 小時（核准後剩 ${fmtH(avail - Number(it.hours || 0))}）</div>`);
    } else if (ledger) {
        lines.push('<div class="dim">餘額：無資料</div>');
    } else {
        lines.push('<div class="dim">此假別不走時數帳</div>');
    }
    const same = c.same_period || [];
    lines.push(same.length
        ? `<div class="amber">同期休假：${same.map(s => `${esc(s.staff_name)}（${esc(s.start)}${s.end && s.end !== s.start ? ' ~ ' + esc(s.end) : ''}）`).join('、')}</div>`
        : '<div class="dim">同期無人休假</div>');
    const shoots = c.shoot_conflicts || [];
    if (shoots.length) lines.push(`<div class="short">撞場次：${shoots.map(s => `${esc(s.date)} ${esc(s.project_name)}`).join('、')}</div>`);
    if (c.notice_days !== undefined && c.notice_days !== null) {
        const need = _vocab.notice_days || 7;
        lines.push(Number(c.notice_days) < need
            ? `<div class="amber">提前 ${c.notice_days} 天送出（不足 ${need} 天）</div>`
            : `<div class="dim">提前 ${c.notice_days} 天送出</div>`);
    }
    host.innerHTML = lines.join('');
}

async function _approve(id) {
    const r = await hpost(`/leave/${id}/approve`);
    if (!r.ok) { alert(await _fail(r, '核准失敗')); return; }
    _loadQueue();
}

async function _reject(id) {
    const note = (prompt('退回理由（必填，會通知申請人）') || '').trim();
    if (!note) return;
    const r = await hpost(`/leave/${id}/reject`, { note });
    if (!r.ok) { alert(await _fail(r, '退回失敗')); return; }
    _loadQueue();
}

async function _cancelDecide(id, approve) {
    let note = '';
    if (!approve) {
        note = (prompt('不同意消假的理由（必填，會通知申請人）') || '').trim();
        if (!note) return;
    } else {
        note = (prompt('備註（選填）') || '').trim();
    }
    const r = await hpost(`/leave/${id}/cancel_decide`, { approve, note });
    if (!r.ok) { alert(await _fail(r, '操作失敗')); return; }
    _loadQueue();
}

function _bindQueue() {
    el('hl-content').onclick = (ev) => {
        const t = ev.target.closest('button');
        if (!t) return;
        if (t.dataset.approveReq) return _approve(t.dataset.approveReq);
        if (t.dataset.rejectReq) return _reject(t.dataset.rejectReq);
        if (t.dataset.cancelDecide) return _cancelDecide(t.dataset.cancelDecide, t.dataset.approve === '1');
    };
    el('hl-content').onchange = null;
}

// ══════════════════════════ 2. 請假紀錄（篩選＋代登） ══════════════════════════
const LEAVE_TH = '<tr>'
    + sortableTh('staff', '人員') + sortableTh('type', '假別') + sortableTh('period', '期間')
    + sortableTh('hours', '時數', 'class="num"') + sortableTh('reason', '事由') + sortableTh('status', '狀態')
    + '<th>操作</th></tr>';

const _recordsSorter = createSortable({
    storageKey: 'hr_leave_records_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'hl-records-table',
    onChange: () => _renderRecords(),
    getters: {
        staff: i => i.staff_name || '',
        type: i => i.leave_type || '',
        period: i => i.start_date || '',
        hours: i => i.hours ?? '',
        reason: i => i.reason || '',
        // 狀態照工作流順序排（待審→已核准→已退回→已撤回→消假待審），不是字串序
        status: i => enumIndex(Object.keys(STATUS_PILL), i.status),
    },
});

async function _loadRecords() {
    const q = new URLSearchParams();
    if (_filters.status) q.set('status', _filters.status);
    if (_filters.staff_id) q.set('staff_id', _filters.staff_id);
    if (_filters.year) q.set('year', _filters.year);
    const r = await hget('/leave?' + q.toString());
    if (!r.ok) { el('hl-content').innerHTML = `<div class="hl-empty">${tabLoadError(r.status, '請補修')}</div>`; return; }
    _items = (await r.json()).items || [];
    _renderRecords();
}

function _leaveRow(it) {
    const notes = [];
    if (it.approved_by) notes.push(`核可：${esc(it.approved_by)}`);
    if (it.reject_note) notes.push(`退回理由：${esc(it.reject_note)}`);
    if (it.cancel_note) notes.push(`消假理由：${esc(it.cancel_note)}`);
    return `<tr>
        <td>${esc(it.staff_name)}</td>
        <td>${esc(it.leave_type)}</td>
        <td>${_periodText(it)}</td>
        <td class="num">${fmtH(it.hours)}<div class="hl-note" style="margin-top:0;">${days(it.hours)} 天</div></td>
        <td>${esc(it.reason) || '—'}</td>
        <td>${_pill(it.status)}${notes.length ? `<div class="hl-note">${notes.join('<br>')}</div>` : ''}</td>
        <td style="white-space:nowrap;"><button class="hl-btn danger" data-del="${esc(it.id)}">刪除</button></td>
    </tr>`;
}

function _renderRecords() {
    const years = [0, -1, -2].map(d => new Date().getFullYear() + d);
    const statuses = _vocab.request_statuses || Object.keys(STATUS_PILL);
    el('hl-content').innerHTML = `<div class="hl-card">
        <h3>請假紀錄</h3>
        <div class="hl-form" style="margin-bottom:10px;">
            <select id="hl-f-status">
                <option value="">全部狀態</option>
                ${statuses.map(s => `<option ${_filters.status === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
            </select>
            <select id="hl-f-staff">${_staffOptions(_filters.staff_id, '全部人員')}</select>
            <select id="hl-f-year">
                ${years.map(y => `<option value="${y}" ${_filters.year === y ? 'selected' : ''}>${y} 年</option>`).join('')}
            </select>
            <button class="hl-btn ghost" id="hl-reload">重新整理</button>
        </div>
        ${_items.length ? `<table id="hl-records-table">
            ${LEAVE_TH}
            ${_recordsSorter.sorted(_items).map(_leaveRow).join('')}
        </table>` : '<div class="hl-empty">此篩選下沒有請假紀錄</div>'}
        <div class="hl-form" style="margin-top:12px;padding-top:12px;border-top:1px solid #333;">
            <span style="color:#888;font-size:12px;">代登：</span>
            <select id="hl-n-staff">${_staffOptions('', '')}</select>
            <select id="hl-n-type">${_typeOptions('')}</select>
            <input type="date" id="hl-n-start">
            <input type="date" id="hl-n-end">
            ${_partOptions('hl-n')}
            <input type="text" id="hl-n-reason" placeholder="事由" style="width:180px;">
            <button class="hl-btn" id="hl-n-add">建立</button>
        </div>
        <div class="hl-note">代登直接進「待審」，仍要在待核佇列核准才會扣時數；時數由起迄與整天／半天／時段算出（週末與假日表不計）。</div>
    </div>`;
    _bindRecords();
    _recordsSorter.attach();
}

function _wirePartToggle(prefix) {
    const sel = el(prefix + '-part'), rng = el(prefix + '-range');
    if (!sel || !rng) return;
    const sync = () => { rng.style.display = sel.value === 'range' ? 'inline-flex' : 'none'; };
    sel.addEventListener('change', sync);
    sync();
}

async function _addRecord() {
    const part = el('hl-n-part').value;
    const body = {
        staff_id: el('hl-n-staff').value,
        leave_type: el('hl-n-type').value,
        start_date: el('hl-n-start').value,
        end_date: el('hl-n-end').value || el('hl-n-start').value,
        part,
        start_time: part === 'range' ? el('hl-n-st').value : null,
        end_time: part === 'range' ? el('hl-n-et').value : null,
        reason: el('hl-n-reason').value.trim(),
    };
    if (!body.staff_id || !body.start_date) { alert('請選人員並填起日'); return; }
    const r = await hpost('/leave', body);
    if (!r.ok) { alert(await _fail(r, '建立失敗')); return; }
    _loadRecords();
}

function _bindRecords() {
    const root = el('hl-content');
    _wirePartToggle('hl-n');
    root.onclick = async (ev) => {
        const t = ev.target.closest('button');
        if (!t) return;
        if (t.dataset.del) {
            if (!confirm('確定刪除此請假單？已核准的會一併釋放扣掉的時數。')) return;
            const r = await hdel('/leave/' + t.dataset.del);
            if (!r.ok) { alert(await _fail(r, '刪除失敗')); return; }
            return _loadRecords();
        }
        if (t.id === 'hl-reload') return _loadRecords();
        if (t.id === 'hl-n-add') return _addRecord();
    };
    root.onchange = (ev) => {
        if (ev.target.id === 'hl-f-status') { _filters.status = ev.target.value; _loadRecords(); }
        if (ev.target.id === 'hl-f-staff') { _filters.staff_id = ev.target.value; _loadRecords(); }
        if (ev.target.id === 'hl-f-year') { _filters.year = parseInt(ev.target.value, 10); _loadRecords(); }
    };
}

// ══════════════════════════ 3. 時數帳 ══════════════════════════
function _bal(s, kind) {
    const src = (s.balances && s.balances[kind]) || s[kind] || {};
    const exp = (src.expiring || [])[0];
    return {
        available: fmtH(src.available ?? 0),
        reserved: fmtH(src.reserved ?? 0),
        expiring: exp ? `${fmtH(exp.hours)} h · ${esc(exp.expires_on)}` : '—',
    };
}

function _balanceRow(s) {
    const a = _bal(s, '特休'), b = _bal(s, '補休');
    const sick = s.sick || {};
    const open = _openCredits.has(s.staff_id);
    const name = s.staff_name || s.name || '';
    return `<tr class="hl-row-click" data-toggle-credits="${esc(s.staff_id)}">
        <td>${esc(name)}</td>
        <td class="num">${a.available}</td><td class="num">${a.reserved}</td><td>${a.expiring}</td>
        <td class="num">${b.available}</td><td class="num">${b.reserved}</td><td>${b.expiring}</td>
        <td class="num">${fmtH(sick.used_days ?? 0)} / ${fmtH(sick.cap_days ?? (_vocab.sick_cap_days || 30))}</td>
        <td><button class="hl-btn ghost" data-toggle-credits="${esc(s.staff_id)}">${open ? '收起' : '明細'}</button></td>
    </tr>
    ${open ? `<tr class="hl-detail"><td colspan="9" data-credits-host="${esc(s.staff_id)}">${_creditsHtml(s.staff_id, name)}</td></tr>` : ''}`;
}

function _creditsHtml(staffId, name) {
    const list = _credits[staffId];
    const granted = today();
    const rows = list === undefined ? '<div class="hl-empty">載入中…</div>'
        : !list.length ? '<div class="hl-empty">尚無 credit</div>'
        : `<table>
            <tr><th>種類</th><th class="num">時數</th><th class="num">已扣</th><th class="num">剩餘</th><th>發放日</th><th>到期日</th><th>來源</th><th>狀態</th><th>說明</th><th></th></tr>
            ${list.map(c => {
                // used／remaining 由後端 credit_dict 算好（前端再算一次會跟核准扣帳的那份漂開）
                const used = Number(c.used || 0), remain = Number(c.remaining ?? (Number(c.hours || 0) - used));
                return `<tr>
                    <td>${esc(c.kind)}</td>
                    <td class="num">${fmtH(c.hours)}</td>
                    <td class="num">${fmtH(used)}</td>
                    <td class="num" style="color:${remain <= 0 ? '#777' : '#ccc'};">${fmtH(remain)}</td>
                    <td>${esc(c.granted_on) || '—'}</td>
                    <td>${esc(c.expires_on) || '—'}</td>
                    <td>${esc(c.source) || '—'}</td>
                    <td>${esc(c.status) || '—'}</td>
                    <td>${esc(c.reason) || esc(c.note) || '—'}</td>
                    <td>${used > 0 ? '' : `<button class="hl-btn danger" data-del-credit="${esc(c.id)}" data-staff="${esc(staffId)}">刪除</button>`}</td>
                </tr>`;
            }).join('')}
        </table>`;
    return `<div style="color:#aaa;font-size:12px;margin-bottom:8px;">${esc(name)} 的 credit 明細</div>
        ${rows}
        <div class="hl-form" data-credit-form="${esc(staffId)}" style="margin-top:10px;padding-top:10px;border-top:1px solid #333;">
            <span style="color:#888;font-size:12px;">手開：</span>
            <select data-c="kind">${CREDIT_KINDS.map(k => `<option>${k}</option>`).join('')}</select>
            <input type="number" data-c="hours" min="0.5" step="0.5" value="8" title="時數">
            <span style="color:#777;font-size:12px;">發放</span><input type="date" data-c="granted" value="${granted}">
            <span style="color:#777;font-size:12px;">到期</span><input type="date" data-c="expires">
            <input type="text" data-c="reason" placeholder="事由（例：加班補休 9/1 拍攝）" style="width:220px;">
            <button class="hl-btn" data-c-add="${esc(staffId)}">建立</button>
        </div>`;
}

function _renderBalances() {
    const years = [0, -1].map(d => new Date().getFullYear() + d);
    el('hl-content').innerHTML = `<div class="hl-card">
        <h3>時數帳（${_filters.year} 年）</h3>
        <div class="hl-form" style="margin-bottom:10px;">
            <select id="hl-b-year">${years.map(y => `<option value="${y}" ${_filters.year === y ? 'selected' : ''}>${y} 年</option>`).join('')}</select>
            <button class="hl-btn ghost" id="hl-b-reload">重新整理</button>
        </div>
        ${_balances.length ? `<table id="hl-balances-table">
            <tr><th rowspan="2">人員</th><th colspan="3" style="text-align:center;">特休（小時）</th><th colspan="3" style="text-align:center;">補休（小時）</th><th rowspan="2" class="num">病假已用（天）</th><th rowspan="2"></th></tr>
            <tr><th class="num">可用</th><th class="num">保留</th><th>最近到期</th><th class="num">可用</th><th class="num">保留</th><th>最近到期</th></tr>
            ${_balances.map(_balanceRow).join('')}
        </table>` : '<div class="hl-empty">人力庫沒有在職人員</div>'}
        <div class="hl-note">可用 = 有效 credit − 已核准扣掉的；保留 = 待審中會扣的；餘額不存快照，每次由 credit 重算。點列展開明細可手開 credit（年度特休、加班補休）。</div>
    </div>`;
    _bindBalances();
    _openCredits.forEach(sid => { if (_credits[sid] === undefined) _loadCredits(sid); });
}

async function _loadCredits(staffId) {
    const r = await hget('/credits?staff_id=' + encodeURIComponent(staffId));
    _credits[staffId] = r.ok ? ((await r.json()).items || []) : [];
    const host = document.querySelector(`#hl-content [data-credits-host="${CSS.escape(String(staffId))}"]`);
    if (host) {
        const s = _balances.find(x => x.staff_id === staffId) || {};
        host.innerHTML = _creditsHtml(staffId, s.staff_name || s.name || '');
    }
}

// 手開 credit：欄位一律從**這個人自己那張表單**讀（同時展開兩個人時，id 會撞到彼此的值）。
async function _addCredit(staffId, btn) {
    const form = btn ? btn.closest('[data-credit-form]')
        : document.querySelector(`#hl-content [data-credit-form="${CSS.escape(String(staffId))}"]`);
    if (!form) return;
    const f = (k) => form.querySelector(`[data-c="${k}"]`)?.value ?? '';
    const body = {
        staff_id: staffId,
        kind: f('kind'),
        hours: parseFloat(f('hours') || '0'),
        granted_on: f('granted'),
        expires_on: f('expires') || null,
        reason: f('reason').trim(),
    };
    if (!(body.hours > 0) || !body.granted_on) { alert('請填時數與發放日'); return; }
    const r = await hpost('/credits', body);
    if (!r.ok) { alert(await _fail(r, '建立失敗')); return; }
    delete _credits[staffId];
    await _loadBalances();
    _renderBalances();
}

function _bindBalances() {
    const root = el('hl-content');
    root.onclick = async (ev) => {
        const btn = ev.target.closest('button');
        if (btn && btn.dataset.delCredit) {
            if (!confirm('確定刪除此 credit？（只有沒被扣過的才能刪）')) return;
            const r = await hdel('/credits/' + btn.dataset.delCredit);
            if (!r.ok) { alert(await _fail(r, '刪除失敗')); return; }
            delete _credits[btn.dataset.staff];
            await _loadBalances();
            return _renderBalances();
        }
        if (btn && btn.dataset.cAdd) return _addCredit(btn.dataset.cAdd, btn);
        if (btn && btn.id === 'hl-b-reload') { _credits = {}; await _loadBalances(); return _renderBalances(); }
        if (ev.target.closest('.hl-detail')) return;      // 明細列內的點擊不收合
        const tog = ev.target.closest('[data-toggle-credits]');
        if (tog) {
            const sid = tog.dataset.toggleCredits;
            if (_openCredits.has(sid)) _openCredits.delete(sid); else _openCredits.add(sid);
            return _renderBalances();
        }
    };
    root.onchange = async (ev) => {
        if (ev.target.id === 'hl-b-year') { _filters.year = parseInt(ev.target.value, 10); _credits = {}; await _loadBalances(); _renderBalances(); }
    };
}

// ══════════════════════════ 4. 假日表 ══════════════════════════
async function _loadHolidays() {
    const r = await hget('/holidays?year=' + _hyear);
    if (!r.ok) { el('hl-content').innerHTML = `<div class="hl-empty">${tabLoadError(r.status, '請補修')}</div>`; return; }
    const d = await r.json();
    _holidays = (d.items || d.holidays || []).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
    _renderHolidays();
}

function _renderHolidays() {
    const years = [1, 0, -1].map(d => new Date().getFullYear() + d);
    el('hl-content').innerHTML = `<div class="hl-card">
        <h3>假日表（${_hyear} 年，${_holidays.length} 天）</h3>
        <div class="hl-form" style="margin-bottom:10px;">
            <select id="hl-h-year">${years.map(y => `<option value="${y}" ${_hyear === y ? 'selected' : ''}>${y} 年</option>`).join('')}</select>
            <input type="date" id="hl-h-date">
            <input type="text" id="hl-h-name" placeholder="名稱（例：端午節）" style="width:160px;">
            <select id="hl-h-kind">${HOLIDAY_KINDS.map(k => `<option>${k}</option>`).join('')}</select>
            <button class="hl-btn" id="hl-h-add">新增</button>
        </div>
        ${_holidays.length ? `<table id="hl-holidays-table">
            <tr><th>日期</th><th>名稱</th><th>種類</th><th></th></tr>
            ${_holidays.map(h => `<tr>
                <td>${esc(h.date)}</td><td>${esc(h.name) || '—'}</td><td>${esc(h.kind) || '—'}</td>
                <td><button class="hl-btn danger" data-del-holiday="${esc(h.date)}">刪除</button></td>
            </tr>`).join('')}
        </table>` : '<div class="hl-empty">此年度沒有假日資料——貼行政院行事曆 CSV 匯入</div>'}
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid #333;">
            <div style="color:#888;font-size:12px;margin-bottom:6px;">貼上行政院行事曆 CSV（欄：西元日期、星期、是否放假、備註）：</div>
            <textarea id="hl-h-csv" rows="5" placeholder="西元日期,星期,是否放假,備註&#10;20260101,四,2,開國紀念日"></textarea>
            <div class="hl-form" style="margin-top:6px;">
                <button class="hl-btn" id="hl-h-import">匯入</button>
                <span id="hl-h-import-result" style="color:#aaa;font-size:12px;">${esc(_importResult)}</span>
            </div>
        </div>
        <div class="hl-note">假日表決定請假時數怎麼算：週一～五且不在表內才算工作日；補班日的週六算工作日；颱風假當日已核准的假不可消。</div>
    </div>`;
    _bindHolidays();
}

async function _importHolidays() {
    const csv = el('hl-h-csv').value;
    if (!csv.trim()) { alert('先貼 CSV 內容'); return; }
    const r = await hpost('/holidays/import', { csv });
    if (!r.ok) { alert(await _fail(r, '匯入失敗')); return; }
    const d = await r.json();
    const parts = Object.entries(d).filter(([, v]) => typeof v === 'number').map(([k, v]) => `${k} ${v}`);
    _importResult = parts.length ? `匯入完成：${parts.join('、')}` : '匯入完成';
    _loadHolidays();
}

function _bindHolidays() {
    const root = el('hl-content');
    root.onclick = async (ev) => {
        const t = ev.target.closest('button');
        if (!t) return;
        if (t.dataset.delHoliday) {
            if (!confirm(`刪除 ${t.dataset.delHoliday}？`)) return;
            const r = await hdel('/holidays/' + t.dataset.delHoliday);
            if (!r.ok) { alert(await _fail(r, '刪除失敗')); return; }
            return _loadHolidays();
        }
        if (t.id === 'hl-h-add') {
            const body = { date: el('hl-h-date').value, name: el('hl-h-name').value.trim(), kind: el('hl-h-kind').value };
            if (!body.date) { alert('請填日期'); return; }
            const r = await hpost('/holidays', body);
            if (!r.ok) { alert(await _fail(r, '新增失敗')); return; }
            return _loadHolidays();
        }
        if (t.id === 'hl-h-import') return _importHolidays();
    };
    root.onchange = (ev) => {
        if (ev.target.id === 'hl-h-year') { _hyear = parseInt(ev.target.value, 10); _loadHolidays(); }
    };
}

// ── 進入點 ──
export async function initHrLeaveTab() {
    const nav = el('hl-nav');
    if (nav && !nav.dataset.bound) {
        nav.dataset.bound = '1';
        nav.onclick = (ev) => {
            const b = ev.target.closest('[data-hl-view]');
            if (b) _show(b.dataset.hlView);
        };
    }
    await _load();
}
