/**
 * podcast.js — 每一章一集 podcast（owner 2026-09-19：「每一章一個podcast」）。
 *
 * 章節分頁：清單上每一章標「已有 / 產生中 / 還沒有」，點進單章有播放器與「產生這一章」；
 * 章節清單最上面有「整本都產」。
 *
 * 產一集要十幾分鐘（兩趟 claude 寫稿 ＋ 兩百多句配音），所以按下去是**背景工作**，
 * 這裡每 5 秒問一次進度；有任何一集在跑就繼續問，全部停了就不問了。
 *
 * 🔴 音檔是私有的：`<audio src="/api/…">` 送不了 Authorization，所以跟圖一樣
 *    帶 token 抓成 blob 再餵給 player（同 book.js 的 `_fillAssets`）。
 *
 * 只 import ctx（葉節點）；同 gallery.js／extend.js 的規矩。
 */
import { API, S, nav, api, esc, errText, alive, toast } from './ctx.js';
import { bearerHeader } from '../shared/utils.js';

const POLL_MS = 5000;

/** 這本書的 podcast 狀態（章節分頁畫的時候要）。 */
export async function loadPodcast() {
    if (!S.book) return;
    const bookId = S.book.id;
    try {
        const d = await api(`/${encodeURIComponent(bookId)}/podcast`);
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.podcast = d || null;
    } catch (_) {
        S.podcast = null;                 // 讀不到就不畫那幾顆鈕，不要用錯誤蓋掉章節
        return;
    }
    // 這支是葉節點：回頭叫 book.js 一律走 ctx 的 nav 表（同 chat.js 的規矩）
    if (S.pane === 'chapters' && nav.renderPane) nav.renderPane();
    if ((S.podcast.making || 0) > 0) watchPodcast();
}

/** 有集數在產的時候每 5 秒問一次。 */
export function watchPodcast() {
    if (S.podcastTimer) return;
    S.podcastTimer = setInterval(async () => {
        if (!alive() || !S.book) { stopPodcastWatch(); return; }
        const bookId = S.book.id;
        let d;
        try {
            d = await api(`/${encodeURIComponent(bookId)}/podcast`);
        } catch (_) { return; }
        if (!alive() || !S.book || S.book.id !== bookId) { stopPodcastWatch(); return; }
        S.podcast = d;
        if (S.pane === 'chapters' && nav.renderPane) nav.renderPane();
        if (!(d.making || 0)) stopPodcastWatch();
    }, POLL_MS);
}

export function stopPodcastWatch() {
    if (S.podcastTimer) clearInterval(S.podcastTimer);
    S.podcastTimer = null;
}

const _row = (n) => ((S.podcast && S.podcast.items) || []).find((x) => String(x.n) === String(n));

/** 章節清單上每一章右邊那一小塊（已有幾分鐘／產生中／還沒有）。 */
export function podcastTag(n) {
    const r = _row(n);
    if (!r) return '';
    if (r.stage) return `<span class="kb-pod-tag on">${esc(r.stage)}</span>`;
    if (r.has) return `<span class="kb-pod-tag">${r.minutes ? `${r.minutes} 分鐘` : 'podcast'}</span>`;
    return '';
}

/** 章節清單最上面：整本都產。 */
export function podcastAllHtml() {
    const p = S.podcast;
    if (!p || !p.total) return '';
    const left = p.total - p.done;
    return `<div class="kb-pod-all">
        <span class="note">podcast：${p.done}／${p.total} 章已經有了${p.making ? `　·　${p.making} 集正在產` : ''}</span>
        ${left && !p.making ? `<button type="button" class="kb-btn" data-kact="pod-all">把剩下的 ${left} 章都產出來</button>` : ''}
    </div>`;
}

