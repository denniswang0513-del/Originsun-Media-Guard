/**
 * references.js — 片庫 tab（參考影片庫 v2 的 SPA 入口）
 * ---
 * 總覽卡片牆（搜尋 / 分類篩選 / 建檔狀態 / 只看沒被引用的 / CSV 匯入）
 * → 點卡進單片研究頁，內容直接掛 `tabs/proposals/reference-page.js` 元件
 * （元件的預設膚色就是深色，SPA 不用覆寫；官網白底頁才掛 html.ref-theme-light）。
 *
 * 與 /reference.html 獨立頁的分工：同一組 API、同一個元件，差別只有外框與登入方式。
 */

import { tfetch } from '../proposals/prop-fetch.js';
import { refPills } from '../proposals/ref-pills.js';

const API = '/api/v1/references';
const FACET_LABELS = {
    category: '類別', brand: '品牌', studio: '製作單位', paragon: '典範',
    technique: '技巧', emotion: '情感取向', keyword: '關鍵字', study: '研究用途',
};

let _facetOptions = {};
let _inited = false;

const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };
const attr = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');

const STYLE_ID = 'reft-style';
const CSS = `
.ref-tab .reft-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.ref-tab .reft-head h2 { margin: 0; font-size: 17px; }
.ref-tab .reft-hint { font-size: 11.5px; color: #8b8b8b; }
.ref-tab .reft-link { font-size: 11.5px; color: #93c5fd; text-decoration: none; }
.ref-tab .reft-link:hover { text-decoration: underline; }
.ref-tab .reft-bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.ref-tab .reft-bar input[type="search"], .ref-tab .reft-bar select {
  background: #1f1f1f; border: 1px solid #3a3a3a; border-radius: 3px; color: #e8e8e8;
  font-size: 12.5px; padding: 6px 8px; outline: none; }
.ref-tab .reft-bar input[type="search"] { min-width: 220px; }
.ref-tab .reft-bar input:focus, .ref-tab .reft-bar select:focus { border-color: #3b82f6; }
.ref-tab .reft-check { display: flex; gap: 5px; align-items: center; font-size: 12px; color: #8b8b8b; cursor: pointer; }
.ref-tab .reft-btn { background: #1f1f1f; border: 1px solid #3a3a3a; border-radius: 3px; color: #e8e8e8;
  font-size: 12px; padding: 6px 12px; cursor: pointer; }
.ref-tab .reft-btn:hover { border-color: #3b82f6; color: #93c5fd; }
.ref-tab .reft-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 14px; }
.ref-tab .reft-card { border: 1px solid #3a3a3a; border-radius: 3px; overflow: hidden; cursor: pointer;
  background: #232323; display: flex; flex-direction: column; transition: border-color .15s, transform .1s; }
.ref-tab .reft-card:hover { border-color: #3b82f6; transform: translateY(-1px); }
.ref-tab .reft-card .thumb { aspect-ratio: 16/9; background: #111 center/cover no-repeat;
  display: flex; align-items: center; justify-content: center; color: #666; font-size: 11px; }
.ref-tab .reft-card .body { padding: 9px 11px 11px; display: flex; flex-direction: column; gap: 6px; flex: 1; }
.ref-tab .reft-card .t { font-size: 13px; line-height: 1.5; }
.ref-tab .reft-card .n { font-size: 11.5px; color: #8b8b8b; line-height: 1.6;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ref-tab .reft-card .foot { display: flex; gap: 5px; flex-wrap: wrap; margin-top: auto; }
.ref-tab .pill { font-size: 10.5px; padding: 2px 7px; border-radius: 2px; background: #2f2f2f; color: #9b9b9b; }
.ref-tab .pill.ok { background: #14371f; color: #7ee2a8; }
.ref-tab .pill.used { background: #1e2a4d; color: #93c5fd; }
.ref-tab .pill.warn { background: #3d2f14; color: #f5c064; }
.ref-tab .pill.err { background: #401f1f; color: #f5a2a2; }
.ref-tab .reft-empty { color: #8b8b8b; font-size: 13px; padding: 30px 6px; text-align: center; line-height: 1.9; }
.ref-tab .reft-detail-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.ref-tab .reft-detail-head h3 { margin: 0; font-size: 15px; font-weight: 500; flex: 1; min-width: 180px; }
`;

function _injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = CSS;
    document.head.appendChild(st);
}

function _card(r) {
    const thumb = /^https?:\/\/|^\//.test(r.thumb_url || '')
        ? `<div class="thumb" style="background-image:url('${attr(r.thumb_url)}')"></div>`
        : '<div class="thumb">無封面</div>';
    return `<div class="reft-card" data-rid="${attr(r.id)}">
        ${thumb}
        <div class="body">
            <div class="t">${esc(r.title || r.url)}</div>
            ${r.note ? `<div class="n">${esc(r.note)}</div>` : ''}
            <div class="foot">${refPills(r)}</div>
        </div>
    </div>`;
}

