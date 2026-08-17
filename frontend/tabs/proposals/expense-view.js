// 專案的「支出」分頁（docs/PETTY_CASH_PLAN.md §3.5）——「這個案子花了多少、花在哪」。
//
// 🔴 這一頁**不是**個人視角。誰墊的只是其中一欄；「我的單據」在 /petty-cash.html。
// 兩邊讀的是同一張表（crm_project_expenses），所以一筆現場登記同時餵專案成本
// 與個人請款 —— 這正是整合掉雙重登錄的那一刀。
//
// 閘門在呼叫端（/project.html 只在有 money_view 時才掛這個分頁）。後端那支端點
// 本身也掛 money_dep，所以就算有人繞過前端也拿不到。
//
// 住 tabs/proposals/ 而不是 tabs/petty/：/project.html 的 import 閉包只准
// tabs/proposals + js/shared（core/public_assets.py，tests/unit/test_public_surface.py
// 釘住）。這個目錄實際的意思是「/project.html 掛的元件」，不是「提案專用」——
// staff-view / delivery-view 也都住這裡。

const esc = (s) => { const d = document.createElement("div"); d.textContent = String(s ?? ""); return d.innerHTML; };
const money = (n) => (n === null || n === undefined) ? "—"
    : (n < 0 ? "-NT$ " : "NT$ ") + Math.abs(n).toLocaleString();

const CSS = `
<style>
.ex-sum { display:flex; gap:28px; flex-wrap:wrap; align-items:baseline;
  border:1px solid var(--line); padding:16px; margin-bottom:18px; }
.ex-sum .lbl { font-size:11px; letter-spacing:.2em; color:var(--sub); text-transform:uppercase; }
.ex-sum .big { font-size:28px; font-weight:600; letter-spacing:-.02em; }
.ex-sum .warn { color:var(--red); }
.ex-bar { height:4px; background:var(--line); margin-top:8px; width:180px; }
.ex-bar > i { display:block; height:100%; background:var(--ink); }
.ex-bar > i.over { background:var(--red); }
.ex-h { font-size:11px; letter-spacing:.15em; color:var(--sub); text-transform:uppercase;
  margin:22px 0 6px; }
.ex-row { display:grid; grid-template-columns:92px 1fr 110px 96px 120px 90px; gap:10px;
  align-items:center; padding:10px 0; border-bottom:1px solid var(--line); font-size:14px; }
.ex-row .amt { text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }
.ex-row .sub { color:var(--sub); font-size:12px; }
.ex-pill { display:inline-block; padding:1px 8px; border:1px solid var(--line);
  border-radius:999px; font-size:11px; color:var(--sub); }
.ex-item { display:flex; justify-content:space-between; padding:7px 0;
  border-bottom:1px solid var(--line); font-size:13px; max-width:420px; }
.ex-empty { color:var(--sub); font-size:13px; padding:22px 0; }
@media (max-width:720px) {
  .ex-row { grid-template-columns:1fr auto; row-gap:3px; }
  .ex-row .ex-h, .ex-row.head { display:none; }
}
</style>`;

/** host 裡畫出這個專案的支出。
 *  opts: { projectId, fetcher } —— `fetcher` 吃 **CRM 相對路徑**並回 parsed JSON，
 *  與 staff-view / delivery-view 同一個慣例（呼叫端傳的是 project.html 的
 *  `_crmFetch`，它自己補 `/api/v1/crm` 前綴）。 */
