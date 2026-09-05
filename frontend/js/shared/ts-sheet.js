/**
 * ts-sheet.js — Sheet 式工作紀錄格子（唯一正本）。
 *
 * 工作追蹤「我的一天」（tabs/timesheets/timesheets.js）與員工工作台「今天的專案紀錄」
 * （/my.html）都 import 這裡；列的 html、格線／列號／選到藍框的 CSS、鍵盤走列、
 * 起訖算小時、工作階段跟著分類走、逐列自動存，全部只有這一份（tests/unit/test_ts_shared_components.py 釘住）。
 *
 * 欄位：# | 專案 | 分類 | 工作階段 | 做了什麼 | 備註 | 起 | 訖 | 實際 h | 計畫 h | 狀態 | ×
 * - 專案格掛 project-pop（進行中／已結案浮層），選到的 id 記在 data-pid。
 * - 工作階段：select 只列「該列分類」的階段（opts.stages ＝ {分類名: [{id, name}]}，來自
 *   /timesheets/options 的 stages）；分類改了、原階段不在新清單 → 清空並在狀態格提示。
 *   停用的舊階段（列上帶 stage_id 但不在清單）照樣顯示，不硬清。
 * - 預設五空列；最後一列有內容自動再長五列；↓ 走到底也長列；Enter 不跳列。
 * - 主題：CSS 變數 --sh-*，預設深色（SPA），白底頁在 table.ts-sheet 上覆寫變數即可。
 */
import { esc, ensureStyle } from './dom.js';
import { attachProjectPop } from './project-pop.js';

export const SHEET_COLS = [
    ['project', '專案'], ['type', '分類'], ['stage', '工作階段'], ['note', '做了什麼'], ['remark', '備註'],
    ['t0', '起'], ['t1', '訖'], ['hours', '時數 h'], ['state', ''],
];
export const BLANK_ROWS = 5;

const CSS = `
table.ts-sheet { --sh-bg:#1b1b1b; --sh-line:#3a3a3a; --sh-head:#262626; --sh-head-ink:#bbb; --sh-ink:#eee; --sh-sub:#777;
    --sh-ro:#1f1f1f; --sh-ro-ink:#888; --sh-hover:#202020; --sh-focus:#1f2937; --sh-accent:#3b82f6; --sh-del:#666; --sh-del-hover:#f87171;
    --sh-ok:#6ee7b7; --sh-busy:#93c5fd; --sh-err:#fca5a5;
    width:100%; table-layout:fixed; border-collapse:collapse; counter-reset:sheetrow; background:var(--sh-bg); font-size:12.5px; }
table.ts-sheet th, table.ts-sheet td { border:1px solid var(--sh-line); padding:0; height:32px; color:var(--sh-ink); }
table.ts-sheet th { background:var(--sh-head); color:var(--sh-head-ink); font-weight:500; font-size:12px; text-align:center; padding:6px 4px; white-space:nowrap; }
table.ts-sheet th[data-col="project"] { width:18%; }
table.ts-sheet th[data-col="type"] { width:96px; }
table.ts-sheet th[data-col="stage"] { width:96px; }
table.ts-sheet th[data-col="remark"] { width:15%; }
table.ts-sheet th[data-col="t0"], table.ts-sheet th[data-col="t1"] { width:64px; }
table.ts-sheet th[data-col="hours"] { width:54px; }
table.ts-sheet th[data-col="state"] { width:60px; }
table.ts-sheet td.ts-sheet-state { font-size:11px; color:var(--sh-sub); text-align:center; white-space:nowrap; padding:0 4px; overflow:hidden; text-overflow:ellipsis; }
table.ts-sheet tr[data-readonly] td { color:var(--sh-ro-ink); background:var(--sh-ro); }
table.ts-sheet td input:disabled, table.ts-sheet td select:disabled, table.ts-sheet td input[readonly] { color:var(--sh-ro-ink); opacity:1; }
table.ts-sheet td input, table.ts-sheet td select { width:100%; height:32px; box-sizing:border-box; margin:0; padding:4px 8px;
    background:transparent; border:none; border-radius:0; color:var(--sh-ink); font-size:13px; font-family:inherit; }
table.ts-sheet td input[type="number"] { text-align:right; }
table.ts-sheet td textarea { width:100%; height:32px; min-height:32px; box-sizing:border-box; margin:0; padding:7px 8px; display:block;
    background:transparent; border:none; border-radius:0; color:var(--sh-ink); font-size:13px; font-family:inherit; line-height:18px;
    resize:none; overflow:hidden; white-space:pre-wrap; word-break:break-all; }
table.ts-sheet td textarea[readonly] { color:var(--sh-ro-ink); opacity:1; }
table.ts-sheet td select option { background:var(--sh-bg); color:var(--sh-ink); }
table.ts-sheet td:focus-within { outline:2px solid var(--sh-accent); outline-offset:-2px; background:var(--sh-focus); }
table.ts-sheet tbody tr:hover td { background:var(--sh-hover); }
table.ts-sheet tbody tr[data-readonly]:hover td { background:var(--sh-ro); }
table.ts-sheet .ts-sheet-num { width:34px; background:var(--sh-head); color:var(--sh-sub); text-align:center; font-size:11px; }
table.ts-sheet tbody tr { counter-increment:sheetrow; }
table.ts-sheet tbody td.ts-sheet-num::before { content:counter(sheetrow); }
table.ts-sheet .ts-sheet-del { width:30px; text-align:center; border-left:none; background:transparent; border-color:transparent; }
table.ts-sheet .ts-sheet-del button { background:none; border:none; color:var(--sh-del); cursor:pointer; font-size:14px; }
table.ts-sheet .ts-sheet-del button:hover { color:var(--sh-del-hover); }
`;

