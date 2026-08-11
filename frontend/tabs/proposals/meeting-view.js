/**
 * meeting-view.js — 提案的「會議記錄」分頁（多筆，人手寫）。
 *
 * 刻意**沒有 AI、沒有版本**：會議記錄是創意發想與企劃書的輸入素材，不是
 * 產出物。所以每一筆就是四個欄位，逐欄自動儲存（打完就存，不必按鈕）。
 *
 * 兩個介面共用（後台提案庫的詳情、獨立企劃頁），所以照 brief-view / quote-view
 * 那三條：只 import js/shared 與同目錄、自帶 --mv-* 變數、外殼由呼叫端給。
 *
 * 🔴 內部資料：公開 ?t= 訪客模式**不掛**這個元件 —— 呼叫端連分頁鈕都不建
 * （比照企劃書/報價單；唯讀不是靠隱藏元素，是靠沒建出來）。
 */

import { autosaveDelegated, syncBaseline } from '../../js/shared/autosave.js';
import { ensureStyle, esc } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

const API = '/api/v1/crm';
const _base = (pid) => `${API}/proposals/${encodeURIComponent(pid)}/meetings`;

/**
 * @param host  掛載容器（會被清空）
 * @param opts.proposalId
 */
export async function renderMeetings(host, { proposalId }) {
    ensureStyle('mv-style', STYLE);
    host.classList.add('mv');
    host.__mv = { proposalId, notes: [] };
    // 逐欄自動儲存走共用件（debounce + 失敗退基準重試 + 重畫不重綁）。
    // 委派綁在 host 上一次就好 —— 清單每次動作都整塊重畫，逐顆綁必漏。
    autosaveDelegated(host, '[data-f]', (v, el) =>
        tfetch(`${_base(proposalId)}/${encodeURIComponent(el.dataset.id)}`,
               { method: 'PATCH', json: { [el.dataset.f]: v } }),
        { onError: (e) => alert('儲存失敗：' + (e.message || e)) });
    await _load(host);
}

