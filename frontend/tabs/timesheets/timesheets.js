/**
 * timesheets.js — 工作追蹤 Tab（人事管理；docs/WORK_TRACKING_UI_PLAN.md P1）
 *
 * MASTER 同源功能：打 /api/v1/timesheets/*（帶 auth token）。
 * 七個分頁：今日（每日看板：每個人每天做了什麼，實際為主、計畫加分）／我的一天（登入者
 * 自己記：實際或計畫、時數快捷鈕、複製昨天）／專案（burn 表 → 專案檔案頁、類似專案並排）／
 * 人員（月視圖 → 人員檔案頁）／總表（一個月每一列，管理員逐列改細節與備註）／儀表板（大家
 * 四格＋主管兩格）／設定（Sheet 拉取、digest、未對映指定、代填、token；管理員）。
 * 一列的「計畫／實際」由後端的 status 決定（plan／draft），這裡只顯示、不再自己判。
 * 事件：整個 tab 一個委派的 click 監聽（initTimesheetsTab 註冊一次），局部重繪不再重綁。
 * 資料 = Google Sheet 每週六拉 + 系統內填（同人同日同案手填優先）；總表為準（刪過的 Sheet 列不再插回）。不審核。
 * 新增 UI 依 owner 鐵則無 emoji（既有元素不回溯）。
 */

import { esc } from '../website/website-utils.js';
import { createSortable, sortableTh, today as _today } from '../crm/crm-utils.js';
import { openProjectPicker } from '../../js/shared/project-picker.js';   // 指定專案：共用挑選視窗
import { authDownload } from '../../js/shared/utils.js';
import { hbars } from '../../js/shared/svg-charts.js';

async function tfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        method: opts.method || 'GET',
        headers: {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            ...(token ? { 'Authorization': 'Bearer ' + token } : {}),
        },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.json();
}

let _content = null;
let _view = 'today';   // today | mine | projects | staff | ledger | dash | settings | compare | project:<案名> | person:<人名>
let _workTypes = [];   // 後端的 WORK_TYPES（/mine 與 /rows 都帶）
let _month = _today().slice(0, 7);   // YYYY-MM（本地時區；toISOString 是 UTC，1 號早上會停在上個月）
let _day = _today();                 // 今日看板／我的一天的日期
let _boardDays = 1;                  // 1＝日、7＝週
let _summaryCache = null;   // 最近一次 summary（供點欄頭排序重繪）
let _staffCache = null;     // 最近一次 by_staff
let _recentCache = null;    // 最近一次 recent rows
let _pullCache = null;      // GET /timesheets/pull（主控端定時拉 Sheet 的設定與上次結果）
let _mineCache = null;      // 最近一次 /mine（我的一天）
let _projOpts = null;       // project_options（我的一天的專案 datalist）
let _projectCache = null;   // 最近一次 /project（專案檔案頁）
let _projectPid = '';       // 專案檔案頁：從 burn 表點進來帶的 project_id（撈整個案）；Sheet 案名進來就空
let _projQ = '';            // 專案頁搜尋列（同時篩未對映表與 burn 表；前端篩、不重打 API）
const _projHit = (s) => !_projQ || String(s || '').toLowerCase().includes(_projQ.trim().toLowerCase());
let _compareNames = [];     // 類似專案並排：目前選的案名
let _digestCache = null;    // GET /timesheets/digest
let _ledgerCache = null;    // GET /timesheets/rows（總表：一個月所有列）
let _conflictsCache = [];   // GET /timesheets/conflicts（Sheet 與總表改過的列撞到，等 owner 選；管理員才拉）
let _monthTo = '';          // 總表：迄月（空＝只看 _month 那個月；最多 12 個月）
let _ledgerSel = new Set(); // 總表：勾選的列 id（批次調整）
const _ledgerFilter = { staff: '', project: '', source: '', q: '' };   // 總表篩選（前端做）

function _shiftDay(ymd, delta) {
    const [y, m, d] = ymd.split('-').map(Number);
    const dt = new Date(y, m - 1, d + delta);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}
const _WD = ['日', '一', '二', '三', '四', '五', '六'];
function _dayLabel(ymd) {
    const [y, m, d] = ymd.split('-').map(Number);
    return `${m}/${d}（${_WD[new Date(y, m - 1, d).getDay()]}）`;
}

// ── 設定分頁：Sheet 拉取狀態列 ──
function _pullBar() {
    const p = _pullCache;
    if (!p) return '';
    const state = p.enabled ? `自動拉（cron ${esc(p.cron)}）` : '自動拉取：關';
    const last = p.last_summary ? esc(p.last_summary) : '還沒拉過';
    return `
        <div class="ts-note" style="margin:6px 0 12px;">
            Google Sheet 拉取 — ${state}｜${last}
            ${p.sheet_id ? '' : '｜<b>還沒設試算表 id</b>'}
            <button class="ts-btn ghost" data-ts-action="pull" ${p.running ? 'disabled' : ''}
                    style="margin-left:8px;">${p.running ? '拉取中…' : '立即拉取'}</button>
            <button class="ts-btn ghost" data-ts-action="pull-settings">設定</button>
        </div>`;
}

/** 設定視窗最小化：prompt 試算表 id → 是否啟用；cron 用預設（每週六 09:00）。 */
async function _pullSettings() {
    const cur = _pullCache || {};
    const sid = prompt('工時試算表 id（網址 /d/<id>/ 那段；公開連結即可）', cur.sheet_id || '');
    if (sid === null) return;
    const enable = confirm('要開啟自動拉取嗎？（預設每週六 09:00）\n（取消＝關閉自動拉取，仍可手動「立即拉取」）');
    try {
        _pullCache = await tfetch('/api/v1/timesheets/pull', {
            method: 'PUT', body: { sheet_id: sid.trim(), enabled: enable },
        });
        await refresh();
    } catch (e) {
        alert('儲存失敗：' + (e.message || e));
    }
}

async function _pullNow() {
    try {
        const r = await tfetch('/api/v1/timesheets/pull', { method: 'POST' });
        if (r.status !== 'ok') { alert('拉取沒成功：' + (r.message || r.status)); }
        else {
            alert(`拉了 ${r.rows} 列：新增 ${r.inserted}、重複 ${r.skipped}、壞列 ${r.bad_rows}`
                + (r.unmatched_projects.length ? `\n找不到 ${r.unmatched_projects.length} 個專案名（看未對映表）` : ''));
        }
        _summaryCache = null;
        await refresh();
    } catch (e) {
        alert('拉取失敗：' + (e.message || e));
    }
}

// ── 點欄頭排序：預設 key '' = 不排序、維持後端順序（burn 表本身已按消耗率排好），點了才生效 ──
function _redrawTbody(tableId, tbodyHtmlFn, sorter) {
    const tb = document.querySelector('#' + tableId + ' tbody');
    if (!tb) return;
    tb.innerHTML = tbodyHtmlFn();
    sorter.attach();
}

const _ledgerSorter = createSortable({
    storageKey: 'timesheets_ledger_sort',
    defaultSort: { key: 'date', dir: 'desc' },
    panelId: 'ts-ledger-table',
    onChange: () => _ledgerRedraw(),
    getters: {
        date: i => i.date, staff: i => i.staff_name, project: i => i.project_name || '',
        type: i => i.work_type || '', task: i => i.task_note || '', remark: i => i.remark || '', planned: i => i.planned_hours ?? '',
        hours: i => i.hours, source: i => i.source, note: i => i.note || '',
    },
});
const _burnSorter = createSortable({
    storageKey: 'timesheets_burn_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'ts-burn-table',
    onChange: () => _redrawTbody('ts-burn-table', _burnTbodyHtml, _burnSorter),
    getters: {
        project: p => p.project_name || p.project_id || '',
        status: p => p.status || '',
        type: p => p.project_type || '',
        used: p => p.hours_used ?? '',
        budget: p => p.budget_hours ?? '',
        remaining: p => p.remaining ?? '',
        pct: p => p.pct ?? '',
        rows: p => p.rows ?? '',
        last: p => p.last_entry || '',
    },
});

const _unmatchedSorter = createSortable({
    storageKey: 'timesheets_unmatched_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'ts-unmatched-table',
    onChange: () => _redrawTbody('ts-unmatched-table', _unmatchedTbodyHtml, _unmatchedSorter),
    getters: {
        name: u => u.project_name || '',
        hours: u => u.hours_used ?? '',
        rows: u => u.rows ?? '',
    },
});

const _staffSorter = createSortable({
    storageKey: 'timesheets_staff_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'ts-staff-table',
    onChange: () => _redrawTbody('ts-staff-table', _staffTbodyHtml, _staffSorter),
    getters: {
        name: s => s.name || '',
        hours: s => s.total_hours ?? '',
    },
});

const _recentSorter = createSortable({
    storageKey: 'timesheets_recent_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'ts-recent-table',
    onChange: () => _redrawTbody('ts-recent-table', _recentTbodyHtml, _recentSorter),
    getters: {
        date: r => r.date || '',
        staff: r => r.staff_name || '',
        project: r => r.project_name || '',
        matched: r => r.project_id ? 1 : 0,
        task: r => r.task_note || '',
        hours: r => r.hours ?? '',
    },
});

export async function initTimesheetsTab() {
    _content = document.getElementById('ts-content');
    if (!_content) return;
    // 委派：只註冊一次，之後任何局部重繪（加列、複製昨天、改列）都不用再綁
    _content.addEventListener('click', (ev) => {
        const btn = ev.target.closest('[data-ts-action]');
        if (btn && _content.contains(btn)) _onAction(btn);
    });
    // 起訖時間 → 實際小時（委派，新增列也吃得到）
    _content.addEventListener('change', (ev) => {
        const t = ev.target.closest('[data-f="t0"], [data-f="t1"]');
        if (t) { t.value = _normTime(t.value); _applyTimeRange(t.closest('tr')); }
        const cell = ev.target.closest('#ts-mine-add [data-f]');
        if (cell) _mineScheduleSave(cell.closest('tr'));       // 選單／時間／數字改了就存
        const ts = ev.target.closest('select[data-set-type]');
        if (ts) _setProjectType(ts.dataset.setType, ts.value);   // burn 表改案型
        const pk = ev.target.closest('input[data-pick]');
        if (pk) { if (pk.checked) _ledgerSel.add(pk.dataset.pick); else _ledgerSel.delete(pk.dataset.pick); _ledgerRedraw(); }
        const pa = ev.target.closest('input[data-pick-all]');
        if (pa) { _ledgerSel = pa.checked ? new Set(_ledgerRows().map(i => i.id)) : new Set(); _ledgerRedraw(); }
    });
    _bindProjectPop(_content);                       // 要在 _sheetKeydown 前綁（capture），浮層開著時 ↓↑ 歸浮層
    _content.addEventListener('keydown', _sheetKeydown);
    _content.addEventListener('input', (ev) => {
        _sheetGrow(ev);
        const cell = ev.target.closest('#ts-mine-add [data-f]');
        if (cell && cell.dataset.f !== 't0' && cell.dataset.f !== 't1') _mineScheduleSave(cell.closest('tr'));   // 起訖等離開格子再算
    });
    await refresh();
}

