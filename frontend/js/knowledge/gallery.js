/**
 * gallery.js — 書頁的「圖輯」分頁（owner 2026-09-19：「同時在 tab 處開一個圖輯，
 * 把書裡抽出來的圖片全部集合一處」）。
 *
 * 章末的相簿（book.js 的 `_galleryHtml`）只列那一章的；這一頁是整本書的總覽，照章分組，
 * 每張附圖說與頁數。圖說優先用 AI 整理過的那句（編譯時寫進 `meta.asset_captions`），
 * 沒有才用從 PDF 刮下來的原文。
 *
 * 圖跟章節裡一樣是 `<img data-md-src>`，由 book.js 的 `_fillAssets` 帶 token 換成 blob
 * —— `<img src>` 送不了 Authorization。
 *
 * 只 import ctx（葉節點）。
 */
import { S, api, esc, errText, alive, toast, chapterList, figureSource } from './ctx.js';

/** 照章分組：`[[章標題, [圖…]], …]`，落在任何一章之外的收在最後。 */
function _byChapter(assets) {
    const chs = chapterList(S.book || {});
    const groups = [];
    const used = new Set();
    for (const c of chs) {
        const a = Number(c.start_page) || 0;
        const b = Number(c.end_page) || 0;
        if (!a && !b) continue;
        const rows = assets.filter((x) => x.page >= a && x.page <= b);
        rows.forEach((x) => used.add(x.name));
        if (rows.length) groups.push([`第 ${c.n} 章 ${c.title || ''}（p.${a}–${b}）`, rows]);
    }
    const rest = assets.filter((x) => !used.has(x.name));
    if (rest.length) groups.push([chs.length ? '其他頁' : '全書', rest]);
    return groups;
}

function _figure(a) {
    return `<figure>
        <img data-md-src="assets/${esc(a.name)}" alt="${esc(a.caption || '')}" loading="lazy">
        <figcaption>${a.caption ? esc(a.caption) : '（原書沒有圖說）'}
            <span class="src">${esc(figureSource(S.book, a.page))}</span></figcaption>
    </figure>`;
}

export function galleryHtml() {
    const assets = S.assets || [];
    if (S.assetsLoading) return '<div class="kb-empty">載入中…</div>';
    if (!assets.length) {
        return `<div class="kb-empty">這本書沒有抽到圖。<br>
            純文字的書本來就沒有；整頁掃描的書我們也刻意不收（那種圖沒有上下文）。
            2026-09-18 之前加進來的書，按一次「重新讀這本書」就會補抽。</div>`;
    }
    return `<div class="kb-gallery all">
        <h4>整本書的圖（${assets.length}）<span class="why">從 PDF 原書抽出來的，括號是頁數</span></h4>
        ${_byChapter(assets).map(([title, rows]) => `
            <div class="grp"><h5>${esc(title)}<span class="n">${rows.length}</span></h5>
            ${rows.map(_figure).join('')}</div>`).join('')}
    </div>`;
}

export async function loadGallery() {
    if (!S.book) return;
    const bookId = S.book.id;
    S.assetsLoading = true;
    try {
        const rows = await api(`/${encodeURIComponent(bookId)}/assets`);
        if (!alive() || !S.book || S.book.id !== bookId) return;
        S.assets = Array.isArray(rows) ? rows : [];
    } catch (e) {
        if (alive()) toast('讀圖輯失敗：' + errText(e), true);
        S.assets = [];
    } finally {
        S.assetsLoading = false;
    }
}