/** 帶 Bearer 的 JSON fetch（工作台頁沒有 tab 的 tfetch 時用這支；錯誤丟 Error(detail)）。 */
export async function tsFetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        method: opts.method || 'GET',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json',
                   ...(token ? { 'Authorization': 'Bearer ' + token } : {}) },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) {
        const err = new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
        err.status = r.status;
        throw err;
    }
    return r.json();
}

// ── 下拉 ──
export const stagesFor = (stages, workType) => ((stages && workType && stages[workType]) || []);

export function typeSelectHtml(cur, attr, workTypes) {
    return `<select ${attr} data-no-search><option value="">分類</option>${(workTypes || []).map(t =>
        `<option value="${esc(t)}"${t === cur ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
}

/** 工作階段下拉：只列該分類的階段；目前值不在清單（停用了）→ 多留一個選項把它顯示出來。 */
export function stageSelectHtml(cur, attr, list) {
    const curId = (cur && cur.id) || '';
    const rows = [...(list || [])];
    if (curId && !rows.some(s => s.id === curId)) rows.push({ id: curId, name: (cur.name || '') + '（停用）' });
    return `<select ${attr} data-no-search><option value="">—</option>${rows.map(s =>
        `<option value="${esc(s.id)}"${s.id === curId ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select>`;
}

/** 列的 html —— 唯一的一份。v＝格子的值；o＝{id, readonly}；ctx＝{workTypes, stages}。 */
export function rowHtml(v = {}, o = {}, ctx = {}) {
    // Sheet 列：input 用 readonly（文字還能選取、複製貼到下一列）；select 沒有 readonly 只能 disabled
    const ro = o.readonly ? ' readonly' : '';
    const rosel = o.readonly ? ' disabled' : '';
    const t = 'type="text" inputmode="numeric" maxlength="5" placeholder="09:00" autocomplete="off"';
    const pid = v.project_id ? ` data-pid="${esc(v.project_id)}" data-pname="${esc(v.project || '')}"` : '';
    return `<tr class="ts-mine-row"${o.id ? ` data-id="${esc(o.id)}"` : ''}${o.readonly ? ' data-readonly="1"' : ''}${v.bulletin_id ? ` data-bulletin="${esc(v.bulletin_id)}"` : ''}>
        <td class="ts-sheet-num"></td>
        <td><textarea data-proj-pick autocomplete="off" data-f="project" rows="1"${pid}${ro}>${esc(v.project || '')}</textarea></td>
        <td>${typeSelectHtml(v.work_type || '', `data-f="type"${rosel}`, ctx.workTypes)}</td>
        <td>${stageSelectHtml({ id: v.stage_id || '', name: v.stage_name || '' }, `data-f="stage"${rosel}`, stagesFor(ctx.stages, v.work_type))}</td>
        <td><input type="text" data-f="note" value="${esc(v.note || '')}"${ro}></td>
        <td><input type="text" data-f="remark" value="${esc(v.remark || '')}"${ro}></td>
        <td><input ${t} data-f="t0" value="${esc(v.t0 || '')}"${ro}></td>
        <td><input ${t} data-f="t1" value="${esc(v.t1 || '')}"${ro}></td>
        <td><input type="number" data-f="hours" min="0" step="any" value="${v.hours ?? ''}"${ro}></td>
        <td class="ts-sheet-state" data-f="state">${o.readonly ? 'Sheet' : (o.id ? '已存' : '')}</td>
        <td class="ts-sheet-del">${o.readonly ? '' : '<button type="button" data-ts-action="row-remove" title="刪這一列">×</button>'}</td>
    </tr>`;
}

export function tableHtml(rowsHtml, id = 'ts-mine-add') {
    return `<table ${id ? `id="${id}"` : ''} class="ts-sheet">
        <thead><tr><th class="ts-sheet-num"></th>${SHEET_COLS.map(([k, l]) => `<th data-col="${k}">${l}</th>`).join('')}<th class="ts-sheet-del"></th></tr></thead>
        <tbody>${rowsHtml}</tbody>
    </table>`;
}

/** 後端的一列（ts_dict）→ 格子的值。 */
export function rowFromItem(i) {
    return { project: i.project_name, project_id: i.project_id, work_type: i.work_type, stage_id: i.stage_id, stage_name: i.stage_name,
             note: i.task_note, remark: i.remark, t0: i.start_time || '', t1: i.end_time || '',
             planned: i.planned_hours, hours: i.hours || '', bulletin_id: i.bulletin_id };
}

const _ctx = (host) => host._tsCtx || {};
const _tbody = (host) => host.querySelector('table.ts-sheet tbody');

/**
 * 把格子畫進 host。rows＝後端的列；opts：
 *   id（table id，預設 ts-mine-add）、readonly（整張唯讀）、stages、workTypes、
 *   projectPicker（() => rows|Promise，給專案浮層）、blankRows（預設 5）。
 */
export function renderSheet(host, rows, opts = {}) {
    ensureStyle('ts-sheet-css', CSS);
    host._tsCtx = { ...(host._tsCtx || {}), workTypes: opts.workTypes || [], stages: opts.stages || {}, readonly: !!opts.readonly };
    const ctx = host._tsCtx;
    const body = (rows || []).map(r => rowHtml(rowFromItem(r), { id: r.id, readonly: opts.readonly || !r.editable }, ctx)).join('');
    host.innerHTML = tableHtml(body + (opts.readonly ? '' : rowHtml({}, {}, ctx).repeat(opts.blankRows ?? BLANK_ROWS)), opts.id ?? 'ts-mine-add');
    if (opts.projectPicker) attachProjectPop(host, { options: opts.projectPicker, value: (p) => (p.id ? (p.name || p.label) : (p.label || p.name)) });
    _wire(host);
    fitTextareas(host);
    return host.querySelector('table.ts-sheet');
}

export function appendBlankRows(host, n = BLANK_ROWS) {
    const tb = _tbody(host);
    if (tb) tb.insertAdjacentHTML('beforeend', rowHtml({}, {}, _ctx(host)).repeat(n));
}

/** 加一列帶值的新列（帶入鈕用）。 */
export function appendRow(host, v = {}) {
    const tb = _tbody(host);
    if (!tb) return null;
    tb.insertAdjacentHTML('beforeend', rowHtml(v, {}, _ctx(host)));
    fitTextareas(tb.lastElementChild);
    return tb.lastElementChild;
}

/** 把沒填的空列拿掉（帶入前用，帶進來的列才不會夾在空列後面）。 */
export function dropEmptyRows(host) {
    [..._tbody(host)?.querySelectorAll('.ts-mine-row') || []]
        .filter(tr => !tr.dataset.id && !tr.querySelector('[data-f="project"]').value && !tr.querySelector('[data-f="note"]').value)
        .forEach(tr => tr.remove());
}

/** 換一套階段清單（工作階段設定改完）：新列用新清單；既有列重畫它的階段下拉、值保留。 */
export function setStages(host, stages) {
    host._tsCtx = { ...(host._tsCtx || {}), stages: stages || {} };
    host.querySelectorAll('.ts-mine-row').forEach(tr => _syncStage(tr, host, true));
}

/** 一列的值（唯讀列也回；project_id 來自浮層選到的 data-pid）。 */
export function rowValues(tr) {
    const q = f => tr.querySelector(`[data-f="${f}"]`);
    const v = f => q(f)?.value ?? '';
    const proj = q('project');
    return {
        tr, id: tr.dataset.id || '', readonly: !!tr.dataset.readonly, bulletin_id: tr.dataset.bulletin || '',
        project: v('project').trim(), project_pid: (proj && proj.dataset.pid && v('project').trim() === proj.dataset.pname) ? proj.dataset.pid : '',
        work_type: v('type'), stage_id: v('stage'), stage_name: q('stage')?.selectedOptions?.[0]?.textContent || '',
        note: v('note'), remark: v('remark'), t0: v('t0'), t1: v('t1'),
        hours: v('hours') ? parseFloat(v('hours')) : null, planned: v('planned') ? parseFloat(v('planned')) : null,
    };
}

/** 有專案或內容的列（儲存只送這些）；all=true 連空列一起回。 */
export function collectRows(host, { all = false } = {}) {
    return [...host.querySelectorAll('.ts-mine-row')].map(rowValues).filter(r => all || r.project || r.note);
}

/** 專案格打的字 → {project_id, project_name}：浮層選的帶 id；對得到清單的 label／案名也帶 id；對不到就照打的字送（後端再對映）。 */
export function projectFromInput(text, el = null, projects = []) {
    const t = (text || '').trim();
    if (el && el.dataset && el.dataset.pid && t === el.dataset.pname) return { project_id: el.dataset.pid, project_name: t };   // 從浮層選的
    const hit = (projects || []).find(p => p.id && (p.label === t || p.name === t));
    return hit ? { project_id: hit.id, project_name: hit.name } : { project_id: null, project_name: t };
}

/** 一列 → 送給後端的 body（POST /timesheets/mine/rows 的 rows[]、PUT /timesheets/mine/{id} 同一形狀）。 */
export function rowBody(tr, { day = '', projects = [] } = {}) {
    const v = f => tr.querySelector(`[data-f="${f}"]`)?.value ?? '';
    const body = {
        work_date: v('date') || day, ...projectFromInput(v('project'), tr.querySelector('[data-f="project"]'), projects),
        work_type: v('type') || null,
        task_note: v('note'), remark: v('remark'),
        hours: v('hours') ? parseFloat(v('hours')) : null,
    };
    // 起／訖：有那兩欄的格子才送（"" ＝清空）；重新整理要還在（owner 2026-09-06）
    if (tr.querySelector('[data-f="t0"]')) { body.start_time = v('t0'); body.end_time = v('t1'); }
    // 計畫 h 欄 2026-09-06 從格子拿掉；有那欄的格子才送，沒有就不碰既有的 planned_hours
    if (tr.querySelector('[data-f="planned"]')) body.planned_hours = v('planned') ? parseFloat(v('planned')) : null;
    // 只有格子本身有階段欄才送 stage_id（空字串＝清空）；總表改列／代填的列沒這欄，不能把人家的階段洗掉
    if (tr.querySelector('[data-f="stage"]')) body.stage_id = v('stage') || '';
    if (tr.dataset.bulletin) body.bulletin_id = tr.dataset.bulletin;
    return body;
}

/** 「9」「930」「0930」「9:30」「17.30」→ "09:30"；看不懂 → ""。 */
export function normTime(v) {
    const m = String(v || '').trim().replace(/[.．：]/g, ':').match(/^(\d{1,2})(?::?(\d{2}))?$/);
    if (!m) return '';
    const h = Number(m[1]), mm = Number(m[2] || 0);
    if (h > 23 || mm > 59) return '';
    return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

/** 起訖時間 → 實際小時（兩位小數；訖比起早＝跨午夜）；填進同一列的「實際」欄。 */
export function applyTimeRange(tr) {
    const t0 = tr.querySelector('[data-f="t0"]')?.value, t1 = tr.querySelector('[data-f="t1"]')?.value;
    if (!t0 || !t1) return;
    const m = s => { const [h, mm] = s.split(':').map(Number); return h * 60 + mm; };
    let mins = m(t1) - m(t0);
    if (mins < 0) mins += 24 * 60;
    tr.querySelector('[data-f="hours"]').value = Math.round(mins / 60 * 100) / 100;
}

/** 分類變了 → 階段下拉換成該分類的清單；原階段不在裡面就清空（提示在狀態格）。keep＝重畫但值照舊（換清單用）。 */
function _syncStage(tr, host, keep = false) {
    const sel = tr.querySelector('[data-f="stage"]');
    if (!sel) return;
    const type = tr.querySelector('[data-f="type"]')?.value || '';
    const list = stagesFor(_ctx(host).stages, type);
    const cur = sel.value, curName = sel.selectedOptions?.[0]?.textContent || '';
    const stillThere = cur && list.some(s => s.id === cur);
    const wrap = document.createElement('div');
    wrap.innerHTML = stageSelectHtml(keep && cur ? { id: cur, name: curName.replace(/（停用）$/, '') } : (stillThere ? { id: cur, name: curName } : { id: '', name: '' }),
                                     `data-f="stage"${sel.disabled ? ' disabled' : ''}`, list);
    sel.replaceWith(wrap.firstElementChild);
    if (cur && !stillThere && !keep) {
        const st = tr.querySelector('[data-f="state"]');
        if (st) { st.textContent = '階段已清空'; st.style.color = ''; }
    }
}

/** 折行的格子（textarea）：高度回到 CSS 的一列，內容放不下才長到剛好（owner 2026-09-06：案名太長要折行）。 */
export function fitTextareas(root) {
    (root?.querySelectorAll ? root.querySelectorAll('table.ts-sheet td textarea, td textarea') : []).forEach(fitTextarea);
}
function fitTextarea(ta) {
    if (!ta || ta.tagName !== 'TEXTAREA') return;
    ta.style.height = '';
    if (ta.scrollHeight > ta.clientHeight) ta.style.height = ta.scrollHeight + 'px';
}

/** 鍵盤：↓ 到下一列同欄（沒有就長一列）、↑ 上一列同欄；Enter 不跳列也不換行（專案格的 Enter 是選取浮層建議）；分類（select）的上下鍵留給它自己。 */
function _keydown(ev, host) {
    const inp = ev.target.closest('table.ts-sheet [data-f]');
    if (!inp || _ctx(host).readonly) return;
    if (ev.key === 'Enter' && inp.tagName === 'TEXTAREA') { ev.preventDefault(); return; }   // 折行格：Enter 不是換行
    const down = ev.key === 'ArrowDown', up = ev.key === 'ArrowUp';
    if (!down && !up) return;
    if (inp.tagName === 'SELECT') return;
    const tr = inp.closest('tr');
    let target = up ? tr.previousElementSibling : tr.nextElementSibling;
    if (!target && down) { tr.insertAdjacentHTML('afterend', rowHtml({}, {}, _ctx(host))); target = tr.nextElementSibling; }
    if (!target) return;
    ev.preventDefault();
    const next = target.querySelector(`[data-f="${inp.dataset.f}"]`);
    if (next) { next.focus(); if (next.select) next.select(); }
}

/** 在最後一列打了東西 → 自動再長五列（Sheet 的感覺：永遠有空列可以往下填）。 */
function _grow(ev, host) {
    const inp = ev.target.closest('table.ts-sheet [data-f]');
    if (!inp) return;
    const tr = inp.closest('tr');
    if (!tr.nextElementSibling && inp.value) tr.insertAdjacentHTML('afterend', rowHtml({}, {}, _ctx(host)).repeat(BLANK_ROWS));
}

function _wire(host) {
    if (host.dataset.tsSheetWired) return;
    host.dataset.tsSheetWired = '1';
    host.addEventListener('keydown', (ev) => _keydown(ev, host));
    host.addEventListener('input', (ev) => {
        _grow(ev, host);
        fitTextarea(ev.target);
        const cell = ev.target.closest('table.ts-sheet [data-f]');
        if (cell && cell.dataset.f !== 't0' && cell.dataset.f !== 't1') host._tsSchedule?.(cell.closest('tr'));   // 起訖等離開格子再算
    });
    host.addEventListener('change', (ev) => {
        const t = ev.target.closest('table.ts-sheet [data-f="t0"], table.ts-sheet [data-f="t1"]');
        if (t) { t.value = normTime(t.value); applyTimeRange(t.closest('tr')); }
        const ty = ev.target.closest('table.ts-sheet [data-f="type"]');
        if (ty) _syncStage(ty.closest('tr'), host);
        const cell = ev.target.closest('table.ts-sheet [data-f]');
        if (cell) host._tsSchedule?.(cell.closest('tr'));       // 選單／時間／數字改了就存
    });
}

// ── 逐列自動存（我的一天／今天的專案紀錄同一條）──
const _timers = new WeakMap();

/**
 * 掛逐列自動存：填齊（專案＋實際或計畫）才 POST，回來把 id 掛上；有 id＝PUT；同列上一筆還在飛就排在後面。
 * cfg：tfetch（預設 tsFetch）、day（() => YYYY-MM-DD）、projects（() => 專案清單，對 label 回 id）、
 *      onSaved(tr, body)、onUnmatched(names)。
 */
export function wireAutosave(host, cfg = {}) {
    const f = cfg.tfetch || tsFetch;
    const save = async (tr) => {
        if (!tr || !tr.isConnected || tr.dataset.readonly) return;
        if (tr._saving) { tr._again = true; return; }
        const st = tr.querySelector('[data-f="state"]');
        const body = rowBody(tr, { day: cfg.day ? cfg.day() : '', projects: cfg.projects ? (cfg.projects() || []) : [] });
        const complete = body.project_name && ((body.hours || 0) > 0 || (body.planned_hours || 0) > 0);
        if (!complete) { st.textContent = body.project_name || body.task_note ? '再填時數' : ''; st.style.color = ''; return; }
        tr._saving = true;
        st.textContent = '儲存中…'; st.style.color = 'var(--sh-busy)';
        try {
            if (tr.dataset.id) {
                await f('/api/v1/timesheets/mine/' + tr.dataset.id, { method: 'PUT', body });
            } else {
                const r = await f('/api/v1/timesheets/mine/rows', { method: 'POST', body: { rows: [body] } });
                tr.dataset.id = (r.ids || [])[0] || '';
                if ((r.unmatched_projects || []).length) cfg.onUnmatched?.(r.unmatched_projects);
            }
            st.textContent = '已存'; st.style.color = 'var(--sh-ok)';
            cfg.onSaved?.(tr, body);
        } catch (e) {
            st.textContent = '沒存：' + (e.message || e); st.style.color = 'var(--sh-err)';
        } finally {
            tr._saving = false;
            if (tr._again) { tr._again = false; host._tsSchedule(tr); }
        }
    };
    host._tsSchedule = (tr) => { clearTimeout(_timers.get(tr)); _timers.set(tr, setTimeout(() => save(tr), 600)); };
    host._tsSaveNow = save;
    return { save, schedule: host._tsSchedule };
}

/** 立刻存這一列（帶入鈕把列加進來後用）。 */
export const saveRowNow = (host, tr) => host._tsSaveNow?.(tr);

/** 刪一列：有 id 先問再 DELETE；表空了補一列。 */
export async function removeRow(host, tr, cfg = {}) {
    const f = cfg.tfetch || tsFetch;
    // 第一次自動存（POST）還在飛：等它回來拿到 id 再刪，不然 DOM 拿掉了、伺服器卻多一列孤兒
    for (let i = 0; tr._saving && i < 50; i++) await new Promise((r) => setTimeout(r, 100));
    if (tr.dataset.id) {
        if (!confirm('刪掉這一列？')) return false;
        try { await f('/api/v1/timesheets/mine/' + tr.dataset.id, { method: 'DELETE' }); }
        catch (e) { alert('刪除失敗：' + (e.message || e)); return false; }
    }
    const tb = tr.parentElement;
    tr.remove();
    if (tb && !tb.children.length) tb.insertAdjacentHTML('beforeend', rowHtml({}, {}, _ctx(host)));
    return true;
}
