/**
 * category.ts — 作品分類 TS interface
 * 對應 core/schemas_website.py 的 CategoryPublicResponse
 */
export interface ICategory {
    slug: string;
    name_zh: string;
    name_en?: string | null;
    count: number;
}
