// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第一區視圖 2 團隊的一週＋本週里程碑彈窗、視圖 3 專案查詢，以及頁面 boot（開頁就跑，所以這支一定載最後）
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、mfetch、mjson、TS_READY、_z1Week、_findState、_mdLabel、_shiftDays、_localToday、_POST、_PUT、
//            TOKEN_KEY／_resetToken／loadWorkspace／showLogin（檔尾 boot 用）
// 跨檔提供：loadTeamWeek、_msWeek、_msDraft、_ms* 家族、loadFind、_find* 家族、PCT_BANDS、_openFindProject
// ────────────────────────────────────────────────────────────────────────────
// ── 視圖 2：團隊的一週（人×日，案名＋小時＋內容；計畫淺灰、場次藍、休假灰）──
async function loadTeamWeek() {
    const host = $("z1-week");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    // 兩支互不相依：一起發（原本串著等，翻一週要吃兩趟來回）。里程碑抓不到就沒有那條帶，不擋週表
    const [dRes, ms] = await Promise.all([
        mjson("/api/v1/me/team_week?start=" + _z1Week).catch(e => ({ __err: e.message })),
        mjson("/api/v1/milestones/week?start=" + _z1Week).catch(() => null),
    ]);
    if (dRes && dRes.__err) { host.innerHTML = `<div class="notice">${esc(dRes.__err)}</div>`; return; }
    const d = dRes;
    _msWeek = ms;
    const msOf = (name, iso) => ms ? ms.projects.flatMap(p => p.milestones.filter(m => m.assignee_name === name && m.due_date === iso).map(m => ({ ...m, project_name: p.name }))) : [];
    const days = d.days || [];
    const today = _localToday();
    const shootsOf = (iso) => (d.shoots && d.shoots[iso]) || [];
    const leaveOf = (iso) => (d.leave && d.leave[iso]) || [];
    const people = d.people || [];
    const msNames = ms ? ms.projects.flatMap(p => p.milestones.map(m => m.assignee_name).filter(Boolean)) : [];   // 有里程碑的負責人也要有一列
    const names = [...new Set([...people.map(p => p.name), ...days.flatMap(iso => [...shootsOf(iso).flatMap(s => s.crew || []), ...leaveOf(iso)]), ...msNames])];
    const hasAny = (iso) => people.some(p => (p.cells && p.cells[iso] || []).length) || shootsOf(iso).length || leaveOf(iso).length;
    const cols = days.filter(iso => { const w = _dow(iso); return (w !== 0 && w !== 6) || hasAny(iso); });   // 週末只有有東西才畫
    const cell = (name, iso) => {
        const p = people.find(x => x.name === name);
        const items = (p && p.cells && p.cells[iso]) || [];
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
        return parts.join("") || '<span class="none">—</span>';
    };

    const range = cols.length ? `${_mdLabel(cols[0]).slice(0, -3)} – ${_mdLabel(cols[cols.length - 1]).slice(0, -3)}` : "";
    host.innerHTML = `
        <div class="vhead"><span class="ey">Team Week<b>團隊的一週 ${esc(range)}</b></span>
            <span class="vrow"><button type="button" class="btn" data-z1="week" data-start="${_shiftDays(_z1Week, -7)}">‹</button>
                <span class="meta">${esc(_z1Week)} 起</span>
                <button type="button" class="btn" data-z1="week" data-start="${_shiftDays(_z1Week, 7)}">›</button>
                ${_z1Week === _mondayOf(today) ? "" : '<button type="button" class="btn" data-z1="week">本週</button>'}
                <button type="button" class="btn pri" data-z1="ms-open">設定專案里程碑</button></span></div>
        ${ms ? _msBandHtml(ms) : ""}
        ${names.length ? `<div style="overflow-x:auto;"><table class="week">
            <thead><tr><th style="width:84px;">人員</th>${cols.map(iso => `<th class="${iso === today ? "today" : ""}">${esc(_mdLabel(iso))}</th>`).join("")}</tr></thead>
            <tbody>${names.map(n => `<tr><td class="who">${esc(n)}</td>${cols.map(iso => `<td>${cell(n, iso)}</td>`).join("")}</tr>`).join("")}</tbody>
        </table></div>` : `<div class="empty">這一週還沒有人填。</div>`}`;
}

