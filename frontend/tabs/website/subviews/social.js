/**
 * social.js — 📣 社群工作台（社群自動化階段一）
 *
 * AI 產生的社群文稿（FB / IG / Threads）進佇列 → 人工審核/編輯 → 核准 →
 * 📋 複製文稿去平台手動貼文 → 回來 🔗 貼回實際連結標記已發布。
 * 設定卡（收合式）管平台開關 / 日週上限 / 發佈時段 / 品牌語氣 / 三平台模板。
 *
 * 後端合約（routers/website/admin_social.py + services/website/social_runner.py）：
 *   GET  /api/website/admin/social/posts?status=      → {items:[...]}（id 為 hex 字串）
 *   PUT  /api/website/admin/social/posts/{id}         → {content?, media_url?}
 *   POST /api/website/admin/social/posts/{id}/approve | /reject
 *   POST /api/website/admin/social/posts/{id}/published → {published_url}
 *   GET  /api/website/admin/social/settings → 平面 cfg：enabled/platforms[]/slots[]/
 *        daily_cap/weekly_cap/brand_voice/tpl{fb,ig,th}/cron/running/last_run_at
 *   PUT  同路徑，攤平鍵：platforms/slots/daily_cap/weekly_cap/brand_voice/tpl_facebook…
 *   POST /api/website/admin/social/run                → {started|busy}（非同步）
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError,
    openModal, closeModal, getApiBase, fmtDt, fmtRelative, emptyHint,
} from '../website-utils.js';

const PLATFORMS = {
    facebook:  { label: 'Facebook',  bg: '#1e3a8a', fg: '#93c5fd' },   // 藍
    instagram: { label: 'Instagram', bg: '#581c87', fg: '#d8b4fe' },   // 紫
    threads:   { label: 'Threads',   bg: '#111111', fg: '#e5e5e5' },   // 黑
};

const GROUPS = [
    { key: 'draft',     label: '🕐 待審',   color: '#f59e0b', hint: 'AI 產生的文稿，等你審核' },
    { key: 'approved',  label: '✅ 已核准', color: '#059669', hint: '審核通過 — 複製文稿去平台貼文，貼完回來標記已發布' },
    { key: 'published', label: '📤 已發布', color: '#3b82f6', hint: '已貼到平台（含實際貼文連結）' },
    { key: 'rejected',  label: '❌ 退回',   color: '#6b7280', hint: '不採用的文稿' },
];

const SOURCE_LABEL = { work: '🎬 作品', post: '📝 文章', blog: '📝 文章', service: '🧩 服務', manual: '✍️ 手動' };

let _state = { posts: [], settings: {} };
let _container = null;
let _isCurrent = () => true;   // 由 render ctx 帶入；3 秒延遲重整前檢查（切走就不動 DOM）
const _social = (window._social = window._social || {});

function _inp() {
    return 'background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:7px 9px;border-radius:4px;width:100%;box-sizing:border-box;font-size:13px;font-family:inherit;';
}
function _lbl() {
    return 'color:#9aa0a6;font-size:11px;display:block;margin-bottom:3px;';
}

// 配圖相對路徑（/uploads/...）要補 API base 才看得到預覽 / 複製出可用連結
function _mediaSrc(url) {
    if (!url) return '';
    return /^(https?:|data:|blob:)/i.test(url) ? url : `${getApiBase()}${url}`;
}

function _find(id) {
    return _state.posts.find(x => x.id === id);
}

function _sourceHtml(p) {
    const label = SOURCE_LABEL[p.source_type] || esc(p.source_type || '');
    const sid = p.source_id != null ? esc(String(p.source_id)) : '';
    return `${label} ${sid}`.trim();
}

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    _isCurrent = isCurrent;
    container.innerHTML = '<h2>📣 社群工作台</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const [posts, settings] = await Promise.all([
            websiteFetch('/api/website/admin/social/posts'),
            websiteFetch('/api/website/admin/social/settings'),
        ]);
        if (!isCurrent()) return;
        _state.posts = posts?.items || [];
        _state.settings = settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '📣 社群工作台', e);
        return;
    }
    _renderShell();
}

// ── Shell ──

function _renderShell() {
    const counts = {};
    for (const p of _state.posts) counts[p.status] = (counts[p.status] || 0) + 1;
    _container.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <h2 style="margin:0;">📣 社群工作台 <span style="color:#888;font-size:12px;font-weight:400;">· AI 文稿 → 審核 → 手動貼文</span></h2>
            <button class="btn" style="background:#059669;" onclick="window._social.runNow(this)">⚡ 立即產生今日文稿</button>
        </div>
        <p style="color:#888;font-size:12px;margin:6px 0 12px;">
            排程（或上面的按鈕）用 AI 從作品/文章產生 FB / IG / Threads 文稿進「待審」。
            點卡片審核 → ✅ 核准 → <strong style="color:#ddd;">📋 複製文稿</strong>去平台手動貼文 → 回來 🔗 標記已發布。
        </p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px;font-size:12px;">
            ${GROUPS.map(g => `<span class="website-pill" style="border-left:3px solid ${g.color};">${g.label} <strong style="color:#fff;">${counts[g.key] || 0}</strong></span>`).join('')}
            <span style="color:#888;margin-left:auto;">共 ${_state.posts.length} 則</span>
        </div>
        ${GROUPS.map(_renderGroup).join('')}
        ${_renderSettingsCard()}
    `;
}

function _renderGroup(g) {
    const posts = _state.posts
        .filter(p => p.status === g.key)
        .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    return `
        <div class="card" style="border-left:3px solid ${g.color};margin-bottom:14px;">
            <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
                <h3 style="color:#fff;margin:0;font-size:14px;">${g.label} <span style="color:#888;font-weight:400;font-size:12px;">(${posts.length})</span></h3>
                <span style="color:#666;font-size:11px;">${g.hint}</span>
            </div>
            ${posts.length
                ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;">${posts.map(_postCard).join('')}</div>`
                : emptyHint('（空）', { padding: 10, fontSize: 11 })}
        </div>`;
}

function _postCard(p) {
    const pf = PLATFORMS[p.platform] || { label: p.platform || '?', bg: '#333', fg: '#ccc' };
    const src = _sourceHtml(p);
    const content = p.content || '';
    const preview = content.length > 120 ? content.slice(0, 120) + '…' : (content || '（空白文稿）');
    return `
        <div onclick="window._social.openReview('${esc(p.id)}')"
             style="background:#1f1f1f;border:1px solid #2f2f2f;border-radius:6px;padding:10px 12px;cursor:pointer;"
             onmouseover="this.style.borderColor='#3b82f6'" onmouseout="this.style.borderColor='#2f2f2f'">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap;">
                <span class="website-pill" style="background:${pf.bg};color:${pf.fg};font-weight:600;">${esc(pf.label)}</span>
                ${src ? `<span style="color:#888;font-size:11px;">${src}</span>` : ''}
                ${p.media_url ? '<span title="有配圖" style="font-size:11px;">🖼️</span>' : ''}
                <span style="color:#666;font-size:10px;margin-left:auto;" title="${esc(fmtDt(p.created_at))}">${esc(fmtRelative(p.created_at))}</span>
            </div>
            <div style="color:#ccc;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;">${esc(preview)}</div>
            ${p.published_url ? `<div style="margin-top:6px;font-size:11px;"><a href="${esc(p.published_url)}" target="_blank" onclick="event.stopPropagation()" style="color:#3b82f6;">貼文連結 ↗</a></div>` : ''}
        </div>`;
}

// ── 設定卡（收合式） ──

function _renderSettingsCard() {
    const s = _state.settings;
    const platforms = Array.isArray(s.platforms) ? s.platforms : [];
    const tpl = s.tpl || {};
    const tmpl = (pfKey, ph) => `
        <div>
            <label style="${_lbl()}">${PLATFORMS[pfKey].label} 模板</label>
            <textarea id="social-set-tmpl-${pfKey}" rows="3" placeholder="${esc(ph)}" style="${_inp()};resize:vertical;">${esc(tpl[pfKey] || '')}</textarea>
        </div>`;
    const runnerLine = s.last_run_at
        ? `上次產生：${esc(fmtDt(s.last_run_at))}${s.running ? '（產生中…）' : ''}`
        : '尚未跑過（排程 09:00 或按上面的按鈕）';
    return `
        <details class="card" style="border-left:3px solid #8b5cf6;">
            <summary style="cursor:pointer;color:#fff;font-size:14px;font-weight:600;">⚙️ 社群設定
                <span style="color:#888;font-size:11px;font-weight:400;">（平台 / 上限 / 時段 / 語氣 / 模板 — 點開展開）· ${runnerLine}</span></summary>
            <div style="margin-top:14px;">
                <div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:12px;align-items:center;">
                    <label style="color:#fff;display:inline-flex;gap:6px;align-items:center;font-size:13px;cursor:pointer;font-weight:600;">
                        <input id="social-set-enabled" type="checkbox" ${s.enabled ? 'checked' : ''} style="width:auto;" /> 啟用每日排程</label>
                    <span style="color:#444;">|</span>
                    ${Object.keys(PLATFORMS).map(k => `
                        <label style="color:#ddd;display:inline-flex;gap:6px;align-items:center;font-size:13px;cursor:pointer;">
                            <input id="social-set-${k}" type="checkbox" ${platforms.includes(k) ? 'checked' : ''} style="width:auto;" /> ${PLATFORMS[k].label}</label>`).join('')}
                </div>
                <div style="display:grid;grid-template-columns:repeat(3,minmax(120px,180px));gap:10px;margin-bottom:12px;">
                    <div><label style="${_lbl()}">每日上限（則/平台）</label>
                        <input id="social-set-daily" type="number" min="0" value="${esc(String(s.daily_cap ?? 1))}" style="${_inp()}" /></div>
                    <div><label style="${_lbl()}">每週上限（則/平台）</label>
                        <input id="social-set-weekly" type="number" min="0" value="${esc(String(s.weekly_cap ?? 4))}" style="${_inp()}" /></div>
                    <div><label style="${_lbl()}">發佈時段（逗號分隔，階段二用）</label>
                        <input id="social-set-slots" value="${esc((s.slots || []).join(', '))}" placeholder="12:00, 19:00" style="${_inp()}" /></div>
                </div>
                <div style="margin-bottom:12px;">
                    <label style="${_lbl()}">品牌語氣</label>
                    <textarea id="social-set-voice" rows="2" placeholder="例：專業但有溫度、避免浮誇；公司自稱用 源日影像" style="${_inp()};resize:vertical;">${esc(s.brand_voice || '')}</textarea>
                </div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:12px;">
                    ${tmpl('facebook', '（留空用內建預設）敘事長文 2-3 段，文末附官網連結')}
                    ${tmpl('instagram', '（留空用內建預設）精煉短文 + hashtag，連結在 bio')}
                    ${tmpl('threads', '（留空用內建預設）口語 1-2 句鉤子 + 連結')}
                </div>
                <button class="btn" style="background:#3b82f6;" onclick="window._social.saveSettings()">💾 儲存設定</button>
            </div>
        </details>`;
}

_social.saveSettings = async () => {
    const v = (id) => document.getElementById(id);
    // PUT 收攤平鍵（白名單見 social_runner._ALLOWED_SETTINGS）；GET 回巢狀 cfg
    const payload = {
        enabled: v('social-set-enabled').checked,
        platforms: Object.keys(PLATFORMS).filter(k => v(`social-set-${k}`).checked),
        daily_cap: Number(v('social-set-daily').value) || 0,
        weekly_cap: Number(v('social-set-weekly').value) || 0,
        slots: v('social-set-slots').value.split(',').map(x => x.trim()).filter(Boolean),
        brand_voice: v('social-set-voice').value.trim(),
        tpl_facebook: v('social-set-tmpl-facebook').value,
        tpl_instagram: v('social-set-tmpl-instagram').value,
        tpl_threads: v('social-set-tmpl-threads').value,
    };
    try {
        const r = await websiteFetch('/api/website/admin/social/settings', { method: 'PUT', body: payload });
        _state.settings = r || _state.settings;
        toastOk('社群設定已儲存');
    } catch (e) { toastErr(e.message); }
};

// ── 立即產生 ──

_social.runNow = async (btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '產生中…'; }
    try {
        const r = await websiteFetch('/api/website/admin/social/run', { method: 'POST' });
        if (r?.status === 'busy') toastErr('已有一輪在產生中，稍後再試');
        else toastOk('已觸發產生今日文稿（背景執行，3 秒後重新整理佇列）');
        setTimeout(() => { _reloadPosts().catch(() => {}); }, 3000);
    } catch (e) { toastErr(e.message); }
    if (btn) { btn.disabled = false; btn.textContent = '⚡ 立即產生今日文稿'; }
};

async function _reloadPosts() {
    const posts = await websiteFetch('/api/website/admin/social/posts');
    if (!_isCurrent()) return;   // 使用者已切走 → 不碰共用 content DOM
    _state.posts = posts?.items || [];
    _renderShell();
}

// ── 審核 Modal ──

_social.openReview = (id) => {
    const p = _find(id);
    if (!p) { toastErr('找不到此文稿'); return; }
    const pf = PLATFORMS[p.platform] || { label: p.platform || '?', bg: '#333', fg: '#ccc' };
    const st = GROUPS.find(g => g.key === p.status);
    const canApprove = p.status === 'draft' || p.status === 'rejected';
    const canReject = p.status === 'draft' || p.status === 'approved';
    const canPublish = p.status === 'approved' || p.status === 'published';
    const inner = `
        <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;color:#fff;font-size:15px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span class="website-pill" style="background:${pf.bg};color:${pf.fg};font-weight:600;">${esc(pf.label)}</span>
                審核文稿
                <span style="color:#888;font-size:12px;font-weight:400;">· ${_sourceHtml(p)} · ${st ? st.label : esc(p.status || '')} · ${esc(fmtDt(p.created_at))}</span>
            </h3>
            <button onclick="window._social.close()" style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;width:30px;height:30px;border-radius:4px;flex-shrink:0;">✕</button>
        </div>
        <div style="padding:18px;">
            <div style="margin-bottom:14px;">
                <label style="${_lbl()}">文稿內容（失焦自動儲存，或按 💾 儲存）</label>
                <textarea id="social-rv-content" rows="10" style="${_inp()};resize:vertical;line-height:1.6;"
                          onchange="window._social.saveDraft('${esc(p.id)}', true)">${esc(p.content || '')}</textarea>
            </div>
            <div>
                <label style="${_lbl()}">配圖</label>
                <div style="display:flex;gap:10px;align-items:flex-start;">
                    <div id="social-rv-media-prev" style="width:128px;height:72px;flex-shrink:0;background:#0d0d0d;border:1px solid #2a2a2a;border-radius:4px;overflow:hidden;"></div>
                    <input id="social-rv-media" value="${esc(p.media_url || '')}" placeholder="配圖網址（/uploads/… 或 https://…），留空 = 無配圖"
                           oninput="window._social.refreshMediaPrev(this.value)"
                           onchange="window._social.saveDraft('${esc(p.id)}', true)" style="${_inp()};flex:1;" />
                </div>
            </div>
            ${p.published_url ? `<div style="font-size:12px;margin-top:10px;color:#888;">已發布連結：<a href="${esc(p.published_url)}" target="_blank" style="color:#3b82f6;word-break:break-all;">${esc(p.published_url)}</a></div>` : ''}
        </div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;">
            <button class="btn btn-ghost btn-sm" onclick="window._social.close()">關閉</button>
            <button class="btn btn-ghost btn-sm" onclick="window._social.saveDraft('${esc(p.id)}')">💾 儲存</button>
            <button class="btn btn-sm" style="background:#374151;" onclick="window._social.copy('${esc(p.id)}')">📋 複製文稿</button>
            ${canReject ? `<button class="btn btn-sm btn-danger" onclick="window._social.reject('${esc(p.id)}')">❌ 退回</button>` : ''}
            ${canApprove ? `<button class="btn btn-sm" style="background:#059669;" onclick="window._social.approve('${esc(p.id)}')">✅ 核准</button>` : ''}
            ${canPublish ? `<button class="btn btn-sm" style="background:#3b82f6;" onclick="window._social.markPublished('${esc(p.id)}')">🔗 ${p.status === 'published' ? '更新貼文連結' : '標記已發布'}</button>` : ''}
        </div>`;
    openModal('social-modal', inner, { width: '680px' });
    _social.refreshMediaPrev(p.media_url || '');
};

_social.close = () => closeModal('social-modal');

_social.refreshMediaPrev = (url) => {
    const prev = document.getElementById('social-rv-media-prev');
    if (!prev) return;
    prev.innerHTML = url
        ? `<img src="${esc(_mediaSrc(url))}" style="width:100%;height:100%;object-fit:cover;" onerror="this.style.opacity=0.2" />`
        : '<div style="color:#555;font-size:11px;display:flex;align-items:center;justify-content:center;height:100%;">無配圖</div>';
};

// 儲存文稿/配圖（silent=true 給失焦自動儲存用，不彈成功 toast）。
// 回傳 true/false 給 approve 判斷「先存再核」是否成功。
_social.saveDraft = async (id, silent = false) => {
    const contentEl = document.getElementById('social-rv-content');
    const mediaEl = document.getElementById('social-rv-media');
    if (!contentEl) return false;
    const content = contentEl.value;
    const media = mediaEl ? mediaEl.value.trim() : '';
    try {
        await websiteFetch(`/api/website/admin/social/posts/${id}`, {
            method: 'PUT', body: { content, media_url: media || null },
        });
        const p = _find(id);
        if (p) { p.content = content; p.media_url = media || null; }
        if (!silent) toastOk('文稿已儲存');
        return true;
    } catch (e) { toastErr(e.message); return false; }
};

_social.approve = async (id) => {
    if (!(await _social.saveDraft(id, true))) return;   // 先存目前編輯，存失敗不核准
    try {
        await websiteFetch(`/api/website/admin/social/posts/${id}/approve`, { method: 'POST' });
        toastOk('已核准 — 用 📋 複製文稿去平台貼文');
        _social.close();
        await _reloadPosts();
    } catch (e) { toastErr(e.message); }
};

_social.reject = async (id) => {
    if (!confirm('退回此文稿？（不採用，保留在「退回」區可再核准）')) return;
    try {
        await websiteFetch(`/api/website/admin/social/posts/${id}/reject`, { method: 'POST' });
        toastOk('已退回');
        _social.close();
        await _reloadPosts();
    } catch (e) { toastErr(e.message); }
};

_social.copy = async (id) => {
    const p = _find(id);
    const contentEl = document.getElementById('social-rv-content');
    const mediaEl = document.getElementById('social-rv-media');
    const content = contentEl ? contentEl.value : ((p && p.content) || '');
    const media = mediaEl ? mediaEl.value.trim() : ((p && p.media_url) || '');
    const txt = content + (media ? `\n\n配圖：${_mediaSrc(media)}` : '');
    try {
        await _copyText(txt);
        toastOk(media ? '已複製文稿 + 配圖連結' : '已複製文稿');
    } catch (e) { toastErr('複製失敗：' + (e.message || e)); }
};

// navigator.clipboard 需要 secure context（LAN http 不算）→ 失敗時退回 execCommand
function _copyText(txt) {
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
        return navigator.clipboard.writeText(txt);
    }
    return new Promise((resolve, reject) => {
        const ta = document.createElement('textarea');
        ta.value = txt;
        ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy') ? resolve() : reject(new Error('execCommand 失敗'));
        } finally { ta.remove(); }
    });
}

_social.markPublished = async (id) => {
    const p = _find(id);
    const url = prompt('貼上實際貼文連結（https://…）', (p && p.published_url) || '');
    if (url == null) return;   // 取消
    const u = url.trim();
    if (!/^https?:\/\//i.test(u)) { toastErr('請輸入 http(s):// 開頭的完整連結'); return; }
    try {
        await websiteFetch(`/api/website/admin/social/posts/${id}/published`, {
            method: 'POST', body: { published_url: u },
        });
        toastOk('已標記為已發布');
        _social.close();
        await _reloadPosts();
    } catch (e) { toastErr(e.message); }
};
