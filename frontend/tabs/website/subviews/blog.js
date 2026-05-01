/**
 * blog.js — 部落格管理（DB-as-truth + Notion 是匯入器）
 *
 * 4 個 sub-tab：
 *   📰 文章        — list + filters + metadata Modal CRUD
 *   📚 分類        — CRUD 多對多分類（inline table）
 *   📥 從 Notion   — 匯入器（Phase A 後 Notion 只是 seed，不再是 truth）
 *   🌐 SEO 移轉    — 軟+硬 301 計數 + 強制同步 + 跨域遷移指引
 *
 * Modal 含完整 block 編輯器（6 種 block：paragraph / heading / image / video /
 * quote / list）+ 圖片上傳 + YouTube 解析 + 即時預覽 + SEO health widget。
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError,
    readRowPatch, openModal, closeModal, getApiBase,
} from '../website-utils.js';

const SUB_TABS = ['posts', 'categories', 'notion', 'seo-migration'];

// upload endpoint 回的 URL 是相對路徑 /uploads/posts/{id}/{name}.webp，
// 但 admin Tab 是從 master Web UI 開啟的（origin = master），那邊沒這個路徑。
// 圖片實際存在 NAS website 容器 → 預覽時要 prepend NAS API base。
// 完整 URL（http/https/data/blob） 原樣 passthrough。
function _resolveImageUrl(url) {
    if (!url) return '';
    if (/^(https?:|data:|blob:)/i.test(url)) return url;
    if (url.startsWith('/')) return getApiBase() + url;
    return url;
}

// 文章狀態 metadata 集中（label + 顯示色 + emoji 一處改全套同步）
const STATUS = {
    draft:     { label: '草稿',   color: '#5f3f1e', emoji: '📝' },
    published: { label: '已發布', color: '#1e5f2e', emoji: '🚀' },
    archived:  { label: '已下架', color: '#3a3a3a', emoji: '🗄' },
};
const STATUS_FALLBACK = { label: '?', color: '#444', emoji: '❓' };

const EMPTY_POST = Object.freeze({
    id: null, slug: '', title: '', excerpt: '', cover_url: '',
    body: [], category_slugs: [], status: 'draft', published_at: null,
    seo_title: '', seo_description: '', og_image_url: '', canonical_url: '',
    noindex: false, author_name: '', author_url: '',
    ai_allow_override: null, old_urls: [],
});

let _state = {
    activeTab: 'posts',
    posts: [],
    categories: [],
    notionStatus: { connected: false, has_token: false, has_database_id: false },
    rebuildStatus: { state: 'idle' },
    redirectCount: 0,
    redirectSyncOk: null,    // null = 未試過 / true / false
    lastRedirectSyncAt: null,
    filters: { status: '', category: '', q: '' },
};
let _container = null;

const _blog = (window._blog = window._blog || {});


export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>📝 部落格</h2><div style="color:#888;padding:20px;">載入中…</div>';

    try {
        const [posts, cats, notion, rebuild, redirects] = await Promise.all([
            websiteFetch('/api/website/admin/posts'),
            websiteFetch('/api/website/admin/post_categories'),
            websiteFetch('/api/website/admin/notion/status'),
            websiteFetch('/api/website/admin/rebuild/status'),
            websiteFetch('/api/website/redirects'),
        ]);
        if (!isCurrent()) return;
        _state.posts = posts?.items || [];
        _state.categories = cats?.items || [];
        _state.notionStatus = notion || _state.notionStatus;
        _state.rebuildStatus = rebuild || _state.rebuildStatus;
        _state.redirectCount = redirects?.count || 0;
    } catch (e) {
        if (!isCurrent()) return;
        const hint = e.status === 404
            ? 'NAS website-api 可能跑舊版（沒 admin_posts router）。請在 master 跑 /publish 同步後端到 NAS。'
            : '';
        renderLoadError(container, '📝 部落格', e, hint);
        return;
    }
    _renderShell();
}


// ══════════════════════════════════════════════════════════
// Shell + tab nav
// ══════════════════════════════════════════════════════════

function _renderShell() {
    _container.innerHTML = `
        <h2>📝 部落格管理 <span style="color:#888;font-size:12px;font-weight:400;">· DB-as-truth · Notion 是匯入器</span></h2>

        <div class="card" style="padding:0;margin-bottom:12px;">
            <div style="display:flex;border-bottom:1px solid #2a2a2a;">
                ${_tabBtn('posts',         '📰 文章',   _state.posts.length)}
                ${_tabBtn('categories',    '📚 分類',   _state.categories.length)}
                ${_tabBtn('notion',        '📥 從 Notion 匯入', null)}
                ${_tabBtn('seo-migration', '🌐 SEO 移轉中心', _state.redirectCount)}
            </div>
            <div id="blog-tab-body" style="padding:16px;"></div>
        </div>
    `;
    _renderActive();
}

function _tabBtn(id, label, count) {
    const active = _state.activeTab === id;
    return `
        <button onclick="window._blog.switchTab('${id}')"
                style="
                    flex:1;padding:12px 16px;cursor:pointer;border:none;
                    background:${active ? '#252525' : 'transparent'};
                    color:${active ? '#fff' : '#888'};
                    border-bottom:2px solid ${active ? '#3b82f6' : 'transparent'};
                    font-size:13px;text-align:center;transition:all 0.15s;
                ">
            ${label}${count !== null ? ` <span style="opacity:0.6;">(${count})</span>` : ''}
        </button>
    `;
}

_blog.switchTab = (id) => {
    if (!SUB_TABS.includes(id)) return;
    _state.activeTab = id;
    _renderShell();
};

const VIEW_RENDERERS = {
    'posts':          _viewPosts,
    'categories':     _viewCategories,
    'notion':         _viewNotion,
    'seo-migration':  _viewSEOMigration,
};

function _renderActive() {
    const body = document.getElementById('blog-tab-body');
    if (!body) return;
    body.innerHTML = VIEW_RENDERERS[_state.activeTab]?.() ?? '';
}


// ══════════════════════════════════════════════════════════
// Sub-tab 1: 📰 文章列表
// ══════════════════════════════════════════════════════════

function _viewPosts() {
    const filtered = _state.posts.filter(p => {
        if (_state.filters.status && p.status !== _state.filters.status) return false;
        if (_state.filters.category &&
            !(p.category_slugs || []).includes(_state.filters.category)) return false;
        if (_state.filters.q) {
            const q = _state.filters.q.toLowerCase();
            if (!(p.title || '').toLowerCase().includes(q) &&
                !(p.slug || '').toLowerCase().includes(q)) return false;
        }
        return true;
    });

    return `
        <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center;">
            <select id="filter-status" onchange="window._blog.setFilter('status', this.value)" style="font-size:12px;">
                <option value="" ${!_state.filters.status ? 'selected' : ''}>全部狀態</option>
                <option value="draft"     ${_state.filters.status === 'draft' ? 'selected' : ''}>草稿</option>
                <option value="published" ${_state.filters.status === 'published' ? 'selected' : ''}>已發布</option>
                <option value="archived"  ${_state.filters.status === 'archived' ? 'selected' : ''}>已下架</option>
            </select>
            <select id="filter-category" onchange="window._blog.setFilter('category', this.value)" style="font-size:12px;">
                <option value="">全部分類</option>
                ${_state.categories.map(c =>
                    `<option value="${esc(c.slug)}" ${_state.filters.category === c.slug ? 'selected' : ''}>${esc(c.label_zh)}</option>`
                ).join('')}
            </select>
            <input id="filter-q" type="text" placeholder="搜尋標題或 slug…"
                   value="${esc(_state.filters.q)}"
                   oninput="window._blog.setFilter('q', this.value)"
                   style="font-size:12px;flex:1;min-width:180px;" />
            <button class="btn" onclick="window._blog.openCreatePost()" style="background:#059669;">+ 新增空白文章</button>
        </div>

        <div style="color:#888;font-size:11px;margin-bottom:6px;">
            顯示 ${filtered.length} / 共 ${_state.posts.length} 篇
        </div>

        <table>
            <thead><tr>
                <th style="width:50px;">#</th>
                <th>標題</th>
                <th style="width:140px;">分類</th>
                <th style="width:90px;">狀態</th>
                <th style="width:100px;">發布日</th>
                <th style="width:60px;">轉址</th>
                <th style="width:90px;">操作</th>
            </tr></thead>
            <tbody>${
                filtered.length
                    ? filtered.map(_postRow).join('')
                    : '<tr><td colspan="7" style="padding:30px;text-align:center;color:#888;">沒有符合條件的文章</td></tr>'
            }</tbody>
        </table>
    `;
}

function _postRow(p) {
    const catLabels = (p.category_slugs || []).map(slug => {
        const c = _state.categories.find(x => x.slug === slug);
        return c ? c.label_zh : slug;
    });
    const meta = STATUS[p.status] ?? STATUS_FALLBACK;
    const pub = p.published_at
        ? new Date(p.published_at).toISOString().slice(0, 10)
        : '-';
    return `
        <tr>
            <td style="color:#666;font-family:monospace;">${esc(p.slug)}</td>
            <td>
                <div style="color:#ddd;">${esc(p.title)}</div>
                ${p.notion_page_id ? '<div style="color:#666;font-size:10px;">📥 Notion 匯入</div>' : ''}
            </td>
            <td>${catLabels.map(l => `<span class="website-pill">${esc(l)}</span>`).join(' ') || '<span style="color:#666;">未分類</span>'}</td>
            <td><span class="website-pill" style="background:${meta.color};color:#fff;">${meta.label}</span></td>
            <td style="color:#888;font-family:monospace;font-size:11px;">${pub}</td>
            <td style="text-align:center;color:${p.redirect_count ? '#f59e0b' : '#666'};">${p.redirect_count || 0}</td>
            <td>
                <button class="btn btn-sm" onclick="window._blog.openEditPost(${p.id})">編輯</button>
                <button class="btn btn-sm btn-danger" onclick="window._blog.deletePost(${p.id})">🗑</button>
            </td>
        </tr>
    `;
}

_blog.setFilter = (field, value) => {
    _state.filters[field] = value;
    document.getElementById('blog-tab-body').innerHTML = _viewPosts();
    // 保留搜尋框 focus + cursor
    if (field === 'q') {
        const el = document.getElementById('filter-q');
        if (el) { el.focus(); el.setSelectionRange(value.length, value.length); }
    }
};


// ══════════════════════════════════════════════════════════
// Post metadata Modal（Phase C 加 body 編輯）
// ══════════════════════════════════════════════════════════

let _editingPost = null;     // 目前編輯中的 post object（null = 新增模式）

_blog.openCreatePost = () => {
    _editingPost = { ...EMPTY_POST };
    _textMode = false;
    _textModeError = null;
    _showPostModal('新增文章');
};

_blog.openEditPost = async (id) => {
    const summary = _state.posts.find(x => x.id === id);
    if (!summary) { toastErr('找不到此文章'); return; }
    // 列表 API 不回 body 省 payload；編輯 Modal 開啟時才拉完整資料（含 body block 陣列）
    try {
        const full = await websiteFetch(`/api/website/admin/posts/${id}`);
        _editingPost = { ...full, body: full.body || [] };
        _textMode = false;
        _textModeError = null;
        _showPostModal(`編輯文章 #${summary.slug}`);
    } catch (e) {
        toastErr(e.message);
    }
};

function _showPostModal(title) {
    const p = _editingPost;
    const isNew = !p.id;
    // datetime-local 需要 yyyy-MM-ddTHH:mm 格式
    const pubLocal = p.published_at
        ? new Date(p.published_at).toISOString().slice(0, 16)
        : '';

    // Modal 內所有原生 input/select/textarea 統一深色 + 高對比樣式
    // （style.css `.form-group input` 只 scope 到 .form-group 容器，這裡的散裝 input
    //  會掉回瀏覽器預設 → 已填值看起來像 placeholder。scope 到 #post-modal 修正）
    const scopedCss = `
        <style>
            #post-modal input[type=text], #post-modal input[type=number],
            #post-modal input[type=datetime-local], #post-modal textarea,
            #post-modal select {
                background:#0d0d0d; border:1px solid #333; color:#f0f0f0;
                padding:7px 9px; border-radius:4px; font-family:inherit;
                box-sizing:border-box; font-size:13px;
            }
            #post-modal input:focus, #post-modal textarea:focus, #post-modal select:focus {
                outline:none; border-color:#3b82f6; background:#111;
            }
            #post-modal input::placeholder, #post-modal textarea::placeholder {
                color:#555; font-style:italic;
            }
            #post-modal label { color:#9aa0a6; font-size:11px; }
            #post-modal h3, #post-modal h4 { color:#fff; }
            #post-modal .pm-section {
                background:#1d1d1d; border:1px solid #2a2a2a;
                border-radius:6px; padding:14px; margin-bottom:14px;
            }
            #post-modal .pm-section-title {
                color:#9aa0a6; font-size:11px; text-transform:uppercase;
                letter-spacing:0.5px; margin-bottom:10px; font-weight:600;
            }
            #post-modal .pm-cat-pill {
                display:inline-flex; align-items:center; gap:5px;
                padding:5px 11px; background:#252525; border:1px solid #333;
                border-radius:14px; cursor:pointer; color:#ddd;
                font-size:12px; transition:all 0.12s;
            }
            #post-modal .pm-cat-pill:hover { border-color:#3b82f6; color:#fff; }
            #post-modal .pm-cat-pill.checked {
                background:#1e3a5f; border-color:#3b82f6; color:#fff;
            }
            #post-modal .pm-block-card {
                border:1px solid #2a2a2a; border-radius:6px; margin-bottom:8px;
                background:#1a1a1a; transition:border-color 0.15s;
            }
            #post-modal .pm-block-card:hover { border-color:#3a3a3a; }
            #post-modal .pm-block-insert {
                opacity:0; transition:opacity 0.15s;
                display:flex; gap:4px; flex-wrap:wrap;
                padding:4px 10px;
            }
            #post-modal .pm-block-card:hover + .pm-block-insert,
            #post-modal .pm-block-insert:hover { opacity:1; }
            #post-modal .pm-mini-btn {
                background:#252525; border:1px solid #333; color:#ddd;
                padding:2px 8px; border-radius:3px; font-size:11px;
                cursor:pointer; transition:all 0.12s;
            }
            #post-modal .pm-mini-btn:hover {
                background:#3b82f6; border-color:#3b82f6; color:#fff;
            }
            #post-modal .pm-mode-btn {
                background:transparent; border:none; color:#888;
                padding:5px 11px; border-radius:3px; font-size:11px;
                cursor:pointer; transition:all 0.12s;
            }
            #post-modal .pm-mode-btn:hover { color:#ddd; }
            #post-modal .pm-mode-btn.active {
                background:#252525; color:#3b82f6; font-weight:600;
            }
            #post-modal #m-body-text:focus {
                outline:none; border-color:#3b82f6;
            }
            #post-modal code {
                background:#1d1d1d; padding:1px 5px; border-radius:3px;
                color:#fbbf24; font-size:10px;
            }
        </style>
    `;

    const inner = scopedCss + `
        <div style="
            padding:12px 16px 12px 20px;border-bottom:1px solid #2a2a2a;
            display:flex;justify-content:space-between;align-items:center;gap:10px;
            position:sticky;top:0;background:#1a1a1a;z-index:2;
        ">
            <h3 style="margin:0;font-size:15px;">${esc(title)}</h3>
            <div style="display:flex;gap:6px;align-items:center;">
                ${isNew ? '' : `<button class="btn btn-sm btn-ghost" onclick="window._blog.togglePreview()" style="white-space:nowrap;">${_previewVisible ? '👁 隱藏預覽' : '👁 預覽'}</button>`}
                <button onclick="window._blog.closeModal()" title="關閉"
                        style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;font-size:14px;line-height:1;width:30px;height:30px;border-radius:4px;display:inline-flex;align-items:center;justify-content:center;">✕</button>
            </div>
        </div>

        <div id="post-modal-body" style="display:flex;gap:0;align-items:stretch;">
            <div id="post-modal-edit" style="flex:1 1 auto;padding:18px;min-width:0;">
                ${_modalBasicSection(p, isNew, pubLocal)}
                ${_modalCategorySection(p)}
                ${_modalBlockEditor(p)}
                ${_modalSEOSection(p)}
                ${_modalRedirectsSection(p)}
                ${_modalSEOHealth(p)}
            </div>
            ${isNew || !_previewVisible ? '' : `
                <div id="post-modal-preview" style="flex:0 0 50%;border-left:1px solid #2a2a2a;background:#fafafa;color:#222;padding:20px;overflow-y:auto;max-height:80vh;">
                    ${_renderPreview(p)}
                </div>`}
        </div>

        <div style="
            padding:12px 20px;border-top:1px solid #2a2a2a;
            display:flex;justify-content:space-between;align-items:center;gap:8px;
            position:sticky;bottom:0;background:#1a1a1a;z-index:2;
        ">
            <button class="btn btn-ghost btn-sm" onclick="window._blog.closeModal()">取消</button>
            <div style="display:flex;gap:8px;align-items:center;">
                ${isNew ? '' : `<button class="btn btn-sm btn-ghost" onclick="window._blog.saveAndPublish()" style="white-space:nowrap;">🚀 儲存並發布</button>`}
                <button class="btn" onclick="window._blog.savePost()" style="background:#3b82f6;white-space:nowrap;">💾 儲存</button>
            </div>
        </div>
    `;
    openModal('post-modal', inner, { width: _previewVisible ? '1180px' : '780px' });
}

let _previewVisible = true;

function _modalBasicSection(p, isNew, pubLocal) {
    const noPostId = !p.id;
    const uploadDisabledAttr = noPostId
        ? 'disabled style="opacity:0.5;cursor:not-allowed;" title="新文章請先儲存草稿才能上傳封面"'
        : '';

    return `
        <div class="pm-section">
            <div style="margin-bottom:14px;">
                <label style="display:block;margin-bottom:5px;">標題 <span style="color:#f87171;">*</span></label>
                <input id="m-title" type="text" value="${esc(p.title)}" style="width:100%;font-size:15px;font-weight:500;" />
            </div>
            <div style="display:grid;grid-template-columns:140px 1fr;gap:12px;margin-bottom:14px;">
                <div>
                    <label style="display:block;margin-bottom:5px;">Slug ${isNew ? '<span style="color:#666;">（自動）</span>' : ''}</label>
                    <input id="m-slug" type="text" value="${esc(p.slug)}" placeholder="auto" style="width:100%;font-family:monospace;" />
                </div>
                <div>
                    <label style="display:block;margin-bottom:5px;">封面圖</label>
                    <div id="m-cover-wrap" style="display:flex;gap:10px;align-items:flex-start;">
                        ${_coverThumbHtml(p.cover_url)}
                        <div style="flex:1;display:flex;flex-direction:column;gap:5px;min-width:0;">
                            <div style="display:flex;gap:6px;">
                                <label class="btn btn-sm btn-ghost" style="margin:0;cursor:${noPostId ? 'not-allowed' : 'pointer'};font-size:11px;${noPostId ? 'opacity:0.5;' : ''}white-space:nowrap;" ${noPostId ? 'title="新文章請先儲存草稿"' : ''}>
                                    📤 ${p.cover_url ? '換封面' : '上傳檔案'}
                                    <input type="file" accept="image/*" style="display:none;"
                                           ${uploadDisabledAttr}
                                           onchange="window._blog.uploadCover(this)" />
                                </label>
                                <input id="m-cover" type="text" value="${esc(p.cover_url || '')}"
                                       placeholder="或貼 URL（https://... 或 /uploads/...）"
                                       oninput="window._blog.refreshCoverThumb(this.value)"
                                       style="flex:1;min-width:0;" />
                            </div>
                            ${noPostId
                                ? '<div style="color:#666;font-size:10px;">💡 新文章請先儲存草稿（取得 ID）後才能上傳封面</div>'
                                : ''}
                        </div>
                    </div>
                </div>
            </div>
            <div>
                <label style="display:block;margin-bottom:5px;">摘要 <span style="color:#666;">（首頁與 SERP description fallback）</span></label>
                <textarea id="m-excerpt" rows="2" style="width:100%;">${esc(p.excerpt || '')}</textarea>
            </div>
        </div>

        <div class="pm-section">
            <div class="pm-section-title">📅 發布管理</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:end;">
                <div>
                    <label style="display:block;margin-bottom:5px;">狀態</label>
                    <select id="m-status" style="width:100%;">
                        ${Object.entries(STATUS).map(([key, m]) =>
                            `<option value="${key}" ${p.status === key ? 'selected' : ''}>${m.emoji} ${m.label}</option>`
                        ).join('')}
                    </select>
                </div>
                <div>
                    <label style="display:block;margin-bottom:5px;">發布時間 <span style="color:#666;">（未來=排程）</span></label>
                    <input id="m-published" type="datetime-local" value="${pubLocal}" style="width:100%;" />
                </div>
            </div>
        </div>
    `;
}

function _modalCategorySection(p) {
    const selected = new Set(p.category_slugs || []);
    return `
        <div class="pm-section">
            <div class="pm-section-title">🏷️ 分類（多選）</div>
            <div id="m-cats" style="display:flex;flex-wrap:wrap;gap:6px;">
                ${_state.categories.map(c => {
                    const checked = selected.has(c.slug);
                    return `
                        <label class="pm-cat-pill ${checked ? 'checked' : ''}">
                            <input type="checkbox" value="${esc(c.slug)}" ${checked ? 'checked' : ''}
                                   onchange="this.parentElement.classList.toggle('checked', this.checked)"
                                   style="margin:0;" />
                            ${esc(c.label_zh)}
                        </label>
                    `;
                }).join('')}
            </div>
            ${_state.categories.length === 0
                ? '<div style="color:#888;font-size:11px;margin-top:6px;">尚無分類，到「📚 分類」分頁新增</div>'
                : ''}
        </div>
    `;
}

function _modalSEOSection(p) {
    return `
        <details style="margin-bottom:14px;background:#1d1d1d;border:1px solid #2a2a2a;border-radius:6px;">
            <summary style="padding:11px 14px;cursor:pointer;color:#3b82f6;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">
                🔍 SEO 覆寫 <span style="color:#666;font-weight:400;text-transform:none;letter-spacing:0;">（選填，空則自動推算）</span>
            </summary>
            <div style="padding:12px 14px 14px;border-top:1px solid #2a2a2a;">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                    <div>
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">SEO Title 覆寫</label>
                        <input id="m-seo-title" type="text" value="${esc(p.seo_title || '')}" maxlength="200" style="width:100%;" />
                    </div>
                    <div>
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">OG Image 覆寫</label>
                        <input id="m-og-image" type="text" value="${esc(p.og_image_url || '')}" placeholder="不填用 cover_url" style="width:100%;" />
                    </div>
                </div>
                <div style="margin-top:10px;">
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">SEO Description 覆寫（不填用 excerpt）</label>
                    <textarea id="m-seo-desc" rows="2" maxlength="300" style="width:100%;">${esc(p.seo_description || '')}</textarea>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px;">
                    <div>
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">作者姓名</label>
                        <input id="m-author-name" type="text" value="${esc(p.author_name || '')}" placeholder="不填用公司名" style="width:100%;" />
                    </div>
                    <div>
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">作者連結</label>
                        <input id="m-author-url" type="text" value="${esc(p.author_url || '')}" style="width:100%;" />
                    </div>
                </div>
                <div style="margin-top:10px;">
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">Canonical URL（跨站發布時指向原作）</label>
                    <input id="m-canonical" type="text" value="${esc(p.canonical_url || '')}" style="width:100%;" />
                </div>
                <div style="margin-top:10px;display:flex;gap:16px;">
                    <label style="display:flex;align-items:center;gap:6px;color:#ddd;font-size:12px;">
                        <input id="m-noindex" type="checkbox" ${p.noindex ? 'checked' : ''} />
                        強制 noindex（個別頁不被索引）
                    </label>
                </div>
            </div>
        </details>
    `;
}

function _modalRedirectsSection(p) {
    const oldUrls = (p.old_urls || []).join('\n');
    return `
        <details style="margin-bottom:14px;background:#1d1d1d;border:1px solid #2a2a2a;border-radius:6px;">
            <summary style="padding:11px 14px;cursor:pointer;color:#f59e0b;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">
                🔗 SEO 301 轉址 <span style="color:#666;font-weight:400;text-transform:none;letter-spacing:0;">（${p.old_urls?.length || 0} 條 · 從舊網址轉到此文）</span>
            </summary>
            <div style="padding:12px 14px 14px;border-top:1px solid #2a2a2a;">
                <div style="color:#888;font-size:11px;margin-bottom:6px;line-height:1.6;">
                    一行一個舊網址（含或不含網域都可，自動 strip 為相對路徑）。<br/>
                    軟 301（Astro static page）+ 硬 301（NAS nginx）雙保險，Google 將舊 URL 權重轉到此文。
                </div>
                <textarea id="m-old-urls" rows="4" placeholder="/blog/2020/old-article&#10;/old-news/45" style="width:100%;font-family:monospace;font-size:12px;">${esc(oldUrls)}</textarea>
            </div>
        </details>
    `;
}

// ══════════════════════════════════════════════════════════
// Block Editor — 6 種 block (paragraph/heading/image/video/quote/list)
// ══════════════════════════════════════════════════════════

// ── inline updateBlockField binding helpers ──
// 集中於三個 cast 模式（text/checkbox/number），消除 ~30 處重複的 oninput 字串
const _bindText = (idx, field) =>
    `oninput="window._blog.updateBlockField(${idx}, '${field}', this.value)"`;
const _bindCheck = (idx, field) =>
    `onchange="window._blog.updateBlockField(${idx}, '${field}', this.checked)"`;
const _bindNumber = (idx, field) =>
    `onchange="window._blog.updateBlockField(${idx}, '${field}', Number(this.value))"`;


// _textMode = false → 視覺 block 編輯（預設）；true → markdown 純文字
let _textMode = false;
let _textModeError = null;   // {line, msg} or null

function _modalBlockEditor(p) {
    const blocks = p.body || [];
    const headerRight = `
        <div style="display:flex;align-items:center;gap:8px;">
            <div style="display:inline-flex;background:#0d0d0d;border-radius:4px;padding:2px;">
                <button class="pm-mode-btn ${!_textMode ? 'active' : ''}" onclick="window._blog.switchMode(false)">📝 視覺</button>
                <button class="pm-mode-btn ${_textMode ? 'active' : ''}" onclick="window._blog.switchMode(true)">📄 純文字</button>
            </div>
        </div>
    `;

    return `
        <div class="pm-section" style="border-left:3px solid #f59e0b;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
                <div class="pm-section-title" style="margin:0;">📝 內文 <span id="post-blocks-count" style="text-transform:none;color:#666;">(${blocks.length} blocks)</span></div>
                ${headerRight}
            </div>

            <div id="post-blocks-host">
                ${_textMode ? _textModeView(p) : _visualModeView(blocks)}
            </div>
        </div>
    `;
}

function _visualModeView(blocks) {
    const insertBar = `
        <div style="background:#0d0d0d;padding:6px 8px;border-radius:4px;margin-bottom:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
            <span style="color:#666;font-size:11px;">快速插入：</span>
            ${Object.entries(BLOCK_REGISTRY).map(([key, t]) =>
                `<button class="pm-mini-btn" onclick="window._blog.appendBlock('${key}')" title="加到結尾">${t.label}</button>`
            ).join('')}
        </div>
    `;
    const body = blocks.length === 0
        ? `<div style="color:#666;font-size:12px;text-align:center;padding:24px;border:1px dashed #2a2a2a;border-radius:4px;">
             尚無內文 — 用上方按鈕新增第一個 block
           </div>`
        : `<div id="post-blocks">${blocks.map((b, i) => _renderBlockItem(b, i, blocks.length)).join('')}</div>`;
    return insertBar + body;
}

function _textModeView(p) {
    const md = _blocksToMarkdown(p.body || []);
    const noPostId = !p.id;
    return `
        <div style="background:#0d0d0d;padding:6px 8px;border-radius:4px;margin-bottom:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
            <label class="pm-mini-btn" style="margin:0;cursor:${noPostId ? 'not-allowed' : 'pointer'};${noPostId ? 'opacity:0.5;' : ''}" ${noPostId ? 'title="新文章請先儲存草稿"' : 'title="上傳圖檔，自動插入 markdown"'}>
                📤 圖片
                <input type="file" accept="image/*" style="display:none;" ${noPostId ? 'disabled' : ''}
                       onchange="window._blog.textModeUploadImage(this)" />
            </label>
            <button class="pm-mini-btn" onclick="window._blog.textModeInsertVideo()" title="貼 YouTube URL → 插入 video block">▶ YouTube</button>
            <span style="color:#444;">|</span>
            <button class="pm-mini-btn" onclick="window._blog.textModeInsert('paragraph')" title="插入段落樣板">¶ 段落</button>
            <button class="pm-mini-btn" onclick="window._blog.textModeInsert('heading')" title="插入 H2 標題">H 標題</button>
            <button class="pm-mini-btn" onclick="window._blog.textModeInsert('quote')" title="插入引言">❝ 引言</button>
            <button class="pm-mini-btn" onclick="window._blog.textModeInsert('list')" title="插入列表">• 列表</button>
            <span id="text-mode-stats" style="margin-left:auto;color:#666;font-size:11px;font-family:monospace;"></span>
        </div>

        <div id="text-mode-error" style="display:${_textModeError ? 'block' : 'none'};background:#3a1a1a;border:1px solid #6b2222;color:#fca5a5;padding:8px 10px;border-radius:4px;margin-bottom:8px;font-size:12px;">
            ${_textModeError ? esc(_textModeError.msg) : ''}
        </div>

        <textarea id="m-body-text"
                  oninput="window._blog.textModeSync()"
                  onkeyup="window._blog._statsTick()"
                  onclick="window._blog._statsTick()"
                  ondrop="window._blog.textModeDrop(event)"
                  ondragover="event.preventDefault()"
                  spellcheck="false"
                  style="width:100%;min-height:340px;font-family:'JetBrains Mono','Consolas',monospace;font-size:13px;line-height:1.6;background:#0d0d0d;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:12px;resize:vertical;tab-size:2;">${esc(md)}</textarea>

        <div style="margin-top:6px;color:#666;font-size:10.5px;line-height:1.5;">
            語法速查：<code>## 標題</code> · <code>![alt](url){.wide}</code> 圖片 · <code>::: video YT_ID ::: </code> 影片 · <code>&gt; 引言</code> · <code>- 列表</code> · 圖片下一行寫 <code>*圖說*</code> 變 caption · 段首寫 <code>{.lead}</code> 變引言段落
        </div>
    `;
}


// ── Markdown ↔ Block 雙向轉換 ──────────────────────────────────────

const _MD_IMG_RE  = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)(?:\{\.(content|wide|full)\})?\s*$/;
const _MD_CAP_RE  = /^\*([^*].*?)\*\s*$/;
const _MD_VIDEO_OPEN_RE = /^:::\s*video\s+(\S+)/;
const _MD_LEAD_RE = /^\{\.lead\}\s*$/;

function _blocksToMarkdown(blocks) {
    return blocks.map(b => {
        switch (b.type) {
            case 'paragraph': {
                const lead = b.lead ? '{.lead}\n' : '';
                return lead + (b.text || '');
            }
            case 'heading': {
                const hashes = '#'.repeat(b.level === 3 ? 3 : 2);
                return `${hashes} ${b.text || ''}`;
            }
            case 'image': {
                const w = b.width && b.width !== 'content' ? `{.${b.width}}` : '';
                const cap = b.caption ? `\n*${b.caption}*` : '';
                return `![${b.alt || ''}](${b.src || ''})${w}${cap}`;
            }
            case 'video': {
                const lines = [`::: video ${b.youtube_id || ''}`];
                if (b.caption)                     lines.push(`caption: ${b.caption}`);
                if (b.width && b.width !== 'content') lines.push(`width: ${b.width}`);
                lines.push(':::');
                return lines.join('\n');
            }
            case 'quote': {
                const body = (b.text || '').split('\n').map(l => `> ${l}`).join('\n');
                return body + (b.author ? `\n> — ${b.author}` : '');
            }
            case 'list': {
                return (b.items || []).map((it, i) =>
                    (b.ordered ? `${i + 1}. ` : '- ') + it
                ).join('\n');
            }
            default:
                // 不認識的 type 用 HTML comment + JSON 保留，不丟資料
                return `<!-- unknown block: ${JSON.stringify(b)} -->`;
        }
    }).join('\n\n');
}

/**
 * 解析 markdown 字串成 block 陣列。
 * @returns {{blocks: PostBlock[], error: {line, msg}|null}}
 *          解析過程遇到語法錯誤會繼續往下走（盡量保留資料），但回 error
 *          物件給 UI 顯示行號 + 訊息。
 */
