/**
 * project.ts — 對外作品 TS interface
 * 對應 core/schemas_website.py 的 ProjectPublicResponse / ProjectPublicDetail
 */

export interface IPublicProject {
    slug: string;
    title: string;
    title_en?: string | null;       // 英文標題（英文模式顯示，空則 fallback title）
    client?: string | null;
    client_en?: string | null;      // 英文客戶名（空則 fallback client）
    youtube_id?: string | null;
    description?: string | null;
    description_en?: string | null; // 英文描述（空則 fallback description）
    year?: number | null;
    categories: string[];          // 製作類型 slug（kind=category）
    tags: string[];                // 使用場景 slug（kind=tag）
    thumbnail_url?: string | null; // YouTube maxresdefault
    cover_url?: string | null;     // OG image — sc.cover_url 鏡像，作品集卡片用
    carousel_image?: string | null;        // 首頁輪播取圖（精選圖→成果展示第一張；空則前端接 YouTube）
    featured: boolean;
    noindex?: boolean;             // per-work 強制 noindex
    // SEO 301 來源舊 URL — JSON-LD sameAs / markdown 鏡像「曾用 URL」用
    old_urls?: string[];
    // 列表卡片用 credits 摘要（「主演 邱雲福 · 導演 王小明」）
    credits_summary?: string;
    // credits 雙模式：'block' (用 credits[]) / 'text' (用 credits_text 純文字)
    credits_mode?: "block" | "text";
    credits_text?: string | null;
}

export interface ICreditEntry {
    duty?: string;
    name: string;
    resume_url?: string;
}

export interface ICreditBlock {
    role_id: number | null;
    name_zh: string;
    name_en?: string | null;
    entries: ICreditEntry[];
}

// 過渡期容忍兩種格式：新（block list）或舊（dict）
export type CreditsData = ICreditBlock[] | Record<string, string | string[]>;

export interface IGalleryItem {
    url: string;
    caption?: string;
}

export interface IProcessItem {
    url?: string;
    phase?: string;
    caption?: string;
    video_url?: string;
}

export interface ISeoKeyFact {
    label: string;
    value: string;
}

export interface ISeoFAQ {
    q: string;
    a: string;
}

export interface IPublicProjectDetail extends IPublicProject {
    credits: CreditsData;
    published_at?: string | null;
    related?: IPublicProject[];
    gallery?: IGalleryItem[];
    process_items?: IProcessItem[];
    // 作品級 SEO / AI SEO 內容（從 website_project_seo 表來；沒設則為 null/空陣列）
    seo_title?: string | null;
    seo_description?: string | null;
    seo_keywords?: string[];
    canonical_url?: string | null;
    narrative_long?: string | null;
    key_facts?: ISeoKeyFact[];
    faqs?: ISeoFAQ[];
}

export interface IWorksListResponse {
    items: IPublicProject[];
    total: number;
    page: number;
    limit: number;
}
