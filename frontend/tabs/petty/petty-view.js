// 零用金四個分頁的畫面（docs/PETTY_CASH_PLAN.md §3）。
//
// import 閉包只准 `tabs/petty/` 與 `js/shared/` —— 同 /project.html 的規則
// （tests/unit/test_public_surface.py 釘住）。頁面的 fetch 包裝由殼層放在
// `window.__petty` 上：同源同頁、只有這一個消費者，另建一層抽象只是多一個檔案。
const F = () => window.__petty;

const esc = (s) => { const d = document.createElement("div"); d.textContent = String(s ?? ""); return d.innerHTML; };
const money = (n) => (n === null || n === undefined) ? "—"
    : (n < 0 ? "-NT$ " : "NT$ ") + Math.abs(n).toLocaleString();
const today = () => new Date().toISOString().slice(0, 10);

async function get(path) {
    const r = await F().mfetch(path);
    if (!r.ok) {
        const e = new Error((await r.json().catch(() => ({}))).detail || ("HTTP " + r.status));
        e.status = r.status;          // 409（沒綁人員檔案）要當狀態畫，不是當錯誤
        throw e;
    }
    return r.json();
}
async function send(method, path, body) {
    // body 傳**物件**，由 fetch 包裝負責 stringify —— 對齊
    // js/shared/utils.authFetch 的合約，SPA 子視圖才能直接把 authFetch 接上來
    const r = await F().mfetch(path, { method, body });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ("HTTP " + r.status));
    return r.json().catch(() => ({}));
}

const CSS = `
<style>
.pc-sum { display:flex; gap:24px; flex-wrap:wrap; align-items:baseline;
  border:1px solid var(--line); padding:16px; margin-bottom:16px; }
.pc-sum .big { font-size:30px; font-weight:600; letter-spacing:-.02em; }
.pc-sum .lbl { font-size:11px; letter-spacing:.2em; color:var(--sub); text-transform:uppercase; }
.pc-form { border:1px solid var(--line); padding:16px; margin-bottom:16px; }
.pc-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; }
.pc-form label { display:block; font-size:11px; color:var(--sub); margin-bottom:3px; }
.pc-form input, .pc-form select, .pc-form textarea {
  width:100%; padding:10px; border:1px solid var(--line); border-radius:2px;
  font-size:15px; font-family:inherit; }
.pc-btn { padding:11px 20px; background:var(--ink); color:#fff; border:0; border-radius:2px;
  font-size:14px; cursor:pointer; }
.pc-btn.ghost { background:none; color:var(--sub); border:1px solid var(--line); }
.pc-btn.danger { background:none; color:var(--red); border:1px solid var(--line); }
.pc-btn:disabled { opacity:.45; cursor:not-allowed; }
.pc-row { display:grid; grid-template-columns:96px 1fr 110px 96px 150px; gap:10px;
  align-items:center; padding:11px 0; border-bottom:1px solid var(--line); font-size:14px; }
.pc-row .amt { text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }
.pc-row .sub { color:var(--sub); font-size:12px; }
.pc-head { font-size:11px; letter-spacing:.15em; color:var(--sub); text-transform:uppercase; }
.pc-pill { display:inline-block; padding:2px 8px; border:1px solid var(--line);
  border-radius:999px; font-size:11px; color:var(--sub); }
.pc-warn { color:var(--red); font-weight:600; }
.pc-empty { color:var(--sub); font-size:13px; padding:22px 0; text-align:center; }
.pc-actions { display:flex; gap:8px; justify-content:flex-end; }
@media (max-width:720px) {
  .pc-row { grid-template-columns:1fr auto; grid-auto-rows:min-content; row-gap:4px; }
  .pc-row .amt { text-align:right; }
  .pc-row .pc-actions { grid-column:1/-1; justify-content:flex-start; }
  .pc-head { display:none; }
}
</style>`;

