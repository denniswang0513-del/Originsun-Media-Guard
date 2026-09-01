/**
 * row-picker.js — 可搜尋、勾選樣式的單選挑選視窗（跨 tab 共用）
 *
 * owner 2026-09-01：「專案列表要可以勾選、搜尋」→「支出的請款單勾記我希望
 * 也可以在這裡直接勾，比照專案」。兩個挑選器的形狀完全一樣（搜尋框＋單選列
 * ＋取消連結＋「主清單／全部」切換），差別只有「一列長什麼樣」與文案 ——
 * 所以殼在這裡只有一份，專案／請款單各自只提供 `line()` 與幾個字串。
 *
 * 單選：一列收支只掛一個專案；請款單雖然可以多張（一筆匯出付多張），那條路
 * 走詳情面板的分配面板，這裡負責最常見的「一對一掛上去」。
 *
 * openRowPicker({
 *   rows,          // 主清單（預設顯示）：[{id, ...}]
 *   allRows,       // 全部（可省略＝不提供切換）
 *   currentId,     // 目前連結的 id（'' ＝未連結）
 *   title, placeholder, emptyMain, emptyAll, scopeMain, scopeAll,
 *   hay(row),      // 搜尋要比對的字串
 *   line(row),     // 一列的內容 HTML（不含勾選框與外框）
 *   onPick(id),    // 選定（id）或取消連結（''）；關窗不呼叫
 * })
 */
import { esc } from './dom.js';

const MAX_ROWS = 120;   // 同 recon 挑選視窗的道理：超過就請人打字縮小範圍

export function openRowPicker(o) {
    const main = o.rows || [];
    const all = o.allRows || [];
    // 目前連著的那一筆一定要在清單裡，否則畫面看起來像連結不見了
    const list0 = main.slice();
    if (o.currentId && !list0.some((r) => r.id === o.currentId)) {
        const cur = all.find((r) => r.id === o.currentId);
        if (cur) { list0.unshift(cur); }
    }
    // 搜尋字串每列算一次就好（405 個專案 × 每按一次鍵重拼一次字串）
    const hay = new Map();
    all.concat(list0).forEach((r) => {
        if (!hay.has(r.id)) { hay.set(r.id, (o.hay(r) || '').toLowerCase()); }
    });
    const canToggle = !!(all.length && main.length);
    let showAll = !canToggle;
    const list = () => (showAll && all.length ? all : list0);

    const wrap = document.createElement('div');
    // z-index 9820：壓過拆項編輯器（9800）以外的一切；跟它互不嵌套
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9820;'
        + 'display:flex;align-items:center;justify-content:center;';
    document.body.appendChild(wrap);
    const close = () => wrap.remove();
    let q = '';

    const rowsHtml = () => {
        const hit = list().filter((r) => !q || (hay.get(r.id) || '').includes(q));
        if (!hit.length) {
            return `<div style="color:#6b7280;font-size:12px;padding:14px;">${
                esc((showAll ? (o.emptyAll || o.emptyMain)
                    : (o.emptyMain || o.emptyAll)) || '找不到符合的項目')}</div>`;
        }
        const rows = hit.slice(0, MAX_ROWS);
        const tail = hit.length > rows.length
            ? `<div style="color:#6b7280;font-size:11px;padding:8px 10px;">
                 還有 ${hit.length - rows.length} 個沒顯示 —— 繼續輸入縮小範圍</div>` : '';
        return rows.map((r) => {
            const on = r.id === o.currentId;
            // 🔴 這一列是 <div> 不是 <label>：label 會把 click **轉發**給裡面的
            // checkbox，input 的 click 再冒泡回 label —— 同一次點擊跑兩次 onclick，
            // 於是送出兩個併發的寫入請求（2026-09-01 生產實帳：專案已收被加了
            // 兩次，4 個顧問月費案各溢收 22,000）。
            // pointer-events:none 擋不住這個 —— 它擋的是指標事件，不是 label 的轉發。
            return `<div data-pick="${esc(r.id)}" style="display:flex;gap:10px;align-items:center;
                    padding:7px 10px;border-bottom:1px solid #262626;cursor:pointer;${on ? 'background:#1e3a2a;' : ''}">
                <input type="checkbox" ${on ? 'checked' : ''} tabindex="-1" style="pointer-events:none;">
                ${o.line(r)}
            </div>`;
        }).join('') + tail;
    };

    wrap.innerHTML = `<div style="background:#1b1b1b;border:1px solid #333;border-radius:12px;
            width:min(560px,94vw);max-height:80vh;display:flex;flex-direction:column;padding:14px 16px;">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <div style="font-size:14px;font-weight:600;color:#eee;flex:1;">${esc(o.title || '選一項')}</div>
            ${o.currentId ? '<button id="pp-clear" class="crm-btn crm-btn-secondary" style="font-size:12px;color:#fca5a5;">取消連結</button>' : ''}
            <button id="pp-close" class="crm-btn crm-btn-secondary" style="font-size:12px;">關閉</button>
        </div>
        <input id="pp-q" type="search" placeholder="${esc(o.placeholder || '搜尋…')}" autocomplete="off"
            style="background:#141414;border:1px solid #333;color:#eee;border-radius:6px;padding:6px 10px;font-size:13px;margin-bottom:6px;">
        ${canToggle ? `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <span id="pp-scope" style="color:#9ca3af;font-size:11px;flex:1;"></span>
            <button id="pp-toggle" class="crm-btn crm-btn-secondary" style="font-size:11px;padding:2px 8px;"></button>
        </div>` : ''}
        <div id="pp-list" style="overflow:auto;border:1px solid #262626;border-radius:8px;flex:1;min-height:120px;"></div>
    </div>`;

    /** 清單重畫（切換範圍／搜尋共用）—— 只換清單那塊，輸入框與焦點不動。
     *  點擊走**事件委派**（掛在清單容器上）：重畫不必重掛 120 個 onclick。 */
    const listBox = wrap.querySelector('#pp-list');
    const redraw = () => {
        listBox.innerHTML = rowsHtml();
        const scope = wrap.querySelector('#pp-scope');
        const btn = wrap.querySelector('#pp-toggle');
        if (scope) {
            scope.textContent = showAll
                ? `${o.scopeAll || '全部'} ${all.length}`
                : `${o.scopeMain || '主清單'} ${list0.length}`;
        }
        if (btn) { btn.textContent = showAll ? `只看${o.scopeMain || '主清單'}` : (o.scopeAll || '顯示全部'); }
    };
    listBox.onclick = (ev) => {
        const el = ev.target.closest('[data-pick]');
        if (!el) { return; }
        const id = el.dataset.pick;
        o.onPick(id === o.currentId ? '' : id);   // 再點目前那列＝取消連結
        close();
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
