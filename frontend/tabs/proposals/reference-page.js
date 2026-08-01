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
import { renderShapes } from './annotate.js';
import { autosaveDelegated, syncBaseline } from '../../js/shared/autosave.js';
import { ARCHIVE_LABEL } from './ref-pills.js';

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
.rfc .rfc-arch { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  font-size: 11.5px; color: var(--rfc-sub); padding: 6px 6px 0; }
.rfc .rfc-arch .badge { border: 1px solid var(--rfc-line); border-radius: 2px; padding: 1px 7px; }
.rfc .rfc-arch .badge.ok { color: #7ee2a8; border-color: #2c5a3c; }
.rfc .rfc-arch .badge.err { color: #f5a2a2; border-color: #6b2f2f; }
.rfc .rfc-arch label { display: flex; gap: 4px; align-items: center; cursor: pointer; }
.rfc .rfc-localnote { font-size: 11px; color: var(--rfc-sub); padding: 4px 6px 0; }
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
.rfc .rfc-link-row .tt { font-size: 10.5px; color: var(--rfc-sub); border: 1px solid var(--rfc-line);
  border-radius: 2px; padding: 1px 5px; flex: none; }
.rfc .rfc-link-row .rfc-btn { margin-left: auto; padding: 2px 8px; font-size: 11px; }
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
.rfc .rfc-shots { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.rfc .rfc-shot { border: 1px solid var(--rfc-line); border-radius: 2px; background: var(--rfc-cell); }
.rfc .rfc-shot .pic { position: relative; line-height: 0; cursor: zoom-in; background: #000; }
.rfc .rfc-shot .pic img { width: 100%; display: block; }
.rfc .rfc-shot .pic svg { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.rfc .rfc-shot .pic .mk { position: absolute; right: 6px; top: 6px; font-size: 10.5px;
  background: rgba(0,0,0,.6); color: #fff; padding: 1px 6px; border-radius: 2px; }
.rfc .rfc-shot .sb { display: flex; gap: 4px; align-items: center; padding: 5px 6px; }
.rfc .rfc-shot .sb input.tcin { width: 62px; flex: none; }
.rfc .rfc-shot .sb .sp { flex: 1; min-width: 0; }
.rfc .rfc-shot .rfc-btn { padding: 2px 6px; font-size: 11px; white-space: nowrap; }
.rfc .rfc-shot .who { font-size: 10.5px; color: var(--rfc-sub); padding: 0 8px 6px; }
.rfc .rfc-shotbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
.rfc .rfc-drop { border: 1px dashed var(--rfc-line); border-radius: 2px; padding: 14px;
  text-align: center; font-size: 11.5px; color: var(--rfc-sub); line-height: 1.7; }
.rfc .rfc-drop.hot { border-color: var(--rfc-accent); color: var(--rfc-accent); }
@media (max-width: 620px) { .rfc .rfc-shots { grid-template-columns: 1fr; } }
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
    // 一律用後端 parse_video_url 產的 embed_url —— 自己拿 video_id 拼會漏掉
    // Vimeo 未公開影片的 ?h= 私密雜湊，播放器會直接拒播
    const embed = safeUrl(ref.embed_url);
    if (embed) {
        return `<iframe src="${attr(embed)}" title="影片"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
            referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>`;
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
    const ac = new AbortController();
    const ctx = {
        ...opts, can, signal: ac.signal, abort: () => ac.abort(),
        fetcher: opts.fetcher || tfetch,
        endpoints: {
            patch: base,
            rows: `${base}/research/rows`,
            row: (id) => `${base}/research/rows/${encodeURIComponent(id)}`,
            // guest 參數收進產生函式：公開路徑要帶署名與不可見識別，call site 不碰 query string
            shots: (guest) => `${base}/shots${_guestQS(shared, guest)}`,
            shot: (id, guest) => `${base}/shots/${encodeURIComponent(id)}${_guestQS(shared, guest)}`,
            shotsReorder: `${base}/shots/reorder`,
        },
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
                <div class="rfc-arch" data-arch-bar></div>
                <div class="rfc-row"><label>連結</label><div class="v">
                    <input class="rfc-in" data-kind="field" data-key="url" placeholder="https://…">
                    ${(can.facets === false || ref.thumb_url || ref.provider === 'youtube') ? '' :
                      '<button class="rfc-btn" id="rfc-cover" style="margin:4px 0 0 6px;">抓封面</button>'}
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
                        <label class="rfc-cb"><input type="checkbox" data-kind="curated"> 研究完成（研究四欄已寫完）</label>
                    </div></div>`}
                </div>
                <div class="rfc-card">
                    <h4>分類</h4>
                    ${facetBlock}
                    ${can.facets === false ? '' : `
                    <div class="rfc-rowbar">
                        <button class="rfc-btn" id="rfc-ai">✨ AI 建議分類</button>
                        <span class="rfc-hint" id="rfc-ai-msg"></span>
                    </div>`}
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
                <button class="rfc-btn" id="rfc-md" title="把這支片的研究複製成 Markdown">複製 Markdown</button>
                <span class="rfc-hint">四欄同時看：這支片為誰解決什麼、用什麼概念、拿什麼素材、哪個橋段最有效。</span>
            </div>
        </div>
        <div class="rfc-card">
            <h4 id="rfc-shots-h">截圖（${(ref.shots || []).length}）</h4>
            <div class="rfc-shots" id="rfc-shots"></div>
            <div class="rfc-shotbar">
                <button class="rfc-btn" id="rfc-shot-pick">＋ 上傳截圖</button>
                <input type="file" id="rfc-shot-file" accept="image/*" multiple style="display:none;">
                <span class="rfc-hint">或直接 Ctrl+V 貼上剪貼簿的截圖；點圖可加框／箭頭／文字標示。</span>
            </div>
            <div class="rfc-drop" id="rfc-drop">把圖片拖進來也可以</div>
        </div>
        ${ref.links ? `
        <div class="rfc-card rfc-links">
            <h4>被引用（${ref.links.length}）</h4>
            ${ref.links.length ? ref.links.map(l => `
                <div class="rfc-link-row" data-link="${attr(l.link_id)}">
                    <span class="tt">${l.target_type === 'crm_project' ? '專案' : '提案'}</span>
                    <a href="${l.target_type === 'crm_project' ? '/#crm-projects' : '/proposal-plan.html?pid=' + encodeURIComponent(l.target_id)}"
                       target="_blank" rel="noopener">${esc(l.title || l.target_id)}</a>
                    <span class="st">${esc(l.status || '')}</span>
                    ${l.note ? `<span class="st">— ${esc(l.note)}</span>` : ''}
                    <button class="rfc-btn danger rfc-link-del" data-link="${attr(l.link_id)}">解除</button>
                </div>`).join('')
            : '<div class="rfc-hint">還沒有任何提案或專案引用這支片。</div>'}
            <div class="rfc-hint">片庫共用：解除只拿掉那一邊的引用，片子仍留在庫裡。</div>
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
    _paintShots(container, ctx);
    _wire(container, ctx);
}

// 公開共編的身分：署名（顯示用）+ 瀏覽器產生的不可見 key（「只刪自己貼的」憑證）
function _guestKey() {
    let k = localStorage.getItem('plan_guest_key');
    if (!k) {
        k = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random())).replace(/-/g, '');
        localStorage.setItem('plan_guest_key', k);
    }
    return k;
}

function _guestQS(shared, guest) {
    if (!shared) return '';
    const p = new URLSearchParams({ guest_key: _guestKey() });
    if (guest) p.set('guest_name', guest);
    return '?' + p.toString();
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
        ctx.abort?.();                 // 收掉掛在 document 上的 paste 監聽（連同它抓的舊 ctx）
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
            try {
                const d = await patch(isRes
                    ? { kind: 'research', row_id: el.dataset.row, col: el.dataset.col,
                        value: want, base_updated_at: base.get(el) }
                    : { kind: 'field', key: el.dataset.key, value: want });
                el._saved = want;
                // 本地 ref 一起更新 —— 匯出 Markdown / 重畫都讀它，只寫後端會拿到舊值
                if (isRes) {
                    base.set(el, d.updated_at);
                    const row = (ref.research?.rows || []).find(r => r.id === el.dataset.row);
                    if (row) row.cells[el.dataset.col] = { answer: want, updated_at: d.updated_at, updated_by: '' };
                } else {
                    ref[el.dataset.key] = want;
                }
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

    const saveFacet = _wireFacets(container, ctx, { patch, say });
    _wireAiFacets(container, ctx, { say, saveFacet });
    _wireCover(container, ctx, { say });
    _wireMarkdown(container, ctx, { say });
    container.querySelectorAll('.rfc-link-del').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('解除這筆引用？（片子仍留在片庫）')) return;
        try {
            await fetcher(`${API}/links/${encodeURIComponent(btn.dataset.link)}`, { method: 'DELETE' });
            await redraw();
        } catch (e) { say('解除失敗：' + (e.message || e), true); }
    }));
    _wireShotUploads(container, ctx, say);
    _wireArchive(container, ctx, say);
    _wireShotItems(container, ctx, say);

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

// ── 截圖牆（兩欄，對齊 Notion 的 Pic 01 / Pic 02）────────────
function _paintShots(container, ctx) {
    const host = container.querySelector('#rfc-shots');
    if (!host) return;
    const shots = ctx.ref.shots || [];
    const head = container.querySelector('#rfc-shots-h');
    if (head) head.textContent = `截圖（${shots.length}）`;
    host.innerHTML = shots.length ? shots.map((sh, i) => `
        <div class="rfc-shot" data-sid="${attr(sh.id)}">
            <div class="pic" data-open="${attr(sh.id)}">
                <img src="${attr(sh.image_url)}" alt="截圖" loading="lazy">
                <svg></svg>
                ${(sh.annotations?.shapes || []).length ? `<span class="mk">✎ ${sh.annotations.shapes.length}</span>` : ''}
            </div>
            <div class="sb">
                <input class="rfc-in tcin" data-shot="${attr(sh.id)}" data-sfield="timecode" placeholder="0:42">
                <input class="rfc-in sp" data-shot="${attr(sh.id)}" data-sfield="caption" placeholder="這張要看什麼？">
                <button class="rfc-btn" data-shot-up="${attr(sh.id)}" title="往前排"${i === 0 ? ' disabled' : ''}>←</button>
                <button class="rfc-btn" data-shot-down="${attr(sh.id)}" title="往後排"${i === shots.length - 1 ? ' disabled' : ''}>→</button>
                <button class="rfc-btn danger" data-shot-del="${attr(sh.id)}" title="刪掉這張">刪</button>
            </div>
            ${sh.created_by ? `<div class="who">${esc(sh.created_by)}</div>` : ''}
        </div>`).join('')
        : '<div class="rfc-hint">還沒有截圖。看片時遇到好畫面就截圖貼上來，再框出重點。</div>';

    // 值走 property；標示疊圖用同一份 renderShapes（與編輯器共用繪圖規則）。
    // 卡片順序就是 shots 順序 → 用 children 對位，不用逐張 querySelector 掃 DOM。
    [...host.children].forEach((card, i) => {
        const sh = shots[i];
        if (!sh) return;
        card.querySelectorAll('[data-sfield]').forEach(inp => {
            inp.value = sh[inp.dataset.sfield] || '';
        });
        const svg = card.querySelector('svg');
        if (svg) renderShapes(svg, sh.annotations);
    });
    syncBaseline(host, '[data-sfield]');       // 新節點的 dirty-check 基準＝當下值
}

// 上傳入口（工具列 / 拖放 / 貼上）—— **只在整頁 render 時綁一次**。
// 這些節點不會被 _paintShots 換掉，若隨重畫重綁，第 k 次重畫後選一次檔會送 k+1 份
// （同一張圖產生 k+1 列截圖）。實測踩過，所以一次性與每次重畫的綁定分成兩個函式。
function _wireShotUploads(container, ctx, say) {
    const { ref, fetcher, endpoints, getGuestName } = ctx;
    if (!container.querySelector('#rfc-shots')) return;
    const guest = () => (getGuestName ? (getGuestName() || '') : '');

    async function upload(files) {
        const list = [...files].filter(f => f.type.startsWith('image/'));
        if (!list.length) return;
        say(`上傳 ${list.length} 張…`);
        const fd = new FormData();
        list.forEach(f => fd.append('files', f));        // 端點吃多檔：一次 request
        try {
            const d = await fetcher(endpoints.shots(guest()), { method: 'POST', body: fd });
            ref.shots = [...(ref.shots || []), ...(d.shots || [])];
            _paintShots(container, ctx);                  // 用回傳值，不重抓整份 reference
            say('已儲存 ✓');
        } catch (e) { say('上傳失敗：' + (e.message || e), true); }
    }

    container.querySelector('#rfc-shot-pick').addEventListener('click',
        () => container.querySelector('#rfc-shot-file').click());
    container.querySelector('#rfc-shot-file').addEventListener('change', (e) => {
        upload(e.target.files);
        e.target.value = '';
    });
    // 貼上：整頁監聽（焦點在輸入框時不搶）。ctx.signal 讓重畫時一起收掉舊 closure。
    document.addEventListener('paste', (e) => {
        if (!container.isConnected) return;
        const t = e.target;
        if (t && (t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && t.type !== 'file'))) return;
        const files = [...(e.clipboardData?.files || [])];
        if (files.length) { e.preventDefault(); upload(files); }
    }, ctx.signal ? { signal: ctx.signal } : false);

    const drop = container.querySelector('#rfc-drop');
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, (e) => {
        e.preventDefault(); drop.classList.add('hot');
    }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove('hot')));
    drop.addEventListener('drop', (e) => { e.preventDefault(); upload(e.dataTransfer?.files || []); });
}

