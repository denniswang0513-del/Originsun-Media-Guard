/**
 * book.js — 知識庫書頁：左欄（標題／狀態／標籤／分頁鈕／動作）＋右欄（分頁內容）。
 * 分頁：討論（chat.js）／結論／筆記（整檔編輯）／骨架（SKILL.md＋速查表）／章節（清單 → 單章）。
 * 編譯：POST compile → 每 3 秒抓 GET /{id} 直到 status 離開 compiling（分頁在背景就跳過那一拍）。
 * 待補（pending）＝先建了書名還沒有檔：這裡用「補上 PDF」取代「讀這本書」，補完 status 變 uploaded。
 *
 * 章節裡的圖（§9.7）：md-lite 只產 `<img data-md-src="assets/pNNN-k.jpg">`（**沒有 src**），
 * 這裡 fetch 成 blob 再填 —— `<img src>` 送不了 Authorization，圖是私有的。
 */
import { mdToHtml } from '../shared/md-lite.js';
import { API, S, PANES, TAGS_MAX, COMPILE_POLL_MS, api, esc, errText, stageText, alive, toast, stopTimers, normBook,
         chapterList, bookTags, parseTags, statusPill, tagsHtml, figureSource } from './ctx.js';
import { authDownload, bearerHeader } from '../shared/utils.js';
import { loadShelf, refreshShelfQuietly } from './shelf.js';
import { chatHtml, scrollChat, loadChat } from './chat.js';
import { extendHtml, loadExtend } from './extend.js';
import { infoHtml, loadShare } from './info.js';
import { galleryHtml, loadGallery } from './gallery.js';

export async function openBook(id, { compile: thenCompile = false } = {}) {
    stopTimers();
    // 每本書自己的分頁狀態全部歸零 —— S.pane 是跨書持久的（站在 A 的延伸分頁回書架再開 B，
    // 進來就是 B 的延伸分頁），少清一個就是把 A 的東西畫在 B 上：A 的延伸清單（評分會打到 B 的 n）、
    // A 還在找資料時的 extendStage（B 就永遠不會 loadExtend）、A 那則「存成結論」的編輯框
    // （2026-09-19 /polish BUG-4）。
    S.chapter = null; S.editing = false; S.editingTags = false; S.chat = []; S.assets = []; S.share = null;
    S.extend = []; S.extendStage = ''; S.extendNote = ''; S.concEdit = null; S.focusEdit = false; S.infoEdit = false;
    try {
        S.book = normBook(await api(`/${encodeURIComponent(id)}`));
    } catch (e) {
        if (!alive()) return;
        toast(errText(e), true);
        if (!S.book) await loadShelf();
        return;
    }
    if (!alive()) return;
    renderBook();
    if (thenCompile) await compile();                       // 剛上傳完、使用者說現在就讀（compile 自己會 watch）
    else if (S.book.status === 'compiling') watchCompile();
    if (!alive() || !S.book) return;
    _loadPaneData(S.pane);                                  // 落地的分頁要抓的東西，跟 switchPane 同一支
    if (S.pane !== 'chat' && S.book.chatting) loadChat();   // chatting＝上一輪還在回，不在討論分頁也接著等
}

/** 切到（或開書時落在）某個分頁要抓什麼：討論第一次進來才抓；延伸沒在找才抓；圖輯沒抓過才抓；分享沒讀過才讀。
 *  🔴 openBook 與 switchPane 都走這裡 —— 只掛在 switchPane 的話，開書時落在延伸／圖輯／資訊分頁就沒人抓。 */
function _loadPaneData(pane) {
    if (pane === 'chat' && !S.chat.length && !S.wait) loadChat();
    if (pane === 'extend' && !S.extendStage) loadExtend();
    if (pane === 'gallery' && !S.assets.length) loadGallery().then(() => { if (S.pane === 'gallery') renderPane(); });
    if (pane === 'info' && !S.share) loadShare();
}

