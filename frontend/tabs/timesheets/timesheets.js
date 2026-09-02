/**
 * timesheets.js — 工作追蹤 Tab（人事管理；docs/WORK_TRACKING_UI_PLAN.md P1）
 *
 * MASTER 同源功能：打 /api/v1/timesheets/*（帶 auth token）。
 * 六個分頁：今日（每日看板：每個人每天做了什麼，實際為主、計畫加分）／我的一天（登入者
 * 自己記：實際或計畫、時數快捷鈕、複製昨天）／專案（burn 表）／人員（月視圖）／儀表板（P3）／
 * 設定（Sheet 拉取、未對映指定、代填、token；管理員）。
 * 資料 = Google Sheet 每小時拉 + 系統內填（同人同日同案手填優先）。不審核。
 * 新增 UI 依 owner 鐵則無 emoji（既有元素不回溯）。
 */

import { esc } from '../website/website-utils.js';
import { createSortable, sortableTh, today as _today } from '../crm/crm-utils.js';
import { openProjectPicker } from '../../js/shared/project-picker.js';   // 指定專案：共用挑選視窗

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
let _view = 'today';                                   // today | mine | projects | staff | dash | settings
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
let _compareNames = [];     // 類似專案並排：目前選的案名
let _digestCache = null;    // GET /timesheets/digest

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
    const state = p.enabled ? `每小時自動拉（cron ${esc(p.cron)}）` : '自動拉取：關';
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

