// hr_payroll.js — 人事管理 › 薪資（docs/PAYROLL_OVERTIME_PLAN.md 第一批）
// 三塊：每月薪資單（產生草稿 → 手填 → 確認長請款單 → 匯出印領清冊）/ 薪資主檔（一人一段，調薪新增一段）/ 費率表（一年一份）。
// API: /api/v1/hr/payroll/*（整支 hr_payroll 鑰匙）。UI 無 emoji（owner 鐵則）。這個檔一律用雙斜線註解（同 hr_leave.js 的理由）。

import { esc } from '../../js/shared/dom.js';
import { authDownload, authFetch, tabLoadError } from '../../js/shared/utils.js';

const API = '/api/v1/hr/payroll';
const el = (id) => document.getElementById(id);
const pget = (path) => authFetch(API + path);
const ppost = (path, body) => authFetch(API + path, { method: 'POST', body: body ?? {} });
const pput = (path, body) => authFetch(API + path, { method: 'PUT', body: body ?? {} });
const pdel = (path) => authFetch(API + path, { method: 'DELETE' });

let _view = 'runs';
let _vocab = { pay_types: ['月薪', '時薪', '日薪'], payroll_entities: ['公司', '代發'], line_editable: [], overtime_multiplier: {}, legal_overtime_min: {} };
let _runs = [];
let _run = null;               // 目前打開的薪資單（含 lines）
let _profiles = null;          // /profiles 的 staff 陣列
let _profMonth = '';
let _openHist = new Set();     // 主檔展開歷史段的 staff_id
let _editProfile = null;       // {staff_id, name, profile|null} 正在填的表單
let _rates = null;             // /rates 回的整包
let _msg = { text: '', err: false };

const fmt = (n) => Number(n || 0).toLocaleString('zh-TW');
const fmtH = (h) => { const n = Number(h || 0); return Number.isInteger(n) ? String(n) : n.toFixed(1); };
const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };

async function _fail(r, fallback) {
    const d = await r.json().catch(() => ({}));
    const det = d.detail;
    if (Array.isArray(det)) return det.map(x => x.msg || x.message || JSON.stringify(x)).join('\n');
    return (typeof det === 'string' && det) || fallback;
}
function _say(text, err = false) { _msg = { text, err }; const m = el('hp-msg'); if (m) { m.textContent = text; m.className = 'hp-msg' + (err ? ' err' : ''); } }

// ── 每月薪資單 ────────────────────────────────────────────────────────────

async function _loadRuns() {
    const r = await pget('/runs');
    if (!r.ok) throw new Error(await _fail(r, '載入薪資單失敗'));
    const d = await r.json();
    _runs = d.runs || [];
    if (d.vocab) _vocab = { ..._vocab, ...d.vocab };
}

async function _openRun(id) {
    const r = await pget('/runs/' + encodeURIComponent(id));
    if (!r.ok) { _say(await _fail(r, '打不開這張薪資單'), true); return; }
    const d = await r.json();
    _run = d.run; if (d.vocab) _vocab = { ..._vocab, ...d.vocab };
    _renderRuns();
}

const COLS = [
    ['work_hours', '時數', 'h'], ['base_pay', '底薪'], ['meal_allowance', '伙食費'], ['overtime_pay', '加班費', 'e'], ['bonus_pay', '獎金', 'e'],
    ['leave_deduction', '請假扣薪', 'e'], ['gross_pay', '應發', 'b'], ['labor_self', '勞保自負'], ['health_self', '健保自負'], ['pension_self', '勞退自提'],
    ['other_deduction', '其他扣款', 'e'], ['net_pay', '實發', 'b'], ['employer_total', '公司總成本'],
];

