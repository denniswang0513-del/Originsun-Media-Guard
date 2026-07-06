// blog/posts-list.js — 拆自 blog.js：Sub-tab 1 📰 文章列表（純搬移，行為不變）
import { esc } from '../../website-utils.js';
import { STATUS, STATUS_FALLBACK, _state, _blog } from './shared.js';

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

export { _viewPosts };
