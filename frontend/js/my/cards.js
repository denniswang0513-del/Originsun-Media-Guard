// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 第三區卡片（上）：上週回顧 iframe／影像紀錄／零用金／福委會／專案管理／卡片收合／我的待辦／個人資料
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、esc、money、mfetch、WS、grants、renderWorkspace（shell.js）
// 跨檔提供：cardJournal、cardMediaLog、cardPettyCash、cardBenefits、cardProposalPlan、makeCard、FOLDED、cardTodos、cardProfile
// ────────────────────────────────────────────────────────────────────────────
// ── 第二區：上週回顧 —— 直接內嵌完整的 /journal.html（embed 模式；左＝我的上週回顧、右＝大家的回顧）──
// owner 2026-07-24：登入後就要看到完整日誌，不要小卡再點一次。內嵌＝零重複實作，回顧只有 journal.html 一份正本。
// 高度由子頁 postMessage('journal-embed-height') 回報（同 showcase-edit 慣例）。
const JR_MIN_H = 320;                       // iframe 初始高＝內容回報前的佔位
function cardJournal() {
    const card = makeCard("Weekly Review", "上週回顧", "", "journal");
    const body = card.querySelector(".card-body");
    body.classList.add("flush");            // 內嵌頁自帶留白，卡片不再補 padding
    // 收合狀態下不載入（省一次完整頁面 + 2 支 API）；首次展開才給 src
    const folded = card.classList.contains("folded");
    body.innerHTML = `<iframe id="jr-frame" ${folded ? "data-src" : "src"}="/journal.html?embed=1"
        title="工作日誌" loading="lazy" scrolling="no"
        style="width:100%;height:${JR_MIN_H}px;border:0;display:block;overflow:hidden;"></iframe>`;
    card.addEventListener("card-unfold", () => {
        const f = card.querySelector("#jr-frame");
        if (f && !f.src && f.dataset.src) { f.src = f.dataset.src; delete f.dataset.src; }
    });
    return card;
}
window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || d.type !== "journal-embed-height") return;
    const f = $("jr-frame");
    if (f && d.height > 0) f.style.height = Math.max(JR_MIN_H, d.height) + "px";
});

// ── 卡：影像紀錄 —— 直接連到獨立網址 /media-log-workspace.html（帳號登入、完整 Tab）──
// owner 2026-07-26：不要 my.html 內嵌，卡片直接就是獨立網址入口。管理端需 master 完整
// CRM 後端（見 _check_media_log_auth：admin 或 media_log 模組），走 master/foundry。
function cardMediaLog() {
    const card = makeCard("Media Log", "影像紀錄", "", "media_log");
    card.querySelector(".card-body").innerHTML = `
        <div class="meta" style="margin-bottom:12px;">劇組劇照 / 花絮的收集與管理：瀏覽資料夾、連結專案、上傳、分享連結 QR、縮圖與設定。</div>
        <a class="mini-btn" href="/media-log-workspace.html"
           style="display:inline-block;text-decoration:none;line-height:1.5;">開啟影像紀錄 ↗</a>`;
    return card;
}

// ── 卡：零用金 —— 入口連到獨立網址 /petty-cash.html（帳號登入）──
// 閘門是獨立的 me_petty（owner 2026-08-19「請款要單獨控制」，從 me_finance
// 拆出）。財務端的審核／匯款清冊在那一頁裡另外看 money_view +
// finance_approve，這張卡不管那些。
function cardPettyCash() {
    const card = makeCard("Petty Cash", "零用金", "", "petty_cash");
    card.querySelector(".card-body").innerHTML = `
        <div class="meta" style="margin-bottom:12px;">自己墊的錢：現場登記金額與收據、掛專案與會計項目、送出請款，並查看公司要匯給你多少。</div>
        <a class="mini-btn" href="/petty-cash.html"
           style="display:inline-block;text-decoration:none;line-height:1.5;">開啟零用金 ↗</a>`;
    return card;
}

/** 載入失敗時要說什麼。後端有 detail 就用它，沒有才落到這裡。
 *  與 js/shared/utils.js 的 tabLoadError 同一套說法（這一頁不是 module，
 *  沒辦法 import —— 內容有變的話兩邊要一起改）。 */
