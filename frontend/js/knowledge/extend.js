/**
 * extend.js — 書頁的「延伸」分頁（研究助理；docs/KNOWLEDGE_BASE_PLAN.md §9）。
 *
 * 定期或手動去網路上找跟這本書有關的新研究，一則一行存在書資料夾的 `延伸.md`。
 * 這裡只管畫面：清單、兩顆「去找新的／收錄這篇」、每則的「有用／沒用」，以及在找的時候輪詢。
 *
 * 🔴 每一則的文字都來自**網頁**（不可信輸入）。畫面上一律 `esc()` 之後才放進 HTML，
 *    連結一律 `rel="noopener noreferrer"`，而且只認 http／https —— 別的協定只顯示文字不做成連結。
 *
 * 只 import ctx（葉節點）；要回頭叫 book.js 走 `nav`（同 chat.js 的規矩）。
 */
import { S, api, esc, errText, stageText, alive, toast } from './ctx.js';

export const EXTEND_POLL_MS = 3000;

const RATING_LABEL = { useful: '有用', useless: '沒用' };

/** 只有 http／https 才做成連結（網址是網頁給的，javascript: 之類一律不碰）。 */
function _safeHref(url) {
    return /^https?:\/\//i.test(String(url || '')) ? String(url) : '';
}

function _itemHtml(it) {
    const href = _safeHref(it.url);
    const title = esc(it.title_zh || it.title_original || '（無標題）');
    const head = href
        ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${title}</a>`
        : `<span class="bad">${title}</span>`;
    const lang = String(it.lang || '');
    const foreign = lang && !lang.toLowerCase().startsWith('zh');
    const meta = [it.source, it.published, it.chapter_guess].filter(Boolean).map(esc).join('｜');
    const rated = it.rating || '';
    const btn = (v) => `<button type="button" data-kact="extend-rate" data-n="${esc(it.n)}" data-v="${rated === v ? '' : v}"
        class="${rated === v ? 'on' : ''}">${RATING_LABEL[v]}</button>`;
    return `
        <li class="kb-ext ${rated === 'useless' ? 'off' : ''}">
            <div class="t">${head}${foreign ? ` <span class="lang">${esc(lang)}</span>` : ''}</div>
            ${foreign && it.title_original ? `<div class="orig">${esc(it.title_original)}</div>` : ''}
            ${meta ? `<div class="meta">${meta}</div>` : ''}
            ${it.summary_zh ? `<div class="sum">${esc(it.summary_zh)}</div>` : ''}
            ${it.why_it_matters ? `<div class="why">為什麼值得看：${esc(it.why_it_matters)}</div>` : ''}
            <div class="act">${btn('useful')}${btn('useless')}<span class="on-date">${esc(it.date || '')}</span></div>
        </li>`;
}

export function extendHtml() {
    const items = (S.extend || []).slice().reverse();      // 新的放前面
    const stage = stageText(S.extendStage);
    const busy = stage ? ' disabled' : '';
    return `
        <div class="kb-ext-head">
            <div class="n">${items.length ? `${items.length} 則` : '還沒有'}</div>
            <div class="b">
                <button type="button" class="kb-btn" data-kact="extend-run"${busy}>去找新的</button>
                <button type="button" class="kb-btn ghost" data-kact="extend-one"${busy}>收錄這篇</button>
            </div>
        </div>
        <div class="note">網路上跟這本書有關的東西，附出處、翻譯與摘要。<b>不是作者說的</b>，是別人後來寫的。
            按「沒用」之後，那一則下次討論就不會再帶進去。</div>
        ${stage ? '<div class="kb-ext-busy">正在找資料…這會跑幾分鐘，可以先去做別的事，回來再看。</div>'
        : (S.extendNote ? `<div class="kb-ext-busy">${esc(S.extendNote)}</div>` : '')}
        ${items.length ? `<ul class="kb-ext-list">${items.map(_itemHtml).join('')}</ul>`
        : '<div class="kb-empty">還沒找過。按「去找新的」讓它依這本書的主題去搜，或用「收錄這篇」貼一個網址進來。</div>'}`;
}

function _rerender() {
    const pane = S.root && S.root.querySelector('#kb-pane');
    if (pane && S.pane === 'extend') pane.innerHTML = extendHtml();
}

/** 進「延伸」分頁時抓一次；正在找就順便開始輪詢。 */
export async function loadExtend() {
    if (!S.book) return;
    try {
        const d = await api(`/${encodeURIComponent(S.book.id)}/extend`);
        if (!alive() || !S.book) return;
        S.extend = (d && Array.isArray(d.items)) ? d.items : [];
        S.extendStage = (d && d.stage) || '';
        S.extendNote = (d && d.note) || '';
        _rerender();
        if (S.extendStage) watchExtend();
    } catch (e) {
        if (alive()) toast('讀延伸失敗：' + errText(e), true);
    }
}

/** 找資料要跑幾分鐘：每 3 秒問一次，跑完自動重畫並把新的幾則帶出來。 */
export function watchExtend() {
    clearInterval(S.extendTimer);
    S.extendTimer = setInterval(async () => {
        if (!alive() || !S.book) { clearInterval(S.extendTimer); S.extendTimer = null; return; }
        let d;
        try {
            d = await api(`/${encodeURIComponent(S.book.id)}/extend`);
        } catch (e) {
            return;                                        // 網路抖一下不算結束，下一輪再問
        }
        if (!alive() || !S.book) return;
        const before = (S.extend || []).length;
        S.extend = (d && Array.isArray(d.items)) ? d.items : [];
        S.extendStage = (d && d.stage) || '';
        S.extendNote = (d && d.note) || '';
        _rerender();
        if (!S.extendStage) {
            clearInterval(S.extendTimer); S.extendTimer = null;
            const got = S.extend.length - before;
            toast(got > 0 ? `找到 ${got} 則新的` : '這一輪沒有找到新的東西');
        }
    }, EXTEND_POLL_MS);
}

/** 去找新的（不給網址＝它自己依這本書的主題搜）／收錄這篇（給網址）。 */
export async function runExtend(url = '') {
    if (!S.book || S.extendStage) return;
    S.extendStage = 'queued';
    _rerender();
    try {
        await api(`/${encodeURIComponent(S.book.id)}/extend`, { method: 'POST', body: { url } });
        if (!alive()) return;
        watchExtend();
    } catch (e) {
        if (!alive()) return;
        S.extendStage = '';
        _rerender();
        toast((url ? '收錄失敗：' : '找資料失敗：') + errText(e), true);
    }
}

export function collectOne() {
    const url = window.prompt('貼一個網址，它會打開來讀完，整理成中文摘要收進這本書的延伸。');
    if (url && url.trim()) runExtend(url.trim());
}

/** 評「有用／沒用」；`n` 是檔內流水號（不是第幾筆）。再按一次同一顆＝收回評分。 */
export async function rateExtend(n, rating) {
    if (!S.book) return;
    const hit = (S.extend || []).find((i) => String(i.n) === String(n));
    if (!hit) return;
    const before = hit.rating || '';
    hit.rating = rating;                                   // 先動畫面，失敗再改回來
    _rerender();
    try {
        await api(`/${encodeURIComponent(S.book.id)}/extend/${encodeURIComponent(n)}`,
            { method: 'PUT', body: { rating } });
    } catch (e) {
        if (!alive()) return;
        hit.rating = before;
        _rerender();
        toast('存評分失敗：' + errText(e), true);
    }
}
