// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第一區「今天與這週」的**殼**：鑰匙判定、分頁鈕列、掛載共用的 ts-zone，
// 以及給 parttime.js／cards-hr.js 用的全域轉接。
//
// 🔴 四個視圖（今天的專案紀錄／我的一週／團隊的一週／專案查詢）2026-09-12 起住在 js/shared/ts-zone/
//    （ES module，跟 CRM 工作追蹤分頁同一份；docs/WORK_TRACKING_V2_PLAN.md §5）。這裡不再有視圖邏輯。
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、mfetch、failText、WS（shell.js／cards.js）、_localToday（cards-hr.js）、_pt* 家族（parttime.js）
// 跨檔提供：TS_READY、Z1_*、_shiftDays、_dow、_mondayOf、_mdLabel、mjson、_POST、_PUT、buildZone1、
//            _logProjectOptions、_logProjects、_isPlan、_planCardHtml、_z1MarkStale、_z1Action、_resetTodayStrip
// ────────────────────────────────────────────────────────────────────────────
// 格子（ts-sheet）、專案表（ts-projects）、四個視圖（ts-zone）都是 js/shared/ 的同一份元件，
// 由頁尾的 <script type="module"> import 後掛到 window.TS／window.TSZ；這裡（非 module）等 TS_READY 再用。
const TS_READY = new Promise(res => { window._tsReady = res; });
const Z1_KEY = "my_zone1_view";                       // 記住上次開的視圖（同收合狀態的做法）
const _WD_Z1 = ["日", "一", "二", "三", "四", "五", "六"];

// 日期小工具：parttime.js 在**載入當下**就要（`_pt.week = _mondayOf(...)`），等不了 module，
// 所以這四支跟 js/shared/ts-zone/ctx.js 各一份（一模一樣）；改一邊要改另一邊。
function _shiftDays(ymd, n) {
    const [y, m, d] = ymd.split("-").map(Number);
    const dt = new Date(y, m - 1, d + n);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}
function _dow(ymd) { const [y, m, d] = ymd.split("-").map(Number); return new Date(y, m - 1, d).getDay(); }
function _mondayOf(ymd) { return _shiftDays(ymd, -((_dow(ymd) + 6) % 7)); }
function _mdLabel(ymd) { const [, m, d] = ymd.split("-").map(Number); return `${m}/${d}（${_WD_Z1[_dow(ymd)]}）`; }
async function mjson(path, opts) {
    const r = await mfetch(path, opts);
    if (!r.ok) { const d = await r.json().catch(() => ({})); const e = new Error(d.detail || failText(r.status)); e.status = r.status; throw e; }
    return r.json();
}
const _POST = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const _PUT = (body) => ({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// 「今天與這週」＝總開關 Z1_MASTER **且** 任一把子視圖鑰匙才出現；子視圖 → 鑰匙一顆一把（正本 core.auth.ME_ZONE_MASTER／ME_ZONE1_KEYS）。
// 沒那把就不畫那顆按鈕；總開關沒開整塊不畫（後端 require_zone_staff 同一條，不是只靠前端）。
const Z1_MASTER = "me_today_zone";
const Z1_KEYS = ["me_worklog", "me_week_plan", "me_team_week", "me_project_lookup"];
const Z1_VIEW_KEY = { log: "me_worklog", plan: "me_week_plan", week: "me_team_week", find: "me_project_lookup" };
const _z1Can = (v) => !!(WS && WS.allowed && WS.allowed.includes(Z1_MASTER) && WS.allowed.includes(Z1_VIEW_KEY[v]));
async function buildZone1() {
    const TS = await TS_READY;
    const z = $("ws-zone1");
    const btn = (v, label) => _z1Can(v) ? `<button type="button" class="view-btn" data-view="${v}">${label}</button>` : "";
    // 兼職排班（owner 2026-09-08）：有那把鑰匙才有這顆鈕；開獨立視窗幫兼職排他的一週
    const pt = (WS && WS.allowed && WS.allowed.includes("me_plan_parttime")) ? '<span style="margin-left:auto;align-self:center;"><button type="button" class="btn" data-z1="pt-open">兼職排班</button></span>' : "";
    z.innerHTML = `<div class="views" id="z1-views">
            ${btn("log", "今天的專案紀錄")}${btn("plan", "我的一週")}${btn("week", "團隊的一週")}${btn("find", "專案查詢")}${pt}
        </div>
        <div class="view" data-view="log" id="z1-log"></div>
        <div class="view" data-view="plan" id="z1-plan"></div>
        <div class="view" data-view="week" id="z1-week"></div>
        <div class="view" data-view="find" id="z1-find"></div>`;
    TS.mountZone({
        host: z, $, esc, mjson, today: _localToday, can: _z1Can,
        first: localStorage.getItem(Z1_KEY) || "log",
        hooks: {
            modalRoot: () => $("ws-view") || document.body,
            journalHref: "#ws-journal",
            onView: (v) => { try { localStorage.setItem(Z1_KEY, v); } catch (_) {} },
            onAction: _ptAction,      // 兼職排班視窗（pt-*）住在這一頁（owner 2026-09-11：保留獨立視窗，不併進共用視圖）
        },
    });
}
/** 兼職排班視窗的按鈕（pt-*）：這一頁自己的，ts-zone 分派前先經過這裡；收了回 true。 */
function _ptAction(act, btn) {
    if (!act || !act.startsWith("pt-")) return false;
    if (act === "pt-open") _ptOpen();
    else if (act === "pt-close") { $("ws-pt-modal")?.remove(); _z1MarkStale("z1-week"); }
    else if (act === "pt-week") { _pt.week = btn.dataset.start || _mondayOf(_localToday()); _ptLoad(); }
    else if (act === "pt-add") _ptOpenAdd(btn.dataset.day);
    else if (act === "pt-add-cancel") _ptRender();
    else if (act === "pt-add-ok") _ptSubmitAdd(btn.dataset.day);
    else if (act === "pt-del") _ptDelete(btn.dataset.id);
    else if (act === "pt-defer") { const i = _pt.rows.find(x => x.id === btn.dataset.id); if (i) _ptMove(i.id, _shiftDays(i.date, 1)); }
    else if (act === "pt-from-ms") _ptFromMilestones();
    else if (act === "pt-copy-last") _ptCopyLast();
    return true;
}

// ── 給 parttime.js／cards-hr.js 的轉接：都在使用者操作時才會被叫到（那時 window.TSZ 早就在了）──
const _logProjectOptions = () => window.TSZ._logProjectOptions();
const _isPlan = (i) => window.TSZ._isPlan(i);
const _planCardHtml = (i) => window.TSZ._planCardHtml(i);
const _z1MarkStale = (...ids) => window.TSZ._z1MarkStale(...ids);
const _z1Action = (btn, ev) => window.TSZ._z1Action(btn, ev);
/** 今天那條（請假待審 N 件…）下次重抓（cards-hr.js 請假送單／撤回後叫）。 */
const _resetTodayStrip = () => { if (window.TSZ) window.TSZ.resetToday(); };
