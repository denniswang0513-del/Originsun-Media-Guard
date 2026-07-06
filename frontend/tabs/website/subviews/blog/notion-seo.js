// blog/notion-seo.js — 拆自 blog.js：Sub-tab 3 📥 從 Notion 匯入 + Sub-tab 4 🌐 SEO 移轉中心（純搬移，行為不變）
import { websiteFetch, esc, toastOk, toastErr } from '../../website-utils.js';
import { _state, _blog } from './shared.js';
import { _renderShell } from './shell.js';

// ══════════════════════════════════════════════════════════
// Sub-tab 3: 📥 從 Notion 匯入
// ══════════════════════════════════════════════════════════

function _viewNotion() {
    const s = _state.notionStatus;
    const r = _state.rebuildStatus;
    const connected = s.connected;

    return `
        <div class="card" style="margin-bottom:12px;border-left:3px solid #06b6d4;">
            <h3 style="color:#fff;margin:0 0 6px;font-size:13px;">🔗 從公開 Notion 連結匯入單篇 <span style="color:#06b6d4;font-size:11px;font-weight:400;">· 免 token</span></h3>
            <div style="color:#888;font-size:11.5px;line-height:1.7;margin-bottom:10px;">
                把任一<strong style="color:#ddd;">公開分享</strong>的 Notion 文章貼進來，自動抓圖文、下載圖片轉 WebP、套用「封面圖／內文與內文配圖」模板（文末撰寫筆記會自動略過），建立成一篇 <strong>草稿</strong>。<br/>
                <span style="color:#666;">在 Notion 該頁右上「Share → Publish / 發佈到網路」開啟公開後，複製網址貼這裡。</span>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <input id="notion-url" type="text" placeholder="https://xxx.notion.site/頁面標題-d904da33...　或直接貼 page id"
                       style="flex:1;min-width:260px;font-size:12px;" />
                <button class="btn" onclick="window._blog.importNotionUrl()" style="background:#06b6d4;white-space:nowrap;">📥 匯入</button>
            </div>
            <div id="notion-url-result" style="margin-top:10px;"></div>
        </div>

        <div class="card" style="background:#1f1f1f;margin-bottom:12px;">
            <div style="color:#666;font-size:11px;margin-bottom:8px;">— 或 — 從已連線的 Notion 內容資料庫批次同步：</div>
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${connected ? '#4ade80' : '#888'};"></span>
                <strong style="color:#fff;font-size:13px;">${connected ? '已連線 Notion' : '未連線 Notion'}</strong>
            </div>
            <div style="color:#aaa;font-size:12px;">
                Token：${s.has_token ? '✓' : '✗'}　Database ID：${s.has_database_id ? '✓' : '✗'}
            </div>
            ${!connected
                ? '<div style="color:#888;font-size:11px;margin-top:8px;">設定方式：到「⚙️ 網站設定」填入 <code>notion.token</code> 與 <code>notion.database_id</code>，儲存後回來。</div>'
                : ''}
        </div>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:13px;">匯入規則</h3>
            <div style="color:#888;font-size:12px;line-height:1.7;">
                · DB 為真：已存在 <code>notion_page_id</code> 的文章<strong style="color:#fbbf24;">預設跳過</strong>（保留 admin 編輯）<br/>
                · 新文章插入為 <strong>status=draft</strong>，admin 必須在「📰 文章」過目才上線<br/>
                · 強制重置：勾選後已存在文章會整篇覆寫（保留 admin 設的 status / old_urls / SEO 覆寫）
            </div>
        </div>

        <div class="card" style="margin-bottom:12px;">
            <label style="display:flex;align-items:center;gap:8px;color:#fbbf24;font-size:12px;cursor:pointer;margin-bottom:10px;">
                <input id="notion-force" type="checkbox" />
                ⚠ 強制重置（已存在文章將被 Notion 最新版覆寫，admin 編輯會丟）
            </label>
            <button class="btn" onclick="window._blog.runNotionImport()" ${connected ? '' : 'disabled'} style="background:#059669;">
                📥 開始匯入
            </button>
        </div>

        <div id="notion-import-result"></div>

        <div class="card" style="background:#252525;color:#888;font-size:11px;line-height:1.7;">
            <strong style="color:#fff;">ℹ️ Phase A 後行為差異</strong><br/>
            · 之前：Notion → posts.json → Astro build<br/>
            · 現在：Notion → DB 為真 → admin 編輯永久保留 → Astro fetch DB build<br/>
            · 想單純 trigger rebuild（不撈 Notion）：<button class="btn btn-sm btn-ghost" onclick="window._blog.triggerRebuild()">🔄 觸發 Rebuild</button>
        </div>
    `;
}