function _markdownToBlocks(md) {
    const lines = (md || '').split(/\r?\n/);
    const blocks = [];
    let error = null;
    let i = 0;

    const isBlockBoundary = (l) => !l.trim()
        || /^#{2,3}\s+/.test(l)
        || /^!\[/.test(l)
        || l.startsWith(':::')
        || l.startsWith('> ')
        || /^-\s+/.test(l)
        || /^\d+\.\s+/.test(l)
        || _MD_LEAD_RE.test(l);

    while (i < lines.length) {
        const line = lines[i];

        if (!line.trim()) { i++; continue; }

        // 不認識的 block 的 round-trip comment
        const unknownMatch = line.match(/^<!--\s*unknown block:\s*(.+?)\s*-->$/);
        if (unknownMatch) {
            try { blocks.push(JSON.parse(unknownMatch[1])); }
            catch { /* 壞掉就丟掉 */ }
            i++; continue;
        }

        // Heading: ## or ###
        const h = line.match(/^(#{2,3})\s+(.+?)\s*$/);
        if (h) {
            blocks.push({ type: 'heading', level: h[1].length, text: h[2] });
            i++; continue;
        }

        // Image
        const img = line.match(_MD_IMG_RE);
        if (img) {
            const block = {
                type: 'image',
                src: img[2],
                alt: img[1] || '',
                width: img[3] || 'content',
            };
            // 下一行如果是 *caption* → 吃掉當 caption
            const next = lines[i + 1];
            if (next) {
                const c = next.match(_MD_CAP_RE);
                if (c) { block.caption = c[1]; i++; }
            }
            blocks.push(block);
            i++; continue;
        }

        // Video fenced block
        const v = line.match(_MD_VIDEO_OPEN_RE);
        if (v) {
            let id = v[1];
            const ytm = id.match(_YT_RE);
            if (ytm) id = ytm[1];
            const block = { type: 'video', youtube_id: id, width: 'content' };
            const startLine = i + 1;
            i++;
            let closed = false;
            while (i < lines.length) {
                if (lines[i].trim().startsWith(':::')) { closed = true; i++; break; }
                const kv = lines[i].match(/^\s*(caption|width)\s*:\s*(.+?)\s*$/);
                if (kv) {
                    if (kv[1] === 'width' && ['content', 'wide', 'full'].includes(kv[2])) {
                        block.width = kv[2];
                    } else if (kv[1] === 'caption') {
                        block.caption = kv[2];
                    }
                }
                i++;
            }
            if (!closed && !error) {
                error = { line: startLine, msg: `第 ${startLine} 行：::: video 區塊缺少結尾 :::` };
            }
            blocks.push(block);
            continue;
        }

        // Quote (greedy: collect consecutive `> ` lines)
        if (line.startsWith('> ')) {
            const textLines = [];
            let author = null;
            while (i < lines.length && lines[i].startsWith('> ')) {
                const t = lines[i].slice(2);
                const am = t.match(/^—\s+(.+)$/);
                if (am) author = am[1];
                else textLines.push(t);
                i++;
            }
            const block = { type: 'quote', text: textLines.join('\n') };
            if (author) block.author = author;
            blocks.push(block);
            continue;
        }

        // List (ul or ol)
        const ulFirst = line.match(/^-\s+(.+)$/);
        const olFirst = line.match(/^\d+\.\s+(.+)$/);
        if (ulFirst || olFirst) {
            const ordered = !!olFirst;
            const items = [];
            const re = ordered ? /^\d+\.\s+(.+)$/ : /^-\s+(.+)$/;
            while (i < lines.length) {
                const m = lines[i].match(re);
                if (!m) break;
                items.push(m[1]);
                i++;
            }
            blocks.push({ type: 'list', items, ordered });
            continue;
        }

        // Paragraph: collect 連續非邊界行
        const isLead = _MD_LEAD_RE.test(line);
        if (isLead) i++;
        const paraLines = [];
        while (i < lines.length && !isBlockBoundary(lines[i])) {
            paraLines.push(lines[i]);
            i++;
        }
        if (paraLines.length || isLead) {
            const block = { type: 'paragraph', text: paraLines.join('\n') };
            if (isLead) block.lead = true;
            blocks.push(block);
        }
    }

    return { blocks, error };
}


// 各 block type 顯示圖示（取代 raw type string，視覺更直觀）
const BLOCK_TYPE_ICON = {
    paragraph: '¶', heading: 'H', image: '🖼', video: '▶', quote: '❝', list: '•',
};

function _renderBlockItem(b, idx, total) {
    const formFn = BLOCK_REGISTRY[b.type]?.form || _UNKNOWN_BLOCK_FORM;
    const icon = BLOCK_TYPE_ICON[b.type] || '?';
    return `
        <div data-block-idx="${idx}" class="pm-block-card">
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:#222;border-bottom:1px solid #2a2a2a;border-radius:6px 6px 0 0;">
                <span style="color:#aaa;font-size:11px;display:flex;align-items:center;gap:6px;">
                    <span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;background:#0d0d0d;border-radius:3px;color:#3b82f6;font-weight:600;font-size:10px;">${icon}</span>
                    #${idx + 1} <span style="color:#666;">· ${esc(b.type)}</span>
                </span>
                <div style="display:flex;gap:3px;">
                    <button class="pm-mini-btn" onclick="window._blog.moveBlockUp(${idx})" ${idx === 0 ? 'disabled style="opacity:0.3;cursor:not-allowed;"' : ''} title="上移">↑</button>
                    <button class="pm-mini-btn" onclick="window._blog.moveBlockDown(${idx})" ${idx === total - 1 ? 'disabled style="opacity:0.3;cursor:not-allowed;"' : ''} title="下移">↓</button>
                    <button class="pm-mini-btn" onclick="window._blog.removeBlock(${idx})" title="刪除" style="background:#3a1a1a;border-color:#5a2a2a;color:#f87171;">✕</button>
                </div>
            </div>
            <div style="padding:10px;">
                ${formFn(b, idx)}
            </div>
        </div>
        <div class="pm-block-insert">
            ${Object.entries(BLOCK_REGISTRY).map(([key, t]) =>
                `<button class="pm-mini-btn" onclick="window._blog.insertBlockAt(${idx + 1}, '${key}')" style="font-size:10px;">+ ${t.label}</button>`
            ).join('')}
        </div>
    `;
}


// === 6 個 block-type form renderer ===
// 每個 input 用 oninput 直接更新 _editingPost.body[idx][field]
// 不觸發整個 Modal re-render（保持 cursor focus），只在新增/刪除/排序時 refresh

// ── BLOCK_REGISTRY：每種 block type 一個物件含 label / defaults / form / preview ──
// 加新 block type 只需在這裡加一筆（不再需要動 BLOCK_TYPES + BLOCK_FORMS + switch 三處）

const BLOCK_REGISTRY = {
    paragraph: {
        label: '¶ 段落',
        defaults: { type: 'paragraph', text: '' },
        form: (b, idx) => `
            <textarea rows="3" style="width:100%;font-family:inherit;" ${_bindText(idx, 'text')}>${esc(b.text || '')}</textarea>
            <label style="display:flex;gap:6px;align-items:center;color:#aaa;font-size:11px;margin-top:6px;cursor:pointer;">
                <input type="checkbox" ${b.lead ? 'checked' : ''} ${_bindCheck(idx, 'lead')} />
                Lead 段（首段引言放大字）
            </label>
        `,
        preview: (b) => {
            const cls = b.lead
                ? 'style="font-size:18px;line-height:1.7;color:#333;margin:0 0 14px;"'
                : 'style="font-size:14px;line-height:1.7;color:#444;margin:0 0 12px;"';
            return `<p ${cls}>${esc(b.text || '')}</p>`;
        },
    },

    heading: {
        label: 'H 標題',
        defaults: { type: 'heading', level: 2, text: '' },
        form: (b, idx) => `
            <div style="display:grid;grid-template-columns:80px 1fr;gap:8px;align-items:center;">
                <select ${_bindNumber(idx, 'level')} style="font-size:11px;">
                    <option value="2" ${b.level === 2 ? 'selected' : ''}>H2</option>
                    <option value="3" ${b.level === 3 ? 'selected' : ''}>H3</option>
                </select>
                <input type="text" value="${esc(b.text || '')}" placeholder="標題文字"
                       style="width:100%;font-size:14px;font-weight:600;" ${_bindText(idx, 'text')} />
            </div>
        `,
        preview: (b) => {
            const tag = b.level === 2 ? 'h2' : 'h3';
            const size = b.level === 2 ? '20px' : '17px';
            return `<${tag} style="font-size:${size};font-weight:700;margin:18px 0 8px;color:#222;">${esc(b.text || '')}</${tag}>`;
        },
    },

    image: {
        label: '🖼 圖片',
        defaults: { type: 'image', src: '', alt: '', caption: '', width: 'content' },
        form: (b, idx) => {
            const previewSrc = b.src || '';
            const altWarn = !b.alt ? `<span style="color:#f59e0b;">⚠ 缺 alt 影響 SEO + 無障礙</span>` : '';
            const noPostId = !_editingPost?.id;
            const uploadDisabled = noPostId
                ? 'disabled style="opacity:0.5;cursor:not-allowed;" title="新文章請先儲存草稿才能上傳圖片"'
                : '';
            return `
                ${previewSrc
                    ? `<img src="${esc(_resolveImageUrl(previewSrc))}" style="max-width:100%;max-height:140px;display:block;margin-bottom:6px;border:1px solid #2a2a2a;border-radius:4px;background:#0d0d0d;" onerror="this.style.opacity='0.3'" />`
                    : '<div style="background:#0d0d0d;border:1px dashed #333;border-radius:4px;padding:14px;text-align:center;color:#555;font-size:11px;margin-bottom:6px;">📷 尚未上傳圖片（拖檔或貼 URL）</div>'}

                <div style="display:flex;gap:6px;margin-bottom:6px;align-items:center;">
                    <label class="btn btn-sm btn-ghost" style="margin:0;cursor:${noPostId ? 'not-allowed' : 'pointer'};font-size:11px;${noPostId ? 'opacity:0.5;' : ''}" ${noPostId ? 'title="新文章請先儲存草稿"' : ''}>
                        📤 ${previewSrc ? '換圖' : '上傳檔案'}
                        <input type="file" accept="image/*" style="display:none;" ${uploadDisabled}
                               onchange="window._blog.uploadBlockImage(${idx}, this)" />
                    </label>
                    <input type="text" value="${esc(previewSrc)}" placeholder="或貼 URL"
                           style="flex:1;font-size:11px;"
                           oninput="window._blog.updateBlockField(${idx}, 'src', this.value);window._blog.refreshBlockItem(${idx});" />
                </div>

                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:4px;">
                    <input type="text" value="${esc(b.alt || '')}" placeholder="alt 文字（必填）"
                           style="width:100%;font-size:11px;" ${_bindText(idx, 'alt')} />
                    <input type="text" value="${esc(b.caption || '')}" placeholder="caption（選填）"
                           style="width:100%;font-size:11px;" ${_bindText(idx, 'caption')} />
                </div>

                <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#888;">
                    <span>${altWarn}</span>
                    <label>寬度：
                        <select ${_bindText(idx, 'width').replace('oninput', 'onchange')} style="font-size:11px;padding:2px 4px;">
                            <option value="content" ${(b.width || 'content') === 'content' ? 'selected' : ''}>content (680px)</option>
                            <option value="wide"    ${b.width === 'wide' ? 'selected' : ''}>wide (1024px)</option>
                            <option value="full"    ${b.width === 'full' ? 'selected' : ''}>full (滿版)</option>
                        </select>
                    </label>
                </div>
            `;
        },
        preview: (b) => {
            const w = b.width === 'full' ? '100%' : b.width === 'wide' ? '110%' : '100%';
            const cap = b.caption
                ? `<figcaption style="text-align:center;color:#888;font-size:11px;margin-top:4px;">${esc(b.caption)}</figcaption>`
                : '';
            return `<figure style="margin:14px 0;width:${w};">
                ${b.src
                    ? `<img src="${esc(_resolveImageUrl(b.src))}" alt="${esc(b.alt || '')}" style="width:100%;border-radius:4px;display:block;" />`
                    : '<div style="background:#eee;padding:30px;text-align:center;color:#999;font-size:12px;border-radius:4px;">未設圖</div>'}
                ${cap}
            </figure>`;
        },
    },

    video: {
        label: '▶ YouTube',
        defaults: { type: 'video', youtube_id: '', caption: '', width: 'content' },
        form: (b, idx) => {
            const id = b.youtube_id || '';
            const validId = /^[A-Za-z0-9_-]{11}$/.test(id);
            const thumb = validId ? `https://img.youtube.com/vi/${id}/hqdefault.jpg` : '';
            return `
                <div style="display:flex;gap:8px;align-items:flex-start;">
                    ${thumb
                        ? `<img src="${thumb}" alt="YouTube preview" style="width:120px;height:68px;object-fit:cover;border-radius:4px;flex-shrink:0;" />`
                        : '<div style="width:120px;height:68px;background:#0d0d0d;border:1px dashed #333;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#555;font-size:11px;flex-shrink:0;">無預覽</div>'}
                    <div style="flex:1;min-width:0;">
                        <input type="text" value="${esc(id)}" placeholder="YouTube URL 或 11-char ID"
                               style="width:100%;font-size:11px;margin-bottom:4px;"
                               oninput="window._blog.parseYoutubeAndStore(${idx}, this.value);" />
                        <div style="font-size:10px;color:${validId ? '#4ade80' : '#f59e0b'};margin-bottom:6px;">
                            ${validId ? `✓ ID: ${id}` : '⚠ 解析中...貼 https://youtu.be/XXX 或 https://youtube.com/watch?v=XXX'}
                        </div>
                        <input type="text" value="${esc(b.caption || '')}" placeholder="caption（選填）"
                               style="width:100%;font-size:11px;margin-bottom:4px;" ${_bindText(idx, 'caption')} />
                        <label style="font-size:11px;color:#888;">寬度：
                            <select ${_bindText(idx, 'width').replace('oninput', 'onchange')} style="font-size:11px;padding:2px 4px;">
                                <option value="content" ${(b.width || 'content') === 'content' ? 'selected' : ''}>content</option>
                                <option value="wide"    ${b.width === 'wide' ? 'selected' : ''}>wide</option>
                                <option value="full"    ${b.width === 'full' ? 'selected' : ''}>full</option>
                            </select>
                        </label>
                    </div>
                </div>
            `;
        },
        preview: (b) => {
            const validId = /^[A-Za-z0-9_-]{11}$/.test(b.youtube_id || '');
            const cap = b.caption
                ? `<figcaption style="text-align:center;color:#888;font-size:11px;margin-top:4px;">${esc(b.caption)}</figcaption>`
                : '';
            return `<figure style="margin:14px 0;">
                ${validId
                    ? `<div style="position:relative;padding-bottom:56.25%;height:0;border-radius:4px;overflow:hidden;background:#000;">
                        <img src="https://img.youtube.com/vi/${b.youtube_id}/hqdefault.jpg" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;" />
                        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:48px;color:#fff;text-shadow:0 0 8px rgba(0,0,0,0.6);">▶</div>
                       </div>`
                    : '<div style="background:#eee;padding:30px;text-align:center;color:#999;font-size:12px;border-radius:4px;">未設 YouTube ID</div>'}
                ${cap}
            </figure>`;
        },
    },

    quote: {
        label: '❝ 引言',
        defaults: { type: 'quote', text: '', author: '' },
        form: (b, idx) => `
            <textarea rows="2" placeholder="引用內容" style="width:100%;font-style:italic;font-family:inherit;" ${_bindText(idx, 'text')}>${esc(b.text || '')}</textarea>
            <input type="text" value="${esc(b.author || '')}" placeholder="作者 / 出處（選填）"
                   style="width:100%;font-size:11px;margin-top:4px;" ${_bindText(idx, 'author')} />
        `,
        preview: (b) => `
            <blockquote style="border-left:3px solid #c9372c;padding:6px 14px;margin:14px 0;color:#444;font-style:italic;">
                ${esc(b.text || '')}
                ${b.author ? `<footer style="font-size:11px;color:#888;margin-top:6px;font-style:normal;">— ${esc(b.author)}</footer>` : ''}
            </blockquote>
        `,
    },

    list: {
        label: '• 列表',
        defaults: { type: 'list', items: [''], ordered: false },
        form: (b, idx) => {
            const items = b.items || [''];
            return `
                <label style="display:flex;gap:6px;align-items:center;color:#aaa;font-size:11px;margin-bottom:6px;cursor:pointer;">
                    <input type="checkbox" ${b.ordered ? 'checked' : ''} ${_bindCheck(idx, 'ordered')} />
                    編號列表（1. 2. 3.）
                </label>
                <div id="list-items-${idx}">
                    ${items.map((it, i) => `
                        <div style="display:flex;gap:4px;margin-bottom:4px;">
                            <span style="color:#666;font-size:11px;width:18px;text-align:center;flex-shrink:0;padding-top:6px;">${b.ordered ? i + 1 + '.' : '•'}</span>
                            <input type="text" value="${esc(it)}" placeholder="條目"
                                   style="flex:1;font-size:12px;"
                                   oninput="window._blog.updateListItem(${idx}, ${i}, this.value)" />
                            <button class="btn btn-sm btn-danger" style="padding:2px 6px;font-size:10px;"
                                    onclick="window._blog.removeListItem(${idx}, ${i})">✕</button>
                        </div>
                    `).join('')}
                </div>
                <button class="btn btn-sm btn-ghost" onclick="window._blog.addListItem(${idx})" style="margin-top:4px;font-size:11px;">+ 加一項</button>
            `;
        },
        preview: (b) => {
            const tag = b.ordered ? 'ol' : 'ul';
            const items = (b.items || []).map(it => `<li style="margin-bottom:4px;">${esc(it)}</li>`).join('');
            return `<${tag} style="font-size:14px;line-height:1.6;color:#444;padding-left:24px;margin:12px 0;">${items}</${tag}>`;
        },
    },
};

const _UNKNOWN_BLOCK_FORM = (b) =>
    `<pre style="color:#f87171;font-size:11px;background:#0d0d0d;padding:6px;border-radius:4px;">不認識的 block type "${esc(b.type)}"，原始 JSON：\n${esc(JSON.stringify(b, null, 2))}</pre>`;
const _UNKNOWN_BLOCK_PREVIEW = (b) =>
    `<pre style="background:#fee;color:#900;padding:6px;font-size:11px;">未知 block: ${esc(b.type)}</pre>`;


// ══════════════════════════════════════════════════════════
// Block 操作 handlers
// ══════════════════════════════════════════════════════════

function _refreshBlocks() {
    if (!_editingPost) return;
    const blocks = _editingPost.body || [];
    // 視覺模式：重 render 整個 #post-blocks-host（含 toolbar + 內容）。
    // 之前只重畫 #post-blocks，但空 body 時 _visualModeView 不會生那個容器
    // → 第一個 block 加進來時找不到 host → 畫面卡在「尚無內文」狀態。
    if (!_textMode) {
        const host = document.getElementById('post-blocks-host');
        if (host) {
            host.innerHTML = _visualModeView(blocks);
        }
    }
    if (_textMode) {
        const ta = document.getElementById('m-body-text');
        if (ta && document.activeElement !== ta) {
            ta.value = _blocksToMarkdown(blocks);
            _updateTextModeStats();
        }
    }
    const cnt = document.getElementById('post-blocks-count');
    if (cnt) cnt.textContent = `(${blocks.length} blocks)`;
    _refreshPreview();
    _refreshSEOHealth();
}

// 切換編輯模式（視覺 ↔ 文字）
_blog.switchMode = (toText) => {
    if (!_editingPost) return;
    if (toText === _textMode) return;

    if (toText) {
        // 視覺 → 文字：直接重畫，_blocksToMarkdown 從 _editingPost.body 拿
        _textMode = true;
        _textModeError = null;
    } else {
        // 文字 → 視覺：先 sync textarea 到 _editingPost.body
        const ta = document.getElementById('m-body-text');
        if (ta) {
            const { blocks, error } = _markdownToBlocks(ta.value);
            if (error) {
                _textModeError = error;
                toastErr(error.msg);
                _renderHostOnly();
                return;
            }
            _editingPost.body = blocks;
        }
        _textMode = false;
        _textModeError = null;
    }
    _renderHostOnly();
}

// 只重畫 #post-blocks-host（避免整個 modal re-render 失去其他欄位 cursor）
function _renderHostOnly() {
    const host = document.getElementById('post-blocks-host');
    if (!host || !_editingPost) return;
    host.innerHTML = _textMode ? _textModeView(_editingPost) : _visualModeView(_editingPost.body || []);
    if (_textMode) _updateTextModeStats();
    const cnt = document.getElementById('post-blocks-count');
    if (cnt) cnt.textContent = `(${(_editingPost.body || []).length} blocks)`;
    _refreshPreview();
    _refreshSEOHealth();
}

// 文字模式：textarea 變動 → 即時 parse → 更新 _editingPost.body + preview
// debounce 200ms 避免每按一鍵全 re-parse
let _textSyncTimer = null;
_blog.textModeSync = () => {
    if (_textSyncTimer) clearTimeout(_textSyncTimer);
    _textSyncTimer = setTimeout(() => {
        const ta = document.getElementById('m-body-text');
        if (!ta || !_editingPost) return;
        const { blocks, error } = _markdownToBlocks(ta.value);
        _editingPost.body = blocks;
        _textModeError = error;
        const errEl = document.getElementById('text-mode-error');
        if (errEl) {
            errEl.style.display = error ? 'block' : 'none';
            if (error) errEl.textContent = error.msg;
        }
        _updateTextModeStats();
        const cnt = document.getElementById('post-blocks-count');
        if (cnt) cnt.textContent = `(${blocks.length} blocks)`;
        _refreshPreview();
        _refreshSEOHealth();
    }, 200);
};

// 給 onkeyup/onclick 直接呼叫的 alias（避免重複 timer，stats 不需 debounce）
_blog._statsTick = () => _updateTextModeStats();

function _updateTextModeStats() {
    const ta = document.getElementById('m-body-text');
    const stats = document.getElementById('text-mode-stats');
    if (!ta || !stats) return;
    const v = ta.value || '';
    const { lineNo, colNo } = _cursorLineCol(ta);
    stats.textContent = `${v.length} 字 · 行 ${lineNo} 列 ${colNo}`;
}

function _cursorLineCol(ta) {
    const pos = ta.selectionStart || 0;
    const before = ta.value.slice(0, pos);
    const lines = before.split('\n');
    return { lineNo: lines.length, colNo: lines[lines.length - 1].length + 1 };
}

// 共用：在 textarea cursor 處插入 snippet，並把 selection 移到 placeholder
function _insertAtCursor(ta, snippet, selectMatch) {
    const pos = ta.selectionStart || 0;
    const before = ta.value.slice(0, pos);
    const after = ta.value.slice(pos);
    // 段落間自動補空行（避免黏在前一行尾巴）
    const sep = (before && !before.endsWith('\n\n')) ? (before.endsWith('\n') ? '\n' : '\n\n') : '';
    const trail = (after && !after.startsWith('\n\n')) ? '\n\n' : '';
    const inserted = sep + snippet + trail;
    ta.value = before + inserted + after;

    // 選 placeholder 區段方便用戶覆寫
    let selStart = pos + sep.length;
    let selEnd = selStart + snippet.length;
    if (selectMatch) {
        const m = snippet.match(selectMatch);
        if (m && m.index !== undefined) {
            selStart = pos + sep.length + m.index;
            selEnd = selStart + m[0].length;
        }
    }
    ta.focus();
    ta.setSelectionRange(selStart, selEnd);
    _blog.textModeSync();
}

// Toolbar：插入 block 樣板（text mode 用）
const _TEXT_TEMPLATES = {
    paragraph: { snippet: '在這裡寫段落內容...',           select: /在這裡寫段落內容\.\.\./ },
    heading:   { snippet: '## 新標題',                     select: /新標題/ },
    quote:     { snippet: '> 引言內容\n> — 出處',           select: /引言內容/ },
    list:      { snippet: '- 第一項\n- 第二項\n- 第三項',  select: /第一項/ },
};

_blog.textModeInsert = (type) => {
    const ta = document.getElementById('m-body-text');
    const t = _TEXT_TEMPLATES[type];
    if (!ta || !t) return;
    _insertAtCursor(ta, t.snippet, t.select);
};

// 文字模式：加 YouTube — prompt 貼 URL 解析成 ID
_blog.textModeInsertVideo = () => {
    const ta = document.getElementById('m-body-text');
    if (!ta) return;
    const raw = prompt('貼 YouTube URL 或 11-字 ID：');
    if (!raw) return;
    let id = raw.trim();
    if (id.length !== 11 || !/^[A-Za-z0-9_-]{11}$/.test(id)) {
        const m = id.match(_YT_RE);
        if (m) id = m[1];
        else { toastErr('無法解析成 YouTube ID'); return; }
    }
    _insertAtCursor(ta, `::: video ${id}\ncaption: \n:::`, /caption: /);
};

// 文字模式：上傳圖片按鈕 → 呼 backend → 在 cursor 插入 ![]() markdown
_blog.textModeUploadImage = async (fileInput) => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (!_editingPost?.id) {
        toastErr('新文章請先儲存草稿（取得 ID）才能上傳圖片');
        fileInput.value = '';
        return;
    }
    try {
        const url = await _uploadImageGetUrl(file);
        const ta = document.getElementById('m-body-text');
        if (ta) _insertAtCursor(ta, `![請補 alt 文字](${url})`, /請補 alt 文字/);
        toastOk('已上傳');
    } catch (e) {
        toastErr(`上傳失敗：${e.message}`);
    } finally {
        fileInput.value = '';
    }
};

// 文字模式：textarea 拖檔 → 取 file → 上傳 → 在 drop 位置插入 markdown
_blog.textModeDrop = async (e) => {
    e.preventDefault();
    if (!_editingPost?.id) {
        toastErr('新文章請先儲存草稿才能上傳圖片');
        return;
    }
    const file = e.dataTransfer?.files?.[0];
    if (!file || !file.type.startsWith('image/')) return;
    const ta = e.target;
    // 把 cursor 移到 drop 點（document.caretRangeFromPoint 在新 Chromium 才有；fallback 用尾巴）
    if (document.caretPositionFromPoint) {
        const cp = document.caretPositionFromPoint(e.clientX, e.clientY);
        if (cp) ta.setSelectionRange(cp.offset, cp.offset);
    } else if (document.caretRangeFromPoint) {
        const r = document.caretRangeFromPoint(e.clientX, e.clientY);
        if (r) ta.setSelectionRange(r.startOffset, r.startOffset);
    }
    try {
        const url = await _uploadImageGetUrl(file);
        _insertAtCursor(ta, `![請補 alt 文字](${url})`, /請補 alt 文字/);
        toastOk('已上傳');
    } catch (err) {
        toastErr(`上傳失敗：${err.message}`);
    }
};

async function _uploadImageGetUrl(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await websiteFetch(`/api/website/admin/posts/${_editingPost.id}/upload-image`, {
        method: 'POST', body: fd,
    });
    return r.url;
}

// 封面圖縮圖（共用：初始 render + URL 變動 + 上傳完成各自呼叫）
function _coverThumbHtml(url) {
    if (!url) {
        return `<div id="m-cover-thumb"
            style="width:80px;height:80px;border:1px dashed #333;border-radius:4px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#444;font-size:11px;flex-shrink:0;background:#0d0d0d;text-align:center;line-height:1.3;">
            <div style="font-size:22px;">🖼</div>
            <div>無封面</div>
        </div>`;
    }
    const resolved = _resolveImageUrl(url);
    return `<div id="m-cover-thumb" style="width:80px;height:80px;border-radius:4px;border:1px solid #333;flex-shrink:0;background:#0d0d0d;background-image:url('${esc(resolved)}');background-size:cover;background-position:center;position:relative;overflow:hidden;">
        <img src="${esc(resolved)}" alt="" style="width:100%;height:100%;object-fit:cover;display:block;"
             onerror="this.style.display='none';this.parentElement.innerHTML='<div style=\\'width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#888;font-size:10px;text-align:center;line-height:1.3;padding:4px;box-sizing:border-box;\\'><div style=\\'font-size:18px;color:#f87171;\\'>⚠</div>圖片載入失敗</div>'" />
    </div>`;
}

// 封面圖 URL 輸入時即時更新縮圖（避免用戶疑惑「貼 URL 沒反應」）
_blog.refreshCoverThumb = (url) => {
    const thumb = document.getElementById('m-cover-thumb');
    if (thumb) thumb.outerHTML = _coverThumbHtml((url || '').trim());
};

// 封面圖本機上傳（複用 block 上傳 endpoint，回的 url 寫進 cover_url）
_blog.uploadCover = async (fileInput) => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (!_editingPost?.id) {
        toastErr('新文章請先儲存草稿（取得 ID）才能上傳封面');
        fileInput.value = '';
        return;
    }
    const fd = new FormData();
    fd.append('file', file);
    try {
        const r = await websiteFetch(`/api/website/admin/posts/${_editingPost.id}/upload-image`, {
            method: 'POST', body: fd,
        });
        const input = document.getElementById('m-cover');
        if (input) input.value = r.url;
        _editingPost.cover_url = r.url;
        _blog.refreshCoverThumb(r.url);
        toastOk(`已上傳封面：${r.filename}`);
    } catch (e) {
        toastErr(`上傳失敗：${e.message}`);
    } finally {
        fileInput.value = '';
    }
};

_blog.refreshBlockItem = (idx) => {
    if (!_editingPost) return;
    const wrap = document.querySelector(`[data-block-idx="${idx}"]`);
    if (!wrap) return;
    const blocks = _editingPost.body || [];
    wrap.outerHTML = _renderBlockItem(blocks[idx], idx, blocks.length);
    _refreshPreview();
};

_blog.updateBlockField = (idx, field, value) => {
    if (!_editingPost?.body?.[idx]) return;
    _editingPost.body[idx][field] = value;
    _refreshPreview();
    _refreshSEOHealth();
};

_blog.appendBlock = (type) => {
    if (!_editingPost) return;
    const def = BLOCK_REGISTRY[type]?.defaults;
    if (!def) return;
    _editingPost.body = _editingPost.body || [];
    _editingPost.body.push({ ...def });
    _refreshBlocks();
};

_blog.insertBlockAt = (idx, type) => {
    if (!_editingPost) return;
    const def = BLOCK_REGISTRY[type]?.defaults;
    if (!def) return;
    _editingPost.body = _editingPost.body || [];
    _editingPost.body.splice(idx, 0, { ...def });
    _refreshBlocks();
};

_blog.removeBlock = (idx) => {
    if (!_editingPost?.body?.[idx]) return;
    if (!confirm('刪除此 block？')) return;
    _editingPost.body.splice(idx, 1);
    _refreshBlocks();
};

_blog.moveBlockUp = (idx) => {
    if (!_editingPost?.body || idx <= 0) return;
    const arr = _editingPost.body;
    [arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]];
    _refreshBlocks();
};

