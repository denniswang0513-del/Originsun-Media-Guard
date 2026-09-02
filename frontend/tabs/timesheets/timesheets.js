/**
 * timesheets.js — 專案工時 Tab（人事管理；N2 階段0 + 手填，藍圖 §3.5/§3.6）
 *
 * MASTER 同源功能：打 /api/v1/timesheets/*（帶 auth token）。
 * 資料來源 = Google Sheet 每小時自動同步 + 系統內快速補登（source=manual），
 * 兩源共存；同 (人,日,專案) 手填優先於 Sheet（ingest 端擋）。
 * 視圖：專案分析（burn 表，預設 — 與專案聯動的工作狀態分析）/ 人員月視圖。
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
let _view = 'board';                                   // board（專案分析）| staff（人員月視圖）
let _month = _today().slice(0, 7);   // YYYY-MM（本地時區；toISOString 是 UTC，1 號早上會停在上個月）
let _summaryCache = null;   // 最近一次 summary（供點欄頭排序重繪）
let _staffCache = null;     // 最近一次 by_staff
let _recentCache = null;    // 最近一次 recent rows

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
        matched: r => (r.matched ? 1 : 0),
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
        if (_view === 'staff') {
            const d = await tfetch('/api/v1/timesheets/by_staff?month=' + _month);
            _staffCache = d;
            _content.innerHTML = _renderStaffView(d);
        } else {
            const s = await tfetch('/api/v1/timesheets/summary');
            _summaryCache = s;
            _content.innerHTML = (s.total_rows === 0) ? _renderEmpty() : _renderBoard(s);
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

// 視圖切換列（純文字，無 emoji）
function _viewBtns() {
    const b = (key, label) => `<button class="ts-btn ${_view === key ? '' : 'ghost'}"
        data-ts-action="view" data-view="${key}">${label}</button>`;
    return `<div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;">
        ${b('board', '專案分析')}${b('staff', '人員月視圖')}
        <button class="ts-btn ghost" data-ts-action="toggle-manual">快速補登</button>
    </div>
    <div id="ts-manual-slot" style="display:none;"></div>`;
}

// 消耗率配色：對齊團隊 Sheet 的綠→紅直覺
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
            <td>${esc(p.project_name || p.project_id)}</td>
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

// 未對映的原因（後端 resolver 回的；規則正本 core.hr_logic.resolve_project）
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

function _renderBoard(s) {
    const unmatchedCard = s.unmatched.length ? `
        <div class="ts-card">
            <h3>🔗 未對映專案（${s.unmatched.length}）</h3>
            <table id="ts-unmatched-table">
                <thead><tr>${sortableTh('name', 'Sheet 專案名')}${sortableTh('hours', '時數', 'class="num"')}${sortableTh('rows', '列數', 'class="num"')}<th>原因</th><th></th></tr></thead>
                <tbody>${_unmatchedTbodyHtml()}</tbody>
            </table>
            <div class="ts-note">去掉「客戶_」前綴後與私帳案名相同即自動對映；撞案（同名兩案）與找不到的按「指定專案」
                決定一次，之後每小時同步自動吃到。「行政庶務」等內部桶留在這裡是正常的。</div>
        </div>` : '';

    const totalHours = s.projects.reduce((a, p) => a + (p.hours_used || 0), 0)
        + s.unmatched.reduce((a, u) => a + (u.hours_used || 0), 0);

    return `
        <h2>專案工時</h2>
        <div class="ts-sub">資料來源：工時 Google Sheet 每小時自動同步 + 系統內快速補登（同人同日同案手填優先）</div>
        ${_viewBtns()}
        <div style="margin-bottom:12px;">
            <span class="ts-chip"><b>${s.total_rows}</b>總列數</span>
            <span class="ts-chip"><b>${s.projects.length}</b>已對映專案</span>
            <span class="ts-chip"><b>${s.unmatched.length}</b>未對映</span>
            <span class="ts-chip"><b>${Math.round(totalHours)}</b>總時數</span>
            <button class="ts-btn ghost" data-ts-action="refresh" style="vertical-align:top;">↻ 重新整理</button>
            <button class="ts-btn ghost" data-ts-action="recent" style="vertical-align:top;">🔍 最近同步列</button>
        </div>
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
        ${unmatchedCard}
        <div id="ts-recent-slot"></div>`;
}

// 人員月視圖：每人 × 每專案 時數（人事管理視角）
// 排序作用在「人員區塊」層級（人列 + 其專案子列一起移動，子列維持原順序）
function _staffTbodyHtml() {
    const staffBlocks = _staffSorter.sorted((_staffCache && _staffCache.staff) || []).map(s => `
        <tr style="background:#262626;">
            <td style="color:#eee;font-weight:600;">${esc(s.name)}</td>
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
    return `
        <h2>專案工時</h2>
        <div class="ts-sub">人員月視圖：每人投入的專案時數（含 Sheet 同步與手填）</div>
        ${_viewBtns()}
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
        <div class="ts-card" style="border-color:#3b82f6;">
            <h3>快速補登</h3>
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
            </div>
        </div>`;
        slot.dataset.rowTemplate = rowHtml;
    } catch (e) {
        slot.innerHTML = `<div style="color:#f87171;padding:8px;">選項載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _renderEmpty() {
    return `
        <h2>專案工時</h2>
        <div class="ts-sub">資料來源：工時 Google Sheet 自動同步 + 系統內快速補登</div>
        ${_viewBtns()}
        <div class="ts-card" style="max-width:720px;">
            <h3>🚀 尚無資料 — 把同步腳本裝進 Google Sheet（約 5 分鐘，一次性）</h3>
            <ol>
                <li>打開工時試算表 → <b>擴充功能 → Apps Script</b></li>
                <li>貼上 repo 裡 <code>docs/appsscript/timesheet_sync.gs</code> 的全部內容</li>
                <li>改頂部 CONFIG.TOKEN（按下面按鈕取得）—— 分頁名與欄位已照工時表設好</li>
                <li>歷史列由 <code>scripts/import_timesheets.py</code> 一次匯入；裝腳本前先執行一次
                    <code>executeSetMarkerToEnd</code>，之後只送新列</li>
                <li>執行一次 <code>syncNewRows</code>（首次會要求授權）→ 紀錄顯示 inserted 即成功</li>
                <li>觸發條件 → 新增 → <code>syncNewRows</code> → 時間驅動 → 每小時</li>
            </ol>
            <button class="ts-btn" data-ts-action="token">🔑 顯示同步 Token</button>
            <span id="ts-token-slot" style="margin-left:10px;"></span>
            <div class="ts-note">裝好後第一次執行會把歷史列全部匯入（後端自動去重，重跑安全）。
                團隊照常填 Sheet，這頁的數字每小時自動更新。</div>
        </div>`;
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

function _bind() {
    const monthInp = document.getElementById('ts-month');
    if (monthInp) monthInp.addEventListener('change', () => { _month = monthInp.value; refresh(); });

    _content.querySelectorAll('[data-ts-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const act = btn.dataset.tsAction;
            if (act === 'refresh') return refresh();
            if (act === 'map') return _mapProject(btn.dataset.name);
            if (act === 'view') { _view = btn.dataset.view; return refresh(); }
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
                            <h3>🔍 最近同步 50 列</h3>
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
