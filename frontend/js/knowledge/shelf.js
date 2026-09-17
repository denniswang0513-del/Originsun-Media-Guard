/**
 * shelf.js — 知識庫書架：標籤篩選列、卡片、上傳（拖放／選檔）。
 *
 * 標籤篩選：點一個標籤就 `GET ?tag=`（後端篩），篩選列本身用的是「沒篩時」抓到的全部標籤
 * （S.allTags），所以切標籤不會讓別的標籤從列上消失。
 * 上傳走 XHR（js/shared/utils.uploadWithProgress，有進度條）；其餘 JSON 走 ctx.api。
 */
import { bearerHeader, uploadWithProgress, wireFileDrop } from '../shared/utils.js';
import { API, OFFLINE_MSG, S, nav, api, esc, errText, alive, toast, statusPill, bookTags, tagsHtml } from './ctx.js';

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
            <li>把一本書的 PDF 拖到上面的框裡（或按「選擇檔案」）。主控主機會先抽文字，幾秒就好。</li>
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
        return `<div class="kb-card" data-kact="open" data-id="${esc(b.id)}" role="button" tabindex="0">
        <div class="t">${esc(b.title || b.source_name || '（未命名）')}</div>
        ${b.author ? `<div class="a">${esc(b.author)}</div>` : ''}
        ${tags.length ? `<div class="tags">${tagsHtml(tags)}</div>` : ''}
        <div class="m">${statusPill(b)}${b.pages ? `<span>${esc(String(b.pages))} 頁</span>` : ''}${b.chapters ? `<span>${esc(String(b.chapters))} 章</span>` : ''}${b.has_conclusion ? '<span>有結論</span>' : ''}${b.has_notes ? '<span>有筆記</span>' : ''}</div>
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
        <div class="kb-drop" id="kb-drop">把 PDF 拖到這裡，或
            <button type="button" class="kb-btn" data-kact="pick">選擇檔案</button>
            <input type="file" id="kb-file" hidden>
            <div class="kb-prog" id="kb-prog" hidden><i></i></div>
            <div id="kb-upload-note" class="note"></div>
        </div>
        ${tagBar}
        <div class="kb-shelf">${cards}</div>
        <div class="kb-foot"><b>一本書三層</b>：結論（你認同過的原則，之後每次討論必讀）→ 筆記（你自己寫的）→ 骨架與章節（編譯自動產）。
            「讀這本書」要 10–20 分鐘，編譯中可以先聊；沒編譯過的書 AI 只看得到你的筆記與結論。</div>`;
    const input = S.root.querySelector('#kb-file');
    input.accept = '.pdf,application/pdf';
    input.addEventListener('change', () => { if (input.files && input.files[0]) upload(input.files[0]); input.value = ''; });
    wireFileDrop(S.root.querySelector('#kb-drop'), (items) => { if (items[0]) upload(items[0].file); });
}

export async function setTag(tag) {
    S.tag = tag || '';
    await loadShelf();
}

export async function upload(file) {
    const note = S.root.querySelector('#kb-upload-note');
    const prog = S.root.querySelector('#kb-prog');
    const bar = prog && prog.querySelector('i');
    if (!file || !note || !prog || !bar) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') {
        if (!confirm(`「${file.name}」看起來不是 PDF。仍要上傳？（主控主機會看檔頭決定收不收）`)) return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    prog.hidden = false; bar.style.width = '0';
    note.classList.remove('bad');
    note.textContent = `上傳中：${file.name}`;
    try {
        const meta = await uploadWithProgress(API, fd, {
            headers: bearerHeader(),
            onProgress: (loaded, total) => { if (total) bar.style.width = Math.round(loaded / total * 100) + '%'; },
            onUploaded: () => { note.textContent = `已送達，主控主機正在抽文字：${file.name}`; bar.style.width = '100%'; },
        });
        if (!alive()) return;
        toast(`已上傳：${meta && meta.title ? meta.title : file.name}`);
        await loadShelf();
        if (!alive() || !meta || !meta.id) return;
        if (confirm('現在就讓主控主機「讀這本書」？（10–20 分鐘，會在背景跑）')) {
            await nav.openBook(meta.id, { compile: true });
        }
    } catch (e) {
        if (!alive()) return;
        prog.hidden = true;
        note.classList.add('bad');
        note.textContent = '上傳失敗：' + (e.status === 404 || e.status === 503 ? OFFLINE_MSG : e.message);
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
