// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第一區「今天與這週」骨架：TS_READY／視圖切換（buildZone1・switchZ1）／視圖 1 今天的專案紀錄／_z1Action 事件委派／專案檔案彈窗
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、mfetch、WS（shell.js）、_localToday（cards-hr.js）、loadMyWeek（week-plan.js）、
//            loadTeamWeek／loadFind／_ms* 家族（team-week.js）、_pt* 家族（parttime.js）
// 跨檔提供：TS_READY、Z1_*、_z1View、_logDay、_logProjects、_logStages、_z1Week、_findState、_shiftDays、_dow、
//            _mondayOf、_mdLabel、_prevWorkday、mjson、_logProjectOptions、buildZone1、switchZ1、_z1MarkStale、
//            loadLog、_POST、_PUT、_z1Action、_openProjectModal
// ────────────────────────────────────────────────────────────────────────────
// ═══ 第一區「今天與這週」：今天的專案紀錄／團隊的一週／專案查詢 ═══
// 格子（ts-sheet）、專案表與專案檔案（ts-projects）、工作階段設定（stage-editor）都是 js/shared/ 的同一份元件，
// 由頁尾的 <script type="module"> import 後掛到 window.TS；這裡（非 module）等 TS_READY 再用。
const TS_READY = new Promise(res => { window._tsReady = res; });
const Z1_KEY = "my_zone1_view";                       // 記住上次開的視圖（同收合狀態的做法）
let _z1View = localStorage.getItem(Z1_KEY) || "log";
let _logDay = _localToday();
let _logProjects = null;    // 專案清單（浮層＋對 label 回 id）
let _logStages = null;      // {分類: [{id, name}]}（/timesheets/options.stages；工作階段設定改完就換）
let _logWorkTypes = [];
let _todayInfo = null;      // /me/today（場次、待辦、上週回顧狀態）
let _z1Week = _mondayOf(_localToday());
let _findRows = null;       // 專案查詢的表資料
let _findSorter = null;
const _findState = { q: "", status: "", type: "", pct: "", from: "", to: "" };   // 一列篩選：狀態、案型、消耗率區間、最後填報日期區間
const _WD_Z1 = ["日", "一", "二", "三", "四", "五", "六"];

function _shiftDays(ymd, n) {
    const [y, m, d] = ymd.split("-").map(Number);
    const dt = new Date(y, m - 1, d + n);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}
function _dow(ymd) { const [y, m, d] = ymd.split("-").map(Number); return new Date(y, m - 1, d).getDay(); }
function _mondayOf(ymd) { return _shiftDays(ymd, -((_dow(ymd) + 6) % 7)); }
function _mdLabel(ymd) { const [, m, d] = ymd.split("-").map(Number); return `${m}/${d}（${_WD_Z1[_dow(ymd)]}）`; }
/** 上個工作日：跳過週六日（owner：複製昨天改成跳過週末）。 */
function _prevWorkday(ymd) { let d = _shiftDays(ymd, -1); while (_dow(d) === 0 || _dow(d) === 6) d = _shiftDays(d, -1); return d; }
async function mjson(path, opts) {
    const r = await mfetch(path, opts);
    if (!r.ok) { const d = await r.json().catch(() => ({})); const e = new Error(d.detail || failText(r.status)); e.status = r.status; throw e; }
    return r.json();
}
/** 專案清單：工時的 project_options（timesheets 模組拿整份；綁定人員檔案的員工也給，多帶本人最近填過的）。 */
async function _logProjectOptions() {
    if (_logProjects) return _logProjects;
    try { _logProjects = (await mjson("/api/v1/timesheets/project_options")).projects || []; }   // timesheets 或綁定人員都能拿
    catch (_) { _logProjects = []; }
    return _logProjects;
}