export async function renderExpenses(host, opts) {
    const { projectId } = opts;
    const f = opts.fetcher
        || ((p) => fetch("/api/v1/crm" + p).then(r => r.json()));
    host.innerHTML = CSS + '<div class="ex-empty">載入中…</div>';

    let data;
    try {
        data = await f(`/projects/${encodeURIComponent(projectId)}/expenses`);
    } catch (e) {
        host.innerHTML = CSS + '<div class="ex-empty" style="color:var(--red);">'
            + '支出載入失敗：' + esc(e && e.message || e) + "</div>";
        return;
    }

    const rows = (data.expenses || []).slice().sort(
        (a, b) => String(b.expense_date || b.created_at || "")
            .localeCompare(String(a.expense_date || a.created_at || "")));
    const total = rows.reduce((s, r) => s + (r.actual || 0), 0);
    const budget = data.misc_budget_total;          // null＝未設（不是 0）
    // 🔴 三態：`misc_budget_total` 不在 payload＝沒有金額權限（但這頁本來就要權限）；
    // 是 null＝真的沒設預算；有值才畫進度。用 `== null` 會把「未設」畫成 0 元預算。
    const hasBudget = budget !== null && budget !== undefined;
    const pct = hasBudget && budget > 0 ? Math.min(100, Math.round(total / budget * 100)) : 0;
    const over = hasBudget && total > budget;

    // 依會計項目小計 —— 這一頁回答的「花在哪」就是這張表
    const byItem = new Map();
    for (const r of rows) {
        const k = r.item || "（未分類）";
        byItem.set(k, (byItem.get(k) || 0) + (r.actual || 0));
    }
    const items = [...byItem.entries()].sort((a, b) => b[1] - a[1]);

    const unclaimed = rows.filter(r => r.status && r.status !== "已付款");

    host.innerHTML = CSS + `
      <div class="ex-sum">
        <div><div class="lbl">雜支實際</div><div class="big${over ? " warn" : ""}">${money(total)}</div>
          ${hasBudget ? `<div class="ex-bar"><i class="${over ? "over" : ""}"
             style="width:${pct}%"></i></div>` : ""}</div>
        <div><div class="lbl">雜支預算</div><div class="big">${
            hasBudget ? money(budget) : '<span style="font-size:16px;color:var(--sub);">未設</span>'}</div></div>
        ${hasBudget ? `<div><div class="lbl">${over ? "超支" : "剩餘"}</div>
          <div class="big${over ? " warn" : ""}">${money(Math.abs(budget - total))}</div></div>` : ""}
        <div><div class="lbl">筆數</div><div class="big">${rows.length}</div></div>
        ${unclaimed.length ? `<div><div class="lbl">尚未付款</div>
          <div class="big">${money(unclaimed.reduce((s, r) => s + (r.actual || 0), 0))}</div>
          <div class="sub" style="font-size:11px;color:var(--sub);">${unclaimed.length} 筆請款中</div></div>` : ""}
      </div>

      ${items.length ? `<div class="ex-h">依項目</div>
        ${items.map(([k, v]) => `<div class="ex-item"><span>${esc(k)}</span>
           <span style="font-variant-numeric:tabular-nums;">${money(v)}</span></div>`).join("")}` : ""}

      <div class="ex-h">明細</div>
      <div class="ex-row head"><span>日期</span><span>摘要</span><span>項目</span>
        <span class="amt">金額</span><span>誰墊的</span><span>狀態</span></div>
      ${rows.length ? rows.map(r => `
        <div class="ex-row">
          <span class="sub">${esc(r.expense_date || r.created_at || "待補")}</span>
          <span>${esc(r.sub_item || r.category || "（無摘要）")}
            ${r.receipt_url ? '<span class="ex-pill">收據</span>' : ""}
            ${r.invoice_no ? `<span class="sub"> ${esc(r.invoice_no)}</span>` : ""}</span>
          <span class="sub">${esc(r.item || r.category || "")}</span>
          <span class="amt">${money(r.actual)}</span>
          <span class="sub">${esc(r.staff_name || "—")}</span>
          <span>${r.status ? `<span class="ex-pill">${esc(r.status)}</span>` : ""}</span>
        </div>`).join("")
      : '<div class="ex-empty">這個案子還沒有支出紀錄。現場登記走 <a href="/petty-cash.html">零用金</a>。</div>'}

      <div class="ex-empty" style="font-size:12px;">
        成本認列用<strong>消費日</strong>：8 月拍攝的午餐即使 10 月才匯款，仍算 8 月的成本。
        請款狀態只影響現金流，不影響這裡的金額。</div>`;
}
