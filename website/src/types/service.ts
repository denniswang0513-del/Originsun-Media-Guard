/**
 * service.ts — 服務項目 TS interface
 * 對應 core/schemas_website.py 的 ServicePublicResponse
 */
export interface IService {
    slug: string;
    title: string;
    icon?: string | null;
    short_desc?: string | null;
    cover_image?: string | null;
    related_category_slug?: string | null;
}
