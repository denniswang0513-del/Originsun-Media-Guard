// ────────────────────────────────────────────────────────────────────────────
// ts-zone 視圖 2：團隊的一週（人×日，案名＋小時＋內容；計畫淺灰、場次藍、休假灰）＋ 本週里程碑（帶＋設定彈窗）
// ────────────────────────────────────────────────────────────────────────────
import { z, _shiftDays, _dow, _mondayOf, _mdLabel, _POST } from "./ctx.js";
import { _logProjectOptions, resetToday } from "./log.js";

export async function loadTeamWeek() {
    const { $, esc, mjson, s } = z;
    const host = $("z1-week");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    // 兩支互不相依：一起發（原本串著等，翻一週要吃兩趟來回）。里程碑抓不到就沒有那條帶，不擋週表
    const [dRes, ms, conflicts] = await Promise.all([
        mjson(z.api.teamWeek(s.week)).catch(e => ({ __err: e.message })),
        mjson(z.api.milestonesWeek(s.week)).catch(() => null),
        // 管理視角：Sheet 與總表改過的同一列撞到、等 owner 決定的（管理員才拉得到；決定在總表）
        z.manage && z.api.conflicts && z.hooks.isAdmin() ? mjson(z.api.conflicts()).catch(() => null) : Promise.resolve(null),
    ]);
    const nConf = conflicts && conflicts.items ? conflicts.items.length : 0;
    if (dRes && dRes.__err) { host.innerHTML = `<div class="notice">${esc(dRes.__err)}</div>`; return; }
    const d = dRes;
    s.msWeek = ms;
    const msOf = (name, iso) => ms ? ms.projects.flatMap(p => p.milestones.filter(m => m.assignee_name === name && m.due_date === iso).map(m => ({ ...m, project_name: p.name }))) : [];
    const days = d.days || [];
    const today = z.today();
    const shootsOf = (iso) => (d.shoots && d.shoots[iso]) || [];
    const leaveOf = (iso) => (d.leave && d.leave[iso]) || [];
    const people = d.people || [];
    const msNames = ms ? ms.projects.flatMap(p => p.milestones.map(m => m.assignee_name).filter(Boolean)) : [];   // 有里程碑的負責人也要有一列
    const names = [...new Set([...people.map(p => p.name), ...days.flatMap(iso => [...shootsOf(iso).flatMap(s => s.crew || []), ...leaveOf(iso)]), ...msNames])];
    const hasAny = (iso) => people.some(p => (p.cells && p.cells[iso] || []).length) || shootsOf(iso).length || leaveOf(iso).length;
    const cols = days.filter(iso => { const w = _dow(iso); return (w !== 0 && w !== 6) || hasAny(iso); });   // 週末只有有東西才畫
    // 管理視角（docs/WORK_TRACKING_V2_PLAN.md §4-5）：每人週合計、今天以前的工作日空白標「未填」（在職／合夥才點名；兼職不）
    const status = new Map(z.people.map(p => [p.name, p.status]));
    const since = new Map(z.people.map(p => [p.name, p.since || ""]));   // 到職日或第一筆工時（/people）；那天之前不點名
    const nagged = (name) => z.manage && status.has(name) && status.get(name) !== "兼職";
    const cellsOf = (name, iso) => { const p = people.find(x => x.name === name); return (p && p.cells && p.cells[iso]) || []; };
    const weekHours = (name) => Math.round(cols.reduce((a, iso) => a + cellsOf(name, iso).reduce((b, i) => b + (i.hours || 0), 0), 0) * 10) / 10;
    const blank = (name, iso) => nagged(name) && iso <= today && iso >= since.get(name) && _dow(iso) !== 0 && _dow(iso) !== 6 && !leaveOf(iso).includes(name)
        && !cellsOf(name, iso).some(i => i.status !== "plan");
    const cell = (name, iso) => {
        const items = cellsOf(name, iso);
        const parts = [];
        if (leaveOf(iso).includes(name)) parts.push('<div class="c off">休假</div>');
        shootsOf(iso).filter(s => (s.crew || []).includes(name)).forEach(s => parts.push(`<div class="c shoot">${esc(s.title || s.project_name || "場次")}<i>${[s.project_name && s.project_name !== s.title ? s.project_name : "", s.location, s.start_time, (s.crew || []).join("、")].filter(Boolean).map(esc).join(" · ")}</i></div>`));
        // 指定了負責人的里程碑：到期那天出現在他的格子（沒指定的只在上方的帶）
        msOf(name, iso).forEach(m => parts.push(`<div class="c ms${m.done ? " done" : ""}">${m.done ? "已完成：" : "里程碑："}${esc(m.title)}<div class="cn">${esc(m.project_name)}</div></div>`));
        // 每筆＝案名（可點開專案檔案）＋右側小時、內容、分類·階段（不放任何個人合計——員工頁的鐵則）
        items.forEach(i => {
            const plan = i.status === "plan";
            const stage = [i.work_type, i.stage_name].filter(Boolean).join(" · ");
            const proj = i.project
                ? `<span class="cp ts-link" data-ts-action="proj-pop" data-name="${esc(i.project)}" data-pid="${esc(i.project_id || "")}" title="點開這個案的執行狀態">${esc(i.project)}</span>`
                : '<span class="cp blank">未填專案</span>';
            // 計畫列（「我的一週」排的多半沒計畫時數）→「計畫」；沒時數的草稿 →「草稿」；其餘實際小時
            const hrs = plan ? `<span class="hrs plan">計畫${i.planned_hours ? " " + i.planned_hours + " h" : ""}</span>`
                : (i.hours > 0 ? `<span class="hrs">${i.hours} h</span>` : '<span class="hrs plan">草稿</span>');
            parts.push(`<div class="c${plan ? " plan" : ""}"><div class="ch">${proj}${hrs}</div>${i.note ? `<div class="cn">${esc(i.note)}</div>` : ""}${stage ? `<div class="ct">${esc(stage)}</div>` : ""}</div>`);
        });
        if (!parts.length && blank(name, iso)) return '<span class="blank-day">未填</span>';
        return parts.join("") || '<span class="none">—</span>';
    };
    const sumTh = z.manage ? '<th class="num sum">週合計</th>' : "";
    const sumTd = (n) => { if (!z.manage) return ""; const h = weekHours(n); return `<td class="num sum${nagged(n) && h < 20 ? " low" : ""}">${h}</td>`; };

    const range = cols.length ? `${_mdLabel(cols[0]).slice(0, -3)} – ${_mdLabel(cols[cols.length - 1]).slice(0, -3)}` : "";
    host.innerHTML = `
        <div class="vhead"><span class="ey">Team Week<b>團隊的一週 ${esc(range)}</b></span>
            <span class="vrow"><button type="button" class="btn" data-z1="week" data-start="${_shiftDays(s.week, -7)}">‹</button>
                <span class="meta">${esc(s.week)} 起</span>
                <button type="button" class="btn" data-z1="week" data-start="${_shiftDays(s.week, 7)}">›</button>
                ${s.week === _mondayOf(today) ? "" : '<button type="button" class="btn" data-z1="week">本週</button>'}
                <button type="button" class="btn pri" data-z1="ms-open">設定專案里程碑</button>
                ${nConf ? `<button type="button" class="btn sm warn" data-ts-action="view" data-view="ledger" title="Sheet 與總表改過的同一列內容不同，到總表選要留哪邊">衝突待決 ${nConf}</button>` : ""}</span></div>
        ${ms ? _msBandHtml(ms) : ""}
        ${names.length ? `<div style="overflow-x:auto;"><table class="week">
            <thead><tr><th style="width:84px;">人員</th>${cols.map(iso => `<th class="${iso === today ? "today" : ""}">${esc(_mdLabel(iso))}</th>`).join("")}${sumTh}</tr></thead>
            <tbody>${names.map(n => `<tr><td class="who">${esc(n)}</td>${cols.map(iso => `<td>${cell(n, iso)}</td>`).join("")}${sumTd(n)}</tr>`).join("")}</tbody>
        </table></div>` : `<div class="empty">這一週還沒有人填。</div>`}`;
}

