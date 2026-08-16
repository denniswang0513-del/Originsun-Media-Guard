// ─── 金額檢視授權（前端鏡射）─────────────────────────────────
// owner 2026-08-15：**預設看不到金額，除非我授權**。
//
// 🔴 **執行的是後端**（core/money.py：整支 403 + 欄位從回應裡刪掉）。這裡不是
// 守衛，是為了「不要畫出謊話」：沒授權時金額欄位根本不在 payload 裡，而畫面上
// 一堆 `p.contract_amount || 0` 會把「看不到」畫成「這個客戶總營收 0 元」。
//
// 所以這支只回答一件事：**這一格該不該畫**。要藏的是整塊（欄／卡／區段），
// 不是把數字換成 `***` —— 遮罩會讓人一直追問「那到底多少」，整塊不存在傳達的
// 才是「這個角色不需要這個」。
export function canSeeMoney() {
    if ((window._accessLevel || 0) >= 3) return true;   // 管理員一律看得到
    return (window._modules || []).includes('money_view');
}

// 整塊「這裡本來是錢」的替代畫面。兩個呼叫端（專案詳情的財務摘要、執行人員）
// 說的是同一句話 —— 各寫一份的話改字時只會改到一邊。
export const NO_MONEY_HTML =
    '<div class="crm-empty" style="padding:8px 0;font-size:12px;">'
    + '此帳號沒有金額檢視權限</div>';
