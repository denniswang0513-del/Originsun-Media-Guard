// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第三區卡片（下）：我的假勤（時數帳＋申請單）／請款與薪酬／我的工時
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、money、mfetch、WS、makeCard、WIP_LABEL（cards.js）
// 跨檔提供：cardLeave、loadLeave、cardFinance、_localToday（zone1.js／week-plan.js／parttime.js 都用）、loadMyTs
// ────────────────────────────────────────────────────────────────────────────
// ── 卡：我的假勤（me_leave；docs/LEAVE_PLAN.md §7.4／§7.6：時數帳＋申請單）──
// 卡片自己打 /api/v1/me/leave/summary（不吃 workspace bundle）：三個數字＋請假表單（即時 preview）＋近期清單。
// 字彙（假別／半天／每日時數）從 summary.vocab 拿，不寫死；撤回／申請消假依後端算好的 cancel_mode。
// 時段的中文由後端字彙給（/me/leave/summary 的 vocab.part_labels，正本 core/leave_logic.PART_LABELS）；
// 這裡只留還沒拿到 summary 時的預設值 —— 卡片的其他字彙也是這個規矩。
const LV_PART_FALLBACK = { all: "整天", am: "上午", pm: "下午", range: "時段" };
const LV_PART_LABEL = (code) => ((LV && LV.vocab && LV.vocab.part_labels) || LV_PART_FALLBACK)[code] || code || "";
let LV = null;                 // 最近一次 summary
let _lvPreviewTimer = null;    // preview 去抖
let _lvPreviewSeq = 0;         // 舊回應不蓋新回應
function cardLeave(bound) {
    // 🔴 標「開發中」（owner 2026-09-10）：功能是做好的，但**沒有人的時數帳被匯入過**
    //    （生產的 hr_leave_credits／hr_leave_allocations 都是 0 列），所以每個人看到的
    //    特休與補休都是 0、送出去會被擋在「特休不足」。不標的話同事會以為是壞了。
    //    時數帳匯入完成後把這個徽章與下面那條說明一起拿掉（docs/LEAVE_PLAN.md）。
    // 🔴 不直接引用 cards.js 的 WIP_LABEL：這七支是**傳統 script**、共用全域詞法環境，
    //    而 Cloudflare 給 .js 四小時瀏覽器快取 —— 出現「舊 cards.js ＋ 新 cards-hr.js」
    //    的組合時 `WIP_LABEL is not defined` 會從這裡拋出去，而 shell.js 那串建卡是直線
    //    呼叫沒有 try/catch，工作台下半頁（零用金、基本資料、週記整區）會一起不見。
    //    徽章的判定在 cards.js 只比值相等，所以退回字面值就夠。
    const card = makeCard("My Leave", "我的假勤",
                          typeof WIP_LABEL !== "undefined" ? WIP_LABEL : "開發中", "leave");
    const body = card.querySelector(".card-body");
    if (!bound) { body.innerHTML = `<div class="empty">尚未綁定人員檔案</div>`; return card; }
    body.innerHTML = `<div class="empty">載入中…</div>`;
    setTimeout(loadLeave, 0);
    return card;
}
function _lvBody() {
    const c = document.querySelector('.card[data-card="leave"] .card-body');
    return c || null;
}
async function loadLeave() {
    const body = _lvBody();
    if (!body) return;
    try { LV = await mjson("/api/v1/me/leave/summary"); }
    catch (e) { body.innerHTML = `<div class="empty">${esc(e.message || "載入失敗")}</div>`; return; }
    renderLeave(body);
}
const _lvH = (h) => { const n = Number(h || 0); return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, ""); };
function _lvDays(h) { return _lvH(Number(h || 0) / ((LV && LV.vocab && LV.vocab.hours_per_day) || 8)); }
function renderLeave(body) {
    const v = LV.vocab || {};
    const bal = LV.balances || {};
    const an = bal["特休"] || {}, comp = bal["補休"] || {};
    const sick = LV.sick || {};
    const types = v.leave_types || ["特休", "補休", "病假", "事假", "公假", "婚假", "喪假", "其他"];
    const parts = v.parts || Object.keys(LV_PART_FALLBACK);
    const today = _localToday();
    const expiring = (an.expiring || [])[0];
    let html = `<div class="wip-note">開發中 —— 時數帳（特休／補休）還沒匯入，所以數字都是 0，送出的申請也還不算數。</div>
    <div class="stat-row">
        <div class="stat"><div class="num">${_lvH(an.available)}<span style="font-size:13px;color:var(--sub);"> h（${_lvDays(an.available)} 天）</span></div><div class="lbl">特休剩餘</div></div>
        <div class="stat"><div class="num">${_lvH(comp.available)}<span style="font-size:13px;color:var(--sub);"> h（${_lvDays(comp.available)} 天）</span></div><div class="lbl">補休剩餘</div></div>
        <div class="stat"><div class="num">${_lvH(sick.used_days)}<span style="font-size:13px;color:var(--sub);"> / ${_lvH(sick.cap_days ?? v.sick_cap_days ?? 30)} 天</span></div><div class="lbl">病假已用</div></div>
    </div>
    ${(an.reserved || comp.reserved || expiring) ? `<div class="meta" style="font-size:11px;color:var(--sub);margin:-4px 0 8px;">
        ${an.reserved ? `特休待審保留 ${_lvH(an.reserved)} h　` : ""}${comp.reserved ? `補休待審保留 ${_lvH(comp.reserved)} h　` : ""}${expiring ? `特休最近到期：${_lvH(expiring.hours)} h（${esc(expiring.expires_on)}）` : ""}
    </div>` : ""}
    <div class="pf-edit" id="lv-form" style="border-top:1px solid #f5f5f5;padding-top:12px;padding-bottom:4px;">
        <div class="inline-row" style="margin-bottom:8px;">
            <select id="lv-type">${types.map(t => `<option>${esc(t)}</option>`).join("")}</select>
            <select id="lv-part">${parts.map(p => `<option value="${esc(p)}">${esc(LV_PART_LABEL(p))}</option>`).join("")}</select>
        </div>
        <div class="inline-row" style="margin-bottom:8px;">
            <input type="date" id="lv-start" value="${today}">
            <input type="date" id="lv-end" value="${today}">
        </div>
        <div class="inline-row" id="lv-range" style="margin-bottom:8px;display:none;">
            <input type="time" id="lv-start-time" step="1800" value="09:00">
            <input type="time" id="lv-end-time" step="1800" value="13:00">
        </div>
        <div id="lv-preview" style="font-size:12px;color:var(--sub);margin-bottom:8px;line-height:1.7;"></div>
        <div class="field"><textarea id="lv-reason" rows="2" placeholder="事由（必填）"></textarea></div>
        <div style="display:flex;gap:8px;align-items:center;">
            <button class="mini-btn" id="lv-submit" onclick="applyLeave()">送出請假</button>
            <span class="err" id="lv-err" style="margin-top:0;"></span>
        </div>
    </div>`;
    html += (LV.requests || []).map(_lvRow).join("");
    body.innerHTML = html;
    const form = $("lv-form");
    form.addEventListener("input", (e) => { if (e.target.id !== "lv-reason") lvPreviewSoon(); });
    form.addEventListener("change", (e) => {
        if (e.target.id === "lv-part") $("lv-range").style.display = e.target.value === "range" ? "" : "none";
        if (e.target.id === "lv-start" && $("lv-end").value < e.target.value) $("lv-end").value = e.target.value;
        if (e.target.id !== "lv-reason") lvPreviewSoon();
    });
    lvPreviewSoon();
}
function _lvRow(r) {
    const hot = r.status === "待審" || r.status === "消假待審";
    const partLbl = LV_PART_LABEL(r.part);
    const tm = r.part === "range" && r.start_time ? ` ${esc(r.start_time)}–${esc(r.end_time || "")}` : "";
    const period = r.start_date === r.end_date ? esc(r.start_date) : `${esc(r.start_date)} ~ ${esc(r.end_date)}`;
    const metas = [];
    if (r.reason) metas.push(esc(r.reason));
    if (r.status === "已退回" && r.reject_note) metas.push(`退回理由：${esc(r.reject_note)}`);
    if (r.status === "消假待審" && r.cancel_note) metas.push(`消假理由：${esc(r.cancel_note)}`);
    if (r.status === "已核准" && r.approved_by) metas.push(`核可：${esc(r.approved_by)}`);
    let act = "";
    if (r.status === "待審") {
        act = `<button class="mini-btn" onclick="cancelLeave('${esc(r.id)}', 'free')">撤回</button>`;
    } else if (r.status === "已核准") {
        if (r.cancel_mode === "free") act = `<button class="mini-btn" onclick="cancelLeave('${esc(r.id)}', 'free')">撤回</button>`;
        else if (r.cancel_mode === "apply") act = `<button class="mini-btn" onclick="cancelLeave('${esc(r.id)}', 'apply')">申請消假</button>`;
        else if (r.cancel_mode === "locked") act = `<button class="mini-btn" disabled title="颱風假當日不可消">颱風假當日不可消</button>`;
    }
    return `<div class="row">
        <div class="grow">
            <div class="title">${esc(r.leave_type)}　${period}${partLbl ? `（${partLbl}${tm}）` : ""}　${_lvH(r.hours)} 小時／${_lvDays(r.hours)} 天</div>
            ${metas.length ? `<div class="meta">${metas.join("　")}</div>` : ""}
        </div>
        <span class="pill${hot ? " hot" : ""}">${esc(r.status)}</span>
        ${act}
    </div>`;
}
function _lvPayload() {
    const part = $("lv-part").value;
    return {
        leave_type: $("lv-type").value, start_date: $("lv-start").value, end_date: $("lv-end").value || $("lv-start").value,
        part, start_time: part === "range" ? $("lv-start-time").value : null, end_time: part === "range" ? $("lv-end-time").value : null,
    };
}
// 任何欄位一動就重算，但等 300ms 沒再動才真的打（打字改日期不會每個鍵一發）
function lvPreviewSoon() {
    clearTimeout(_lvPreviewTimer);
    _lvPreviewTimer = setTimeout(lvPreview, 300);
}
function _lvMsgs(list, color) {
    return (list || []).map(x => `<div style="color:${color};">${esc(x.msg || x.code || x)}</div>`).join("");
}
async function lvPreview() {
    const host = $("lv-preview");
    if (!host) return;
    const seq = ++_lvPreviewSeq;
    const p = _lvPayload();
    if (!p.start_date) { host.innerHTML = ""; return; }
    let d;
    try { d = await mjson("/api/v1/me/leave/preview", { method: "POST", body: JSON.stringify(p) }); }
    catch (e) { if (seq === _lvPreviewSeq) host.innerHTML = `<div style="color:var(--red);">${esc(e.message)}</div>`; return; }
    if (seq !== _lvPreviewSeq) return;
    const errs = d.errors || [], warns = d.warnings || [];
    host.innerHTML = `<div>共 ${_lvH(d.hours)} 小時（${_lvDays(d.hours)} 天）</div>${_lvMsgs(warns, "#b45309")}${_lvMsgs(errs, "var(--red)")}`;
    const btn = $("lv-submit");
    if (btn) btn.disabled = errs.length > 0;
}
async function applyLeave() {
    const errEl = $("lv-err");
    const show = (msg) => { errEl.innerHTML = msg; errEl.style.display = "inline"; };
    errEl.style.display = "none";
    const reason = ($("lv-reason").value || "").trim();
    if (!reason) { show("請填事由"); $("lv-reason").focus(); return; }
    // 送出中鎖住：連點兩下會建出兩張一模一樣的待審單（手機版的 withBusy 早就有，桌機這條原本沒有）
    const btn = $("lv-submit");
    if (btn) { if (btn.dataset.busy) return; btn.dataset.busy = "1"; btn.disabled = true; }
    try {
        const r = await mfetch("/api/v1/me/leave", { method: "POST", body: JSON.stringify(Object.assign(_lvPayload(), { reason })) });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            const errs = Array.isArray(d.errors) ? d.errors : (Array.isArray(d.detail) ? d.detail : (d.detail && d.detail.errors) || []);
            show(errs.length ? errs.map(x => esc(x.msg || x.code || x)).join("<br>") : esc((typeof d.detail === "string" && d.detail) || "送出失敗"));
            return;
        }
        _todayInfo = null;   // 今天那條「請假待審 N 件」下次重抓
        await loadLeave();   // 🔴 要 await：不等重畫完就走到 finally，鎖會在舊表單還在畫面上時就解開，那段時間再點一次就是第二張單
    } catch (_) { show("連線失敗"); }
    finally { if (btn && btn.isConnected) { delete btn.dataset.busy; btn.disabled = false; } }   // 重畫過就換了節點，不用還原
}
// mode：free＝直接撤回（待審、或已核准但還在免申請期）；apply＝已核准且逾期，要寫消假理由送「消假待審」
async function cancelLeave(id, mode) {
    let body = {};
    if (mode === "apply") {
        const note = (prompt("消假理由（必填，送出後由主管決定）") || "").trim();
        if (!note) return;
        body = { note };   // schema LeaveCancel 收 note（之前送 cancel_note 必 422）
    } else if (!confirm("確定撤回這張請假單？")) return;
    try {
        const r = await mfetch("/api/v1/me/leave/" + id + "/cancel", { method: "POST", body: JSON.stringify(body) });
        if (r.ok) { _todayInfo = null; loadLeave(); }
        else { const d = await r.json().catch(() => ({})); alert((typeof d.detail === "string" && d.detail) || "撤回失敗"); }
    } catch (_) {}
}