function _lineRow(ln) {
    const draft = _run.status === '草稿';
    const manual = new Set(ln.manual_fields || []);
    const cell = (k, kind) => {
        if (kind === 'h') {
            if (ln.pay_type === '月薪') return '<td class="num" style="color:#666;">-</td>';
            return `<td class="num"><input class="cell${manual.has(k) ? ' manual' : ''}" type="number" step="0.5" min="0" data-line="${esc(ln.id)}" data-k="work_hours" value="${fmtH(ln.work_hours)}" ${draft ? '' : 'disabled'}></td>`;
        }
        if (kind === 'e') return `<td class="num"><input class="cell${manual.has(k) ? ' manual' : ''}" type="number" step="1" min="0" data-line="${esc(ln.id)}" data-k="${k}" value="${ln[k]}" ${draft ? '' : 'disabled'}></td>`;
        return `<td class="num"${kind === 'b' ? ' style="color:#fff;font-weight:600;"' : ''}>${fmt(ln[k])}</td>`;
    };
    return `<tr>
        <td>${esc(ln.staff_name)}<div style="color:#777;font-size:11px;">${esc(ln.pay_type)}${ln.pay_type === '時薪' ? ` ${fmt(ln.base_amount)}/h` : ''}${ln.payroll_entity === '代發' ? ' <span class="hp-pill proxy">代發</span>' : ''}</div></td>
        ${COLS.map(([k, , kind]) => cell(k, kind)).join('')}
        <td><input class="cell" style="width:120px;text-align:left;" type="text" data-line="${esc(ln.id)}" data-k="note" value="${esc(ln.note || '')}" placeholder="備註" ${draft ? '' : 'disabled'}></td>
        <td>${ln.payment_request_id ? '<span class="hp-pill done">請款單已開</span>' : (ln.bank_account ? '' : '<span class="hp-pill none">沒銀行帳號</span>')}</td>
    </tr>`;
}

function _runDetailHtml() {
    if (!_run) return '';
    const t = _run.totals || {};
    const draft = _run.status === '草稿';
    return `<div class="hp-card">
        <h3>${esc(_run.month)} 薪資單 <span class="hp-pill ${draft ? 'draft' : 'done'}">${esc(_run.status)}</span>
            <span class="hp-msg" id="hp-msg"></span><span class="sp"></span>
            ${draft ? '<button class="hp-btn ghost" data-act="refresh" title="主檔或費率改了之後按這顆；手填的欄位會留著">重新帶入</button>' : ''}
            <button class="hp-btn ghost" data-act="export">匯出印領清冊</button>
            ${draft ? '<button class="hp-btn ok" data-act="confirm">確認本月薪資單</button><button class="hp-btn danger" data-act="delete">刪掉草稿</button>' : ''}
        </h3>
        <div class="hp-wrap"><table>
            <tr><th>人員</th>${COLS.map(([, label]) => `<th class="num">${label}</th>`).join('')}<th>備註</th><th></th></tr>
            ${(_run.lines || []).map(_lineRow).join('')}
            <tr class="total"><td>合計 ${_run.count} 人</td>${COLS.map(([k, , kind]) => `<td class="num">${kind === 'h' ? '' : fmt(t[k])}</td>`).join('')}<td></td><td></td></tr>
        </table></div>
        <div class="hp-note">黃框＝手改過的欄（重新帶入不會覆蓋）。時薪制的時數手填，底薪＝時薪 × 時數。應發＝底薪＋伙食費＋加班費＋獎金－請假扣薪；實發＝應發－勞健保自負－勞退自提－其他扣款。
        公司總成本另加雇主勞保（含職災）、健保、勞退 6%。${draft ? '按「確認」後每人自動長一張請款單（應付款、預計下個月付），到財務管理的應付彙總／匯款通知就看得到；確認後這張單不能再改。' : `已由 ${esc(_run.confirmed_by || '')} 確認；改數字請到財務管理的請款單。`}</div>
    </div>`;
}