// ── 本週里程碑（owner 2026-09-07；示範 /demo/milestones.html 定稿）：週表上方的帶＋「設定專案里程碑」彈窗 ──
function _msDue(m) { return m.due_date ? _mdLabel(m.due_date).slice(0, -3) : ""; }
function _msBandHtml(ms) {
    const esc = z.esc;
    const withMs = ms.projects.filter(p => p.milestones.length);
    const total = ms.open_count + ms.done_count;
    const body = withMs.length ? withMs.map(p => `<div class="ms-proj"><div class="pn">${esc(p.name)}<small>${esc(p.client || "")}${p.client ? " · " : ""}本週已投入 ${p.hours_week} h</small></div><div class="ms-list">${p.milestones.map(m => `
        <div class="ms${m.done ? " done" : ""}${m.late ? " late" : ""}${m.carried ? " carried" : ""}"><input type="checkbox" data-ms-done="${esc(m.id)}" ${m.done ? "checked" : ""} title="${m.done ? "取消完成" : "標記完成"}"> ${esc(m.title)} <span class="due">${esc(_msDue(m))}</span>${m.assignee_name ? `<span class="who">· ${esc(m.assignee_name)}</span>` : ""}</div>`).join("")}</div></div>`).join("")
        : `<div class="empty">這週還沒有里程碑。<button type="button" class="linkish" data-z1="ms-open">設定專案里程碑</button></div>`;
    return `<div class="ms-band"><div class="ey"><span>This week's milestones　本週里程碑</span><span>${total ? `${ms.done_count}/${total} 完成${ms.late_count ? ` · 過期 ${ms.late_count}` : ""}` : ""}</span></div>${body}</div>`;
}
export async function _msToggleDone(id, done, cb) {
    if (cb) { if (cb.disabled) return; cb.disabled = true; }      // 送出中不讓再點（重畫會換掉整個節點，不用還原）
    try { await z.mjson(`/api/v1/milestones/${encodeURIComponent(id)}/done`, _POST({ done })); await loadTeamWeek(); resetToday(); }
    catch (e) { alert("更新失敗：" + e.message); await loadTeamWeek(); }
}
export async function _openMsModal() {
    const { $, s } = z;
    // 週表剛抓過同一週就用那份（原本無條件再抓一次＝每開一次彈窗多一支請求／後端多算一整週）
    let ms = s.msWeek && s.msWeek.week_start === s.week ? s.msWeek : null;
    if (!ms) {
        try { ms = await z.mjson(z.api.milestonesWeek(s.week)); } catch (e) { alert("抓不到里程碑：" + e.message); return; }
    }
    s.msWeek = ms;
    s.msDraft = { week_start: ms.week_start, default_due: ms.default_due, staff: ms.staff, projects: ms.projects.map(p => ({ ...p, milestones: p.milestones.map(m => ({ ...m })) })), removed: [] };
    const root = z.hooks.modalRoot();
    let bg = $("ws-ms-modal");
    if (!bg) {
        bg = document.createElement("div"); bg.id = "ws-ms-modal"; bg.className = "ws-modal-bg";
        bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });
        root.appendChild(bg);
    }
    bg.innerHTML = `<div class="ws-modal" style="max-width:980px;"><div id="ws-ms-body"></div></div>`;
    _msRenderModal();
    _logProjectOptions().then(list => { const dl = $("ms-proj-dl"); if (dl) dl.innerHTML = (list || []).filter(p => !p.closed).map(p => `<option value="${z.esc(p.label)}">`).join(""); });
}
function _msRenderModal() {
    const { $, esc } = z;
    const d = z.s.msDraft, host = $("ws-ms-body");
    if (!d || !host) return;
    const wk = d.week_start, wkEnd = _shiftDays(wk, 5);
    // 負責人下拉只有在職人員；已經指定給不在清單裡的人（離職／兼職狀態變了）要多留一個選項把他顯示出來 ——
    // 不然開一次彈窗按儲存，那些里程碑的負責人就被清成「（不指定）」（save_week 是整批覆寫）
    const staffOpts = (sel, name) => {
        const list = d.staff || [];
        const rows = sel && !list.some(s => s.id === sel) ? [...list, { id: sel, name: (name || "") + "（已不在人力庫）" }] : list;
        return `<option value="" ${!sel ? "selected" : ""}>（不指定）</option>` + rows.map(s => `<option value="${esc(s.id)}" ${sel === s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
    };
    host.innerHTML = `
        <div class="msm-hd"><h3>設定專案里程碑</h3><span class="vrow"><button type="button" class="btn" data-ms="wk" data-delta="-7">‹ 上週</button><b>${esc(_mdLabel(wk))} – ${esc(_mdLabel(wkEnd))}</b><button type="button" class="btn" data-ms="wk" data-delta="7">下週 ›</button></span></div>
        <div class="msm-cols"><span></span><span>里程碑</span><span>到期</span><span>負責人</span><span>備註</span><span></span></div>
        ${d.projects.map((p, pi) => `<div class="msm-row" data-pi="${pi}"><div class="rh"><b>${esc(p.name)}</b><span class="meta">${esc(p.client || "")}${p.client ? " · " : ""}本週已投入 <b>${p.hours_week} h</b>${p.milestones.length ? ` · ${p.milestones.filter(m => m.done).length}/${p.milestones.length} 完成` : ""}</span></div>
            <div>${p.milestones.map((m, mi) => `<div class="msm-item${m.done ? " done" : ""}" data-mi="${mi}"><input type="checkbox" data-k="done" ${m.done ? "checked" : ""}><input type="text" data-k="title" value="${esc(m.title)}" placeholder="里程碑"><input type="date" data-k="due_date" value="${esc(m.due_date || "")}"><select data-k="assignee_staff_id">${staffOpts(m.assignee_staff_id, m.assignee_name)}</select><input type="text" data-k="note" value="${esc(m.note || "")}" placeholder="備註"><span><span class="st${m.late ? " late" : ""}">${m.done ? "已完成" : (m.late ? "過期" : (m.carried ? "延自上週" : ""))}</span> ${m.id ? `<button type="button" class="btn sm" data-ms="defer" title="搬到下週">延</button>` : ""} <button type="button" class="btn sm" data-ms="del" title="刪除">×</button></span></div>`).join("")}</div>
            <div class="msm-add"><input type="text" data-ms-add="${pi}" placeholder="加一個里程碑，例：B-copy 給客戶（Enter 也可以）"><button type="button" class="btn sm pri" data-ms="add">加入</button></div></div>`).join("")}
        <div class="msm-addproj"><span class="meta">還沒排的案：</span><input type="text" id="ms-proj-q" list="ms-proj-dl" placeholder="打字找案名"><datalist id="ms-proj-dl"></datalist><button type="button" class="btn sm" data-ms="addproj">加進這週</button></div>
        <div class="msm-ft"><span class="meta">到期預設本週五；負責人可不指定；上週沒完成的已自動帶進來（標「延自上週」）。儲存才寫入。</span>
            <span class="vrow"><button type="button" class="btn" data-ms="cancel">取消</button><button type="button" class="btn pri" data-ms="save">儲存</button></span></div>`;
    if (!host.dataset.wired) {
        host.dataset.wired = "1";
        host.addEventListener("click", (e) => { const b = e.target.closest("[data-ms]"); if (b) _msModalAction(b); });
        host.addEventListener("keydown", (e) => { const inp = e.target.closest("input[data-ms-add]"); if (inp && e.key === "Enter") { e.preventDefault(); _msAdd(Number(inp.dataset.msAdd)); } });
    }
}
function _msSync() {
    // 把彈窗裡的欄位收回 draft（重畫前一定先收，不然打到一半的字會掉）
    const draft = z.s.msDraft;
    document.querySelectorAll("#ws-ms-body .msm-row").forEach(row => {
        const p = draft.projects[Number(row.dataset.pi)];
        row.querySelectorAll(".msm-item").forEach(it => {
            const m = p.milestones[Number(it.dataset.mi)]; if (!m) return;
            const v = (k) => it.querySelector(`[data-k="${k}"]`);
            m.done = v("done").checked; m.title = v("title").value.trim(); m.due_date = v("due_date").value || draft.default_due;
            m.assignee_staff_id = v("assignee_staff_id").value;
            m.assignee_name = (v("assignee_staff_id").selectedOptions[0]?.textContent || "").replace("（不指定）", "").replace("（已不在人力庫）", "");
            m.note = v("note").value.trim();
        });
    });
}
function _msAdd(pi) {
    _msSync();
    const draft = z.s.msDraft;
    const inp = document.querySelector(`#ws-ms-body input[data-ms-add="${pi}"]`); const t = (inp?.value || "").trim(); if (!t) return;
    draft.projects[pi].milestones.push({ id: null, title: t, due_date: draft.default_due, assignee_staff_id: "", assignee_name: "", note: "", done: false, late: false, carried: false });
    _msRenderModal();
    document.querySelector(`#ws-ms-body input[data-ms-add="${pi}"]`)?.focus();
}
async function _msModalAction(btn) {
    const { $, mjson, s } = z;
    const act = btn.dataset.ms, bg = $("ws-ms-modal");
    if (act === "cancel") { bg?.remove(); return; }
    if (act === "wk") { s.week = _shiftDays(s.msDraft.week_start, Number(btn.dataset.delta)); await _openMsModal(); return; }
    if (act === "add") { const row = btn.closest(".msm-row"); return _msAdd(Number(row.dataset.pi)); }
    if (act === "del") {
        _msSync();
        const row = btn.closest(".msm-row"), it = btn.closest(".msm-item"), p = s.msDraft.projects[Number(row.dataset.pi)];
        const [m] = p.milestones.splice(Number(it.dataset.mi), 1);
        if (m && m.id) s.msDraft.removed.push(m.id);
        return _msRenderModal();
    }
    if (act === "defer") {
        _msSync();
        const it = btn.closest(".msm-item"), row = btn.closest(".msm-row"), p = s.msDraft.projects[Number(row.dataset.pi)], m = p.milestones[Number(it.dataset.mi)];
        if (!m || !m.id) return;
        if (!window.confirm(`「${m.title}」延到下週？`)) return;
        try { await mjson(`/api/v1/milestones/${encodeURIComponent(m.id)}/defer`, _POST({ week_start: s.msDraft.week_start })); p.milestones.splice(Number(it.dataset.mi), 1); _msRenderModal(); }
        catch (e) { alert("延期失敗：" + e.message); }
        return;
    }
    if (act === "addproj") {
        _msSync();
        const q = ($("ms-proj-q")?.value || "").trim(); if (!q) return;
        const list = await _logProjectOptions();
        const hit = (list || []).find(p => p.label === q || p.name === q);
        if (!hit) { alert("找不到這個案，請從清單選"); return; }
        if (s.msDraft.projects.some(p => p.project_id === hit.id)) { alert("這個案已經在清單裡"); return; }
        s.msDraft.projects.push({ project_id: hit.id, name: hit.name, client: hit.client || "", hours_week: 0, milestones: [] });
        _msRenderModal();
        return;
    }
    if (act === "save") {
        _msSync();
        const items = [];
        // 🔴 負責人送空字串不是 null：後端「沒帶就不動」靠的是 null／不帶（保護被 CF 快取的舊分頁），
        // 空字串才是「這次明講要清成不指定」。送 null 會讓「改回不指定」永遠取消不掉，
        // 而同一列的 assignee_name（送 ""）卻會被清掉 → 留下有 id 沒名字的髒列。
        s.msDraft.projects.forEach(p => p.milestones.forEach(m => items.push({ id: m.id || null, project_id: p.project_id, title: m.title, due_date: m.due_date, assignee_staff_id: m.assignee_staff_id || "", assignee_name: m.assignee_name || "", note: m.note || "", done: !!m.done })));
        s.msDraft.removed.forEach(id => items.push({ id, project_id: "", title: "", delete: true }));
        try { await mjson("/api/v1/milestones/week/save", _POST({ week_start: s.msDraft.week_start, items })); bg?.remove(); resetToday(); await loadTeamWeek(); }
        catch (e) { alert("儲存失敗：" + e.message); }
    }
}