// 「今天與這週」＝總開關 Z1_MASTER **且** 任一把子視圖鑰匙才出現；子視圖 → 鑰匙一顆一把（正本 core.auth.ME_ZONE_MASTER／ME_ZONE1_KEYS）。
// 沒那把就不畫那顆按鈕；總開關沒開整塊不畫（後端 require_zone_staff 同一條，不是只靠前端）。
const Z1_MASTER = "me_today_zone";
const Z1_KEYS = ["me_worklog", "me_week_plan", "me_team_week", "me_project_lookup"];
const Z1_VIEW_KEY = { log: "me_worklog", plan: "me_week_plan", week: "me_team_week", find: "me_project_lookup" };
const _z1Can = (v) => !!(WS && WS.allowed && WS.allowed.includes(Z1_MASTER) && WS.allowed.includes(Z1_VIEW_KEY[v]));
function buildZone1() {
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
    z.querySelector("#z1-views").addEventListener("click", (e) => { const b = e.target.closest(".view-btn"); if (b) switchZ1(b.dataset.view); });
    z.addEventListener("click", (e) => { const b = e.target.closest("[data-z1], [data-ts-action]"); if (b) _z1Action(b, e); });
    z.addEventListener("change", (e) => { const cb = e.target.closest("input[data-ms-done]"); if (cb) _msToggleDone(cb.dataset.msDone, cb.checked, cb); });   // 週表那條帶上直接勾完成
    z.addEventListener("keydown", (e) => { const f = e.target.closest("#z1-plan .addform"); if (f && e.key === "Enter") { e.preventDefault(); _planSubmitAdd(f.dataset.day); } });
    _planWireDnd(z);
    switchZ1(_z1View);
}
const Z1_LOADERS = { log: () => loadLog(), plan: () => loadMyWeek(), week: () => loadTeamWeek(), find: () => loadFind() };
function switchZ1(v) {
    if (!["log", "plan", "week", "find"].includes(v) || !_z1Can(v)) v = ["log", "plan", "week", "find"].find(_z1Can) || "log";   // 記住的視圖沒鑰匙 → 第一個有鑰匙的
    _z1View = v;
    try { localStorage.setItem(Z1_KEY, v); } catch (_) {}
    document.querySelectorAll("#z1-views .view-btn").forEach(b => b.classList.toggle("active", b.dataset.view === v));
    document.querySelectorAll("#ws-zone1 .view").forEach(el => el.classList.toggle("on", el.dataset.view === v));
    const el = $("z1-" + v);
    // 沒載過、或別的視圖動過同一批列（stale）→ 載一次
    if (el && (!el.dataset.loaded || el.dataset.stale)) {
        el.dataset.loaded = "1";
        delete el.dataset.stale;
        el._ready = Z1_LOADERS[v]();
    }
    return el && el._ready;   // 第一次載入的 promise（別的視圖要跳過來開東西時等它）
}
/** 我的一週與今天的格子是同一批列：一邊動了，另一邊下次切過去要重抓。 */
function _z1MarkStale(...ids) { ids.forEach(id => { const el = $(id); if (el && el.dataset.loaded) el.dataset.stale = "1"; }); }

