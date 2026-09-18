/**
 * extend.js — 書頁的「延伸」分頁（研究助理；docs/KNOWLEDGE_BASE_PLAN.md §9）。
 *
 * 定期或手動去網路上找跟這本書有關的新研究，一則一行存在書資料夾的 `延伸.md`。
 * 這裡只管畫面：清單、兩顆「去找新的／收錄這篇」、每則的「有用／沒用」、研究方向那一格、
 * 每週自動找的開關，以及在找的時候輪詢。
 *
 * 「研究方向」是他自己寫的一兩句，權重排在結論之前（core/knowledge_logic.extend_prompt）。
 * 「有用／沒用」除了決定討論要不要帶那一則，也會當成下一次搜尋的正反例。
 *
 * 「每週自動找」有兩道開關，兩道都開才會自動跑（services/knowledge_watch.py）：
 *   這本書的 `meta.watch`（誰都能改）＋ 全域的 `knowledge.watch.enabled`（只有管理員）。
 * 兩道都畫在這裡 —— 「要去設定頁打開」對不寫程式的人等於這個功能永遠開不起來。
 *
 * 🔴 每一則的文字都來自**網頁**（不可信輸入）。畫面上一律 `esc()` 之後才放進 HTML，
 *    連結一律 `rel="noopener noreferrer"`，而且只認 http／https —— 別的協定只顯示文字不做成連結。
 *
 * 只 import ctx（葉節點）；要回頭叫 book.js 走 `nav`（同 chat.js 的規矩）。
 */
import { S, api, esc, errText, stageText, alive, toast } from './ctx.js';

export const EXTEND_POLL_MS = 3000;

