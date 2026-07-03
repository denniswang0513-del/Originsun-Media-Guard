/**
 * backup.js — 資料備份後台卡片
 *
 * 每日打包資料庫 + 上傳媒體到 Google Drive（異地備援）。
 * 排程 + 立即備份一次 + 上次執行結果。NAS 另有常駐本地快照，
 * 不受 master 開關機影響。
 *
 * 結構/風格對齊 translation.js（default async render、website-utils、
 * _inp() helper、CRON 下拉、save/run/status、toastOk/toastErr）。
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError,
} from '../website-utils.js';

let _state = { settings: {} };
let _container = null;
const _bk = (window._bk = window._bk || {});

// 排程時間選單 — value 是 cron 字串，主機台灣時區。
const CRON_PRESETS = [
    { v: '0 3 * * *',    label: '每日 03:00（預設）' },
    { v: '30 4 * * *',   label: '每日 04:30' },
    { v: '0 */12 * * *', label: '每 12 小時' },
    { v: '0 3 * * 0',    label: '每週日 03:00' },
];
function _cronOptions(cur) {
    const vals = CRON_PRESETS.map(p => p.v);
    let opts = CRON_PRESETS.map(p => `<option value="${p.v}"${p.v === cur ? ' selected' : ''}>${esc(p.label)}</option>`).join('');
    if (!vals.includes(cur)) opts = `<option value="${esc(cur)}" selected>自訂：${esc(cur)}</option>` + opts;
    return opts;
}

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🗄 資料備份</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const settings = await websiteFetch('/api/website/admin/backup/settings');
        if (!isCurrent()) return;
        _state.settings = settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🗄 資料備份', e);
        return;
    }
    _renderShell();
}

function _inp() {
    return 'background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:7px 9px;border-radius:4px;width:100%;box-sizing:border-box;font-size:13px;font-family:inherit;';
}

function _fmtMB(bytes) {
    return (Number(bytes || 0) / 1048576).toFixed(1);
}

function _masterOffline(s) {
    // master 目前可能離線：非 master 檢視，且 master_seen_at 為 null 或超過 5 分鐘。
    if (s.is_master === true) return false;
    if (s.master_seen_at == null) return true;
    return (Date.now() / 1000 - s.master_seen_at) > 300;
}

function _statusBlock(s) {
    const sum = s.last_run_summary;
    const lastRun = s.last_run_at
        ? `<div style="color:#888;font-size:12px;margin-top:6px;">上次執行：${esc(new Date(s.last_run_at * 1000).toLocaleString())}</div>`
        : '';
    if (!sum) {
        return `<div style="color:#888;font-size:13px;">尚未執行過備份</div>${lastRun}`;
    }
    if (sum.ok) {
        const ts = sum.ts ? esc(String(sum.ts)) : '';
        const link = sum.drive_url
            ? ` · <a href="${esc(sum.drive_url)}" target="_blank" rel="noopener" style="color:#93c5fd;">🔗 Drive</a>` : '';
        const driveErr = sum.drive_error
            ? `<div style="color:#fbbf24;font-size:12px;margin-top:6px;">⚠ 上雲未完成：${esc(sum.drive_error)}</div>` : '';
        return `
            <div style="color:#6ee7b7;font-size:13px;">
                ✓ 成功 · ${ts} · 檔案 ${_fmtMB(sum.bundle_bytes)} MB${link}
            </div>
            ${driveErr}${lastRun}`;
    }
    return `<div style="color:#fca5a5;font-size:13px;">✗ 失敗：${esc(sum.error || '未知錯誤')}</div>${lastRun}`;
}

