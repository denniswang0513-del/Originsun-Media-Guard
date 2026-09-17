// ────────────────────────────────────────────────────────────────────────────
// ts-zone 的「要補填」那條（owner 2026-09-13「新增提醒區塊，有沒寫的工作日誌、哪一天沒填的專案日誌，好讓同事補填，
// 持續出現到填完為止」＋「可以有個按鈕給他跳到那個地方去填寫」）。
// 資料在 /me/reminders（core.reminder_logic：只提醒在職的人、到職日起、週末假日請假不算、今天不算）；
// 這裡只畫：每一項一顆「去填」—— 專案紀錄跳到那一天的格子、週記跳到那一週（宿主 hooks.journalGoto）。
// 畫在分頁鈕列**上面**（mountZone 插的 #z1-remind），哪個視圖都看得到；填完重抓就消失。
// ────────────────────────────────────────────────────────────────────────────
import { z, _mdLabel } from "./ctx.js";
import { isNarrow, ensureNarrowCss } from "./narrow.js";

/** 重抓＋重畫。宿主在週記送出後、格子存了列之後也可以叫（window.TSZ.refreshReminders）。 */
export async function loadReminders() {
    const host = z.host && z.host.querySelector("#z1-remind");
    if (!host) return;
    const url = z.api.reminders && z.api.reminders();
    if (!url) { host.innerHTML = ""; return; }            // 管理視角看別人／全部：沒有「我的」提醒
    let r;
    try { r = await z.mjson(url); } catch (_) { host.innerHTML = ""; return; }   // 提醒只是提醒，拿不到不擋整區
    host.innerHTML = isNarrow(host.parentElement || host) ? remindNarrowHtml(r) : remindHtml(r);
}

/** 窄螢幕（docs/WORKSPACE_RWD_PLAN.md 第二批）：一行摘要「專案紀錄沒填 N 天、週記沒送 M 週 → 先補最近那天」，點開才是完整那條。 */
export function remindNarrowHtml(r) {
    const full = remindHtml(r);
    if (!full) return "";
    ensureNarrowCss();
    const esc = z.esc;
    const parts = [];
    if ((r.log_missing || []).length) parts.push(`專案紀錄沒填 ${r.log_missing.length} 天`);
    if ((r.log_pending || []).length) parts.push(`草稿沒時數 ${r.log_pending.length} 天`);
    if ((r.journals || []).length) parts.push(`週記沒送 ${r.journals.length} 週`);
    const latest = [...(r.log_missing || []), ...(r.log_pending || [])].sort().pop();
    const go = latest ? `<button type="button" class="linkish warn" data-z1="day-goto" data-day="${esc(latest)}">先補 ${esc(_mdLabel(latest))}</button>` : "";
    return `<details class="tsn-remind"><summary><span class="k" style="color:#c9372c;font-weight:600">要補填</span><span>${parts.join("、")}</span>${go}<span class="meta">點開看全部</span></summary>${full}</details>`;
}

/** 純畫（測試用）。三段：專案紀錄沒填的日子／存了草稿沒填時數的日子／週記還沒送出的週。都空＝什麼都不畫。 */
export function remindHtml(r) {
    const esc = z.esc;
    if (!r || r.active === false) return "";
    const days = (list, act) => list.map(d => `<button type="button" class="linkish warn" data-z1="day-goto" data-day="${esc(d)}" title="${act}">${esc(_mdLabel(d))}</button>`).join("、");
    const weeks = (r.journals || []).map(j => {
        const ws = j.week_start, d = new Date(ws + "T00:00:00"), end = new Date(d.getTime() + 6 * 86400000);
        const md = (x) => `${x.getMonth() + 1}/${x.getDate()}`;
        const label = `${md(d)}–${md(end)}${j.status === "draft" ? "（草稿還沒送出）" : ""}`;
        // 宿主沒給 journalGoto（CRM 工作追蹤分頁沒有週記卡）：只列不跳
        return z.hooks.journalGoto ? `<button type="button" class="linkish warn" data-z1="journal-goto" data-week="${esc(ws)}" title="去寫這一週的回顧">${label}</button>` : `<span class="warn">${label}</span>`;
    }).join("、");
    const parts = [];
    if ((r.log_missing || []).length) parts.push(`<span><span class="k">專案紀錄沒填</span>${days(r.log_missing, "去填這一天")}</span>`);
    if ((r.log_pending || []).length) parts.push(`<span><span class="k">草稿還沒填時數</span>${days(r.log_pending, "去補時數")}</span>`);
    if (weeks) parts.push(`<span><span class="k">週記沒送出</span>${weeks}</span>`);
    if (!parts.length) return "";
    return `<div class="today-strip remind"><span><span class="k">要補填</span></span>${parts.join("")}<span class="meta">點日期就跳過去填；填完就不會再出現</span></div>`;
}
