/**
 * config.ts — 從 Astro 環境變數讀 runtime / build-time 設定
 *
 * 注意：Astro 的 `import.meta.env` 在 build 時與 dev 時都可取，但：
 * - 以 PUBLIC_ 為前綴的變數才會 expose 到 client bundle
 * - 其他只在 build / SSR 時可見（本專案幾乎純 SSG，所以主要用 build-time）
 */

export const WEBSITE_API_BASE =
    import.meta.env.WEBSITE_API_BASE?.toString() || "http://localhost:8001";

export const TURNSTILE_SITE_KEY =
    import.meta.env.PUBLIC_TURNSTILE_SITE_KEY?.toString() || "";

export const IS_DEV = import.meta.env.DEV === true;
