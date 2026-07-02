/**
 * service.ts — 服務項目 TS interface
 * 對應 core/schemas_website.py 的 ServicePublicResponse
 */
export interface IService {
    slug: string;
    title: string;
    title_en?: string | null;      // 英文標題（英文模式顯示，空則 fallback title）
    icon?: string | null;
    short_desc?: string | null;
    short_desc_en?: string | null; // 英文簡述（空則 fallback short_desc）
    full_desc?: string | null;
    full_desc_en?: string | null;  // 英文詳述（空則 fallback full_desc）
    cover_image?: string | null;
    related_category_slug?: string | null;
}
