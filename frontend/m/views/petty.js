/**
 * 零用金分頁：**不另做表單**，掛既有 tabs/petty/petty-view.js 的 renderMine
 * （/petty-cash.html 與 CRM 財務子視圖同一個模組，含拍照收據）。
 * 閘門 me_petty（或管理員）；沒有就在分頁裡放一句話，不藏分頁。
 * petty-view 自帶的 <style> 吃白底主題的 CSS 變數 —— 深色值定義在 #petty 宿主上
 * （見 crm.html 的 #petty 區塊）。fetch 出口用 petty-view 自己的預設宿主（authFetch）。
 */
import { state, errBox, isAdmin as isAdminOf } from '../ui.js';

let _mounted = false;

export async function render(host, { first }) {
    if (!first && _mounted) return;
    const me = state.me || {};
    const isAdmin = isAdminOf(me);
    if (!isAdmin && !(me.modules || []).includes('me_petty')) {
        host.innerHTML = `<div class="m-h">零用金</div>
          <div class="m-empty">此帳號尚未開通零用金（需要「我的零用金」me_petty 模組），請找管理員開通。<br>
          桌機版在 <a href="/petty-cash.html">/petty-cash.html</a>。</div>`;
        return;
    }
    host.innerHTML = '<div class="m-h">零用金</div><div id="petty"><div class="m-empty">載入中…</div></div>';
    const inner = host.querySelector('#petty');
    try {
        const { renderMine } = await import('/tabs/petty/petty-view.js');
        await renderMine(inner);
        _mounted = true;
    } catch (e) {
        inner.innerHTML = errBox(e);
    }
}
