// ─────────────────────────────────────────────────────────────────────────────
// ts-zone 的**窄螢幕排版**（docs/WORKSPACE_RWD_PLAN.md，owner 2026-09-17「全部執行」第一批）。
//
// 原則：資料與算式一份、排版兩種。表格在窄螢幕不刪、只藏起來（它還負責自動存、列號、狀態格），
// 旁邊多畫一份卡片＋一個「編輯這一列」的面板；面板寫回同一列的欄位、觸發同一套事件，存檔走同一條路。
// 窄的判定：視窗 640px 以下，或容器被夾到 560px 以下（跟零用金卡片 .pc-narrow 的做法一樣——卡片在桌機也可能很窄）。
//
// 🔴 這是**新檔**：team-week.js／log.js 只從這裡具名匯入（不動 ctx.js／index.js 的 export 清單）——
//    每支 .js 各自被 Cloudflare 快取 4 小時，改既有檔的 export 會讓新舊版互相對不上（見 team-week.js 頂端的紅字）。
// 🔴 這裡的 html 不能含列的 class（ts-mine-row）與列號格的 class（ts-sheet-num）那兩個字面（tests/unit/test_ts_shared_components.py：
//    只能在 ts-sheet.js），程式碼也不能有 emoji（tests/unit/test_my_workspace_layout.py 掃 my.html 的碼；註解不算）。
// ─────────────────────────────────────────────────────────────────────────────
import { ensureStyle } from "/js/shared/dom.js";
import { rowValues, hoursBetween, normTime, saveRowNow, appendBlankRows, stagesFor } from "/js/shared/ts-sheet.js";

export const NARROW_VIEWPORT = "(max-width: 640px)";
export const NARROW_CONTAINER_PX = 560;

/** 這個容器現在要不要用窄排版：視窗窄、或容器本身窄（0 寬＝還沒掛上／display:none，當不窄）。 */
export function isNarrow(el) {
    if (typeof matchMedia === "function" && matchMedia(NARROW_VIEWPORT).matches) return true;
    const w = el && el.getBoundingClientRect ? el.getBoundingClientRect().width : 0;
    return w > 0 && w < NARROW_CONTAINER_PX;
}

