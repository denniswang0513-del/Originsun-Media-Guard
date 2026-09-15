// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第三區卡片（下）：我的假勤（時數帳＋申請單）／請款與薪酬／我的工時
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、money、mfetch、WS、makeCard、WIP_LABEL（cards.js）
// 跨檔提供：cardLeave、loadLeave、cardFinance、_localToday（zone1.js／week-plan.js／parttime.js 都用）、loadMyTs
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
let _lvDates = [];             // 「挑幾天」模式選好的日期（空＝用起迄那組）
let _lvTrim = {};              // 其中被削成剩下小時的那天 {date: hours}（試算回的）
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
    const hook = typeof window.onLeaveRendered === "function" ? window.onLeaveRendered : null;   // 掛鉤在 window 上：my.html 沒這名字
    try { LV = await mjson("/api/v1/me/leave/summary" + (typeof window.LV_LIMIT === "number" ? "?limit=" + window.LV_LIMIT : "")); }
    catch (e) {
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
function renderLeave(body) {
    const v = LV.vocab || {};
    const bal = LV.balances || {};
    const an = bal["特休"] || {}, comp = bal["補休"] || {};
    const sick = LV.sick || {};
    // 自助只開特休／補休／病假（owner 2026-09-15）；vocab 沒給 self_service_types 的舊後端退回整份清單
    const types = v.self_service_types || v.leave_types || ["特休", "補休", "病假"];
    const parts = v.parts || Object.keys(LV_PART_FALLBACK);
    const today = _localToday();
    const expiring = (an.expiring || [])[0];
    const ledgerEmpty = _lvLedgerEmpty(bal);
    const badge = document.querySelector('.card[data-card="leave"] .count.wip');
    if (badge) badge.style.display = ledgerEmpty ? "" : "none";
    let html = `${ledgerEmpty ? `<div class="wip-note">你的特休／補休時數帳還沒建，所以這兩個數字是 0、這兩種假送出會被擋（其他假別照常）。要用請找管理員在人事管理的時數帳補額度。</div>` : ""}
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
        <div style="margin:-2px 0 8px;">
            <button class="mini-btn" type="button" id="lv-multi-toggle" onclick="lvToggleMulti()">挑幾天（不連續）</button>
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
        <div id="lv-preview" style="font-size:12px;color:var(--sub);margin-bottom:8px;line-height:1.7;"></div>
        <div class="field"><textarea id="lv-reason" rows="2" placeholder="事由（必填）"></textarea></div>
        <div class="field" id="lv-proof-wrap" style="display:${_lvNeedsProof(types[0]) ? "" : "none"};">
            <label style="display:block;font-size:11px;color:var(--sub);margin-bottom:4px;">病假證明（必附：診斷證明或掛號單照片／PDF）</label>
            <input type="file" id="lv-proof" accept=".jpg,.jpeg,.png,.heic,.webp,.pdf">
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
            <button class="mini-btn" id="lv-submit" onclick="applyLeave()">送出請假</button>
            <span class="err" id="lv-err" style="margin-top:0;"></span>
        </div>
    </div>`;
    html += (LV.requests || []).slice(0, LV_RECENT_MAX).map(_lvRow).join("");
    body.innerHTML = html;
    _lvDates = []; _lvTrim = {};   // 重畫（送單後）就回到起迄模式
    const form = $("lv-form");
    form.addEventListener("input", (e) => { if (e.target.id !== "lv-reason") lvPreviewSoon(); });
    form.addEventListener("change", (e) => {
        if (e.target.id === "lv-part") $("lv-range").style.display = e.target.value === "range" ? "" : "none";
        if (e.target.id === "lv-type") $("lv-proof-wrap").style.display = _lvNeedsProof(e.target.value) ? "" : "none";
        if (e.target.id === "lv-start" && $("lv-end").value < e.target.value) $("lv-end").value = e.target.value;
        if (e.target.id !== "lv-reason") lvPreviewSoon();
    });
    lvPreviewSoon();
}
// 病假要附證明（vocab.proof_required_types；正本 core/leave_logic.PROOF_REQUIRED_TYPES）
const _lvNeedsProof = (t) => ((LV && LV.vocab && LV.vocab.proof_required_types) || ["病假"]).includes(t);
function _lvAuthHeaders() {
    const h = {}; const tok = localStorage.getItem(TOKEN_KEY);
    if (tok) h["Authorization"] = "Bearer " + tok;
    return h;
}
// 上傳走 multipart：不能用 mfetch（它會補 JSON 的 Content-Type）
function _lvUploadProof(id, file) {
    const fd = new FormData(); fd.append("file", file);
    return fetch("/api/v1/me/leave/" + id + "/proof", { method: "POST", headers: _lvAuthHeaders(), body: fd });
}
async function viewLeaveProof(id) {
    // <a href> 帶不了 Authorization，抓成 blob 再開（同 js/shared/utils.authDownload 的理由）
    try {
        const r = await fetch("/api/v1/me/leave/" + id + "/proof", { headers: _lvAuthHeaders() });
        if (!r.ok) { const d = await r.json().catch(() => ({})); alert((typeof d.detail === "string" && d.detail) || "開不了證明"); return; }
        const href = URL.createObjectURL(await r.blob());
        window.open(href, "_blank");
        setTimeout(() => URL.revokeObjectURL(href), 60000);
    } catch (_) { alert("連線失敗"); }
}
async function lvProofPick(id, input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const r = await _lvUploadProof(id, file).catch(() => null);
    if (!r || !r.ok) { const d = r ? await r.json().catch(() => ({})) : {}; alert((typeof d.detail === "string" && d.detail) || "上傳失敗"); return; }
    loadLeave();
}
function _lvProofCell(r) {
    if (!_lvNeedsProof(r.leave_type)) return "";
    if (r.proof_path) return `<button class="mini-btn" onclick="viewLeaveProof('${esc(r.id)}')">看證明</button>`;
    if (r.status === "待審" || r.status === "消假待審" || r.status === "已核准")
        return `<label class="mini-btn" style="color:#b45309;border-color:#fcd34d;">缺證明，補傳<input type="file" accept=".jpg,.jpeg,.png,.heic,.webp,.pdf" hidden onchange="lvProofPick('${esc(r.id)}', this)"></label>`;
    return "";
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
function _lvPayload() {
    const part = $("lv-part").value;
    return {
        leave_type: $("lv-type").value, start_date: $("lv-start").value, end_date: $("lv-end").value || $("lv-start").value,
        part, start_time: part === "range" ? $("lv-start-time").value : null, end_time: part === "range" ? $("lv-end-time").value : null,
    };
}
// ── 挑幾天（不連續）：owner 2026-09-15。選了日期就走 /me/leave/batch（一天一張單）；清空就回到起迄那組 ──
// 🔴 「挑幾天」開著就算挑幾天模式，不看有沒有挑到日期：原本用 _lvDates.length 判定，開了模式、還沒挑、直接按送出
//    會把被藏起來的起迄日期送出去（/polish 2026-09-15 BUG-1）。沒挑日期時試算與送出都要說「先挑日期」。
const _lvMultiOn = () => { const b = $("lv-multi"); return !!b && b.style.display !== "none"; };
const _lvMulti = () => _lvMultiOn();
function lvToggleMulti() {
    const box = $("lv-multi"), on = box.style.display === "none";
    box.style.display = on ? "" : "none";
    $("lv-start").parentElement.style.display = on ? "none" : "";
    $("lv-multi-toggle").textContent = on ? "改回起迄日期" : "挑幾天（不連續）";
    if (!on) { _lvDates = []; _lvTrim = {}; }
    _lvDrawChips(); lvPreviewSoon();
}
function lvAddDate() {
    const d = $("lv-multi-date").value;
    if (!d || _lvDates.includes(d)) return;
    _lvDates.push(d); _lvDates.sort();
    _lvDrawChips(); lvPreviewSoon();
}
function lvRemoveDate(d) { _lvDates = _lvDates.filter(x => x !== d); delete _lvTrim[d]; _lvDrawChips(); lvPreviewSoon(); }
function _lvDrawChips() {
    const host = $("lv-multi-chips");
    if (!host) return;
    host.innerHTML = _lvDates.map(d => `<span class="pill" style="text-transform:none;letter-spacing:0;font-size:12px;${d in _lvTrim ? "border-color:#fcd34d;color:#b45309;" : ""}">${esc(d.slice(5).replace("-", "/"))}${d in _lvTrim ? ` ${_lvH(_lvTrim[d])}h` : ""}
        <button type="button" onclick="lvRemoveDate('${esc(d)}')" style="border:0;background:none;cursor:pointer;color:var(--sub);padding:0 0 0 4px;font-size:12px;" title="拿掉">×</button></span>`).join("")
        || `<span style="font-size:12px;color:var(--sub);">還沒挑日期</span>`;
}
function _lvBatchPayload() {
    const p = _lvPayload();
    return { leave_type: p.leave_type, dates: _lvDates.slice(), part: p.part, start_time: p.start_time, end_time: p.end_time };
}
// 任何欄位一動就重算，但等 300ms 沒再動才真的打（打字改日期不會每個鍵一發）
function lvPreviewSoon() {
    clearTimeout(_lvPreviewTimer);
    _lvPreviewTimer = setTimeout(lvPreview, 300);
}
// 走時數帳的假別（特休／補休）：挑的過程直接看到「可用多少 → 這次用多少 → 還剩多少」（owner 2026-09-15）
function _lvBalanceLine(type, d) {
    const b = d.balance;
    if (!b || typeof b.available !== "number") return "";
    const free = Math.round((b.available - (b.reserved || 0)) * 100) / 100;
    const left = Math.round((free - Number(d.hours || 0)) * 100) / 100;
    const tr = d.trimmed || [];
    const tail = left < 0
        ? `<b style="color:var(--red);">超過 ${_lvH(-left)} 小時</b>`
        : (tr.length
            ? `<b style="color:#b45309;">剛好用完；${tr.map(t => `${esc(t.date.slice(5).replace("-", "/"))} 只休 ${_lvH(t.hours)} 小時`).join("、")}</b>`
            : `還剩 <b>${_lvH(left)} 小時（${_lvDays(left)} 天）</b>`);
    return `<div>${esc(type)}可用 ${_lvH(free)} 小時${b.reserved ? `（已扣掉待審保留 ${_lvH(b.reserved)}）` : ""} → 這次 ${_lvH(d.hours)} 小時 → ${tail}</div>`;
}
function _lvMsgs(list, color) {
    return (list || []).map(x => `<div style="color:${color};">${esc(x.msg || x.code || x)}</div>`).join("");
}
async function lvPreview() {
    const host = $("lv-preview");
    if (!host) return;
    const seq = ++_lvPreviewSeq;
    const multi = _lvMulti();
    const p = multi ? _lvBatchPayload() : _lvPayload();
    if (!multi && !p.start_date) { host.innerHTML = ""; return; }
    if (multi && !_lvDates.length) { host.innerHTML = `<div style="color:var(--sub);">先挑日期（按「加入這天」）</div>`; const b = $("lv-submit"); if (b) b.disabled = true; return; }
    let d;
    try { d = await mjson(multi ? "/api/v1/me/leave/batch/preview" : "/api/v1/me/leave/preview", { method: "POST", body: JSON.stringify(p) }); }
    catch (e) { if (seq === _lvPreviewSeq) host.innerHTML = `<div style="color:var(--red);">${esc(e.message)}</div>`; return; }
    if (seq !== _lvPreviewSeq) return;
    _lvTrim = Object.fromEntries((d.trimmed || []).map(t => [t.date, t.hours])); _lvDrawChips();   // 削過的那天在標籤上也標
    // 挑幾天：每一天自己的錯誤（撞單、假日、餘額用完）標上日期，整批的照常
    const perDay = multi ? (d.dates || []).flatMap(x => x.errors.map(e => ({ msg: `${x.date.slice(5).replace("-", "/")}：${e.msg}` }))) : [];
    const errs = [...perDay, ...(d.errors || [])], warns = d.warnings || [];
    host.innerHTML = `<div>${multi ? `挑了 ${_lvDates.length} 天，` : ""}共 ${_lvH(d.hours)} 小時（${_lvDays(d.hours)} 天）</div>${_lvBalanceLine(p.leave_type, d)}${_lvMsgs(warns, "#b45309")}${_lvMsgs(errs, "var(--red)")}`;
    const btn = $("lv-submit");
    if (btn) btn.disabled = errs.length > 0;
}
async function applyLeave() {
    const errEl = $("lv-err");
    const show = (msg) => { errEl.innerHTML = msg; errEl.style.display = "inline"; };
    errEl.style.display = "none";
    const reason = ($("lv-reason").value || "").trim();
    if (!reason) { show("請填事由"); $("lv-reason").focus(); return; }
    if (_lvMulti() && !_lvDates.length) { show("還沒挑日期"); return; }
    const proofFile = _lvNeedsProof($("lv-type").value) ? ($("lv-proof").files || [])[0] : null;
    if (_lvNeedsProof($("lv-type").value) && !proofFile) { show("病假要附證明（照片或 PDF）"); return; }
    // 送出中鎖住：連點兩下會建出兩張一模一樣的待審單（手機版的 withBusy 早就有，桌機這條原本沒有）
    const btn = $("lv-submit");
    if (btn) { if (btn.dataset.busy) return; btn.dataset.busy = "1"; btn.disabled = true; }
    try {
        const r = _lvMulti()
            ? await mfetch("/api/v1/me/leave/batch", { method: "POST", body: JSON.stringify(Object.assign(_lvBatchPayload(), { reason })) })
            : await mfetch("/api/v1/me/leave", { method: "POST", body: JSON.stringify(Object.assign(_lvPayload(), { reason })) });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            const errs = Array.isArray(d.errors) ? d.errors : (Array.isArray(d.detail) ? d.detail : (d.detail && d.detail.errors) || []);
            show(errs.length ? errs.map(x => esc(x.msg || x.code || x)).join("<br>") : esc((typeof d.detail === "string" && d.detail) || "送出失敗"));
            return;
        }
        if (proofFile) {
            // 單建好了才傳證明；傳失敗要說出來（清單上那筆會掛「缺證明，補傳」）。挑幾天＝每一張都要附同一份。
            const created = await r.json().catch(() => ({}));
            const ids = created.requests ? created.requests.map(x => x.id) : (created.id ? [created.id] : []);
            let failed = 0;
            for (const id of ids) { const up = await _lvUploadProof(id, proofFile).catch(() => null); if (!up || !up.ok) failed++; }
            if (failed) alert(`請假單已送出，但有 ${failed} 張證明上傳失敗，請在清單裡補傳。`);
        }
        _resetTodayStrip();   // 今天那條「請假待審 N 件」下次重抓
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
