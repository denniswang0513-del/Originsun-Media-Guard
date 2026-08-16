/**
 * project-list.js — 專案清單（`/project.html` 沒帶參數時的落地畫面）
 *
 * 為什麼會有這一支：2026-08-15 把頁面主鍵從提案換成專案、網址也改成
 * `/project.html`，但「沒帶參數」還是落在**提案**清單 —— 網址說專案、內容說
 * 提案，而且專案頁根本沒有任何地方列得出「有哪些案子可以開」，只能從 CRM 詳情
 * 的「開啟專案頁 ↗」進去。owner 2026-08-15 選 A：預設給專案清單，提案清單移到
 * 頁內的切換。
 *
 * 🔴 **一個金額都不畫**。這頁的閘門比 CRM 寬（拍攝企劃的人進得來），而
 * `GET /crm/projects` 會回 contract_amount / amount_receivable 之類 ——
 * 沒有 money_view 時那些鍵會被後端刪掉（core/money.py），畫了就是 `$0` 謊報。
 * 這裡連欄位都不開：清單要回答的是「哪個案子」，不是「多少錢」。
 *
 * fetcher 由呼叫端注入（公開頁走它自己的 mfetch 包裝），理由同 staff-view。
 */

import { esc, ensureStyle } from '../../js/shared/dom.js';

const STYLE_ID = 'projlist-style';
const CSS = `
.pl-row{display:flex;align-items:center;gap:12px;padding:11px 14px;
  border-bottom:1px solid #eee;cursor:pointer;background:#fff;}
.pl-row:hover{background:#fafafa;}
.pl-row:focus-visible{outline:2px solid var(--red,#e03131);outline-offset:-2px;}
.pl-name{flex:1;min-width:0;font-size:14px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.pl-client{font-size:12px;color:#888;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;max-width:180px;}
.pl-stage{font-size:11px;padding:2px 10px;border-radius:2px;background:#f2f2f2;
  color:#555;white-space:nowrap;}
.pl-date{font-size:12px;color:#999;white-space:nowrap;}
.pl-empty{padding:28px 14px;color:#888;font-size:13px;text-align:center;}
@media (max-width:640px){ .pl-client,.pl-date{display:none;} }
`;

/** 清單只給「還在動」的案子；歸檔/未成案要看的人有 CRM。 */
const DONE = new Set(['歸檔', '未成案', '已取消']);

/**
 * @param host        掛載節點
 * @param opts.fetcher  `(path) => Promise<json>`，CRM 前綴
 * @param opts.onOpen   點一列時呼叫，帶 project id
 * @param opts.showAll  true = 連歸檔的也列
 */
export async function renderProjectList(host, opts = {}) {
    ensureStyle(STYLE_ID, CSS);
    host.innerHTML = '<div class="pl-empty">載入中…</div>';
    let rows;
    try {
        rows = (await opts.fetcher('/projects')).projects || [];
    } catch (e) {
        host.innerHTML = `<div class="pl-empty" style="color:#f87171;">專案清單載入失敗：${
            esc(e && e.message ? e.message : String(e))}</div>`;
        return;
    }
    if (!opts.showAll) rows = rows.filter(p => !DONE.has(p.status));
    if (!rows.length) {
        host.innerHTML = '<div class="pl-empty">沒有進行中的專案</div>';
        return;
    }
    host.innerHTML = rows.map(p => `
        <div class="pl-row" tabindex="0" data-id="${esc(p.id)}">
          <span class="pl-name">${esc(p.name || '（未命名）')}</span>
          <span class="pl-client">${esc(p.client_short_name || '')}</span>
          <span class="pl-stage">${esc(p.status || '')}</span>
          <span class="pl-date">${esc((p.start_date || '').slice(0, 10))}</span>
        </div>`).join('');

    const open = (el) => opts.onOpen && opts.onOpen(el.dataset.id);
    host.querySelectorAll('.pl-row').forEach(el => {
        el.addEventListener('click', () => open(el));
        // 鍵盤也要進得去 —— 這是清單的主要動作，不能只有滑鼠
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(el); }
        });
    });
}