// ── 卡：請款與薪酬 ——（owner 2026-09-05 鐵則：員工頁不畫個人工時合計卡；本月／累計／每案小時全拿掉，
//    只留請款與「我的工時」逐列清單）──
function cardFinance(ts, pay) {
    const card = makeCard("Payments", "請款與薪酬", "", "finance");
    const body = card.querySelector(".card-body");
    if (!ts && !pay) { body.innerHTML = `<div class="empty">尚未綁定人員檔案</div>`; return card; }
    let html = "";
    if (pay) {
        html += `<div class="stat-row">
            <div class="stat"><div class="num ${pay.unpaid_count ? "accent" : ""}">${pay.unpaid_count}</div><div class="lbl">未付請款</div></div>
        </div>`;
        if (pay.unpaid_count) html += `<div class="notice" style="margin:14px 0 4px;">未付請款 ${pay.unpaid_count} 筆，共 <b class="accent">${money(pay.unpaid_amount)}</b></div>`;
        html += (pay.recent || []).map(p => `
            <div class="row"><div class="grow"><div class="title">${esc(p.summary)}</div>
            <div class="meta">${esc(p.date)}${p.project ? " · " + esc(p.project) : ""}</div></div>
            <div style="text-align:right;"><div style="font-size:13px;">${money(p.amount)}</div>
            <span class="pill${p.status !== "已付款" ? " hot" : ""}">${esc(p.status || "—")}</span></div></div>`).join("");
    }
    if (ts) {
        // 我的工時：看自己該月的每一列（Sheet＋手填）、改／刪自己填的、一次新增多列
        // （docs/TIMESHEET_SELF_ENTRY_PLAN.md 階段 1；同人同日同案手填優先於 Sheet）
        html += `<div style="padding-top:14px;" id="my-ts"></div>`;
    }
    body.innerHTML = html || `<div class="empty">尚無工時／請款紀錄</div>`;
    if (ts) setTimeout(loadMyTs, 0);
    return card;
}