_blog.importNotionUrl = async () => {
    const input = document.getElementById('notion-url');
    const url = (input?.value || '').trim();
    const host = document.getElementById('notion-url-result');
    if (!url) { toastErr('請先貼上 Notion 連結'); input?.focus(); return; }

    if (host) host.innerHTML = '<div style="color:#aaa;font-size:12px;">📥 匯入中…（抓頁面 + 下載圖片轉 WebP，約 10–30 秒）</div>';
    try {
        const r = await websiteFetch('/api/website/admin/posts/import-notion-url', {
            method: 'POST', body: { url },
        });
        if (!r.ok) {
            if (host) host.innerHTML = `<div style="color:#f87171;font-size:12px;">❌ ${esc(r.error || '匯入失敗')}${r.slug ? `（<a href="#" onclick="window._blog.switchTab('posts');return false;" style="color:#3b82f6;">看文章列表</a>）` : ''}</div>`;
            toastErr(r.error || '匯入失敗');
            return;
        }
        const cats = (r.category_slugs || []).map(slug => {
            const c = _state.categories.find(x => x.slug === slug);
            return c ? c.label_zh : slug;
        });
        if (host) host.innerHTML = `
            <div style="border-left:3px solid #4ade80;background:#16241a;padding:10px 12px;border-radius:4px;">
                <div style="color:#fff;font-size:13px;margin-bottom:6px;">✅ 已建立草稿：${esc(r.title)}</div>
                <div style="color:#aaa;font-size:11.5px;line-height:1.7;">
                    slug <code>${esc(r.slug)}</code> · ${r.block_count} 個內文區塊 · ${r.image_count} 張圖已 host
                    · 分類：${cats.length ? cats.map(esc).join('、') : '<span style="color:#fbbf24;">未對應（請在編輯時選）</span>'}
                </div>
                ${r.warnings?.length ? `<div style="color:#fbbf24;font-size:11px;margin-top:6px;">⚠ ${r.warnings.map(esc).join('；')}</div>` : ''}
                <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
                    <button class="btn btn-sm" onclick="window._blog.openEditPost(${r.post_id})" style="background:#3b82f6;">✏️ 開啟編輯 / 過目</button>
                    <span style="color:#666;font-size:11px;align-self:center;">過目後在編輯視窗按「🚀 儲存並發布」即上線</span>
                </div>
            </div>`;
        toastOk(`已匯入草稿：${r.title}`);
        if (input) input.value = '';
        // 刷新文章列表（讓新草稿出現在「📰 文章」分頁 + tab 計數）
        const posts = await websiteFetch('/api/website/admin/posts');
        _state.posts = posts?.items || [];
    } catch (e) {
        toastErr(e.message);
        if (host) host.innerHTML = `<div style="color:#f87171;font-size:12px;">匯入失敗：${esc(e.message)}</div>`;
    }
};