const CSS = `
/* 共用：卡、分段鈕、面板。顏色只用半透明灰與 currentColor，白底（員工頁）與深底（CRM 分頁）都讀得到 */
.tsn-cards { display: grid; gap: 8px; margin: 6px 0 10px; }
.tsn-card { border: 1px solid rgba(128,128,128,.35); border-radius: 12px; padding: 10px 12px; cursor: pointer; background: rgba(128,128,128,.04); }
.tsn-card.ro { cursor: default; opacity: .7; }
.tsn-card.plan { border-left: 3px solid #3b82f6; }
.tsn-card .r1 { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.tsn-card .r1 b { font-size: 15px; min-width: 0; overflow-wrap: anywhere; }
.tsn-card .r1 b.blank { opacity: .55; font-weight: 500; }
.tsn-card .r1 .h { font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tsn-card .r1 .h.plan { font-weight: 500; opacity: .7; }
.tsn-card .r2 { display: flex; gap: 10px; flex-wrap: wrap; font-size: 13px; opacity: .75; margin-top: 2px; }
.tsn-card .r3 { font-size: 13px; margin-top: 2px; overflow-wrap: anywhere; }
.tsn-card .st { font-size: 12px; opacity: .6; margin-top: 4px; }
.tsn-card.add { border-style: dashed; text-align: center; opacity: .8; padding: 12px; background: transparent; }
.tsn-editor { border: 1px solid rgba(128,128,128,.35); border-radius: 14px; padding: 12px 12px 10px; margin: 8px 0 10px; background: rgba(128,128,128,.06); }
.tsn-editor .ttl { display: flex; justify-content: space-between; align-items: center; font-weight: 700; margin-bottom: 6px; }
.tsn-editor label { display: block; font-size: 12px; opacity: .7; margin: 8px 0 3px; }
.tsn-editor .in { width: 100%; box-sizing: border-box; border: 1px solid rgba(128,128,128,.4); border-radius: 8px; padding: 10px; font: inherit; font-size: 16px; background: transparent; color: inherit; min-height: 44px; }
.tsn-editor .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tsn-editor .three { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.tsn-editor .acts { display: flex; gap: 8px; margin-top: 12px; }
.tsn-btn { min-height: 44px; border: 1px solid rgba(128,128,128,.45); border-radius: 10px; background: transparent; color: inherit; font: inherit; font-size: 15px; padding: 0 14px; cursor: pointer; flex: 1; }
.tsn-btn.pri { background: #262626; color: #fff; border-color: #262626; }
.tsn-btn.sm { flex: 0 0 auto; min-height: 36px; font-size: 14px; }
.tsn-btn.danger { color: #b3261e; border-color: rgba(179,38,30,.5); flex: 0 0 auto; }
.tsn-on table.ts-sheet { display: none; }
/* 團隊的一週：一天一頁 */
.tsn-seg { display: flex; gap: 4px; background: rgba(128,128,128,.08); border: 1px solid rgba(128,128,128,.25); border-radius: 12px; padding: 4px; margin: 8px 0 10px; }
.tsn-seg button { flex: 1; border: 0; background: transparent; border-radius: 9px; padding: 6px 0; font: inherit; font-size: 13px; line-height: 1.25; color: inherit; opacity: .7; min-height: 44px; cursor: pointer; }
.tsn-seg button small { display: block; font-size: 11px; }
.tsn-seg button.on { background: rgba(255,255,255,.9); color: #262626; opacity: 1; font-weight: 700; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.tsn-seg button.today { color: #c9372c; }
.tsn-seg button.today.on { color: #c9372c; }
.tsn-week .p { display: grid; grid-template-columns: 68px 1fr; gap: 10px; padding: 10px 0; border-top: 1px solid rgba(128,128,128,.25); }
.tsn-week .p .who { font-weight: 700; padding-top: 5px; overflow-wrap: anywhere; }
.tsn-week .p .who small { display: block; font-weight: 500; opacity: .65; font-size: 11px; }
.tsn-week .p .who small.low { color: #b26a00; }
.tsn-week .c { border-left: 3px solid rgba(128,128,128,.35); background: rgba(128,128,128,.06); border-radius: 0 8px 8px 0; padding: 7px 10px; margin-bottom: 6px; font-size: 14px; line-height: 1.4; }
.tsn-week .c i { display: block; font-style: normal; opacity: .7; font-size: 12.5px; }
.tsn-week .c b { font-weight: 600; margin-left: 4px; }
.tsn-week .c .ch { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.tsn-week .c .ch .cp { font-weight: 600; min-width: 0; overflow-wrap: anywhere; }
.tsn-week .c .ch .cp.ts-link { cursor: pointer; text-decoration: underline dotted; }
.tsn-week .c .ch .cp.blank { opacity: .55; font-weight: 500; }
.tsn-week .c .ch .hrs { flex: none; font-weight: 600; font-variant-numeric: tabular-nums; font-size: 12px; background: rgba(128,128,128,.15); border-radius: 999px; padding: 0 7px; line-height: 18px; }
.tsn-week .c .ch .hrs.plan { background: transparent; opacity: .7; font-weight: 500; }
.tsn-week .c .cn { font-size: 13px; margin-top: 2px; overflow-wrap: anywhere; }
.tsn-week .c .ct { font-size: 11.5px; opacity: .7; margin-top: 2px; }
.tsn-week .c.plan { opacity: .65; }
.tsn-week .c.shoot { border-left-color: #2f5fae; background: #eef3fb; color: #1e3a8a; }
.tsn-week .c.off { border-left-color: #3a9c6c; background: #eef7f1; color: #25623f; }
.tsn-week .c.fo { border-left-color: #d99a1e; background: #fdf5e4; color: #7a5200; }
.tsn-week .c.ms { border-left-color: #c9372c; }
.tsn-week .c.ms.done { opacity: .6; text-decoration: line-through; }
.tsn-week .none, .tsn-week .blank-day { opacity: .5; font-size: 13px; padding-top: 6px; display: block; }
.tsn-week .blank-day { color: #b26a00; opacity: 1; }
/* 我的一週：一天一列，今天展開、其他天點標題展開 */
.tsn-plan { grid-template-columns: 1fr !important; }
.tsn-plan .pcol { min-height: 0 !important; }
.tsn-plan .pcol .dh { min-height: 44px; cursor: pointer; align-items: center; }
.tsn-plan .pcol .dh .cnt { margin-left: auto; font-weight: 500; opacity: .75; }
.tsn-plan .pcol:not(.open) .cards, .tsn-plan .pcol:not(.open) .add { display: none; }
.tsn-plan .pcard { font-size: 14px; }
.tsn-plan .pcol .addbtn { min-height: 44px; font-size: 14px; }
/* 專案查詢：篩選收成一顆、一案一卡 */
details.tsn-filters > summary { list-style: none; cursor: pointer; min-height: 44px; display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 14px; }
details.tsn-filters > summary::-webkit-details-marker { display: none; }
details.tsn-filters > summary .pill { border: 1px solid rgba(128,128,128,.45); border-radius: 999px; padding: 2px 10px; }
details.tsn-filters .find-bar { flex-direction: column; align-items: stretch; }
details.tsn-filters .find-bar .in, details.tsn-filters .find-bar .sel { width: 100%; min-width: 0; min-height: 44px; font-size: 16px; box-sizing: border-box; }
.tsn-on-find > table { display: none; }
.tsn-find { display: grid; gap: 8px; }
.tsn-find .fc { border: 1px solid rgba(128,128,128,.35); border-radius: 12px; padding: 10px 12px; cursor: pointer; }
.tsn-find .fc .r1 { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.tsn-find .fc .r1 b { font-size: 15px; min-width: 0; overflow-wrap: anywhere; }
.tsn-find .fc .r1 .pct { flex: none; font-weight: 700; font-variant-numeric: tabular-nums; font-size: 13px; }
.tsn-find .fc .r1 .pct.hi { color: #b26a00; } .tsn-find .fc .r1 .pct.over { color: #b3261e; }
.tsn-find .fc .r2 { font-size: 13px; opacity: .75; display: flex; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
.tsn-find .fc .badge { font-size: 11px; border: 1px solid #fed7aa; color: #b45309; background: #fff7ed; border-radius: 999px; padding: 0 6px; }
.tsn-find .fc .meter { height: 6px; background: rgba(128,128,128,.18); border-radius: 999px; overflow: hidden; margin-top: 6px; }
.tsn-find .fc .meter i { display: block; height: 100%; background: #1f7a4d; }
.tsn-find .fc .meter i.hi { background: #b26a00; } .tsn-find .fc .meter i.over { background: #b3261e; }
.tsn-find .fc .r3 { font-size: 12.5px; opacity: .7; margin-top: 4px; }
.tsn-find .more { text-align: center; }
/* 要補填：一行摘要，點開才列全部日期 */
details.tsn-remind > summary { list-style: none; cursor: pointer; min-height: 44px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
details.tsn-remind > summary::-webkit-details-marker { display: none; }
details.tsn-remind > .today-strip { margin-top: 8px; }
`;
export function ensureNarrowCss() { ensureStyle("ts-zone-narrow-css", CSS); }

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** 一列有沒有內容（跟自動存的判定一樣：專案／做了什麼／階段／備註任一）。 */
const hasContent = (v) => !!(v.project || v.note || v.stage_id || v.remark);