_blog.moveBlockDown = (idx) => {
    if (!_editingPost?.body || idx >= (_editingPost.body.length - 1)) return;
    const arr = _editingPost.body;
    [arr[idx + 1], arr[idx]] = [arr[idx], arr[idx + 1]];
    _refreshBlocks();
};

_blog.updateListItem = (blockIdx, itemIdx, value) => {
    const block = _editingPost?.body?.[blockIdx];
    if (!block?.items) return;
    block.items[itemIdx] = value;
    _refreshPreview();
};

_blog.addListItem = (blockIdx) => {
    const block = _editingPost?.body?.[blockIdx];
    if (!block?.items) return;
    block.items.push('');
    _refreshBlocks();
};

_blog.removeListItem = (blockIdx, itemIdx) => {
    const block = _editingPost?.body?.[blockIdx];
    if (!block?.items || block.items.length <= 1) return;
    block.items.splice(itemIdx, 1);
    _refreshBlocks();
};


// ── 圖片上傳 ──

_blog.uploadBlockImage = async (idx, fileInput) => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (!_editingPost?.id) {
        toastErr('新文章請先儲存草稿（取得 ID）才能上傳圖片');
        fileInput.value = '';
        return;
    }
    const fd = new FormData();
    fd.append('file', file);
    try {
        const r = await websiteFetch(`/api/website/admin/posts/${_editingPost.id}/upload-image`, {
            method: 'POST', body: fd,
        });
        _blog.updateBlockField(idx, 'src', r.url);
        toastOk(`已上傳 ${r.filename}`);
        _blog.refreshBlockItem(idx);
    } catch (e) {
        toastErr(`上傳失敗：${e.message}`);
    } finally {
        fileInput.value = '';
    }
};


