/**
 * pins-panel.js — 「重點提案」卡片列 ＋ 對客戶的授權開關。
 *
 * 勾起來的資產在這裡以卡片呈現。不一定是最終版，是**此刻**要人看的那一版 ——
 * 同事一眼知道現在以哪一份為準。
 *
 * 🔴 兩件事刻意分開，不要合併成一個開關：
 *   勾選（folder-view 的 checkbox）＝ 策展，只在內部標記「以這份為準」
 *   這裡的「客戶看得到」   ＝ 授權，才會讓拿到分享連結的人真的看到
 * 勾一下就把檔案送到客戶眼前，是這個模組最不能犯的錯。所以警示行與卡片列
 * 在同一個元件、同一份狀態裡渲染 —— 「客戶目前看得到上面這 N 項」的 N
 * 要是跨元件傳來的，遲早會對不上。
 *
 * 純渲染：不持有狀態（狀態在 pins-store），每次要更新就整支再呼叫一次 ——
 * 比照 plan-matrix / survey-table 的慣例，呼叫端不必留 handle。
 *
 * 同一份 UI 活在兩個色系：CRM SPA（深色）與 /proposal-plan.html（官網白底）。
 * 主題化＝自帶 --pin-* 變數、深色為預設、白底靠 html.plan-theme-light 覆寫
 * （吃呼叫端變數會在沒定義的頁面變成看不見的字）。
 */

// esc 走 js/shared/utils.js 而不是 crm-utils —— 公開共編頁要在 NAS 對外容器
// serve，那台只 serve tabs/proposals 與 js/shared 兩個子目錄。
import { ensureStyle, esc, wireAsyncToggle } from '../../js/shared/utils.js';

/**
 * @param host           掛載容器（會被清空）
 * @param opts.state     {pinned, pins_public}（pins-store 的 get()）
 * @param opts.setPublic 可選；async (want) => void
 *                       沒給就不長「客戶看得到」開關（唯讀展示用）
 * @param opts.onOpen    可選；(pin) => void   點卡片要做什麼。收整筆記錄而不是
 *                       拆成 (rel, name, isDir) —— 呼叫端要判斷 is_dir（資料夾
 *                       要走進去、檔案才下載），拆開只會逼它把物件組回來。
 */
export function renderPins(host, opts = {}) {
    const { state = {}, setPublic = null, onOpen = null } = opts;
    const pins = state.pinned || [];
    const isPublic = !!state.pins_public;
    ensureStyle('pin-style', CSS);
    // 變數的作用域自己掛，不外包給呼叫端 —— 漏掛不會報錯，只會讓所有顏色
    // 落空（白底頁上變成看不見的字），而那是最難被發現的一種壞。
    host.classList.add('pin-wrap');

    if (!pins.length) {
        // 空狀態也要留著 —— 「勾了會發生什麼」只有在這裡說得清楚
        host.innerHTML = '<div class="pin-empty">重點提案：在下面的檔案列勾選，'
            + '就會出現在這裡。</div>';
        return;
    }

    const cards = pins.map((p, i) => {
        // 縮圖壞掉（NAS 產圖失敗、檔案被換掉）要退回佔位方塊，不能留一塊空白：
        // 一排有圖一排沒圖看起來像壞掉。同 proposal-plan.html 的 refThumb。
        const cover = p.thumb_url
            ? `<img src="${esc(p.thumb_url)}" alt="" loading="lazy" class="pin-img"
                    onerror="this.parentElement.classList.add('ph');this.remove();">`
            : `<div class="pin-img pin-ph">${p.is_dir ? '📁' : '📄'}</div>`;
        // 索引當把手：點擊時直接取回那筆 pin 記錄，不必把欄位攤成一堆 data-*
        return `<div class="pin-card" data-pincard="${i}" title="${esc(p.rel)}">
            ${cover}<div class="pin-name">${esc((p.rel || '').split('/').pop())}</div>
        </div>`;
    }).join('');
    const toggle = setPublic ? `
        <label class="pin-pub" title="打開後，拿到提案分享連結的客戶會在頁面上看到這些檔案">
            <input type="checkbox" data-pinpub${isPublic ? ' checked' : ''}>
            客戶看得到
        </label>` : '';
    host.innerHTML = `
        <div class="pin-head">
            <span class="pin-title">重點提案</span>
            <span class="pin-count">${pins.length} 項</span>
            <span class="pin-gap"></span>
            ${toggle}
        </div>
        ${isPublic ? `<div class="pin-warn">客戶目前看得到上面這 ${pins.length} 項。`
            + '取消勾選會立刻失效。</div>' : ''}
        <div class="pin-cards">${cards}</div>`;

    const pub = host.querySelector('[data-pinpub]');
    if (pub) wireAsyncToggle(pub, setPublic, '設定失敗');
    if (onOpen) host.querySelectorAll('[data-pincard]').forEach(el => {
        el.addEventListener('click', () => onOpen(pins[Number(el.dataset.pincard)]));
    });
}

const CSS = `
.pin-wrap { --pin-ink: #ddd; --pin-sub: #6b6b6b; --pin-line: #2a2a2a; --pin-card: #141414;
            --pin-ph: #111; --pin-phi: #5b5b5b; --pin-accent: #c9372c; --pin-warn: #fbbf24; }
html.plan-theme-light .pin-wrap { --pin-ink: #262626; --pin-sub: #8b8b8b; --pin-line: #e5e5e5;
            --pin-card: #fff; --pin-ph: #f5f5f4; --pin-phi: #b4b4b4;
            --pin-accent: #c9372c; --pin-warn: #b8860b; }
.pin-empty { font-size: 11.5px; color: var(--pin-sub); margin-bottom: 8px; }
.pin-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.pin-title { font-size: 12px; color: var(--pin-ink); font-weight: 600; }
.pin-count { font-size: 11px; color: var(--pin-sub); }
.pin-gap { flex: 1; }
.pin-pub { font-size: 11.5px; color: var(--pin-sub); display: flex; align-items: center;
           gap: 5px; cursor: pointer; }
.pin-pub input { margin: 0; cursor: pointer; accent-color: var(--pin-accent); }
.pin-pub input:disabled { opacity: .45; }
.pin-warn { font-size: 11px; color: var(--pin-warn); margin-bottom: 6px; }
.pin-cards { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.pin-card { width: 150px; border: 1px solid var(--pin-line); border-radius: 5px;
            overflow: hidden; background: var(--pin-card); cursor: pointer; }
.pin-card:hover { border-color: var(--pin-sub); }
.pin-img { width: 100%; height: 96px; object-fit: cover; display: block;
           background: var(--pin-ph); }
.pin-ph { display: flex; align-items: center; justify-content: center;
          color: var(--pin-phi); font-size: 26px; }
.pin-name { padding: 5px 7px; font-size: 11.5px; line-height: 1.45; color: var(--pin-ink);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }`;
