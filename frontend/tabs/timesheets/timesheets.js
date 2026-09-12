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

import { esc, debounce } from '../website/website-utils.js';
import { createSortable, sortableTh, today as _today } from '../crm/crm-utils.js';
import { openProjectPicker } from '../../js/shared/project-picker.js';   // 指定專案：共用挑選視窗
import { authDownload } from '../../js/shared/utils.js';
import { attachProjectPop } from '../../js/shared/project-pop.js';   // 專案格的浮層（進行中／已結案），零用金也用同一個
import { hbars } from '../../js/shared/svg-charts.js';
// Sheet 式格子（設定頁的快速補登 grid）與專案檔案的渲染只有 js/shared/ 那一份；「今天的專案紀錄」等四視圖
// 2026-09-12 起整組在 js/shared/ts-zone/（下面 TSZ），這裡不再自己畫格子
import { rowBody, projectFromInput, typeSelectHtml, tsFetch as tfetch } from '../../js/shared/ts-sheet.js';   // Bearer/JSON fetch 只有 ts-sheet 一份
import { isPlan as _isPlan, hoursLabel as _hoursLabel, typeTag as _typeTag, projLink as _projLink,
         srcTag as _srcTag, dayLog as _dayLog, pctStyle as _pctStyle, bars as _bars, projectFileHtml } from '../../js/shared/ts-projects.js';
// 「今天與這週」四個視圖（今天的專案紀錄／我的一週／團隊的一週／專案查詢）＝員工頁 /my.html 的同一份，
// 這裡以管理視角掛（看誰的、替人填、還沒填、週合計；docs/WORK_TRACKING_V2_PLAN.md）
import * as TSZ from '../../js/shared/ts-zone/index.js';


let _content = null;
let _view = 'zone';    // zone（員工四視圖＋管理層）| ledger | dash | settings | compare | project:<案名>
                       // （today／mine／projects／staff／person 五個舊 view 2026-09-12 拿掉：那些能力在 ts-zone）
let _zoneFirst = 'log';        // zone 裡一開始切到哪個視圖（log／plan／week／find；從別的分頁按四顆鈕回來時帶）
let _zoneWho = null;           // 管理視角「看誰的」：null＝全部；{id, name}
let _zoneManage = true;        // 管理視角開關（關掉＝以員工的角度看，紫色的東西全消失）
let _zoneMe = undefined;       // 登入者綁的人員 {id, name}｜null（沒綁）；undefined＝還沒問過
let _zonePeople = null;        // /timesheets/people（5 分鐘快取，同 _projectOptions：新建的人不用重整就出現）
let _zonePeopleAt = 0;
let _workTypes = [];   // 後端的 WORK_TYPES（/mine 與 /rows 都帶）
let _month = _today().slice(0, 7);   // YYYY-MM（本地時區；toISOString 是 UTC，1 號早上會停在上個月）
let _day = _today();                 // 設定頁「快速補登」的 rowBody 用（沒日期欄就用當天）
let _summaryCache = null;   // 最近一次 summary（供點欄頭排序重繪）
let _recentCache = null;    // 最近一次 recent rows
let _pullCache = null;      // GET /timesheets/pull（主控端定時拉 Sheet 的設定與上次結果）
let _projOpts = null;       // project_options（我的一天的專案 datalist）
let _projectCache = null;   // 最近一次 /project（專案檔案頁）
let _projectPid = '';       // 專案檔案頁：從 burn 表點進來帶的 project_id（撈整個案）；Sheet 案名進來就空
// 權限稽核第二批（2026-09-08）：/summary、/projects、/recent 對 timesheets 分頁鑰匙開放讀，但私帳對映與預算
// （指定專案／project_map／remap／建議預算／改預算／案型）、Sheet 拉取、代填、同步 token 仍是管理員——這些鈕只在 Lv3 畫。
const _isAdmin = () => (window._accessLevel || 0) >= 3;
let _compareNames = [];     // 類似專案並排：目前選的案名
let _digestCache = null;    // GET /timesheets/digest
let _ledgerCache = null;    // GET /timesheets/rows（總表：一個月所有列）
let _conflictsCache = [];   // GET /timesheets/conflicts（Sheet 與總表改過的列撞到，等 owner 選；管理員才拉）
let _monthTo = '';          // 總表：迄月（空＝只看 _month 那個月；最多 12 個月）
let _ledgerSel = new Set(); // 總表：勾選的列 id（批次調整）
const _ledgerFilter = { staff: '', project: '', source: '', q: '' };   // 總表篩選（前端做）

