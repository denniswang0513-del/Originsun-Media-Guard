/**
 * 參考影片詳情元件（docs/REFERENCE_LIBRARY.md 階段 1）
 * ---
 * 一支片一個小頁面：影片嵌入 + 基本欄位 + 八族分類 + 研究四欄（逐格自動儲存）。
 *
 * 用法（獨立頁與 SPA overlay 共用同一份，比照 plan-matrix.js）：
 *   renderReference(container, {
 *     ref,                      // GET /references/{rid} 的 reference
 *     shareToken,               // 有值 = 公開共編模式（免登入、權限自動收窄）
 *     fetcher,                  // 可省，預設 prop-fetch.js 的 tfetch
 *     getGuestName,             // 公開共編署名
 *     facetOptions,             // { technique: [...], ... } datalist 建議（可省）
 *   });
 *
 * URL 與權限都由元件自己算（宿主頁只給 ref + 模式）—— 宿主頁不再手拼端點，
 * 也不用宣告「哪些欄位唯讀」（那是後端 _PUBLIC_ALLOW 的事，這裡只跟著模式走）。
 *
 * 換膚：外層 .rfc 吃 --rfc-* 變數（深色預設）；官網白底頁在 <html> 掛
 * `ref-theme-light` 覆寫（同 plan-matrix 的 plan-theme-light 慣例）。
 */

import { tfetch } from './prop-fetch.js';

const STYLE_ID = 'rfc-style';
const API = '/api/v1/references';

// 研究四欄 —— 欄 key 與後端 RESEARCH_COLS 對齊（改這裡要同步 api_references.py）
const RESEARCH_COLS = [
    { key: 'purpose', label: '核心目的', hint: '客戶想要什麼？' },
    { key: 'idea', label: '創意 idea & 手法', hint: '影片概念是什麼？怎麼說？' },
    { key: 'material', label: '創意素材', hint: '用什麼元素達成這個概念？' },
    { key: 'moment', label: '關鍵橋段', hint: '亮眼的畫面或情節在哪？' },
];

// 八族分類 —— 對映 Notion 參考影片資料庫的屬性
const FACETS = [
    { key: 'category', label: '類別', ph: '企業形象、宣傳影片…' },
    { key: 'brand', label: '品牌', ph: '客戶或品牌名' },
    { key: 'studio', label: '製作單位', ph: '簡訊設計…', single: true },
    { key: 'paragon', label: '典範', ph: '導演／工作室' },
    { key: 'technique', label: '技巧', ph: '平行剪接、分割畫面…' },
    { key: 'emotion', label: '情感取向', ph: '溫暖、幽默、衝擊…' },
    { key: 'keyword', label: '關鍵字', ph: '孩童、公益、汽車…' },
    { key: 'study', label: '研究用途', ph: '創意研究、BTS研究…' },
];

