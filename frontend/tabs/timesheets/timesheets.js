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
let _month = new Date().toISOString().slice(0, 7);     // YYYY-MM

export async function initTimesheetsTab() {
    _content = document.getElementById('ts-content');
    if (!_content) return;
    await refresh();
}

async function refresh() {
    try {
        if (_view === 'staff') {
            const d = await tfetch('/api/v1/timesheets/by_staff?month=' + _month);
            _content.innerHTML = _renderStaffView(d);
        } else {
            const s = await tfetch('/api/v1/timesheets/summary');
            _content.innerHTML = (s.total_rows === 0) ? _renderEmpty() : _renderBoard(s);
        }
        _bind();
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

function _renderBoard(s) {
    const projRows = s.projects.map(p => `
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

    const unmatchedCard = s.unmatched.length ? `
        <div class="ts-card">
            <h3>🔗 未對映專案（${s.unmatched.length}）</h3>
            <table>
                <thead><tr><th>Sheet 專案名</th><th class="num">時數</th><th class="num">列數</th></tr></thead>
                <tbody>${s.unmatched.map(u => `
                    <tr><td>${esc(u.project_name)}</td>
                        <td class="num">${u.hours_used}</td>
                        <td class="num">${u.rows}</td></tr>`).join('')}
                </tbody>
            </table>
            <div class="ts-note">名稱與 CRM 專案完全一致即自動對映（下次同步生效）。
                「行政庶務」等內部桶留在這裡是正常的。</div>
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
            <table>
                <thead><tr>
                    <th>專案</th><th>狀態</th><th class="num">已投入(h)</th><th class="num">預算(h)</th>
                    <th class="num">剩餘(h)</th><th class="num">消耗率</th><th class="num">列數</th><th>最後填報</th>
                </tr></thead>
                <tbody>${projRows || '<tr><td colspan="8" style="color:#666;text-align:center;">尚無已對映專案</td></tr>'}</tbody>
            </table>
        </div>
        ${unmatchedCard}
        <div id="ts-recent-slot"></div>`;
}

// 人員月視圖：每人 × 每專案 時數（人事管理視角）
function _renderStaffView(d) {
    const staffBlocks = d.staff.map(s => `
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
            <table>
                <thead><tr><th>人員 / 專案</th><th class="num">時數(h)</th><th class="num"></th></tr></thead>
                <tbody>${staffBlocks || '<tr><td colspan="3" style="color:#666;text-align:center;">本月尚無工時資料</td></tr>'}</tbody>
            </table>
        </div>`;
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
        const today = new Date().toISOString().slice(0, 10);
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
                <li>改頂部 CONFIG：分頁名稱、欄位位置（日期/員工/專案/內容/時數/預算是第幾欄）、
                    TOKEN 按下面按鈕取得</li>
                <li>執行一次 <code>syncNewRows</code>（首次會要求授權）→ 紀錄顯示 inserted 即成功</li>
                <li>觸發條件 → 新增 → <code>syncNewRows</code> → 時間驅動 → 每小時</li>
            </ol>
            <button class="ts-btn" data-ts-action="token">🔑 顯示同步 Token</button>
            <span id="ts-token-slot" style="margin-left:10px;"></span>
            <div class="ts-note">裝好後第一次執行會把歷史列全部匯入（後端自動去重，重跑安全）。
                團隊照常填 Sheet，這頁的數字每小時自動更新。</div>
        </div>`;
}

function _bind() {
    const monthInp = document.getElementById('ts-month');
    if (monthInp) monthInp.addEventListener('change', () => { _month = monthInp.value; refresh(); });

    _content.querySelectorAll('[data-ts-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const act = btn.dataset.tsAction;
            if (act === 'refresh') return refresh();
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
                    slot.innerHTML = `
                        <div class="ts-card">
                            <h3>🔍 最近同步 50 列</h3>
                            <table>
                                <thead><tr><th>日期</th><th>員工</th><th>專案</th><th>對映</th><th>內容</th><th class="num">時數</th></tr></thead>
                                <tbody>${d.rows.map(r => `
                                    <tr><td>${esc(r.date || '')}</td><td>${esc(r.staff)}</td>
                                        <td>${esc(r.project)}</td>
                                        <td>${r.matched ? '✅' : '<span style="color:#f59e0b;">—</span>'}</td>
                                        <td style="color:#999;">${esc(r.task || '')}</td>
                                        <td class="num">${r.hours}</td></tr>`).join('')}
                                </tbody>
                            </table>
                        </div>`;
                } catch (e) {
                    slot.innerHTML = `<div style="color:#f87171;padding:8px;">載入失敗：${esc(e.message || e)}</div>`;
                }
            }
        });
    });
}
