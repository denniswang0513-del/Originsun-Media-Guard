/**
 * chat.js — 知識庫書頁「討論」分頁：泡泡列＋輸入框、送出後每 1 秒輪詢（partial 串流、排隊／處理中）、
 * 「存成結論」（選取文字優先，否則整則）、「整理結論」（每 2 秒抓結論，變長了就停，最多 20 次）。
 *
 * 輪詢都有終點：回覆到／放棄（10 分鐘）／書關掉／換書；分頁在背景（document.hidden）那一拍跳過。
 * 重畫分頁走 nav.renderPane（book.js 在 mount 時填），這支不 import book.js（避免循環）。
 */
import { mdToHtml } from '../shared/md-lite.js';
import { waitingText } from '../shared/quote-wait.js';
import { S, nav, CHAT_POLL_MS, CHAT_GIVE_UP_MS, CONCLUDE_POLL_MS, CONCLUDE_MAX_TRIES, CHAT_TYPICAL,
         api, esc, errText, fmtWhen, alive, toast, normBook, CONCLUSION_MARK } from './ctx.js';

const _renderPane = () => { if (nav.renderPane) nav.renderPane(); };

export function chatHtml() {
    _captureConcEdit();
    const msgs = (S.chat || []).map((m, i) => {
        const me = m.role === 'user';
        return `<div class="kb-msg ${me ? 'me' : 'ai'}">
            <div class="who">${me ? '你' : 'AI'}${m.at ? ` · ${esc(fmtWhen(m.at))}` : ''}</div>
            <div class="body ${me ? '' : 'kb-md'}" data-idx="${i}">${me ? esc(m.text) : mdToHtml(m.text)}</div>
            ${me ? '' : _concHtml(i)}
        </div>`;
    }).join('');
    const waiting = S.wait ? `<div class="kb-msg ai" id="kb-wait"><div class="who">AI</div>
        <div class="body">${S.wait.partial ? `<span class="stream" id="kb-stream">${esc(S.wait.partial)}</span><span class="caret"></span>`
            : `<span class="wait" id="kb-tick">${esc(waitingText(Date.now() - S.wait.since, S.wait.stage, CHAT_TYPICAL))}</span>`}</div></div>` : '';
    const empty = !msgs && !S.wait ? `<div class="kb-empty">還沒有討論。${S.book.status === 'compiled' ? '問它這本書的核心規則怎麼套在你身上。' : '這本書還沒編譯，AI 只看得到你的筆記與結論；先按「讀這本書」比較有料。'}</div>` : '';
    return `<div class="kb-chat" id="kb-chat-log">${empty}${msgs}${waiting}</div>
        <div class="kb-chat-foot">
            <div class="row wide-only">
                <button type="button" class="kb-btn" data-kact="conclude" ${S.wait || !(S.chat || []).length ? 'disabled' : ''}>整理結論</button>
                <span class="note" id="kb-conclude-note">${S.book.concluding ? '上一次的「整理結論」還在跑，稍後到「結論」分頁看' : '把這段討論收成幾條原則，追加到「結論」'}</span>
            </div>
            <div class="row send">
                <textarea id="kb-chat-text" class="kb-textarea chat" placeholder="跟它討論這本書怎麼用在你身上" ${S.wait ? 'disabled' : ''}></textarea>
                <button type="button" class="kb-btn pri" data-kact="send" ${S.wait ? 'disabled' : ''}>送出</button>
            </div>
        </div>`;
}

export function scrollChat() {
    const log = S.root.querySelector('#kb-chat-log');
    if (log) log.scrollTop = log.scrollHeight;
}

export async function loadChat() {
    if (!S.book) return;
    const bookId = S.book.id;
    try {
        const d = await api(`/${encodeURIComponent(bookId)}/chat`);
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.chat = d.chat || [];
        // 進來時 AI 正在回（上一次送出後切走了）：接著等
        if (d.stage === 'queued' || d.stage === 'running' || d.partial) {
            S.wait = { since: Date.now(), stage: d.stage || '', partial: d.partial || '', expect: S.chat.length + 1 };
            _startChatPoll();
        }
        if (d.concluding) S.book.concluding = true;   // 上一次按的「整理結論」還在跑
    } catch (e) {
        if (!alive()) return;
        toast('討論載入失敗：' + errText(e), true);
    }
    if (S.pane === 'chat') _renderPane();
}

