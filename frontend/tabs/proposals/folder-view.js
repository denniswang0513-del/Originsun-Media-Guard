/**
 * folder-view.js — 資產資料夾的逐層瀏覽元件（四個介面共用同一份渲染）：
 *
 *   CRM 專案「提案企劃」分頁 → /api/v1/crm/projects/{id}/proposal-assets
 *   企劃頁（員工登入）        → 同上
 *   企劃頁（訪客 ?t=）        → /api/v1/proposals/shared/{token}/folder
 *   提案資產夾總覽            → /api/v1/crm/proposal-assets/folder/files
 *
 * 🔴 看得到什麼是**端點**決定的，不是這個元件的參數決定的 —— 元件只會顯示
 * load 回什麼。想改「客戶看得到什麼」要去改後端的 root，改這裡沒有用也不該有用。
 *
 * 三個能力都是**可選的**，不給就不存在：
 *   write      建夾／上傳（工具列 + 拖放區）—— 訪客那條不給，連按鈕都沒有
 *   pin        「重點提案」勾選欄
 *   rowActions 每列的額外動作（★設為簡報、✕刪除…）
 * 唯讀不是靠隱藏元素，是靠沒建出來。
 *
 * 主題化：自帶 --fv-* 變數、深色為預設、白底靠 html.plan-theme-light 覆寫
 * （比照 plan-matrix / survey-table / pins-panel）。**不要**改回吃呼叫端的
 * --line/--sub/--ink：沒定義那些變數的頁面會 fallback 成猜的顏色。
 */

import { ensureStyle, esc, folderCrumbsHtml, inputUploadItems, uploadFailText,
         uploadProgress, wireAsyncToggle, wireFileDrop } from '../../js/shared/utils.js';
import { fmtSize } from '../../js/shared/clip_utils.js';

/** rowActions 的 title 允許字串或函式 —— 固定文字不必包成 `() => '刪除'`。 */
const _label = (t, f) => (typeof t === 'function' ? t(f) : t) || '';

const _day = (mtime) => (mtime ? new Date(mtime * 1000).toISOString().slice(0, 10) : '');

/**
 * @param host    掛載容器（會被清空）
 * @param opts.load      async (rel) => {rel, dirs, files, truncated}
 * @param opts.initial   可選；最外層的內容。呼叫端為了別的事（設定卡片、
 *        探測有沒有開放）已經打過一次同一支端點時傳進來 —— 不傳的話這裡的
 *        `go('')` 會把那趟**整份 NAS 掃描**再做一次。
 * @param opts.onFile    (file) => void   點檔案要做什麼（各自決定怎麼下載）
 * @param opts.startRel  可選；一開始停在哪一層（預設最外層）。提案的「家」
 *        是專案資產夾底下的子夾時用它 —— 往上一層照樣走得過去（同一個案子的
 *        素材本來就該互通），只是預設不從別人的東西裡開始翻。
 * @param opts.rootLabel 麵包屑最左邊顯示的名字
 * @param opts.emptyHint **唯讀**時整個資料夾空的話要說什麼（有 write 的話
 *        空狀態說的是「拖進來」—— 那才是使用者當下該做的事）
 * @param opts.write     可選；有給才有工具列與拖放：
 *        { mkdir(name, rel) -> level,
 *          upload(items, rel, {onProgress, onUploaded, signal}) -> level }
 * @param opts.pin       可選；有給才長出「重點」勾選欄：
 *        { isPinned(rel) -> bool, toggle(rel, isDir, want) -> Promise,
 *          sync?(level) -> void }
 *        pins-store 那顆本身就滿足這個形狀，直接整顆傳進來即可 —— 每一層的
 *        回應都帶最新的 pinned/pins_public，`sync` 由這裡在**真的去載了**的
 *        時候餵回去（快取命中刻意不餵：舊快照會把 store 往回倒）。
 *        勾選是**策展**（這份是此刻的重點），不搬檔案、不等於授權 ——
 *        客戶看不看得到由另一個開關決定。
 * @param opts.rowActions 可選；每個**檔案**列的額外動作（資料夾列不長）：
 *        [{ at:'lead'|'trail'（省略＝trail）,
 *           html(file) -> string, cls?(file) -> string,
 *           title?: string | (file) -> string, errPrefix?: string,
 *           run(file, ctx) -> level | void }]
 *        `at:'lead'` 放在檔名前（狀態標記，例如「這份是提案簡報」的★），
 *        `at:'trail'` 放在最右（破壞性動作，例如刪除）。
 *        **狀態請走 `cls`（回 'on' 就是亮起來），不要在 `html` 裡寫顏色** ——
 *        inline style 永遠贏過 CSS，hover 會整個死掉。
 *        `ctx.drop()` = 「這一列不見了」（刪除）：從目前這層減掉一列就好，
 *        不必為此重掃一趟 NAS。`run` 回傳新的一層則整層換上；兩個都沒有就
 *        只重刷動作格（呼叫端只改了自己那顆狀態，上千列不必重建）。
 *        動作**自己吞掉點擊**，不會順便觸發「點列＝下載」。
 *
 * 🔴 這個擴充點的紅線：**folder-view 裡不可以出現任何一個具體動作的名字**。
 * 元件只管位置與轉發；哪天這裡開始 `if (key === 'deck')`，這個 port 就已經
 * 變成特例了。
 * @returns { go(rel) }   走到某一層（卡片列點「重點資料夾」要用）
 */
