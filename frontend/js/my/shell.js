// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 殼：mfetch／登入·註冊·忘記密碼·重設密碼／Google 登入／載入 workspace／renderWorkspace 骨架
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   改成 type="module" 每支會變成獨立作用域，所有跨檔引用都要改 import／export。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔提供：TOKEN_KEY、$、esc、money、WS、mfetch、showLogin、loadWorkspace、renderWorkspace、grants、renderActions
// 跨檔用到：buildZone1（zone1.js）、cardXxx 家族（cards.js／cards-hr.js）
// ────────────────────────────────────────────────────────────────────────────
"use strict";
const TOKEN_KEY = "auth_token";   // 與內部 App 同 key（frontend/js/auth/auth-state.js）
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
const money = (n) => "NT$ " + Number(n || 0).toLocaleString("zh-TW");
let WS = null;   // 最近一次 workspace bundle

// ⚠ 全域 fetch monkeypatch（login-modal.js）不涵蓋 /api/v1/me/* —— 本頁自帶 Authorization。
async function mfetch(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const tok = localStorage.getItem(TOKEN_KEY);
    if (tok) headers["Authorization"] = "Bearer " + tok;
    return fetch(path, Object.assign({}, opts, { headers }));
}

// ── 視圖切換 ──
function showLogin(msg) {
    $("ws-view").style.display = "none";
    $("register-view").style.display = "none";
    $("forgot-view").style.display = "none";
    $("reset-view").style.display = "none";
    $("login-view").style.display = "block";
    $("hdr-user").style.display = "none";
    $("hdr-hr").style.display = "none";
    $("btn-logout").style.display = "none";
    if (msg) { const e = $("login-err"); e.textContent = msg; e.style.display = "block"; }
    initGoogle();
}
function doLogout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem("auth_user");
    location.reload();
}

// ── 登入（與內部 App 打同一組端點；成功後 token 同源共用）──
function onLoginOk(d) {
    localStorage.setItem(TOKEN_KEY, d.token);
    $("login-err").style.display = "none";
    loadWorkspace();
}
async function doPwdLogin(ev) {
    ev.preventDefault();
    try {
        const r = await fetch("/api/v1/auth/login", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: $("in-user").value.trim(), password: $("in-pwd").value }),
        });
        const d = await r.json();
        if (!r.ok) { showLogin(d.detail || "登入失敗"); return; }
        onLoginOk(d);
    } catch (_) { showLogin("連線失敗，請稍後再試"); }
}
// ── 註冊（兩步驟公司驗證；答案只在伺服端比對）──
async function showRegister() {
    $("login-view").style.display = "none";
    $("register-view").style.display = "block";
    const box = $("rg-choices");
    if (!box.childElementCount) {
        let choices = [];
        try {
            const r = await fetch("/api/v1/auth/register/config");
            choices = (await r.json()).company_choices || [];
        } catch (_) { /* 載入失敗下方送出時會報錯 */ }
        // 洗牌 — 正解不固定在第一位
        for (let i = choices.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [choices[i], choices[j]] = [choices[j], choices[i]];
        }
        box.innerHTML = "";
        choices.forEach(name => {
            const label = document.createElement("label");
            label.className = "rg-choice";
            const radio = document.createElement("input");
            radio.type = "radio"; radio.name = "rg-company"; radio.value = name;
            radio.addEventListener("change", () => {
                box.querySelectorAll(".rg-choice").forEach(el => el.classList.remove("on"));
                label.classList.add("on");
            });
            const span = document.createElement("span");
            span.textContent = name;
            label.appendChild(radio); label.appendChild(span);
            box.appendChild(label);
        });
    }
}
function showLoginFromRegister() {
    $("register-view").style.display = "none";
    $("rg-err").style.display = "none";
    showLogin("");
}