export async function send() {
    const ta = S.root.querySelector('#kb-chat-text');
    const text = ((ta && ta.value) || '').trim();
    if (!text || S.wait || !S.book) return;
    const expect = (S.chat || []).length + 2;    // 我這則 ＋ AI 那則
    S.wait = { since: Date.now(), stage: '', partial: '', expect };
    S.chat = [...S.chat, { role: 'user', text, at: '' }];
    _renderPane();
    try {
        const r = await api(`/${encodeURIComponent(S.book.id)}/chat`, { method: 'POST', body: { text } });
        if (!alive() || !S.book) return;
        if (r && Array.isArray(r.chat)) S.chat = r.chat;
        _startChatPoll();
    } catch (e) {
        if (!alive()) return;
        S.wait = null;
        S.chat = S.chat.slice(0, -1);
        _renderPane();
        const ta2 = S.root.querySelector('#kb-chat-text');
        if (ta2) ta2.value = text;
        toast('送出失敗：' + errText(e), true);
    }
}

function _startChatPoll() {
    clearInterval(S.chatTimer);
    const bookId = S.book.id;
    const t0 = Date.now();
    S.chatTimer = setInterval(async () => {
        if (!alive() || !S.book || S.book.id !== bookId || !S.wait) { clearInterval(S.chatTimer); S.chatTimer = null; return; }
        if (Date.now() - t0 > CHAT_GIVE_UP_MS) {
            clearInterval(S.chatTimer); S.chatTimer = null; S.wait = null;
            if (S.pane === 'chat') _renderPane();
            toast('AI 沒有在時間內回覆（可能在排隊、或主控主機沒登入 claude）', true);
            return;
        }
        if (document.hidden) return;
        let d;
        try { d = await api(`/${encodeURIComponent(bookId)}/chat`); } catch (_) { return; }
        if (!alive() || !S.book || S.book.id !== bookId || !S.wait) return;
        if ((d.chat || []).length >= S.wait.expect) {
            clearInterval(S.chatTimer); S.chatTimer = null;
            S.chat = d.chat || []; S.wait = null;
            if (S.pane === 'chat') _renderPane();
            return;
        }
        if (typeof d.stage === 'string') S.wait.stage = d.stage;
        if (typeof d.partial === 'string' && d.partial !== S.wait.partial) {
            S.wait.partial = d.partial;
            const grow = S.root.querySelector('#kb-stream');
            if (grow) { grow.textContent = S.wait.partial; scrollChat(); }
            else if (S.pane === 'chat') _renderPane();
        } else {
            const tick = S.root.querySelector('#kb-tick');
            if (tick) tick.textContent = waitingText(Date.now() - S.wait.since, S.wait.stage, CHAT_TYPICAL);
        }
    }, CHAT_POLL_MS);
}

/** 「可存成結論：」之後的那幾行（提示要求 AI 這樣收尾）。沒有那段就回空。 */
function _pickLines(text) {
    const i = String(text || '').indexOf(CONCLUSION_MARK);
    if (i === -1) return '';
    return String(text).slice(i + CONCLUSION_MARK.length).split('\n')
        .map((ln) => ln.trim().replace(/^[-*\u2022]\s*/, '').trim())
        .filter(Boolean).map((ln) => '- ' + ln).join('\n');
}

/** 那一則泡泡下面的東西：平常是一顆「存成結論」，開著編輯時是一格加兩顆鈕。 */
function _concHtml(i) {
    if (!S.concEdit || S.concEdit.idx !== i) {
        return `<div class="act"><button type="button" data-kact="save-conclusion" data-idx="${i}">存成結論</button></div>`;
    }
    return `<div class="kb-conc-edit">
        <div class="hint">改成你自己的話再存。結論是給顧問與以後的討論優先讀的，越短越好。</div>
        <textarea class="kb-textarea" id="kb-conc-text">${esc(S.concEdit.text)}</textarea>
        <div class="act">
            <button type="button" class="kb-btn" data-kact="conc-save" data-idx="${i}">存進結論</button>
            <button type="button" class="kb-btn ghost" data-kact="conc-cancel">取消</button>
        </div>
    </div>`;
}