async function _load(host) {
    const s = host.__mv;
    try {
        s.notes = (await tfetch(_base(s.proposalId))).notes || [];
    } catch (e) {
        host.innerHTML = `<div class="mv-note mv-err">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _render(host);
}

function _render(host) {
    const s = host.__mv;
    host.innerHTML = `
        <div class="mv-bar">
            <button class="mv-btn primary" data-add>新增會議記錄</button>
            <span class="mv-note">共 ${s.notes.length} 筆；打完字自動儲存。</span>
        </div>
        ${s.notes.map(_cardHtml).join('') ||
          '<div class="mv-note">還沒有會議記錄 —— 開會前先按上面新增一筆，邊聽邊記。</div>'}`;
    _wire(host);
}

/** 值一律走 DOM property 餵（不進模板字串）—— 這裡裝的是自由文字。 */
function _cardHtml(m) {
    const id = esc(String(m.id));
    return `
        <div class="mv-card" data-card="${id}">
            <div class="mv-head">
                <input type="date" class="mv-in mv-date" data-f="met_at" data-id="${id}">
                <input class="mv-in mv-title" data-f="title" data-id="${id}"
                       placeholder="會議主題">
                <span class="mv-gap"></span>
                <button class="mv-btn sm" data-del="${id}">刪除</button>
            </div>
            <input class="mv-in mv-att" data-f="attendees" data-id="${id}"
                   placeholder="出席者（自由填，例：客戶王經理、導演、製片）">
            <textarea class="mv-in mv-body" data-f="content" data-id="${id}"
                      placeholder="會議記錄…"></textarea>
            <div class="mv-note mv-by"></div>
        </div>`;
}

function _wire(host) {
    const s = host.__mv;
    const byId = {};
    s.notes.forEach(m => { byId[String(m.id)] = m; });

    host.querySelector('[data-add]').addEventListener('click', async (e) => {
        e.target.disabled = true;
        try {
            await tfetch(_base(s.proposalId), { method: 'POST', json: {} });
            await _load(host);
        } catch (err) {
            alert('新增失敗：' + (err.message || err));
            e.target.disabled = false;
        }
    });

    host.querySelectorAll('[data-del]').forEach(el => {
        el.addEventListener('click', async () => {
            const m = byId[el.dataset.del];
            const what = (m.title || '').trim() || m.met_at || '這一筆';
            if (!confirm(`刪除會議記錄「${what}」？`)) return;
            try {
                await tfetch(`${_base(s.proposalId)}/${encodeURIComponent(el.dataset.del)}`,
                             { method: 'DELETE' });
                await _load(host);
            } catch (e) { alert('刪除失敗：' + (e.message || e)); }
        });
    });

    // 值與「誰建的」在重畫後填回；textarea 隨內容長高（同企劃矩陣的手感）
    host.querySelectorAll('[data-f]').forEach(el => {
        const m = byId[el.dataset.id] || {};
        el.value = m[el.dataset.f] || '';
        if (el.tagName === 'TEXTAREA') {
            _grow(el);
            el.addEventListener('input', () => _grow(el));
        }
    });
    host.querySelectorAll('.mv-card').forEach(card => {
        const m = byId[card.dataset.card] || {};
        const by = card.querySelector('.mv-by');
        by.textContent = m.created_by
            ? `由 ${m.created_by} 建立於 ${String(m.created_at || '').slice(0, 10)}` : '';
    });
    syncBaseline(host, '[data-f]');
}

/** 內容多長格子就多長（min-height 由 CSS 保底）。 */
function _grow(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
}

const STYLE = `
.mv { --mv-ink: #ddd; --mv-sub: #8b8b8b; --mv-line: #2a2a2a; --mv-card: #161616;
      --mv-accent: #c9372c; --mv-err: #f87171; color: var(--mv-ink); }
html.plan-theme-light .mv { --mv-ink: #262626; --mv-sub: #737373; --mv-line: #e5e5e5;
      --mv-card: #fafafa; --mv-accent: #c9372c; --mv-err: #d33; }
.mv * { box-sizing: border-box; }
.mv-gap { flex: 1; }
.mv-note { font-size: 11.5px; color: var(--mv-sub); line-height: 1.7; }
.mv-err { color: var(--mv-err); }
.mv-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.mv-btn { border: 1px solid var(--mv-line); background: none; cursor: pointer;
      color: var(--mv-ink); font: inherit; font-size: 12.5px; padding: 6px 12px; border-radius: 3px; }
.mv-btn:hover:not(:disabled) { border-color: var(--mv-accent); color: var(--mv-accent); }
.mv-btn.primary { border-color: var(--mv-accent); color: var(--mv-accent); }
.mv-btn.sm { font-size: 11.5px; padding: 3px 9px; }
.mv-btn:disabled { opacity: .4; cursor: default; }
.mv-card { border: 1px solid var(--mv-line); border-radius: 3px; background: var(--mv-card);
      padding: 10px 12px; margin-bottom: 10px; }
.mv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
/* 可編控件：平時像文字，focus 才顯邊框（同基本資料的漸進揭露） */
.mv-in { font: inherit; color: var(--mv-ink); background: transparent;
      border: 1px solid transparent; border-radius: 2px; padding: 4px 6px; outline: none; }
.mv-in:hover { border-color: var(--mv-line); }
.mv-in:focus { border-color: var(--mv-accent); }
.mv-date { flex: none; font-size: 12.5px; color: var(--mv-sub); }
.mv-title { flex: 1; min-width: 160px; font-size: 13.5px; font-weight: 600; }
.mv-att { width: 100%; font-size: 12px; color: var(--mv-sub); }
.mv-body { width: 100%; font-size: 13px; line-height: 1.9; min-height: 88px;
      resize: none; overflow-y: hidden; }
.mv-by { margin-top: 4px; }
/* 手機：日期與主題各佔一行，觸控目標 44px（同 proposal-plan 的既有斷點） */
@media (max-width: 720px) {
  .mv-in { font-size: 16px; }
  .mv-title { min-width: 100%; }
  .mv-btn { min-height: 44px; }
}
`;
