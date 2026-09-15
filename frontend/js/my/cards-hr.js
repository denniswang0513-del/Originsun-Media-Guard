// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第三區卡片（下）：我的假勤（時數帳＋申請單）／請款與薪酬／我的工時
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、money、mfetch、WS、makeCard、WIP_LABEL（cards.js）
// 跨檔提供：cardLeave、loadLeave、cardFinance、_localToday（zone1.js／week-plan.js／parttime.js 都用）、loadMyTs
// 假勤卡＝三步送單（owner 2026-09-15）：1 日期算需要幾小時 → 2 從自己的假裡挑要扣的（/me/leave/inventory，順序＝扣的順序，
// 挑超過最後一筆只扣還需要的） → 3 一整張申請單送出（/me/leave/applications；管理員一次核准；核准前可編輯／撤回）。
// 舊的單張單（/me/leave）只剩手機版與匯入的歷史單在用，清單上沒有申請單的那些照舊一列一筆。
// 第二個宿主：/leave.html（owner 2026-09-15「在這裡新增假勤」—— 最上排一顆鈕開寬頁，同零用金）。
// 那一頁載同一支檔（前面先載 js/my/leave-host.js 備好 $／esc／mfetch／mjson／makeCard／_resetTodayStrip），
// 另外兩個可選掛鉤放 window：window.LV_LIMIT（summary 拿幾筆）、window.onLeaveRendered(LV, err)（畫完
// 通知宿主畫休假總表）。表單／送單／撤回都不抄第二份。
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
// 三步送單的狀態（owner 2026-09-15：日期算小時 → 自己挑要扣的假 → 一整張申請單送出、一次核准、核准前可編輯）
let _lvDates = [];             // 「挑幾天」模式選好的日期
let _lvPicks = [];             // 第 2 步挑的假（清單 id，順序＝扣的順序）
let _lvInv = [];               // 第 2 步清單（/me/leave/inventory）
let _lvEditing = "";           // 正在編輯的申請單 id（空＝新單）
let _lvProofNeeded = false;    // 挑到要附證明的假別（試算回的）
function cardLeave(bound) {
    // 「開發中」徽章 2026-09-15 拿掉（owner「把開發中移除」）：時數帳已逐人建好，沒額度的人卡裡另有一條黃字說明。
    const card = makeCard("My Leave", "我的假勤", "", "leave");
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
    const hook = typeof window.onLeaveRendered === "function" ? window.onLeaveRendered : null;   // 掛鉤在 window 上：my.html 沒這名字
    try {
        LV = await mjson("/api/v1/me/leave/summary" + (typeof window.LV_LIMIT === "number" ? "?limit=" + window.LV_LIMIT : ""));
        _lvInv = (await mjson("/api/v1/me/leave/inventory" + (_lvEditing ? "?exclude=" + encodeURIComponent(_lvEditing) : ""))).items || [];
    } catch (e) {
        body.innerHTML = `<div class="empty">${esc(e.message || "載入失敗")}</div>`;
        if (hook) hook(null, e);   // 宿主的總表也要知道（例如沒綁人員檔案的 409）
        return;
    }
    renderLeave(body);
    if (hook) hook(LV);
}
const LV_RECENT_MAX = 20;   // 卡片裡只列最近這些筆；整本在 /leave.html 的休假總表
// 時數帳還沒建的人：特休／補休可用、待審保留、將到期全是 0 —— 這時才掛「開發中」那條說明與徽章
// （2026-09-15 起有人的時數帳會陸續建好，不能再對每個人都說「還沒匯入」）。
function _lvLedgerEmpty(bal) {
    return ["特休", "補休"].every(k => { const b = (bal || {})[k] || {}; return !Number(b.available) && !Number(b.reserved) && !(b.expiring || []).length; });
}
const _lvH = (h) => { const n = Number(h || 0); return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, ""); };
function _lvDays(h) { return _lvH(Number(h || 0) / ((LV && LV.vocab && LV.vocab.hours_per_day) || 8)); }
// 工作台的假勤卡＝**輸入的窗口**（owner 2026-09-15「我希望這裡是輸入的窗口，頁面頂部才是跳出的詳情視窗」）：
// 三步表單就在卡裡，清單只列最近幾張；最上排「假勤」開的浮動視窗（/leave.html）才是整本總表與大家的休假。
const _lvFullHost = () => typeof window.onLeaveRendered === "function";
const LV_CARD_RECENT = 5;      // 工作台卡裡最近幾張；整本在 /leave.html
function renderLeave(body) {
    const v = LV.vocab || {};
    const bal = LV.balances || {};
    const an = bal["特休"] || {}, comp = bal["補休"] || {};
    const parts = v.parts || Object.keys(LV_PART_FALLBACK);
    const today = _localToday();
    const expiring = (an.expiring || [])[0];
    const ledgerEmpty = _lvLedgerEmpty(bal);
    let html = `${ledgerEmpty ? `<div class="wip-note">你的特休／補休時數帳還沒建，所以這兩個數字是 0、這兩種假挑不到。要用請找管理員在人事管理的時數帳補額度。</div>` : ""}
    <div class="stat-row">
        <div class="stat"><div class="num">${_lvH(an.available)}<span style="font-size:13px;color:var(--sub);"> h（${_lvDays(an.available)} 天）</span></div><div class="lbl">特休剩餘</div></div>
        <div class="stat"><div class="num">${_lvH(comp.available)}<span style="font-size:13px;color:var(--sub);"> h（${_lvDays(comp.available)} 天）</span></div><div class="lbl">補休剩餘</div></div>
    </div>
    ${(an.reserved || comp.reserved || expiring) ? `<div class="meta" style="font-size:11px;color:var(--sub);margin:-4px 0 8px;">
        ${an.reserved ? `特休待審保留 ${_lvH(an.reserved)} h　` : ""}${comp.reserved ? `補休待審保留 ${_lvH(comp.reserved)} h　` : ""}${expiring ? `特休最近到期：${_lvH(expiring.hours)} h（${esc(expiring.expires_on)}）` : ""}
    </div>` : ""}
    <div class="pf-edit" id="lv-form" style="border-top:1px solid #f5f5f5;padding-top:12px;padding-bottom:4px;">
        <div class="lv-step"><span class="lv-n">1</span>日期<span class="lv-need" id="lv-need"></span></div>
        <div class="inline-row" style="margin-bottom:8px;">
            <select id="lv-mode"><option value="range">起迄日期</option><option value="pick">挑幾天（不連續）</option></select>
            <select id="lv-part">${parts.map(p => `<option value="${esc(p)}">${esc(LV_PART_LABEL(p))}</option>`).join("")}</select>
        </div>
        <div class="inline-row" id="lv-range-row" style="margin-bottom:8px;">
            <input type="date" id="lv-start" value="${today}">
            <input type="date" id="lv-end" value="${today}">
        </div>
        <div id="lv-multi" style="display:none;margin-bottom:8px;">
            <div class="inline-row" style="margin-bottom:6px;">
                <input type="date" id="lv-multi-date" value="${today}">
                <button class="mini-btn" type="button" onclick="lvAddDate()">加入這天</button>
            </div>
            <div id="lv-multi-chips" style="display:flex;flex-wrap:wrap;gap:6px;"></div>
        </div>
        <div class="inline-row" id="lv-range" style="margin-bottom:8px;display:none;">
            <input type="time" id="lv-start-time" step="1800" value="09:00">
            <input type="time" id="lv-end-time" step="1800" value="13:00">
        </div>
        <div class="lv-step"><span class="lv-n">2</span>從自己的假裡挑要扣的<span class="lv-need" id="lv-fit"></span></div>
        <div id="lv-inv" class="lv-inv"></div>
        <div id="lv-preview" style="font-size:12px;color:var(--sub);margin:8px 0;line-height:1.7;"></div>
        <div class="lv-step"><span class="lv-n">3</span>送出</div>
        <div class="field"><textarea id="lv-reason" rows="2" placeholder="事由（必填）"></textarea></div>
        <div class="field" id="lv-proof-wrap" style="display:none;">
            <label style="display:block;font-size:11px;color:var(--sub);margin-bottom:4px;">證明（必附：診斷證明／掛號單／相關文件的照片或 PDF）</label>
            <input type="file" id="lv-proof" accept=".jpg,.jpeg,.png,.heic,.webp,.pdf">
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <button class="mini-btn" id="lv-submit" onclick="applyLeave()">送出請假</button>
            <button class="mini-btn" id="lv-cancel-edit" type="button" onclick="lvCancelEdit()" style="display:none;">取消編輯</button>
            <span class="err" id="lv-err" style="margin-top:0;"></span>
        </div>
    </div>`;
    html += _lvListHtml();
    body.innerHTML = html;
    _lvDates = []; _lvPicks = _lvPicks.filter(id => _lvInv.some(i => i.id === id));   // 重畫（送單後）就回到起迄模式；編輯中的挑法保留
    const form = $("lv-form");
    form.addEventListener("input", (e) => { if (e.target.id !== "lv-reason") lvPreviewSoon(); });
    form.addEventListener("change", (e) => {
        if (e.target.id === "lv-part") $("lv-range").style.display = e.target.value === "range" ? "" : "none";
        if (e.target.id === "lv-mode") lvSetMode(e.target.value);
        if (e.target.id === "lv-start" && $("lv-end").value < e.target.value) $("lv-end").value = e.target.value;
        if (e.target.id !== "lv-reason") lvPreviewSoon();
    });
    _lvDrawInv(); _lvDrawChips();
    if (_lvEditing) _lvFillEditing();
    lvPreviewSoon();
}
// ── 清單（申請單為主；沒有申請單的舊單／手機單照舊一列一筆）──
function _lvListHtml() {
    const n = _lvFullHost() ? LV_RECENT_MAX : LV_CARD_RECENT;
    const apps = (LV.applications || []).slice(0, n).map(_lvAppRow).join("");
    const legacy = (LV.requests || []).filter(r => !r.application_id).slice(0, n).map(_lvRow).join("");
    const more = !_lvFullHost() && ((LV.applications || []).length > n || (LV.requests || []).filter(r => !r.application_id).length > n)
        ? `<div style="padding:10px 0 2px;"><button class="mini-btn" type="button" onclick="openActionModal('/leave.html', '假勤')">看全部／休假總表</button></div>` : "";
    return apps + legacy + more;
}
function _lvDatesLabel(dates) {
    const md = d => esc(String(d).slice(5).replace("-", "/"));
    if (!dates || !dates.length) return "";
    if (dates.length === 1) return md(dates[0]);
    // 連續的寫成起～迄，不連續的逐個列（最多列 6 個）
    const consecutive = dates.every((d, i) => i === 0 || (new Date(d) - new Date(dates[i - 1])) / 86400000 <= 3);
    return consecutive && dates.length > 2 ? `${md(dates[0])}～${md(dates[dates.length - 1])}（${dates.length} 天）`
        : dates.slice(0, 6).map(md).join("、") + (dates.length > 6 ? `…共 ${dates.length} 天` : "");
}
function _lvAppRow(a) {
    const hot = a.status === "待審" || a.status === "消假待審";
    const items = (a.items || []).map(i => `${esc(i.label || i.kind)} ${_lvH(i.hours)}h`).join("、");
    const metas = [`扣：${items || "—"}`];
    if (a.reason) metas.push(esc(a.reason));
    if (a.status === "已退回" && a.reject_note) metas.push(`退回理由：${esc(a.reject_note)}`);
    if (a.status === "消假待審" && a.cancel_note) metas.push(`消假理由：${esc(a.cancel_note)}`);
    if (a.status === "已核准" && a.approved_by) metas.push(`核可：${esc(a.approved_by)}`);
    let act = "";
    if (a.status === "待審") {
        act = `<button class="mini-btn" onclick="lvEditApp('${esc(a.id)}')">編輯</button><button class="mini-btn" onclick="cancelLeaveApp('${esc(a.id)}', 'free')">撤回</button>`;
    } else if (a.status === "已核准") {
        if (a.cancel_mode === "free") act = `<button class="mini-btn" onclick="cancelLeaveApp('${esc(a.id)}', 'free')">撤回</button>`;
        else if (a.cancel_mode === "apply") act = `<button class="mini-btn" onclick="cancelLeaveApp('${esc(a.id)}', 'apply')">申請消假</button>`;
        else if (a.cancel_mode === "locked") act = `<button class="mini-btn" disabled title="颱風假當日不可消">颱風假當日不可消</button>`;
    }
    return `<div class="row">
        <div class="grow">
            <div class="title">請假單　${_lvDatesLabel(a.dates)}${a.part && a.part !== "all" ? `（${esc(LV_PART_LABEL(a.part))}）` : ""}　${_lvH(a.hours)} 小時／${_lvDays(a.hours)} 天</div>
            <div class="meta">${metas.join("　")}</div>
        </div>
        <span class="pill${hot ? " hot" : ""}">${esc(a.status)}</span>
        ${_lvAppProofCell(a)}${act}
    </div>`;
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
        ${_lvProofCell(r)}${act}
    </div>`;
}
// ── 第 1 步：日期 ──
function lvSetMode(mode) {
    const pick = mode === "pick";
    $("lv-multi").style.display = pick ? "" : "none";
    $("lv-range-row").style.display = pick ? "none" : "";
    if (!pick) _lvDates = [];
    _lvDrawChips();
}
const _lvPickMode = () => { const m = $("lv-mode"); return !!m && m.value === "pick"; };
function lvAddDate() {
    const d = $("lv-multi-date").value;
    if (!d || _lvDates.includes(d)) return;
    _lvDates.push(d); _lvDates.sort();
    _lvDrawChips(); lvPreviewSoon();
}
function lvRemoveDate(d) { _lvDates = _lvDates.filter(x => x !== d); _lvDrawChips(); lvPreviewSoon(); }
function _lvDrawChips() {
    const host = $("lv-multi-chips");
    if (!host) return;
    host.innerHTML = _lvDates.map(d => `<span class="pill" style="text-transform:none;letter-spacing:0;font-size:12px;">${esc(d.slice(5).replace("-", "/"))}
        <button type="button" onclick="lvRemoveDate('${esc(d)}')" style="border:0;background:none;cursor:pointer;color:var(--sub);padding:0 0 0 4px;font-size:12px;" title="拿掉">×</button></span>`).join("")
        || `<span style="font-size:12px;color:var(--sub);">還沒挑日期</span>`;
}
// ── 第 2 步：從自己的假裡挑（順序＝扣的順序）──
// 事假／婚假／喪假平常用不到，收在「其他假別」底下（owner 2026-09-15「這三項我希望有個下拉箭頭，可以收攏起來」）；
// 特休／補休／病假常用，永遠展開。收合狀態記在 localStorage；裡面有挑到的就強制展開（不然看不到自己挑了什麼）。
const LV_FOLDED_KINDS = ["事假", "婚假", "喪假"];
const LV_FOLD_KEY = "lv_inv_more_open";
function _lvInvRow(i) {
    const n = _lvPicks.indexOf(i.id);
    const off = i.available !== null && i.available !== undefined && Number(i.available) <= 0;
    const amount = i.available === null || i.available === undefined ? "不限" : `${_lvH(i.available)} h（${_lvDays(i.available)} 天）`;
    const sub = n >= 0 ? `第 ${n + 1} 個挑的` : [i.expires_on ? `${esc(i.expires_on)} 到期` : "", i.proof_required ? "要附證明" : "", i.paid && i.paid !== "給薪" ? esc(i.paid) : ""].filter(Boolean).join("・");
    return `<label class="lv-inv-row${off ? " off" : ""}${n >= 0 ? " on" : ""}">
        <input type="checkbox" ${n >= 0 ? "checked" : ""} ${off ? "disabled" : ""} onchange="lvTogglePick('${esc(i.id)}', this.checked)">
        <span><span class="lv-kind">${esc(i.kind)}</span>${esc(i.label)}</span>
        <span class="lv-amt">${off ? "沒有庫存" : amount}<small>${sub}</small></span>
    </label>`;
}
function _lvDrawInv() {
    const host = $("lv-inv");
    if (!host) return;
    if (!_lvInv.length) { host.innerHTML = `<div class="empty" style="padding:8px 0;">沒有可以挑的假</div>`; return; }
    const main = _lvInv.filter(i => !LV_FOLDED_KINDS.includes(i.kind));
    const more = _lvInv.filter(i => LV_FOLDED_KINDS.includes(i.kind));
    let open = false;
    try { open = localStorage.getItem(LV_FOLD_KEY) === "1"; } catch (_) { /* 私密視窗 */ }
    if (more.some(i => _lvPicks.includes(i.id))) open = true;
    host.innerHTML = main.map(_lvInvRow).join("") + (more.length ? `
        <button type="button" class="lv-inv-more${open ? " open" : ""}" onclick="lvToggleInvMore()">
            <span class="lv-fold">▾</span>其他假別（${more.map(i => esc(i.kind)).join("／")}）
        </button>
        <div id="lv-inv-more" style="${open ? "" : "display:none;"}">${more.map(_lvInvRow).join("")}</div>` : "");
}
function lvToggleInvMore() {
    const box = $("lv-inv-more"), btn = document.querySelector(".lv-inv-more");
    if (!box) return;
    const open = box.style.display === "none";
    box.style.display = open ? "" : "none";
    if (btn) btn.classList.toggle("open", open);
    try { localStorage.setItem(LV_FOLD_KEY, open ? "1" : "0"); } catch (_) { /* 私密視窗 */ }
}
function lvTogglePick(id, on) {
    _lvPicks = _lvPicks.filter(x => x !== id);
    if (on) _lvPicks.push(id);
    _lvDrawInv(); lvPreviewSoon();
}
// ── 試算（後端 /me/leave/applications/preview 算：需要幾小時、每筆扣幾小時、還差幾小時、展開成哪幾張）──
function _lvAppPayload(withReason) {
    const part = $("lv-part").value;
    const p = {
        part, start_time: part === "range" ? $("lv-start-time").value : null, end_time: part === "range" ? $("lv-end-time").value : null,
        items: _lvPicks.map(id => ({ id })),
    };
    if (_lvPickMode()) p.dates = _lvDates.slice();
    else { p.dates = []; p.start_date = $("lv-start").value; p.end_date = $("lv-end").value || $("lv-start").value; }
    if (withReason) p.reason = ($("lv-reason").value || "").trim();
    return p;
}
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
    const p = _lvAppPayload(false);
    const need = $("lv-need"), fit = $("lv-fit"), btn = $("lv-submit");
    if (_lvPickMode() ? !p.dates.length : !p.start_date) {
        need.textContent = _lvPickMode() ? "先挑日期" : ""; fit.textContent = ""; host.innerHTML = ""; if (btn) btn.disabled = true; return;
    }
    let d;
    try { d = await mjson("/api/v1/me/leave/applications/preview" + (_lvEditing ? "?exclude=" + encodeURIComponent(_lvEditing) : ""), { method: "POST", body: JSON.stringify(p) }); }
    catch (e) { if (seq === _lvPreviewSeq) host.innerHTML = `<div style="color:var(--red);">${esc(e.message)}</div>`; return; }
    if (seq !== _lvPreviewSeq) return;
    const errs = d.errors || [], warns = d.warnings || [];
    need.textContent = `需要 ${_lvH(d.needed_hours)} 小時（${_lvDays(d.needed_hours)} 天）`;
    const picked = (d.takes || []).reduce((a, t) => a + Number(t.take || 0), 0);
    if (!(d.takes || []).length) fit.innerHTML = `<span style="color:#b45309;">還沒挑要扣的假</span>`;
    else if (Number(d.remain) > 0) fit.innerHTML = `<span style="color:#b45309;">已挑 ${_lvH(picked)} 小時 → 還差 ${_lvH(d.remain)} 小時，再挑一筆</span>`;
    else fit.innerHTML = `<span style="color:var(--ok, #15803d);">已挑 ${_lvH(picked)} 小時 → 剛好</span>`;
    // 每一筆扣多少、剩多少；沒扣到的說出來
    const takeLines = (d.takes || []).map(t => {
        const left = t.available === null || t.available === undefined ? "" : `，剩 ${_lvH(Number(t.available) - Number(t.take))} h`;
        return `<div>${esc(t.kind)}｜${esc(t.label || "")}：${Number(t.take) > 0 ? `扣 ${_lvH(t.take)} h${left}` : "<span style='color:var(--sub);'>已經夠了，這筆沒扣到</span>"}${t.proof_required && Number(t.take) > 0 ? " <b style='color:#b45309;'>要附證明</b>" : ""}</div>`;
    }).join("");
    const dayLines = (d.children || []).length && !errs.length
        ? `<div style="margin-top:4px;">這張單每一天扣的是：</div>` + (d.children || []).map(c => `<div>${esc(c.date.slice(5).replace("-", "/"))}　${esc(c.kind)} ${_lvH(c.hours)} h${c.part && c.part !== "all" ? `（${esc(LV_PART_LABEL(c.part))}${c.part === "range" ? " " + esc(c.start_time) + "–" + esc(c.end_time) : ""}）` : ""}</div>`).join("")
        : "";
    const sickNote = d.sick_offset ? `<div style="color:#b45309;">病假第 1 天給薪；其餘用特休／補休折抵（全薪）—— 系統照規章自動分。</div>` : "";
    host.innerHTML = takeLines + sickNote + dayLines + _lvMsgs(warns, "#b45309") + _lvMsgs(errs, "var(--red)");
    _lvProofNeeded = !!d.proof_required;
    const pw = $("lv-proof-wrap");
    if (pw) pw.style.display = _lvProofNeeded ? "" : "none";
    if (btn) btn.disabled = errs.length > 0;
}
// ── 第 3 步：送出（新單 POST；編輯中的單 PUT）──
async function applyLeave() {
    const errEl = $("lv-err");
    const show = (msg) => { errEl.innerHTML = msg; errEl.style.display = "inline"; };
    errEl.style.display = "none";
    const reason = ($("lv-reason").value || "").trim();
    if (!reason) { show("請填事由"); $("lv-reason").focus(); return; }
    if (_lvPickMode() && !_lvDates.length) { show("還沒挑日期"); return; }
    if (!_lvPicks.length) { show("還沒挑要扣的假"); return; }
    const proofFile = ($("lv-proof").files || [])[0];
    const editing = _lvEditing;
    const hasProof = editing && (LV.applications || []).some(a => a.id === editing && a.proof_path);
    if (_lvProofNeeded && !proofFile && !hasProof) { show("這種假要附證明（照片或 PDF）"); return; }
    // 送出中鎖住：連點兩下會建出兩張一模一樣的待審單
    const btn = $("lv-submit");
    if (btn) { if (btn.dataset.busy) return; btn.dataset.busy = "1"; btn.disabled = true; }
    try {
        const r = editing
            ? await mfetch("/api/v1/me/leave/applications/" + editing, { method: "PUT", body: JSON.stringify(_lvAppPayload(true)) })
            : await mfetch("/api/v1/me/leave/applications", { method: "POST", body: JSON.stringify(_lvAppPayload(true)) });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            const errs = Array.isArray(d.errors) ? d.errors : (Array.isArray(d.detail) ? d.detail : (d.detail && d.detail.errors) || []);
            show(errs.length ? errs.map(x => esc(x.msg || x.code || x)).join("<br>") : esc((typeof d.detail === "string" && d.detail) || "送出失敗"));
            return;
        }
        if (proofFile) {
            // 單建好了才傳證明；傳失敗要說出來（清單上那張會掛「缺證明，補傳」）
            const created = await r.json().catch(() => ({}));
            const up = created.id ? await _lvUploadAppProof(created.id, proofFile).catch(() => null) : null;
            if (!up || !up.ok) alert("申請單已送出，但證明上傳失敗，請在清單裡補傳。");
        }
        _lvEditing = ""; _lvPicks = [];
        _resetTodayStrip();   // 今天那條「請假待審 N 件」下次重抓
        await loadLeave();   // 🔴 要 await：不等重畫完就走到 finally，鎖會在舊表單還在畫面上時就解開，那段時間再點一次就是第二張單
    } catch (_) { show("連線失敗"); }
    finally { if (btn && btn.isConnected) { delete btn.dataset.busy; btn.disabled = false; } }   // 重畫過就換了節點，不用還原
}
// ── 編輯待審的申請單：把那張單灌回表單，送出走 PUT ──
async function lvEditApp(id) {
    const a = (LV.applications || []).find(x => x.id === id);
    if (!a) return;
    _lvEditing = id;
    _lvPicks = (a.items || []).map(i => i.id);
    await loadLeave();   // 重抓清單（排除自己那張的保留）再灌表單
    const f = $("lv-form");
    if (f) f.scrollIntoView({ behavior: "smooth", block: "start" });
}
function _lvFillEditing() {
    const a = (LV.applications || []).find(x => x.id === _lvEditing);
    if (!a) { _lvEditing = ""; return; }
    const dates = a.dates || [];
    const consecutive = dates.length > 1 && dates.every((d, i) => i === 0 || (new Date(d) - new Date(dates[i - 1])) / 86400000 <= 3);
    $("lv-part").value = a.part || "all";
    $("lv-range").style.display = a.part === "range" ? "" : "none";
    if (a.part === "range") { $("lv-start-time").value = a.start_time || "09:00"; $("lv-end-time").value = a.end_time || "13:00"; }
    if (dates.length <= 1 || consecutive) {
        $("lv-mode").value = "range"; lvSetMode("range");
        $("lv-start").value = dates[0] || _localToday(); $("lv-end").value = dates[dates.length - 1] || $("lv-start").value;
    } else {
        $("lv-mode").value = "pick"; lvSetMode("pick");
        _lvDates = dates.slice(); _lvDrawChips();
    }
    $("lv-reason").value = a.reason || "";
    $("lv-submit").textContent = "儲存修改";
    $("lv-cancel-edit").style.display = "";
    _lvDrawInv();
}
function lvCancelEdit() { _lvEditing = ""; _lvPicks = []; loadLeave(); }
// ── 撤回整張申請單（mode：free＝直接撤回；apply＝已核准且逾期，要寫消假理由送「消假待審」）──
async function cancelLeaveApp(id, mode) {
    let body = {};
    if (mode === "apply") {
        const note = (prompt("消假理由（必填，送出後由主管決定）") || "").trim();
        if (!note) return;
        body = { note };
    } else if (!confirm("確定撤回這張請假單？")) return;
    try {
        const r = await mfetch("/api/v1/me/leave/applications/" + id + "/cancel", { method: "POST", body: JSON.stringify(body) });
        if (r.ok) { if (_lvEditing === id) { _lvEditing = ""; _lvPicks = []; } _resetTodayStrip(); loadLeave(); }
        else { const d = await r.json().catch(() => ({})); alert((typeof d.detail === "string" && d.detail) || "撤回失敗"); }
    } catch (_) {}
}
// ── 證明：申請單一份（病假／婚假／喪假）；舊單照舊走 /me/leave/{id}/proof ──
function _lvAuthHeaders() {
    const h = {}; const tok = localStorage.getItem(TOKEN_KEY);
    if (tok) h["Authorization"] = "Bearer " + tok;
    return h;
}
// 上傳走 multipart：不能用 mfetch（它會補 JSON 的 Content-Type）
function _lvUploadAppProof(id, file) {
    const fd = new FormData(); fd.append("file", file);
    return fetch("/api/v1/me/leave/applications/" + id + "/proof", { method: "POST", headers: _lvAuthHeaders(), body: fd });
}
function _lvUploadProof(id, file) {
    const fd = new FormData(); fd.append("file", file);
    return fetch("/api/v1/me/leave/" + id + "/proof", { method: "POST", headers: _lvAuthHeaders(), body: fd });
}
async function _lvOpenBlob(url) {
    // <a href> 帶不了 Authorization，抓成 blob 再開（同 js/shared/utils.authDownload 的理由）
    try {
        const r = await fetch(url, { headers: _lvAuthHeaders() });
        if (!r.ok) { const d = await r.json().catch(() => ({})); alert((typeof d.detail === "string" && d.detail) || "開不了證明"); return; }
        const href = URL.createObjectURL(await r.blob());
        window.open(href, "_blank");
        setTimeout(() => URL.revokeObjectURL(href), 60000);
    } catch (_) { alert("連線失敗"); }
}
const viewLeaveProof = (id) => _lvOpenBlob("/api/v1/me/leave/" + id + "/proof");
const viewLeaveAppProof = (id) => _lvOpenBlob("/api/v1/me/leave/applications/" + id + "/proof");
async function _lvPickAndUpload(uploader, id, input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const r = await uploader(id, file).catch(() => null);
    if (!r || !r.ok) { const d = r ? await r.json().catch(() => ({})) : {}; alert((typeof d.detail === "string" && d.detail) || "上傳失敗"); return; }
    loadLeave();
}
const lvProofPick = (id, input) => _lvPickAndUpload(_lvUploadProof, id, input);
const lvAppProofPick = (id, input) => _lvPickAndUpload(_lvUploadAppProof, id, input);
const _lvNeedsProof = (t) => ((LV && LV.vocab && LV.vocab.proof_required_types) || ["病假"]).includes(t);
function _lvProofCell(r) {
    if (!_lvNeedsProof(r.leave_type)) return "";
    if (r.proof_path) return `<button class="mini-btn" onclick="viewLeaveProof('${esc(r.id)}')">看證明</button>`;
    if (r.status === "待審" || r.status === "消假待審" || r.status === "已核准")
        return `<label class="mini-btn" style="color:#b45309;border-color:#fcd34d;">缺證明，補傳<input type="file" accept=".jpg,.jpeg,.png,.heic,.webp,.pdf" hidden onchange="lvProofPick('${esc(r.id)}', this)"></label>`;
    return "";
}
function _lvAppProofCell(a) {
    if (!a.proof_required) return "";
    if (a.proof_path) return `<button class="mini-btn" onclick="viewLeaveAppProof('${esc(a.id)}')">看證明</button>`;
    if (a.status === "待審" || a.status === "消假待審" || a.status === "已核准")
        return `<label class="mini-btn" style="color:#b45309;border-color:#fcd34d;">缺證明，補傳<input type="file" accept=".jpg,.jpeg,.png,.heic,.webp,.pdf" hidden onchange="lvAppProofPick('${esc(a.id)}', this)"></label>`;
    return "";
}
// 舊單（沒有申請單的：手機送的、匯入的）的撤回照舊
async function cancelLeave(id, mode) {
    let body = {};
    if (mode === "apply") {
        const note = (prompt("消假理由（必填，送出後由主管決定）") || "").trim();
        if (!note) return;
        body = { note };   // schema LeaveCancel 收 note（之前送 cancel_note 必 422）
    } else if (!confirm("確定撤回這張請假單？")) return;
    try {
        const r = await mfetch("/api/v1/me/leave/" + id + "/cancel", { method: "POST", body: JSON.stringify(body) });
        if (r.ok) { _resetTodayStrip(); loadLeave(); }
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