function _tagsRowHtml(b) {
    const tags = bookTags(b);
    if (S.editingTags) {
        // 一顆一顆加（owner 2026-09-19：「標籤我需要的是可以增加很多個」）——
        // 原本是一個逗號分隔的輸入框，塞在窄欄裡很難加到第五個以後。
        return `<div class="kb-tags-edit">
            <div class="chips">${tags.map((t, i) => `<span class="kb-tag on">${esc(t)}
                <button type="button" data-kact="tag-del" data-i="${i}" aria-label="移除 ${esc(t)}">×</button></span>`).join('')}
                ${tags.length ? '' : '<span class="none">還沒有標籤</span>'}</div>
            <input type="text" id="kb-tags-input" class="kb-input"
                placeholder="打一個標籤按 Enter 就加一個（也可以一次貼多個，用逗號分隔）">
            <div class="row">
                <button type="button" class="kb-btn pri" data-kact="tags-add">加進去</button>
                <button type="button" class="kb-btn" data-kact="tags-done">完成</button>
                <span class="note">${tags.length}／${TAGS_MAX} 個</span>
            </div></div>`;
    }
    return `<div class="kb-tags" id="kb-tags">${tags.length ? tagsHtml(tags) : '<span class="none">沒有標籤</span>'}
        <button type="button" class="kb-link" data-kact="tags-edit">編輯標籤</button></div>`;
}

/** 表頭右下那一列：研究筆記 PDF ／ 原書 PDF ／ 這本書掛在哪個案子（owner 2026-09-19 那幾句）。
 *  兩份 PDF 分開兩顆鈕（他選的）：一顆是我們整理出來的、一顆是他當初上傳的那個檔。 */
function _headLinksHtml(b) {
    const proj = b.project && b.project.id ? b.project : null;
    return `<button type="button" class="kb-link" data-kact="pdf">下載研究筆記</button>
        ${b.status === 'pending' ? ''
        : '<button type="button" class="kb-link" data-kact="source">下載原書</button>'}
        ${proj
        ? `<a class="kb-link" href="/project.html?id=${encodeURIComponent(proj.id)}"
              target="_blank" rel="noopener">案子：${esc(proj.label || proj.id)}</a>`
        : '<button type="button" class="kb-link" data-kact="pane" data-pane="info">掛一個案子</button>'}`;
}

/** 整本的研究筆記 PDF（owner 2026-09-19：「這裡多一個 pdf 下載，讓大家可以下載資料」）。
 *  🔴 走 authDownload —— 書是私有的，`<a href>` 送不了 Authorization。 */
export function downloadPdf() {
    const b = S.book;
    if (!b) return;
    toast('正在做 PDF，十幾秒');
    authDownload(`${API}/${encodeURIComponent(b.id)}/pdf`,
        _fileName(b, '-研究筆記.pdf'), '下載研究筆記');
}

/** 他當初上傳的那個 PDF 原檔（owner 2026-09-19：「我的 pdf 希望放上書的 pdf」）。
 *  🔴 只有這裡拿得到整本原書；分享出去的那一頁沒有這顆鈕，也不該有。 */
export function downloadSource() {
    const b = S.book;
    if (!b) return;
    authDownload(`${API}/${encodeURIComponent(b.id)}/source`,
        b.source_name || _fileName(b, '.pdf'), '下載原書');
}

const _fileName = (b, tail) => `${(b.title || '書').replace(/[\\/:*?"<>|]+/g, '_')}${tail}`;

