// blog/editor.js — 拆自 blog.js：文章 Modal + Block 編輯器（視覺/Markdown 雙模式）
// + 即時預覽 + SEO 健康度 + 儲存/刪除（純搬移，行為不變）。
// 模組層可變狀態 _editingPost / _previewVisible / _textMode / _textModeError /
// _textSyncTimer 會被重新賦值 → 所有讀寫者必須留在本檔（ES module live-binding
// 限制，import 進來的 binding 不可賦值）；切檔界線以此為準，所以本檔較大。
import { websiteFetch, esc, toastOk, toastErr, openModal, closeModal } from '../../website-utils.js';
import { EMPTY_POST, STATUS, _resolveImageUrl, _state, _blog } from './shared.js';
import { _renderShell } from './shell.js';

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
                ${_modalAiSeoSection(p)}
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

function _modalAiSeoSection(p) {
    const noPostId = !p.id;
    return `
        <div class="pm-section" style="border-left:3px solid #c8a45c;">
            <div class="pm-section-title">🤖 AI SEO</div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px;">
                <button class="btn btn-sm" id="m-ai-seo-btn" onclick="window._blog.aiGenerateSeo()" ${noPostId ? 'disabled' : ''}
                        style="background:#c8a45c;color:#1a1a1a;font-weight:600;${noPostId ? 'opacity:0.5;cursor:not-allowed;' : ''}">
                    🤖 AI 生成 SEO 內容
                </button>
                <span style="color:#888;font-size:11px;">
                    ${noPostId
                        ? '新文章請先「💾 儲存」草稿後才能生成'
                        : '依文章內容生成 SEO 標題/描述、摘要(空才補) + 3 題 FAQ，<strong style="color:#c8a45c;">生成後直接套用並儲存</strong>；可再編輯後按 💾 重存'}
                </span>
            </div>
            <div id="m-faqs-host">${_renderFaqEditor(p.faqs || [])}</div>
        </div>`;
}

function _renderFaqEditor(faqs) {
    faqs = faqs || [];
    return `
        <div style="margin-top:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <label style="color:#9aa0a6;font-size:11px;">常見問題 FAQ <span style="color:#666;">(${faqs.length}) · 輸出 FAQPage 結構化資料 + 文章底部可見區段</span></label>
                <button class="pm-mini-btn" onclick="window._blog.addFaq()">+ 加一題</button>
            </div>
            ${faqs.length === 0
                ? '<div style="color:#666;font-size:11px;padding:8px;border:1px dashed #2a2a2a;border-radius:4px;">尚無 FAQ — 按上方「🤖 AI 生成」或「+ 加一題」</div>'
                : faqs.map((f, i) => `
                    <div style="border:1px solid #2a2a2a;border-radius:4px;padding:8px;margin-bottom:6px;background:#1a1a1a;">
                        <div style="display:flex;gap:6px;align-items:center;margin-bottom:4px;">
                            <span style="color:#c8a45c;font-size:11px;flex-shrink:0;font-weight:600;">Q${i + 1}</span>
                            <input type="text" value="${esc(f.q || '')}" placeholder="問題"
                                   oninput="window._blog.updateFaq(${i}, 'q', this.value)" style="flex:1;font-size:12px;" />
                            <button class="pm-mini-btn" onclick="window._blog.removeFaq(${i})" title="刪除"
                                    style="background:#3a1a1a;border-color:#5a2a2a;color:#f87171;flex-shrink:0;">✕</button>
                        </div>
                        <textarea rows="2" placeholder="答案（1-2 句，訊息密度高）"
                                  oninput="window._blog.updateFaq(${i}, 'a', this.value)"
                                  style="width:100%;font-size:12px;">${esc(f.a || '')}</textarea>
                    </div>`).join('')}
        </div>`;
}

_blog.refreshFaqs = () => {
    const host = document.getElementById('m-faqs-host');
    if (host) host.innerHTML = _renderFaqEditor(_editingPost?.faqs || []);
};
_blog.addFaq = () => {
    if (!_editingPost) return;
    _editingPost.faqs = _editingPost.faqs || [];
    _editingPost.faqs.push({ q: '', a: '' });
    _blog.refreshFaqs();
};
_blog.removeFaq = (i) => {
    if (_editingPost?.faqs) { _editingPost.faqs.splice(i, 1); _blog.refreshFaqs(); }
};
_blog.updateFaq = (i, field, val) => {
    if (_editingPost?.faqs?.[i]) _editingPost.faqs[i][field] = val;
};