// ── YouTube URL → 11 字 ID 解析 ──

const _YT_RE = /(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|embed\/|v\/)|^)([A-Za-z0-9_-]{11})(?:[?&]|$)/;

_blog.parseYoutubeAndStore = (idx, raw) => {
    const trimmed = (raw || '').trim();
    let id = trimmed;
    if (trimmed.length !== 11 || !/^[A-Za-z0-9_-]{11}$/.test(trimmed)) {
        const m = trimmed.match(_YT_RE);
        if (m) id = m[1];
    }
    _blog.updateBlockField(idx, 'youtube_id', id);
    _blog.refreshBlockItem(idx);
};


// ══════════════════════════════════════════════════════════
// Live Preview Pane (mirror Astro 渲染)
// ══════════════════════════════════════════════════════════

function _renderPreview(p) {
    const blocks = p.body || [];
    const titleHtml = `<h1 style="font-size:24px;font-weight:700;margin:0 0 12px;color:#111;line-height:1.3;">${esc(p.title || '(未命名)')}</h1>`;
    const metaHtml = p.published_at || p.author_name
        ? `<div style="color:#888;font-size:12px;margin-bottom:16px;">${[
            p.author_name, p.published_at ? new Date(p.published_at).toLocaleDateString() : null,
          ].filter(Boolean).join(' · ')}</div>`
        : '';
    const cover = p.cover_url
        ? `<img src="${esc(_resolveImageUrl(p.cover_url))}" style="width:100%;max-height:240px;object-fit:cover;border-radius:6px;margin-bottom:16px;" />`
        : '';
    const excerpt = p.excerpt
        ? `<p style="color:#555;font-size:14px;font-style:italic;border-left:3px solid #c9372c;padding-left:10px;margin:0 0 16px;">${esc(p.excerpt)}</p>`
        : '';
    const body = blocks.length === 0
        ? '<div style="color:#999;font-size:13px;padding:20px;text-align:center;">無內文</div>'
        : blocks.map(_renderPreviewBlock).join('');

    return cover + titleHtml + metaHtml + excerpt + body;
}

