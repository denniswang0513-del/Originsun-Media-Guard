/**
 * post.ts — Insight / 專欄文章 TS interface
 *
 * 資料源：lib/posts.ts → fetch /api/website/posts（DB-as-truth，PostgreSQL）
 *         lib/categories.ts → fetch /api/website/post_categories
 *   （Phase A 起取代 src/content/posts.json + categories.json，
 *    admin 在「部落格管理」Tab 編輯，60s debounce 觸發 Astro rebuild）
 *
 * 分類為動態 — admin 可任意新增/改名，所以 PostCategory 是 plain string 而非 literal union。
 */

export type PostCategory = string;

/**
 * PostBlock — 文章內容區塊（對齊 Notion block 概念）
 * - paragraph: 段落；可標 lead:true 呈現較大字體（引言）
 * - heading: H2（level 2）/ H3（level 3）
 * - image: 支援 3 種寬度（content / wide / full）+ caption
 * - video: YouTube 影片 embed（用 lite-youtube-embed，點擊才載 iframe）
 * - quote: 引言區塊 + 可選出處
 * - list: 條列（bullet / ordered）
 */
export type PostBlockWidth = "content" | "wide" | "full";

export type PostBlock =
    | { type: "paragraph"; text: string; lead?: boolean }
    | { type: "heading"; level: 2 | 3; text: string }
    | { type: "image"; src: string; alt?: string; caption?: string; width?: PostBlockWidth }
    | { type: "video"; youtube_id: string; caption?: string; width?: PostBlockWidth }
    | { type: "quote"; text: string; author?: string }
    | { type: "list"; items: string[]; ordered?: boolean };

export interface IPost {
    slug: string;
    title: string;
    category: PostCategory;           // 主分類 slug（多選分類取第一個）
    category_slugs?: string[];        // 全部分類 slugs（DB 多對多）
    category_label_zh: string;
    category_label_en: string;
    cover_url: string;
    excerpt: string;
    published_at: string;             // ISO date
    body?: PostBlock[];               // 詳情頁內文，列表頁只需 excerpt
    read_time_min?: number;
    // SEO 用欄位（從 DB API 帶過來，可選）
    date_modified?: string;           // admin 最後編輯 ISO timestamp
    author_name?: string;             // per-post 作者；空則 fallback 到公司
    author_url?: string;
    seo_title?: string;
    seo_description?: string;
    og_image_url?: string;
    canonical_url?: string;
    noindex?: boolean;
    faqs?: { q: string; a: string }[];   // AI SEO 生成的常見問題（FAQPage + 文章底部可見區）
}

/**
 * IPostCategoryOption — 對應 lib/categories.ts 載入的單筆 category。
 * 欄位形狀對齊 Python notion_service 同步輸出 + 一個特殊 id="all" pseudo-category
 * 給篩選器當「全部」按鈕。
 */
export interface IPostCategoryOption {
    id: string;        // category slug (e.g. "documentary"，"all" 為篩選器全部按鈕)
    name: string;      // 原 Notion option name（debug / fallback 顯示）
    label_zh: string;
    label_en: string;
    color?: string;    // Notion option color
    count?: number;    // 該分類下的文章數
}
