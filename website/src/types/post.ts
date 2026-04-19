/**
 * post.ts — Insight / 專欄文章 TS interface
 *
 * 目前資料源：lib/posts.ts 硬編（6 篇假文章 + picsum 封面）。
 * M-E-5.2 後改接 Notion as CMS 時，保持此 interface 形狀不變即可。
 */

export type PostCategory = "documentary" | "post-production" | "project-review" | "workflow";

export interface IPost {
    slug: string;
    title: string;
    category: PostCategory;
    category_label_zh: string;
    category_label_en: string;
    cover_url: string;
    excerpt: string;
    published_at: string;     // ISO date
    body?: string;            // Markdown / 純文字，詳情頁用；列表頁只需 excerpt
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
