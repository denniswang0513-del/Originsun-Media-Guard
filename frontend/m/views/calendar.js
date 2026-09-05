/**
 * 行事曆分頁：拍攝排程＋器材登記（docs/SHOOT_CALENDAR_PLAN.md §3；owner 2026-09-03 拍板取代「付款」分頁）。
 * 一場拍攝＝哪一天、哪個案子、去哪裡、誰去、帶什麼器材；勾器材＝在器材庫開預約，
 * 那天被別場拿走的標紅（送出前 confirm），「器材已領」「已歸還」直接動同一列。
 *
 * 端點（routers/api_shoots.py）：
 *   GET  /api/v1/shoots/options?date=&end_date=   字彙（statuses／equipment_states）、器材（帶 busy）、地點、人員、專案
 *   GET  /api/v1/shoots?from=&limit=               清單；POST /api/v1/shoots、PUT /{id}
 *   POST /{id}/status {status}、/{id}/equipment/pickup｜return、/{id}/resync
 *   GET  /calendar/status、PUT /calendar/config、POST /calendar/test（管理員的「行事曆設定」卡）
 * 字彙（狀態、器材狀態）全部來自 options：這裡不寫死任何一個狀態字（計畫 §7）。
 * 從專案抽屜「登記拍攝」進來：state.shootPreset 帶 project_id，render 套進專案欄並把表單展開。
 */
import { mfetch, toast, esc, todayLocal, dateIso, fmtDate } from '../shell.js';
import { state, skeleton, emptyBox, errBox, pill, withBusy, shouldLoad, markStale, pickerHtml, mountPicker, renderPaged, projectLabel } from '../ui.js';

const API = '/api/v1/shoots';
const F = (id) => document.getElementById('cal-' + id);
const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六'];
const PAST_DAYS = 60;      // 「已過」折疊往回看多久
const DEFAULT_HINT = '送出＝登記一場拍攝：勾到的器材在器材庫開預約，並寫進公司 Google 日曆';

let _o = {};            // /shoots/options 整包
let _rows = [];         // 清單（ShootRow）
let _editing = null;    // 修改中的場次
let _crew = [];         // 已選人員 [{staff_id, name}]
let _equip = [];        // 已勾器材 id（字串）
let _busy = {};         // equipment_id → {shoot_id, project_name, date}：表單那天被別場拿走的
let _host = null;

// ── 字彙（全部來自 options）──
const statuses = () => (Array.isArray(_o.statuses) ? _o.statuses : []);
const scheduled = () => _o.scheduled_status || statuses()[0] || '';
const doneStatus = () => _o.done_status || statuses()[1] || '';
const cancelled = () => _o.cancelled_status || statuses()[2] || '';
const eqStates = () => (Array.isArray(_o.equipment_states) ? _o.equipment_states : []);
const reserved = () => eqStates()[0] || '';     // 預約中
const pickedUp = () => eqStates()[1] || '';     // 已領走
const isAdmin = () => ((state.me || {}).access_level || 0) >= 3;
/** 狀態 pill 顏色：完成綠、取消紅、其餘灰——認的是 options 給的字，不是字面。 */
const statusCls = (s) => (s === doneStatus() ? 'ok' : (s === cancelled() ? 'bad' : ''));

function addDays(iso, n) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return dateIso(d); }
const weekday = (iso) => (iso ? '（' + WEEKDAY[new Date(iso + 'T00:00:00').getDay()] + '）' : '');
const cnt = (n, arr) => (n ?? (Array.isArray(arr) ? arr.length : 0));
const eqOf = (id) => (_o.equipment || []).find(x => String(x.id) === String(id)) || {};
const shootName = (r) => [r.project_name, r.client_short_name ? '（' + r.client_short_name + '）' : ''].join('') || r.title || '';