export function renderFolderView(host, opts) {
    const { load, onFile, rootLabel = '資料夾', emptyHint = '這裡還沒有檔案。',
            initial = null, startRel = '', write = null, pin = null, rowActions = [] } = opts;
    // 兩個插槽分一次就好（原本每列跑兩次 filter），index 帶著走 —— DOM 上放
    // 原始索引，點擊時直接 rowActions[i]，不必再把 'lead:0' 拆回來
    const byAt = { lead: [], trail: [] };
    rowActions.forEach((a, i) => byAt[a.at === 'lead' ? 'lead' : 'trail'].push({ a, i }));
    // 走過的層留著：往回走是最常見的動作，重打一次是整趟 NAS 掃描
    const cache = new Map();
    if (initial) cache.set(initial.rel || '', initial);
    let cur = '';
    let level = null;              // 畫在畫面上的那一層
    let byRel = new Map();         // rel → file，畫的時候建一次（見 _delegate）
    ensureStyle('fv-style', STYLE);
    host.classList.add('fv');      // CSS 變數的作用域，元件自己掛

    async function go(rel) {
        cur = rel;
        const hit = cache.get(rel);
        if (hit) { paint(hit); return; }
        host.innerHTML = '<div class="fv-body">載入中…</div>';
        try {
            const d = await load(rel);
            // 每一層的回應都帶最新的 pinned/pins_public。只在**真的去載了**的
            // 時候餵回 store —— 快取命中餵的是舊快照，會把 store 往回倒。
            pin?.sync?.(d);
            cache.set(rel, d);
            if (cur === rel) paint(d);       // await 期間使用者可能已走到別層
        } catch (e) {
            host.innerHTML = `<div class="fv-body fv-err">載入失敗：${esc(e.message || e)}</div>`;
        }
    }

    function paint(d) {
        const dirs = d.dirs || [];
        const files = d.files || [];
        level = d;
        // 反查表建一次：委派的點擊、★的重刷都要從 rel 找回 file，
        // 每次 files.find() 在上千列時是平方級
        byRel = new Map(files.map(f => [f.rel, f]));
        const crumbs = d.rel
            ? `<div class="fv-crumbs">${folderCrumbsHtml(rootLabel, d.rel)}</div>` : '';
        // 勾選框放在列**最前面**且自己吞掉點擊 —— 跟「點列＝進資料夾/下載」
        // 這個既有行為共存，不會勾一下就把人帶進資料夾。
        const pinBox = (rel, isDir) => pin
            ? `<input type="checkbox" class="fv-pin" data-pin="${esc(rel)}"
                      data-pindir="${isDir ? '1' : ''}"${pin.isPinned(rel) ? ' checked' : ''}
                      title="設為重點提案">` : '';
        // 每列的額外動作由呼叫端給（★ 設為簡報、✕ 刪除…）——
        // 元件不知道那些動作是什麼，只負責放位置與把點擊轉出去
        const acts = (f, at) => byAt[at]
            .map(({ a, i }) => `<span class="fv-act ${esc(a.cls?.(f) || '')}"
                             data-rowact="${i}"
                             title="${esc(_label(a.title, f))}">${a.html(f)}</span>`)
            .join('');
        const rows = dirs.map(x => `
            <div class="fv-row fv-dir" data-dir="${esc(x.rel)}">
                ${pinBox(x.rel, true)}
                <span class="fv-caret">▸</span><span class="fv-name">${esc(x.name)}</span>
            </div>`).join('')
            + files.map(f => `
            <div class="fv-row fv-file" data-file="${esc(f.rel)}">
                ${pinBox(f.rel, false)}
                ${acts(f, 'lead')}
                <span class="fv-name" title="下載">${esc(f.filename)}</span>
                <span class="fv-meta">${fmtSize(f.size_bytes)}</span>
                <span class="fv-meta">${esc(_day(f.mtime))}</span>
                ${acts(f, 'trail')}
            </div>`).join('');
        host.innerHTML = _toolbarHtml() + crumbs + (rows
            ? `<div class="fv-list">${rows}</div>`
            : `<div class="fv-body">${esc(d.rel ? '這一層是空的。'
                : (write ? '把檔案或整個資料夾拖進來，或用上面的按鈕。' : emptyHint))}</div>`)
            + (d.truncated ? '<div class="fv-body fv-warn">項目過多，只顯示前 1000 筆。</div>' : '');
        // 勾選框是唯一還逐列綁的：它要的是 change（不是 click），而
        // wireAsyncToggle 的「鎖住→失敗還原」語意綁在單一元素上才成立
        host.querySelectorAll('[data-pin]').forEach(el => {
            wireAsyncToggle(el, (want) => pin.toggle(el.dataset.pin, !!el.dataset.pindir, want),
                            '設定重點提案失敗');
        });
        if (write) _wireToolbar(d);
    }

    // 點擊**只綁一次**在 host 上（拖放同一個理由）：paint() 換的是 children，
    // 逐列綁的話上千列每次導覽就重建幾千個閉包。
    function _delegate(e) {
        if (e.target.closest('[data-pin]')) return;      // 勾選框自己有 change
        const act = e.target.closest('[data-rowact]');
        if (act) { e.stopPropagation(); _runAct(act); return; }
        const nav = e.target.closest('[data-dir],[data-crumb]');
        if (nav) { go(nav.dataset.dir ?? nav.dataset.crumb); return; }
        const file = e.target.closest('[data-file]');
        if (file) onFile(byRel.get(file.dataset.file));
    }

    async function _runAct(el) {
        const a = rowActions[Number(el.dataset.rowact)];
        const f = byRel.get(el.closest('[data-file]').dataset.file);
        const d = level;                   // 這個動作屬於**這一層**
        // drop：這一列不見了。從 d 減掉，不是從「此刻在看的那層」——
        // DELETE 在飛的時候使用者可能已經走到別層去了
        let dropped = false;
        const drop = () => {
            dropped = true;
            const next = { ...d, files: (d.files || []).filter(x => x.rel !== f.rel) };
            cache.set(d.rel || '', next);
            if (cur === (d.rel || '')) paint(next);
        };
        try {
            const next = await a.run(f, { drop });
            if (next) apply(next);
            else if (!dropped) _paintActs();   // 只換狀態 → 不重建上千列
        } catch (err) {
            alert((a.errPrefix ? a.errPrefix + '：' : '') + ((err && err.message) || err));
        }
    }

    // 只重刷動作格：★換一份、✕還在原地。整層 paint() 會重建上千列 DOM，
    // 只為了換一個顏色。
    function _paintActs() {
        host.querySelectorAll('[data-rowact]').forEach(el => {
            const a = rowActions[Number(el.dataset.rowact)];
            const f = byRel.get(el.closest('[data-file]').dataset.file);
            if (!a || !f) return;
            el.innerHTML = a.html(f);
            el.title = _label(a.title, f);
            el.className = 'fv-act ' + (a.cls?.(f) || '');
        });
    }

    // ── 寫入工具列（只有傳 write 才存在）──────────────────────
    function _toolbarHtml() {
        if (!write) return '';
        return `<div class="fv-tools">
            <button class="fv-btn" data-act="mkdir">＋ 新增資料夾</button>
            <button class="fv-btn" data-act="files">＋ 上傳檔案</button>
            <button class="fv-btn" data-act="dir">＋ 上傳資料夾</button>
            <span class="fv-hint">也可以把檔案或整個資料夾拖進來</span>
            <span class="fv-busy" hidden>上傳中…</span>
        </div>`;
    }

    async function run(fn) {
        const busy = host.querySelector('.fv-busy');
        if (busy) busy.hidden = false;
        try {
            apply(await fn());
        } catch (e) {
            alert((e && e.message) || e);
        } finally {
            if (busy && busy.isConnected) busy.hidden = true;
        }
    }

    /** 上傳專用：進度條 + 取消。`write.upload` 收 {onProgress, signal} 自己去打。 */
    async function runUpload(items, rel) {
        const ctrl = new AbortController();
        const bar = uploadProgress(host, () => ctrl.abort());
        try {
            // 一個都沒存成時端點不回這一層（省一次掃描）→ 畫面維持原樣。
            // 這條規矩收在這裡，三個呼叫端就不必各寫一次 `r.files ? r : null`
            const r = await write.upload(items, rel, {
                signal: ctrl.signal,
                onProgress: (l, t) => bar.update(l, t),
                // 傳完之後伺服器還在寫 NAS —— 進度條卡在 100% 不動會像當掉；
                // 而且從這一刻起取消已經沒有意義（finishing 會把取消鈕收掉）
                onUploaded: () => bar.finishing(),
            });
            apply(r && r.files ? r : null);
            bar.remove();
            // 被擋下的項目（副檔名/大小）也收在這裡 —— 呼叫端只要回報成功的部分
            const bad = ((r && r.skipped) || []).map(s => `${s.filename}（${s.reason}）`);
            if (bad.length) alert('部分項目未上傳：\n' + bad.join('\n'));
        } catch (e) {
            bar.fail(uploadFailText(e));
        }
    }

    function _wireToolbar(d) {
        const rel = d.rel || '';
        const pick = (asDir) => {
            const inp = document.createElement('input');
            inp.type = 'file';
            inp.multiple = true;
            if (asDir) inp.webkitdirectory = true;   // 選整個資料夾（含夾名）
            inp.addEventListener('change', () => {
                const items = inputUploadItems(inp.files);
                if (items.length) runUpload(items, rel);
            });
            inp.click();
        };
        const act = {
            mkdir: () => {
                const name = (prompt('新資料夾名稱（會建在你目前看的這一層）：') || '').trim();
                if (name) run(() => write.mkdir(name, rel));
            },
            files: () => pick(false),
            dir: () => pick(true),
        };
        host.querySelectorAll('[data-act]').forEach(el => {
            el.addEventListener('click', () => act[el.dataset.act]());
        });
    }

    function apply(level) {
        if (!level) return;
        const key = level.rel || '';
        cache.set(key, level);
        if (key === cur) paint(level);
        else go(key);
    }

    // 拖放**只綁一次**在 host 上：paint() 換的是 host 的 children，host 本身
    // 不變 —— 每次重畫都綁一遍的話，導覽 5 層之後拖一次會送出 5 份。
    // 落點層在放開的當下才讀 cur，所以永遠是「使用者現在看的這一層」。
    if (write) wireFileDrop(host, (items) => runUpload(items, cur));
    host.addEventListener('click', _delegate);

    go(startRel || '');
    return { go };
}