function _renderRuns() {
    const month = thisMonth();
    const have = new Set(_runs.map(r => r.month));
    el('hp-content').innerHTML = `<div class="hp-card">
        <h3>每月薪資單 <span class="sp"></span>
            <input type="month" id="hp-new-month" value="${have.has(month) ? '' : month}" style="width:150px;">
            <button class="hp-btn" data-act="create">產生薪資單</button></h3>
        ${_runs.length ? `<div class="hp-wrap"><table>
            <tr><th>月份</th><th>狀態</th><th class="num">人數</th><th class="num">實發合計</th><th class="num">公司總成本</th><th></th></tr>
            ${_runs.map(r => `<tr class="hp-row-click${_run && _run.id === r.id ? ' on' : ''}" data-open="${esc(r.id)}">
                <td>${esc(r.month)}</td><td><span class="hp-pill ${r.status === '草稿' ? 'draft' : 'done'}">${esc(r.status)}</span></td>
                <td class="num">${r.count}</td><td class="num">${fmt(r.net_total)}</td><td class="num">${fmt(r.employer_total)}</td><td style="color:#777;">${r.note ? esc(r.note) : ''}</td></tr>`).join('')}
        </table></div>` : '<div class="hp-empty">還沒有薪資單。先到「薪資主檔」填每個人的薪水，再回來按「產生薪資單」。</div>'}
    </div>${_runDetailHtml()}`;
    if (_msg.text) _say(_msg.text, _msg.err);
    _bindRuns();
}

function _bindRuns() {
    const host = el('hp-content');
    host.onclick = async (ev) => {
        const row = ev.target.closest('[data-open]');
        if (row) { _msg = { text: '', err: false }; await _openRun(row.dataset.open); return; }
        const b = ev.target.closest('[data-act]');
        if (!b) return;
        const act = b.dataset.act;
        if (act === 'create') {
            const m = el('hp-new-month').value;
            if (!m) { alert('先選月份'); return; }
            const r = await ppost('/runs', { month: m });
            if (!r.ok) { alert(await _fail(r, '產生失敗')); return; }
            _run = (await r.json()).run; _msg = { text: `已帶入 ${_run.count} 人`, err: false };
            await _loadRuns(); _renderRuns(); return;
        }
        if (!_run) return;
        if (act === 'refresh') {
            const r = await ppost(`/runs/${_run.id}/refresh`);
            if (!r.ok) { _say(await _fail(r, '重算失敗'), true); return; }
            _run = (await r.json()).run; _msg = { text: '已照主檔重算', err: false }; await _loadRuns(); _renderRuns(); return;
        }
        if (act === 'export') { await authDownload(`${API}/runs/${_run.id}/export`, `薪資清冊_${_run.month}.xlsx`, '匯出'); return; }
        if (act === 'confirm') {
            if (!confirm(`確認 ${_run.month} 薪資單？每人會自動長一張請款單，之後不能再改這張單。`)) return;
            const r = await ppost(`/runs/${_run.id}/confirm`);
            if (!r.ok) { _say(await _fail(r, '確認失敗'), true); return; }
            const d = await r.json(); _run = d.run; _msg = { text: `已確認，長出 ${d.payment_requests} 張請款單`, err: false };
            await _loadRuns(); _renderRuns(); return;
        }
        if (act === 'delete') {
            if (!confirm(`刪掉 ${_run.month} 的草稿？主檔不會動，之後可以再產生。`)) return;
            const r = await pdel(`/runs/${_run.id}`);
            if (!r.ok) { _say(await _fail(r, '刪除失敗'), true); return; }
            _run = null; _msg = { text: '', err: false }; await _loadRuns(); _renderRuns();
        }
    };
    host.onchange = async (ev) => {
        const inp = ev.target.closest('input.cell[data-line]');
        if (!inp || !_run) return;
        const k = inp.dataset.k;
        const body = {}; body[k] = k === 'note' ? inp.value : Number(inp.value || 0);
        const r = await pput(`/runs/${_run.id}/lines/${inp.dataset.line}`, body);
        if (!r.ok) { _say(await _fail(r, '存不了'), true); return; }
        const line = (await r.json()).line;
        _run.lines = _run.lines.map(x => x.id === line.id ? line : x);
        _run.totals = {}; for (const [kk] of COLS) _run.totals[kk] = _run.lines.reduce((s, x) => s + Number(x[kk] || 0), 0);
        _msg = { text: `${line.staff_name} 已存`, err: false };
        _renderRuns();
    };
}

// ── 薪資主檔 ──────────────────────────────────────────────────────────────

