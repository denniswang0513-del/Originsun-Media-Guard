// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 兼職排班視窗（有 me_plan_parttime 的正職幫「兼職」排他的一週；走 /timesheets/plan-for/{staff_id}）
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、mfetch、mjson、_shiftDays、_mondayOf、_mdLabel、_localToday、_POST、_PUT、_logProjectOptions、_planCardHtml、_isPlan（zone1.js 轉接到 js/shared/ts-zone）
// 跨檔提供：_pt（狀態物件）、_ptApi、_ptDays、_ptNote、_ptMsg、_ptOpen、_pt* 家族（zone1.js 的 _z1Action 會叫）
// ────────────────────────────────────────────────────────────────────────────
// ── 兼職排班視窗（owner 2026-09-08）：有 me_plan_parttime 的正職幫「兼職」排他的一週 ──
// 同一套板（pboard／pcol／pcard 的 CSS），資料走 /timesheets/plan-for/{staff_id}（只碰計畫列、時數不收；守衛在後端）。
// 兼職那邊完全不變：卡出現在他的格子與團隊的一週，卡上帶「由 X 排」。
const _pt = { week: _mondayOf(_localToday()), sid: "", targets: null, rows: [], name: "" };
const _ptApi = (p) => `/api/v1/timesheets/plan-for/${encodeURIComponent(_pt.sid)}${p}`;
const _ptDays = () => [...Array(7)].map((_, k) => _shiftDays(_pt.week, k));
const _ptNote = (cls, msg) => `<div class="ws-modal"><button type="button" class="ws-modal-x" data-z1="pt-close" title="關閉">×</button><div class="${cls}">${msg}</div></div>`;   // 視窗的錯誤／空狀態殼（三處共用）
function _ptMsg(text) { const m = $("pt-msg"); if (m) m.textContent = text || ""; }
async function _ptOpen() {
    const root = $("ws-view") || document.body;
    let bg = $("ws-pt-modal");
    if (!bg) {
        bg = document.createElement("div");
        bg.id = "ws-pt-modal"; bg.className = "ws-modal-bg";
        // 視窗掛在 #ws-view（不在 zone1 裡），data-z1 的委派要自己接一份
        bg.addEventListener("click", (e) => { if (e.target === bg) { bg.remove(); _z1MarkStale("z1-week"); return; } const b = e.target.closest("[data-z1]"); if (b) _z1Action(b, e); });
        bg.addEventListener("change", (e) => { if (e.target.id === "pt-target") { _pt.sid = e.target.value; _ptLoad(); } });
        bg.addEventListener("keydown", (e) => { const f = e.target.closest(".addform"); if (f && e.key === "Enter") { e.preventDefault(); _ptSubmitAdd(f.dataset.day); } });
        _ptWireDnd(bg);
        root.appendChild(bg);
    }
    bg.innerHTML = `<div class="ws-modal"><div class="empty">載入中…</div></div>`;
    if (_pt.targets === null) {
        try { _pt.targets = (await mjson("/api/v1/timesheets/plan-for/targets")).targets || []; }
        catch (e) { bg.innerHTML = _ptNote("notice", `${esc(e.message)}`); return; }
    }
    if (!_pt.targets.length) { bg.innerHTML = _ptNote("empty", `人力庫裡沒有狀態是「兼職」的人。`); return; }
    if (!_pt.sid || !_pt.targets.some(t => t.id === _pt.sid)) _pt.sid = _pt.targets[0].id;
    await _ptLoad();
}
async function _ptLoad() {
    const days = _ptDays();
    try {
        const d = await mjson(_ptApi(`/rows?from=${days[0]}&to=${days[6]}`));
        _pt.rows = d.items || []; _pt.name = d.staff_name || "";
    } catch (e) {
        const bg = $("ws-pt-modal"); if (bg) bg.innerHTML = _ptNote("notice", `${esc(e.message)}`);
        return;
    }
    _logProjectOptions();
    _ptRender();
}
function _ptCardHtml(i) {
    const plan = _isPlan(i), done = i.hours > 0;
    const stage = [i.work_type, i.stage_name].filter(Boolean).join(" · ");
    const foot = done ? `<span class="s done">${i.hours} h</span>` : (plan ? '<span class="s plan">計畫</span>' : '<span class="s">草稿</span>');
    const by = i.planned_by ? `<span class="from">由 ${esc(i.planned_by)} 排</span>` : (plan ? '<span class="from">他自己排的</span>' : "");
    return `<div class="pcard${plan ? " plan" : ""}${done ? " done" : ""}" draggable="${plan}" data-ptcard="${esc(i.id)}" ${done ? 'title="他自己填的實際紀錄，這裡不能動"' : ""}>
        ${plan ? `<button type="button" class="x" data-z1="pt-del" data-id="${esc(i.id)}" title="刪掉這張卡">×</button>` : ""}
        <div class="p">${i.project_name ? esc(i.project_name) : '<span class="meta">未填專案</span>'}</div>
        ${i.task_note ? `<div class="t">${esc(i.task_note)}</div>` : ""}${stage ? `<div class="ct">${esc(stage)}</div>` : ""}
        <div class="f">${foot}${by}${plan ? `<button type="button" class="btn xs" data-z1="pt-defer" data-id="${esc(i.id)}">挪到隔天</button>` : ""}</div></div>`;
}
function _ptRender() {
    const bg = $("ws-pt-modal"); if (!bg) return;
    const today = _localToday(), days = _ptDays();
    const byDay = (d) => _pt.rows.filter(i => i.date === d);
    const cols = days.filter(d => { const w = _dow(d); return (w !== 0 && w !== 6) || byDay(d).length; });
    const sel = _pt.targets.length > 1
        ? `<select id="pt-target" class="sel">${_pt.targets.map(t => `<option value="${esc(t.id)}" ${t.id === _pt.sid ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select>`
        : `<b>${esc(_pt.name)}</b>`;
    bg.innerHTML = `<div class="ws-modal"><button type="button" class="ws-modal-x" data-z1="pt-close" title="關閉">×</button>
        <div class="vhead"><span class="ey">Part-time Plan<b>兼職排班</b></span>
            <span class="vrow"><span class="meta">兼職：</span>${sel}
                <button type="button" class="btn" data-z1="pt-week" data-start="${_shiftDays(_pt.week, -7)}">‹</button>
                <span class="meta">${esc(_mdLabel(days[0]).slice(0, -3))} – ${esc(_mdLabel(days[6]).slice(0, -3))}</span>
                <button type="button" class="btn" data-z1="pt-week" data-start="${_shiftDays(_pt.week, 7)}">›</button>
                ${_pt.week === _mondayOf(today) ? "" : '<button type="button" class="btn" data-z1="pt-week">本週</button>'}
                <button type="button" class="btn" data-z1="pt-from-ms" title="這週指定給他的里程碑，落在到期那天">從里程碑帶入</button>
                <button type="button" class="btn" data-z1="pt-copy-last" title="他上週的列照星期幾貼到這週（不帶時數）">複製上週</button></span></div>
        <div class="wk-sum"><span><span class="k">幫 ${esc(_pt.name)} 排了</span>${_pt.rows.filter(_isPlan).length} 項</span><span class="meta" id="pt-msg">加了就存、拖了就存；時數由他自己在格子裡填，這裡不碰。</span></div>
        <div class="pboard" style="grid-template-columns:repeat(${cols.length}, minmax(0, 1fr));">${cols.map(d => `
            <div class="pcol${d === today ? " today" : (d < today ? " past" : "")}" data-ptday="${d}">
                <div class="dh"><span>${esc(_mdLabel(d))}</span>${d === today ? "<small>今天</small>" : ""}</div>
                <div class="cards">${byDay(d).map(_ptCardHtml).join("") || '<div class="none">沒排</div>'}</div>
                <div class="add" data-ptadd="${d}"><button type="button" class="addbtn" data-z1="pt-add" data-day="${d}">加一項</button></div>
            </div>`).join("")}</div>
        <div class="sheet-note">卡會出現在他的「今天的專案紀錄」格子（藍底、狀態「計畫」）與團隊的一週，卡上帶「由你排」。他自己也能挪、能刪、能填時數。</div></div>`;
}
async function _ptOpenAdd(day) {
    const host = document.querySelector(`#ws-pt-modal .add[data-ptadd="${day}"]`); if (!host) return;
    const projects = await _logProjectOptions();
    host.innerHTML = `<form class="addform" data-day="${esc(day)}" onsubmit="return false">
        <input list="pt-plan-projects" data-f="project" placeholder="專案（打字找）" autocomplete="off">
        <datalist id="pt-plan-projects">${projects.map(p => `<option value="${esc(p.label || p.name)}"></option>`).join("")}</datalist>
        <input data-f="note" placeholder="要做什麼（Enter 加入）">
        <div class="fr"><button type="button" class="btn xs" data-z1="pt-add-cancel">取消</button><button type="button" class="btn xs pri" data-z1="pt-add-ok" data-day="${esc(day)}">加入</button></div></form>`;
    host.querySelector('[data-f="project"]').focus();
}
async function _ptSubmitAdd(day) {
    const form = document.querySelector(`#ws-pt-modal .addform[data-day="${day}"]`);
    if (!form || form.dataset.busy) return;
    const text = form.querySelector('[data-f="project"]').value.trim(), note = form.querySelector('[data-f="note"]').value.trim();
    if (!text && !note) { form.querySelector('[data-f="project"]').focus(); return; }
    form.dataset.busy = "1";
    const hit = (await _logProjectOptions()).find(p => p.id && (p.label === text || p.name === text));
    await _ptCreate([{ work_date: day, project_id: hit ? hit.id : null, project_name: hit ? hit.name : text, task_note: note, plan: true }]);
    if (form.isConnected) delete form.dataset.busy;
}
async function _ptCreate(rows) {
    if (!rows.length) return 0;
    try {
        const r = await mjson(_ptApi("/rows"), _POST({ rows }));
        await _ptLoad();
        if ((r.unmatched_projects || []).length) _ptMsg(`「${r.unmatched_projects.join("、")}」對不到案（已存下來，管理員會指定）`);
        return rows.length;
    } catch (e) { _ptMsg("沒存：" + e.message); return 0; }
}
async function _ptMove(id, day) {
    const i = _pt.rows.find(x => x.id === id);
    if (!i || i.date === day) return;
    try { await mjson(_ptApi("/" + encodeURIComponent(id)), _PUT({ work_date: day })); }
    catch (e) { _ptMsg("沒挪：" + e.message); return; }
    if (_mondayOf(day) !== _pt.week) _pt.week = _mondayOf(day);
    await _ptLoad();
}
async function _ptDelete(id) {
    if (!window.confirm("刪掉這張卡？")) return;
    try { await mjson(_ptApi("/" + encodeURIComponent(id)), { method: "DELETE" }); }
    catch (e) { _ptMsg("沒刪：" + e.message); return; }
    await _ptLoad();
}
async function _ptFromMilestones() {
    let ms;
    try { ms = await mjson("/api/v1/milestones/week?start=" + _pt.week); } catch (e) { _ptMsg(e.message); return; }
    const days = _ptDays(), rows = [];
    (ms.projects || []).forEach(p => (p.milestones || []).forEach(m => {
        if (m.done || (m.assignee_name || "") !== _pt.name) return;
        const day = days.includes(m.due_date) ? m.due_date : ms.default_due;
        if (_pt.rows.some(i => i.date === day && (i.project_id === p.project_id || i.project_name === p.name) && (i.task_note || "") === m.title)) return;
        rows.push({ work_date: day, project_id: p.project_id, project_name: p.name, task_note: m.title, plan: true });
    }));
    if (!rows.length) { _ptMsg(`這週指定給 ${_pt.name} 的里程碑都已經在板上了（或沒有指定給他的）。`); return; }
    const n = await _ptCreate(rows); if (n) _ptMsg(`從里程碑帶入 ${n} 張`);
}
async function _ptCopyLast() {
    const prev = _shiftDays(_pt.week, -7);
    let r;
    try { r = await mjson(_ptApi(`/rows?from=${prev}&to=${_shiftDays(prev, 6)}`)); } catch (e) { _ptMsg(e.message); return; }
    const rows = [];
    (r.items || []).forEach(i => {
        if (!(i.hours > 0) && !_isPlan(i)) return;
        const day = _shiftDays(i.date, 7);
        if (rows.some(x => x.work_date === day && x.project_name === i.project_name && x.task_note === (i.task_note || ""))) return;
        if (_pt.rows.some(x => x.date === day && x.project_name === i.project_name && (x.task_note || "") === (i.task_note || ""))) return;
        rows.push({ work_date: day, project_id: i.project_id || null, project_name: i.project_name, task_note: i.task_note || "", work_type: i.work_type || null, plan: true });
    });
    if (!rows.length) { _ptMsg("上週沒有可以抄的列（或都已經在板上了）。"); return; }
    if (!window.confirm(`把 ${_pt.name} 上週 ${rows.length} 列照星期幾貼到這週（不帶時數）？`)) return;
    const n = await _ptCreate(rows); if (n) _ptMsg(`從上週複製 ${n} 張`);
}
function _ptWireDnd(bg) {
    let dragId = null;
    bg.addEventListener("dragstart", (e) => { const c = e.target.closest && e.target.closest(".pcard[draggable='true'][data-ptcard]"); if (!c) return; dragId = c.dataset.ptcard; c.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    bg.addEventListener("dragend", () => { dragId = null; bg.querySelectorAll(".pcol.over, .pcard.dragging").forEach(x => x.classList.remove("over", "dragging")); });
    bg.addEventListener("dragover", (e) => { const col = e.target.closest && e.target.closest(".pcol[data-ptday]"); if (!col || !dragId) return; e.preventDefault(); bg.querySelectorAll(".pcol.over").forEach(x => x.classList.remove("over")); col.classList.add("over"); });
    bg.addEventListener("drop", (e) => { const col = e.target.closest && e.target.closest(".pcol[data-ptday]"); if (!col || !dragId) return; e.preventDefault(); const id = dragId; dragId = null; _ptMove(id, col.dataset.ptday); });
}
