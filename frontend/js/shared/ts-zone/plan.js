// ────────────────────────────────────────────────────────────────────────────
// ts-zone 視圖 1.5：我的一週（個人週規劃板；卡＝一列工時 status=plan、沒時數）
// owner 2026-09-08；示範 /demo/week-plan.html 定稿：一天一欄的規劃板。
// 一張卡＝一列工時（status=plan、沒時數；POST /timesheets/mine/rows 帶 plan:true）。當天的卡自動就在「今天的專案紀錄」的格子裡，
// 時數在那裡填、填了就是一般紀錄。不判有做沒做；執行動作只有填時數與「挪到隔天」（PUT work_date）。只能排自己的。
// ────────────────────────────────────────────────────────────────────────────
import { z, _z1MarkStale, _shiftDays, _dow, _mondayOf, _mdLabel, _isPlan, _POST, _PUT } from "./ctx.js";
import { _logProjectOptions } from "./log.js";

function _planDays() { return [...Array(7)].map((_, k) => _shiftDays(z.s.planWeek, k)); }
export async function loadMyWeek() {
    const { $, esc, mjson, s } = z;
    const host = $("z1-plan");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    const days = _planDays();
    try {
        s.planRows = (await mjson(z.api.mineRows(days[0], days[6]))).items || [];
    } catch (e) {
        host.innerHTML = `<div class="notice">${esc(e.message)}<br><span class="meta">要排我的一週，帳號要在「使用者管理」綁定人員檔案並開通工作紀錄。</span></div>`;
        return;
    }
    _logProjectOptions();    // 加卡的專案清單先抓好
    _renderMyWeek();
}
/** 一張卡的 html（兼職排班視窗也用同一份）。 */
export function _planCardHtml(i) {
    const esc = z.esc;
    const plan = _isPlan(i), done = i.hours > 0;
    const stage = [i.work_type, i.stage_name].filter(Boolean).join(" · ");
    const foot = done ? `<span class="s done">${i.hours} h</span>` : (plan ? '<span class="s plan">計畫</span>' : `<span class="s">${i.source && i.source !== "manual" ? "Sheet" : "草稿（沒時數）"}</span>`);
    const by = i.planned_by ? `<span class="from" title="兼職排班：正職幫排的">由 ${esc(i.planned_by)} 排</span>` : "";   // 兼職看得到是誰幫他排的
    return `<div class="pcard${plan ? " plan" : ""}${done ? " done" : ""}" draggable="${plan}" data-card="${esc(i.id)}">
        ${plan ? `<button type="button" class="x" data-z1="plan-del" data-id="${esc(i.id)}" title="刪掉這張卡">×</button>` : ""}
        <div class="p">${i.project_name ? esc(i.project_name) : '<span class="meta">未填專案</span>'}</div>
        ${i.task_note ? `<div class="t">${esc(i.task_note)}</div>` : ""}${stage ? `<div class="ct">${esc(stage)}</div>` : ""}
        <div class="f">${foot}${by}${plan ? `<button type="button" class="btn xs" data-z1="plan-defer" data-id="${esc(i.id)}">挪到隔天</button>` : ""}</div></div>`;
}
export function _renderMyWeek() {
    const { $, esc, s } = z;
    const host = $("z1-plan"), today = z.today(), days = _planDays();
    const byDay = (d) => s.planRows.filter(i => i.date === d);
    const cols = days.filter(d => { const w = _dow(d); return (w !== 0 && w !== 6) || byDay(d).length; });   // 週末有排才畫
    const filled = s.planRows.filter(i => i.hours > 0).length;
    host.innerHTML = `
        <div class="vhead"><span class="ey">My Week<b>我的一週 ${esc(_mdLabel(days[0]).slice(0, -3))} – ${esc(_mdLabel(days[6]).slice(0, -3))}</b></span>
            <span class="vrow"><button type="button" class="btn" data-z1="plan-week" data-start="${_shiftDays(s.planWeek, -7)}">‹</button>
                <span class="meta">${esc(s.planWeek)} 起</span>
                <button type="button" class="btn" data-z1="plan-week" data-start="${_shiftDays(s.planWeek, 7)}">›</button>
                ${s.planWeek === _mondayOf(today) ? "" : '<button type="button" class="btn" data-z1="plan-week">本週</button>'}
                <button type="button" class="btn" data-z1="plan-from-ms" title="這週指定給我的里程碑，落在到期那天">從里程碑帶入</button>
                <button type="button" class="btn" data-z1="plan-copy-last" title="上週的列照星期幾貼到這週（不帶時數）">複製上週</button></span></div>
        <div class="wk-sum"><span><span class="k">這週排了</span>${s.planRows.length} 項</span><span><span class="k">填了時數</span>${filled} 項</span><span class="meta" id="z1-plan-msg">只看排了什麼、填到哪；不判有做沒做。</span></div>
        <div class="pboard" style="grid-template-columns:repeat(${cols.length}, minmax(0, 1fr));">${cols.map(d => `
            <div class="pcol${d === today ? " today" : (d < today ? " past" : "")}" data-day="${d}">
                <div class="dh"><span>${esc(_mdLabel(d))}</span>${d === today ? "<small>今天</small>" : ""}</div>
                <div class="cards">${byDay(d).map(_planCardHtml).join("") || '<div class="none">沒排</div>'}</div>
                <div class="add" data-add="${d}"><button type="button" class="addbtn" data-z1="plan-add" data-day="${d}">加一項</button></div>
            </div>`).join("")}</div>
        <div class="sheet-note">拖卡片到別的日子；卡片右上的 × 刪；還沒填時數的卡可以「挪到隔天」。當天的卡會自動出現在「今天的專案紀錄」的格子裡（藍底、狀態「計畫」），時數在那裡填。</div>`;
}
export async function _planOpenAdd(day) {
    const esc = z.esc;
    const host = z.host.querySelector(`#z1-plan .add[data-add="${day}"]`);
    if (!host) return;
    const projects = await _logProjectOptions();
    host.innerHTML = `<form class="addform" data-day="${esc(day)}" onsubmit="return false">
        <input list="z1-plan-projects" data-f="project" placeholder="專案（打字找）" autocomplete="off">
        <datalist id="z1-plan-projects">${projects.map(p => `<option value="${esc(p.label || p.name)}"></option>`).join("")}</datalist>
        <input data-f="note" placeholder="要做什麼（Enter 加入）">
        <div class="fr"><button type="button" class="btn xs" data-z1="plan-add-cancel">取消</button><button type="button" class="btn xs pri" data-z1="plan-add-ok" data-day="${esc(day)}">加入</button></div></form>`;
    host.querySelector('[data-f="project"]').focus();
}
export async function _planSubmitAdd(day) {
    const form = z.host.querySelector(`#z1-plan .addform[data-day="${day}"]`);
    if (!form || form.dataset.busy) return;      // 建立中：Enter 連按兩下會建出兩張一樣的卡
    const text = form.querySelector('[data-f="project"]').value.trim(), note = form.querySelector('[data-f="note"]').value.trim();
    if (!text && !note) { form.querySelector('[data-f="project"]').focus(); return; }
    form.dataset.busy = "1";
    const hit = (z.s.logProjects || []).find(p => p.id && (p.label === text || p.name === text));   // 對得到清單的帶 id；對不到照打的字送（後端再對映）
    await _planCreate([{ work_date: day, project_id: hit ? hit.id : null, project_name: hit ? hit.name : text, task_note: note, plan: true }]);
    if (form.isConnected) delete form.dataset.busy;   // 建成功會重畫整個板（表單不在了）；失敗時要放行讓人重試
}
/** 一次建幾張卡（status=plan、沒時數）；建完整週重抓。回建了幾張。 */
async function _planCreate(rows) {
    const { $, mjson } = z;
    if (!rows.length) return 0;
    try {
        const r = await mjson(z.api.mineCreate(), _POST({ rows }));
        await loadMyWeek();
        _z1MarkStale("z1-log", "z1-week");
        if ((r.unmatched_projects || []).length) { const m = $("z1-plan-msg"); if (m) m.textContent = `「${r.unmatched_projects.join("、")}」對不到案（已存下來，管理員會指定）`; }
        return rows.length;
    } catch (e) { const m = $("z1-plan-msg"); if (m) m.textContent = "沒存：" + e.message; return 0; }
}
export async function _planMove(id, day) {
    const { $, mjson, s } = z;
    const i = s.planRows.find(x => x.id === id);
    if (!i || i.date === day) return;
    try { await mjson(z.api.mineRow(id), _PUT({ work_date: day })); }
    catch (e) { const m = $("z1-plan-msg"); if (m) m.textContent = "沒挪：" + e.message; return; }
    if (_mondayOf(day) !== s.planWeek) s.planWeek = _mondayOf(day);   // 週日挪到隔天＝下週一，板子跟著翻過去
    _z1MarkStale("z1-log", "z1-week");
    await loadMyWeek();
}
export async function _planDelete(id) {
    const { $, mjson } = z;
    if (!window.confirm("刪掉這張卡？")) return;
    try { await mjson(z.api.mineRow(id), { method: "DELETE" }); }
    catch (e) { const m = $("z1-plan-msg"); if (m) m.textContent = "沒刪：" + e.message; return; }
    _z1MarkStale("z1-log", "z1-week");
    await loadMyWeek();
}
async function _myName() {
    if (z.s.myStaffName) return z.s.myStaffName;
    try { z.s.myStaffName = (await z.mjson(z.api.mineDay(z.today()))).staff_name || ""; } catch (_) {}
    return z.s.myStaffName;
}
/** 從里程碑帶入：這週指定給我、還沒完成的里程碑 → 到期那天一張卡（到期不在這週就落預設到期日）；已在板上的不重複。 */
export async function _planFromMilestones() {
    const { $, mjson, s } = z;
    const msg = $("z1-plan-msg");
    let ms;
    try { ms = await mjson(z.api.milestonesWeek(s.planWeek)); } catch (e) { if (msg) msg.textContent = e.message; return; }
    const me = await _myName(), days = _planDays(), rows = [];
    (ms.projects || []).forEach(p => (p.milestones || []).forEach(m => {
        if (m.done || !me || (m.assignee_name || "") !== me) return;
        const day = days.includes(m.due_date) ? m.due_date : ms.default_due;
        if (s.planRows.some(i => i.date === day && (i.project_id === p.project_id || i.project_name === p.name) && (i.task_note || "") === m.title)) return;
        rows.push({ work_date: day, project_id: p.project_id, project_name: p.name, task_note: m.title, plan: true });
    }));
    if (!rows.length) { if (msg) msg.textContent = me ? "這週指定給你的里程碑都已經在板上了（或沒有指定給你的）。" : "抓不到你的人員名字，帶不了。"; return; }
    const n = await _planCreate(rows);
    if (n) { const m = $("z1-plan-msg"); if (m) m.textContent = `從里程碑帶入 ${n} 張`; }
}
/** 複製上週：上週有時數或是計畫的列，照星期幾貼到這週（案、分類、做了什麼；不帶時數、不帶階段）；已在板上的不重複。 */
export async function _planCopyLast() {
    const { $, mjson, s } = z;
    const msg = $("z1-plan-msg"), prev = _shiftDays(s.planWeek, -7);
    let r;
    try { r = await mjson(z.api.mineRows(prev, _shiftDays(prev, 6))); } catch (e) { if (msg) msg.textContent = e.message; return; }
    const rows = [];
    (r.items || []).forEach(i => {
        if (!(i.hours > 0) && !_isPlan(i)) return;
        const day = _shiftDays(i.date, 7);
        if (rows.some(x => x.work_date === day && x.project_name === i.project_name && x.task_note === (i.task_note || ""))) return;
        if (s.planRows.some(x => x.date === day && x.project_name === i.project_name && (x.task_note || "") === (i.task_note || ""))) return;
        rows.push({ work_date: day, project_id: i.project_id || null, project_name: i.project_name, task_note: i.task_note || "", work_type: i.work_type || null, plan: true });
    });
    if (!rows.length) { if (msg) msg.textContent = "上週沒有可以抄的列（或都已經在板上了）。"; return; }
    if (!window.confirm(`把上週 ${rows.length} 列照星期幾貼到這週（不帶時數）？`)) return;
    const n = await _planCreate(rows);
    if (n) { const m = $("z1-plan-msg"); if (m) m.textContent = `從上週複製 ${n} 張`; }
}
/** 拖卡到別的日子（只有計畫卡能拖；HTML5 drag，掛在第一區的容器上一次）。 */
export function _planWireDnd(host) {
    let dragId = null;
    host.addEventListener("dragstart", (e) => { const c = e.target.closest && e.target.closest("#z1-plan .pcard[draggable='true']"); if (!c) return; dragId = c.dataset.card; c.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    host.addEventListener("dragend", () => { dragId = null; host.querySelectorAll(".pcol.over, .pcard.dragging").forEach(x => x.classList.remove("over", "dragging")); });
    host.addEventListener("dragover", (e) => { const col = e.target.closest && e.target.closest("#z1-plan .pcol"); if (!col || !dragId) return; e.preventDefault(); host.querySelectorAll(".pcol.over").forEach(x => x.classList.remove("over")); col.classList.add("over"); });
    host.addEventListener("drop", (e) => { const col = e.target.closest && e.target.closest("#z1-plan .pcol"); if (!col || !dragId) return; e.preventDefault(); const id = dragId; dragId = null; _planMove(id, col.dataset.day); });
}
