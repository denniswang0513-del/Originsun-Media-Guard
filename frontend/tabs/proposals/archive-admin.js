/**
 * archive-admin.js — 影片封存的管理卡（admin 限定；總覽獨立頁與 SPA 片庫 tab 共用）
 * ---
 * owner 要求：「管理頁面也讓我有一個地方可以填資料庫資料夾」。
 * NAS 資料夾存檔前由後端**實際驗證可寫**（建目錄 + 寫探針），不可寫回明確錯誤。
 *
 * 設定卡刻意用「儲存」按鈕不用自動儲存 —— 資料夾驗證要同步回饋，而且
 * enabled 開關是會啟動整條下載線的決定，不該在打字途中就生效。
 */

import { tfetch } from './prop-fetch.js';

const API = '/api/v1/references';
const STYLE_ID = 'arcadm-style';

const CSS = `
.arcadm { border: 1px solid var(--arc-line, #3a3a3a); border-radius: 3px; margin-bottom: 14px;
  background: var(--arc-card, #232323); color: var(--arc-ink, #e8e8e8); font-size: 12.5px; }
.arcadm summary { cursor: pointer; padding: 9px 13px; font-size: 13px; user-select: none; }
.arcadm .bd { padding: 4px 14px 13px; }
.arcadm .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.arcadm label { font-size: 11.5px; color: var(--arc-sub, #8b8b8b); white-space: nowrap; }
.arcadm input[type="text"], .arcadm input[type="number"] {
  background: var(--arc-cell, #1f1f1f); border: 1px solid var(--arc-line, #3a3a3a);
  border-radius: 3px; color: inherit; font-size: 12.5px; padding: 6px 8px; outline: none; }
.arcadm input[type="text"] { flex: 1; min-width: 280px; }
.arcadm input[type="number"] { width: 76px; }
.arcadm input:focus { border-color: var(--arc-accent, #3b82f6); }
.arcadm .chk { display: flex; gap: 6px; align-items: center; font-size: 12.5px; cursor: pointer; }
.arcadm button { background: var(--arc-accent, #1f538d); border: 0; border-radius: 3px;
  color: #fff; font-size: 12px; padding: 6px 16px; cursor: pointer; }
.arcadm .stats { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0 2px; }
.arcadm .st { font-size: 11px; padding: 3px 9px; border-radius: 2px;
  background: var(--arc-cell, #1f1f1f); border: 1px solid var(--arc-line, #3a3a3a);
  color: var(--arc-sub, #8b8b8b); }
.arcadm .st b { color: var(--arc-ink, #e8e8e8); font-weight: 600; }
.arcadm .msg { font-size: 11.5px; color: var(--arc-sub, #8b8b8b); min-height: 15px; margin-top: 6px; }
.arcadm .msg.err { color: #e05252; }
.arcadm .warn { color: #f5a524; }
`;

const STATUS_ORDER = [
    ['done', '已建檔'], ['pending', '待建檔'], ['downloading', '建檔中'],
    ['retry', '重試中'], ['unavailable', '已失效'], ['excluded', '不建檔'],
];

/** 掛管理卡。container 內渲染；非 admin 呼叫端自己別掛（後端仍會再閘）。 */
export async function mountArchiveAdmin(container) {
    if (!document.getElementById(STYLE_ID)) {
        const st = document.createElement('style');
        st.id = STYLE_ID;
        st.textContent = CSS;
        document.head.appendChild(st);
    }
    container.innerHTML = `
        <details class="arcadm">
            <summary>影片建檔設定（NAS 封存・管理員）</summary>
            <div class="bd">
                <div class="stats" data-stats>載入中…</div>
                <div class="row">
                    <label>NAS 資料夾</label>
                    <input type="text" data-k="dir" placeholder="\\\\192.168.1.132\\…\\ReferenceArchive">
                </div>
                <div class="row">
                    <label class="chk"><input type="checkbox" data-k="enabled"> 啟用自動建檔</label>
                    <label>畫質上限</label><input type="number" data-k="max_height" min="240" max="2160" step="120">
                    <label>每小時支數</label><input type="number" data-k="per_hour" min="1" max="60">
                    <label>空間上限 GB</label><input type="number" data-k="max_gb" min="1" max="5000">
                    <button data-save>儲存設定</button>
                </div>
                <div class="msg" data-msg>儲存時會實際驗證資料夾可寫。啟用後由主控端背景逐支下載（節流，整庫要消化數天屬正常）。</div>
            </div>
        </details>`;

    const $ = (sel) => container.querySelector(sel);
    const msg = $('[data-msg]');
    const say = (t, err) => { msg.textContent = t; msg.classList.toggle('err', !!err); };

    async function refresh() {
        try {
            const d = await tfetch(`${API}/archive/status`);
            const c = d.counts || {};
            const r = d.runner || {};
            const parts = STATUS_ORDER
                .filter(([k]) => c[k])
                .map(([k, label]) => `<span class="st">${label} <b>${c[k]}</b></span>`);
            if (r.used_gb != null) parts.push(`<span class="st">已用 <b>${Number(r.used_gb).toFixed(1)}</b> GB</span>`);
            if (r.busy) parts.push('<span class="st warn">建檔中…</span>');
            if (!r.ytdlp_present) parts.push('<span class="st">下載工具將於首次執行時自動安裝</span>');
            if (r.paused_reason) parts.push(`<span class="st warn">${r.paused_reason}</span>`);
            $('[data-stats]').innerHTML = parts.join('') || '<span class="st">片庫是空的</span>';
            const s = d.settings || {};
            for (const k of ['dir', 'max_height', 'per_hour', 'max_gb']) {
                const el = container.querySelector(`[data-k="${k}"]`);
                if (el && s[k] != null && document.activeElement !== el) el.value = s[k];
            }
            const en = container.querySelector('[data-k="enabled"]');
            if (en) en.checked = !!s.enabled;
        } catch (e) {
            $('[data-stats]').innerHTML = `<span class="st">狀態載入失敗：${(e.message || e)}</span>`;
        }
    }

    $('[data-save]').addEventListener('click', async (ev) => {
        ev.target.disabled = true;
        say('驗證資料夾並儲存中…');
        try {
            await tfetch(`${API}/archive/settings`, { method: 'POST', json: {
                dir: container.querySelector('[data-k="dir"]').value.trim(),
                enabled: container.querySelector('[data-k="enabled"]').checked,
                max_height: +container.querySelector('[data-k="max_height"]').value || 720,
                per_hour: +container.querySelector('[data-k="per_hour"]').value || 6,
                max_gb: +container.querySelector('[data-k="max_gb"]').value || 200,
            } });
            say('已儲存 ✓（資料夾驗證通過）');
            refresh();
        } catch (e) {
            say('儲存失敗：' + (e.message || e), true);
        } finally { ev.target.disabled = false; }
    });

    // 卡片預設收合：展開才抓狀態（別讓每個 admin 開頁都白打一次全表統計）
    container.querySelector('details').addEventListener('toggle', (e) => {
        if (e.target.open) refresh();
    });
}
