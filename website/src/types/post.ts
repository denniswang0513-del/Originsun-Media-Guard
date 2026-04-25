/**
 * post.ts — Insight / 專欄文章 TS interface
 *
 * 資料源：lib/posts.ts → src/content/posts.json
 *   （Python notion_service 同步時產生；無檔案時 lib 會 fallback 空陣列）
 *
 * 分類為動態 — Notion 「分類」multi_select 改了 lib/categories.ts 自動跟上，
 * 所以 PostCategory 是 plain string 而非 literal union。
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
    category: PostCategory;
    category_label_zh: string;
    category_label_en: string;
    cover_url: string;
    excerpt: string;
    published_at: string;     // ISO date
    body?: PostBlock[];       // 詳情頁內文，列表頁只需 excerpt
    read_time_min?: number;
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