const STYLE_CSS = `
.rfc { --rfc-ink: #e8e8e8; --rfc-sub: #8b8b8b; --rfc-line: #333; --rfc-cell: #1f1f1f;
  --rfc-head: #262626; --rfc-card: #1a1a1a; --rfc-accent: #3b82f6;
  color: var(--rfc-ink); font-size: 13px; }
html.ref-theme-light .rfc { --rfc-ink: #262626; --rfc-sub: #737373; --rfc-line: #e5e5e5;
  --rfc-cell: #fff; --rfc-head: #fafafa; --rfc-card: #fff; --rfc-accent: #c9372c; }
.rfc .rfc-top { display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }
.rfc .rfc-video { flex: 1 1 420px; min-width: 280px; }
.rfc .rfc-embed { position: relative; width: 100%; aspect-ratio: 16/9; background: #000;
  border: 1px solid var(--rfc-line); border-radius: 2px; overflow: hidden; }
.rfc .rfc-embed iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
.rfc .rfc-embed .ph { display: flex; align-items: center; justify-content: center; height: 100%;
  color: var(--rfc-sub); font-size: 12px; text-align: center; padding: 20px; line-height: 1.8; }
.rfc .rfc-embed .ph a { color: var(--rfc-accent); }
.rfc .rfc-meta { flex: 1 1 320px; min-width: 260px; }
.rfc .rfc-card { border: 1px solid var(--rfc-line); border-radius: 2px; background: var(--rfc-card);
  padding: 12px 14px; margin-bottom: 12px; }
.rfc .rfc-card h4 { margin: 0 0 8px; font-size: 13px; font-weight: 600; letter-spacing: .05em; }
.rfc .rfc-row { display: flex; gap: 10px; align-items: flex-start; padding: 5px 0; }
.rfc .rfc-row > label { min-width: 68px; font-size: 11.5px; color: var(--rfc-sub);
  white-space: nowrap; padding-top: 6px; }
.rfc .rfc-row > .v { flex: 1; min-width: 0; }
.rfc input.rfc-in, .rfc textarea.rfc-in, .rfc select.rfc-in {
  width: 100%; box-sizing: border-box; font-family: inherit; font-size: 13px;
  color: var(--rfc-ink); background: transparent; border: 1px solid transparent;
  border-radius: 2px; padding: 5px 6px; outline: none; }
.rfc input.rfc-in:hover, .rfc textarea.rfc-in:hover { border-color: var(--rfc-line); }
.rfc input.rfc-in:focus, .rfc textarea.rfc-in:focus { border-color: var(--rfc-accent);
  background: var(--rfc-cell); }
.rfc textarea.rfc-in { min-height: 60px; resize: none; overflow-y: hidden; line-height: 1.8; }
.rfc .rfc-in[readonly] { color: var(--rfc-sub); }
.rfc .rfc-in[readonly]:hover { border-color: transparent; }
.rfc .rfc-url { font-size: 11px; color: var(--rfc-sub); word-break: break-all; padding: 0 6px; }
.rfc .rfc-chips { display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
.rfc .rfc-chip { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px;
  background: var(--rfc-head); border: 1px solid var(--rfc-line); border-radius: 2px;
  padding: 2px 4px 2px 8px; }
.rfc .rfc-chip button { background: none; border: 0; cursor: pointer; color: var(--rfc-sub);
  font-size: 13px; line-height: 1; padding: 0 3px; }
.rfc .rfc-chip button:hover { color: #e05252; }
.rfc .rfc-chip.ro { padding: 2px 8px; }
.rfc .rfc-facet-in { border: 1px dashed var(--rfc-line); border-radius: 2px; background: transparent;
  color: var(--rfc-ink); font-size: 11.5px; padding: 3px 6px; width: 130px; outline: none; }
.rfc .rfc-facet-in:focus { border-color: var(--rfc-accent); border-style: solid; }
.rfc .rfc-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
  background: var(--rfc-line); border: 1px solid var(--rfc-line); }
.rfc .rfc-gh { background: var(--rfc-head); padding: 8px 10px; }
.rfc .rfc-gh .t { font-size: 12px; font-weight: 600; }
.rfc .rfc-gh .h { font-size: 10.5px; color: var(--rfc-sub); margin-top: 3px; line-height: 1.5; }
.rfc .rfc-gc { background: var(--rfc-cell); padding: 4px; position: relative; }
.rfc .rfc-gc textarea { min-height: 100px; font-size: 12.5px; }
.rfc .rfc-rowbar { display: flex; justify-content: space-between; align-items: center;
  gap: 8px; margin-top: 8px; }
.rfc .rfc-btn { background: var(--rfc-card); border: 1px solid var(--rfc-line); border-radius: 2px;
  color: var(--rfc-ink); font-size: 11.5px; padding: 4px 10px; cursor: pointer; }
.rfc .rfc-btn:hover { border-color: var(--rfc-accent); color: var(--rfc-accent); }
.rfc .rfc-btn.danger:hover { border-color: #e05252; color: #e05252; }
.rfc .rfc-links a { color: var(--rfc-accent); text-decoration: none; }
.rfc .rfc-links a:hover { text-decoration: underline; }
.rfc .rfc-link-row { display: flex; gap: 8px; align-items: baseline; padding: 5px 0;
  border-bottom: 1px dashed var(--rfc-line); font-size: 12.5px; }
.rfc .rfc-link-row:last-child { border-bottom: none; }
.rfc .rfc-link-row .st { font-size: 11px; color: var(--rfc-sub); }
.rfc .rfc-hint { font-size: 11px; color: var(--rfc-sub); line-height: 1.7; }
.rfc .rfc-save { position: sticky; bottom: 0; text-align: right; font-size: 11px;
  color: var(--rfc-sub); min-height: 16px; padding: 3px 0; }
.rfc .rfc-save.err { color: #e05252; }
.rfc .rfc-conflict { border: 1px solid #e05252; border-radius: 2px; margin-top: 4px; padding: 7px;
  font-size: 11.5px; background: var(--rfc-card); }
.rfc .rfc-conflict .ct { color: #e05252; font-weight: 600; margin-bottom: 3px; }
.rfc .rfc-conflict pre { white-space: pre-wrap; margin: 4px 0; padding: 5px;
  background: var(--rfc-cell); border: 1px solid var(--rfc-line);
  font-family: inherit; font-size: 11.5px; }
.rfc .rfc-cb { display: flex; gap: 6px; align-items: center; font-size: 12px; cursor: pointer; }
@media (max-width: 860px) { .rfc .rfc-grid { grid-template-columns: 1fr; } }
`;

