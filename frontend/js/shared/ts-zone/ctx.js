// ────────────────────────────────────────────────────────────────────────────
// ts-zone —— 「今天與這週」四個視圖（今天的專案紀錄／我的一週／團隊的一週／專案查詢）的**唯一正本**。
//
// 2026-09-12 從 frontend/js/my/{zone1,week-plan,team-week}.js（傳統 script、全域詞法環境）抽成 ES module，
// 讓員工工作台（/my.html）與 CRM 工作追蹤分頁吃同一份（docs/WORK_TRACKING_V2_PLAN.md §5）。
//
// 這一支是共用的**上下文**：狀態、宿主給的工具（$／esc／mjson／today）、鑰匙判定、視圖切換與 stale 標記。
// 🔴 刻意是模組層單例（一份 `z`）：原本那三支就是共用一個全域環境，四個視圖互相讀寫同一批狀態
//    （格子與我的一週是同一批列、里程碑存了要清今天那條…）。一個 document 只會有一個「今天與這週」，
//    員工頁與 CRM 分頁是不同的 document，不會撞。要在同一頁掛第二份時再改成工廠。
// ────────────────────────────────────────────────────────────────────────────
const _WD_Z1 = ["日", "一", "二", "三", "四", "五", "六"];

export function _shiftDays(ymd, n) {
    const [y, m, d] = ymd.split("-").map(Number);
    const dt = new Date(y, m - 1, d + n);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}