_blog.runNotionImport = async () => {
    const force = document.getElementById('notion-force').checked;
    if (force && !confirm('強制重置會覆寫所有來自 Notion 的文章，admin 後續編輯（除 status / old_urls / SEO 覆寫外）將丟失。確定？')) return;

    const host = document.getElementById('notion-import-result');
    if (host) host.innerHTML = '<div class="card" style="color:#aaa;font-size:12px;">📥 匯入中…（從 Notion 抓 + 寫 DB，可能需要幾分鐘）</div>';

    try {
        const r = await websiteFetch('/api/website/admin/posts/import-notion', {
            method: 'POST', body: { force },
        });
        if (host) host.innerHTML = `
            <div class="card" style="border-left:3px solid #4ade80;">
                <div style="color:#fff;font-size:13px;margin-bottom:8px;">📥 匯入完成（${r.duration_ms} ms）</div>
                <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:8px;text-align:center;font-size:13px;color:#ddd;">
                    <div><div style="color:#4ade80;font-size:18px;font-weight:600;">${r.inserted}</div>新增</div>
                    <div><div style="color:#888;font-size:18px;font-weight:600;">${r.skipped}</div>跳過</div>
                    <div><div style="color:#fbbf24;font-size:18px;font-weight:600;">${r.overwritten}</div>覆寫</div>
                    <div><div style="color:#f87171;font-size:18px;font-weight:600;">${r.failed}</div>失敗</div>
                </div>
                ${r.new_categories?.length ? `<div style="color:#888;font-size:11px;margin-top:10px;">自動新建分類：${r.new_categories.map(esc).join(', ')}</div>` : ''}
                ${r.warnings?.length ? `<details style="margin-top:8px;"><summary style="color:#fbbf24;font-size:11px;cursor:pointer;">⚠ ${r.warnings.length} 個警告</summary><pre style="font-size:11px;color:#aaa;margin-top:6px;">${esc(r.warnings.join('\n'))}</pre></details>` : ''}
                <div style="margin-top:10px;color:#888;font-size:11px;">60 秒後對外網站 rebuild；新增文章會在「📰 文章」分頁顯示為 draft 等你過目</div>
            </div>
        `;
        toastOk(`新增 ${r.inserted}、跳過 ${r.skipped}、覆寫 ${r.overwritten}`);

        // refresh post list + 更新 tab btn 計數
        const posts = await websiteFetch('/api/website/admin/posts');
        _state.posts = posts?.items || [];
        _renderShell();
    } catch (e) {
        toastErr(e.message);
        if (host) host.innerHTML = `<div class="card" style="color:#f87171;">匯入失敗：${esc(e.message)}</div>`;
    }
};

_blog.triggerRebuild = async () => {
    try {
        const r = await websiteFetch('/api/website/admin/rebuild', { method: 'POST' });
        toastOk(r.queued ? '已排入 rebuild' : (r.reason || '無法排入'));
    } catch (e) { toastErr(e.message); }
};


// ══════════════════════════════════════════════════════════
// Sub-tab 4: 🌐 SEO 移轉中心
// ══════════════════════════════════════════════════════════

function _viewSEOMigration() {
    const cnt = _state.redirectCount;
    const lastSync = _state.lastRedirectSyncAt
        ? new Date(_state.lastRedirectSyncAt).toLocaleString()
        : '尚未同步（此 session）';
    const syncStatus = _state.redirectSyncOk === null
        ? '<span style="color:#888;">未試</span>'
        : _state.redirectSyncOk
            ? '<span style="color:#4ade80;">✓ 上次同步成功</span>'
            : '<span style="color:#f87171;">✗ 上次同步失敗</span>';

    return `
        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">🌐 SEO 移轉中心
                <span style="color:#888;font-size:11px;font-weight:400;">· 文章 + 作品集 統一管理</span>
            </h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
                <div style="background:#1f1f1f;padding:12px;border-radius:6px;border-left:3px solid #4ade80;">
                    <div style="color:#888;font-size:11px;">軟 301（Astro 靜態頁）</div>
                    <div style="color:#fff;font-size:20px;font-weight:600;margin:4px 0;">${cnt}</div>
                    <div style="color:#888;font-size:11px;">每次 build 自動為每個舊 URL 產一張（含 /news/* 跟 /works/*）</div>
                </div>
                <div style="background:#1f1f1f;padding:12px;border-radius:6px;border-left:3px solid #f59e0b;">
                    <div style="color:#888;font-size:11px;">硬 301（NAS nginx）</div>
                    <div style="color:#fff;font-size:20px;font-weight:600;margin:4px 0;">${cnt}</div>
                    <div style="color:#888;font-size:11px;">${syncStatus} · ${lastSync}</div>
                </div>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button class="btn" onclick="window._blog.viewAllRedirects()">📝 檢視所有 redirect</button>
                <button class="btn btn-ghost" onclick="window._blog.forceSyncRedirects()">🔄 強制重新同步硬 301</button>
            </div>
            <div style="color:#888;font-size:11px;margin-top:10px;line-height:1.6;">
                ℹ️ 文章 / 作品集編輯後 publish 流程末段會自動同步硬 301（兩者共用同一條 nginx config）。
                手動按鈕用於 admin 看到計數對不上時自救。作品集改 slug 時舊 slug 會自動加進 redirect 清單。
            </div>
        </div>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">🌍 跨域遷移指引</h3>
            <div style="color:#aaa;font-size:13px;line-height:1.8;">
                <p>從另一個網域（如 <code>old.originsun.tw</code>）遷移文章到 <code>originsun-studio.com</code>，<strong style="color:#fbbf24;">舊網域</strong>也要設定：</p>
                <ol style="padding-left:20px;color:#ccc;">
                    <li>舊網域 nginx 加 301 → 新網域對應 URL</li>
                    <li>Google Search Console「Change of Address」告訴 Google 整體網域搬家
                        <br/><a href="https://search.google.com/search-console/settings/move-site" target="_blank" style="color:#3b82f6;font-size:12px;">🔗 開啟 GSC Change of Address →</a>
                    </li>
                    <li>兩個網域都加進 GSC 並驗證所有權</li>
                </ol>
                <p style="color:#888;font-size:12px;">完整手冊：<a href="/docs/SEO_MIGRATION_GUIDE.md" target="_blank" style="color:#3b82f6;">docs/SEO_MIGRATION_GUIDE.md</a></p>
            </div>
        </div>
    `;
}