/** 團隊的一週（窄）：一天一頁。`cols`＝這週要畫的日子、`names`＝人、`cellHtml(name, iso)`＝那格的 html（跟表格同一份）、
 *  `sumOf(name)`＝週合計（管理視角才給，其他 null）。回 html；日鈕帶 data-wk-day，宿主自己接。 */
export function weekNarrowHtml({ cols, names, day, today, labelOf, cellHtml, sumOf, lowSum }) {
    const seg = cols.map(iso => {
        const lab = labelOf(iso);                       // 「9/17（四）」→ 上「四」下「9/17」
        const m = lab.match(/^(\d+\/\d+)（(.)）/);
        return `<button type="button" data-wk-day="${esc(iso)}" class="${iso === day ? "on " : ""}${iso === today ? "today" : ""}">${esc(m ? m[2] : lab)}<small>${esc(m ? m[1] : "")}</small></button>`;
    }).join("");
    const rows = names.map(n => {
        const s = sumOf ? sumOf(n) : null;
        const sum = s === null || s === undefined ? "" : `<small class="${lowSum && lowSum(n, s) ? "low" : ""}">週 ${s} h</small>`;
        return `<div class="p"><div class="who">${esc(n)}${sum}</div><div>${cellHtml(n, day)}</div></div>`;
    }).join("");
    return `<div class="tsn-week"><div class="tsn-seg">${seg}</div>${rows || '<div class="none">這一天沒有人。</div>'}</div>`;
}