async function _loadProfiles() {
    const r = await pget('/profiles' + (_profMonth ? '?month=' + _profMonth : ''));
    if (!r.ok) throw new Error(await _fail(r, '載入主檔失敗'));
    const d = await r.json();
    _profiles = d.staff || []; _profMonth = d.month; if (d.vocab) _vocab = { ..._vocab, ...d.vocab };
    const cnt = el('hp-nav-profiles-cnt');
    if (cnt) { const missing = _profiles.filter(s => !s.current).length; cnt.textContent = missing ? `${missing} 人沒填` : ''; }
}

function _profileRow(s) {
    const c = s.current;
    const open = _openHist.has(s.staff_id);
    const main = `<tr class="hp-row-click" data-hist="${esc(s.staff_id)}">
        <td>${esc(s.name)}<div style="color:#777;font-size:11px;">${esc(s.role || '')}${s.employment_type ? '・' + esc(s.employment_type) : ''}</div></td>
        ${c ? `<td>${esc(c.pay_type)}</td><td class="num">${fmt(c.base_amount)}${c.pay_type === '時薪' ? '/h' : (c.pay_type === '日薪' ? '/天' : '')}</td>
            <td class="num">${fmt(c.meal_allowance)}</td><td class="num">${fmt(c.labor_grade)}</td><td class="num">${fmt(c.health_grade)}</td>
            <td class="num">${c.dependents || 0}</td><td class="num">${c.pension_self_rate ? c.pension_self_rate + '%' : '-'}</td>
            <td>${c.payroll_entity === '代發' ? '<span class="hp-pill proxy">代發</span>' : '公司'}</td><td>${c.pay_day} 日</td><td>${esc(c.effective_from)} 起</td>`
            : `<td colspan="10"><span class="hp-pill none">還沒填</span>${s.history.length ? ` <span style="color:#777;">（${esc(s.history[0].effective_from)} 起才有）</span>` : ''}</td>`}
        <td>${s.has_bank ? '' : '<span class="hp-pill none" title="員工檔案沒填銀行帳號，確認薪資單時請款單會沒帳號">無帳號</span>'}
            <button class="hp-btn ghost" data-edit="${esc(s.staff_id)}" ${c ? `data-pid="${esc(c.id)}"` : ''}>${c ? '改這段' : '填'}</button>
            <button class="hp-btn ghost" data-new="${esc(s.staff_id)}" title="調薪：新增一段，從某個月起適用">新增一段</button></td>
    </tr>`;
    if (!open) return main;
    return main + `<tr><td colspan="12" class="hp-hist">${s.history.length ? `<table>
        <tr><th>從</th><th>制度</th><th class="num">底薪</th><th class="num">伙食費</th><th class="num">勞保級距</th><th class="num">健保級距</th><th>誰付</th><th>備註</th><th></th></tr>
        ${s.history.map(h => `<tr><td>${esc(h.effective_from)}</td><td>${esc(h.pay_type)}</td><td class="num">${fmt(h.base_amount)}</td><td class="num">${fmt(h.meal_allowance)}</td>
            <td class="num">${fmt(h.labor_grade)}</td><td class="num">${fmt(h.health_grade)}</td><td>${esc(h.payroll_entity)}</td><td style="white-space:normal;">${esc(h.note || '')}</td>
            <td><button class="hp-btn ghost" data-edit="${esc(s.staff_id)}" data-pid="${esc(h.id)}">改</button> <button class="hp-btn danger" data-delp="${esc(h.id)}">刪</button></td></tr>`).join('')}
        </table>` : '<span style="color:#777;">沒有任何一段</span>'}</td></tr>`;
}

