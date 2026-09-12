// ────────────────────────────────────────────────────────────────────────────
// /showcase-edit.html 作品編輯器 —— 狀態（TOKEN／API／EMBED／DATA／EDITABLE）、小工具、系列快建、credits 正規化、影片 provider 提示、SEO 區塊、未存編輯的收放（_dirtyEls）、載入／儲存／錯誤、AI SEO
//
// ⚠ 這是「傳統 script」不是 module：showcase-edit.html 底部依序 <script src> 載入 js/showcase-edit/*.js，
//   六支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見（原本是同一段 inline script，
//   2026-09-12 原樣切開、零改寫；同 my.html → js/my/ 的拆法）。載入順序＝原本由上而下的執行順序，不可調換。
// ────────────────────────────────────────────────────────────────────────────
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const API = '/api/v1/crm/public/showcase-edit/' + TOKEN;
// embed 偵測：?embed 參數優先（?embed=0 = 逃生口），沒帶參數時 iframe 自動視為 embed
const _embedParam = new URLSearchParams(location.search).get('embed');
const EMBED = _embedParam != null ? _embedParam === '1' : (window.self !== window.top);
if (EMBED) document.documentElement.classList.add('embed-mode');
let DATA = null;
let EDITABLE = false;

// HTML 逃脫：跟 js/shared/dom.js 同一套（& < > " ' 都逃）。原本走 textContent → innerHTML 不逃引號，
// 塞進 attr="…" 就是屬性逃逸（test_one_esc 釘全前端只准一種寫法；這頁是傳統 script 不能 import，抄同一份）
function _esc(s) { return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function _toast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2000); }
// 「即存」pill — 該區操作立即寫入後端，不用等 master save；唯讀模式一律不顯示
function _instantPill(title = '此區的變更立即儲存，不需按「儲存所有變更」') {
    return EDITABLE ? `<span class="sc-instant" title="${title}">即存</span>` : '';
}

// 快速建立系列（系列下拉/分類小測驗「找不到 → 新增」）— 建立為隱藏系列（自動 slug），
// 名稱/介紹/封面/網址之後在官網管理補齊再開顯示。回傳新系列物件或 null。
async function _quickCreateSeries() {
    const title = (prompt('新系列名稱（建立後預設隱藏，介紹/封面/網址之後在官網管理補齊）') || '').trim();
    if (!title) return null;
    try {
        const res = await fetch(API + '/series', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title_zh: title }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '建立失敗');
        const s = await res.json();
        (DATA.series_available = DATA.series_available || []).push(
            { id: s.id, slug: s.slug, title_zh: s.title_zh, visible: !!s.visible });
        _toast(`已建立系列「${s.title_zh}」`);
        return s;
    } catch (e) { _toast(e.message); return null; }
}

// 在系列 <select> 補一個剛建立/缺漏的選項並選取（插在「+ 建立新系列…」之前）
function _ensureSeriesOption(sel, id) {
    if (!sel || !id) return;
    if (!sel.querySelector(`option[value="${id}"]`)) {
        const s = (DATA.series_available || []).find(x => String(x.id) === String(id));
        const label = `${_esc((s && s.title_zh) || id)}${s && s.visible === false ? '（隱藏中）' : ''}`;
        const anchor = sel.querySelector('option[value="__new__"]');
        const html = `<option value="${id}">${label}</option>`;
        if (anchor) anchor.insertAdjacentHTML('beforebegin', html);
        else sel.insertAdjacentHTML('beforeend', html);
    }
    sel.value = String(id);
}

// Embed 模式：把內容高度回傳給父視窗（CRM 完稿結案 iframe），讓外框自動長高、免內部捲軸。
function _postHeight() {
    if (!EMBED) return;
    try {
        const pg = document.getElementById('page');
        const h = Math.max(pg ? pg.scrollHeight : 0, document.body.scrollHeight || 0);
        if (window.parent) window.parent.postMessage({ type: 'showcase-embed-height', height: h + 24 }, '*');
    } catch (_) {}
}

