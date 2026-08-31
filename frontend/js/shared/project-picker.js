/**
 * project-picker.js — 可搜尋、勾選樣式的專案選擇器（跨 tab 共用）
 *
 * owner 2026-09-01「分類是專案時，專案列表要可以勾選、搜尋」—— 私帳 405 個
 * 專案塞在原生 <select> 裡等於沒得選。這裡是單選（一列收支只掛一個專案），
 * 勾選框只是視覺：點列＝選定並關窗，再點目前那列＝取消連結。
 *
 * openProjectPicker({
 *   projects,     // [{id, name, client?/client_short_name?, status?}, …]
 *   currentId,    // 目前連結的專案 id（'' ＝未連結）
 *   title,        // 視窗標題（預設「選專案」）
 *   onPick(id),   // 使用者選定（id）或取消連結（''）時呼叫；關窗不呼叫
 * })
 */
import { esc } from './dom.js';

const MAX_ROWS = 120;   // 同 recon 挑選視窗的道理：超過就請人打字縮小範圍

export function openProjectPicker(o) {
    const list = o.projects || [];
    const wrap = document.createElement('div');
    // z-index 9820：要壓過拆項編輯器（9800）以外的一切；跟它互不嵌套
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9820;'
        + 'display:flex;align-items:center;justify-content:center;';
    document.body.appendChild(wrap);
    const close = () => wrap.remove();

    const clientOf = (p) => p.client_short_name || p.client || '';
    let q = '';

    const rowsHtml = () => {
        const hit = list.filter((p) => !q
            || `${p.name || ''} ${clientOf(p)} ${p.status || ''}`.toLowerCase().includes(q));
        if (!hit.length) {
            return '<div style="color:#6b7280;font-size:12px;padding:14px;">找不到符合的專案</div>';
        }
        const rows = hit.slice(0, MAX_ROWS);
        const tail = hit.length > rows.length
            ? `<div style="color:#6b7280;font-size:11px;padding:8px 10px;">
                 還有 ${hit.length - rows.length} 個沒顯示 —— 繼續輸入縮小範圍</div>` : '';
        return rows.map((p) => {
            const on = p.id === o.currentId;
            return `<label data-pick="${esc(p.id)}" style="display:flex;gap:10px;align-items:center;
                    padding:7px 10px;border-bottom:1px solid #262626;cursor:pointer;${on ? 'background:#1e3a2a;' : ''}">
                <input type="checkbox" ${on ? 'checked' : ''} style="pointer-events:none;">
                <span style="flex:1;min-width:0;">
                    <span style="color:#eee;font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.name || p.id)}</span>
                    ${clientOf(p) || p.status ? `<span style="color:#9ca3af;font-size:11px;">${esc(clientOf(p))}${p.status ? '　·　' + esc(p.status) : ''}</span>` : ''}
                </span>
            </label>`;
        }).join('') + tail;
    };

    wrap.innerHTML = `<div style="background:#1b1b1b;border:1px solid #333;border-radius:12px;
            width:min(560px,94vw);max-height:80vh;display:flex;flex-direction:column;padding:14px 16px;">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <div style="font-size:14px;font-weight:600;color:#eee;flex:1;">${esc(o.title || '選專案')}</div>
            ${o.currentId ? '<button id="pp-clear" class="crm-btn crm-btn-secondary" style="font-size:12px;color:#fca5a5;">取消連結</button>' : ''}
            <button id="pp-close" class="crm-btn crm-btn-secondary" style="font-size:12px;">關閉</button>
        </div>
        <input id="pp-q" type="search" placeholder="搜尋專案名稱／客戶…" autocomplete="off"
            style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:6px 10px;font-size:13px;margin-bottom:8px;">
        <div id="pp-list" style="overflow:auto;border:1px solid #262626;border-radius:8px;flex:1;min-height:120px;">${rowsHtml()}</div>
    </div>`;

    const bind = () => {
        wrap.querySelectorAll('[data-pick]').forEach((el) => {
            el.onclick = () => {
                const id = el.dataset.pick;
                o.onPick(id === o.currentId ? '' : id);   // 再點目前那列＝取消連結
                close();
            };
        });
    };
    bind();
    wrap.onclick = (ev) => { if (ev.target === wrap) close(); };
    wrap.querySelector('#pp-close').onclick = close;
    const clr = wrap.querySelector('#pp-clear');
    if (clr) clr.onclick = () => { o.onPick(''); close(); };
    const qbox = wrap.querySelector('#pp-q');
    qbox.oninput = () => {          // 只重畫清單，不動輸入框（打一個字跳一次焦點會沒法用）
        q = qbox.value.trim().toLowerCase();
        wrap.querySelector('#pp-list').innerHTML = rowsHtml();
        bind();
    };
    qbox.focus();
}