// ── 專案查詢（窄）：一案一卡（點卡＝走原本的 open-project 委派開專案檔案）；一次 PAGE 張，再載 ──
export const FIND_PAGE = 30;
const pctCls = (p) => (p == null ? "" : (p > 100 ? "over" : (p >= 90 ? "hi" : "")));
export function findCardsHtml(rows, page = 1) {
    const shown = rows.slice(0, page * FIND_PAGE);
    const cards = shown.map(p => {
        const budget = p.budget_hours ?? p.base_hours ?? p.suggested_hours;
        const pct = p.pct == null ? null : Number(p.pct);
        return `<div class="fc" data-ts-action="open-project" data-name="${esc(p.project_name || "")}" data-pid="${esc(p.project_id)}">
            <div class="r1"><b>${esc(p.project_name || p.project_id)}${p.stale ? ' <span class="badge">停滯</span>' : ""}</b>${pct == null ? "" : `<span class="pct ${pctCls(pct)}">${pct}%</span>`}</div>
            <div class="r2">${p.client ? `<span>${esc(p.client)}</span>` : ""}${p.status || p.project_type ? `<span>${esc([p.status, p.project_type].filter(Boolean).join(" · "))}</span>` : ""}</div>
            ${pct == null ? "" : `<div class="meter"><i class="${pctCls(pct)}" style="width:${Math.max(2, Math.min(100, pct))}%"></i></div>`}
            <div class="r3">已投入 ${esc(p.hours_used ?? 0)}h${budget != null ? `／預算 ${esc(budget)}h` : ""}${p.last_entry ? ` · 最後填報 ${esc(p.last_entry)}` : ""}</div>
        </div>`;
    }).join("");
    const rest = rows.length - shown.length;
    return `<div class="tsn-find">${cards || '<div class="tsn-card add">沒有符合的</div>'}${rest > 0 ? `<button type="button" class="tsn-btn more" data-tsn-more>再載 ${Math.min(rest, FIND_PAGE)} 案（還有 ${rest}）</button>` : ""}</div>`;
}

