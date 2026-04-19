/**
 * blog.js — 部落格同步（Notion as CMS）
 * Notion token + database_id 存在 website_settings，實際同步邏輯在
 * M-E 階段 Astro build 時實作。此視圖提供連線狀態 + 手動觸發 rebuild。
 */
import { websiteFetch, esc, toastOk, toastErr } from '../website-utils.js';

export default async function render(container) {
    container.innerHTML = '<h2>📝 部落格</h2><div style="color:#888;padding:20px;">載入中…</div>';

    let status = {};
    let rebuild = {};
    try {
        [status, rebuild] = await Promise.all([
            websiteFetch('/api/website/admin/notion/status'),
            websiteFetch('/api/website/admin/rebuild/status'),
        ]);
    } catch (e) {
        container.innerHTML = `<h2>📝 部落格</h2><div class="card" style="color:#f87171;">${esc(e.message)}</div>`;
        return;
    }

    const connected = status.connected;
    const dotColor = connected ? '#4ade80' : '#888';

    container.innerHTML = `
        <h2>📝 部落格（Notion as CMS）</h2>

        <div class="card" style="margin-bottom:12px;">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
                <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${dotColor};"></span>
                <strong style="color:#fff;font-size:14px;">${connected ? '已連線' : '未設定'}</strong>
            </div>
            <div style="color:#aaa;font-size:13px;line-height:1.6;">
                Token 已設定：${status.has_token ? '✓' : '✗'}<br/>
                Database ID 已設定：${status.has_database_id ? '✓' : '✗'}
            </div>
            ${!connected
                ? `<div style="margin-top:10px;color:#888;font-size:12px;">
                      設定方式：到「⚙️ 網站設定」填入 <code>notion.token</code> 與
                      <code>notion.database_id</code> 兩個 key，儲存後回來此頁。
                   </div>`
                : ''}
        </div>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 8px 0;font-size:14px;">網站 Rebuild 狀態</h3>
            <div style="color:#aaa;font-size:13px;margin-bottom:10px;">
                <span class="website-pill" style="background:${_stateColor(rebuild.state)};">${esc(rebuild.state || 'idle')}</span>
                ${rebuild.started_at ? ` · 開始於 ${new Date(rebuild.started_at * 1000).toLocaleString()}` : ''}
                ${rebuild.finished_at ? ` · 結束於 ${new Date(rebuild.finished_at * 1000).toLocaleString()}` : ''}
            </div>
            ${rebuild.output_tail ? `<pre style="background:#0d0d0d;color:#9af;padding:10px;font-size:11px;border-radius:4px;overflow:auto;max-height:200px;">${esc(rebuild.output_tail)}</pre>` : ''}
            ${rebuild.error ? `<div style="color:#f87171;font-size:12px;margin-top:6px;">錯誤：${esc(rebuild.error)}</div>` : ''}
            <div style="display:flex;gap:8px;margin-top:10px;">
                <button class="btn" onclick="window._websiteTriggerRebuild()" ${rebuild.state === 'running' ? 'disabled' : ''}>
                    🔄 觸發完整 Rebuild
                </button>
                <button class="btn btn-ghost" onclick="window._websiteTriggerNotionSync()" ${!connected || rebuild.state === 'running' ? 'disabled' : ''}>
                    ⟲ 同步 Notion（走 rebuild）
                </button>
                <button class="btn btn-ghost btn-sm" onclick="window.websiteSwitchSubview && window.websiteSwitchSubview('blog')" style="margin-left:auto;">↻ 重新整理</button>
            </div>
        </div>

        <div class="card" style="color:#888;font-size:12px;">
            <strong style="color:#fff;">ℹ️ 工作流程</strong><br/>
            1. Notion 上建立 Database，欄位：Title / Slug / Cover / Publish Date / Body<br/>
            2. 分享 Database 給 Notion Integration（取得 token 與 database_id）<br/>
            3. 填入網站設定後，按「觸發 Rebuild」，Astro 會在 build 時抓 Notion 文章<br/>
            4. 部署後新文章會出現在 <code>/news</code>
        </div>
    `;
}

function _stateColor(state) {
    return { idle: '#333', running: '#5f3f1e', success: '#1e5f2e', error: '#5f1e1e' }[state] || '#333';
}

window._websiteTriggerRebuild = async () => {
    try {
        const r = await websiteFetch('/api/website/admin/rebuild', { method: 'POST' });
        toastOk(r.queued ? '已排入 rebuild' : (r.reason || '無法排入'));
        setTimeout(() => window.websiteSwitchSubview && window.websiteSwitchSubview('blog'), 500);
    } catch (e) { toastErr(e.message); }
};

window._websiteTriggerNotionSync = async () => {
    try {
        const r = await websiteFetch('/api/website/admin/notion/sync', { method: 'POST' });
        toastOk(r.queued ? '已排入 Notion 同步 rebuild' : (r.reason || '無法排入'));
        setTimeout(() => window.websiteSwitchSubview && window.websiteSwitchSubview('blog'), 500);
    } catch (e) { toastErr(e.message); }
};