export function _dow(ymd) { const [y, m, d] = ymd.split("-").map(Number); return new Date(y, m - 1, d).getDay(); }
export function _mondayOf(ymd) { return _shiftDays(ymd, -((_dow(ymd) + 6) % 7)); }
export function _mdLabel(ymd) { const [, m, d] = ymd.split("-").map(Number); return `${m}/${d}（${_WD_Z1[_dow(ymd)]}）`; }
/** 上個工作日：跳過週六日（owner：複製昨天改成跳過週末）。 */
export function _prevWorkday(ymd) { let d = _shiftDays(ymd, -1); while (_dow(d) === 0 || _dow(d) === 6) d = _shiftDays(d, -1); return d; }
export const _POST = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const _PUT = (body) => ({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
/** 計畫列＝status 是 plan 而且還沒填時數（有時數的舊 plan 列不當計畫）。 */
export function _isPlan(i) { return i.status === "plan" && !(i.hours > 0); }

export const VIEWS = ["log", "plan", "week", "find"];

/** 共用上下文：`configure()` 填好之後四個視圖都從這裡拿。 */
export const z = {
    host: null,                 // 掛 .view 容器的元素（視圖切換、事件委派都在它身上）
    $: (id) => document.getElementById(id),
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    mjson: null,                // (path, opts) → json；失敗丟 Error（宿主給：員工頁帶自己的 failText 文案）
    today: () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; },
    can: () => true,            // (view) → 有沒有那把鑰匙（員工頁：總開關＋子鑰匙；CRM：timesheets 恆 true）
    api: null,                  // 端點表（configure 填預設；CRM 管理視角換掉幾支）
    hooks: {
        modalRoot: () => document.body,   // 專案檔案／里程碑彈窗掛哪
        journalHref: "",                  // 今天那條「上週回顧」要連去哪（空＝不畫那一格）
        onAction: null,                   // (act, btn, ev) → 收了回 true（員工頁的兼職排班 pt-* 走這裡）
        onView: null,                     // (view) → 切了視圖（員工頁拿來記 localStorage）
    },
    loaders: {},                // view → () => Promise（index.js 註冊）
    s: null,                    // 狀態（configure 建）
};

/** 預設端點：員工端 own-scope。CRM 管理視角在 configure 時覆蓋要換的那幾支。 */
export function defaultApi() {
    return {
        today: () => "/api/v1/me/today",
        mineDay: (day) => "/api/v1/timesheets/mine?date=" + day,
        mineIncomplete: () => "/api/v1/timesheets/mine/incomplete?days=30",
        options: () => "/api/v1/timesheets/options",
        projectOptions: () => "/api/v1/timesheets/project_options",
        mineRows: (from, to) => `/api/v1/timesheets/mine/rows?from=${from}&to=${to}`,
        mineRow: (id) => "/api/v1/timesheets/mine/" + encodeURIComponent(id),
        mineCreate: () => "/api/v1/timesheets/mine/rows",
        merge: () => "/api/v1/timesheets/mine/merge",
        mergeUndo: () => "/api/v1/timesheets/mine/merge/undo",
        mergeLast: (day) => "/api/v1/timesheets/mine/merge/last?date=" + day,
        teamWeek: (start) => "/api/v1/me/team_week?start=" + start,
        milestonesWeek: (start) => "/api/v1/milestones/week?start=" + start,
        projectsBurn: () => "/api/v1/me/projects_burn",
        projectFile: (name, pid) => "/api/v1/timesheets/project?name=" + encodeURIComponent(name) + (pid ? "&project_id=" + encodeURIComponent(pid) : ""),
    };
}

/** 宿主掛載前呼叫一次。`opts`：host／$／esc／mjson／today／can／api（部分覆蓋）／hooks（部分覆蓋）。 */
export function configure(opts) {
    Object.assign(z, {
        host: opts.host,
        $: opts.$ || z.$,
        esc: opts.esc || z.esc,
        mjson: opts.mjson,
        today: opts.today || z.today,
        can: opts.can || z.can,
        api: Object.assign(defaultApi(), opts.api || {}),
        hooks: Object.assign({}, z.hooks, opts.hooks || {}),
    });
    const today = z.today();
    z.s = {
        view: null,
        logDay: today,
        logProjects: null,      // 專案清單（浮層＋對 label 回 id）
        logStages: null,        // {分類: [{id, name}]}（/timesheets/options.stages；工作階段設定改完就換）
        logWorkTypes: [],
        todayInfo: null,        // /me/today（場次、待辦、上週回顧狀態）
        week: _mondayOf(today), // 團隊的一週看哪一週
        planWeek: _mondayOf(today),
        planRows: [],           // 這週本人的列（GET /timesheets/mine/rows?from&to）
        myStaffName: "",        // 里程碑帶入要對負責人名字
        findRows: null,         // 專案查詢的表資料
        findSorter: null,
        findState: { q: "", status: "", type: "", pct: "", from: "", to: "" },   // 一列篩選：狀態、案型、消耗率區間、最後填報日期區間
        msWeek: null,           // GET /milestones/week 的結果（畫帶、開彈窗用）
        msDraft: null,          // 彈窗裡正在改的（儲存才寫入）
    };
    return z;
}

const _z1Can = (v) => z.can(v);
/** 切視圖：沒載過、或別的視圖動過同一批列（stale）→ 載一次。回第一次載入的 promise。 */
export function switchZ1(v) {
    if (!["log", "plan", "week", "find"].includes(v) || !_z1Can(v)) v = ["log", "plan", "week", "find"].find(_z1Can) || "log";   // 記住的視圖沒鑰匙 → 第一個有鑰匙的
    z.s.view = v;
    if (z.hooks.onView) z.hooks.onView(v);
    z.host.querySelectorAll(".views .view-btn").forEach(b => b.classList.toggle("active", b.dataset.view === v));
    z.host.querySelectorAll(".view").forEach(el => el.classList.toggle("on", el.dataset.view === v));
    const el = z.$("z1-" + v);
    if (el && (!el.dataset.loaded || el.dataset.stale)) {
        el.dataset.loaded = "1";
        delete el.dataset.stale;
        el._ready = z.loaders[v]();
    }
    return el && el._ready;   // 第一次載入的 promise（別的視圖要跳過來開東西時等它）
}
/** 我的一週與今天的格子是同一批列：一邊動了，另一邊下次切過去要重抓。 */
export function _z1MarkStale(...ids) { ids.forEach(id => { const el = z.$(id); if (el && el.dataset.loaded) el.dataset.stale = "1"; }); }
