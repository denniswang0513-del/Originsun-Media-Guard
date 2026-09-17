// ────────────────────────────────────────────────────────────────────────────
// /leave.html 我的假勤獨立頁 —— 宿主層（owner 2026-09-15「在這裡新增假勤」）
//
// ⚠ 傳統 script（不是 module），與同頁的 js/my/cards-hr.js 共用全域詞法環境，**要先載**。
// 這支做兩件事：
//   1. 備好 cards-hr.js 在工作台由 shell.js／cards.js／zone1.js 提供的全域（$、esc、mfetch、mjson、
//      makeCard、WIP_LABEL、_resetTodayStrip）—— 最小版本，makeCard 不做收合。
//   2. 畫右邊的「休假總表」：cards-hr.js 每次畫完卡（載入／送單／撤回後）叫 window.onLeaveRendered(LV, err)。
// 核准後上 Google 日曆是後端的事（api_hr._calendar_sync_leave），這裡只標「已上日曆／日曆未同步」。
//   3. 管理層的「大家的休假」（唯讀）：/api/v1/hr/balances（全員餘額）＋ /api/v1/hr/leave（清單）——
//      只畫，不放核准／退回鈕（那是 CRM 人事管理的事）。leave.html 的 boot 依鑰匙決定要不要叫 loadTeamLeave。
// 跨檔用到：cardLeave（cards-hr.js；leave.html 的 module 段呼叫）
// 跨檔提供：onLeaveRendered、loadTeamLeave
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
/** 總表一列要顯示的欄位（已跳脫）：表格列 _histRow 跟手機卡片 _histCard 共用，兩邊永遠同一份。 */
function _histParts(r) {
    const part = ((_lvNow && _lvNow.vocab && _lvNow.vocab.part_labels) || {})[r.part] || (r.part === "all" ? "整天" : r.part || "");
    const tm = r.part === "range" && r.start_time ? ` ${esc(r.start_time)}–${esc(r.end_time || "")}` : "";
    const period = r.start_date === r.end_date ? esc(r.start_date) : `${esc(r.start_date)} ~ ${esc(r.end_date)}`;
    const dim = r.status === "已退回" || r.status === "已撤回";
    // 同 cards-hr.js 的 _lvReasonText：故意各留一份，不跨檔引用（4 小時快取的地雷）
    const reasonText = String(r.reason || "").replace(/\s*~?\s*來源[:：]\s*\S+/g, "").trim();
    const notes = [reasonText, r.status === "已退回" && r.reject_note ? `退回：${r.reject_note}` : "",
                   r.status === "消假待審" && r.cancel_note ? `消假：${r.cancel_note}` : ""].filter(Boolean).map(esc).join("　");
    const pill = `<span class="pill${r.status === "待審" || r.status === "消假待審" ? " hot" : ""}">${esc(r.status)}</span>`;
    return { period, part: esc(part) + tm, dim, notes, pill, who: r.approved_by ? esc(r.approved_by) : "" };
}
function _histRow(r) {
    const p = _histParts(r);
    return `<tr${p.dim ? ' class="dim"' : ""}>
        <td>${p.period}</td><td>${esc(r.leave_type)}</td><td>${p.part}</td>
        <td class="num">${fmtH(r.hours)}</td><td class="num">${fmtD(r.hours)}</td>
        <td>${p.pill}</td>
        <td class="why">${p.notes}</td>
        <td>${p.who} ${_calTag(r)}</td>
    </tr>`;
}
/** 窄螢幕（≤640px）的總表：一列一卡——日期＋假別、右邊小時（天）、時段、狀態、核可／日曆，事由另起一行。 */
const _histNarrow = () => typeof matchMedia === "function" && matchMedia("(max-width: 640px)").matches;
function _histCard(r) {
    const p = _histParts(r);
    return `<div class="hist-card${p.dim ? " dim" : ""}">
        <div class="r1"><b>${p.period}　${esc(r.leave_type)}</b><span class="h">${fmtH(r.hours)} h <small>${fmtD(r.hours)} 天</small></span></div>
        <div class="r2"><span>${p.part}</span>${p.pill}${p.who ? `<span>核可 ${p.who}</span>` : ""}${_calTag(r)}</div>
        ${p.notes ? `<div class="r3">${p.notes}</div>` : ""}
    </div>`;
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
    const narrow = _histNarrow();
    $("hist-body").innerHTML = [...byYear.entries()].map(([y, list]) => `<div class="hist-year">
        <h3>${esc(y)} 年 <span class="tot">已核准合計：${totLine(list)}</span></h3>
        ${narrow ? `<div class="hist-cards">${list.map(_histCard).join("")}</div>` : `<div class="tbl-wrap"><table class="hist">
            <thead><tr><th>日期</th><th>假別</th><th>時段</th><th class="num">小時</th><th class="num">天</th><th>狀態</th><th>事由</th><th>核可／日曆</th></tr></thead>
            <tbody>${list.map(_histRow).join("")}</tbody>
        </table></div>`}</div>`).join("");
}
window.onLeaveRendered = onLeaveRendered;