/** 單章的播放器（有就播、沒有就給一顆產生的鈕）。 */
export function podcastHtml(n) {
    const r = _row(n);
    if (!r) return '';
    if (r.stage) {
        return `<div class="kb-pod"><div class="h">Podcast</div>
            <div class="note">${esc(r.stage)}　·　寫稿到配音大概十幾分鐘，可以先去做別的事</div></div>`;
    }
    if (!r.has) {
        return `<div class="kb-pod"><div class="h">Podcast</div>
            <div class="note">把這一章做成兩個人對談的 podcast（約 20 分鐘）。</div>
            <button type="button" class="kb-btn" data-kact="pod-one" data-n="${esc(String(n))}">產生這一章</button>
        </div>`;
    }
    return `<div class="kb-pod"><div class="h">Podcast<span class="m">${r.minutes ? `${r.minutes} 分鐘` : ''}${r.mb ? `　${r.mb} MB` : ''}</span></div>
        <audio controls preload="none" data-pod="${esc(String(n))}"></audio>
        <div class="row">
            <button type="button" class="kb-link" data-kact="pod-script" data-n="${esc(String(n))}">看逐字稿</button>
            <button type="button" class="kb-link" data-kact="pod-one" data-n="${esc(String(n))}">重產</button>
        </div>
        <div class="kb-pod-script" id="kb-pod-script-${esc(String(n))}"></div>
    </div>`;
}

/** 🔴 音檔是私有的：帶 token 抓成 blob 再餵給 <audio>（`src` 送不了 Authorization）。 */
export async function fillPodcastAudio(host) {
    const el = host && host.querySelector('audio[data-pod]');
    if (!el || el.src || !S.book) return;
    const bookId = S.book.id;
    const n = el.dataset.pod;
    try {
        const r = await fetch(`${API}/${encodeURIComponent(bookId)}/podcast/${encodeURIComponent(n)}.mp3`,
            { headers: bearerHeader() });
        if (!r.ok) return;
        const url = URL.createObjectURL(await r.blob());
        if (!alive() || !S.book || S.book.id !== bookId) { URL.revokeObjectURL(url); return; }
        (S.assetUrls = S.assetUrls || []).push(url);     // 重畫時跟圖一起收回去
        el.src = url;
    } catch (_) { /* 拿不到就留一個空的 player，不要讓整章壞掉 */ }
}

/** 產這一章（重產會蓋掉舊的）。 */
export async function makePodcast(n) {
    if (!S.book) return;
    const r = _row(n);
    if (r && r.has && !confirm(`第 ${n} 章已經有 podcast 了，重產會蓋掉舊的。要繼續嗎？`)) return;
    try {
        await api(`/${encodeURIComponent(S.book.id)}/podcast/${encodeURIComponent(n)}`, { method: 'POST', body: {} });
        toast('開始產了，大概十幾分鐘');
        await loadPodcast();
        watchPodcast();
    } catch (e) { toast('產不了：' + errText(e), true); }
}

/** 整本把還沒有的都產出來。 */
export async function makeAllPodcast() {
    if (!S.book || !S.podcast) return;
    const left = S.podcast.total - S.podcast.done;
    if (!confirm(`要把剩下的 ${left} 章都產出來嗎？一章十幾分鐘，會在背景一章一章跑。`)) return;
    try {
        const d = await api(`/${encodeURIComponent(S.book.id)}/podcast`, { method: 'POST', body: {} });
        toast(`排了 ${d.queued} 集`);
        await loadPodcast();
        watchPodcast();
    } catch (e) { toast('排不了：' + errText(e), true); }
}

/** 逐字稿（點了才抓）。 */
export async function showScript(n) {
    if (!S.book) return;
    const box = S.root.querySelector(`#kb-pod-script-${CSS.escape(String(n))}`);
    if (!box) return;
    if (box.innerHTML) { box.innerHTML = ''; return; }        // 再點一次收起來
    box.innerHTML = '<div class="note">載入中…</div>';
    try {
        const d = await api(`/${encodeURIComponent(S.book.id)}/podcast/${encodeURIComponent(n)}/script`);
        box.innerHTML = (d.items || []).map((x) =>
            `<p><b>${esc(x.who)}</b>　${esc(x.text)}</p>`).join('') || '<div class="note">沒有逐字稿</div>';
    } catch (e) { box.innerHTML = `<div class="note bad">${esc(errText(e))}</div>`; }
}