export function renderBook() {
    const b = S.book;
    const compiling = b.status === 'compiling';
    const pending = b.status === 'pending';
    // 窄螢幕：整個 aside 收成「一行標題列＋黏著的分頁」，其餘動作進 ⋯ 抽屜（kb-sheet）。
    // 寬螢幕的左欄維持原樣 —— 同一份 HTML，差別全在 knowledge.css 的 @media。
    S.root.innerHTML = `
      <div class="kb-book">
        <!-- 表頭跨整個寬度（owner 2026-09-19：「紅框處滿寬，他是表頭」）——
             書名不用在 300px 的欄位裡折行，標籤也才有地方一顆一顆加。
             窄螢幕它收成一行（見 knowledge.css 的 @media），⋯ 抽屜在那時才出現。 -->
        <div class="kb-bookhead">
            <div class="kb-head">
                <button type="button" class="kb-link kb-back" data-kact="back" aria-label="回書架">← <span class="w">書架</span></button>
                <h3 id="kb-title">${esc(b.title || b.source_name || '（未命名）')}</h3>
                <span id="kb-status">${statusPill(b)}</span>
                <button type="button" class="kb-more" data-kact="more" aria-label="更多動作">⋯</button>
            </div>
            <div class="a" id="kb-author">${esc(b.author || '')}</div>
            <div class="st"><span class="stage" id="kb-stage">${compiling ? esc(stageText(b.stage)) : ''}</span></div>
            ${b.status === 'failed' && b.error ? `<div class="err" title="${esc(b.error)}">編譯失敗：${esc(String(b.error).slice(0, 80))}</div>` : ''}
            <div id="kb-tags-row">${_tagsRowHtml(b)}</div>
            <div class="kb-headlinks">${_headLinksHtml(b)}</div>
        </div>
        <aside class="kb-side">
            <nav class="kb-tabs">${PANES.map(([k, l]) => `<button type="button" data-kact="pane" data-pane="${k}" class="${k === S.pane ? 'on' : ''}">${l}</button>`).join('')}</nav>
            <div class="kb-actions">
                ${pending
        ? '<button type="button" class="kb-btn pri" data-kact="attach">補上 PDF</button>'
        : `<button type="button" class="kb-btn pri" data-kact="compile" ${compiling ? 'disabled' : ''}>${b.status === 'compiled' ? '重新讀這本書' : (compiling ? '編譯中' : '讀這本書')}</button>`}
                <button type="button" class="kb-btn" data-kact="rename">改名</button>
                <button type="button" class="kb-btn danger" data-kact="delete">刪除</button>
            </div>
        </aside>
        <section class="kb-main" id="kb-pane"></section>
      </div>
      <input type="file" id="kb-book-file" accept=".pdf,application/pdf" hidden>
      <div id="kb-sheet-host"></div>`;
    const input = S.root.querySelector('#kb-book-file');
    input.addEventListener('change', () => {
        const f = input.files && input.files[0];
        input.value = '';
        if (f) attachFile(f);
    });
    renderPane();
}

/** 待補的書補上檔案：抽文字要幾秒，期間把鈕鎖起來並講在做什麼。 */
export async function attachFile(file) {
    const b = S.book;
    if (!file || !b) return;
    const bookId = b.id;
    const stage = S.root.querySelector('#kb-stage');
    if (stage) stage.textContent = `上傳中：${file.name}`;
    S.root.querySelectorAll('[data-kact="attach"]').forEach((el) => { el.disabled = true; });
    const fd = new FormData();
    fd.append('file', file, file.name);
    try {
        const meta = await api(`/${encodeURIComponent(bookId)}/file`, { method: 'POST', body: fd });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        // 先吃回傳的 status（pending -> uploaded），「讀這本書」那顆立刻解開，不用等重抓
        if (meta && meta.status) S.book.status = meta.status;
        renderBook();
        toast('已補上檔案' + (meta && meta.pages ? `，共 ${meta.pages} 頁` : ''));
        await refetchBook();
        if (!alive() || !S.book) return;
        if (confirm('現在就讓主控主機「讀這本書」？（10–20 分鐘，會在背景跑）')) compile();
    } catch (e) {
        if (!alive()) return;
        if (stage) stage.textContent = '';
        S.root.querySelectorAll('[data-kact="attach"]').forEach((el) => { el.disabled = false; });
        toast('補檔失敗：' + errText(e), true);
    }
}

