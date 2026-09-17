/**
 * 假勤（分頁 #leave；docs/LEAVE_PLAN.md §7.6 手機那一段）。
 *
 * 員工自己的假：兩個數字（特休剩餘／補休剩餘；病假已用 2026-09-15 拿掉）＋請假表單（底部抽屜、打字就試算）＋近期申請清單。
 *   GET  /api/v1/me/leave/summary        {vocab, balances:{特休:{available,reserved,expiring[]}, 補休:{…}}, sick:{used_days,cap_days}, requests[], pending_count}
 *   POST /api/v1/me/leave/preview        {leave_type,start_date,end_date,part,start_time,end_time} → {hours, days, errors[{code,msg}], warnings[{code,msg}]}
 *   POST /api/v1/me/leave                同 body ＋ reason → 422 {detail:{errors:[…]}}（也可能是字串或清單，兩種都畫）
 *   POST /api/v1/me/leave/{id}/cancel    {note?}：待審→已撤回；已核准依卡片上的 cancel_mode：free→撤回、apply→申請消假（note 必填）、locked→不給按
 * 字彙（假別／時段／每日時數／狀態）全從 summary.vocab 拿，這裡不寫死假別；時數與天數也全由後端 preview 算，手機不自己算工作日。
 * 寫入守衛是「本人＋綁定人員檔案＋me_leave」，不看 CRM 的 can_write，所以按鈕不掛 .w；沒綁人員（409）／沒鑰匙（403）畫提示。
 * 只 import ../shell.js 與 ../ui.js（test_leave_mobile_phase1 釘著）；不直接 fetch。
 */
import { mfetch, toast, esc, todayLocal, fmtDate } from '../shell.js';
import { skeleton, errBox, emptyBox, pill, withBusy, segHtml, mountSeg, openSheet, closeSheet, shouldLoad, markStale, renderPaged } from '../ui.js';

const F = (id) => document.getElementById('lv-' + id);
const PREVIEW_DEBOUNCE_MS = 300;

let _sum = null;          // 最後一次 summary
let _rows = [];           // summary.requests（撤回時從這裡拿 cancel_mode）
let _previewTimer = null;
let _previewSeq = 0;      // 打字太快時只認最後一次 preview 的回應
let _blocked = true;      // preview 回了 errors → 送出鈕鎖住

// ── 字彙（全部從 summary.vocab 來；形狀容忍清單／物件兩種）──
const vocab = () => (_sum && _sum.vocab) || {};
const hoursPerDay = () => Number(vocab().hours_per_day) || 8;
const needsProof = (t) => (vocab().proof_required_types || []).includes(t);   // 病假要附證明
/** 假別：後端可能給 ['特休', …] 或 {ledger:[…], record:[…]}，攤成一條清單。 */
function leaveTypes() {
    const v = vocab().self_service_types || vocab().leave_types;   // 自助只開特休／補休／病假（owner 2026-09-15）
    if (Array.isArray(v)) return v.map(x => (typeof x === 'object' ? x.value : x)).filter(Boolean);
    if (v && typeof v === 'object') return Object.values(v).flat().filter(x => typeof x === 'string');
    return [];
}
/** 時段：後端給 parts=['all','am',…] ＋ part_labels={all:'整天',…}（core/leave_logic.vocab），統一成 [{value,label}]。
 *   label 一定要查 part_labels：只用 value 當 label 的話，抽屜裡顯示的是 all／am／pm／range。 */
function parts() {
    const v = vocab().parts, labels = vocab().part_labels || {};
    if (Array.isArray(v)) return v.map(x => (typeof x === 'object' ? { value: x.value, label: x.label || labels[x.value] || x.value }
                                                                   : { value: x, label: labels[x] || x }));
    if (v && typeof v === 'object') return Object.entries(v).map(([value, label]) => ({ value, label: String(label) }));
    return [];
}
const partLabel = (code) => ((parts().find(p => p.value === code) || {}).label || code || '');
const partCode = (label) => ((parts().find(p => p.label === label) || {}).value || label || '');
/** 「時段」那一格（要填起迄時間的）：找 value 是 range 的，沒有就拿最後一格。 */
const rangePart = () => { const ps = parts(); return (ps.find(p => p.value === 'range') || ps[ps.length - 1] || {}).value; };
/** 申請單狀態：位置＝契約 §7.1 的順序（待審／已核准／已退回／已撤回／消假待審）；後端給 request_statuses 就用它的。 */
const statuses = () => (Array.isArray(vocab().request_statuses) && vocab().request_statuses.length === 5)
    ? vocab().request_statuses : ['待審', '已核准', '已退回', '已撤回', '消假待審'];
