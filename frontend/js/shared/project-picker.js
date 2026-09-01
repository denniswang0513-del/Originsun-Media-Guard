/**
 * project-picker.js — 可搜尋、勾選樣式的專案選擇器（跨 tab 共用）
 *
 * owner 2026-09-01「分類是專案時，專案列表要可以勾選、搜尋」—— 私帳 405 個
 * 專案塞在原生 <select> 裡等於沒得選。這裡是單選（一列收支只掛一個專案），
 * 勾選框只是視覺：點列＝選定並關窗，再點目前那列＝取消連結。
 *
 * 🔴 預設只列**還沒收齊**的案（owner 同日補充：「這個清單要是款項沒收齊的
 * 清單」）—— 記一筆進帳要連的一定是還在等錢的案；411 案裡 217 案早就結清，
 * 全列出來等於把答案埋在雜訊裡。已結清的案要用「顯示全部」切換（補記舊帳
 * 還是得選得到，而且目前連著的那一案不管收齊沒都一定要在清單裡，否則畫面
 * 會看起來像連結不見了）。
 *
 * openProjectPicker({
 *   projects,     // 全部：[{id, name, client?/client_short_name?, status?}, …]
 *   outstanding,  // 未收齊：[{id, name, receivable}]（可省略＝不分兩份）
 *   currentId,    // 目前連結的專案 id（'' ＝未連結）
 *   title,        // 視窗標題（預設「選專案」）
 *   onPick(id),   // 使用者選定（id）或取消連結（''）時呼叫；關窗不呼叫
 * })
 */
import { esc } from './dom.js';

const MAX_ROWS = 120;   // 同 recon 挑選視窗的道理：超過就請人打字縮小範圍

export function openProjectPicker(o) {
    const all = o.projects || [];
    const dueRaw = o.outstanding || [];
    // 未收額查表（畫面上要標出來，跟拆項編輯器同一個樣子）
    const dueAmt = {};
    dueRaw.forEach((p) => { dueAmt[p.id] = Number(p.receivable) || 0; });
    // 未收清單缺的欄位（客戶/狀態）從全清單補；目前連著的那案一定放進來
    const byId = {};
    all.forEach((p) => { byId[p.id] = p; });
    const due = dueRaw.map((p) => ({ ...(byId[p.id] || {}), ...p }));
    if (o.currentId && !due.some((p) => p.id === o.currentId) && byId[o.currentId]) {
        due.unshift(byId[o.currentId]);
    }
    // 沒給 outstanding 就沒有兩份清單可切
    let showAll = !dueRaw.length;
    const list = () => (showAll ? all : due);
    const wrap = document.createElement('div');
    // z-index 9820：要壓過拆項編輯器（9800）以外的一切；跟它互不嵌套
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9820;'
        + 'display:flex;align-items:center;justify-content:center;';
    document.body.appendChild(wrap);
    const close = () => wrap.remove();

    const clientOf = (p) => p.client_short_name || p.client || '';
    let q = '';

    const rowsHtml = () => {
        const hit = list().filter((p) => !q
            || `${p.name || ''} ${clientOf(p)} ${p.status || ''}`.toLowerCase().includes(q));
        if (!hit.length) {
            return `<div style="color:#6b7280;font-size:12px;padding:14px;">${
                showAll ? '找不到符合的專案'
                    : '沒有還在等錢的案 —— 要連結已收齊的案請按「顯示全部」'}</div>`;
        }
        const rows = hit.slice(0, MAX_ROWS);
        const tail = hit.length > rows.length
            ? `<div style="color:#6b7280;font-size:11px;padding:8px 10px;">
                 還有 ${hit.length - rows.length} 個沒顯示 —— 繼續輸入縮小範圍</div>` : '';
        return rows.map((p) => {
            const on = p.id === o.currentId;
            // 🔴 這一列是 <div> 不是 <label>：label 會把 click **轉發**給裡面的
            // checkbox，input 的 click 再冒泡回 label —— 同一次點擊跑兩次 onclick，
            // 於是送出兩個併發的 PUT，兩邊都讀到「原本沒掛專案」，專案已收就
            // 被加了兩次（2026-09-01 生產實帳：4 個顧問月費案各溢收 22,000）。
            // pointer-events:none 擋不住這個 —— 它擋的是指標事件，不是 label 的轉發。
            return `<div data-pick="${esc(p.id)}" style="display:flex;gap:10px;align-items:center;
                    padding:7px 10px;border-bottom:1px solid #262626;cursor:pointer;${on ? 'background:#1e3a2a;' : ''}">
                <input type="checkbox" ${on ? 'checked' : ''} tabindex="-1" style="pointer-events:none;">
                <span style="flex:1;min-width:0;">
                    <span style="color:#eee;font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.name || p.id)}</span>
                    ${clientOf(p) || p.status ? `<span style="color:#9ca3af;font-size:11px;">${esc(clientOf(p))}${p.status ? '　·　' + esc(p.status) : ''}</span>` : ''}
                </span>
                ${dueAmt[p.id] ? `<span style="color:#fbbf24;font-size:11px;white-space:nowrap;">未收 $${
                    dueAmt[p.id].toLocaleString('zh-TW')}</span>`
                    : '<span style="color:#4b5563;font-size:11px;white-space:nowrap;">已收齊</span>'}
            </div>`;
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
            style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:6px 10px;font-size:13px;margin-bottom:6px;">
        ${dueRaw.length ? `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <span id="pp-scope" style="color:#9ca3af;font-size:11px;flex:1;"></span>
            <button id="pp-toggle" class="crm-btn crm-btn-secondary" style="font-size:11px;padding:2px 8px;"></button>
        </div>` : ''}
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
    /** 清單重畫（切換範圍／搜尋共用）—— 只換清單那塊，輸入框與焦點不動。 */
    const redraw = () => {
        wrap.querySelector('#pp-list').innerHTML = rowsHtml();
        const scope = wrap.querySelector('#pp-scope');
        const btn = wrap.querySelector('#pp-toggle');
        if (scope) {
            scope.textContent = showAll
                ? `全部 ${all.length} 案` : `還沒收齊的 ${due.length} 案`;
        }
        if (btn) btn.textContent = showAll ? '只看未收齊' : '顯示全部';
        bind();
    };
    redraw();
    const tgl = wrap.querySelector('#pp-toggle');
    if (tgl) tgl.onclick = () => { showAll = !showAll; redraw(); };
    wrap.onclick = (ev) => { if (ev.target === wrap) close(); };
    wrap.querySelector('#pp-close').onclick = close;
    const clr = wrap.querySelector('#pp-clear');
    if (clr) clr.onclick = () => { o.onPick(''); close(); };
    const qbox = wrap.querySelector('#pp-q');
    qbox.oninput = () => {          // 只重畫清單，不動輸入框（打一個字跳一次焦點會沒法用）
        q = qbox.value.trim().toLowerCase();
        redraw();
    };
    qbox.focus();
}