async function _loadList() {
    const host = $('reft-grid');
    const p = new URLSearchParams();
    if ($('reft-q').value.trim()) p.set('q', $('reft-q').value.trim());
    if ($('reft-curated').value) p.set('curated', $('reft-curated').value);
    if ($('reft-unused').checked) p.set('unused', '1');
    if ($('reft-facet-key').value && $('reft-facet-val').value) {
        p.set('facet', $('reft-facet-key').value);
        p.set('value', $('reft-facet-val').value);
    }
    try {
        const d = await tfetch(API + (p.toString() ? '?' + p.toString() : ''));
        const refs = d.references || [];
        $('reft-count').textContent = `${d.total ?? refs.length} 支`;
        host.innerHTML = refs.length ? refs.map(_card).join('')
            : '<div class="reft-empty">沒有符合的參考影片。<br>片子從提案或專案的「參考影片」加入，也可以用「匯入 CSV」批次帶進來。</div>';
        host.querySelectorAll('.reft-card').forEach(c =>
            c.addEventListener('click', () => _openDetail(c.dataset.rid)));
    } catch (e) {
        host.innerHTML = `<div class="reft-empty" style="color:#fca5a5;">載入失敗：${esc(e.message || e)}</div>`;
    }
}

async function _openDetail(rid) {
    const host = $('reft-host');
    $('reft-grid').style.display = 'none';
    $('reft-bar').style.display = 'none';
    $('reft-detail').style.display = '';
    $('reft-standalone').href = `/reference.html?id=${encodeURIComponent(rid)}`;
    host.innerHTML = '<div class="reft-empty">載入中…</div>';
    try {
        const [{ reference: ref }, { renderReference }] = await Promise.all([
            tfetch(`${API}/${encodeURIComponent(rid)}`),
            import('../proposals/reference-page.js'),
        ]);
        $('reft-title').textContent = ref.title || ref.url || '參考影片';
        host.innerHTML = '';
        renderReference(host, { ref, fetcher: tfetch, facetOptions: _facetOptions });
    } catch (e) {
        host.innerHTML = `<div class="reft-empty" style="color:#fca5a5;">載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _backToList() {
    $('reft-detail').style.display = 'none';
    $('reft-host').innerHTML = '';          // 卸掉元件（它的共編輪詢靠 isConnected 自停）
    $('reft-grid').style.display = '';
    $('reft-bar').style.display = '';
    _loadList();                             // 回清單時重抓：剛剛可能改了標題/分類/建檔
}

function _wireBar() {
    const fk = $('reft-facet-key');
    fk.innerHTML = '<option value="">（全部分類族）</option>' +
        Object.keys(FACET_LABELS).map(k => `<option value="${k}">${FACET_LABELS[k]}</option>`).join('');
    let t = null;
    $('reft-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(_loadList, 400); });
    $('reft-curated').addEventListener('change', _loadList);
    $('reft-unused').addEventListener('change', _loadList);
    fk.addEventListener('change', () => {
        const vals = _facetOptions[fk.value] || [];
        const fv = $('reft-facet-val');
        if (!fk.value || !vals.length) { fv.style.display = 'none'; fv.innerHTML = ''; _loadList(); return; }
        fv.innerHTML = vals.map(v => `<option value="${attr(v)}">${esc(v)}</option>`).join('');
        fv.style.display = '';
        _loadList();
    });
    $('reft-facet-val').addEventListener('change', _loadList);
    $('reft-back').addEventListener('click', _backToList);

    // CSV 匯入（Notion 匯出檔）：只新增不覆蓋
    $('reft-import').addEventListener('click', () => $('reft-import-file').click());
    $('reft-import-file').addEventListener('change', async (e) => {
        const f = e.target.files[0];
        e.target.value = '';
        if (!f) return;
        $('reft-count').textContent = '匯入中…';
        try {
            const fd = new FormData();
            fd.append('file', f);
            const d = await tfetch(`${API}/import_csv`, { method: 'POST', body: fd });
            alert(`匯入完成\n\n新增 ${d.imported} 支\n已存在跳過 ${d.skipped} 支` +
                  (d.invalid ? `\n網址不合法略過 ${d.invalid} 列` : '') +
                  '\n\n（跳過的不會被覆蓋：系統裡寫的研究與分類優先，Notion 後來的改動不會同步進來）');
        } catch (err) {
            alert('匯入失敗：' + ((err && err.message) || err));
        }
        _loadList();
    });
}

export async function initReferencesTab() {
    _injectStyle();
    if (!_inited) {
        _inited = true;
        _wireBar();
        try { _facetOptions = (await tfetch(`${API}/facet_options`)).options || {}; } catch (_) { _facetOptions = {}; }
        // 封存管理卡（admin；window._accessLevel 由 auth-state 掛，後端仍會再閘）
        if ((window._accessLevel || 0) >= 3) {
            try {
                const { mountArchiveAdmin } = await import('../proposals/archive-admin.js');
                const host = document.createElement('div');
                document.querySelector('#reft-bar').after(host);
                mountArchiveAdmin(host);
            } catch (_) { /* 不擋片庫 */ }
        }
    }
    // 每次切回這個 tab 都重抓清單（別人可能剛加了片）
    if ($('reft-detail').style.display === 'none') _loadList();
}
