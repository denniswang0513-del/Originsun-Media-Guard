/**
 * post.ts — Insight / 專欄文章 TS interface
 *
 * 目前資料源：lib/posts.ts 硬編（6 篇假文章 + picsum 封面）。
 * M-E-5.2 後改接 Notion as CMS 時，保持此 interface 形狀不變即可。
 */

export type PostCategory = "documentary" | "post-production" | "project-review" | "workflow";

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

export interface IPostCategoryOption {
    value: PostCategory | "all";
    label_zh: string;
    label_en: string;
}

export const POST_CATEGORIES: IPostCategoryOption[] = [
    { value: "all",             label_zh: "全部",   label_en: "All" },
    { value: "documentary",     label_zh: "紀錄片", label_en: "Documentary" },
    { value: "post-production", label_zh: "後期",   label_en: "Post-production" },
    { value: "project-review",  label_zh: "案件回顧", label_en: "Project Review" },
    { value: "workflow",        label_zh: "工作流程", label_en: "Workflow" },
];