function _profileFormHtml() {
    if (!_editProfile) return '';
    const p = _editProfile.profile || {};
    const v = (k, d) => (p[k] !== undefined && p[k] !== null && p[k] !== '') ? p[k] : d;
    const opts = (list, cur) => list.map(x => `<option value="${x}" ${x === cur ? 'selected' : ''}>${x}</option>`).join('');
    return `<div class="hp-card" id="hp-pform">
        <h3>${_editProfile.profile ? '改' : '新增'}：${esc(_editProfile.name)} 的薪資 <span class="sp"></span><button class="hp-btn ghost" data-act="pcancel">取消</button></h3>
        <div class="hp-form">
            <label>從哪個月起<input type="month" data-p="effective_from" value="${esc(v('effective_from', thisMonth()))}"></label>
            <label>制度<select data-p="pay_type">${opts(_vocab.pay_types, v('pay_type', '月薪'))}</select></label>
            <label>底薪（月薪＝每月；時薪＝每小時；日薪＝每天）<input type="number" min="0" step="1" data-p="base_amount" value="${v('base_amount', 0)}"></label>
            <label>伙食費（免稅上限 ${fmt(_vocab.meal_tax_free || 3000)}）<input type="number" min="0" step="1" data-p="meal_allowance" value="${v('meal_allowance', 0)}"></label>
            <label>勞保投保級距（留空＝照底薪＋伙食費帶）<input type="number" min="0" step="100" data-p="labor_grade" value="${p.labor_grade || ''}"></label>
            <label>健保投保級距（留空＝同上）<input type="number" min="0" step="100" data-p="health_grade" value="${p.health_grade || ''}"></label>
            <label>健保眷屬加保人數<input type="number" min="0" max="9" step="1" data-p="dependents" value="${v('dependents', 0)}"></label>
            <label>勞退自提 %（0–6）<input type="number" min="0" max="6" step="0.5" data-p="pension_self_rate" value="${v('pension_self_rate', 0)}"></label>
            <label>誰付<select data-p="payroll_entity">${opts(_vocab.payroll_entities, v('payroll_entity', '公司'))}</select></label>
            <label>每月幾號匯款<input type="number" min="1" max="31" step="1" data-p="pay_day" value="${v('pay_day', 5)}"></label>
            <label class="full">備註<input type="text" data-p="note" value="${esc(v('note', ''))}"></label>
        </div>
        <div class="hp-acts"><button class="hp-btn ok" data-act="psave">儲存</button><span class="hp-msg" id="hp-pmsg"></span></div>
        <div class="hp-note">「代發」＝股東自己的人，公司只是過帳，請款單會用「代發薪資」分類、不算公司費用。調薪不要改舊的那段，用「新增一段」從新月份起。</div>
    </div>`;
}

function _renderProfiles() {
    const months = [-1, 0, 1].map(d => { const t = new Date(); t.setMonth(t.getMonth() + d); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}`; });
    if (!months.includes(_profMonth)) months.unshift(_profMonth);
    el('hp-content').innerHTML = _profileFormHtml() + `<div class="hp-card">
        <h3>薪資主檔（${esc(_profMonth)} 適用）<span class="sp"></span>
            <select id="hp-pm">${months.map(m => `<option value="${m}" ${m === _profMonth ? 'selected' : ''}>${m}</option>`).join('')}</select></h3>
        <div class="hp-wrap"><table>
            <tr><th>人員</th><th>制度</th><th class="num">底薪</th><th class="num">伙食費</th><th class="num">勞保級距</th><th class="num">健保級距</th><th class="num">眷屬</th><th class="num">勞退自提</th><th>誰付</th><th>匯款日</th><th>適用</th><th></th></tr>
            ${(_profiles || []).map(_profileRow).join('')}
        </table></div>
        <div class="hp-note">一人一段：每段從某個月起適用到下一段為止。點列展開歷史段。投保級距留空會照底薪＋伙食費自動帶（時薪制先帶最低級距，請自己改）。銀行帳號在「員工檔案」填。</div>
    </div>`;
    _bindProfiles();
}

function _bindProfiles() {
    const host = el('hp-content');
    host.onclick = async (ev) => {
        const b = ev.target.closest('button');
        if (b && b.dataset.edit !== undefined) {
            const s = _profiles.find(x => x.staff_id === b.dataset.edit);
            const prof = b.dataset.pid ? (s.history.find(h => h.id === b.dataset.pid) || s.current) : null;
            _editProfile = { staff_id: s.staff_id, name: s.name, profile: prof }; _renderProfiles(); window.scrollTo({ top: 0 }); return;
        }
        if (b && b.dataset.new !== undefined) {
            const s = _profiles.find(x => x.staff_id === b.dataset.new);
            const base = s.current ? { ...s.current, id: undefined, effective_from: thisMonth() } : null;
            _editProfile = { staff_id: s.staff_id, name: s.name, profile: null, prefill: base }; if (base) _editProfile.profile = null;
            _renderProfiles();
            if (base) for (const [k, val] of Object.entries(base)) { const i = document.querySelector(`#hp-pform [data-p="${k}"]`); if (i && val !== undefined && val !== null) i.value = val; }
            window.scrollTo({ top: 0 }); return;
        }
        if (b && b.dataset.delp) {
            if (!confirm('刪掉這一段主檔？')) return;
            const r = await pdel('/profiles/' + b.dataset.delp);
            if (!r.ok) { alert(await _fail(r, '刪不掉')); return; }
            await _loadProfiles(); _renderProfiles(); return;
        }
        if (b && b.dataset.act === 'pcancel') { _editProfile = null; _renderProfiles(); return; }
        if (b && b.dataset.act === 'psave') { await _saveProfile(); return; }
        const row = ev.target.closest('[data-hist]');
        if (row) { const id = row.dataset.hist; if (_openHist.has(id)) _openHist.delete(id); else _openHist.add(id); _renderProfiles(); }
    };
    host.onchange = async (ev) => {
        if (ev.target.id === 'hp-pm') { _profMonth = ev.target.value; await _loadProfiles(); _renderProfiles(); }
    };
}

