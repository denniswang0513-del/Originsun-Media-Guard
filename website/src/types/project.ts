/**
 * project.ts — 對外作品 TS interface
 * 對應 core/schemas_website.py 的 ProjectPublicResponse / ProjectPublicDetail
 */

export interface IPublicProject {
    slug: string;
    title: string;
    client?: string | null;
    youtube_id?: string | null;
    description?: string | null;
    year?: number | null;
    categories: string[];          // category slugs
    thumbnail_url?: string | null; // YouTube maxresdefault
    cover_url?: string | null;     // OG image — sc.cover_url 鏡像，作品集卡片用
    featured: boolean;
    noindex?: boolean;             // per-work 強制 noindex
    // SEO 301 來源舊 URL — JSON-LD sameAs / markdown 鏡像「曾用 URL」用
    old_urls?: string[];
    // 列表卡片用 credits 摘要（「主演 邱雲福 · 導演 王小明」）
    credits_summary?: string;
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

export interface IPublicProjectDetail extends IPublicProject {
    credits: CreditsData;
    published_at?: string | null;
    related?: IPublicProject[];
    gallery?: IGalleryItem[];
    process_items?: IProcessItem[];
}

export interface IWorksListResponse {
    items: IPublicProject[];
    total: number;
    page: number;
    limit: number;
}