// ── 分頁 1：我的請款 ────────────────────────────────────────────────
export async function renderMine(host) {
    let data, opts;
    try {
        [data, opts] = await Promise.all([
            get("/api/v1/crm/petty/me"), get("/api/v1/crm/petty/options")]);
    } catch (e) {
        // 帳號沒綁人員檔案不是故障，是「還差一步設定」——說清楚差哪一步，
        // 別讓人看到紅字的「載入失敗」以為系統壞了
        if (e.status === 409) {
            host.innerHTML = CSS + '<div class="pc-empty">'
                + '這個帳號還沒有綁定人員檔案，所以無法登記請款。<br>'
                + '請管理員在「使用者管理」把帳號連到你的人員資料。</div>';
            return;
        }
        throw e;
    }

    const projOpts = '<option value="">（公司支出，不歸專案）</option>'
        + opts.projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    const itemOpts = opts.items.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join("");

    host.innerHTML = CSS + `
    <div class="pc-sum">
      <div><div class="lbl">本期應請款</div><div class="big">${money(data.pending_total)}</div></div>
      ${data.petty_float ? `<div><div class="lbl">手上備用金</div><div class="big">${
          money(data.petty_float - data.pending_total)}</div>
          <div class="sub" style="font-size:11px;color:var(--sub);">額度 ${money(data.petty_float)}</div></div>` : ""}
      <div style="margin-left:auto;">
        <button class="pc-btn" id="pc-submit" ${data.pending.length ? "" : "disabled"}>
          送出請款（${data.pending.length} 筆）</button>
      </div>
    </div>

    <div class="pc-form">
      <div class="pc-grid">
        <div><label>日期</label><input type="date" id="f-date" value="${today()}"></div>
        <div><label>金額</label><input type="number" id="f-amt" inputmode="numeric" placeholder="0"></div>
        <div style="grid-column:span 2;"><label>摘要</label>
          <input id="f-sum" placeholder="例：兩廳院拍攝午餐"></div>
        <div><label>專案</label><select id="f-proj">${projOpts}</select></div>
        <div><label>項目（會計）</label><select id="f-item">${itemOpts}</select></div>
        <div><label>發票號碼（沒有可留白）</label><input id="f-inv" placeholder="AB12345678"></div>
        <div><label>收據照片</label><input type="file" id="f-file" accept="image/*" capture="environment"></div>
      </div>
      <div style="margin-top:12px;"><button class="pc-btn" id="pc-add">登記這一筆</button>
        <span id="pc-msg" style="margin-left:10px;font-size:13px;color:var(--sub);"></span></div>
    </div>

    <div class="pc-head pc-row"><span>日期</span><span>摘要</span><span>項目</span>
      <span class="amt">金額</span><span></span></div>
    <div id="pc-list"></div>

    ${data.claims.length ? `<h3 style="font-size:13px;letter-spacing:.15em;color:var(--sub);
        margin:28px 0 8px;text-transform:uppercase;">歷史批次</h3>
      <div id="pc-claims"></div>` : ""}`;

    const list = host.querySelector("#pc-list");
    if (!data.pending.length) {
        list.innerHTML = '<div class="pc-empty">目前沒有未請款的單據。</div>';
    } else {
        list.innerHTML = data.pending.map(e => `
          <div class="pc-row">
            <span class="sub">${esc(e.expense_date || "待補")}</span>
            <span>${esc(e.summary || "（無摘要）")}
              ${e.project_name ? `<span class="sub"> · ${esc(e.project_name)}</span>` : ""}
              ${e.receipt_url ? '<span class="pc-pill">收據</span>' : ""}</span>
            <span class="sub">${esc(e.item || "")}</span>
            <span class="amt">${money(e.actual)}</span>
            <span class="pc-actions">
              <button class="pc-btn ghost" data-del="${esc(e.id)}">刪除</button></span>
          </div>`).join("");
        list.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
            if (!confirm("確定刪除這筆？")) return;
            await send("DELETE", "/api/v1/crm/petty/expenses/" + b.dataset.del);
            renderMine(host);
        });
    }

    const claimHost = host.querySelector("#pc-claims");
    if (claimHost) {
        claimHost.innerHTML = data.claims.map(c => `
          <div class="pc-row">
            <span class="sub">${esc(c.submitted_at || "")}</span>
            <span>${esc(c.period_start || "")} ~ ${esc(c.period_end || "")}
              <span class="sub"> · ${esc(c.notes || "")}</span></span>
            <span><span class="pc-pill">${esc(c.status)}</span></span>
            <span class="amt">${money(c.total_claim)}</span>
            <span class="sub">${c.paid_at ? "匯款 " + esc(c.paid_at) : ""}</span>
          </div>`).join("");
    }

    const msg = host.querySelector("#pc-msg");
    host.querySelector("#pc-add").onclick = async (ev) => {
        const btn = ev.currentTarget;
        const amt = parseInt(host.querySelector("#f-amt").value, 10);
        if (!amt) { msg.textContent = "金額不可為 0"; return; }
        btn.disabled = true; msg.textContent = "送出中…";
        try {
            const r = await send("POST", "/api/v1/crm/petty/expenses", {
                expense_date: host.querySelector("#f-date").value,
                actual: amt,
                summary: host.querySelector("#f-sum").value.trim(),
                project_id: host.querySelector("#f-proj").value || null,
                item: host.querySelector("#f-item").value,
                invoice_no: host.querySelector("#f-inv").value.trim(),
            });
            const file = host.querySelector("#f-file").files[0];
            if (file) {
                const fd = new FormData(); fd.append("file", file);
                const up = await F().ufetch(
                    `/api/v1/crm/petty/expenses/${r.id}/receipt`, fd);
                // 🔴 收據上傳失敗要說出來 —— 單據已經建了，靜默吞掉會變成
                // 「我明明拍了收據」但財務那邊永遠看不到。
                if (!up.ok) { alert("單據已登記，但收據上傳失敗，請在清單裡補傳。"); }
            }
            renderMine(host);
        } catch (e) {
            msg.textContent = String(e.message || e); btn.disabled = false;
        }
    };

    const sub = host.querySelector("#pc-submit");
    if (sub) sub.onclick = async () => {
        // 送出即成立 —— 講清楚，不要讓人以為還有一關會擋（那會讓人隨便送）
        if (!confirm(`送出 ${data.pending.length} 筆、合計 ${money(data.pending_total)}？`
                     + "\n\n送出後直接成立並列入匯款清冊，你就不能自己修改了。"
                     + "\n財務端看過覺得有問題會退回給你。")) return;
        sub.disabled = true;
        try { await send("POST", "/api/v1/crm/petty/submit", { notes: "" }); renderMine(host); }
        catch (e) { alert(String(e.message || e)); sub.disabled = false; }
    };
}