// 把舊 flat 格式 credits 轉成 block 格式（向下相容讀取，寫入永遠是新格式）
function _normalizeCredits(raw) {
    const arr = Array.isArray(raw) ? raw : [];
    if (!arr.length) return [];
    if (arr[0] && typeof arr[0] === 'object' && 'entries' in arr[0]) return arr;  // 已是新格式
    // fallback block 留空 name — _renderBlock + Astro [slug].astro 兩端各自有「其他」fallback。
    return [{
        role_id: null,
        name_zh: '',
        name_en: '',
        entries: arr.map(c => ({
            duty: c.role || '',
            name: c.name || '',
            resume_url: c.resume_url || '',
        })),
    }];
}

// 取得當前 blocks（已 normalize），用於 mutation 起點
function _currentBlocks() {
    return _normalizeCredits((DATA && DATA.credits) || []);
}

// ── 附加影片列模板（_render 初始渲染 + 「+ 新增影片」動態插入共用）──
function _extraVideoRowHtml(v) {
    return `<div class="extra-video-row" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
        <input type="url" class="extra-video-url" value="${_esc(v.url || '')}"
               placeholder="YouTube / Vimeo / Facebook 影片連結" style="flex:2;min-width:0;">
        <input type="text" class="extra-video-caption" value="${_esc(v.caption || '')}"
               placeholder="影片說明（可空）" style="flex:1;min-width:0;">
        <button class="btn btn-danger btn-sm" type="button" data-action="del-extra-video" title="移除此列">✕</button>
    </div>`;
}