const pendingStatus = () => statuses()[0];
const approvedStatus = () => statuses()[1];
/** 清單分組順序：待審、消假待審（都還在等主管）排前面，再來已核准，退回／撤回墊底。 */
const groupOrder = () => { const s = statuses(); return [s[0], s[4], s[1], s[2], s[3]]; };
function statusPillCls(status) {
    const s = statuses();
    if (status === s[0] || status === s[4]) return 'warn';
    if (status === s[1]) return 'ok';
    if (status === s[2]) return 'bad';
    return '';
}

const fmtH = (h) => { const v = Number(h) || 0; return Number.isInteger(v) ? String(v) : v.toFixed(1); };
const daysOf = (h) => fmtH((Number(h) || 0) / hoursPerDay());

// ── 頂部三個數字 ──
function statsHtml() {
    const b = (_sum && _sum.balances) || {};
    const sick = (_sum && _sum.sick) || {};
    const ledger = (name) => {
        const x = b[name] || {};
        const avail = Number(x.available) || 0;
        const extra = [];
        if (Number(x.reserved) > 0) extra.push(`保留 ${fmtH(x.reserved)} 小時`);
        (x.expiring || []).forEach(e => extra.push(`${fmtH(e.hours)} 小時 ${fmtDate(e.expires_on)} 到期`));
        return `<div class="k" data-stat="${esc(name)}"><div class="l">${esc(name)}剩餘</div>
          <div class="n">${fmtH(avail)}<span style="font-size:13px;font-weight:500;color:var(--sub)"> 小時（${daysOf(avail)} 天）</span></div>
          ${extra.length ? `<div class="l">${extra.map(esc).join(' · ')}</div>` : ''}</div>`;
    };
    const cap = sick.cap_days != null ? sick.cap_days : (vocab().sick_cap_days != null ? vocab().sick_cap_days : 30);
    return `<div class="m-strip" id="lv-stats" style="grid-template-columns:1fr 1fr">
      ${ledger('特休')}${ledger('補休')}
      <div class="k" data-stat="pending"><div class="l">等主管審</div><div class="n">${Number((_sum || {}).pending_count) || 0}<span style="font-size:13px;font-weight:500;color:var(--sub)"> 件</span></div></div>
    </div>`;
}

// ── 清單 ──
function periodText(r) {
    const same = !r.end_date || r.end_date === r.start_date;
    const span = same ? fmtDate(r.start_date) : `${fmtDate(r.start_date)} 至 ${fmtDate(r.end_date)}`;
    const when = r.part === rangePart() && r.start_time && r.end_time ? `${r.start_time}–${r.end_time}` : partLabel(r.part);
    return when ? `${span}（${when}）` : span;
}

function actionsHtml(r) {
    const id = esc(r.id);
    if (r.status === pendingStatus())
        return `<button type="button" class="m-btn sm" data-cancel="${id}" data-mode="free">撤回</button>`;
    if (r.status !== approvedStatus()) return '';
    if (r.cancel_mode === 'free') return `<button type="button" class="m-btn sm" data-cancel="${id}" data-mode="free">撤回</button>`;
    if (r.cancel_mode === 'apply') return `<button type="button" class="m-btn sm" data-cancel="${id}" data-mode="apply">申請消假</button>`;
    if (r.cancel_mode === 'locked') return `<span class="m-hint" style="margin:0" data-locked="${id}">颱風假當日不可消</span>`;
    return '';
}