_blog.viewAllRedirects = async () => {
    try {
        const r = await websiteFetch('/api/website/redirects');
        const items = r.items || {};
        // 文章 + 作品 標題 lookup（顯示 redirect 目標時帶上人類可讀名稱）
        const slugLookup = new Map(_state.posts.map(p => [`/news/${p.slug}`, p.title]));

        const modal = document.createElement('div');
        modal.id = 'redirect-modal';
        modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;padding:20px;';
        modal.innerHTML = `
            <div style="background:#1a1a1a;border:1px solid #3a3a3a;border-radius:8px;width:100%;max-width:720px;max-height:80vh;overflow-y:auto;">
                <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
                    <h3 style="color:#fff;margin:0;font-size:14px;">全部 SEO 301 對應（${Object.keys(items).length} 條）</h3>
                    <button onclick="document.getElementById('redirect-modal').remove()" style="background:transparent;border:none;color:#888;cursor:pointer;font-size:18px;">✕</button>
                </div>
                <div style="padding:14px 18px;">
                    ${Object.keys(items).length === 0
                        ? '<div style="color:#888;text-align:center;padding:30px;">尚無轉址。在「📰 文章 → 編輯 → 🔗 SEO 301 轉址」可以新增。</div>'
                        : `<table style="width:100%;font-size:12px;">
                            <thead><tr>
                                <th style="text-align:left;padding:6px;color:#888;">舊路徑</th>
                                <th style="text-align:left;padding:6px;color:#888;">新位</th>
                                <th style="text-align:left;padding:6px;color:#888;">文章</th>
                            </tr></thead>
                            <tbody>${Object.entries(items).sort().map(([from, to]) => `
                                <tr>
                                    <td style="padding:5px;color:#ddd;font-family:monospace;">${esc(from)}</td>
                                    <td style="padding:5px;color:#3b82f6;font-family:monospace;">${esc(to)}</td>
                                    <td style="padding:5px;color:#aaa;">${esc(slugLookup.get(to) || '-')}</td>
                                </tr>
                            `).join('')}</tbody>
                        </table>`}
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    } catch (e) { toastErr(e.message); }
};

_blog.forceSyncRedirects = async () => {
    try {
        const r = await websiteFetch('/api/website/admin/redirects/sync', { method: 'POST' });
        _state.redirectSyncOk = r.ok;
        _state.lastRedirectSyncAt = r.last_sync || new Date().toISOString();
        toastOk(r.ok ? `已同步 ${r.synced} 條硬 301` : (r.error || '同步失敗'));
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

export { _viewNotion, _viewSEOMigration };