async function _saveProfile() {
    const f = (k) => { const i = document.querySelector(`#hp-pform [data-p="${k}"]`); return i ? i.value : ''; };
    const num = (k) => { const v = f(k); return v === '' ? null : Number(v); };
    const body = { effective_from: f('effective_from'), pay_type: f('pay_type'), base_amount: num('base_amount') || 0, meal_allowance: num('meal_allowance') || 0,
                   labor_grade: num('labor_grade'), health_grade: num('health_grade'), dependents: num('dependents') || 0,
                   pension_self_rate: num('pension_self_rate') || 0, payroll_entity: f('payroll_entity'), pay_day: num('pay_day') || 5, note: f('note') };
    const isEdit = !!(_editProfile.profile && _editProfile.profile.id);
    if (!isEdit) body.staff_id = _editProfile.staff_id;
    if (isEdit) { if (body.labor_grade === null) delete body.labor_grade; if (body.health_grade === null) delete body.health_grade; }
    const r = isEdit ? await pput('/profiles/' + _editProfile.profile.id, body) : await ppost('/profiles', body);
    if (!r.ok) { const m = el('hp-pmsg'); if (m) { m.textContent = await _fail(r, '存不了'); m.className = 'hp-msg err'; } return; }
    _editProfile = null; _openHist.add(body.staff_id || _editProfile?.staff_id);
    await _loadProfiles(); _renderProfiles();
}

// ── 費率表 ────────────────────────────────────────────────────────────────

const RATE_FIELDS = [
    ['labor_rate', '勞保費率（含就業保險）', 0.001], ['labor_employee_share', '勞保勞工負擔比例', 0.01], ['labor_employer_share', '勞保雇主負擔比例', 0.01],
    ['accident_rate', '職災保險費率（雇主全額，依行業別）', 0.0001], ['health_rate', '健保費率', 0.0001], ['health_employee_share', '健保本人負擔比例', 0.01],
    ['health_employer_share', '健保雇主負擔比例', 0.01], ['avg_dependents', '健保平均眷口數', 0.01], ['pension_employer_rate', '勞退雇主提繳率', 0.01],
    ['labor_max_level', '勞保投保上限', 100], ['pension_max_level', '勞退提繳上限', 100], ['health_max_level', '健保投保上限', 100],
    ['hours_per_month', '月薪換時薪的除數', 1], ['meal_tax_free', '伙食費免稅上限', 100],
];

async function _loadRates(year) {
    const r = await pget('/rates' + (year ? '?year=' + year : ''));
    if (!r.ok) throw new Error(await _fail(r, '載入費率失敗'));
    _rates = await r.json();
}

