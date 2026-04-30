/**
 * blog.js — 部落格管理（DB-as-truth + Notion 是匯入器）
 *
 * 4 個 sub-tab：
 *   📰 文章        — list + filters + metadata Modal CRUD
 *   📚 分類        — CRUD 多對多分類（inline table）
 *   📥 從 Notion   — 匯入器（Phase A 後 Notion 只是 seed，不再是 truth）
 *   🌐 SEO 移轉    — 軟+硬 301 計數 + 強制同步 + 跨域遷移指引
 *
 * Body block 編輯器在 Phase C；目前 metadata Modal 不含 body 編輯（draft 文章
 * 先存空 body，admin 過目時提示「Phase C 上線後再編內容」）。
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError,
    readRowPatch, openModal, closeModal,
} from '../website-utils.js';

const SUB_TABS = ['posts', 'categories', 'notion', 'seo-migration'];

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
    _showPostModal('新增文章');
};

_blog.openEditPost = (id) => {
    const p = _state.posts.find(x => x.id === id);
    if (!p) { toastErr('找不到此文章'); return; }
    _editingPost = { ...p };
    _showPostModal(`編輯文章 #${p.slug}`);
};

function _showPostModal(title) {
    const p = _editingPost;
    const isNew = !p.id;
    // datetime-local 需要 yyyy-MM-ddTHH:mm 格式
    const pubLocal = p.published_at
        ? new Date(p.published_at).toISOString().slice(0, 16)
        : '';

    const inner = `
        <div style="
            padding:16px 20px;border-bottom:1px solid #2a2a2a;
            display:flex;justify-content:space-between;align-items:center;
            position:sticky;top:0;background:#1a1a1a;z-index:1;
        ">
            <h3 style="color:#fff;margin:0;font-size:15px;">${esc(title)}</h3>
            <button onclick="window._blog.closeModal()" style="background:transparent;border:none;color:#888;cursor:pointer;font-size:20px;">✕</button>
        </div>

        <div style="padding:20px;">
            ${_modalBasicSection(p, isNew, pubLocal)}
            ${_modalCategorySection(p)}
            ${_modalSEOSection(p)}
            ${_modalRedirectsSection(p)}
            ${isNew ? '' : _modalBodyHint()}
        </div>

        <div style="
            padding:14px 20px;border-top:1px solid #2a2a2a;
            display:flex;justify-content:space-between;align-items:center;gap:8px;
            position:sticky;bottom:0;background:#1a1a1a;
        ">
            <button class="btn btn-ghost btn-sm" onclick="window._blog.closeModal()">取消</button>
            <div style="display:flex;gap:8px;">
                ${isNew ? '' : `<button class="btn btn-sm btn-ghost" onclick="window._blog.saveAndPublish()">💾 儲存並發布</button>`}
                <button class="btn" onclick="window._blog.savePost()">💾 儲存</button>
            </div>
        </div>
    `;
    openModal('post-modal', inner, { width: '720px' });
}

function _modalBasicSection(p, isNew, pubLocal) {
    return `
        <div style="margin-bottom:16px;">
            <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">標題 *</label>
            <input id="m-title" type="text" value="${esc(p.title)}" style="width:100%;font-size:14px;" />
        </div>
        <div style="display:grid;grid-template-columns:120px 1fr;gap:12px;margin-bottom:16px;">
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">Slug ${isNew ? '<span style="color:#666;">（自動）</span>' : ''}</label>
                <input id="m-slug" type="text" value="${esc(p.slug)}" placeholder="auto" style="width:100%;font-family:monospace;" />
            </div>
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">封面圖 URL</label>
                <input id="m-cover" type="text" value="${esc(p.cover_url || '')}" placeholder="https://... 或 /uploads/..." style="width:100%;" />
            </div>
        </div>
        <div style="margin-bottom:16px;">
            <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">摘要（首頁與 SERP description fallback）</label>
            <textarea id="m-excerpt" rows="2" style="width:100%;">${esc(p.excerpt || '')}</textarea>
        </div>

        <div style="background:#252525;border-radius:6px;padding:12px;margin-bottom:16px;">
            <div style="color:#888;font-size:11px;margin-bottom:8px;">📅 發布管理</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:end;">
                <div>
                    <label style="color:#aaa;font-size:11px;display:block;margin-bottom:4px;">狀態</label>
                    <select id="m-status" style="width:100%;">
                        ${Object.entries(STATUS).map(([key, m]) =>
                            `<option value="${key}" ${p.status === key ? 'selected' : ''}>${m.emoji} ${m.label}</option>`
                        ).join('')}
                    </select>
                </div>
                <div>
                    <label style="color:#aaa;font-size:11px;display:block;margin-bottom:4px;">發布時間（未來=排程）</label>
                    <input id="m-published" type="datetime-local" value="${pubLocal}" style="width:100%;" />
                </div>
            </div>
        </div>
    `;
}

function _modalCategorySection(p) {
    const selected = new Set(p.category_slugs || []);
    return `
        <div style="margin-bottom:16px;">
            <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">分類（多選）</label>
            <div id="m-cats" style="display:flex;flex-wrap:wrap;gap:6px;">
                ${_state.categories.map(c => `
                    <label style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;background:#252525;border-radius:4px;cursor:pointer;color:#ddd;font-size:12px;">
                        <input type="checkbox" value="${esc(c.slug)}" ${selected.has(c.slug) ? 'checked' : ''} />
                        ${esc(c.label_zh)}
                    </label>
                `).join('')}
            </div>
            ${_state.categories.length === 0
                ? '<div style="color:#888;font-size:11px;margin-top:6px;">尚無分類，到「📚 分類」分頁新增</div>'
                : ''}
        </div>
    `;
}

function _modalSEOSection(p) {
    return `
        <details style="margin-bottom:16px;background:#1f1f1f;border-radius:6px;">
            <summary style="padding:10px 14px;cursor:pointer;color:#3b82f6;font-size:13px;font-weight:500;">
                🔍 SEO 覆寫（選填，空則自動推算）
            </summary>
            <div style="padding:12px 14px 14px;">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                    <div>
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">SEO Title 覆寫</label>
                        <input id="m-seo-title" type="text" value="${esc(p.seo_title || '')}" style="width:100%;" />
                    </div>
                    <div>
                        <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">OG Image 覆寫</label>
                        <input id="m-og-image" type="text" value="${esc(p.og_image_url || '')}" placeholder="不填用 cover_url" style="width:100%;" />
                    </div>
                </div>
                <div style="margin-top:10px;">
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">SEO Description 覆寫（不填用 excerpt）</label>
                    <textarea id="m-seo-desc" rows="2" style="width:100%;">${esc(p.seo_description || '')}</textarea>
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
        <details style="margin-bottom:16px;background:#1f1f1f;border-radius:6px;">
            <summary style="padding:10px 14px;cursor:pointer;color:#f59e0b;font-size:13px;font-weight:500;">
                🔗 SEO 301 轉址（${p.old_urls?.length || 0} 條 — 從舊網址轉到此文）
            </summary>
            <div style="padding:12px 14px 14px;">
                <div style="color:#888;font-size:11px;margin-bottom:6px;line-height:1.6;">
                    一行一個舊網址（含或不含網域都可，自動 strip 為相對路徑）。<br/>
                    軟 301（Astro static page）+ 硬 301（NAS nginx）雙保險，Google 將舊 URL 權重轉到此文。
                </div>
                <textarea id="m-old-urls" rows="4" placeholder="/blog/2020/old-article&#10;/old-news/45" style="width:100%;font-family:monospace;font-size:12px;">${esc(oldUrls)}</textarea>
            </div>
        </details>
    `;
}

function _modalBodyHint() {
    return `
        <div class="card" style="background:#1f2937;border-left:3px solid #3b82f6;color:#aaa;font-size:12px;padding:10px 12px;">
            ℹ️ <strong style="color:#fff;">內文 body 編輯</strong>：Phase C 上線後可在此 Modal 加入 block 編輯器
            （paragraph / heading / image / video / quote / list 拖拉排序）。<br/>
            目前若需改內文，請從 Notion 編輯後到「📥 從 Notion」分頁按「強制重新匯入」。
        </div>
    `;
}

_blog.closeModal = () => {
    closeModal('post-modal');
    _editingPost = null;
};

function _readModalForm() {
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
