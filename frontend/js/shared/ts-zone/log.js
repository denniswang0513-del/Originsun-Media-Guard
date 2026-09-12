// ────────────────────────────────────────────────────────────────────────────
// ts-zone 視圖 1：今天的專案紀錄（ts-sheet 格子；不畫合計）＋ 今天那條 strip ＋ 合併同案
// 狀態與工具全在 ctx.js 的 `z`；格子本身是 js/shared/ts-sheet.js（同一份給員工頁與 CRM 分頁）。
// ────────────────────────────────────────────────────────────────────────────
import { renderSheet, wireAutosave } from "/js/shared/ts-sheet.js";
import { z, _z1MarkStale, _shiftDays, _dow, _mdLabel, _isPlan, _POST, whoIsMe } from "./ctx.js";

/** 專案清單：工時的 project_options（timesheets 模組拿整份；綁定人員檔案的員工也給，多帶本人最近填過的）。 */
export async function _logProjectOptions() {
    if (z.s.logProjects) return z.s.logProjects;
    try { z.s.logProjects = (await z.mjson(z.api.projectOptions())).projects || []; }   // timesheets 或綁定人員都能拿
    catch (_) { z.s.logProjects = []; }
    return z.s.logProjects;
}
/** 今天那條（場次、待辦、上週回顧、請假待審、里程碑）快取；請假送單／里程碑存檔後要 `resetToday()` 才會重抓。 */
export function resetToday() { z.s.todayInfo = null; }
async function _loadToday() {
    if (!z.api.today()) return null;            // 管理視角看別人／全部：沒有「我的今天」那條
    if (z.s.todayInfo) return z.s.todayInfo;
    try { z.s.todayInfo = await z.mjson(z.api.today()); } catch (e) { z.s.todayInfo = { error: e.message, shoots: [], todos: [] }; }
    return z.s.todayInfo;
}
function _todayStripHtml(t, incomplete = [], planned = 0) {
    const esc = z.esc;
    if (!t || t.error) return `<div class="today-strip"><span class="meta">${esc((t && t.error) || "")}</span></div>`;
    const shoots = (t.shoots || []).map(s => `<span>${esc(s.title || s.project_name || "")}${s.location ? " · " + esc(s.location) : ""}${s.start_time ? " · " + esc(s.start_time) : ""}</span>`).join("　");
    const jr = t.last_week_journal;
    const monday = _dow(z.today()) === 1;
    const jrText = jr === "submitted" ? "已送出" : (jr === "draft" ? "草稿" : "還沒寫");
    const journal = z.hooks.journalHref
        ? `<span><span class="k">上週回顧</span><a href="${esc(z.hooks.journalHref)}" class="${monday && jr !== "submitted" ? "warn" : ""}">${monday && jr !== "submitted" ? "上週回顧還沒寫" : jrText}</a></span>`
        : "";
    return `<div class="today-strip">
        <span><span class="k">今天</span>${esc(_mdLabel(t.date || z.today()))}</span>
        <span><span class="k">場次</span>${shoots || "沒有"}</span>
        <span><span class="k">待辦</span>${(t.todos || []).length} 件</span>
        ${planned ? `<span><span class="k">我的一週</span>今天排了 ${planned} 項</span>` : ""}
        ${journal}
        ${(t.leave_pending || []).length ? `<span><span class="k">請假待審</span>${t.leave_pending.length} 件</span>` : ""}
        ${t.milestones && t.milestones.total ? `<span><span class="k">本週里程碑</span>${t.milestones.total} 個、今天到期 <span class="${t.milestones.due_today.length ? "warn" : ""}">${t.milestones.due_today.length} 個</span>${t.milestones.due_today.length ? "（" + t.milestones.due_today.map(m => esc((m.assignee_name ? m.assignee_name + "：" : "") + m.title)).join("、") + "）" : ""}</span>` : ""}
        ${incomplete.length ? `<span><span class="k">未完成</span>${incomplete.map(d => `<button type="button" class="linkish warn" data-z1="day-goto" data-day="${esc(d.date)}">${esc(_mdLabel(d.date))} 專案紀錄未完成</button>`).join("、")}</span>` : ""}
    </div>`;
}
/** 標題列（日期翻頁）—— 員工版與管理版共用。 */
function _logHeadHtml(isToday) {
    const { esc, s } = z;
    const who = z.manage ? (z.who ? `<span class="meta">${esc(z.who.name)}${whoIsMe() ? "（我）" : ""}</span>` : '<span class="meta">全部（團隊）</span>') : "";
    return `<div class="vhead"><span class="ey">Project Log<b>今天的專案紀錄 ${esc(_mdLabel(s.logDay))}</b>${who}</span>
            <span class="vrow"><button type="button" class="btn" data-z1="day" data-delta="-1">‹ ${esc(_mdLabel(_shiftDays(s.logDay, -1)))}</button>
                <span class="meta">${isToday ? "今天" : ""}</span>
                <button type="button" class="btn" data-z1="day" data-delta="1" ${isToday ? "disabled" : ""}>${esc(_mdLabel(_shiftDays(s.logDay, 1)))} ›</button>
                ${isToday ? "" : '<button type="button" class="btn" data-z1="day" data-delta="0">今天</button>'}</span></div>`;
}
/** 管理視角「今天還沒填」那條（/board 的 absent；週末後端回空）。點名字＝切到他。 */
function _absentStripHtml(board) {
    const esc = z.esc;
    const day = ((board && board.items) || [])[0] || {};
    const absent = day.absent || [];
    if (!absent.length) return "";
    const byName = new Map(z.people.map(p => [p.name, p]));
    return `<div class="today-strip absent"><span><span class="k">還沒填</span>${absent.map(n => {
        const p = byName.get(n);
        return p ? `<button type="button" class="linkish warn" data-z1="who" data-id="${esc(p.id)}" data-name="${esc(p.name)}">${esc(n)}</button>` : `<span class="warn">${esc(n)}</span>`;
    }).join("、")}</span><span class="meta">在職／合夥、當天沒有任何實際或草稿列（只有計畫卡不算）</span></div>`;
}
/** 管理視角「全部」：當天每個人一段唯讀格子（/timesheets/rows?date=），點「替他填」切到他。 */
async function _loadTeamDay() {
    const { $, esc, mjson, s } = z;
    const host = $("z1-log");
    const isToday = s.logDay === z.today();
    const [rowsRes, board, opts] = await Promise.all([
        mjson(z.api.mineDay(s.logDay)).catch(e => ({ __err: e.message })),
        mjson(z.api.board(s.logDay)).catch(() => null),
        s.logStages === null ? mjson(z.api.options()).catch(() => ({ stages: {} })) : Promise.resolve(null),
    ]);
    if (opts) s.logStages = opts.stages || {};
    if (rowsRes && rowsRes.__err) { host.innerHTML = _logHeadHtml(isToday) + `<div class="notice">${esc(rowsRes.__err)}</div>`; return; }
    s.logWorkTypes = rowsRes.work_types || s.logWorkTypes;
    const byName = new Map();
    (rowsRes.items || []).forEach(i => { const k = i.staff_name || "(空白)"; if (!byName.has(k)) byName.set(k, []); byName.get(k).push(i); });
    const people = new Map(z.people.map(p => [p.name, p]));
    const rank = (n) => (people.has(n) ? z.people.indexOf(people.get(n)) : 999);   // 照 /people 的順序（在職 → 兼職 → 不在清單的）
    const order = [...byName.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b, "zh-Hant"));
    host.innerHTML = _logHeadHtml(isToday) + (isToday ? _absentStripHtml(board) : "") + (order.length ? order.map((n, k) => {
        const p = people.get(n), items = byName.get(n);
        const hrs = items.reduce((a, i) => a + (i.hours || 0), 0);
        return `<div class="team-day"><div class="td-h"><b>${esc(n)}</b><span class="meta">${items.length} 列 · ${Math.round(hrs * 10) / 10} h</span>
            ${p ? `<button type="button" class="btn sm" data-z1="who" data-id="${esc(p.id)}" data-name="${esc(p.name)}">${p.id === (z.me || {}).id ? "看我的" : "替他填"}</button>` : ""}</div>
            <div class="td-sheet" data-k="${k}"></div></div>`;
    }).join("") : `<div class="empty">${esc(_mdLabel(s.logDay))} 還沒有人填。</div>`);
    order.forEach((n, k) => {
        const el = host.querySelector(`.td-sheet[data-k="${k}"]`);
        if (el) renderSheet(el, byName.get(n), { id: "team-sheet-" + k, readonly: true, workTypes: s.logWorkTypes, stages: s.logStages });
    });
}
export async function loadLog() {
    const { $, esc, mjson, s } = z;
    const host = $("z1-log");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    if (z.manage && !z.who) return _loadTeamDay();        // 管理視角「全部」＝團隊當天，唯讀
    const isToday = s.logDay === z.today();
    const other = z.manage && !whoIsMe();                 // 替別人填：沒有「我的今天」那條、沒有未完成提醒、沒有合併同案
    let mine = null, err = "", incomplete = [];
    // 四支互不相依，一起發（原本串著等，翻一天要吃四趟來回）；未完成提醒只畫在今天那條，翻到別天不抓
    const [today, mineRes, incRes, opts] = await Promise.all([
        _loadToday(),
        mjson(z.api.mineDay(s.logDay)).catch(e => ({ __err: e.message })),
        // 近 30 天存了草稿卻沒填時數的日期 → 今天那條提醒「9/5 專案紀錄未完成」（owner 2026-09-07）
        isToday && z.api.mineIncomplete() ? mjson(z.api.mineIncomplete()).catch(() => ({ days: [] })) : Promise.resolve({ days: [] }),
        s.logStages === null ? mjson(z.api.options()).catch(() => ({ stages: {} })) : Promise.resolve(null),
    ]);
    if (mineRes && mineRes.__err) err = mineRes.__err; else mine = mineRes;
    incomplete = (incRes.days || []).filter(d => d.date !== z.today());
    if (opts) s.logStages = opts.stages || {};
    if (mine) s.logWorkTypes = mine.work_types || s.logWorkTypes;
    const canEdit = !mine || mine.editable !== false;     // own-scope 沒有 editable 欄；管理端點只給管理員 true
    host.innerHTML = `
        ${_logHeadHtml(isToday)}
        ${isToday && today ? _todayStripHtml(today, incomplete, mine ? (mine.items || []).filter(_isPlan).length : 0) : ""}
        ${other && mine ? `<div class="today-strip"><span><span class="k">替他填</span>${esc(z.who.name)}${canEdit ? "" : "（你不是管理員：只能看）"}</span><span class="meta">存進去的列跟他自己填的一樣（來源手填），他在員工頁看得到、也改得動。</span></div>` : ""}
        ${mine ? `
        <div id="z1-sheet"></div>
        <div class="sheet-foot">
            <span class="vrow"><button type="button" class="btn" data-z1="stages">工作階段設定</button>
                ${canEdit ? `<button type="button" class="btn" data-z1="row-add">＋ 加五列</button>
                <button type="button" class="btn pri" data-z1="save">儲存草稿</button>` : ""}
                ${z.api.merge() ? `<button type="button" class="btn" data-z1="merge" title="同案、同分類、同階段的列併成一列：時數相加、內容去重">合併同案</button>
                <button type="button" class="btn" data-z1="unmerge" id="z1-unmerge" hidden title="把最近一次合併退回去">復原合併</button>` : ""}
                <span id="z1-log-msg" class="meta"></span></span>
        </div>
        <div class="sheet-note">一列＝一個案子做了什麼；填了任何一格就自動存成草稿，時數填了才進彙整。藍底的列是「我的一週」排的（狀態「計畫」，可以按「隔天」挪過去）。起訖用 24 小時制（打「9」「930」「1730」都可以），會自己算出實際 h。
            工作階段只列該列分類的階段。往前翻可以補記；Sheet 拉進來的舊列也可以直接改（一改就轉成你自己的手填列，不會再被試算表蓋回去）。</div>`
        : `<div class="notice">${esc(err || "載入失敗")}<br><span class="meta">要記專案紀錄，帳號要在「使用者管理」綁定人員檔案並開通工作紀錄。</span></div>`}`;
    if (!mine) return;
    const sheet = $("z1-sheet");
    renderSheet(sheet, mine.items || [], { id: "my-log-sheet", readonly: !canEdit, workTypes: s.logWorkTypes, stages: s.logStages,
        projectPicker: z.hooks.projectPicker === undefined ? _logProjectOptions : z.hooks.projectPicker });   // CRM 分頁整個 tab 已掛一份浮層，不能再掛
    if (!canEdit) return;
    wireAutosave(sheet, { day: () => s.logDay, projects: () => s.logProjects || [], endpoints: (z.api.sheetEndpoints && z.api.sheetEndpoints()) || null,
        // 格子與「我的一週」是同一批列：這裡存了（填時數、改案名）＝那邊要重抓，不然切過去還是舊卡
        onSaved: () => _z1MarkStale("z1-plan", "z1-week"),
        onUnmatched: (names) => { const el = $("z1-log-msg"); if (el) el.textContent = `「${names.join("、")}」對不到案（已存下來，管理員會指定）`; } });
    _logProjectOptions();   // 先抓好，專案格一點就有得選
    _refreshMergeBtn();
}
/** 「復原合併」只在這一天有還沒復原的合併紀錄時才出現（owner 2026-09-07：合併要能復原）。 */
async function _refreshMergeBtn() {
    const btn = z.$("z1-unmerge");
    if (!btn || !z.api.mergeLast(z.s.logDay)) return;
    try {
        const d = await z.mjson(z.api.mergeLast(z.s.logDay));
        btn.hidden = !(d && d.last);
        btn.dataset.logId = d && d.last ? d.last.log_id : "";
    } catch (_) { btn.hidden = true; }
}
/** 合併同案：先 dry_run 拿預覽 → 確認 → 正式併（同案同分類同階段；時數相加、內容去重、起訖清空；可復原）。 */
export async function mergeSameProject() {
    const { $, mjson, s } = z;
    const msg = $("z1-log-msg");
    try {
        const pv = await mjson(z.api.merge(), _POST({ date: s.logDay, dry_run: true }));
        if (!pv.groups.length) { if (msg) msg.textContent = "沒有可以合併的列（要同案、同分類、同階段，而且都填了時數）"; return; }
        const lines = pv.groups.map(g => `${g.label}：${g.count} 列 → 1 列，${g.hours} h`).join("\n");
        if (!window.confirm(`會這樣合併（合併後可以按「復原合併」退回）：\n\n${lines}\n\n不動：${pv.skipped} 列`)) return;
        const r = await mjson(z.api.merge(), _POST({ date: s.logDay, dry_run: false }));
        _z1MarkStale("z1-plan", "z1-week");
        await loadLog();
        const m2 = $("z1-log-msg"); if (m2) m2.textContent = `已合併 ${r.groups.length} 組，可以直接改內容；要退回按「復原合併」`;
    } catch (e) { if (msg) msg.textContent = "合併失敗：" + e.message; }
}
export async function undoMerge(logId) {
    const { $, mjson } = z;
    if (!logId) return;
    if (!window.confirm("復原最近一次合併？被併掉的列會放回來，合併後對那幾列的修改會被蓋掉。")) return;
    try { await mjson(z.api.mergeUndo(), _POST({ log_id: logId })); _z1MarkStale("z1-plan", "z1-week"); await loadLog(); const m = $("z1-log-msg"); if (m) m.textContent = "已復原合併"; }
    catch (e) { const m = $("z1-log-msg"); if (m) m.textContent = "復原失敗：" + e.message; }
}
