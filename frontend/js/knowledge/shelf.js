/**
 * shelf.js — 知識庫書架：標籤篩選列、書單、上傳（拖放／選檔，可以一次多個）。
 *
 * 標籤篩選：點一個標籤就 `GET ?tag=`（後端篩），篩選列本身用的是「沒篩時」抓到的全部標籤
 * （S.allTags），所以切標籤不會讓別的標籤從列上消失。
 * 上傳走 XHR（js/shared/utils.uploadWithProgress，有進度條）；其餘 JSON 走 ctx.api。
 */
import { bearerHeader, uploadWithProgress, wireFileDrop } from '../shared/utils.js';
import { API, OFFLINE_MSG, S, nav, api, esc, errText, alive, toast, statusPill, bookTags, tagsHtml,
         UPLOAD_PARALLEL } from './ctx.js';

function _collectTags(books) {
    const seen = [];
    for (const b of books || []) for (const t of bookTags(b)) if (!seen.includes(t)) seen.push(t);
    return seen;
}

export async function loadShelf() {
    try {
        S.books = await api(S.tag ? `?tag=${encodeURIComponent(S.tag)}` : '');
    } catch (e) {
        if (!alive()) return;
        S.root.innerHTML = `<div class="kb-top"><h2>知識庫</h2></div>
            <div class="${e.offline ? 'kb-offline' : 'kb-error'}">${esc(errText(e))}
                <button type="button" class="kb-btn" data-kact="reload">重試</button></div>`;
        return;
    }
    if (!alive()) return;
    if (!S.tag) S.allTags = _collectTags(S.books);
    renderShelf();
}

function _guideHtml() {
    if (S.tag) return `<div class="kb-empty">沒有標了「${esc(S.tag)}」的書。<button type="button" class="kb-link" data-kact="tag" data-tag="">看全部</button></div>`;
    return `<div class="kb-empty kb-guide">
        <h3>第一本書怎麼開始</h3>
        <ol>
            <li>把書的 PDF 拖到上面的框裡（一次拖幾本也可以）。主控主機會先抽文字，一本幾秒。</li>
            <li>手邊還沒有檔的書，可以先按「先建書名」記下來，狀態是「待補」，之後再補 PDF。</li>
            <li>按「讀這本書」：主控主機用 AI 把它讀成骨架（心智模型、決策規則、名詞、速查表）與每章重點，10–20 分鐘，在背景跑。</li>
            <li>到「討論」分頁問它：這本書的核心規則怎麼套在我身上？覺得講得對的那段，按「存成結論」。</li>
            <li>幫書上標籤（書頁標題列可編，逗號分隔）。標了「財務」的書，財務顧問每次回答都會先讀它的結論。</li>
        </ol>
    </div>`;
}

export function renderShelf() {
    S.book = null; S.chat = []; S.chapter = null; S.editing = false; S.editingTags = false;
    const cards = (S.books || []).map((b) => {
        const tags = bookTags(b);
        const facts = [
            b.pages ? `${b.pages} 頁` : '',
            b.chapters ? `${b.chapters} 章` : '',
            b.has_conclusion ? '有結論' : '',
            b.has_notes ? '有筆記' : '',
            b.extend_new ? `延伸 ${b.extend_new} 則未讀` : (b.has_extend ? '有延伸' : ''),
        ].filter(Boolean);
        return `<div class="kb-card" data-kact="open" data-id="${esc(b.id)}" role="button" tabindex="0">
        <div class="l">
            <div class="t">${esc(b.title || b.source_name || '（未命名）')}</div>
            <div class="s">${b.author ? `<span class="a">${esc(b.author)}</span>` : ''}${tags.length ? tagsHtml(tags) : ''}</div>
        </div>
        <div class="r">${statusPill(b)}<div class="m">${facts.map((f) => `<span>${esc(f)}</span>`).join('')}</div></div>
    </div>`;
    }).join('') || _guideHtml();
    const tagBar = S.allTags.length
        ? `<div class="kb-tagbar">
            <button type="button" class="kb-chip ${S.tag ? '' : 'on'}" data-kact="tag" data-tag="">全部</button>
            ${S.allTags.map((t) => `<button type="button" class="kb-chip ${t === S.tag ? 'on' : ''}" data-kact="tag" data-tag="${esc(t)}">${esc(t)}</button>`).join('')}
          </div>`
        : '';
    S.root.innerHTML = `
        <div class="kb-top"><h2>知識庫</h2>
            <div class="why">上傳一本書的 PDF，主控主機把它讀成骨架（心智模型、決策規則、名詞、模式、速查表）與每章的重點；
                之後在書頁跟 AI 討論，把講定的原則存成結論 —— 之後每次討論、顧問每次回答都先讀結論。</div></div>
        <div class="kb-drop" id="kb-drop">把 PDF 拖到這裡（一次幾本都可以），或
            <button type="button" class="kb-btn" data-kact="pick">選擇檔案</button>
            <button type="button" class="kb-btn ghost" data-kact="new-pending">先建書名</button>
            <input type="file" id="kb-file" hidden multiple>
            <div id="kb-upload-note" class="note">${esc(S.uploadNote)}</div>
            <ul class="kb-uploads" id="kb-uploads">${_uploadsHtml()}</ul>
        </div>
        ${tagBar}
        <div class="kb-shelf">${cards}</div>
        <div class="kb-foot"><b>一本書三層</b>：結論（你認同過的原則，之後每次討論必讀）→ 筆記（你自己寫的）→ 骨架與章節（編譯自動產）。
            「讀這本書」要 10–20 分鐘，編譯中可以先聊；沒編譯過的書 AI 只看得到你的筆記與結論。</div>`;
    const input = S.root.querySelector('#kb-file');
    input.accept = '.pdf,application/pdf';
    input.addEventListener('change', () => { uploadMany([...(input.files || [])]); input.value = ''; });
    wireFileDrop(S.root.querySelector('#kb-drop'), (items) => uploadMany(items.map((it) => it.file)));
}