function cardHtml(r) {
    const acts = actionsHtml(r);
    return `
      <div class="m-card" data-id="${esc(r.id)}">
        <div class="t"><div class="name">${esc(r.leave_type || '')} · ${fmtH(r.hours)} 小時</div>${pill(r.status, statusPillCls(r.status))}</div>
        <div class="sub">${esc(periodText(r))} · ${daysOf(r.hours)} 天</div>
        ${r.reason ? `<div class="sub" style="color:var(--ink)">${esc(r.reason)}</div>` : ''}
        ${needsProof(r.leave_type) ? `<div class="sub" style="color:${r.proof_path ? 'var(--ok)' : 'var(--warn)'}">${r.proof_path ? '已附證明' : '缺證明（請到電腦版假勤頁補傳）'}</div>` : ''}
        ${r.reject_note ? `<div class="sub" style="color:var(--bad)">退回理由：${esc(r.reject_note)}</div>` : ''}
        ${r.cancel_note ? `<div class="sub" style="color:var(--warn)">消假說明：${esc(r.cancel_note)}</div>` : ''}
        ${acts ? `<div class="m-actions" style="justify-content:flex-end">${acts}</div>` : ''}
      </div>`;
}

function drawList(host) {
    const box = host.querySelector('#lv-list');
    if (!_rows.length) { box.innerHTML = emptyBox('還沒有請假紀錄'); return; }
    const groups = new Map(groupOrder().map(s => [s, []]));
    for (const r of _rows) {
        if (!groups.has(r.status)) groups.set(r.status, []);
        groups.get(r.status).push(r);
    }
    const shown = [...groups].filter(([, arr]) => arr.length);
    box.innerHTML = shown.map(([s, arr], i) => `<div class="m-h">${esc(s)}（${arr.length}）</div><div data-group="${i}"></div>`).join('');
    shown.forEach(([, arr], i) => renderPaged(box.querySelector(`[data-group="${i}"]`),
        arr.sort((a, b) => String(b.start_date || '').localeCompare(String(a.start_date || ''))), cardHtml));
}

async function load(host) {
    host.querySelector('#lv-stats-box').innerHTML = skeleton(2);
    host.querySelector('#lv-list').innerHTML = skeleton(3);
    try {
        _sum = await mfetch('/api/v1/me/leave/summary');
        _rows = _sum.requests || [];
        host.querySelector('#lv-stats-box').innerHTML = statsHtml();
        host.querySelector('#lv-new').disabled = false;
        drawList(host);
    } catch (e) {
        _sum = null; _rows = [];
        host.querySelector('#lv-new').disabled = true;
        host.querySelector('#lv-stats-box').innerHTML = '';
        host.querySelector('#lv-list').innerHTML = (e.status === 409 || e.status === 403)
            ? `<div class="m-notice"><pre>${esc(e.message)}</pre><div class="m-hint" style="margin:0">${e.status === 409
                ? '假勤記在人員檔案底下，請管理員在「使用者管理」把這個帳號綁到人員。'
                : '這個帳號沒有「我的請假」權限，請管理員在「使用者管理」開通。'}</div></div>`
            : errBox(e);
    }
}

// ── 撤回／申請消假 ──
async function cancelRequest(btn, host) {
    const id = btn.dataset.cancel, mode = btn.dataset.mode;
    const row = _rows.find(r => r.id === id) || {};
    const label = `${row.leave_type || ''} ${periodText(row)}`;
    const body = {};
    if (mode === 'apply') {
        const note = (window.prompt(`申請消假：${label}\n開始前不到 ${vocab().cancel_free_days != null ? vocab().cancel_free_days : 2} 天，要由主管決定。請填消假原因（必填）：`) || '').trim();
        if (!note) return;
        body.note = note;
    } else if (!window.confirm(`撤回這張請假單？${label}`)) return;
    await withBusy(btn, async () => {
        try {
            await mfetch(`/api/v1/me/leave/${encodeURIComponent(id)}/cancel`, { method: 'POST', body });
            toast(mode === 'apply' ? '已送出消假申請，等主管決定' : '已撤回');
            markStale('leave');
            await load(host);
        } catch (e) { toast(e.message, 'err'); }
    });
}

