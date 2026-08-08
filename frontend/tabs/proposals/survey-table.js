/**
 * survey-table.js — 提案「現況盤點」表（項目 / 內容 / 備註）
 *
 * 對齊 owner 的 Notion 專案啟動面版那張表。**登入後台與客戶公開頁共用這一份**
 * 渲染，差別只在建構時傳進來的 save 打哪支端點：
 *
 *   登入（員工）→ PATCH /api/v1/proposals/{id}/survey
 *   訪客（?t=）  → PATCH /api/v1/proposals/shared/{token}/survey
 *
 * 🔴 客戶看不到哪些欄目**不是這裡決定的** —— 後端 core/proposal_survey 的
 * PRIVATE_KEYS（預算）在 public 路徑直接不回那一列，前端拿到什麼就畫什麼。
 * 想調整可見範圍請改後端，改這裡沒有用也不該有用。
 *
 * 自動儲存：打字 debounce 800ms、離開欄位立刻送。送出中/失敗會在表尾說一聲，
 * 不用彈窗打斷填表。
 *
 * 樣式走呼叫端的 CSS 變數（--line/--sub/--ink/--red），SPA 深色與官網白底都合用。
 */

import { esc } from '../../js/shared/utils.js';

const SAVE_DEBOUNCE_MS = 800;

/**
 * @param host      掛載容器（會被清空）
 * @param opts.rows      後端回的列（[{key,label,content,note}]）
 * @param opts.save      async (key, field, value) => void
 * @param opts.addRow    可選；async (label) => rows   有給才長出「＋ 欄目」
 * @param opts.removeRow 可選；async (key) => rows     有給才在自訂列出現「移除」
 * @param opts.hint      可選；表尾說明文字
 * @returns { setRows(rows) }
 */
export function renderSurveyTable(host, opts) {
    const { save, addRow = null, removeRow = null,
            hint = '欄位改完會自動儲存。' } = opts;
    let rows = opts.rows || [];
    const timers = new Map();
    _ensureStyle();

    const say = (text, err = false) => {
        const el = host.querySelector('.sv-msg');
        if (!el) return;
        el.textContent = text;
        el.classList.toggle('err', err);
    };

    async function commit(key, field, value) {
        say('儲存中…');
        try {
            await save(key, field, value);
            const r = rows.find(x => x.key === key);
            if (r) r[field] = value;            // 記憶體同步：重畫時不會退回舊值
            say('已儲存');
        } catch (e) {
            say('儲存失敗：' + ((e && e.message) || e), true);
        }
    }

    function schedule(key, field, value) {
        const id = `${key}/${field}`;
        clearTimeout(timers.get(id));
        timers.set(id, setTimeout(() => commit(key, field, value), SAVE_DEBOUNCE_MS));
    }

    function flush(key, field, value) {
        const id = `${key}/${field}`;
        clearTimeout(timers.get(id));
        timers.delete(id);
        const r = rows.find(x => x.key === key);
        if (r && r[field] === value) return;    // 沒改就不要白打一趟
        commit(key, field, value);
    }

    function paint() {
        const isExtra = (k) => String(k).startsWith('x-');
        host.innerHTML = `
            <table class="sv-table">
                <thead><tr><th class="sv-k">項目</th><th>內容</th><th class="sv-n">備註</th></tr></thead>
                <tbody>${rows.map(r => `
                    <tr data-key="${esc(r.key)}">
                        <td class="sv-k">${esc(r.label)}${
                            isExtra(r.key) && removeRow
                                ? '<button class="sv-x" title="移除這個自訂欄目">×</button>' : ''}</td>
                        <td><textarea rows="1" data-field="content"></textarea></td>
                        <td class="sv-n"><textarea rows="1" data-field="note"></textarea></td>
                    </tr>`).join('')}
                </tbody>
            </table>
            ${addRow ? '<div class="sv-add"><button class="sv-btn" data-act="add">＋ 自訂欄目</button></div>' : ''}
            <div class="sv-hint">${esc(hint)}</div>
            <div class="sv-msg"></div>`;

        host.querySelectorAll('tr[data-key]').forEach(tr => {
            const r = rows.find(x => x.key === tr.dataset.key) || {};
            tr.querySelectorAll('textarea[data-field]').forEach(ta => {
                ta.value = r[ta.dataset.field] || '';    // DOM property — 不進模板字串
                grow(ta);
                ta.addEventListener('input', () => {
                    grow(ta);
                    schedule(tr.dataset.key, ta.dataset.field, ta.value);
                });
                ta.addEventListener('blur', () => flush(tr.dataset.key, ta.dataset.field, ta.value));
            });
            tr.querySelector('.sv-x')?.addEventListener('click', async () => {
                if (!confirm(`移除欄目「${r.label}」？填在裡面的內容會一起消失。`)) return;
                try { setRows(await removeRow(tr.dataset.key)); }
                catch (e) { say('移除失敗：' + ((e && e.message) || e), true); }
            });
        });

        host.querySelector('[data-act="add"]')?.addEventListener('click', async () => {
            const label = (prompt('新欄目名稱（例：競品、法規限制）：') || '').trim();
            if (!label) return;
            try { setRows(await addRow(label)); }
            catch (e) { say('新增失敗：' + ((e && e.message) || e), true); }
        });
    }

    function setRows(next) {
        if (!next) return;
        rows = next;
        paint();
    }

    paint();
    return { setRows };
}

function grow(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }

// 自帶樣式（只注一次）—— 呼叫端不必為了掛這張表去改自己的 CSS 檔
function _ensureStyle() {
    if (document.getElementById('sv-style')) return;
    const st = document.createElement('style');
    st.id = 'sv-style';
    st.textContent = `
.sv-table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
.sv-table th { text-align: left; font-weight: 600; font-size: 11.5px; color: var(--sub, #8b8b8b);
               padding: 4px 6px; border-bottom: 1px solid var(--line, #e5e5e5); }
.sv-table td { padding: 3px 6px; border-bottom: 1px solid var(--line, #e5e5e5);
               vertical-align: top; }
.sv-table .sv-k { width: 27%; color: var(--ink, #222); font-weight: 600; word-break: break-all;
                  padding-top: 9px; }
.sv-table .sv-n { width: 26%; }
.sv-table textarea { width: 100%; box-sizing: border-box; background: none; resize: none;
                     border: 1px solid transparent; border-radius: 2px; padding: 4px 5px;
                     color: var(--ink, #222); font: inherit; font-size: 12.5px; line-height: 1.7;
                     overflow: hidden; }
.sv-table textarea:hover { border-color: var(--line, #e5e5e5); }
.sv-table textarea:focus { outline: none; border-color: var(--sub, #8b8b8b); }
.sv-x { border: 0; background: none; cursor: pointer; color: var(--sub, #8b8b8b);
        font-size: 13px; padding: 0 4px; }
.sv-x:hover { color: var(--red, #d33); }
.sv-add { padding: 8px 0 0; }
.sv-btn { border: 1px solid var(--line, #d5d5d5); background: none; cursor: pointer;
          color: var(--ink, #222); font: inherit; font-size: 12.5px; padding: 4px 9px; }
.sv-btn:hover { background: rgba(127,127,127,.08); }
.sv-hint, .sv-msg { font-size: 11.5px; color: var(--sub, #8b8b8b); padding: 6px 0 0; }
.sv-msg.err { color: var(--red, #d33); }`;
    document.head.appendChild(st);
}