function _renderPreviewBlock(b) {
    return BLOCK_REGISTRY[b.type]?.preview(b) ?? _UNKNOWN_BLOCK_PREVIEW(b);
}

function _refreshPreview() {
    const pane = document.getElementById('post-modal-preview');
    if (pane && _editingPost) {
        pane.innerHTML = _renderPreview(_editingPost);
    }
}

_blog.togglePreview = () => {
    if (!_editingPost) return;
    // 先把 Modal 上 metadata 表單值 merge 進 _editingPost — 切換預覽會 re-render
    // 整個 Modal，沒先存就會把使用者剛改的 title/excerpt/SEO/old_urls 全丟掉
    Object.assign(_editingPost, _readModalForm());
    _previewVisible = !_previewVisible;
    const isNew = !_editingPost.id;
    _showPostModal(isNew ? '新增文章' : `編輯文章 #${_editingPost.slug}`);
};


// ══════════════════════════════════════════════════════════
// SEO 健康度 widget（編輯時即時評分）
// ══════════════════════════════════════════════════════════

function _modalSEOHealth(p) {
    const checks = _seoChecks(p);
    const passed = checks.filter(c => c.pass).length;
    const pct = Math.round((passed / checks.length) * 100);
    const color = pct >= 80 ? '#4ade80' : pct >= 50 ? '#f59e0b' : '#f87171';

    const items = checks.map(c => `
        <li style="display:flex;gap:6px;padding:3px 0;font-size:11px;color:${c.pass ? '#aaa' : '#ddd'};">
            <span style="color:${c.pass ? '#4ade80' : '#f87171'};">${c.pass ? '✓' : '✗'}</span>
            <span>${esc(c.label)}</span>
        </li>
    `).join('');

    return `
        <div id="seo-health-widget" class="pm-section" style="border-left:3px solid ${color};">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <div class="pm-section-title" style="margin:0;">🎯 SEO 健康度（即時）</div>
                <span style="color:${color};font-size:18px;font-weight:700;">${pct}<span style="font-size:11px;opacity:0.8;"> % · ${passed}/${checks.length}</span></span>
            </div>
            <ul style="list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:0 16px;">${items}</ul>
        </div>
    `;
}