// ── 主影片 provider 偵測（純提示用；正式解析在後端 services/website/video_utils.py，
//    pattern 增減平台時要跟它同步，否則提示會講錯話）──
// youtube watch?v=/youtu.be/embed/shorts → 'youtube'；vimeo.com/<數字> → 'vimeo'；
// facebook.com/*/videos/ | facebook.com/watch | fb.watch → 'facebook'；
// 其他 http(s) → 'link'（對外站顯示「到原平台觀看」按鈕）；空/非網址 → null
function _videoProvider(url) {
    const u = (url || '').trim();
    if (!u) return null;
    if (/(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)/i.test(u)) return 'youtube';
    if (/vimeo\.com\/\d+/i.test(u)) return 'vimeo';
    if (/(?:facebook\.com\/(?:[^\/?#]+\/videos\/|watch)|fb\.watch\/)/i.test(u)) return 'facebook';
    if (/^https?:\/\//i.test(u)) return 'link';
    return null;
}

// 依 #inp-video 現值更新 #video-hint 提示（youtube/空值 → 隱藏）
function _updateVideoHint() {
    const inp = document.getElementById('inp-video');
    const hint = document.getElementById('video-hint');
    if (!inp || !hint) return;
    const p = _videoProvider(inp.value);
    if (!p || p === 'youtube') {
        hint.style.display = 'none';
        hint.textContent = '';
        return;
    }
    if (p === 'vimeo') {
        hint.textContent = '✓ 偵測到 Vimeo 影片 — 對外站將內嵌播放。此平台無法自動取縮圖，建議上傳封面圖。';
    } else if (p === 'facebook') {
        hint.textContent = '✓ 偵測到 Facebook 影片 — 對外站將內嵌播放。此平台無法自動取縮圖，建議上傳封面圖；FB 影片須為「公開」。';
    } else {
        hint.textContent = '此連結非 YouTube/Vimeo/FB — 對外站將顯示「到原平台觀看」按鈕。';
    }
    hint.style.display = 'block';
}

// ── 統一 SEO 區塊：301 轉址 + SEO 覆寫 + AI SEO 規劃（Stage 6 內容；三塊各掛 sub-id）
function _renderSeoSection(sc) {
    const seo = sc.seo || {};
    const oldSlugs = sc.public_old_slugs || [];
    const kw = Array.isArray(seo.keywords) ? seo.keywords.join(', ') : '';
    const reviewedAt = seo.last_ai_review_at
        ? new Date(seo.last_ai_review_at).toLocaleDateString('zh-TW')
        : '尚未生成';
    const reviewedBy = seo.last_ai_review_by || '—';
    const needsReview = seo.needs_ai_review !== false;
    const facts = Array.isArray(seo.key_facts) ? seo.key_facts : [];
    const faqs = Array.isArray(seo.faqs) ? seo.faqs : [];
    const factsHtml = facts.length
        ? facts.map(f => `<li><strong>${_esc(f.label || '')}</strong>：${_esc(f.value || '')}</li>`).join('')
        : '<li style="color:var(--text3);">（尚未生成）</li>';
    const faqsHtml = faqs.length
        ? faqs.map(f => `<li style="margin-bottom:8px;"><strong>Q：${_esc(f.q || '')}</strong><br>A：${_esc(f.a || '')}</li>`).join('')
        : '<li style="color:var(--text3);">（尚未生成）</li>';
    const statusBadge = needsReview
        ? '<span style="color:var(--warn);font-size:11px;">● 等待 AI 重新生</span>'
        : '<span style="color:var(--success);font-size:11px;">✓ 已審核</span>';

    const _h = (label, hint) => `
        <div style="margin-top:18px;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border);">
            <span style="color:var(--accent);font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;">${label}</span>
            ${hint ? `<span style="color:var(--text3);font-size:11px;margin-left:8px;">${hint}</span>` : ''}
        </div>`;

    return `<div class="section" style="margin-bottom:0;">
        <div id="sec-seo-301">
            ${_h('SEO 301 轉址', `（${oldSlugs.length} 條 · 從舊網址轉到此作品）`)}
            <div style="color:var(--text3);font-size:11px;margin-bottom:6px;line-height:1.6;">
                一行一個舊網址（純字串自動展為 <code>/works/{slug}</code>、<code>/</code> 開頭視為完整路徑）。<br>
                軟 301（Astro）+ 硬 301（NAS nginx）雙保險，Google 將舊 URL 權重轉到此作品。
            </div>
            <textarea id="inp-old-slugs" rows="3"
                placeholder="/portfolio/abc&#10;/2024/03/exhibition&#10;old-name"
                style="width:100%;font-family:ui-monospace,monospace;font-size:12px;">${_esc(oldSlugs.join('\n'))}</textarea>
        </div>

        <div id="sec-seo-override">
            ${_h('SEO 覆寫', '（選填，空則自動推算）')}
            <div style="display:grid;grid-template-columns:1fr;gap:10px;">
                <div>
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">SEO Title 覆寫（不填用作品標題）</label>
                    <input id="inp-seo-title" type="text" maxlength="120"
                        value="${_esc(seo.seo_title || '')}"
                        placeholder="搜尋結果標題，建議 50-60 字以內" style="width:100%;">
                </div>
                <div>
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">SEO Description 覆寫（不填用作品描述）</label>
                    <textarea id="inp-seo-desc" maxlength="500" rows="2"
                        placeholder="搜尋結果摘要，建議中文 60-100 字最佳" style="width:100%;">${_esc(seo.seo_description || '')}</textarea>
                </div>
                <div>
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">Keywords（逗號分隔；不填用分類+標籤）</label>
                    <input id="inp-seo-keywords" type="text"
                        value="${_esc(kw)}"
                        placeholder="如:紀錄片, 展覽, 美術館" style="width:100%;">
                </div>
                <div>
                    <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">Canonical URL（跨站發布時指向原作；少用）</label>
                    <input id="inp-seo-canonical" type="text" maxlength="500"
                        value="${_esc(seo.canonical_url || '')}"
                        placeholder="https://other-site.com/original-article" style="width:100%;">
                </div>
            </div>
        </div>

        <div id="sec-seo-ai">
            ${_h('AI SEO 規劃 ' + _instantPill('此區的操作立即生效，不需按「儲存所有變更」'), '（自動生成預覽，不顯示在頁面 HTML，給 AI 爬蟲讀）')}
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:11px;color:var(--text3);">
                <span>上次審閱：${_esc(reviewedAt)} by ${_esc(reviewedBy)}</span>
                ${statusBadge}
            </div>
            <div style="margin-bottom:10px;">
                <label style="display:block;color:var(--text3);font-size:11px;margin-bottom:4px;">詳細介紹</label>
                <textarea readonly rows="4"
                    style="width:100%;font-size:12px;background:var(--bg3);color:var(--text2);cursor:default;">${_esc(seo.narrative_long || '（尚未生成；請點下方「請 AI 重新生」按鈕，下次 admin/Claude 跑 pipeline 時會補）')}</textarea>
            </div>
            <div style="margin-bottom:10px;">
                <div style="color:var(--text3);font-size:11px;margin-bottom:4px;">作品事實</div>
                <ul style="margin:0;padding-left:20px;font-size:12px;color:var(--text2);">${factsHtml}</ul>
            </div>
            <div style="margin-bottom:12px;">
                <div style="color:var(--text3);font-size:11px;margin-bottom:4px;">常見問題</div>
                <ul style="margin:0;padding-left:20px;font-size:12px;color:var(--text2);">${faqsHtml}</ul>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button id="btn-request-ai-review" type="button" class="btn btn-sm" title="標記等待 AI，下次排程才實際跑（不耗額度）">請 AI 重新生</button>
                <button id="btn-run-ai-now" type="button" class="btn btn-sm" title="立即呼叫 Claude 跑此作品 SEO（耗 Max 額度，30-60 秒）">立即執行此作品</button>
            </div>
        </div>
    </div>`;
}


// ── 未存的編輯要在重畫之間活下來 ─────────────────────────────────────
// 🔴 上傳圖／套用專案描述／AI 生成／存 credits 都會重抓整份資料並**整頁重畫**（_render 是
//    innerHTML 換掉）。原本重畫＝把伺服器那份蓋回畫面，打到一半、還沒按「儲存所有變更」的
//    欄位全部消失（同事 2026-09-11 回饋：只有上傳的圖不會消失）。
//    做法：使用者動過的欄位記在 _dirtyEls（模組層，跨重畫存活）；_render 先收值、畫完放回、
//    再標回未儲存。**只放回動過的** —— 沒動過的要拿伺服器的新值，「套用專案描述」寫進去的
//    描述才顯示得出來；而那種「本來就是要覆蓋」的動作要先 _forgetDirty 它覆蓋的欄位。
//    key 形狀：'f:<data-field>' | 'id:<element id>' | 'rows:extra-videos' | 'data:cats'
const _dirtyEls = new Set();
// 🔴 分類勾選存在 DATA 裡，而 _reload 是先把 DATA 換成伺服器那份才 _render —— 快照時已經是舊值。
//    所以草稿另外記在這裡（toggle／小測驗套用時寫），不從 DATA 讀。
let _catsDraft = null;
const _DIRTY_GROUP = {   // key → setDirty 用的群組名（給狀態列顯示「有未儲存的變更：…」）
    'id:inp-ai-ref-notes': '_ai_notes', 'id:inp-old-slugs': '_old_slugs',
    'id:inp-seo-title': '_seo', 'id:inp-seo-desc': '_seo', 'id:inp-seo-keywords': '_seo', 'id:inp-seo-canonical': '_seo',
    'rows:extra-videos': '_extra_videos', 'data:cats': '_cats',
};
function _markDirtyEl(key) { _dirtyEls.add(key); }
function _forgetDirty(...keys) { keys.forEach(k => _dirtyEls.delete(k)); }
function _readDirtyEl(key) {
    if (key.startsWith('f:')) {
        const el = document.querySelector(`[data-field="${key.slice(2)}"]`);
        return el ? (el.type === 'checkbox' ? el.checked : el.value) : undefined;
    }
    if (key.startsWith('id:')) { const el = document.getElementById(key.slice(3)); return el ? el.value : undefined; }
    if (key === 'rows:extra-videos') {
        return [...document.querySelectorAll('#extra-videos-list .extra-video-row')].map(row => ({
            url: row.querySelector('.extra-video-url')?.value || '',
            caption: row.querySelector('.extra-video-caption')?.value || '' }));
    }
    if (key === 'data:cats') return Array.isArray(_catsDraft) ? [..._catsDraft] : undefined;
    return undefined;
}
function _writeDirtyEl(key, v) {
    if (key.startsWith('f:')) {
        const el = document.querySelector(`[data-field="${key.slice(2)}"]`);
        if (!el) return false;
        if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
        return true;
    }
    if (key.startsWith('id:')) { const el = document.getElementById(key.slice(3)); if (!el) return false; el.value = v; return true; }
    if (key === 'rows:extra-videos') {
        const list = document.getElementById('extra-videos-list');
        if (!list) return false;
        list.innerHTML = (v || []).map(r => _extraVideoRowHtml(r)).join('');
        return true;
    }
    return false;   // data:cats 是畫之前直接寫進 DATA 的，不走這裡
}
/** 重畫前收：{key → 值}。沒有動過任何東西就是空的（第一次載入也是）。 */
function _snapshotDirty() {
    const keep = new Map();
    for (const k of _dirtyEls) { const v = _readDirtyEl(k); if (v !== undefined) keep.set(k, v); }
    return keep;
}
/** 畫完放回，並把群組重新標成未儲存（_bindEvents 剛把 _dirtyKeys 建成新的空 Set）。 */
function _restoreDirty(keep) {
    for (const [k, v] of keep) {
        if (k === 'data:cats') { window._scSetDirty?.('_cats'); continue; }
        if (_writeDirtyEl(k, v)) window._scSetDirty?.(_DIRTY_GROUP[k] || (k.startsWith('f:') ? k.slice(2) : '_misc'));
    }
}

async function _load() {
    if (!TOKEN) { _showError(); return; }
    try {
        const res = await fetch(API);
        if (!res.ok) { _showError(); return; }
        DATA = await res.json();
        EDITABLE = !!DATA.editable;
        _render();
    } catch (_) { _showError(); }
}

function _showError() {
    document.getElementById('loading').classList.add('fade');
    document.getElementById('error').style.display = 'block';
}

async function _save(body) {
    const res = await fetch(API, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '儲存失敗'); }
    return res.json();
}

async function _upload(subpath, file) {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(API + subpath, { method: 'POST', body: form });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '上傳失敗'); }
    return res.json();
}

async function _searchStaff(q) {
    const res = await fetch(`${API}/staff_search?q=${encodeURIComponent(q || '')}`);
    if (!res.ok) throw new Error('搜尋失敗');
    return (await res.json()).items || [];
}

async function _quickAddStaff(name, role) {
    const res = await fetch(`${API}/staff_quick_add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, role }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '建立失敗'); }
    return res.json();
}

// === AI 協助撰寫 SEO 專案描述（AI 出題 → 答 → 生成 → 套用到 描述 + seo_description）===
let _aiDescAnswers = [];
async function _aiDescStart() {
    const panel = document.getElementById('ai-desc-panel');
    if (!panel) return;
    panel.style.display = 'block';
    panel.innerHTML = '<div style="color:var(--text3);font-size:13px;">AI 產生引導問題中…（約 30–60 秒）</div>';
    let questions = [];
    try {
        const r = await fetch(API + '/ai_description/questions', { method: 'POST' });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'AI 產生問題失敗');
        questions = (await r.json()).questions || [];
    } catch (e) {
        panel.innerHTML = `<div style="color:var(--danger);font-size:13px;">${_esc(e.message)}</div>`;
        return;
    }
    if (!questions.length) {
        panel.innerHTML = '<div style="color:var(--text3);font-size:13px;">AI 沒有回問題，請直接手動撰寫。</div>';
        return;
    }
    panel.innerHTML =
        '<div style="font-size:13px;color:var(--text2);margin-bottom:8px;">回答幾個問題（可跳過空題），AI 會幫你寫成精簡的 SEO 描述：</div>' +
        '<div id="ai-desc-qs">' + questions.map(q =>
            `<div style="margin-bottom:8px;">
                <label style="display:block;color:var(--text2);font-size:12px;margin-bottom:3px;">${_esc(q)}</label>
                <input type="text" class="ai-desc-a" data-q="${_esc(q)}" style="width:100%;" placeholder="（可留空）">
            </div>`).join('') + '</div>' +
        '<button class="btn btn-sm" id="btn-ai-desc-gen" type="button">生成描述</button> ' +
        '<button class="btn btn-sm" id="btn-ai-desc-cancel" type="button" style="opacity:.7;">取消</button>';
    document.getElementById('btn-ai-desc-cancel')?.addEventListener('click', () => { panel.style.display = 'none'; });
    document.getElementById('btn-ai-desc-gen')?.addEventListener('click', () => _aiDescGenerate());
}
async function _aiDescGenerate() {
    const panel = document.getElementById('ai-desc-panel');
    const inputs = document.querySelectorAll('.ai-desc-a');
    if (inputs.length) _aiDescAnswers = [...inputs].map(i => ({ q: i.dataset.q, a: i.value.trim() }));
    const btn = document.getElementById('btn-ai-desc-gen');
    if (btn) { btn.disabled = true; btn.textContent = '生成中…（約 30–60 秒）'; }
    let draft = '';
    try {
        const r = await fetch(API + '/ai_description/draft', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers: _aiDescAnswers }),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'AI 生成失敗');
        draft = (await r.json()).description || '';
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = '生成描述'; }
        _toast(e.message); return;
    }
    panel.innerHTML =
        '<div style="font-size:12px;color:var(--text3);margin-bottom:6px;">AI 草稿（可再手動編輯）：</div>' +
        `<textarea id="ai-desc-draft" style="width:100%;min-height:90px;">${_esc(draft)}</textarea>` +
        '<div style="margin-top:8px;">' +
        '<button class="btn btn-sm" id="btn-ai-desc-apply" type="button">✓ 套用（存進描述 + SEO）</button> ' +
        '<button class="btn btn-sm" id="btn-ai-desc-regen" type="button" style="opacity:.7;">↻ 重新生成</button> ' +
        '<button class="btn btn-sm" id="btn-ai-desc-cancel2" type="button" style="opacity:.7;">取消</button>' +
        '</div>';
    document.getElementById('btn-ai-desc-regen')?.addEventListener('click', () => _aiDescGenerate());
    document.getElementById('btn-ai-desc-cancel2')?.addEventListener('click', () => { panel.style.display = 'none'; });
    document.getElementById('btn-ai-desc-apply')?.addEventListener('click', () => _aiDescApply());
}
async function _aiDescApply() {
    const draft = (document.getElementById('ai-desc-draft')?.value || '').trim();
    if (!draft) { _toast('草稿是空的'); return; }
    try {
        await _save({ description: draft, seo: { seo_description: draft } });
        _toast('已套用（描述 + SEO meta 已更新）');
        _forgetDirty('f:description', 'id:inp-seo-desc');   // 這兩格就是要被覆蓋的，重畫時拿新值
        await _reload();
    } catch (e) { _toast(e.message); }
}