const _WD = ['日', '一', '二', '三', '四', '五', '六'];

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
    _content.addEventListener('change', (ev) => {
        const pk = ev.target.closest('input[data-pick]');
        if (pk) { if (pk.checked) _ledgerSel.add(pk.dataset.pick); else _ledgerSel.delete(pk.dataset.pick); _ledgerRedraw(); }
        const pa = ev.target.closest('input[data-pick-all]');
        if (pa) { _ledgerSel = pa.checked ? new Set(_ledgerRows().map(i => i.id)) : new Set(); _ledgerRedraw(); }
    });
    // 專案格浮層掛在整個 tab（總表的批次列、改列、我的一天的格子都吃）：capture，開著時 ↓↑ 歸浮層，
    // 關著時才輪到 ts-sheet 的換列。格子只放案名（整串太長），id 記在 data-pid。
    // ts-zone 的格子不再自己掛一份（hooks.projectPicker: null）—— 同一個 input 掛兩個 root 會讓 ↓↑ 走兩格。
    attachProjectPop(_content, { options: _projectOptions, value: (p) => (p.id ? (p.name || p.label) : (p.label || p.name)) });
    await refresh();
}

async function refresh() {
    try {
        if (_view === 'zone') {
            await _mountZone();
            return;
        }
        if (_view === 'ledger') {
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
        } else {
            _view = 'zone';          // 認不得的 view（舊書籤／舊分頁）一律回到員工四視圖
            await _mountZone();
            return;
        }
        _bind();
        // 各表點欄頭排序（attach 對不存在的表是 no-op）
        _unmatchedSorter.attach();
    } catch (e) {
        _content.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">
            工時資料載入失敗：${esc(e.message || e)}</div>`;
    }
}

// 分頁鈕列：左邊四顆＝員工的四個視圖（zone），右邊＝管理次級（總表／儀表板／設定）。新元素純文字，無 emoji。
// zone 掛著時這一列就是 ts-zone 的 .views（四顆用 data-view 給 ts-zone 切）；在總表那些分頁時四顆改帶 data-ts-action="zone"
// （按了回 zone 並切到那個視圖）—— 同一顆鈕不能兩個都帶，不然 ts-zone 切完、外層又把整塊重畫一次。
function _viewBtns(extra = '') {
    const inZone = _view === 'zone';
    const v = (key, label) => inZone
        ? `<button type="button" class="view-btn" data-view="${key}">${label}</button>`
        : `<button type="button" class="view-btn" data-ts-action="zone" data-view="${key}">${label}</button>`;
    const m = (key, label) => `<button type="button" class="view-btn sub ${_view === key ? 'active' : ''}" data-ts-action="view" data-view="${key}">${label}</button>`;
    return `<div class="views" id="ts-views">
        ${v('log', '今天的專案紀錄')}${v('plan', '我的一週')}${v('week', '團隊的一週')}${v('find', '專案查詢')}
        <span class="gap"></span>${extra}${m('ledger', '總表')}${m('dash', '儀表板')}${_isAdmin() ? m('settings', '設定') : ''}
    </div>`;
}

// ── zone：員工四視圖＋管理層（js/shared/ts-zone；docs/WORK_TRACKING_V2_PLAN.md §3–4）──
async function _zoneIdentity() {
    // 登入者綁的人員（看誰的＝自己時走 own-scope，跟員工頁一模一樣）＋ 人員清單（切換器、未填點名）
    if (!_zonePeople || Date.now() - _zonePeopleAt > 5 * 60 * 1000) {
        try { _zonePeople = (await tfetch('/api/v1/timesheets/people')).people || []; _zonePeopleAt = Date.now(); } catch (_) { _zonePeople = _zonePeople || []; }
    }
    if (_zoneMe === undefined) {
        // 綁定人員從登入時的 /auth/me 拿（window._authUser.staff_id），不另打 /timesheets/mine（沒綁的管理員會收一顆 409）
        const sid = (window._authUser || {}).staff_id || '';
        const hit = sid ? _zonePeople.find(p => p.id === sid) : null;
        _zoneMe = hit ? { id: hit.id, name: hit.name } : null;
    }
}
function _whoSelectHtml() {
    const opt = (val, label) => `<option value="${esc(val)}" ${(_zoneWho ? _zoneWho.id : '') === val ? 'selected' : ''}>${esc(label)}</option>`;
    const people = _zonePeople || [];
    // data-no-search：SPA 會把 select 升級成 searchable 小工具，但那個小工具不會跟著程式改值（點「替他填」／「還沒填」
    // 之後顯示的還是上一個名字）；人員只有十來個，原生 select 就好
    return `<label class="who">看誰的<select id="ts-zone-who" data-no-search>${opt('', '全部（團隊）')}${people.map(p => opt(p.id, p.name + (_zoneMe && p.id === _zoneMe.id ? '（我）' : (p.status === '兼職' ? '（兼職）' : '')))).join('')}</select></label>
        <label class="sw" id="ts-zone-sw" title="關掉＝以員工的角度看（管理的東西全部消失）"><span>管理視角</span><i></i></label>`;
}
async function _mountZone() {
    await _zoneIdentity();
    _content.innerHTML = `<h2>工作追蹤</h2><div class="ts-sub">員工的四個視圖，加上管理看得到的：看誰的、替他填、還沒填、週合計。切「看誰的」到自己＝跟員工頁一模一樣。
        員工端的團隊月表在 <a href="/hours.html" target="_blank" class="ts-link">/hours.html</a>。</div>
        <div class="ts-zone${_zoneManage ? '' : ' emp'}" id="ts-zone">${_viewBtns(_whoSelectHtml())}
            <div class="view" data-view="log" id="z1-log"></div>
            <div class="view" data-view="plan" id="z1-plan"></div>
            <div class="view" data-view="week" id="z1-week"></div>
            <div class="view" data-view="find" id="z1-find"></div></div>`;
    const host = document.getElementById('ts-zone');
    TSZ.mountZone({
        host, esc, mjson: tfetch, today: _today,          // ts-zone 的 body 是物件，tsFetch 正好收物件
        can: () => true,                        // timesheets 鑰匙／管理員整區都開（後端各端點自己守）
        first: _zoneFirst,
        manage: _zoneManage, who: _zoneManage ? _zoneWho : (_zoneMe || null), me: _zoneMe, people: _zonePeople,
        hooks: {
            modalRoot: () => document.getElementById('ts-root') || document.body,
            journalHref: '',                    // CRM 分頁沒有週記卡
            // 這個 tab 自己收的鈕：鈕列右邊那幾顆、從別的分頁按回來的四顆、專案檔案的匯出 CSV（用 tab 既有的 authDownload 那條）
            passthrough: (b) => ['view', 'zone', 'export-project', 'export-month'].includes(b.dataset.tsAction),
            projectPicker: null,                // 整個 tab 已掛一份專案浮層（initTimesheetsTab），不再掛
            isAdmin: _isAdmin,                  // 改預算／指定／套用建議是寫私帳：只給 Lv3（後端 _require_mine_admin）
            onCompare: (names) => { _compareNames = names; },   // 「加入比較」→ 並排比較頁（_view='compare'）吃同一份清單
            onView: (v) => { _zoneFirst = v; },
            onWho: (who) => { _zoneWho = who; const sel = document.getElementById('ts-zone-who'); if (sel) sel.value = who ? who.id : ''; },
        },
    });
    document.getElementById('ts-zone-who')?.addEventListener('change', (e) => {
        const p = (_zonePeople || []).find(x => x.id === e.target.value);
        TSZ.setWho(p ? { id: p.id, name: p.name } : null);
    });
    document.getElementById('ts-zone-sw')?.addEventListener('click', () => { _zoneManage = !_zoneManage; refresh(); });
}


function _head(sub) {
    // 鈕列的樣式住在 ts-zone.css（#ts-root .ts-zone .views）：不在 zone 裡的分頁也套同一個殼，四顆鈕才長一樣
    return `<h2>工作追蹤</h2><div class="ts-sub">${sub}</div><div class="ts-zone bar">${_viewBtns()}</div>`;
}

// ── 一列的共用片段（_isPlan／_hoursLabel／_typeTag／_projLink／_srcTag／_dayLog）在 js/shared/ts-projects.js ──

// ── 今日：每日看板（每個人每天做了什麼；實際為主，計畫加分；不排名、不標紅）──
function _itemCard(i) {
    return `<div style="padding:6px 8px;border:1px solid #333;border-radius:6px;margin:4px 0;background:${_isPlan(i) ? '#1a2233' : '#1f1f1f'};">
        <div style="color:#ddd;font-size:12.5px;">${_typeTag(i)}${_projLink(i.project_name, i.project_id)}</div>
        ${i.task_note ? `<div style="color:#999;font-size:11.5px;">${esc(i.task_note)}</div>` : ''}
        ${i.remark ? `<div style="color:#777;font-size:11px;">備註：${esc(i.remark)}</div>` : ''}
        <div style="font-size:11.5px;margin-top:2px;">${_hoursLabel(i)}${_srcTag(i)}</div>
    </div>`;
}

// ── 我的一天：登入者自己記（實際或計畫）；時數快捷鈕；複製昨天；改／刪 ──
let _projOptsAt = 0;
const _ledgerRedrawQ = debounce(() => _ledgerRedraw(), 200);
async function _projectOptions() {
    if (_projOpts && Date.now() - _projOptsAt < 5 * 60 * 1000) return _projOpts;   // 5 分鐘內用快取；同事新建的案之後打得到
    try { _projOpts = (await tfetch('/api/v1/timesheets/project_options')).projects || []; _projOptsAt = Date.now(); }
    catch (_) { _projOpts = _projOpts || []; }
    return _projOpts;
}
const _typeSelect = (cur, attr) => typeSelectHtml(cur, attr, _workTypes);
/** 一個工作項的五格輸入（專案／分類／內容／計畫／實際）；總表改列用。 */
function _rowCells(v = {}) {
    return `<td><input data-proj-pick autocomplete="off" data-f="project" value="${esc(v.project || '')}" placeholder="專案（可打字）" style="width:100%;"></td>
        <td>${_typeSelect(v.work_type || '', 'data-f="type"')}</td>
        <td><input type="text" data-f="note" value="${esc(v.note || '')}" placeholder="做了什麼" style="width:100%;"></td>
        <td><input type="text" data-f="remark" value="${esc(v.remark || '')}" placeholder="備註" style="width:100%;"></td>
        <td><input type="number" data-f="planned" min="0" step="0.25" value="${v.planned ?? ''}" placeholder="計畫" style="width:64px;"></td>
        <td><input type="number" data-f="hours" min="0" step="any" value="${v.hours ?? ''}" placeholder="實際" style="width:64px;"></td>`;
}
// ── 我的一天的新增區：像 Google Sheet 的格子（owner 2026-09-03）——
/** 一列輸入 → 送給後端的 body（總表改列、代填同一形狀；沒日期欄就用當天）。正本在 ts-sheet.rowBody。 */
const _rowBody = (tr) => rowBody(tr, { day: _day, projects: _projOpts || [] });
const _projectFromInput = (text, el = null) => projectFromInput(text, el, _projOpts || []);
/** 工作階段設定（同 /my.html 那顆鈕）：改完把新清單餵給格子，新列的下拉立即更新。 */
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
        <td></td>
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

// ── 專案檔案頁：摘要／分類組成／時間軸／各人各月／預算／報價人日／類似專案（渲染在 ts-projects.projectFileHtml）──
function _renderProject(d, modal = false) {
    return projectFileHtml(d, {
        modal, editable: true, budgetEditable: _isAdmin(), compareNames: _compareNames,
        head: _head('專案檔案：這個案的整個執行狀態 —— 誰在哪天做了什麼、花了多少、跟類似的案比起來如何。'),
        backHtml: '<button class="ts-btn ghost" data-ts-action="view" data-view="projects">‹ 專案清單</button>',
    });
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
                        <td>${u.reason === 'bucket' || !_isAdmin() ? '' : `
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
    return `${_head('設定：工作階段、Sheet 拉取、未對映指定、代填、週一 digest、同步 token（管理員）')}
        ${_pullBar()}
        <div class="ts-card">
            <h3>工作階段</h3>
            <div class="ts-note" style="margin:0 0 8px;">每個分類（剪接、拍攝、前期企劃…）自己的階段清單；改名、上下排序、新增、停用不刪。「我的一天」與員工工作台的格子都用這份。</div>
            <button class="ts-btn ghost" data-ts-action="stages">編輯工作階段</button>
        </div>
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
                ${_isAdmin() ? `<button class="ts-btn ghost" data-ts-action="token" style="margin-left:6px;">顯示同步 Token</button>` : ''}
                <span id="ts-token-slot" style="margin-left:10px;"></span></div>
        </div>`;
}

function _recentTbodyHtml() {
    return _recentSorter.sorted(_recentCache || []).map(r => `
                                    <tr><td>${esc(r.date || '')}</td><td>${esc(r.staff_name)}</td>
                                        <td>${esc(r.project_name)}</td>
                                        <td>${r.project_id ? '<span style="color:#6ee7b7;">對到</span>' : '<span style="color:#f59e0b;">—</span>'}</td>
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
    if (_view === 'ledger') _projectOptions();     // 先抓好，專案格一點就有得選
    const mt = document.getElementById('ts-month-to');
    if (mt) mt.addEventListener('change', () => { _monthTo = mt.value; refresh(); });
    ['staff', 'project', 'source', 'q'].forEach(k => {
        const el = document.getElementById('ts-lf-' + k);
        if (el) el.addEventListener(k === 'q' ? 'input' : 'change', () => {
            _ledgerFilter[k] = el.value;
            (k === 'q' ? _ledgerRedrawQ : _ledgerRedraw)();      // 打字去抖：整表重畫一次幾百毫秒
        });
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
            if (act === 'zone') { _view = 'zone'; _zoneFirst = btn.dataset.view || 'log'; return refresh(); }
            if (act === 'proj-pop') return _openProjectModal(btn.dataset.name, btn.dataset.pid);
            if (act === 'proj-pop-close') { document.getElementById('ts-proj-modal')?.remove(); return; }
            if (act === 'open-project') {
                // 帶 pid＝撈整個案（所有對到它的 Sheet 案名）；比較清單的 id:<pid> 鍵也走這裡
                const key = btn.dataset.name || '';
                _projectPid = btn.dataset.pid || (key.startsWith('id:') ? key.slice(3) : '');
                _view = 'project:' + (key.startsWith('id:') ? '' : key);
                return refresh();
            }
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
            if (act === 'batch-clear') { _ledgerSel = new Set(); return _ledgerRedraw(); }
            if (act === 'batch-apply') {
                const g = id => (document.getElementById(id) || {}).value || '';
                const body = { ids: [..._ledgerSel] };
                const proj = g('ts-batch-project').trim();
                if (proj) Object.assign(body, _projectFromInput(proj, document.getElementById('ts-batch-project')));
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