function _seoChecks(p) {
    const blocks = p.body || [];
    const titleLen = (p.title || '').length;
    const descSrc = p.seo_description || p.excerpt || '';
    const descLen = descSrc.length;
    const wordCount = blocks.reduce((sum, b) => {
        if (b.type === 'paragraph' || b.type === 'heading' || b.type === 'quote') return sum + (b.text || '').length;
        if (b.type === 'list') return sum + (b.items || []).reduce((s, x) => s + x.length, 0);
        return sum;
    }, 0);
    const imgs = blocks.filter(b => b.type === 'image');
    const imgsWithoutAlt = imgs.filter(b => !(b.alt && b.alt.trim())).length;
    // 內鏈格式：markdown `[文字](/news/X)` / `(/works/X)` / `(/services...)`
    const _LINK_RE = /\[[^\]]+\]\((?:\/news\/|\/works\/|\/services)/;
    const hasInternalLink = blocks.some(b => {
        if (b.type === 'paragraph' || b.type === 'quote') return _LINK_RE.test(b.text || '');
        if (b.type === 'list') return (b.items || []).some(it => _LINK_RE.test(it));
        return false;
    });

    return [
        { label: `Title 長度 ${titleLen} 字（建議 30-60）`,        pass: titleLen >= 30 && titleLen <= 60 },
        { label: `Description ${descLen} 字（建議 60-160）`,        pass: descLen >= 60 && descLen <= 160 },
        { label: `OG image 已設`,                                    pass: !!(p.og_image_url || p.cover_url) },
        { label: `字數 ${wordCount}（建議 ≥ 800）`,                  pass: wordCount >= 800 },
        { label: `至少 1 張內文圖`,                                   pass: imgs.length >= 1 },
        { label: `所有內文圖有 alt（${imgsWithoutAlt} 張缺）`,       pass: imgsWithoutAlt === 0 },
        { label: `Author 已填（E-E-A-T 加分）`,                       pass: !!(p.author_name && p.author_name.trim()) },
        { label: `已填舊網址轉址 — SEO 權重連續性`,                   pass: (p.old_urls || []).length > 0 },
        { label: `published_at 不是未來時間`,                          pass: !p.published_at || new Date(p.published_at) <= new Date() },
        { label: `內文有內鏈到其他文章/作品/服務`,                     pass: hasInternalLink },
    ];
}