// ── 分頁 2：審核（P1 唯讀；核准／退回是 P2）────────────────────────────
export async function renderClaims(host) {
    // 預設直接成立（owner 2026-08-17）→ 這一頁的主體是**已核准待匯**，不是待審。
    // 「待審」仍然撈：歷史匯入的批次是待審，而且將來若改回人工審核也不用改這裡。
    const [approved, pending] = await Promise.all([
        get("/api/v1/crm/petty/claims?status=" + encodeURIComponent("已核准")),
        get("/api/v1/crm/petty/claims?status=" + encodeURIComponent("待審"))]);
    const data = { claims: [...pending.claims, ...approved.claims] };
    if (!data.claims.length) {
        host.innerHTML = CSS + '<div class="pc-empty">沒有待處理的請款批次。</div>';
        return;
    }
    host.innerHTML = CSS
        + '<div class="pc-empty" style="text-align:left;padding:0 0 14px;">'
        + '送出即成立，不需核准。這裡是<strong>看過、覺得不對就退回</strong>的地方'
        + ' —— 退回會一併撤掉已產生的應付款，單據回到本人草稿。已匯款的退不了。</div>'
        + data.claims.map(c => `
      <div style="border:1px solid var(--line);padding:16px;margin-bottom:14px;">
        <div style="display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;">
          <strong style="font-size:16px;">${esc(c.staff_name)}</strong>
          <span class="sub" style="color:var(--sub);font-size:12px;">
            ${esc(c.period_start)} ~ ${esc(c.period_end)} · ${c.lines.length} 筆</span>
          <span class="pc-pill">${esc(c.status)}</span>
          <span style="margin-left:auto;font-size:20px;font-weight:600;">${money(c.total_claim)}</span>
        </div>
        <div style="margin-top:12px;">
          ${c.lines.map(l => `<div class="pc-row">
            <span class="sub">${esc(l.expense_date || "待補")}</span>
            <span>${esc(l.summary || "（無摘要）")}
              ${l.project_name ? `<span class="sub"> · ${esc(l.project_name)}</span>`
                : (l.project_label ? `<span class="pc-pill">${esc(l.project_label)}（未歸戶）</span>` : "")}</span>
            <span class="sub">${esc(l.item || "")}</span>
            <span class="amt">${money(l.actual)}</span>
            <span class="pc-actions"><span class="sub" style="margin-right:8px;">${
              l.receipt_url ? "有收據" : (l.invoice_no ? esc(l.invoice_no) : "")}</span>
              <button class="pc-btn ghost" data-line="${esc(l.id)}"
                      data-claim="${esc(c.id)}">退回</button></span>
          </div>`).join("")}
        </div>
        <div class="pc-actions" style="margin-top:12px;">
          <button class="pc-btn danger" data-rej="${esc(c.id)}">整批退回</button>
          ${c.status === "待審"
            ? `<button class="pc-btn" data-app="${esc(c.id)}">核准並產應付款</button>` : ""}
        </div>
      </div>`).join("");

    host.querySelectorAll("[data-app]").forEach(b => b.onclick = async () => {
        if (!confirm("核准這張批次？會依「會計項目 × 認列月份」產生應付款。")) return;
        b.disabled = true;
        try {
            const r = await send("POST", `/api/v1/crm/petty/claims/${b.dataset.app}/approve`);
            alert(`已核准，產生 ${r.payment_requests.length} 張應付款。到「匯款清冊」登記匯款。`);
            renderClaims(host);
        } catch (e) { alert(String(e.message || e)); b.disabled = false; }
    });
    host.querySelectorAll("[data-rej]").forEach(b => b.onclick = async () => {
        const why = prompt("整批退回的原因（會寫進批次紀錄）：", "");
        if (why === null) return;
        b.disabled = true;
        try {
            await send("POST", `/api/v1/crm/petty/claims/${b.dataset.rej}/reject`
                       + "?reason=" + encodeURIComponent(why));
            renderClaims(host);
        } catch (e) { alert(String(e.message || e)); b.disabled = false; }
    });
    // 逐行退回：一張 30 筆的批次不該因為一張收據沒拍好被整個打回
    host.querySelectorAll("[data-line]").forEach(b => b.onclick = async () => {
        const why = prompt("退回這一筆的原因：", "收據不清楚");
        if (why === null) return;
        b.disabled = true;
        try {
            await send("POST", `/api/v1/crm/petty/claims/${b.dataset.claim}`
                       + `/lines/${b.dataset.line}/reject?reason=` + encodeURIComponent(why));
            renderClaims(host);
        } catch (e) { alert(String(e.message || e)); b.disabled = false; }
    });
}

