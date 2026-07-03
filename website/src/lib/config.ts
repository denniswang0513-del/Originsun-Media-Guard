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


/** 正式網域 fallback（未設 Astro.site 時的預設） */
export const SITE_URL_FALLBACK = "https://www.originsun-studio.com";


/** 各頁面/元件共用的數量上限，集中避免散落 magic numbers */
export const LIMITS = {
    HOME_FEATURED: 9,       // 首頁精選作品數（3×3 無縫網格）
    RELATED_WORKS: 3,       // 作品詳情頁相關作品數
    WORKS_PAGE_SIZE: 12,    // /works 分頁大小（client-side filter 已廢用）
    BUILD_MAX_WORKS: 200,   // 建置時從 API 撈的作品上限（超過需調 API）
} as const;
