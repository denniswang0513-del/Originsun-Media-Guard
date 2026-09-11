// ────────────────────────────────────────────────────────────────────────────
// ts-zone 視圖 3：專案查詢（工作追蹤「專案」表＋專案檔案，開放給員工，唯讀）＋ 專案檔案彈窗（團隊的一週點案名）
// ────────────────────────────────────────────────────────────────────────────
import { createBurnSorter, burnTbodyHtml, burnTableHtml, projectFileHtml } from "/js/shared/ts-projects.js";
import { z } from "./ctx.js";

async function _loadFindRows() {
    if (z.s.findRows) return z.s.findRows;
    // 員工端唯讀版（只有全案工時數字，沒金額、沒建議預算）；守衛跟這一區一樣是「有綁員工」，
    // 失敗就讓 loadFind 的 catch 顯示原因（以前的 /timesheets/projects → /summary 三段備援已拿掉）
    // 管理視角打 /timesheets/summary（同一張表的完整版：多 suggested_hours 與未對映；錢欄位是 P3）
    const d = await z.mjson(z.api.projectsBurn());
    z.s.findRows = d.projects || [];
    z.s.findUnmatched = d.unmatched || [];
    return z.s.findRows;
}
export async function loadFind() {
    const { $, esc, s } = z;
    const host = $("z1-find");
    host.innerHTML = `<div class="empty">載入中…</div>`;
    try { await _loadFindRows(); } catch (e) { host.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
    if (!s.findSorter) s.findSorter = createBurnSorter({ storageKey: "my_find_sort_v2", panelId: "my-burn-table", onChange: () => _redrawFindBody(), defaultSort: { key: "last", dir: "desc" } });   // 預設最新填報在最上面（owner 2026-09-05）
    _renderFindTable();
}
// 消耗率區間與最後填報區間都是固定選項（下拉），不用打數字
export const PCT_BANDS = [["", "消耗率：全部"], ["none", "未設預算"], ["lt60", "60% 以下"], ["60-90", "60–90%"], ["90-100", "90–100%"], ["over", "超過 100%"]];
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
    const st = z.s.findState, q = st.q.trim().toLowerCase();
    return (z.s.findRows || []).filter(p => (!st.status || (p.status || "") === st.status)
        && (!st.type || (p.project_type || "") === st.type)
        && _pctInBand(p.pct, st.pct) && _lastInRange(p.last_entry, st.from, st.to)
        && (!q || [p.project_name, p.status, p.project_type].some(x => String(x || "").toLowerCase().includes(q))));
}
function _findActive() { const st = z.s.findState; return !!(st.q || st.status || st.type || st.pct || st.from || st.to); }
function _redrawFindBody() {
    const tb = document.querySelector("#my-burn-table tbody");
    if (tb) { tb.innerHTML = burnTbodyHtml(z.s.findSorter.sorted(_findFiltered()), { editable: false, emptyText: "沒有符合的" }); z.s.findSorter.attach(); }
    const c = z.$("z1-find-count"); if (c) c.textContent = `${_findFiltered().length} 案`;
}
export function _renderFindTable() {
    const { $, esc, s } = z;
    const host = $("z1-find");
    const statuses = [...new Set((s.findRows || []).map(p => p.status).filter(Boolean))];
    const types = [...new Set((s.findRows || []).map(p => p.project_type).filter(Boolean))].sort();
    host.innerHTML = `
        <div class="vhead"><span class="ey">Find<b>專案查詢</b></span></div>
        <div class="find-bar">
            <input class="in" id="z1-find-q" placeholder="搜尋案名、狀態、案型…" value="${esc(s.findState.q)}">
            <select class="sel" id="z1-f-status"><option value="">狀態：全部</option>${statuses.map(x => `<option value="${esc(x)}" ${s.findState.status === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
            <select class="sel" id="z1-f-type"><option value="">案型：全部</option>${types.map(x => `<option value="${esc(x)}" ${s.findState.type === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
            <select class="sel" id="z1-f-pct">${PCT_BANDS.map(([v, l]) => `<option value="${v}" ${s.findState.pct === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <span class="meta">最後填報</span><input type="date" class="sel" id="z1-f-from" value="${esc(s.findState.from)}"><span class="meta">～</span><input type="date" class="sel" id="z1-f-to" value="${esc(s.findState.to)}">
            <button type="button" class="btn" id="z1-f-clear" ${_findActive() ? "" : "hidden"}>清除</button>
            <span class="meta" id="z1-find-count"></span></div>
        <div class="tsp" id="z1-find-body" style="overflow-x:auto;">${burnTableHtml("", "my-burn-table")}</div>`;
    _redrawFindBody();          // 表身、排序、計數只有它一份（殼先畫空的 tbody）
    const clear = $("z1-f-clear");
    const sync = () => { clear.hidden = !_findActive(); _redrawFindBody(); };
    $("z1-find-q").addEventListener("input", (e) => { s.findState.q = e.target.value; sync(); });
    [["z1-f-status", "status"], ["z1-f-type", "type"], ["z1-f-pct", "pct"], ["z1-f-from", "from"], ["z1-f-to", "to"]].forEach(([id, key]) =>
        $(id).addEventListener("change", (e) => { s.findState[key] = e.target.value; sync(); }));
    clear.addEventListener("click", () => {
        Object.assign(s.findState, { q: "", status: "", type: "", pct: "", from: "", to: "" });
        $("z1-find-q").value = ""; ["z1-f-status", "z1-f-type", "z1-f-pct", "z1-f-from", "z1-f-to"].forEach(id => { $(id).value = ""; });
        sync();
    });
}
export async function _openFindProject(name, pid) {
    const { $, esc } = z;
    const body = $("z1-find-body");
    if (!body) return;
    body.innerHTML = `<div class="empty">載入中…</div>`;
    try {
        const d = await z.mjson(z.api.projectFile(name, pid));
        body.innerHTML = projectFileHtml(d, { editable: false, chartWidth: 340, backHtml: '<button type="button" class="btn" data-z1="find-back">‹ 專案清單</button>' });
        body.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
        body.innerHTML = `<div class="notice">${esc(e.message)}</div><button type="button" class="btn" data-z1="find-back">‹ 專案清單</button>`;
    }
}

// ── 專案檔案彈窗（團隊的一週點案名；owner 2026-09-06：要彈出視窗可打叉，不是跳頁）──
export async function _openProjectModal(name, pid) {
    const { $, esc } = z;
    const root = z.hooks.modalRoot();
    let bg = $("ws-proj-modal");
    if (!bg) {
        bg = document.createElement("div");
        bg.id = "ws-proj-modal"; bg.className = "ws-modal-bg";
        bg.addEventListener("click", (e) => { if (e.target === bg || e.target.closest(".ws-modal-x")) bg.remove(); });
        root.appendChild(bg);
        if (!window.__wsModalEsc) {       // 全頁只掛一次（每開一次掛一個會累積）
            window.__wsModalEsc = true;
            document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("ws-proj-modal")?.remove(); });
        }
    }
    const x = '<button type="button" class="ws-modal-x" title="關閉">×</button>';
    bg.innerHTML = `<div class="ws-modal">${x}<div class="empty">載入中…</div></div>`;
    try {
        const d = await z.mjson(z.api.projectFile(name, pid));
        bg.innerHTML = `<div class="ws-modal">${x}<div class="tsp">${projectFileHtml(d, { modal: true, editable: false, chartWidth: 340 })}</div></div>`;
    } catch (e) {
        bg.innerHTML = `<div class="ws-modal">${x}<div class="notice">${esc(e.message)}</div></div>`;
    }
}
