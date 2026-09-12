// ────────────────────────────────────────────────────────────────────────────
// ts-zone 入口：掛載「今天與這週」（事件委派 `_z1Action`、視圖切換、四個視圖的 loader）。
//
// 宿主（員工頁 my.html／CRM 工作追蹤分頁）自己畫分頁鈕列（`.views` 裡的 `.view-btn[data-view]`）與四個
// `.view[data-view]#z1-<view>` 容器 —— 鈕列兩邊長得不一樣（CRM 多「看誰的」與管理次級鈕），視圖本身同一份。
// 然後 `mountZone({ host, ... })`：接事件、註冊 loader、切到第一個視圖。
// ────────────────────────────────────────────────────────────────────────────
import { appendBlankRows, removeRow, collectRows, saveRowNow, setStages } from "/js/shared/ts-sheet.js";
import { openStageEditor } from "/js/shared/stage-editor.js";
import { z, configure, switchZ1, setWho, _z1MarkStale, _shiftDays, _mondayOf, _mdLabel, _PUT, VIEWS } from "./ctx.js";
import { loadLog, mergeSameProject, undoMerge, resetToday, _logProjectOptions } from "./log.js";
import { loadMyWeek, _renderMyWeek, _planOpenAdd, _planSubmitAdd, _planDelete, _planMove, _planFromMilestones, _planCopyLast, _planWireDnd, _planCardHtml } from "./plan.js";
import { loadTeamWeek, _msToggleDone, _openMsModal } from "./team-week.js";
import { loadFind, _renderFindTable, _openFindProject, _openProjectModal, mapSheetName, applySuggestedBudgets, setProjectBudget, reopenFindProject } from "./find.js";

export { z, switchZ1, setWho, _z1MarkStale, resetToday, _logProjectOptions, _planCardHtml, loadLog, loadMyWeek, loadTeamWeek, loadFind };
export { _shiftDays, _dow, _mondayOf, _mdLabel, _prevWorkday, _isPlan, _POST, _PUT } from "./ctx.js";

/** 掛載。`opts` 見 ctx.configure；`opts.first`＝一開始切到哪個視圖（沒鑰匙會退到第一個有鑰匙的）。 */
export function mountZone(opts) {
    configure(opts);
    const host = z.host;
    z.loaders = { log: () => loadLog(), plan: () => loadMyWeek(), week: () => loadTeamWeek(), find: () => loadFind() };
    // 只認四個視圖的鈕：CRM 分頁把 總表／儀表板／設定 也放在同一列（.view-btn[data-view="ledger"]…），那幾顆是宿主的 ——
    // 收進來會先 switchZ1 退到第一個視圖、順手把 onView 記成 log，然後宿主才切走（review 2026-09-12）
    host.querySelector(".views").addEventListener("click", (e) => { const b = e.target.closest(".view-btn[data-view]"); if (b && VIEWS.includes(b.dataset.view)) switchZ1(b.dataset.view); });
    // 宿主自己的按鈕（CRM 分頁鈕列上的 總表／儀表板／設定）交回去；其餘在這裡處理完就不再冒泡
    //（CRM 分頁在外層也掛了一個 [data-ts-action] 的委派：row-remove／proj-pop 兩邊都收＝刪兩次、開兩個彈窗）
    host.addEventListener("click", (e) => {
        const b = e.target.closest("[data-z1], [data-ts-action]");
        if (!b || (z.hooks.passthrough && z.hooks.passthrough(b))) return;
        e.stopPropagation();
        _z1Action(b, e);
    });
    host.addEventListener("change", (e) => { const cb = e.target.closest("input[data-ms-done]"); if (cb) _msToggleDone(cb.dataset.msDone, cb.checked, cb); });   // 週表那條帶上直接勾完成
    host.addEventListener("keydown", (e) => { const f = e.target.closest("#z1-plan .addform"); if (f && e.key === "Enter") { e.preventDefault(); _planSubmitAdd(f.dataset.day); } });
    _planWireDnd(host);
    switchZ1(opts.first || "log");
    return z;
}