// ── 請假表單（底部抽屜）──
function formHtml() {
    const types = leaveTypes();
    const ps = parts();
    const today = todayLocal();
    return `
      <div class="ttl">請假</div>
      <form class="m-form" id="lv-form" autocomplete="off">
        <label class="req">假別</label>${types.length ? segHtml('lv-leave_type', types, types[0]) : '<input id="lv-leave_type" placeholder="假別">'}
        <div class="row2">
          <div><label class="req">起日</label><input type="date" id="lv-start_date" value="${today}"></div>
          <div><label class="req">迄日</label><input type="date" id="lv-end_date" value="${today}"></div>
        </div>
        <label>時段</label>${ps.length ? segHtml('lv-part_label', ps.map(p => p.label), ps[0].label) : '<input id="lv-part_label" value="">'}
        <div class="row2" id="lv-range" hidden>
          <div><label class="req">起</label><input type="time" id="lv-start_time" step="1800"></div>
          <div><label class="req">訖</label><input type="time" id="lv-end_time" step="1800"></div>
        </div>
        <div class="m-card" id="lv-preview" style="padding:10px 12px"><span class="sub">填好日期就會算時數</span></div>
        <div id="lv-warn" class="m-notice" style="background:#2a2410;border-color:#92400e;color:var(--warn);font-size:13px;line-height:1.6" hidden></div>
        <div id="lv-errs" class="m-err" hidden></div>
        <div id="lv-proof-wrap" hidden>
          <label class="req">病假證明（診斷證明或掛號單照片／PDF）</label><input type="file" id="lv-proof" accept=".jpg,.jpeg,.png,.heic,.webp,.pdf">
        </div>
        <label class="req">事由</label><textarea id="lv-reason" rows="2" placeholder="例：家中有事、回診"></textarea>
        <button type="submit" class="m-btn-primary" id="lv-submit" disabled>送出申請</button>
      </form>`;
}

function formBody() {
    const part = partCode(F('part_label').value);
    const isRange = part === rangePart();
    return {
        leave_type: (F('leave_type').value || '').trim(),
        start_date: F('start_date').value,
        end_date: F('end_date').value || F('start_date').value,
        part,
        start_time: isRange ? (F('start_time').value || null) : null,
        end_time: isRange ? (F('end_time').value || null) : null,
    };
}

/** 錯誤形狀容忍：{detail:{errors:[{msg}]}}／{detail:[{msg}]}／{detail:'字串'}／{errors:[…]} 都攤成字串清單。 */
function errorMessages(e) {
    const d = e && e.data && e.data.detail !== undefined ? e.data.detail : (e && e.data);
    const pick = (x) => (x && typeof x === 'object') ? (x.msg || x.message || JSON.stringify(x)) : String(x);
    if (Array.isArray(d)) return d.map(pick);
    if (d && typeof d === 'object' && Array.isArray(d.errors)) return d.errors.map(pick);
    if (typeof d === 'string' && d) return [d];
    return [(e && e.message) || '失敗'];
}

function showIssues(errors, warnings) {
    const errBoxEl = F('errs'), warnEl = F('warn');
    const li = (arr) => arr.map(x => `<div>${esc(x.msg || x.message || String(x))}</div>`).join('');
    errBoxEl.hidden = !errors.length; errBoxEl.innerHTML = li(errors);
    warnEl.hidden = !warnings.length; warnEl.innerHTML = li(warnings);
}

function setBlocked(blocked) {
    _blocked = blocked;
    const b = F('submit'); if (b) b.disabled = blocked;
}

