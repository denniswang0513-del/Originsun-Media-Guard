/**
 * prop-dialog.js — 提案模組共用的對話框外殼（可換膚）。
 *
 * 為什麼不用 js/shared/modal-styles.js 的 _createFormModal：
 *   (a) 它硬編深色（#252525/#1a1a1a），在官網白底的獨立頁上會整塊變黑；
 *   (b) 它 `import` 了 tabs/crm/crm-utils.js —— NAS 對外容器只 serve
 *       tabs/proposals 與 js/shared，客戶那頁只要載到就直接壞。
 * 這兩點都不是加個參數能解的（而且它的 descriptor API 生不出 textarea、
 * 兩欄併排、欄位旁掛一顆按鈕這三種東西），所以提案模組自己一份最小的。
 *
 * 退場條件：`_createFormModal` 全專案只剩 3 個呼叫端（js/admin/api-keys、
 * js/admin/user-mgmt、tabs/crm/crm-projects-proposals）。要收斂的話方向是
 * 把那三個搬過來、刪掉 modal-styles.js —— 不是讓這支去遷就它。
 *
 * 主題化比照 plan-matrix / survey-table / pins-panel：自帶 --pdlg-* 變數、
 * 深色為預設、白底靠 html.plan-theme-light 覆寫。
 */

import { ensureStyle, esc } from '../../js/shared/utils.js';

/**
 * @param opts.title  標題（純文字，會轉義）
 * @param opts.body   內容 HTML（由呼叫端組；使用者輸入請自己 esc）
 * @param opts.width  最大寬度 px（預設 560，永遠不超過 96vw）
 * @param opts.onClose 關閉時呼叫（按 ✕ / Esc / 點背景）
 * @returns { el, close }  el = 遮罩層；查詢欄位請用 el.querySelector
 */
export function openDialog({ title, body, width = 560, onClose = null }) {
    ensureStyle('pdlg-style', CSS);
    const ov = document.createElement('div');
    ov.className = 'pdlg-ov';
    ov.innerHTML = `
        <div class="pdlg" style="width:min(${Number(width) || 560}px,96vw);">
            <div class="pdlg-head">
                <h3>${esc(title)}</h3>
                <button class="pdlg-x" title="關閉">✕</button>
            </div>
            <div class="pdlg-body">${body}</div>
        </div>`;
    document.body.appendChild(ov);

    let closed = false;
    // 🔴 capture 階段 + stopPropagation：底下的詳情面板也在 document 上綁了 Esc，
    // 不攔的話按一次 Esc 會連它一起關掉（表單開在詳情之上，只該收掉最上層）。
    const onKey = (e) => {
        if (e.key !== 'Escape') return;
        e.stopPropagation();
        close();
    };
    function close() {
        if (closed) return;                 // Esc + 點背景可能同時發生
        closed = true;
        document.removeEventListener('keydown', onKey, true);
        ov.remove();
        if (onClose) onClose();
    }
    document.addEventListener('keydown', onKey, true);
    ov.querySelector('.pdlg-x').addEventListener('click', close);
    // 只有點到遮罩本身才關 —— 在表單裡拖選文字時滑鼠常會放開在遮罩上，
    // 用 click 事件的 target 判斷才不會把填到一半的表單關掉
    ov.addEventListener('click', (e) => { if (e.target === ov) close(); });

    return { el: ov, close };
}

/** 表單的一列（label + 欄位）。label 是純文字，field 是已組好的 HTML。 */
export function field(label, fieldHtml) {
    return `<div class="pdlg-row"><label>${esc(label)}</label>${fieldHtml}</div>`;
}

const CSS = `
.pdlg-ov { --pdlg-bg: #252525; --pdlg-line: #3a3a3a; --pdlg-ink: #e5e7eb;
           --pdlg-sub: #999; --pdlg-field: #1a1a1a; --pdlg-accent: #3b82f6;
           --pdlg-ring: rgba(59,130,246,.2); --pdlg-btn-ink: #fff;
           position: fixed; inset: 0; z-index: 10000; background: rgba(0,0,0,.72);
           display: flex; align-items: center; justify-content: center; padding: 16px; }
html.plan-theme-light .pdlg-ov { --pdlg-bg: #fff; --pdlg-line: #e5e5e5; --pdlg-ink: #262626;
           --pdlg-sub: #737373; --pdlg-field: #fcfcfc; --pdlg-accent: #c9372c;
           --pdlg-ring: rgba(201,55,44,.15); --pdlg-btn-ink: #fff;
           background: rgba(0,0,0,.35); }
.pdlg { background: var(--pdlg-bg); border: 1px solid var(--pdlg-line); border-radius: 8px;
        max-height: 88vh; display: flex; flex-direction: column; overflow: hidden;
        color: var(--pdlg-ink); box-shadow: 0 18px 44px rgba(0,0,0,.35); }
.pdlg * { box-sizing: border-box; }
.pdlg-head { display: flex; align-items: center; gap: 10px; padding: 14px 18px;
             border-bottom: 1px solid var(--pdlg-line); }
.pdlg-head h3 { margin: 0; font-size: 14.5px; font-weight: 600; flex: 1; }
.pdlg-x { background: none; border: 0; cursor: pointer; color: var(--pdlg-sub);
          font-size: 16px; line-height: 1; padding: 4px 6px; }
.pdlg-x:hover { color: var(--pdlg-ink); }
.pdlg-body { padding: 16px 18px; overflow-y: auto; }
.pdlg-row { margin-bottom: 12px; }
.pdlg-row > label { display: block; font-size: 11.5px; color: var(--pdlg-sub); margin-bottom: 5px; }
.pdlg-cols { display: flex; gap: 10px; }
.pdlg-cols > .pdlg-row { flex: 1; min-width: 0; }
@media (max-width: 520px) { .pdlg-cols { flex-direction: column; gap: 0; } }
.pdlg input, .pdlg select, .pdlg textarea {
        width: 100%; background: var(--pdlg-field); color: var(--pdlg-ink);
        border: 1px solid var(--pdlg-line); border-radius: 4px; padding: 8px 10px;
        font: inherit; font-size: 13px; outline: none; }
.pdlg textarea { resize: vertical; line-height: 1.65; }
.pdlg input:focus, .pdlg select:focus, .pdlg textarea:focus {
        border-color: var(--pdlg-accent); box-shadow: 0 0 0 2px var(--pdlg-ring); }
.pdlg-acts { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
.pdlg-btn { border: 1px solid var(--pdlg-accent); background: var(--pdlg-accent);
            color: var(--pdlg-btn-ink); cursor: pointer; font: inherit; font-size: 13px;
            padding: 7px 16px; border-radius: 4px; }
.pdlg-btn:hover { filter: brightness(1.1); }
.pdlg-btn:disabled { opacity: .5; cursor: not-allowed; }
.pdlg-btn.ghost { background: none; border-color: var(--pdlg-line); color: var(--pdlg-ink); }
.pdlg-btn.ghost:hover { border-color: var(--pdlg-sub); filter: none; }
.pdlg-inline { display: flex; gap: 6px; }
.pdlg-inline > :first-child { flex: 1; min-width: 0; }
.pdlg-err { color: #e05252; font-size: 12px; margin-top: 10px; min-height: 1em; }`;