// ── 忘記密碼（寄重設連結）──
function showForgot(ev) {
    if (ev) ev.preventDefault();
    $("login-view").style.display = "none";
    $("forgot-view").style.display = "block";
    $("fg-msg").style.display = "none";
    $("fg-acc").value = $("in-user").value;   // 登入卡打過的帳號帶過來
    $("fg-acc").focus();
}
function showLoginFromForgot() {
    $("fg-msg").style.display = "none";
    showLogin("");
}
function _fgMsg(msg, ok) {
    const e = $("fg-msg");
    e.textContent = msg;
    e.style.display = "block";
    e.style.color = ok ? "var(--sub)" : "";   // 成功訊息不用紅字
}
async function doForgot(ev) {
    ev.preventDefault();
    const acc = $("fg-acc").value.trim();
    if (!acc) { _fgMsg("請輸入帳號或 Email", false); return; }
    const btn = $("fg-go");
    btn.disabled = true;
    try {
        const r = await fetch("/api/v1/auth/forgot", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ account: acc }),
        });
        const d = await r.json();
        if (!r.ok) { _fgMsg(d.detail || "送出失敗，請稍後再試", false); return; }
        _fgMsg(d.message || "已送出", !!d.sent);
    } catch (_) { _fgMsg("連線失敗，請稍後再試", false); }
    finally { btn.disabled = false; }
}

// ── 設定新密碼（重設信連結 ?reset=token 進來）──
function _resetToken() {
    return new URLSearchParams(location.search).get("reset") || "";
}
function cancelReset() {
    history.replaceState(null, "", location.pathname);   // 把 token 從網址拿掉
    showLogin("");
}
function _rsErr(msg) { const e = $("rs-err"); e.textContent = msg; e.style.display = "block"; }
async function doReset(ev) {
    ev.preventDefault();
    $("rs-err").style.display = "none";
    const pwd = $("rs-pwd").value;
    if (pwd !== $("rs-pwd2").value) { _rsErr("兩次密碼不一致"); return; }
    try {
        const r = await fetch("/api/v1/auth/reset", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: _resetToken(), new_password: pwd }),
        });
        const d = await r.json();
        if (!r.ok) { _rsErr(d.detail || "設定失敗，請重新申請重設"); return; }
        history.replaceState(null, "", location.pathname);
        $("reset-view").style.display = "none";
        onLoginOk(d);   // 設定即登入，直接進工作台
    } catch (_) { _rsErr("連線失敗，請稍後再試"); }
}
function _rgErr(msg) { const e = $("rg-err"); e.textContent = msg; e.style.display = "block"; }
async function doRegister(ev) {
    ev.preventDefault();
    $("rg-err").style.display = "none";
    const pwd = $("rg-pwd").value;
    if (pwd !== $("rg-pwd2").value) { _rgErr("兩次密碼不一致"); return; }
    const picked = document.querySelector('input[name="rg-company"]:checked');
    if (!picked) { _rgErr("請選擇公司名稱"); return; }
    try {
        const r = await fetch("/api/v1/auth/register", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: $("rg-user").value.trim(),
                password: pwd,
                email: $("rg-email").value.trim(),
                tax_id: $("rg-taxid").value.trim(),
                company_name: picked.value,
            }),
        });
        const d = await r.json();
        if (!r.ok) { _rgErr(d.detail || "註冊失敗"); return; }
        $("register-view").style.display = "none";
        onLoginOk(d);   // 註冊即登入，直接進工作台
        // 確認信狀態提示（工作台頂部通知列）
        setTimeout(() => {
            const n = $("ws-notice");
            if (n) n.innerHTML = '<div style="border:1px solid var(--line);padding:12px 16px;font-size:13px;color:var(--sub);margin-bottom:18px;">'
                + (d.email_sent ? "確認信已寄至你的信箱。" : "帳號已建立（確認信寄送未完成，不影響使用）。")
                + "其他功能將由管理員開通後生效。</div>";
        }, 800);
    } catch (_) { _rgErr("連線失敗，請稍後再試"); }
}