// 牆內每張卡的操作（標示 / 排序 / 刪除）—— 節點每次重畫都換新，所以走事件委派綁一次。
function _wireShotItems(container, ctx, say) {
    const { ref, fetcher, endpoints, getGuestName } = ctx;
    const host = container.querySelector('#rfc-shots');
    if (!host) return;
    const guest = () => (getGuestName ? (getGuestName() || '') : '');
    const shotOf = (id) => (ref.shots || []).find(x => x.id === id);

    // 說明 / 時間碼走共用 autosave 的委派版（牆每次重畫都換新節點：逐顆綁會漏、
    // 重畫時重綁又會疊加 —— 兩個坑都踩過，機制收在 shared/autosave.js）
    autosaveDelegated(host, '[data-sfield]', async (value, inp) => {
        await fetcher(endpoints.shot(inp.dataset.shot), { method: 'PATCH',
            json: { [inp.dataset.sfield]: value } });
        const sh = shotOf(inp.dataset.shot);
        if (sh) sh[inp.dataset.sfield] = value;
    }, { onOk: () => say('已儲存 ✓'), onError: (e) => say('儲存失敗：' + (e.message || e), true) });

    host.addEventListener('click', async (e) => {
        const pic = e.target.closest('[data-open]');
        if (pic) {
            const sh = shotOf(pic.dataset.open);
            if (!sh) return;
            const { openAnnotator } = await import('./annotate.js');
            const saved = await openAnnotator({
                imageUrl: sh.image_url, annotations: sh.annotations,
                title: [sh.timecode, sh.caption].filter(Boolean).join('　') || '',
                onSave: async (ann) => {
                    const d = await fetcher(endpoints.shot(sh.id), { method: 'PATCH', json: { annotations: ann } });
                    Object.assign(sh, d.shot || { annotations: ann });
                },
            });
            if (saved) _paintShots(container, ctx);
            return;
        }
        const mv = e.target.closest('[data-shot-up], [data-shot-down]');
        if (mv) {
            const sid = mv.dataset.shotUp || mv.dataset.shotDown;
            const delta = mv.dataset.shotUp ? -1 : 1;
            const ids = (ref.shots || []).map(x => x.id);
            const i = ids.indexOf(sid), j = i + delta;
            if (i < 0 || j < 0 || j >= ids.length) return;
            [ids[i], ids[j]] = [ids[j], ids[i]];
            try {
                await fetcher(endpoints.shotsReorder, { method: 'POST', json: { ids } });
                ref.shots = ids.map(shotOf).filter(Boolean);   // 順序在本地就算得出來
                _paintShots(container, ctx);
                say('已儲存 ✓');
            } catch (err) { say('排序失敗：' + (err.message || err), true); }
            return;
        }
        const del = e.target.closest('[data-shot-del]');
        if (del) {
            if (!confirm('刪掉這張截圖？（連同標示）')) return;
            try {
                await fetcher(endpoints.shot(del.dataset.shotDel, guest()), { method: 'DELETE' });
                ref.shots = (ref.shots || []).filter(x => x.id !== del.dataset.shotDel);
                _paintShots(container, ctx);
                say('已儲存 ✓');
            } catch (err) { say('刪除失敗：' + (err.message || err), true); }
        }
    });
}

