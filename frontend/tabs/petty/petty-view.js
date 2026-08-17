// 零用金四個分頁的畫面（docs/PETTY_CASH_PLAN.md §3）。
//
// import 閉包只准 `tabs/petty/` 與 `js/shared/` —— 同 /project.html 的規則
// （tests/unit/test_public_surface.py 釘住）。頁面的 fetch 包裝由殼層放在
// `window.__petty` 上：同源同頁、只有這一個消費者，另建一層抽象只是多一個檔案。
const F = () => window.__petty;

// 🔴 純字串替換，不建 DOM：帳冊一次渲染會叫這支上萬次，
// 舊寫法（createElement + textContent）光這裡就吃掉兩秒多。
const _ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => _ESC[c]);
const money = (n) => (n === null || n === undefined) ? "—"
    : (n < 0 ? "-NT$ " : "NT$ ") + Math.abs(n).toLocaleString();
const today = () => new Date().toISOString().slice(0, 10);

// 「只有專案雜支可連結專案」—— 規則正本在後端（/petty/options 的
// project_link_items），這裡只有斷線時的 fallback；所有用點共用這一份。
const linkableSet = (opts) => new Set(opts.project_link_items || ["專案雜支"]);
// 專案欄跟著項目開關（登記表單與帳冊新增列共用；規則或文案變了只改這裡）
function wireProjectGate(itemEl, projEl, linkable) {
    if (!itemEl || !projEl) return;
    const sync = () => {
        projEl.disabled = !linkable.has(itemEl.value);
        if (projEl.disabled) projEl.value = "";
        projEl.title = projEl.disabled ? "只有「專案雜支」開放連結專案" : "";
    };
    itemEl.addEventListener("change", sync);
    sync();
}

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
// 代為登記：目前正在管理誰的零用金（null＝自己）。審核者才切得動。
// 放模組層而不是參數：切換之後每一次重繪（新增/刪除/送出後）都要留在同一個人身上。
let _asStaff = null;
const _base = () => _asStaff
    ? `/api/v1/crm/petty/staff/${encodeURIComponent(_asStaff)}`
    : "/api/v1/crm/petty";