/** 第一區所有 `data-z1`／`data-ts-action` 按鈕的分派（宿主的彈窗，例如兼職排班，也把按鈕交到這裡）。 */
export async function _z1Action(btn, ev) {
    const { $, s } = z;
    const act = btn.dataset.z1 || btn.dataset.tsAction;
    const sheet = $("z1-sheet");
    if (z.hooks.onAction && z.hooks.onAction(act, btn, ev)) return;   // 宿主先挑（員工頁：pt-* 兼職排班視窗）
    if (act === "who") return setWho(btn.dataset.id ? { id: btn.dataset.id, name: btn.dataset.name || "" } : null);   // 管理視角：點名字／替他填
    if (act === "day") {
        const delta = Number(btn.dataset.delta);
        s.logDay = delta === 0 ? z.today() : _shiftDays(s.logDay, delta);
        return loadLog();
    }
    if (act === "row-add") { if (sheet) appendBlankRows(sheet); return; }
    if (act === "row-remove") { if (sheet) { if (await removeRow(sheet, btn.closest("tr"))) _z1MarkStale("z1-plan"); } return; }
    if (act === "row-defer") {
        // 計畫列挪到隔天（owner 2026-09-08）：只改 work_date，列本身不動；從今天的格子拿掉、我的一週下次切過去重抓
        const tr = btn.closest("tr"), day = _shiftDays(s.logDay, 1), el = $("z1-log-msg");
        if (!tr || !tr.dataset.id) return;
        try { await z.mjson(z.api.mineRow(tr.dataset.id), _PUT({ work_date: day })); tr.remove(); _z1MarkStale("z1-plan"); if (el) el.textContent = `已挪到 ${_mdLabel(day)}`; }
        catch (e) { if (el) el.textContent = "沒挪：" + e.message; }
        return;
    }
    if (act === "plan-week") { s.planWeek = btn.dataset.start || _mondayOf(z.today()); return loadMyWeek(); }
    if (act === "plan-add") return _planOpenAdd(btn.dataset.day);
    if (act === "plan-add-cancel") return _renderMyWeek();
    if (act === "plan-add-ok") return _planSubmitAdd(btn.dataset.day);
    if (act === "plan-del") return _planDelete(btn.dataset.id);
    if (act === "plan-defer") { const i = s.planRows.find(x => x.id === btn.dataset.id); return i && _planMove(i.id, _shiftDays(i.date, 1)); }
    if (act === "plan-from-ms") return _planFromMilestones();
    if (act === "plan-copy-last") return _planCopyLast();
    if (act === "save") {
        // 儲存草稿：每一列有內容就存（沒時數＝草稿，不進彙整）；其實每格改了就自動存，這顆只是催一次＋講清楚
        if (!sheet) return;
        const rows = collectRows(sheet);
        rows.forEach(r => { if (!r.readonly) saveRowNow(sheet, r.tr); });
        const noHours = rows.filter(r => !r.readonly && !(Number(r.hours) > 0)).length;
        const el = $("z1-log-msg");
        if (el) el.textContent = rows.length ? `已存 ${rows.length} 列` + (noHours ? `，其中 ${noHours} 列還沒填時數（草稿，不進彙整）` : "") : "還沒有填任何一列";
        return;
    }
    if (act === "day-goto") { s.logDay = btn.dataset.day || z.today(); return loadLog(); }
    if (act === "merge") return mergeSameProject();
    if (act === "unmerge") return undoMerge(btn.dataset.logId);
    if (act === "stages") {
        return openStageEditor({ onSaved: (map) => { s.logStages = map; if (sheet) setStages(sheet, map); } });
    }
    if (act === "week") { s.week = btn.dataset.start || _mondayOf(z.today()); return loadTeamWeek(); }
    if (act === "ms-open") return _openMsModal();
    if (act === "find-back") return _renderFindTable();
    if (act === "proj-pop") return _openProjectModal(btn.dataset.name || "", btn.dataset.pid || "");   // 團隊的一週點案名：彈窗
    if (act === "open-project") return _openFindProject(btn.dataset.name || "", btn.dataset.pid || "");
    // ── 管理視角（P3）：指定未對映、套用建議預算、改預算、加入比較 ──
    if (act === "map") return mapSheetName(btn.dataset.name || "");
    if (act === "suggest") return applySuggestedBudgets();
    if (act === "budget") return setProjectBudget(btn.dataset.pid || "", btn.dataset.cur || "");
    if (act === "compare-add" || act === "compare-remove") {
        const name = btn.dataset.name || "";
        s.compareNames = act === "compare-add" ? [...new Set([...s.compareNames, name])] : s.compareNames.filter(x => x !== name);
        if (z.hooks.onCompare) z.hooks.onCompare(s.compareNames);
        return reopenFindProject();   // 重畫同一個案：「並排比較（N）」那顆鈕才出現
    }
}