/** ⋯ 的底部抽屜（只在窄螢幕出現；寬螢幕左欄本來就看得到這些）。危險動作排最後、紅字。 */
export function toggleSheet(on) {
    const host = S.root.querySelector('#kb-sheet-host');
    if (!host) return;
    if (!on) { host.innerHTML = ''; return; }
    const b = S.book;
    const compiling = b.status === 'compiling';
    host.innerHTML = `<div class="kb-sheet-dim" data-kact="sheet-close"></div>
      <div class="kb-sheet" role="dialog" aria-label="更多動作">
        <div class="grab"></div>
        <button type="button" class="item" data-kact="conclude-from-sheet" ${compiling ? 'disabled' : ''}>整理這段討論成結論</button>
        <button type="button" class="item" data-kact="tags-edit">編輯標籤<span class="sub">${esc(bookTags(b).join('、') || '沒有標籤')}</span></button>
        ${b.status === 'pending'
        ? '<button type="button" class="item" data-kact="attach">補上 PDF<span class="sub">這本書還沒有檔案</span></button>'
        : `<button type="button" class="item" data-kact="compile" ${compiling ? 'disabled' : ''}>${b.status === 'compiled' ? '重新讀這本書' : '讀這本書'}<span class="sub">約 10–20 分鐘</span></button>`}
        <button type="button" class="item" data-kact="pdf">下載研究筆記<span class="sub">整理出來的那一份（PDF）</span></button>
        ${b.status === 'pending' ? ''
        : `<button type="button" class="item" data-kact="source">下載原書<span class="sub">${esc(b.source_name || '你上傳的那個 PDF')}</span></button>`}
        <button type="button" class="item" data-kact="rename">改名</button>
        <button type="button" class="item danger" data-kact="delete">刪除這本書</button>
      </div>`;
}

/** 這一輪畫面借出去的 blob 網址：重畫前要收回來，不然翻幾十章會一直吃記憶體。 */
function _revokeAssets() {
    for (const u of S.assetUrls || []) URL.revokeObjectURL(u);
    S.assetUrls = [];
}

/** 把 md-lite 留下的 `<img data-md-src>` 填起來（帶 token 去拿，拿到的是 blob）。
 *  拿不到就讓它留著 alt 文字 —— 一張圖掛掉不該讓整章看不了。 */
async function _fillAssets(host) {
    const imgs = [...host.querySelectorAll('img[data-md-src]')];
    if (!imgs.length || !S.book) return;
    const bookId = S.book.id;
    // 每張各自抓、一起等：一章幾十張圖一張一張排隊會等很久，而彼此之間沒有先後
    const one = async (img) => {
        const rel = img.dataset.mdSrc || '';
        if (!/^assets\/[A-Za-z0-9._-]+$/.test(rel)) return;     // 只認我們自己產的那種
        try {
            const r = await fetch(`${API}/${encodeURIComponent(bookId)}/${rel}`, { headers: bearerHeader() });
            if (!r.ok) return;
            const url = URL.createObjectURL(await r.blob());
            if (!alive() || !S.book || S.book.id !== bookId) { URL.revokeObjectURL(url); return; }
            (S.assetUrls = S.assetUrls || []).push(url);
            img.src = url;
            img.classList.add('on');
            // 圖說：claude 寫在 alt 裡（提示叫它寫 `![說明](…)`）。alt 只有讀螢幕看得到，
            // 書裡的圖沒有說明等於看不懂，所以再畫一行出來。
            // 🔴 相簿裡的圖不用 —— 那邊每張本來就有 <figcaption>，再補一行會變成圖說出現兩次。
            const alt = img.closest('figure') ? '' : (img.alt || '').trim();
            if (alt && !(img.nextElementSibling || {}).classList?.contains('kb-cap')) {
                const cap = document.createElement('div');
                cap.className = 'kb-cap';
                cap.textContent = alt;
                img.insertAdjacentElement('afterend', cap);
            }
        } catch (_) { /* 一張拿不到就算了，alt 還在 */ }
    };
    await Promise.all(imgs.map(one));
}