function _refreshSEOHealth() {
    if (!_editingPost) return;
    const widget = document.getElementById('seo-health-widget');
    if (widget) {
        widget.outerHTML = _modalSEOHealth(_editingPost);
    }
}

_blog.closeModal = () => {
    closeModal('post-modal');
    _editingPost = null;
};

function _readModalForm() {
    // 文字模式：textareaSync 是 debounce 200ms，存檔可能搶在 timer 前面 →
    // 強制再 parse 一次保證最新內容入帳
    if (_textMode) {
        const ta = document.getElementById('m-body-text');
        if (ta && _editingPost) {
            const { blocks } = _markdownToBlocks(ta.value);
            _editingPost.body = blocks;
        }
    }

    const checkedCats = Array.from(
        document.querySelectorAll('#m-cats input[type=checkbox]:checked')
    ).map(el => el.value);
    const oldUrlsRaw = document.getElementById('m-old-urls').value || '';
    const old_urls = oldUrlsRaw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const pubLocal = document.getElementById('m-published').value;
    const published_at = pubLocal ? new Date(pubLocal).toISOString() : null;

    return {
        slug: document.getElementById('m-slug').value.trim() || null,
        title: document.getElementById('m-title').value.trim(),
        excerpt: document.getElementById('m-excerpt').value.trim() || null,
        cover_url: document.getElementById('m-cover').value.trim() || null,
        category_slugs: checkedCats,
        status: document.getElementById('m-status').value,
        published_at,
        // body 不在 DOM 表單裡 — 隨 _editingPost 維護的 PostBlock 陣列直接送
        // （視覺模式：block 編輯器即時改 _editingPost.body[i].field
        //   文字模式：上面 if (_textMode) 強制 sync 過了）
        body: _editingPost?.body || [],
        seo_title: document.getElementById('m-seo-title').value.trim() || null,
        seo_description: document.getElementById('m-seo-desc').value.trim() || null,
        og_image_url: document.getElementById('m-og-image').value.trim() || null,
        canonical_url: document.getElementById('m-canonical').value.trim() || null,
        noindex: document.getElementById('m-noindex').checked,
        author_name: document.getElementById('m-author-name').value.trim() || null,
        author_url: document.getElementById('m-author-url').value.trim() || null,
        old_urls,
    };
}

