/**
 * build-redirects.mjs — Astro build init 時抓 redirect map
 *
 * 對應後端 GET /api/website/redirects（聚合所有 post.old_urls → 新 URL）。
 * 結果塞進 astro.config.mjs `redirects` config，build 期 Astro 為每個舊 URL
 * 產一張靜態 redirect 頁（含 meta refresh + canonical = 軟 301）。
 *
 * 真正硬 301 由 NAS nginx 透過 sync_redirects_to_nas() 處理（同樣資料源）。
 *
 * API 離線時回 {} → build 不中斷，redirect 暫缺；下次 rebuild 補上。
 */
import { apiGet } from "./_api.mjs";

export async function fetchRedirects() {
    try {
        const data = await apiGet("/api/website/redirects");
        const map = (data && data.items) || {};
        const count = Object.keys(map).length;
        if (count > 0) console.log(`[build-redirects] loaded ${count} redirects`);
        return map;
    } catch (e) {
        console.warn(`[build-redirects] fetch failed: ${e.message}, fallback {}`);
        return {};
    }
}