/** 打字就試算：300ms 沒再動才打 preview；起迄沒填就不打。回應照序號只認最後一次。 */
function schedulePreview() {
    clearTimeout(_previewTimer);
    _previewTimer = setTimeout(runPreview, PREVIEW_DEBOUNCE_MS);
}
async function runPreview() {
    if (!F('form')) return;
    const body = formBody();
    const pv = F('preview');
    if (!body.leave_type || !body.start_date || !body.end_date || (body.part === rangePart() && (!body.start_time || !body.end_time))) {
        pv.innerHTML = '<span class="sub">填好假別、日期（時段要填起訖時間）就會算時數</span>';
        showIssues([], []); setBlocked(true);
        return;
    }
    const seq = ++_previewSeq;
    pv.innerHTML = '<span class="sub">試算中…</span>';
    try {
        const r = await mfetch('/api/v1/me/leave/preview', { method: 'POST', body });
        if (seq !== _previewSeq || !F('form')) return;
        const errors = r.errors || [], warnings = r.warnings || [];
        pv.innerHTML = `<span style="font-weight:600;color:#fff">共 ${fmtH(r.hours)} 小時（${fmtH(r.days)} 天）</span>`;
        showIssues(errors, warnings);
        setBlocked(errors.length > 0);
    } catch (e) {
        if (seq !== _previewSeq || !F('form')) return;
        // 試算打不到（斷線／後端沒起來）：畫出來但不鎖送出，讓後端在送出時再驗一次
        pv.innerHTML = '<span class="sub">試算失敗，送出時會再檢查一次</span>';
        showIssues(errorMessages(e).map(msg => ({ msg })), []);
        setBlocked(false);
    }
}

function openForm(host) {
    if (!_sum) { toast('資料還沒載好，請稍後再試', 'err'); return; }
    const body = openSheet(formHtml());
    const ps = parts();
    const syncRange = () => {
        const isRange = partCode(F('part_label').value) === rangePart();
        F('range').hidden = !isRange;
    };
    const syncProof = () => { F('proof-wrap').hidden = !needsProof(F('leave_type').value); };
    mountSeg('lv-leave_type', () => { syncProof(); schedulePreview(); });
    mountSeg('lv-part_label', () => { syncRange(); schedulePreview(); });
    syncRange(); syncProof();
    // 迄日跟著起日走：迄日還沒動過（或早於起日）就同步成起日
    F('start_date').addEventListener('change', () => {
        const s = F('start_date').value, e = F('end_date');
        if (s && (!e.value || e.value < s || !e.dataset.touched)) e.value = s;
        schedulePreview();
    });
    F('end_date').addEventListener('change', () => { F('end_date').dataset.touched = '1'; schedulePreview(); });
    F('start_time').addEventListener('change', schedulePreview);
    F('end_time').addEventListener('change', schedulePreview);
    if (!ps.length) F('part_label').addEventListener('input', schedulePreview);
    if (!leaveTypes().length) F('leave_type').addEventListener('input', schedulePreview);
    schedulePreview();

    body.querySelector('#lv-form').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const reason = F('reason').value.trim();
        if (!reason) { toast('請填事由', 'err'); F('reason').focus(); return; }
        if (_blocked) { toast('還有錯誤沒解決，不能送出', 'err'); return; }
        const payload = { ...formBody(), reason };
        const proofFile = needsProof(payload.leave_type) ? (F('proof').files || [])[0] : null;
        if (needsProof(payload.leave_type) && !proofFile) { toast('病假要附證明（照片或 PDF）', 'err'); return; }
        await withBusy(F('submit'), async () => {
            try {
                const created = await mfetch('/api/v1/me/leave', { method: 'POST', body: payload });
                if (proofFile) {
                    // 單建好了才傳證明（multipart：mfetch 看到 FormData 不會補 JSON header）
                    const fd = new FormData(); fd.append('file', proofFile);
                    try { await mfetch('/api/v1/me/leave/' + created.id + '/proof', { method: 'POST', body: fd }); }
                    catch (e) { toast('單已送出，但證明上傳失敗：' + (e.message || '') + '。請到電腦版假勤頁補傳', 'err'); }
                }
                toast('已送出，等主管審核');
                markStale('leave');
                closeSheet();
                await load(host);
            } catch (e) {
                const msgs = errorMessages(e);
                showIssues(msgs.map(msg => ({ msg })), []);
                toast(msgs[0], 'err');
            }
        });
        // withBusy 的 finally 會把 disabled 還原成 false；失敗時照 preview 的結論鎖回去
        if (F('submit')) F('submit').disabled = _blocked;
    });
}