const RATING_LABEL = { useful: '有用', useless: '沒用' };
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

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
        ${_focusHtml()}
        <label class="kb-watch"><input type="checkbox" data-kact="watch"${S.book && S.book.watch ? ' checked' : ''}${busy}>
            這本書每週自動找一次</label>
        ${_globalWatchHtml()}
        ${stage ? '<div class="kb-ext-busy">正在找資料…這會跑幾分鐘，可以先去做別的事，回來再看。</div>'
        : (S.extendNote ? `<div class="kb-ext-busy">${esc(S.extendNote)}</div>` : '')}
        ${items.length ? `<ul class="kb-ext-list">${items.map(_itemHtml).join('')}</ul>`
        : '<div class="kb-empty">還沒找過。按「去找新的」讓它依這本書的主題去搜，或用「收錄這篇」貼一個網址進來。</div>'}`;
}

/** 研究方向：他用自己的話寫一兩句，比標籤精準得多（權重排在結論之前）。 */
function _focusHtml() {
    const cur = (S.book && S.book.focus) || '';
    if (!S.focusEdit) {
        return `<div class="kb-focus">
            <span class="v">${cur ? esc(cur) : '還沒指定方向 —— 它會照你的結論與筆記自己判斷。'}</span>
            <button type="button" class="kb-link" data-kact="focus-edit">${cur ? '改' : '指定方向'}</button>
        </div>`;
    }
    return `<div class="kb-focus editing">
        <div class="hint">用一兩句話說你要它往哪邊找，例如「多找台灣本地的稅務與勞健保實務，少找美股」。
            這段的權重比結論還高。留白＝讓它自己判斷。</div>
        <textarea class="kb-textarea" id="kb-focus-text" maxlength="300">${esc(cur)}</textarea>
        <div class="act">
            <button type="button" class="kb-btn" data-kact="focus-save">存起來</button>
            <button type="button" class="kb-btn ghost" data-kact="focus-cancel">取消</button>
        </div>
    </div>`;
}

export function editFocus(on) {
    if (S.focusEdit && !on) S.focusEdit = false;
    else S.focusEdit = !!on;
    _rerender();
    const ta = S.root && S.root.querySelector('#kb-focus-text');
    if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

export async function saveFocus() {
    const ta = S.root && S.root.querySelector('#kb-focus-text');
    if (!ta || !S.book) return;
    const focus = ta.value.trim();
    const bookId = S.book.id;
    const before = S.book.focus || '';
    await _optimistic({
        set: () => { S.book.focus = focus; S.focusEdit = false; },
        undo: () => { S.book.focus = before; },
        call: () => api(`/${encodeURIComponent(bookId)}`, { method: 'PUT', body: { focus } }),
        onOk: (b) => { if (b && typeof b.focus === 'string') S.book.focus = b.focus; },   // 後端會截到 300 字
        ok: focus ? '下次找的時候會照這個方向' : '已清掉，之後它自己判斷',
        fail: '存不起來：',
    });
}

/** 先動畫面再存、存不起來就改回去 —— 研究方向、兩道開關、評分四個地方同一套。
 *  `set`／`undo` 改 S；`call` 打 API；`onOk(res)` 成功後要不要拿後端的值再改一次（可省）；
 *  `ok` 成功的 toast（可省）；`fail` 失敗 toast 的前綴。切走這本書之後回來的結果一律丟掉。 */
async function _optimistic({ set, undo, call, onOk, ok, fail }) {
    const bookId = S.book ? S.book.id : '';
    const stale = () => !alive() || (bookId && (!S.book || S.book.id !== bookId));
    set();
    _rerender();
    try {
        const r = await call();
        if (stale()) return;
        if (onOk) onOk(r);
        _rerender();
        if (ok) toast(ok);
    } catch (e) {
        if (stale()) return;
        undo();
        _rerender();
        toast(fail + errText(e), true);
    }
}

/** 全域那一道（只有管理員看得到、也只有管理員改得動）。 */
function _globalWatchHtml() {
    const w = S.watchConf;
    if (!w) return '';
    const when = `每週${WEEKDAYS[w.weekday] || '日'} ${String(w.hour).padStart(2, '0')}:00`;
    if (!w.can_edit) {
        return w.enabled ? '' : '<div class="note sub">研究助理的總開關目前是關的，要請管理員打開。</div>';
    }
    return `<label class="kb-watch"><input type="checkbox" data-kact="watch-all"${w.enabled ? ' checked' : ''}>
        研究助理總開關（全部的書）<span class="sub">${esc(when)}，一本最多 8 則</span></label>`;
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
    if (S.watchConf) return;                           // 全域開關讀一次就好（一個 session 內不會變）
    try {
        const w = await api('/watch');
        if (!alive()) return;
        S.watchConf = w || null;
        _rerender();
    } catch (_) { /* 讀不到就不畫那一行，不要用錯誤蓋掉清單 */ }
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

/** 全域那一道：關著的話，每本書自己開了也不會動。 */
export async function toggleWatchAll(on) {
    const before = S.watchConf ? S.watchConf.enabled : false;
    await _optimistic({
        set: () => { if (S.watchConf) S.watchConf.enabled = !!on; },
        undo: () => { if (S.watchConf) S.watchConf.enabled = before; },
        call: () => api('/watch', { method: 'PUT', body: { enabled: !!on } }),
        onOk: (w) => { if (S.watchConf) S.watchConf = { ...S.watchConf, ...w }; },
        ok: on ? '研究助理開了' : '研究助理關了',
        fail: '改不動：',
    });
}

/** 每週自動找：這本書的開關（另一道是上面的全域開關）。 */
export async function toggleWatch(on) {
    if (!S.book) return;
    const bookId = S.book.id;
    const before = !!S.book.watch;
    await _optimistic({
        set: () => { S.book.watch = !!on; },
        undo: () => { S.book.watch = before; },
        call: () => api(`/${encodeURIComponent(bookId)}`, { method: 'PUT', body: { watch: !!on } }),
        ok: on ? '之後每週會自動找一次' : '已關掉每週自動找',
        fail: '改不動：',
    });
}

/** 評「有用／沒用」；`n` 是檔內流水號（不是第幾筆）。再按一次同一顆＝收回評分。 */
export async function rateExtend(n, rating) {
    if (!S.book) return;
    const hit = (S.extend || []).find((i) => String(i.n) === String(n));
    if (!hit) return;
    const before = hit.rating || '';
    const bookId = S.book.id;
    await _optimistic({
        set: () => { hit.rating = rating; },               // 先動畫面，失敗再改回來
        undo: () => { hit.rating = before; },
        call: () => api(`/${encodeURIComponent(bookId)}/extend/${encodeURIComponent(n)}`,
            { method: 'PUT', body: { rating } }),
        fail: '存評分失敗：',
    });
}