export function renderPane() {
    const pane = S.root.querySelector('#kb-pane');
    if (!pane) return;
    _revokeAssets();
    S.root.querySelectorAll('.kb-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.pane === S.pane));
    if (S.pane === 'chat') pane.innerHTML = chatHtml();
    else if (S.pane === 'conclusion') pane.innerHTML = _docHtml('conclusion');
    else if (S.pane === 'notes') pane.innerHTML = _docHtml('notes');
    else if (S.pane === 'skeleton') pane.innerHTML = _skeletonHtml();
    else if (S.pane === 'extend') pane.innerHTML = extendHtml();
    else if (S.pane === 'info') pane.innerHTML = infoHtml();
    else if (S.pane === 'gallery') pane.innerHTML = galleryHtml();
    else pane.innerHTML = _chaptersHtml();
    if (S.pane === 'chat') scrollChat();
    else _fillAssets(pane);
}

/** 切分頁（點分頁鈕）：同一頁且不在編輯就不動；討論分頁第一次進來才抓 */
export function switchPane(next) {
    if (next === S.pane && !S.editing) return;
    S.pane = next; S.editing = false; S.chapter = null; S.infoEdit = false;
    renderPane();
    _loadPaneData(next);
}

// ── 標籤 ────────────────────────────────────────────────────
function _rerenderTags() {
    const row = S.root.querySelector('#kb-tags-row');
    if (row && S.book) row.innerHTML = _tagsRowHtml(S.book);
}

export function editTags(on) {
    S.editingTags = !!on;
    _rerenderTags();
    const input = S.root.querySelector('#kb-tags-input');
    if (input) input.focus();
}

/** 把輸入框裡的字加成標籤（逗號或 Enter 都可以），立刻存。 */
export async function addTags() {
    const input = S.root.querySelector('#kb-tags-input');
    if (!input || !S.book) return;
    const add = parseTags(input.value);
    if (!add.length) return;
    input.value = '';
    await _putTags([...bookTags(S.book), ...add]);
    const again = S.root.querySelector('#kb-tags-input');
    if (again) again.focus();
}

/** 移除第 i 個標籤。 */
export async function removeTag(i) {
    if (!S.book) return;
    const next = bookTags(S.book).filter((_, k) => k !== Number(i));
    await _putTags(next);
}

async function _putTags(tags) {
    const bookId = S.book.id;
    const before = bookTags(S.book);
    S.book.tags = tags;
    _rerenderTags();
    try {
        const d = await api(`/${encodeURIComponent(bookId)}`, { method: 'PUT', body: { tags } });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.book.tags = (d && Array.isArray(d.tags)) ? d.tags : tags;   // 後端會去重、截長、擋上限
        _rerenderTags();
        refreshShelfQuietly();
    } catch (e) {
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.book.tags = before;
        _rerenderTags();
        toast('標籤存不起來：' + errText(e), true);
    }
}

export async function saveTags(btn) {
    const input = S.root.querySelector('#kb-tags-input');
    if (!input || !S.book) return;
    const tags = parseTags(input.value);
    btn.disabled = true;
    try {
        const d = await api(`/${encodeURIComponent(S.book.id)}`, { method: 'PUT', body: { tags } });
        if (!alive() || !S.book) return;
        S.book.tags = (d && Array.isArray(d.tags)) ? d.tags : tags;
        S.editingTags = false;
        _rerenderTags();
        toast('標籤已儲存');
        refreshShelfQuietly();
    } catch (e) {
        if (!alive()) return;
        btn.disabled = false;
        toast('儲存標籤失敗：' + errText(e), true);
    }
}

// ── 結論／筆記（整檔編輯） ─────────────────────────────────────
function _docHtml(key) {
    const label = key === 'conclusion' ? '結論' : '筆記';
    const text = S.book[key] || '';
    const why = key === 'conclusion'
        ? '你認同過的原則。之後每次討論、顧問每次回答都先讀這裡、優先引用。「存成結論」與「整理結論」都追加在這個檔尾端；也可以整檔改。'
        : '你自己的筆記，每次討論都讀（排在結論之後、書之前）。';
    if (S.editing) {
        return `<div class="kb-doc">
            <div class="tools">
                <button type="button" class="kb-btn pri" data-kact="doc-save" data-key="${key}">儲存${label}</button>
                <button type="button" class="kb-btn" data-kact="doc-cancel">取消</button>
                <span class="note">整檔覆寫；Markdown。</span>
            </div>
            <textarea id="kb-doc-text" class="kb-textarea">${esc(text)}</textarea>
        </div>`;
    }
    return `<div class="kb-doc">
        <div class="tools">
            <button type="button" class="kb-btn" data-kact="doc-edit">編輯${label}</button>
            <span class="note">${why}</span>
        </div>
        ${text.trim() ? `<div class="kb-md">${mdToHtml(text)}</div>` : `<div class="kb-empty">${label}還是空的。</div>`}
    </div>`;
}

export async function refetchBook() {
    if (!S.book) return;
    const bookId = S.book.id;
    try {
        const d = await api(`/${encodeURIComponent(bookId)}`);
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.book = normBook(Object.assign(S.book, d));
    } catch (e) {
        if (!alive() || !S.book) return;
        toast(errText(e), true);
        return;
    }
    if ((S.pane === 'conclusion' || S.pane === 'notes') && !S.editing) renderPane();
}

export async function saveDoc(key, btn) {
    const ta = S.root.querySelector('#kb-doc-text');
    if (!ta || !S.book) return;
    const text = ta.value;
    btn.disabled = true;
    try {
        await api(`/${encodeURIComponent(S.book.id)}/${key}`, { method: 'PUT', body: { text } });
        if (!alive()) return;
        S.book[key] = text;
        if (key === 'conclusion') S.book.has_conclusion = !!text.trim();
        else S.book.has_notes = !!text.trim();
        S.editing = false;
        renderPane();
        toast('已儲存');
    } catch (e) {
        if (!alive()) return;
        btn.disabled = false;
        toast('儲存失敗：' + errText(e), true);
    }
}

// ── 骨架／章節 ──────────────────────────────────────────────
/** 骨架很長（實測一本三章的書就 7,480px），頂端給一排段落跳轉。
 *  段落標題直接從 markdown 的 `## ` 行取（不用正則猜內容），取不到就不畫這排。 */
function _skeletonJumpHtml(md) {
    const heads = String(md || '').split('\n')
        .filter((ln) => ln.startsWith('## '))
        .map((ln) => ln.slice(3).trim())
        .filter(Boolean);
    if (heads.length < 2) return '';
    return `<div class="kb-jump">${heads.map((h, i) => `<button type="button" data-kact="jump" data-i="${i}">${esc(h)}</button>`).join('')}</div>`;
}

function _skeletonHtml() {
    const b = S.book;
    if (!b.skill && !b.cheatsheet) {
        return `<div class="kb-empty">${b.status === 'compiling' ? '編譯中，骨架還沒產出來。' : '還沒編譯。按「讀這本書」，主控主機會產出骨架與章節。'}</div>`;
    }
    return `
        ${_skeletonJumpHtml(b.skill)}
        <div class="kb-sec"><h4>SKILL.md</h4><span class="why">核心心智模型、決策規則（當你 X 就 Y，因為 Z）、章節索引</span></div>
        <div class="kb-md" id="kb-skill-md">${b.skill ? mdToHtml(b.skill) : '<div class="kb-empty">（空）</div>'}</div>
        <div class="kb-sec"><h4>速查表</h4><span class="why">cheatsheet.md</span></div>
        <div class="kb-md">${b.cheatsheet ? mdToHtml(b.cheatsheet) : '<div class="kb-empty">（空）</div>'}</div>
        <button type="button" class="kb-totop" data-kact="to-top" aria-label="回到頂端">↑</button>`;
}

/** 章末的圖：那一章頁碼範圍內的**每一張**，附圖說與頁數。
 *  2026-09-19：原本靠提示叫 claude 自己插，實測三本書只引用了 3/50、0/10、0/35 ——
 *  靠模型挑，大部分的圖永遠不會出現。現在由程式一律列出。
 *  owner 同日補充「能插入的就插入，但是有抽出來的圖片就放相簿」：文中插過的**照樣**列在這裡，
 *  相簿是這一章的完整索引，不是「剩下的」。 */
function _galleryHtml(ch) {
    const rows = (ch && ch.assets) || [];
    if (!rows.length) return '';
    return `<div class="kb-gallery">
        <h4>本章的圖（${rows.length}）<span class="why">從 PDF 原書抽出來的，括號是頁數</span></h4>
        ${rows.map((a) => `<figure>
            <img data-md-src="assets/${esc(a.name)}" alt="${esc(a.caption || '')}" loading="lazy">
            <figcaption>${a.caption ? esc(a.caption) : '（原書沒有圖說）'}
                <span class="src">${esc(figureSource(S.book, a.page))}</span></figcaption>
        </figure>`).join('')}
    </div>`;
}

/** 跳到骨架的第 i 個 `## ` 段（渲染後的第 i 個 h2）。 */
export function jumpToSection(i) {
    const md = S.root.querySelector('#kb-skill-md');
    if (!md) return;
    const h = md.querySelectorAll('h2')[Number(i)];
    if (h && h.scrollIntoView) h.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function _chaptersHtml() {
    const list = chapterList(S.book);
    if (S.chapter) {
        const idx = list.findIndex((c) => String(c.n) === String(S.chapter.n));
        const prev = idx > 0 ? list[idx - 1] : null;
        const next = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : null;
        const nav = `<div class="kb-chapnav">
            ${prev ? `<button type="button" class="kb-btn" data-kact="chapter" data-n="${esc(String(prev.n))}">← 第 ${esc(String(prev.n))} 章</button>` : '<span></span>'}
            ${next ? `<button type="button" class="kb-btn" data-kact="chapter" data-n="${esc(String(next.n))}">第 ${esc(String(next.n))} 章 →</button>` : '<span></span>'}
        </div>`;
        return `<div class="kb-doc">
            <div class="tools">
                <button type="button" class="kb-link" data-kact="chapter-back">← 章節清單</button>
                <span class="note">第 ${esc(String(S.chapter.n))} 章 ${esc(S.chapter.title || '')}</span>
            </div>
            <div class="kb-md">${S.chapter.md ? mdToHtml(S.chapter.md) : '<div class="kb-empty">這章沒有內容。</div>'}</div>
            ${_galleryHtml(S.chapter)}
            ${nav}
            <button type="button" class="kb-totop" data-kact="to-top" aria-label="回到頂端">↑</button>
        </div>`;
    }
    if (!list.length) {
        const st = stageText(S.book.stage);
        return `<div class="kb-empty">${S.book.status === 'compiling' ? `編譯中${st ? `（${esc(st)}）` : ''}，章節一章一章長出來。` : '還沒編譯，沒有章節。'}</div>`;
    }
    return `<div class="kb-chapters">${list.map((c) => `<button type="button" data-kact="chapter" data-n="${esc(String(c.n))}"><span class="n">${esc(String(c.n)).padStart(2, '0')}</span>${esc(c.title || c.file || '')}</button>`).join('')}</div>`;
}

export async function openChapter(n) {
    if (!S.book) return;
    const bookId = S.book.id;
    const meta = chapterList(S.book).find((c) => String(c.n) === String(n)) || { n };
    try {
        const d = await api(`/${encodeURIComponent(bookId)}/chapters/${encodeURIComponent(n)}`);
        if (!alive() || !S.book || S.book.id !== bookId) return;
        const md = typeof d === 'string' ? d : (d && (d.md || d.text || d.content)) || '';
        S.chapter = { n: meta.n, title: (d && d.title) || meta.title || '', md,
            assets: (d && d.assets) || [], start_page: d && d.start_page, end_page: d && d.end_page };
        renderPane();
        window.scrollTo(0, 0);
    } catch (e) {
        if (!alive()) return;
        toast('章節載入失敗：' + errText(e), true);
    }
}

// ── 編譯 ────────────────────────────────────────────────────
export async function compile() {
    // 待補的書還沒有檔 —— 後端會回 409，但讓他按下去才知道不好；這裡先擋並講原因
    if (S.book && S.book.status === 'pending') { toast('這本書還沒有檔案，先補 PDF 再讀', true); return; }
    if (!S.book) return;
    // 已編譯的書再按＝整本重來（force）；失敗／沒編過的＝接著補缺的章節（後端預設）
    const force = S.book.status === 'compiled';
    if (force && !confirm('重新讀這本書？已產的章節與骨架會丟掉、整本重編（10–20 分鐘）。')) return;
    try {
        await api(`/${encodeURIComponent(S.book.id)}/compile`, { method: 'POST', body: { force } });
    } catch (e) {
        if (!alive()) return;
        toast((e.status === 409 ? '已經在編譯了' : '啟動編譯失敗：' + errText(e)), e.status !== 409);
        if (e.status !== 409) return;
    }
    if (!alive() || !S.book) return;
    S.book.status = 'compiling';
    renderBook();
    watchCompile();
}

export function watchCompile() {
    clearInterval(S.compileTimer);
    const bookId = S.book.id;
    S.compileTimer = setInterval(async () => {
        if (!alive() || !S.book || S.book.id !== bookId) { clearInterval(S.compileTimer); S.compileTimer = null; return; }
        if (document.hidden) return;    // 分頁在背景就不打（有終點的輪詢，但一次 10–20 分鐘，省一點）
        let d;
        try { d = await api(`/${encodeURIComponent(bookId)}`); } catch (_) { return; }
        if (!alive() || !S.book || S.book.id !== bookId) return;
        const wasPane = S.pane;
        S.book = normBook(Object.assign(S.book, d));
        if (d.status !== 'compiling') {
            clearInterval(S.compileTimer); S.compileTimer = null;
            renderBook();
            toast(d.status === 'compiled' ? '這本書讀完了' : '編譯失敗：' + (d.error || '（沒有說明）'), d.status !== 'compiled');
            refreshShelfQuietly();
            return;
        }
        const stage = S.root.querySelector('#kb-stage');
        if (stage) stage.textContent = stageText(d.stage);
        const pill = S.root.querySelector('#kb-status');
        if (pill) pill.innerHTML = statusPill(d);
        // 章節分頁：一章一章長出來，清單跟著更新（沒打開某一章時才重畫）
        if (wasPane === 'chapters' && !S.chapter) renderPane();
    }, COMPILE_POLL_MS);
}

// ── 改名／刪除 ────────────────────────────────────────────────
export async function rename() {
    if (!S.book) return;
    const title = prompt('書名', S.book.title || '');
    if (title === null) return;
    const author = prompt('作者（可空）', S.book.author || '');
    if (author === null) return;
    try {
        const d = await api(`/${encodeURIComponent(S.book.id)}`, { method: 'PUT', body: { title: title.trim(), author: author.trim() } });
        if (!alive() || !S.book) return;
        S.book = normBook(Object.assign(S.book, d && typeof d === 'object' ? d : {}, { title: title.trim(), author: author.trim() }));
        renderBook();
        refreshShelfQuietly();
    } catch (e) {
        if (!alive()) return;
        toast('改名失敗：' + errText(e), true);
    }
}

export async function remove() {
    if (!S.book) return;
    const name = S.book.title || S.book.source_name || S.book.id;
    if (!confirm(`刪除「${name}」？原檔、骨架、章節、討論、結論、筆記整個資料夾都會刪掉，不能復原。`)) return;
    try {
        await api(`/${encodeURIComponent(S.book.id)}`, { method: 'DELETE' });
        if (!alive()) return;
        stopTimers();
        toast('已刪除');
        await loadShelf();
    } catch (e) {
        if (!alive()) return;
        toast('刪除失敗：' + errText(e), true);
    }
}
