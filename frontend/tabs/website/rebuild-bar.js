/**
 * rebuild-bar.js — 對外網站 rebuild 狀態列（網站 Tab 頂部，跨 subview 共用）
 *
 * 顯示：
 *   - 上次發布時間（相對 / 絕對切換）
 *   - 待發布變動數
 *   - 自動 rebuild 倒數（debounce 期間）
 *   - state badge（idle / running / error）
 *   - 立即重建按鈕
 *
 * 機制：
 *   - 進 Tab 時 init 一次，啟 polling /api/website/admin/rebuild/status
 *   - polling 間隔依 state 動態調整：running/debounce 5s、idle/success 30s
 *   - 多人協作 debouncer 在 server 端，前端純顯示
 */
import { websiteFetch, esc, fmtRelative, toastOk, toastErr } from './website-utils.js';

const POLL_INTERVAL_FAST = 5000;   // running / debounce 倒數中
const POLL_INTERVAL_SLOW = 30000;  // idle

let _poll = null;
let _state = null;  // 最後一次 status 回應

export function initRebuildBar(host) {
    if (!host) return;
    host.id = 'website-rebuild-bar';
    host.style.cssText = 'padding:10px 14px;background:#222;border-bottom:1px solid #3a3a3a;font-size:12px;color:#ccc;display:flex;align-items:center;gap:14px;flex-wrap:wrap;';
    host.innerHTML = `<span style="color:#888;">對外網站發布狀態：載入中…</span>`;
    _refresh(host);
    _scheduleNext(host, POLL_INTERVAL_FAST);
}

export function destroyRebuildBar() {
    if (_poll) { clearTimeout(_poll); _poll = null; }
    _state = null;
}

function _scheduleNext(host, ms) {
    if (_poll) clearTimeout(_poll);
    _poll = setTimeout(() => _refresh(host), ms);
}

async function _refresh(host) {
    try {
        const s = await websiteFetch('/api/website/admin/rebuild/status');
        _state = s;
        host.innerHTML = _renderBar(s);
        _bindActions(host);
        const fast = (s.state === 'running') || (s.auto_rebuild_fires_at);
        _scheduleNext(host, fast ? POLL_INTERVAL_FAST : POLL_INTERVAL_SLOW);
    } catch (e) {
        host.innerHTML = `<span style="color:#f87171;">⚠ rebuild 狀態查詢失敗：${esc(e.message || e)}</span>`;
        _scheduleNext(host, POLL_INTERVAL_SLOW);
    }
}

function _renderBar(s) {
    const stateLabel = {
        idle: { txt: '✓ 已同步', color: '#10b981' },
        running: { txt: '⟳ 發布中…', color: '#3b82f6' },
        success: { txt: '✓ 已發布', color: '#10b981' },
        error: { txt: '✗ 發布失敗', color: '#ef4444' },
    }[s.state || 'idle'] || { txt: s.state, color: '#888' };

    const last = s.last_success_at
        ? `上次發布：${fmtRelative(new Date(s.last_success_at * 1000).toISOString())}`
        : '尚未發布過';

    const pending = (s.pending_count || 0) > 0
        ? `<span style="color:#f59e0b;font-weight:600;">${s.pending_count} 筆未發布變動</span>`
        : `<span style="color:#666;">無待發布變動</span>`;

    let countdown = '';
    if (s.auto_rebuild_fires_at && s.state !== 'running') {
        const sec = Math.max(0, Math.round(s.auto_rebuild_fires_at - Date.now() / 1000));
        countdown = `<span style="color:#888;">· ${sec}s 後自動發布</span>`;
    }

    const btn = s.state === 'running'
        ? `<button class="btn btn-sm" disabled style="opacity:0.6;cursor:not-allowed;">發布中…</button>`
        : `<button class="btn btn-sm" data-action="rebuild-now" title="跳過倒數立即發布">立即發布</button>`;

    const errMsg = s.state === 'error' && s.error
        ? `<div style="color:#fca5a5;font-size:11px;width:100%;margin-top:6px;">${esc(s.error)}</div>`
        : '';

    return `
        <span style="color:${stateLabel.color};font-weight:600;">${stateLabel.txt}</span>
        <span style="color:#666;">|</span>
        <span>${esc(last)}</span>
        <span style="color:#666;">|</span>
        ${pending}
        ${countdown}
        <span style="margin-left:auto;">${btn}</span>
        ${errMsg}
    `;
}

function _bindActions(host) {
    host.querySelector('[data-action="rebuild-now"]')?.addEventListener('click', async () => {
        try {
            const r = await websiteFetch('/api/website/admin/rebuild', { method: 'POST' });
            if (r.queued === false) {
                toastErr(r.reason || '已有發布在跑');
            } else {
                toastOk('發布已排入');
            }
            _refresh(host);
        } catch (e) {
            toastErr('觸發失敗：' + (e.message || e));
        }
    });
}