/** 重畫之前先把他正在打的字收回 S，不然輪詢一跳就整段不見。 */
function _captureConcEdit() {
    if (!S.concEdit || !S.root) return;
    const ta = S.root.querySelector('#kb-conc-text');
    if (ta) S.concEdit.text = ta.value;
}

/** 按「存成結論」：先開一格讓他改。
 *  2026-09-18 之前是按下去就把整則原封不動塞進結論 —— AI 一則常常五六百字，
 *  結論檔很快就變成第二份聊天紀錄，反而讓顧問每次要讀的東西越來越長。
 *  預設帶「可存成結論：」那幾行，沒有那段才帶整則。 */
export function openConclusionEdit(idx) {
    const m = (S.chat || [])[idx];
    if (!m || !S.book) return;
    const text = _pickLines(m.text) || (m.text || '').trim();
    if (!text) return;
    S.concEdit = { idx, text };
    _renderPane();
    const ta = S.root && S.root.querySelector('#kb-conc-text');
    if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

export function cancelConclusionEdit() {
    S.concEdit = null;
    _renderPane();
}

/** 存進結論（他改過的那一格）。 */
export async function saveConclusion(idx) {
    if (!S.book || !S.concEdit || S.concEdit.idx !== idx) return;
    _captureConcEdit();
    const text = (S.concEdit.text || '').trim();
    if (!text) { toast('內容是空的', true); return; }
    const bookId = S.book.id;
    S.concEdit = null;
    _renderPane();
    try {
        const r = await api(`/${encodeURIComponent(bookId)}/conclusions`, { method: 'POST', body: { text } });
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.book.has_conclusion = true;
        toast('已加進結論');
        // 後端追加了一段 `## 日期` 並把整份結論回來；沒回就下次切到結論分頁時重抓
        if (r && typeof r.conclusion === 'string') S.book.conclusion = r.conclusion;
        else if (nav.refetchBook) nav.refetchBook();
    } catch (e) {
        if (!alive()) return;
        toast('存結論失敗：' + errText(e), true);
    }
}

export async function conclude() {
    if (!S.book || S.concludeTimer) return;
    const note = S.root.querySelector('#kb-conclude-note');
    const bookId = S.book.id;
    const before = (S.book.conclusion || '').length;
    try {
        await api(`/${encodeURIComponent(bookId)}/conclude`, { method: 'POST', body: {} });
    } catch (e) {
        if (!alive()) return;
        toast('整理結論失敗：' + errText(e), true);
        return;
    }
    if (!alive()) return;
    const t0 = Date.now();
    let tries = 0;
    if (note) note.textContent = '整理中… 0 秒';
    S.concludeTimer = setInterval(async () => {
        if (!alive() || !S.book || S.book.id !== bookId) { clearInterval(S.concludeTimer); S.concludeTimer = null; return; }
        if (document.hidden) return;
        tries += 1;
        const n = S.root.querySelector('#kb-conclude-note');
        if (n) n.textContent = `整理中… ${Math.floor((Date.now() - t0) / 1000)} 秒`;
        let d;
        try { d = await api(`/${encodeURIComponent(bookId)}`); } catch (_) { d = null; }
        if (!alive() || !S.book || S.book.id !== bookId) return;
        const grown = d && typeof d.conclusion === 'string' && d.conclusion.length > before;
        if (grown || tries >= CONCLUDE_MAX_TRIES) {
            clearInterval(S.concludeTimer); S.concludeTimer = null;
            if (d) S.book = normBook(Object.assign(S.book, d));
            const n2 = S.root.querySelector('#kb-conclude-note');
            if (grown) {
                if (n2) n2.innerHTML = '已加進結論 <button type="button" class="kb-link" data-kact="pane" data-pane="conclusion">看結論</button>';
                toast('結論已整理好');
            } else if (n2) {
                n2.textContent = '還沒看到新結論（可能還在跑；稍後到「結論」分頁看）';
            }
        }
    }, CONCLUDE_POLL_MS);
}