// ── 我的工時 ──
let _tsMonth = _localToday().slice(0, 7);
let _tsProjects = null;     // timesheet_options（進行中案＋本人最近填過的）
let _tsItems = [];          // 最近一次 /me/timesheets 的列（改列時從這裡拿，不刮 DOM）
function _localToday() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
async function _tsOptions() {
    if (_tsProjects) return _tsProjects;
    try {
        const r = await mfetch("/api/v1/timesheets/project_options");
        _tsProjects = r.ok ? ((await r.json()).projects || []) : [];
    } catch (_) { _tsProjects = []; }
    return _tsProjects;
}
async function loadMyTs() {
    const host = $("my-ts");
    if (!host) return;
    host.innerHTML = `<div class="empty">載入中…</div>`;
    try {
        const r = await mfetch("/api/v1/me/timesheets?month=" + _tsMonth);
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            host.innerHTML = `<div class="notice">${esc(d.detail || ("HTTP " + r.status))}</div>`;
            return;
        }
        renderMyTs(await r.json());
    } catch (_) { host.innerHTML = `<div class="notice">連線失敗</div>`; }
}
function renderMyTs(d) {
    const host = $("my-ts");
    _tsItems = d.items || [];
    const rows = _tsItems.map(it => `
        <div class="row" data-ts-id="${esc(it.id)}">
            <div class="grow">
                <div class="title">${esc(it.project_name || "(空白)")}${it.task_note ? `<span style="color:var(--sub);"> · ${esc(it.task_note)}</span>` : ""}</div>
                <div class="meta">${esc(it.date)} · ${it.source === "manual" ? "系統填" : "Sheet"}${it.status && it.status !== "draft" && it.status !== "import" ? " · " + esc(it.status) : ""}</div>
            </div>
            <span class="pill">${it.hours} h</span>
            ${it.editable ? `<button class="mini-btn" onclick="editMyTs('${esc(it.id)}')">改</button>
                             <button class="mini-btn" onclick="deleteMyTs('${esc(it.id)}')">刪</button>` : ""}
        </div>`).join("");
    const mon = d.month;
    const prev = _shiftMonth(mon, -1), next = _shiftMonth(mon, 1);
    host.innerHTML = `
        <div class="inline-row" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">
            <button class="mini-btn" onclick="setTsMonth('${prev}')">‹</button>
            <b>${esc(mon)}</b>
            <button class="mini-btn" onclick="setTsMonth('${next}')">›</button>
            <span style="flex:1;"></span>
            <a class="mini-btn" href="/hours.html">團隊工時 →</a>
            <button class="mini-btn" onclick="toggleTsAdd()">新增工時</button>
        </div>
        <div class="pf-edit" id="ts-add" style="display:none;margin:8px 0 12px;">
            <div id="ts-add-rows"></div><datalist id="ts-proj-list"></datalist>
            <div style="display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap;">
                <button class="mini-btn" onclick="addTsRow()">＋ 再一列</button>
                <button class="mini-btn" onclick="submitTsRows()">送出</button>
                <span class="err" id="ts-add-err"></span>
            </div>
            <div class="meta" style="margin-top:6px;color:var(--sub);">同一天同一案只填一邊：在這裡填了，Sheet 那列會被擋（手填優先）。Sheet 已拉進來的列在下面看得到，別重複填。</div>
        </div>
        ${rows || `<div class="empty">這個月還沒有工時</div>`}`;
}
function _shiftMonth(ym, delta) {
    const [y, m] = ym.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function setTsMonth(ym) { _tsMonth = ym; loadMyTs(); }
async function _tsFillList() {
    // 找不到就照打：用 datalist 而不是鎖死的 select（案名照 Sheet 規則對映，撞案留給管理員指定）
    const opts = await _tsOptions();
    const dl = $("ts-proj-list");
    if (dl) dl.innerHTML = opts.map(p => `<option value="${esc(p.name)}"></option>`).join("");
}
function _tsRowHtml(v = {}) {
    return `<div class="inline-row ts-new-row" style="margin-bottom:6px;">
        <input type="date" data-f="date" value="${esc(v.date || _localToday())}">
        <input list="ts-proj-list" data-f="project" value="${esc(v.project || "")}" placeholder="專案（可打字）">
        <input data-f="note" placeholder="內容（選填）" value="${esc(v.note || "")}">
        <input type="number" data-f="hours" min="0.25" step="0.25" placeholder="時數" value="${v.hours || ""}" style="max-width:90px;">
    </div>`;
}
async function toggleTsAdd() {
    const box = $("ts-add");
    if (!box) return;
    const show = box.style.display === "none";
    box.style.display = show ? "" : "none";
    if (show) { await _tsFillList(); if (!$("ts-add-rows").children.length) addTsRow(); }
}
function addTsRow() { $("ts-add-rows").insertAdjacentHTML("beforeend", _tsRowHtml()); }
/** 一列輸入 → 送給後端的 body（新增與改列同一形狀）。 */
function _tsRowBody(el) {
    const v = f => el.querySelector(`[data-f="${f}"]`).value;
    return { work_date: v("date"), project_name: v("project").trim(), task_note: v("note"), hours: parseFloat(v("hours") || "0") };
}
async function submitTsRows() {
    const errEl = $("ts-add-err");
    errEl.textContent = "";
    const rows = [...document.querySelectorAll("#ts-add-rows .ts-new-row")].map(_tsRowBody).filter(r => r.work_date && r.hours > 0);
    if (!rows.length) { errEl.textContent = "至少填一列（日期＋時數）"; return; }
    try {
        const r = await mfetch("/api/v1/me/timesheets/batch", { method: "POST", body: JSON.stringify({ rows }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { errEl.textContent = d.detail || "送出失敗"; return; }
        $("ts-add-rows").innerHTML = "";
        $("ts-add").style.display = "none";
        await loadMyTs();
        if ((d.unmatched_projects || []).length) alert("這幾個專案名系統對不到案（已存下來，管理員會指定）：" + d.unmatched_projects.join("、"));
    } catch (_) { errEl.textContent = "連線失敗"; }
}
async function editMyTs(id) {
    const rowEl = document.querySelector(`[data-ts-id="${id}"]`);
    const it = _tsItems.find(x => x.id === id);
    if (!rowEl || !it) return;
    await _tsFillList();
    const date = it.date, project = it.project_name, note = it.task_note, hours = it.hours;
    rowEl.outerHTML = `<div class="pf-edit" data-ts-edit="${esc(id)}" style="margin:6px 0;">
        ${_tsRowHtml({ date, project, note, hours }).replace("ts-new-row", "ts-edit-row")}
        <button class="mini-btn" onclick="saveMyTs('${esc(id)}')">儲存</button>
        <button class="mini-btn" onclick="loadMyTs()">取消</button>
        <span class="err" data-err></span></div>`;
}
async function saveMyTs(id) {
    const box = document.querySelector(`[data-ts-edit="${id}"]`);
    if (!box) return;
    const el = box.querySelector(".ts-edit-row");
    const it = _tsItems.find(x => x.id === id) || {};
    // 這頁不編分類／計畫小時，原值帶回去（PUT 是整列覆蓋）
    const body = { ..._tsRowBody(el), work_type: it.work_type || null, planned_hours: it.planned_hours };
    try {
        const r = await mfetch("/api/v1/me/timesheets/" + encodeURIComponent(id), { method: "PUT", body: JSON.stringify(body) });
        if (!r.ok) { const d = await r.json().catch(() => ({})); box.querySelector("[data-err]").textContent = d.detail || "儲存失敗"; return; }
        loadMyTs();
    } catch (_) { box.querySelector("[data-err]").textContent = "連線失敗"; }
}
async function deleteMyTs(id) {
    if (!confirm("刪掉這一列工時？")) return;
    try {
        const r = await mfetch("/api/v1/me/timesheets/" + encodeURIComponent(id), { method: "DELETE" });
        if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.detail || "刪除失敗"); return; }
        loadMyTs();
    } catch (_) { alert("連線失敗"); }
}