async function refresh() {
    try {
        if (_view === 'today') {
            const d = await tfetch(`/api/v1/timesheets/board?date=${_day}&days=${_boardDays}`);
            _content.innerHTML = _renderToday(d);
        } else if (_view === 'mine') {
            let d = null, err = '';
            try { d = await tfetch('/api/v1/timesheets/mine?date=' + _day); }
            catch (e) { err = e.message || String(e); }
            _mineCache = d;
            if (d) _workTypes = d.work_types || [];
            _content.innerHTML = _renderMine(d, err);
        } else if (_view === 'staff') {
            const d = await tfetch('/api/v1/timesheets/by_staff?month=' + _month);
            _staffCache = d;
            _content.innerHTML = _renderStaffView(d);
        } else if (_view === 'ledger') {
            _ledgerCache = await tfetch('/api/v1/timesheets/rows?month=' + _month + (_monthTo ? '&to=' + _monthTo : ''));
            _ledgerSel = new Set();
            _workTypes = _ledgerCache.work_types || [];
            _conflictsCache = _ledgerCache.editable ? ((await tfetch('/api/v1/timesheets/conflicts')).items || []) : [];
            _content.innerHTML = _renderLedger(_ledgerCache);
        } else if (_view === 'dash') {
            _content.innerHTML = _renderDash(await tfetch('/api/v1/timesheets/dashboard'));
        } else if (_view === 'settings') {
            // /summary 是這個 tab 最重的讀（兩個全表 group by＋查表＋相似度）：有快取就用，
            // 拉取／指定／改預算會把 _summaryCache 清掉，「重新整理」也會
            const [s, pull, digest] = await Promise.all([
                _summaryCache || tfetch('/api/v1/timesheets/summary'),
                tfetch('/api/v1/timesheets/pull').catch(() => null),
                tfetch('/api/v1/timesheets/digest').catch(() => null),
            ]);
            _summaryCache = s; _pullCache = pull; _digestCache = digest;
            _content.innerHTML = _renderSettings(s);
        } else if (_view.startsWith('project:')) {
            const d = await tfetch('/api/v1/timesheets/project?name=' + encodeURIComponent(_view.slice(8))
                + (_projectPid ? '&project_id=' + encodeURIComponent(_projectPid) : ''));
            _projectCache = d;
            _content.innerHTML = _renderProject(d);
        } else if (_view === 'compare') {
            const d = await tfetch('/api/v1/timesheets/compare?names=' + encodeURIComponent(_compareNames.join('|')));
            _content.innerHTML = _renderCompare(d);
        } else if (_view.startsWith('person:')) {
            const d = await tfetch(`/api/v1/timesheets/person?name=${encodeURIComponent(_view.slice(7))}&month=${_month}`);
            _content.innerHTML = _renderPerson(d);
        } else {
            const s = _summaryCache || await tfetch('/api/v1/timesheets/summary');
            _summaryCache = s;
            _content.innerHTML = _renderProjects(s);
        }
        _bind();
        // 各表點欄頭排序（attach 對不存在的表是 no-op）
        _burnSorter.attach();
        _unmatchedSorter.attach();
        _staffSorter.attach();
    } catch (e) {
        _content.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">
            工時資料載入失敗：${esc(e.message || e)}</div>`;
    }
}

// 七個分頁（新元素純文字，無 emoji）
function _viewBtns() {
    const b = (key, label) => `<button class="ts-btn ${_view === key ? '' : 'ghost'}"
        data-ts-action="view" data-view="${key}">${label}</button>`;
    return `<div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        ${b('today', '今日')}${b('mine', '我的一天')}${b('projects', '專案')}${b('staff', '人員')}${b('ledger', '總表')}${b('dash', '儀表板')}${b('settings', '設定')}
    </div>`;
}

function _head(sub) {
    return `<h2>工作追蹤</h2><div class="ts-sub">${sub}</div>${_viewBtns()}`;
}

function _dayNav(extra = '') {
    return `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
        <button class="ts-btn ghost" data-ts-action="day" data-delta="-1">‹</button>
        <input type="date" id="ts-day" value="${esc(_day)}"
               style="background:#1a1a1a;border:1px solid #333;color:#ddd;border-radius:4px;padding:5px 8px;">
        <button class="ts-btn ghost" data-ts-action="day" data-delta="1">›</button>
        <button class="ts-btn ghost" data-ts-action="day" data-delta="0">今天</button>
        ${extra}
    </div>`;
}

// ── 一列的共用片段（看板卡片／週表格／我的一天／時間軸／逐日都吃這幾支）──
const _isPlan = i => i.status === 'plan';                       // 後端 row_state 決定，這裡不重判
function _hoursLabel(i) {
    return _isPlan(i)
        ? `<span style="color:#93c5fd;">計畫 ${i.planned_hours} h</span>`
        : `<b style="color:#eee;">${i.hours} h</b>${i.planned_hours ? `<span style="color:#666;"> ／計畫 ${i.planned_hours}</span>` : ''}`;
}
const _typeTag = i => (i.work_type ? `<span style="color:#9ca3af;">[${esc(i.work_type)}]</span> ` : '');
const _projLink = (name, pid) => (name || pid)
    ? `<span class="ts-link" data-ts-action="proj-pop" data-name="${esc(name || '')}" data-pid="${esc(pid || '')}" title="點開這個案的執行狀態">${esc(name || '(空白)')}</span>`
    : '<span style="color:#777;">(空白)</span>';   // 沒案名沒 id 的列沒有東西可以看
const _srcTag = i => (i.source === 'manual' ? '' : '<span style="color:#666;"> · Sheet</span>');
/** 逐日流水：[{date, items}] → 每天一段；primary＝每項第一個字（時間軸放人名、人員頁放案名）。 */
/** 逐日流水＝四欄表（owner 2026-09-03「日期、人員、內容、使用時數」）；primary＝第二欄放什麼
 *  （專案頁放人名、人員頁放案名可點）。同一天多列只在第一列印日期。 */
function _dayLogRows(days, primary) {
    return days.map(day => day.items.map((i, k) => `<tr>
        <td style="white-space:nowrap;color:#888;">${k === 0 ? esc(_dayLabel(day.date)) : ''}</td>
        <td style="white-space:nowrap;">${primary === 'project_name' ? _projLink(i.project_name, i.project_id) : `<span style="color:#eee;">${esc(i[primary] || '(空白)')}</span>`}</td>
        <td style="color:#bbb;">${_typeTag(i)}${esc(i.task_note || '')}</td>
        <td style="color:#9ca3af;">${esc(i.remark || '')}</td>
        <td class="num" style="white-space:nowrap;">${_hoursLabel(i)}</td>
    </tr>`).join('')).join('');
}
function _dayLogTable(bodyHtml, primary) {
    return `<table class="ts-daylog"><thead><tr><th style="width:110px;">日期</th><th style="width:160px;">${primary === 'project_name' ? '專案' : '人員'}</th><th>內容</th><th style="width:22%;">備註</th><th class="num" style="width:110px;">使用時數</th></tr></thead>
        <tbody>${bodyHtml}</tbody></table>`;
}
function _dayLog(days, primary) {
    return days.length ? _dayLogTable(_dayLogRows(days, primary), primary) : '<div style="color:#666;">還沒有紀錄</div>';
}

// ── 今日：每日看板（每個人每天做了什麼；實際為主，計畫加分；不排名、不標紅）──
function _itemCard(i) {
    return `<div style="padding:6px 8px;border:1px solid #333;border-radius:6px;margin:4px 0;background:${_isPlan(i) ? '#1a2233' : '#1f1f1f'};">
        <div style="color:#ddd;font-size:12.5px;">${_typeTag(i)}${_projLink(i.project_name, i.project_id)}</div>
        ${i.task_note ? `<div style="color:#999;font-size:11.5px;">${esc(i.task_note)}</div>` : ''}
        ${i.remark ? `<div style="color:#777;font-size:11px;">備註：${esc(i.remark)}</div>` : ''}
        <div style="font-size:11.5px;margin-top:2px;">${_hoursLabel(i)}${_srcTag(i)}</div>
    </div>`;
}

function _renderToday(d) {
    const toggle = `<span style="flex:1;"></span>
        <button class="ts-btn ${_boardDays === 1 ? '' : 'ghost'}" data-ts-action="board-days" data-days="1">日</button>
        <button class="ts-btn ${_boardDays === 7 ? '' : 'ghost'}" data-ts-action="board-days" data-days="7">週</button>`;
    let body;
    if (_boardDays === 1) {
        const day = d.items[0] || { people: [] };
        body = day.people.length ? `<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;">
            ${day.people.map(p => `<div class="ts-card" style="flex:1 1 220px;max-width:340px;margin:0;">
                <h3>${esc(p.name)} <span style="color:#777;font-weight:400;font-size:12px;">${p.hours} h${p.planned ? `（計畫 ${p.planned}）` : ''}</span></h3>
                ${p.items.map(_itemCard).join('')}
            </div>`).join('')}
        </div>` : `<div class="ts-card" style="color:#777;">這一天還沒有人填。填了就會出現在這裡（當天、晚上、隔天補都行）。</div>`;
    } else {
        const people = [...new Set(d.items.flatMap(x => x.people.map(p => p.name)))].sort();
        body = people.length ? `<div class="ts-card" style="overflow-x:auto;"><table>
            <thead><tr><th>人員</th>${d.items.map(x => `<th>${esc(_dayLabel(x.date))}</th>`).join('')}</tr></thead>
            <tbody>${people.map(n => `<tr><td style="color:#eee;font-weight:600;white-space:nowrap;">${esc(n)}</td>
                ${d.items.map(x => {
                    const p = x.people.find(q => q.name === n);
                    return `<td style="vertical-align:top;min-width:150px;">${p ? p.items.map(i =>
                        `<div style="font-size:11.5px;color:#ccc;">${esc(i.project_name || '(空白)')} ${_hoursLabel(i)}</div>`).join('') : '<span style="color:#444;">—</span>'}</td>`;
                }).join('')}</tr>`).join('')}</tbody>
        </table></div>` : `<div class="ts-card" style="color:#777;">這一週還沒有人填。</div>`;
    }
    return `${_head('每日看板：大家每天做了什麼。填了就出現；有先排計畫的人會先看到計畫，做完變實際。')}
        ${_dayNav(toggle)}${body}`;
}

// ── 我的一天：登入者自己記（實際或計畫）；時數快捷鈕；複製昨天；改／刪 ──
async function _projectOptions() {
    if (_projOpts) return _projOpts;
    try { _projOpts = (await tfetch('/api/v1/timesheets/project_options')).projects || []; }
    catch (_) { _projOpts = []; }
    return _projOpts;
}
// ── 專案格的下拉：分「進行中（預設展開）／已結案（收著，打字會搜到、也可點開）」（owner 2026-09-03）──
// 原生 datalist 分不了組，改成自己的浮層。顯示「年份 客戶 案名」（同零用金）；選了把字填回格子並觸發 input／change，
// 存檔還是 _projectFromInput 對回 id。鍵盤：浮層開著時 ↓↑ 在浮層裡走、Enter 選、Esc／Tab 關；關著時 ↓↑ 才是列的上下移動。
let _pop = null;   // {el, input, idx, showClosed, flat}
function _projPopClose() { if (_pop) { _pop.el.remove(); _pop = null; } }
function _projPopSplit(q, showClosed) {
    const needle = (q || '').trim().toLowerCase();
    const hit = p => !needle || (p.label || p.name || '').toLowerCase().includes(needle);
    const opts = _projOpts || [];
    return { active: opts.filter(p => !p.closed && hit(p)), closed: opts.filter(p => p.closed && hit(p)), show: showClosed || !!needle };
}
function _projPopRender() {
    const { el, input } = _pop;
    const { active, closed, show } = _projPopSplit(input.value, _pop.showClosed);
    const flat = [];
    const item = (p) => { flat.push(p); const i = flat.length - 1; return `<div class="ts-pp-item${i === _pop.idx ? ' on' : ''}" data-i="${i}">${esc(p.label || p.name)}</div>`; };
    let html = `<div class="ts-pp-h">進行中（${active.length}）</div>` + (active.length ? active.map(item).join('') : '<div class="ts-pp-empty">沒有符合的</div>');
    html += `<div class="ts-pp-h ts-pp-toggle" data-toggle="1">已結案（${closed.length}）${show ? '' : '　點一下展開'}</div>`;
    if (show) html += closed.length ? closed.map(item).join('') : '<div class="ts-pp-empty">沒有符合的</div>';
    _pop.flat = flat;
    el.innerHTML = html;
    const r = input.getBoundingClientRect();
    el.style.left = `${r.left + window.scrollX}px`; el.style.top = `${r.bottom + window.scrollY}px`;
    el.style.minWidth = `${Math.max(r.width, 320)}px`;
    el.querySelector('.ts-pp-item.on')?.scrollIntoView({ block: 'nearest' });
}
async function _projPopOpen(input) {
    await _projectOptions();
    if (document.activeElement !== input) return;      // 抓完選項時人已經離開那一格
    if (!_pop || _pop.input !== input) {
        _projPopClose();
        const el = document.createElement('div');
        el.className = 'ts-proj-pop';
        el.addEventListener('pointerdown', (ev) => {        // pointerdown：blur 會先於 click 把浮層收掉
            ev.preventDefault();
            const it = ev.target.closest('.ts-pp-item');
            if (it) { _projPopPick(_pop.flat[Number(it.dataset.i)]); return; }
            if (ev.target.closest('[data-toggle]')) { _pop.showClosed = !_pop.showClosed; _pop.idx = -1; _projPopRender(); }
        });
        document.body.appendChild(el);
        _pop = { el, input, idx: -1, showClosed: false, flat: [] };
    }
    _projPopRender();
}
function _projPopPick(p) {
    if (!p || !_pop) return;
    const input = _pop.input;
    input.value = p.label || p.name;
    _projPopClose();
    const ev = new Event('input', { bubbles: true });
    ev._fromPick = true;                                  // 讓下面的 input 監聽別把浮層又打開
    input.dispatchEvent(ev);
    input.dispatchEvent(new Event('change', { bubbles: true }));
}
function _projPopKeydown(ev) {   // capture 階段：浮層開著時先於 _sheetKeydown 吃掉 ↓↑ Enter Esc
    if (!_pop || ev.target !== _pop.input) return;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault(); ev.stopPropagation();
        const n = _pop.flat.length; if (!n) return;
        _pop.idx = (_pop.idx + (ev.key === 'ArrowDown' ? 1 : -1) + n) % n;
        _projPopRender();
    } else if (ev.key === 'Enter') {
        if (_pop.idx >= 0) { ev.preventDefault(); ev.stopPropagation(); _projPopPick(_pop.flat[_pop.idx]); }
        else _projPopClose();
    } else if (ev.key === 'Escape' || ev.key === 'Tab') {
        _projPopClose();
    }
}
function _bindProjectPop(root) {
    root.addEventListener('keydown', _projPopKeydown, true);
    root.addEventListener('focusin', (ev) => { const inp = ev.target.closest?.('input[data-proj-pick]'); if (inp && !inp.readOnly) _projPopOpen(inp); });
    root.addEventListener('input', (ev) => {
        const inp = ev.target.closest?.('input[data-proj-pick]'); if (!inp || ev._fromPick) return;
        if (_pop && _pop.input === inp) { _pop.idx = -1; _projPopRender(); } else _projPopOpen(inp);
    });
    root.addEventListener('focusout', (ev) => {
        if (!ev.target.closest?.('input[data-proj-pick]')) return;
        setTimeout(() => { if (_pop && document.activeElement !== _pop.input) _projPopClose(); }, 120);
    });
}
function _typeSelect(cur, attr) {
    return `<select ${attr} data-no-search><option value="">分類</option>${_workTypes.map(t =>
        `<option value="${esc(t)}"${t === cur ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
}
/** 一個工作項的五格輸入（專案／分類／內容／計畫／實際）；總表改列用。 */
function _rowCells(v = {}) {
    return `<td><input data-proj-pick autocomplete="off" data-f="project" value="${esc(v.project || '')}" placeholder="專案（可打字）" style="width:100%;"></td>
        <td>${_typeSelect(v.work_type || '', 'data-f="type"')}</td>
        <td><input type="text" data-f="note" value="${esc(v.note || '')}" placeholder="做了什麼" style="width:100%;"></td>
        <td><input type="text" data-f="remark" value="${esc(v.remark || '')}" placeholder="備註" style="width:100%;"></td>
        <td><input type="number" data-f="planned" min="0" step="0.25" value="${v.planned ?? ''}" placeholder="計畫" style="width:64px;"></td>
        <td><input type="number" data-f="hours" min="0" step="any" value="${v.hours ?? ''}" placeholder="實際" style="width:64px;"></td>`;
}
/** 起訖時間 → 實際小時（兩位小數；訖比起早＝跨午夜）；填進同一列的「實際」欄。 */
function _applyTimeRange(tr) {
    const t0 = tr.querySelector('[data-f="t0"]')?.value, t1 = tr.querySelector('[data-f="t1"]')?.value;
    if (!t0 || !t1) return;
    const m = s => { const [h, mm] = s.split(':').map(Number); return h * 60 + mm; };
    let mins = m(t1) - m(t0);
    if (mins < 0) mins += 24 * 60;
    tr.querySelector('[data-f="hours"]').value = Math.round(mins / 60 * 100) / 100;
}
// ── 我的一天的新增區：像 Google Sheet 的格子（owner 2026-09-03）──
// 一列＝一個工作項；Enter／↓／↑ 在同一欄上下走，走到底自動多一列；在最後一列打字也會自動多一列。
const _SHEET_COLS = [['project', '專案'], ['type', '分類'], ['note', '做了什麼'], ['remark', '備註'], ['t0', '起'], ['t1', '訖'], ['hours', '實際 h'], ['planned', '計畫 h'], ['state', '']];
function _newRowHtml(v = {}, o = {}) {
    // Sheet 列：input 用 readonly（文字還能選取、複製貼到下一列）；select 沒有 readonly 只能 disabled
    const ro = o.readonly ? ' readonly' : '';
    const rosel = o.readonly ? ' disabled' : '';
    const t = 'type="text" inputmode="numeric" maxlength="5" placeholder="09:00" autocomplete="off"';
    return `<tr class="ts-mine-row"${o.id ? ` data-id="${esc(o.id)}"` : ''}${o.readonly ? ' data-readonly="1"' : ''}>
        <td class="ts-sheet-num"></td>
        <td><input data-proj-pick autocomplete="off" data-f="project" value="${esc(v.project || '')}"${ro}></td>
        <td>${_typeSelect(v.work_type || '', `data-f="type"${rosel}`)}</td>
        <td><input type="text" data-f="note" value="${esc(v.note || '')}"${ro}></td>
        <td><input type="text" data-f="remark" value="${esc(v.remark || '')}"${ro}></td>
        <td><input ${t} data-f="t0" value="${esc(v.t0 || '')}"${ro}></td>
        <td><input ${t} data-f="t1" value="${esc(v.t1 || '')}"${ro}></td>
        <td><input type="number" data-f="hours" min="0" step="any" value="${v.hours ?? ''}"${ro}></td>
        <td><input type="number" data-f="planned" min="0" step="0.25" value="${v.planned ?? ''}"${ro}></td>
        <td class="ts-sheet-state" data-f="state">${o.readonly ? 'Sheet' : (o.id ? '已存' : '')}</td>
        <td class="ts-sheet-del">${o.readonly ? '' : '<button data-ts-action="row-remove" title="刪這一列">×</button>'}</td>
    </tr>`;
}
function _sheetTableHtml(rowsHtml, id = 'ts-mine-add') {
    return `<table ${id ? `id="${id}"` : ''} class="ts-sheet">
        <thead><tr><th class="ts-sheet-num"></th>${_SHEET_COLS.map(([, l]) => `<th>${l}</th>`).join('')}<th class="ts-sheet-del"></th></tr></thead>
        <tbody>${rowsHtml}</tbody>
    </table>`;
}
/** 鍵盤：↓ 到下一列同欄（沒有就長一列）、↑ 上一列同欄；Enter **不跳列**（owner 2026-09-03：留在原格，
 *  專案格的 Enter 就是選取下拉建議）；分類（select）的上下鍵留給它自己。 */
function _sheetKeydown(ev) {
    const inp = ev.target.closest('#ts-mine-add [data-f]');
    if (!inp) return;
    const down = ev.key === 'ArrowDown', up = ev.key === 'ArrowUp';
    if (!down && !up) return;
    if (inp.tagName === 'SELECT') return;
    const tr = inp.closest('tr');
    let target = up ? tr.previousElementSibling : tr.nextElementSibling;
    if (!target && down) { tr.insertAdjacentHTML('afterend', _newRowHtml()); target = tr.nextElementSibling; }
    if (!target) return;
    ev.preventDefault();
    const next = target.querySelector(`[data-f="${inp.dataset.f}"]`);
    if (next) { next.focus(); if (next.select) next.select(); }
}
/** 在最後一列打了東西 → 自動再長一列（Sheet 的感覺：永遠有空列可以往下填）。 */
function _sheetGrow(ev) {
    const inp = ev.target.closest('#ts-mine-add [data-f]');
    if (!inp) return;
    const tr = inp.closest('tr');
    if (!tr.nextElementSibling && inp.value) tr.insertAdjacentHTML('afterend', _newRowHtml());
}
function _mineRowHtml(i) {
    return _newRowHtml({ project: i.project_name, work_type: i.work_type, note: i.task_note, remark: i.remark, planned: i.planned_hours, hours: i.hours || '' },
                       { id: i.id, readonly: !i.editable });
}
function _mineChips(d) {
    return `<span class="ts-chip"><b>${d.actual_total}</b>實際 h</span>${d.planned_total ? `<span class="ts-chip"><b>${d.planned_total}</b>計畫 h</span>` : ''}`;
}
function _renderMine(d, err) {
    if (!d) {
        return `${_head('我的一天：今天做了什麼，直接在格子裡填，填了就存。')}
            <div class="ts-card" style="color:#fca5a5;">${esc(err || '載入失敗')}
                <div class="ts-note">要用「我的一天」，帳號要在「使用者管理」綁定人員檔案。</div></div>`;
    }
    const items = d.items || [];
    return `${_head('我的一天：今天做了什麼，直接在格子裡填，填了就存（不審核，隨時可改）。')}
        ${_dayNav(`<span id="ts-mine-chips">${_mineChips(d)}</span><span style="color:#777;font-size:12px;">${esc(d.staff_name)}</span>`)}
        <div class="ts-card" style="border-color:#3b82f6;">
            <h3>${esc(_dayLabel(d.date))} 的工作項</h3>
            ${_sheetTableHtml(items.map(_mineRowHtml).join('') + _newRowHtml().repeat(5))}
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px;">
                <button class="ts-btn ghost" data-ts-action="row-add">＋ 五列</button>
                <button class="ts-btn ghost" data-ts-action="copy-yesterday" ${(d.yesterday || []).length ? '' : 'disabled'}>複製昨天（${(d.yesterday || []).length} 列）</button>
                <span id="ts-mine-result" style="font-size:12px;color:#888;"></span>
            </div>
            <div class="ts-note">專案＋時數（實際或計畫）填齊那一列就自動存成當天的工項，之後改任何一格也自動存。
                起訖用 24 小時制，打「9」「930」「1730」都可以，會自己算出實際 h。↓ 往下一列、↑ 往上，走到底自動多一列（Enter 不跳列）。
                Sheet 拉進來的列會標「Sheet」、不能改。</div>
        </div>`;
}
/** 專案格打的字 → {project_id, project_name}：對得到下拉的「年份 客戶 案名」或案名就帶 id；對不到就照打的字送（後端再對映）。 */
function _projectFromInput(text) {
    const t = (text || '').trim();
    const hit = (_projOpts || []).find(p => p.id && (p.label === t || p.name === t));
    return hit ? { project_id: hit.id, project_name: hit.name } : { project_id: null, project_name: t };
}
/** 一列輸入 → 送給後端的 body（我的一天新增／改列、總表改列同一形狀；沒日期欄就用當天）。 */
function _rowBody(tr) {
    const v = f => tr.querySelector(`[data-f="${f}"]`)?.value ?? '';
    return {
        work_date: v('date') || _day, ..._projectFromInput(v('project')), work_type: v('type') || null,
        task_note: v('note'), remark: v('remark'), planned_hours: v('planned') ? parseFloat(v('planned')) : null,
        hours: v('hours') ? parseFloat(v('hours')) : null,
    };
}
/** 「9」「930」「0930」「9:30」「17.30」→ "09:30"；看不懂 → ""。 */
function _normTime(v) {
    const m = String(v || '').trim().replace(/[.．：]/g, ':').match(/^(\d{1,2})(?::?(\d{2}))?$/);
    if (!m) return '';
    const h = Number(m[1]), mm = Number(m[2] || 0);
    if (h > 23 || mm > 59) return '';
    return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}
const _saveTimers = new WeakMap();
function _mineScheduleSave(tr) {
    clearTimeout(_saveTimers.get(tr));
    _saveTimers.set(tr, setTimeout(() => _mineSaveRow(tr), 600));
}
/** 一列的存檔：沒 id＝填齊（專案＋實際或計畫）才 POST，回來把 id 掛上；有 id＝PUT。同列上一筆還在飛就排在後面。 */
async function _mineSaveRow(tr) {
    if (!tr.isConnected || tr.dataset.readonly) return;
    if (tr._saving) { tr._again = true; return; }
    const st = tr.querySelector('[data-f="state"]');
    const body = _rowBody(tr);
    const complete = body.project_name && ((body.hours || 0) > 0 || (body.planned_hours || 0) > 0);
    if (!complete) { st.textContent = body.project_name || body.task_note ? '再填時數' : ''; st.style.color = '#777'; return; }
    tr._saving = true;
    st.textContent = '儲存中…'; st.style.color = '#93c5fd';
    try {
        if (tr.dataset.id) {
            await tfetch('/api/v1/timesheets/mine/' + tr.dataset.id, { method: 'PUT', body });
        } else {
            const r = await tfetch('/api/v1/timesheets/mine/rows', { method: 'POST', body: { rows: [body] } });
            tr.dataset.id = (r.ids || [])[0] || '';
            if ((r.unmatched_projects || []).length) {
                const el = document.getElementById('ts-mine-result');
                if (el) el.textContent = `「${r.unmatched_projects.join('、')}」對不到案（已存下來，管理員會指定）`;
            }
        }
        st.textContent = '已存'; st.style.color = '#6ee7b7';
        _mineTotals();
    } catch (e) {
        st.textContent = '沒存：' + (e.message || e); st.style.color = '#fca5a5';
    } finally {
        tr._saving = false;
        if (tr._again) { tr._again = false; _mineScheduleSave(tr); }
    }
}
/** 存完只更新上面的合計 chip（不重畫表：正在打字的格子不能被洗掉）。 */
async function _mineTotals() {
    try {
        const d = await tfetch('/api/v1/timesheets/mine?date=' + _day);
        _mineCache = d;
        const el = document.getElementById('ts-mine-chips');
        if (el) el.innerHTML = _mineChips(d);
    } catch (_) { /* 合計晚點再更新就好 */ }
}
async function _mineRemove(tr) {
    if (tr.dataset.id) {
        if (!confirm('刪掉這一列？')) return;
        try { await tfetch('/api/v1/timesheets/mine/' + tr.dataset.id, { method: 'DELETE' }); }
        catch (e) { return alert('刪除失敗：' + (e.message || e)); }
    }
    const tb = tr.parentElement;
    tr.remove();
    if (tb && !tb.children.length) tb.insertAdjacentHTML('beforeend', _newRowHtml());
    _mineTotals();
}

// ── 總表：這個月每一列；管理員逐列改細節與備註（像發票總表）──
function _ledgerRows() {
    const f = _ledgerFilter, q = f.q.trim().toLowerCase();
    return ((_ledgerCache && _ledgerCache.items) || []).filter(i =>
        (!f.staff || i.staff_name === f.staff)
        && (!f.project || (f.project === '__blank__' ? !i.project_name : i.project_name === f.project))
        && (!f.source || (f.source === 'manual') === (i.source === 'manual'))
        && (!q || [i.project_name, i.task_note, i.remark, i.note, i.staff_name].some(s => (s || '').toLowerCase().includes(q))));
}
const _srcLabel = i => (i.source === 'manual' ? '手填' : 'Sheet');
function _ledgerTbodyHtml() {
    const can = _ledgerCache && _ledgerCache.editable;
    return _ledgerSorter.sorted(_ledgerRows()).map(i => `<tr data-ledger-id="${esc(i.id)}">
        ${can ? `<td style="width:26px;text-align:center;"><input type="checkbox" data-pick="${esc(i.id)}" ${_ledgerSel.has(i.id) ? 'checked' : ''} style="accent-color:#3b82f6;"></td>` : ''}
        <td style="white-space:nowrap;">${esc(i.date)}${i.edited ? ' <span class="ts-badge" title="總表改過：Sheet 同格之後再變會記成衝突，不自動蓋">改過</span>' : ''}</td>
        <td>${esc(i.staff_name)}</td>
        <td>${_projLink(i.project_name, i.project_id)}${i.project_id ? '' : ' <span class="ts-badge">未對映</span>'}</td>
        <td style="color:#9ca3af;">${esc(i.work_type || '')}</td>
        <td style="color:#bbb;">${esc(i.task_note || '')}</td>
        <td style="color:#9ca3af;">${esc(i.remark || '')}</td>
        <td class="num" style="color:#93c5fd;">${i.planned_hours ?? ''}</td>
        <td class="num">${_isPlan(i) ? '<span style="color:#666;">計畫</span>' : `<b>${i.hours}</b>`}</td>
        <td style="color:#777;">${_srcLabel(i)}</td>
        <td style="color:#fbbf24;">${esc(i.note || '')}</td>
        <td style="white-space:nowrap;">${can ? `<button class="ts-btn ghost" data-ts-action="ledger-edit" data-id="${esc(i.id)}" style="padding:2px 8px;">改</button>
            <button class="ts-btn ghost" data-ts-action="ledger-del" data-id="${esc(i.id)}" style="padding:2px 8px;">刪</button>` : ''}</td>
    </tr>`).join('') || '<tr><td colspan="12" style="color:#666;text-align:center;">沒有符合的列</td></tr>';
}
/** 篩選／排序／改列後只重繪表身與計數（不重繪整頁 → 搜尋框不失焦）。 */
function _ledgerRedraw() {
    _redrawTbody('ts-ledger-table', _ledgerTbodyHtml, _ledgerSorter);
    const rows = _ledgerRows();
    const c = document.getElementById('ts-ledger-count');
    if (c) c.innerHTML = `<span class="ts-chip"><b>${rows.length}</b>列</span><span class="ts-chip"><b>${Math.round(rows.reduce((a, i) => a + (i.hours || 0), 0) * 10) / 10}</b>實際 h</span>`;
    const bar = document.getElementById('ts-batch-bar'), n = document.getElementById('ts-batch-n');
    if (bar) bar.style.display = _ledgerSel.size ? 'flex' : 'none';
    if (n) n.textContent = _ledgerSel.size;
}
function _ledgerEditHtml(i) {
    const inp = 'style="background:#1a1a1a;border:1px solid #333;color:#ddd;border-radius:4px;padding:3px 6px;"';
    return `<tr data-ledger-edit="${esc(i.id)}">
        <td><input type="date" data-f="date" value="${esc(i.date)}" ${inp}></td>
        <td>${esc(i.staff_name)}</td>
        ${_rowCells({ project: i.project_name, work_type: i.work_type, note: i.task_note, remark: i.remark, planned: i.planned_hours, hours: i.hours || '' })}
        <td style="color:#777;">${_srcLabel(i)}</td>
        <td><input type="text" data-f="admnote" value="${esc(i.note)}" placeholder="管理員備註" style="width:100%;"></td>
        <td style="white-space:nowrap;"><button class="ts-btn" data-ts-action="ledger-save" data-id="${esc(i.id)}" style="padding:2px 8px;">存</button>
            <button class="ts-btn ghost" data-ts-action="ledger-cancel" style="padding:2px 8px;">取消</button>
            <div data-err style="color:#fca5a5;font-size:11px;"></div></td>
    </tr>`;
}
/** 衝突待決：總表改過的列 vs Sheet 新版並排（不同的格標色），三個決定鈕。 */
function _conflictsHtml() {
    if (!_conflictsCache.length) return '';
    const cols = [['date', '日期'], ['staff_name', '人員'], ['project_name', '專案'], ['task_note', '內容'], ['hours', '時數']];
    const row = (label, v, other) => `<tr><td style="color:#888;white-space:nowrap;">${label}</td>${cols.map(([k]) => {
        const diff = String(v[k] ?? '') !== String(other[k] ?? '');
        return `<td style="${diff ? 'color:#fbbf24;font-weight:600;' : ''}">${esc(String(v[k] ?? ''))}</td>`;
    }).join('')}</tr>`;
    return `<div class="ts-card" style="border-color:#f59e0b;">
        <h3>Sheet 與總表衝突（${_conflictsCache.length}）—— 這幾列你在總表改過，Sheet 那格之後又變了；沒有自動覆蓋，選一個</h3>
        ${_conflictsCache.map(c => `<div style="margin:8px 0 12px;padding:8px;border:1px solid #333;border-radius:6px;">
            <table style="margin-bottom:6px;"><thead><tr><th></th>${cols.map(([, l]) => `<th>${l}</th>`).join('')}</tr></thead>
                <tbody>${row('總表', c.mine, c.sheet)}${row('Sheet 新版', c.sheet, c.mine)}</tbody></table>
            ${c.mine.note ? `<div class="ts-note" style="margin:0 0 6px;">總表備註：${esc(c.mine.note)}</div>` : ''}
            <button class="ts-btn" data-ts-action="conflict" data-id="${esc(c.id)}" data-choice="keep_mine">用總表的</button>
            <button class="ts-btn ghost" data-ts-action="conflict" data-id="${esc(c.id)}" data-choice="use_sheet">用 Sheet 的</button>
            <button class="ts-btn ghost" data-ts-action="conflict" data-id="${esc(c.id)}" data-choice="keep_both">兩列都留</button>
        </div>`).join('')}
        <div class="ts-note">用總表的＝Sheet 這個版本以後不再進來；用 Sheet 的＝把 Sheet 內容套回這列（備註／分類／計畫保留）；兩列都留＝Sheet 版本另插一列。</div>
    </div>`;
}
function _renderLedger(d) {
    const items = d.items || [];
    const sel = 'style="background:#1a1a1a;border:1px solid #333;color:#ddd;border-radius:4px;padding:5px 8px;"';
    const opts = (key, list, label) => `<select id="ts-lf-${key}" ${sel}><option value="">${label}</option>${list.map(v =>
        `<option value="${esc(v)}"${_ledgerFilter[key] === v ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
    const uniq = k => [...new Set(items.map(i => i[k]).filter(Boolean))].sort();
    return `${_head(d.editable ? '總表：這個月每一列（每人每案每項）。可以逐列改日期／專案／分類／內容／時數，加管理員備註（員工端看不到）。'
                                : '總表：這個月每一列（每人每案每項）。')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
            <input type="month" id="ts-month" value="${esc(_month)}" ${sel} title="起月">
            <span style="color:#666;">～</span>
            <input type="month" id="ts-month-to" value="${esc(_monthTo)}" ${sel} title="迄月（空＝只看起月；最多 12 個月）">
            ${opts('staff', uniq('staff_name'), '全部人員')}
            <select id="ts-lf-project" ${sel}><option value="">全部專案</option>
                <option value="__blank__"${_ledgerFilter.project === '__blank__' ? ' selected' : ''}>(空白專案)</option>
                ${uniq('project_name').map(v => `<option value="${esc(v)}"${_ledgerFilter.project === v ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select>
            <select id="ts-lf-source" ${sel}><option value="">全部來源</option>
                <option value="sheet"${_ledgerFilter.source === 'sheet' ? ' selected' : ''}>Sheet</option>
                <option value="manual"${_ledgerFilter.source === 'manual' ? ' selected' : ''}>手填</option></select>
            <input type="search" id="ts-lf-q" value="${esc(_ledgerFilter.q)}" placeholder="搜專案／內容／備註" ${sel}>
            <span id="ts-ledger-count"></span>
            <button class="ts-btn ghost" data-ts-action="export-month">匯出 CSV</button>
        </div>
        ${_conflictsHtml()}
        ${d.editable ? `<div id="ts-batch-bar" style="display:none;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;padding:8px 10px;border:1px solid #3b82f6;border-radius:6px;background:#1a2233;">
            <span style="color:#ddd;">已勾 <b id="ts-batch-n">0</b> 列 → 一次改：</span>
            <input data-proj-pick autocomplete="off" id="ts-batch-project" placeholder="專案（留空不改）" ${sel}>
            ${_typeSelect('', `id="ts-batch-type" ${sel}`).replace('>分類<', '>分類（不改）<')}
            <input type="text" id="ts-batch-remark" placeholder="備註（留空不改）" ${sel}>
            <input type="text" id="ts-batch-note" placeholder="管理員備註（留空不改）" ${sel}>
            <button class="ts-btn" data-ts-action="batch-apply">套用</button>
            <button class="ts-btn ghost" data-ts-action="batch-clear">取消勾選</button>
        </div>` : ''}
        <div class="ts-card" style="overflow-x:auto;">
            <table id="ts-ledger-table">
                <thead><tr>${d.editable ? '<th style="width:26px;"><input type="checkbox" data-pick-all title="勾選目前篩出來的全部" style="accent-color:#3b82f6;"></th>' : ''}${sortableTh('date', '日期')}${sortableTh('staff', '人員')}${sortableTh('project', '專案')}${sortableTh('type', '分類')}${sortableTh('task', '內容')}${sortableTh('remark', '備註')}${sortableTh('planned', '計畫', 'class="num"')}${sortableTh('hours', '實際', 'class="num"')}${sortableTh('source', '來源')}${sortableTh('note', '管理員備註')}<th></th></tr></thead>
                <tbody></tbody>   <!-- _bind → _ledgerRedraw 填（不在這裡建一次又重建一次） -->
            </table>
        </div>`;
}

// ── 專案：burn 表（消耗率高在前）＋ 最近同步列 ──
function _pctStyle(pct) {
    if (pct == null) return 'background:#2c2c2c;color:#777;';
    if (pct >= 100) return 'background:#7f1d1d;color:#fca5a5;';
    if (pct >= 90) return 'background:#78350f;color:#fbbf24;';
    if (pct >= 60) return 'background:#1e3a5f;color:#93c5fd;';
    return 'background:#064e3b;color:#6ee7b7;';
}

/** burn 表的案型下拉：字彙＝/summary 回的 project_types（settings ∪ 毛利表 ∪ 在用的）。改了就 PUT 專案，建議預算跟著重算。 */
function _burnTypeSelect(p) {
    // 平時只畫文字，點到才變成下拉（317 列 × 11 個 option 畫一次就是幾千個節點）
    const cur = p.project_type || '';
    return `<span class="ts-link" data-ts-action="type-edit" data-pid="${esc(p.project_id)}" data-cur="${esc(cur)}"
                style="color:${cur ? '#ccc' : '#f59e0b'};" title="點一下改案型（預期毛利／建議預算照這個算）">${cur ? esc(cur) : '— 案型 —'} ▾</span>`;
}
function _burnTbodyHtml() {
    const rows = _burnSorter.sorted(((_summaryCache && _summaryCache.projects) || [])
        .filter(p => _projHit(p.project_name) || _projHit(p.status))).map(p => `
        <tr>
            <td><span class="ts-link" data-ts-action="open-project" data-name="${esc(p.project_name || '')}" data-pid="${esc(p.project_id)}">${esc(p.project_name || p.project_id)}</span>
                ${p.stale ? '<span class="ts-badge warn" title="進行中但 7 天沒工時">停滯</span>' : ''}</td>
            <td style="color:#888;">${esc(p.status || '')}</td>
            <td>${_burnTypeSelect(p)}</td>
            <td class="num">${p.hours_used}</td>
            <td class="num">${p.budget_hours ?? (p.suggested_hours != null
                ? `<span class="ts-link" data-ts-action="budget" data-pid="${esc(p.project_id)}" data-cur="${p.suggested_hours}" title="依私帳設定的預期毛利（${esc(p.project_type || '')}）與日成本算的建議，點一下就套用">建議 ${p.suggested_hours}</span>`
                : '<span style="color:#666;">未設</span>')}</td>
            <td class="num">${p.remaining ?? '—'}</td>
            <td class="num"><span class="ts-pct" style="${_pctStyle(p.pct)}">${p.pct != null ? p.pct + '%' : '—'}</span></td>
            <td class="num" style="color:#777;">${p.rows}</td>
            <td style="color:#777;">${esc(p.last_entry || '')}</td>
        </tr>`).join('');
    return rows || `<tr><td colspan="9" style="color:#666;text-align:center;">${_projQ ? '沒有符合的' : '尚無已對映專案'}</td></tr>`;
}

/** 專案頁的未對映表身（搜尋列會重畫它；內部桶不列）。 */
function _unmatchedProjRowsHtml() {
    const rows = ((_summaryCache && _summaryCache.unmatched) || []).filter(u => u.reason !== 'bucket' && _projHit(u.project_name));
    return rows.map(u => `
        <tr><td><span class="ts-link" data-ts-action="open-project" data-name="${esc(u.project_name)}">${esc(u.project_name)}</span>
                <span class="ts-badge">未對映</span></td>
            <td class="num">${u.hours_used}</td>
            <td><button class="ts-btn ghost" data-ts-action="map" data-name="${esc(u.project_name)}" style="padding:2px 8px;">指定專案</button></td></tr>`).join('')
        || '<tr><td colspan="3" style="color:#666;text-align:center;">沒有符合的</td></tr>';
}
function _renderProjects(s) {
    const totalHours = s.projects.reduce((a, p) => a + (p.hours_used || 0), 0)
        + s.unmatched.reduce((a, u) => a + (u.hours_used || 0), 0);
    const stale = s.projects.filter(p => p.stale).length;
    const unmatched = s.unmatched.filter(u => u.reason !== 'bucket');
    return `${_head('專案：每案投入時數對預算。點案名進檔案頁（時間軸、分類組成、類似專案並排）。')}
        <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <input type="search" id="ts-proj-q" value="${esc(_projQ)}" placeholder="搜案名／狀態（兩張表一起篩）"
                   style="background:#1a1a1a;border:1px solid #333;color:#ddd;border-radius:4px;padding:6px 10px;min-width:260px;">
            <span class="ts-chip"><b>${s.total_rows}</b>總列數</span>
            <span class="ts-chip"><b>${s.projects.length}</b>已對映專案</span>
            <span class="ts-chip"><b>${unmatched.length}</b>未對映</span>
            <span class="ts-chip"><b>${stale}</b>停滯</span>
            <span class="ts-chip"><b>${Math.round(totalHours)}</b>總時數</span>
            <button class="ts-btn ghost" data-ts-action="refresh" style="vertical-align:top;">↻ 重新整理</button>
            <button class="ts-btn ghost" data-ts-action="recent" style="vertical-align:top;">最近同步列</button>
            <button class="ts-btn ghost" data-ts-action="export-month" style="vertical-align:top;">匯出本月 CSV</button>
            ${(() => { const n = s.projects.filter(p => p.budget_hours == null && p.suggested_hours != null).length;
                return n ? `<button class="ts-btn ghost" data-ts-action="budget-suggest" data-n="${n}" style="vertical-align:top;" title="工時預算＝合約未稅 ×（1−預期毛利）÷ 日成本 × 每日工時；只填沒設的案，已設的不動">套用建議預算（${n} 案沒設）</button>` : ''; })()}
        </div>
        ${unmatched.length ? `<div class="ts-card"><h3>未對映（${unmatched.length}）—— 對到案之後才有預算與 burn</h3>
            <table><thead><tr><th>Sheet 案名</th><th class="num">時數</th><th></th></tr></thead><tbody id="ts-unmatched-proj-body">${_unmatchedProjRowsHtml()}</tbody></table></div>` : ''}
        <div class="ts-card">
            <h3>📊 專案 Burn（消耗率高在前）</h3>
            <table id="ts-burn-table">
                <thead><tr>
                    ${sortableTh('project', '專案')}${sortableTh('status', '狀態')}${sortableTh('type', '案型')}${sortableTh('used', '已投入(h)', 'class="num"')}${sortableTh('budget', '預算(h)', 'class="num"')}
                    ${sortableTh('remaining', '剩餘(h)', 'class="num"')}${sortableTh('pct', '消耗率', 'class="num"')}${sortableTh('rows', '列數', 'class="num"')}${sortableTh('last', '最後填報')}
                </tr></thead>
                <tbody>${_burnTbodyHtml()}</tbody>
            </table>
        </div>
        <div id="ts-recent-slot"></div>`;
}

// ── 專案彈窗：在看板／人員頁／總表點案名，就地看這個案的執行狀態（不離開目前頁）──
/** 改案型（PUT CRM 專案，部分更新）→ 建議預算跟著重算 → 重畫 burn 表。 */
async function _setProjectType(pid, type) {
    try {
        await tfetch('/api/v1/crm/projects/' + encodeURIComponent(pid), { method: 'PUT', body: { project_type: type } });
        _summaryCache = null;
        refresh();
    } catch (e) { alert('案型沒存：' + (e.message || e)); }
}
async function _openProjectModal(name, pid) {
    let bg = document.getElementById('ts-proj-modal');
    if (!bg) {
        bg = document.createElement('div');
        bg.id = 'ts-proj-modal'; bg.className = 'ts-modal-bg';
        bg.addEventListener('click', (ev) => { if (ev.target === bg) bg.remove(); });
        _content.appendChild(bg);
    }
    bg.innerHTML = `<div class="ts-modal"><div style="color:#888;padding:30px;text-align:center;">載入中…</div></div>`;
    try {
        const d = await tfetch('/api/v1/timesheets/project?name=' + encodeURIComponent(name || '')
            + (pid ? '&project_id=' + encodeURIComponent(pid) : ''));
        _projectCache = d;
        bg.innerHTML = `<div class="ts-modal">
            <button class="ts-modal-x" data-ts-action="proj-pop-close" title="關閉">×</button>
            ${_renderProject(d, true)}
        </div>`;
    } catch (e) {
        bg.innerHTML = `<div class="ts-modal"><button class="ts-modal-x" data-ts-action="proj-pop-close">×</button>
            <div style="color:#fca5a5;padding:20px;">載入失敗：${esc(e.message || e)}</div></div>`;
    }
}

// ── 專案檔案頁：摘要／分類組成／時間軸／各人各月／預算／報價人日／類似專案 ──
/** [(label, hours, pct?)…] → 共用的水平佔比條（js/shared/svg-charts.hbars）。 */
function _bars(pairs) {
    return hbars(pairs.map(([label, value, pct]) => ({ label, value, pct })),
                 { formatValue: v => `${v} h`, showPct: pairs.some(p => p[2] != null), emptyText: '—' });
}
/** 逐日流水按月分段（每月小計），專案時間軸用：整個案的執行狀態一路看到底。 */
function _dayLogByMonth(days, primary) {
    if (!days.length) return '<div style="color:#666;">還沒有紀錄</div>';
    const groups = [];
    for (const day of days) {
        const m = day.date.slice(0, 7);
        if (!groups.length || groups[groups.length - 1].m !== m) groups.push({ m, days: [], hours: 0 });
        const g = groups[groups.length - 1];
        g.days.push(day);
        g.hours += day.items.reduce((a, i) => a + (i.hours || 0), 0);
    }
    // 一張表：每月一列小計當分隔，底下接那個月的逐日列
    return _dayLogTable(groups.map(g => `<tr class="ts-month"><td colspan="5" style="background:#262626;color:#ddd;padding:6px 8px;">
            <b>${esc(g.m)}</b><span style="color:#888;"> ｜ ${g.days.length} 天 ｜ ${Math.round(g.hours * 10) / 10} h</span></td></tr>${_dayLogRows(g.days, primary)}`).join(''), primary);
}
function _renderProject(d, modal = false) {
    const pct = d.pct == null ? '—' : d.pct + '%';
    const key = d.project_id ? 'id:' + d.project_id : d.project_name;    // 比較清單的鍵：整個案用 id
    const inCompare = _compareNames.includes(key);
    const statusPill = d.status ? `<span class="ts-badge" style="font-size:12px;padding:2px 8px;">${esc(d.status)}</span>` : (d.mapped ? '' : '<span class="ts-badge warn">未對映</span>');
    const sheetNames = (d.sheet_names || []).filter(n => n !== d.project_name);
    return `${modal ? '' : _head('專案檔案：這個案的整個執行狀態 —— 誰在哪天做了什麼、花了多少、跟類似的案比起來如何。')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">
            ${modal ? '' : '<button class="ts-btn ghost" data-ts-action="view" data-view="projects">‹ 專案清單</button>'}
            <b style="color:#eee;font-size:15px;">${esc(d.project_name)}</b>${statusPill}
            <span style="flex:1;"></span>
            <button class="ts-btn ghost" data-ts-action="compare-add" data-name="${esc(key)}">${inCompare ? '已在比較清單' : '加入比較'}</button>
            ${_compareNames.length ? `<button class="ts-btn" data-ts-action="view" data-view="compare">並排比較（${_compareNames.length}）</button>` : ''}
            ${d.mapped ? `<button class="ts-btn ghost" data-ts-action="budget" data-pid="${esc(d.project_id)}" data-cur="${d.budget_hours ?? d.suggested_hours ?? ''}">改預算</button>` : ''}
            <button class="ts-btn ghost" data-ts-action="export-project" data-name="${esc(d.project_name)}" data-pid="${esc(d.project_id || '')}">匯出 CSV</button>
        </div>
        ${sheetNames.length ? `<div class="ts-note" style="margin:0 0 10px;">Sheet 上的案名：${sheetNames.map(esc).join('、')}</div>` : ''}
        <div style="margin-bottom:12px;">
            <span class="ts-chip"><b>${d.total}</b>總時數</span>
            <span class="ts-chip"><b>${d.people}</b>人</span>
            <span class="ts-chip"><b>${d.span_days}</b>天（${esc(d.first || '—')} → ${esc(d.last || '—')}）</span>
            <span class="ts-chip"><b>${d.budget_hours ?? '—'}</b>預算 h　<span class="ts-pct" style="${_pctStyle(d.pct)}">${pct}</span></span>
            ${d.quote_days != null ? `<span class="ts-chip"><b>${d.quote_days}</b>報價人日（≈ ${d.quote_hours} h）</span>` : ''}
            ${d.suggested_hours != null ? `<span class="ts-chip" title="依私帳設定：合約未稅 ×（1−${esc(d.project_type || '')}預期毛利）÷ 日成本 × 每日工時"><b>${d.suggested_hours}</b>建議預算 h</span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;">
            <div class="ts-card" style="margin:0;"><h3>分類組成</h3>${_bars(d.composition)}</div>
            <div class="ts-card" style="margin:0;"><h3>各人</h3>${_bars(d.by_person)}</div>
            <div class="ts-card" style="margin:0;"><h3>各月</h3>${_bars(d.by_month)}</div>
            <div class="ts-card" style="margin:0;"><h3>類似專案（自動推薦，人再挑）</h3>
                ${d.similar.length ? d.similar.map(([n, sc]) => `<div style="display:flex;gap:8px;align-items:center;font-size:12px;margin:4px 0;">
                    <span class="ts-link" data-ts-action="open-project" data-name="${esc(n)}" style="flex:1;">${esc(n)}</span>
                    <span style="color:#666;">${Math.round(sc * 100)}%</span>
                    <button class="ts-btn ghost" data-ts-action="compare-add" data-name="${esc(n)}" style="padding:2px 8px;">${_compareNames.includes(n) ? '已加' : '加入比較'}</button></div>`).join('')
                : '<div style="color:#666;">沒有像的案（同客戶／案名相似／時數量級接近）</div>'}
            </div>
        </div>
        <div class="ts-card" style="margin-top:14px;"><h3>時間軸（整個案，共 ${d.timeline.length} 個有紀錄的日子）</h3>
            ${_dayLogByMonth(d.timeline, 'staff_name')}
        </div>`;
}
function _renderCompare(d) {
    const cols = d.items;
    const row = (label, fn) => `<tr><th style="white-space:nowrap;">${label}</th>${cols.map(c => `<td>${fn(c)}</td>`).join('')}</tr>`;
    const types = [...new Set(cols.flatMap(c => c.composition.map(x => x[0])))];
    return `${_head('類似專案並排：總時數、跨了幾天、幾個人、分類組成、各人 —— 接類似案時的參考。')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
            <button class="ts-btn ghost" data-ts-action="view" data-view="projects">‹ 專案清單</button>
            ${_compareNames.map(n => `<span class="ts-chip" style="padding:4px 10px;">${esc((cols.find(c => c.key === n) || {}).project_name || n)} <span class="ts-link" data-ts-action="compare-remove" data-name="${esc(n)}" style="margin-left:6px;">×</span></span>`).join('')}
        </div>
        <div class="ts-card" style="overflow-x:auto;"><table>
            <thead><tr><th></th>${cols.map(c => `<th><span class="ts-link" data-ts-action="open-project" data-name="${esc(c.key || c.project_name)}">${esc(c.project_name)}</span></th>`).join('')}</tr></thead>
            <tbody>
                ${row('總時數', c => `<b>${c.total}</b> h`)}
                ${row('人數', c => c.people)}
                ${row('起訖', c => `${esc(c.first || '—')} → ${esc(c.last || '—')}`)}
                ${row('跨天數', c => c.span_days)}
                ${types.map(t => row(t, c => { const x = c.composition.find(y => y[0] === t); return x ? `${x[1]} h <span style="color:#666;">${x[2]}%</span>` : '<span style="color:#444;">—</span>'; })).join('')}
                ${row('各人', c => c.by_person.slice(0, 5).map(([n, h]) => `${esc(n)} ${h}`).join('<br>'))}
            </tbody>
        </table></div>`;
}

// ── 人員檔案頁：逐日流水／熱圖／案別／分類／12 個月走勢 ──
function _heatCell(h) {
    if (!h) return '#2a2a2a';
    if (h >= 8) return '#1d4ed8';
    if (h >= 4) return '#2563eb';
    if (h >= 2) return '#3b82f6';
    return '#60a5fa';
}
function _renderPerson(d) {
    const [y, m] = d.month.split('-').map(Number);
    const first = new Date(y, m - 1, 1);
    const daysIn = new Date(y, m, 0).getDate();
    let cells = '';
    for (let i = 0; i < first.getDay(); i++) cells += '<div></div>';
    for (let dd = 1; dd <= daysIn; dd++) {
        const k = `${d.month}-${String(dd).padStart(2, '0')}`;
        const h = d.heat[k] || 0;
        cells += `<div title="${k}：${h} h" style="height:26px;border-radius:4px;background:${_heatCell(h)};display:flex;align-items:center;justify-content:center;font-size:10px;color:${h ? '#fff' : '#555'};">${dd}</div>`;
    }
    return `${_head('人員檔案：這個人每天做了什麼、投入哪些案、近一年的走勢。')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
            <button class="ts-btn ghost" data-ts-action="view" data-view="staff">‹ 人員清單</button>
            <b style="color:#eee;font-size:15px;">${esc(d.name)}</b>
            <input type="month" id="ts-month" value="${esc(d.month)}" style="background:#1a1a1a;border:1px solid #333;color:#ddd;border-radius:4px;padding:5px 8px;">
            <span class="ts-chip"><b>${d.total}</b>本月 h</span>
            <span class="ts-chip"><b>${d.days_filled}</b>填了幾天</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;">
            <div class="ts-card" style="margin:0;"><h3>每天幾小時</h3>
                <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;font-size:10px;color:#666;text-align:center;">${_WD.map(w => `<div>${w}</div>`).join('')}</div>
                <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-top:4px;">${cells}</div></div>
            <div class="ts-card" style="margin:0;"><h3>案別</h3>${_bars(d.projects)}</div>
            <div class="ts-card" style="margin:0;"><h3>分類組成</h3>${_bars(d.composition)}</div>
            <div class="ts-card" style="margin:0;"><h3>近 12 個月</h3>${_bars(d.trend)}</div>
        </div>
        <div class="ts-card" style="margin-top:14px;"><h3>逐日</h3>
            ${_dayLog(d.days, 'project_name')}
        </div>`;
}

// ── 人員月視圖：每人 × 每專案 時數；點人名進檔案頁 ──
function _staffTbodyHtml() {
    const staffBlocks = _staffSorter.sorted((_staffCache && _staffCache.staff) || []).map(s => `
        <tr style="background:#262626;">
            <td style="color:#eee;font-weight:600;"><span class="ts-link" data-ts-action="open-person" data-name="${esc(s.name)}">${esc(s.name)}</span></td>
            <td class="num" style="color:#eee;font-weight:600;">${s.total_hours}</td>
            <td class="num" style="color:#777;">${s.projects.length} 案</td>
        </tr>
        ${s.projects.map(p => `
        <tr>
            <td style="padding-left:24px;color:#999;">${esc(p.project_name)}</td>
            <td class="num">${p.hours}</td>
            <td class="num" style="color:#777;">${p.rows} 列</td>
        </tr>`).join('')}`).join('');
    return staffBlocks || '<tr><td colspan="3" style="color:#666;text-align:center;">本月尚無工時資料</td></tr>';
}

function _renderStaffView(d) {
    return `${_head('人員：每人投入的專案時數（含 Sheet 同步與系統填）。員工端的團隊月表在 <a href="/hours.html" target="_blank" style="color:#93c5fd;">/hours.html</a>。')}
        <div class="ts-card">
            <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;">
                <input type="month" id="ts-month" value="${esc(d.month)}"
                       style="background:#1a1a1a;border:1px solid #333;color:#ddd;border-radius:4px;padding:5px 8px;">
                <span class="ts-chip"><b>${d.total_hours}</b>本月總時數</span>
                <span class="ts-chip"><b>${d.staff.length}</b>有填報人數</span>
            </div>
            <table id="ts-staff-table">
                <thead><tr>${sortableTh('name', '人員 / 專案')}${sortableTh('hours', '時數(h)', 'class="num"')}<th class="num"></th></tr></thead>
                <tbody>${_staffTbodyHtml()}</tbody>
            </table>
        </div>`;
}

// ── 儀表板：大家的四格 ＋ 主管層兩格（後端只給管理員 manager）──
function _renderDash(d) {
    const card = (title, body) => `<div class="ts-card" style="margin:0;"><h3>${title}</h3>${body}</div>`;
    const m = d.manager;
    return `${_head('儀表板：一眼看今天、本週、專案 burn、分類組成' + (m ? '；主管層：負載、漏填、有計畫沒結果' : '') + '。')}
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;">
            ${card('今天在做什麼', `<div style="margin-bottom:8px;"><span class="ts-chip"><b>${d.today.people}</b>人</span><span class="ts-chip"><b>${d.today.items}</b>工作項</span><span class="ts-chip"><b>${d.today.hours}</b>h</span></div>
                <button class="ts-btn ghost" data-ts-action="view" data-view="today">看每日看板 →</button>`)}
            ${card(`本週（${esc(d.week.from)} 起）`, `<div><span class="ts-chip"><b>${d.week.total}</b>全體 h</span><span class="ts-chip"><b>${d.week.people}</b>人有填</span></div>
                <div class="ts-note">參考：每人每週 ${d.week.reference_per_person} h（週一到週五 × 8，只是對照）</div>`)}
            ${card('專案 Burn 前五（超預算標紅、停滯標黃）', d.burn_top.map(p => `<div style="display:flex;gap:10px;align-items:center;font-size:12.5px;padding:4px 0;">
                <span class="ts-link" data-ts-action="open-project" data-name="${esc(p.project_name)}" style="flex:1;">${esc(p.project_name)}</span>
                ${p.stale ? '<span class="ts-badge warn">停滯</span>' : ''}
                <span class="ts-pct" style="${_pctStyle(p.pct)}">${p.pct != null ? p.pct + '%' : '—'}</span>
                <span style="color:#888;width:110px;text-align:right;">${p.hours_used} / ${p.budget_hours ?? '—'}</span></div>`).join('') || '<div style="color:#666;">—</div>')}
            ${card('本月分類組成', _bars(d.month_composition))}
            ${m ? card(`負載（本週每人；超過 ${d.week.reference_per_person} h 標紅）`, hbars(m.load.map(x => ({ label: x.name, value: x.hours, color: x.hours > d.week.reference_per_person ? '#f87171' : '#3b82f6' })),
                { formatValue: v => `${v} h`, showPct: false, emptyText: '本週還沒有人填' })) : ''}
            ${m ? card(`漏填與未完成的計畫（只有管理員看得到）`, `<div style="font-size:12.5px;color:#bbb;">${esc(m.missing_yesterday.date)} 沒填：${m.missing_yesterday.names.length ? m.missing_yesterday.names.map(esc).join('、') : '<span style="color:#6ee7b7;">大家都填了</span>'}</div>
                <div style="font-size:12.5px;color:#bbb;margin-top:6px;">有計畫還沒填實際：${m.plans_open.length ? m.plans_open.map(esc).join('、') : '沒有'}</div>`) : ''}
        </div>`;
}

// ── 設定（管理員）：Sheet 拉取、未對映指定、代填、token ──
const _REASON = {
    ambiguous: ['撞案', '#fbbf24'], none: ['找不到', '#fca5a5'], bucket: ['內部桶', '#6b7280'],
};

/** 「為什麼沒對到」那一格：撞案列候選、找不到列相似建議（建議不是對映，只是給人看）。 */
function _unmatchedWhyHtml(u) {
    const [label, color] = _REASON[u.reason] || ['', '#888'];
    let extra = '';
    if (u.reason === 'ambiguous' && (u.candidates || []).length) {
        extra = u.candidates.map(c => `${esc(c.name)}<span style="color:#666;">（${esc(c.client || '無客戶')}）</span>`).join('　/　');
    } else if (u.reason === 'none' && (u.suggestions || []).length) {
        extra = '像：' + u.suggestions.map(esc).join('、');
    }
    return `<span style="color:${color};font-size:11px;">${label}</span>`
        + (extra ? `<div style="color:#9ca3af;font-size:11px;">${extra}</div>` : '');
}

function _unmatchedTbodyHtml() {
    return _unmatchedSorter.sorted((_summaryCache && _summaryCache.unmatched) || []).map(u => `
                    <tr><td>${esc(u.project_name)}</td>
                        <td class="num">${u.hours_used}</td>
                        <td class="num">${u.rows}</td>
                        <td>${_unmatchedWhyHtml(u)}</td>
                        <td>${u.reason === 'bucket' ? '' : `
                            <button class="ts-btn ghost" data-ts-action="map"
                                    data-name="${esc(u.project_name)}">指定專案</button>`}</td></tr>`).join('');
}

function _renderSettings(s) {
    const unmatchedCard = `
        <div class="ts-card">
            <h3>未對映專案（${s.unmatched.length}）</h3>
            <table id="ts-unmatched-table">
                <thead><tr>${sortableTh('name', 'Sheet 專案名')}${sortableTh('hours', '時數', 'class="num"')}${sortableTh('rows', '列數', 'class="num"')}<th>原因</th><th></th></tr></thead>
                <tbody>${_unmatchedTbodyHtml() || '<tr><td colspan="5" style="color:#666;text-align:center;">全部對到了</td></tr>'}</tbody>
            </table>
            <div class="ts-note">去掉「客戶_」前綴後與私帳案名相同即自動對映；撞案（同名兩案）與找不到的按「指定專案」
                決定一次，之後每次同步自動吃到。「行政庶務」等內部桶留在這裡是正常的。</div>
        </div>`;
    const dg = _digestCache;
    const digestCard = dg ? `
        <div class="ts-card">
            <h3>週一工時 digest（Google Chat）</h3>
            <div class="ts-note" style="margin:0 0 8px;">${dg.enabled ? `開：cron ${esc(dg.cron)}` : '關'}｜${dg.last_summary ? esc(dg.last_summary) : '還沒發過'}
                　每人上週合計／填了幾天／漏填幾天；發到 notification.google_chat_webhook。</div>
            <button class="ts-btn ghost" data-ts-action="digest-toggle" data-on="${dg.enabled ? '0' : '1'}">${dg.enabled ? '關閉' : '開啟每週一 09:00'}</button>
            <button class="ts-btn ghost" data-ts-action="digest-preview">預覽上週</button>
            <button class="ts-btn ghost" data-ts-action="digest-send">立即發送</button>
            <pre id="ts-digest-preview" style="display:none;white-space:pre-wrap;color:#bbb;font-size:12px;background:#1a1a1a;padding:8px;border-radius:4px;margin-top:8px;"></pre>
        </div>` : '';
    return `${_head('設定：Sheet 拉取、未對映指定、代填、週一 digest、同步 token（管理員）')}
        ${_pullBar()}
        ${digestCard}
        ${unmatchedCard}
        <div class="ts-card">
            <h3>代填（管理員幫人補登）</h3>
            <button class="ts-btn ghost" data-ts-action="toggle-manual">快速補登</button>
            <div id="ts-manual-slot" style="display:none;margin-top:8px;"></div>
        </div>
        <div class="ts-card">
            <h3>從 Sheet 那邊推（可選）</h3>
            <div class="ts-note">主控端已經每週六自動拉，不必裝。若要改成 Sheet 端推，裝 <code>docs/appsscript/timesheet_sync.gs</code>，token 按這裡取：
                <button class="ts-btn ghost" data-ts-action="token" style="margin-left:6px;">顯示同步 Token</button>
                <span id="ts-token-slot" style="margin-left:10px;"></span></div>
        </div>`;
}

function _recentTbodyHtml() {
    return _recentSorter.sorted(_recentCache || []).map(r => `
                                    <tr><td>${esc(r.date || '')}</td><td>${esc(r.staff_name)}</td>
                                        <td>${esc(r.project_name)}</td>
                                        <td>${r.project_id ? '✅' : '<span style="color:#f59e0b;">—</span>'}</td>
                                        <td style="color:#999;">${esc(r.task_note || '')}</td>
                                        <td class="num">${r.hours}</td></tr>`).join('');
}

// 快速補登：一位人員 + 多列（日期/專案/內容/時數）→ POST /manual
async function _renderManual(slot) {
    slot.innerHTML = '<div style="color:#777;padding:8px;">載入選項…</div>';
    try {
        const [staffD, projList] = await Promise.all([
            tfetch('/api/v1/crm/staff?status=在職'),
            _projectOptions(),                       // 同一份 memo（我的一天／總表也用）
        ]);
        const staffOpts = (staffD.staff || []).map(s =>
            `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
        const projOpts = ['<option value="">— 選專案 —</option>']
            .concat(projList.map(p =>
                `<option value="${esc(p.name)}">${esc(p.name)}</option>`)).join('');
        const today = _today();   // 本地時區；toISOString 是 UTC 面值，會差一天
        const rowHtml = `
            <tr class="ts-mrow">
                <td><input type="date" value="${today}" data-f="date"></td>
                <td><select data-f="project">${projOpts}</select></td>
                <td><input type="text" data-f="note" placeholder="工作內容" style="width:100%;"></td>
                <td><input type="number" data-f="hours" min="0.1" step="0.1" style="width:70px;" placeholder="時數"></td>
            </tr>`;
        slot.innerHTML = `
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:8px;">
            <span style="color:#888;font-size:12px;">人員</span>
            <select id="ts-m-staff">${staffOpts}</select>
            <span class="ts-note" style="margin:0;">同人同日同案的手填列優先於 Sheet 同步（不會被覆蓋）</span>
        </div>
        <table id="ts-m-table" style="margin-bottom:8px;">
            <thead><tr><th style="width:140px;">日期</th><th style="width:220px;">專案</th><th>內容</th><th style="width:80px;">時數</th></tr></thead>
            <tbody>${rowHtml}</tbody>
        </table>
        <div style="display:flex;gap:8px;align-items:center;">
            <button class="ts-btn ghost" data-ts-action="manual-addrow">加一列</button>
            <button class="ts-btn" data-ts-action="manual-submit">送出</button>
            <span id="ts-m-result" style="font-size:12px;color:#888;"></span>
        </div>`;
        slot.dataset.rowTemplate = rowHtml;
    } catch (e) {
        slot.innerHTML = `<div style="color:#f87171;padding:8px;">選項載入失敗：${esc(e.message || e)}</div>`;
    }
}

// 私帳專案清單（挑選視窗用）—— 一次載入，這頁開著期間不會變
let _mineProjects = null;

/** 指定一個 Sheet 專案名對到哪一案：寫對映表 → 回填既有列 → 重整。
 *  🔴 走 PUT /project_map ＋ POST /remap，不自己改 timesheets.project_id ——
 *  對映表是之後每次同步也要吃的正本，只改列就會下次拉取又冒出來。 */
async function _mapProject(sheetName) {
    if (!sheetName) return;
    if (!_mineProjects) {
        // 工時自己的薄端點：同一份查表、只有 id/名稱/客戶；可見性照私帳規矩 ——
        // 沒 finance_mine 的管理員拿 403，把後端那句理由直接給他看，不要開空視窗
        try {
            _mineProjects = (await tfetch('/api/v1/timesheets/projects')).projects || [];
        } catch (e) {
            alert('無法列出私帳案：' + (e.message || e));
            return;
        }
    }
    openProjectPicker({
        projects: _mineProjects,
        currentId: '',
        title: '指定專案 — ' + sheetName,
        onPick: async (pid) => {
            if (!pid) return;
            try {
                await tfetch('/api/v1/timesheets/project_map', {
                    method: 'PUT', body: { items: [{ sheet_name: sheetName, project_id: pid }] },
                });
                await tfetch('/api/v1/timesheets/remap', { method: 'POST' });
                _summaryCache = null;
                await refresh();
            } catch (e) {
                alert('指定失敗：' + (e.message || e));
            }
        },
    });
}


/** 每次整頁重繪後：輸入框的 change（按鈕的 click 走 initTimesheetsTab 的委派，不在這裡綁）。 */
function _bind() {
    const monthInp = document.getElementById('ts-month');
    if (monthInp) monthInp.addEventListener('change', () => { _month = monthInp.value; refresh(); });
    const dayInp = document.getElementById('ts-day');
    if (dayInp) dayInp.addEventListener('change', () => { if (dayInp.value) { _day = dayInp.value; refresh(); } });
    if (_view === 'mine' || _view === 'ledger') _projectOptions();     // 先抓好，專案格一點就有得選
    const pq = document.getElementById('ts-proj-q');
    if (pq) pq.addEventListener('input', () => {
        _projQ = pq.value;
        _redrawTbody('ts-burn-table', _burnTbodyHtml, _burnSorter);
        const ub = document.getElementById('ts-unmatched-proj-body');
        if (ub) ub.innerHTML = _unmatchedProjRowsHtml();
    });
    const mt = document.getElementById('ts-month-to');
    if (mt) mt.addEventListener('change', () => { _monthTo = mt.value; refresh(); });
    ['staff', 'project', 'source', 'q'].forEach(k => {
        const el = document.getElementById('ts-lf-' + k);
        if (el) el.addEventListener(k === 'q' ? 'input' : 'change', () => { _ledgerFilter[k] = el.value; _ledgerRedraw(); });
    });
    if (_view === 'ledger') _ledgerRedraw();   // 計數 chip
}

async function _onAction(btn) {
            const act = btn.dataset.tsAction;
            if (act === 'refresh') { _summaryCache = null; return refresh(); }
            if (act === 'pull') return _pullNow();
            if (act === 'pull-settings') return _pullSettings();
            if (act === 'map') return _mapProject(btn.dataset.name);
            if (act === 'view') { _view = btn.dataset.view; return refresh(); }
            if (act === 'day') {
                const delta = Number(btn.dataset.delta);
                _day = delta === 0 ? _today() : _shiftDay(_day, delta);
                return refresh();
            }
            if (act === 'board-days') { _boardDays = Number(btn.dataset.days); return refresh(); }
            if (act === 'proj-pop') return _openProjectModal(btn.dataset.name, btn.dataset.pid);
            if (act === 'proj-pop-close') { document.getElementById('ts-proj-modal')?.remove(); return; }
            if (act === 'open-project') {
                // 帶 pid＝撈整個案（所有對到它的 Sheet 案名）；比較清單的 id:<pid> 鍵也走這裡
                const key = btn.dataset.name || '';
                _projectPid = btn.dataset.pid || (key.startsWith('id:') ? key.slice(3) : '');
                _view = 'project:' + (key.startsWith('id:') ? '' : key);
                return refresh();
            }
            if (act === 'open-person') { _view = 'person:' + btn.dataset.name; return refresh(); }
            if (act === 'compare-add' || act === 'compare-remove') {
                const n = btn.dataset.name;
                _compareNames = act === 'compare-add'
                    ? (_compareNames.includes(n) ? _compareNames : [..._compareNames, n])
                    : _compareNames.filter(x => x !== n);
                if (!_compareNames.length && _view === 'compare') _view = 'projects';
                // 只是切前端的比較清單：專案檔案頁用快取重繪，不再打一次最重的 /project
                if (_view.startsWith('project:') && _projectCache) { _content.innerHTML = _renderProject(_projectCache); _bind(); return; }
                return refresh();
            }
            if (act === 'budget-suggest') {
                if (!confirm(`把建議預算填進 ${btn.dataset.n} 個還沒設預算的案？（已設的不會動）`)) return;
                try {
                    const r = await tfetch('/api/v1/timesheets/budgets/suggest', { method: 'POST' });
                    _summaryCache = null;
                    alert(`已填 ${r.applied} 案`);
                    return refresh();
                } catch (e) { return alert('套用失敗：' + (e.message || e)); }
            }
            if (act === 'budget') {
                const v = prompt('這個案的預算小時（清空＝拿掉預算）', btn.dataset.cur || '');
                if (v === null) return;
                try {
                    await tfetch('/api/v1/timesheets/project_budget', { method: 'PUT',
                        body: { project_id: btn.dataset.pid, budget_hours: v.trim() ? parseFloat(v) : null } });
                    _summaryCache = null;
                    return refresh();
                } catch (e) { return alert('改預算失敗：' + (e.message || e)); }
            }
            if (act === 'export-month' || act === 'export-project') {
                const key = act === 'export-month' ? _month : btn.dataset.name;
                const q = act === 'export-month' ? 'month=' + _month
                    : (btn.dataset.pid ? 'project_id=' + encodeURIComponent(btn.dataset.pid) : 'project=' + encodeURIComponent(key));
                return authDownload('/api/v1/timesheets/export.csv?' + q, `timesheets_${key}.csv`, '匯出');
            }
            if (act === 'digest-toggle') {
                try { _digestCache = await tfetch('/api/v1/timesheets/digest', { method: 'PUT', body: { enabled: btn.dataset.on === '1' } }); return refresh(); }
                catch (e) { return alert('儲存失敗：' + (e.message || e)); }
            }
            if (act === 'digest-preview' || act === 'digest-send') {
                const pre = document.getElementById('ts-digest-preview');
                try {
                    const r = await tfetch('/api/v1/timesheets/digest' + (act === 'digest-preview' ? '?preview=1' : ''), { method: 'POST' });
                    pre.style.display = '';
                    pre.textContent = (r.status === 'ok' ? '已發送：\n' : r.status === 'skipped' ? '沒設 webhook，沒送：\n' : r.status === 'preview' ? '' : (r.message || r.status) + '\n') + (r.text || '');
                    if (act === 'digest-send') _digestCache = await tfetch('/api/v1/timesheets/digest').catch(() => _digestCache);
                } catch (e) { pre.style.display = ''; pre.textContent = '失敗：' + (e.message || e); }
                return;
            }
            if (act === 'conflict') {
                try {
                    await tfetch(`/api/v1/timesheets/conflicts/${btn.dataset.id}/resolve`, { method: 'POST', body: { choice: btn.dataset.choice } });
                    return refresh();
                } catch (e) { alert('決定失敗：' + (e.message || e)); return; }
            }
            if (act === 'type-edit') {
                const opts = (_summaryCache && _summaryCache.project_types) || [];
                const sel = document.createElement('select');
                sel.setAttribute('data-set-type', btn.dataset.pid); sel.setAttribute('data-no-search', '');
                sel.style.cssText = 'background:#1a1a1a;border:1px solid #333;color:#ccc;border-radius:4px;padding:2px 4px;font-size:12px;max-width:130px;';
                sel.innerHTML = `<option value="">— 案型 —</option>${opts.map(t => `<option value="${esc(t)}"${t === btn.dataset.cur ? ' selected' : ''}>${esc(t)}</option>`).join('')}`;
                sel.addEventListener('blur', () => { if (sel.isConnected) sel.replaceWith(Object.assign(document.createElement('div'), { innerHTML: _burnTypeSelect({ project_id: btn.dataset.pid, project_type: sel.value }) }).firstElementChild); });
                btn.replaceWith(sel); sel.focus();
                return;
            }
            if (act === 'batch-clear') { _ledgerSel = new Set(); return _ledgerRedraw(); }
            if (act === 'batch-apply') {
                const g = id => (document.getElementById(id) || {}).value || '';
                const body = { ids: [..._ledgerSel] };
                const proj = g('ts-batch-project').trim();
                if (proj) Object.assign(body, _projectFromInput(proj));
                if (g('ts-batch-type')) body.work_type = g('ts-batch-type');
                if (g('ts-batch-remark').trim()) body.remark = g('ts-batch-remark').trim();
                if (g('ts-batch-note').trim()) body.note = g('ts-batch-note').trim();
                if (Object.keys(body).length === 1) return alert('專案／分類／備註至少填一個');
                if (!confirm(`把 ${body.ids.length} 列一次改掉？`)) return;
                try {
                    const r = await tfetch('/api/v1/timesheets/rows/batch', { method: 'POST', body });
                    alert(`已改 ${r.updated} 列`);
                    return refresh();
                } catch (e) { return alert('批次沒存：' + (e.message || e)); }
            }
            if (act === 'ledger-edit') {
                const i = (_ledgerCache.items || []).find(x => x.id === btn.dataset.id);
                const tr = document.querySelector(`[data-ledger-id="${btn.dataset.id}"]`);
                if (!i || !tr) return;
                tr.outerHTML = _ledgerEditHtml(i);      // datalist 在表外，_bind 已填好
                return;
            }
            if (act === 'ledger-cancel') return _ledgerRedraw();
            if (act === 'ledger-save') {
                const tr = btn.closest('tr');
                try {
                    const it = await tfetch('/api/v1/timesheets/rows/' + btn.dataset.id, { method: 'PUT',
                        body: { ..._rowBody(tr), note: tr.querySelector('[data-f="admnote"]').value } });
                    const k = _ledgerCache.items.findIndex(x => x.id === it.id);
                    if (k >= 0) _ledgerCache.items[k] = it;
                    _ledgerRedraw();
                } catch (e) { tr.querySelector('[data-err]').textContent = e.message || e; }
                return;
            }
            if (act === 'ledger-del') {
                const row = (_ledgerCache.items || []).find(x => x.id === btn.dataset.id);
                const fromSheet = row && row.source !== 'manual';
                if (!confirm(fromSheet ? '刪掉這一列？\n這是 Sheet 拉進來的：刪了之後下次拉取不會再插回來（總表為準）。' : '刪掉這一列？')) return;
                try {
                    await tfetch('/api/v1/timesheets/rows/' + btn.dataset.id, { method: 'DELETE' });
                    _ledgerCache.items = _ledgerCache.items.filter(x => x.id !== btn.dataset.id);
                    _ledgerRedraw();
                } catch (e) { alert('刪除失敗：' + (e.message || e)); }
                return;
            }
            if (act === 'row-add') {
                const tb = document.querySelector('#ts-mine-add tbody');
                if (tb) tb.insertAdjacentHTML('beforeend', _newRowHtml().repeat(5));   // owner：一次五列
                return;
            }
            if (act === 'row-remove') return _mineRemove(btn.closest('tr'));
            if (act === 'copy-yesterday') {
                const tb = document.querySelector('#ts-mine-add tbody');
                if (!tb || !_mineCache) return;
                [...tb.querySelectorAll('.ts-mine-row')].filter(tr => !tr.querySelector('[data-f="project"]').value).forEach(tr => tr.remove());
                (_mineCache.yesterday || []).forEach(i => tb.insertAdjacentHTML('beforeend',
                    _newRowHtml({ project: i.project_name, work_type: i.work_type, note: i.task_note, planned: i.planned_hours, hours: '' })));
                [...tb.querySelectorAll('.ts-mine-row:not([data-id])')].forEach(_mineScheduleSave);   // 有計畫的立刻存成今天的計畫
                return;
            }
            if (act === 'toggle-manual') {
                const slot = document.getElementById('ts-manual-slot');
                if (!slot) return;
                const show = slot.style.display === 'none';
                slot.style.display = show ? '' : 'none';
                if (show && !slot.innerHTML) await _renderManual(slot);
                return;
            }
            if (act === 'manual-addrow') {
                const tbody = document.querySelector('#ts-m-table tbody');
                const slot = document.getElementById('ts-manual-slot');
                if (tbody && slot) tbody.insertAdjacentHTML('beforeend', slot.dataset.rowTemplate || '');
                return;
            }
            if (act === 'manual-submit') {
                const resEl = document.getElementById('ts-m-result');
                const staffSel = document.getElementById('ts-m-staff');
                const rows = [...document.querySelectorAll('#ts-m-table .ts-mrow')].map(_rowBody)
                    .filter(r => r.work_date && r.hours > 0);
                if (!staffSel?.value || !rows.length) {
                    if (resEl) resEl.textContent = '請選人員並至少填一列（日期 + 時數）';
                    return;
                }
                try {
                    const d = await tfetch('/api/v1/timesheets/manual', {
                        method: 'POST', body: { staff_id: staffSel.value, rows },
                    });
                    if (resEl) resEl.textContent = `已寫入 ${d.inserted} 列`
                        + (d.unmatched_projects.length ? `（未對映：${d.unmatched_projects.join('、')}）` : '');
                    setTimeout(refresh, 800);
                } catch (e) {
                    if (resEl) resEl.textContent = '送出失敗：' + (e.message || e);
                }
                return;
            }
            if (act === 'token') {
                try {
                    const d = await tfetch('/api/v1/timesheets/ingest_token');
                    document.getElementById('ts-token-slot').innerHTML = `<code>${esc(d.token)}</code>`;
                } catch (e) {
                    document.getElementById('ts-token-slot').textContent = '取失敗：' + (e.message || e);
                }
                return;
            }
            if (act === 'recent') {
                const slot = document.getElementById('ts-recent-slot');
                if (!slot) return;
                slot.innerHTML = '<div style="color:#777;padding:8px;">載入中…</div>';
                try {
                    const d = await tfetch('/api/v1/timesheets/recent?limit=50');
                    _recentCache = d.rows || [];
                    slot.innerHTML = `
                        <div class="ts-card">
                            <h3>最近同步 50 列</h3>
                            <table id="ts-recent-table">
                                <thead><tr>${sortableTh('date', '日期')}${sortableTh('staff', '員工')}${sortableTh('project', '專案')}${sortableTh('matched', '對映')}${sortableTh('task', '內容')}${sortableTh('hours', '時數', 'class="num"')}</tr></thead>
                                <tbody>${_recentTbodyHtml()}</tbody>
                            </table>
                        </div>`;
                    _recentSorter.attach();
                } catch (e) {
                    slot.innerHTML = `<div style="color:#f87171;padding:8px;">載入失敗：${esc(e.message || e)}</div>`;
                }
            }
}