// ── 大家的休假（管理層唯讀）────────────────────────────────────────────────
const TEAM_AHEAD_DAYS = 60;   // 「接下來」看多遠
function _isoShift(days) { const d = new Date(); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); }
function _teamLeaveLine(r, withName) {
    const period = r.start_date === r.end_date ? esc(r.start_date) : `${esc(r.start_date)} ~ ${esc(r.end_date)}`;
    const part = ((_lvNow && _lvNow.vocab && _lvNow.vocab.part_labels) || {})[r.part] || (r.part === "all" ? "" : r.part || "");
    return `<div class="row"><div class="grow"><div class="title">${withName ? esc(r.staff_name) + "　" : ""}${period}　${esc(r.leave_type)}${part ? `（${esc(part)}）` : ""}　${fmtH(r.hours)} h</div>${String(r.reason || "").replace(/\s*~?\s*來源[:：]\s*\S+/g, "").trim() ? `<div class="meta">${esc(String(r.reason || "").replace(/\s*~?\s*來源[:：]\s*\S+/g, "").trim())}</div>` : ""}</div><span class="pill${r.status === "待審" || r.status === "消假待審" ? " hot" : ""}">${esc(r.status)}</span></div>`;
}
async function loadTeamLeave() {
    const body = $("team-body");
    let bal, approved, pending, cancelling;
    try {
        [bal, approved, pending, cancelling] = await Promise.all([
            mjson("/api/v1/hr/balances"),
            mjson("/api/v1/hr/leave?status=" + encodeURIComponent("已核准")),
            mjson("/api/v1/hr/leave?status=" + encodeURIComponent("待審")),
            mjson("/api/v1/hr/leave?status=" + encodeURIComponent("消假待審")),
        ]);
    } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message || "載入失敗")}</div>`; return; }
    if (!_lvNow) _lvNow = { vocab: bal.vocab || {} };   // 自己那張卡沒畫（純管理層）時，時段字彙從這裡拿
    const today = bal.today || new Date().toISOString().slice(0, 10);
    const horizon = _isoShift(TEAM_AHEAD_DAYS);
    const upcoming = (approved.items || []).filter(r => r.end_date >= today && r.start_date <= horizon)
        .sort((a, b) => a.start_date.localeCompare(b.start_date));
    const hot = [...(pending.items || []), ...(cancelling.items || [])].sort((a, b) => a.start_date.localeCompare(b.start_date));
    const nextOf = {};
    for (const r of upcoming) if (!nextOf[r.staff_id]) nextOf[r.staff_id] = r;
    const staff = bal.staff || [];
    $("team-sum").textContent = `${staff.length} 人　·　接下來 ${TEAM_AHEAD_DAYS} 天 ${upcoming.length} 筆假　·　待審 ${hot.length} 筆`;
    const rows = staff.map(s => {
        const an = (s.balances || {})["特休"] || {}, comp = (s.balances || {})["補休"] || {};
        const nx = nextOf[s.staff_id];
        return `<tr><td>${esc(s.name)}</td><td class="num">${hd(an.available)}</td><td class="num">${hd(comp.available)}</td>
            <td class="num">${fmtH(s.sick_used_days)} 天</td><td class="num">${s.pending_count ? `<span class="pill hot">${s.pending_count}</span>` : ""}</td>
            <td>${nx ? `${esc(nx.start_date)}${nx.end_date !== nx.start_date ? " ~ " + esc(nx.end_date) : ""}　${esc(nx.leave_type)}` : `<span style="color:#a3a3a3">—</span>`}</td></tr>`;
    }).join("");
    body.innerHTML = `<div class="tbl-wrap"><table class="hist">
        <thead><tr><th>人員</th><th class="num">特休剩餘</th><th class="num">補休剩餘</th><th class="num">病假已用</th><th class="num">待審</th><th>下一次休假</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="empty">沒有在職人員</td></tr>`}</tbody></table></div>
      <div class="hist-year"><h3>待審中 <span class="tot">${hot.length} 筆（核准在 CRM 人事管理）</span></h3>${hot.map(r => _teamLeaveLine(r, true)).join("") || `<div class="empty">沒有待審的單</div>`}</div>
      <div class="hist-year"><h3>接下來 ${TEAM_AHEAD_DAYS} 天 <span class="tot">已核准 ${upcoming.length} 筆</span></h3>${upcoming.map(r => _teamLeaveLine(r, true)).join("") || `<div class="empty">接下來沒有人排假</div>`}</div>`;
}
window.loadTeamLeave = loadTeamLeave;