/** 設定視窗最小化：prompt 試算表 id → 是否啟用；cron 用預設每小時。 */
async function _pullSettings() {
    const cur = _pullCache || {};
    const sid = prompt('工時試算表 id（網址 /d/<id>/ 那段；公開連結即可）', cur.sheet_id || '');
    if (sid === null) return;
    const enable = confirm('要開啟每小時自動拉取嗎？\n（取消＝關閉自動拉取，仍可手動「立即拉取」）');
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

const _burnSorter = createSortable({
    storageKey: 'timesheets_burn_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'ts-burn-table',
    onChange: () => _redrawTbody('ts-burn-table', _burnTbodyHtml, _burnSorter),
    getters: {
        project: p => p.project_name || p.project_id || '',
        status: p => p.status || '',
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
        staff: r => r.staff || '',
        project: r => r.project || '',
        matched: r => r.matched ? 1 : 0,
        task: r => r.task || '',
        hours: r => r.hours ?? '',
    },
});

export async function initTimesheetsTab() {
    _content = document.getElementById('ts-content');
    if (!_content) return;
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
            _content.innerHTML = _renderMine(d, err);
        } else if (_view === 'staff') {
            const d = await tfetch('/api/v1/timesheets/by_staff?month=' + _month);
            _staffCache = d;
            _content.innerHTML = _renderStaffView(d);
        } else if (_view === 'dash') {
            _content.innerHTML = _renderDash(await tfetch('/api/v1/timesheets/dashboard'));
        } else if (_view === 'settings') {
            const s = await tfetch('/api/v1/timesheets/summary');
            _summaryCache = s;
            _pullCache = await tfetch('/api/v1/timesheets/pull').catch(() => null);
            _digestCache = await tfetch('/api/v1/timesheets/digest').catch(() => null);
            _content.innerHTML = _renderSettings(s);
        } else if (_view.startsWith('project:')) {
            const d = await tfetch('/api/v1/timesheets/project?name=' + encodeURIComponent(_view.slice(8)));
            _projectCache = d;
            _content.innerHTML = _renderProject(d);
        } else if (_view === 'compare') {
            const d = await tfetch('/api/v1/timesheets/compare?names=' + encodeURIComponent(_compareNames.join('|')));
            _content.innerHTML = _renderCompare(d);
        } else if (_view.startsWith('person:')) {
            const d = await tfetch(`/api/v1/timesheets/person?name=${encodeURIComponent(_view.slice(7))}&month=${_month}`);
            _content.innerHTML = _renderPerson(d);
        } else {
            const s = await tfetch('/api/v1/timesheets/summary');
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

// 六個分頁（純文字，無 emoji）
function _viewBtns() {
    const b = (key, label) => `<button class="ts-btn ${_view === key ? '' : 'ghost'}"
        data-ts-action="view" data-view="${key}">${label}</button>`;
    return `<div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        ${b('today', '今日')}${b('mine', '我的一天')}${b('projects', '專案')}${b('staff', '人員')}${b('dash', '儀表板')}${b('settings', '設定')}
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

// ── 今日：每日看板（每個人每天做了什麼；實際為主，計畫加分；不排名、不標紅）──
function _itemCard(i) {
    const hrs = i.hours > 0
        ? `<b style="color:#eee;">${i.hours} h</b>${i.planned_hours ? `<span style="color:#666;"> ／計畫 ${i.planned_hours}</span>` : ''}`
        : `<span style="color:#93c5fd;">計畫 ${i.planned_hours} h</span>`;
    return `<div style="padding:6px 8px;border:1px solid #333;border-radius:6px;margin:4px 0;background:${i.hours > 0 ? '#1f1f1f' : '#1a2233'};">
        <div style="color:#ddd;font-size:12.5px;">${i.work_type ? `<span style="color:#9ca3af;">[${esc(i.work_type)}]</span> ` : ''}${esc(i.project_name || '(空白)')}</div>
        ${i.task_note ? `<div style="color:#999;font-size:11.5px;">${esc(i.task_note)}</div>` : ''}
        <div style="font-size:11.5px;margin-top:2px;">${hrs}${i.source === 'sheet' || i.source === 'import' ? '<span style="color:#666;"> · Sheet</span>' : ''}</div>
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
                        `<div style="font-size:11.5px;color:${i.hours > 0 ? '#ccc' : '#93c5fd'};">${esc(i.project_name || '(空白)')} ${i.hours > 0 ? i.hours : '計畫 ' + i.planned_hours}h</div>`).join('') : '<span style="color:#444;">—</span>'}</td>`;
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
function _typeSelect(cur, attr) {
    const wts = (_mineCache && _mineCache.work_types) || [];
    return `<select ${attr}><option value="">分類</option>${wts.map(t =>
        `<option value="${esc(t)}"${t === cur ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
}
function _newRowHtml(v = {}) {
    return `<tr class="ts-mine-row">
        <td><input list="ts-proj-list" data-f="project" value="${esc(v.project || '')}" placeholder="專案（可打字）" style="width:100%;"></td>
        <td>${_typeSelect(v.work_type || '', 'data-f="type"')}</td>
        <td><input type="text" data-f="note" value="${esc(v.note || '')}" placeholder="做了什麼" style="width:100%;"></td>
        <td><input type="number" data-f="planned" min="0" step="0.25" value="${v.planned ?? ''}" placeholder="計畫" style="width:64px;"></td>
        <td><input type="number" data-f="hours" min="0" step="0.25" value="${v.hours ?? ''}" placeholder="實際" style="width:64px;">
            ${[0.5, 1, 2, 4, 8].map(h => `<button class="ts-btn ghost" data-hq="${h}" style="padding:2px 6px;font-size:11px;margin-left:2px;">${h}</button>`).join('')}</td>
        <td><button class="ts-btn ghost" data-ts-action="row-remove" style="padding:2px 8px;">×</button></td>
    </tr>`;
}
function _mineItemRow(i) {
    const hrs = i.hours > 0 ? `<b>${i.hours} h</b>${i.planned_hours ? `<span style="color:#666;">（計畫 ${i.planned_hours}）</span>` : ''}`
        : `<span style="color:#93c5fd;">計畫 ${i.planned_hours} h</span>`;
    const src = i.source === 'manual' ? '' : '<span style="color:#666;"> · Sheet</span>';
    const acts = i.editable ? `
        ${i.hours > 0 ? '' : `<button class="ts-btn ghost" data-ts-action="mine-done" data-id="${esc(i.id)}" style="padding:2px 8px;">完成（照計畫）</button>`}
        <button class="ts-btn ghost" data-ts-action="mine-edit" data-id="${esc(i.id)}" style="padding:2px 8px;">改</button>
        <button class="ts-btn ghost" data-ts-action="mine-del" data-id="${esc(i.id)}" style="padding:2px 8px;">刪</button>` : '';
    return `<tr data-mine-id="${esc(i.id)}">
        <td>${i.work_type ? `<span style="color:#9ca3af;">[${esc(i.work_type)}]</span> ` : ''}${esc(i.project_name || '(空白)')}</td>
        <td style="color:#999;">${esc(i.task_note || '')}</td>
        <td class="num">${hrs}${src}</td>
        <td style="white-space:nowrap;">${acts}</td>
    </tr>`;
}
function _renderMine(d, err) {
    if (!d) {
        return `${_head('我的一天：自己記今天做了什麼（實際小時），想先排的可以填計畫小時。')}
            <div class="ts-card" style="color:#fca5a5;">${esc(err || '載入失敗')}
                <div class="ts-note">要用「我的一天」，帳號要在「使用者管理」綁定人員檔案。</div></div>`;
    }
    const items = d.items || [];
    return `${_head('我的一天：自己記今天做了什麼（實際小時），想先排的可以填計畫小時。不審核，隨時可改。')}
        ${_dayNav(`<span class="ts-chip"><b>${d.actual_total}</b>實際 h</span>${d.planned_total ? `<span class="ts-chip"><b>${d.planned_total}</b>計畫 h</span>` : ''}
            <span style="color:#777;font-size:12px;">${esc(d.staff_name)}</span>`)}
        <div class="ts-card">
            <h3>${esc(_dayLabel(d.date))} 的工作項</h3>
            <table id="ts-mine-table">
                <thead><tr><th>專案</th><th>做了什麼</th><th class="num">時數</th><th></th></tr></thead>
                <tbody>${items.map(_mineItemRow).join('') || '<tr><td colspan="4" style="color:#666;text-align:center;">這一天還沒有紀錄</td></tr>'}</tbody>
            </table>
        </div>
        <div class="ts-card" style="border-color:#3b82f6;">
            <h3>新增</h3>
            <table id="ts-mine-add">
                <thead><tr><th style="width:26%;">專案</th><th style="width:110px;">分類</th><th>做了什麼</th><th style="width:70px;">計畫</th><th style="width:250px;">實際</th><th style="width:36px;"></th></tr></thead>
                <tbody>${_newRowHtml()}</tbody>
            </table>
            <datalist id="ts-proj-list"></datalist>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px;">
                <button class="ts-btn ghost" data-ts-action="row-add">＋ 一列</button>
                <button class="ts-btn ghost" data-ts-action="copy-yesterday" ${(d.yesterday || []).length ? '' : 'disabled'}>複製昨天（${(d.yesterday || []).length} 列）</button>
                <button class="ts-btn" data-ts-action="mine-submit">送出</button>
                <span id="ts-mine-result" style="font-size:12px;color:#888;"></span>
            </div>
            <div class="ts-note">實際或計畫至少填一個。只填計畫＝先排；之後按「完成（照計畫）」或改實際小時。
                同一天同一案 Sheet 已有的列會標「Sheet」，不用再填一次。</div>
        </div>`;
}
function _readNewRows() {
    return [...document.querySelectorAll('#ts-mine-add .ts-mine-row')].map(tr => {
        const v = f => tr.querySelector(`[data-f="${f}"]`).value;
        return {
            work_date: _day, project_name: v('project').trim(), work_type: v('type') || null,
            task_note: v('note'), planned_hours: v('planned') ? parseFloat(v('planned')) : null,
            hours: v('hours') ? parseFloat(v('hours')) : null,
        };
    }).filter(r => (r.hours || 0) > 0 || (r.planned_hours || 0) > 0);
}
async function _mineSubmit() {
    const resEl = document.getElementById('ts-mine-result');
    const rows = _readNewRows();
    if (!rows.length) { resEl.textContent = '至少一列要有實際或計畫小時'; return; }
    try {
        const r = await tfetch('/api/v1/timesheets/mine/rows', { method: 'POST', body: { rows } });
        resEl.textContent = `已寫入 ${r.inserted} 列` + (r.unmatched_projects.length ? `（專案名對不到案，管理員會指定：${r.unmatched_projects.join('、')}）` : '');
        setTimeout(refresh, 600);
    } catch (e) { resEl.textContent = '送出失敗：' + (e.message || e); }
}
async function _mineDone(id) {
    const i = (_mineCache.items || []).find(x => x.id === id);
    if (!i) return;
    try {
        await tfetch('/api/v1/timesheets/mine/' + id, { method: 'PUT', body: {
            work_date: i.date, project_name: i.project_name, task_note: i.task_note,
            work_type: i.work_type || null, planned_hours: i.planned_hours, hours: i.planned_hours,
        } });
        refresh();
    } catch (e) { alert('完成失敗：' + (e.message || e)); }
}
async function _mineEdit(id) {
    const i = (_mineCache.items || []).find(x => x.id === id);
    const tr = document.querySelector(`[data-mine-id="${id}"]`);
    if (!i || !tr) return;
    await _projectOptions();
    tr.outerHTML = `<tr data-mine-edit="${esc(id)}"><td colspan="4"><table><tbody>
        ${_newRowHtml({ project: i.project_name, work_type: i.work_type, note: i.task_note, planned: i.planned_hours, hours: i.hours || '' }).replace('data-ts-action="row-remove"', 'data-ts-action="mine-cancel"')}
        <tr><td colspan="6"><button class="ts-btn" data-ts-action="mine-save" data-id="${esc(id)}">儲存</button>
            <button class="ts-btn ghost" data-ts-action="mine-cancel">取消</button>
            <span data-err style="color:#fca5a5;font-size:12px;margin-left:8px;"></span></td></tr>
    </tbody></table></td></tr>`;
    _bind();
}
async function _mineSave(id) {
    const box = document.querySelector(`[data-mine-edit="${id}"]`);
    if (!box) return;
    const tr = box.querySelector('.ts-mine-row');
    const v = f => tr.querySelector(`[data-f="${f}"]`).value;
    try {
        await tfetch('/api/v1/timesheets/mine/' + id, { method: 'PUT', body: {
            work_date: _day, project_name: v('project').trim(), work_type: v('type') || null,
            task_note: v('note'), planned_hours: v('planned') ? parseFloat(v('planned')) : null,
            hours: v('hours') ? parseFloat(v('hours')) : null,
        } });
        refresh();
    } catch (e) { box.querySelector('[data-err]').textContent = e.message || e; }
}
async function _mineDelete(id) {
    if (!confirm('刪掉這一列？')) return;
    try { await tfetch('/api/v1/timesheets/mine/' + id, { method: 'DELETE' }); refresh(); }
    catch (e) { alert('刪除失敗：' + (e.message || e)); }
}

// ── 專案：burn 表（消耗率高在前）＋ 最近同步列 ──
function _pctStyle(pct) {
    if (pct == null) return 'background:#2c2c2c;color:#777;';
    if (pct >= 100) return 'background:#7f1d1d;color:#fca5a5;';
    if (pct >= 90) return 'background:#78350f;color:#fbbf24;';
    if (pct >= 60) return 'background:#1e3a5f;color:#93c5fd;';
    return 'background:#064e3b;color:#6ee7b7;';
}

function _burnTbodyHtml() {
    const rows = _burnSorter.sorted((_summaryCache && _summaryCache.projects) || []).map(p => `
        <tr>
            <td><span class="ts-link" data-ts-action="open-project" data-name="${esc(p.project_name || '')}">${esc(p.project_name || p.project_id)}</span>
                ${p.stale ? '<span class="ts-badge warn" title="進行中但 7 天沒工時">停滯</span>' : ''}</td>
            <td style="color:#888;">${esc(p.status || '')}</td>
            <td class="num">${p.hours_used}</td>
            <td class="num">${p.budget_hours ?? '<span style="color:#666;">未設</span>'}</td>
            <td class="num">${p.remaining ?? '—'}</td>
            <td class="num"><span class="ts-pct" style="${_pctStyle(p.pct)}">${p.pct != null ? p.pct + '%' : '—'}</span></td>
            <td class="num" style="color:#777;">${p.rows}</td>
            <td style="color:#777;">${esc(p.last_entry || '')}</td>
        </tr>`).join('');
    return rows || '<tr><td colspan="8" style="color:#666;text-align:center;">尚無已對映專案</td></tr>';
}

function _renderProjects(s) {
    const totalHours = s.projects.reduce((a, p) => a + (p.hours_used || 0), 0)
        + s.unmatched.reduce((a, u) => a + (u.hours_used || 0), 0);
    const stale = s.projects.filter(p => p.stale).length;
    const unmatchedRows = s.unmatched.filter(u => u.reason !== 'bucket').map(u => `
        <tr><td><span class="ts-link" data-ts-action="open-project" data-name="${esc(u.project_name)}">${esc(u.project_name)}</span>
                <span class="ts-badge">未對映</span></td>
            <td class="num">${u.hours_used}</td>
            <td><button class="ts-btn ghost" data-ts-action="map" data-name="${esc(u.project_name)}" style="padding:2px 8px;">指定專案</button></td></tr>`).join('');
    return `${_head('專案：每案投入時數對預算。點案名進檔案頁（時間軸、分類組成、類似專案並排）。')}
        <div style="margin-bottom:12px;">
            <span class="ts-chip"><b>${s.total_rows}</b>總列數</span>
            <span class="ts-chip"><b>${s.projects.length}</b>已對映專案</span>
            <span class="ts-chip"><b>${s.unmatched.length}</b>未對映</span>
            <span class="ts-chip"><b>${stale}</b>停滯</span>
            <span class="ts-chip"><b>${Math.round(totalHours)}</b>總時數</span>
            <button class="ts-btn ghost" data-ts-action="refresh" style="vertical-align:top;">↻ 重新整理</button>
            <button class="ts-btn ghost" data-ts-action="recent" style="vertical-align:top;">最近同步列</button>
            <button class="ts-btn ghost" data-ts-action="export-month" style="vertical-align:top;">匯出本月 CSV</button>
        </div>
        ${unmatchedRows ? `<div class="ts-card"><h3>未對映（${s.unmatched.filter(u => u.reason !== 'bucket').length}）—— 對到案之後才有預算與 burn</h3>
            <table><thead><tr><th>Sheet 案名</th><th class="num">時數</th><th></th></tr></thead><tbody>${unmatchedRows}</tbody></table></div>` : ''}
        <div class="ts-card">
            <h3>📊 專案 Burn（消耗率高在前）</h3>
            <table id="ts-burn-table">
                <thead><tr>
                    ${sortableTh('project', '專案')}${sortableTh('status', '狀態')}${sortableTh('used', '已投入(h)', 'class="num"')}${sortableTh('budget', '預算(h)', 'class="num"')}
                    ${sortableTh('remaining', '剩餘(h)', 'class="num"')}${sortableTh('pct', '消耗率', 'class="num"')}${sortableTh('rows', '列數', 'class="num"')}${sortableTh('last', '最後填報')}
                </tr></thead>
                <tbody>${_burnTbodyHtml()}</tbody>
            </table>
        </div>
        <div id="ts-recent-slot"></div>`;
}

// ── 專案檔案頁：摘要／分類組成／時間軸／各人各月／預算／報價人日／類似專案 ──
function _bars(pairs, colorFn) {
    const max = Math.max(1, ...pairs.map(p => p[1]));
    return pairs.map(([k, v, pct]) => `<div style="display:flex;gap:8px;align-items:center;font-size:12px;margin:3px 0;">
        <span style="width:110px;color:#bbb;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(k)}</span>
        <div style="flex:1;height:8px;background:#2c2c2c;border-radius:4px;overflow:hidden;"><i style="display:block;height:100%;width:${Math.round(v / max * 100)}%;background:${colorFn ? colorFn(k) : '#3b82f6'};"></i></div>
        <span style="width:90px;text-align:right;color:#ddd;">${v} h${pct != null ? `<span style="color:#666;"> ${pct}%</span>` : ''}</span></div>`).join('') || '<div style="color:#666;">—</div>';
}
function _renderProject(d) {
    const pct = d.pct == null ? '—' : d.pct + '%';
    const inCompare = _compareNames.includes(d.project_name);
    return `${_head('專案檔案：這個案誰在哪天做了什麼、花了多少、跟類似的案比起來如何。')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
            <button class="ts-btn ghost" data-ts-action="view" data-view="projects">‹ 專案清單</button>
            <b style="color:#eee;font-size:15px;">${esc(d.project_name)}</b>
            <span style="color:#888;">${esc(d.status || (d.mapped ? '' : '未對映'))}</span>
            <span style="flex:1;"></span>
            <button class="ts-btn ghost" data-ts-action="compare-add" data-name="${esc(d.project_name)}">${inCompare ? '已在比較清單' : '加入比較'}</button>
            ${_compareNames.length ? `<button class="ts-btn" data-ts-action="view" data-view="compare">並排比較（${_compareNames.length}）</button>` : ''}
            ${d.mapped ? `<button class="ts-btn ghost" data-ts-action="budget" data-pid="${esc(d.project_id)}" data-cur="${d.budget_hours ?? ''}">改預算</button>` : ''}
            <button class="ts-btn ghost" data-ts-action="export-project" data-name="${esc(d.project_name)}">匯出 CSV</button>
        </div>
        <div style="margin-bottom:12px;">
            <span class="ts-chip"><b>${d.total}</b>總時數</span>
            <span class="ts-chip"><b>${d.people}</b>人</span>
            <span class="ts-chip"><b>${d.span_days}</b>天（${esc(d.first || '—')} → ${esc(d.last || '—')}）</span>
            <span class="ts-chip"><b>${d.budget_hours ?? '—'}</b>預算 h　<span class="ts-pct" style="${_pctStyle(d.pct)}">${pct}</span></span>
            ${d.quote_days != null ? `<span class="ts-chip"><b>${d.quote_days}</b>報價人日（≈ ${d.quote_days * 8} h）</span>` : ''}
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
        <div class="ts-card" style="margin-top:14px;"><h3>時間軸（最近 90 個有紀錄的日子）</h3>
            ${d.timeline.map(day => `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #2c2c2c;font-size:12.5px;">
                <span style="width:100px;color:#888;white-space:nowrap;">${esc(_dayLabel(day.date))}</span>
                <div style="flex:1;">${day.items.map(i => `<div><span style="color:#eee;">${esc(i.name)}</span>
                    ${i.work_type ? `<span style="color:#9ca3af;">[${esc(i.work_type)}]</span>` : ''}
                    <span style="color:#bbb;">${esc(i.task || '')}</span>
                    <span style="color:${i.hours > 0 ? '#ddd' : '#93c5fd'};float:right;">${i.hours > 0 ? i.hours + ' h' : '計畫 ' + (i.planned_hours || 0) + ' h'}</span></div>`).join('')}</div>
            </div>`).join('') || '<div style="color:#666;">還沒有紀錄</div>'}
        </div>`;
}
function _renderCompare(d) {
    const cols = d.items;
    const row = (label, fn) => `<tr><th style="white-space:nowrap;">${label}</th>${cols.map(c => `<td>${fn(c)}</td>`).join('')}</tr>`;
    const types = [...new Set(cols.flatMap(c => c.composition.map(x => x[0])))];
    return `${_head('類似專案並排：總時數、跨了幾天、幾個人、分類組成、各人 —— 接類似案時的參考。')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
            <button class="ts-btn ghost" data-ts-action="view" data-view="projects">‹ 專案清單</button>
            ${_compareNames.map(n => `<span class="ts-chip" style="padding:4px 10px;">${esc(n)} <span class="ts-link" data-ts-action="compare-remove" data-name="${esc(n)}" style="margin-left:6px;">×</span></span>`).join('')}
        </div>
        <div class="ts-card" style="overflow-x:auto;"><table>
            <thead><tr><th></th>${cols.map(c => `<th><span class="ts-link" data-ts-action="open-project" data-name="${esc(c.project_name)}">${esc(c.project_name)}</span></th>`).join('')}</tr></thead>
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
            ${d.days.map(day => `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #2c2c2c;font-size:12.5px;">
                <span style="width:100px;color:#888;white-space:nowrap;">${esc(_dayLabel(day.date))}</span>
                <div style="flex:1;">${day.items.map(i => `<div>${i.work_type ? `<span style="color:#9ca3af;">[${esc(i.work_type)}]</span> ` : ''}<span style="color:#eee;">${esc(i.project_name || '(空白)')}</span>
                    <span style="color:#bbb;"> ${esc(i.task || '')}</span>
                    <span style="color:${i.hours > 0 ? '#ddd' : '#93c5fd'};float:right;">${i.hours > 0 ? i.hours + ' h' : '計畫 ' + (i.planned_hours || 0) + ' h'}</span></div>`).join('')}</div>
            </div>`).join('') || '<div style="color:#666;">這個月沒有紀錄</div>'}
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
    const loadMax = m ? Math.max(1, ...m.load.map(x => x.hours)) : 1;
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
            ${m ? card('負載（本週每人）', m.load.map(x => `<div style="display:flex;gap:8px;align-items:center;font-size:12px;margin:3px 0;">
                <span class="ts-link" data-ts-action="open-person" data-name="${esc(x.name)}" style="width:90px;">${esc(x.name)}</span>
                <div style="flex:1;height:8px;background:#2c2c2c;border-radius:4px;overflow:hidden;"><i style="display:block;height:100%;width:${Math.round(x.hours / loadMax * 100)}%;background:${x.hours > 40 ? '#f87171' : '#3b82f6'};"></i></div>
                <span style="width:50px;text-align:right;color:#ddd;">${x.hours} h</span></div>`).join('') || '<div style="color:#666;">本週還沒有人填</div>') : ''}
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
                決定一次，之後每小時同步自動吃到。「行政庶務」等內部桶留在這裡是正常的。</div>
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
            <div class="ts-note">主控端已經每小時拉，不必裝。若要改成 Sheet 端推，裝 <code>docs/appsscript/timesheet_sync.gs</code>，token 按這裡取：
                <button class="ts-btn ghost" data-ts-action="token" style="margin-left:6px;">顯示同步 Token</button>
                <span id="ts-token-slot" style="margin-left:10px;"></span></div>
        </div>`;
}

function _recentTbodyHtml() {
    return _recentSorter.sorted(_recentCache || []).map(r => `
                                    <tr><td>${esc(r.date || '')}</td><td>${esc(r.staff)}</td>
                                        <td>${esc(r.project)}</td>
                                        <td>${r.matched ? '✅' : '<span style="color:#f59e0b;">—</span>'}</td>
                                        <td style="color:#999;">${esc(r.task || '')}</td>
                                        <td class="num">${r.hours}</td></tr>`).join('');
}

// 快速補登：一位人員 + 多列（日期/專案/內容/時數）→ POST /manual
async function _renderManual(slot) {
    slot.innerHTML = '<div style="color:#777;padding:8px;">載入選項…</div>';
    try {
        const [staffD, projD] = await Promise.all([
            tfetch('/api/v1/crm/staff?status=在職'),
            tfetch('/api/v1/timesheets/project_options'),
        ]);
        const staffOpts = (staffD.staff || []).map(s =>
            `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
        const projOpts = ['<option value="">— 選專案 —</option>']
            .concat((projD.projects || []).map(p =>
                `<option value="${esc(p.name)}">${esc(p.name)}</option>`)).join('');
        const today = _today();   // 本地時區；toISOString 是 UTC 面值，會差一天
        const rowHtml = `
            <tr class="ts-mrow">
                <td><input type="date" value="${today}" data-m="date"></td>
                <td><select data-m="project">${projOpts}</select></td>
                <td><input type="text" data-m="note" placeholder="工作內容" style="width:100%;"></td>
                <td><input type="number" data-m="hours" min="0.1" step="0.1" style="width:70px;" placeholder="時數"></td>
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
 *  對映表是之後每小時同步也要吃的正本，只改列就會下一小時又冒出來。 */
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

/** 帶 token 抓 CSV 再觸發下載（<a href> 帶不了 Authorization）。 */
async function _download(path) {
    try {
        const token = localStorage.getItem('auth_token');
        const r = await fetch(path, { headers: token ? { 'Authorization': 'Bearer ' + token } : {} });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
        const blob = await r.blob();
        const cd = r.headers.get('Content-Disposition') || '';
        const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = m ? decodeURIComponent(m[1]) : 'timesheets.csv';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { alert('匯出失敗：' + (e.message || e)); }
}

async function _fillProjectDatalist() {
    const dl = document.getElementById('ts-proj-list');
    if (!dl || dl.children.length) return;
    const opts = await _projectOptions();
    dl.innerHTML = opts.map(p => `<option value="${esc(p.name)}"></option>`).join('');
}

function _bind() {
    const monthInp = document.getElementById('ts-month');
    if (monthInp) monthInp.addEventListener('change', () => { _month = monthInp.value; refresh(); });
    const dayInp = document.getElementById('ts-day');
    if (dayInp) dayInp.addEventListener('change', () => { if (dayInp.value) { _day = dayInp.value; refresh(); } });
    if (_view === 'mine') _fillProjectDatalist();

    // 時數快捷鈕：填進同一列的「實際」欄（事件委派，新增列也吃得到）
    _content.querySelectorAll('[data-hq]').forEach(btn => {
        btn.onclick = () => {
            const inp = btn.closest('td').querySelector('[data-f="hours"]');
            if (inp) inp.value = btn.dataset.hq;
        };
    });

    _content.querySelectorAll('[data-ts-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const act = btn.dataset.tsAction;
            if (act === 'refresh') return refresh();
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
            if (act === 'open-project') { _view = 'project:' + btn.dataset.name; return refresh(); }
            if (act === 'open-person') { _view = 'person:' + btn.dataset.name; return refresh(); }
            if (act === 'compare-add') {
                if (!_compareNames.includes(btn.dataset.name)) _compareNames.push(btn.dataset.name);
                return refresh();
            }
            if (act === 'compare-remove') {
                _compareNames = _compareNames.filter(n => n !== btn.dataset.name);
                if (!_compareNames.length) _view = 'projects';
                return refresh();
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
                const q = act === 'export-month' ? 'month=' + _month : 'project=' + encodeURIComponent(btn.dataset.name);
                return _download('/api/v1/timesheets/export.csv?' + q);
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
            if (act === 'row-add') {
                const tb = document.querySelector('#ts-mine-add tbody');
                if (tb) { tb.insertAdjacentHTML('beforeend', _newRowHtml()); _bind(); }
                return;
            }
            if (act === 'row-remove') { btn.closest('tr').remove(); return; }
            if (act === 'copy-yesterday') {
                const tb = document.querySelector('#ts-mine-add tbody');
                if (!tb || !_mineCache) return;
                const empty = [...tb.querySelectorAll('.ts-mine-row')].filter(tr => !tr.querySelector('[data-f="project"]').value);
                empty.forEach(tr => tr.remove());
                (_mineCache.yesterday || []).forEach(i => tb.insertAdjacentHTML('beforeend',
                    _newRowHtml({ project: i.project_name, work_type: i.work_type, note: i.task_note, planned: i.planned_hours, hours: '' })));
                _bind();
                return;
            }
            if (act === 'mine-submit') return _mineSubmit();
            if (act === 'mine-done') return _mineDone(btn.dataset.id);
            if (act === 'mine-edit') return _mineEdit(btn.dataset.id);
            if (act === 'mine-save') return _mineSave(btn.dataset.id);
            if (act === 'mine-cancel') return refresh();
            if (act === 'mine-del') return _mineDelete(btn.dataset.id);
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
                const rows = [...document.querySelectorAll('#ts-m-table .ts-mrow')].map(tr => ({
                    work_date: tr.querySelector('[data-m="date"]').value,
                    project_name: tr.querySelector('[data-m="project"]').value,
                    task_note: tr.querySelector('[data-m="note"]').value,
                    hours: parseFloat(tr.querySelector('[data-m="hours"]').value || '0'),
                })).filter(r => r.work_date && r.hours > 0);
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
        });
    });
}