// ── 今天的專案紀錄（窄）：卡片＋編輯面板，鏡射同一張表 ──
function cardHtml(v, idx) {
    const tr = v.tr;
    const plan = !!tr.dataset.plan;
    const state = (tr.querySelector('[data-f="state"]')?.textContent || "").trim();
    const hrs = plan && !(v.hours > 0) ? '<span class="h plan">計畫</span>' : (v.hours ? `<span class="h">${esc(v.hours)} h</span>` : '<span class="h plan">沒時數</span>');
    const type = [v.work_type, v.stage_name && !/^—?$/.test(v.stage_name) ? v.stage_name : ""].filter(Boolean).join(" · ");
    const time = v.t0 || v.t1 ? `${esc(v.t0)}–${esc(v.t1)}` : "";
    return `<div class="tsn-card${plan ? " plan" : ""}${v.readonly ? " ro" : ""}" data-tsn-open="${idx}">
        <div class="r1">${v.project ? `<b>${esc(v.project)}</b>` : '<b class="blank">未填專案</b>'}${hrs}</div>
        ${type || time ? `<div class="r2">${type ? `<span>${esc(type)}</span>` : ""}${time ? `<span>${time}</span>` : ""}</div>` : ""}
        ${v.note ? `<div class="r3">${esc(v.note)}</div>` : ""}
        ${state && state !== "已存" ? `<div class="st">${esc(state)}</div>` : ""}
    </div>`;
}
function editorHtml(v, ctx) {
    const tr = v.tr;
    const types = [...(tr.querySelector('[data-f="type"]')?.options || [])].map(o => `<option value="${esc(o.value)}"${o.value === v.work_type ? " selected" : ""}>${esc(o.textContent)}</option>`).join("");
    const stages = stagesFor(ctx.stages, v.work_type);
    const stageOpts = `<option value="">—</option>` + stages.map(s => `<option value="${esc(s.id)}"${s.id === v.stage_id ? " selected" : ""}>${esc(s.name)}</option>`).join("")
        + (v.stage_id && !stages.some(s => s.id === v.stage_id) ? `<option value="${esc(v.stage_id)}" selected>${esc(v.stage_name)}</option>` : "");
    const projects = (ctx.projects || []).map(p => `<option value="${esc(p.label || p.name)}">`).join("");
    return `<div class="tsn-editor">
        <div class="ttl"><span>${v.id ? "編輯這一列" : "新的一列"}</span><button type="button" class="tsn-btn sm" data-tsn-close>關閉</button></div>
        <label>專案</label><input class="in" data-tsn-f="project" list="tsn-proj-dl" value="${esc(v.project)}" placeholder="打字找案名" autocomplete="off"><datalist id="tsn-proj-dl">${projects}</datalist>
        <div class="two"><div><label>分類</label><select class="in" data-tsn-f="type">${types}</select></div>
            <div><label>工作階段</label><select class="in" data-tsn-f="stage">${stageOpts}</select></div></div>
        <div class="three"><div><label>起</label><input class="in" data-tsn-f="t0" value="${esc(v.t0)}" placeholder="09:00" inputmode="numeric"></div>
            <div><label>訖</label><input class="in" data-tsn-f="t1" value="${esc(v.t1)}" placeholder="12:00" inputmode="numeric"></div>
            <div><label>時數 h</label><input class="in" data-tsn-f="hours" value="${esc(v.hours ?? "")}" placeholder="可打 2.5+1" inputmode="decimal"></div></div>
        <label>做了什麼</label><input class="in" data-tsn-f="note" value="${esc(v.note)}">
        <label>備註</label><input class="in" data-tsn-f="remark" value="${esc(v.remark)}">
        <div class="acts">${v.id || hasContent(v) ? '<button type="button" class="tsn-btn danger" data-tsn-del>刪除</button>' : ""}<button type="button" class="tsn-btn pri" data-tsn-save>存這一列</button></div>
    </div>`;
}

/** 把面板的值寫回那一列（觸發表格自己的事件：分類→階段清單、起訖→時數、離開格子→自動存），再立刻存。 */
function writeBack(sheetHost, tr, panel) {
    const g = (f) => panel.querySelector(`[data-tsn-f="${f}"]`);
    const set = (f, val, ev) => {
        const el = tr.querySelector(`[data-f="${f}"]`); if (!el) return;
        el.value = val;
        if (f === "project" && el.dataset.pid && el.dataset.pname !== val) { delete el.dataset.pid; delete el.dataset.pname; }   // 改了字＝不是浮層選的那個
        el.dispatchEvent(new Event(ev, { bubbles: true }));
    };
    set("project", g("project").value.trim(), "input");
    set("type", g("type").value, "change");                 // 表格會依分類換階段清單
    set("stage", g("stage").value, "change");
    set("note", g("note").value, "input");
    set("remark", g("remark").value, "input");
    set("t0", normTime(g("t0").value), "change");
    set("t1", normTime(g("t1").value), "change");           // 起訖都有 → 表格自己把時數算進去
    const t0 = tr.querySelector('[data-f="t0"]')?.value, t1 = tr.querySelector('[data-f="t1"]')?.value;
    if (!(t0 && t1)) set("hours", g("hours").value, "change");
    saveRowNow(sheetHost, tr);
}

/**
 * 在窄螢幕把格子鏡射成卡片：`sheetHost`＝放 table.ts-sheet 的容器（#z1-sheet）。
 * opts：projects（() => 專案清單）、stages（() => {分類: [...]}）。表格留在 DOM（display:none），卡片跟著它的列變。
 * 回 { refresh }；再呼叫一次同一個 host 只會重畫、不會重掛事件。
 */