// ── 分頁 3：匯款清冊 ─────────────────────────────────────────────────
export async function renderAccounts(host) {
    const [data, approved] = await Promise.all([
        get("/api/v1/crm/petty/accounts"),
        get("/api/v1/crm/petty/claims?status=" + encodeURIComponent("已核准"))]);
    const payable = data.accounts.filter(a => (a.claim_total + a.draft_total) > 0);
    const owed = data.accounts.filter(a => (a.claim_total + a.draft_total) < 0);
    const total = payable.reduce((s, a) => s + a.claim_total, 0);

    host.innerHTML = CSS + `
      <div class="pc-sum">
        <div><div class="lbl">本期應匯總額（已送出）</div><div class="big">${money(total)}</div></div>
        <div><div class="lbl">待匯人數</div><div class="big">${payable.filter(a => a.claim_total).length}</div></div>
        <div style="margin-left:auto;"><button class="pc-btn ghost" id="pc-csv">匯出銀行 CSV</button></div>
      </div>
      <div class="pc-head pc-row"><span>收款人</span><span>銀行帳號</span><span>未送出</span>
        <span class="amt">應匯金額</span><span>狀態</span></div>
      ${payable.length ? payable.map(a => `
        <div class="pc-row">
          <span>${esc(a.name)}</span>
          <span class="${a.bank_missing ? "pc-warn" : "sub"}">${
            a.bank_missing ? "⚠ 未填帳號，無法匯款" : esc(a.bank)}</span>
          <span class="sub">${a.draft_total ? money(a.draft_total) + "（草稿）" : ""}</span>
          <span class="amt">${money(a.claim_total)}</span>
          <span><span class="pc-pill">${esc(a.status)}</span></span>
        </div>`).join("") : '<div class="pc-empty">目前沒有要匯的款項。</div>'}
      ${owed.length ? `<h3 style="font-size:13px;letter-spacing:.15em;color:var(--sub);
          margin:28px 0 8px;text-transform:uppercase;">應向本人收回</h3>`
        + owed.map(a => `<div class="pc-row"><span>${esc(a.name)}</span><span></span><span></span>
            <span class="amt pc-warn">${money(a.claim_total + a.draft_total)}</span><span></span></div>`).join("")
        : ""}
      <h3 style="font-size:13px;letter-spacing:.15em;color:var(--sub);
          margin:28px 0 8px;text-transform:uppercase;">已核准，待匯款</h3>
      ${approved.claims.length ? approved.claims.map(c => `
        <div class="pc-row">
          <span>${esc(c.staff_name)}</span>
          <span class="sub">${esc(c.period_start)} ~ ${esc(c.period_end)}</span>
          <span class="sub">${c.lines.length} 筆</span>
          <span class="amt">${money(c.total_claim)}</span>
          <span class="pc-actions">
            <button class="pc-btn" data-pay="${esc(c.id)}">登記已匯款</button></span>
        </div>`).join("") : '<div class="pc-empty">沒有已核准待匯的批次。</div>'}`;

    host.querySelector("#pc-csv").onclick = async () => {
        // 這支回 CSV 不是 JSON，而且要帶 Authorization —— 直接 <a href> 沒有標頭
        const r = await F().mfetch("/api/v1/crm/petty/payout.csv");
        if (!r.ok) { alert("匯出失敗（" + r.status + "）"); return; }
        const url = URL.createObjectURL(await r.blob());
        const a = document.createElement("a");
        a.href = url; a.download = "payout.csv"; a.click();
        URL.revokeObjectURL(url);
    };
    host.querySelectorAll("[data-pay]").forEach(b => b.onclick = async () => {
        const d = prompt("匯款日期（YYYY-MM-DD）：", new Date().toISOString().slice(0, 10));
        if (!d) return;
        b.disabled = true;
        try {
            const r = await send("POST", `/api/v1/crm/petty/claims/${b.dataset.pay}/pay`
                                 + "?payment_date=" + encodeURIComponent(d));
            alert(`已登記匯款：結清 ${r.payment_requests} 張應付款、寫入 ${r.cash_entries} 列收支明細。`);
            renderAccounts(host);
        } catch (e) { alert(String(e.message || e)); b.disabled = false; }
    });
}

// ── 分頁 4：未歸戶標籤（一次綁一個標籤，帶走底下所有列）────────────────
export async function renderLabels(host) {
    const [data, opts] = await Promise.all([
        get("/api/v1/crm/petty/unbound-labels"), get("/api/v1/crm/petty/options")]);
    if (!data.labels.length) {
        host.innerHTML = CSS + '<div class="pc-empty">沒有未歸戶的專案標籤。</div>';
        return;
    }
    const sel = '<option value="">選擇專案…</option>'
        + opts.projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    host.innerHTML = CSS + `
      <div class="pc-empty" style="text-align:left;padding:0 0 14px;">
        匯入的歷史單據裡，這些專案標籤在 CRM 找不到對應專案。綁一次就會帶走底下所有列
        —— 綁完那筆錢才會進專案毛利。</div>
      <div class="pc-head pc-row"><span>列數</span><span>標籤</span><span></span>
        <span class="amt">金額</span><span>綁定到</span></div>
      ${data.labels.map(l => `
        <div class="pc-row">
          <span class="sub">${l.count} 列</span>
          <span>${esc(l.label)}</span><span></span>
          <span class="amt">${money(l.total)}</span>
          <span class="pc-actions">
            <select data-label="${esc(l.label)}" style="max-width:100%;padding:6px;">${sel}</select>
          </span>
        </div>`).join("")}`;
    host.querySelectorAll("select[data-label]").forEach(s => s.onchange = async () => {
        if (!s.value) return;
        const label = s.dataset.label;
        if (!confirm(`把「${label}」底下所有列綁到這個專案？`)) { s.value = ""; return; }
        await send("POST", "/api/v1/crm/petty/bind-label?label="
                   + encodeURIComponent(label) + "&project_id=" + encodeURIComponent(s.value));
        renderLabels(host);
    });
}
