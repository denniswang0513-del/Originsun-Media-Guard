// blog/shell.js — 拆自 blog.js：render 進入點（default export）+ Shell / tab nav（純搬移，行為不變）
import { websiteFetch, renderLoadError, renderCopyCard } from '../../website-utils.js';
import { SUB_TABS, NEWS_COPY_BLOCKS, _state, _blog } from './shared.js';
import { _viewPosts } from './posts-list.js';
import { _viewCategories } from './categories.js';
import { _viewNotion, _viewSEOMigration } from './notion-seo.js';

let _container = null;
export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>📝 部落格</h2><div style="color:#888;padding:20px;">載入中…</div>';

    try {
        const [posts, cats, notion, rebuild, redirects, settings] = await Promise.all([
            websiteFetch('/api/website/admin/posts'),
            websiteFetch('/api/website/admin/post_categories'),
            websiteFetch('/api/website/admin/notion/status'),
            websiteFetch('/api/website/admin/rebuild/status'),
            websiteFetch('/api/website/redirects'),
            websiteFetch('/api/website/admin/settings'),
        ]);
        if (!isCurrent()) return;
        _state.posts = posts?.items || [];
        _state.categories = cats?.items || [];
        _state.notionStatus = notion || _state.notionStatus;
        _state.rebuildStatus = rebuild || _state.rebuildStatus;
        _state.redirectCount = redirects?.count || 0;
        _state.settings = settings?.settings || {};
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

        ${renderCopyCard('copy.news', _state.settings, NEWS_COPY_BLOCKS, { title: '📝 專欄列表頁文案', note: '對應 /news 列表頁的 hero、背景圖與介紹段；留空則維持預設。' })}

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

export { _renderShell };