function failText(status) {
    if (status === 0) return "連不到伺服器 —— 請確認自己在公司區網，或稍後再試。";
    if (status === 401) return "登入已經過期，請重新整理頁面再登入一次。";
    if (status === 403) return "沒有權限，請找管理員開通。";
    if (status === 503) return "資料庫暫時不可用（不是權限問題）—— 通常是連線數滿了，稍等一下重新整理就會恢復。";
    if (status >= 500) return `伺服器錯誤（${status}）—— 這不是你的權限問題，請告訴管理員。`;
    return `載入失敗（${status}）`;
}

// ── 卡：福委會 —— 員工自己登記（owner 2026-08-21「這兩塊員工都可以登記，
// 他們登記後我審核通過，就進公司請款」）──
// 直接做在卡片裡而不是另開一頁：只有三個欄位（池／項目／金額），
// 為它開一頁反而讓人多點一次。版式沿用請假卡（.pf-edit + .inline-row + .row/.pill）
// —— 這一頁沒有 .mini-input/.mini-table 那種 class，自己發明等於畫面空白。
// 餘額由後端算（前端自己算會跟後端講不同的話）。
function cardBenefits(bound) {
    const card = makeCard("Welfare", "福委會", "", "benefits");
    const body = card.querySelector(".card-body");
    // 🔴 沒綁人員檔案就不要發請求 —— 後端會回 409，而畫面上只會出現
    //    「載入失敗（409）」。admin 這種系統帳號本來就沒有綁人，
    //    那不是錯誤，是「這張卡對你沒有內容」。
    if (bound === false) {
        body.innerHTML = `<div class="meta">這個帳號還沒有綁定人員檔案，
            所以沒有屬於你的福委會紀錄。請管理員到「使用者管理」把帳號
            綁到人員之後就會出現。</div>`;
        return card;
    }
    body.innerHTML = `<div class="meta">載入中…</div>`;

    const render = (d) => {
        const pools = d.pools || [];
        if (!pools.length) {
            body.innerHTML = `<div class="meta">還沒有開放的福利池。</div>`;
            return;
        }
        const today = new Date().toLocaleDateString("sv-SE");   // 本地日期，不是 UTC
        const opts = pools.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
        // 兩種數字要分清楚：池餘額是**全公司共用**的那一份，我已用是我自己的。
        // 只顯示池餘額的話，員工會以為那是自己的額度。
        // 年度活動（每人一份）就直接顯示我的額度 —— 那本來就是我自己的。
        const stats = pools.map(p => {
            if ((p.quota || "shared") === "per_person") {
                const a = p.mine_allowance;
                if (!a) {
                    return `<div class="stat">
                        <div class="num">—</div>
                        <div class="lbl">${esc(p.name)}（沒有發給你）</div></div>`;
                }
                // 顯示 available（額度 − 已用 − 審核中）而不是 balance ——
                // 那才是「我還能再申請多少」，也才跟後端擋不擋是同一個數字。
                return `<div class="stat">
                    <div class="num">${money(a.available)}</div>
                    <div class="lbl">${esc(p.name)} 我還可以用${
                        a.pending ? `（審核中 ${money(a.pending)}）` : ""}</div></div>
                    <div class="stat">
                    <div class="num">${money(a.quota)}</div>
                    <div class="lbl">我的額度${
                        a.valid_from || a.valid_to
                            ? `　${esc(a.valid_from || "不限")} ~ ${esc(a.valid_to || "不限")}`
                            : ""}</div></div>`;
            }
            return `<div class="stat">
                <div class="num">${money(p.balance)}</div>
                <div class="lbl">${esc(p.name)} 池餘額</div></div>
                <div class="stat">
                <div class="num">${money(p.mine_used || 0)}</div>
                <div class="lbl">我在${esc(p.name)}已用${
                    p.mine_pending ? `（審核中 ${money(p.mine_pending)}）` : ""}</div></div>`;
        }).join("");

        // 說明與附件（owner 2026-08-21：健檢方案「可以就是一個可以打字、
        // 附上文件的說明」）。沒有說明也沒有附件的項目就不畫。
        const about = pools.filter(p => p.description || (p.attachments || []).length)
            .map(p => `
            <div style="border-top:1px solid #f5f5f5;padding-top:10px;margin-top:10px;">
                <div class="title">${esc(p.name)}</div>
                ${p.description
                    ? `<div class="meta" style="white-space:pre-wrap;">${esc(p.description)}</div>`
                    : ""}
                ${(p.attachments || []).map(f => `
                    <div class="meta" style="margin-top:4px;">
                        <a href="/api/v1/crm/receipt-file?path=${encodeURIComponent(f.path)}"
                           target="_blank" rel="noopener">${esc(f.name)}</a></div>`).join("")}
            </div>`).join("");
        // 單據與心得都是**選填**（owner）—— 這裡只標示有沒有，沒有也不擋。
        // 還在待審/退回的可以補傳單據。
        // 🔴 不截斷。原本只畫 8 筆而且沒說被截掉 —— 有人有 22 筆，
        //    「我上個月那筆呢」在畫面上直接消失。多的用捲軸，不是砍掉。
        const all = d.entries || [];
        const mine = all.map(e => {
            const editable = e.status === "待審" || e.status === "退回";
            // 🔴 退回是唯一需要員工動手的狀態，原因一定要看得到，
            //    不然他只知道被退、不知道要改什麼。
            const why = (e.status === "退回" && e.notes)
                ? `<div class="meta" style="color:#b91c1c;">${esc(e.notes)}</div>` : "";
            return `
            <div class="row">
                <div class="grow">
                    <div class="title">${esc(e.title)}　${money(e.amount)}</div>
                    <div class="meta">${esc(e.pool_name)}　${esc(e.spend_date)}
                        ${e.has_receipt ? "・有單據" : ""}${e.has_reflection ? "・有心得" : ""}</div>
                    ${why}
                </div>
                ${editable ? `<button class="mini-btn" data-up="${esc(e.id)}"
                    >${e.has_receipt ? "換單據" : "傳單據"}</button>
                    <button class="mini-btn" data-note="${esc(e.id)}"
                    >${e.has_reflection ? "改心得" : "寫心得"}</button>` : ""}
                <span class="pill${e.status === "待審" ? " hot" : ""}">${esc(e.status)}</span>
            </div>`;
        }).join("");
        const listWrap = all.length
            ? `<div style="max-height:340px;overflow-y:auto;margin-top:12px;">${mine}</div>
               <div class="meta" style="margin-top:6px;">共 ${all.length} 筆。
                   待審＝等審核；已核准＝進公司請款；已付款＝錢已經匯出。
                   單據與心得都是選填。</div>`
            : `<div class="meta" style="margin-top:12px;">你還沒有登記過。</div>`;
        body.innerHTML = `
            <div class="stats">${stats}</div>
            ${about}
            <div class="pf-edit" style="border-top:1px solid #f5f5f5;padding-top:12px;padding-bottom:4px;">
                <div class="inline-row" style="margin-bottom:8px;">
                    <select id="mb-pool">${opts}</select>
                    <input type="number" id="mb-amount" placeholder="金額" min="1">
                </div>
                <div class="field"><input id="mb-title" placeholder="項目（電影／餐廳／課程）"></div>
                <div class="inline-row" style="margin-bottom:8px;">
                    <input type="date" id="mb-date" value="${today}">
                </div>
                <div class="field"><textarea id="mb-reflection" rows="2"
                    placeholder="心得筆記（選填）"></textarea></div>
                <button class="mini-btn" id="mb-add">送出登記</button>
                <span class="err" id="mb-err" style="margin-left:8px;"></span>
            </div>
            ${listWrap}`;
        body.querySelector("#mb-add").onclick = async () => {
            const errEl = body.querySelector("#mb-err");
            errEl.textContent = "";
            const title = (body.querySelector("#mb-title").value || "").trim();
            const amount = parseInt(body.querySelector("#mb-amount").value || "0", 10);
            if (!title || !amount) { errEl.textContent = "請填項目與金額"; return; }
            const r = await mfetch("/api/v1/crm/benefits/me/entries", {
                method: "POST",
                body: JSON.stringify({
                    pool_id: body.querySelector("#mb-pool").value,
                    title, amount,
                    spend_date: body.querySelector("#mb-date").value || "",
                    reflection: body.querySelector("#mb-reflection").value || "",
                }),
            });
            if (!r.ok) {            // 🔴 失敗一定要出聲，不能靜默
                let msg = r.status;
                try { msg = (await r.json()).detail || msg; } catch (_) { /* 非 JSON */ }
                errEl.textContent = "送出失敗：" + msg;
                return;
            }
            load();
        };
        // 補傳單據：一個隱藏 input 重複用，記住是哪一筆
        let target = null;
        const fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.accept = "image/*,.pdf";
        fileInput.style.display = "none";
        body.appendChild(fileInput);
        fileInput.onchange = async () => {
            if (!fileInput.files[0] || !target) return;
            const fd = new FormData();
            fd.append("file", fileInput.files[0]);
            // 🔴 FormData 不能自己設 Content-Type（會蓋掉 multipart boundary），
            // 所以這裡不走 mfetch —— 它固定塞 application/json。
            const tok = localStorage.getItem(TOKEN_KEY);
            const r = await fetch(`/api/v1/crm/benefits/entries/${target}/receipt`, {
                method: "POST", body: fd,
                headers: tok ? { Authorization: "Bearer " + tok } : {},
            });
            fileInput.value = "";
            if (!r.ok) {
                let msg = r.status;
                try { msg = (await r.json()).detail || msg; } catch (_) { /* 非 JSON */ }
                alert("單據上傳失敗：" + msg);
                return;
            }
            load();
        };
        body.querySelectorAll("[data-up]").forEach(btn => {
            btn.onclick = () => { target = btn.dataset.up; fileInput.click(); };
        });
        // 補／改心得。走 me 那支 PUT（own-scope），所以要把原本的欄位一起送回去
        // —— 那支是整筆覆蓋，只送 reflection 會把項目與金額洗成空的。
        body.querySelectorAll("[data-note]").forEach(btn => {
            btn.onclick = async () => {
                const e = all.find(x => x.id === btn.dataset.note);
                if (!e) return;
                const row = btn.closest(".row");
                if (row.querySelector("[data-noteedit]")) return;   // 已經開著
                const box = document.createElement("div");
                box.dataset.noteedit = "1";
                // 掛進 .grow 而不是 .row —— .row 是 flex 但沒有 flex-wrap，
                // 直接塞進去會被擠在同一列（flex-basis:100% 沒有換行效果）。
                box.style.cssText = "margin-top:8px;";
                box.innerHTML = `
                    <textarea rows="3" style="width:100%;"
                              placeholder="心得筆記（選填）"></textarea>
                    <div style="margin-top:6px;">
                        <button class="mini-btn" data-save>儲存</button>
                        <button class="mini-btn" data-cancel>取消</button>
                        <span class="err" data-err style="margin-left:8px;"></span>
                    </div>`;
                const ta = box.querySelector("textarea");
                ta.value = e.reflection || "";
                row.querySelector(".grow").appendChild(box);
                ta.focus();
                box.querySelector("[data-cancel]").onclick = () => box.remove();
                box.querySelector("[data-save]").onclick = async () => {
                    // 🔴 me 那支 PUT 是**整筆覆蓋** —— 只送 reflection 會把
                    //    項目與金額洗成空的（然後被 422 擋下來）。原欄位一起送回去。
                    const r = await mfetch(`/api/v1/crm/benefits/me/entries/${e.id}`, {
                        method: "PUT",
                        body: JSON.stringify({
                            pool_id: e.pool_id, title: e.title, amount: e.amount,
                            spend_date: e.spend_date, reflection: ta.value,
                        }),
                    });
                    if (!r.ok) {
                        let msg = r.status;
                        try { msg = (await r.json()).detail || msg; } catch (_) { /* 非 JSON */ }
                        box.querySelector("[data-err]").textContent = "儲存失敗：" + msg;
                        return;
                    }
                    load();
                };
            };
        });
    };

    const load = async () => {
        try {
            const r = await mfetch("/api/v1/crm/benefits/me");
            if (!r.ok) {
                // 🔴 後端的 detail 已經是一句人話（例如 409＝帳號沒綁人員檔案），
                //    丟掉它只印狀態碼，看的人什麼也不知道。
                //    這一頁不是 ES module，沒辦法 import js/shared/utils.js 的
                //    tabLoadError，所以這裡留一份最小的對照。
                let msg = "";
                try { msg = (await r.json()).detail || ""; } catch (_) { /* 非 JSON */ }
                body.innerHTML = `<div class="meta">${esc(msg || failText(r.status))}</div>`;
                return;
            }
            render(await r.json());
        } catch (_) {
            body.innerHTML = `<div class="meta">${esc(failText(0))}</div>`;
        }
    };
    load();
    return card;
}