// ── 加班申請（docs/PAYROLL_OVERTIME_PLAN.md 第二批；/api/v1/me/overtime*）──
// 員工只填日期、起訖、換補休或加班費、事由；時數與工作日／假日由後端 preview 算。清單畫在請假清單上面一小段。
let _ot = null;
let _otTimer = null;
let _otSeq = 0;
let _otBlocked = true;
const OT = (id) => document.getElementById('ot-' + id);

function otFormHtml() {
    const v = (_ot && _ot.vocab) || {};
    const payouts = v.payouts || ['補休', '加班費'];
    const today = todayLocal();
    return `
      <div class="ttl">報加班</div>
      <form class="m-form" id="ot-form" autocomplete="off">
        <label class="req">加班日</label><input type="date" id="ot-date" value="${today}" max="${today}">
        <div class="row2">
          <div><label class="req">起</label><input type="time" id="ot-start_time" step="1800" value="18:00"></div>
          <div><label class="req">訖</label><input type="time" id="ot-end_time" step="1800" value="20:00"></div>
        </div>
        <label class="req">換成</label>${segHtml('ot-payout', payouts, payouts[0])}
        <div class="m-card" id="ot-preview" style="padding:10px 12px"><span class="sub">填好起訖就會算</span></div>
        <div id="ot-warn" class="m-notice" style="background:#2a2410;border-color:#92400e;color:var(--warn);font-size:13px;line-height:1.6" hidden></div>
        <div id="ot-errs" class="m-err" hidden></div>
        <label>案子（選填）</label><input type="text" id="ot-project" placeholder="哪個案子">
        <label class="req">事由</label><textarea id="ot-reason" rows="2" placeholder="做了什麼"></textarea>
        <button type="submit" class="m-btn-primary" id="ot-submit" disabled>送出加班申請</button>
      </form>`;
}

function otSchedulePreview() { clearTimeout(_otTimer); _otTimer = setTimeout(otRunPreview, PREVIEW_DEBOUNCE_MS); }

async function otRunPreview() {
    const seq = ++_otSeq;
    const d = OT('date'), a = OT('start_time'), b = OT('end_time');
    if (!d || !a || !b) return;
    if (!d.value || !a.value || !b.value) { _otBlocked = true; if (OT('submit')) OT('submit').disabled = true; return; }
    try {
        const p = await mfetch('/api/v1/me/overtime/preview', { method: 'POST', body: { date: d.value, start_time: a.value, end_time: b.value, payout: OT('payout').value } });
        if (seq !== _otSeq) return;
        const errs = p.errors || [], warns = p.warnings || [];
        const what = OT('payout').value === '補休' ? `補休 ${fmtH(p.credit_hours)} 小時` : (p.pay_amount != null ? `加班費 ${Number(p.pay_amount).toLocaleString('zh-TW')} 元` : '加班費（金額核准時算）');
        OT('preview').innerHTML = `<b>${fmtH(p.hours)} 小時</b>（${esc(p.day_kind)}）→ ${what}<div class="sub">本月累計 ${fmtH(p.month_total)} 小時</div>`;
        OT('warn').hidden = !warns.length; OT('warn').textContent = warns.map(w => w.msg).join('；');
        OT('errs').hidden = !errs.length; OT('errs').textContent = errs.map(e => e.msg).join('；');
        _otBlocked = errs.length > 0;
    } catch (e) {
        if (seq !== _otSeq) return;
        OT('errs').hidden = false; OT('errs').textContent = (e && e.message) || '算不出來'; _otBlocked = true;
    }
    if (OT('submit')) OT('submit').disabled = _otBlocked;
}

function openOtForm(host) {
    const body = openSheet(otFormHtml());
    mountSeg('ot-payout', otSchedulePreview);
    ['date', 'start_time', 'end_time'].forEach(k => OT(k).addEventListener('change', otSchedulePreview));
    otSchedulePreview();
    body.querySelector('#ot-form').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const reason = OT('reason').value.trim();
        if (!reason) { toast('請填事由', 'err'); OT('reason').focus(); return; }
        if (_otBlocked) { toast('還有錯誤沒解決，不能送出', 'err'); return; }
        await withBusy(OT('submit'), async () => {
            try {
                await mfetch('/api/v1/me/overtime', { method: 'POST', body: { date: OT('date').value, start_time: OT('start_time').value, end_time: OT('end_time').value,
                                                                       payout: OT('payout').value, reason, project_name: OT('project').value.trim() || null } });
                toast('已送出，等主管核准');
                closeSheet();
                await loadOt(host);
            } catch (e) {
                const msgs = errorMessages(e);
                OT('errs').hidden = false; OT('errs').textContent = msgs.join('；');
                toast(msgs[0], 'err');
            }
        });
        if (OT('submit')) OT('submit').disabled = _otBlocked;
    });
}

