// cards-ot.js — 員工工作台「加班申請」卡（docs/PAYROLL_OVERTIME_PLAN.md 第二批）。
// 傳統 script（同 cards-hr.js：與 shell.js／cards.js 共用一個全域詞法環境）；my.html 與 leave.html 都掛。
// 跨檔用到：$、esc、mfetch、mjson、makeCard（cards.js／leave-host.js 備好）。
// 跨檔提供：cardOvertime、loadOvertime。
// 規則都在後端（/api/v1/me/overtime/preview 算時數、判工作日／假日、換算補休或加班費、46／54 小時守衛）；
// 這裡只畫、只送起訖，不自己算。字彙從 vocab 拿。
let OT = null;                  // 最近一次 GET /me/overtime
let _otPreviewTimer = null;
let _otPreviewSeq = 0;
let _otBlocked = true;
const OT_PILL = { "待審": "hot", "已核准": "ok", "已退回": "", "已撤回": "" };

function cardOvertime(bound) {
    const card = makeCard("Overtime", "加班申請", "", "overtime");
    const body = card.querySelector(".card-body");
    if (!bound) { body.innerHTML = `<div class="empty">尚未綁定人員檔案</div>`; return card; }
    body.innerHTML = `<div class="empty">載入中…</div>`;
    setTimeout(loadOvertime, 0);
    return card;
}
function _otBody() { return document.querySelector('.card[data-card="overtime"] .card-body'); }
function _otStyle() {
    if (document.getElementById("ot-style")) return;
    const st = document.createElement("style"); st.id = "ot-style";
    st.textContent = `.ot-stats{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 12px}.ot-stat b{display:block;font-size:26px;font-weight:300;line-height:1.1}
.ot-stat b small{font-size:13px;color:var(--sub)}.ot-stat b.warn{color:var(--warn,#b45309)}.ot-stat b.bad{color:var(--red)}.ot-stat span{font-size:12px;color:var(--sub);letter-spacing:.08em}
.ot-radio{font-size:13px;display:flex;align-items:center;gap:4px;white-space:nowrap}.ot-radio input{width:auto}.fo-hint.warn{color:var(--warn,#b45309)}
.ot-shoot{border:1px solid var(--line);border-radius:2px;padding:6px 8px;font-size:13px;background:#fff;color:var(--ink)}.ot-list .pill{margin-left:6px}
.card[data-card=overtime] .fo-head b{white-space:nowrap}
@media (max-width:640px){.ot-stats{gap:12px}.ot-stat b{font-size:22px}.ot-list .fo-row{flex-wrap:wrap}.ot-list .fo-row .fo-meta{flex-basis:100%;white-space:normal}}`;
    document.head.appendChild(st);
}
async function loadOvertime() {
    const body = _otBody();
    if (!body) return;
    try {
        OT = await mjson("/api/v1/me/overtime");
    } catch (e) {
        body.innerHTML = `<div class="empty">${esc(e.message || "載入失敗")}</div>`;
        return;
    }
    renderOvertime(body);
}
function _otH(h) { const n = Number(h || 0); return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, ""); }
function renderOvertime(body) {
    _otStyle();
    const v = OT.vocab || {};
    const m = OT.month || {};
    const today = v.today || new Date().toISOString().slice(0, 10);
    const cap = v.month_cap || 46, warn = cap - (v.warn_before_cap || 8);
    const monthCls = m.hours > cap ? "bad" : (m.hours > warn ? "warn" : "");
    const payouts = v.payouts || ["補休", "加班費"];
    body.innerHTML = `
      <div class="ot-stats">
        <div class="ot-stat"><b class="${monthCls}">${_otH(m.hours)}<small> h</small></b><span>本月已報加班</span></div>
        <div class="ot-stat"><b>${_otH(m.credit_hours)}<small> h</small></b><span>換到的補休</span></div>
        <div class="ot-stat"><b>${Number(m.pay_amount || 0).toLocaleString("zh-TW")}<small> 元</small></b><span>換到的加班費</span></div>
      </div>
      <div class="fo-box">
        <div class="fo-head"><b>報加班</b><span class="fo-rule">${esc(v.rule_text || "")}</span></div>
        <div class="inline-row fo-times">
          <input type="date" id="ot-date" value="${esc(today)}" max="${esc(today)}" onchange="otDateChanged()">
          <input type="time" id="ot-a" step="1800" value="18:00" onchange="otPreview()">
          <span class="fo-dash">–</span>
          <input type="time" id="ot-b" step="1800" value="20:00" onchange="otPreview()">
        </div>
        <div class="inline-row">
          ${payouts.map((p, i) => `<label class="ot-radio"><input type="radio" name="ot-payout" value="${esc(p)}" ${i === 0 ? "checked" : ""} onchange="otPreview()"> 換${esc(p)}</label>`).join("")}
          <select id="ot-shoot" class="ot-shoot" onchange="otShootPicked()" style="display:none"></select>
        </div>
        <div class="inline-row">
          <input type="text" id="ot-project" placeholder="案子（選填；那天有場次會自動帶）" style="flex:1">
        </div>
        <div class="inline-row">
          <input type="text" id="ot-reason" placeholder="事由：做了什麼（必填）" style="flex:1">
          <button class="mini-btn" type="button" id="ot-submit" onclick="otSubmit()" disabled>送出</button>
        </div>
        <div class="fo-hint" id="ot-hint">填日期與起訖就會算</div>
        ${OT.has_pay_profile === false ? `<div class="fo-hint warn">你還沒有薪資主檔，換加班費核准時算不出金額；換補休不受影響。</div>` : ""}
      </div>
      <div class="ot-list" id="ot-list">${_otListHtml()}</div>`;
    otDateChanged();
}
function _otListHtml() {
    const items = (OT.items || []).slice(0, 8);
    if (!items.length) return `<div class="empty">今年還沒報過加班</div>`;
    return items.map(i => {
        const what = i.payout === "補休" ? `補休 ${_otH(i.credit_hours)} h` : (i.pay_amount != null ? `加班費 ${Number(i.pay_amount).toLocaleString("zh-TW")} 元${i.pay_month ? `（${esc(i.pay_month)} 薪資）` : ""}` : "加班費");
        return `<div class="fo-row"><span>${esc(i.date.slice(5).replace("-", "/"))}　${esc(i.start_time)}–${esc(i.end_time)}　${_otH(i.hours)} h（${esc(i.day_kind)}）</span>
          <span class="fo-meta">${what}${i.project_name ? "・" + esc(i.project_name) : ""}${i.status === "已退回" && i.reject_note ? "・退回：" + esc(i.reject_note) : ""}</span>
          <span class="pill ${OT_PILL[i.status] || ""}">${esc(i.status)}</span>
          ${i.status === "待審" ? `<button class="mini-btn" type="button" onclick="otCancel('${esc(i.id)}')">撤回</button>` : ""}</div>`;
    }).join("");
}
async function otDateChanged() {
    const d = $("ot-date"), sel = $("ot-shoot"), proj = $("ot-project");
    if (!d || !sel) return;
    sel.style.display = "none"; sel.innerHTML = "";
    if (d.value) {
        try {
            const items = (await mjson("/api/v1/me/overtime/shoots?date=" + encodeURIComponent(d.value))).items || [];
            if (items.length) {
                sel.innerHTML = `<option value="">從場次帶入…</option>` + items.map(s => `<option value="${esc(s.shoot_id)}" data-name="${esc(s.project_name || s.title)}">${esc(s.title || s.project_name)}</option>`).join("");
                sel.style.display = "";
                if (items.length === 1 && proj && !proj.value) { sel.value = items[0].shoot_id; proj.value = items[0].project_name || items[0].title; }
            }
        } catch (_) { /* 沒場次就手填 */ }
    }
    otPreview();
}
function otShootPicked() {
    const sel = $("ot-shoot"), proj = $("ot-project");
    const opt = sel && sel.selectedOptions[0];
    if (opt && opt.dataset.name && proj) proj.value = opt.dataset.name;
}
function _otPayout() { const r = document.querySelector('input[name="ot-payout"]:checked'); return r ? r.value : "補休"; }
function otPreview() {
    clearTimeout(_otPreviewTimer);
    _otPreviewTimer = setTimeout(async () => {
        const d = $("ot-date"), a = $("ot-a"), b = $("ot-b"), h = $("ot-hint"), btn = $("ot-submit");
        if (!d || !a || !b || !h || !btn) return;
        const seq = ++_otPreviewSeq;
        if (!d.value || !a.value || !b.value) { h.textContent = "填日期與起訖就會算"; h.className = "fo-hint"; _otBlocked = true; btn.disabled = true; return; }
        try {
            const r = await mfetch("/api/v1/me/overtime/preview", { method: "POST", body: JSON.stringify({ date: d.value, start_time: a.value, end_time: b.value, payout: _otPayout() }) });
            const p = await r.json().catch(() => ({}));
            if (seq !== _otPreviewSeq) return;
            if (!r.ok) { h.textContent = p.detail || "算不出來"; h.className = "fo-hint bad"; _otBlocked = true; btn.disabled = true; return; }
            const errs = (p.errors || []).map(e => e.msg), warns = (p.warnings || []).map(w => w.msg);
            let msg = `${_otH(p.hours)} 小時（${p.day_kind}）→ ` + (_otPayout() === "補休" ? `補休 ${_otH(p.credit_hours)} 小時` : (p.pay_amount != null ? `加班費 ${Number(p.pay_amount).toLocaleString("zh-TW")} 元` : "加班費（金額核准時算）"));
            msg += `；本月累計 ${_otH(p.month_total)} 小時`;
            if (errs.length) { h.textContent = errs.join("；"); h.className = "fo-hint bad"; _otBlocked = true; }
            else if (warns.length) { h.textContent = msg + "。" + warns.join("；"); h.className = "fo-hint warn"; _otBlocked = false; }
            else { h.textContent = msg; h.className = "fo-hint ok"; _otBlocked = false; }
            btn.disabled = _otBlocked;
        } catch (_) { h.textContent = "連線失敗"; h.className = "fo-hint bad"; _otBlocked = true; btn.disabled = true; }
    }, 250);
}
async function otSubmit() {
    const btn = $("ot-submit"), h = $("ot-hint"), reason = $("ot-reason");
    if (!btn || btn.disabled || _otBlocked) return;
    if (!reason.value.trim()) { h.textContent = "事由必填"; h.className = "fo-hint bad"; reason.focus(); return; }
    btn.disabled = true;
    const sel = $("ot-shoot");
    try {
        const r = await mfetch("/api/v1/me/overtime", { method: "POST", body: JSON.stringify({
            date: $("ot-date").value, start_time: $("ot-a").value, end_time: $("ot-b").value, payout: _otPayout(),
            reason: reason.value.trim(), project_name: ($("ot-project").value || "").trim() || null, shoot_id: (sel && sel.value) || null }) });
        const dd = await r.json().catch(() => ({}));
        if (!r.ok) { h.textContent = (Array.isArray(dd.detail) ? dd.detail.map(x => x.msg).join("；") : dd.detail) || "送出失敗"; h.className = "fo-hint bad"; btn.disabled = false; return; }
        await loadOvertime();
        const hh = $("ot-hint"); if (hh) { hh.textContent = "已送出，等主管核准。"; hh.className = "fo-hint ok"; }
    } catch (_) { h.textContent = "連線失敗"; h.className = "fo-hint bad"; btn.disabled = false; }
}
async function otCancel(id) {
    if (!confirm("撤回這張加班單？")) return;
    try { await mfetch("/api/v1/me/overtime/" + encodeURIComponent(id) + "/cancel", { method: "POST", body: "{}" }); } catch (_) { /* 下面重抓 */ }
    loadOvertime();
}
