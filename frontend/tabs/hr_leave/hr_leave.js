// hr_leave.js — 人事管理 › 請補修（N-hr H2 極簡請假；2026-07-22 自「出缺勤」改名）
// 三塊：待核佇列 / 請假紀錄（篩選+代登） / 特休額度。UI 無 emoji（owner 鐵則）。
// API: /api/v1/hr/leave*（管理端）；員工自助在 /my.html 走 /api/v1/me/leave。

import { createSortable, sortableTh, enumIndex } from '../crm/crm-utils.js';
import { tabLoadError } from '../../js/shared/utils.js';

const LEAVE_TYPES = ['特休', '病假', '事假', '公假', '婚假', '喪假', '其他'];
const STATUS_PILL = { '待審': 'pending', '已核准': 'approved', '已退回': 'rejected' };

let _items = [];        // 目前篩選下的請假單
let _quota = [];        // 在職人員額度列
let _filters = { status: '', staff_id: '', year: new Date().getFullYear() };

function hfetch(path, opts = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    const tok = localStorage.getItem('auth_token');
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    return fetch(path, Object.assign({}, opts, { headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined }));
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const el = (id) => document.getElementById(id);

async function _load() {
    const q = new URLSearchParams();
    if (_filters.status) q.set('status', _filters.status);
    if (_filters.staff_id) q.set('staff_id', _filters.staff_id);
    if (_filters.year) q.set('year', _filters.year);
    const [lr, qr] = await Promise.all([
        hfetch('/api/v1/hr/leave?' + q.toString()),
        hfetch('/api/v1/hr/leave/quota?year=' + _filters.year),
    ]);
    if (!lr.ok || !qr.ok) {
        el('hl-content').innerHTML =
            `<div class="hl-empty">${tabLoadError(lr.ok ? qr.status : lr.status, '請補修')}</div>`;
        return;
    }
    _items = (await lr.json()).items || [];
    _quota = (await qr.json()).staff || [];
    _render();
}

function _pill(status) {
    return `<span class="hl-pill ${STATUS_PILL[status] || ''}">${esc(status)}</span>`;
}

function _staffOptions(selected) {
    return ['<option value="">全部人員</option>']
        .concat(_quota.map(s => `<option value="${esc(s.staff_id)}" ${s.staff_id === selected ? 'selected' : ''}>${esc(s.name)}</option>`))
        .join('');
}

// 兩張表（待核佇列 / 請假紀錄）共用同一份表頭模板；data-sort-key 相同沒關係，靠 panel 區隔
const LEAVE_TH = '<tr>'
    + sortableTh('staff', '人員') + sortableTh('type', '假別') + sortableTh('period', '期間')
    + sortableTh('days', '天數', 'class="num"') + sortableTh('reason', '事由') + sortableTh('status', '狀態')
    + '<th>操作</th></tr>';

// ── 點欄頭排序：預設 key '' = 不排序、維持後端順序，點了才生效 ──
const _leaveGetters = {
    staff: i => i.staff_name || '',
    type: i => i.leave_type || '',
    period: i => i.start_date || '',
    days: i => i.days ?? '',
    reason: i => i.reason || '',
    // 狀態照工作流順序排（待審→已核准→已退回），不是字串序
    status: i => enumIndex(Object.keys(STATUS_PILL), i.status),
};
const _pendingSorter = createSortable({
    storageKey: 'hr_leave_pending_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'hl-pending-table',
    onChange: () => _render(),
    getters: _leaveGetters,
});
const _recordsSorter = createSortable({
    storageKey: 'hr_leave_records_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'hl-records-table',
    onChange: () => _render(),
    getters: _leaveGetters,
});
const _quotaSorter = createSortable({
    storageKey: 'hr_leave_quota_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'hl-quota-table',
    onChange: () => _render(),
    getters: {
        name: s => s.name || '',
        role: s => s.role || '',
        annual: s => s.annual ?? '',
        used: s => s.used ?? '',
        remaining: s => s.remaining ?? '',
    },
});

function _leaveRow(it) {
    const acts = [];
    if (it.status === '待審') {
        acts.push(`<button class="hl-btn ok" data-approve="${it.id}">核准</button>`);
        acts.push(`<button class="hl-btn warn" data-reject="${it.id}">退回</button>`);
    }
    acts.push(`<button class="hl-btn danger" data-del="${it.id}">刪除</button>`);
    return `<tr>
        <td>${esc(it.staff_name)}</td>
        <td>${esc(it.leave_type)}</td>
        <td>${esc(it.start_date)} ~ ${esc(it.end_date)}</td>
        <td class="num">${it.days}</td>
        <td>${esc(it.reason) || '—'}</td>
        <td>${_pill(it.status)}${it.approved_by ? `<div class="hl-note">核可：${esc(it.approved_by)}</div>` : ''}</td>
        <td style="white-space:nowrap;display:flex;gap:6px;">${acts.join('')}</td>
    </tr>`;
}

function _render() {
    const pending = _items.filter(i => i.status === '待審');
    const years = [0, -1].map(d => new Date().getFullYear() + d);
    el('hl-content').innerHTML = `
        <h2>請補修</h2>
        <div class="hl-sub">極簡請假：員工從個人工作台送單、這裡簽核。額度即時計算（特休），不做打卡。</div>

        <div class="hl-card">
            <h3>待核佇列（${pending.length}）</h3>
            ${pending.length ? `<table id="hl-pending-table">
                ${LEAVE_TH}
                ${_pendingSorter.sorted(pending).map(_leaveRow).join('')}
            </table>` : `<div class="hl-empty">沒有待核的請假單</div>`}
        </div>

        <div class="hl-card">
            <h3>請假紀錄</h3>
            <div class="hl-form" style="margin-bottom:10px;">
                <select id="hl-f-status">
                    <option value="">全部狀態</option>
                    ${Object.keys(STATUS_PILL).map(s => `<option ${_filters.status === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
                <select id="hl-f-staff">${_staffOptions(_filters.staff_id)}</select>
                <select id="hl-f-year">
                    ${years.map(y => `<option value="${y}" ${_filters.year === y ? 'selected' : ''}>${y} 年</option>`).join('')}
                </select>
                <button class="hl-btn ghost" id="hl-reload">重新整理</button>
            </div>
            ${_items.length ? `<table id="hl-records-table">
                ${LEAVE_TH}
                ${_recordsSorter.sorted(_items).map(_leaveRow).join('')}
            </table>` : `<div class="hl-empty">此篩選下沒有請假紀錄</div>`}
            <div class="hl-form" style="margin-top:12px;padding-top:12px;border-top:1px solid #333;">
                <span style="color:#888;font-size:12px;">代登：</span>
                <select id="hl-n-staff">${_quota.map(s => `<option value="${esc(s.staff_id)}">${esc(s.name)}</option>`).join('')}</select>
                <select id="hl-n-type">${LEAVE_TYPES.map(t => `<option>${t}</option>`).join('')}</select>
                <input type="date" id="hl-n-start">
                <input type="date" id="hl-n-end">
                <input type="number" id="hl-n-days" value="1" min="0.5" step="0.5" title="天數">
                <input type="text" id="hl-n-reason" placeholder="事由（選填）" style="width:180px;">
                <button class="hl-btn" id="hl-n-add">建立</button>
            </div>
        </div>

        <div class="hl-card">
            <h3>特休額度（${_filters.year} 年）</h3>
            ${_quota.length ? `<table id="hl-quota-table">
                <tr>${sortableTh('name', '人員')}${sortableTh('role', '職能')}${sortableTh('annual', '年度額度（天）', 'class="num"')}${sortableTh('used', '已休', 'class="num"')}${sortableTh('remaining', '剩餘', 'class="num"')}<th></th></tr>
                ${_quotaSorter.sorted(_quota).map(s => `<tr>
                    <td>${esc(s.name)}</td><td>${esc(s.role) || '—'}</td>
                    <td class="num"><input type="number" min="0" step="1" value="${s.annual ?? ''}" placeholder="未設定" data-quota-input="${esc(s.staff_id)}"></td>
                    <td class="num">${s.used}</td>
                    <td class="num" style="color:${s.remaining < 0 ? '#f87171' : '#ccc'};">${s.remaining ?? '—'}</td>
                    <td><button class="hl-btn ghost" data-quota-save="${esc(s.staff_id)}">儲存</button></td>
                </tr>`).join('')}
            </table>` : `<div class="hl-empty">人力庫沒有在職人員</div>`}
            <div class="hl-note">已休 = 該年度「已核准」特休合計；剩餘 = 額度 − 已休（負數表示超休）。</div>
        </div>`;
    _bind();
    // 點欄頭排序（attach 對不存在的表是 no-op）
    _pendingSorter.attach();
    _recordsSorter.attach();
    _quotaSorter.attach();
}

async function _setStatus(id, status) {
    const r = await hfetch('/api/v1/hr/leave/' + id, { method: 'PUT', body: { status } });
    if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        alert(d.detail || '操作失敗');
    }
    _load();
}

function _bind() {
    const root = el('hl-content');
    root.onclick = async (ev) => {
        const t = ev.target;
        if (t.dataset.approve) return _setStatus(t.dataset.approve, '已核准');
        if (t.dataset.reject) return _setStatus(t.dataset.reject, '已退回');
        if (t.dataset.del) {
            if (!confirm('確定刪除此請假單？')) return;
            await hfetch('/api/v1/hr/leave/' + t.dataset.del, { method: 'DELETE' });
            return _load();
        }
        if (t.id === 'hl-reload') return _load();
        if (t.id === 'hl-n-add') {
            const body = {
                staff_id: el('hl-n-staff').value,
                leave_type: el('hl-n-type').value,
                start_date: el('hl-n-start').value,
                end_date: el('hl-n-end').value,
                days: parseFloat(el('hl-n-days').value || '0'),
                reason: el('hl-n-reason').value,
            };
            const r = await hfetch('/api/v1/hr/leave', { method: 'POST', body });
            if (!r.ok) {
                const d = await r.json().catch(() => ({}));
                alert(d.detail || '建立失敗');
                return;
            }
            return _load();
        }
        if (t.dataset.quotaSave) {
            const sid = t.dataset.quotaSave;
            const inp = root.querySelector(`input[data-quota-input="${sid}"]`);
            const v = (inp?.value ?? '').trim();
            const r = await hfetch(`/api/v1/hr/staff/${sid}/annual_leave`, {
                method: 'PUT',
                body: { annual_leave_days: v === '' ? null : parseInt(v, 10) },
            });
            if (!r.ok) { alert('儲存失敗'); return; }
            return _load();
        }
    };
    root.onchange = (ev) => {
        if (ev.target.id === 'hl-f-status') { _filters.status = ev.target.value; _load(); }
        if (ev.target.id === 'hl-f-staff') { _filters.staff_id = ev.target.value; _load(); }
        if (ev.target.id === 'hl-f-year') { _filters.year = parseInt(ev.target.value, 10); _load(); }
    };
}

export async function initHrLeaveTab() {
    await _load();
}