function esc(s) { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; }
function attr(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function safeUrl(u) { return /^https?:\/\//i.test(String(u ?? '')) ? String(u) : ''; }

function _injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = STYLE_CSS;
    document.head.appendChild(st);
}

// ── 影片嵌入（provider 由後端 parse_video_url 快取；link 無 embed → 外連）──
function _embedHtml(ref) {
    const u = safeUrl(ref.url);
    if (ref.provider === 'youtube' && ref.video_id) {
        return `<iframe src="https://www.youtube.com/embed/${attr(ref.video_id)}"
            title="影片" allow="accelerometer; clipboard-write; encrypted-media; picture-in-picture"
            referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>`;
    }
    if (ref.provider === 'vimeo' && ref.video_id) {
        return `<iframe src="https://player.vimeo.com/video/${attr(ref.video_id)}"
            title="影片" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>`;
    }
    if (ref.provider === 'facebook' && u) {
        return `<iframe src="https://www.facebook.com/plugins/video.php?href=${encodeURIComponent(u)}&show_text=false"
            title="影片" allow="encrypted-media" allowfullscreen></iframe>`;
    }
    return `<div class="ph">${u
        ? `這個連結沒有內嵌播放器<br><a href="${attr(u)}" target="_blank" rel="noopener">在新視窗開啟 ↗</a>`
        : '尚未填影片連結'}</div>`;
}

export function renderReference(container, opts) {
    _injectStyle();
    const ref = opts.ref;
    const fo = opts.facetOptions || {};
    // 公開共編模式：權限跟著模式收窄（fail-closed），URL 也由這裡組
    const shared = !!opts.shareToken;
    const can = { facets: !shared, curated: !shared };
    const base = shared
        ? `${API}/shared/${encodeURIComponent(opts.shareToken)}/${encodeURIComponent(ref.id)}`
        : `${API}/${encodeURIComponent(ref.id)}`;
    const ctx = {
        ...opts, can,
        fetcher: opts.fetcher || tfetch,
        endpoints: { patch: base, rows: `${base}/research/rows`, row: (id) => `${base}/research/rows/${encodeURIComponent(id)}` },
        reload: opts.reload || (async () => (await (opts.fetcher || tfetch)(base)).reference),
    };
    container.classList.add('rfc');

    const field = (key, label, ph, multi) => `
        <div class="rfc-row"><label>${esc(label)}</label><div class="v">${multi
            ? `<textarea class="rfc-in rfc-ta" data-kind="field" data-key="${key}" placeholder="${attr(ph)}"></textarea>`
            : `<input class="rfc-in" data-kind="field" data-key="${key}" placeholder="${attr(ph)}">`}</div></div>`;

    const facetBlock = FACETS.map(f => `
        <div class="rfc-row" data-facet="${f.key}">
            <label>${esc(f.label)}</label>
            <div class="v">
                <div class="rfc-chips" data-chips="${f.key}"></div>
                ${can.facets === false ? '' : `
                <input class="rfc-facet-in" data-facet-in="${f.key}" placeholder="${attr(f.ph)}"
                       list="rfc-dl-${f.key}" ${f.single ? '' : 'title="輸入後按 Enter 加入"'}>
                <datalist id="rfc-dl-${f.key}">
                    ${(fo[f.key] || []).map(v => `<option value="${attr(v)}"></option>`).join('')}
                </datalist>`}
            </div>
        </div>`).join('');

    const rows = ref.research?.rows || [];
    const researchRows = rows.map((row, i) => `
        ${RESEARCH_COLS.map((c, ci) => `
        <div class="rfc-gc" data-row="${attr(row.id)}" data-col="${c.key}">
            <textarea class="rfc-in rfc-ta" data-kind="research" data-row="${attr(row.id)}"
                data-col="${c.key}" placeholder="${i === 0 ? attr(c.hint) : ''}"></textarea>
            ${(ci === RESEARCH_COLS.length - 1 && rows.length > 1) ? `
            <button class="rfc-btn danger rfc-row-del" data-row="${attr(row.id)}"
                    title="刪這一列研究">刪這列</button>` : ''}
        </div>`).join('')}`).join('');

    container.innerHTML = `
        <div class="rfc-top">
            <div class="rfc-video">
                <div class="rfc-embed">${_embedHtml(ref)}</div>
                <div class="rfc-row"><label>連結</label><div class="v">
                    <input class="rfc-in" data-kind="field" data-key="url" placeholder="https://…">
                    <div class="rfc-url">${safeUrl(ref.url)
                        ? `<a href="${attr(ref.url)}" target="_blank" rel="noopener" style="color:var(--rfc-accent);">原始連結 ↗</a>`
                        : ''}</div>
                </div></div>
            </div>
            <div class="rfc-meta">
                <div class="rfc-card">
                    <h4>基本</h4>
                    ${field('title', '標題', '影片名稱')}
                    ${field('note', '一句話心得', '這支片好在哪？（例：以具體物件帶出概念，訊息明確簡潔）', true)}
                    ${field('description', '說明', '背景、客戶、脈絡…', true)}
                    ${can.curated === false ? '' : `
                    <div class="rfc-row"><label>建檔</label><div class="v">
                        <label class="rfc-cb"><input type="checkbox" data-kind="curated"> 建檔完成（研究已寫完）</label>
                    </div></div>`}
                </div>
                <div class="rfc-card">
                    <h4>分類</h4>
                    ${facetBlock}
                    ${can.facets === false ? '<div class="rfc-hint">分類由內部維護，共編連結不可修改。</div>' : ''}
                </div>
            </div>
        </div>
        <div class="rfc-card">
            <h4>研究</h4>
            <div class="rfc-grid" id="rfc-grid">
                ${RESEARCH_COLS.map(c => `<div class="rfc-gh"><div class="t">${esc(c.label)}</div><div class="h">${esc(c.hint)}</div></div>`).join('')}
                ${researchRows}
            </div>
            <div class="rfc-rowbar">
                <button class="rfc-btn" id="rfc-add-row">＋ 加一列</button>
                <span class="rfc-hint">四欄同時看：這支片為誰解決什麼、用什麼概念、拿什麼素材、哪個橋段最有效。</span>
            </div>
        </div>
        ${ref.links ? `
        <div class="rfc-card rfc-links">
            <h4>被引用（${ref.links.length}）</h4>
            ${ref.links.length ? ref.links.map(l => `
                <div class="rfc-link-row">
                    <a href="/proposal-plan.html?pid=${encodeURIComponent(l.target_id)}" target="_blank" rel="noopener">${esc(l.title || l.target_id)}</a>
                    <span class="st">${esc(l.status || '')}</span>
                </div>`).join('')
            : '<div class="rfc-hint">還沒有任何提案引用這支片。</div>'}
        </div>` : ''}
        <div class="rfc-save" id="rfc-save"></div>`;

    // 值一律走 DOM property（不進模板字串 — XSS 防線）
    container.querySelectorAll('[data-kind="field"]').forEach(el => {
        el.value = ref[el.dataset.key] || '';
    });
    (ref.research?.rows || []).forEach(row => {
        RESEARCH_COLS.forEach(c => {
            const el = container.querySelector(`textarea[data-row="${CSS.escape(row.id)}"][data-col="${c.key}"]`);
            if (el) el.value = (row.cells?.[c.key]?.answer) || '';
        });
    });
    const cb = container.querySelector('[data-kind="curated"]');
    if (cb) cb.checked = !!ref.curated;
    container.querySelectorAll('textarea.rfc-ta').forEach(ta => {
        _grow(ta);
        ta.addEventListener('input', () => _grow(ta));
    });
    _paintChips(container, ref, can);
    _wire(container, ctx);
}

function _grow(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }

// ── 分類 chips ────────────────────────────────────────────
function _facetValues(ref, key) {
    const v = ref.facets?.[key];
    if (Array.isArray(v)) return v;
    return v ? [v] : [];
}

function _paintChips(container, ref, can, onlyKey) {
    (onlyKey ? FACETS.filter(f => f.key === onlyKey) : FACETS).forEach(f => {
        const host = container.querySelector(`[data-chips="${f.key}"]`);
        if (!host) return;
        const vals = _facetValues(ref, f.key);
        host.innerHTML = vals.length
            ? vals.map(v => can.facets === false
                ? `<span class="rfc-chip ro">${esc(v)}</span>`
                : `<span class="rfc-chip">${esc(v)}<button data-del-facet="${f.key}" data-val="${attr(v)}" title="移除">×</button></span>`).join('')
            : (can.facets === false ? '<span class="rfc-hint">—</span>' : '');
    });
}

// ── 自動儲存（debounce 800ms + blur/change flush + dirty-check + 409 並列）──
function _wire(container, ctx) {
    const { ref, fetcher, endpoints, getGuestName, can } = ctx;
    const saveEl = container.querySelector('#rfc-save');
    const timers = new Map();
    const base = new Map();      // 研究格的樂觀鎖基準
    let msgTimer = null;

    const say = (t, err) => {
        saveEl.textContent = t;
        saveEl.classList.toggle('err', !!err);
        if (!err && t) { clearTimeout(msgTimer); msgTimer = setTimeout(() => { saveEl.textContent = ''; }, 1500); }
    };
    const guest = () => (getGuestName ? (getGuestName() || null) : null);
    const patch = (body) => fetcher(endpoints.patch, { method: 'PATCH', json: { ...body, guest_name: guest() } });
    // 重畫前先清掉待送的 debounce：否則舊 render 的 timer 會對已卸下的節點送舊值，
    // 還把整個舊 closure（含前一份 ref 全文）留到它燒完為止。
    const redraw = async () => {
        timers.forEach(clearTimeout);
        timers.clear();
        const fresh = await ctx.reload();
        container.innerHTML = '';
        renderReference(container, { ...ctx, ref: fresh });
    };

    container.querySelectorAll('[data-kind="field"], [data-kind="research"]').forEach(el => {
        const isRes = el.dataset.kind === 'research';
        if (isRes) {
            const row = (ref.research?.rows || []).find(r => r.id === el.dataset.row);
            base.set(el, row?.cells?.[el.dataset.col]?.updated_at || null);
        }
        el._saved = el.value;
        const save = async () => {
            if (el.value === el._saved) return;
            const want = el.value;
            const body = isRes
                ? { kind: 'research', row_id: el.dataset.row, col: el.dataset.col,
                    value: want, base_updated_at: base.get(el) }
                : { kind: 'field', key: el.dataset.key, value: want };
            try {
                const d = await patch(body);
                el._saved = want;
                if (isRes) base.set(el, d.updated_at);
                el.parentElement.querySelector('.rfc-conflict')?.remove();
                say('已儲存 ✓');
                if (el.dataset.key === 'url') await redraw();   // provider 變了，播放器要換
            } catch (e) {
                if (e.status === 409 && e.detail?.reason === 'conflict') { _conflict(el, e.detail, save, base); return; }
                say('儲存失敗：' + (e.message || e), true);
            }
        };
        const queue = () => { clearTimeout(timers.get(el)); timers.set(el, setTimeout(save, 800)); };
        el.addEventListener('input', queue);
        el.addEventListener('change', () => { clearTimeout(timers.get(el)); save(); });
        el.addEventListener('blur', () => { clearTimeout(timers.get(el)); save(); });
    });

    container.querySelector('[data-kind="curated"]')?.addEventListener('change', async (e) => {
        try { await patch({ kind: 'field', key: 'curated', value: e.target.checked }); say('已儲存 ✓'); }
        catch (err) { e.target.checked = !e.target.checked; say('儲存失敗：' + (err.message || err), true); }
    });

    _wireFacets(container, ctx, { patch, say });

    // 研究加列 / 刪列（刪列的「至少留一列」規則守在後端）
    container.querySelector('#rfc-add-row')?.addEventListener('click', async () => {
        try { await fetcher(endpoints.rows, { method: 'POST' }); await redraw(); }
        catch (e) { say('加列失敗：' + (e.message || e), true); }
    });
    container.querySelectorAll('.rfc-row-del').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('刪掉這一列研究？')) return;
        try { await fetcher(endpoints.row(btn.dataset.row), { method: 'DELETE' }); await redraw(); }
        catch (e) { say('刪列失敗：' + (e.message || e), true); }
    }));
}

