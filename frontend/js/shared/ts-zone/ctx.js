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
// body 是**物件**，由宿主的 mjson 決定怎麼送（員工頁的 mfetch 要自己 stringify；CRM 的 tsFetch 收物件）——
// 兩邊的 fetch 包裝對字串 body 的處理相反，這裡不預先 stringify 就不會有人被雙重編碼。
export const _POST = (body) => ({ method: "POST", body });
export const _PUT = (body) => ({ method: "PUT", body });
/** 計畫列＝status 是 plan 而且還沒填時數（有時數的舊 plan 列不當計畫）。 */
export function _isPlan(i) { return i.status === "plan" && !(i.hours > 0); }

export const VIEWS = ["log", "plan", "week", "find"];

/** 共用上下文：`configure()` 填好之後四個視圖都從這裡拿。 */
export const z = {
    host: null,                 // 掛 .view 容器的元素（視圖切換、事件委派都在它身上）
    $: (id) => document.getElementById(id),
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    mjson: null,                // (path, opts) → json；失敗丟 Error（宿主給：員工頁帶自己的 failText 文案）。opts.body 是物件
    today: () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; },
    can: () => true,            // (view) → 有沒有那把鑰匙（員工頁：總開關＋子鑰匙；CRM：timesheets 恆 true）
    api: null,                  // 端點表（configure 填預設；CRM 管理視角換掉幾支）
    hooks: {
        modalRoot: () => document.body,   // 專案檔案／里程碑彈窗掛哪
        journalHref: "",                  // 今天那條「上週回顧」要連去哪（空＝不畫那一格）
        onAction: null,                   // (act, btn, ev) → 收了回 true（員工頁的兼職排班 pt-* 走這裡）
        onView: null,                     // (view) → 切了視圖（員工頁拿來記 localStorage）
        onWho: null,                      // (who) → 管理視角換了「看誰的」（宿主同步它的切換器）
        isAdmin: () => false,             // 管理視角裡「寫私帳」的那幾顆（改預算、指定、套用建議）只給管理員（後端 _require_mine_admin）
        onCompare: null,                  // (names) → 管理視角的「加入比較」清單變了（CRM 分頁的並排比較頁吃它）
        passthrough: null,                // (btn) → true＝這顆是宿主自己的，ts-zone 不碰、讓它冒泡
        projectPicker: undefined,         // 格子專案格的浮層資料（undefined＝用 _logProjectOptions；null＝宿主自己掛，別再掛一份）
    },
    loaders: {},                // view → () => Promise（index.js 註冊）
    s: null,                    // 狀態（configure 建）
    // ── 管理視角（CRM 工作追蹤分頁；docs/WORK_TRACKING_V2_PLAN.md §3–4）──
    manage: false,              // true＝管理視角：看誰的、替人填、週合計、錢欄位…；false＝員工頁原貌（一個位元都不多）
    who: null,                  // 看誰的：null＝全部（團隊）；{id, name}＝某個人
    me: null,                   // 目前登入者綁的人員 {id, name}（沒綁＝null）；看誰的＝自己時走 own-scope 端點（跟員工頁完全一樣）
    people: [],                 // /timesheets/people（切換器與「未填」判定用）
};
/** 看誰的是不是登入者自己（綁定人員）：是的話一切走 own-scope，跟員工頁一模一樣。 */
export const whoIsMe = () => !!(z.who && z.me && z.who.id === z.me.id);
/** 管理視角替**別人**填：走管理端點（/timesheets/manual、/rows/{id}）。 */
export const whoIsOther = () => !!(z.manage && z.who && !whoIsMe());

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
        createBody: (rows) => ({ rows }),      // POST mineCreate 的 body（管理視角替人填要多帶 staff_id）
        merge: () => "/api/v1/timesheets/mine/merge",
        mergeUndo: () => "/api/v1/timesheets/mine/merge/undo",
        mergeLast: (day) => "/api/v1/timesheets/mine/merge/last?date=" + day,
        teamWeek: (start) => "/api/v1/me/team_week?start=" + start,
        milestonesWeek: (start) => "/api/v1/milestones/week?start=" + start,
        projectsBurn: () => "/api/v1/me/projects_burn",
        projectFile: (name, pid) => "/api/v1/timesheets/project?name=" + encodeURIComponent(name) + (pid ? "&project_id=" + encodeURIComponent(pid) : ""),
    };
}