function layout() {
    return `
      <button type="button" class="m-btn wide w" id="cal-toggle" style="margin-bottom:12px">登記拍攝</button>
      <form class="m-form m-card w" id="cal-form" autocomplete="off" hidden>
        <label class="req">專案</label>${pickerHtml('cal-project_id')}
        <label class="req">日期</label><input id="cal-date" type="date">
        <div class="row2">
          <div><label>開始</label><input id="cal-start_time" type="time"></div>
          <div><label>結束</label><input id="cal-end_time" type="time"></div>
        </div>
        <div class="m-hint">時間留空＝全天</div>
        <label>地點</label>${pickerHtml('cal-location')}
        <label>人員</label>${pickerHtml('cal-crew')}
        <div class="m-chips wrap" id="cal-crew-chips"></div>
        <label>器材</label>${pickerHtml('cal-equip')}
        <div class="m-chips wrap" id="cal-equip-chips"></div>
        <div class="m-hint" id="cal-equip-hint" hidden></div>
        <details class="m-more"><summary>更多欄位（結束日期、標題、備註）</summary>
          <label>結束日期</label><input id="cal-end_date" type="date">
          <div class="m-hint">多日拍攝才填；空＝當天</div>
          <label>標題</label><input id="cal-title" placeholder="空白＝用案名">
          <label>備註</label><textarea id="cal-notes"></textarea>
        </details>
        <div class="m-hint" id="cal-mode-hint">${DEFAULT_HINT}</div>
        <button type="submit" class="m-btn-primary" id="cal-submit">送出</button>
        <button type="button" class="m-btn wide" id="cal-cancel-edit" hidden>取消修改</button>
      </form>
      ${state.canWrite ? '' : '<div class="m-empty" id="cal-ro-note">此帳號只能檢視拍攝排程</div>'}
      <div id="cal-list">${skeleton(3)}</div>
      <div id="cal-cfg"></div>`;
}