let _gisInited = false;
async function initGoogle() {
    if (_gisInited) return;
    try {
        const r = await fetch("/api/v1/auth/google/config");
        if (!r.ok) return;
        const cfg = await r.json();
        if (!cfg.enabled || !cfg.client_id) return;
        for (let i = 0; i < 40 && (typeof google === "undefined" || !google.accounts); i++)
            await new Promise(res => setTimeout(res, 150));
        if (typeof google === "undefined" || !google.accounts) return;
        google.accounts.id.initialize({
            client_id: cfg.client_id, auto_select: false, cancel_on_tap_outside: true,
            callback: async (resp) => {
                try {
                    const r2 = await fetch("/api/v1/auth/google/login", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ credential: resp.credential }),
                    });
                    const d = await r2.json();
                    if (!r2.ok) { showLogin(d.detail || "Google 登入失敗"); return; }
                    onLoginOk(d);
                } catch (_) { showLogin("連線失敗，請稍後再試"); }
            },
        });
        google.accounts.id.renderButton($("g_id_signin_container"), {
            theme: "outline", size: "large", width: 320, text: "signin_with",
            shape: "rectangular", logo_alignment: "left",
        });
        $("g_id_signin_container").style.display = "flex";
        $("login-divider").style.display = "flex";
        _gisInited = true;
    } catch (e) { console.warn("[GIS] init failed", e); }
}

// ── 工作台載入 ──
let _tokenSwapRetried = false;
async function loadWorkspace() {
    let r;
    try { r = await mfetch("/api/v1/me/workspace"); }
    catch (_) { showLogin("連線失敗，請稍後再試"); return; }
    if (r.status === 401) { localStorage.removeItem(TOKEN_KEY); showLogin(""); return; }
    if (r.status === 403) {
        // 帳號沒有任何 me_* 權限，或權限剛更新而 token 還是舊的
        showLogin("此帳號尚未開通個人工作台（或權限剛更新）— 請聯絡管理員，或重新登入一次。");
        localStorage.removeItem(TOKEN_KEY);
        return;
    }
    if (!r.ok) { showLogin("載入失敗（" + r.status + "），請稍後再試"); return; }
    WS = await r.json();
    // 後端發現 token 裡簽死的權限跟帳號現在的不一樣（管理員改了／回填補了鑰匙），會順手回一顆新 token：
    // 換掉再抓一次（allowed 是照 token 算的）。新 token 不會再漂，所以只會多抓這一次。
    if (WS.token && WS.token !== localStorage.getItem(TOKEN_KEY)) {
        localStorage.setItem(TOKEN_KEY, WS.token);
        // 最多只補抓一次：後端若還是判「漂」（例如舊機隊寫進來的清單形狀不同），不能在這裡無限重抓
        if (!_tokenSwapRetried) { _tokenSwapRetried = true; return loadWorkspace(); }
    }
    _tokenSwapRetried = false;
    renderWorkspace();
}

