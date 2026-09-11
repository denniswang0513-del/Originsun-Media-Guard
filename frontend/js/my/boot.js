// ────────────────────────────────────────────────────────────────────────────
// /my.html 員工工作台 —— 頁面 boot（開頁就跑，所以這支一定載最後）
//
// ⚠ 這是「傳統 script」不是 module：my.html 底部依序 <script src> 載入 js/my/*.js，
//   幾支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見。
//   載入順序＝原本 inline script 由上而下的執行順序，不可調換。
// 跨檔用到：$、TOKEN_KEY／_resetToken／loadWorkspace／showLogin（shell.js）
// ────────────────────────────────────────────────────────────────────────────
// ── boot ──
(function () {
    if (_resetToken()) {                 // 重設信連結進來：先設新密碼
        $("reset-view").style.display = "block";
        $("rs-pwd").focus();
        return;
    }
    if (localStorage.getItem(TOKEN_KEY)) loadWorkspace();
    else showLogin("");
})();