// 自帶樣式（只注一次）—— 呼叫端不必為了掛這個元件去改自己的 CSS 檔
const STYLE = `
.fv { --fv-ink: #ddd; --fv-sub: #8b8b8b; --fv-line: #2a2a2a; --fv-accent: #c9372c;
      --fv-warn: #fbbf24; --fv-err: #f87171; }
html.plan-theme-light .fv { --fv-ink: #262626; --fv-sub: #737373; --fv-line: #e5e5e5;
      --fv-accent: #c9372c; --fv-warn: #b8860b; --fv-err: #d33; }
.fv-body { padding: 18px 4px; font-size: 13px; color: var(--fv-sub); }
.fv-err { color: var(--fv-err); }
.fv-warn { color: var(--fv-warn); }
.fv-crumbs { font-size: 12.5px; padding: 4px 2px 10px; color: var(--fv-ink); }
.fv-list { border-top: 1px solid var(--fv-line); }
.fv-row { display: flex; align-items: center; gap: 10px; padding: 9px 4px;
          border-bottom: 1px solid var(--fv-line); cursor: pointer; font-size: 13.5px;
          color: var(--fv-ink); }
.fv-row:hover { background: rgba(127,127,127,.07); }
.fv-pin { flex: none; margin: 0; cursor: pointer; accent-color: var(--fv-accent); }
.fv-pin:disabled { opacity: .45; }
.fv-caret { color: var(--fv-sub); }
.fv-dir .fv-name { font-weight: 600; }
.fv-name { flex: 1; word-break: break-all; }
.fv-meta { color: var(--fv-sub); font-size: 12px; white-space: nowrap; }
.fv-act { flex: none; cursor: pointer; font-size: 12.5px; color: var(--fv-sub);
          padding: 0 3px; user-select: none; }
.fv-act.on { color: var(--fv-warn); }
.fv-act:hover { color: var(--fv-accent); }
.fv-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 2px 0 12px; }
.fv-btn { border: 1px solid var(--fv-line); background: none; cursor: pointer;
          color: var(--fv-ink); font: inherit; font-size: 12.5px; padding: 5px 10px; }
.fv-btn:hover { background: rgba(127,127,127,.08); }
.fv-btn.primary { border-color: var(--fv-accent); color: var(--fv-accent); }
.fv-hint, .fv-busy { font-size: 12px; color: var(--fv-sub); }
.fv-busy[hidden] { display: none; }
/* 手機：整列（含資料夾列）都是可點目標，要有手指按得到的高度。
   勾選框本身的尺寸由頁面層的通用規則給 —— 這裡只負責「列有多高」。 */
@media (max-width: 720px) {
  .fv-row { min-height: 44px; }
}`;