// ── 選擇器（全部打字就過濾；人員／器材選一個就變 chip、再選下一個）──
const projectItems = () => (_o.projects || []).map(p => ({ value: p.id, label: projectLabel(p) }));
function mountProject(value, extra = null) {
    const items = projectItems();
    if (extra && extra.value && !items.some(i => String(i.value) === String(extra.value))) items.push(extra);   // 修改一場不在清單裡（已結案）的案子
    mountPicker('cal-project_id', { items, placeholder: '打字找案名或客戶', value });
}
// 地點：選場景庫或自己打（free）；值一律是「名字」，送出時再對回 location_id
function mountLocation(value) {
    mountPicker('cal-location', { items: (_o.locations || []).map(l => ({ value: l.name, label: l.name })),
                                  placeholder: '打字找場景庫，或自己打地址', free: true, value });
}
function mountCrew() {
    const items = (_o.staff || []).filter(s => !_crew.some(c => String(c.staff_id) === String(s.id)))
        .map(s => ({ value: s.id, label: s.name + (s.role ? '｜' + s.role : '') }));
    mountPicker('cal-crew', { items, placeholder: '打字找人，選一個加一個', onPick: (v) => {
        if (!v) return;
        const s = (_o.staff || []).find(x => String(x.id) === String(v));
        if (s) _crew.push({ staff_id: s.id, name: s.name });
        drawCrew(); mountCrew();
    } });
}
function equipItems() {
    const rows = (_o.equipment || []).filter(e => !_equip.includes(String(e.id)));
    rows.sort((a, b) => String(a.category || '').localeCompare(String(b.category || ''), 'zh-Hant')
        || String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hant'));
    return rows.map(e => ({ value: String(e.id),
        label: [e.category, e.name].filter(Boolean).join('｜') + (_busy[String(e.id)] ? `（撞：${_busy[String(e.id)].project_name || ''}）` : '') }));
}
function mountEquip() {
    mountPicker('cal-equip', { items: equipItems(), placeholder: '打字找器材（類別｜名稱），選一個加一個', onPick: (v) => {
        if (!v) return;
        if (!_equip.includes(String(v))) _equip.push(String(v));
        drawEquip(); mountEquip();
    } });
}

const chip = (label, key, bad = false) =>
    `<button type="button" class="chip${bad ? ' bad' : ''}" data-rm="${esc(key)}" aria-label="移除 ${esc(label)}">${esc(label)} ×</button>`;
function drawCrew() {
    F('crew-chips').innerHTML = _crew.map(c => chip(c.name, 'c:' + c.staff_id)).join('')
        || '<span class="m-hint" style="margin:0">還沒選人</span>';
}
function drawEquip() {
    F('equip-chips').innerHTML = _equip.map(id => {
        const e = eqOf(id), b = _busy[id];
        const own = _editing && (_editing.equipment || []).find(x => String(x.equipment_id) === id);   // 修改模式：這場原本的預約列
        const bad = !!b || !!(own && own.conflict);
        const label = (e.name || id) + (b ? `（撞：${b.project_name || ''}）` : (own && own.conflict ? '（撞期）' : ''))
            + (own && own.state ? ' · ' + own.state : '');
        return chip(label, 'e:' + id, bad);
    }).join('') || '<span class="m-hint" style="margin:0">還沒選器材</span>';
    const n = _equip.filter(id => _busy[id]).length;
    F('equip-hint').hidden = !n;
    F('equip-hint').textContent = n ? `${n} 件在這天已被別場預約（紅框），送出前會再確認一次` : '';
}
function onChipRm(ev) {
    const b = ev.target.closest('button[data-rm]'); if (!b) return;
    const [kind, id] = [b.dataset.rm.slice(0, 1), b.dataset.rm.slice(2)];
    if (kind === 'c') { _crew = _crew.filter(c => String(c.staff_id) !== id); drawCrew(); mountCrew(); return; }
    const own = _editing && (_editing.equipment || []).find(x => String(x.equipment_id) === id);
    if (own && pickedUp() && own.state === pickedUp()) { toast('已領走的器材不能從這場拿掉，先做「已歸還」', 'err'); return; }
    _equip = _equip.filter(x => x !== id); drawEquip(); mountEquip();
}

// 換日期／結束日期：重抓 options?date= 看那天哪些器材被別場拿走（自己這場的不算）
function computeBusy(equipment) {
    _busy = {};
    for (const e of equipment || []) {
        if (e.busy && !(_editing && String(e.busy.shoot_id) === String(_editing.id))) _busy[String(e.id)] = e.busy;
    }
}
function optionsQuery() {
    const qs = new URLSearchParams({ date: (F('date') && F('date').value) || todayLocal() });
    if (F('end_date') && F('end_date').value) qs.set('end_date', F('end_date').value);
    return qs;
}
async function refreshBusy() {
    if (!F('date').value) return;
    try {
        const d = await mfetch(`${API}/options?${optionsQuery()}`);
        if (Array.isArray(d.equipment)) _o.equipment = d.equipment;
        computeBusy(_o.equipment);
    } catch (_) { /* busy 只是提示，抓不到就不標 */ }
    mountEquip(); drawEquip();
}

async function loadOptions() {
    const keep = { project: F('project_id').value, loc: F('location').value };
    try { _o = await mfetch(`${API}/options?${optionsQuery()}`); }
    catch (e) { _o = {}; toast('行事曆字彙載入失敗：' + e.message, 'err'); }
    // 行事曆的寫入權限是 crm_projects 模組（api_shoots._check_write），不是 CRM 的 can_write（Lv3）：
    // /shoots/options 回 me.can_write 就是給這裡用的，能寫的人把 .w 的遮罩拿掉
    if (_o.me && _o.me.can_write) {
        const root = document.getElementById('tab-calendar');
        if (root) { root.querySelectorAll('.w').forEach((el) => el.classList.remove('w')); const n = root.querySelector('#cal-ro-note'); if (n) n.hidden = true; }
    }
    computeBusy(_o.equipment);
    mountProject(keep.project, _editing ? { value: _editing.project_id, label: shootName(_editing) } : null);
    mountLocation(keep.loc);
    drawCrew(); mountCrew(); drawEquip(); mountEquip();
}

// ── 表單：新增／修改共用同一份 ──
function setFormOpen(open) {
    F('form').hidden = !open;
    F('toggle').classList.toggle('pri', open);
}
function markEditButton(id) {
    _host.querySelectorAll('#cal-list button[data-act="edit"]').forEach(b => b.classList.toggle('pri', !!id && b.dataset.id === String(id)));
}
function resetForm() {
    _editing = null; _crew = []; _equip = [];
    F('project_id')._set?.(''); F('location')._set?.('');
    F('date').value = todayLocal(); F('end_date').value = ''; F('start_time').value = ''; F('end_time').value = '';
    F('title').value = ''; F('notes').value = '';
    F('mode-hint').textContent = DEFAULT_HINT; F('submit').textContent = '送出'; F('cancel-edit').hidden = true;
    markEditButton(null);
    drawCrew(); mountCrew(); drawEquip(); mountEquip();
    refreshBusy();
}
function startEdit(row) {
    _editing = row;
    mountProject(String(row.project_id || ''), { value: row.project_id, label: shootName(row) });
    F('date').value = row.date || ''; F('end_date').value = (row.end_date && row.end_date !== row.date) ? row.end_date : '';
    F('start_time').value = row.start_time || ''; F('end_time').value = row.end_time || '';
    F('location')._set?.(row.location_name || row.location_text || '');
    _crew = (row.crew || []).map(c => ({ staff_id: c.staff_id, name: c.name }));
    _equip = (row.equipment || []).map(e => String(e.equipment_id));
    F('title').value = row.title || ''; F('notes').value = row.notes || '';
    if (F('end_date').value || F('title').value || F('notes').value) F('form').querySelector('details.m-more').open = true;
    F('mode-hint').textContent = '修改中：' + shootName(row) + '（改完送出會覆蓋這場；器材加減勾也在這裡）';
    F('submit').textContent = '儲存修改'; F('cancel-edit').hidden = false;
    markEditButton(row.id);
    drawCrew(); mountCrew(); drawEquip(); mountEquip();
    setFormOpen(true);
    refreshBusy();
    F('form').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function payload() {
    const locText = F('location').value.trim();
    const loc = (_o.locations || []).find(l => l.name === locText);
    return {
        project_id: F('project_id').value || (_editing && _editing.project_id) || null,
        title: F('title').value.trim(),
        date: F('date').value, end_date: F('end_date').value || null,
        start_time: F('start_time').value || null, end_time: F('end_time').value || null,
        location_id: loc ? loc.id : null, location_text: loc ? '' : locText,
        crew: _crew.slice(), equipment_ids: _equip.slice(),
        notes: F('notes').value.trim(),
    };
}

async function submit(ev) {
    ev.preventDefault();
    const body = payload();
    if (!body.project_id) { toast('請先選專案', 'err'); F('project_id-q').focus(); return; }
    if (!body.date) { toast('請選拍攝日期', 'err'); F('date').focus(); return; }
    if (body.end_date && body.end_date < body.date) { toast('結束日期不能早於拍攝日', 'err'); F('end_date').focus(); return; }
    if (body.start_time && body.end_time && body.end_time < body.start_time) { toast('結束時間不能早於開始時間', 'err'); F('end_time').focus(); return; }
    const clash = _equip.filter(id => _busy[id]);
    if (clash.length) {
        const names = clash.map(id => `${eqOf(id).name || id}（${_busy[id].project_name || ''}）`).join('、');
        if (!window.confirm(`有 ${clash.length} 件器材那天已被別場預約：${names}。仍要送出？`)) return;
    }
    const editing = _editing;
    let saved = false;
    await withBusy(F('submit'), async () => {
        try {
            const r = editing
                ? await mfetch(`${API}/${encodeURIComponent(editing.id)}`, { method: 'PUT', body })
                : await mfetch(API, { method: 'POST', body });
            const s = r && r.shoot;
            toast((editing ? '已修改' : '已登記') + (s && s.sync_error ? '，但日曆同步失敗（清單上可按「重試」）' : ''), s && s.sync_error ? 'err' : 'ok');
            saved = true;
            markStale('projects');     // 專案的拍攝日從場次算
            await loadList();
        } catch (e) { toast(e.message || '儲存失敗', 'err'); }
    });
    if (saved) { resetForm(); setFormOpen(false); window.scrollTo({ top: 0, behavior: 'smooth' }); }
}

// ── 清單：今天／本週／下週／之後 直接列；已過、已取消 各收一個折疊（10 筆一頁）──
function cardHtml(r) {
    const when = fmtDate(r.date) + weekday(r.date)
        + (r.end_date && r.end_date !== r.date ? '～' + fmtDate(r.end_date) : '')
        + ' ' + (r.start_time ? r.start_time + (r.end_time ? '–' + r.end_time : '') : '全天');
    const sync = r.sync_error
        ? `${pill('同步失敗', 'bad')} <button type="button" class="m-btn sm w" data-act="resync" data-id="${esc(r.id)}">重試</button>`
        : (r.synced_at ? pill('已同步日曆', 'ok') : '');
    const meta = [r.location_name || r.location_text || '', `人員 ${cnt(r.crew_count, r.crew)}`, `器材 ${cnt(r.equipment_count, r.equipment)}`]
        .filter(Boolean).map(esc).join(' · ');
    const eq = r.equipment || [];
    const eqHtml = eq.length ? `<details class="m-more"><summary>器材明細（${eq.length}）</summary>${eq.map(e =>
        `<div class="li"><span class="l">${esc([e.category, e.name].filter(Boolean).join('｜'))}</span>
         <span class="r">${pill(e.state, e.state === pickedUp() ? 'pri' : '')}${e.conflict ? ' ' + pill('撞期', 'bad') : ''}</span></div>`).join('')}</details>` : '';
    const btn = (act, label, { cls = '', disabled = false } = {}) =>
        `<button type="button" class="m-btn ${cls}" data-act="${act}" data-id="${esc(r.id)}"${disabled ? ' disabled' : ''}>${esc(label)}</button>`;
    const canPickup = eq.some(e => e.state === reserved()), canReturn = eq.some(e => e.state === pickedUp());
    let actions = '';
    if (r.status === cancelled()) actions = btn('reopen', '改回' + scheduled());
    else if (r.status === doneStatus()) actions = btn('edit', '修改') + (canReturn ? btn('return', '已歸還') : '') + btn('reopen', '改回' + scheduled());
    else actions = btn('edit', '修改')
        + (eq.length ? btn('pickup', '器材已領', { disabled: !canPickup }) + btn('return', '已歸還', { disabled: !canReturn }) : '')
        + btn('done', '完成') + btn('cancel', '取消', { cls: 'danger' });
    return `
      <div class="m-card" data-id="${esc(r.id)}">
        <div class="t"><div class="name">${esc(when)}</div>${pill(r.status, statusCls(r.status))}</div>
        <div class="sub" style="color:var(--ink);font-size:14px">${esc(shootName(r))}${r.title && r.title !== r.project_name ? ' · ' + esc(r.title) : ''}</div>
        <div class="sub">${meta}${sync ? ' · ' + sync : ''}</div>
        ${r.sync_error ? `<div class="sub" style="color:var(--bad)">${esc(String(r.sync_error).slice(0, 160))}</div>` : ''}
        ${r.notes ? `<div class="sub">${esc(r.notes)}</div>` : ''}
        ${eqHtml}
        <div class="m-actions w">${actions}</div>
      </div>`;
}

function drawList() {
    const box = F('list');
    const today = todayLocal();
    const dow = (new Date(today + 'T00:00:00').getDay() + 6) % 7;     // 週一＝0
    const monday = addDays(today, -dow), weekEnd = addDays(monday, 6), nextEnd = addDays(monday, 13);
    const endOf = (r) => r.end_date || r.date || '';
    const key = (r) => (r.date || '') + ' ' + (r.start_time || '');
    const asc = (a, b) => key(a).localeCompare(key(b)), desc = (a, b) => key(b).localeCompare(key(a));
    const live = _rows.filter(r => r.status !== cancelled());
    const g = {
        today: live.filter(r => r.date <= today && endOf(r) >= today).sort(asc),
        week: live.filter(r => r.date > today && r.date <= weekEnd).sort(asc),
        next: live.filter(r => r.date > weekEnd && r.date <= nextEnd).sort(asc),
        later: live.filter(r => r.date > nextEnd).sort(asc),
        past: live.filter(r => endOf(r) < today).sort(desc),
        cancelled: _rows.filter(r => r.status === cancelled()).sort(desc),
    };
    const group = (title, rows) => (rows.length ? `<div class="m-h">${esc(title)}（${rows.length}）</div>${rows.map(cardHtml).join('')}` : '');
    const upcoming = g.today.length + g.week.length + g.next.length + g.later.length;
    box.innerHTML = `<div class="m-h">今天（${g.today.length}）</div>${g.today.length ? g.today.map(cardHtml).join('') : '<div class="m-empty" style="padding:12px 0">今天沒有拍攝</div>'}`
        + group('本週', g.week) + group('下週', g.next) + group('之後', g.later)
        + (upcoming ? '' : emptyBox('之後還沒有排拍攝，按上面「登記拍攝」'))
        + `<details class="m-fold"><summary>已過（${g.past.length}）</summary><div id="cal-past"></div></details>
           <details class="m-fold"><summary>已取消（${g.cancelled.length}）</summary><div id="cal-cancelled"></div></details>`;
    renderPaged(box.querySelector('#cal-past'), g.past, cardHtml, { empty: `最近 ${PAST_DAYS} 天沒有已過的場次` });
    renderPaged(box.querySelector('#cal-cancelled'), g.cancelled, cardHtml, { empty: '沒有取消的場次' });
    if (_editing) markEditButton(_editing.id);
}

async function loadList() {
    const box = F('list');
    if (!_rows.length) box.innerHTML = skeleton(3);
    try {
        const qs = new URLSearchParams({ from: addDays(todayLocal(), -PAST_DAYS), limit: '300', offset: '0' });
        const d = await mfetch(`${API}?${qs}`);
        _rows = d.shoots || [];
        drawList();
    } catch (e) { box.innerHTML = errBox(e); }
}

// 卡片動作：修改填回表單；其餘直接打端點（取消先 confirm），回來的 shoot 換掉清單那一列
async function act(btn) {
    const id = btn.dataset.id, kind = btn.dataset.act;
    const row = _rows.find(r => String(r.id) === id); if (!row) return;
    if (kind === 'edit') { startEdit(row); return; }
    if (kind === 'cancel' && !window.confirm(`取消「${shootName(row)}」${fmtDate(row.date)} 這場？器材預約會一起釋放，日曆事件會刪掉。`)) return;
    const path = { pickup: '/equipment/pickup', return: '/equipment/return', resync: '/resync', done: '/status', cancel: '/status', reopen: '/status' }[kind];
    if (!path) return;
    const body = kind === 'done' ? { status: doneStatus() } : kind === 'cancel' ? { status: cancelled() } : kind === 'reopen' ? { status: scheduled() } : {};
    await withBusy(btn, async () => {
        try {
            const r = await mfetch(`${API}/${encodeURIComponent(id)}${path}`, { method: 'POST', body });
            const s = r && r.shoot;
            const failed = kind === 'resync' && s && s.sync_error;
            toast({ pickup: '器材已登記領出', return: '器材已登記歸還', done: '已標記完成', cancel: '已取消這場',
                    reopen: '已改回' + scheduled(), resync: failed ? '同步仍失敗：' + s.sync_error : '已同步到日曆' }[kind], failed ? 'err' : 'ok');
            if (s) { const i = _rows.findIndex(x => String(x.id) === id); if (i >= 0) _rows[i] = s; drawList(); }
            else await loadList();
            markStale('projects');
            if (_editing && String(_editing.id) === id) resetForm();     // 正在改的那場被取消／完成了，表單別留著舊資料
        } catch (e) { toast(e.message, 'err'); }
    });
}

// ── 行事曆設定卡（只有管理員）：服務帳號 email、日曆 ID、測試連線、最近一次錯誤 ──
function cfgHtml(d) {
    return `
      <div class="m-h">行事曆設定</div>
      <div class="m-card m-form" id="cal-cfg-card">
        <div class="kv"><span class="k">服務帳號</span><span class="v" id="cal-cfg-email" style="word-break:break-all">${esc(d.service_account_email || '（還沒設定服務帳號金鑰）')}</span></div>
        <div class="m-hint" style="margin:8px 0 12px">把公司 Google 日曆分享給這個信箱，權限選「變更活動」</div>
        <label>日曆 ID</label><input id="cal-cfg-id" value="${esc(d.calendar_id || '')}" placeholder="xxx@group.calendar.google.com" autocapitalize="none" autocorrect="off">
        <div class="m-actions" style="margin-top:0"><button type="button" class="m-btn pri" id="cal-cfg-save">儲存</button><button type="button" class="m-btn" id="cal-cfg-test">測試連線</button></div>
        <div class="sub" style="color:var(--sub);font-size:12px;margin-top:10px">${d.configured ? '已設定' : '未設定（存了日曆 ID 才會同步）'}${d.last_sync_at ? ' · 最近同步 ' + esc(String(d.last_sync_at).replace('T', ' ').slice(0, 16)) : ''}</div>
        <div class="m-err" id="cal-cfg-err" style="margin:10px 0 0"${d.last_error ? '' : ' hidden'}>${esc(d.last_error || '')}</div>
      </div>`;
}
function wireCfg() {
    F('cfg-save').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        try {
            const d = await mfetch(API + '/calendar/config', { method: 'PUT', body: { calendar_id: F('cfg-id').value.trim() } });
            toast('已儲存日曆 ID');
            F('cfg').innerHTML = cfgHtml(d); wireCfg();
        } catch (e) { toast(e.message, 'err'); }
    }));
    F('cfg-test').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        try {
            const r = await mfetch(API + '/calendar/test', { method: 'POST', body: {} });
            toast(r.ok ? '連得上：' + (r.calendar_summary || r.message || '') : (r.message || '連線失敗'), r.ok ? 'ok' : 'err');
        } catch (e) { toast(e.message, 'err'); }
    }));
}
async function loadCfg() {
    const box = F('cfg');
    if (!isAdmin()) { box.innerHTML = ''; return; }
    try { const d = await mfetch(API + '/calendar/status'); box.innerHTML = cfgHtml(d); wireCfg(); }
    catch (e) { box.innerHTML = '<div class="m-h">行事曆設定</div>' + errBox(e); }
}

