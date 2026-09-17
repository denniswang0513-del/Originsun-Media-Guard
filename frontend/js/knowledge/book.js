/**
 * book.js — 知識庫書頁：左欄（標題／狀態／標籤／分頁鈕／動作）＋右欄（分頁內容）。
 * 分頁：討論（chat.js）／結論／筆記（整檔編輯）／骨架（SKILL.md＋速查表）／章節（清單 → 單章）。
 * 編譯：POST compile → 每 3 秒抓 GET /{id} 直到 status 離開 compiling（分頁在背景就跳過那一拍）。
 */
import { mdToHtml } from '../shared/md-lite.js';
import { S, PANES, COMPILE_POLL_MS, api, esc, errText, stageText, alive, toast, stopTimers, normBook,
         chapterList, bookTags, parseTags, statusPill, tagsHtml } from './ctx.js';
import { loadShelf, refreshShelfQuietly } from './shelf.js';
import { chatHtml, scrollChat, loadChat } from './chat.js';

export async function openBook(id, { compile: thenCompile = false } = {}) {
    stopTimers();
    S.chapter = null; S.editing = false; S.editingTags = false; S.chat = [];
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
    if (alive() && S.book && (S.pane === 'chat' || S.book.chatting)) loadChat();   // chatting＝上一輪還在回，進來就接著等
}

function _tagsRowHtml(b) {
    const tags = bookTags(b);
    if (S.editingTags) {
        return `<div class="kb-tags-edit">
            <input type="text" id="kb-tags-input" class="kb-input" value="${esc(tags.join(', '))}" placeholder="逗號分隔，例如：財務, 投資">
            <div class="row">
                <button type="button" class="kb-btn pri" data-kact="tags-save">儲存標籤</button>
                <button type="button" class="kb-btn" data-kact="tags-cancel">取消</button>
            </div></div>`;
    }
    return `<div class="kb-tags" id="kb-tags">${tags.length ? tagsHtml(tags) : '<span class="none">沒有標籤</span>'}
        <button type="button" class="kb-link" data-kact="tags-edit">編輯標籤</button></div>`;
}

export function renderBook() {
    const b = S.book;
    const compiling = b.status === 'compiling';
    S.root.innerHTML = `
      <div class="kb-book">
        <aside class="kb-side">
            <button type="button" class="kb-link kb-back" data-kact="back">← 書架</button>
            <h3 id="kb-title">${esc(b.title || b.source_name || '（未命名）')}</h3>
            <div class="a" id="kb-author">${esc(b.author || '')}</div>
            <div class="st"><span id="kb-status">${statusPill(b)}</span>
                <span class="stage" id="kb-stage">${compiling ? esc(stageText(b.stage)) : ''}</span></div>
            ${b.status === 'failed' && b.error ? `<div class="err" title="${esc(b.error)}">編譯失敗：${esc(String(b.error).slice(0, 80))}</div>` : ''}
            <div id="kb-tags-row">${_tagsRowHtml(b)}</div>
            <nav class="kb-tabs">${PANES.map(([k, l]) => `<button type="button" data-kact="pane" data-pane="${k}" class="${k === S.pane ? 'on' : ''}">${l}</button>`).join('')}</nav>
            <div class="kb-actions">
                <button type="button" class="kb-btn pri" data-kact="compile" ${compiling ? 'disabled' : ''}>${b.status === 'compiled' ? '重新讀這本書' : (compiling ? '編譯中' : '讀這本書')}</button>
                <button type="button" class="kb-btn" data-kact="rename">改名</button>
                <button type="button" class="kb-btn danger" data-kact="delete">刪除</button>
            </div>
        </aside>
        <section class="kb-main" id="kb-pane"></section>
      </div>`;
    renderPane();
}

export function renderPane() {
    const pane = S.root.querySelector('#kb-pane');
    if (!pane) return;
    S.root.querySelectorAll('.kb-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.pane === S.pane));
    if (S.pane === 'chat') pane.innerHTML = chatHtml();
    else if (S.pane === 'conclusion') pane.innerHTML = _docHtml('conclusion');
    else if (S.pane === 'notes') pane.innerHTML = _docHtml('notes');
    else if (S.pane === 'skeleton') pane.innerHTML = _skeletonHtml();
    else pane.innerHTML = _chaptersHtml();
    if (S.pane === 'chat') scrollChat();
}

/** 切分頁（點分頁鈕）：同一頁且不在編輯就不動；討論分頁第一次進來才抓 */
export function switchPane(next) {
    if (next === S.pane && !S.editing) return;
    S.pane = next; S.editing = false; S.chapter = null;
    renderPane();
    if (next === 'chat' && !S.chat.length && !S.wait) loadChat();
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
function _skeletonHtml() {
    const b = S.book;
    if (!b.skill && !b.cheatsheet) {
        return `<div class="kb-empty">${b.status === 'compiling' ? '編譯中，骨架還沒產出來。' : '還沒編譯。按左邊的「讀這本書」，主控主機會產出骨架與章節。'}</div>`;
    }
    return `
        <div class="kb-sec"><h4>SKILL.md</h4><span class="why">核心心智模型、決策規則（當你 X 就 Y，因為 Z）、章節索引</span></div>
        <div class="kb-md">${b.skill ? mdToHtml(b.skill) : '<div class="kb-empty">（空）</div>'}</div>
        <div class="kb-sec"><h4>速查表</h4><span class="why">cheatsheet.md</span></div>
        <div class="kb-md">${b.cheatsheet ? mdToHtml(b.cheatsheet) : '<div class="kb-empty">（空）</div>'}</div>`;
}

function _chaptersHtml() {
    const list = chapterList(S.book);
    if (S.chapter) {
        return `<div class="kb-doc">
            <div class="tools">
                <button type="button" class="kb-link" data-kact="chapter-back">← 章節清單</button>
                <span class="note">第 ${esc(String(S.chapter.n))} 章 ${esc(S.chapter.title || '')}</span>
            </div>
            <div class="kb-md">${S.chapter.md ? mdToHtml(S.chapter.md) : '<div class="kb-empty">這章沒有內容。</div>'}</div>
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
        S.chapter = { n: meta.n, title: (d && d.title) || meta.title || '', md };
        renderPane();
        window.scrollTo(0, 0);
    } catch (e) {
        if (!alive()) return;
        toast('章節載入失敗：' + errText(e), true);
    }
}

// ── 編譯 ────────────────────────────────────────────────────
export async function compile() {
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