// ── 卡：專案管理 —— 入口連到獨立網址 /project.html（帳號登入）──
// 2026-08-15 改名：那一頁的主鍵已經從提案換成專案，落地畫面也換成專案清單，
// 卡片名稱是最後一個還在說「提案企劃」的地方。提案清單仍在，走頁內切換。
// 閘門對齊後端守衛（提案庫/拍攝企劃/專案管理任一模組，owner 2026-08-11「權限全通」），
// 由 /me/workspace 的 allowed 帶 proposal_plan 宣告，前端不自行探測權限。
function cardProposalPlan() {
    const card = makeCard("Project", "專案管理", "", "proposal_plan");
    card.querySelector(".card-body").innerHTML = `
        <div class="meta" style="margin-bottom:12px;">每個案子的工作面：進度五軌、創意發想、參考影片、企劃書、報價單、會議記錄、人員配置、完稿結案。頁內可切到提案清單。</div>
        <a class="mini-btn" href="/project.html"
           style="display:inline-block;text-decoration:none;line-height:1.5;">開啟專案管理 ↗</a>`;
    return card;
}

// 每張卡都可收合：點標題列切換，狀態記在 localStorage（跨登入保留）。
// key 用穩定代號而非顯示字串 —— 改標題文案不該把大家的收合狀態洗掉。
const FOLD_KEY = "my_folded_cards";
const FOLDED = new Set(JSON.parse(localStorage.getItem(FOLD_KEY) || "[]"));
// 卡頭那顆數字的位置也拿來放「開發中」徽章 —— 傳這個字串就會上色（樣式在 my.html）。
// 用常數而不是在兩邊各寫一次中文：改字時不會只改到一邊、徽章默默變回一般數字。
const WIP_LABEL = "開發中";