function renderWorkspace() {
    const ws = WS;
    $("login-view").style.display = "none";
    $("ws-view").style.display = "block";
    const display = (ws.profile && ws.profile.name) || ws.username || "";
    $("ws-name").textContent = display;
    $("hdr-user").textContent = display;
    $("hdr-user").style.display = "";
    $("btn-logout").style.display = "";
    $("hdr-hr").style.display = ws.hr_manager ? "" : "none";

    const staffSections = ["me_profile", "me_projects", "me_finance", "me_leave", "me_petty", "me_benefits", Z1_MASTER, ...Z1_KEYS];
    $("ws-notice").innerHTML = (!ws.bound && ws.allowed.some(k => staffSections.includes(k)))
        ? `<div class="notice">此帳號尚未綁定人員檔案 — 專案／個人資料／工時卡片暫無資料，請管理員在「使用者管理」完成綁定。</div>` : "";

    // 最上排功能鍵（每次重繪：allowed 不會變，但便宜）
    renderActions(ws);

    // 第一區「今天與這週」：綁了人員、且有任一把子視圖的鑰匙才有（一顆功能一把，owner 2026-09-08；
    // 後端 /me/today＝me_worklog、/me/team_week＝me_team_week、/me/projects_burn＝me_project_lookup，正本 core.auth.ME_ZONE1_KEYS）。
    // me_profile 只開基本資料卡。
    const zone1On = ws.bound && ws.allowed.includes(Z1_MASTER) && ws.allowed.some(k => Z1_KEYS.includes(k));
    $("ws-zone1-h").style.display = zone1On ? "" : "none";
    $("ws-zone1").style.display = zone1On ? "" : "none";
    if (zone1On && !$("ws-zone1").childElementCount) buildZone1();   // 只建一次：格子裡有正在打的字

    const grid = $("ws-grid");
    grid.innerHTML = "";
    // owner 2026-09-05：「我的權益與資料」這區先只留零用金，其餘卡（待辦／請款與薪酬／福委會／個人資料／影像紀錄／提案企劃）
    // 還在規劃中，先不畫；程式留著，之後照規劃逐張放回。
    // 2026-09-07 假勤放回（docs/LEAVE_PLAN.md §7.6：時數帳＋申請單，卡片自己打 /me/leave/summary）。
    // 2026-09-08 個人資料放回：剛註冊的帳號只有 me_profile，這張卡是他唯一看得到的東西（沒有它整頁是空的）。
    const ME_ZONE_ON = new Set(["me_petty", "me_leave", "me_profile"]);   // 規劃好一張放回一張
    const on = (k) => ME_ZONE_ON.has(k);
    if (on("me_todos") && ws.allowed.includes("me_todos"))    grid.appendChild(cardTodos(ws.todos || []));
    if (on("me_leave") && ws.allowed.includes("me_leave"))    grid.appendChild(cardLeave(ws.bound));
    if (on("me_finance") && ws.allowed.includes("me_finance"))  grid.appendChild(cardFinance(ws.timesheet, ws.payments));
    if (on("me_petty") && ws.allowed.includes("me_petty"))    grid.appendChild(cardPettyCash());
    if (on("me_benefits") && ws.allowed.includes("me_benefits")) grid.appendChild(cardBenefits(ws.bound));
    if (on("me_profile") && ws.allowed.includes("me_profile"))  grid.appendChild(cardProfile(ws.profile));
    if (on("media_log") && ws.allowed.includes("media_log"))   grid.appendChild(cardMediaLog());
    if (on("proposal_plan") && ws.allowed.includes("proposal_plan")) grid.appendChild(cardProposalPlan());
    $("ws-grid-h").style.display = grid.childElementCount ? "" : "none";
    // 上週回顧在 grid 之外自成一區（見 #ws-journal 註解）：只建一次，之後只切顯示
    const jr = $("ws-journal");
    const jrOn = ws.allowed.includes("journal");
    if (jrOn && !jr.childElementCount) jr.appendChild(cardJournal());
    jr.style.display = jrOn ? "" : "none";
    $("ws-journal-h").style.display = jrOn ? "" : "none";
    if (!ws.allowed.length)
        grid.innerHTML = `<div class="empty">此帳號尚未開通任何工作台功能，請聯絡管理員。</div>`;
}

// ── 帶 token 的權限（modules／管理員）：只拿來決定要不要畫入口鈕，守衛在後端 ──
function grants() {
    try {
        const part = (localStorage.getItem(TOKEN_KEY) || "").split(".")[1] || "";
        const bin = atob(part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "="));
        const p = JSON.parse(decodeURIComponent(Array.from(bin, ch => "%" + ch.charCodeAt(0).toString(16).padStart(2, "0")).join("")));
        const mods = Array.isArray(p.modules) ? p.modules : [];
        const admin = Number(p.access_level || 0) >= 3;
        return { admin, has: (...m) => admin || m.some(x => mods.includes(x)) };
    } catch (_) { return { admin: false, has: () => false }; }
}

// ── 最上排功能鍵 ──
// owner 2026-09-05：「先只留下零用金」。原本還有請開發票／登記拍攝／領用器材
// （更早的版本還有請款／請假／福委會）—— 一次擺七顆等於沒有重點，先收到只剩
// 真的每天在用的那一顆，其餘等要用了再一顆一顆放回來。
// 卡片區同一天也做了同樣的收斂（見 renderWorkspace 裡「先只留零用金」那段）。
function renderActions(ws) {
    const has = k => ws.allowed.includes(k);
    const items = [
        has("me_petty") && { label: "零用金", href: "/petty-cash.html" },
    ].filter(Boolean);
    $("ws-actions").innerHTML = items.map(it => `<a class="act" href="${it.href}">${it.label}</a>`).join("");
}
