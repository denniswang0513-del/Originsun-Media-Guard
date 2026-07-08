/**
 * rebuild-bar.js — 對外網站 rebuild 狀態 widget（網站 Tab 左側欄底部）
 *
 * 顯示：
 *   - state badge（idle / running / error）
 *   - 上次發布時間（相對）
 *   - 待發布變動數 / 自動 rebuild 倒數
 *   - 立即發布按鈕
 *
 * 機制：
 *   - 進 Tab 時 init 一次，啟 polling /api/website/admin/rebuild/status
 *   - polling 間隔依 state 動態調整：running/debounce 5s、idle/success 30s
 *   - 多人協作 debouncer 在 server 端，前端純顯示
 *
 * 版面：垂直緊湊（適合 200px 寬側邊欄），跟既有 API/狀態 區塊風格一致。
 */
import { websiteFetch, esc, fmtRelative, toastOk, toastErr } from './website-utils.js';

const POLL_INTERVAL_FAST = 5000;   // running / debounce 倒數中
const POLL_INTERVAL_SLOW = 30000;  // idle
const FAIL_THRESHOLD = 3;          // 連續失敗達此數才整塊變紅（單次瞬斷不嚇人）

let _poll = null;
let _host = null;
let _failCount = 0;
let _lastGoodHtml = '';
let _visListener = null;

export function initRebuildBar(host) {
    if (!host) return;
    _host = host;
    _failCount = 0;
    _lastGoodHtml = '';
    host.id = 'website-rebuild-bar';
    host.style.cssText = 'padding:12px 16px;border-top:1px solid #2a2a2a;font-size:11px;color:#999;line-height:1.6;';
    host.innerHTML = `<div style="color:#666;">對外網站發布狀態：載入中…</div>`;
    // 背景分頁的 setTimeout 會被 Chrome 節流到 ~10 分鐘一次；切回前景立即刷新，
    // 否則使用者看到的是最舊可達 10 分鐘前的狀態（含早已恢復的瞬斷錯誤）。
    if (!_visListener) {
        _visListener = () => {
            if (document.visibilityState === 'visible' && _host) _refresh(_host);
        };
        document.addEventListener('visibilitychange', _visListener);
    }
    _refresh(host);
}

export function destroyRebuildBar() {
    if (_poll) { clearTimeout(_poll); _poll = null; }
    if (_visListener) { document.removeEventListener('visibilitychange', _visListener); _visListener = null; }
    _host = null;
}

function _scheduleNext(host, ms) {
    if (_poll) clearTimeout(_poll);
    _poll = setTimeout(() => _refresh(host), ms);
}

async function _refresh(host) {
    try {
        const s = await websiteFetch('/api/website/admin/rebuild/status');
        _failCount = 0;
        host.innerHTML = _renderSidebar(s);
        _lastGoodHtml = host.innerHTML;
        _bindActions(host);
        const fast = (s.state === 'running') || (s.auto_rebuild_fires_at);
        _scheduleNext(host, fast ? POLL_INTERVAL_FAST : POLL_INTERVAL_SLOW);
    } catch (e) {
        _failCount++;
        if (_failCount >= FAIL_THRESHOLD) {
            host.innerHTML = `<div style="color:#f87171;font-size:11px;">⚠ 發布狀態查詢失敗（連續 ${_failCount} 次）</div>
                <div style="color:#666;font-size:10px;margin-top:2px;">${esc(e.message || e)}</div>`;
        } else if (_lastGoodHtml) {
            // 單次瞬斷：保留上次成功畫面，只加一行小字，不整塊變紅
            host.innerHTML = _lastGoodHtml
                + `<div style="color:#f59e0b;font-size:10px;margin-top:4px;">⚠ 狀態查詢暫時失敗，重試中…</div>`;
            _bindActions(host);
        } else {
            // 頁面剛載入的第一發常死在瀏覽器→CF edge 的新連線建立（QUIC 退回 TCP），
            // 重試幾乎必成 — 沒到閾值前維持中性文案，不用紅字嚇人。
            host.innerHTML = `<div style="color:#888;">對外網站發布狀態：連線中（重試 ${_failCount}/${FAIL_THRESHOLD}）…</div>`;
        }
        // 首次失敗 1.5s 就補打（連線層 flake 退回 TCP 後立即可用），之後 5s
        _scheduleNext(host, _failCount === 1 ? 1500 : POLL_INTERVAL_FAST);
    }
}

function _renderSidebar(s) {
    const stateLabel = {
        idle: { txt: '✓ 已同步', color: '#4ade80' },
        running: { txt: '⟳ 發布中…', color: '#60a5fa' },
        success: { txt: '✓ 已發布', color: '#4ade80' },
        error: { txt: '✗ 發布失敗', color: '#f87171' },
    }[s.state || 'idle'] || { txt: s.state, color: '#888' };

    const last = s.last_success_at
        ? fmtRelative(new Date(s.last_success_at * 1000).toISOString())
        : '尚未發布過';

    const pendingLine = (s.pending_count || 0) > 0
        ? `<div style="color:#f59e0b;">📝 ${s.pending_count} 筆未發布</div>`
        : `<div style="color:#666;">無待發布變動</div>`;

    let countdownLine = '';
    if (s.auto_rebuild_fires_at && s.state !== 'running') {
        const sec = Math.max(0, Math.round(s.auto_rebuild_fires_at - Date.now() / 1000));
        countdownLine = `<div style="color:#888;font-size:10px;">${sec}s 後自動發布</div>`;
    }

    const btn = s.state === 'running'
        ? `<button class="btn btn-sm" disabled style="width:100%;opacity:0.6;cursor:not-allowed;">發布中…</button>`
        : `<button class="btn btn-sm" data-action="rebuild-now" title="跳過倒數立即發布" style="width:100%;">立即發布</button>`;

    const errMsg = s.state === 'error' && s.error
        ? `<div style="color:#fca5a5;font-size:10px;margin-top:6px;word-break:break-all;">${esc(s.error)}</div>`
        : '';

    return `
        <div style="margin-bottom:6px;">對外網站發布</div>
        <div style="color:${stateLabel.color};font-weight:600;margin-bottom:4px;">${stateLabel.txt}</div>
        <div style="color:#888;">上次發布：${esc(last)}</div>
        ${pendingLine}
        ${countdownLine}
        <div style="margin-top:8px;">${btn}</div>
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