// ── 階段 4：AI 建議分類 / 抓封面 / 匯出 Markdown ──────────────
function _wireAiFacets(container, ctx, { say, saveFacet }) {
    const { ref, fetcher } = ctx;
    container.querySelector('#rfc-ai')?.addEventListener('click', async (e) => {
        const msg = container.querySelector('#rfc-ai-msg');
        e.target.disabled = true;
        msg.textContent = 'AI 讀這支片的資訊中…（十幾秒）';
        try {
            const d = await fetcher(`${API}/${encodeURIComponent(ref.id)}/ai_facets`, { method: 'POST' });
            const sug = d.suggestions || {};
            const picks = Object.entries(sug).filter(([, v]) => (v || []).length);
            if (!picks.length) { msg.textContent = 'AI 沒有給出有把握的建議'; return; }
            // 只回建議不自動寫入：分類是人的判斷，AI 只負責先想到
            const preview = picks.map(([k, v]) =>
                `${(FACETS.find(f => f.key === k) || {}).label || k}：${v.join('、')}`).join('\n');
            if (!confirm(`AI 建議加上這些分類：\n\n${preview}\n\n要套用嗎？（現有分類會保留）`)) {
                msg.textContent = '未套用';
                return;
            }
            for (const [k, v] of picks) {
                const f = FACETS.find(x => x.key === k);
                if (!f || f.single) continue;
                // 走 _wireFacets 的同一條寫入路徑，別開第二條分類寫入
                await saveFacet(k, [...new Set([..._facetValues(ref, k), ...v])]);
            }
            msg.textContent = '已套用（可再手動增刪）';
        } catch (err) {
            msg.textContent = 'AI 建議失敗：' + (err.message || err);
        } finally { e.target.disabled = false; }
    });
}