/** 管理視角的端點表：看誰的＝自己 → own-scope（同員工頁）；別人 → /timesheets/rows（讀）＋ /manual／/rows/{id}（寫）；全部 → 只讀團隊。
 *  回 null 的那支＝這個模式沒有那個功能（loader 自己跳過：今天那條、未完成提醒、合併同案都是 own-scope 才有）。 */
export function manageApi() {
    const own = defaultApi();
    const sid = () => encodeURIComponent(z.who.id);
    return {
        ...own,
        today: () => (whoIsMe() ? own.today() : null),
        mineDay: (day) => (whoIsMe() ? own.mineDay(day) : "/api/v1/timesheets/rows?date=" + day + (z.who ? "&staff_id=" + sid() : "")),
        mineIncomplete: () => (whoIsMe() ? own.mineIncomplete() : null),
        mineRows: (from, to) => (whoIsMe() ? own.mineRows(from, to) : (z.who ? `/api/v1/timesheets/rows?from=${from}&to_day=${to}&staff_id=${sid()}` : null)),
        mineRow: (id) => (whoIsMe() ? own.mineRow(id) : "/api/v1/timesheets/rows/" + encodeURIComponent(id)),
        mineCreate: () => (whoIsMe() ? own.mineCreate() : "/api/v1/timesheets/manual"),
        createBody: (rows) => (whoIsMe() ? { rows } : { staff_id: z.who.id, rows }),
        merge: () => (whoIsMe() ? own.merge() : null),
        mergeUndo: () => (whoIsMe() ? own.mergeUndo() : null),
        mergeLast: (day) => (whoIsMe() ? own.mergeLast(day) : null),
        projectsBurn: () => "/api/v1/timesheets/summary",
        board: (day) => "/api/v1/timesheets/board?date=" + day + "&days=1",
        people: () => "/api/v1/timesheets/people",
        conflicts: () => "/api/v1/timesheets/conflicts",
        suggestBudgets: () => "/api/v1/timesheets/budgets/suggest",
        projectBudget: () => "/api/v1/timesheets/project_budget",
        projectMap: () => "/api/v1/timesheets/project_map",
        remap: () => "/api/v1/timesheets/remap",
        mineProjects: () => "/api/v1/timesheets/projects",
        /** ts-sheet 的自動存要打哪裡（替別人填才換；自己＝預設 own-scope）。 */
        sheetEndpoints: () => (whoIsOther() ? {
            update: (id) => "/api/v1/timesheets/rows/" + encodeURIComponent(id),
            create: () => "/api/v1/timesheets/manual",
            createBody: (rows) => ({ staff_id: z.who.id, rows }),
            remove: (id) => "/api/v1/timesheets/rows/" + encodeURIComponent(id),
        } : null),
    };
}

/** 宿主掛載前呼叫一次。`opts`：host／$／esc／mjson／today／can／api（部分覆蓋）／hooks（部分覆蓋）／manage／who／me／people。 */
export function configure(opts) {
    Object.assign(z, {
        host: opts.host,
        $: opts.$ || z.$,
        esc: opts.esc || z.esc,
        mjson: opts.mjson,
        today: opts.today || z.today,
        can: opts.can || z.can,
        manage: !!opts.manage,
        who: opts.who || null,
        me: opts.me || null,
        people: opts.people || [],
        hooks: Object.assign({}, z.hooks, opts.hooks || {}),
    });
    z.api = Object.assign(z.manage ? manageApi() : defaultApi(), opts.api || {});
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
        findUnmatched: [],      // 管理視角：/summary 的未對映 Sheet 案名
        compareNames: [],       // 管理視角：專案檔案「加入比較」的清單（並排比較頁在 CRM 分頁）
        mineProjects: null,     // 管理視角：/timesheets/projects（指定專案的挑選視窗用；私帳 scope 才拿得到）
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
/** 管理視角：換「看誰的」。四個視圖全部標 stale，目前這個立刻重抓；宿主的切換器由 hooks.onWho 同步。 */
export function setWho(who) {
    z.who = who || null;
    if (z.hooks.onWho) z.hooks.onWho(z.who);
    _z1MarkStale("z1-log", "z1-plan", "z1-week", "z1-find");
    if (z.s.view) { const el = z.$("z1-" + z.s.view); if (el) delete el.dataset.loaded; switchZ1(z.s.view); }
}