_blog.savePost = async (overrides = {}) => {
    const data = { ..._readModalForm(), ...overrides };
    if (!data.title) { toastErr('標題必填'); return; }
    try {
        const isNew = !_editingPost.id;
        const url = isNew
            ? '/api/website/admin/posts'
            : `/api/website/admin/posts/${_editingPost.id}`;
        const method = isNew ? 'POST' : 'PUT';
        const result = await websiteFetch(url, { method, body: data });

        if (isNew) _state.posts.push(result);
        else {
            const idx = _state.posts.findIndex(p => p.id === _editingPost.id);
            if (idx >= 0) _state.posts[idx] = result;
        }
        toastOk(isNew ? '已新增' : '已儲存（60 秒後對外網站重 build）');
        _blog.closeModal();
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

_blog.saveAndPublish = () => {
    // 沒設發布時間 → 用現在
    const pubLocal = document.getElementById('m-published').value;
    const published_at = pubLocal ? new Date(pubLocal).toISOString() : new Date().toISOString();
    return _blog.savePost({ status: 'published', published_at });
};

_blog.deletePost = async (id) => {
    const p = _state.posts.find(x => x.id === id);
    const label = p?.title || `#${id}`;
    if (!confirm(`刪除「${label}」？\n（會一併刪除其 SEO 301 轉址）`)) return;
    try {
        await websiteFetch(`/api/website/admin/posts/${id}`, { method: 'DELETE' });
        _state.posts = _state.posts.filter(x => x.id !== id);
        toastOk('已刪除');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};


// ══════════════════════════════════════════════════════════
// Sub-tab 2: 📚 分類 CRUD
// ══════════════════════════════════════════════════════════

function _viewCategories() {
    return `
        <div class="card" style="margin-bottom:12px;background:#1f1f1f;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:13px;">新增分類</h3>
            <div style="display:grid;grid-template-columns:120px 1fr 1fr 100px auto;gap:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">slug</label>
                    <input id="cat-new-slug" type="text" placeholder="workflow" style="width:100%;font-family:monospace;" /></div>
                <div><label style="color:#888;font-size:11px;">中文名</label>
                    <input id="cat-new-zh" type="text" placeholder="工作流程" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">英文名（選填）</label>
                    <input id="cat-new-en" type="text" placeholder="Workflow" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">排序</label>
                    <input id="cat-new-sort" type="number" value="0" style="width:100%;" /></div>
                <button class="btn" onclick="window._blog.createCategory()">+ 新增</button>
            </div>
        </div>

        <table>
            <thead><tr>
                <th style="width:40px;">ID</th>
                <th style="width:120px;">slug</th>
                <th>中文</th>
                <th>英文</th>
                <th style="width:60px;">文章</th>
                <th style="width:60px;">排序</th>
                <th style="width:50px;">可見</th>
                <th style="width:90px;">操作</th>
            </tr></thead>
            <tbody>${
                _state.categories.length
                    ? _state.categories.map(_categoryRow).join('')
                    : '<tr><td colspan="8" style="padding:30px;text-align:center;color:#888;">尚無分類</td></tr>'
            }</tbody>
        </table>
    `;
}

function _categoryRow(c) {
    return `
        <tr>
            <td style="color:#666;">${c.id}</td>
            <td><input data-id="${c.id}" data-field="slug" value="${esc(c.slug)}" style="width:100%;font-family:monospace;" /></td>
            <td><input data-id="${c.id}" data-field="label_zh" value="${esc(c.label_zh)}" style="width:100%;" /></td>
            <td><input data-id="${c.id}" data-field="label_en" value="${esc(c.label_en || '')}" style="width:100%;" /></td>
            <td style="text-align:center;color:#aaa;">${c.post_count ?? 0}</td>
            <td><input type="number" data-id="${c.id}" data-field="sort_order" value="${c.sort_order}" style="width:100%;" /></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${c.id}" data-field="visible" ${c.visible ? 'checked' : ''} /></td>
            <td>
                <button class="btn btn-sm" onclick="window._blog.saveCategory(${c.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._blog.deleteCategory(${c.id})">🗑</button>
            </td>
        </tr>
    `;
}

_blog.createCategory = async () => {
    const body = {
        slug: document.getElementById('cat-new-slug').value.trim(),
        label_zh: document.getElementById('cat-new-zh').value.trim(),
        label_en: document.getElementById('cat-new-en').value.trim() || null,
        sort_order: Number(document.getElementById('cat-new-sort').value || 0),
        visible: true,
    };
    if (!body.slug || !body.label_zh) { toastErr('slug 與中文名必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/post_categories', { method: 'POST', body });
        _state.categories.push({ ...created, post_count: 0 });
        toastOk('已新增');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

_blog.saveCategory = async (id) => {
    try {
        const updated = await websiteFetch(`/api/website/admin/post_categories/${id}`, {
            method: 'PUT', body: readRowPatch('#blog-tab-body', id),
        });
        const idx = _state.categories.findIndex(c => c.id === id);
        if (idx >= 0) _state.categories[idx] = { ..._state.categories[idx], ...updated };
        toastOk('已更新');
    } catch (e) { toastErr(e.message); }
};

_blog.deleteCategory = async (id) => {
    const c = _state.categories.find(x => x.id === id);
    if (c?.post_count > 0 &&
        !confirm(`「${c.label_zh}」有 ${c.post_count} 篇文章引用，會被一起拿掉分類。確定？`)) return;
    if (!c?.post_count && !confirm(`刪除分類「${c?.label_zh}」？`)) return;
    try {
        await websiteFetch(`/api/website/admin/post_categories/${id}`, { method: 'DELETE' });
        _state.categories = _state.categories.filter(x => x.id !== id);
        toastOk('已刪除');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};


// ══════════════════════════════════════════════════════════
// Sub-tab 3: 📥 從 Notion 匯入
// ══════════════════════════════════════════════════════════

function _viewNotion() {
    const s = _state.notionStatus;
    const r = _state.rebuildStatus;
    const connected = s.connected;

    return `
        <div class="card" style="background:#1f1f1f;margin-bottom:12px;">
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
            <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">🌐 SEO 移轉中心</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
                <div style="background:#1f1f1f;padding:12px;border-radius:6px;border-left:3px solid #4ade80;">
                    <div style="color:#888;font-size:11px;">軟 301（Astro 靜態頁）</div>
                    <div style="color:#fff;font-size:20px;font-weight:600;margin:4px 0;">${cnt}</div>
                    <div style="color:#888;font-size:11px;">每次 build 自動為每個舊 URL 產一張</div>
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
                ℹ️ 任何文章編輯後 publish 流程末段會自動同步硬 301。手動按鈕用於 admin 看到計數對不上時自救。
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
        const r = await websiteFetch('/api/website/admin/posts/redirects/sync', { method: 'POST' });
        _state.redirectSyncOk = r.ok;
        _state.lastRedirectSyncAt = r.last_sync || new Date().toISOString();
        toastOk(r.ok ? `已同步 ${r.synced} 條硬 301` : (r.error || '同步失敗'));
        _renderShell();
    } catch (e) { toastErr(e.message); }
};