export function mountLogCards(sheetHost, opts = {}) {
    ensureNarrowCss();
    sheetHost.classList.add("tsn-on");
    let wrap = sheetHost.querySelector(":scope > .tsn-cards");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "tsn-cards"; sheetHost.appendChild(wrap); }
    const ctx = () => ({ projects: opts.projects ? opts.projects() : [], stages: opts.stages ? opts.stages() : {} });
    const rowsOf = () => [...sheetHost.querySelectorAll("table.ts-sheet tbody tr")].map(rowValues);
    let openIdx = -1;
    const render = () => {
        const rows = rowsOf();
        const shown = rows.map((v, i) => ({ v, i })).filter(x => hasContent(x.v) || x.v.id);
        const cards = shown.map(x => cardHtml(x.v, x.i)).join("");
        const ed = openIdx >= 0 && rows[openIdx] ? editorHtml(rows[openIdx], ctx()) : "";
        wrap.innerHTML = cards + (ed || '<div class="tsn-card add" data-tsn-add>＋ 加一列</div>');
        // 面板要放在它那張卡下面（沒有那張卡＝新列，放最後）
        if (ed) {
            const card = wrap.querySelector(`[data-tsn-open="${openIdx}"]`);
            const panel = wrap.querySelector(".tsn-editor");
            if (card && panel) card.insertAdjacentElement("afterend", panel);
        }
    };
    if (!wrap.dataset.wired) {
        wrap.dataset.wired = "1";
        wrap.addEventListener("click", (e) => {
            const open = e.target.closest("[data-tsn-open]");
            if (open && !e.target.closest(".tsn-editor")) {
                const v = rowsOf()[Number(open.dataset.tsnOpen)];
                if (!v || v.readonly) return;
                openIdx = openIdx === Number(open.dataset.tsnOpen) ? -1 : Number(open.dataset.tsnOpen);
                render(); return;
            }
            if (e.target.closest("[data-tsn-add]")) {
                let rows = rowsOf();
                let idx = rows.findIndex(v => !hasContent(v) && !v.id && !v.readonly);
                if (idx < 0) { appendBlankRows(sheetHost, 1); rows = rowsOf(); idx = rows.length - 1; }
                openIdx = idx; render();
                wrap.querySelector('.tsn-editor [data-tsn-f="project"]')?.focus();
                return;
            }
            if (e.target.closest("[data-tsn-close]")) { openIdx = -1; render(); return; }
            if (e.target.closest("[data-tsn-save]")) {
                const tr = rowsOf()[openIdx]?.tr; if (!tr) return;
                writeBack(sheetHost, tr, wrap.querySelector(".tsn-editor"));
                openIdx = -1; render(); return;
            }
            if (e.target.closest("[data-tsn-del]")) {
                const tr = rowsOf()[openIdx]?.tr; if (!tr) return;
                openIdx = -1;
                tr.querySelector('[data-ts-action="row-remove"]')?.click();     // 走原本那條（有 id 會先問、再 DELETE）
                return;
            }
        });
        // 面板裡改分類 → 階段清單跟著換；起訖填齊 → 時數自動算（跟表格同一套算式）
        wrap.addEventListener("change", (e) => {
            const panel = e.target.closest(".tsn-editor"); if (!panel) return;
            if (e.target.matches('[data-tsn-f="type"]')) {
                const sel = panel.querySelector('[data-tsn-f="stage"]');
                sel.innerHTML = `<option value="">—</option>` + stagesFor(ctx().stages, e.target.value).map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
            }
            if (e.target.matches('[data-tsn-f="t0"], [data-tsn-f="t1"]')) {
                e.target.value = normTime(e.target.value);
                const h = hoursBetween(panel.querySelector('[data-tsn-f="t0"]').value, panel.querySelector('[data-tsn-f="t1"]').value);
                if (h != null) panel.querySelector('[data-tsn-f="hours"]').value = h;
            }
        });
        // 表格的列變了（自動存回 id、狀態格改字、加列、刪列）→ 卡片重畫；面板開著時不動（打字中）
        const mo = new MutationObserver(() => { if (openIdx < 0) render(); });
        const tbody = sheetHost.querySelector("table.ts-sheet tbody");
        if (tbody) mo.observe(tbody, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["data-id", "data-plan"] });
    }
    render();
    return { refresh: render };
}
