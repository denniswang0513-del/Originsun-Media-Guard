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
    featured: boolean;
}

export interface IPublicProjectDetail extends IPublicProject {
    credits: Record<string, string | string[]>;
    published_at?: string | null;
    related?: IPublicProject[];
}

export interface IWorksListResponse {
    items: IPublicProject[];
    total: number;
    page: number;
    limit: number;
}