function _renderShell() {
    const s = _state.settings;
    const warn = s.gdrive_ready === false
        ? `<div style="background:#3a2a12;border:1px solid #92600f;color:#fbbf24;border-radius:6px;padding:10px 12px;margin-bottom:14px;font-size:12px;line-height:1.6;">
                ⚠ 尚未完成 Google Drive 授權 — 備份仍會在本地/NAS 產生，但無法上雲。請先完成一次性授權：在 master 執行 <code>scripts/setup_gdrive_backup.py</code>（詳見 <code>docs/BACKUP_RESTORE.md</code>）。
            </div>`
        : '';
    const offlineNote = _masterOffline(s)
        ? `<div style="color:#888;font-size:12px;margin-top:10px;">master 目前可能離線；排程與立即備份需 master 上線才會執行。</div>`
        : '';

    _container.innerHTML = `
        <h2 style="margin:0 0 4px;">🗄 資料備份</h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">
            每日打包資料庫 + 上傳媒體 → 你的 Google Drive（異地備援）；NAS 另有常駐本地快照（不受 master 開關機影響）。
        </p>

        ${warn}

        <!-- 排程與設定 -->
        <div class="card" style="margin-bottom:14px;">
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">⚙️ 排程與設定</h3>
            <div style="display:grid;grid-template-columns:auto 1fr;gap:10px 14px;align-items:center;font-size:13px;">
                <label style="color:#ddd;display:inline-flex;gap:6px;align-items:center;grid-column:1/3;">
                    <input id="bk-enabled" type="checkbox" ${s.enabled ? 'checked' : ''} style="width:auto;"/> 啟用排程</label>
                <span style="color:#9aa0a6;">執行時間</span>
                <select id="bk-cron" style="${_inp()}">${_cronOptions(s.cron || '0 3 * * *')}</select>
                <span style="color:#9aa0a6;align-self:start;padding-top:6px;">Google Drive 資料夾 ID</span>
                <div>
                    <input id="bk-folder" type="text" value="${esc(s.gdrive_folder_id || '')}" style="${_inp()}" />
                    <div style="color:#666;font-size:11px;margin-top:4px;">留空 = 上傳到 Drive 根目錄；建議填備份專用資料夾 ID（跑 setup 腳本會自動建立並印出）</div>
                </div>
                <span style="color:#9aa0a6;">保留最近 N 日 + 每月各留 M 份</span>
                <div style="display:flex;gap:8px;align-items:center;">
                    <input id="bk-keep-daily" type="number" min="0" value="${Number(s.keep_daily ?? 14)}" style="${_inp()};max-width:90px;" />
                    <span style="color:#9aa0a6;">日</span>
                    <input id="bk-keep-monthly" type="number" min="0" value="${Number(s.keep_monthly ?? 6)}" style="${_inp()};max-width:90px;" />
                    <span style="color:#9aa0a6;">月</span>
                </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap;">
                <button class="btn" style="background:#3b82f6;" onclick="window._bk.saveSettings()">💾 儲存設定</button>
            </div>
        </div>

        <!-- 立即備份 -->
        <div class="card" style="margin-bottom:14px;">
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">⚡ 手動備份</h3>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <button class="btn" style="background:#059669;" onclick="window._bk.runNow(this)"
                        ${s.running ? 'disabled' : ''}>${s.running ? '備份中…' : '⚡ 立即備份一次'}</button>
                <span style="color:#888;font-size:12px;">背景執行，約 1 分鐘後重整看結果</span>
            </div>
            ${offlineNote}
        </div>

        <!-- 上次執行結果 -->
        <div class="card">
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">📋 上次執行結果</h3>
            ${_statusBlock(s)}
        </div>
    `;
}

// ── 儲存設定 / 立即備份 ──

_bk.saveSettings = async () => {
    const v = (id) => document.getElementById(id);
    const payload = {
        enabled: v('bk-enabled').checked,
        cron: v('bk-cron').value.trim(),
        gdrive_folder_id: v('bk-folder').value.trim(),
        keep_daily: Number(v('bk-keep-daily').value) || 0,
        keep_monthly: Number(v('bk-keep-monthly').value) || 0,
    };
    try {
        _state.settings = await websiteFetch('/api/website/admin/backup/settings', { method: 'PUT', body: payload });
        toastOk('設定已儲存');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

_bk.runNow = async (btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '啟動中…'; }
    try {
        const r = await websiteFetch('/api/website/admin/backup/run', { method: 'POST' });
        if (r.status === 'started') toastOk('已開始備份（背景執行，約1分鐘後重整看結果）');
        else if (r.status === 'busy') toastErr('已有一次備份在進行');
        else toastErr(r.error || '無法啟動備份');
    } catch (e) { toastErr(e.message); }
    if (btn) { btn.disabled = false; btn.textContent = '⚡ 立即備份一次'; }
};
