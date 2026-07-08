/**
 * timesheets.js — ⏱️ 工時檢核 Tab（N2 階段0，藍圖 §3.5/§3.6）
 *
 * MASTER 同源功能：打 /api/v1/timesheets/*（帶 auth token）。
 * 資料來源 = Google Sheet 每小時自動同步（docs/appsscript/timesheet_sync.gs）。
 * 讀取為主：專案 burn 表（已投入/預算/消耗率，顏色對齊團隊 Sheet 習慣）+
 * 未對映清單 + 最近同步列。空狀態顯示 Apps Script 安裝指引（含 token 揭示）。
 */

import { esc } from '../website/website-utils.js';

async function tfetch(path) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        headers: { 'Accept': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}) },
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.json();
}

let _content = null;

export async function initTimesheetsTab() {
    _content = document.getElementById('ts-content');
    if (!_content) return;
    await refresh();
}

async function refresh() {
    try {
        const s = await tfetch('/api/v1/timesheets/summary');
        _content.innerHTML = (s.total_rows === 0) ? _renderEmpty() : _renderBoard(s);
        _bind();
    } catch (e) {
        _content.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">
            工時資料載入失敗：${esc(e.message || e)}</div>`;
    }
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
        <h2>⏱️ 工時檢核</h2>
        <div class="ts-sub">資料來源：工時 Google Sheet 每小時自動同步（照常填表即可）</div>
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

function _renderEmpty() {
    return `
        <h2>⏱️ 工時檢核</h2>
        <div class="ts-sub">資料來源：工時 Google Sheet 自動同步</div>
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
    _content.querySelectorAll('[data-ts-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const act = btn.dataset.tsAction;
            if (act === 'refresh') return refresh();
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
