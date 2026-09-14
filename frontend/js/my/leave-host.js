// ────────────────────────────────────────────────────────────────────────────
// /leave.html 我的假勤獨立頁 —— 宿主層（owner 2026-09-15「在這裡新增假勤」）
//
// ⚠ 傳統 script（不是 module），與同頁的 js/my/cards-hr.js 共用全域詞法環境，**要先載**。
// 這支做兩件事：
//   1. 備好 cards-hr.js 在工作台由 shell.js／cards.js／zone1.js 提供的全域（$、esc、mfetch、mjson、
//      makeCard、WIP_LABEL、_resetTodayStrip）—— 最小版本，makeCard 不做收合。
//   2. 畫右邊的「休假總表」：cards-hr.js 每次畫完卡（載入／送單／撤回後）叫 window.onLeaveRendered(LV, err)。
// 核准後上 Google 日曆是後端的事（api_hr._calendar_sync_leave），這裡只標「已上日曆／日曆未同步」。
// 跨檔用到：cardLeave（cards-hr.js；leave.html 的 module 段呼叫）
// ────────────────────────────────────────────────────────────────────────────
const TOKEN_KEY = "auth_token";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
const WIP_LABEL = "開發中";
const money = (n) => "NT$ " + Number(n || 0).toLocaleString("zh-TW");   // cardFinance 用（這頁不畫那張卡，但同檔要有來源）
window.LV_LIMIT = 500;                      // 休假總表要整本（後端上限 LEAVE_SUMMARY_LIMIT_MAX）；cards-hr.js 從 window 讀
const _resetTodayStrip = () => {};          // 這頁沒有工作台「今天」那條
async function mfetch(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const tok = localStorage.getItem(TOKEN_KEY);
    if (tok) headers["Authorization"] = "Bearer " + tok;
    return fetch(path, Object.assign({}, opts, { headers }));
}
async function mjson(path, opts) {
    const r = await mfetch(path, opts);
    if (!r.ok) { const d = await r.json().catch(() => ({})); const e = new Error(d.detail || ("載入失敗（" + r.status + "）")); e.status = r.status; throw e; }
    return r.json();
}
function makeCard(en, zh, countText, key) {
    const el = document.createElement("div");
    el.className = "card";
    el.dataset.card = key || en;
    el.innerHTML = `<div class="card-head"><span class="eyebrow">${en} <span class="accent">${zh}</span></span>
        <span style="display:flex;align-items:baseline;gap:10px;">
            ${countText ? `<span class="count${countText === WIP_LABEL ? " wip" : ""}">${countText}</span>` : ""}
            <span class="fold"></span>
        </span></div><div class="card-body"></div>`;
    return el;
}

// ── 休假總表：cards-hr.js 每次畫完卡（載入／送單／撤回後）都會叫 onLeaveRendered(LV) ──
const fmtH = (h) => { const n = Number(h || 0); return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, ""); };
let _lvNow = null;                          // 掛鉤最近一次傳進來的 summary（不摸 cards-hr.js 的全域 LV）
const perDay = () => (_lvNow && _lvNow.vocab && _lvNow.vocab.hours_per_day) || 8;
const fmtD = (h) => fmtH(Number(h || 0) / perDay());
const hd = (h) => `${fmtH(h)} h（${fmtD(h)} 天）`;

function _totals(rows) {
    // 只算已核准（待審／退回／撤回不占假）；回 {假別: 小時}，照 vocab 的假別順序
    const t = {};
    for (const r of rows) if (r.status === "已核准") t[r.leave_type] = (t[r.leave_type] || 0) + Number(r.hours || 0);
    const order = (_lvNow && _lvNow.vocab && _lvNow.vocab.leave_types) || Object.keys(t);
    return Object.keys(t).sort((a, b) => order.indexOf(a) - order.indexOf(b)).map(k => [k, t[k]]);
}
const totLine = (rows) => _totals(rows).map(([k, h]) => `${esc(k)} ${hd(h)}`).join("　") || "沒有已核准的假";

function _calTag(r) {
    if (r.status !== "已核准") return "";
    return r.google_event_id ? `<span class="cal-tag">已上日曆</span>` : `<span class="cal-tag off">日曆未同步</span>`;
}
function _histRow(r) {
    const part = ((_lvNow && _lvNow.vocab && _lvNow.vocab.part_labels) || {})[r.part] || (r.part === "all" ? "整天" : r.part || "");
    const tm = r.part === "range" && r.start_time ? ` ${esc(r.start_time)}–${esc(r.end_time || "")}` : "";
    const period = r.start_date === r.end_date ? esc(r.start_date) : `${esc(r.start_date)} ~ ${esc(r.end_date)}`;
    const dim = r.status === "已退回" || r.status === "已撤回";
    const notes = [r.reason, r.status === "已退回" && r.reject_note ? `退回：${r.reject_note}` : "",
                   r.status === "消假待審" && r.cancel_note ? `消假：${r.cancel_note}` : ""].filter(Boolean).map(esc).join("　");
    return `<tr${dim ? ' class="dim"' : ""}>
        <td>${period}</td><td>${esc(r.leave_type)}</td><td>${esc(part)}${tm}</td>
        <td class="num">${fmtH(r.hours)}</td><td class="num">${fmtD(r.hours)}</td>
        <td><span class="pill${r.status === "待審" || r.status === "消假待審" ? " hot" : ""}">${esc(r.status)}</span></td>
        <td class="why">${notes}</td>
        <td>${r.approved_by ? esc(r.approved_by) : ""} ${_calTag(r)}</td>
    </tr>`;
}
function onLeaveRendered(lv, err) {
    _lvNow = lv;
    if (!lv) { $("hist-sum").textContent = ""; $("hist-body").innerHTML = `<div class="empty">${esc((err && err.message) || "載入失敗")}</div>`; return; }
    const rows = (lv.requests || []).slice().sort((a, b) => (b.start_date || "").localeCompare(a.start_date || ""));
    const thisYear = String(new Date().getFullYear());
    const yr = rows.filter(r => (r.start_date || "").startsWith(thisYear));
    $("hist-sum").innerHTML = `${esc(thisYear)} 年已核准：${totLine(yr)}` +
        (lv.pending_count ? `　·　待審 ${lv.pending_count} 筆` : "") +
        (lv.hire_date ? `　·　到職 ${esc(lv.hire_date)}（法定特休 ${fmtH(lv.annual_days_by_law)} 天／年）` : "");
    if (!rows.length) { $("hist-body").innerHTML = `<div class="empty">還沒有任何請假紀錄</div>`; return; }
    const byYear = new Map();
    for (const r of rows) { const y = (r.start_date || "").slice(0, 4) || "未定"; if (!byYear.has(y)) byYear.set(y, []); byYear.get(y).push(r); }
    $("hist-body").innerHTML = [...byYear.entries()].map(([y, list]) => `<div class="hist-year">
        <h3>${esc(y)} 年 <span class="tot">已核准合計：${totLine(list)}</span></h3>
        <div class="tbl-wrap"><table class="hist">
            <thead><tr><th>日期</th><th>假別</th><th>時段</th><th class="num">小時</th><th class="num">天</th><th>狀態</th><th>事由</th><th>核可／日曆</th></tr></thead>
            <tbody>${list.map(_histRow).join("")}</tbody>
        </table></div></div>`).join("");
}
window.onLeaveRendered = onLeaveRendered;
