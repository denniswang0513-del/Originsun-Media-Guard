/**
 * index.js — 知識庫獨立頁（/knowledge.html）的進入點（docs/KNOWLEDGE_BASE_PLAN.md §11）。
 *
 * owner 上傳 PDF → 主控主機把它編譯成骨架（SKILL.md／cheatsheet／glossary／patterns）＋每章 md →
 * 在這裡跟 AI 討論、把講定的原則「存成結論」；標了「財務」的書，顧問 agent 每次都先讀它的結論。
 * 另外有個研究助理（extend.js）：定期或手動去網路上找跟這本書有關的新研究，一則一行存 `延伸.md`。
 * 兩個畫面：書架（shelf.js）、書頁（book.js ＋ chat.js ＋ extend.js）；狀態與小工具在 ctx.js。桌機手機同一份（響應式）。
 *
 * 宿主契約：`mountKnowledge({ host, fetch, esc, toast })`
 *   fetch(path, {method, body}) → json；FormData 原樣送；!ok 丟 Error 帶 `.status`／`.detail`
 *   toast(msg, 'ok' | 'err')
 * 所有點擊走 .kb 上的一個委派（data-kact），重畫不會疊監聽；離開頁面（pagehide）把四條輪詢收掉。
 */
import { S, hooks, nav, stopTimers } from './ctx.js';
import { loadShelf, renderShelf, setTag, refreshShelfQuietly, createPending } from './shelf.js';
import {
    openBook, renderPane, refetchBook, switchPane, editTags, saveTags, saveDoc, openChapter, compile, rename, remove, toggleSheet, jumpToSection,
} from './book.js';
import { send, saveConclusion, openConclusionEdit, cancelConclusionEdit, conclude } from './chat.js';
import { loadReports, openReport, openFromHash, leaveReports } from './report.js';
import { editInfo, saveInfo, toggleShare, copyShare } from './info.js';
import { runExtend, collectOne, rateExtend, toggleWatch, toggleWatchAll,
    editFocus, saveFocus } from './extend.js';

export async function mountKnowledge({ host, fetch, toast }) {
    hooks.host = host; hooks.fetch = fetch; hooks.toast = toast;
    nav.openBook = openBook; nav.renderPane = renderPane; nav.refetchBook = refetchBook;
    nav.renderShelf = renderShelf;
    stopTimers();
    S.root = document.createElement('div');
    S.root.className = 'kb';
    S.root.innerHTML = '<div class="kb-top"><h2>知識庫</h2></div><div class="kb-loading">載入中…</div>';
    host.innerHTML = '';
    host.appendChild(S.root);
    S.root.addEventListener('click', _onClick);
    S.root.addEventListener('keydown', _onKey);
    window.addEventListener('pagehide', stopTimers, { once: true });
    // Discord 推的報告連結（#report/<id>）：貼進網址列或在同一頁換一份都要能開
    window.addEventListener('hashchange', () => { if (!S.book) openFromHash(); });
    await loadShelf();
    openFromHash();
}

/** 卡片是 div[role=button]：鍵盤 Enter／Space 也要能開 */
function _onKey(ev) {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const el = ev.target.closest('.kb-card[data-kact="open"], .kb-card[data-kact="report"]');
    if (!el) return;
    ev.preventDefault();
    if (el.dataset.kact === 'report') openReport(el.dataset.id);
    else openBook(el.dataset.id);
}

function _onClick(ev) {
    const el = ev.target.closest('[data-kact]');
    if (!el || !S.root.contains(el)) return;
    const act = el.dataset.kact;
    if (act === 'reload') { loadShelf(); return; }
    if (act === 'pick') { const f = S.root.querySelector('#kb-file'); if (f) f.click(); return; }
    if (act === 'new-pending') { createPending(); return; }
    if (act === 'tag') { setTag(el.dataset.tag); return; }
    if (act === 'open') { openBook(el.dataset.id); return; }
    if (act === 'back') { stopTimers(); leaveReports(); refreshShelfQuietly(); return; }
    if (act === 'reports') { stopTimers(); loadReports(); return; }
    if (act === 'report') { openReport(el.dataset.id); return; }
    if (act === 'pane') { toggleSheet(false); switchPane(el.dataset.pane); return; }
    if (act === 'more') { toggleSheet(true); return; }
    if (act === 'sheet-close') { toggleSheet(false); return; }
    if (act === 'conclude-from-sheet') { toggleSheet(false); switchPane('chat'); conclude(); return; }
    if (act === 'jump') { jumpToSection(el.dataset.i); return; }
    if (act === 'to-top') { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    if (act === 'extend-run') { runExtend(); return; }
    if (act === 'extend-one') { collectOne(); return; }
    if (act === 'extend-rate') { rateExtend(el.dataset.n, el.dataset.v); return; }
    if (act === 'watch') { toggleWatch(el.checked); return; }
    if (act === 'watch-all') { toggleWatchAll(el.checked); return; }
    if (act === 'focus-edit') { editFocus(true); return; }
    if (act === 'focus-cancel') { editFocus(false); return; }
    if (act === 'focus-save') { saveFocus(); return; }
    if (act === 'info-edit') { editInfo(true); return; }
    if (act === 'info-cancel') { editInfo(false); return; }
    if (act === 'info-save') { saveInfo(); return; }
    if (act === 'share-toggle') { toggleShare(el.checked); return; }
    if (act === 'share-copy') { copyShare(); return; }
    if (act === 'tags-edit') { toggleSheet(false); editTags(true); return; }
    if (act === 'tags-cancel') { editTags(false); return; }
    if (act === 'tags-save') { saveTags(el); return; }
    if (act === 'send') { send(); return; }
    if (act === 'save-conclusion') { openConclusionEdit(Number(el.dataset.idx)); return; }
    if (act === 'conc-save') { saveConclusion(Number(el.dataset.idx)); return; }
    if (act === 'conc-cancel') { cancelConclusionEdit(); return; }
    if (act === 'conclude') { conclude(); return; }
    if (act === 'doc-edit') { S.editing = true; renderPane(); return; }
    if (act === 'doc-cancel') { S.editing = false; renderPane(); return; }
    if (act === 'doc-save') { saveDoc(el.dataset.key, el); return; }
    if (act === 'chapter') { openChapter(el.dataset.n); return; }
    if (act === 'chapter-back') { S.chapter = null; renderPane(); return; }
    if (act === 'attach') { toggleSheet(false); const f = S.root.querySelector('#kb-book-file'); if (f) f.click(); return; }
    if (act === 'compile') { toggleSheet(false); compile(); return; }
    if (act === 'rename') { toggleSheet(false); rename(); return; }
    if (act === 'delete') { toggleSheet(false); remove(); }
}