// ── 本週里程碑（owner 2026-09-07；示範 /demo/milestones.html 定稿）：週表上方的帶＋「設定專案里程碑」彈窗 ──
let _msWeek = null;          // GET /milestones/week 的結果（畫帶、開彈窗用）
let _msDraft = null;         // 彈窗裡正在改的（儲存才寫入）
function _msDue(m) { return m.due_date ? _mdLabel(m.due_date).slice(0, -3) : ""; }
function _msBandHtml(ms) {
    const withMs = ms.projects.filter(p => p.milestones.length);
    const total = ms.open_count + ms.done_count;
    const body = withMs.length ? withMs.map(p => `<div class="ms-proj"><div class="pn">${esc(p.name)}<small>${esc(p.client || "")}${p.client ? " · " : ""}本週已投入 ${p.hours_week} h</small></div><div class="ms-list">${p.milestones.map(m => `
        <div class="ms${m.done ? " done" : ""}${m.late ? " late" : ""}${m.carried ? " carried" : ""}"><input type="checkbox" data-ms-done="${esc(m.id)}" ${m.done ? "checked" : ""} title="${m.done ? "取消完成" : "標記完成"}"> ${esc(m.title)} <span class="due">${esc(_msDue(m))}</span>${m.assignee_name ? `<span class="who">· ${esc(m.assignee_name)}</span>` : ""}</div>`).join("")}</div></div>`).join("")
        : `<div class="empty">這週還沒有里程碑。<button type="button" class="linkish" data-z1="ms-open">設定專案里程碑</button></div>`;
    return `<div class="ms-band"><div class="ey"><span>This week's milestones　本週里程碑</span><span>${total ? `${ms.done_count}/${total} 完成${ms.late_count ? ` · 過期 ${ms.late_count}` : ""}` : ""}</span></div>${body}</div>`;
}
async function _msToggleDone(id, done, cb) {
    if (cb) { if (cb.disabled) return; cb.disabled = true; }      // 送出中不讓再點（重畫會換掉整個節點，不用還原）
    try { await mjson(`/api/v1/milestones/${encodeURIComponent(id)}/done`, _POST({ done })); await loadTeamWeek(); _todayInfo = null; }
    catch (e) { alert("更新失敗：" + e.message); await loadTeamWeek(); }
}
async function _openMsModal() {
    // 週表剛抓過同一週就用那份（原本無條件再抓一次＝每開一次彈窗多一支請求／後端多算一整週）
    let ms = _msWeek && _msWeek.week_start === _z1Week ? _msWeek : null;
    if (!ms) {
        try { ms = await mjson("/api/v1/milestones/week?start=" + _z1Week); } catch (e) { alert("抓不到里程碑：" + e.message); return; }
    }
    _msWeek = ms;
    _msDraft = { week_start: ms.week_start, default_due: ms.default_due, staff: ms.staff, projects: ms.projects.map(p => ({ ...p, milestones: p.milestones.map(m => ({ ...m })) })), removed: [] };
    const root = $("ws-view") || document.body;
    let bg = $("ws-ms-modal");
    if (!bg) {
        bg = document.createElement("div"); bg.id = "ws-ms-modal"; bg.className = "ws-modal-bg";
        bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });
        root.appendChild(bg);
    }
    bg.innerHTML = `<div class="ws-modal" style="max-width:980px;"><div id="ws-ms-body"></div></div>`;
    _msRenderModal();
    _logProjectOptions().then(list => { const dl = $("ms-proj-dl"); if (dl) dl.innerHTML = (list || []).filter(p => !p.closed).map(p => `<option value="${esc(p.label)}">`).join(""); });
}
function _msRenderModal() {
    const d = _msDraft, host = $("ws-ms-body");
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
    document.querySelectorAll("#ws-ms-body .msm-row").forEach(row => {
        const p = _msDraft.projects[Number(row.dataset.pi)];
        row.querySelectorAll(".msm-item").forEach(it => {
            const m = p.milestones[Number(it.dataset.mi)]; if (!m) return;
            const v = (k) => it.querySelector(`[data-k="${k}"]`);
            m.done = v("done").checked; m.title = v("title").value.trim(); m.due_date = v("due_date").value || _msDraft.default_due;
            m.assignee_staff_id = v("assignee_staff_id").value;
            m.assignee_name = (v("assignee_staff_id").selectedOptions[0]?.textContent || "").replace("（不指定）", "").replace("（已不在人力庫）", "");
            m.note = v("note").value.trim();
        });
    });
}
function _msAdd(pi) {
    _msSync();
    const inp = document.querySelector(`#ws-ms-body input[data-ms-add="${pi}"]`); const t = (inp?.value || "").trim(); if (!t) return;
    _msDraft.projects[pi].milestones.push({ id: null, title: t, due_date: _msDraft.default_due, assignee_staff_id: "", assignee_name: "", note: "", done: false, late: false, carried: false });
    _msRenderModal();
    document.querySelector(`#ws-ms-body input[data-ms-add="${pi}"]`)?.focus();
}
async function _msModalAction(btn) {
    const act = btn.dataset.ms, bg = $("ws-ms-modal");
    if (act === "cancel") { bg?.remove(); return; }
    if (act === "wk") { _z1Week = _shiftDays(_msDraft.week_start, Number(btn.dataset.delta)); await _openMsModal(); return; }
    if (act === "add") { const row = btn.closest(".msm-row"); return _msAdd(Number(row.dataset.pi)); }
    if (act === "del") {
        _msSync();
        const row = btn.closest(".msm-row"), it = btn.closest(".msm-item"), p = _msDraft.projects[Number(row.dataset.pi)];
        const [m] = p.milestones.splice(Number(it.dataset.mi), 1);
        if (m && m.id) _msDraft.removed.push(m.id);
        return _msRenderModal();
    }
    if (act === "defer") {
        _msSync();
        const it = btn.closest(".msm-item"), row = btn.closest(".msm-row"), p = _msDraft.projects[Number(row.dataset.pi)], m = p.milestones[Number(it.dataset.mi)];
        if (!m || !m.id) return;
        if (!window.confirm(`「${m.title}」延到下週？`)) return;
        try { await mjson(`/api/v1/milestones/${encodeURIComponent(m.id)}/defer`, _POST({ week_start: _msDraft.week_start })); p.milestones.splice(Number(it.dataset.mi), 1); _msRenderModal(); }
        catch (e) { alert("延期失敗：" + e.message); }
        return;
    }
    if (act === "addproj") {
        _msSync();
        const q = ($("ms-proj-q")?.value || "").trim(); if (!q) return;
        const list = await _logProjectOptions();
        const hit = (list || []).find(p => p.label === q || p.name === q);
        if (!hit) { alert("找不到這個案，請從清單選"); return; }
        if (_msDraft.projects.some(p => p.project_id === hit.id)) { alert("這個案已經在清單裡"); return; }
        _msDraft.projects.push({ project_id: hit.id, name: hit.name, client: hit.client || "", hours_week: 0, milestones: [] });
        _msRenderModal();
        return;
    }
    if (act === "save") {
        _msSync();
        const items = [];
        // 🔴 負責人送空字串不是 null：後端「沒帶就不動」靠的是 null／不帶（保護被 CF 快取的舊分頁），
        // 空字串才是「這次明講要清成不指定」。送 null 會讓「改回不指定」永遠取消不掉，
        // 而同一列的 assignee_name（送 ""）卻會被清掉 → 留下有 id 沒名字的髒列。
        _msDraft.projects.forEach(p => p.milestones.forEach(m => items.push({ id: m.id || null, project_id: p.project_id, title: m.title, due_date: m.due_date, assignee_staff_id: m.assignee_staff_id || "", assignee_name: m.assignee_name || "", note: m.note || "", done: !!m.done })));
        _msDraft.removed.forEach(id => items.push({ id, project_id: "", title: "", delete: true }));
        try { await mjson("/api/v1/milestones/week/save", _POST({ week_start: _msDraft.week_start, items })); bg?.remove(); _todayInfo = null; await loadTeamWeek(); }
        catch (e) { alert("儲存失敗：" + e.message); }
    }
}

