/**
 * blog.js — 部落格同步（Notion as CMS）
 *
 * 流程：管理員點「預覽」→ dry-run 撈 Notion → 看清單；確認 OK 點「實際同步」→
 * 寫 posts.json + categories.json + 觸發 Astro rebuild。
 *
 * Notion token + database_id 存在 website_settings，從「⚙️ 網站設定」填。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError } from '../website-utils.js';

let _lastResult = null;  // 最近一次 sync/preview 結果，給 _renderResult 用

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>📝 部落格</h2><div style="color:#888;padding:20px;">載入中…</div>';

    let status = {};
    let rebuild = {};
    try {
        [status, rebuild] = await Promise.all([
            websiteFetch('/api/website/admin/notion/status'),
            websiteFetch('/api/website/admin/rebuild/status'),
        ]);
        if (!isCurrent()) return;
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '📝 部落格', e);
        return;
    }

    const connected = status.connected;
    const dotColor = connected ? '#4ade80' : '#888';
    const syncBlocked = !connected || rebuild.state === 'running';

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
            <h3 style="color:#fff;margin:0 0 8px 0;font-size:14px;">Notion 同步</h3>
            <div style="color:#888;font-size:12px;line-height:1.7;margin-bottom:10px;">
                文章從 Notion「官網內容上架管理表」同步：<br/>
                · 篩選條件：<code>製作進度 = 上架官網</code><br/>
                · 排序：<code>last_edited_time</code> 降序<br/>
                · slug 用「官網編號」純數字（永遠不變）<br/>
                · 摘要自動抽 body 第一段前 100 字
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button class="btn btn-ghost" onclick="window._websitePreviewNotion()" ${syncBlocked ? 'disabled' : ''}>
                    👁 預覽同步結果（不寫檔）
                </button>
                <button class="btn" onclick="window._websiteSyncNotion()" ${syncBlocked ? 'disabled' : ''} style="background:#059669;">
                    ⟲ 實際同步 Notion + 觸發 Rebuild
                </button>
            </div>
            <div id="notion-result-host" style="margin-top:14px;"></div>
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
                    🔄 僅觸發 Rebuild（不撈 Notion）
                </button>
                <button class="btn btn-ghost btn-sm" onclick="window.websiteSwitchSubview && window.websiteSwitchSubview('blog')" style="margin-left:auto;">↻ 重新整理</button>
            </div>
        </div>

        <div class="card" style="color:#888;font-size:12px;">
            <strong style="color:#fff;">ℹ️ 工作流程</strong><br/>
            1. Notion 上「官網內容上架管理表」維護文章（製作進度 = 上架官網 才會公開）<br/>
            2. 在「⚙️ 網站設定」填入 <code>notion.token</code> + <code>notion.database_id</code><br/>
            3. 此頁按「預覽同步結果」確認；OK 後按「實際同步」<br/>
            4. 後端寫入 posts.json + categories.json，自動觸發 Astro build<br/>
            5. Build 完成後新文章出現在 <code>/news</code>
        </div>
    `;

    // 重新載入時若上次有結果，重貼上
    if (_lastResult) _renderResult(_lastResult);
}

function _stateColor(state) {
    return { idle: '#333', running: '#5f3f1e', success: '#1e5f2e', error: '#5f1e1e' }[state] || '#333';
}


// ══════════════════════════════════════════════════════════
// Sync 結果渲染（preview / sync 共用）
// ══════════════════════════════════════════════════════════

function _renderResult(r) {
    _lastResult = r;
    const host = document.getElementById('notion-result-host');
    if (!host) return;
    if (!r) { host.innerHTML = ''; return; }

    if (!r.ok) {
        host.innerHTML = `
            <div style="background:#3a1a1a;border:1px solid #5f1e1e;border-radius:6px;padding:12px;color:#fca5a5;font-size:13px;">
                <strong>同步失敗：</strong>${esc(r.error || '未知錯誤')}
            </div>
        `;
        return;
    }

    const isPreview = r.sync_type === 'preview';
    const headerColor = isPreview ? '#1e3a5f' : '#1e5f2e';
    const headerLabel = isPreview ? '預覽結果（dry-run，未寫檔）' : '同步完成';

    host.innerHTML = `
        <div style="background:${headerColor};border-radius:6px 6px 0 0;padding:8px 12px;color:#fff;font-size:12px;font-weight:600;display:flex;align-items:center;gap:10px;">
            <span>${headerLabel}</span>
            <span style="font-weight:400;opacity:0.85;">· ${r.duration_ms} ms · ${r.posts_count} 篇文章 · ${r.categories_count} 個分類</span>
            ${r.rebuild_queued ? '<span style="background:#0e8a4a;padding:1px 6px;border-radius:3px;">已排入 Rebuild</span>' : ''}
        </div>
        <div style="background:#161616;border:1px solid ${headerColor};border-top:none;border-radius:0 0 6px 6px;padding:12px;">
            ${_renderCategoriesList(r.categories || [])}
            ${_renderPostsList(r.posts || [])}
            ${_renderSkipped(r.skipped || [])}
            ${_renderWarnings(r.warnings || [])}
        </div>
    `;
}

function _renderCategoriesList(cats) {
    if (!cats.length) return '<div style="color:#888;font-size:12px;">（沒有分類）</div>';
    return `
        <div style="margin-bottom:12px;">
            <div style="color:#888;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">分類（${cats.length}）</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;">
                ${cats.map(c => `
                    <span class="website-pill" title="${esc(c.name)}（${esc(c.color || 'default')}）">
                        ${esc(c.label_zh)} · ${c.count || 0}
                    </span>
                `).join('')}
            </div>
        </div>
    `;
}

function _renderPostsList(posts) {
    if (!posts.length) {
        return '<div style="color:#888;font-size:12px;margin-bottom:12px;">（沒有符合條件的文章）</div>';
    }
    return `
        <div style="margin-bottom:12px;">
            <div style="color:#888;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">公開文章（${posts.length}）</div>
            <table style="width:100%;font-size:12px;">
                <thead><tr style="color:#666;">
                    <th style="text-align:left;padding:4px 6px;width:50px;">編號</th>
                    <th style="text-align:left;padding:4px 6px;">標題</th>
                    <th style="text-align:left;padding:4px 6px;width:120px;">分類</th>
                    <th style="text-align:left;padding:4px 6px;width:140px;">最後更新</th>
                </tr></thead>
                <tbody>${posts.map(p => `
                    <tr>
                        <td style="padding:4px 6px;color:#888;font-family:monospace;">#${esc(p.slug)}</td>
                        <td style="padding:4px 6px;color:#ddd;">${esc(p.title)}</td>
                        <td style="padding:4px 6px;color:#aaa;">${esc(p.category_label_zh || p.category || '-')}</td>
                        <td style="padding:4px 6px;color:#888;">${p.published_at ? new Date(p.published_at).toLocaleDateString() : '-'}</td>
                    </tr>
                `).join('')}</tbody>
            </table>
        </div>
    `;
}

function _renderSkipped(skipped) {
    if (!skipped.length) return '';
    return `
        <div style="margin-bottom:12px;background:#2a1f0d;border:1px solid #5f3f1e;border-radius:4px;padding:8px 10px;">
            <div style="color:#fbbf24;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">跳過（${skipped.length}）</div>
            <ul style="margin:0;padding-left:20px;color:#ddd;font-size:12px;">
                ${skipped.map(s => `<li>${esc(s.title)} — <span style="color:#aaa;">${esc(s.reason)}</span></li>`).join('')}
            </ul>
        </div>
    `;
}

function _renderWarnings(warnings) {
    if (!warnings.length) return '';
    return `
        <div style="background:#2a1f0d;border:1px solid #5f3f1e;border-radius:4px;padding:8px 10px;">
            <div style="color:#fbbf24;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">警告（${warnings.length}）</div>
            <ul style="margin:0;padding-left:20px;color:#ddd;font-size:12px;line-height:1.7;">
                ${warnings.map(w => `<li>${esc(w)}</li>`).join('')}
            </ul>
        </div>
    `;
}


// ══════════════════════════════════════════════════════════
// Action handlers
// ══════════════════════════════════════════════════════════

window._websitePreviewNotion = async () => {
    const host = document.getElementById('notion-result-host');
    if (host) host.innerHTML = '<div style="color:#888;font-size:12px;padding:8px;">撈 Notion 中…</div>';
    try {
        const r = await websiteFetch('/api/website/admin/notion/preview');
        _renderResult(r);
        if (r.ok) toastOk(`預覽：${r.posts_count} 篇 / ${r.categories_count} 分類`);
    } catch (e) {
        toastErr(e.message);
        if (host) host.innerHTML = '';
    }
};

window._websiteSyncNotion = async () => {
    if (!confirm('將寫入 posts.json + categories.json 並觸發 Astro rebuild。確定？')) return;
    const host = document.getElementById('notion-result-host');
    if (host) host.innerHTML = '<div style="color:#888;font-size:12px;padding:8px;">同步 Notion 中…</div>';
    try {
        const r = await websiteFetch('/api/website/admin/notion/sync', { method: 'POST' });
        _renderResult(r);
        if (r.ok) {
            toastOk(`同步完成：${r.posts_count} 篇 / ${r.categories_count} 分類${r.rebuild_queued ? '（rebuild 已排入）' : ''}`);
        } else {
            toastErr(r.error || '同步失敗');
        }
    } catch (e) {
        toastErr(e.message);
        if (host) host.innerHTML = '';
    }
};

window._websiteTriggerRebuild = async () => {
    try {
        const r = await websiteFetch('/api/website/admin/rebuild', { method: 'POST' });
        toastOk(r.queued ? '已排入 rebuild' : (r.reason || '無法排入'));
        setTimeout(() => window.websiteSwitchSubview && window.websiteSwitchSubview('blog'), 500);
    } catch (e) { toastErr(e.message); }
};