export async function setTag(tag) {
    S.tag = tag || '';
    await loadShelf();
}

/** 一次拖幾本進來：每本自己一列、自己的進度條，一本失敗不影響其他本。
 *  同時只傳 UPLOAD_PARALLEL 個 —— 每支上傳都把整包讀進記憶體，五本厚書同時傳就是 1GB。 */
export async function uploadMany(files) {
    const list = (files || []).filter(Boolean);
    if (!list.length) return;
    const odd = list.filter((f) => !/\.pdf$/i.test(f.name) && f.type !== 'application/pdf');
    if (odd.length && !confirm(`這幾個看起來不是 PDF：\n${odd.map((f) => f.name).join('\n')}\n仍要上傳？（主控主機會看檔頭決定收不收）`)) return;
    S.uploads = list.map((f) => ({ name: f.name, state: '排隊中', cls: '', pct: 0 }));
    S.uploadNote = list.length > 1 ? `共 ${list.length} 本，一次傳 ${UPLOAD_PARALLEL} 本` : '';
    _paintUploads();
    let next = 0;
    const done = [];
    const worker = async () => {
        while (next < list.length) {
            const i = next++;
            const meta = await _uploadOne(list[i], i);
            if (meta) done.push(meta);
        }
    };
    await Promise.all(Array.from({ length: Math.min(UPLOAD_PARALLEL, list.length) }, worker));
    if (!alive()) return;
    const bad = list.length - done.length;
    S.uploadNote = `完成 ${done.length} 本` + (bad ? `，失敗 ${bad} 本（下面那幾列寫了原因）` : '');
    if (done.length) toast(`已上傳 ${done.length} 本`);
    await loadShelf();                                  // 重畫會把上面那幾列一起帶回來（狀態存在 S）
    if (!alive() || done.length !== 1) return;          // 只傳一本才順口問要不要現在讀
    if (confirm('現在就讓主控主機「讀這本書」？（10–20 分鐘，會在背景跑）')) {
        await nav.openBook(done[0].id, { compile: true });
    }
}

function _uploadsHtml() {
    return (S.uploads || []).map((u, i) => `<li id="kb-up-${i}" class="${u.cls}">
        <span class="n">${esc(u.name)}</span><span class="st">${esc(u.state)}</span>
        <span class="kb-prog"><i style="width:${Math.round(u.pct)}%"></i></span></li>`).join('');
}

/** 只重畫上傳那一段（進度一秒好幾次，不要整個書架重畫）。 */
function _paintUploads() {
    const host = S.root && S.root.querySelector('#kb-uploads');
    const note = S.root && S.root.querySelector('#kb-upload-note');
    if (host) host.innerHTML = _uploadsHtml();
    if (note) note.textContent = S.uploadNote;
}

async function _uploadOne(file, i) {
    const u = S.uploads[i];
    if (!u) return null;
    const set = (state, pct, cls) => {
        u.state = state; u.pct = pct; if (cls) u.cls = cls;
        _paintUploads();
    };
    const fd = new FormData();
    fd.append('file', file, file.name);
    set('上傳中', 0);
    try {
        const meta = await uploadWithProgress(API, fd, {
            headers: bearerHeader(),
            onProgress: (loaded, total) => { if (total) set('上傳中', loaded / total * 100); },
            onUploaded: () => set('抽文字中', 100),
        });
        if (alive()) set('完成', 100, 'ok');
        return meta && meta.id ? meta : null;
    } catch (e) {
        if (alive()) set(e.status === 404 || e.status === 503 ? OFFLINE_MSG : (e.detail || e.message), 100, 'bad');
        return null;
    }
}

/** 手邊還沒有檔的書：先把書名記下來（狀態「待補」），之後再補 PDF。 */
export async function createPending() {
    const title = window.prompt('書名（之後再補 PDF）');
    if (!title || !title.trim()) return;
    try {
        const b = await api('/pending', { method: 'POST', body: { title: title.trim() } });
        if (!alive()) return;
        toast('已建立：' + (b && b.title ? b.title : title.trim()));
        await loadShelf();
    } catch (e) {
        if (alive()) toast('建立失敗：' + errText(e), true);
    }
}

/** 背景重抓書架（狀態 pill 會變）；回到書架而且清單真的變了才重畫 —— 不打斷正在進行的上傳 */
export async function refreshShelfQuietly() {
    const was = JSON.stringify(S.books || []);
    let next;
    try { next = await api(S.tag ? `?tag=${encodeURIComponent(S.tag)}` : ''); } catch (_) { return; }
    S.books = next;
    if (!S.tag) S.allTags = _collectTags(S.books);
    if (alive() && !S.book && JSON.stringify(S.books || []) !== was) renderShelf();
}