function makeCard(en, zh, countText, key) {
    const foldKey = key || en;
    const el = document.createElement("div");
    el.className = "card";
    el.dataset.card = foldKey;                  // 供測試/除錯定位用
    el.innerHTML = `<div class="card-head"><span class="eyebrow">${en} <span class="accent">${zh}</span></span>
        <span style="display:flex;align-items:baseline;gap:10px;">
            ${countText ? `<span class="count${countText === WIP_LABEL ? " wip" : ""}">${countText}</span>` : ""}
            <span class="fold">▾</span>
        </span></div><div class="card-body"></div>`;
    if (FOLDED.has(foldKey)) el.classList.add("folded");
    el.querySelector(".card-head").onclick = () => {
        const folded = el.classList.toggle("folded");
        folded ? FOLDED.add(foldKey) : FOLDED.delete(foldKey);
        localStorage.setItem(FOLD_KEY, JSON.stringify([...FOLDED]));
        if (!folded) el.dispatchEvent(new CustomEvent("card-unfold"));   // 供懶載入用
    };
    return el;
}

// ──「我的專案」舊卡（吃派工）已拿掉（2026-09-05）：專案在第一區「今天的專案紀錄」與「專案查詢」。

// ── 卡：我的待辦（公布欄，own-scope）──
function cardTodos(list) {
    const card = makeCard("My Tasks", "我的待辦", list.length ? list.length + " 項" : "", "todos");
    const body = card.querySelector(".card-body");
    if (!list.length) { body.innerHTML = `<div class="empty">沒有待辦事項</div>`; return card; }
    body.innerHTML = list.map(t => `
        <div class="row" data-todo="${esc(t.id)}">
            <div class="grow">
                <div class="title">${t.pinned ? '<span class="pill" style="margin-right:6px;">釘選</span>' : ""}${esc(t.title)}${t.assigned_to_me ? ' <span class="pill hot" style="margin-left:4px;">指派給我</span>' : ""}</div>
                ${t.note ? `<div class="meta">${esc(t.note)}</div>` : ""}
            </div>
            <button class="mini-btn" onclick="cycleTodo('${esc(t.id)}','${t.status === "todo" ? "doing" : "todo"}')">${t.status === "doing" ? "進行中" : "待辦"}</button>
            <button class="mini-btn" onclick="cycleTodo('${esc(t.id)}','done')">完成</button>
        </div>`).join("");
    return card;
}
async function cycleTodo(id, status) {
    try {
        const r = await mfetch("/api/v1/me/todos/" + id, { method: "PUT", body: JSON.stringify({ status }) });
        if (!r.ok || !WS) return;
        // 本地更新 + 重繪即可，不重抓整包 bundle
        if (status === "done") WS.todos = (WS.todos || []).filter(t => t.id !== id);
        else { const t = (WS.todos || []).find(t => t.id === id); if (t) t.status = status; }
        renderWorkspace();
    } catch (_) {}
}