function _wireCover(container, ctx, { say }) {
    const { ref, fetcher } = ctx;
    container.querySelector('#rfc-cover')?.addEventListener('click', async (e) => {
        e.target.textContent = '抓取中…';
        try {
            const d = await fetcher(`${API}/${encodeURIComponent(ref.id)}/cover_fetch`, { method: 'POST' });
            ref.thumb_url = d.thumb_url;
            say('已儲存 ✓');
            e.target.remove();
        } catch (err) {
            say('抓封面失敗：' + (err.message || err), true);
            e.target.textContent = '抓封面';
        }
    });
}

function _wireMarkdown(container, ctx, { say }) {
    const { ref } = ctx;
    container.querySelector('#rfc-md')?.addEventListener('click', async () => {
        const md = _toMarkdown(ref);
        try { await navigator.clipboard.writeText(md); say('已複製 Markdown ✓'); }
        catch { window.prompt('複製這段 Markdown：', md); }
    });
}

// 研究內容匯出 Markdown（貼進提案簡報/會議記錄用）
function _toMarkdown(ref) {
    const L = [`# ${ref.title || ref.url}`, '', ref.url, ''];
    if (ref.note) L.push(`> ${ref.note}`, '');
    if (ref.description) L.push(ref.description, '');
    const facets = FACETS
        .map(f => [f.label, _facetValues(ref, f.key).join('、')])
        .filter(([, v]) => v);
    if (facets.length) {
        L.push(...facets.map(([k, v]) => `- **${k}**：${v}`), '');
    }
    L.push('## 研究', '', '| ' + RESEARCH_COLS.map(c => c.label).join(' | ') + ' |',
           '|' + RESEARCH_COLS.map(() => '---').join('|') + '|');
    (ref.research?.rows || []).forEach(row => {
        L.push('| ' + RESEARCH_COLS.map(c =>
            (row.cells?.[c.key]?.answer || '').replace(/\n/g, '<br>').replace(/\|/g, '\\|')).join(' | ') + ' |');
    });
    const shots = ref.shots || [];
    if (shots.length) {
        L.push('', '## 截圖', '');
        shots.forEach(s => L.push(`- ${s.timecode ? `\`${s.timecode}\` ` : ''}${s.caption || '(無說明)'} — ${location.origin}${s.image_url}`));
    }
    return L.join('\n');
}

// ── 影片封存（「已建檔」）：徽章 + 排除/重試 + 封存檔播放切換 ──────
// 狀態表基底在 ref-pills.js（卡片 pill 與詳情徽章同一正本）；詳情語境較寬，
// 只覆寫兩個要講更清楚的文案。


const ARCH_BADGE = Object.fromEntries(Object.entries(ARCHIVE_LABEL)
    .map(([k, v]) => [k, [v.text, v.cls === 'ok' ? 'ok' : (v.cls === 'err' ? 'err' : '')]]));
ARCH_BADGE.done = ['已建檔 NAS', 'ok'];                       // 詳情頁講清楚存在哪
ARCH_BADGE.unavailable = ['原連結已失效（無封存檔）', 'err'];  // 與「已建檔但連結死」區分


function _wireArchive(container, ctx, say) {
    const { ref, fetcher, can } = ctx;
    const bar = container.querySelector('[data-arch-bar]');
    if (!bar) return;
    if (can.curated === false) { bar.remove(); return; }   // 公開共編不露封存（版權：內部備援）

    const paint = () => {
        const st = ref.archive_status || '';
        const [text, cls] = ARCH_BADGE[st] || ['尚未排入建檔', ''];
        bar.innerHTML = `
            <span class="badge ${cls}">${esc(text)}</span>
            ${ref.archived_at ? `<span>${esc(ref.archived_at.slice(0, 10))} 建檔</span>` : ''}
            ${st === 'done' ? '<button class="rfc-btn" data-arch-play>改用封存檔播放</button>' : ''}
            ${(st === 'retry' || st === 'unavailable' || st === 'excluded' || !st)
                ? '<button class="rfc-btn" data-arch-retry>立即建檔</button>' : ''}
            <label><input type="checkbox" data-arch-excl${st === 'excluded' ? ' checked' : ''}> 排除建檔</label>`;
    };
    paint();

    let showingLocal = false;
    bar.addEventListener('click', async (e) => {
        const play = e.target.closest('[data-arch-play]');
        if (play) {
            const embedBox = container.querySelector('.rfc-embed');
            if (!showingLocal) {
                // <video> 帶不了 Authorization header → 端點收 ?token=（僅此端點，見後端註解）
                const src = `${API}/${encodeURIComponent(ref.id)}/archive_video?token=` +
                            encodeURIComponent(localStorage.getItem('auth_token') || '');
                embedBox.innerHTML = '';
                const v = document.createElement('video');
                v.controls = true;
                v.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;';
                v.src = src;
                embedBox.appendChild(v);
                play.textContent = '改回線上播放';
                let note = container.querySelector('.rfc-localnote');
                if (!note) {
                    note = document.createElement('div');
                    note.className = 'rfc-localnote';
                    embedBox.after(note);
                }
                note.textContent = '播放來源：NAS 封存檔（內部備援；原連結正常時建議用線上版）';
                showingLocal = true;
            } else {
                container.querySelector('.rfc-localnote')?.remove();
                container.querySelector('.rfc-embed').innerHTML = _embedHtml(ref);
                play.textContent = '改用封存檔播放';
                showingLocal = false;
            }
            return;
        }
        const retry = e.target.closest('[data-arch-retry]');
        if (retry) {
            retry.disabled = true;
            try {
                const d = await fetcher(`${API}/${encodeURIComponent(ref.id)}/archive_retry`, { method: 'POST' });
                ref.archive_status = 'pending';
                paint();
                say(d.started ? '已開始建檔（下載需要幾分鐘）' : '已排入建檔佇列 ✓');
            } catch (err) { say('排入失敗：' + (err.message || err), true); retry.disabled = false; }
        }
    });
    bar.addEventListener('change', async (e) => {
        const excl = e.target.closest('[data-arch-excl]');
        if (!excl) return;
        try {
            const d = await fetcher(`${API}/${encodeURIComponent(ref.id)}/archive_exclude`,
                                    { method: 'POST', json: { excluded: excl.checked } });
            ref.archive_status = d.archive_status;
            paint();
            say('已儲存 ✓');
        } catch (err) { say('儲存失敗：' + (err.message || err), true); }
    });

    // 原連結已失效且有封存檔的情境：unavailable 是「下載失敗」不會有檔；
    // 但 link 類（無 embed）而已建檔 → 直接預設用封存檔播
    if (!ref.embed_url && ref.archive_status === 'done') {
        bar.querySelector('[data-arch-play]')?.click();
    }
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
    return saveFacet;      // AI 建議套用共用這條，不要第二條寫入路徑
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
