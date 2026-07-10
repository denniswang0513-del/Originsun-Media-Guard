/**
 * _api.mjs — build 期呼叫 website-api 的共用件
 *
 * integrations/ 在純 Node context 跑（astro.config.mjs 啟動前就執行，沒有 Vite/Astro
 * 的 import.meta.env、也沒載 .env），所以用 process.env 而非 src/lib/config.ts。
 * 環境變數須在 shell / docker 設好。
 *
 * 契約：API 離線時各 integration 一律降級（回空 map / 空 redirects），build 不中斷。
 */
export const WEBSITE_API_BASE = process.env.WEBSITE_API_BASE || "http://localhost:8001";

/** GET 一個 JSON endpoint。非 2xx 或逾時 → throw，由 caller 決定降級行為。 */
export async function apiGet(path) {
    const res = await fetch(`${WEBSITE_API_BASE}${path}`, {
        signal: AbortSignal.timeout(10_000),
        headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`);
    return res.json();
}