export async function renderMine(host) {
    let data, opts, staffList = null;
    // 審核者才拿得到人員清單（沒權限就 403，那不是錯誤，是「你只能記自己的」）
    try { staffList = (await get("/api/v1/crm/petty/staff-options")).staff; }
    catch (_) { staffList = null; }

    try {
        [data, opts] = await Promise.all([
            get(_asStaff ? _base() : "/api/v1/crm/petty/me"),
            get("/api/v1/crm/petty/options")]);
    } catch (e) {
        // 帳號沒綁人員檔案不是故障，是「還差一步設定」。但如果這個人是審核者，
        // 他其實有路可走 —— 直接把「代為登記」的選單給他，而不是留一句死路。
        if (e.status === 409 && staffList && staffList.length) {
            host.innerHTML = CSS + `
              <div class="pc-empty" style="text-align:left;">
                這個帳號沒有綁定人員檔案（例如共用的 admin），所以沒有「自己的」請款。<br>
                你有審核權限，可以直接<strong>代為登記</strong>某個人的零用金：
                <div style="margin-top:12px;">
                  <select id="pc-as" style="padding:9px;min-width:220px;">
                    <option value="">選擇要管理誰的零用金…</option>
                    ${staffList.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}
                  </select>
                </div>
              </div>`;
            host.querySelector("#pc-as").onchange = (ev) => {
                if (!ev.target.value) return;
                _asStaff = ev.target.value;
                renderMine(host);
            };
            return;
        }
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

    const asSel = staffList && staffList.length ? `
      <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;">
        <span class="lbl" style="font-size:11px;color:var(--sub);">管理誰的零用金</span>
        <select id="pc-as" style="padding:7px 10px;max-width:260px;">
          <option value="">我自己</option>
          ${staffList.map(s => `<option value="${esc(s.id)}"${
            s.id === _asStaff ? " selected" : ""}>${esc(s.name)}</option>`).join("")}
        </select>
        ${_asStaff ? '<span class="pc-pill">代為登記中</span>' : ""}
      </div>` : "";

    host.innerHTML = CSS + asSel + `
    <div class="pc-sum">
      <div><div class="lbl">本期應請款${_asStaff ? "（" + esc(data.staff.name) + "）" : ""}</div>
        <div class="big">${money(data.pending_total)}</div></div>
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

    const asEl = host.querySelector("#pc-as");
    if (asEl) asEl.onchange = (ev) => { _asStaff = ev.target.value || null; renderMine(host); };

    wireProjectGate(host.querySelector("#f-item"),
                    host.querySelector("#f-proj"), linkableSet(opts));

    const msg = host.querySelector("#pc-msg");
    host.querySelector("#pc-add").onclick = async (ev) => {
        const btn = ev.currentTarget;
        const amt = parseInt(host.querySelector("#f-amt").value, 10);
        if (!amt) { msg.textContent = "金額不可為 0"; return; }
        btn.disabled = true; msg.textContent = "送出中…";
        try {
            const r = await send("POST", _base() + "/expenses", {
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
        try { await send("POST", _base() + "/submit", { notes: "" }); renderMine(host); }
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
// owner 2026-08-17：「需要列出所有員工狀態，沒有請款就標註 --，欠款就標註負值」。
// 原本只列「還要付的人」—— 那讓「這個人這期沒有請款」與「這個人不在清單裡」
// 長得一模一樣，看的人分不出是沒花錢還是漏了。改成全員一列，用符號區分三態。
export async function renderAccounts(host) {
    const [data, approved] = await Promise.all([
        get("/api/v1/crm/petty/accounts"),
        get("/api/v1/crm/petty/claims?status=" + encodeURIComponent("已核准"))]);

    const rows = data.accounts;
    // 應匯＝已送出待付 + 未送出草稿（草稿還不能匯，但要讓人看到有東西在路上）
    // 淨額＝他墊的（待付＋草稿）− 歸屬他但別人墊的（還沒還）
    const due = (a) => a.claim_total + a.draft_total - (a.owed_by_staff || 0);
    const payable = rows.filter(a => due(a) > 0);
    const total = payable.reduce((s, a) => s + a.claim_total, 0);
    const owed = rows.filter(a => due(a) < 0);

    host.innerHTML = CSS + `
      <div class="pc-sum">
        <div><div class="lbl">本期應匯總額（已送出）</div><div class="big">${money(total)}</div></div>
        <div><div class="lbl">待匯人數</div><div class="big">${
          payable.filter(a => a.claim_total).length}<span class="sub"
          style="font-size:13px;color:var(--sub);"> ／ 全員 ${rows.length}</span></div></div>
        ${owed.length ? `<div><div class="lbl">應收回</div>
          <div class="big pc-warn">${money(owed.reduce((s, a) => s + due(a), 0))}</div></div>` : ""}
        <div style="margin-left:auto;"><button class="pc-btn ghost" id="pc-csv">匯出銀行 CSV</button></div>
      </div>

      <div class="pc-head pc-row"><span>收款人</span><span>銀行帳號</span><span>應收回</span>
        <span class="amt">應匯淨額</span><span>狀態</span></div>
      ${rows.map(a => {
        const d = due(a);
        // 三態：正＝要匯給他／零＝這期沒請款（畫 —，不是 NT$ 0）／負＝他欠公司
        const amt = d > 0 ? money(d)
                  : d < 0 ? `<span class="pc-warn">${money(d)}</span>`
                  : '<span class="sub">—</span>';
        const state = d > 0 ? (a.claim_total ? "待匯款" : "未送出")
                    : d < 0 ? "應收回" : (a.rows ? "無請款" : "無紀錄");
        return `
        <div class="pc-row">
          <span>${esc(a.name)}${a.bound_user ? "" :
            '<span class="pc-pill">未綁帳號</span>'}</span>
          <span class="${a.bank_missing && d > 0 ? "pc-warn" : "sub"}">${
            a.bank_missing ? (d > 0 ? "⚠ 未填帳號，無法匯款" : "—") : esc(a.bank)}</span>
          <span class="${a.owed_by_staff ? "pc-warn" : "sub"}">${
            a.owed_by_staff ? money(a.owed_by_staff)
              : (a.draft_total ? money(a.draft_total) + "（草稿）" : "—")}</span>
          <span class="amt">${amt}</span>
          <span><span class="pc-pill">${state}</span></span>
        </div>`; }).join("")}

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
        // 這支回 CSV 不是 JSON，而且要帶 Authorization —— <a href> 送不了標頭
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

// ── 分頁：全部零用金 —— **逐筆帳冊**（欄位對齊 owner 的 Google Sheet）─────
//
// owner 2026-08-17 看過每人彙總版後：「我希望全部的零用金是像這樣的管理頁面」
// —— 附圖是那張 443 列的表。彙總表回答不了「那筆 4,309 的影印是誰、哪個案子」，
// 所以主體換成逐筆，欄位順序照原表：日期／請款／摘要／附註／項目／收款人／專案標籤。
// 下拉就地改（項目／收款人／專案），change 即存。
const LEDGER_CSS = `
<style>
.lg-bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
.lg-bar input, .lg-bar select { padding:7px 9px; border:1px solid var(--line);
  border-radius:2px; font-size:13px; font-family:inherit; background:transparent; color:inherit; }
.lg-sum { font-size:12px; color:var(--sub); margin-left:auto; }
.lg-wrap { overflow-x:auto; border:1px solid var(--line); }
table.lg { border-collapse:collapse; width:100%; font-size:13px; min-width:980px; }
table.lg th { text-align:left; font-weight:600; font-size:11px; letter-spacing:.08em;
  color:var(--sub); padding:8px 10px; border-bottom:1px solid var(--line);
  position:sticky; top:0; background:var(--bg-soft); white-space:nowrap; }
table.lg td { padding:4px 8px; border-bottom:1px solid var(--line); vertical-align:middle; }
table.lg tr:hover td { background:rgba(127,127,127,.07); }
table.lg td.amt { text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }
table.lg td.dt { white-space:nowrap; color:var(--sub); }
table.lg select, table.lg input { width:100%; background:transparent; color:inherit;
  border:1px solid transparent; border-radius:2px; padding:4px 5px; font-size:13px;
  font-family:inherit; }
table.lg select:hover, table.lg input:hover { border-color:var(--line); }
table.lg select:focus, table.lg input:focus { border-color:var(--sub); outline:none; }
table.lg tr.locked select, table.lg tr.locked input { pointer-events:none; opacity:.5; }
.lg-lock { font-size:11px; color:var(--sub); white-space:nowrap; }
.lg-more { text-align:center; padding:14px; }
.lg-modal { position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:900;
  display:flex; align-items:center; justify-content:center; padding:20px; }
.lg-modal-in { background:var(--bg-soft); border:1px solid var(--line); padding:20px;
  max-width:520px; width:100%; max-height:82vh; overflow:auto; }
.lg-modal-in select { background:#1e1e1e; color:inherit; border:1px solid var(--line);
  border-radius:2px; font-family:inherit; }
</style>`;

const _LG = { q: "", staff_id: "", item: "", month: "", unbound: 0, limit: 300 };

export async function renderOverview(host) {
    const qs = new URLSearchParams(
        Object.entries(_LG).filter(([, v]) => v !== "" && v !== 0)).toString();
    const [d, opts, people] = await Promise.all([
        get("/api/v1/crm/petty/entries?" + qs),
        get("/api/v1/crm/petty/options"),
        get("/api/v1/crm/petty/staff-options"),
    ]);

    // 選項字串**只組一次**（300 列各組一次＝300 倍的無謂工作）；
    // 選中哪一個等 innerHTML 掛好之後用 `.value =` 設，一列一次賦值。
    const ITEM_OPTS = '<option value=""></option>'
        + opts.items.map(i => `<option>${esc(i)}</option>`).join("");
    const STAFF_OPTS = '<option value=""></option>'
        + people.staff.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    // 🔴 專案下拉**不預先展開**：238 個專案 × 300 列 ＝ 七萬個 <option>，
    // 那就是「載入有點慢」的來源（實測 DOM 節點數差一個數量級）。這裡只放
    // 目前值那一個，其餘等使用者真的點下去（focus）才補 —— 一次只長一個下拉。
    // 🔴 專案有 238 個 —— 純 <select> 沒辦法用（owner 2026-08-17：「這個清單太多
    // 要可以搜尋」）。用**共用一份 <datalist> + 可打字的 input**：
    //   - 238 個 option 全表只長一次，不是每列一份（那是先前載入慢的原因）
    //   - 型別提示與過濾是瀏覽器原生的，不必引 searchableSelect
    //     （那支住在 tabs/crm/，而這個元件要同時跑在獨立頁與 SPA 子視圖）
    // 顯示字串必須唯一才能反查 id —— 同名專案補一個短碼。
    const projName = Object.fromEntries(opts.projects.map(p => [p.id, p.name]));
    const seen = new Map();
    const labelOf = {}, idOfLabel = {};
    for (const p of opts.projects) {
        const n = (seen.get(p.name) || 0) + 1;
        seen.set(p.name, n);
        const label = n === 1 ? p.name : `${p.name}｜${p.id.slice(0, 6)}`;
        labelOf[p.id] = label;
        idOfLabel[label] = p.id;
    }
    const PROJ_DATALIST = '<datalist id="pc-proj-dl">'
        + Object.keys(idOfLabel).map(l => `<option value="${esc(l)}"></option>`).join("")
        + "</datalist>";
    // 新增列仍用 select（只有一個，238 個選項無所謂，而且可直接挑）
    const PROJ_OPTS = '<option value="">（無專案）</option>'
        + opts.projects.map(p => `<option value="${esc(p.id)}">${esc(labelOf[p.id])}</option>`).join("");
    // 只有「專案雜支」開放連結專案（owner 2026-08-17）。規則來自後端的
    // `project_link_items`，不在前端寫死 —— 兩邊各寫一份就會漂。
    const LINKABLE = linkableSet(opts);
    const projCell = (e) => {
        const cur = e.project_id;
        const val = cur ? (labelOf[cur] || projName[cur] || "（已刪除的專案）") : "";
        if (!LINKABLE.has(e.item)) {
            // 鎖住而不是隱藏：既有的標籤文字還看得到（那是資料），
            // 只是這個項目不該連專案
            return `<input data-f="project_id" value="${esc(val)}" disabled
                           style="opacity:.45;"
                           placeholder="${e.project_label ? esc(e.project_label) : "—"}"
                           title="「${esc(e.item || "")}」不開放連結專案（只有專案雜支可以）">`;
        }
        return `<input data-f="project_id" list="pc-proj-dl"
                       value="${esc(val)}" data-was="${esc(val)}"
                       placeholder="${e.project_label ? esc(e.project_label) + "（未歸戶）" : "（無專案）"}"
                       title="${e.project_label ? "原始標籤：" + esc(e.project_label)
                                                : "打字搜尋專案；清空＝不歸專案"}">`;
    };

    host.innerHTML = CSS + LEDGER_CSS + PROJ_DATALIST + `
      <div class="lg-bar">
        <input id="lg-q" placeholder="搜尋摘要／附註／發票號／標籤（Enter）"
               value="${esc(_LG.q)}" style="min-width:240px;">
        <select id="lg-staff" data-no-search><option value="">全部收款人</option>
          ${people.staff.map(p => `<option value="${esc(p.id)}"${
            p.id === _LG.staff_id ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
        <select id="lg-item" data-no-search><option value="">全部項目</option>
          ${opts.items.map(i => `<option${i === _LG.item ? " selected" : ""}>${esc(i)}</option>`).join("")}</select>
        <input id="lg-month" type="month" value="${esc(_LG.month)}">
        <label style="font-size:12px;color:var(--sub);display:flex;gap:4px;align-items:center;">
          <input type="checkbox" id="lg-unbound" ${_LG.unbound ? "checked" : ""} style="width:auto;">
          只看未歸戶專案</label>
        <button class="pc-btn ghost" id="lg-owners"
                style="padding:6px 12px;font-size:12px;">費用歸屬設定</button>
        <span class="lg-sum">${d.total.toLocaleString()} 筆 ／ 合計 ${money(d.amount)}${
          d.returned < d.total ? `（顯示前 ${d.returned}）` : ""}</span>
      </div>

      <div class="lg-wrap"><table class="lg">
        <thead><tr>
          <th style="width:112px;">日期</th><th style="width:92px;text-align:right;">請款</th>
          <th style="min-width:210px;">摘要</th><th style="width:135px;">附註</th>
          <th style="width:130px;">項目</th><th style="width:130px;">收款人</th>
          <th style="width:200px;">專案標籤</th><th style="width:64px;"></th>
        </tr></thead>
        <tbody>
          <tr class="lg-new">
            <td class="dt"><input type="date" id="n-date" title="留白＝日期待補"></td>
            <td class="amt"><input type="number" id="n-amt" placeholder="金額" style="text-align:right;"></td>
            <td><input id="n-sum" placeholder="＋ 新增一筆：摘要"></td>
            <td><input id="n-note" placeholder="發票號／附註"></td>
            <td><select data-no-search id="n-item">${ITEM_OPTS}</select></td>
            <td><select data-no-search id="n-staff">${STAFF_OPTS}</select></td>
            <td><select data-no-search id="n-proj" disabled>${PROJ_OPTS}</select></td>
            <td><button class="pc-btn" id="n-add"
                        style="padding:5px 10px;font-size:12px;">新增</button></td>
          </tr>
          ${d.entries.map(e => `
          <tr data-id="${esc(e.id)}" class="${e.locked ? "locked" : ""}">
            <td class="dt"><input type="date" data-f="expense_date" value="${esc(e.expense_date)}"></td>
            <td class="amt"><input type="number" data-f="actual" value="${e.actual}"
                                   style="text-align:right;"></td>
            <td><input data-f="summary" value="${esc(e.summary)}"></td>
            <td><input data-f="${e.invoice_no ? "invoice_no" : "note"}"
                       value="${esc(e.invoice_no || e.note)}"></td>
            <td><select data-no-search data-f="item" data-v="${esc(e.item)}">${ITEM_OPTS}</select></td>
            <td>${e.staff_id || !e.staff_name
              // 非員工的代墊（生產有 14 列「外部製片_現金提款」）沒有 staff_id ——
              // 不補這一個選項的話，下拉空白、名字也不見了，看起來像資料掉了
              ? `<select data-no-search data-f="staff_id" data-v="${esc(e.staff_id)}">${STAFF_OPTS}</select>`
              : `<select data-no-search data-f="staff_id" data-v="">
                   <option value="" selected>${esc(e.staff_name)}（非員工）</option>
                   ${STAFF_OPTS}</select>`}</td>
            <td>${projCell(e)}</td>
            <td class="lg-lock">${e.locked ? "已入帳" : esc(e.status)}</td>
          </tr>`).join("")}</tbody>
      </table></div>
      ${d.entries.length ? "" : '<div class="pc-empty">沒有符合條件的單據。</div>'}
      ${d.returned < d.total
        ? `<div class="lg-more"><button class="pc-btn ghost" id="lg-more">
             再載 300 筆（已顯示 ${d.returned}／${d.total}）</button></div>`
        : ""}`;

    // 篩選：搜尋框走 Enter（每個字打一次 API 會讓 443 列的表卡住），其餘 change 即查
    host.querySelector("#lg-q").onkeydown = (ev) => {
        if (ev.key === "Enter") { _LG.q = ev.target.value.trim(); _LG.limit = 300; rerun(); }
    };
    host.querySelector("#lg-staff").onchange = (ev) => { _LG.staff_id = ev.target.value; rerun(); };
    host.querySelector("#lg-item").onchange = (ev) => { _LG.item = ev.target.value; rerun(); };
    host.querySelector("#lg-month").onchange = (ev) => { _LG.month = ev.target.value; rerun(); };
    host.querySelector("#lg-unbound").onchange = (ev) => {
        _LG.unbound = ev.target.checked ? 1 : 0; rerun();
    };
    const more = host.querySelector("#lg-more");
    if (more) more.onclick = () => { _LG.limit += 300; rerun(); };

    const rerun = () => renderOverview(host);

    // 新增列：專案欄跟著項目開關（同一條規則，不讓人填了才被後端退回）
    wireProjectGate(host.querySelector("#n-item"),
                    host.querySelector("#n-proj"), LINKABLE);

    // 「項目 → 費用歸屬人」設定：設定一次，之後建立的單據自動帶
    // （owner 2026-08-17：不用在帳冊上為此多開一欄讓人每筆挑）
    host.querySelector("#lg-owners").onclick = async () => {
        const cfg = await get("/api/v1/crm/petty/item-owners");
        const staffOpts = (cur) => '<option value="">（不指定 —— 墊款人自己負擔）</option>'
            + cfg.staff.map(p => `<option value="${esc(p.id)}"${
                p.id === cur ? " selected" : ""}>${esc(p.name)}</option>`).join("");
        const box = document.createElement("div");
        box.className = "lg-modal";
        box.innerHTML = `
          <div class="lg-modal-in">
            <h3 style="margin:0 0 6px;font-size:15px;">費用歸屬設定</h3>
            <div class="pc-empty" style="text-align:left;padding:0 0 12px;">
              哪個<strong>會計項目</strong>的費用該算在誰頭上。設定之後，新登記的
              單據會自動帶上歸屬人；公司照樣把錢匯給墊款人，再從歸屬人那邊收回。
              留空＝墊款人自己負擔（多數項目都是這樣）。</div>
            ${cfg.items.map(i => `
              <div style="display:flex;gap:10px;align-items:center;margin-bottom:6px;">
                <span style="min-width:120px;font-size:13px;">${esc(i)}</span>
                <select data-item="${esc(i)}" style="flex:1;padding:6px;">
                  ${staffOpts(cfg.mapping[i] || "")}</select>
              </div>`).join("")}
            <label style="display:flex;gap:6px;align-items:center;margin:14px 0 4px;font-size:13px;">
              <input type="checkbox" id="lg-apply" checked style="width:auto;">
              同時套用到<strong>還沒指定歸屬</strong>的既有單據</label>
            <div class="pc-empty" style="text-align:left;padding:0 0 12px;font-size:12px;">
              已經指定過的（含手動改的例外、已結清的歷史）不會被覆蓋。</div>
            <div class="pc-actions">
              <button class="pc-btn ghost" id="lg-cancel">取消</button>
              <button class="pc-btn" id="lg-save">儲存</button>
            </div>
          </div>`;
        host.appendChild(box);
        const close = () => box.remove();
        box.querySelector("#lg-cancel").onclick = close;
        box.onclick = (ev) => { if (ev.target === box) close(); };
        box.querySelector("#lg-save").onclick = async (ev) => {
            ev.currentTarget.disabled = true;
            const mapping = {};
            box.querySelectorAll("select[data-item]").forEach(sel => {
                if (sel.value) mapping[sel.dataset.item] = sel.value;
            });
            const apply = box.querySelector("#lg-apply").checked ? 1 : 0;
            try {
                const r = await send("PUT",
                    "/api/v1/crm/petty/item-owners?apply_existing=" + apply, { mapping });
                close();
                alert(`已儲存 ${Object.keys(r.mapping).length} 條對映`
                      + (r.applied ? `，並套用到 ${r.applied} 筆既有單據。` : "。"));
                rerun();
            } catch (e) { alert(String(e.message || e)); ev.currentTarget.disabled = false; }
        };
    };

    // ＋ 新增一筆：收款人是必填（錢要算在誰頭上），其餘可空 —— 尤其日期，
    // Sheet 裡本來就有 37 列沒填，留白比塞今天更誠實
    const nAdd = host.querySelector("#n-add");
    if (nAdd) nAdd.onclick = async () => {
        const staffId = host.querySelector("#n-staff").value;
        const amt = parseInt(host.querySelector("#n-amt").value, 10);
        if (!staffId) { alert("請先選收款人（這筆錢是誰墊的）"); return; }
        if (!amt) { alert("金額不可為 0"); return; }
        nAdd.disabled = true;
        try {
            const r = await send("POST", `/api/v1/crm/petty/staff/${encodeURIComponent(staffId)}/expenses`, {
                expense_date: host.querySelector("#n-date").value,   // 空＝待補
                actual: amt,
                summary: host.querySelector("#n-sum").value.trim(),
                item: host.querySelector("#n-item").value,
                invoice_no: host.querySelector("#n-note").value.trim(),
                project_id: host.querySelector("#n-proj").value || null,
            });
            rerun();
        } catch (e) { alert(String(e.message || e)); nAdd.disabled = false; }
    };

    // 選中值：innerHTML 用同一份選項字串，掛好後才逐列設 value
    host.querySelectorAll("tbody select[data-v]").forEach(sel => {
        sel.value = sel.dataset.v;
    });

    // 專案有多張成本子表 → 問掛哪一張；只有一張（或沒有）就回 "" 讓後端落主表。
    // 回 null ＝ 使用者按了取消。
    const _pickCostGroup = async (projectId) => {
        let groups = [];
        try {
            groups = (await get("/api/v1/crm/petty/project-groups/"
                                + encodeURIComponent(projectId))).groups;
        } catch (_) { return ""; }
        if (groups.length <= 1) return "";
        return new Promise(resolve => {
            const box = document.createElement("div");
            box.className = "lg-modal";
            box.innerHTML = `
              <div class="lg-modal-in" style="max-width:420px;">
                <h3 style="margin:0 0 6px;font-size:15px;">要掛哪一張成本子表？</h3>
                <div class="pc-empty" style="text-align:left;padding:0 0 12px;">
                  這個專案有 ${groups.length} 張子表。選錯只是分組不對，金額仍算進
                  專案成本；不確定就選第一張。</div>
                ${groups.map((g, i) => `
                  <label style="display:flex;gap:8px;align-items:center;padding:8px;
                                border:1px solid var(--line);margin-bottom:6px;cursor:pointer;">
                    <input type="radio" name="lg-g" value="${esc(g.id)}"
                           ${i === 0 ? "checked" : ""} style="width:auto;">
                    <span>${esc(g.name)}${g.shoot_date
                      ? `<span class="sub"> · ${esc(g.shoot_date)}</span>` : ""}</span>
                  </label>`).join("")}
                <div class="pc-actions" style="margin-top:12px;">
                  <button class="pc-btn ghost" data-x>取消</button>
                  <button class="pc-btn" data-ok>確定</button>
                </div>
              </div>`;
            host.appendChild(box);
            const done = (v) => { box.remove(); resolve(v); };
            box.querySelector("[data-x]").onclick = () => done(null);
            box.onclick = (ev) => { if (ev.target === box) done(null); };
            box.querySelector("[data-ok]").onclick = () => done(
                box.querySelector('input[name="lg-g"]:checked').value);
        });
    };

    // 就地修改：change 才送（不是每個鍵），存好把邊框閃綠當回饋
    host.querySelectorAll("tbody [data-f]").forEach(el => {
        el.onchange = async () => {
            const tr = el.closest("tr");
            const f = el.dataset.f;
            let val = el.type === "number" ? Number(el.value) : el.value;
            if (f === "project_id" && el.tagName === "INPUT") {
                const typed = el.value.trim();
                if (typed && !(typed in idOfLabel)) {
                    // 🔴 打錯字不要靜默當成「不歸專案」—— 那會無聲地把歸屬清掉
                    alert("找不到專案「" + typed + "」。請從清單挑一個，或清空表示不歸專案。");
                    el.value = el.dataset.was || "";
                    return;
                }
                val = typed ? idOfLabel[typed] : "";
                el.dataset.was = typed;
                // 專案有不只一張成本子表時，問要掛哪一張（owner 2026-08-17）——
                // 預設落主表雖然不會錯，但「這筆算哪一天的拍攝」只有人知道
                if (val) {
                    const gid = await _pickCostGroup(val);
                    if (gid === null) {            // 使用者取消 → 整個動作放棄
                        el.value = el.dataset.was = tr.querySelector(
                            '[data-f="project_id"]').defaultValue || "";
                        return;
                    }
                    await send("PATCH", "/api/v1/crm/petty/entries/" + tr.dataset.id,
                               { project_id: val, cost_group_id: gid });
                    el.style.borderColor = "var(--ok)";
                    setTimeout(() => { el.style.borderColor = ""; }, 900);
                    rerun();
                    return;
                }
            }
            try {
                const r = await send("PATCH",
                    "/api/v1/crm/petty/entries/" + tr.dataset.id, { [f]: val });
                el.style.borderColor = "var(--ok)";
                setTimeout(() => { el.style.borderColor = ""; }, 900);
                // 綁定專案會讓「未歸戶」那一格的樣子改變 —— 重抓比就地補畫可靠
                // 項目換了會改變「這一列能不能連專案」，而且後端可能順手解除了
                // 既有連結 —— 兩者都要讓畫面跟上，否則使用者看到的是舊狀態
                if (f === "project_id" || f === "item") {
                    if (r && r.unlinked) {
                        alert("項目已改為「" + el.value
                              + "」，不開放連結專案，原本的專案連結已解除。");
                    }
                    rerun();
                }
            } catch (err) {
                el.style.borderColor = "var(--red)";
                alert(String(err.message || err));
            }
        };
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