function _renderRates() {
    const R = _rates.rates; const om = R.overtime_multiplier || {};
    const years = new Set([..._rates.years, _rates.year, new Date().getFullYear(), new Date().getFullYear() + 1]);
    el('hp-content').innerHTML = `<div class="hp-card">
        <h3>費率表 <select id="hp-ry">${[...years].sort().map(y => `<option value="${y}" ${y === _rates.year ? 'selected' : ''}>${y} 年${_rates.years.includes(y) ? '' : '（未存，用預設）'}</option>`).join('')}</select>
            <span class="hp-pill ${_rates.saved ? 'done' : 'draft'}">${_rates.saved ? '已存' : '系統預設 ' + _rates.default_year + ' 版'}</span>
            <span class="sp"></span><button class="hp-btn ok" data-act="rsave">儲存這一年</button><span class="hp-msg" id="hp-msg"></span></h3>
        <div class="hp-form">
            ${RATE_FIELDS.map(([k, label, step]) => `<label>${label}<input type="number" step="${step}" min="0" data-r="${k}" value="${R[k]}"></label>`).join('')}
            <label>加班倍率：工作日<input type="number" step="0.01" min="0" data-om="工作日" value="${om['工作日'] ?? 1}"></label>
            <label>加班倍率：假日<input type="number" step="0.01" min="0" data-om="假日" value="${om['假日'] ?? 2}"></label>
            <label class="full">投保級距（一行一個或用逗號隔開；勞保、勞退、健保共用這一串，各取到自己的上限）<textarea class="hp-levels" data-r="levels">${(R.levels || []).join(', ')}</textarea></label>
        </div>
        <div class="hp-note">每年 1 月照勞保局、健保署公告改一次就好。加班倍率是公司規定（工作日 ×1、假日 ×2）；法定最低是工作日前 2 小時 ×1.34、之後 ×1.67，這裡只提醒不擋。
        自負額＝級距 × 費率 × 負擔比例，四捨五入到元；雇主健保另乘（1＋平均眷口數）。</div>
    </div>`;
    const host = el('hp-content');
    host.onchange = async (ev) => { if (ev.target.id === 'hp-ry') { await _loadRates(Number(ev.target.value)); _renderRates(); } };
    host.onclick = async (ev) => {
        const b = ev.target.closest('[data-act="rsave"]'); if (!b) return;
        const data = {};
        for (const [k] of RATE_FIELDS) data[k] = Number(host.querySelector(`[data-r="${k}"]`).value);
        data.levels = host.querySelector('[data-r="levels"]').value.split(/[\s,，]+/).filter(Boolean).map(Number);
        data.overtime_multiplier = { '工作日': Number(host.querySelector('[data-om="工作日"]').value), '假日': Number(host.querySelector('[data-om="假日"]').value) };
        const r = await pput('/rates/' + _rates.year, { data });
        if (!r.ok) { _say(await _fail(r, '存不了'), true); return; }
        await _loadRates(_rates.year); _renderRates(); _say('已存');
    };
}

// ── 殼 ────────────────────────────────────────────────────────────────────

async function _show(view) {
    _view = view;
    document.querySelectorAll('#hp-nav button').forEach(b => b.classList.toggle('active', b.dataset.hpView === view));
    try {
        if (view === 'runs') { await _loadRuns(); _renderRuns(); }
        else if (view === 'profiles') { await _loadProfiles(); _renderProfiles(); }
        else { await _loadRates(0); _renderRates(); }
    } catch (e) {
        el('hp-content').innerHTML = `<div class="hp-empty">${esc(e.message || '載入失敗')}</div>`;
    }
}

export async function initHrPayrollTab() {
    const nav = el('hp-nav');
    if (nav && !nav.dataset.bound) {
        nav.dataset.bound = '1';
        nav.onclick = (ev) => { const b = ev.target.closest('[data-hp-view]'); if (b) _show(b.dataset.hpView); };
    }
    try {
        await _loadProfiles();          // 先算「幾人沒填」的提示
    } catch (e) {
        el('hp-content').innerHTML = `<div class="hp-empty">${esc(e.message || tabLoadError(0))}</div>`;
        return;
    }
    await _show(_view);
}