// ── 視圖 3：專案查詢（工作追蹤「專案」表＋專案檔案，開放給員工，唯讀）──
async function _loadFindRows() {
    if (_findRows) return _findRows;
    // 員工端唯讀版（只有全案工時數字，沒金額、沒建議預算）；守衛跟這一區一樣是「有綁員工」，
    // 失敗就讓 loadFind 的 catch 顯示原因（以前的 /timesheets/projects → /summary 三段備援已拿掉）
    const rows = (await mjson("/api/v1/me/projects_burn")).projects || [];
    _findRows = rows;
    return rows;
}
async function loadFind() {
    const host = $("z1-find");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    const TS = await TS_READY;
    try { await _loadFindRows(); } catch (e) { host.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
    if (!_findSorter) _findSorter = TS.createBurnSorter({ storageKey: "my_find_sort_v2", panelId: "my-burn-table", onChange: () => _redrawFindBody(), defaultSort: { key: "last", dir: "desc" } });   // 預設最新填報在最上面（owner 2026-09-05）
    _renderFindTable();
}
// 消耗率區間與最後填報區間都是固定選項（下拉），不用打數字
const PCT_BANDS = [["", "消耗率：全部"], ["none", "未設預算"], ["lt60", "60% 以下"], ["60-90", "60–90%"], ["90-100", "90–100%"], ["over", "超過 100%"]];
function _pctInBand(pct, band) {
    if (!band) return true;
    if (band === "none") return pct == null;
    if (pct == null) return false;
    const v = Number(pct);
    return band === "lt60" ? v < 60 : band === "60-90" ? (v >= 60 && v < 90) : band === "90-100" ? (v >= 90 && v <= 100) : v > 100;
}
function _lastInRange(lastEntry, from, to) {
    if (!from && !to) return true;
    if (!lastEntry) return false;
    return (!from || lastEntry >= from) && (!to || lastEntry <= to);
}
function _findFiltered() {
    const q = _findState.q.trim().toLowerCase(), st = _findState;
    return (_findRows || []).filter(p => (!st.status || (p.status || "") === st.status)
        && (!st.type || (p.project_type || "") === st.type)
        && _pctInBand(p.pct, st.pct) && _lastInRange(p.last_entry, st.from, st.to)
        && (!q || [p.project_name, p.status, p.project_type].some(x => String(x || "").toLowerCase().includes(q))));
}
function _findActive() { const st = _findState; return !!(st.q || st.status || st.type || st.pct || st.from || st.to); }
async function _redrawFindBody() {
    const TS = await TS_READY;
    const tb = document.querySelector("#my-burn-table tbody");
    if (tb) { tb.innerHTML = TS.burnTbodyHtml(_findSorter.sorted(_findFiltered()), { editable: false, emptyText: "沒有符合的" }); _findSorter.attach(); }
    const c = $("z1-find-count"); if (c) c.textContent = `${_findFiltered().length} 案`;
}
async function _renderFindTable() {
    const TS = await TS_READY;
    const host = $("z1-find");
    const statuses = [...new Set((_findRows || []).map(p => p.status).filter(Boolean))];
    const types = [...new Set((_findRows || []).map(p => p.project_type).filter(Boolean))].sort();
    host.innerHTML = `
        <div class="vhead"><span class="ey">Find<b>專案查詢</b></span></div>
        <div class="find-bar">
            <input class="in" id="z1-find-q" placeholder="搜尋案名、狀態、案型…" value="${esc(_findState.q)}">
            <select class="sel" id="z1-f-status"><option value="">狀態：全部</option>${statuses.map(x => `<option value="${esc(x)}" ${_findState.status === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
            <select class="sel" id="z1-f-type"><option value="">案型：全部</option>${types.map(x => `<option value="${esc(x)}" ${_findState.type === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
            <select class="sel" id="z1-f-pct">${PCT_BANDS.map(([v, l]) => `<option value="${v}" ${_findState.pct === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <span class="meta">最後填報</span><input type="date" class="sel" id="z1-f-from" value="${esc(_findState.from)}"><span class="meta">～</span><input type="date" class="sel" id="z1-f-to" value="${esc(_findState.to)}">
            <button type="button" class="btn" id="z1-f-clear" ${_findActive() ? "" : "hidden"}>清除</button>
            <span class="meta" id="z1-find-count"></span></div>
        <div class="tsp" id="z1-find-body" style="overflow-x:auto;">${TS.burnTableHtml("", "my-burn-table")}</div>`;
    _redrawFindBody();          // 表身、排序、計數只有它一份（殼先畫空的 tbody）
    const clear = $("z1-f-clear");
    const sync = () => { clear.hidden = !_findActive(); _redrawFindBody(); };
    $("z1-find-q").addEventListener("input", (e) => { _findState.q = e.target.value; sync(); });
    [["z1-f-status", "status"], ["z1-f-type", "type"], ["z1-f-pct", "pct"], ["z1-f-from", "from"], ["z1-f-to", "to"]].forEach(([id, key]) =>
        $(id).addEventListener("change", (e) => { _findState[key] = e.target.value; sync(); }));
    clear.addEventListener("click", () => {
        Object.assign(_findState, { q: "", status: "", type: "", pct: "", from: "", to: "" });
        $("z1-find-q").value = ""; ["z1-f-status", "z1-f-type", "z1-f-pct", "z1-f-from", "z1-f-to"].forEach(id => { $(id).value = ""; });
        sync();
    });
}
async function _openFindProject(name, pid) {
    const TS = await TS_READY;
    const body = $("z1-find-body");
    if (!body) return;
    body.innerHTML = `<div class="empty">載入中…</div>`;
    try {
        const d = await mjson("/api/v1/timesheets/project?name=" + encodeURIComponent(name) + (pid ? "&project_id=" + encodeURIComponent(pid) : ""));
        body.innerHTML = TS.projectFileHtml(d, { editable: false, chartWidth: 340, backHtml: '<button type="button" class="btn" data-z1="find-back">‹ 專案清單</button>' });
        body.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
        body.innerHTML = `<div class="notice">${esc(e.message)}</div><button type="button" class="btn" data-z1="find-back">‹ 專案清單</button>`;
    }
}

// ── boot ──
(function () {
    if (_resetToken()) {                 // 重設信連結進來：先設新密碼
        $("reset-view").style.display = "block";
        $("rs-pwd").focus();
        return;
    }
    if (localStorage.getItem(TOKEN_KEY)) loadWorkspace();
    else showLogin("");
})();