// ── 卡 3：我的個人資料（白名單欄位可編輯）──
const PF_FIELDS = [
    ["phone", "電話"], ["email", "Email"], ["address", "地址"],
    ["emergency_contact", "緊急聯絡"], ["portfolio_url", "作品集"],
];
function cardProfile(pf) {
    const card = makeCard("My Profile", "個人資料", "", "profile");
    const body = card.querySelector(".card-body");
    if (!pf) { body.innerHTML = `<div class="empty">尚未綁定人員檔案</div>`; return card; }
    body.innerHTML = `
        <div class="pf-line"><span class="k">姓名</span><span class="v">${esc(pf.name)}${pf.role ? `　<span class="pill">${esc(pf.role)}</span>` : ""}</span></div>
        ${PF_FIELDS.map(([k, lbl]) => `<div class="pf-line"><span class="k">${lbl}</span><span class="v">${esc(pf[k]) || "<span style='color:#a3a3a3'>—</span>"}</span></div>`).join("")}
        <div class="pf-line"><span class="k">簡介</span><span class="v">${esc(pf.bio) || "<span style='color:#a3a3a3'>—</span>"}</span></div>
        <div style="padding-top:14px;"><button class="mini-btn" onclick="editProfile(this)">編輯資料</button></div>`;
    return card;
}
function editProfile(btn) {
    const pf = (WS && WS.profile) || {};
    const body = btn.closest(".card").querySelector(".card-body");
    body.innerHTML = `<div class="pf-edit" style="padding-top:10px;">
        ${PF_FIELDS.map(([k, lbl]) => `<div class="field"><label>${lbl}</label><input data-pf="${k}" value="${esc(pf[k])}"></div>`).join("")}
        <div class="field"><label>簡介</label><textarea data-pf="bio" rows="3">${esc(pf.bio)}</textarea></div>
        <div style="display:flex;gap:8px;padding-top:4px;">
            <button class="mini-btn" onclick="saveProfile(this)">儲存</button>
            <button class="mini-btn" onclick="renderWorkspace()">取消</button>
        </div>
        <div class="err" data-pf-err style="margin-top:10px;"></div>
    </div>`;
}
async function saveProfile(btn) {
    const body = btn.closest(".card-body");
    const payload = {};
    body.querySelectorAll("[data-pf]").forEach(el => { payload[el.getAttribute("data-pf")] = el.value; });
    try {
        const r = await mfetch("/api/v1/me/profile", { method: "PUT", body: JSON.stringify(payload) });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            const e = body.querySelector("[data-pf-err]");
            e.textContent = d.detail || "儲存失敗"; e.style.display = "block";
            return;
        }
        // PUT 已回傳最新 profile — 直接更新本地狀態重繪，不重抓 bundle
        const d = await r.json();
        if (WS && d.profile) WS.profile = d.profile;
        renderWorkspace();
    } catch (_) {}
}
