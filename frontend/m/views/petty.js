/**
 * 零用金分頁：**不另做表單**，掛既有 tabs/petty/petty-view.js 的 renderMine
 * （/petty-cash.html 與 CRM 財務子視圖同一個模組，含拍照收據）。
 * 閘門 me_petty（或管理員）；沒有就在分頁裡放一句話，不藏分頁。
 * petty-view 自帶的 <style> 吃白底主題的 CSS 變數 —— 深色值定義在 #petty 宿主上
 * （見 crm.html 的 #petty 區塊），fetch 合約走 window.__petty（同 petty-cash.html）。
 */
import { state, errBox } from '../ui.js';

let _mounted = false;

export async function render(host, { first }) {
    if (!first && _mounted) return;
    const me = state.me || {};
    const isAdmin = (me.access_level || 0) >= 3;
    if (!isAdmin && !(me.modules || []).includes('me_petty')) {
        host.innerHTML = `<div class="m-h">零用金</div>
          <div class="m-empty">此帳號尚未開通零用金（需要「我的零用金」me_petty 模組），請找管理員開通。<br>
          桌機版在 <a href="/petty-cash.html">/petty-cash.html</a>。</div>`;
        return;
    }
    host.innerHTML = '<div class="m-h">零用金</div><div id="petty" class="petty-host"><div class="m-empty">載入中…</div></div>';
    const inner = host.querySelector('#petty');
    try {
        const [{ authFetch, bearerHeader }, { renderMine }] = await Promise.all([
            import('/js/shared/utils.js'),
            import('/tabs/petty/petty-view.js'),
        ]);
        // 與 petty-cash.html 相同的宿主合約：body 傳物件由 authFetch stringify；
        // 上傳走 bearerHeader() 讓瀏覽器自己補 multipart boundary
        window.__petty = window.__petty || {
            mfetch: authFetch,
            ufetch: (path, form) => fetch(path, { method: 'POST', headers: bearerHeader(), body: form }),
        };
        await renderMine(inner);
        _mounted = true;
    } catch (e) {
        inner.innerHTML = errBox(e);
    }
}