function otCardHtml(i) {
    const what = i.payout === '補休' ? `補休 ${fmtH(i.credit_hours)} h` : (i.pay_amount != null ? `加班費 ${Number(i.pay_amount).toLocaleString('zh-TW')} 元` : '加班費');
    return `<div class="m-card" style="padding:10px 12px">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline"><b>${esc(fmtDate(i.date))} ${esc(i.start_time)}–${esc(i.end_time)}</b>${pill(i.status, statusPillCls(i.status))}</div>
      <div class="sub">${fmtH(i.hours)} h（${esc(i.day_kind)}）→ ${what}${i.project_name ? '・' + esc(i.project_name) : ''}</div>
      ${i.reason ? `<div class="sub">${esc(i.reason)}</div>` : ''}
      ${i.status === '已退回' && i.reject_note ? `<div class="sub" style="color:var(--warn)">退回：${esc(i.reject_note)}</div>` : ''}
      ${i.status === '待審' ? `<div class="m-actions" style="margin-top:6px"><button type="button" class="m-btn" data-ot-cancel="${esc(i.id)}">撤回</button></div>` : ''}
    </div>`;
}

async function loadOt(host) {
    const box = host.querySelector('#lv-ot-box');
    if (!box) return;
    try {
        _ot = await mfetch('/api/v1/me/overtime');
    } catch (e) {
        box.innerHTML = ''; return;   // 沒鑰匙／沒綁人員：請假那段已經畫了提示，這裡不重複
    }
    const m = _ot.month || {};
    const items = (_ot.items || []).slice(0, 5);
    box.innerHTML = `<div class="m-h" style="font-size:15px;margin-top:6px">加班 <span class="sub">本月 ${fmtH(m.hours)} h・補休 +${fmtH(m.credit_hours)} h・加班費 ${Number(m.pay_amount || 0).toLocaleString('zh-TW')} 元</span></div>
      ${items.length ? items.map(otCardHtml).join('') : '<div class="sub" style="padding:4px 0 8px">今年還沒報過加班</div>'}`;
    const btn = host.querySelector('#lv-ot-new'); if (btn) btn.disabled = false;
}

async function cancelOt(btn, host) {
    if (!confirm('撤回這張加班單？')) return;
    await withBusy(btn, async () => {
        try { await mfetch('/api/v1/me/overtime/' + btn.dataset.otCancel + '/cancel', { method: 'POST', body: {} }); toast('已撤回'); await loadOt(host); }
        catch (e) { toast(errorMessages(e)[0], 'err'); }
    });
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
          <div class="m-h">我的假勤</div>
          <div id="lv-stats-box"></div>
          <div class="m-actions" style="margin:0 0 12px">
            <button type="button" class="m-btn pri" id="lv-new" disabled>請假</button>
            <button type="button" class="m-btn" id="lv-ot-new" disabled>報加班</button>
          </div>
          <div id="lv-ot-box"></div>
          <div id="lv-list"></div>`;
        host.addEventListener('click', (ev) => {
            if (ev.target.closest('#lv-new')) { openForm(host); return; }
            if (ev.target.closest('#lv-ot-new')) { openOtForm(host); return; }
            const oc = ev.target.closest('button[data-ot-cancel]');
            if (oc) { cancelOt(oc, host); return; }
            const c = ev.target.closest('button[data-cancel]');
            if (c) cancelRequest(c, host);
        });
    }
    if (shouldLoad('leave', { first })) { await load(host); await loadOt(host); }   // 切回來 60 秒內沒改過就不重抓（同其他分頁）
}