_blog.aiGenerateSeo = async () => {
    if (!_editingPost?.id) { toastErr('新文章請先「💾 儲存」草稿再生成'); return; }
    const btn = document.getElementById('m-ai-seo-btn');
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ AI 生成中…（約 20–40 秒）'; }
    try {
        const r = await websiteFetch(`/api/website/admin/seo/posts/${_editingPost.id}/generate`, { method: 'POST' });
        if (!r.ok) { toastErr('生成失敗：' + (r.error || '未知')); return; }
        // 1) 填入欄位（讓使用者看到套用了什麼）
        const setVal = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
        setVal('m-seo-title', r.seo_title);
        setVal('m-seo-desc', r.seo_description);
        const exEl = document.getElementById('m-excerpt');
        const fillExcerpt = exEl && !exEl.value.trim() && r.excerpt;
        if (fillExcerpt) exEl.value = r.excerpt;
        _editingPost.faqs = Array.isArray(r.faqs) ? r.faqs : [];
        _blog.refreshFaqs();
        const det = document.querySelector('#post-modal details');
        if (det) det.open = true;
        // 2) 直接套用：targeted PUT 存進文章（不關 Modal，使用者仍可微調後再按 💾）
        const body = { seo_title: r.seo_title, seo_description: r.seo_description, faqs: _editingPost.faqs };
        if (fillExcerpt) body.excerpt = r.excerpt;
        const saved = await websiteFetch(`/api/website/admin/posts/${_editingPost.id}`, { method: 'PUT', body });
        Object.assign(_editingPost, {
            seo_title: saved.seo_title, seo_description: saved.seo_description,
            faqs: saved.faqs, excerpt: saved.excerpt,
        });
        const idx = _state.posts.findIndex(p => p.id === _editingPost.id);
        if (idx >= 0) _state.posts[idx] = saved;
        toastOk(`已生成並套用 SEO + ${(_editingPost.faqs || []).length} 題 FAQ（對外網站 60 秒後重建）`);
    } catch (e) {
        toastErr('生成失敗：' + (e.message || e));
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = orig; }
    }
};

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
                    #${idx + 1}
                    <select onchange="window._blog.changeBlockType(${idx}, this.value)" title="更改區塊類型（保留文字內容）"
                            style="font-size:11px;padding:2px 6px;background:#0d0d0d;border:1px solid #333;color:#ccc;border-radius:3px;cursor:pointer;margin-left:2px;">
                        ${Object.entries(BLOCK_REGISTRY).map(([key, t]) =>
                            `<option value="${key}" ${b.type === key ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}
                    </select>
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

// ── 更改區塊類型（保留文字內容）──
// 把任一 block 的文字內容萃出來，再依目標 type 重建。text-type 之間
// （段落/標題/引言/列表）互轉會完整保留文字；轉成圖片/影片則把文字當 caption。
function _blockToText(b) {
    if (!b) return '';
    if (b.type === 'list') return (b.items || []).join('\n');
    if (b.type === 'image' || b.type === 'video') return b.caption || '';
    return b.text || '';
}

function _convertBlock(b, newType) {
    const text = _blockToText(b);
    switch (newType) {
        case 'paragraph':
            return b.lead ? { type: 'paragraph', text, lead: true } : { type: 'paragraph', text };
        case 'heading':
            return { type: 'heading', level: b.level === 3 ? 3 : 2, text };
        case 'quote':
            return b.author ? { type: 'quote', text, author: b.author } : { type: 'quote', text };
        case 'list': {
            const items = text.split('\n').map(s => s.trim()).filter(Boolean);
            return { type: 'list', items: items.length ? items : [''], ordered: !!b.ordered };
        }
        case 'image':
            return { type: 'image', src: b.src || '', alt: b.alt || '',
                     caption: b.caption || (b.type !== 'image' ? text : ''), width: b.width || 'content' };
        case 'video':
            return { type: 'video', youtube_id: b.youtube_id || '',
                     caption: b.caption || (b.type !== 'video' ? text : ''), width: b.width || 'content' };
        default:
            return b;
    }
}

_blog.changeBlockType = (idx, newType) => {
    if (!_editingPost?.body?.[idx]) return;
    const cur = _editingPost.body[idx];
    if (cur.type === newType || !BLOCK_REGISTRY[newType]) return;
    _editingPost.body[idx] = _convertBlock(cur, newType);
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
        // FAQ 不在 DOM 表單，隨 _editingPost.faqs 維護（AI 生成 / 手動編輯）
        faqs: (_editingPost?.faqs || []).filter(f => (f.q || '').trim() && (f.a || '').trim()),
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