// 從專案抽屜「登記拍攝」進來：套專案、展開表單
function applyPreset() {
    if (!state.shootPreset) return;
    const pid = String(state.shootPreset);
    state.shootPreset = null;
    if (_editing) resetForm();
    const h = F('project_id');
    if (h && h._set && (h._items || []).some(i => String(i.value) === pid)) h._set(pid);
    else toast('這個案子不在專案清單裡，請打字找', 'err');
    setFormOpen(true);
}

export async function render(host, { first }) {
    _host = host;
    if (first) {
        host.innerHTML = layout();
        F('date').value = todayLocal();
        F('toggle').addEventListener('click', () => setFormOpen(F('form').hidden));
        F('form').addEventListener('submit', submit);
        F('cancel-edit').addEventListener('click', resetForm);
        F('date').addEventListener('change', refreshBusy);
        F('end_date').addEventListener('change', refreshBusy);
        F('crew-chips').addEventListener('click', onChipRm);
        F('equip-chips').addEventListener('click', onChipRm);
        F('list').addEventListener('click', (ev) => { const b = ev.target.closest('button[data-act]'); if (b) act(b); });
    }
    // 字彙／器材／專案跟清單一起抓：專案分頁新建案子會 markStale('calendar')，切過來才看得到它
    if (shouldLoad('calendar', { first })) await Promise.all([loadOptions(), loadList(), first ? loadCfg() : Promise.resolve()]);
    applyPreset();
}