// ── 視圖 1：今天的專案紀錄（ts-sheet 格子；不畫合計）──
async function _loadToday() {
    if (_todayInfo) return _todayInfo;
    try { _todayInfo = await mjson("/api/v1/me/today"); } catch (e) { _todayInfo = { error: e.message, shoots: [], todos: [] }; }
    return _todayInfo;
}
function _todayStripHtml(t, incomplete = [], planned = 0) {
    if (!t || t.error) return `<div class="today-strip"><span class="meta">${esc((t && t.error) || "")}</span></div>`;
    const shoots = (t.shoots || []).map(s => `<span>${esc(s.title || s.project_name || "")}${s.location ? " · " + esc(s.location) : ""}${s.start_time ? " · " + esc(s.start_time) : ""}</span>`).join("　");
    const jr = t.last_week_journal;
    const monday = _dow(_localToday()) === 1;
    const jrText = jr === "submitted" ? "已送出" : (jr === "draft" ? "草稿" : "還沒寫");
    return `<div class="today-strip">
        <span><span class="k">今天</span>${esc(_mdLabel(t.date || _localToday()))}</span>
        <span><span class="k">場次</span>${shoots || "沒有"}</span>
        <span><span class="k">待辦</span>${(t.todos || []).length} 件</span>
        ${planned ? `<span><span class="k">我的一週</span>今天排了 ${planned} 項</span>` : ""}
        <span><span class="k">上週回顧</span><a href="#ws-journal" class="${monday && jr !== "submitted" ? "warn" : ""}">${monday && jr !== "submitted" ? "上週回顧還沒寫" : jrText}</a></span>
        ${(t.leave_pending || []).length ? `<span><span class="k">請假待審</span>${t.leave_pending.length} 件</span>` : ""}
        ${t.milestones && t.milestones.total ? `<span><span class="k">本週里程碑</span>${t.milestones.total} 個、今天到期 <span class="${t.milestones.due_today.length ? "warn" : ""}">${t.milestones.due_today.length} 個</span>${t.milestones.due_today.length ? "（" + t.milestones.due_today.map(m => esc((m.assignee_name ? m.assignee_name + "：" : "") + m.title)).join("、") + "）" : ""}</span>` : ""}
        ${incomplete.length ? `<span><span class="k">未完成</span>${incomplete.map(d => `<button type="button" class="linkish warn" data-z1="day-goto" data-day="${esc(d.date)}">${esc(_mdLabel(d.date))} 專案紀錄未完成</button>`).join("、")}</span>` : ""}
    </div>`;
}
async function loadLog() {
    const host = $("z1-log");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    const TS = await TS_READY;
    const isToday = _logDay === _localToday();
    let mine = null, err = "", incomplete = [];
    // 四支互不相依，一起發（原本串著等，翻一天要吃四趟來回）；未完成提醒只畫在今天那條，翻到別天不抓
    const [today, mineRes, incRes, opts] = await Promise.all([
        _loadToday(),
        mjson("/api/v1/timesheets/mine?date=" + _logDay).catch(e => ({ __err: e.message })),
        // 近 30 天存了草稿卻沒填時數的日期 → 今天那條提醒「9/5 專案紀錄未完成」（owner 2026-09-07）
        isToday ? mjson("/api/v1/timesheets/mine/incomplete?days=30").catch(() => ({ days: [] })) : Promise.resolve({ days: [] }),
        _logStages === null ? mjson("/api/v1/timesheets/options").catch(() => ({ stages: {} })) : Promise.resolve(null),
    ]);
    if (mineRes && mineRes.__err) err = mineRes.__err; else mine = mineRes;
    incomplete = (incRes.days || []).filter(d => d.date !== _localToday());
    if (opts) _logStages = opts.stages || {};
    if (mine) _logWorkTypes = mine.work_types || _logWorkTypes;
    host.innerHTML = `
        <div class="vhead"><span class="ey">Project Log<b>今天的專案紀錄 ${esc(_mdLabel(_logDay))}</b></span>
            <span class="vrow"><button type="button" class="btn" data-z1="day" data-delta="-1">‹ ${esc(_mdLabel(_shiftDays(_logDay, -1)))}</button>
                <span class="meta">${isToday ? "今天" : ""}</span>
                <button type="button" class="btn" data-z1="day" data-delta="1" ${isToday ? "disabled" : ""}>${esc(_mdLabel(_shiftDays(_logDay, 1)))} ›</button>
                ${isToday ? "" : '<button type="button" class="btn" data-z1="day" data-delta="0">今天</button>'}</span></div>
        ${isToday ? _todayStripHtml(today, incomplete, mine ? (mine.items || []).filter(_isPlan).length : 0) : ""}
        ${mine ? `
        <div id="z1-sheet"></div>
        <div class="sheet-foot">
            <span class="vrow"><button type="button" class="btn" data-z1="stages">工作階段設定</button>
                <button type="button" class="btn" data-z1="row-add">＋ 加五列</button>
                <button type="button" class="btn pri" data-z1="save">儲存草稿</button>
                <button type="button" class="btn" data-z1="merge" title="同案、同分類、同階段的列併成一列：時數相加、內容去重">合併同案</button>
                <button type="button" class="btn" data-z1="unmerge" id="z1-unmerge" hidden title="把最近一次合併退回去">復原合併</button>
                <span id="z1-log-msg" class="meta"></span></span>
        </div>
        <div class="sheet-note">一列＝一個案子做了什麼；填了任何一格就自動存成草稿，時數填了才進彙整。藍底的列是「我的一週」排的（狀態「計畫」，可以按「隔天」挪過去）。起訖用 24 小時制（打「9」「930」「1730」都可以），會自己算出實際 h。
            工作階段只列該列分類的階段。往前翻可以補記；Sheet 拉進來的舊列也可以直接改（一改就轉成你自己的手填列，不會再被試算表蓋回去）。</div>`
        : `<div class="notice">${esc(err || "載入失敗")}<br><span class="meta">要記專案紀錄，帳號要在「使用者管理」綁定人員檔案並開通工作紀錄。</span></div>`}`;
    if (!mine) return;
    const sheet = $("z1-sheet");
    TS.renderSheet(sheet, mine.items || [], { id: "my-log-sheet", workTypes: _logWorkTypes, stages: _logStages, projectPicker: _logProjectOptions });
    TS.wireAutosave(sheet, { day: () => _logDay, projects: () => _logProjects || [],
        // 格子與「我的一週」是同一批列：這裡存了（填時數、改案名）＝那邊要重抓，不然切過去還是舊卡
        onSaved: () => _z1MarkStale("z1-plan", "z1-week"),
        onUnmatched: (names) => { const el = $("z1-log-msg"); if (el) el.textContent = `「${names.join("、")}」對不到案（已存下來，管理員會指定）`; } });
    _logProjectOptions();   // 先抓好，專案格一點就有得選
    _refreshMergeBtn();
}
/** 「復原合併」只在這一天有還沒復原的合併紀錄時才出現（owner 2026-09-07：合併要能復原）。 */
async function _refreshMergeBtn() {
    const btn = $("z1-unmerge");
    if (!btn) return;
    try {
        const d = await mjson("/api/v1/timesheets/mine/merge/last?date=" + _logDay);
        btn.hidden = !(d && d.last);
        btn.dataset.logId = d && d.last ? d.last.log_id : "";
    } catch (_) { btn.hidden = true; }
}
const _POST = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const _PUT = (body) => ({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
async function _z1Action(btn, ev) {
    const TS = await TS_READY;
    const act = btn.dataset.z1 || btn.dataset.tsAction;
    const sheet = $("z1-sheet");
    if (act === "day") {
        const delta = Number(btn.dataset.delta);
        _logDay = delta === 0 ? _localToday() : _shiftDays(_logDay, delta);
        return loadLog();
    }
    if (act === "row-add") { if (sheet) TS.appendBlankRows(sheet); return; }
    if (act === "row-remove") { if (sheet) { if (await TS.removeRow(sheet, btn.closest("tr"))) _z1MarkStale("z1-plan"); } return; }
    if (act === "row-defer") {
        // 計畫列挪到隔天（owner 2026-09-08）：只改 work_date，列本身不動；從今天的格子拿掉、我的一週下次切過去重抓
        const tr = btn.closest("tr"), day = _shiftDays(_logDay, 1), el = $("z1-log-msg");
        if (!tr || !tr.dataset.id) return;
        try { await mjson("/api/v1/timesheets/mine/" + encodeURIComponent(tr.dataset.id), _PUT({ work_date: day })); tr.remove(); _z1MarkStale("z1-plan"); if (el) el.textContent = `已挪到 ${_mdLabel(day)}`; }
        catch (e) { if (el) el.textContent = "沒挪：" + e.message; }
        return;
    }
    // 兼職排班視窗（pt-*）
    if (act === "pt-open") return _ptOpen();
    if (act === "pt-close") { $("ws-pt-modal")?.remove(); _z1MarkStale("z1-week"); return; }
    if (act === "pt-week") { _pt.week = btn.dataset.start || _mondayOf(_localToday()); return _ptLoad(); }
    if (act === "pt-add") return _ptOpenAdd(btn.dataset.day);
    if (act === "pt-add-cancel") return _ptRender();
    if (act === "pt-add-ok") return _ptSubmitAdd(btn.dataset.day);
    if (act === "pt-del") return _ptDelete(btn.dataset.id);
    if (act === "pt-defer") { const i = _pt.rows.find(x => x.id === btn.dataset.id); return i && _ptMove(i.id, _shiftDays(i.date, 1)); }
    if (act === "pt-from-ms") return _ptFromMilestones();
    if (act === "pt-copy-last") return _ptCopyLast();
    if (act === "plan-week") { _planWeek = btn.dataset.start || _mondayOf(_localToday()); return loadMyWeek(); }
    if (act === "plan-add") return _planOpenAdd(btn.dataset.day);
    if (act === "plan-add-cancel") return _renderMyWeek();
    if (act === "plan-add-ok") return _planSubmitAdd(btn.dataset.day);
    if (act === "plan-del") return _planDelete(btn.dataset.id);
    if (act === "plan-defer") { const i = _planRows.find(x => x.id === btn.dataset.id); return i && _planMove(i.id, _shiftDays(i.date, 1)); }
    if (act === "plan-from-ms") return _planFromMilestones();
    if (act === "plan-copy-last") return _planCopyLast();
    if (act === "save") {
        // 儲存草稿：每一列有內容就存（沒時數＝草稿，不進彙整）；其實每格改了就自動存，這顆只是催一次＋講清楚
        if (!sheet) return;
        const rows = TS.collectRows(sheet);
        rows.forEach(r => { if (!r.readonly) TS.saveRowNow(sheet, r.tr); });
        const noHours = rows.filter(r => !r.readonly && !(Number(r.hours) > 0)).length;
        const el = $("z1-log-msg");
        if (el) el.textContent = rows.length ? `已存 ${rows.length} 列` + (noHours ? `，其中 ${noHours} 列還沒填時數（草稿，不進彙整）` : "") : "還沒有填任何一列";
        return;
    }
    if (act === "day-goto") { _logDay = btn.dataset.day || _localToday(); return loadLog(); }
    if (act === "merge") {
        // 合併同案：先 dry_run 拿預覽 → 確認 → 正式併（同案同分類同階段；時數相加、內容去重、起訖清空；可復原）
        const msg = $("z1-log-msg");
        try {
            const pv = await mjson("/api/v1/timesheets/mine/merge", _POST({ date: _logDay, dry_run: true }));
            if (!pv.groups.length) { if (msg) msg.textContent = "沒有可以合併的列（要同案、同分類、同階段，而且都填了時數）"; return; }
            const lines = pv.groups.map(g => `${g.label}：${g.count} 列 → 1 列，${g.hours} h`).join("\n");
            if (!window.confirm(`會這樣合併（合併後可以按「復原合併」退回）：\n\n${lines}\n\n不動：${pv.skipped} 列`)) return;
            const r = await mjson("/api/v1/timesheets/mine/merge", _POST({ date: _logDay, dry_run: false }));
            _z1MarkStale("z1-plan", "z1-week");
            await loadLog();
            const m2 = $("z1-log-msg"); if (m2) m2.textContent = `已合併 ${r.groups.length} 組，可以直接改內容；要退回按「復原合併」`;
        } catch (e) { if (msg) msg.textContent = "合併失敗：" + e.message; }
        return;
    }
    if (act === "unmerge") {
        const logId = btn.dataset.logId;
        if (!logId) return;
        if (!window.confirm("復原最近一次合併？被併掉的列會放回來，合併後對那幾列的修改會被蓋掉。")) return;
        try { await mjson("/api/v1/timesheets/mine/merge/undo", _POST({ log_id: logId })); _z1MarkStale("z1-plan", "z1-week"); await loadLog(); const m = $("z1-log-msg"); if (m) m.textContent = "已復原合併"; }
        catch (e) { const m = $("z1-log-msg"); if (m) m.textContent = "復原失敗：" + e.message; }
        return;
    }
    if (act === "stages") {
        return TS.openStageEditor({ onSaved: (map) => { _logStages = map; if (sheet) TS.setStages(sheet, map); } });
    }
    if (act === "week") { _z1Week = btn.dataset.start || _mondayOf(_localToday()); return loadTeamWeek(); }
    if (act === "ms-open") return _openMsModal();
    if (act === "find-back") return _renderFindTable();
    if (act === "proj-pop") return _openProjectModal(btn.dataset.name || "", btn.dataset.pid || "");   // 團隊的一週點案名：彈窗
    if (act === "open-project") return _openFindProject(btn.dataset.name || "", btn.dataset.pid || "");
}

// ── 專案檔案彈窗（團隊的一週點案名；owner 2026-09-06：要彈出視窗可打叉，不是跳頁）──
async function _openProjectModal(name, pid) {
    const TS = await TS_READY;
    const root = $("ws-view") || document.body;
    let bg = $("ws-proj-modal");
    if (!bg) {
        bg = document.createElement("div");
        bg.id = "ws-proj-modal"; bg.className = "ws-modal-bg";
        bg.addEventListener("click", (e) => { if (e.target === bg || e.target.closest(".ws-modal-x")) bg.remove(); });
        root.appendChild(bg);
        if (!window.__wsModalEsc) {       // 全頁只掛一次（每開一次掛一個會累積）
            window.__wsModalEsc = true;
            document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("ws-proj-modal")?.remove(); });
        }
    }
    const x = '<button type="button" class="ws-modal-x" title="關閉">×</button>';
    bg.innerHTML = `<div class="ws-modal">${x}<div class="empty">載入中…</div></div>`;
    try {
        const d = await mjson("/api/v1/timesheets/project?name=" + encodeURIComponent(name) + (pid ? "&project_id=" + encodeURIComponent(pid) : ""));
        bg.innerHTML = `<div class="ws-modal">${x}<div class="tsp">${TS.projectFileHtml(d, { modal: true, editable: false, chartWidth: 340 })}</div></div>`;
    } catch (e) {
        bg.innerHTML = `<div class="ws-modal">${x}<div class="notice">${esc(e.message)}</div></div>`;
    }
}
