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
import { S, api, esc, errText, alive, toast, chapterList, figureSource, sizeStyle } from './ctx.js';

/**
 * 點開的大圖（只在手機；桌機本來就是整排大圖）。
 *
 * owner 2026-09-19：「手機的圖輯 點開跳大圖」—— 兩欄的縮圖只有 170px 寬，
 * 書裡的地圖與表格那種圖在那個尺寸等於看不到。
 *
 * 🔴 大圖直接沿用縮圖已經換好的 blob（`_fillAssets` 帶 token 抓的）——
 * 再打一次 `<img src="/api/…">` 會 401，圖是私有的。
 */
let _lb = null;

const _figs = () => [...(S.root ? S.root.querySelectorAll('.kb-gallery.all figure') : [])];

export function openBigImage(img) {
    const i = _figs().indexOf(img.closest('figure'));
    if (i < 0) return;
    if (!_lb) {
        _lb = document.createElement('div');
        _lb.className = 'kb-lb';
        _lb.innerHTML = `<span class="n-of"></span><img alt="">
            <button type="button" class="nav p" aria-label="上一張">\u2039</button>
            <button type="button" class="nav n" aria-label="下一張">\u203a</button>
            <button type="button" class="x" aria-label="關閉">\u00d7</button>
            <div class="cap"></div>`;
        _lb.addEventListener('click', (ev) => {
            if (ev.target.closest('.nav.p')) { stepBigImage(-1); return; }
            if (ev.target.closest('.nav.n')) { stepBigImage(1); return; }
            if (!ev.target.closest('img')) closeBigImage();
        });
        document.body.appendChild(_lb);
        document.addEventListener('keydown', _lbKey);
    }
    _lb.dataset.i = String(i);
    _paintBigImage();
    document.body.style.overflow = 'hidden';
}

function _paintBigImage() {
    const figs = _figs();
    const i = Number(_lb.dataset.i) || 0;
    const fig = figs[i];
    if (!fig) { closeBigImage(); return; }
    const src = fig.querySelector('img');
    _lb.querySelector('img').src = src.currentSrc || src.src;
    _lb.querySelector('.cap').innerHTML = fig.querySelector('figcaption').innerHTML;
    _lb.querySelector('.n-of').textContent = `${i + 1} / ${figs.length}`;
    _lb.querySelector('.nav.p').disabled = i <= 0;
    _lb.querySelector('.nav.n').disabled = i >= figs.length - 1;
}

export function stepBigImage(d) {
    if (!_lb) return;
    const i = (Number(_lb.dataset.i) || 0) + d;
    if (i < 0 || i >= _figs().length) return;
    _lb.dataset.i = String(i);
    _paintBigImage();
}

export function closeBigImage() {
    if (_lb) _lb.remove();
    _lb = null;
    document.removeEventListener('keydown', _lbKey);
    document.body.style.overflow = '';
}

function _lbKey(ev) {
    if (!_lb) return;
    if (ev.key === 'Escape') closeBigImage();
    else if (ev.key === 'ArrowLeft') stepBigImage(-1);
    else if (ev.key === 'ArrowRight') stepBigImage(1);
}

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
        <img data-md-src="assets/${esc(a.name)}" alt="${esc(a.caption || '')}" loading="lazy"${sizeStyle(a)}>
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