// ── 分類 chips 的加/刪（chips 由 innerHTML 重畫 → 刪鈕用事件委派，不逐顆綁）──
function _wireFacets(container, ctx, { patch, say }) {
    const { ref, can } = ctx;
    const saveFacet = async (key, vals) => {
        const f = FACETS.find(x => x.key === key);
        const value = f.single ? (vals[0] || '') : vals;
        try {
            await patch({ kind: 'facet', key, value });
            ref.facets = { ...(ref.facets || {}), [key]: value };
            _paintChips(container, ref, can, key);      // 只重畫動到的那一族
            say('已儲存 ✓');
        } catch (e) { say('儲存失敗：' + (e.message || e), true); }
    };

    container.querySelectorAll('[data-facet-in]').forEach(inp => {
        const key = inp.dataset.facetIn;
        const f = FACETS.find(x => x.key === key);
        const commit = () => {
            const v = inp.value.trim();
            if (!v) return;
            const cur = _facetValues(ref, key);
            if (!f.single && cur.includes(v)) { inp.value = ''; return; }
            inp.value = '';
            saveFacet(key, f.single ? [v] : [...cur, v]);
        };
        inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
        inp.addEventListener('change', commit);   // datalist 點選也算
        inp.addEventListener('blur', commit);
    });
    container.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-del-facet]');
        if (!btn) return;
        const key = btn.dataset.delFacet;
        saveFacet(key, _facetValues(ref, key).filter(v => v !== btn.dataset.val));
    });
}

function _conflict(el, detail, save, base) {
    el.parentElement.querySelector('.rfc-conflict')?.remove();
    const box = document.createElement('div');
    box.className = 'rfc-conflict';
    box.innerHTML = `
        <div class="ct">⚠ 這格已被 ${esc(detail.updated_by || '其他人')} 更新</div>
        <div>對方的版本：</div><pre></pre>
        <button class="rfc-btn" data-act="mine">保留我的</button>
        <button class="rfc-btn" data-act="theirs">改用對方的</button>`;
    box.querySelector('pre').textContent = detail.server_answer || '（空白）';
    el.parentElement.appendChild(box);
    box.querySelector('[data-act="mine"]').addEventListener('click', () => {
        base.set(el, detail.updated_at);
        box.remove(); save();
    });
    box.querySelector('[data-act="theirs"]').addEventListener('click', () => {
        el.value = detail.server_answer || '';
        el._saved = el.value;
        base.set(el, detail.updated_at);
        box.remove(); _grow(el);
    });
}
